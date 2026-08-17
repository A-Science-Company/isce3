"""
Stage G4 -- Goldstein filter, phase-sigma coherence, water mask, SNAPHU unwrap.

A faithful port of the isce+ course chain (utils.py, Zhenli Tang / Zhang Yunjun,
July 2026) in the course's own order:

    multilook            <- already done, in stage G3
    Goldstein filter     -> <prefix>.filt.int.tif
    phase-sigma coh      -> <prefix>.filt.phsig.coh.tif      FROM THE FILTERED IFG
    water mask           -> zeroed INTO the interferogram, not passed to snaphu
    snaphu.unwrap        -> <prefix>.filt.unw.tif + .filt.unw.conncomp.tif

THREE ORDERING FACTS THAT ARE EASY TO GET WRONG
-----------------------------------------------
1. phsig is computed from the FILTERED interferogram, never the raw multilooked
   one. This is deliberate in the course and it inflates phsig relative to a
   true coherence -- Goldstein filtering reduces local phase variance, and phsig
   is *defined* as an inversion of phase variance. Do not "improve" it by
   feeding it the unfiltered interferogram: snaphu's cost model was tuned
   against this convention in ISCE2.

2. phsig is computed BEFORE water masking, on the unmasked filtered field. The
   mask never touches the correlation array.

3. The water mask is applied by ZEROING THE INTERFEROGRAM, not via snaphu's
   `mask=` parameter -- the course is explicit about this ("invalid regions are
   zeroed in igram, no separate mask needed"). Masked pixels therefore reach
   snaphu as zero-magnitude complex paired with a NONZERO correlation. That is
   faithful; switching to `mask=` changes connected-component labelling.

WHICH COHERENCE FEEDS SNAPHU
----------------------------
The PHASE-SIGMA coherence, not the boxcar/multilook coherence. Stage G3's
`.coh.tif` (the |sum s1 conj s2| / sqrt(...) estimator, land median ~0.43 here)
is the DISPLAY and QA product; the course never unwraps with it. Both course
drivers pass `filt_mli.phsig.coh.tif` as `corr`, so this stage generates phsig
from the filtered interferogram and feeds that.

THE nlooks ASYMMETRY -- THE THING THAT SILENTLY BREAKS ON A PORT
-----------------------------------------------------------------
The two "looks" numbers are different quantities and the course feeds them
differently:

    phsig  gets the NOMINAL look count, uncorrected      nlks   = ry * rx
    snaphu gets the EFFECTIVE (1.44-corrected) count     nlooks = ry * rx / 1.2^2

The 1.2 per dimension is the ISCE2 convention (contrib/stack/topsStack/unwrap.py)
for turning a nominal look count into an equivalent number of INDEPENDENT looks:
an SLC is oversampled ~20% relative to its true resolution in each dimension, so
adjacent samples are correlated and a nominal N-sample average does not deliver
N independent samples. snaphu uses nlooks only inside its statistical cost
model, to map correlation onto expected phase variance -- understating it makes
snaphu trust the coherence less and produce a smoother, more conservative
solution, so the /1.44 errs in the safe direction.

For the course's 4 x 2 configuration those two numbers are 8 and 5.56 -- and
`generate_phsig_coh_tif`'s DEFAULT nlks is also 8, so the stack notebook can omit
it and still be correct BY COINCIDENCE. At our 16 x 2 = 32 looks that coincidence
breaks: anyone copying the notebook's bare call inherits a silent nlks=8 and
badly overestimates coherence. Both numbers are therefore passed explicitly here
and recorded in the provenance sidecar.

One caveat written down rather than silently inherited: the 1.2 oversampling
factor is a property of a SLANT-RANGE SLC. Our inputs are geocoded GSLCs on a
5 x 40 m posting, where inter-pixel correlation is set by the geocoding
resampling kernel, not by the original range/azimuth oversampling. 1.44 is not
derived for this geometry. It is kept because it errs conservative and because
it agrees with the measurement: 32/1.44 = 22.2, and the observed water coherence
floor (0.175-0.19) inverts through E[|gamma|] = sqrt(pi)/(2 sqrt(L)) to L ~= 22.
Two independent routes to the same number.

DELIBERATE DIVERGENCES FROM THE COURSE CODE
--------------------------------------------
Everything below changes behaviour and is therefore listed rather than buried:

* NaN is sanitised to 0 BEFORE filtering. The course's nodata test is
  `complex_arr == no_data_value`, and `nan == 0` is False, so a single NaN
  poisons its whole 32x32 FFT patch and, through the 50% overlap, a 48x48
  neighbourhood. Our grid is a rotated parallelogram in a north-up box with
  ~33% NaN, so this is not a corner case here -- it would wreck the frame edge.
* `estimate_phsig_correlation` batch size is raised from 500 to 5000, and the
  pixel-index arrays are built with repeat/tile as int32 instead of a pair of
  int64 meshgrids. Both are pure memory/speed changes with NO numerical effect
  (identical iteration order, identical arithmetic).
* The connected-component filename is built from the prefix, not by
  `str.replace('.unw.tif', ...)`. In the course, an output not ending in
  '.unw.tif' makes that replace a no-op and THE CONNCOMP OVERWRITES THE
  UNWRAPPED PHASE.
* conncomp is written UInt16 (the course's stack path) rather than Int32 (its
  notebook path); the two course drivers disagree and UInt16 is the leaner.
  MintPy reads either.
* GeoTIFFs are written DEFLATE-compressed and tiled with an explicit nodata.
  The course helper passes no creation options at all.
* The water-mask PRODUCER is substituted -- see below. The consumer contract
  (warp a categorical mask onto the target grid, nearest neighbour) is kept.

WHY THE WATER MASK IS SUBSTITUTED
----------------------------------
`download_nasadem_water_mask` is unusable here and dangerous in a specific way:
it pre-fills its mosaic with 255 = WATER and `continue`s past any tile that
404s or errors. The LP DAAC `lp-prod-protected` route needs a token-bearing
redirect that plain `requests` basic auth often does not satisfy, so the failure
mode is that EVERY tile 404s, the function exits 0, and it writes a 100%-water
mask -- which would then mask the entire scene at unwrap time. Its classifier is
independently wrong for us too: `h <= 0` is an orthometric test applied to our
ellipsoidal DEM, and `(h >> 15) & 1` is just the int16 sign bit, not a water flag.

Stage `watermask` (step 7) replaces the producer with a DEM-orthometric
threshold, with an amplitude threshold as fallback. This stage consumes its
product when present and otherwise builds the same thing in memory, and in
either case asserts a plausible water fraction BEFORE the mask is allowed
anywhere near snaphu.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from scipy import ndimage

from .config import Config
from .igram import CREATE_OPTS, pair_list, pair_paths
from .ingest import load_stack
from .util import Logger, Result, StepFailed, fmt_s, write_sidecar


# ==========================================================================
# Goldstein adaptive phase filter -- utils.py:1185-1250
# ==========================================================================
def goldstein_filter(complex_arr: np.ndarray, alpha: float = 0.5, psize: int = 32,
                     no_data_value=None) -> np.ndarray:
    """
    Weighted overlap-add Goldstein-Werner filter. Returns complex64.

    Patch `psize` with `step = psize//2` gives 50% overlap, so every interior
    pixel is covered by exactly four patches. The taper is a separable
    TRIANGULAR (pyramid) window applied only AFTER the inverse FFT and then
    divided out by the accumulated weight -- a true weighted overlap-add, exact
    rather than relying on the COLA property of the window.

    Two properties of the course kernel worth knowing before reading the output:

    * `H = |S|**alpha; S = H*S` multiplies the spectrum by its own magnitude
      raised to alpha. The power spectrum is NOT smoothed first (no boxcar or
      Gaussian on |S|), unlike the original Goldstein-Werner and unlike ISCE2's
      Filter.py.
    * There is no magnitude renormalisation, so output MAGNITUDE is inflated by
      roughly |S|^alpha. Only the PHASE of this product is meaningful. Every
      consumer here takes np.angle() or hands it to snaphu, which cares about
      relative magnitude within a patch, so that is fine -- but do not treat
      `.filt.int.tif` amplitude as backscatter.

    Nodata: zeroed inside each patch before the FFT and restored to exact 0+0j
    after cropping. Note that `norm` accumulates weight at nodata pixels too
    while the signal there is zeroed, so valid pixels within ~psize/2 of a
    nodata edge are attenuated toward zero -- expect an amplitude roll-off
    collar around the swath border. That is the course's behaviour, kept.
    """
    orig_rows, orig_cols = complex_arr.shape
    pad = psize // 2
    step = pad
    half = pad

    wx = (1.0 - np.abs(np.arange(half) - (psize / 2.0 - 1.0)) / (psize / 2.0 - 1.0))
    wy = (1.0 - np.abs(np.arange(half) - (psize / 2.0 - 1.0)) / (psize / 2.0 - 1.0))
    q = np.outer(wy, wx)
    wf = np.block([[q, np.flip(q, 1)],
                   [np.flip(q, 0), np.flip(np.flip(q, 0), 1)]])

    nodata_mask = (complex_arr == no_data_value)

    padded = np.pad(complex_arr, ((pad, pad), (pad, pad)), mode="constant")
    p_rows, p_cols = padded.shape
    nodata = np.pad(nodata_mask, ((pad, pad), (pad, pad)),
                    mode="constant", constant_values=True)

    filtered = np.zeros((p_rows, p_cols), dtype=np.complex64)
    norm = np.zeros((p_rows, p_cols), dtype=np.float32)

    for i in range(0, p_rows - psize + 1, step):
        for j in range(0, p_cols - psize + 1, step):
            ri, rj = slice(i, i + psize), slice(j, j + psize)
            patch = padded[ri, rj].copy()

            if np.all(nodata[ri, rj]):
                continue

            patch[nodata[ri, rj]] = 0
            S = np.fft.fft2(patch, s=(psize, psize))
            H = np.power(np.abs(S), alpha)
            S = H * S
            pf = np.fft.ifft2(S, s=(psize, psize))

            w = wf[:patch.shape[0], :patch.shape[1]]
            filtered[ri, rj] += pf * w
            norm[ri, rj] += w

    valid = norm > 0
    filtered[valid] /= norm[valid]
    filtered = filtered[pad:pad + orig_rows, pad:pad + orig_cols]
    filtered[nodata_mask] = 0 + 0j
    return filtered


# ==========================================================================
# phase-sigma correlation -- utils.py:1258-1406 (ISCE2 ph_slope.F + ph_sigma.F)
# ==========================================================================
def _gaussian_kernel(size: int) -> np.ndarray:
    """
    Normalised 2-D Gaussian, exp(-r^2 / (size/2)), matching ISCE2's Fortran.

    The effective sigma^2 is size/4; the docstring convention in ph_slope.F
    names the DENOMINATOR sigma^2, which is not sigma^2 in the standard form.
    """
    half = size // 2
    s1 = 0.0
    kernel = np.zeros((size, size), dtype=np.float64)
    for k in range(size):
        for j in range(size):
            w1 = (k - half) ** 2 + (j - half) ** 2
            kernel[k, j] = np.exp(-w1 / (size / 2.0))
            s1 += kernel[k, j]
    return (kernel / s1).astype(np.float32)


def estimate_phsig_correlation(ifg_arr: np.ndarray, ps_win: int = 5, grad_win: int = 5,
                               nlks: float = 3.0, batch_size: int = 5000,
                               log: Logger | None = None) -> np.ndarray:
    """
    Phase-sigma correlation ("phsig.cor" in topsApp), clipped to [0, 1], float32.

    1. Local fringe rate. Range gradient ifg[i,j]*conj(ifg[i,j-1]) and azimuth
       gradient ifg[i,j]*conj(ifg[i-1,j]) on a zero-padded copy, Gaussian-smoothed
       as COMPLEX products (the correct circular average) before atan2 -> slope
       in rad/pixel.
    2. Border zeroing reproducing the Fortran valid-index range.
    3. Deramp each ps_win x ps_win window by exp(-i*ramp), take the UNWEIGHTED
       circular mean as the phase reference, rotate it out, then take a
       GAUSSIAN-WEIGHTED mean and mean-square of the residual phase ->
       var = <phi^2> - <phi>^2. The asymmetry (unweighted reference, weighted
       variance) is faithful to the Fortran.
    4. coh = 1/sqrt(2*nlks*var + 1), the inversion of sigma_phi^2 = (1-g^2)/(2Ng^2).
       var <= 0 -> coh = 1. |sum| <= 1e-10 -> left at 0.

    A ps_half-wide border (2 px at ps_win=5) is left at exactly 0 and never
    computed, which reads to snaphu as zero coherence at the frame edge.
    """
    rows, cols = ifg_arr.shape

    if ps_win % 2 == 0:
        ps_win += 1
    if grad_win % 2 == 0:
        grad_win += 1
    ps_half = ps_win // 2
    grad_half = grad_win // 2

    padded = np.pad(ifg_arr, ((grad_half, grad_half), (grad_half, grad_half)),
                    mode="constant")

    rg_diff = (padded[grad_half:grad_half + rows, grad_half:grad_half + cols] *
               np.conj(padded[grad_half:grad_half + rows,
                              grad_half - 1:grad_half + cols - 1]))
    az_diff = (padded[grad_half:grad_half + rows, grad_half:grad_half + cols] *
               np.conj(padded[grad_half - 1:grad_half + rows - 1,
                              grad_half:grad_half + cols]))
    del padded

    gk = _gaussian_kernel(grad_win)
    rg_smooth = ndimage.correlate(rg_diff, gk)
    az_smooth = ndimage.correlate(az_diff, gk)
    del rg_diff, az_diff

    rg_slope = np.arctan2(rg_smooth.imag, rg_smooth.real)
    az_slope = np.arctan2(az_smooth.imag, az_smooth.real)
    rg_slope[np.abs(rg_smooth) == 0] = 0.0
    az_slope[np.abs(az_smooth) == 0] = 0.0
    del rg_smooth, az_smooth

    # Fortran ph_slope.F computes slopes only for [half+1, size-half-1]
    if grad_half > 0:
        for arr in (rg_slope, az_slope):
            arr[:grad_half + 1, :] = 0.0
            arr[-(grad_half):, :] = 0.0
            arr[:, :grad_half + 1] = 0.0
            arr[:, -(grad_half):] = 0.0

    offsets = np.arange(-ps_half, ps_half + 1)
    di_mesh, dj_mesh = np.meshgrid(offsets, offsets, indexing="ij")
    ps_weights = _gaussian_kernel(ps_win)

    coh = np.zeros((rows, cols), dtype=np.float32)

    i_idx = np.arange(ps_half, rows - ps_half)
    j_idx = np.arange(ps_half, cols - ps_half)
    # repeat/tile is exactly meshgrid(indexing='ij').ravel(), at int32 and
    # without materialising two int64 (rows x cols) arrays first.
    i_flat = np.repeat(i_idx, len(j_idx)).astype(np.int32)
    j_flat = np.tile(j_idx, len(i_idx)).astype(np.int32)

    n_total = len(i_flat)
    t0 = time.time()
    n_batches = (n_total + batch_size - 1) // batch_size

    for bn, b_start in enumerate(range(0, n_total, batch_size)):
        b_end = min(b_start + batch_size, n_total)
        bi = i_flat[b_start:b_end]
        bj = j_flat[b_start:b_end]

        row_idx = bi[:, None, None] + di_mesh[None, :, :]
        col_idx = bj[:, None, None] + dj_mesh[None, :, :]
        windows = ifg_arr[row_idx, col_idx]

        rg_s = rg_slope[bi, bj]
        az_s = az_slope[bi, bj]
        ramp = (di_mesh[None, :, :] * az_s[:, None, None] +
                dj_mesh[None, :, :] * rg_s[:, None, None])

        exp_ramp = np.cos(ramp) - 1j * np.sin(ramp)
        comp = windows * exp_ramp

        wsum = np.sum(comp, axis=(1, 2))
        mag = np.abs(wsum)

        valid = mag > 1e-10
        if not np.any(valid):
            continue
        vidx = np.flatnonzero(valid)

        norm_sum = wsum[vidx] / mag[vidx]
        deramped = comp[vidx] * np.conj(norm_sum[:, None, None])

        phases = np.arctan2(deramped.imag, deramped.real)
        wt = ps_weights[None, :, :]
        mean_ph = np.sum(wt * phases, axis=(1, 2))
        mean_ph2 = np.sum(wt * phases * phases, axis=(1, 2))
        var = mean_ph2 - mean_ph * mean_ph

        var_pos = var > 0
        if np.any(var_pos):
            gidx = vidx[var_pos]
            coh[bi[gidx], bj[gidx]] = 1.0 / np.sqrt(2.0 * nlks * var[var_pos] + 1.0)
        if np.any(~var_pos):
            gidx = vidx[~var_pos]
            coh[bi[gidx], bj[gidx]] = 1.0

        if log is not None and (bn % 400 == 0 or bn == n_batches - 1):
            log.info(f"    phsig batch {bn + 1}/{n_batches}  ({fmt_s(time.time() - t0)})")

    return np.clip(coh, 0.0, 1.0)


# ==========================================================================
# water mask -- substitute producer, course consumer contract
# ==========================================================================
def water_mask_on_grid(cfg: Config, log: Logger, transform, crs, shape,
                       valid: np.ndarray, amp: np.ndarray) -> tuple[np.ndarray, dict]:
    """
    Water mask on the interferogram grid: 1 = water. Returns (mask, provenance).

    Route order, all three of which end in the same contract the course's
    `load_water_mask` provides -- a categorical mask resampled onto the target
    grid with NEAREST NEIGHBOUR:

      1. the stage-7 `watermask` product, if it exists (warped to this grid if
         it was built on a different one);
      2. PRIMARY -- DEM converted to ORTHOMETRIC height and thresholded, which
         is what stage 7 would have written;
      3. FALLBACK -- amplitude threshold, which needs no ancillary data at all.

    The primary route's geoid transform is probed against a known EGM2008 value
    before it is trusted. PROJ does not fail when its geoid grid is missing; it
    falls back to a "ballpark vertical transformation" that returns the height
    UNCHANGED, which would silently degrade this into the naive ellipsoidal
    threshold. That is the same class of silent-wrong-answer bug as the NASADEM
    route this whole substitution exists to avoid, so it raises instead.
    """
    import rasterio
    from rasterio.warp import Resampling, reproject

    from .watermask import (OUTSIDE, WATER, _amplitude_water, _geoid_transformer,
                            _geoid_undulation)

    H, W = shape
    wm = cfg.watermask
    prod = cfg.root / "aux" / "watermask" / f"watermask_{cfg.case_name}.tif"

    # ---- 1. the stage-7 product -----------------------------------------
    if prod.exists():
        with rasterio.open(prod) as src:
            if (src.width, src.height) == (W, H) and src.crs == crs \
                    and np.allclose(np.asarray(src.transform)[:6],
                                    np.asarray(transform)[:6], atol=1e-6):
                m = src.read(1)
                log.info(f"  water mask: reusing {prod.name} (already on this grid)")
            else:
                m = np.full((H, W), OUTSIDE, dtype="uint8")
                reproject(rasterio.band(src, 1), m, dst_transform=transform,
                          dst_crs=crs, src_nodata=OUTSIDE, dst_nodata=OUTSIDE,
                          resampling=Resampling.nearest)
                log.info(f"  water mask: warped {prod.name} onto this grid "
                         f"(nearest neighbour, categorical)")
        return (m == WATER), {"source": "watermask stage product", "path": str(prod)}

    # ---- 2. primary: DEM -> orthometric ---------------------------------
    log.info(f"  water mask: no stage product at {prod}; building in memory")
    if wm.method == "dem_orthometric":
        dem_path = cfg.dem_path
        if dem_path.exists():
            try:
                dem = np.full((H, W), np.nan, dtype="float32")
                with rasterio.open(dem_path) as src:
                    reproject(rasterio.band(src, 1), dem, dst_transform=transform,
                              dst_crs=crs, src_nodata=src.nodata, dst_nodata=np.nan,
                              resampling=Resampling.average, num_threads=8)
                tr = _geoid_transformer(wm.geoid_crs, log)
                n_grid = _geoid_undulation(transform, crs, H, W, wm.block_rows, tr, log)
                ortho = dem - n_grid
                water = (ortho <= wm.sea_level_margin_m) | ~np.isfinite(dem)
                log.info(f"  orthometric threshold H <= {wm.sea_level_margin_m} m")
                return water, {"source": "dem_orthometric (in memory)",
                               "dem": str(dem_path), "geoid_crs": wm.geoid_crs,
                               "sea_level_margin_m": wm.sea_level_margin_m}
            except StepFailed as exc:
                log.warn(f"primary water-mask route failed: {exc}".splitlines()[0])
                log.warn("falling back to the amplitude threshold")
        else:
            log.warn(f"DEM not found at {dem_path}; falling back to the amplitude threshold")

    # ---- 3. fallback: amplitude -----------------------------------------
    if wm.amplitude_db is None and wm.amplitude_percentile is None:
        raise StepFailed(
            "the water-mask fallback needs a threshold, and none is configured.\n"
            "  Either run the watermask stage first:\n"
            "    python run_track_g.py --config <cfg> --only watermask\n"
            "  or set watermask.amplitude_db (TRUE dB = 20*log10 of magnitude;\n"
            "  a sea/land split on this scene sits near -13 dB), or disable\n"
            "  masking with unwrap.water_mask: false."
        )
    water = _amplitude_water(amp, valid, wm, log)
    return water, {"source": "amplitude threshold (fallback)",
                   "amplitude_db": wm.amplitude_db,
                   "amplitude_percentile": wm.amplitude_percentile}


# ==========================================================================
# raster IO
# ==========================================================================
def _write_tif(path: Path, arr: np.ndarray, gt, proj, gdal_dtype, nodata=None) -> None:
    """
    Write one band with an EXPLICIT GDAL dtype.

    The course's `save_tiff` auto-maps numpy dtypes and lets anything unmapped
    (complex128, float16, int64, bool) fall through to Float32 silently, which
    is a real corruption path. Here the caller states the type.
    """
    from osgeo import gdal

    gdal.UseExceptions()
    drv = gdal.GetDriverByName("GTiff")
    r = drv.Create(str(path), int(arr.shape[1]), int(arr.shape[0]), 1,
                   gdal_dtype, CREATE_OPTS)
    r.SetGeoTransform(gt)
    r.SetProjection(proj)
    r.GetRasterBand(1).WriteArray(arr)
    if nodata is not None:
        r.GetRasterBand(1).SetNoDataValue(float(nodata))
    r.FlushCache()
    r = None


def unwrap_paths(cfg: Config, ref: str, sec: str) -> dict:
    """
    Output paths for one pair.

    The `.filt.` infix mirrors the course's `filt_mli.*` naming and keeps these
    products distinct from any earlier hand-run unwrap: this chain's input is the
    FILTERED interferogram and its correlation is phsig, so it is a different
    product from an unwrap driven by the boxcar coherence.

    The conncomp name is CONSTRUCTED from the prefix rather than produced by
    `str.replace('.unw.tif', ...)`. In the course, an output path not ending in
    '.unw.tif' makes that replace a no-op and the conncomp silently overwrites
    the unwrapped phase.
    """
    p = pair_paths(cfg, ref, sec)
    pre = p["prefix"]
    return {
        "dir": p["dir"],
        "igram": p["igram"],
        "coh": p["coh"],
        "amp": p["amp"],
        "filt": Path(f"{pre}.filt.int.tif"),
        "phsig": Path(f"{pre}.filt.phsig.coh.tif"),
        "unw": Path(f"{pre}.filt.unw.tif"),
        "conncomp": Path(f"{pre}.filt.unw.conncomp.tif"),
    }


def expected_outputs(cfg: Config, ref: str, sec: str) -> list[Path]:
    p = unwrap_paths(cfg, ref, sec)
    return [p["filt"], p["phsig"], p["unw"], p["conncomp"]]


# ==========================================================================
# the stage
# ==========================================================================
def unwrap_pair(cfg: Config, log: Logger, ref: str, sec: str) -> dict:
    """Run the full course chain on one pair. Returns measured statistics."""
    import rasterio
    import snaphu
    from osgeo import gdal

    gdal.UseExceptions()
    uc = cfg.unwrap
    p = unwrap_paths(cfg, ref, sec)

    nominal = int(cfg.igram.looks_y) * int(cfg.igram.looks_x)
    nlooks_eff = (uc.nlooks_nominal or nominal) / (uc.oversample_factor ** 2)
    nlks_phsig = float(uc.nlooks_nominal or nominal)

    # ---- read the interferogram -----------------------------------------
    ds = gdal.Open(str(p["igram"]))
    gt, proj = ds.GetGeoTransform(), ds.GetProjection()
    ifg = ds.GetRasterBand(1).ReadAsArray().astype(np.complex64)
    ds = None
    H, W = ifg.shape
    log.info(f"  {p['igram'].name}: {H} x {W}")

    # NaN -> 0 BEFORE anything else. The course's `== no_data_value` test never
    # matches NaN, and a NaN poisons its entire 32x32 FFT patch plus, through the
    # 50% overlap, a 48x48 neighbourhood. A third of this grid is NaN.
    nan_frac = float((~np.isfinite(ifg)).mean())
    ifg[~np.isfinite(ifg)] = 0
    log.info(f"  sanitised {nan_frac * 100:.2f}% non-finite samples to 0+0j "
             f"(the course's nodata test does not catch NaN)")

    # ---- 1. Goldstein ----------------------------------------------------
    t0 = time.time()
    filt = goldstein_filter(ifg, alpha=uc.filter_alpha, psize=uc.filter_psize,
                            no_data_value=0)
    log.info(f"  Goldstein filter alpha={uc.filter_alpha} psize={uc.filter_psize}: "
             f"{fmt_s(time.time() - t0)}")
    _write_tif(p["filt"], filt, gt, proj, gdal.GDT_CFloat32)
    log.info(f"    -> {p['filt'].name}")

    # ---- 2. phase-sigma coherence FROM THE FILTERED IFG ------------------
    t0 = time.time()
    log.info(f"  phase-sigma coherence: ps_win={uc.phsig_win} grad_win={uc.phsig_grad_win} "
             f"nlks={nlks_phsig:g} (NOMINAL looks, not the 1.44-corrected count)")
    phsig = estimate_phsig_correlation(filt, ps_win=uc.phsig_win,
                                       grad_win=uc.phsig_grad_win,
                                       nlks=nlks_phsig, batch_size=uc.phsig_batch,
                                       log=log)
    log.info(f"  phsig in {fmt_s(time.time() - t0)}: "
             f"median {np.median(phsig[phsig > 0]):.4f} over {int((phsig > 0).sum())} px")
    _write_tif(p["phsig"], phsig.astype(np.float32), gt, proj, gdal.GDT_Float32)
    log.info(f"    -> {p['phsig'].name}")

    # ---- 3. water mask ---------------------------------------------------
    with rasterio.open(p["igram"]) as src:
        transform, crs = src.transform, src.crs
    zero_amp = (np.abs(filt) == 0)
    water_prov: dict = {"source": "disabled"}
    water = np.zeros((H, W), dtype=bool)
    if uc.water_mask:
        ds = gdal.Open(str(p["amp"]))
        amp = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
        ds = None
        water, water_prov = water_mask_on_grid(cfg, log, transform, crs, (H, W),
                                               ~zero_amp, amp)
        water = np.asarray(water, dtype=bool)
        # A mask is only counted over pixels that carry data; the swath is a
        # rotated parallelogram and ~a third of the box is outside it.
        in_swath = ~zero_amp
        wfrac = float(water[in_swath].mean()) if in_swath.any() else 0.0
        log.info(f"  water fraction over the swath: {wfrac * 100:.2f}%")
        if wfrac >= 1.0 or wfrac > uc.water_max_fraction:
            raise StepFailed(
                f"water mask covers {wfrac * 100:.2f}% of the swath, above the "
                f"unwrap.water_max_fraction ceiling of {uc.water_max_fraction * 100:.1f}%.\n"
                "  Refusing to unwrap. An all-water or near-all-water mask is the exact\n"
                "  signature of the broken NASADEM producer, which pre-fills its mosaic\n"
                "  with WATER and exits 0 when every tile 404s. Masking the whole scene\n"
                "  would produce an empty unwrapped product with no error anywhere.\n"
                f"  Mask source was: {water_prov.get('source')}"
            )
        water_prov["water_fraction_of_swath"] = round(wfrac, 6)
    else:
        log.info("  water mask: disabled by config (unwrap.water_mask: false)")

    # ---- 4. zero the interferogram at invalid ----------------------------
    invalid = zero_amp | water
    masked_frac = float(invalid.mean())
    log.info(f"  masked pixels: {int(invalid.sum())} ({masked_frac * 100:.2f}% of the grid; "
             f"{int(zero_amp.sum())} outside the swath, {int(water.sum())} water)")

    ifg_msk = filt.copy()
    ifg_msk[invalid] = 0.0 + 0.0j
    # phsig is deliberately NOT zeroed at `invalid` -- the course leaves the
    # correlation untouched and hands snaphu zero-magnitude complex paired with a
    # nonzero correlation. Faithful; changing it changes conncomp labelling.

    # ---- 5. snaphu -------------------------------------------------------
    log.info(f"  snaphu.unwrap nlooks={nlooks_eff:.4f} (= {nominal} nominal / "
             f"{uc.oversample_factor}^2) cost={uc.cost} init={uc.init} "
             f"ntiles={tuple(uc.ntiles)} nproc={uc.nproc}")
    t0 = time.time()
    kwargs = dict(nlooks=float(nlooks_eff), cost=uc.cost, init=uc.init,
                  ntiles=tuple(uc.ntiles), nproc=uc.nproc)
    if tuple(uc.ntiles) != (1, 1):
        kwargs["tile_overlap"] = uc.tile_overlap
        kwargs["single_tile_reoptimize"] = uc.single_tile_reoptimize
    if uc.scratchdir:
        sd = Path(uc.scratchdir)
        if not sd.is_absolute():
            sd = cfg.root / sd
        sd.mkdir(parents=True, exist_ok=True)
        kwargs["scratchdir"] = str(sd)
    try:
        unw, conncomp = snaphu.unwrap(ifg_msk, phsig, **kwargs)
    except Exception as exc:
        raise StepFailed(
            f"snaphu.unwrap failed on {ref}_{sec}: {type(exc).__name__}: {exc}\n"
            f"  grid {H} x {W} = {H * W / 1e6:.1f} Mpx, ntiles={tuple(uc.ntiles)}.\n"
            f"  MEASURED on a 14.2 Mpx grid: a cold single-tile solve holds ~5 GB RSS\n"
            f"  and runs single-threaded for tens of minutes. If the process was killed,\n"
            f"  this box ran out of memory. Set unwrap.ntiles to e.g. [4, 4] with\n"
            f"  unwrap.nproc 8 -- far faster and lighter, but it DIVERGES from the\n"
            f"  course's single-tile settings and changes connected-component labelling."
        ) from exc
    log.info(f"  snaphu finished in {fmt_s(time.time() - t0)}")

    unw = np.asarray(unw, dtype=np.float32)
    conncomp = np.asarray(conncomp)
    unw[invalid] = 0.0
    conncomp[invalid] = 0

    # ---- 6. write --------------------------------------------------------
    # nodata = 0 on the unwrapped phase matches the course's convention of
    # ZEROING masked pixels, and makes the raster display sensibly on its own.
    # It is a display convenience, not the authoritative mask: `conncomp == 0` is
    # what actually means "snaphu could not place this pixel", and a pixel whose
    # unwrapped phase is genuinely 0.0 inside a component would be hidden by this
    # nodata. Every consumer here masks on conncomp, not on unw != 0.
    _write_tif(p["unw"], unw.astype(np.float32), gt, proj, gdal.GDT_Float32, nodata=0.0)
    # snaphu-py returns uint32 labels; the course downcasts to uint16 and that is
    # safe in practice (component counts run to the tens). Assert it rather than
    # assume it -- a silent wraparound would relabel components as each other,
    # which is unrecoverable and looks like a perfectly valid result.
    cc_max = int(conncomp.max()) if conncomp.size else 0
    if cc_max > np.iinfo(np.uint16).max:
        raise StepFailed(
            f"snaphu returned connected-component label {cc_max}, which does not fit "
            f"in the uint16 the course writes.\n"
            f"  Downcasting would wrap labels onto each other silently. Widen the "
            f"conncomp dtype in unwrap.py before using this result."
        )
    cc16 = conncomp.astype(np.uint16)
    _write_tif(p["conncomp"], cc16, gt, proj, gdal.GDT_UInt16, nodata=0)
    log.info(f"    -> {p['unw'].name}, {p['conncomp'].name}")

    # ---- 7. statistics ---------------------------------------------------
    lab, cnt = np.unique(cc16[cc16 > 0], return_counts=True)
    order = np.argsort(cnt)[::-1]
    unwrapped = cc16 > 0
    n_valid = int((~invalid).sum())
    biggest = int(cnt[order[0]]) if len(lab) else 0
    stats = {
        "grid": [H, W],
        "nan_fraction_input": round(nan_frac, 6),
        "masked_fraction": round(masked_frac, 6),
        "water_fraction_of_swath": water_prov.get("water_fraction_of_swath"),
        "n_connected_components": int(len(lab)),
        "largest_component_label": int(lab[order[0]]) if len(lab) else None,
        "largest_component_px": biggest,
        "largest_component_frac_of_valid": round(biggest / n_valid, 6) if n_valid else None,
        "unwrapped_frac_of_valid": round(int(unwrapped.sum()) / n_valid, 6) if n_valid else None,
        "top_components": [(int(lab[i]), int(cnt[i])) for i in order[:5]],
        "nlooks_snaphu": round(float(nlooks_eff), 4),
        "nlks_phsig": nlks_phsig,
        "phsig_median_nonzero": round(float(np.median(phsig[phsig > 0])), 4),
        "water_mask": water_prov,
    }
    if unwrapped.any():
        u = unw[unwrapped]
        stats["unw_range_rad"] = [round(float(u.min()), 4), round(float(u.max()), 4)]
        stats["unw_p2_p98_rad"] = [round(float(v), 4) for v in np.percentile(u, [2, 98])]
        stats["unw_std_rad"] = round(float(u.std()), 4)
    else:
        stats["unw_range_rad"] = None

    log.info(f"  connected components: {stats['n_connected_components']}; "
             f"largest {stats['largest_component_px']} px "
             f"({(stats['largest_component_frac_of_valid'] or 0) * 100:.1f}% of valid)")
    log.info(f"  unwrapped phase range: {stats['unw_range_rad']} rad")
    return stats


def run(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    started = time.time()
    res = Result(stage="unwrap")
    stack = load_stack(cfg)
    uc = cfg.unwrap
    pairs = pair_list(cfg, stack)

    nominal = int(cfg.igram.looks_y) * int(cfg.igram.looks_x)
    nlooks_eff = (uc.nlooks_nominal or nominal) / (uc.oversample_factor ** 2)

    if dry_run:
        for ref, sec in pairs:
            p = unwrap_paths(cfg, ref, sec)
            log.info(f"  would unwrap {ref}_{sec} from {p['igram'].name}")
            log.info(f"      Goldstein alpha={uc.filter_alpha} psize={uc.filter_psize}")
            log.info(f"      phsig ps_win={uc.phsig_win} grad_win={uc.phsig_grad_win} "
                     f"nlks={nominal} (nominal looks)")
            log.info(f"      snaphu nlooks={nlooks_eff:.4f} cost={uc.cost} init={uc.init} "
                     f"ntiles={tuple(uc.ntiles)}")
            log.info(f"      water mask: {'on' if uc.water_mask else 'off'} "
                     f"(method {cfg.watermask.method})")
            for f in expected_outputs(cfg, ref, sec):
                log.info(f"      -> {f}")
        res.skipped = True
        return res

    todo = []
    for ref, sec in pairs:
        want = expected_outputs(cfg, ref, sec)
        if all(f.exists() for f in want) and not force:
            log.info(f"  {ref}_{sec}: all {len(want)} product(s) present -- skipping "
                     f"(--force to rebuild)")
        else:
            todo.append((ref, sec))
    if not todo:
        res.skipped = True
        res.outputs = [str(f) for r, s in pairs for f in expected_outputs(cfg, r, s)]
        return res

    for ref, sec in todo:
        p = unwrap_paths(cfg, ref, sec)
        for key in ("igram", "amp"):
            if not p[key].exists():
                raise StepFailed(
                    f"{p[key]} not found -- stage G3 has not run for this pair.\n"
                    f"    python run_track_g.py --config {cfg.config_path} --only igram"
                )

    report: dict[str, dict] = {}
    outputs: list[str] = []
    for ref, sec in todo:
        log.info(f"  pair {ref}_{sec}")
        report[f"{ref}_{sec}"] = unwrap_pair(cfg, log, ref, sec)
        outputs += [str(f) for f in expected_outputs(cfg, ref, sec)]

    write_sidecar(
        cfg.prov_dir / "unwrap.json", "unwrap",
        inputs={"pairs": {f"{r}_{s}": str(unwrap_paths(cfg, r, s)["igram"])
                          for r, s in todo}},
        outputs={"pairs": report},
        parameters={
            "filter_alpha": uc.filter_alpha, "filter_psize": uc.filter_psize,
            "phsig_win": uc.phsig_win, "phsig_grad_win": uc.phsig_grad_win,
            "nlks_phsig_nominal": float(uc.nlooks_nominal or nominal),
            "nlooks_snaphu_effective": round(float(nlooks_eff), 4),
            "oversample_factor": uc.oversample_factor,
            "cost": uc.cost, "init": uc.init, "ntiles": list(uc.ntiles),
            "nproc": uc.nproc, "water_mask": uc.water_mask,
            "conncomp_dtype": "uint16",
            "correlation_fed_to_snaphu": "phase-sigma, from the FILTERED interferogram "
                                         "(NOT the boxcar coherence)",
            "mask_applied_by": "zeroing the interferogram (snaphu mask= not used)",
        },
        started=started,
    )

    first = report[list(report)[0]]
    res.outputs = outputs
    res.metrics = {
        "pairs": len(todo),
        "masked_fraction": first["masked_fraction"],
        "water_fraction_of_swath": first["water_fraction_of_swath"],
        "n_connected_components": first["n_connected_components"],
        "largest_component_frac_of_valid": first["largest_component_frac_of_valid"],
        "unw_range_rad": first["unw_range_rad"],
        "nlooks_snaphu": first["nlooks_snaphu"],
    }
    res.notes = [
        "correlation fed to snaphu is PHSIG from the filtered interferogram, "
        "not the boxcar coherence",
        "each connected component carries its own arbitrary 2*pi offset; only "
        "pixels sharing a label are mutually comparable",
    ]
    return res
