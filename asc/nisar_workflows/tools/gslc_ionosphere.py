#!/usr/bin/env python3
"""
Split-spectrum ionospheric phase screen for a GEOCODED (Track G) pair.

    python tools/gslc_ionosphere.py \
        --pair-dir <case>/pairs/20260714_20260726/trackG \
        --freq-a-prefix ifg_A_HH_8x8 --freq-b-prefix ifg_B_HH_8x8 \
        --out-dir  <case>/pairs/20260714_20260726/trackG/ionosphere

WHY THIS EXISTS
---------------
ISCE3 has NO ionosphere support in the GSLC path. Verified: zero matches for
"ionosphere" across nisar/workflows/gslc.py, gslc_runconfig.py,
workflows/schemas/gslc.yaml and workflows/defaults/gslc.yaml. There is no flag
to turn on -- the geocoded branch simply never had the correction written.

The correction is still recoverable, because `gslc.py` never passes
`flatten_with_corrected_srange`. The ionospheric phase is therefore NOT removed
during geocoding and survives intact into a GSLC x GSLC interferogram. So the
same main/side-band solve that ISCE3 applies in radar coordinates is valid here
-- it just has to be driven by hand.

WHAT IS REUSED vs NEW
---------------------
Reused verbatim from ISCE3 (all pure numpy, no grid assumptions):
  * isce3.atmosphere.main_band_estimation.estimate_iono_main_side -- the 2x2
    dispersive / non-dispersive inversion
  * isce3.atmosphere.ionosphere_filter.nan_aware_gaussian -- normalised
    Gaussian that ignores NaN instead of bleeding it
  * isce3.atmosphere.ionosphere_filter.nan_median_filter -- mask conditioning

New here: the driver, the geocoded grid handling, the unwrap step, and the
sigma/TECU bookkeeping.

NOTE on an upstream bug: MainSideBandIonosphereEstimation.__init__ forwards
`method` into the base class's FIFTH positional parameter, which is
`slant_main`, so `self.slant_main` silently becomes the string
'main_side_band'. It is inert for this method (compute_disp_nondisp takes slant
ranges explicitly) but it is why this tool calls `estimate_iono_main_side`
directly rather than instantiating the class.

UNWRAPPING IS A PREREQUISITE -- this is not the deferred full-res unwrap
---------------------------------------------------------------------
The split-spectrum decomposition is LINEAR IN ABSOLUTE PHASE:

    phi_A = phi_nondisp          +  phi_disp
    phi_B = (f1/f0) phi_nondisp  +  (f0/f1) phi_disp

Feeding wrapped phase in gives nonsense, because each band carries its own
unknown 2*pi*n. ISCE3 says so plainly -- compute_disp_nondisp documents
`phi_main : unwrapped phase array of frequency A interferogram`.

So both bands must be unwrapped BEFORE the solve. That is not the expensive
Track G unwrap that was deferred: that one is the full-resolution 1x1 grid,
60600 x 62800 = 3805 Mpx. This one runs on the 8x8 / 40 m grid, 7575 x 7850 =
59 Mpx -- 1/64th the pixels. Track R already unwrapped 2886 Mpx on this box, so
59 Mpx is not a concern. The full-resolution Track G unwrap remains deferred.

THE GEOCODED DOMAIN MAKES THIS SOLVE EASIER, NOT HARDER
-------------------------------------------------------
In radar coordinates the two bands land on DIFFERENT grids -- for this case
frequency A came out 5911 x 6781 and frequency B 5911 x 847 -- so ISCE3 has to
reconcile them explicitly (`decimate_freq_a_array`, `interpolate_freq_b_array`)
and the dispersive solve ends up on a 5911 x 847 grid at ~302 m range posting,
then gets interpolated back up.

Here both GSLCs are geocoded onto the SAME pinned 60600 x 62800 lattice at 5 m,
so at identical looks they are pixel-for-pixel aligned by construction. No
decimation, no interpolation, no resampling -- the solve is exactly elementwise
at the full 40 m posting. That is a real advantage of Track G for this product,
and it is checked (not assumed) below: the geotransforms must match.

NOISE AMPLIFICATION -- read before interpreting the screen
----------------------------------------------------------
f0 = 1.239 GHz and f1 = 1.2935 GHz are only 54.5 MHz apart, so the 2x2 system is
ill-conditioned: the inversion amplifies interferogram phase noise into the
dispersive estimate by ~16.8x for this geometry. The raw dispersive layer is
therefore dominated by noise and is NOT directly interpretable; the filtered
layer is the product. This is why the Gaussian sigma is large by default.

FILTER SCALE -- deliberately different from Track R, and why
------------------------------------------------------------
ISCE3's defaults are kernel 100 px, sigma 33 px. Those are PIXEL counts, and
Track R's iono grid is 302 m (range) x 40 m (azimuth), so in physical terms its
smoothing was ~10 km sigma in range but only ~1.3 km in azimuth -- an 7.6:1
anisotropy that is an artifact of the radar grid, not of the ionosphere.

This grid is isotropic 40 m in map coordinates, so a pixel-count kernel copied
across would smooth 4 km isotropically instead. The ionosphere is a
long-wavelength field, so this tool specifies the scale in KILOMETRES
(--sigma-km, default 10.0, matching Track R's range-direction sigma) and
converts to pixels from the geotransform. Use --sigma-km to change it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

# pyre parses sys.argv inside isce3's package __init__ and a CLI with flags
# crashes it -- scrub argv before importing anything that pulls pyre in.
_ARGV = sys.argv[1:]
sys.argv = [sys.argv[0]]

from osgeo import gdal, osr  # noqa: E402
from isce3.atmosphere.main_band_estimation import (  # noqa: E402
    estimate_iono_main_side,
)

gdal.UseExceptions()

# Physical constants for the TEC conversion.
#   phi_iono = -(4 pi K / (c f)) * TEC      =>   TEC = phi * c * f / (4 pi K)
# K = 40.31 m^3/s^2 is the standard ionospheric refraction constant, and
# 1 TECU = 1e16 electrons/m^2. Validated against Track R: its RUNW
# ionospherePhaseScreen median of -19.63 rad at f0 = 1.239 GHz gives -1.44 TECU,
# which is exactly the value Track R reported.
K_IONO = 40.31
C_LIGHT = 299792458.0
TECU = 1e16


def _open(path: Path):
    ds = gdal.Open(str(path))
    if ds is None:
        raise SystemExit(f"cannot open {path}")
    return ds


def _grid_of(ds):
    return (ds.RasterYSize, ds.RasterXSize, tuple(ds.GetGeoTransform()),
            ds.GetProjectionRef())


def _write(path: Path, arr: np.ndarray, ref_ds, dtype=gdal.GDT_Float32,
           meta: dict | None = None, nodata=float("nan")):
    drv = gdal.GetDriverByName("GTiff")
    out = drv.Create(str(path), arr.shape[1], arr.shape[0], 1, dtype,
                     options=["TILED=YES", "COMPRESS=DEFLATE", "ZLEVEL=6",
                              "BIGTIFF=IF_SAFER"])
    out.SetGeoTransform(ref_ds.GetGeoTransform())
    out.SetProjection(ref_ds.GetProjectionRef())
    b = out.GetRasterBand(1)
    b.WriteArray(arr)
    if dtype == gdal.GDT_Float32:
        b.SetNoDataValue(nodata)
    if meta:
        out.SetMetadata({k: str(v) for k, v in meta.items()})
    b.FlushCache()
    out = None


def _stats(name, a, unit=""):
    g = a[np.isfinite(a)]
    if g.size == 0:
        print(f"  {name:26s} all-NaN")
        return
    print(f"  {name:26s} median {np.median(g):+10.4f}{unit}  "
          f"p5 {np.percentile(g, 5):+9.4f}  p95 {np.percentile(g, 95):+9.4f}  "
          f"valid {100 * g.size / a.size:.1f}%")


def unwrap_band(igram_path: Path, coh_path: Path, out_unw: Path,
                out_ccomp: Path, nlooks: float, ntiles, nproc: int,
                scratchdir: Path, force: bool) -> tuple[np.ndarray, np.ndarray]:
    """
    Unwrap one band at 40 m. Cached: a completed unwrap is reused, because the
    solve below is cheap to re-run with different filter settings and the
    unwrap is not.
    """
    import snaphu

    if out_unw.exists() and out_ccomp.exists() and not force:
        print(f"  [cached] {out_unw.name}")
        return (_open(out_unw).ReadAsArray(), _open(out_ccomp).ReadAsArray())

    ig_ds, co_ds = _open(igram_path), _open(coh_path)
    print(f"  unwrapping {igram_path.name}  {ig_ds.RasterYSize} x "
          f"{ig_ds.RasterXSize}  nlooks={nlooks:g}  ntiles={ntiles} nproc={nproc}")

    igram = ig_ds.ReadAsArray()
    corr = co_ds.ReadAsArray().astype(np.float32)
    # snaphu wants coherence in [0,1]; NaN becomes 0 (treated as no data).
    corr = np.nan_to_num(corr, nan=0.0, posinf=0.0, neginf=0.0)
    np.clip(corr, 0.0, 1.0, out=corr)

    scratchdir.mkdir(parents=True, exist_ok=True)
    tiled = (ntiles[0] * ntiles[1]) > 1
    unw, ccomp = snaphu.unwrap(
        igram, corr, nlooks=nlooks, cost="smooth", init="mcf",
        ntiles=tuple(ntiles), nproc=nproc, tile_overlap=200,
        # Both MUST be False when tiled: per-tile reoptimisation and conncomp
        # regrowth re-run the solver over the whole array, which defeats the
        # tiling and restores the single-tile memory peak.
        single_tile_reoptimize=not tiled,
        regrow_conncomps=not tiled,
        scratchdir=str(scratchdir), delete_scratch=True,
    )
    unw = np.asarray(unw, dtype=np.float32)
    ccomp = np.asarray(ccomp, dtype=np.uint32)

    _write(out_unw, unw, ig_ds, meta={
        "SOURCE_IGRAM": str(igram_path), "SOURCE_COH": str(coh_path),
        "NLOOKS": nlooks, "NTILES": str(ntiles), "UNITS": "radians"})
    _write(out_ccomp, ccomp, ig_ds, dtype=gdal.GDT_UInt32)
    n_cc = int(ccomp.max())
    cov = 100.0 * (ccomp > 0).mean()
    print(f"    -> {n_cc} connected components, {cov:.1f}% of grid labelled")
    return unw, ccomp


def resolve_cycle_offsets(f0: float, f1: float, phi_a: np.ndarray,
                          phi_b: np.ndarray, mask: np.ndarray,
                          search: int) -> tuple[int, int, list]:
    """
    Choose the integer 2*pi offsets (m, n) to add to bands A and B.

    THE PROBLEM. snaphu determines phase only up to an arbitrary integer number
    of cycles, independently per connected component, and the two bands are
    unwrapped separately (here: 20 components in A, 18 in B, with no cross-band
    reference). The split-spectrum solve is linear in ABSOLUTE phase, so any
    relative constant between the bands lands directly in the dispersive term
    -- amplified. For this f0/f1 pair ONE cycle in band A injects +76.2 rad of
    false dispersive phase = +5.6 TECU. It is the dominant error term, far
    larger than anything the coherence masking or filtering controls.

    Measured on this case: the uncorrected solve gave +9.74 TECU where Track R
    independently measured -1.44 TECU. The gap was 2.0020 cycles of band A --
    two whole cycles, to 0.1%.

    THE CRITERION. The non-dispersive term is deformation plus residual
    topography. Over a 12-day L-band pair it must be SMALL -- its median over a
    large area should sit near zero. The dispersive term, by contrast, is
    genuinely allowed to be non-zero, so it cannot be used to pin the offset
    without assuming the answer. So: search integer (m, n) and take the pair
    minimising |median(non_dispersive)|, tie-broken toward the smallest total
    shift.

    This is self-contained -- it needs no external reference -- and on this case
    it independently recovers m = -2, giving -1.43 TECU against Track R's -1.44.

    Residual degeneracy is real but bounded: several (m, n) give a small median
    non-dispersive, and they span only ~0.25 TECU of dispersive, which is well
    inside the noise. The full search table is returned so the choice is
    auditable rather than hidden.
    """
    two_pi = 2.0 * math.pi
    table = []
    for m in range(-search, search + 1):
        for n in range(-search, search + 1):
            d, nd = estimate_iono_main_side(f0, f1, phi_a + m * two_pi,
                                            phi_b + n * two_pi)
            med_nd = float(np.median(nd[mask]))
            med_d = float(np.median(d[mask]))
            table.append({"m": m, "n": n, "median_nondispersive_rad": med_nd,
                          "median_dispersive_rad": med_d,
                          "abs_median_nondispersive": abs(med_nd)})
    # THE SEARCH IS DEGENERATE -- and understanding how is the whole point.
    #
    # Adding 2*pi to BOTH bands shifts the dispersive estimate by only
    # +3.209 rad (0.235 TECU) and the non-dispersive by +3.074 rad. So
    # |median(non_dispersive)| decreases MONOTONICALLY along the (m+1, n+1)
    # direction and never turns around: a plain argmin is unbounded, and
    # whatever you set `search` to silently picks the answer. That is exactly
    # what happened on the first run -- with search=3 it returned the corner
    # (m=-3, n=-1) rather than a physically determined point.
    #
    # What the data DO determine is the combination d = m - n, because moving
    # across classes changes |median(non_dispersive)| steeply. On this case
    # every one of the top candidates has d = -2, so d is well constrained
    # while the position along the degenerate line is not.
    #
    # So: pick the class d by the criterion, then take the MINIMAL-NORM
    # representative of that class rather than letting the search bound choose.
    # The residual freedom is +/-0.235 TECU per cycle, which is reported.
    by_class = {}
    for t in table:
        d = t["m"] - t["n"]
        cur = by_class.get(d)
        if cur is None or t["abs_median_nondispersive"] < cur["abs_median_nondispersive"]:
            by_class[d] = t
    best_d = min(by_class.values(),
                 key=lambda t: t["abs_median_nondispersive"])["m"] - \
             min(by_class.values(),
                 key=lambda t: t["abs_median_nondispersive"])["n"]
    # minimal-norm representative of the chosen class
    in_class = [t for t in table if t["m"] - t["n"] == best_d]
    in_class.sort(key=lambda t: (abs(t["m"]) + abs(t["n"]),
                                 t["abs_median_nondispersive"]))
    best = in_class[0]
    table.sort(key=lambda t: (t["abs_median_nondispersive"],
                              abs(t["m"]) + abs(t["n"])))
    return best["m"], best["n"], table


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pair-dir", required=True, type=Path)
    ap.add_argument("--freq-a-prefix", default="ifg_A_HH_8x8")
    ap.add_argument("--freq-b-prefix", default="ifg_B_HH_8x8")
    ap.add_argument("--out-dir", default=None, type=Path)
    ap.add_argument("--f0", type=float, default=None,
                    help="freq A centre frequency [Hz]; default from the "
                         "igram sidecar JSON, else 1.239e9")
    ap.add_argument("--f1", type=float, default=None,
                    help="freq B centre frequency [Hz]; default 1.2935e9")
    ap.add_argument("--nlooks", type=float, default=64.0,
                    help="equivalent independent looks in the coherence "
                         "(default 64 = the 8x8 box)")
    ap.add_argument("--coherence-threshold", type=float, default=0.5,
                    help="mask threshold, applied to BOTH bands (Track R used 0.5)")
    ap.add_argument("--median-filter-size", type=int, default=15,
                    help="median filter for mask conditioning (Track R used 15)")
    ap.add_argument("--sigma-km", type=float, default=10.0,
                    help="Gaussian sigma in KILOMETRES (default 10, matching "
                         "Track R's range-direction physical scale)")
    ap.add_argument("--cycle-search", type=int, default=3,
                    help="half-width of the integer 2*pi offset search applied "
                         "to each band (default 3 -> 7x7 candidates). 0 "
                         "disables the search and trusts snaphu's absolute "
                         "level, which on this case was wrong by 2 cycles "
                         "= 11.2 TECU.")
    ap.add_argument("--cycle-offsets", nargs=2, type=int, default=None,
                    metavar=("M", "N"),
                    help="force the 2*pi offsets for bands A and B instead of "
                         "searching")
    ap.add_argument("--ntiles", nargs=2, type=int, default=[4, 4])
    ap.add_argument("--nproc", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(_ARGV)

    pd = args.pair_dir
    out = args.out_dir or (pd / "ionosphere")
    out.mkdir(parents=True, exist_ok=True)

    ifg_a = pd / f"{args.freq_a_prefix}.igram.tif"
    coh_a = pd / f"{args.freq_a_prefix}.coh.tif"
    ifg_b = pd / f"{args.freq_b_prefix}.igram.tif"
    coh_b = pd / f"{args.freq_b_prefix}.coh.tif"
    for p in (ifg_a, coh_a, ifg_b, coh_b):
        if not p.exists():
            raise SystemExit(f"missing input: {p}")

    # ---- grid agreement is the whole premise; check it, never assume --------
    ga, gb = _grid_of(_open(ifg_a)), _grid_of(_open(ifg_b))
    if ga[:3] != gb[:3]:
        raise SystemExit(
            "frequency A and B are NOT on the same grid, so the elementwise "
            "solve is invalid:\n"
            f"  A: {ga[0]} x {ga[1]}  gt={ga[2]}\n"
            f"  B: {gb[0]} x {gb[1]}  gt={gb[2]}\n"
            "Re-form both interferograms at the same looks from the same "
            "pinned geogrid.")
    L, W, gt, _ = ga
    px_m = abs(gt[1])
    print(f"grid {L} x {W} = {L * W / 1e6:.1f} Mpx at {px_m:g} m  "
          f"(A and B geotransforms identical -- elementwise solve is valid)")

    # ---- centre frequencies -------------------------------------------------
    f0, f1 = args.f0, args.f1
    if f0 is None or f1 is None:
        for pfx, which in ((args.freq_a_prefix, "f0"), (args.freq_b_prefix, "f1")):
            j = pd / f"{pfx}.json"
            if j.exists():
                try:
                    v = json.loads(j.read_text()).get("parameters", {}).get(
                        "center_frequency_hz")
                    if v:
                        if which == "f0" and f0 is None:
                            f0 = float(v)
                        if which == "f1" and f1 is None:
                            f1 = float(v)
                except Exception:
                    pass
    f0 = f0 or 1.239e9
    f1 = f1 or 1.2935e9
    # Condition number of the 2x2 system -> how much phase noise lands in the
    # dispersive estimate. BOTH coefficients contribute, so the amplification is
    # sqrt(a^2 + b^2), not `a` alone -- measured 16.83x against 16.79x predicted
    # on synthetic truth for this f0/f1 pair.
    _a = (f1 ** 2) / (f1 ** 2 - f0 ** 2)
    _b = (f0 * f1) / (f1 ** 2 - f0 ** 2)
    amp = math.sqrt(_a ** 2 + _b ** 2)
    print(f"f0 = {f0 / 1e9:.4f} GHz   f1 = {f1 / 1e9:.4f} GHz   "
          f"separation {abs(f1 - f0) / 1e6:.1f} MHz")
    print(f"dispersive noise amplification ~ {amp:.2f}x  "
          f"-- the RAW dispersive layer is noise-dominated by design")

    # ---- unwrap both bands --------------------------------------------------
    print("\n[1/4] unwrapping both bands at 40 m "
          "(prerequisite: the solve is linear in ABSOLUTE phase)")
    unw_a, cc_a = unwrap_band(ifg_a, coh_a, out / "unw_A.tif", out / "conncomp_A.tif",
                              args.nlooks, args.ntiles, args.nproc,
                              out / "scratch_A", args.force)
    unw_b, cc_b = unwrap_band(ifg_b, coh_b, out / "unw_B.tif", out / "conncomp_B.tif",
                              args.nlooks, args.ntiles, args.nproc,
                              out / "scratch_B", args.force)

    # ---- mask ---------------------------------------------------------------
    print("\n[2/4] mask")
    from isce3.atmosphere.ionosphere_filter import nan_median_filter

    ca = np.nan_to_num(_open(coh_a).ReadAsArray().astype(np.float32))
    cb = np.nan_to_num(_open(coh_b).ReadAsArray().astype(np.float32))
    thr = args.coherence_threshold
    # Both bands must be reliable: the solve mixes them, so a pixel is only as
    # good as its worse band. Connected-component 0 means snaphu could not
    # unwrap it, and an unwrapped value there is meaningless.
    mask = (ca > thr) & (cb > thr) & (cc_a > 0) & (cc_b > 0)
    print(f"  cohA>{thr}: {100 * (ca > thr).mean():.1f}%   "
          f"cohB>{thr}: {100 * (cb > thr).mean():.1f}%   "
          f"both+unwrapped: {100 * mask.mean():.1f}%")
    if args.median_filter_size > 1:
        m = nan_median_filter(mask.astype(np.float32), args.median_filter_size)
        mask = np.nan_to_num(m) > 0.5
        print(f"  after {args.median_filter_size}x{args.median_filter_size} "
              f"median conditioning: {100 * mask.mean():.1f}%")
    if not mask.any():
        raise SystemExit("mask is empty -- lower --coherence-threshold")

    # ---- resolve the absolute-phase ambiguity BEFORE solving ---------------
    # This is the single largest error term. See resolve_cycle_offsets.
    two_pi = 2.0 * math.pi
    d_per_cyc_a = (-(f1 / f0) * two_pi) / ((f0 / f1) - (f1 / f0))
    d_per_cyc_b = (two_pi) / ((f0 / f1) - (f1 / f0))
    rad2tecu = C_LIGHT * f0 / (4.0 * math.pi * K_IONO) / TECU
    print(f"\n[3/4] resolving absolute-phase ambiguity")
    print(f"  ONE 2*pi cycle of unwrap offset injects "
          f"{d_per_cyc_a:+.1f} rad ({d_per_cyc_a * rad2tecu:+.2f} TECU) via band A, "
          f"{d_per_cyc_b:+.1f} rad ({d_per_cyc_b * rad2tecu:+.2f} TECU) via band B")
    cyc_table = []
    if args.cycle_offsets is not None:
        m_off, n_off = int(args.cycle_offsets[0]), int(args.cycle_offsets[1])
        print(f"  forced offsets m={m_off:+d} (A), n={n_off:+d} (B)")
    elif args.cycle_search > 0:
        m_off, n_off, cyc_table = resolve_cycle_offsets(
            f0, f1, unw_a, unw_b, mask, args.cycle_search)
        print(f"  searched {(2 * args.cycle_search + 1) ** 2} candidates, "
              f"criterion = min |median(non-dispersive)|")
        for t in cyc_table[:5]:
            print(f"    m={t['m']:+d} n={t['n']:+d}  nondisp median "
                  f"{t['median_nondispersive_rad']:+9.2f} rad   disp median "
                  f"{t['median_dispersive_rad']:+9.2f} rad = "
                  f"{t['median_dispersive_rad'] * rad2tecu:+7.3f} TECU")
        joint_d = (two_pi - (f1 / f0) * two_pi) / ((f0 / f1) - (f1 / f0))
        print(f"  DEGENERACY: adding 2*pi to BOTH bands moves the answer by only "
              f"{joint_d * rad2tecu:+.3f} TECU, and |median(non-dispersive)| falls "
              f"monotonically along it -- so a plain argmin is unbounded.")
        print(f"  The data DO determine the class d = m - n = {m_off - n_off:+d}; "
              f"position along the degenerate line is a prior, not a measurement.")
        print(f"  CHOSEN m={m_off:+d} (A), n={n_off:+d} (B)  "
              f"(minimal-norm representative of d={m_off - n_off:+d})")
        print(f"  ABSOLUTE TEC IS THEREFORE UNCERTAIN BY ~"
              f"{abs(joint_d * rad2tecu):.2f} TECU per cycle. For an absolute "
              f"reference use a TEC product (isce3.atmosphere.tec_product) or an "
              f"independent measurement.")
    else:
        m_off = n_off = 0
        print("  search disabled -- trusting snaphu's absolute level")

    # ---- dispersive / non-dispersive solve ----------------------------------
    print("\n[3b/4] split-spectrum solve (isce3 estimate_iono_main_side)")
    disp, nondisp = estimate_iono_main_side(
        f0, f1, unw_a + m_off * two_pi, unw_b + n_off * two_pi)
    disp = np.where(mask, disp, np.nan).astype(np.float32)
    nondisp = np.where(mask, nondisp, np.nan).astype(np.float32)
    _stats("dispersive (raw)", disp, " rad")
    _stats("non-dispersive (raw)", nondisp, " rad")

    # Phase sigma from coherence: sigma_phi = sqrt(1-g^2) / (g * sqrt(2N)).
    with np.errstate(divide="ignore", invalid="ignore"):
        sa = np.sqrt(1 - ca ** 2) / (ca * math.sqrt(2 * args.nlooks))
        sb = np.sqrt(1 - cb ** 2) / (cb * math.sqrt(2 * args.nlooks))
    # estimate_sigma_main_side, inlined (it is three lines and needs no object)
    a_ = (f1 ** 2) / (f1 ** 2 - f0 ** 2)
    b_ = (f0 * f1) / (f1 ** 2 - f0 ** 2)
    sig_iono = np.sqrt(a_ ** 2 * sa ** 2 + b_ ** 2 * sb ** 2).astype(np.float32)
    # The 15x15 median conditioning re-admits pixels whose coherence is 0 or
    # below the threshold, where sigma is infinite. One infinity inside the
    # support of the 250 px Gaussian below made dispersive_sigma_filtered.tif
    # 100% +inf ("RuntimeWarning: overflow encountered in cast"). Sigma is only
    # defined where BOTH coherences exceed the threshold; everything else is NaN.
    # (Affects only the uncertainty layers, never the dispersive screen.)
    sig_ok = mask & np.isfinite(sig_iono) & (ca > thr) & (cb > thr)
    sig_iono = np.where(sig_ok, sig_iono, np.nan)
    _stats("dispersive sigma", sig_iono, " rad")

    # ---- filter -------------------------------------------------------------
    sigma_px = args.sigma_km * 1000.0 / px_m
    print(f"\n[4/4] low-pass filter: sigma {args.sigma_km:g} km = "
          f"{sigma_px:.1f} px at {px_m:g} m (isotropic, map coordinates)")
    from isce3.atmosphere.ionosphere_filter import nan_aware_gaussian

    disp_masked = np.where(mask, disp, np.nan)
    filt = nan_aware_gaussian(disp_masked.astype(np.float64), sigma=sigma_px)
    filt = filt.astype(np.float32)
    sig_f = nan_aware_gaussian(
        sig_iono.astype(np.float64),
        sigma=sigma_px).astype(np.float32)
    # Smoothing averages ~N_eff independent samples, so the screen's own
    # uncertainty shrinks; report both the per-pixel and the smoothed sigma.
    _stats("dispersive (filtered)", filt, " rad")

    # ---- TEC ----------------------------------------------------------------
    tec = (filt * C_LIGHT * f0 / (4.0 * math.pi * K_IONO) / TECU).astype(np.float32)
    _stats("dispersive (filtered)", tec, " TECU")

    ref = _open(ifg_a)
    common = {"F0_HZ": f0, "F1_HZ": f1, "METHOD": "main_side_band",
              "CYCLE_OFFSET_A": m_off, "CYCLE_OFFSET_B": n_off,
              "CYCLE_OFFSET_NOTE": "integer 2*pi offsets applied to resolve the "
                                   "independent-unwrap ambiguity; one cycle in "
                                   "A = %.1f rad of dispersive" % d_per_cyc_a,
              "NLOOKS": args.nlooks, "COH_THRESHOLD": thr,
              "SIGMA_KM": args.sigma_km, "SIGMA_PX": f"{sigma_px:.2f}",
              "NOISE_AMPLIFICATION": f"{amp:.2f}",
              "NOTE": "GSLC/geocoded split-spectrum; ISCE3 has no GSLC "
                      "ionosphere path, this is a port of estimate_iono_main_side"}
    _write(out / "dispersive.tif", disp, ref, meta={**common, "UNITS": "radians",
           "WARNING": "RAW solve, noise-amplified ~%.1fx; use the filtered "
                      "layer for interpretation" % amp})
    _write(out / "dispersive_filtered.tif", filt, ref,
           meta={**common, "UNITS": "radians"})
    _write(out / "dispersive_tecu.tif", tec, ref, meta={**common, "UNITS": "TECU"})
    _write(out / "dispersive_sigma.tif", sig_iono, ref,
           meta={**common, "UNITS": "radians"})
    _write(out / "dispersive_sigma_filtered.tif", sig_f, ref,
           meta={**common, "UNITS": "radians"})
    _write(out / "non_dispersive.tif", nondisp, ref,
           meta={**common, "UNITS": "radians"})
    _write(out / "mask.tif", mask.astype(np.uint8), ref, dtype=gdal.GDT_Byte)

    summary = {
        "grid": {"lines": L, "width": W, "posting_m": px_m},
        "frequencies": {"f0_hz": f0, "f1_hz": f1,
                        "separation_mhz": abs(f1 - f0) / 1e6,
                        "noise_amplification": amp},
        "parameters": {"nlooks": args.nlooks, "coherence_threshold": thr,
                       "median_filter_size": args.median_filter_size,
                       "sigma_km": args.sigma_km, "sigma_px": sigma_px,
                       "ntiles": args.ntiles},
        "cycle_offsets": {"A": m_off, "B": n_off,
                          "rad_per_cycle_A": d_per_cyc_a,
                          "rad_per_cycle_B": d_per_cyc_b,
                          "search_table": cyc_table[:10]},
        "mask_valid_fraction": float(mask.mean()),
        "dispersive_filtered_rad_median": float(np.nanmedian(filt)),
        "dispersive_filtered_tecu_median": float(np.nanmedian(tec)),
        "dispersive_sigma_rad_median": float(np.nanmedian(sig_iono)),
    }
    (out / "ionosphere.json").write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
