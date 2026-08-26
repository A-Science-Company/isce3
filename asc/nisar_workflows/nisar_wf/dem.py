"""
Stage B -- DEM staging.

ISCE3 geocoding needs ELLIPSOIDAL (WGS84) heights. Feeding it a geoid/EGM2008
DEM biases geolocation by the local undulation, which over this AOI is about
-20 m -- far more than the sub-pixel accuracy the whole workflow exists to get.

Two routes, both delivering ellipsoidal heights via `sardem`:

  NISAR (primary)
      Native WGS84 ellipsoidal. HARD PRECONDITION: a free NASA Earthdata
      account with a `urs.earthdata.nasa.gov` entry in ~/.netrc. sardem's
      `_check_earthdata_credentials()` raises before any network call, so a
      missing credential fails in milliseconds instead of halfway through.
      Ocean is filled with real ellipsoidal values (about -20 m here), never 0.

  COP (fallback)
      Copernicus GLO-30, delivered as EGM2008 and converted to WGS84 by sardem
      unless `--keep-egm`. Ocean arrives as nodata == 0 BY DESIGN, because
      sardem passes `-srcnodata 0 -dstnodata 0` to preserve it through the
      geoid conversion. So on this route ocean == 0 is expected and is NOT
      evidence of a geoid-referenced DEM. The datum verification below is
      therefore scoped per-source.

Deliberately NOT staged: a water mask. The `sardem --data-source NASA_WATER`
route is broken upstream -- the SRTMSWBD tiles 404 at e4ftl01.cr.usgs.gov and
sardem substitutes zeros while still exiting 0, silently yielding an all-land
mask. See README "Known upstream breakage".

Every check reads the DEM DECIMATED (gdal buf_xsize/buf_ysize), never whole.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np

from .config import Config, ConfigError
from .ingest import load_stack
from .util import (
    Logger,
    Result,
    StepFailed,
    human_bytes,
    require_tool,
    run_cmd,
    write_sidecar,
)

EARTHDATA_HOST = "urs.earthdata.nasa.gov"

# Overview size used for all DEM statistics. 512 x 512 is plenty to judge a
# datum and costs a few MB regardless of how large the DEM is.
STAT_OVERVIEW = 512


# --------------------------------------------------------------------------
# preconditions
# --------------------------------------------------------------------------
def check_earthdata_netrc(log: Logger) -> bool:
    """
    True if ~/.netrc carries usable credentials for Earthdata.

    Mirrors sardem's own precondition so we can fail (or fall back) with a
    useful message before spawning it.
    """
    path = Path(os.path.expanduser("~/.netrc"))
    if not path.exists():
        log.warn(f"~/.netrc not found -- the NISAR DEM route requires Earthdata credentials")
        return False
    try:
        import netrc

        auth = netrc.netrc(str(path)).authenticators(EARTHDATA_HOST)
    except Exception as exc:
        log.warn(f"could not parse ~/.netrc ({exc})")
        return False
    if not auth:
        log.warn(f"~/.netrc has no entry for {EARTHDATA_HOST}")
        return False
    login, _, password = auth
    if not login or not password:
        log.warn(f"~/.netrc entry for {EARTHDATA_HOST} is missing a login or password")
        return False
    log.info(f"Earthdata credentials found in ~/.netrc for {EARTHDATA_HOST} (login {login!r})")
    return True


def netrc_help() -> str:
    return (
        f"The NISAR DEM route needs a free NASA Earthdata account.\n"
        f"    1. register at https://urs.earthdata.nasa.gov/users/new\n"
        f"    2. add to ~/.netrc:\n"
        f"         machine {EARTHDATA_HOST}\n"
        f"           login YOUR_USERNAME\n"
        f"           password YOUR_PASSWORD\n"
        f"    3. chmod 600 ~/.netrc\n"
        f"  Or set `dem.source: COP` in the config to use Copernicus GLO-30, which\n"
        f"  needs no credentials."
    )


# --------------------------------------------------------------------------
# raster inspection (always decimated)
# --------------------------------------------------------------------------
def dem_info(path: Path) -> dict:
    """Geotransform, size, EPSG and decimated statistics for an existing DEM."""
    from osgeo import gdal, osr

    gdal.UseExceptions()
    ds = gdal.Open(str(path))
    if ds is None:
        raise StepFailed(f"GDAL could not open {path}")
    try:
        gt = ds.GetGeoTransform()
        nx, ny = ds.RasterXSize, ds.RasterYSize
        band = ds.GetRasterBand(1)

        epsg = None
        wkt = ds.GetProjection()
        if wkt:
            sref = osr.SpatialReference(wkt=wkt)
            code = sref.GetAuthorityCode(None)
            if code:
                epsg = int(code)

        # DECIMATED read: buf_xsize/buf_ysize let GDAL subsample rather than
        # materialise 10981 x 10621 float32 (~470 MB)
        bx = min(nx, STAT_OVERVIEW)
        by = min(ny, STAT_OVERVIEW)
        arr = band.ReadAsArray(0, 0, nx, ny, buf_xsize=bx, buf_ysize=by).astype(np.float64)

        nodata = band.GetNoDataValue()
        finite = np.isfinite(arr)
        if nodata is not None:
            finite &= arr != nodata
        valid = arr[finite]

        info = {
            "path": str(path),
            "size": [nx, ny],
            "geotransform": list(gt),
            "epsg": epsg,
            "dtype": gdal.GetDataTypeName(band.DataType),
            "nodata": nodata,
            "bounds": {
                "lon_min": gt[0],
                "lon_max": gt[0] + gt[1] * nx,
                "lat_max": gt[3],
                "lat_min": gt[3] + gt[5] * ny,
            },
            "file_bytes": path.stat().st_size,
            "overview_shape": [int(by), int(bx)],
        }
        if valid.size:
            info["stats"] = {
                "min": float(valid.min()),
                "max": float(valid.max()),
                "mean": float(valid.mean()),
                "median": float(np.median(valid)),
                "frac_exact_zero": float(np.mean(arr == 0.0)),
                "frac_negative": float(np.mean(valid < 0)),
                "valid_fraction": float(finite.mean()),
            }
        else:
            info["stats"] = None
        return info
    finally:
        ds = None


def covers_aoi(info: dict, aoi: dict, tol_deg: float = 1e-6) -> tuple[bool, list[str]]:
    """Does an existing DEM span the requested AOI box?"""
    b = info["bounds"]
    gaps: list[str] = []
    if b["lon_min"] > aoi["lon_min"] + tol_deg:
        gaps.append(f"west edge short by {b['lon_min'] - aoi['lon_min']:.5f} deg")
    if b["lon_max"] < aoi["lon_max"] - tol_deg:
        gaps.append(f"east edge short by {aoi['lon_max'] - b['lon_max']:.5f} deg")
    if b["lat_min"] > aoi["lat_min"] + tol_deg:
        gaps.append(f"south edge short by {b['lat_min'] - aoi['lat_min']:.5f} deg")
    if b["lat_max"] < aoi["lat_max"] - tol_deg:
        gaps.append(f"north edge short by {aoi['lat_max'] - b['lat_max']:.5f} deg")
    return (not gaps), gaps


def verify_datum(info: dict, source: str, log: Logger) -> list[str]:
    """
    Source-scoped sanity check that heights are ellipsoidal.

    Regional EGM2008 undulation over this AOI is about -20 m (range -9 m in the
    SW to -38 m in the NE), so an ellipsoidal DEM reads clearly NEGATIVE over
    water while a geoid-referenced one reads near 0.

    The check is scoped by source because COP writes ocean as nodata == 0 by
    design; applying the NISAR test there would flag a perfectly good DEM.
    """
    notes: list[str] = []
    stats = info.get("stats")
    if not stats:
        notes.append("DEM overview has no valid pixels -- cannot verify the vertical datum")
        return notes

    log.info(
        f"DEM stats (decimated {info['overview_shape'][0]}x{info['overview_shape'][1]}): "
        f"min {stats['min']:.1f} max {stats['max']:.1f} median {stats['median']:.1f} m, "
        f"{stats['frac_exact_zero'] * 100:.1f}% exactly zero"
    )

    if source == "NISAR":
        # this route fills ocean with real ellipsoidal values; zeros are suspect
        if stats["frac_exact_zero"] > 0.10:
            notes.append(
                f"{stats['frac_exact_zero'] * 100:.1f}% of the NISAR DEM is exactly 0. "
                f"That route should carry real ellipsoidal values everywhere including "
                f"ocean -- suspect a partial/failed download."
            )
        if stats["min"] > -5.0:
            notes.append(
                f"DEM minimum is {stats['min']:.1f} m, i.e. nothing clearly below the "
                f"ellipsoid. Over this AOI a WGS84-ellipsoidal DEM should read roughly "
                f"-18 to -37 m over water (EGM2008 undulation ~ -20 m). This DEM may be "
                f"GEOID-referenced, which would bias geolocation by ~20 m."
            )
        else:
            log.info(
                f"vertical datum looks ellipsoidal: minimum {stats['min']:.1f} m "
                f"(expected roughly -18 to -37 m over water for this AOI)"
            )
    else:
        # COP and friends: ocean == 0 is nodata, so only comment, never fail
        log.info(
            f"source {source}: ocean arrives as nodata == 0 by design "
            f"({stats['frac_exact_zero'] * 100:.1f}% zero here), so ocean values are not "
            f"a datum indicator. Land heights are converted EGM2008 -> WGS84 by sardem."
        )
        if stats["frac_exact_zero"] > 0.95:
            notes.append(
                f"{stats['frac_exact_zero'] * 100:.1f}% of the DEM is zero -- almost "
                f"nothing but nodata. Check the AOI actually overlaps land."
            )
    return notes


# --------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------
def sardem_cmd(cfg: Config, aoi: dict, out_path: Path, source: str) -> list[str]:
    """
    Build the sardem invocation.

    `--bbox left bottom right top` points at pixel EDGES (gdal "pixel is area").
    sardem then snaps each bound OUTWARD to a source pixel edge at (k+0.5)/3600,
    so a bbox landing on whole 1/3600 multiples yields N+1 pixels per dimension.
    We therefore verify COVERAGE, never an exact pixel count.
    """
    cmd = [
        require_tool("sardem"),
        "--bbox",
        f"{aoi['lon_min']:.10f}",
        f"{aoi['lat_min']:.10f}",
        f"{aoi['lon_max']:.10f}",
        f"{aoi['lat_max']:.10f}",
        "--data-source",
        source,
        "--output-format",
        "GTiff",
        "--output-type",
        "float32",
        "-o",
        str(out_path),
    ]
    if cfg.dem.cache_dir:
        cmd += ["--cache-dir", str(cfg.dem.cache_dir)]
    return cmd


def buffered_aoi(stack: dict, buffer_deg: float) -> dict:
    """
    AOI for the DEM: the footprint union already widened to include the snapped
    UTM grid corners, then padded by `buffer_deg`.
    """
    base = stack["geogrid"]["aoi_lonlat"]["with_snapped_grid"]
    return {
        "lon_min": base["lon_min"] - buffer_deg,
        "lat_min": base["lat_min"] - buffer_deg,
        "lon_max": base["lon_max"] + buffer_deg,
        "lat_max": base["lat_max"] + buffer_deg,
    }


def _try_download(cfg: Config, aoi: dict, out_path: Path, source: str, log: Logger) -> None:
    """One download attempt into a temporary path, promoted only on success."""
    if source == "NISAR" and not check_earthdata_netrc(log):
        raise StepFailed(f"Earthdata credentials missing.\n  {netrc_help()}")

    tmp = out_path.with_suffix(out_path.suffix + ".part")
    for stale in (tmp, Path(str(tmp) + ".rsc")):
        if stale.exists():
            stale.unlink()

    log.info(f"staging DEM from source {source}")
    run_cmd(sardem_cmd(cfg, aoi, tmp, source), log, tag=f"sardem/{source}")

    if not tmp.exists() or tmp.stat().st_size == 0:
        raise StepFailed(f"sardem exited 0 but produced no output at {tmp}")

    # validate BEFORE promoting, so a bad download never masquerades as good
    info = dem_info(tmp)
    ok, gaps = covers_aoi(info, aoi)
    if not ok:
        raise StepFailed(
            f"freshly downloaded DEM does not cover the AOI: {'; '.join(gaps)}"
        )
    os.replace(tmp, out_path)
    log.info(f"DEM written: {out_path} ({human_bytes(out_path.stat().st_size)})")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def run(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    started = time.time()
    res = Result(stage="dem")

    stack = load_stack(cfg)
    aoi = buffered_aoi(stack, cfg.dem.buffer_deg)
    out_path = cfg.dem_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log.info(
        f"AOI for DEM (buffer {cfg.dem.buffer_deg:g} deg): "
        f"[{aoi['lon_min']:.4f}, {aoi['lat_min']:.4f}, {aoi['lon_max']:.4f}, {aoi['lat_max']:.4f}]"
    )
    est_nx = int(round((aoi["lon_max"] - aoi["lon_min"]) * 3600)) + 1
    est_ny = int(round((aoi["lat_max"] - aoi["lat_min"]) * 3600)) + 1
    log.info(
        f"expected size approx {est_nx} x {est_ny} px at 1 arcsec "
        f"({human_bytes(est_nx * est_ny * 4)} uncompressed float32; sardem writes "
        f"GTiff with no compression)"
    )

    # ---------------- idempotency ----------------
    if out_path.exists() and not force:
        try:
            info = dem_info(out_path)
            ok, gaps = covers_aoi(info, aoi)
            if ok:
                log.info(f"DEM already present and covers the AOI: {out_path}")
                log.info(
                    f"  {info['size'][0]} x {info['size'][1]} px, {info['dtype']}, "
                    f"EPSG {info['epsg']}, {human_bytes(info['file_bytes'])}"
                )
                notes = verify_datum(info, cfg.dem.source, log)
                for n in notes:
                    log.warn(n)
                log.info("  SKIP (use --force to re-download)")
                res.skipped = True
                res.outputs = [str(out_path)]
                res.notes = notes
                res.metrics = {"size": info["size"], "stats": info.get("stats")}
                return res
            log.warn(
                f"existing DEM does not cover the AOI ({'; '.join(gaps)}); re-downloading"
            )
        except StepFailed as exc:
            log.warn(f"existing DEM unusable ({exc}); re-downloading")

    if dry_run:
        log.info(f"  would run: {' '.join(sardem_cmd(cfg, aoi, out_path, cfg.dem.source))}")
        if cfg.dem.fallback:
            log.info(f"  fallback source on failure: {cfg.dem.fallback}")
        res.skipped = True
        return res

    # ---------------- download, with fallback ----------------
    attempts: list[dict] = []
    sources = [cfg.dem.source] + ([cfg.dem.fallback] if cfg.dem.fallback else [])
    last_error: Exception | None = None
    used_source: str | None = None

    for source in sources:
        try:
            _try_download(cfg, aoi, out_path, source, log)
            used_source = source
            attempts.append({"source": source, "status": "ok"})
            break
        except (StepFailed, OSError) as exc:
            log.error(f"DEM source {source} failed: {exc}")
            attempts.append({"source": source, "status": "failed", "error": str(exc)})
            last_error = exc

    if used_source is None:
        raise StepFailed(
            f"all DEM sources failed ({', '.join(str(s) for s in sources)}).\n"
            f"  last error: {last_error}\n"
            f"  Stage G1 cannot run without a DEM: ISCE3 geocoding needs it to build\n"
            f"  the radar->map transform. Fix credentials or network, or set\n"
            f"  `dem.path` to a DEM you already have (must be WGS84 ELLIPSOIDAL heights)."
        )

    info = dem_info(out_path)
    notes = verify_datum(info, used_source, log)
    for n in notes:
        log.warn(n)

    write_sidecar(
        cfg.prov_dir / "dem.json",
        stage="dem",
        inputs={"stack_json": str(cfg.stack_json), "aoi_lonlat": aoi},
        outputs={"dem_file": str(out_path), "dem_info": info},
        parameters={
            "source_requested": cfg.dem.source,
            "source_used": used_source,
            "fallback": cfg.dem.fallback,
            "buffer_deg": cfg.dem.buffer_deg,
            "attempts": attempts,
            "vertical_datum": "WGS84 ellipsoidal",
        },
        started=started,
        extra={"notes": notes},
    )

    res.outputs = [str(out_path)]
    res.notes = notes
    res.metrics = {
        "source_used": used_source,
        "size": info["size"],
        "stats": info.get("stats"),
    }
    return res
