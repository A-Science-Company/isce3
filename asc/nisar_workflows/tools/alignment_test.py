#!/usr/bin/env python3
"""
Is the RSLC-vs-GSLC difference a registration difference, and does alignment recover RSLC quality?

    python -u tools/alignment_test.py [--force]        # after compare_four_way.py v2

Read-only on every product. Writes <case>/comparison_v2/alignment/ (alignment.json, figures_alignment.json,
chip tables) and figures <case>/comparison_v2/report/fig/f1[2-3]_*.webp.

Mechanism under test
--------------------
The RSLC chain registers the secondary twice: geometry only (geo2rdr -> coarse_resample), then with a
dense-offset rubber sheet (fine_resample). The GSLC chain geocodes each date from its own orbit and the DEM
and never registers the dates to each other, so its inter-date registration is geometry-only - the
RSLC *coarse* state. A residual misregistration d costs coherence (a factor |rho(d)|, the normalised
Fourier transform of the SLC power spectrum) and, because a zero-Doppler SLC carries an azimuth Doppler
carrier, adds interferometric phase k*d_az (k = 2 pi f_dc / line rate).

Why the registration experiment is done on RSLC data
----------------------------------------------------
The delivered GSLCs have spectrally WHITE 5 m samples (every 256x256 chip is within ~6 dB of flat, while an
RSLC chip occupies ~2/3 of its spectrum). Band-limited sub-pixel estimation and Fourier shifting are therefore
not valid on the GSLCs themselves (tried and failed: tools/legacy/alignment_test_v2_invalid_gslc_shift.py,
outputs under _ABORTED_*). The experiment is run where it is valid: the RSLC reference against the
geometry-only and the rubber-sheet secondaries; the GSLC is then compared with both.

Estimator
---------
Complex cross-correlation of Tukey-tapered chips, upsampled DFT refinement (x100), after DEMODULATING both
chips by their joint spectral centroid. Without demodulation the RSLC azimuth band (centred on the Doppler
carrier, ~0.63 of the line rate, i.e. wrapped across Nyquist) makes a physical delay d read as ~-0.28 d.

Stages
------
P   phase distributions of the four legs (40 m and 5 m); Jensen-Shannon distances to R_full.
E0  estimator calibration with PHYSICAL sub-sample delays (demodulate, shift, remodulate) on RSLC chips,
    carrier-aware vs naive, at coherence 1 and 0.6.
E1  RSLC control: reference vs geometry-only secondary must reproduce ISCE3's rubber-sheet offsets; reference vs
    rubber-sheet secondary must give ~0; geometry-only vs rubber-sheet must equal the rubber sheet.
E2  spectral occupancy of RSLC and GSLC chips + 2-D mean spectra figures.
E3  closure: Rc = R_full * unit(fine * conj(coarse)) = benchmark with geometry-only registration. Does G agree
    with Rc better than with R? Does coherence(coarse)/coherence(fine) match G/R in level and spatial pattern?
E4  carrier coefficient b in  phi_R - phi_X = a + b k d_az  for X = G and X = Rc (block bootstrap CIs; theory -1).
E5  coherence loss predicted from the measured misregistration and measured RSLC spectra, vs observed.
S   sampling and sharpness: lookup duplicates, speckle contrast, intensity autocorrelation width.
"""

from __future__ import annotations

import importlib.util
import io
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

_ARGV = sys.argv[1:]
sys.argv = [sys.argv[0]]

import h5py  # noqa: E402
from osgeo import gdal  # noqa: E402
from scipy import stats  # noqa: E402
from scipy.ndimage import uniform_filter  # noqa: E402
from scipy.signal.windows import tukey  # noqa: E402
from skimage.registration import phase_cross_correlation  # noqa: E402

gdal.UseExceptions()
TOOLS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cfw", TOOLS / "compare_four_way.py")
cfw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfw)

C = Path("/home/sharath/isce3/case_studies/nepal_glof")
PAIR, TAG = "20260714_20260726", "20260714_20260726_A_HH_1x1"
REF_D, SEC_D = PAIR.split("_")
CMP = C / "comparison_v2"
OUT = CMP / "alignment"
FIG = CMP / "report" / "fig"
LAY = CMP / "layers"
SC = C / "aoi_v2" / "scratch" / "trackR" / TAG
GSLC = C / "aoi_v2" / "L2_GSLC"
P, PV = C / "pairs" / PAIR, C / "aoi_v2" / "pairs" / PAIR
CHIP, UP, K, BS = 128, 100, 8, 32
BINS = ((0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.01))
RNG = np.random.default_rng(20260915)
T0 = time.time()
report: dict = {"tool": "alignment_test v3", "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "conventions": {"displacement": "of the moving image relative to the reference, in radar lines/samples "
                                                "(= minus the skimage registration shift)",
                                "pair_key": "P__vs__Q = P minus Q", "block": f"{BS}x{BS} 40 m cells = 1.28 km"}}
FIGMETA: dict = {"figures": {}, "charts": {}}
G: dict = {}
_TW = tukey(CHIP, 0.25)
TAPER = np.outer(_TW, _TW)


def log(m):
    print(f"[{time.time() - T0:7.1f}s] {m}", flush=True)


def save():
    (OUT / "alignment.json").write_text(json.dumps(report, indent=2))
    (OUT / "figures_alignment.json").write_text(json.dumps(FIGMETA, indent=1))


# ------------------------------------------------------------------------------ carrier-aware estimation
def carrier(a, b=None):
    """Continuous spectral centroid per axis (cycles/sample): circular mean of the marginal power."""
    Pw = np.abs(np.fft.fft2(a)) ** 2
    if b is not None:
        Pw = Pw + np.abs(np.fft.fft2(b)) ** 2
    out = []
    for axis, n in ((1, a.shape[0]), (0, a.shape[1])):
        prof = Pw.sum(axis=axis)
        out.append(float(np.angle(np.sum(prof * np.exp(2j * np.pi * np.arange(n) / n))) / (2 * np.pi)))
    return out


def modulation(shape, f, sign):
    return np.exp(sign * 2j * np.pi * (f[0] * np.arange(shape[0])[:, None] + f[1] * np.arange(shape[1])[None, :]))


def fourier_shift(t, sy, sx):
    ky = np.fft.fftfreq(t.shape[0])[:, None]
    kx = np.fft.fftfreq(t.shape[1])[None, :]
    return np.fft.ifft2(np.fft.fft2(t) * np.exp(-2j * np.pi * (ky * sy + kx * sx)))


def physical_delay(z, d):
    """Delay a carrier-modulated band-limited signal by d samples: demodulate, shift, remodulate."""
    f = carrier(z)
    return fourier_shift(z * modulation(z.shape, f, -1), *d) * modulation(z.shape, f, +1)


def estimate(a, b, demod=True):
    """Displacement (rows, cols) of b relative to a, and chip coherence."""
    a = np.nan_to_num(a.astype(np.complex128))
    b = np.nan_to_num(b.astype(np.complex128))
    den = math.sqrt(float(np.sum(np.abs(a) ** 2) * np.sum(np.abs(b) ** 2)))
    gam = float(abs(np.sum(a * np.conj(b))) / den) if den > 0 else 0.0
    if demod:
        m = modulation(a.shape, carrier(a, b), -1)
        a, b = a * m, b * m
    s, _, _ = phase_cross_correlation(a * TAPER, b * TAPER, upsample_factor=UP, normalization=None)
    return -float(s[0]), -float(s[1]), gam


# ------------------------------------------------------------------------------ statistics
def regress(x, y, groups, n_boot=2000):
    ok = np.isfinite(x) & np.isfinite(y)
    x, y, groups = x[ok], y[ok], groups[ok]
    members = [np.flatnonzero(groups == g) for g in np.unique(groups)]
    b, a = np.polyfit(x, y, 1)
    ts = stats.theilslopes(y, x)
    reps = []
    for _ in range(n_boot):
        ix = np.concatenate([members[i] for i in RNG.integers(0, len(members), len(members))])
        reps.append(np.polyfit(x[ix], y[ix], 1))
    reps = np.array(reps)
    return {"n": int(len(x)), "blocks": len(members), "ols_slope": float(b), "ols_intercept": float(a),
            "ols_slope_ci95": [float(np.percentile(reps[:, 0], 2.5)), float(np.percentile(reps[:, 0], 97.5))],
            "ols_intercept_ci95": [float(np.percentile(reps[:, 1], 2.5)), float(np.percentile(reps[:, 1], 97.5))],
            "theil_sen_slope": float(ts[0]), "theil_sen_ci95": [float(ts[2]), float(ts[3])],
            "pearson_r": float(np.corrcoef(x, y)[0, 1]), "mean_x": float(x.mean()), "mean_y": float(y.mean()),
            "residual_std": float(np.std(y - (a + b * x)))}


def boot_ci(v, fn=np.mean, n=2000):
    reps = [fn(v[RNG.integers(0, len(v), len(v))]) for _ in range(n)]
    return [float(np.percentile(reps, 2.5)), float(np.percentile(reps, 97.5))]


def hist(values, lo, hi, bins):
    h, e = np.histogram(values, bins=bins, range=(lo, hi), density=True)
    return {"centers": [round(float(c), 4) for c in (e[:-1] + e[1:]) / 2], "density": [round(float(v), 5) for v in h]}


def js_distance(p, q):
    p = np.asarray(p, float) + 1e-12; q = np.asarray(q, float) + 1e-12
    p /= p.sum(); q /= q.sum(); m = 0.5 * (p + q)
    return float(math.sqrt(max(0.0, 0.5 * np.sum(p * np.log2(p / m)) + 0.5 * np.sum(q * np.log2(q / m)))))


def circ(z):
    z = z[np.abs(z) > 0]
    mm = (z / np.abs(z)).mean()
    return {"circular_mean_rad": float(np.angle(mm)), "mean_resultant_length": float(abs(mm))}


def unit(z):
    return np.where(np.abs(z) > 0, z / np.maximum(np.abs(z), 1e-30), 0).astype(np.complex64)


def block_ids(shape):
    return (np.arange(shape[0])[:, None] // BS) * ((shape[1] + BS - 1) // BS) + (np.arange(shape[1])[None, :] // BS)


def block_sum(bid, v, nb):
    return np.bincount(bid.ravel(), np.asarray(v, np.float64).ravel(), nb)


# ------------------------------------------------------------------------------ workers
def _calib(args):
    r, c, dy, dx, gam, r2, c2 = args
    big = np.asarray(G["ref"][r:r + 2 * CHIP, c:c + 2 * CHIP]).astype(np.complex128)
    oth = np.asarray(G["ref"][r2:r2 + 2 * CHIP, c2:c2 + 2 * CHIP]).astype(np.complex128)
    if np.count_nonzero(big) < 0.99 * big.size or np.count_nonzero(oth) < 0.99 * oth.size:
        return None
    sh = physical_delay(big, (dy, dx))
    oth = oth * math.sqrt(np.mean(np.abs(sh) ** 2) / max(np.mean(np.abs(oth) ** 2), 1e-12))
    mix = gam * sh + math.sqrt(1 - gam * gam) * oth
    o = CHIP // 2
    a, b = big[o:o + CHIP, o:o + CHIP], mix[o:o + CHIP, o:o + CHIP]
    d1 = estimate(a, b, True)
    d0 = estimate(a, b, False)
    return [dy, dx, d1[0], d1[1], d0[0], d0[1]]


def _control(args):
    r, c = args
    ref = np.asarray(G["ref"][r:r + CHIP, c:c + CHIP])
    coa = np.asarray(G["coarse"][r:r + CHIP, c:c + CHIP])
    fin = np.asarray(G["fine"][r:r + CHIP, c:c + CHIP])
    if min(np.count_nonzero(ref), np.count_nonzero(coa), np.count_nonzero(fin)) < 0.98 * CHIP * CHIP:
        return None
    rc, rf, cf = estimate(ref, coa), estimate(ref, fin), estimate(coa, fin)
    return [r, c, *rc, *rf, *cf, float(np.mean(G["daz"][r:r + CHIP, c:c + CHIP])), float(np.mean(G["drg"][r:r + CHIP, c:c + CHIP]))]


# ------------------------------------------------------------------------------ figures
def webp(name, rgba, **info):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "WEBP", quality=80, method=6)
    (FIG / f"{name}.webp").write_bytes(buf.getvalue())
    FIGMETA["figures"][name] = {"file": f"fig/{name}.webp", "px": [rgba.shape[1], rgba.shape[0]],
                                "kb": round(len(buf.getvalue()) / 1024), **info}
    log(f"  figure {name} {rgba.shape[1]}x{rgba.shape[0]}")


def colorize(values, lo, hi, mask, cmap):
    x = np.clip((np.nan_to_num(values) - lo) / (hi - lo), 0, 1)
    rgba = (cmap(x) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(mask, 255, 0)
    return rgba


# ============================================================================== main
def main() -> int:
    from matplotlib import colormaps
    from matplotlib.colors import LinearSegmentedColormap, to_hex
    from PIL import Image
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "alignment.json").exists() and "--force" not in _ARGV:
        raise SystemExit(f"{OUT / 'alignment.json'} exists; pass --force to recompute")
    DIV = LinearSegmentedColormap.from_list("div", ["#1c5cab", "#6da7ec", "#f0efec", "#ee8a89", "#b8302f"])
    stops = lambda cm, n=11: [to_hex(cm(i / (n - 1))) for i in range(n)]

    rep = json.loads((CMP / "comparison.json").read_text())
    X0, Y1 = rep["lattice"]["origin"]
    PX = rep["lattice"]["px_m"]
    NY, NX = rep["lattice"]["shape"]
    AZ0, RG0, L, W = rep["crop_geometry"]["reference_A"]
    WR0, WR1, WC0, WC1 = rep["radar_data_window_crop_frame"]
    ext = [NX * PX / 1000, NY * PX / 1000]

    # ------------------------------------------------------------------ lattice inputs
    log("loading lattice layers")
    aoi = cfw.read_tif(CMP / "aoi_polygon_mask.tif").astype(bool)
    lut = LAY / "lut_fullframe_rowcol.tif"
    RI, CI, RES = cfw.read_tif(lut, 1), cfw.read_tif(lut, 2), cfw.read_tif(lut, 3)
    valid = (RI >= 0) & (CI >= 0)
    geo_ok = valid & (RES <= 15.0)
    RI, CI = RI.astype(np.int32), CI.astype(np.int32)
    del RES
    rr_w, cc_w = RI[valid] - AZ0 - WR0, CI[valid] - RG0 - WC0

    def geocode(win_arr):
        out = np.zeros((NY, NX), win_arr.dtype)
        out[valid] = win_arr[rr_w, cc_w]
        return out

    r_ifg = cfw.read_tif(LAY / "R_full_ifg.tif")
    r_coh = cfw.read_tif(LAY / "R_full_coh_flat.tif")
    daz = cfw.read_tif(LAY / "R_full_dense_azimuth_residual_lines.tif")
    gcc, grc = cfw.lattice_offset(PV / "trackG" / "ifg_A_HH_1x1.igram.tif", X0, Y1)
    g_ifg = cfw.c64(cfw.read_window(PV / "trackG" / "ifg_A_HH_1x1.igram.tif", gcc, grc, NX, NY))
    g_coh = cfw.f32(cfw.read_window(PV / "trackG" / "ifg_A_HH_1x1.coh.tif", gcc, grc, NX, NY))
    common = aoi & geo_ok & (r_ifg != 0) & (g_ifg != 0)
    common8 = cfw.multilook(common.astype(np.float32), K) >= 0.75
    report["common_mask_fraction_of_aoi"] = float(common.sum() / aoi.sum())

    # ------------------------------------------------------------------ P
    log("P phase distributions")
    Gf = P / "trackG" / "ifg_A_HH.igram.tif"
    gcf, grf = cfw.lattice_offset(Gf, X0, Y1)
    legs = {"R_full": r_ifg, "R_crop": cfw.read_tif(LAY / "R_crop_ifg.tif"),
            "G_full": cfw.c64(cfw.read_window(Gf, gcf, grf, NX, NY)), "G_crop": g_ifg}
    ml = {k: cfw.multilook(v, K) for k, v in legs.items()}
    sub5 = common[::3, ::3]
    pdist = {"40m": {}, "5m": {}, "circular_40m": {}, "js_distance_to_R_full_40m": {}, "js_distance_to_R_full_5m": {}}
    for k in legs:
        pdist["40m"][k] = hist(np.angle(ml[k][common8]), -math.pi, math.pi, 72)
        pdist["circular_40m"][k] = circ(ml[k][common8])
        pdist["5m"][k] = hist(np.angle(legs[k][::3, ::3][sub5]), -math.pi, math.pi, 72)
    for k in legs:
        for res in ("40m", "5m"):
            pdist[f"js_distance_to_R_full_{res}"][k] = js_distance(pdist[res]["R_full"]["density"], pdist[res][k]["density"])
    del legs
    report["P_phase_distributions"] = pdist
    save()
    log(f"  JS distance to R_full 40 m: { {k: round(v, 4) for k, v in pdist['js_distance_to_R_full_40m'].items()} }")

    # ------------------------------------------------------------------ radar arrays
    shp = (L, W)
    G["ref"] = np.memmap(SC / "crossmul/freqA/HH/reference.slc", dtype="<c8", mode="r", shape=shp)
    G["coarse"] = np.memmap(SC / "coarse_resample_slc/freqA/HH/coregistered_secondary.slc", dtype="<c8", mode="r", shape=shp)
    G["fine"] = np.memmap(SC / "fine_resample_slc/freqA/HH/coregistered_secondary.slc", dtype="<c8", mode="r", shape=shp)
    G["daz"] = np.memmap(SC / "rubbersheet_offsets/freqA/HH/resampled_az_offsets", dtype="<f8", mode="r", shape=shp)
    G["drg"] = np.memmap(SC / "rubbersheet_offsets/freqA/HH/resampled_rg_offsets", dtype="<f8", mode="r", shape=shp)

    # ------------------------------------------------------------------ E0
    log("E0 estimator calibration with physical delays (RSLC radar chips)")
    cand = [(r, c) for r in range(WR0, WR1 - 2 * CHIP, 2 * CHIP) for c in range(WC0, WC1 - 2 * CHIP, 2 * CHIP)]
    e0 = {"true_delay_range_samples": [-0.3, 0.3], "chips_per_case": 300,
          "method": "moving = reference delayed by a PHYSICAL sub-sample delay (demodulate by the chip's spectral centroid, "
                    "Fourier shift, remodulate), mixed with independent speckle to the stated coherence; central 128x128"}
    for gam in (1.0, 0.6):
        pick = [cand[i] for i in RNG.choice(len(cand), 300, replace=False)]
        other = [cand[i] for i in RNG.choice(len(cand), 300, replace=True)]
        jobs = [(r, c, float(d[0]), float(d[1]), gam, r2, c2) for (r, c), (r2, c2), d in zip(pick, other, RNG.uniform(-0.3, 0.3, (300, 2)))]
        with Pool(8) as pool:
            arr = np.array([x for x in pool.map(_calib, jobs, chunksize=8) if x is not None])
        key = f"coherence_{gam:.1f}"
        e0[key] = {"n": int(len(arr))}
        for est, off in (("carrier_aware", 2), ("naive", 4)):
            for i, ax in ((0, "azimuth"), (1, "range")):
                b, a = np.polyfit(arr[:, i], arr[:, off + i], 1)
                e0[key][f"{est}_{ax}"] = {"gain": float(b), "offset": float(a),
                                          "rmse": float(np.sqrt(np.mean((arr[:, off + i] - arr[:, i]) ** 2)))}
        log(f"  {key}: carrier-aware gain az {e0[key]['carrier_aware_azimuth']['gain']:.3f} rg {e0[key]['carrier_aware_range']['gain']:.3f} "
            f"rmse {e0[key]['carrier_aware_azimuth']['rmse']:.4f}/{e0[key]['carrier_aware_range']['rmse']:.4f}; naive gain az "
            f"{e0[key]['naive_azimuth']['gain']:.3f} rg {e0[key]['naive_range']['gain']:.3f}")
    report["E0_calibration"] = e0
    save()
    gains = [e0[k][f"carrier_aware_{ax}"]["gain"] for k in ("coherence_1.0", "coherence_0.6") for ax in ("azimuth", "range")]
    if min(gains) < 0.9 or max(gains) > 1.1:
        raise SystemExit(f"carrier-aware estimator calibration failed: {gains}")

    # ------------------------------------------------------------------ E1
    log("E1 RSLC registration control")
    jobs = [(r, c) for r in range(WR0, WR1 - CHIP, CHIP) for c in range(WC0, WC1 - CHIP, CHIP)]
    with Pool(8) as pool:
        E1 = np.array([x for x in pool.map(_control, jobs, chunksize=16) if x is not None])
    cols = ["row", "col", "ref_coarse_az", "ref_coarse_rg", "ref_coarse_gamma", "ref_fine_az", "ref_fine_rg", "ref_fine_gamma",
            "coarse_fine_az", "coarse_fine_rg", "coarse_fine_gamma", "rubber_az", "rubber_rg"]
    np.savez_compressed(OUT / "E1_rslc_control_chips.npz", E1=E1, columns=np.array(cols))
    col = {c_: i for i, c_ in enumerate(cols)}
    q = (E1[:, col["ref_coarse_gamma"]] >= 0.5)
    for k_ in ("ref_coarse_az", "ref_coarse_rg", "ref_fine_az", "ref_fine_rg"):
        q &= np.abs(E1[:, col[k_]]) < 1
    qc = np.abs(E1[:, col["coarse_fine_az"]]) < 1
    grp = (E1[:, 0] // (4 * CHIP)) * 100000 + (E1[:, 1] // (4 * CHIP))
    e1 = {"chips": int(len(E1)), "chips_used": int(q.sum()),
          "selection": "reference-vs-coarse chip coherence >= 0.5 and all |displacements| < 1 sample",
          "ref_vs_geometry_only__vs_rubber_az": regress(E1[q, col["rubber_az"]], E1[q, col["ref_coarse_az"]], grp[q]),
          "ref_vs_geometry_only__vs_rubber_rg": regress(E1[q, col["rubber_rg"]], E1[q, col["ref_coarse_rg"]], grp[q]),
          "geometry_only_vs_rubber_sheet__vs_rubber_az": regress(E1[qc, col["rubber_az"]], E1[qc, col["coarse_fine_az"]], grp[qc]),
          "geometry_only_vs_rubber_sheet__vs_rubber_rg": regress(E1[qc, col["rubber_rg"]], E1[qc, col["coarse_fine_rg"]], grp[qc]),
          "ref_vs_rubber_sheet_residual": {ax: {"mean": float(E1[q, col[f"ref_fine_{ax}"]].mean()),
                                                "mean_ci95": boot_ci(E1[q, col[f"ref_fine_{ax}"]]),
                                                "std": float(E1[q, col[f"ref_fine_{ax}"]].std())} for ax in ("az", "rg")},
          "ref_vs_geometry_only_mean": {ax: {"mean": float(E1[q, col[f"ref_coarse_{ax}"]].mean()),
                                             "mean_ci95": boot_ci(E1[q, col[f"ref_coarse_{ax}"]]),
                                             "rubber_mean": float(E1[q, col[f"rubber_{ax}"]].mean())} for ax in ("az", "rg")}}
    report["E1_rslc_control"] = e1
    save()
    log(f"  ref vs geometry-only az: slope {e1['ref_vs_geometry_only__vs_rubber_az']['ols_slope']:.3f} "
        f"r {e1['ref_vs_geometry_only__vs_rubber_az']['pearson_r']:.3f}; rg slope {e1['ref_vs_geometry_only__vs_rubber_rg']['ols_slope']:.3f}; "
        f"coarse vs fine az slope {e1['geometry_only_vs_rubber_sheet__vs_rubber_az']['ols_slope']:.3f}; "
        f"fine residual az {e1['ref_vs_rubber_sheet_residual']['az']['mean']:+.4f} rg {e1['ref_vs_rubber_sheet_residual']['rg']['mean']:+.4f}")

    # ------------------------------------------------------------------ E2
    log("E2 spectral occupancy RSLC vs GSLC")
    win = np.outer(np.hanning(256), np.hanning(256))
    gp = "/science/LSAR/GSLC/grids/frequencyA"
    with h5py.File(GSLC / f"{REF_D}_gslc_freqA.h5", "r") as h:
        xg, yg = h[f"{gp}/xCoordinates"][()], h[f"{gp}/yCoordinates"][()]
        co, ro = int(round((X0 + PX / 2 - xg[0]) / PX)), int(round((yg[0] - (Y1 - PX / 2)) / PX))
        gblk = cfw.c64(h[f"{gp}/HH"][ro + NY // 2 - 2000:ro + NY // 2 + 2000, co + NX // 2 - 3000:co + NX // 2 + 3000])

    def spectra(get, n):
        acc, occ = 0.0, []
        while len(occ) < n:
            z = get()
            if np.count_nonzero(z) < 0.99 * z.size:
                continue
            S = np.abs(np.fft.fft2(z.astype(np.complex128) * win)) ** 2
            acc = acc + S
            Ss = uniform_filter(S, 9, mode="wrap")
            d = 10 * np.log10(Ss / Ss.max())
            occ.append([(d > -6).mean(), (d > -10).mean(), d.min()])
        return np.fft.fftshift(acc), np.array(occ)

    def rchip():
        r, c = int(RNG.integers(WR0, WR1 - 256)), int(RNG.integers(WC0, WC1 - 256))
        return np.asarray(G["ref"][r:r + 256, c:c + 256])

    def gchip():
        r, c = int(RNG.integers(0, 4000 - 256)), int(RNG.integers(0, 6000 - 256))
        return gblk[r:r + 256, c:c + 256]

    Sr, occ_r = spectra(rchip, 60)
    Sg, occ_g = spectra(gchip, 60)
    report["E2_spectral_occupancy"] = {
        "chip": 256, "chips": 60, "smoothing": "9x9 periodic box on the power spectrum",
        "RSLC_radar": {"fraction_within_6dB_median": float(np.median(occ_r[:, 0])), "fraction_within_10dB_median": float(np.median(occ_r[:, 1])),
                       "min_dB_median": float(np.median(occ_r[:, 2]))},
        "GSLC_lattice": {"fraction_within_6dB_median": float(np.median(occ_g[:, 0])), "fraction_within_10dB_median": float(np.median(occ_g[:, 1])),
                         "min_dB_median": float(np.median(occ_g[:, 2]))},
        "consequence": "band-limited sub-sample estimation / Fourier shifting is valid on the RSLC but not on the delivered GSLC samples"}
    for nm, S in (("f12_spectrum2d_rslc", Sr), ("f12_spectrum2d_gslc", Sg)):
        d = 10 * np.log10(S / S.max())
        rgba = (colormaps["magma"](np.clip((d + 30) / 30, 0, 1)) * 255).astype(np.uint8)
        rgba = np.asarray(Image.fromarray(rgba, "RGBA").resize((512, 512), Image.NEAREST))
        webp(nm, rgba, units="dB relative to peak", vrange=[-30, 0], cmap_stops=stops(colormaps["magma"]))
    del gblk
    save()
    log(f"  occupancy within 6 dB: RSLC {report['E2_spectral_occupancy']['RSLC_radar']['fraction_within_6dB_median']:.3f} "
        f"GSLC {report['E2_spectral_occupancy']['GSLC_lattice']['fraction_within_6dB_median']:.3f}")

    # ------------------------------------------------------------------ E3 closure
    log("E3 closure: geometry-only RSLC interferogram vs GSLC")
    ref = np.asarray(G["ref"][WR0:WR1, WC0:WC1])
    coa = np.asarray(G["coarse"][WR0:WR1, WC0:WC1])
    pa = np.abs(ref); pa *= pa
    pc = np.abs(coa); pc *= pc
    coh_c = geocode(cfw.box_coherence(ref * np.conj(coa), pa, pc))
    del pc
    fin = np.asarray(G["fine"][WR0:WR1, WC0:WC1])
    pf = np.abs(fin); pf *= pf
    coh_f = geocode(cfw.box_coherence(ref * np.conj(fin), pa, pf))
    del pa, pf, ref
    x_cf = geocode(unit(fin * np.conj(coa)))
    del coa, fin
    rc_ifg = r_ifg * x_cf
    del x_cf
    R8, G8, C8 = ml["R_full"], ml["G_crop"], cfw.multilook(rc_ifg, K)
    del rc_ifg
    bench8 = cfw.multilook(r_coh, K)

    def agree(p8, q8):
        e = cfw.phase_agreement(p8, q8, common8, K * PX / 1000, quadrants=False)
        e["by_benchmark_coherence"] = {f"{lo:.1f}-{min(hi, 1.0):.1f}": cfw.phase_agreement(
            p8, q8, common8 & (bench8 >= lo) & (bench8 < hi), K * PX / 1000, quadrants=False, plane=False).get("phase_diff_coherence")
            for lo, hi in BINS}
        return {k: e.get(k) for k in ("n", "phase_diff_coherence", "constant_offset_rad", "circular_std_rad", "planar_rad_per_km_x_east",
                                      "planar_rad_per_km_y_south", "phase_diff_coherence_after_planar", "by_benchmark_coherence")}

    e3 = {"definition": "Rc = R_full * unit(fine_secondary * conj(geometry_only_secondary)): the benchmark interferogram with "
                        "the rubber-sheet registration removed (the benchmark's flattening retained)",
          "phase_40m": {"R_full__vs__G_crop": agree(R8, G8), "R_full__vs__Rc": agree(R8, C8), "Rc__vs__G_crop": agree(C8, G8)}}
    cm = common & (coh_c > 0) & (coh_f > 0) & (g_coh > 0) & (r_coh > 0)
    m8 = cfw.multilook(cm.astype(np.float32), K) >= 0.999
    s8 = {k: cfw.multilook(np.where(cm, v, 0), K) for k, v in (("G", g_coh), ("R", r_coh), ("c", coh_c), ("f", coh_f))}
    ratio_bins = {}
    for lo, hi in BINS:
        mm = m8 & (s8["R"] >= lo) & (s8["R"] < hi)
        if mm.sum() > 100:
            ratio_bins[f"{lo:.1f}-{min(hi, 1.0):.1f}"] = {"G_over_R": float(np.median(s8["G"][mm] / s8["R"][mm])),
                                                         "coarse_over_fine": float(np.median(s8["c"][mm] / s8["f"][mm])), "n": int(mm.sum())}
    e3["coherence_ratio_40m_by_R_bin"] = ratio_bins
    e3["coherence_distributions"] = {"G_crop": cfw.dist(g_coh[cm]), "R_full_flat": cfw.dist(r_coh[cm]),
                                     "R_geometry_only_unflat": cfw.dist(coh_c[cm]), "R_rubber_sheet_unflat": cfw.dist(coh_f[cm])}
    FIGMETA["charts"]["coherence_hist"] = {"R_full": hist(r_coh[cm][::5], 0, 1, 50), "G_crop": hist(g_coh[cm][::5], 0, 1, 50),
                                           "R_geometry_only": hist(coh_c[cm][::5], 0, 1, 50), "bias_floor_3x3": math.sqrt(math.pi) / 6}
    bid = block_ids(m8.shape)
    nb = int(bid.max()) + 1
    use = m8 & (s8["R"] > 0.3)
    cnt = block_sum(bid, use, nb)
    ok = cnt > 200
    tot = {k: block_sum(bid, np.where(use, v, 0), nb) for k, v in s8.items()}
    ratio_G = tot["G"][ok] / tot["R"][ok]
    ratio_C = tot["c"][ok] / tot["f"][ok]
    grp_b = np.arange(int(ok.sum())) // 4
    e3["block_coherence_ratio"] = {"blocks": int(ok.sum()), "median_G_over_R": float(np.median(ratio_G)), "median_coarse_over_fine": float(np.median(ratio_C)),
                                   "regression_G_on_coarse": regress(ratio_C, ratio_G, grp_b),
                                   "paired_difference_G_minus_coarse": {"median": float(np.median(ratio_G - ratio_C)), "mean": float(np.mean(ratio_G - ratio_C)),
                                                                        "mean_ci95": boot_ci(ratio_G - ratio_C),
                                                                        "wilcoxon_p": float(stats.wilcoxon(ratio_G - ratio_C).pvalue)}}
    FIGMETA["charts"]["block_ratio_scatter"] = {"coarse_over_fine": [round(float(v), 4) for v in ratio_C], "G_over_R": [round(float(v), 4) for v in ratio_G]}

    def block_R(p8, q8):
        u = np.where(common8, unit(p8 * np.conj(q8)), 0)
        n_ = block_sum(bid, common8, nb)
        v = np.hypot(block_sum(bid, u.real, nb), block_sum(bid, u.imag, nb)) / np.maximum(n_, 1)
        return np.where(n_ > 200, v, np.nan)

    bRG, bCG = block_R(R8, G8), block_R(C8, G8)
    okp = np.isfinite(bRG) & np.isfinite(bCG)
    dd = bCG[okp] - bRG[okp]
    e3["block_phase_agreement_with_G"] = {"blocks": int(okp.sum()), "median_with_R_full": float(np.median(bRG[okp])), "median_with_Rc": float(np.median(bCG[okp])),
                                          "mean_gain": float(dd.mean()), "mean_gain_ci95": boot_ci(dd), "fraction_blocks_closer_to_Rc": float((dd > 0).mean()),
                                          "wilcoxon_p": float(stats.wilcoxon(dd).pvalue)}
    report["E3_closure"] = e3
    FIGMETA["charts"]["phase_hist_40m"] = dict(pdist["40m"])
    FIGMETA["charts"]["phase_hist_40m"]["Rc_geometry_only"] = hist(np.angle(C8[common8]), -math.pi, math.pi, 72)
    pdist["js_distance_to_R_full_40m"]["Rc_geometry_only"] = js_distance(pdist["40m"]["R_full"]["density"], FIGMETA["charts"]["phase_hist_40m"]["Rc_geometry_only"]["density"])
    pdist["js_distance_G_crop_to_Rc_geometry_only_40m"] = js_distance(FIGMETA["charts"]["phase_hist_40m"]["Rc_geometry_only"]["density"], pdist["40m"]["G_crop"]["density"])
    save()
    log(f"  phase 40 m: R-G {e3['phase_40m']['R_full__vs__G_crop']['phase_diff_coherence']:.4f}, Rc-G {e3['phase_40m']['Rc__vs__G_crop']['phase_diff_coherence']:.4f}, "
        f"R-Rc {e3['phase_40m']['R_full__vs__Rc']['phase_diff_coherence']:.4f}; coherence ratios {ratio_bins}")
    cmask8 = m8 & (s8["R"] > 0.3)
    webp("f13_cohratio_G_over_R", colorize(s8["G"] / np.maximum(s8["R"], 1e-6), 0.8, 1.2, cmask8, DIV), extent_km=ext,
         cmap_stops=stops(DIV), vrange=[0.8, 1.2], units="ratio")
    webp("f13_cohratio_coarse_over_fine", colorize(s8["c"] / np.maximum(s8["f"], 1e-6), 0.8, 1.2, cmask8, DIV), extent_km=ext,
         cmap_stops=stops(DIV), vrange=[0.8, 1.2], units="ratio")
    webp("f13_dphase_Rc_vs_G", colorize(np.angle(C8 * np.conj(G8)), -math.pi, math.pi, common8, DIV), extent_km=ext,
         cmap_stops=stops(DIV), vrange=[-math.pi, math.pi], units="rad")
    webp("f13_dphase_R_vs_Rc", colorize(np.angle(R8 * np.conj(C8)), -math.pi, math.pi, common8, DIV), extent_km=ext,
         cmap_stops=stops(DIV), vrange=[-math.pi, math.pi], units="rad")
    save()
    del coh_f

    # ------------------------------------------------------------------ E4
    log("E4 carrier coefficient")
    from nisar.products.readers import SLC
    slc_s = SLC(hdf5file=str(next((C / "L1_RSLC").glob(f"*_{SEC_D}T*.h5"))))
    dop = slc_s.getDopplerCentroid(frequency="A")
    rgc = SLC(hdf5file=str(C / "L1_RSLC_AOI_v2" / f"{REF_D}_aoi.h5")).getRadarGrid("A")
    line_rate = 1.0 / slc_s.getRadarGrid("A").az_time_interval
    tt = np.clip(rgc.sensing_start + (RI[valid] - AZ0) * rgc.az_time_interval, dop.y_start, dop.y_end)
    rr = np.clip(rgc.starting_range + (CI[valid] - RG0) * rgc.range_pixel_spacing, dop.x_start, dop.x_end)
    kmap = np.zeros((NY, NX), np.float32)
    kmap[valid] = 2 * np.pi * dop.eval(tt, rr) / line_rate
    del tt, rr
    vf = np.maximum(cfw.multilook(valid.astype(np.float32), K), 1e-6)
    kd8 = (cfw.multilook(np.where(valid, kmap, 0), K) / vf) * (cfw.multilook(np.where(valid, daz, 0), K) / vf)
    bgrid = np.round(np.arange(-2.0, 1.0001, 0.01), 3)
    Nn = block_sum(bid, common8, nb)
    keep = Nn > 50
    e4 = {"model": "R(b) = |mean exp(i(phi_R - phi_X - b k d_az))| at 40 m; b_hat maximises R; CI by bootstrap over blocks",
          "line_rate_hz": float(line_rate), "k_median": float(np.median(kmap[common]))}
    for label, X8 in (("G_crop", G8), ("Rc_geometry_only", C8)):
        u = np.where(common8, unit(R8 * np.conj(X8)), 0)
        S = np.zeros((nb, len(bgrid)), np.complex128)
        for j, bb in enumerate(bgrid):
            w = u * np.exp(-1j * bb * kd8)
            S[:, j] = block_sum(bid, w.real, nb) + 1j * block_sum(bid, w.imag, nb)
        Sk, Nk = S[keep], Nn[keep]
        Rb = np.abs(Sk.sum(0)) / Nk.sum()
        reps = [bgrid[int(np.argmax(np.abs(Sk[RNG.integers(0, len(Nk), len(Nk))].sum(0))))] for _ in range(2000)]
        e4[label] = {"b_hat": float(bgrid[int(np.argmax(Rb))]), "b_ci95": [float(np.percentile(reps, 2.5)), float(np.percentile(reps, 97.5))],
                     "R_at_b_hat": float(Rb.max()), "R_at_b_minus_1": float(Rb[int(np.argmin(np.abs(bgrid + 1)))]),
                     "R_at_b_0": float(Rb[int(np.argmin(np.abs(bgrid)))]), "blocks": int(keep.sum())}
        FIGMETA["charts"][f"carrier_curve_{label}"] = {"b": bgrid[::5].tolist(), "R": [round(float(v), 5) for v in Rb[::5]]}
        log(f"  {label}: b_hat {e4[label]['b_hat']:+.2f} CI {e4[label]['b_ci95']}  R(b=-1) {e4[label]['R_at_b_minus_1']:.4f}  R(0) {e4[label]['R_at_b_0']:.4f}")
    report["E4_carrier_coefficient"] = e4
    save()

    # ------------------------------------------------------------------ E5
    log("E5 coherence loss predicted from measured misregistration and RSLC spectra")
    prof_az = np.zeros(CHIP); prof_rg = np.zeros(CHIP)
    n_used = 0
    while n_used < 300:
        r, c = int(RNG.integers(WR0, WR1 - CHIP)), int(RNG.integers(WC0, WC1 - CHIP))
        z = np.asarray(G["ref"][r:r + CHIP, c:c + CHIP]).astype(np.complex128)
        if np.count_nonzero(z) < 0.99 * z.size:
            continue
        Sp = np.abs(np.fft.fft2(z * modulation(z.shape, carrier(z), -1))) ** 2
        prof_az += Sp.sum(1); prof_rg += Sp.sum(0)
        n_used += 1
    nu = np.fft.fftfreq(CHIP)

    def rho(prof, d):
        d = np.atleast_1d(np.asarray(d, np.float64))[:, None]
        return np.abs((prof[None, :] * np.exp(2j * np.pi * nu[None, :] * d)).sum(1)) / prof.sum()

    drg_lat = geocode(np.asarray(G["drg"][WR0:WR1, WC0:WC1], dtype=np.float32))
    daz8 = cfw.multilook(np.where(valid, daz, 0), K) / vf
    drg8 = cfw.multilook(np.where(valid, drg_lat, 0), K) / vf
    pred8 = (rho(prof_az, daz8.ravel()) * rho(prof_rg, drg8.ravel())).reshape(daz8.shape)
    pb = block_sum(bid, np.where(use, pred8, 0), nb)[ok] / np.maximum(cnt[ok], 1)
    e5 = {"spectra": "mean demodulated RSLC power profiles (300 chips); rho(d) = |sum P(nu) exp(i 2 pi nu d)| / sum P",
          "rho_az_at": {f"{d:.2f}": float(rho(prof_az, d)[0]) for d in (0.05, 0.1, 0.2, 0.3)},
          "rho_rg_at": {f"{d:.2f}": float(rho(prof_rg, d)[0]) for d in (0.05, 0.1, 0.2, 0.25, 0.3)},
          "misregistration_40m_medians": {"az_lines": float(np.median(daz8[use])), "rg_samples": float(np.median(drg8[use]))},
          "predicted_factor_median": float(np.median(pred8[use])),
          "observed_median_coarse_over_fine": e3["block_coherence_ratio"]["median_coarse_over_fine"],
          "observed_median_G_over_R": e3["block_coherence_ratio"]["median_G_over_R"],
          "blocks_observed_coarse_on_predicted": regress(pb, ratio_C, grp_b),
          "blocks_observed_G_on_predicted": regress(pb, ratio_G, grp_b),
          "note": "prediction ignores estimator bias; blocks restricted to benchmark coherence > 0.3"}
    FIGMETA["charts"]["spectral_profiles_rslc"] = {"nu": np.fft.fftshift(nu).round(4).tolist(),
                                                  "azimuth": [round(float(v), 5) for v in np.fft.fftshift(prof_az / prof_az.max())],
                                                  "range": [round(float(v), 5) for v in np.fft.fftshift(prof_rg / prof_rg.max())]}
    FIGMETA["charts"]["block_ratio_scatter"]["predicted"] = [round(float(v), 4) for v in pb]
    report["E5_predicted_coherence_loss"] = e5
    save()
    log(f"  predicted factor median {e5['predicted_factor_median']:.4f}; observed coarse/fine {e5['observed_median_coarse_over_fine']:.4f}, "
        f"G/R {e5['observed_median_G_over_R']:.4f}; rho_rg(0.25) {e5['rho_rg_at']['0.25']:.4f}")

    # ------------------------------------------------------------------ S
    log("S sampling and sharpness")
    dup_x = (RI[:, 1:] == RI[:, :-1]) & (CI[:, 1:] == CI[:, :-1]) & common[:, 1:] & common[:, :-1]
    dup_y = (RI[1:, :] == RI[:-1, :]) & (CI[1:, :] == CI[:-1, :]) & common[1:, :] & common[:-1, :]
    uniq = np.unique((RI[common].astype(np.int64) << 20) + CI[common].astype(np.int64)).size
    with h5py.File(GSLC / f"{REF_D}_gslc_freqA.h5", "r") as h:
        Ig = np.abs(cfw.c64(h[f"{gp}/HH"][ro:ro + NY, co:co + NX])) ** 2
    Ir = geocode(np.abs(np.asarray(G["ref"][WR0:WR1, WC0:WC1])) ** 2)

    def contrast(I, m, w=7):
        n = np.maximum(uniform_filter(m.astype(np.float32), w), 1e-6)
        mu = uniform_filter(np.where(m, I, 0), w) / n
        mu2 = uniform_filter(np.where(m, I * I, 0), w) / n
        return np.sqrt(np.maximum(mu2 - mu * mu, 0)) / np.maximum(mu, 1e-12)

    cmask = common & (Ir > 0) & (Ig > 0)
    full7 = uniform_filter(cmask.astype(np.float32), 7) > 0.999
    cr, cg = contrast(Ir, cmask), contrast(Ig, cmask)

    def acf_fwhm(chips):
        acc = 0.0
        for I in chips:
            x = I - I.mean()
            a = np.fft.fftshift(np.real(np.fft.ifft2(np.abs(np.fft.fft2(x)) ** 2)))
            acc = acc + a / a.max()
        acc = acc / len(chips)
        c0, c1 = acc.shape[0] // 2, acc.shape[1] // 2

        def fw(p):
            p = p[len(p) // 2:]
            i = int(np.argmax(p < 0.5))
            return float(2 * (i - 1 + (p[i - 1] - 0.5) / (p[i - 1] - p[i]))) if i > 0 else float("nan")
        return [fw(acc[:, c1]), fw(acc[c0, :])]

    picks = []
    while len(picks) < 300:
        r, c = int(RNG.integers(0, NY - CHIP)), int(RNG.integers(0, NX - CHIP))
        if cmask[r:r + CHIP, c:c + CHIP].all():
            picks.append((r, c))
    fr = acf_fwhm([Ir[r:r + CHIP, c:c + CHIP].astype(np.float64) for r, c in picks])
    fg = acf_fwhm([Ig[r:r + CHIP, c:c + CHIP].astype(np.float64) for r, c in picks])
    rad = []
    while len(rad) < 300:
        r, c = int(RNG.integers(WR0, WR1 - CHIP)), int(RNG.integers(WC0, WC1 - CHIP))
        z = np.asarray(G["ref"][r:r + CHIP, c:c + CHIP])
        if np.count_nonzero(z) == z.size:
            rad.append((np.abs(z) ** 2).astype(np.float64))
    fa = acf_fwhm(rad)
    az_m, rg_m = rep["C1_dense_offsets"]["azimuth_sample_spacing_m"], rep["C1_dense_offsets"]["range_sample_spacing_m"]
    report["S_sampling_sharpness"] = {
        "lookup_nearest_neighbour": {"fraction_duplicating_east_neighbour": float(dup_x.sum() / common.sum()),
                                     "fraction_duplicating_south_neighbour": float(dup_y.sum() / common.sum()),
                                     "unique_radar_samples_per_cell": float(uniq / common.sum())},
        "speckle_contrast_7x7": {"R_full_on_lattice_median": float(np.median(cr[full7])), "G_crop_median": float(np.median(cg[full7])),
                                 "paired_ratio_G_over_R_median": float(np.median(cg[full7] / np.maximum(cr[full7], 1e-12)))},
        "intensity_acf_fwhm": {"R_full_on_lattice_cells_rows_cols": fr, "G_crop_cells_rows_cols": fg,
                               "R_radar_samples_az_rg": fa, "R_radar_m_az_slantrange": [fa[0] * az_m, fa[1] * rg_m]}}
    save()
    s_ = report["S_sampling_sharpness"]
    log(f"  duplicates E/S {s_['lookup_nearest_neighbour']['fraction_duplicating_east_neighbour']:.3f}/"
        f"{s_['lookup_nearest_neighbour']['fraction_duplicating_south_neighbour']:.3f}; contrast R {s_['speckle_contrast_7x7']['R_full_on_lattice_median']:.3f} "
        f"G {s_['speckle_contrast_7x7']['G_crop_median']:.3f}; ACF FWHM R {fr} G {fg} cells, radar {fa} samples")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
