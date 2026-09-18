#!/usr/bin/env python3
"""
Four-way comparison, v2: RSLC vs GSLC, full tile vs cropped-first.

    python -u tools/compare_four_way.py                      # Nepal GLOF defaults
    python -u tools/compare_four_way.py --rcrop-root aoi_v2 --gcrop-root aoi_v2 --out comparison_v2

Writes <case>/<out>/comparison.json and summary.md, and caches expensive layers
under <case>/<out>/layers/ with an input manifest -- a cache whose inputs changed
is rebuilt, never silently reused. Read-only on every workflow product.

LEGS (and the ONE sign convention used everywhere)
-------------------------------------------------
  R_full  RSLC, full tile, windowed to the AOI      <- BENCHMARK
  R_crop  RSLC from granules cropped to the AOI first (tools/rslc_subset.py)
  G_full  GSLC, full tile, windowed to the AOI
  G_crop  GSLC from the cropped granules
A pair key "P__vs__Q" ALWAYS means P minus Q: phase angle(P * conj(Q)), screen
P - Q, cycles (P - Q)/2pi. Pairs are enumerated in the leg order above, so the
benchmark is always P when it takes part. (v1 used opposite conventions in two
sections of the same JSON.)

WHAT CHANGED FROM v1, AND WHY (each item was found by adversarial verification)
-----------------------------------------------------------------------------
* Crop legs come from the v2 subset: zero-Doppler window (v1 used the native
  Doppler and missed the north of the AOI), freq-B range origin = freq-A origin/8
  on every date (v1 broke this and corrupted ISCE3's side-band offsets), origins
  snapped to the 9x8 look grid. The tool VERIFIES the A/B alignment and reports
  AOI coverage per quadrant instead of assuming the crop covers the AOI.
* Dense offsets (C1): RUNW pixelOffsets are in METRES (attribute units='meters').
  v1 labelled them pixels and tested a frame-shift hypothesis in mixed units.
  Now reported in metres and converted to radar samples with the RSLC spacings.
* The geolocation lookup is stored in FULL-FRAME reference indices, built from the
  crop's rdr2geo lon/lat (rdr2geo is per-pixel, and the crop is an exact window of
  the full grid), so one lookup serves R_full and R_crop exactly.
* RSLC coherence is computed here, in two forms, from the same radar window:
  FLATTENED (|sum RIFG| / sqrt(sum|a|^2 sum|b|^2) -- the RIFG product is already
  topo/flat-earth flattened) and UNFLATTENED (from a*conj(b), what
  tools/slc_coherence.py produced). v1 compared an unflattened RSLC coherence with
  a flattened GSLC one. The benchmark coherence is now the flattened form.
* Ionosphere: the nearest-integer (m, n) fit was meaningless (any constant is
  matched by some pair) and is gone. Instead the unwrapped A and B phases of the
  two runs are differenced directly: whole cycles plus any non-integer remainder,
  and the wrapped side-band interferograms are compared too.
* The R-vs-G "planar ramp" is no longer interpreted as reference-phase handling
  (refuted). The planar component is fitted with a CIRCULAR gradient estimator,
  quadrant gradients are reported (the difference is not planar), and a physical
  attribution is tested: azimuth registration residual x Doppler carrier,
  k = 2*pi*f_dc / line_rate. The line rate is 1/az_time_interval = 1520 Hz, which
  is what ISCE3's geocodeSlc uses as radarGrid.prf() -- NOT the 1909.6 Hz
  acquisition PRF (a verifier used that and got a constant 25.6% too small). The
  SIGN is determined empirically and reported as such.
* NaN-safe throughout: GSLC products use NaN nodata; masks built only with `!= 0`
  let NaN through.

GEOLOCATION LOOKUP -- why it is a KD-tree, not gdalwarp -geoloc
----------------------------------------------------------------
RSLC layers are geocoded by nearest radar sample: every rdr2geo lon/lat (sample
centres) in the radar window is projected to UTM, and each 5 m map cell takes the
nearest one within 4x --geoloc-tol. Cells whose nearest sample is farther than
--geoloc-tol (radar shadow, gaps on slopes facing away) are excluded, and the
effect of that exclusion is reported.
The first two versions used gdalwarp with GEOLOCATION metadata. On this data it
SKIPPED whole output chunks whenever it could not compute a chunk's source window
("Unable to compute source region for output window ... skipping"): rectangular
holes on a regular grid, 66% AOI coverage; with errorThreshold 0.125 it also
carried a systematic +2.5 m E / +2.6 m N (half-cell) bias. v1's lookup printed the
same skip warning, so its coverage figures were also affected. The tool now
refuses to continue if the mean residual exceeds 1 m in either axis.

CAVEAT on RSLC-vs-GSLC at 5 m: RSLC layers are geocoded by nearest radar sample
(up to half a pixel away) while GSLC sinc-interpolates to the exact map-cell
centre, so the 5 m cross-track agreement is limited mainly by sampling. The 40 m
numbers are the fair cross-track comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

_ARGV = sys.argv[1:]
sys.argv = [sys.argv[0]]

import h5py  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402
from scipy.ndimage import uniform_filter  # noqa: E402

gdal.UseExceptions()
ogr.UseExceptions()

RSW = "/science/LSAR/RSLC/swaths"
OFF = "science/LSAR/RUNW/swaths/frequencyA/pixelOffsets"
IFGP = "science/LSAR/RIFG/swaths/frequencyA/interferogram/HH/wrappedInterferogram"
RUNW_A = "science/LSAR/RUNW/swaths/frequencyA/interferogram"
RUNW_B = "science/LSAR/RUNW/swaths/frequencyB/interferogram"
RIFG_B = "science/LSAR/RIFG/swaths/frequencyB/interferogram"

F0, F1 = 1.239e9, 1.2935e9
RAD2TECU = 299792458.0 * F0 / (4 * math.pi * 40.31) / 1e16
TWO_PI = 2 * math.pi
_R, _S = F1 / F0, F0 / F1
_DET = _S - _R
RAD_PER_CYCLE_A = (-_R * TWO_PI) / _DET          # +76.17 rad of dispersive per cycle in A
RAD_PER_CYCLE_B = TWO_PI / _DET                   # -72.96 rad per cycle in B
RAD_PER_JOINT_CYCLE = RAD_PER_CYCLE_A + RAD_PER_CYCLE_B

LEGS = ("R_full", "R_crop", "G_full", "G_crop")
# Bump whenever a layer builder changes (coherence window, masking, flattening,
# resampling, geolocation convention). It is part of every cache key, so an
# edited builder can never be satisfied by layers made by the old one.
LAYER_VERSION = "cfw2.1-coh3-bad2-nearest-pixelcentre"
T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


# --------------------------------------------------------------------------
# I/O helpers
# --------------------------------------------------------------------------
# Hold the dataset in a local: gdal.Open(p).GetRasterBand(1).ReadAsArray() lets
# the dataset be garbage-collected while its band is in use (SWIG TypeError).
def read_tif(path: Path, band: int = 1) -> np.ndarray:
    ds = gdal.Open(str(path))
    return ds.GetRasterBand(band).ReadAsArray()


def read_window(path: Path, xoff: int, yoff: int, nx: int, ny: int) -> np.ndarray:
    ds = gdal.Open(str(path))
    return ds.GetRasterBand(1).ReadAsArray(int(xoff), int(yoff), int(nx), int(ny))


def write_tif(path: Path, arr, gt, wkt, nodata=None, bands=None) -> None:
    arrs = bands if bands is not None else [arr]
    dt = {np.dtype("float32"): gdal.GDT_Float32, np.dtype("complex64"): gdal.GDT_CFloat32,
          np.dtype("uint8"): gdal.GDT_Byte}[arrs[0].dtype]
    ds = gdal.GetDriverByName("GTiff").Create(
        str(path), arrs[0].shape[1], arrs[0].shape[0], len(arrs), dt,
        ["TILED=YES", "COMPRESS=DEFLATE", "ZLEVEL=1", "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform(gt)
    ds.SetProjection(wkt)
    for i, a in enumerate(arrs, start=1):
        b = ds.GetRasterBand(i)
        if nodata is not None:
            b.SetNoDataValue(nodata)
        b.WriteArray(a)
    ds = None


def lattice_offset(path: Path, x0: float, y0: float) -> tuple[int, int]:
    ds = gdal.Open(str(path))
    gt = ds.GetGeoTransform()
    c, r = (x0 - gt[0]) / gt[1], (gt[3] - y0) / abs(gt[5])
    if abs(c - round(c)) > 1e-6 or abs(r - round(r)) > 1e-6:
        raise SystemExit(f"{path.name}: lattice not aligned to ({x0}, {y0}) -- offset {c}, {r}")
    return int(round(c)), int(round(r))


def manifest_of(paths: list[Path], extra: dict) -> str:
    items = [[str(p), p.stat().st_size, int(p.stat().st_mtime)] for p in paths]
    return hashlib.sha1(json.dumps([items, extra], sort_keys=True).encode()).hexdigest()


def c64(a: np.ndarray) -> np.ndarray:
    return np.nan_to_num(a.astype(np.complex64), nan=0.0, posinf=0.0, neginf=0.0)


def f32(a: np.ndarray) -> np.ndarray:
    return np.nan_to_num(a.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)


def multilook(a: np.ndarray, k: int) -> np.ndarray:
    ny, nx = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    return a[:ny, :nx].reshape(ny // k, k, nx // k, k).mean(axis=(1, 3))


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------
def crop_origin(full_h5: Path, crop_h5: Path, freq: str) -> tuple[int, int, int, int]:
    """(az0, rg0, lines, samples) of a crop, recovered from its coordinate arrays."""
    with h5py.File(full_h5, "r") as hf, h5py.File(crop_h5, "r") as hc:
        zf, zc = hf[f"{RSW}/zeroDopplerTime"][()], hc[f"{RSW}/zeroDopplerTime"][()]
        sf = hf[f"{RSW}/frequency{freq}/slantRange"][()]
        sc = hc[f"{RSW}/frequency{freq}/slantRange"][()]
    az0 = int(np.argmin(np.abs(zf - zc[0])))
    rg0 = int(np.argmin(np.abs(sf - sc[0])))
    if az0 + len(zc) > len(zf) or rg0 + len(sc) > len(sf):
        raise SystemExit(f"{crop_h5.name} extends beyond {full_h5.name}")
    # rtol=0: np.allclose's default rtol=1e-5 on seconds-of-day (~85160 s) tolerates
    # ~0.85 s (~1300 lines) and on slant range (~8.6e5 m) ~9 m -- it could not catch
    # a sub-pixel or even multi-pixel shift. Require 1e-3 of a sample.
    dz, ds = abs(zf[1] - zf[0]), abs(sf[1] - sf[0])
    if not (np.allclose(zf[az0:az0 + len(zc)], zc, rtol=0, atol=1e-3 * dz)
            and np.allclose(sf[rg0:rg0 + len(sc)], sc, rtol=0, atol=1e-3 * ds)):
        raise SystemExit(f"{crop_h5.name} is not an exact slice of {full_h5.name}")
    return az0, rg0, len(zc), len(sc)


def coord_index(full_axis: np.ndarray, crop_axis: np.ndarray) -> tuple[float, bool]:
    """Fractional index of crop_axis[0] in full_axis, and whether steps match."""
    # Step from the end points: a single-difference step carries float round-off
    # that the index multiplies (5e-6 steps by row 5000).
    step_f = (full_axis[-1] - full_axis[0]) / (len(full_axis) - 1)
    step_c = (crop_axis[-1] - crop_axis[0]) / (len(crop_axis) - 1)
    return float((crop_axis[0] - full_axis[0]) / step_f), bool(abs(step_f - step_c) < 1e-6 * abs(step_f))


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def fit_plane(z: np.ndarray, m: np.ndarray, px_km: float) -> tuple[float, float, float]:
    """
    Circular maximum-likelihood plane of a unit-phasor field: the (gx, gy) that
    maximise |mean(z * exp(-i(gx x + gy y)))|. Returns (gx, gy, R_after) in rad/km.

    A lag-1 estimator (angle of sum z[x+1] conj z[x]) is NOT used: it measures
    neighbour-to-neighbour structure, which local phase swamps. On the real 40 m
    R-vs-G data it returned a plane that LOWERED agreement (0.887 -> 0.856) where
    the ML plane raises it to 0.913. Seeded from the peak of a zero-padded 2-D FFT
    of a block-averaged field, then refined by Nelder-Mead on <= 2M samples.
    """
    from scipy.optimize import minimize
    yy, xx = np.nonzero(m)
    if yy.size < 100:
        return 0.0, 0.0, float("nan")
    zm = z[m].astype(np.complex128)
    k = max(1, int(math.ceil(max(z.shape) / 1024)))
    ny, nx = (z.shape[0] // k) * k, (z.shape[1] // k) * k
    zb = np.where(m, z, 0)[:ny, :nx].reshape(ny // k, k, nx // k, k).mean(axis=(1, 3))
    Ny, Nx = 1 << int(math.ceil(math.log2(2 * zb.shape[0]))), 1 << int(math.ceil(math.log2(2 * zb.shape[1])))
    F = np.abs(np.fft.fft2(zb, s=(Ny, Nx)))
    iy, ix = np.unravel_index(int(np.argmax(F)), F.shape)
    fy, fx = np.fft.fftfreq(Ny)[iy], np.fft.fftfreq(Nx)[ix]
    g0 = [2 * math.pi * fx / (k * px_km), 2 * math.pi * fy / (k * px_km)]
    step = max(1, zm.size // 2_000_000)
    X, Y, Z = xx[::step] * px_km, yy[::step] * px_km, zm[::step]
    f = lambda g: -abs(np.mean(Z * np.exp(-1j * (g[0] * X + g[1] * Y))))
    best = min((minimize(f, g, method="Nelder-Mead", options={"xatol": 1e-6, "fatol": 1e-10}) for g in (g0, [0.0, 0.0])),
               key=lambda r: r.fun)
    gx, gy = float(best.x[0]), float(best.x[1])
    R0 = abs(np.mean(zm))
    R1 = abs(np.mean(zm * np.exp(-1j * (gx * xx * px_km + gy * yy * px_km))))
    if R1 < R0:          # an ML plane can never lower agreement; fall back to none
        return 0.0, 0.0, float(R0)
    return gx, gy, float(R1)


def phase_agreement(p: np.ndarray, q: np.ndarray, m: np.ndarray, px_km: float,
                    quadrants: bool = True, plane: bool = True) -> dict:
    """Agreement of P and Q over mask m. Difference is angle(P * conj(Q))."""
    m = m & (p != 0) & (q != 0) & np.isfinite(p) & np.isfinite(q)
    n = int(m.sum())
    if n < 100:
        return {"n": n}
    z2 = np.zeros(p.shape, np.complex64)
    prod = p[m] * np.conj(q[m])
    mag = np.abs(prod)
    ok = mag > 0
    unit = np.zeros_like(prod)
    unit[ok] = prod[ok] / mag[ok]
    z2[m] = unit
    m = m & (z2 != 0)
    zm = z2[m]
    mean = zm.mean()
    R = float(abs(mean))
    off = float(np.angle(mean))
    d = np.angle(zm * np.exp(-1j * off)).astype(np.float32)
    out = {
        "n": int(m.sum()),
        "phase_diff_coherence": R,
        "constant_offset_rad": off,
        "circular_std_rad": float(math.sqrt(max(0.0, -2.0 * math.log(max(R, 1e-12))))),
        "abs_p50_rad": float(np.percentile(np.abs(d), 50)),
        "abs_p95_rad": float(np.percentile(np.abs(d), 95)),
        "frac_lt_0p1": float((np.abs(d) < 0.1).mean()),
        "frac_lt_0p5": float((np.abs(d) < 0.5).mean()),
    }
    if not plane:
        return out
    gx, gy, R2 = fit_plane(z2, m, px_km)
    out.update({"planar_rad_per_km_x_east": gx, "planar_rad_per_km_y_south": gy,
                "phase_diff_coherence_after_planar": R2,
                "planar_change_across_extent_rad": [gx * m.shape[1] * px_km, gy * m.shape[0] * px_km]})
    if quadrants:
        ny, nx = m.shape
        q4 = {}
        for name, (ys, xs) in {"NW": (slice(0, ny // 2), slice(0, nx // 2)),
                               "NE": (slice(0, ny // 2), slice(nx // 2, nx)),
                               "SW": (slice(ny // 2, ny), slice(0, nx // 2)),
                               "SE": (slice(ny // 2, ny), slice(nx // 2, nx))}.items():
            mm = m[ys, xs]
            if mm.sum() > 1000:
                qx, qy, qr = fit_plane(z2[ys, xs], mm, px_km)
                q4[name] = {"gx": qx, "gy": qy, "n": int(mm.sum()),
                            "R": float(abs(z2[ys, xs][mm].mean())), "R_after_planar": qr}
        out["quadrants"] = q4
    return out


def dist(v: np.ndarray) -> dict:
    return {"n": int(v.size), "median": float(np.median(v)), "mean": float(v.mean()),
            "p5": float(np.percentile(v, 5)), "p95": float(np.percentile(v, 95)),
            "frac_gt_0p5": float((v > 0.5).mean())}


def tile_shifts(ref: np.ndarray, mov: np.ndarray, valid: np.ndarray, tile: int = 512, grid: int = 5) -> dict:
    """Shift (rows, cols) that registers `mov` onto `ref`, from log-amplitude phase correlation."""
    from skimage.registration import phase_cross_correlation
    ny, nx = ref.shape
    if ny < 3 * tile or nx < 3 * tile:
        return {"tiles": 0}
    ys = np.linspace(tile, ny - 2 * tile, grid).astype(int)
    xs = np.linspace(tile, nx - 2 * tile, grid).astype(int)
    sh = []
    for y in ys:
        for x in xs:
            v = valid[y:y + tile, x:x + tile]
            if v.mean() < 0.95:
                continue
            ta = np.log(np.maximum(ref[y:y + tile, x:x + tile], 1e-6))
            tb = np.log(np.maximum(mov[y:y + tile, x:x + tile], 1e-6))
            ta = np.where(v, ta, ta[v].mean())
            tb = np.where(v, tb, tb[v].mean())
            s, _, _ = phase_cross_correlation(ta, tb, upsample_factor=20)
            sh.append(s)
    if not sh:
        return {"tiles": 0}
    sh = np.array(sh)
    return {"tiles": int(len(sh)),
            "shift_rows_median_px": float(np.median(sh[:, 0])),
            "shift_cols_median_px": float(np.median(sh[:, 1])),
            "shift_rows_mad_px": float(np.median(np.abs(sh[:, 0] - np.median(sh[:, 0])))),
            "shift_cols_mad_px": float(np.median(np.abs(sh[:, 1] - np.median(sh[:, 1]))))}


def screen_agreement(p: np.ndarray, q: np.ndarray, m: np.ndarray) -> dict:
    """Ionosphere screens P - Q: constant offset and residual shape, rad and TECU."""
    m = m & np.isfinite(p) & np.isfinite(q) & (p != 0) & (q != 0)
    if m.sum() < 100:
        return {"n": int(m.sum())}
    d = (p[m] - q[m]).astype(np.float64)
    off = float(np.median(d))
    res = d - off
    return {"n": int(m.sum()),
            "median_P_rad": float(np.median(p[m])), "median_Q_rad": float(np.median(q[m])),
            "median_P_tecu": float(np.median(p[m]) * RAD2TECU), "median_Q_tecu": float(np.median(q[m]) * RAD2TECU),
            "offset_P_minus_Q_rad": off, "offset_P_minus_Q_tecu": off * RAD2TECU,
            "offset_in_joint_cycles": off / RAD_PER_JOINT_CYCLE,
            "residual_std_rad": float(res.std()), "residual_std_tecu": float(res.std() * RAD2TECU),
            "residual_p95_abs_tecu": float(np.percentile(np.abs(res), 95) * RAD2TECU),
            "pearson_r": float(np.corrcoef(p[m], q[m])[0, 1])}


def cycle_difference(p_unw: np.ndarray, q_unw: np.ndarray, m: np.ndarray) -> dict:
    """(P - Q)/2pi: whole cycles and the non-integer remainder (circular)."""
    m = m & np.isfinite(p_unw) & np.isfinite(q_unw) & (p_unw != 0) & (q_unw != 0)
    if m.sum() < 100:
        return {"n": int(m.sum())}
    c = (p_unw[m] - q_unw[m]) / TWO_PI
    whole = np.round(c)
    rem = c - whole
    vals, counts = np.unique(whole, return_counts=True)
    top = sorted(zip(counts, vals), reverse=True)[:4]
    rem_circ = float(np.angle(np.mean(np.exp(1j * TWO_PI * rem))) / TWO_PI)
    return {"n": int(m.sum()), "median_cycles": float(np.median(c)),
            "modal_whole_cycles": [{"cycles": int(v), "fraction": float(k / m.sum())} for k, v in top],
            "remainder_circular_mean_cycles": rem_circ,
            "remainder_std_cycles": float(rem.std())}


def box_coherence(z: np.ndarray, pa: np.ndarray, pb: np.ndarray, win: int = 3) -> np.ndarray:
    """|sum_w z| / sqrt(sum_w pa * sum_w pb), moving window; zeros contribute nothing."""
    # In place throughout: the radar window is ~160 Mpx, so every temporary is
    # 0.64 GB (float32) and a naive version peaked at ~13 GB.
    s = float(win * win)
    re = uniform_filter(z.real.astype(np.float32), win, mode="constant"); re *= s
    im = uniform_filter(z.imag.astype(np.float32), win, mode="constant"); im *= s
    np.hypot(re, im, out=re); del im
    den = uniform_filter(pa, win, mode="constant"); den *= s
    sb = uniform_filter(pb, win, mode="constant"); sb *= s
    den *= sb; del sb
    np.sqrt(den, out=den)
    good = den > 0
    np.divide(re, den, out=re, where=good)
    re[~good] = 0
    np.nan_to_num(re, copy=False)
    np.clip(re, 0, 1, out=re)
    return re


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", type=Path, default=Path("/home/sharath/isce3/case_studies/nepal_glof"))
    ap.add_argument("--pair", default="20260714_20260726")
    ap.add_argument("--tag", default="20260714_20260726_A_HH_1x1")
    ap.add_argument("--kml", type=Path, default=Path("/home/sharath/asf_slc/glof_exact_aoi.kml"))
    ap.add_argument("--rcrop-root", default="aoi_v2")
    ap.add_argument("--gcrop-root", default="aoi_v2")
    ap.add_argument("--rcrop-granules", default="L1_RSLC_AOI_v2")
    ap.add_argument("--out", default="comparison_v2")
    ap.add_argument("--geoloc-tol", type=float, default=15.0)
    args = ap.parse_args(_ARGV)

    C = args.case
    OUT = C / args.out
    LAY = OUT / "layers"
    LAY.mkdir(parents=True, exist_ok=True)
    ref_d, sec_d = args.pair.split("_")
    P = C / "pairs" / args.pair
    PRC = C / args.rcrop_root / "pairs" / args.pair
    PGC = C / args.gcrop_root / "pairs" / args.pair
    SF = C / "scratch" / "trackR" / args.tag
    SC = C / args.rcrop_root / "scratch" / "trackR" / args.tag
    rslc_full = {d: next((C / "L1_RSLC").glob(f"*_{d}T*.h5")) for d in (ref_d, sec_d)}
    rslc_crop = {d: C / args.rcrop_granules / f"{d}_aoi.h5" for d in (ref_d, sec_d)}
    G_full_ifg = P / "trackG" / "ifg_A_HH.igram.tif"
    G_crop_ifg = PGC / "trackG" / "ifg_A_HH_1x1.igram.tif"
    runw_f = P / "trackR" / f"RUNW_{args.tag}_unw9x8.h5"
    runw_c = PRC / "trackR" / f"RUNW_{args.tag}_unw9x8.h5"
    report: dict = {
        "tool_version": "compare_four_way v2",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": {"R_crop_root": args.rcrop_root, "G_crop_root": args.gcrop_root,
                   "R_crop_granules": args.rcrop_granules},
        "conventions": {
            "pair_key": "P__vs__Q means P minus Q (phase angle(P*conj(Q)); screens P-Q; cycles (P-Q)/2pi)",
            "leg_order": list(LEGS),
            "planar_axes": "x = east (column), y = south (row)",
            "benchmark_coherence": "R_full flattened 3x3",
        },
    }

    # ---- crop geometry and band alignment ---------------------------------
    ref_A = crop_origin(rslc_full[ref_d], rslc_crop[ref_d], "A")
    sec_A = crop_origin(rslc_full[sec_d], rslc_crop[sec_d], "A")
    ref_B = crop_origin(rslc_full[ref_d], rslc_crop[ref_d], "B")
    sec_B = crop_origin(rslc_full[sec_d], rslc_crop[sec_d], "B")
    AZ0, RG0, L, W = ref_A
    align = {"reference_B0x8_minus_A0": ref_B[1] * 8 - ref_A[1],
             "secondary_B0x8_minus_A0": sec_B[1] * 8 - sec_A[1]}
    align["consistent_across_dates"] = align["reference_B0x8_minus_A0"] == align["secondary_B0x8_minus_A0"]
    report["crop_geometry"] = {"reference_A": ref_A, "secondary_A": sec_A, "reference_B": ref_B,
                               "secondary_B": sec_B, "band_alignment": align,
                               "reference_origin_mod_9x8": [AZ0 % 9, RG0 % 8]}
    log(f"crop: ref A {ref_A}, sec A {sec_A}; A/B alignment {align}; origin mod (9,8) {[AZ0 % 9, RG0 % 8]}")
    if not align["consistent_across_dates"]:
        log("WARNING: A/B range origins differ between dates -- ISCE3 side-band offsets will be wrong")

    with h5py.File(rslc_crop[ref_d], "r") as h:
        az_spacing_m = float(h[f"{RSW}/frequencyA/sceneCenterAlongTrackSpacing"][()])
        rg_spacing_m = float(h[f"{RSW}/frequencyA/slantRangeSpacing"][()])
        zdt0 = float(h[f"{RSW}/zeroDopplerTime"][0])
        dz = float(h[f"{RSW}/zeroDopplerTimeSpacing"][()])
        sr0 = float(h[f"{RSW}/frequencyA/slantRange"][0])

    # ---- AOI polygon and 5 m lattice ---------------------------------------
    kds = ogr.Open(str(args.kml))
    geom = None
    for f in kds.GetLayer(0):
        g = f.GetGeometryRef()
        geom = g.Clone() if geom is None else geom.Union(g)
    geom.FlattenTo2D()
    s4326 = osr.SpatialReference(); s4326.ImportFromEPSG(4326)
    s4326.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    sutm = osr.SpatialReference(); sutm.ImportFromEPSG(32645)
    sutm.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    geom.Transform(osr.CoordinateTransformation(s4326, sutm))
    xmin, xmax, ymin, ymax = geom.GetEnvelope()
    gds = gdal.Open(str(G_crop_ifg))
    ggt = gds.GetGeoTransform()
    PX = ggt[1]
    X0 = ggt[0] + math.floor((xmin - ggt[0]) / PX) * PX
    X1 = ggt[0] + math.ceil((xmax - ggt[0]) / PX) * PX
    Y1 = ggt[3] - math.floor((ggt[3] - ymax) / PX) * PX
    Y0 = ggt[3] - math.ceil((ggt[3] - ymin) / PX) * PX
    NX, NY = int(round((X1 - X0) / PX)), int(round((Y1 - Y0) / PX))
    GT = (X0, PX, 0.0, Y1, 0.0, -PX)
    WKT = sutm.ExportToWkt()
    mem = gdal.GetDriverByName("MEM").Create("", NX, NY, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(GT); mem.SetProjection(WKT)
    vds = ogr.GetDriverByName("MEM").CreateDataSource("aoi")
    vl = vds.CreateLayer("aoi", srs=sutm)
    feat = ogr.Feature(vl.GetLayerDefn()); feat.SetGeometry(geom); vl.CreateFeature(feat)
    gdal.RasterizeLayer(mem, [1], vl, burn_values=[1])
    aoi = mem.GetRasterBand(1).ReadAsArray().astype(bool)
    write_tif(OUT / "aoi_polygon_mask.tif", aoi.astype(np.uint8), GT, WKT)
    report["lattice"] = {"origin": [X0, Y1], "px_m": PX, "shape": [NY, NX],
                         "aoi_fraction_of_bbox": float(aoi.mean())}
    log(f"lattice {NY}x{NX} @ {PX:g} m; AOI polygon {100 * aoi.mean():.1f}% of bbox")

    # ---- geolocation lookup in FULL-FRAME indices ---------------------------
    import isce3
    from nisar.products.readers import SLC
    slc_c = SLC(hdf5file=str(rslc_crop[ref_d]))
    rgc = slc_c.getRadarGrid("A")
    geo = isce3.product.GeoGridParameters(start_x=xmin, start_y=ymax, spacing_x=5.0, spacing_y=-5.0,
                                          width=int((xmax - xmin) / 5), length=int((ymax - ymin) / 5),
                                          epsg=32645)
    bb = isce3.geometry.get_radar_bbox(geo, rgc, slc_c.getOrbit(), min_height=1000.0, max_height=8500.0,
                                       doppler=isce3.core.LUT2d(), margin=50)
    pad = 400
    sw_r0, sw_r1 = max(0, bb.first_azimuth_line - pad), min(L, bb.last_azimuth_line + pad)
    sw_c0, sw_c1 = max(0, bb.first_range_sample - pad), min(W, bb.last_range_sample + pad)
    report["radar_aoi_bbox_crop_frame"] = [bb.first_azimuth_line, bb.last_azimuth_line,
                                           bb.first_range_sample, bb.last_range_sample]
    xr_path, yr_path = (SC / "rdr2geo/freqA/x.rdr").resolve(), (SC / "rdr2geo/freqA/y.rdr").resolve()
    lut_key = manifest_of([xr_path, yr_path], {"win": [sw_r0, sw_r1, sw_c0, sw_c1], "AZ0": AZ0, "RG0": RG0,
                                               "lattice": [X0, Y1, NX, NY], "layer_version": LAYER_VERSION,
                                               "method": "kdtree-nearest-utm", "search_m": 4 * args.geoloc_tol})
    lut_path = LAY / "lut_fullframe_rowcol.tif"     # band 1 row, band 2 col (full frame), band 3 residual (m)
    lut_man = LAY / "lut_fullframe_rowcol.json"
    # Nearest radar sample per map cell, by KD-tree on the rdr2geo lon/lat projected
    # to UTM. This REPLACES a gdalwarp -geoloc lookup, which on this data (a) skipped
    # whole output chunks whenever it could not compute a chunk's source window --
    # rectangular holes, 66% AOI coverage, and the same "Unable to compute source
    # region ... skipping" warning v1 had printed -- and (b) with errorThreshold 0.125
    # carried a systematic half-cell bias (+2.54 m E, +2.60 m N). A direct nearest-
    # neighbour search has no chunking, no pixel-convention ambiguity (rdr2geo
    # coordinates are sample centres), and gives each cell's residual for free.
    if not (lut_path.exists() and lut_man.exists() and json.loads(lut_man.read_text()).get("key") == lut_key):
        from pyproj import Transformer
        from scipy.spatial import cKDTree
        tb = time.time()
        nr, nc = sw_r1 - sw_r0, sw_c1 - sw_c0
        log(f"building nearest-neighbour lookup from {args.rcrop_root} rdr2geo: "
            f"window az {sw_r0}..{sw_r1} rg {sw_c0}..{sw_c1} (crop frame) = {nr * nc / 1e6:.0f} Mpx")
        tr = Transformer.from_crs(4326, 32645, always_xy=True)
        xr = np.memmap(xr_path, dtype="<f8", mode="r", shape=(L, W))
        yr = np.memmap(yr_path, dtype="<f8", mode="r", shape=(L, W))
        pad_m = 4 * args.geoloc_tol
        flat_parts, px_parts, py_parts = [], [], []
        for r0 in range(0, nr, 1024):
            n = min(1024, nr - r0)
            lon = np.asarray(xr[sw_r0 + r0:sw_r0 + r0 + n, sw_c0:sw_c1])
            lat = np.asarray(yr[sw_r0 + r0:sw_r0 + r0 + n, sw_c0:sw_c1])
            ux, uy = tr.transform(lon, lat)
            ok = (np.isfinite(ux) & np.isfinite(uy) & (ux > X0 - pad_m) & (ux < X1 + pad_m)
                  & (uy > Y0 - pad_m) & (uy < Y1 + pad_m))
            f = np.flatnonzero(ok)
            flat_parts.append(f + r0 * nc)
            px_parts.append(ux.ravel()[f]); py_parts.append(uy.ravel()[f])
        del xr, yr
        flat = np.concatenate(flat_parts); del flat_parts
        pts = np.column_stack([np.concatenate(px_parts), np.concatenate(py_parts)]); del px_parts, py_parts
        log(f"  {len(flat) / 1e6:.0f} M radar samples near the lattice; building KD-tree")
        tree = cKDTree(pts, leafsize=64, balanced_tree=False, compact_nodes=False)
        log(f"  tree built in {time.time() - tb:.0f}s; querying {NY * NX / 1e6:.0f} M cells")
        rowb = np.full((NY, NX), -1, np.float32)
        colb = np.full((NY, NX), -1, np.float32)
        resb = np.full((NY, NX), np.nan, np.float32)
        cx = X0 + (np.arange(NX) + 0.5) * PX
        sdx = sdy = 0.0
        nhit = 0
        for r0 in range(0, NY, 256):
            n = min(256, NY - r0)
            cy = Y1 - (np.arange(r0, r0 + n) + 0.5) * PX
            gx, gy = np.meshgrid(cx, cy)
            q = np.column_stack([gx.ravel(), gy.ravel()])
            d, i = tree.query(q, k=1, distance_upper_bound=pad_m, workers=-1)
            hit = np.isfinite(d)
            fi = flat[i[hit]]
            rr = np.full(n * NX, -1, np.float32); cc = np.full(n * NX, -1, np.float32)
            rs = np.full(n * NX, np.nan, np.float32)
            rr[hit] = (fi // nc + sw_r0 + AZ0).astype(np.float32)
            cc[hit] = (fi % nc + sw_c0 + RG0).astype(np.float32)
            rs[hit] = d[hit].astype(np.float32)
            rowb[r0:r0 + n] = rr.reshape(n, NX); colb[r0:r0 + n] = cc.reshape(n, NX); resb[r0:r0 + n] = rs.reshape(n, NX)
            sdx += float(np.sum(pts[i[hit], 0] - q[hit, 0])); sdy += float(np.sum(pts[i[hit], 1] - q[hit, 1]))
            nhit += int(hit.sum())
        del tree, pts, flat
        write_tif(lut_path, None, GT, WKT, bands=[rowb, colb, resb])
        lut_man.write_text(json.dumps({"key": lut_key, "seconds": time.time() - tb,
                                       "mean_dx_m": sdx / max(nhit, 1), "mean_dy_m": sdy / max(nhit, 1)}))
        log(f"  lookup built in {time.time() - tb:.0f}s")
        del rowb, colb, resb
    lr, lc, lres = read_tif(lut_path, 1), read_tif(lut_path, 2), read_tif(lut_path, 3)
    valid_lut = (lr >= 0) & (lc >= 0)
    if valid_lut.mean() < 0.05:
        raise SystemExit(f"lookup has {100 * valid_lut.mean():.2f}% valid cells")
    RI, CI = lr.astype(np.int32), lc.astype(np.int32)      # FULL-FRAME reference indices
    del lr, lc
    geo_ok = valid_lut & (lres <= args.geoloc_tol)
    man = json.loads(lut_man.read_text())
    res_valid = lres[valid_lut]
    lut_check = {"method": "KD-tree nearest radar sample (UTM), search radius %g m" % (4 * args.geoloc_tol),
                 "valid_fraction_of_lattice": float(valid_lut.mean()),
                 "mean_dx_m": man["mean_dx_m"], "mean_dy_m": man["mean_dy_m"],
                 "p50_abs_m": float(np.percentile(res_valid, 50)), "p95_abs_m": float(np.percentile(res_valid, 95)),
                 "max_abs_m": float(res_valid.max()), "tolerance_m": args.geoloc_tol,
                 "excluded_fraction_of_valid": float(1 - geo_ok.sum() / max(valid_lut.sum(), 1)),
                 "p50_abs_m_kept": float(np.percentile(lres[geo_ok], 50)),
                 "p95_abs_m_kept": float(np.percentile(lres[geo_ok], 95))}
    # INDEPENDENT check of the stored indices: the residuals above come from the
    # KD-tree's own point coordinates, so they would look perfect even if the
    # flat-index -> (row, col) mapping were wrong. Read lon/lat back from the
    # rdr2geo rasters AT the stored indices and require the same distance.
    from pyproj import Transformer as _T
    _tr = _T.from_crs(4326, 32645, always_xy=True)
    _xr = np.memmap(xr_path, dtype="<f8", mode="r", shape=(L, W))
    _yr = np.memmap(yr_path, dtype="<f8", mode="r", shape=(L, W))
    vr_, vc_ = np.nonzero(valid_lut)
    sel = np.linspace(0, len(vr_) - 1, min(50000, len(vr_))).astype(np.int64)
    r_, c_ = vr_[sel], vc_[sel]
    ux, uy = _tr.transform(np.asarray(_xr[RI[r_, c_] - AZ0, CI[r_, c_] - RG0]),
                           np.asarray(_yr[RI[r_, c_] - AZ0, CI[r_, c_] - RG0]))
    d_indep = np.hypot(ux - (X0 + (c_ + 0.5) * PX), uy - (Y1 - (r_ + 0.5) * PX))
    idx_err = float(np.max(np.abs(d_indep - lres[r_, c_])))
    lut_check["index_verification_max_abs_diff_m"] = idx_err
    lut_check["index_verification_samples"] = int(len(sel))
    del _xr, _yr, vr_, vc_
    if idx_err > 0.01:
        raise SystemExit(f"lookup indices do not reproduce their residuals (max diff {idx_err:.3f} m) -- index mapping bug")
    del lres, res_valid
    # A systematic mean residual means a coordinate-convention error, not terrain.
    if abs(lut_check["mean_dx_m"]) > 1.0 or abs(lut_check["mean_dy_m"]) > 1.0:
        raise SystemExit(f"lookup has a systematic offset ({lut_check['mean_dx_m']:+.2f}, "
                         f"{lut_check['mean_dy_m']:+.2f}) m -- convention error; refusing to continue")
    ny2, nx2 = NY // 2, NX // 2
    quad = {"NW": (slice(0, ny2), slice(0, nx2)), "NE": (slice(0, ny2), slice(nx2, NX)),
            "SW": (slice(ny2, NY), slice(0, nx2)), "SE": (slice(ny2, NY), slice(nx2, NX))}
    lut_check["aoi_coverage_lut_valid"] = float((aoi & valid_lut).sum() / aoi.sum())
    lut_check["aoi_coverage_after_geoloc_mask"] = float((aoi & geo_ok).sum() / aoi.sum())
    lut_check["aoi_coverage_by_quadrant"] = {k: float((aoi[s] & geo_ok[s]).sum() / max(aoi[s].sum(), 1))
                                             for k, s in quad.items()}
    report["lookup_verification"] = lut_check

    def save():   # write after every section: a late failure must not lose the run
        (OUT / "comparison.json").write_text(json.dumps(report, indent=2))
    save()
    log(f"lookup: {lut_check}")

    # radar data window (crop frame) that every map cell samples
    rows_c, cols_c = RI[valid_lut] - AZ0, CI[valid_lut] - RG0
    WR0, WR1 = int(rows_c.min()), int(rows_c.max()) + 1
    WC0, WC1 = int(cols_c.min()), int(cols_c.max()) + 1
    del rows_c, cols_c
    if WR0 < 0 or WC0 < 0 or WR1 > L or WC1 > W:
        raise SystemExit(f"lookup indexes outside the crop: rows {WR0}..{WR1} of {L}, cols {WC0}..{WC1} of {W}")
    WNR, WNC = WR1 - WR0, WC1 - WC0
    report["radar_data_window_crop_frame"] = [WR0, WR1, WC0, WC1]
    log(f"radar data window (crop frame) rows {WR0}..{WR1} cols {WC0}..{WC1} = {WNR * WNC / 1e6:.0f} Mpx")

    def geocode(win_arr: np.ndarray) -> np.ndarray:
        """win_arr is the radar data window (crop-frame rows WR0.., cols WC0..)."""
        out = np.zeros((NY, NX), dtype=win_arr.dtype)
        out[valid_lut] = win_arr[RI[valid_lut] - AZ0 - WR0, CI[valid_lut] - RG0 - WC0]
        return out

    def crop_win(rel: str) -> np.ndarray:
        return read_window(SC / rel, WC0, WR0, WNC, WNR)

    def full_win(rel: str) -> np.ndarray:
        return read_window(SF / rel, RG0 + WC0, AZ0 + WR0, WNC, WNR)

    # ---- C1: dense-offset fields (metres) ----------------------------------
    log("C1 dense offsets")
    with h5py.File(runw_f, "r") as hf, h5py.File(runw_c, "r") as hc:
        zf, sf = hf[f"{OFF}/zeroDopplerTime"][()], hf[f"{OFF}/slantRange"][()]
        zc, sc = hc[f"{OFF}/zeroDopplerTime"][()], hc[f"{OFF}/slantRange"][()]
        units = {k: hf[f"{OFF}/HH/{k}"].attrs.get("units", b"?") for k in ("alongTrackOffset", "slantRangeOffset")}
        FF = {k: hf[f"{OFF}/HH/{k}"][()].astype(np.float64) for k in ("alongTrackOffset", "slantRangeOffset", "correlationSurfacePeak")}
        CC = {k: hc[f"{OFF}/HH/{k}"][()].astype(np.float64) for k in FF}
    # The dense-offset grid starts 52 samples into each product with a 32-sample
    # step, so a crop whose origin is not a multiple of 32 samples its estimates at
    # different radar positions (v2: 7254 % 32 = 22 -> 10 lines apart). Interpolate
    # the full field BILINEARLY onto the crop's grid positions, and report the
    # sub-step offsets instead of claiming an exact grid.
    dzf = (zf[-1] - zf[0]) / (len(zf) - 1)
    dsf = (sf[-1] - sf[0]) / (len(sf) - 1)
    frow = (zc - zf[0]) / dzf
    fcol = (sc - sf[0]) / dsf
    j0 = np.clip(np.floor(frow).astype(int), 0, len(zf) - 2)
    i0 = np.clip(np.floor(fcol).astype(int), 0, len(sf) - 2)
    wr = (frow - j0)[:, None]
    wgt = fcol - i0
    row_px = (zc - zdt0) / dz
    col_px = (sc - sr0) / rg_spacing_m
    inter = np.outer((row_px >= WR0) & (row_px < WR1), (col_px >= WC0) & (col_px < WC1))
    c1 = {"units_in_product": {k: (v.decode() if isinstance(v, bytes) else str(v)) for k, v in units.items()},
          "azimuth_sample_spacing_m": az_spacing_m, "range_sample_spacing_m": rg_spacing_m,
          "offset_grid_step_samples": [float(dzf / dz), float(dsf / rg_spacing_m)],
          "azimuth_subgrid_offset_steps": float(np.median(frow - np.floor(frow))),
          "range_subgrid_offset_steps": float(np.median(wgt)),
          "interpolation": "bilinear full-field onto crop grid positions"}
    for k in FF:
        F_ = FF[k]
        rowlo = F_[j0][:, i0] * (1 - wgt) + F_[j0][:, i0 + 1] * wgt
        rowhi = F_[j0 + 1][:, i0] * (1 - wgt) + F_[j0 + 1][:, i0 + 1] * wgt
        full_at = rowlo * (1 - wr) + rowhi * wr
        # a zero/NaN neighbour must not blend into a valid-looking value
        nb = [F_[j0][:, i0], F_[j0][:, i0 + 1], F_[j0 + 1][:, i0], F_[j0 + 1][:, i0 + 1]]
        full_ok = np.logical_and.reduce([np.isfinite(x) & (x != 0) for x in nb])
        full_at = np.where(full_ok, full_at, np.nan)
        m = inter & np.isfinite(full_at) & np.isfinite(CC[k]) & (CC[k] != 0) & (full_at != 0)
        d = full_at[m] - CC[k][m]
        e = {"n": int(m.sum()), "R_full_median": float(np.median(full_at[m])),
             "R_full_minus_R_crop_median": float(np.median(d)), "R_full_minus_R_crop_std": float(d.std()),
             "R_full_minus_R_crop_p95_abs": float(np.percentile(np.abs(d), 95))}
        scale = {"alongTrackOffset": az_spacing_m, "slantRangeOffset": rg_spacing_m}.get(k)
        if scale:
            for kk in list(e):
                if kk != "n":
                    e[kk + "_samples"] = e[kk] / scale
        c1[k] = e
    report["C1_dense_offsets"] = c1
    save()
    log(f"  {json.dumps({k: c1[k] for k in ('alongTrackOffset', 'slantRangeOffset')})}")

    # ---- C2: coregistered SLCs in the radar data window ---------------------
    log("C2 coregistered SLCs (radar domain)")
    c2 = {}
    for name, rel in (("reference_slc_control", "crossmul/freqA/HH/reference.slc"),
                      ("coregistered_secondary_slc", "fine_resample_slc/freqA/HH/coregistered_secondary.slc")):
        a_f, a_c = full_win(rel), crop_win(rel)
        ok = (a_c != 0) & (a_f != 0)
        e = phase_agreement(a_f[::2, ::2], a_c[::2, ::2], ok[::2, ::2], 0.001, quadrants=False, plane=False)
        amp_f, amp_c = np.abs(a_f), np.abs(a_c)
        rel_d = np.abs(amp_c[ok] - amp_f[ok]) / amp_f[ok]
        e.update({"bit_identical": bool(np.array_equal(a_c, a_f)),
                  "amp_rel_diff_median": float(np.median(rel_d)), "amp_rel_diff_p95": float(np.percentile(rel_d, 95)),
                  "subpixel_shift_crop_onto_full": tile_shifts(amp_f, amp_c, ok)})
        c2[name] = e
        log(f"  {name}: bit_identical={e['bit_identical']} R={e.get('phase_diff_coherence', 0):.6f}")
        del a_f, a_c, amp_f, amp_c, ok, rel_d
    report["C2_coregistered_slc"] = c2
    save()

    # ---- map-domain layers (cached by manifest) ------------------------------
    def cached(name: str, inputs: list[Path], build):
        p, jm = LAY / f"{name}.tif", LAY / f"{name}.json"
        key = manifest_of(inputs, {"lut": lut_key, "win": [WR0, WR1, WC0, WC1], "layer_version": LAYER_VERSION,
                                   "coh_win": 3, "name": name})
        if p.exists() and jm.exists() and json.loads(jm.read_text()).get("key") == key:
            return read_tif(p)
        a = build()
        write_tif(p, a, GT, WKT)
        jm.write_text(json.dumps({"key": key}))
        return a

    def rslc_layers(root: Path, rifg: Path, reader, tag: str):
        """Geocoded flattened ifg, flattened 3x3 coherence, unflattened 3x3 coherence."""
        inputs = [rifg, root / "crossmul/freqA/HH/reference.slc", root / "fine_resample_slc/freqA/HH/coregistered_secondary.slc"]
        box = {}

        def build_all():
            with h5py.File(rifg, "r") as h:
                r0, c0 = (AZ0 + WR0, RG0 + WC0) if tag == "full" else (WR0, WC0)
                z = h[IFGP][r0:r0 + WNR, c0:c0 + WNC]
            if z.dtype != np.complex64:
                z = z.astype(np.complex64)
            z[~np.isfinite(z)] = 0
            a = reader("crossmul/freqA/HH/reference.slc")
            b = reader("fine_resample_slc/freqA/HH/coregistered_secondary.slc")
            pa = np.abs(a); pa *= pa
            pb = np.abs(b); pb *= pb
            bad = (z == 0) | (pa == 0) | (pb == 0) | ~np.isfinite(pa) | ~np.isfinite(pb)
            z[bad] = 0; pa[bad] = 0; pb[bad] = 0
            box["ifg"] = geocode(z)
            c = box_coherence(z, pa, pb); box["coh_flat"] = geocode(c); del c, z
            np.conjugate(b, out=b); a *= b; del b
            a[bad] = 0; del bad
            c = box_coherence(a, pa, pb); box["coh_unflat"] = geocode(c); del c, a, pa, pb
            return box
        out = {}
        for k in ("ifg", "coh_flat", "coh_unflat"):
            out[k] = cached(f"R_{tag}_{k}", inputs, lambda k=k: (box or build_all())[k])
        return out

    log("assembling map-domain layers (RSLC flattened/unflattened coherence computed here)")
    Rf = rslc_layers(SF, SF / "RIFG.h5", full_win, "full")
    Rc = rslc_layers(SC, SC / "RIFG.h5", crop_win, "crop")
    gcf, grf = lattice_offset(G_full_ifg, X0, Y1)
    gcc, grc = lattice_offset(G_crop_ifg, X0, Y1)
    ifg = {"R_full": Rf["ifg"], "R_crop": Rc["ifg"],
           "G_full": c64(read_window(G_full_ifg, gcf, grf, NX, NY)),
           "G_crop": c64(read_window(G_crop_ifg, gcc, grc, NX, NY))}
    coh = {"R_full": Rf["coh_flat"], "R_crop": Rc["coh_flat"],
           "G_full": f32(read_window(P / "trackG" / "ifg_A_HH.coh.tif", gcf, grf, NX, NY)),
           "G_crop": f32(read_window(PGC / "trackG" / "ifg_A_HH_1x1.coh.tif", gcc, grc, NX, NY))}
    coh_unflat = {"R_full": Rf["coh_unflat"], "R_crop": Rc["coh_unflat"]}

    # ---- C3: GSLC per-date amplitude ----------------------------------------
    log("C3 GSLC per-date amplitude, full vs crop")
    c3 = {}
    for d in (ref_d, sec_d):
        af = f32(read_window(P / "trackG" / f"amp_A_HH_{d}.tif", gcf, grf, NX, NY))
        ac = f32(read_window(PGC / "trackG" / f"amp_A_HH_1x1_{d}.tif", gcc, grc, NX, NY))
        ok = aoi & (af > 0) & (ac > 0)
        rel_d = np.abs(ac[ok] - af[ok]) / af[ok]
        c3[d] = {"n": int(ok.sum()), "amp_rel_diff_median": float(np.median(rel_d)),
                 "amp_rel_diff_p95": float(np.percentile(rel_d, 95)),
                 "subpixel_shift_crop_onto_full": tile_shifts(af, ac, ok)}
    report["C3_gslc_amplitude"] = c3
    save()

    # ---- C4: cross-track geolocation ----------------------------------------
    log("C4 cross-track geolocation")
    c4 = {}
    for d, rel in ((ref_d, "crossmul/freqA/HH/reference.slc"),
                   (sec_d, "fine_resample_slc/freqA/HH/coregistered_secondary.slc")):
        ra = geocode(np.abs(full_win(rel)).astype(np.float32))
        ga = f32(read_window(P / "trackG" / f"amp_A_HH_{d}.tif", gcf, grf, NX, NY))
        ok = aoi & geo_ok & (ra > 0) & (ga > 0)
        c4[d] = {"n": int(ok.sum()), "shift_R_full_onto_G_full_px": tile_shifts(ga, ra, ok),
                 "log_amp_pearson_r": float(np.corrcoef(np.log(ra[ok][::7]), np.log(ga[ok][::7]))[0, 1])}
        del ra, ga
    report["C4_cross_track_geolocation"] = c4
    save()

    # ---- I1: wrapped phase ----------------------------------------------------
    log("I1 wrapped phase, all pairs")
    common = aoi & valid_lut & geo_ok
    for k in LEGS:
        common &= ifg[k] != 0
    report["common_mask"] = {"fraction_of_aoi": float(common.sum() / aoi.sum()),
                             "by_quadrant": {k: float((common[s]).sum() / max(aoi[s].sum(), 1)) for k, s in quad.items()}}
    bench = coh["R_full"]
    bins = ((0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.01))
    m8 = multilook(common.astype(np.float32), 8) >= 0.75
    bench8 = multilook(bench, 8)
    i1 = {}
    for i, p in enumerate(LEGS):
        for q in LEGS[i + 1:]:
            e5 = phase_agreement(ifg[p], ifg[q], common, PX / 1000)
            e5["by_benchmark_coherence"] = {f"{lo:.1f}-{min(hi, 1.0):.1f}": phase_agreement(
                ifg[p], ifg[q], common & (bench >= lo) & (bench < hi), PX / 1000, quadrants=False, plane=False).get("phase_diff_coherence")
                for lo, hi in bins}
            A8, B8 = multilook(ifg[p], 8), multilook(ifg[q], 8)
            e40 = phase_agreement(A8, B8, m8, 8 * PX / 1000)
            e40["by_benchmark_coherence"] = {f"{lo:.1f}-{min(hi, 1.0):.1f}": phase_agreement(
                A8, B8, m8 & (bench8 >= lo) & (bench8 < hi), 8 * PX / 1000, quadrants=False, plane=False).get("phase_diff_coherence")
                for lo, hi in bins}
            i1[f"{p}__vs__{q}"] = {"5m": e5, "40m": e40}
            log(f"  {p}__vs__{q}: 5m R={e5.get('phase_diff_coherence', 0):.4f} 40m R={e40.get('phase_diff_coherence', 0):.4f}")
    nomask = aoi & valid_lut & (ifg["R_full"] != 0) & (ifg["G_full"] != 0)
    i1["sensitivity_R_full__vs__G_full_without_geoloc_mask"] = {
        "5m": phase_agreement(ifg["R_full"], ifg["G_full"], nomask, PX / 1000, quadrants=False),
        "40m": phase_agreement(multilook(ifg["R_full"], 8), multilook(ifg["G_full"], 8),
                               multilook(nomask.astype(np.float32), 8) >= 0.75, 8 * PX / 1000, quadrants=False)}
    report["I1_wrapped_phase"] = i1
    save()

    # ---- I1b: azimuth residual x Doppler carrier attribution ----------------
    log("I1b Doppler-carrier attribution for R_full vs G_full")
    # R_full's OWN registration residual: the phase being explained is R_full - G_full,
    # and each chain builds resampled_az_offsets from its own culled grid (the full and
    # crop fields differ with p95 0.034 lines = ~0.14 rad at k ~ 3.96 rad/line).
    daz = cached("R_full_dense_azimuth_residual_lines", [SF / "rubbersheet_offsets/freqA/HH/resampled_az_offsets"],
                 lambda: geocode(f32(full_win("rubbersheet_offsets/freqA/HH/resampled_az_offsets"))))
    slc_s = SLC(hdf5file=str(rslc_full[sec_d]))
    rgs = slc_s.getRadarGrid("A")
    dop_s = slc_s.getDopplerCentroid(frequency="A")
    line_rate = 1.0 / rgs.az_time_interval
    # evaluate the secondary Doppler at each map cell's REFERENCE radar position
    # (adequate: the Doppler field varies over km, the registration over 1 line)
    tt = rgc.sensing_start + ((RI - AZ0).astype(np.float64)) * rgc.az_time_interval
    rr = rgc.starting_range + ((CI - RG0).astype(np.float64)) * rgc.range_pixel_spacing
    fdc = np.zeros((NY, NX), np.float32)
    vm = valid_lut
    tt_c = np.clip(tt[vm], dop_s.y_start, dop_s.y_end)
    rr_c = np.clip(rr[vm], dop_s.x_start, dop_s.x_end)
    fdc[vm] = dop_s.eval(tt_c, rr_c)
    del tt, rr
    kmap = (TWO_PI * fdc / line_rate).astype(np.float32)
    A8, B8 = multilook(ifg["R_full"], 8), multilook(ifg["G_full"], 8)
    # Block means over the pixels where each field is DEFINED (the lookup-valid
    # cells), not over the comparison mask -- otherwise both fields are biased
    # toward zero wherever a block is only partly valid.
    vfrac8 = np.maximum(multilook(valid_lut.astype(np.float32), 8), 1e-6)
    d8 = multilook(np.where(valid_lut, daz, 0).astype(np.float32), 8) / vfrac8
    k8 = multilook(np.where(valid_lut, kmap, 0).astype(np.float32), 8) / vfrac8
    base = phase_agreement(A8, B8, m8, 8 * PX / 1000, quadrants=False)
    i1b = {"line_rate_hz": line_rate, "secondary_doppler_hz_median": float(np.median(fdc[common])),
           "k_rad_per_line_median": float(np.median(kmap[common])),
           "azimuth_residual_lines": dist(daz[common]) if common.any() else {},
           "baseline_40m": {k: base.get(k) for k in ("phase_diff_coherence", "constant_offset_rad",
                                                     "planar_rad_per_km_x_east", "planar_rad_per_km_y_south")}}
    for sign in (+1, -1):
        corr = np.exp(-1j * sign * k8 * d8).astype(np.complex64)
        e = phase_agreement(A8 * corr, B8, m8, 8 * PX / 1000)
        e["by_benchmark_coherence"] = {f"{lo:.1f}-{min(hi, 1.0):.1f}": phase_agreement(
            A8 * corr, B8, m8 & (bench8 >= lo) & (bench8 < hi), 8 * PX / 1000, quadrants=False, plane=False).get("phase_diff_coherence")
            for lo, hi in bins}
        i1b[f"model_sign_{'+' if sign > 0 else '-'}"] = e
    zz = np.where(m8, A8 * np.conj(B8), 0)
    zz = np.where(np.abs(zz) > 0, zz / np.maximum(np.abs(zz), 1e-30), 0)
    grid_k = np.linspace(-8, 8, 321)
    scores = [float(abs(np.mean(zz[m8] * np.exp(-1j * kk * d8[m8])))) for kk in grid_k]
    i1b["free_fit_rad_per_line"] = float(grid_k[int(np.argmax(scores))])
    i1b["free_fit_coherence"] = float(max(scores))
    i1b["note"] = ("sign chosen empirically; which chain carries the registration residual is not "
                   "established by this test (GSLC inter-date registration was measured at 0.02 px)")
    report["I1b_doppler_carrier_attribution"] = i1b
    save()
    log(f"  k={i1b['k_rad_per_line_median']:.3f} rad/line; +: {i1b['model_sign_+'].get('phase_diff_coherence', 0):.4f} "
        f"-: {i1b['model_sign_-'].get('phase_diff_coherence', 0):.4f}; free fit {i1b['free_fit_rad_per_line']:.2f}")
    del zz

    # ---- I2: coherence ---------------------------------------------------------
    log("I2 coherence")
    cm = common.copy()
    for k in LEGS:
        cm &= coh[k] > 0
    cm &= coh_unflat["R_full"] > 0
    i2 = {"distributions": {k: dist(coh[k][cm]) for k in LEGS},
          "R_full_unflattened": dist(coh_unflat["R_full"][cm]),
          "flattening_effect_R_full_flat_minus_unflat": dist(coh["R_full"][cm] - coh_unflat["R_full"][cm]),
          "paired": {}, "bias_floor_3x3": math.sqrt(math.pi) / 6}
    for k in LEGS[1:]:
        dd = coh["R_full"][cm] - coh[k][cm]          # P = R_full, per the convention
        i2["paired"][f"R_full__vs__{k}"] = {"median": float(np.median(dd)), "std": float(dd.std()),
                                          "p95_abs": float(np.percentile(np.abs(dd), 95)),
                                          "pearson_r": float(np.corrcoef(coh[k][cm][::5], coh["R_full"][cm][::5])[0, 1])}
    cm8 = multilook(cm.astype(np.float32), 8) >= 0.999
    rat = {}
    g8, r8 = multilook(coh["G_full"], 8), multilook(coh["R_full"], 8)
    for lo, hi in bins:
        mm = cm8 & (r8 >= lo) & (r8 < hi)
        if mm.sum() > 100:
            rat[f"{lo:.1f}-{min(hi, 1.0):.1f}"] = float(np.median(g8[mm] / r8[mm]))
    i2["G_full_over_R_full_ratio_40m_by_R_bin"] = rat
    report["I2_coherence"] = i2
    save()
    del coh_unflat

    # ---- I3: ionosphere -----------------------------------------------------
    log("I3 ionosphere")
    IO = "HH/ionospherePhaseScreen"
    i3 = {}
    with h5py.File(runw_f, "r") as hf, h5py.File(runw_c, "r") as hc:
        zf9, sf9 = hf[f"{RUNW_A}/zeroDopplerTime"][()], hf[f"{RUNW_A}/slantRange"][()]
        zc9, sc9 = hc[f"{RUNW_A}/zeroDopplerTime"][()], hc[f"{RUNW_A}/slantRange"][()]
        ro, ok_r = coord_index(zf9, zc9)
        co, ok_c = coord_index(sf9, sc9)
        rows9, cols9 = hc[f"{RUNW_A}/{IO}"].shape
        r0, c0 = int(round(ro)), int(round(co))
        align9 = {"row_offset": ro, "col_offset": co, "exact": bool(abs(ro - r0) < 1e-6 and abs(co - c0) < 1e-6)}
        io_f = hf[f"{RUNW_A}/{IO}"][r0:r0 + rows9, c0:c0 + cols9].astype(np.float32)
        io_c = hc[f"{RUNW_A}/{IO}"][()].astype(np.float32)
        un_f = hf[f"{RUNW_A}/HH/unwrappedPhase"][r0:r0 + rows9, c0:c0 + cols9].astype(np.float32)
        un_c = hc[f"{RUNW_A}/HH/unwrappedPhase"][()].astype(np.float32)
    # radar AOI data window on the 9x8 grid
    m9 = np.zeros(io_c.shape, bool)
    m9[max(0, WR0 // 9):WR1 // 9, max(0, WC0 // 8):WC1 // 8] = True
    i3["R_radar9x8_alignment"] = align9
    i3["R_full__vs__R_crop_screen"] = screen_agreement(io_f, io_c, m9)
    i3["R_full__vs__R_crop_unwrapped_A_cycles"] = cycle_difference(un_f, un_c, m9)
    sb_f, sb_c = SF / "ionosphere/main_side_band", SC / "ionosphere/main_side_band"
    try:
        with h5py.File(sb_f / "RUNW.h5", "r") as hf, h5py.File(sb_c / "RUNW.h5", "r") as hc:
            zfb, sfb = hf[f"{RUNW_B}/zeroDopplerTime"][()], hf[f"{RUNW_B}/slantRange"][()]
            zcb, scb = hc[f"{RUNW_B}/zeroDopplerTime"][()], hc[f"{RUNW_B}/slantRange"][()]
            rob, _ = coord_index(zfb, zcb)
            cob, _ = coord_index(sfb, scb)
            nrb, ncb = hc[f"{RUNW_B}/HH/unwrappedPhase"].shape
            exact_b = abs(rob - round(rob)) < 1e-3 and abs(cob - round(cob)) < 1e-3
            # The side band is 9x8 looks on the freq-B grid, so exact cell alignment
            # needs the freq-A range origin on a multiple of 64 samples; v2 is on a
            # multiple of 8 only (1/8-cell = 25 m apart). Interpolate the full-tile
            # layers onto the crop cell centres rather than pairing shifted cells.
            rlo, clo = max(int(math.floor(rob)) - 1, 0), max(int(math.floor(cob)) - 1, 0)
            rhi = min(int(math.ceil(rob)) + nrb + 1, hf[f"{RUNW_B}/HH/unwrappedPhase"].shape[0])
            chi = min(int(math.ceil(cob)) + ncb + 1, hf[f"{RUNW_B}/HH/unwrappedPhase"].shape[1])
            yy_b, xx_b = np.meshgrid(np.arange(nrb) + rob - rlo, np.arange(ncb) + cob - clo, indexing="ij")

            def onto_crop(arr):
                from scipy.ndimage import map_coordinates
                if np.iscomplexobj(arr):
                    u = np.where(np.abs(arr) > 0, arr / np.maximum(np.abs(arr), 1e-30), 0)
                    return (map_coordinates(u.real, [yy_b, xx_b], order=1, mode="nearest")
                            + 1j * map_coordinates(u.imag, [yy_b, xx_b], order=1, mode="nearest"))
                valid = (np.isfinite(arr) & (arr != 0)).astype(np.float32)
                v = map_coordinates(np.nan_to_num(arr).astype(np.float64), [yy_b, xx_b], order=1, mode="nearest")
                ok = map_coordinates(valid, [yy_b, xx_b], order=1, mode="nearest") > 0.999
                return np.where(ok, v, np.nan).astype(np.float32)

            ub_f = onto_crop(hf[f"{RUNW_B}/HH/unwrappedPhase"][rlo:rhi, clo:chi])
            ub_c = hc[f"{RUNW_B}/HH/unwrappedPhase"][()].astype(np.float32)
            cohb_f = onto_crop(hf[f"{RUNW_B}/HH/coherenceMagnitude"][rlo:rhi, clo:chi])
            cohb_c = hc[f"{RUNW_B}/HH/coherenceMagnitude"][()].astype(np.float32)
        with h5py.File(sb_f / "RIFG.h5", "r") as hf, h5py.File(sb_c / "RIFG.h5", "r") as hc:
            wb_f = onto_crop(hf[f"{RIFG_B}/HH/wrappedInterferogram"][rlo:rhi, clo:chi])
            wb_c = hc[f"{RIFG_B}/HH/wrappedInterferogram"][()]
        mb = np.ones(ub_c.shape, bool)
        i3["R_sideband_alignment"] = {"row_offset_cells": rob, "col_offset_cells": cob, "exact": bool(exact_b),
                                      "method": "exact slice" if exact_b else "full-tile layers bilinearly interpolated onto crop cell centres"}
        i3["R_full__vs__R_crop_unwrapped_B_cycles"] = cycle_difference(ub_f, ub_c, mb)
        wm = (wb_f != 0) & (wb_c != 0) & np.isfinite(wb_f) & np.isfinite(wb_c)
        zb = wb_f[wm] * np.conj(wb_c[wm])
        zb = zb / np.maximum(np.abs(zb), 1e-30)
        i3["R_full__vs__R_crop_wrapped_B"] = {"circular_mean_rad": float(np.angle(zb.mean())),
                                              "phase_diff_coherence": float(abs(zb.mean())), "n": int(wm.sum())}
        i3["R_sideband_coherence_median"] = {"R_full": float(np.nanmedian(cohb_f[cohb_f > 0])),
                                             "R_crop": float(np.nanmedian(cohb_c[cohb_c > 0]))}
    except Exception as exc:        # a missing intermediate must not sink the whole report
        i3["R_sideband_error"] = repr(exc)

    g_f, g_c = P / "trackG" / "ionosphere", PGC / "trackG" / "ionosphere"
    report["I3_ionosphere"] = i3      # keep the R results if the G block fails
    save()
    gds_c = gdal.Open(str(g_c / "dispersive_filtered.tif"))
    gt_c = gds_c.GetGeoTransform()
    ox, oy = lattice_offset(g_f / "dispersive_filtered.tif", gt_c[0], gt_c[3])
    nxg, nyg = gds_c.RasterXSize, gds_c.RasterYSize
    c40 = (np.arange(nxg) + 0.5) * gt_c[1] + gt_c[0]
    r40 = gt_c[3] - (np.arange(nyg) + 0.5) * abs(gt_c[5])
    m40 = np.outer((r40 >= Y0) & (r40 <= Y1), (c40 >= X0) & (c40 <= X1))
    gio_f = read_window(g_f / "dispersive_filtered.tif", ox, oy, nxg, nyg)
    gio_c = read_tif(g_c / "dispersive_filtered.tif")
    i3["G_full__vs__G_crop_screen_40m"] = screen_agreement(gio_f, gio_c, m40)
    for band in ("A", "B"):
        i3[f"G_full__vs__G_crop_unwrapped_{band}_cycles"] = cycle_difference(
            read_window(g_f / f"unw_{band}.tif", ox, oy, nxg, nyg), read_tif(g_c / f"unw_{band}.tif"), m40)
    i3["G_cycle_offsets_applied"] = {
        "G_full": json.loads((g_f / "ionosphere.json").read_text()).get("cycle_offsets", {}),
        "G_crop": json.loads((g_c / "ionosphere.json").read_text()).get("cycle_offsets", {})}
    for kk in list(i3["G_cycle_offsets_applied"]):
        i3["G_cycle_offsets_applied"][kk] = {x: i3["G_cycle_offsets_applied"][kk].get(x) for x in ("A", "B")}

    # cross-track, all four screens on the 5 m lattice (every 4th sample)
    rows_full, cols_full = RI[valid_lut], CI[valid_lut]
    scr = {}
    for lbl, arr_full, is_crop in (("R_full", None, False), ("R_crop", io_c, True)):
        a = np.full((NY, NX), np.nan, np.float32)
        if is_crop:
            rr9 = np.clip((rows_full - AZ0) // 9, 0, io_c.shape[0] - 1)
            cc9 = np.clip((cols_full - RG0) // 8, 0, io_c.shape[1] - 1)
            a[valid_lut] = io_c[rr9, cc9]
        else:
            with h5py.File(runw_f, "r") as hf:
                full9 = hf[f"{RUNW_A}/{IO}"]
                rr9 = np.clip(rows_full // 9, 0, full9.shape[0] - 1)
                cc9 = np.clip(cols_full // 8, 0, full9.shape[1] - 1)
                rlo, rhi, clo, chi = int(rr9.min()), int(rr9.max()) + 1, int(cc9.min()), int(cc9.max()) + 1
                sub = full9[rlo:rhi, clo:chi].astype(np.float32)
            a[valid_lut] = sub[rr9 - rlo, cc9 - clo]
        scr[lbl] = a
    yc = Y1 - (np.arange(NY) + 0.5) * PX
    xc = X0 + (np.arange(NX) + 0.5) * PX
    for lbl, path in (("G_full", g_f / "dispersive_filtered.tif"), ("G_crop", g_c / "dispersive_filtered.tif")):
        dsg = gdal.Open(str(path))
        gt = dsg.GetGeoTransform()
        rr = np.floor((gt[3] - yc) / abs(gt[5])).astype(int)
        cc = np.floor((xc - gt[0]) / gt[1]).astype(int)
        r_lo, r_hi = max(int(rr.min()), 0), min(int(rr.max()) + 1, dsg.RasterYSize)
        c_lo, c_hi = max(int(cc.min()), 0), min(int(cc.max()) + 1, dsg.RasterXSize)
        sub = dsg.GetRasterBand(1).ReadAsArray(c_lo, r_lo, c_hi - c_lo, r_hi - r_lo)
        scr[lbl] = sub[np.clip(rr - r_lo, 0, sub.shape[0] - 1)][:, np.clip(cc - c_lo, 0, sub.shape[1] - 1)]
    dm = (aoi & geo_ok)[::4, ::4]
    i3["cross_track_5m_lattice"] = {f"{p}__vs__{q}": screen_agreement(scr[p][::4, ::4], scr[q][::4, ::4], dm)
                                    for i, p in enumerate(LEGS) for q in LEGS[i + 1:]}
    i3["constants"] = {"rad_per_cycle_A": RAD_PER_CYCLE_A, "rad_per_cycle_B": RAD_PER_CYCLE_B,
                       "rad_per_joint_cycle": RAD_PER_JOINT_CYCLE, "rad2tecu": RAD2TECU}
    i3["support_note"] = ("R screens are ISCE3's main_side_band solve on its decimated freq-B grid "
                          "(~40 m azimuth x ~200 m range) with a pixel-unit Gaussian; G screens are "
                          "solved at isotropic 40 m with a 10 km Gaussian. Not a common support.")
    report["I3_ionosphere"] = i3
    log(f"  R screen: {json.dumps(i3['R_full__vs__R_crop_screen'])}")
    log(f"  R unwrapped A cycles: {json.dumps(i3['R_full__vs__R_crop_unwrapped_A_cycles'])}")
    log(f"  G screen: {json.dumps(i3['G_full__vs__G_crop_screen_40m'])}")

    (OUT / "comparison.json").write_text(json.dumps(report, indent=2))
    log(f"wrote {OUT / 'comparison.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
