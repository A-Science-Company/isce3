#!/usr/bin/env python3
"""
Are the two GSLC dates co-registered, and does aligning them close the gap to the RSLC benchmark?

    python -u tools/alignment_test.py            # after compare_four_way.py v2

Read-only on every product. Writes <case>/comparison_v2/alignment/ (alignment.json, chip tables,
figures under comparison_v2/report/fig/f1x_*.webp + alignment/figures_alignment.json).

Background
----------
The RSLC chain registers the secondary twice: geometry only (geo2rdr -> coarse_resample), then a
dense-offset "rubber sheet" correction (fine_resample). The GSLC chain geocodes each date from its own
orbit and the DEM and never registers the dates to each other: its inter-date registration is
geometry-only, like the RSLC *coarse* stage. If geometry leaves a residual misregistration d, the
RSLC chain removes it and the GSLC chain keeps it, and a zero-Doppler SLC with Doppler centroid f_dc
turns an azimuth misregistration into interferometric phase k*d (k = 2 pi f_dc / line rate) and
into coherence loss. comparison_v2 I1b showed the R-G phase difference follows -k * d_az. This tool
tests the mechanism directly.

Stages
------
P   phase distributions of the four legs (wrapped flattened phase, 40 m and 5 m) and of the local
    phase dispersion (1 - 3x3 phase-only coherence), with Jensen-Shannon distances to R_full.
E0  synthetic calibration of the offset estimator (known sub-pixel shifts, real speckle, non-periodic
    crops) -> sign convention, bias, precision.
E1  RSLC control, radar domain (R_crop scratch; R_crop coregistration == R_full, C1/C2):
    reference vs geometry-only secondary must reproduce ISCE3's dense offsets;
    reference vs rubber-sheet secondary must give ~0.
E2  GSLC inter-date offsets on the 5 m lattice, converted to radar lines/samples with the local
    Jacobian of the geolocation lookup, regressed on the RSLC rubber-sheet correction
    (block bootstrap CIs).
E3  Realignment of the GSLC secondary (tile-wise Fourier shift, Hann-blended):
    A = GSLC-measured field, B = field predicted from the RSLC rubber sheet (no fit to GSLC data),
    B- = B with the sign flipped (null test). Each variant: 40 m phase agreement with R_full,
    coherence vs R_full, paired block statistics (Wilcoxon signed-rank, bootstrap CI).
E4  Doppler-carrier coefficient b in  phi_R - phi_G = a + b k d_az  with a block-bootstrap CI (theory -1).
S   Sharpness: lookup duplicate fraction, speckle contrast, intensity autocorrelation width, spectra.
"""

from __future__ import annotations

import importlib.util
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
from scipy.ndimage import gaussian_filter, map_coordinates, median_filter, uniform_filter  # noqa: E402
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
SF = C / "scratch" / "trackR" / TAG
SC = C / "aoi_v2" / "scratch" / "trackR" / TAG
GSLC = C / "aoi_v2" / "L2_GSLC"
P, PV = C / "pairs" / PAIR, C / "aoi_v2" / "pairs" / PAIR
CHIP, UP = 128, 100
K = 8
RNG = np.random.default_rng(20260915)
T0 = time.time()
report: dict = {"tool": "alignment_test v2 (complex estimator)", "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "conventions": {
                    "estimator_shift": "skimage phase_cross_correlation(reference, moving): the shift that, applied to "
                                       "moving with scipy fourier_shift, registers it onto reference; displacement of "
                                       "moving relative to reference = -shift",
                    "displacement_units": "radar lines / samples (E1, E2) or lattice cells (rows south, cols east)",
                    "pair_key": "P__vs__Q = P minus Q"}}
G: dict = {}          # arrays shared with forked workers


def log(m):
    print(f"[{time.time() - T0:7.1f}s] {m}", flush=True)


def save():
    (OUT / "alignment.json").write_text(json.dumps(report, indent=2))


# ----------------------------------------------------------------------------- estimator
_TW = tukey(CHIP, 0.25)
TAPER = np.outer(_TW, _TW)


def carrier_roll(a, b):
    """Integer spectral roll that centres the chips' joint power spectrum (per axis, circular mean)."""
    Pw = np.abs(np.fft.fft2(a)) ** 2 + np.abs(np.fft.fft2(b)) ** 2
    out = []
    for axis, n in ((1, a.shape[0]), (0, a.shape[1])):
        prof = Pw.sum(axis=axis)
        ang = np.angle(np.sum(prof * np.exp(2j * np.pi * np.arange(n) / n)))
        out.append(int(round(ang / (2 * np.pi) * n)) % n)
    return out


def estimate(a, b):
    """(shift_row, shift_col) in input samples, chip coherence (twice, for table compatibility).

    COMPLEX cross-correlation of Tukey-tapered chips without spectral whitening, refined by upsampled DFT
    (factor 100). The complex SLC is band-limited, so the refinement is exact; an amplitude (detected)
    estimator pixel-locked on this data (0.3-sample synthetic shifts read as 0.08). Benchmarked on real
    GSLC chips with known shifts: slope 0.987, rmse 0.004 / 0.007 / 0.011 samples at coherence 1 / 0.6 / 0.4.
    """
    a = np.nan_to_num(a.astype(np.complex128))
    b = np.nan_to_num(b.astype(np.complex128))
    den = math.sqrt(float(np.sum(np.abs(a) ** 2) * np.sum(np.abs(b) ** 2)))
    gam = float(abs(np.sum(a * np.conj(b))) / den) if den > 0 else 0.0
    s, _, _ = phase_cross_correlation(a * TAPER, b * TAPER, upsample_factor=UP, normalization=None)
    return float(s[0]), float(s[1]), gam, gam


# ----------------------------------------------------------------------------- shifting
def fourier_shift_tile(t, sy, sx):
    ky = np.fft.fftfreq(t.shape[0])[:, None]
    kx = np.fft.fftfreq(t.shape[1])[None, :]
    return np.fft.ifft2(np.fft.fft2(t) * np.exp(-2j * np.pi * (ky * sy + kx * sx)))


def apply_shift_field(z, sy_field, sx_field, tile=256):
    """Shift z by a slowly varying field (samples) with 50%-overlap periodic-Hann tiles (sum of windows = 1)."""
    h = tile // 2
    ny, nx = z.shape
    zp = np.zeros((ny + 2 * tile, nx + 2 * tile), np.complex64)
    zp[tile:tile + ny, tile:tile + nx] = z
    out = np.zeros_like(zp)
    w1 = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(tile) / tile)
    w2 = np.outer(w1, w1).astype(np.float32)
    for r0 in range(0, zp.shape[0] - tile + 1, h):
        for c0 in range(0, zp.shape[1] - tile + 1, h):
            yc = min(max(r0 + h - tile, 0), ny - 1)
            xc = min(max(c0 + h - tile, 0), nx - 1)
            sy, sx = float(sy_field(yc, xc)), float(sx_field(yc, xc))
            t = zp[r0:r0 + tile, c0:c0 + tile]
            if not t.any():
                continue
            out[r0:r0 + tile, c0:c0 + tile] += (fourier_shift_tile(t, sy, sx) * w2).astype(np.complex64)
    return out[tile:tile + ny, tile:tile + nx]


# ----------------------------------------------------------------------------- statistics
def block_ids(shape, bs):
    return (np.arange(shape[0])[:, None] // bs) * ((shape[1] + bs - 1) // bs) + (np.arange(shape[1])[None, :] // bs)


def bootstrap_ci(values, fn, n=2000):
    idx = RNG.integers(0, len(values), size=(n, len(values)))
    reps = np.array([fn(values[i]) for i in idx])
    return [float(np.percentile(reps, 2.5)), float(np.percentile(reps, 97.5))]


def regress(x, y, groups):
    """OLS y = a + b x with block-bootstrap CIs over `groups`; also reverse slope and TLS slope."""
    ok = np.isfinite(x) & np.isfinite(y)
    x, y, groups = x[ok], y[ok], groups[ok]
    ug = np.unique(groups)
    members = [np.flatnonzero(groups == g) for g in ug]

    def fit(ix):
        xx, yy = x[ix], y[ix]
        b, a = np.polyfit(xx, yy, 1)
        return a, b

    a, b = fit(np.arange(len(x)))
    reps = []
    for _ in range(2000):
        pick = RNG.integers(0, len(members), len(members))
        ix = np.concatenate([members[i] for i in pick])
        reps.append(fit(ix))
    reps = np.array(reps)
    cxy = np.cov(x, y)
    tls = (cxy[1, 1] - cxy[0, 0] + math.sqrt((cxy[1, 1] - cxy[0, 0]) ** 2 + 4 * cxy[0, 1] ** 2)) / (2 * cxy[0, 1])
    return {"n": int(len(x)), "blocks": int(len(ug)), "intercept": float(a), "slope": float(b),
            "slope_ci95": [float(np.percentile(reps[:, 1], 2.5)), float(np.percentile(reps[:, 1], 97.5))],
            "intercept_ci95": [float(np.percentile(reps[:, 0], 2.5)), float(np.percentile(reps[:, 0], 97.5))],
            "pearson_r": float(np.corrcoef(x, y)[0, 1]), "slope_reverse_regression": float(1 / np.polyfit(y, x, 1)[0]),
            "slope_total_least_squares": float(tls),
            "residual_std": float(np.std(y - (a + b * x))), "median_y_minus_x": float(np.median(y - x)),
            "std_x": float(np.std(x)), "std_y": float(np.std(y))}


def binned(x, y, lo, hi, nb=16):
    e = np.linspace(lo, hi, nb + 1)
    out = []
    for i in range(nb):
        m = (x >= e[i]) & (x < e[i + 1]) & np.isfinite(y)
        if m.sum() >= 10:
            out.append({"x": float((e[i] + e[i + 1]) / 2), "y_median": float(np.median(y[m])),
                        "y_p25": float(np.percentile(y[m], 25)), "y_p75": float(np.percentile(y[m], 75)), "n": int(m.sum())})
    return out


def hist(values, lo, hi, bins):
    h, e = np.histogram(values, bins=bins, range=(lo, hi), density=True)
    return {"centers": [round(float(c), 4) for c in (e[:-1] + e[1:]) / 2], "density": [round(float(v), 5) for v in h]}


def js_distance(p, q):
    p = np.asarray(p, float) + 1e-12; q = np.asarray(q, float) + 1e-12
    p /= p.sum(); q /= q.sum(); m = 0.5 * (p + q)
    return float(math.sqrt(0.5 * np.sum(p * np.log2(p / m)) + 0.5 * np.sum(q * np.log2(q / m))))


def circ(z):
    z = z[np.abs(z) > 0]
    u = z / np.abs(z)
    mm = u.mean()
    return {"circular_mean_rad": float(np.angle(mm)), "mean_resultant_length": float(abs(mm)),
            "circular_std_rad": float(math.sqrt(max(0.0, -2 * math.log(max(abs(mm), 1e-12)))))}


# ----------------------------------------------------------------------------- workers
def _radar_chip(args):
    r, c = args
    ref = G["ref"][r:r + CHIP, c:c + CHIP]
    if np.count_nonzero(ref) < 0.95 * CHIP * CHIP:
        return None
    out = [r, c]
    for key in ("coarse", "fine"):
        sec = G[key][r:r + CHIP, c:c + CHIP]
        if np.count_nonzero(sec) < 0.95 * CHIP * CHIP:
            return None
        out += list(estimate(ref, sec))
    out += [float(np.mean(G["daz"][r:r + CHIP, c:c + CHIP])), float(np.mean(G["drg"][r:r + CHIP, c:c + CHIP]))]
    return out


def _gslc_chip(args):
    r, c = args
    m = G["mask"][r:r + CHIP, c:c + CHIP]
    if m.mean() < 0.8:
        return None
    a = np.where(m, G["gref"][r:r + CHIP, c:c + CHIP], 0)
    b = np.where(m, G["gsec"][r:r + CHIP, c:c + CHIP], 0)
    sy, sx, peak, gam = estimate(a, b)
    v = G["valid"][r:r + CHIP, c:c + CHIP]
    rr, cc = np.nonzero(v)
    if rr.size < 0.8 * CHIP * CHIP:
        return None
    Amat = np.column_stack([np.ones(rr.size), rr, cc])
    jr = np.linalg.lstsq(Amat, G["RI"][r:r + CHIP, c:c + CHIP][v].astype(np.float64), rcond=None)[0]
    jc = np.linalg.lstsq(Amat, G["CI"][r:r + CHIP, c:c + CHIP][v].astype(np.float64), rcond=None)[0]
    daz = float(np.mean(G["daz"][r:r + CHIP, c:c + CHIP][v]))
    drg = float(np.mean(G["drg"][r:r + CHIP, c:c + CHIP][v]))
    return [r, c, sy, sx, peak, gam, jr[1], jr[2], jc[1], jc[2], daz, drg]


def _synthetic(args):
    r, c, dy, dx, gam, r2, c2 = args
    big = G["gref"][r:r + 2 * CHIP, c:c + 2 * CHIP].astype(np.complex128)
    oth = G["gref"][r2:r2 + 2 * CHIP, c2:c2 + 2 * CHIP].astype(np.complex128)
    if np.count_nonzero(big) < 0.95 * big.size or np.count_nonzero(oth) < 0.95 * oth.size:
        return None
    sh = fourier_shift_tile(big, dy, dx)
    oth = oth * math.sqrt(np.mean(np.abs(sh) ** 2) / max(np.mean(np.abs(oth) ** 2), 1e-12))
    mix = gam * sh + math.sqrt(1 - gam * gam) * oth          # independent speckle with the same spectrum
    o = CHIP // 2
    sy, sx, _, _ = estimate(big[o:o + CHIP, o:o + CHIP], mix[o:o + CHIP, o:o + CHIP])
    return [dy, dx, sy, sx]


# ----------------------------------------------------------------------------- figures
def webp(name, rgba, **info):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "WEBP", quality=80, method=6)
    (FIG / f"{name}.webp").write_bytes(buf.getvalue())
    FIGMETA["figures"][name] = {"file": f"fig/{name}.webp", "px": [rgba.shape[1], rgba.shape[0]],
                                "kb": round(len(buf.getvalue()) / 1024), **info}
    log(f"  figure {name} {rgba.shape[1]}x{rgba.shape[0]}")


FIGMETA: dict = {"figures": {}, "charts": {}}


def colorize(values, lo, hi, mask, cmap):
    x = np.clip((np.nan_to_num(values) - lo) / (hi - lo), 0, 1)
    rgba = (cmap(x) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(mask, 255, 0)
    return rgba


# ============================================================================= main
def main() -> int:
    from matplotlib.colors import LinearSegmentedColormap, to_hex
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "alignment.json").exists() and "--force" not in _ARGV:
        raise SystemExit(f"{OUT / 'alignment.json'} exists; pass --force to recompute")
    DIV = LinearSegmentedColormap.from_list("div", ["#1c5cab", "#6da7ec", "#f0efec", "#ee8a89", "#b8302f"])
    div_stops = [to_hex(DIV(i / 10)) for i in range(11)]

    rep = json.loads((CMP / "comparison.json").read_text())
    X0, Y1 = rep["lattice"]["origin"]
    PX = rep["lattice"]["px_m"]
    NY, NX = rep["lattice"]["shape"]
    AZ0, RG0, L, W = rep["crop_geometry"]["reference_A"]
    WR0, WR1, WC0, WC1 = rep["radar_data_window_crop_frame"]
    ext = [NX * PX / 1000, NY * PX / 1000]

    # ------------------------------------------------------------------ inputs on the lattice
    log("loading lattice layers")
    aoi = cfw.read_tif(CMP / "aoi_polygon_mask.tif").astype(bool)
    lut = LAY / "lut_fullframe_rowcol.tif"
    RI, CI, RES = cfw.read_tif(lut, 1), cfw.read_tif(lut, 2), cfw.read_tif(lut, 3)
    valid = (RI >= 0) & (CI >= 0)
    geo_ok = valid & (RES <= 15.0)
    RI, CI = RI.astype(np.int32), CI.astype(np.int32)
    del RES
    r_ifg = cfw.read_tif(LAY / "R_full_ifg.tif")
    r_coh = cfw.read_tif(LAY / "R_full_coh_flat.tif")
    daz = cfw.read_tif(LAY / "R_full_dense_azimuth_residual_lines.tif")

    with h5py.File(GSLC / f"{REF_D}_gslc_freqA.h5", "r") as h:
        gp = "/science/LSAR/GSLC/grids/frequencyA"
        xg, yg = h[f"{gp}/xCoordinates"][()], h[f"{gp}/yCoordinates"][()]
    c_off = (X0 + PX / 2 - xg[0]) / PX
    r_off = (yg[0] - (Y1 - PX / 2)) / PX
    if abs(c_off - round(c_off)) > 1e-6 or abs(r_off - round(r_off)) > 1e-6:
        raise SystemExit(f"GSLC grid not aligned with the lattice: {c_off}, {r_off}")
    c_off, r_off = int(round(c_off)), int(round(r_off))
    gz = {}
    for d in (REF_D, SEC_D):
        with h5py.File(GSLC / f"{d}_gslc_freqA.h5", "r") as h:
            gz[d] = cfw.c64(h[f"{gp}/HH"][r_off:r_off + NY, c_off:c_off + NX])
    gref, gsec = gz[REF_D], gz[SEC_D]
    del gz

    # gate: our ref x conj(sec) reproduces the G_crop interferogram product
    gcc, grc = cfw.lattice_offset(PV / "trackG" / "ifg_A_HH_1x1.igram.tif", X0, Y1)
    yy0, xx0 = NY // 2 - 256, NX // 2 - 256
    prod = cfw.c64(cfw.read_window(PV / "trackG" / "ifg_A_HH_1x1.igram.tif", gcc + xx0, grc + yy0, 512, 512))
    mine = gref[yy0:yy0 + 512, xx0:xx0 + 512] * np.conj(gsec[yy0:yy0 + 512, xx0:xx0 + 512])
    both = (prod != 0) & (mine != 0)
    gate_dphi = float(np.max(np.abs(np.angle(prod[both] * np.conj(mine[both]))))) if both.any() else float("nan")
    report["gates"] = {"gslc_grid_offset_rows_cols": [r_off, c_off], "reproduces_G_crop_ifg_max_abs_dphi_rad": gate_dphi,
                       "reproduces_G_crop_ifg_n": int(both.sum())}
    if not (both.sum() > 1000 and gate_dphi < 1e-3):
        raise SystemExit(f"GSLC read does not reproduce the G_crop interferogram (max dphi {gate_dphi})")
    log(f"gate: GSLC read reproduces G_crop ifg, max |dphi| {gate_dphi:.2e} rad")

    g_ifg = gref * np.conj(gsec)
    common = aoi & geo_ok & (r_ifg != 0) & (g_ifg != 0)
    common8 = cfw.multilook(common.astype(np.float32), K) >= 0.75
    report["common_mask_fraction_of_aoi"] = float(common.sum() / aoi.sum())
    save()

    # ------------------------------------------------------------------ P: phase distributions
    log("P phase distributions")
    legs = {"R_full": r_ifg, "R_crop": cfw.read_tif(LAY / "R_crop_ifg.tif"), "G_full": None, "G_crop": g_ifg}
    Gf = P / "trackG" / "ifg_A_HH.igram.tif"
    gcf, grf = cfw.lattice_offset(Gf, X0, Y1)
    legs["G_full"] = cfw.c64(cfw.read_window(Gf, gcf, grf, NX, NY))
    pdist = {"40m": {}, "5m": {}, "circular_40m": {}, "js_distance_to_R_full_40m": {}, "js_distance_to_R_full_5m": {}}
    ml = {k: cfw.multilook(v, K) for k, v in legs.items()}
    sub5 = common[::3, ::3]
    for k in legs:
        z40 = ml[k][common8]
        pdist["40m"][k] = hist(np.angle(z40), -math.pi, math.pi, 72)
        pdist["circular_40m"][k] = circ(z40)
        pdist["5m"][k] = hist(np.angle(legs[k][::3, ::3][sub5]), -math.pi, math.pi, 72)
    for k in legs:
        pdist["js_distance_to_R_full_40m"][k] = js_distance(pdist["40m"]["R_full"]["density"], pdist["40m"][k]["density"])
        pdist["js_distance_to_R_full_5m"][k] = js_distance(pdist["5m"]["R_full"]["density"], pdist["5m"][k]["density"])
    # local phase dispersion: 1 - |3x3 mean of unit phasors|, each leg in its native sampling for R (radar) and lattice for G
    def phase_only_coh(z):
        u = np.where(np.abs(z) > 0, z / np.maximum(np.abs(z), 1e-30), 0).astype(np.complex64)
        re = uniform_filter(u.real, 3, mode="constant"); im = uniform_filter(u.imag, 3, mode="constant")
        return np.hypot(re, im)
    with h5py.File(SF / "RIFG.h5", "r") as h:
        zr = h[cfw.IFGP][AZ0 + WR0:AZ0 + WR1, RG0 + WC0:RG0 + WC1]
    pr = phase_only_coh(np.nan_to_num(zr)); del zr
    pr_lat = np.zeros((NY, NX), np.float32)
    pr_lat[valid] = pr[RI[valid] - AZ0 - WR0, CI[valid] - RG0 - WC0]; del pr
    pg = phase_only_coh(g_ifg)
    cm = common & (pr_lat > 0) & (pg > 0)
    pdist["phase_only_coherence_3x3"] = {"R_full_radar_3x3": hist(pr_lat[cm][::5], 0, 1, 50), "G_crop_lattice_3x3": hist(pg[cm][::5], 0, 1, 50),
                                         "median_R": float(np.median(pr_lat[cm])), "median_G": float(np.median(pg[cm]))}
    del pr_lat, pg
    report["P_phase_distributions"] = pdist
    save()
    legs.clear()
    log(f"  JS distance to R_full (40 m): { {k: round(v, 4) for k, v in pdist['js_distance_to_R_full_40m'].items()} }")

    # ------------------------------------------------------------------ E0: synthetic calibration
    log("E0 synthetic estimator calibration")
    G.update({"gref": gref, "gsec": gsec})
    cand = [(r, c) for r in range(0, NY - 2 * CHIP, 2 * CHIP) for c in range(0, NX - 2 * CHIP, 2 * CHIP)
            if common[r:r + 2 * CHIP, c:c + 2 * CHIP].mean() > 0.95]
    e0 = {"true_range_samples": [-0.3, 0.3], "chips_per_coherence": 300}
    for gam in (1.0, 0.6):
        pick = [cand[i] for i in RNG.choice(len(cand), size=min(300, len(cand)), replace=False)]
        other = [cand[i] for i in RNG.choice(len(cand), size=len(pick), replace=True)]
        jobs = [(r, c, float(d[0]), float(d[1]), gam, r2, c2)
                for (r, c), (r2, c2), d in zip(pick, other, RNG.uniform(-0.3, 0.3, (len(pick), 2)))]
        with Pool(8) as pool:
            syn = np.array([x for x in pool.map(_synthetic, jobs, chunksize=8) if x is not None])
        key = f"coherence_{gam:.1f}"
        e0[key] = {"n": int(len(syn))}
        for i, ax in ((0, "row"), (1, "col")):
            disp = -syn[:, 2 + i]
            b, a = np.polyfit(syn[:, i], disp, 1)
            e0[key][ax] = {"slope_measured_displacement_vs_true_shift": float(b), "intercept": float(a),
                           "rmse": float(np.sqrt(np.mean((disp - syn[:, i]) ** 2))),
                           "pearson_r": float(np.corrcoef(syn[:, i], disp)[0, 1])}
    e0["note"] = ("moving = reference Fourier-shifted by the true shift, mixed with independent speckle from another chip "
                  "to the stated coherence, both cropped to the central chip (non-periodic)")
    sl = [e0[k][ax]["slope_measured_displacement_vs_true_shift"] for k in ("coherence_1.0", "coherence_0.6") for ax in ("row", "col")]
    if min(sl) < 0.9 or max(sl) > 1.1:
        report["E0_synthetic_calibration"] = e0
        save()
        raise SystemExit(f"estimator calibration failed: slopes {sl}")
    report["E0_synthetic_calibration"] = e0
    save()
    for k in ("coherence_1.0", "coherence_0.6"):
        log(f"  {k}: row slope {e0[k]['row']['slope_measured_displacement_vs_true_shift']:.3f} rmse {e0[k]['row']['rmse']:.4f}; "
            f"col slope {e0[k]['col']['slope_measured_displacement_vs_true_shift']:.3f} rmse {e0[k]['col']['rmse']:.4f}")

    # ------------------------------------------------------------------ E1: RSLC control
    log("E1 RSLC control in radar geometry (R_crop scratch)")
    shp = (L, W)
    G["ref"] = np.memmap(SC / "crossmul/freqA/HH/reference.slc", dtype="<c8", mode="r", shape=shp)
    G["coarse"] = np.memmap(SC / "coarse_resample_slc/freqA/HH/coregistered_secondary.slc", dtype="<c8", mode="r", shape=shp)
    G["fine"] = np.memmap(SC / "fine_resample_slc/freqA/HH/coregistered_secondary.slc", dtype="<c8", mode="r", shape=shp)
    G["daz"] = np.memmap(SC / "rubbersheet_offsets/freqA/HH/resampled_az_offsets", dtype="<f8", mode="r", shape=shp)
    G["drg"] = np.memmap(SC / "rubbersheet_offsets/freqA/HH/resampled_rg_offsets", dtype="<f8", mode="r", shape=shp)
    jobs = [(r, c) for r in range(WR0, WR1 - CHIP, CHIP) for c in range(WC0, WC1 - CHIP, CHIP)]
    with Pool(8) as pool:
        rows = [x for x in pool.map(_radar_chip, jobs, chunksize=16) if x is not None]
    E1 = np.array(rows)
    np.savez_compressed(OUT / "E1_rslc_control_chips.npz", E1=E1,
                        columns=np.array(["row", "col", "coarse_sy", "coarse_sx", "coarse_gamma_dup", "coarse_gamma",
                                          "fine_sy", "fine_sx", "fine_gamma_dup", "fine_gamma", "rubber_az", "rubber_rg"]))
    good = E1[:, 5] >= 0.3
    grp = (E1[:, 0] // (4 * CHIP)) * 1000 + (E1[:, 1] // (4 * CHIP))
    e1 = {"chips_total": int(len(E1)), "chips_gamma_ge_0.3": int(good.sum()), "chip_size": CHIP,
          "coarse_vs_rubber_az": regress(E1[good, 10], -E1[good, 2], grp[good]),
          "coarse_vs_rubber_rg": regress(E1[good, 11], -E1[good, 3], grp[good]),
          "fine_residual_az": {"median": float(np.median(-E1[good, 6])), "std": float(np.std(-E1[good, 6])),
                               "mad": float(stats.median_abs_deviation(-E1[good, 6]))},
          "fine_residual_rg": {"median": float(np.median(-E1[good, 7])), "std": float(np.std(-E1[good, 7])),
                               "mad": float(stats.median_abs_deviation(-E1[good, 7]))},
          "rubber_az_chip_mean": {"median": float(np.median(E1[good, 10])), "std": float(np.std(E1[good, 10]))},
          "binned_az": binned(E1[good, 10], -E1[good, 2], -0.1, 0.3),
          "interpretation_key": "displacement = -estimator shift; ISCE3 rubber-sheet offsets in lines/samples"}
    report["E1_rslc_control"] = e1
    save()
    s_az = float(np.sign(e1["coarse_vs_rubber_az"]["slope"]))
    s_rg = float(np.sign(e1["coarse_vs_rubber_rg"]["slope"]))
    log(f"  coarse vs rubber az: slope {e1['coarse_vs_rubber_az']['slope']:.3f} {e1['coarse_vs_rubber_az']['slope_ci95']} "
        f"r {e1['coarse_vs_rubber_az']['pearson_r']:.3f}; rg slope {e1['coarse_vs_rubber_rg']['slope']:.3f}; "
        f"fine residual az {e1['fine_residual_az']['median']:+.4f} +/- {e1['fine_residual_az']['std']:.4f}")
    for k in ("ref", "coarse", "fine", "daz", "drg"):
        G.pop(k)

    # ------------------------------------------------------------------ E2: GSLC inter-date offsets
    log("E2 GSLC inter-date offsets on the lattice")
    drg_full = np.memmap(SF / "rubbersheet_offsets/freqA/HH/resampled_rg_offsets", dtype="<f8", mode="r", shape=(53200, 54244))
    win = np.asarray(drg_full[AZ0 + WR0:AZ0 + WR1, RG0 + WC0:RG0 + WC1], dtype=np.float32)
    drg = np.zeros((NY, NX), np.float32)
    drg[valid] = win[RI[valid] - AZ0 - WR0, CI[valid] - RG0 - WC0]
    del win, drg_full
    G.update({"mask": common, "valid": valid, "RI": RI, "CI": CI, "daz": daz, "drg": drg})
    jobs = [(r, c) for r in range(0, NY - CHIP + 1, CHIP) for c in range(0, NX - CHIP + 1, CHIP)]
    with Pool(8) as pool:
        rows = [x for x in pool.map(_gslc_chip, jobs, chunksize=8) if x is not None]
    E2 = np.array(rows)
    np.savez_compressed(OUT / "E2_gslc_chips.npz", E2=E2,
                        columns=np.array(["row", "col", "sy", "sx", "gamma_dup", "gamma", "dRI_drow", "dRI_dcol",
                                          "dCI_drow", "dCI_dcol", "rubber_az", "rubber_rg"]))
    dmap = -E2[:, 2:4]                                     # displacement of sec rel. ref, (rows, cols)
    d_line = E2[:, 6] * dmap[:, 0] + E2[:, 7] * dmap[:, 1]
    d_samp = E2[:, 8] * dmap[:, 0] + E2[:, 9] * dmap[:, 1]
    good2 = E2[:, 5] >= 0.3
    grp2 = (E2[:, 0] // (4 * CHIP)) * 1000 + (E2[:, 1] // (4 * CHIP))
    pred_line = s_az * E2[:, 10]
    pred_samp = s_rg * E2[:, 11]
    e2 = {"chips_total": int(len(E2)), "chips_gamma_ge_0.3": int(good2.sum()), "chip_size_cells": CHIP,
          "sign_from_E1": {"az": s_az, "rg": s_rg},
          "gslc_displacement_lines": {"median": float(np.median(d_line[good2])), "std": float(np.std(d_line[good2])),
                                      "p5": float(np.percentile(d_line[good2], 5)), "p95": float(np.percentile(d_line[good2], 95))},
          "gslc_displacement_samples": {"median": float(np.median(d_samp[good2])), "std": float(np.std(d_samp[good2]))},
          "gslc_displacement_cells": {"row_median": float(np.median(dmap[good2, 0])), "col_median": float(np.median(dmap[good2, 1])),
                                      "row_std": float(np.std(dmap[good2, 0])), "col_std": float(np.std(dmap[good2, 1]))},
          "gslc_vs_rslc_rubber_az": regress(pred_line[good2], d_line[good2], grp2[good2]),
          "gslc_vs_rslc_rubber_rg": regress(pred_samp[good2], d_samp[good2], grp2[good2]),
          "binned_az": binned(pred_line[good2], d_line[good2], -0.1, 0.3),
          "test_H0_no_misregistration": {
              "wilcoxon_p_median_d_line_eq_0_block_medians": float(stats.wilcoxon(
                  [np.median(d_line[good2][grp2[good2] == g]) for g in np.unique(grp2[good2])]).pvalue)}}
    report["E2_gslc_offsets"] = e2
    save()
    log(f"  GSLC d_line median {e2['gslc_displacement_lines']['median']:+.4f}; vs RSLC rubber az slope "
        f"{e2['gslc_vs_rslc_rubber_az']['slope']:.3f} {e2['gslc_vs_rslc_rubber_az']['slope_ci95']} r {e2['gslc_vs_rslc_rubber_az']['pearson_r']:.3f}")

    # chip-grid maps for the report
    nrc, ncc = NY // CHIP, NX // CHIP
    gmap = np.full((nrc, ncc), np.nan); pmap = np.full((nrc, ncc), np.nan)
    ii, jj = (E2[:, 0] // CHIP).astype(int), (E2[:, 1] // CHIP).astype(int)
    gmap[ii[good2], jj[good2]] = d_line[good2]
    pmap[ii[good2], jj[good2]] = pred_line[good2]
    lim = 0.25
    up = lambda a: np.kron(a, np.ones((8, 8)))
    mk = up(np.isfinite(gmap)).astype(bool)
    webp("f10_gslc_offset_lines", colorize(up(gmap), -lim, lim, mk, DIV), extent_km=[ncc * CHIP * PX / 1000, nrc * CHIP * PX / 1000],
         cmap_stops=div_stops, vrange=[-lim, lim], units="radar lines")
    webp("f10_rslc_rubber_lines", colorize(up(pmap), -lim, lim, mk, DIV), extent_km=[ncc * CHIP * PX / 1000, nrc * CHIP * PX / 1000],
         cmap_stops=div_stops, vrange=[-lim, lim], units="radar lines")
    webp("f10_offset_difference_lines", colorize(up(gmap - pmap), -lim, lim, mk, DIV),
         extent_km=[ncc * CHIP * PX / 1000, nrc * CHIP * PX / 1000], cmap_stops=div_stops, vrange=[-lim, lim], units="radar lines")

    # ------------------------------------------------------------------ E3: realignment
    log("E3 realignment of the GSLC secondary")
    sy_meas = np.full((nrc, ncc), np.nan); sx_meas = np.full((nrc, ncc), np.nan)
    sy_meas[ii[good2], jj[good2]] = E2[good2, 2]; sx_meas[ii[good2], jj[good2]] = E2[good2, 3]
    # predicted map shift: solve J * d_map = (pred_line, pred_samp) per chip; shift = -d_map
    sy_pred = np.full((nrc, ncc), np.nan); sx_pred = np.full((nrc, ncc), np.nan)
    for k_ in np.flatnonzero(good2):
        J = np.array([[E2[k_, 6], E2[k_, 7]], [E2[k_, 8], E2[k_, 9]]])
        if abs(np.linalg.det(J)) < 1e-6:
            continue
        dm = np.linalg.solve(J, [pred_line[k_], pred_samp[k_]])
        sy_pred[ii[k_], jj[k_]], sx_pred[ii[k_], jj[k_]] = -dm[0], -dm[1]

    def smooth_field(f):
        ok = np.isfinite(f)
        med = median_filter(np.where(ok, f, np.nanmedian(f)), size=3, mode="nearest")
        med = np.where(ok, med, np.nan)
        wsum = gaussian_filter(np.isfinite(med).astype(float), 1.0, mode="nearest")
        vsum = gaussian_filter(np.nan_to_num(med), 1.0, mode="nearest")
        out = np.where(wsum > 1e-3, vsum / np.maximum(wsum, 1e-3), np.nanmedian(f))
        return out

    def field_fn(chipgrid):
        g = smooth_field(chipgrid)
        def fn(y, x):
            return map_coordinates(g, [[y / CHIP - 0.5], [x / CHIP - 0.5]], order=1, mode="nearest")[0]
        return fn, g

    fy_A, gA = field_fn(sy_meas); fx_A, _ = field_fn(sx_meas)
    fy_B, gB = field_fn(sy_pred); fx_B, _ = field_fn(sx_pred)
    variants = {"A_gslc_measured": (fy_A, fx_A),
                "B_rslc_predicted": (fy_B, fx_B),
                "B_neg_null": (lambda y, x: -fy_B(y, x), lambda y, x: -fx_B(y, x))}
    # identity control of the shifter
    zero = lambda y, x: 0.0
    blk = gsec[NY // 2 - 512:NY // 2 + 512, NX // 2 - 512:NX // 2 + 512]
    ident = apply_shift_field(blk, zero, zero)
    nz = np.abs(blk) > 0
    if nz.sum() < 100000:
        raise SystemExit("shift-field identity test block has too little data")
    ident_err = float(np.max(np.abs(ident[nz] - blk[nz])) / np.mean(np.abs(blk[nz])))
    report["gates"]["shifter_identity_max_rel_error"] = ident_err
    if not ident_err <= 1e-3:
        raise SystemExit(f"shift-field identity test failed: {ident_err}")
    log(f"  shifter identity max rel error {ident_err:.2e}")

    bench8 = cfw.multilook(r_coh, K)
    R8 = ml["R_full"]
    g_coh0 = cfw.box_coherence(g_ifg, np.abs(gref) ** 2, np.abs(gsec) ** 2)
    bins = ((0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.01))
    BS = 32
    bid8 = block_ids(common8.shape, BS)

    def evaluate(ifg, coh, label):
        Z8 = cfw.multilook(ifg, K)
        pa = cfw.phase_agreement(R8, Z8, common8, K * PX / 1000, quadrants=False)
        pa["by_benchmark_coherence"] = {f"{lo:.1f}-{min(hi, 1.0):.1f}": cfw.phase_agreement(
            R8, Z8, common8 & (bench8 >= lo) & (bench8 < hi), K * PX / 1000, quadrants=False, plane=False).get("phase_diff_coherence")
            for lo, hi in bins}
        cmk = common & (coh > 0) & (r_coh > 0)
        c8, r8c = cfw.multilook(np.where(cmk, coh, 0), K), cfw.multilook(np.where(cmk, r_coh, 0), K)
        m8 = cfw.multilook(cmk.astype(np.float32), K) >= 0.999
        ratio = {}
        for lo, hi in bins:
            mm = m8 & (r8c >= lo) & (r8c < hi)
            if mm.sum() > 100:
                ratio[f"{lo:.1f}-{min(hi, 1.0):.1f}"] = float(np.median(c8[mm] / r8c[mm]))
        # per-block paired quantities
        u = np.where(common8, R8 * np.conj(Z8), 0)
        u = np.where(np.abs(u) > 0, u / np.maximum(np.abs(u), 1e-30), 0)
        nb = bid8.max() + 1
        s_re = np.bincount(bid8.ravel(), u.real.ravel(), nb); s_im = np.bincount(bid8.ravel(), u.imag.ravel(), nb)
        s_n = np.bincount(bid8.ravel(), common8.ravel().astype(float), nb)
        blk_R = np.where(s_n > 200, np.hypot(s_re, s_im) / np.maximum(s_n, 1), np.nan)
        mc = m8 & (r8c > 0.3)
        c_sum = np.bincount(bid8.ravel(), np.where(mc, c8, 0).ravel(), nb)
        r_sum = np.bincount(bid8.ravel(), np.where(mc, r8c, 0).ravel(), nb)
        blk_ratio = np.where(np.bincount(bid8.ravel(), mc.ravel().astype(float), nb) > 200, c_sum / np.maximum(r_sum, 1e-9), np.nan)
        d = cfw.dist(coh[cmk])
        z40 = Z8[common8]
        res = {"phase_agreement_40m_vs_R_full": {k: pa.get(k) for k in ("phase_diff_coherence", "constant_offset_rad", "circular_std_rad",
                                                                        "planar_rad_per_km_x_east", "planar_rad_per_km_y_south",
                                                                        "phase_diff_coherence_after_planar", "by_benchmark_coherence")},
               "coherence_distribution": d, "coherence_ratio_40m_by_R_bin": ratio,
               "phase_distribution_40m": hist(np.angle(z40), -math.pi, math.pi, 72),
               "coherence_hist": hist(coh[cmk][::5], 0, 1, 50)}
        log(f"  {label}: R40 {pa['phase_diff_coherence']:.4f}  coh median {d['median']:.4f}  ratio {ratio}")
        return res, blk_R, blk_ratio

    e3 = {"method": "tile-wise Fourier shift of the secondary GSLC (256-cell tiles, 50% overlap, periodic Hann), shift field "
                    "smoothed from the chip grid (3x3 median, Gaussian sigma 1 chip); interferogram ref*conj(sec'), 3x3 coherence",
          "variants": {}}
    base_res, base_R, base_ratio = evaluate(g_ifg, g_coh0, "original")
    e3["variants"]["original"] = base_res
    FIGMETA["charts"]["coherence_hist_R_full"] = hist(r_coh[common & (r_coh > 0)][::5], 0, 1, 50)
    blocks = {"original": (base_R, base_ratio)}
    coh_gain_map = None
    for name, (fy, fx) in variants.items():
        sec2 = apply_shift_field(gsec, fy, fx)
        ifg2 = gref * np.conj(sec2)
        ifg2[~(np.abs(gsec) > 0)] = 0
        coh2 = cfw.box_coherence(ifg2, np.abs(gref) ** 2, np.abs(sec2) ** 2)
        res, bR, bratio = evaluate(ifg2, coh2, name)
        e3["variants"][name] = res
        blocks[name] = (bR, bratio)
        if name == "B_rslc_predicted":
            cmk = common & (coh2 > 0) & (g_coh0 > 0)
            coh_gain_map = cfw.multilook(np.where(cmk, coh2 - g_coh0, 0), K) / np.maximum(cfw.multilook(cmk.astype(np.float32), K), 1e-6)
            coh_gain_mask = cfw.multilook(cmk.astype(np.float32), K) >= 0.75
            dphi8 = np.angle(cfw.multilook(ifg2, K) * np.conj(cfw.multilook(g_ifg, K)))
        del sec2, ifg2, coh2
        save()
    tests = {}
    for name in variants:
        out = {}
        for metric, idx in (("block_phase_agreement_R", 0), ("block_coherence_ratio_G_over_R", 1)):
            a, b = blocks["original"][idx], blocks[name][idx]
            ok = np.isfinite(a) & np.isfinite(b)
            dd = b[ok] - a[ok]
            out[metric] = {"blocks": int(ok.sum()), "median_original": float(np.median(a[ok])), "median_variant": float(np.median(b[ok])),
                           "mean_difference": float(dd.mean()), "mean_difference_ci95": bootstrap_ci(dd, np.mean),
                           "fraction_blocks_improved": float((dd > 0).mean()),
                           "wilcoxon_signed_rank_p": float(stats.wilcoxon(dd).pvalue) if ok.sum() > 10 else None}
        tests[name] = out
    e3["paired_block_tests_vs_original"] = tests
    e3["block_size_40m_cells"] = BS
    report["E3_realignment"] = e3
    save()
    if coh_gain_map is not None:
        webp("f11_coherence_gain_B", colorize(coh_gain_map, -0.05, 0.05, coh_gain_mask, DIV), extent_km=ext,
             cmap_stops=div_stops, vrange=[-0.05, 0.05], units="coherence")
        webp("f11_phase_change_B", colorize(dphi8, -1.0, 1.0, coh_gain_mask, DIV), extent_km=ext,
             cmap_stops=div_stops, vrange=[-1.0, 1.0], units="rad")
    del g_coh0

    # ------------------------------------------------------------------ E4: carrier coefficient with CI
    log("E4 Doppler-carrier coefficient with block bootstrap")
    from nisar.products.readers import SLC
    rslc_sec = next((C / "L1_RSLC").glob(f"*_{SEC_D}T*.h5"))
    rslc_ref = C / "L1_RSLC_AOI_v2" / f"{REF_D}_aoi.h5"
    slc_s = SLC(hdf5file=str(rslc_sec)); dop = slc_s.getDopplerCentroid(frequency="A")
    rgc = SLC(hdf5file=str(rslc_ref)).getRadarGrid("A")
    line_rate = 1.0 / slc_s.getRadarGrid("A").az_time_interval
    tt = np.clip(rgc.sensing_start + (RI[valid] - AZ0) * rgc.az_time_interval, dop.y_start, dop.y_end)
    rr = np.clip(rgc.starting_range + (CI[valid] - RG0) * rgc.range_pixel_spacing, dop.x_start, dop.x_end)
    kmap = np.zeros((NY, NX), np.float32)
    kmap[valid] = 2 * np.pi * dop.eval(tt, rr) / line_rate
    del tt, rr
    vf = np.maximum(cfw.multilook(valid.astype(np.float32), K), 1e-6)
    kd8 = (cfw.multilook(np.where(valid, kmap, 0), K) / vf) * (cfw.multilook(np.where(valid, daz, 0), K) / vf)
    Z8 = ml["G_full"]
    u = np.where(common8, R8 * np.conj(Z8), 0)
    u = np.where(np.abs(u) > 0, u / np.maximum(np.abs(u), 1e-30), 0)
    bgrid = np.round(np.arange(-2.0, 0.5001, 0.01), 3)
    nb = bid8.max() + 1
    S = np.zeros((nb, len(bgrid)), np.complex128)
    for j, bb in enumerate(bgrid):
        w = u * np.exp(-1j * bb * kd8)
        S[:, j] = np.bincount(bid8.ravel(), w.real.ravel(), nb) + 1j * np.bincount(bid8.ravel(), w.imag.ravel(), nb)
    Nn = np.bincount(bid8.ravel(), common8.ravel().astype(float), nb)
    keep = Nn > 50
    S, Nn = S[keep], Nn[keep]
    Rb = np.abs(S.sum(0)) / Nn.sum()
    b_hat = float(bgrid[int(np.argmax(Rb))])
    reps = []
    for _ in range(2000):
        pk = RNG.integers(0, len(Nn), len(Nn))
        reps.append(bgrid[int(np.argmax(np.abs(S[pk].sum(0))))])
    e4 = {"model": "phi_R - phi_G = a + b * k * d_az (40 m); R(b) = |mean exp(i(phi_R - phi_G - b k d_az))|",
          "line_rate_hz": float(line_rate), "b_hat": b_hat, "b_ci95_block_bootstrap": [float(np.percentile(reps, 2.5)), float(np.percentile(reps, 97.5))],
          "R_at_b_hat": float(Rb.max()), "R_at_b_minus_1": float(Rb[int(np.argmin(np.abs(bgrid + 1)))]), "R_at_b_0": float(Rb[int(np.argmin(np.abs(bgrid)))]),
          "blocks": int(len(Nn)), "block_size_40m_cells": BS,
          "curve": {"b": bgrid[::5].tolist(), "R": [float(x) for x in Rb[::5]]}}
    report["E4_carrier_coefficient"] = e4
    save()
    log(f"  b_hat {b_hat:+.2f} CI {e4['b_ci95_block_bootstrap']}; R(b=-1) {e4['R_at_b_minus_1']:.4f} R(0) {e4['R_at_b_0']:.4f}")

    # ------------------------------------------------------------------ S: sharpness
    log("S sharpness and sampling")
    dup_x = (RI[:, 1:] == RI[:, :-1]) & (CI[:, 1:] == CI[:, :-1]) & common[:, 1:] & common[:, :-1]
    dup_y = (RI[1:, :] == RI[:-1, :]) & (CI[1:, :] == CI[:-1, :]) & common[1:, :] & common[:-1, :]
    nn_cells = common.sum()
    uniq = np.unique((RI[common].astype(np.int64) << 20) + CI[common].astype(np.int64)).size
    refw = np.memmap(SC / "crossmul/freqA/HH/reference.slc", dtype="<c8", mode="r", shape=(L, W))
    rwin = np.asarray(refw[WR0:WR1, WC0:WC1])
    r_amp_lat = np.zeros((NY, NX), np.float32)
    r_amp_lat[valid] = np.abs(rwin[RI[valid] - AZ0 - WR0, CI[valid] - RG0 - WC0])
    g_amp = np.abs(gref).astype(np.float32)

    def contrast(I, m, win=7):
        mu = uniform_filter(np.where(m, I, 0), win) / np.maximum(uniform_filter(m.astype(np.float32), win), 1e-6)
        mu2 = uniform_filter(np.where(m, I * I, 0), win) / np.maximum(uniform_filter(m.astype(np.float32), win), 1e-6)
        return np.sqrt(np.maximum(mu2 - mu * mu, 0)) / np.maximum(mu, 1e-9)

    Ir, Ig = r_amp_lat ** 2, g_amp ** 2
    cmask = common & (Ir > 0) & (Ig > 0)
    cr, cg = contrast(Ir, cmask), contrast(Ig, cmask)
    full = uniform_filter(cmask.astype(np.float32), 7) > 0.999
    I_rad = np.abs(rwin) ** 2
    mr = I_rad > 0
    crad = contrast(I_rad.astype(np.float32), mr)
    crad_lat = np.zeros((NY, NX), np.float32)
    crad_lat[valid] = crad[RI[valid] - AZ0 - WR0, CI[valid] - RG0 - WC0]
    del crad

    def acf_width(stack_fn, n):
        acc = None
        for img in stack_fn(n):
            x = img - img.mean()
            F = np.fft.fft2(x)
            a = np.fft.fftshift(np.real(np.fft.ifft2(np.abs(F) ** 2)))
            a /= a.max()
            acc = a if acc is None else acc + a
        acc /= n
        c0, c1 = acc.shape[0] // 2, acc.shape[1] // 2
        def fwhm(profile):
            p = profile[len(profile) // 2:]
            i = int(np.argmax(p < 0.5))
            return float(2 * (i - 1 + (p[i - 1] - 0.5) / (p[i - 1] - p[i]))) if i > 0 else float("nan")
        return fwhm(acc[:, c1]), fwhm(acc[c0, :]), acc[c0 - 6:c0 + 7, c1].tolist(), acc[c0, c1 - 6:c1 + 7].tolist()

    lat_chips = [(r, c) for r in range(0, NY - CHIP, CHIP) for c in range(0, NX - CHIP, CHIP) if cmask[r:r + CHIP, c:c + CHIP].all()]
    sel = [lat_chips[i] for i in RNG.choice(len(lat_chips), size=min(400, len(lat_chips)), replace=False)]
    wr_y, wr_x, pr_y, pr_x = acf_width(lambda n: (Ir[r:r + CHIP, c:c + CHIP] for r, c in sel), len(sel))
    wg_y, wg_x, pg_y, pg_x = acf_width(lambda n: (Ig[r:r + CHIP, c:c + CHIP] for r, c in sel), len(sel))
    rad_chips = [(r, c) for r in range(0, rwin.shape[0] - CHIP, CHIP) for c in range(0, rwin.shape[1] - CHIP, CHIP)
                 if mr[r:r + CHIP, c:c + CHIP].all()]
    selr = [rad_chips[i] for i in RNG.choice(len(rad_chips), size=min(400, len(rad_chips)), replace=False)]
    wa, wrg, pa_, prg = acf_width(lambda n: (I_rad[r:r + CHIP, c:c + CHIP] for r, c in selr), len(selr))

    def spectrum_profiles(get, chips):
        acc0 = acc1 = None
        for r, c in chips:
            z = np.nan_to_num(get(r, c).astype(np.complex128))
            F = np.abs(np.fft.fft2(z)) ** 2
            rl = carrier_roll(z, z)
            F = np.fft.fftshift(np.roll(F, (-rl[0], -rl[1]), axis=(0, 1)))
            p0, p1 = F.sum(1), F.sum(0)
            acc0 = p0 if acc0 is None else acc0 + p0
            acc1 = p1 if acc1 is None else acc1 + p1
        f = np.fft.fftshift(np.fft.fftfreq(CHIP))
        out = {}
        for nm, p in (("axis0", acc0), ("axis1", acc1)):
            p = p / p.max()
            out[nm] = {"f_cycles_per_sample": f.round(4).tolist(), "power": [round(float(x), 5) for x in p],
                       "fraction_power_abs_f_gt_0.4": float(p[np.abs(f) > 0.4].sum() / p.sum()),
                       "width_minus3dB_cycles_per_sample": float((p >= 0.5).sum() / CHIP)}
        return out

    spec_g = spectrum_profiles(lambda r, c: gref[r:r + CHIP, c:c + CHIP], sel[:200])
    spec_r = spectrum_profiles(lambda r, c: rwin[r:r + CHIP, c:c + CHIP], selr[:200])
    az_m, rg_m = rep["C1_dense_offsets"]["azimuth_sample_spacing_m"], rep["C1_dense_offsets"]["range_sample_spacing_m"]
    s_out = {"lookup_nearest_neighbour": {"fraction_cells_duplicating_east_neighbour": float(dup_x.sum() / nn_cells),
                                          "fraction_cells_duplicating_south_neighbour": float(dup_y.sum() / nn_cells),
                                          "unique_radar_samples_per_lattice_cell": float(uniq / nn_cells)},
             "speckle_contrast_7x7_intensity": {
                 "R_full_nearest_on_lattice_median": float(np.median(cr[full])), "G_crop_on_lattice_median": float(np.median(cg[full])),
                 "R_full_radar_native_mapped_median": float(np.median(crad_lat[full & (crad_lat > 0)])),
                 "paired_ratio_G_over_R_lattice_median": float(np.median(cg[full] / np.maximum(cr[full], 1e-9)))},
             "intensity_acf_fwhm": {"R_full_on_lattice_cells_rows_cols": [wr_y, wr_x], "G_crop_cells_rows_cols": [wg_y, wg_x],
                                    "R_full_on_lattice_m_rows_cols": [wr_y * PX, wr_x * PX], "G_crop_m_rows_cols": [wg_y * PX, wg_x * PX],
                                    "R_radar_samples_az_rg": [wa, wrg], "R_radar_m_az_slantrange": [wa * az_m, wrg * rg_m]},
             "acf_profiles": {"R_lattice_rows": pr_y, "R_lattice_cols": pr_x, "G_rows": pg_y, "G_cols": pg_x, "R_radar_az": pa_, "R_radar_rg": prg},
             "spectra": {"G_crop_lattice": {"rows_north_south": spec_g["axis0"], "cols_east_west": spec_g["axis1"]},
                         "R_full_radar": {"azimuth": spec_r["axis0"], "range": spec_r["axis1"]}}}
    report["S_sharpness"] = s_out
    save()
    log(f"  duplicates E {s_out['lookup_nearest_neighbour']['fraction_cells_duplicating_east_neighbour']:.3f} "
        f"S {s_out['lookup_nearest_neighbour']['fraction_cells_duplicating_south_neighbour']:.3f}; contrast R {s_out['speckle_contrast_7x7_intensity']['R_full_nearest_on_lattice_median']:.3f} "
        f"G {s_out['speckle_contrast_7x7_intensity']['G_crop_on_lattice_median']:.3f}; ACF FWHM R {wr_y:.2f},{wr_x:.2f} G {wg_y:.2f},{wg_x:.2f} cells")

    (OUT / "figures_alignment.json").write_text(json.dumps(FIGMETA, indent=1))
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
