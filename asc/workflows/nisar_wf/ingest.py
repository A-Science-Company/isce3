"""
Stage A -- ingest.

Reads METADATA ONLY from every NISAR L1 RSLC granule in the case directory
(never touches a raster), then writes `stack.json`: the single source of truth
that stages B, G1, G2 and QA all consume.

The important output is the PINNED GEOGRID. Both dates are geocoded onto the
identical absolute corner coordinates and posting, which is what makes the two
GSLCs pixel-aligned by construction rather than by luck. The course notebook
reads the same index range from two GSLCs without ever comparing their grids --
on mismatched grids that yields silent garbage. Pinning here plus the hard gate
in stage G2 closes that hole from both ends.

Grid-size arithmetic deliberately mirrors nisar.workflows.geogrid._grid_size
exactly, so the shape recorded here is the shape ISCE3 will actually write.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from .config import Config, ConfigError
from .util import (
    Logger,
    Result,
    clean_argv,
    decode,
    grid_size,
    human_bytes,
    read_json,
    snap_ceil,
    snap_floor,
    write_json,
    write_sidecar,
)

LSAR = "/science/LSAR"
IDENT = f"{LSAR}/identification"
SWATHS = f"{LSAR}/RSLC/swaths"
ORBIT = f"{LSAR}/RSLC/metadata/orbit"

SPEED_OF_LIGHT = 299792458.0

# identification fields worth carrying into stack.json
IDENT_FIELDS = (
    "absoluteOrbitNumber",
    "trackNumber",
    "frameNumber",
    "orbitPassDirection",
    "lookDirection",
    "zeroDopplerStartTime",
    "zeroDopplerEndTime",
    "listOfFrequencies",
    "productVersion",
    "productType",
    "granuleId",
    "isFullFrame",
    "isMixedMode",
    "isDithered",
    "radarBand",
    "missionId",
)

# per-frequency scalars worth carrying (all present in the real granules)
FREQ_SCALARS = (
    "slantRangeSpacing",
    "sceneCenterAlongTrackSpacing",
    "sceneCenterGroundRangeSpacing",
    "processedRangeBandwidth",
    "processedAzimuthBandwidth",
    "processedCenterFrequency",
    "acquiredCenterFrequency",
    "acquiredRangeBandwidth",
    "nominalAcquisitionPRF",
    "numberOfSubSwaths",
)


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------
def discover_granules(cfg: Config, log: Logger) -> list[Path]:
    """Explicit `granules:` list if given, else glob the case dir for RSLCs."""
    if cfg.granules:
        out = []
        for g in cfg.granules:
            p = Path(g)
            if not p.is_absolute():
                p = cfg.case / g
            out.append(p.resolve())
    else:
        pats = ("NISAR_L1_*RSLC*.h5", "*RSLC*.h5")
        found: list[Path] = []
        for pat in pats:
            found = sorted(cfg.case.glob(pat))
            if found:
                break
        out = [p.resolve() for p in found]
        log.info(f"auto-discovered {len(out)} RSLC granule(s) in {cfg.case}")

    if not out:
        raise ConfigError(
            f"no NISAR L1 RSLC granules found in {cfg.case}\n"
            f"  Expected files matching 'NISAR_L1_*RSLC*.h5'.\n"
            f"  Either place the granules there or set `granules:` explicitly in the config."
        )
    for p in out:
        if not p.exists():
            raise ConfigError(f"granule does not exist: {p}")
    return out


def _date_from_granule(path: Path, zero_doppler_start: str) -> str:
    """
    YYYYMMDD for the acquisition.

    Authoritative source is zeroDopplerStartTime inside the file; the granule
    filename is only used as a cross-check.
    """
    date = zero_doppler_start[:10].replace("-", "")
    m = re.search(r"_(\d{8})T\d{6}_", path.name)
    if m and m.group(1) != date:
        raise ConfigError(
            f"granule filename date {m.group(1)} disagrees with in-file "
            f"zeroDopplerStartTime {date} for {path.name} -- refusing to guess which is right"
        )
    if not re.fullmatch(r"\d{8}", date):
        raise ConfigError(f"could not parse an acquisition date from {path.name}")
    return date


# --------------------------------------------------------------------------
# metadata read
# --------------------------------------------------------------------------
def read_granule_metadata(path: Path, log: Logger) -> dict:
    """Open one RSLC and pull identification + per-frequency geometry. No rasters."""
    meta: dict[str, Any] = {
        "path": str(path),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
    }
    with h5py.File(path, "r") as f:
        if IDENT not in f:
            raise ConfigError(
                f"{path.name} has no {IDENT} group -- is this really a NISAR L1 RSLC?"
            )
        ident = {}
        for key in IDENT_FIELDS:
            if key in f[IDENT]:
                ident[key] = decode(f[IDENT][key][()])
        meta["identification"] = ident

        for required in ("trackNumber", "frameNumber", "orbitPassDirection", "lookDirection"):
            if required not in ident:
                raise ConfigError(f"{path.name} is missing {IDENT}/{required}")

        meta["date"] = _date_from_granule(path, str(ident["zeroDopplerStartTime"]))

        # bounding polygon (WKT, carries Z)
        wkt = decode(f[f"{IDENT}/boundingPolygon"][()])
        meta["bounding_polygon_wkt"] = wkt

        # swath-level
        if SWATHS not in f:
            raise ConfigError(f"{path.name} has no {SWATHS} group")
        swaths = f[SWATHS]
        if "zeroDopplerTimeSpacing" in swaths:
            dt = float(swaths["zeroDopplerTimeSpacing"][()])
            meta["zero_doppler_time_spacing"] = dt
            meta["prf_from_spacing"] = 1.0 / dt if dt else None

        # per-frequency
        freqs: dict[str, Any] = {}
        for fr in ("A", "B"):
            gpath = f"{SWATHS}/frequency{fr}"
            if gpath not in f:
                continue
            g = f[gpath]
            entry: dict[str, Any] = {}
            pols = decode(g["listOfPolarizations"][()]) if "listOfPolarizations" in g else []
            # sorted: the granules on disk list these in different orders
            # (['HH','HV'] vs ['HV','HH']), and downstream comparisons must not
            # depend on which order a given file happened to use
            entry["polarizations"] = sorted(str(p) for p in pols)
            for key in FREQ_SCALARS:
                if key in g:
                    entry[key] = decode(g[key][()])
            # dims from the first available polarization raster's .shape
            # (h5py reads the header only -- no data transfer)
            for pol in entry["polarizations"]:
                if pol in g:
                    entry["shape"] = list(g[pol].shape)
                    entry["dtype"] = str(g[pol].dtype)
                    break
            # wavelength from the processed centre frequency
            fc = entry.get("processedCenterFrequency") or entry.get("acquiredCenterFrequency")
            if fc:
                entry["wavelength_m"] = SPEED_OF_LIGHT / float(fc)
            freqs[fr] = entry
        if not freqs:
            raise ConfigError(f"{path.name} exposes no frequencyA/frequencyB swath groups")
        meta["frequencies"] = freqs

        # orbit summary (arrays are small: a few hundred state vectors)
        if f"{ORBIT}/position" in f:
            pos = f[f"{ORBIT}/position"][:]
            t = f[f"{ORBIT}/time"][:]
            meta["orbit"] = {
                "n_state_vectors": int(pos.shape[0]),
                "time_start": float(t[0]),
                "time_stop": float(t[-1]),
                "time_spacing": float(np.mean(np.diff(t))) if t.size > 1 else None,
                "units": decode(f[f"{ORBIT}/time"].attrs.get("units", b"")),
                "mid_position_ecef": [float(v) for v in pos[pos.shape[0] // 2]],
                "mean_altitude_m": float(np.mean(np.linalg.norm(pos, axis=1)) - 6371000.0),
            }
    return meta


# --------------------------------------------------------------------------
# consistency gates
# --------------------------------------------------------------------------
def check_stack_consistency(metas: list[dict], cfg: Config, log: Logger) -> list[str]:
    """
    Fail loudly on anything that would invalidate a shared geogrid or a
    like-for-like interferometric pair.
    """
    warnings: list[str] = []

    def uniq(field: str) -> list:
        return sorted({str(m["identification"].get(field)) for m in metas})

    for field in ("trackNumber", "frameNumber", "orbitPassDirection", "lookDirection"):
        vals = uniq(field)
        if len(vals) > 1:
            detail = "\n".join(
                f"    {m['filename']}: {m['identification'].get(field)}" for m in metas
            )
            raise ConfigError(
                f"granules disagree on {field}: {vals}\n{detail}\n"
                f"  A shared geogrid and an interferometric pair both require identical "
                f"track, frame, pass direction and look side. Split these into separate cases."
            )

    # selected frequencies / polarizations must exist everywhere
    for freq in cfg.frequencies:
        for m in metas:
            if freq not in m["frequencies"]:
                raise ConfigError(
                    f"frequency {freq} requested but absent from {m['filename']} "
                    f"(has {sorted(m['frequencies'])})"
                )
            available = m["frequencies"][freq]["polarizations"]
            missing = [p for p in cfg.polarizations if p not in available]
            if missing:
                raise ConfigError(
                    f"polarization(s) {missing} requested for frequency {freq} but "
                    f"{m['filename']} only has {available}"
                )

    # shapes should match across dates for the same frequency (same frame)
    for freq in cfg.frequencies:
        shapes = {tuple(m["frequencies"][freq].get("shape") or ()) for m in metas}
        if len(shapes) > 1:
            warnings.append(
                f"frequency {freq} RSLC shapes differ across dates ({shapes}); "
                f"this is tolerable because geocoding pins the output grid, but it "
                f"suggests the frames are not identically bounded"
            )

    # date uniqueness
    dates = [m["date"] for m in metas]
    dupes = {d for d in dates if dates.count(d) > 1}
    if dupes:
        raise ConfigError(f"duplicate acquisition date(s) {sorted(dupes)} in the stack")

    if len(metas) < 2:
        warnings.append(
            f"only {len(metas)} granule in the stack; stage G2 (grid gate) needs at least 2"
        )
    return warnings


# --------------------------------------------------------------------------
# geogrid
# --------------------------------------------------------------------------
def _polygon_vertices(wkt: str, densify_deg: float = 0.05) -> np.ndarray:
    """
    Exterior vertices of a bounding polygon as an (N, 2) lon/lat array.

    The polygon is densified first: a great-circle-ish frame edge is straight in
    lon/lat but curved in UTM, so transforming only the original vertices can
    under-cover the true envelope.
    """
    from shapely import wkt as shapely_wkt

    poly = shapely_wkt.loads(wkt)
    try:
        poly = poly.segmentize(densify_deg)
    except AttributeError:  # shapely < 2.0
        pass
    coords = np.asarray(poly.exterior.coords)
    return coords[:, :2]


def compute_geogrid(metas: list[dict], cfg: Config, log: Logger) -> dict:
    """
    Union AOI across granules -> EPSG -> snapped absolute corners -> per-frequency shape.

    Snapping is done HERE, and `x_snap`/`y_snap` are deliberately left null in
    the generated runconfig. Two reasons:
      * with all four corners supplied, ISCE3 takes the deterministic
        `_grid_size` path and our corners survive verbatim;
      * ISCE3's snap branch compares `x_snap <= 0` before checking for None, so
        setting only one of the pair raises a TypeError deep inside geogrid.create.
    Snapping ourselves keeps the pin exact and the failure modes visible.
    """
    from osgeo import osr

    osr.UseExceptions()
    # scrubbed argv: importing nisar pulls in pyre, which parses sys.argv at
    # import time and chokes on our CLI flags (see util.clean_argv)
    with clean_argv():
        from nisar.workflows.dumpconfig import point_to_epsg

    # union of all footprints in lon/lat
    all_ll = np.vstack([_polygon_vertices(m["bounding_polygon_wkt"]) for m in metas])
    lon_min, lat_min = float(all_ll[:, 0].min()), float(all_ll[:, 1].min())
    lon_max, lat_max = float(all_ll[:, 0].max()), float(all_ll[:, 1].max())
    lon_c, lat_c = (lon_min + lon_max) / 2.0, (lat_min + lat_max) / 2.0

    epsg = int(cfg.geogrid.epsg) if cfg.geogrid.epsg else int(point_to_epsg(lon_c, lat_c))
    auto_epsg = cfg.geogrid.epsg is None
    log.info(
        f"AOI lon/lat union: [{lon_min:.4f}, {lat_min:.4f}, {lon_max:.4f}, {lat_max:.4f}]"
        f"  centroid ({lon_c:.4f}, {lat_c:.4f})"
    )
    log.info(f"output EPSG: {epsg}" + ("  (auto, from AOI centroid)" if auto_epsg else "  (from config)"))

    src = osr.SpatialReference()
    src.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(epsg)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    fwd = osr.CoordinateTransformation(src, dst)
    inv = osr.CoordinateTransformation(dst, src)

    xy = np.array([fwd.TransformPoint(float(lon), float(lat))[:2] for lon, lat in all_ll])
    x_min, x_max = float(xy[:, 0].min()), float(xy[:, 0].max())
    y_min, y_max = float(xy[:, 1].min()), float(xy[:, 1].max())

    margin = float(cfg.geogrid.margin_m)
    snap = float(cfg.geogrid.snap)
    x_start = snap_floor(x_min - margin, snap)
    x_end = snap_ceil(x_max + margin, snap)
    y_end = snap_floor(y_min - margin, snap)      # bottom_right y (min northing)
    y_start = snap_ceil(y_max + margin, snap)     # top_left y     (max northing)

    log.info(
        f"projected envelope: x [{x_min:.1f}, {x_max:.1f}]  y [{y_min:.1f}, {y_max:.1f}]"
    )
    log.info(
        f"PINNED (snap {snap:g} m, margin {margin:g} m): "
        f"top_left ({x_start:.1f}, {y_start:.1f})  bottom_right ({x_end:.1f}, {y_end:.1f})"
    )

    # per-frequency shape, using ISCE3's own rounding
    per_freq: dict[str, Any] = {}
    for freq in cfg.frequencies:
        p = cfg.geogrid.posting[freq]
        width = grid_size(x_start, x_end, float(p.x))
        length = grid_size(y_start, y_end, float(p.y))
        nbytes = width * length * 8 * len(cfg.polarizations)
        per_freq[freq] = {
            "x_posting": float(p.x),
            "y_posting": float(p.y),
            "width": width,
            "length": length,
            "polarizations": list(cfg.polarizations),
            "uncompressed_bytes_all_pols": nbytes,
        }
        log.info(
            f"  freq {freq}: posting {p.x:g} x {p.y:g} m -> {length} x {width} px"
            f"  ({human_bytes(nbytes)} uncompressed, {len(cfg.polarizations)} pol)"
        )

    # lon/lat AOI must also cover the SNAPPED box, which can stick out past the
    # footprint -- the DEM is staged from this bbox, and a short DEM fails geocoding
    corners_utm = [(x_start, y_start), (x_end, y_start), (x_start, y_end), (x_end, y_end)]
    back = np.array([inv.TransformPoint(float(x), float(y))[:2] for x, y in corners_utm])
    aoi_lon_min = min(lon_min, float(back[:, 0].min()))
    aoi_lon_max = max(lon_max, float(back[:, 0].max()))
    aoi_lat_min = min(lat_min, float(back[:, 1].min()))
    aoi_lat_max = max(lat_max, float(back[:, 1].max()))

    return {
        "epsg": epsg,
        "epsg_auto_derived": auto_epsg,
        "snap_m": snap,
        "margin_m": margin,
        "top_left": {"x_abs": x_start, "y_abs": y_start},
        "bottom_right": {"x_abs": x_end, "y_abs": y_end},
        "projected_envelope": {"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max},
        "per_frequency": per_freq,
        "radar_grid_cube": {
            "posting": float(cfg.geogrid.radar_grid_cube.posting),
            "heights": [float(h) for h in cfg.geogrid.radar_grid_cube.heights],
        },
        "aoi_lonlat": {
            "footprint": {
                "lon_min": lon_min,
                "lat_min": lat_min,
                "lon_max": lon_max,
                "lat_max": lat_max,
            },
            "with_snapped_grid": {
                "lon_min": aoi_lon_min,
                "lat_min": aoi_lat_min,
                "lon_max": aoi_lon_max,
                "lat_max": aoi_lat_max,
            },
            "centroid": {"lon": lon_c, "lat": lat_c},
        },
    }


def _temporal_baseline(metas: list[dict]) -> dict | None:
    """
    Temporal separation only.

    A *perpendicular* baseline is deliberately not computed here. It requires
    interpolating one orbit to the other's zero-Doppler time and projecting onto
    the look vector; the naive mid-orbit position difference is dominated by the
    along-track offset (thousands of metres) and would sit in the provenance
    record looking like a B_perp that is two orders of magnitude too large.
    B_perp belongs to a later stage, where the geometry cubes already exist.
    """
    if len(metas) < 2:
        return None
    from datetime import datetime

    def parse(meta: dict) -> datetime:
        raw = str(meta["identification"]["zeroDopplerStartTime"])
        return datetime.fromisoformat(raw[:26])

    t0, t1 = parse(metas[0]), parse(metas[-1])
    days = (t1 - t0).total_seconds() / 86400.0
    return {
        "reference_date": metas[0]["date"],
        "secondary_date": metas[-1]["date"],
        "temporal_baseline_days": round(days, 4),
        "perpendicular_baseline_m": None,
        "note": (
            "perpendicular baseline not computed at ingest; it needs orbit "
            "interpolation onto the look vector and is deferred to the "
            "interferogram stage"
        ),
    }


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def run(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    """Stage A. Cheap (metadata only) -- safe to re-run."""
    started = time.time()
    res = Result(stage="ingest")

    if cfg.stack_json.exists() and not force:
        try:
            existing = read_json(cfg.stack_json)
            log.info(f"stack.json already present with {len(existing.get('granules', []))} granule(s)")
            log.info(f"  {cfg.stack_json}")
            log.info("  SKIP (use --force to regenerate)")
            res.skipped = True
            res.outputs = [str(cfg.stack_json)]
            return res
        except Exception as exc:
            log.warn(f"existing stack.json unreadable ({exc}); regenerating")

    granules = discover_granules(cfg, log)
    log.info(f"reading metadata from {len(granules)} granule(s) (no raster access)")
    if dry_run:
        for p in granules:
            log.info(f"  would read {p.name}  ({human_bytes(p.stat().st_size)})")
        log.info(f"  would write {cfg.stack_json}")
        res.skipped = True
        return res

    metas: list[dict] = []
    for p in granules:
        t0 = time.time()
        m = read_granule_metadata(p, log)
        metas.append(m)
        log.info(
            f"  {m['date']}  {p.name}  ({human_bytes(m['size_bytes'])}, "
            f"read in {time.time() - t0:.2f}s)"
        )
        for fr, entry in sorted(m["frequencies"].items()):
            shape = entry.get("shape")
            log.info(
                f"      freq {fr}: {shape}  pols {entry['polarizations']}  "
                f"slantRangeSpacing {entry.get('slantRangeSpacing', float('nan')):.4f} m"
            )

    metas.sort(key=lambda m: m["date"])
    warnings = check_stack_consistency(metas, cfg, log)
    for w in warnings:
        log.warn(w)

    geogrid = compute_geogrid(metas, cfg, log)

    ident0 = metas[0]["identification"]
    reference = metas[0]["date"]
    secondaries = [m["date"] for m in metas[1:]]

    stack = {
        "schema_version": 1,
        "case_name": cfg.case_name,
        "case_dir": str(cfg.case),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config_path": cfg.config_path,
        "track": ident0.get("trackNumber"),
        "frame": ident0.get("frameNumber"),
        "orbit_pass_direction": ident0.get("orbitPassDirection"),
        "look_direction": ident0.get("lookDirection"),
        "reference_date": reference,
        "secondary_dates": secondaries,
        "dates": [m["date"] for m in metas],
        "selected": {
            "frequencies": list(cfg.frequencies),
            "polarizations": list(cfg.polarizations),
            "freq_tag": cfg.freq_tag,
        },
        "geogrid": geogrid,
        "granules": metas,
        "baseline": _temporal_baseline(metas),
        "warnings": warnings,
    }
    write_json(cfg.stack_json, stack)

    log.info("")
    log.info(f"track {stack['track']} frame {stack['frame']}  "
             f"{stack['orbit_pass_direction']}  look {stack['look_direction']}")
    log.info(f"reference {reference}   secondary {', '.join(secondaries) if secondaries else '(none)'}")
    if stack["baseline"]:
        log.info(f"temporal baseline: {stack['baseline']['temporal_baseline_days']:g} days")
    log.info(f"wrote {cfg.stack_json}")

    write_sidecar(
        cfg.prov_dir / "ingest.json",
        stage="ingest",
        inputs={"granules": [str(p) for p in granules], "config": cfg.config_path},
        outputs={"stack_json": str(cfg.stack_json)},
        parameters={
            "frequencies": cfg.frequencies,
            "polarizations": cfg.polarizations,
            "geogrid_snap_m": cfg.geogrid.snap,
            "geogrid_margin_m": cfg.geogrid.margin_m,
            "epsg": geogrid["epsg"],
            "epsg_auto_derived": geogrid["epsg_auto_derived"],
            "posting": {
                f: {"x": v["x_posting"], "y": v["y_posting"]}
                for f, v in geogrid["per_frequency"].items()
            },
        },
        started=started,
        extra={"warnings": warnings},
    )

    res.outputs = [str(cfg.stack_json)]
    res.notes = warnings
    res.metrics = {
        "n_granules": len(metas),
        "epsg": geogrid["epsg"],
        "dates": stack["dates"],
    }
    return res


def load_stack(cfg: Config) -> dict:
    """Read stack.json, with an actionable error if stage A has not run."""
    if not cfg.stack_json.exists():
        raise ConfigError(
            f"stack.json not found at {cfg.stack_json}\n"
            f"  Stage A (ingest) has not run. Execute it first:\n"
            f"    python run_track_g.py --config {cfg.config_path or '<config.yaml>'} --only ingest"
        )
    stack = read_json(cfg.stack_json)
    gg = stack.get("geogrid", {})
    if not gg.get("per_frequency"):
        raise ConfigError(
            f"{cfg.stack_json} contains no pinned geogrid -- it is stale or truncated. "
            f"Regenerate with: --only ingest --force"
        )
    for freq in cfg.frequencies:
        if freq not in gg["per_frequency"]:
            raise ConfigError(
                f"stack.json has no pinned geogrid for frequency {freq} "
                f"(has {sorted(gg['per_frequency'])}). The config's `frequencies` changed "
                f"since ingest ran; regenerate with: --only ingest --force"
            )
    return stack
