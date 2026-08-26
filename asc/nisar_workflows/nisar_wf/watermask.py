"""
Water mask -- substitute for the course's broken NASADEM route.

WHY THIS STAGE EXISTS
---------------------
The course derives water from the NASADEM height band's bit 15:

    water = ((h >> 15) & 1) | (h == -32768) | (h <= 0)

i.e. the SRTM water-body flag, OR voids, OR anything at/below 0 m. That route is
dead here: `sardem --data-source NASA_WATER` 404s at e4ftl01.cr.usgs.gov, and
sardem substitutes ZEROS while still exiting 0 -- an all-land mask that every
downstream step would consume as truth.

We cannot recover bit 15 (it lives in NASADEM, which is what 404s), so this
stage reconstructs the only term that carries real information over this AOI:
the sea-level threshold. Two independent routes are implemented and they
cross-check each other.

THE VERTICAL-DATUM TRAP
-----------------------
The course's `h <= 0` is an ORTHOMETRIC test -- NASADEM heights are EGM96
geoid-referenced, so 0 m *is* sea level. Our DEM is WGS84 ELLIPSOIDAL (it has
to be: ISCE3 geocoding requires ellipsoidal heights, see README "Vertical
datum"). Over this AOI the geoid undulation N runs -9.4 m in the SW to -33.3 m
in the NE, so local sea level sits near -28 m ellipsoidal, NOT 0.

Applying `h <= 0` to our DEM unchanged is therefore wrong, and wrong in the
worst direction -- it masks every coastal pixel below roughly +30 m orthometric.
Measured on this scene: it flags 49.84% of the swath as water instead of 47.23%,
wrongly drowning 248,650 pixels = 1,591 km2 of REAL LAND whose true elevation
has median 8.4 m and whose radar amplitude is a land-bright -8.05 dB. That is
precisely the coastal plain a deformation study cares most about.

So: convert to orthometric first (EPSG:4979 -> EPSG:9518, EGM2008), then apply
the course's threshold with the course's semantics intact.

WHY THE THRESHOLD IS +1 m AND NOT 0 m
-------------------------------------
The DEM's ocean is filled at exactly mean sea level, so after the round trip
back to orthometric it lands on H = 0.00 with sub-metre scatter from the
difference between the DEM's internal geoid model and the EGM2008 2.5' grid
used here. Thresholding at exactly 0 therefore slices the ocean population
straight down the middle -- measured, `H <= 0` captures only 49.74% of
unambiguous deep water, essentially a per-pixel coin flip.

A small positive margin steps clear of that ridge onto a flat plateau:

    H <= 0.00 m : deep water captured  49.74% | solid land false-water 0.01%
    H <= 0.25 m :                      98.09% |                        0.02%
    H <= 1.00 m :                      98.21% |                        0.06%   <-- default
    H <= 2.00 m :                      98.23% |                        0.26%
    H <= 3.00 m :                      98.24% |                        0.40%

Capture is flat from 0.25 m to 3 m, so the result is insensitive to the exact
value anywhere in that range -- the sign of a threshold sitting in a real valley
rather than on a slope.

THE FAILURE MODE THIS STAGE REFUSES TO HAVE
-------------------------------------------
pyproj's EGM2008 transform needs the grid `us_nga_egm08_25.tif`. If it is
absent and PROJ_NETWORK is off, PROJ does NOT fail -- it silently falls back to
a "ballpark vertical transformation" that returns the input height UNCHANGED.
Measured on this machine before the grid was installed:

    OFFLINE z = [0. 0. 0.]          <- ballpark, no geoid applied at all

That is the *same* silent-wrong-answer bug as sardem's zero substitution, and it
would turn this stage into the naive ellipsoidal threshold without a word of
warning. `_geoid_transformer` therefore probes the transform against a known
EGM2008 value before it is trusted, and raises rather than return a plausible
wrong mask.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pyproj
import rasterio
from rasterio.warp import Resampling, reproject

from .config import Config
from .util import Logger, Result, StepFailed, fmt_s, write_sidecar

# Mask encoding. 255 matches the GSLC `mask` layer's own "outside the radar
# acquisition extent" fill value, so the two compose without a translation step.
WATER, LAND, OUTSIDE = 1, 0, 255

# EGM2008 N at (0 E, 0 N), metres above the WGS84 ellipsoid. Published value is
# ~17.2 m; measured here 17.2251 m. Used only to prove a real geoid grid is
# loaded -- a ballpark fallback returns 0.0 and fails this by ~17 m.
_PROBE_LON, _PROBE_LAT, _PROBE_N, _PROBE_TOL = 0.0, 0.0, 17.225, 0.5


def _geoid_transformer(geoid_crs: str, log: Logger) -> pyproj.Transformer:
    """
    Build the ellipsoidal -> orthometric transformer and PROVE it is grid-based.

    Returns a transformer for which `transform(lon, lat, h)` yields orthometric
    height H = h - N. Raises StepFailed if PROJ would silently hand back the
    ballpark (no-op) vertical transform.
    """
    tr = pyproj.Transformer.from_crs("EPSG:4979", geoid_crs, always_xy=True)
    _, _, ortho = tr.transform(_PROBE_LON, _PROBE_LAT, 0.0)
    n_probe = -float(ortho)
    if not np.isfinite(n_probe) or abs(n_probe - _PROBE_N) > _PROBE_TOL:
        raise StepFailed(
            "the EGM2008 vertical transform is NOT using a real geoid grid.\n"
            f"  probe at ({_PROBE_LON}, {_PROBE_LAT}): got N = {n_probe:.4f} m, "
            f"expected {_PROBE_N:.3f} +/- {_PROBE_TOL} m\n"
            "  N == 0 means PROJ fell back to its BALLPARK vertical transform, which\n"
            "  returns the input height unchanged. The mask would then be the naive\n"
            "  ellipsoidal threshold -- silently wrong, exactly like the sardem bug\n"
            "  this stage exists to route around.\n"
            "  Fix (downloads ~80 MB once, then works fully offline):\n"
            "      conda activate isce3_env\n"
            "      python -m pyproj sync --file us_nga_egm08_25\n"
            "  Verify:\n"
            "      ls ~/.local/share/proj/us_nga_egm08_25.tif"
        )
    log.info(f"  geoid grid verified: EGM2008 N(0,0) = {n_probe:.4f} m")
    return tr


def _geoid_undulation(transform, crs, height, width, block_rows, tr, log):
    """N (geoid height above the ellipsoid) on the target grid, in row blocks."""
    to_ll = pyproj.Transformer.from_crs(crs, "EPSG:4979", always_xy=True)
    n_grid = np.empty((height, width), dtype="float32")
    cols = np.arange(width)
    for r0 in range(0, height, block_rows):
        r1 = min(r0 + block_rows, height)
        rr, cc = np.meshgrid(np.arange(r0, r1), cols, indexing="ij")
        x, y = rasterio.transform.xy(transform, rr, cc)
        lon, lat, _ = to_ll.transform(np.asarray(x), np.asarray(y),
                                      np.zeros(np.asarray(x).shape))
        _, _, ortho0 = tr.transform(lon, lat, np.zeros_like(lon))
        n_grid[r0:r1] = -np.asarray(ortho0, dtype="float32").reshape(r1 - r0, width)
    log.info(f"  geoid undulation N over grid: {n_grid.min():.2f} .. {n_grid.max():.2f} m")
    return n_grid



def _derive_reference(cfg: Config, log: Logger) -> Path | None:
    """Locate this run's interferogram amplitude raster to use as the mask grid.

    Follows cfg.igram_freq / cfg.igram_pol rather than a config literal, so the
    mask is always built on the grid actually being processed.
    """
    pairs_dir = cfg.root / "pairs"
    if not pairs_dir.is_dir():
        return None
    name = f"ifg_{cfg.igram_freq}_{cfg.igram_pol}.amp.tif"
    hits = sorted(pairs_dir.glob(f"*/trackG/{name}"))
    if not hits:
        return None
    if len(hits) > 1:
        log.warn(f"  {len(hits)} candidate reference rasters; using {hits[0]}")
    log.info(f"  reference grid derived from {hits[0].relative_to(cfg.root)}")
    return hits[0]

def run(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    started = time.time()
    wm = cfg.watermask
    out_dir = cfg.root / "aux" / "watermask"
    out_path = out_dir / f"watermask_{cfg.case_name}.tif"

    ref_path = Path(wm.reference_raster) if wm.reference_raster else None
    if ref_path is None:
        # Derive from the interferogram this run is actually forming. Hardcoding
        # it in the config goes stale the moment frequency, polarization or the
        # date pair changes -- and the failure is a confusing "file not found"
        # pointing at a frequency you are not processing.
        ref_path = _derive_reference(cfg, log)
    if ref_path is None:
        raise StepFailed(
            "watermask.reference_raster is not set and could not be derived.\n"
            "  The mask is built on the grid of an existing product so it is\n"
            "  pixel-aligned by construction. Run the igram stage first, or set\n"
            "  it explicitly:\n"
            "    watermask:\n"
            f"      reference_raster: pairs/<ref>_<sec>/trackG/ifg_{cfg.igram_freq}_{cfg.igram_pol}.amp.tif"
        )
    if not ref_path.is_absolute():
        ref_path = cfg.root / ref_path
    dem_path = cfg.dem_path

    if dry_run:
        return Result(stage="watermask", skipped=True, notes=[
            f"would build {wm.method} mask on the grid of {ref_path}",
            f"would write {out_path}",
            f"threshold: orthometric H <= {wm.sea_level_margin_m} m "
            f"(via {wm.geoid_crs}); amplitude fallback <= {wm.amplitude_db} dB",
        ])
    if out_path.exists() and not force:
        return Result(stage="watermask", skipped=True, outputs=[str(out_path)],
                      notes=[f"{out_path.name} exists; --force to rebuild"])

    for p, what in ((ref_path, "reference raster"), (dem_path, "DEM")):
        if not p.exists():
            raise StepFailed(f"{what} not found: {p}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- target grid, taken verbatim from the reference product ----------
    with rasterio.open(ref_path) as src:
        prof = src.profile.copy()
        H, W, T, CRS = src.height, src.width, src.transform, src.crs
        ref = src.read(1)
        ref_nodata = src.nodata
    log.info(f"  grid {H} x {W}  {CRS}  from {ref_path.name}")

    valid = np.isfinite(ref) if np.issubdtype(ref.dtype, np.floating) else np.ones(ref.shape, bool)
    if ref_nodata is not None and not (isinstance(ref_nodata, float) and np.isnan(ref_nodata)):
        valid &= ref != ref_nodata
    log.info(f"  in-swath (valid) pixels: {valid.mean() * 100:.2f}%")

    # ---- DEM onto that grid ---------------------------------------------
    t0 = time.time()
    dem = np.full((H, W), np.nan, dtype="float32")
    with rasterio.open(dem_path) as src:
        reproject(rasterio.band(src, 1), dem, dst_transform=T, dst_crs=CRS,
                  src_nodata=src.nodata, dst_nodata=np.nan,
                  resampling=Resampling.average, num_threads=8)
    log.info(f"  DEM resampled in {fmt_s(time.time() - t0)}: "
             f"{np.nanmin(dem):.1f} .. {np.nanmax(dem):.1f} m ellipsoidal")

    # ---- ellipsoidal -> orthometric, with the anti-fallback guard --------
    tr = _geoid_transformer(wm.geoid_crs, log)
    n_grid = _geoid_undulation(T, CRS, H, W, wm.block_rows, tr, log)
    ortho = dem - n_grid
    log.info(f"  orthometric H: {np.nanmin(ortho):.1f} .. {np.nanmax(ortho):.1f} m")

    # ---- the mask --------------------------------------------------------
    void = ~np.isfinite(dem)
    if wm.method == "dem_orthometric":
        water = (ortho <= wm.sea_level_margin_m) | void      # course semantics: sea OR void
    elif wm.method == "amplitude":
        water = _amplitude_water(ref, valid, wm, log) | void
    else:
        raise StepFailed(f"unknown watermask.method: {wm.method!r}")

    if wm.include_inland:
        water |= _inland_water(ref, ortho, valid, wm, log)

    mask = np.where(water, WATER, LAND).astype("uint8")
    mask[~valid] = OUTSIDE

    frac = water[valid].mean()
    log.info(f"  water fraction over the swath: {frac * 100:.2f}%")

    # ---- refuse to emit a degenerate mask (the sardem failure) -----------
    if not (wm.min_water_fraction <= frac <= wm.max_water_fraction):
        raise StepFailed(
            f"water fraction {frac * 100:.3f}% is outside the sane band "
            f"[{wm.min_water_fraction * 100:.1f}%, {wm.max_water_fraction * 100:.1f}%].\n"
            "  An all-land or all-water mask is the signature of a broken source --\n"
            "  it is what the NASADEM route produced silently. Refusing to write it."
        )
    if wm.ocean_probe:
        _assert_ocean(mask, T, wm.ocean_probe, log)

    prof.update(dtype="uint8", count=1, nodata=OUTSIDE, compress="deflate",
                tiled=True, zlevel=6)
    with rasterio.open(out_path, "w", **prof) as dst:
        dst.write(mask, 1)
        dst.update_tags(WATER=str(WATER), LAND=str(LAND), OUTSIDE_SWATH=str(OUTSIDE),
                        method=wm.method, sea_level_margin_m=str(wm.sea_level_margin_m),
                        geoid=wm.geoid_crs, water_fraction=f"{frac:.6f}",
                        note="orthometric threshold; DEM is WGS84 ellipsoidal, "
                             "converted via EGM2008 before thresholding")
    log.info(f"  wrote {out_path}")

    write_sidecar(cfg.prov_dir / "watermask.json", "watermask",
                  inputs={"reference_raster": str(ref_path), "dem": str(dem_path)},
                  outputs={"watermask": str(out_path)},
                  parameters={"method": wm.method, "geoid_crs": wm.geoid_crs,
                              "sea_level_margin_m": wm.sea_level_margin_m,
                              "include_inland": wm.include_inland},
                  started=started,
                  extra={"water_fraction": round(float(frac), 6),
                         "geoid_N_min": round(float(n_grid.min()), 3),
                         "geoid_N_max": round(float(n_grid.max()), 3),
                         "valid_fraction": round(float(valid.mean()), 6)})
    return Result(stage="watermask", outputs=[str(out_path)],
                  metrics={"water_fraction": round(float(frac), 6),
                           "geoid_N_range_m": [round(float(n_grid.min()), 2),
                                               round(float(n_grid.max()), 2)]},
                  notes=[f"{frac * 100:.2f}% water over the swath"])


def _to_db(amp: np.ndarray) -> np.ndarray:
    """
    True dB from an AMPLITUDE (magnitude) raster: 20*log10, not 10*log10.

    gslc_igram.py writes `amp = sqrt(sqrt(p1*p2)/n)`, a magnitude. Feeding a
    magnitude to 10*log10 halves every dB value -- the origin of the -6.48 dB
    figure floating around this case, whose true-dB equivalent is -12.96 dB.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        return 20.0 * np.log10(np.maximum(amp, 1e-12))


def _amplitude_water(amp, valid, wm, log):
    db = _to_db(amp)
    if wm.amplitude_db is not None:
        thr = float(wm.amplitude_db)
    elif wm.amplitude_percentile is not None:
        thr = float(np.percentile(db[valid], wm.amplitude_percentile))
        log.info(f"  amplitude p{wm.amplitude_percentile} -> {thr:.2f} dB")
    else:
        raise StepFailed("method 'amplitude' needs watermask.amplitude_db or "
                         "watermask.amplitude_percentile")
    log.info(f"  amplitude threshold {thr:.2f} dB (20*log10 of magnitude)")
    return (db <= thr) & valid


def _inland_water(amp, ortho, valid, wm, log):
    """
    Dark AND well above sea level => inland water body. Speckle-cleaned.

    The elevation term is what makes this safe: it cannot touch the coastline,
    which the DEM term already owns, so a false positive here costs an inland
    lake edge rather than a kilometre of coast.
    """
    # Implemented directly rather than via skimage.morphology.remove_small_objects:
    # that function is mid-deprecation (0.26 renames min_size -> max_size AND
    # flips the comparison from `<` to `<=`), so the same call would silently
    # change which blobs survive across versions. Speckle rejection is exactly
    # the wrong place for a silently version-dependent threshold.
    from scipy import ndimage

    cand = (_to_db(amp) <= wm.inland_db) & valid & (ortho > wm.inland_min_elev_m)
    labels, n = ndimage.label(cand)
    if n:
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        cleaned = (sizes >= wm.inland_min_area_px)[labels]
    else:
        cleaned = np.zeros_like(cand)
    log.info(f"  inland water: {cand.sum()} candidate px in {n} blobs -> "
             f"{cleaned.sum()} px after dropping blobs < {wm.inland_min_area_px} px")
    return cleaned


def _assert_ocean(mask, transform, box, log):
    """
    Assert a box known to be open ocean really came out as water.

    The fraction is taken over IN-SWATH pixels only. Measuring over the raw box
    instead conflates "this mask is wrong" with "this box hangs off the corner
    of a diagonal swath", and the swath here is a rotated parallelogram inside a
    north-up bounding box, so a plausible-looking Caribbean box can be half
    OUTSIDE. That mistake was made once already while writing this stage: a box
    that was 99.95% water over its valid pixels reported 48.96% and tripped the
    assertion. A separate coverage check keeps the two failures distinguishable.
    """
    x0, y0, x1, y1 = box
    inv = ~transform
    c0, r0 = inv * (min(x0, x1), max(y0, y1))
    c1, r1 = inv * (max(x0, x1), min(y0, y1))
    sub = mask[int(r0):int(r1), int(c0):int(c1)]
    if sub.size == 0:
        raise StepFailed(f"watermask.ocean_probe {box} falls outside the grid")

    in_swath = sub != OUTSIDE
    cover = in_swath.mean()
    if cover < 0.5:
        raise StepFailed(
            f"watermask.ocean_probe {box} is only {cover * 100:.1f}% inside the radar\n"
            "  swath, too little to test anything. The swath is a rotated parallelogram\n"
            "  inside a north-up bounding box -- move the probe box toward the centre."
        )
    frac = (sub[in_swath] == WATER).mean()
    log.info(f"  ocean probe box: {frac * 100:.2f}% water over {in_swath.sum()} "
             f"in-swath px ({cover * 100:.1f}% of the box)")
    if frac < 0.95:
        raise StepFailed(
            f"ocean probe box {box} is only {frac * 100:.2f}% water over its in-swath\n"
            "  pixels; expected >95%. A box declared to be open ocean did not come out\n"
            "  as water -- the mask is not trustworthy. This is the assertion the README\n"
            "  demands of any water-mask source, precisely because the broken NASADEM\n"
            "  route returned an all-land mask while exiting 0."
        )
