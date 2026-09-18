#!/usr/bin/env python3
"""
Four-way comparison: RSLC vs GSLC, full tile vs cropped-first.

    python -u tools/compare_four_way.py            # defaults are the Nepal GLOF case

Writes everything under <case>/comparison/ -- never /tmp -- and caches the
expensive intermediates (geolocation lookup, geocoded layers), so a crash or a
reboot costs a re-read, not a re-warp.

THE FOUR LEGS
-------------
  R_full  RSLC, full tile, then windowed to the AOI      <- the BENCHMARK
  R_crop  RSLC granule cropped to the AOI first
  G_full  GSLC, full tile, then windowed to the AOI
  G_crop  GSLC from the cropped granules

WHAT IS COMPARED, AND WHY EACH TEST IS SHAPED THE WAY IT IS
-----------------------------------------------------------
COREGISTRATION
  C1  RSLC dense-offset fields, full vs crop. The secondary crop starts at a
      different line/sample than the reference crop (+824 lines, -13 samples
      here), so IF the stored offsets were raw index differences they would
      differ by exactly that frame shift. Both hypotheses are tested. MEASURED:
      the RAW difference is ~0 and the frame-shifted one is off by the full
      shift -- the RUNW pixelOffsets are frame-independent residuals relative
      to the geometric prediction, so they compare directly. (The first draft of
      this tool predicted the opposite; the data corrected it.)
  C2  RSLC coregistered secondary SLC, full vs crop, in radar coordinates. This
      is the OUTPUT of coregistration and isolates it from crossmul/flattening.
      The reference SLC is compared too, as a control: it is a copy of the input
      and must be bit-identical.
  C3  GSLC per-date amplitude, full vs crop (geocoding is GSLC's coregistration).
  C4  Cross-track geolocation: RSLC amplitude geocoded through its own rdr2geo
      lon/lat arrays vs the GSLC amplitude, as a sub-pixel shift. This is the
      only coregistration statistic that can compare R against G.

INTERFEROMETRY
  I1  Wrapped phase, all 6 pairs, at 5 m and at 40 m (8x8 complex average),
      over the user's AOI POLYGON (not the bbox, not the crop buffer), overall
      and stratified by benchmark coherence.
  I2  Coherence distributions on one common mask, plus paired differences
      against the benchmark.
  I3  Ionosphere: within-track full vs crop, and cross-track -- split into a
      CONSTANT offset and a residual SHAPE difference, because the two-band
      solve fixes the shape but leaves the absolute level ambiguous.

GEOCODING THE RSLC LEGS -- nearest neighbour through a lookup table
-------------------------------------------------------------------
The RSLC products are in radar coordinates. Rather than warping every layer, a
(row, col) index raster is warped ONCE through the rdr2geo lon/lat arrays with
nearest-neighbour resampling. Every RSLC layer is then geocoded by plain
indexing, so all of them are sampled at exactly the same radar pixels.

The cropped reference radar grid is pixel-for-pixel the full grid offset by the
crop origin (verified from the coordinate arrays), and rdr2geo is per-pixel, so
ONE lookup serves both R_full and R_crop. The R_full-vs-R_crop result in map
coordinates must therefore reproduce the radar-domain result; it is reported as
an internal consistency check.

Three GDAL traps, all hit on this project:
  * X_DATASET / Y_DATASET must be ABSOLUTE. Relative paths are resolved against
    the VRT's directory, and the warp then "succeeds" with an all-zero raster
    ("Too many points failed to transform").
  * rdr2geo lon/lat are PIXEL-CENTRE coordinates; GDAL assumes TOP_LEFT_CORNER
    unless told otherwise, which silently shifts everything half a pixel. Set
    GEOREFERENCING_CONVENTION=PIXEL_CENTER. The lookup is then VERIFIED by
    projecting the chosen radar pixels' lon/lat back to UTM.
  * SRS "EPSG:4326" is not accepted in GEOLOCATION metadata ("missing ["); it
    must be WKT.

CAVEAT that shapes the cross-track numbers: nearest-neighbour geocoding picks the
closest radar sample (up to half a pixel away), while GSLC sinc-interpolates to
the exact map-pixel centre. At 5 m that mis-sampling decorrelates speckle and
inflates the R-vs-G phase difference; at 40 m it largely averages out. The 40 m
numbers are the fair cross-track comparison; the 5 m numbers bound the
resampling cost.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

_ARGV = sys.argv[1:]
sys.argv = [sys.argv[0]]

import h5py  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402

gdal.UseExceptions()
ogr.UseExceptions()

TOOLS = Path(__file__).resolve().parent
RSW = "/science/LSAR/RSLC/swaths"
OFF = "science/LSAR/RUNW/swaths/frequencyA/pixelOffsets"
IFGP = "science/LSAR/RIFG/swaths/frequencyA/interferogram/HH/wrappedInterferogram"
IONO = "science/LSAR/RUNW/swaths/frequencyA/interferogram/HH/ionospherePhaseScreen"

F0, F1 = 1.239e9, 1.2935e9
RAD2TECU = 299792458.0 * F0 / (4 * math.pi * 40.31) / 1e16
TWO_PI = 2 * math.pi
_R, _S = F1 / F0, F0 / F1
_DET = _S - _R
RAD_PER_CYCLE_A = (-_R * TWO_PI) / _DET          # +76.17 rad of dispersive
RAD_PER_CYCLE_B = TWO_PI / _DET                   # -72.96 rad
RAD_PER_JOINT_CYCLE = RAD_PER_CYCLE_A + RAD_PER_CYCLE_B   # +3.21 rad

LEGS = ("R_full", "R_crop", "G_full", "G_crop")
T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------
def crop_origin(full_h5: Path, crop_h5: Path, freq: str = "A") -> tuple[int, int, int, int]:
    """(az0, rg0, lines, samples) of a crop, recovered from its coordinate arrays."""
    with h5py.File(full_h5, "r") as hf, h5py.File(crop_h5, "r") as hc:
        zf, zc = hf[f"{RSW}/zeroDopplerTime"][()], hc[f"{RSW}/zeroDopplerTime"][()]
        sf = hf[f"{RSW}/frequency{freq}/slantRange"][()]
        sc = hc[f"{RSW}/frequency{freq}/slantRange"][()]
    az0 = int(np.argmin(np.abs(zf - zc[0])))
    rg0 = int(np.argmin(np.abs(sf - sc[0])))
    if not (np.allclose(zf[az0:az0 + len(zc)], zc) and np.allclose(sf[rg0:rg0 + len(sc)], sc)):
        raise SystemExit(f"{crop_h5.name} is not an exact slice of {full_h5.name}")
    return az0, rg0, len(zc), len(sc)


def write_tif(path: Path, arr: np.ndarray, gt, wkt, nodata=None) -> None:
    dt = {np.dtype("float32"): gdal.GDT_Float32, np.dtype("complex64"): gdal.GDT_CFloat32,
          np.dtype("uint8"): gdal.GDT_Byte}[arr.dtype]
    ds = gdal.GetDriverByName("GTiff").Create(
        str(path), arr.shape[1], arr.shape[0], 1, dt,
        ["TILED=YES", "COMPRESS=DEFLATE", "ZLEVEL=1", "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform(gt)
    ds.SetProjection(wkt)
    b = ds.GetRasterBand(1)
    if nodata is not None:
        b.SetNoDataValue(nodata)
    b.WriteArray(arr)
    ds = None


# The dataset MUST be held in a local. `gdal.Open(p).GetRasterBand(1).ReadAsArray()`
# lets the dataset be garbage-collected while its band is still in use, and SWIG
# then fails with "argument 1 of type 'GDALRasterBandShadow *'".
def read_tif(path: Path) -> np.ndarray:
    ds = gdal.Open(str(path))
    return ds.GetRasterBand(1).ReadAsArray()


def read_window(path: Path, xoff: int, yoff: int, nx: int, ny: int) -> np.ndarray:
    ds = gdal.Open(str(path))
    return ds.GetRasterBand(1).ReadAsArray(xoff, yoff, nx, ny)


def lattice_offset(path: Path, x0: float, y0: float) -> tuple[int, int, float]:
    """Integer (col, row) of map point (x0, y0) in a raster; refuses a non-aligned lattice."""
    gt = gdal.Open(str(path)).GetGeoTransform()
    c = (x0 - gt[0]) / gt[1]
    r = (gt[3] - y0) / abs(gt[5])
    if abs(c - round(c)) > 1e-6 or abs(r - round(r)) > 1e-6:
        raise SystemExit(f"{path.name}: lattice not aligned to ({x0}, {y0}) -- offset {c}, {r}")
    return int(round(c)), int(round(r)), gt[1]


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def phase_agreement(a: np.ndarray, b: np.ndarray, m: np.ndarray, px_km: float) -> dict:
    """Wrapped-phase agreement of two complex rasters over mask m."""
    m = m & (a != 0) & (b != 0) & np.isfinite(a) & np.isfinite(b)
    n = int(m.sum())
    if n < 100:
        return {"n": n}
    z = a[m] * np.conj(b[m])
    z /= np.abs(z)
    mean = z.mean()
    R = float(abs(mean))
    off = float(np.angle(mean))
    d = np.angle(z * np.exp(-1j * off)).astype(np.float32)
    out = {
        "n": n,
        "phase_diff_coherence": R,
        "constant_offset_rad": off,
        "circular_std_rad": float(math.sqrt(max(0.0, -2.0 * math.log(max(R, 1e-12))))),
        "abs_p50_rad": float(np.percentile(np.abs(d), 50)),
        "abs_p95_rad": float(np.percentile(np.abs(d), 95)),
        "frac_lt_0p1": float((np.abs(d) < 0.1).mean()),
        "frac_lt_0p5": float((np.abs(d) < 0.5).mean()),
    }
    # Planar ramp in the difference. A ramp between two interferograms is a
    # reference-phase / baseline-handling difference, not decorrelation, so it
    # is separated out rather than left to inflate the scatter.
    rows, cols = np.nonzero(m)
    step = max(1, n // 400000)
    sel = np.abs(d[::step]) < 1.0
    if sel.sum() > 1000:
        yy = rows[::step][sel] * px_km
        xx = cols[::step][sel] * px_km
        G = np.column_stack([xx, yy, np.ones_like(xx)])
        coef, *_ = np.linalg.lstsq(G, d[::step][sel], rcond=None)
        ramp = coef[0] * (cols * px_km) + coef[1] * (rows * px_km) + coef[2]
        R2 = float(abs(np.mean(z * np.exp(-1j * (off + ramp)))))
        out.update({
            "ramp_rad_per_km_x": float(coef[0]),
            "ramp_rad_per_km_y": float(coef[1]),
            "phase_diff_coherence_after_ramp": R2,
        })
    return out


def multilook(a: np.ndarray, k: int) -> np.ndarray:
    ny, nx = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    return a[:ny, :nx].reshape(ny // k, k, nx // k, k).mean(axis=(1, 3))


def dist(v: np.ndarray) -> dict:
    return {"n": int(v.size), "median": float(np.median(v)), "mean": float(v.mean()),
            "p5": float(np.percentile(v, 5)), "p95": float(np.percentile(v, 95)),
            "frac_gt_0p5": float((v > 0.5).mean())}


def tile_shifts(a: np.ndarray, b: np.ndarray, valid: np.ndarray, tile: int = 512, grid: int = 5) -> dict:
    """Sub-pixel shift of b relative to a from log-amplitude phase correlation on a tile grid."""
    from skimage.registration import phase_cross_correlation
    ny, nx = a.shape
    ys = np.linspace(tile, ny - 2 * tile, grid).astype(int)
    xs = np.linspace(tile, nx - 2 * tile, grid).astype(int)
    sh = []
    for y in ys:
        for x in xs:
            v = valid[y:y + tile, x:x + tile]
            if v.mean() < 0.95:
                continue
            ta = np.log(np.maximum(a[y:y + tile, x:x + tile], 1e-6))
            tb = np.log(np.maximum(b[y:y + tile, x:x + tile], 1e-6))
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


def screen_agreement(a: np.ndarray, b: np.ndarray, m: np.ndarray) -> dict:
    """Ionosphere screens: constant offset + residual shape, in rad and TECU."""
    m = m & np.isfinite(a) & np.isfinite(b) & (a != 0) & (b != 0)
    if m.sum() < 100:
        return {"n": int(m.sum())}
    d = (a[m] - b[m]).astype(np.float64)
    off = float(np.median(d))
    res = d - off
    r = float(np.corrcoef(a[m], b[m])[0, 1])
    # Which integer (m, n) cycle combination explains the constant best?
    best = min(((abs(off - (mm * RAD_PER_CYCLE_A + nn * RAD_PER_CYCLE_B)), mm, nn)
                for mm in range(-8, 9) for nn in range(-8, 9)))
    return {"n": int(m.sum()),
            "median_a_rad": float(np.median(a[m])), "median_b_rad": float(np.median(b[m])),
            "median_a_tecu": float(np.median(a[m]) * RAD2TECU),
            "median_b_tecu": float(np.median(b[m]) * RAD2TECU),
            "constant_offset_rad": off, "constant_offset_tecu": off * RAD2TECU,
            "constant_offset_in_joint_cycles": off / RAD_PER_JOINT_CYCLE,
            "nearest_cycle_combo": {"m_A": best[1], "n_B": best[2], "residual_rad": best[0]},
            "residual_std_rad": float(res.std()), "residual_std_tecu": float(res.std() * RAD2TECU),
            "residual_p95_abs_tecu": float(np.percentile(np.abs(res), 95) * RAD2TECU),
            "pearson_r": r}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", type=Path, default=Path("/home/sharath/isce3/case_studies/nepal_glof"))
    ap.add_argument("--pair", default="20260714_20260726")
    ap.add_argument("--tag", default="20260714_20260726_A_HH_1x1")
    ap.add_argument("--kml", type=Path, default=Path("/home/sharath/asf_slc/glof_exact_aoi.kml"))
    ap.add_argument("--buffer", type=int, default=500, help="crop buffer to exclude (radar px)")
    ap.add_argument("--force-lut", action="store_true")
    ap.add_argument("--geoloc-tol", type=float, default=15.0,
                    help="max distance (m) between a map cell and the radar pixel it "
                         "samples; farther = shadow/layover backmap fill, excluded")
    args = ap.parse_args(_ARGV)

    C = args.case
    OUT = C / "comparison"
    LAY = OUT / "layers"
    LAY.mkdir(parents=True, exist_ok=True)
    ref_d, sec_d = args.pair.split("_")
    P = C / "pairs" / args.pair
    PA = C / "aoi" / "pairs" / args.pair
    SF = C / "scratch" / "trackR" / args.tag
    SC = C / "aoi" / "scratch" / "trackR" / args.tag
    rslc_full = {d: next((C / "L1_RSLC").glob(f"*_{d}T*.h5")) for d in (ref_d, sec_d)}
    rslc_crop = {d: C / "L1_RSLC_AOI" / f"{d}_aoi.h5" for d in (ref_d, sec_d)}
    G_full_ifg = P / "trackG" / "ifg_A_HH.igram.tif"
    G_crop_ifg = PA / "trackG" / "ifg_A_HH_1x1.igram.tif"
    report: dict = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # ---- geometry --------------------------------------------------------
    ref_o = crop_origin(rslc_full[ref_d], rslc_crop[ref_d])
    sec_o = crop_origin(rslc_full[sec_d], rslc_crop[sec_d])
    AZ0, RG0, L, W = ref_o
    d_az, d_rg = sec_o[0] - ref_o[0], sec_o[1] - ref_o[1]
    log(f"reference crop origin ({AZ0}, {RG0}) size {L}x{W}; secondary ({sec_o[0]}, {sec_o[1]}) "
        f"-> frame shift d_az={d_az:+d} d_rg={d_rg:+d}")
    report["crop_origins"] = {"reference": ref_o[:2], "secondary": sec_o[:2],
                              "frame_shift_az": d_az, "frame_shift_rg": d_rg}

    # AOI polygon -> UTM, and a 5 m lattice snapped outward onto the GSLC crop lattice
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
    ggt = gdal.Open(str(G_crop_ifg)).GetGeoTransform()
    PX = ggt[1]
    X0 = ggt[0] + math.floor((xmin - ggt[0]) / PX) * PX
    X1 = ggt[0] + math.ceil((xmax - ggt[0]) / PX) * PX
    Y1 = ggt[3] - math.floor((ggt[3] - ymax) / PX) * PX
    Y0 = ggt[3] - math.ceil((ggt[3] - ymin) / PX) * PX
    NX, NY = int(round((X1 - X0) / PX)), int(round((Y1 - Y0) / PX))
    GT = (X0, PX, 0.0, Y1, 0.0, -PX)
    WKT = sutm.ExportToWkt()
    log(f"AOI lattice {NY}x{NX} @ {PX:g} m, origin ({X0}, {Y1})")
    report["lattice"] = {"origin": [X0, Y1], "px_m": PX, "shape": [NY, NX]}

    mem = gdal.GetDriverByName("MEM").Create("", NX, NY, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(GT); mem.SetProjection(WKT)
    vds = ogr.GetDriverByName("MEM").CreateDataSource("aoi")
    vl = vds.CreateLayer("aoi", srs=sutm)
    feat = ogr.Feature(vl.GetLayerDefn()); feat.SetGeometry(geom); vl.CreateFeature(feat)
    gdal.RasterizeLayer(mem, [1], vl, burn_values=[1])
    aoi = mem.GetRasterBand(1).ReadAsArray().astype(bool)
    write_tif(OUT / "aoi_polygon_mask.tif", aoi.astype(np.uint8), GT, WKT)
    log(f"AOI polygon covers {100 * aoi.mean():.1f}% of its lattice bbox")

    # ---- C1: dense-offset fields ------------------------------------------
    log("C1 dense-offset fields, full vs crop")
    fr = P / "trackR" / f"RUNW_{args.tag}_unw9x8.h5"
    cr = PA / "trackR" / f"RUNW_{args.tag}_unw9x8.h5"
    with h5py.File(rslc_crop[ref_d], "r") as h:
        zdt0 = h[f"{RSW}/zeroDopplerTime"][0]
        dz = h[f"{RSW}/zeroDopplerTimeSpacing"][()]
        sr0 = h[f"{RSW}/frequencyA/slantRange"][0]
        dsr = h[f"{RSW}/frequencyA/slantRangeSpacing"][()]
    with h5py.File(fr, "r") as hf, h5py.File(cr, "r") as hc:
        zf, sf = hf[f"{OFF}/zeroDopplerTime"][()], hf[f"{OFF}/slantRange"][()]
        zc, sc = hc[f"{OFF}/zeroDopplerTime"][()], hc[f"{OFF}/slantRange"][()]
        FF = {k: hf[f"{OFF}/HH/{k}"][()].astype(np.float64)
              for k in ("alongTrackOffset", "slantRangeOffset", "correlationSurfacePeak")}
        CC = {k: hc[f"{OFF}/HH/{k}"][()].astype(np.float64) for k in FF}
    ia = np.rint((zc - zf[0]) / (zf[1] - zf[0])).astype(int)
    fcol = (sc - sf[0]) / (sf[1] - sf[0])
    i0 = np.floor(fcol).astype(int)
    w = fcol - i0
    row_px = (zc - zdt0) / dz
    col_px = (sc - sr0) / dsr
    interior = (np.outer((row_px >= args.buffer) & (row_px <= L - args.buffer),
                         (col_px >= args.buffer) & (col_px <= W - args.buffer)))
    c1 = {"offset_grid_crop": list(CC["alongTrackOffset"].shape),
          "range_subsample_misalignment_steps": float(np.median(w)),
          "azimuth_index_exact": bool(np.allclose(zf[ia], zc))}
    for k in FF:
        full_at = FF[k][ia][:, i0] * (1 - w) + FF[k][ia][:, i0 + 1] * w
        m = interior & np.isfinite(full_at) & np.isfinite(CC[k]) & (CC[k] != 0) & (full_at != 0)
        raw = CC[k][m] - full_at[m]
        e = {"n": int(m.sum()), "full_field_median": float(np.median(full_at[m])),
             "full_field_p5_p95": [float(np.percentile(full_at[m], 5)), float(np.percentile(full_at[m], 95))],
             "raw_diff_median": float(np.median(raw)),
             "raw_diff_std": float(raw.std())}
        if k != "correlationSurfacePeak":
            shift = d_az if k == "alongTrackOffset" else d_rg
            for sign, lbl in ((+1, "crop_eq_full_minus_shift"), (-1, "crop_eq_full_plus_shift")):
                res = CC[k][m] - (full_at[m] - sign * shift)
                e[lbl] = {"median_px": float(np.median(res)), "std_px": float(res.std()),
                          "p95_abs_px": float(np.percentile(np.abs(res), 95))}
        c1[k] = e
    report["C1_dense_offsets"] = c1
    log(f"  along-track: {json.dumps(c1['alongTrackOffset'])}")
    log(f"  slant-range: {json.dumps(c1['slantRangeOffset'])}")

    # ---- C2: coregistered SLCs, radar domain ------------------------------
    log("C2 coregistered SLCs, full vs crop (radar domain, AOI interior)")
    b = args.buffer
    sl = (slice(b, L - b), slice(b, W - b))
    c2 = {}
    for name, rel in (("reference_slc_control", "crossmul/freqA/HH/reference.slc"),
                      ("coregistered_secondary_slc", "fine_resample_slc/freqA/HH/coregistered_secondary.slc")):
        a_c = read_window(SC / rel, 0, 0, W, L)[sl]
        a_f = read_window(SF / rel, RG0, AZ0, W, L)[sl]
        ok = (a_c != 0) & (a_f != 0)
        # Decimated by 2 for the phase statistics only (correspondence stays
        # exact); the bit-identical check and the shift use full resolution.
        # The ramp is in radar-pixel units here, not km -- not used for C2.
        e = phase_agreement(a_c[::2, ::2], a_f[::2, ::2], ok[::2, ::2], 0.001)
        amp_c, amp_f = np.abs(a_c), np.abs(a_f)
        rel_d = np.abs(amp_c[ok] - amp_f[ok]) / amp_f[ok]
        e.update({"bit_identical": bool(np.array_equal(a_c, a_f)),
                  "max_abs_complex_diff": float(np.abs(a_c - a_f).max()),
                  "amp_rel_diff_median": float(np.median(rel_d)),
                  "amp_rel_diff_p95": float(np.percentile(rel_d, 95)),
                  "subpixel_shift": tile_shifts(amp_f, amp_c, ok)})
        c2[name] = e
        log(f"  {name}: bit_identical={e['bit_identical']} phase_diff_coh="
            f"{e.get('phase_diff_coherence', float('nan')):.6f} shift={e['subpixel_shift']}")
        del a_c, a_f, amp_c, amp_f
    report["C2_coregistered_slc"] = c2

    # ---- lookup table: map lattice -> reference radar (crop frame) ---------
    lut_path = LAY / "lut_rowcol.tif"
    if args.force_lut or not lut_path.exists():
        log("building geolocation lookup (one warp, nearest neighbour)")
        idx = LAY / "_index_rowcol.tif"
        ds = gdal.GetDriverByName("GTiff").Create(str(idx), W, L, 2, gdal.GDT_Float32,
                                                  ["TILED=YES", "BIGTIFF=YES"])
        cols = np.arange(W, dtype=np.float32)
        for r0 in range(0, L, 1024):
            n = min(1024, L - r0)
            ds.GetRasterBand(1).WriteArray(
                np.repeat(np.arange(r0, r0 + n, dtype=np.float32)[:, None], W, axis=1), 0, r0)
            ds.GetRasterBand(2).WriteArray(np.broadcast_to(cols, (n, W)).copy(), 0, r0)
        ds = None
        vrt = LAY / "_index_rowcol_geoloc.vrt"
        v = gdal.Translate(str(vrt), str(idx), format="VRT")
        geo_wkt = ('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
                   'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]')
        v.SetMetadata({
            "X_DATASET": str((SC / "rdr2geo/freqA/x.rdr").resolve()), "X_BAND": "1",
            "Y_DATASET": str((SC / "rdr2geo/freqA/y.rdr").resolve()), "Y_BAND": "1",
            "PIXEL_OFFSET": "0", "LINE_OFFSET": "0", "PIXEL_STEP": "1", "LINE_STEP": "1",
            "GEOREFERENCING_CONVENTION": "PIXEL_CENTER", "SRS": geo_wkt}, "GEOLOCATION")
        v = None
        tw = time.time()
        gdal.Warp(str(lut_path), str(vrt), geoloc=True, dstSRS="EPSG:32645",
                  outputBounds=(X0, Y0, X1, Y1), xRes=PX, yRes=PX, resampleAlg="near",
                  dstNodata=-1, errorThreshold=0, multithread=True,
                  warpOptions=["NUM_THREADS=ALL_CPUS"], outputType=gdal.GDT_Float32,
                  creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "ZLEVEL=1", "BIGTIFF=IF_SAFER"])
        idx.unlink(missing_ok=True)
        log(f"  lookup warped in {time.time() - tw:.0f}s")
    lut = gdal.Open(str(lut_path))
    lr = lut.GetRasterBand(1).ReadAsArray()
    lc = lut.GetRasterBand(2).ReadAsArray()
    valid_lut = (lr >= 0) & (lc >= 0)
    ri, ci = lr.astype(np.int32), lc.astype(np.int32)
    del lr, lc
    if valid_lut.mean() < 0.5:
        raise SystemExit(f"lookup is {100 * valid_lut.mean():.1f}% valid -- the warp failed")

    # VERIFY the lookup PER PIXEL: the chosen radar pixel's rdr2geo lon/lat,
    # projected to UTM, against the map-pixel centre it was chosen for.
    #
    # The first run checked a 50k-pixel sample and found the centre of the
    # distribution exact (mean dx/dy ~0.07/0.02 m -> the PIXEL_CENTER convention
    # is right) but a long tail: p95 52 m, max 4.6 km. That tail is radar SHADOW
    # and LAYOVER in Himalayan relief. Shadowed ground has no radar sample at all,
    # and GDAL's backmap fills the hole with an interpolated index pointing at a
    # radar pixel that actually lies far away. Those pixels carry no information
    # about the ground they are assigned to, and left in they inject random phase
    # into every RSLC-vs-GSLC statistic. So every pixel is checked and anything
    # farther than --geoloc-tol from its map cell is excluded (the tolerance
    # leaves room for legitimate nearest-neighbour distance on foreshortened
    # slopes, where one radar pixel spans much more ground than 5 m).
    geo_ok_path = LAY / f"geoloc_ok_tol{args.geoloc_tol:g}m.tif"
    if geo_ok_path.exists() and not args.force_lut:
        geo_ok = read_tif(geo_ok_path).astype(bool)
        lut_check = json.loads((LAY / f"geoloc_ok_tol{args.geoloc_tol:g}m.json").read_text())
    else:
        from pyproj import Transformer
        tr = Transformer.from_crs(4326, 32645, always_xy=True)
        xr = np.fromfile(SC / "rdr2geo/freqA/x.rdr", dtype="<f8").reshape(L, W)
        yr = np.fromfile(SC / "rdr2geo/freqA/y.rdr", dtype="<f8").reshape(L, W)
        vr, vc = np.nonzero(valid_lut)
        err = np.empty(len(vr), np.float32)
        ex_sum = ey_sum = 0.0
        CH = 5_000_000
        for s in range(0, len(vr), CH):
            r_, c_ = vr[s:s + CH], vc[s:s + CH]
            rr_, cc_ = ri[r_, c_], ci[r_, c_]
            px_, py_ = tr.transform(xr[rr_, cc_], yr[rr_, cc_])
            ex = px_ - (X0 + (c_ + 0.5) * PX)
            ey = py_ - (Y1 - (r_ + 0.5) * PX)
            ex_sum += float(ex.sum()); ey_sum += float(ey.sum())
            err[s:s + CH] = np.hypot(ex, ey)
        del xr, yr
        ok = err <= args.geoloc_tol
        geo_ok = np.zeros((NY, NX), bool)
        geo_ok[vr[ok], vc[ok]] = True
        good = err[ok]
        lut_check = {
            "valid_fraction_of_lattice": float(valid_lut.mean()),
            "mean_dx_m_all": ex_sum / len(vr), "mean_dy_m_all": ey_sum / len(vr),
            "p50_abs_m_all": float(np.percentile(err, 50)),
            "p95_abs_m_all": float(np.percentile(err, 95)),
            "max_abs_m_all": float(err.max()),
            "tolerance_m": args.geoloc_tol,
            "excluded_fraction_of_valid": float(1 - ok.mean()),
            "excluded_fraction_of_aoi": float(((valid_lut & ~geo_ok) & aoi).sum() / aoi.sum()),
            "p50_abs_m_kept": float(np.percentile(good, 50)),
            "p95_abs_m_kept": float(np.percentile(good, 95)),
        }
        write_tif(geo_ok_path, geo_ok.astype(np.uint8), GT, WKT)
        (LAY / f"geoloc_ok_tol{args.geoloc_tol:g}m.json").write_text(json.dumps(lut_check, indent=2))
        del vr, vc, err, ok, good
    report["lookup_verification"] = lut_check
    log(f"  lookup check: {lut_check}")

    def geocode(src: np.ndarray, r_off: int = 0, c_off: int = 0) -> np.ndarray:
        out = np.zeros((NY, NX), dtype=src.dtype)
        out[valid_lut] = src[ri[valid_lut] + r_off, ci[valid_lut] + c_off]
        return out

    # ---- assemble map-domain layers (cached) -------------------------------
    def cached(name: str, build):
        p = LAY / f"{name}.tif"
        if p.exists():
            return read_tif(p)
        a = build()
        write_tif(p, a, GT, WKT)
        return a

    def rslc_ifg(h5: Path, full: bool):
        with h5py.File(h5, "r") as h:
            if full:
                src = h[IFGP][AZ0:AZ0 + L, RG0:RG0 + W]
            else:
                src = h[IFGP][()]
        return geocode(src.astype(np.complex64))

    log("assembling map-domain layers")
    ifg = {
        "R_full": cached("R_full_ifg", lambda: rslc_ifg(SF / "RIFG.h5", True)),
        "R_crop": cached("R_crop_ifg", lambda: rslc_ifg(SC / "RIFG.h5", False)),
    }
    gcf, grf, _ = lattice_offset(G_full_ifg, X0, Y1)
    gcc, grc, _ = lattice_offset(G_crop_ifg, X0, Y1)
    ifg["G_full"] = read_window(G_full_ifg, gcf, grf, NX, NY).astype(np.complex64)
    ifg["G_crop"] = read_window(G_crop_ifg, gcc, grc, NX, NY).astype(np.complex64)

    crop_coh_r = PA / "trackR" / "coherence_A_HH_win3.tif"
    if not crop_coh_r.exists():
        log("computing cropped RSLC 3x3 coherence (same estimator as the full tile)")
        subprocess.run([sys.executable, "-u", str(TOOLS / "slc_coherence.py"),
                        "--ref", str(SC / "crossmul/freqA/HH/reference.slc"),
                        "--sec", str(SC / "fine_resample_slc/freqA/HH/coregistered_secondary.slc"),
                        "--win", "3", "--out", str(crop_coh_r)], check=True)
    coh = {
        "R_full": cached("R_full_coh", lambda: geocode(
            read_window(P / "trackR" / "coherence_A_HH_win3.tif", RG0, AZ0, W, L).astype(np.float32))),
        "R_crop": cached("R_crop_coh", lambda: geocode(read_tif(crop_coh_r).astype(np.float32))),
        "G_full": read_window(P / "trackG" / "ifg_A_HH.coh.tif", gcf, grf, NX, NY).astype(np.float32),
        "G_crop": read_window(PA / "trackG" / "ifg_A_HH_1x1.coh.tif", gcc, grc, NX, NY).astype(np.float32),
    }
    for k in coh:
        coh[k] = np.nan_to_num(coh[k], nan=0.0)

    # Map-domain R_full vs R_crop must reproduce the radar-domain result.
    # ---- C3: GSLC per-date amplitude, full vs crop -------------------------
    log("C3 GSLC per-date amplitude, full vs crop")
    c3 = {}
    for d in (ref_d, sec_d):
        af = read_window(P / "trackG" / f"amp_A_HH_{d}.tif", gcf, grf, NX, NY)
        ac = read_window(PA / "trackG" / f"amp_A_HH_1x1_{d}.tif", gcc, grc, NX, NY)
        ok = aoi & (af > 0) & (ac > 0) & np.isfinite(af) & np.isfinite(ac)
        rel_d = np.abs(ac[ok] - af[ok]) / af[ok]
        c3[d] = {"n": int(ok.sum()), "amp_rel_diff_median": float(np.median(rel_d)),
                 "amp_rel_diff_p95": float(np.percentile(rel_d, 95)),
                 "subpixel_shift": tile_shifts(af, ac, ok)}
        log(f"  {d}: {c3[d]}")
    report["C3_gslc_amplitude"] = c3

    # ---- C4: cross-track geolocation ---------------------------------------
    log("C4 cross-track geolocation: geocoded RSLC amplitude vs GSLC amplitude")
    c4 = {}
    for d, rel in ((ref_d, "crossmul/freqA/HH/reference.slc"),
                   (sec_d, "fine_resample_slc/freqA/HH/coregistered_secondary.slc")):
        ra = geocode(np.abs(read_window(SC / rel, 0, 0, W, L)).astype(np.float32))
        ga = read_window(PA / "trackG" / f"amp_A_HH_1x1_{d}.tif", gcc, grc, NX, NY)
        ok = aoi & geo_ok & (ra > 0) & (ga > 0) & np.isfinite(ga)
        c4[d] = {"n": int(ok.sum()), "subpixel_shift_px": tile_shifts(ga, ra, ok),
                 "log_amp_pearson_r": float(np.corrcoef(np.log(ra[ok][::7]), np.log(ga[ok][::7]))[0, 1])}
        log(f"  {d}: {c4[d]}")
        del ra, ga
    report["C4_cross_track_geolocation"] = c4

    # ---- I1: wrapped phase, all pairs, 5 m and 40 m ------------------------
    log("I1 wrapped phase agreement, all pairs")
    common = aoi & valid_lut & geo_ok
    for k in LEGS:
        common &= (ifg[k] != 0)
    report["common_mask_fraction_of_aoi"] = float(common.sum() / aoi.sum())
    # Sensitivity: how much the shadow/layover exclusion changes the cross-track
    # answer. Reported so the mask cannot quietly flatter the result.
    nomask = aoi & valid_lut & (ifg["R_full"] != 0) & (ifg["G_full"] != 0)
    report["I1_sensitivity_R_full_vs_G_full_WITHOUT_geoloc_mask"] = {
        "5m": phase_agreement(ifg["R_full"], ifg["G_full"], nomask, PX / 1000),
        "40m": phase_agreement(multilook(ifg["R_full"], 8), multilook(ifg["G_full"], 8),
                               multilook(nomask.astype(np.float32), 8) >= 0.75, 8 * PX / 1000)}
    del nomask
    bench = coh["R_full"]
    bins = ((0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.01))
    i1 = {}
    # NOTE: loop names are p/q, not a/b -- `b` is the crop buffer further down.
    pairs = [(p, q) for i, p in enumerate(LEGS) for q in LEGS[i + 1:]]
    for p, q in pairs:
        key = f"{p}__vs__{q}"
        e5 = phase_agreement(ifg[p], ifg[q], common, PX / 1000)
        e5["by_benchmark_coherence"] = {
            f"{lo:.1f}-{min(hi, 1.0):.1f}": phase_agreement(
                ifg[p], ifg[q], common & (bench >= lo) & (bench < hi), PX / 1000).get("phase_diff_coherence")
            for lo, hi in bins}
        A8, B8 = multilook(ifg[p], 8), multilook(ifg[q], 8)
        m8 = multilook(common.astype(np.float32), 8) >= 0.75
        e40 = phase_agreement(A8, B8, m8, 8 * PX / 1000)
        bench8 = multilook(bench, 8)
        e40["by_benchmark_coherence"] = {
            f"{lo:.1f}-{min(hi, 1.0):.1f}": phase_agreement(
                A8, B8, m8 & (bench8 >= lo) & (bench8 < hi), 8 * PX / 1000).get("phase_diff_coherence")
            for lo, hi in bins}
        i1[key] = {"5m": e5, "40m": e40}
        log(f"  {key}: 5m R={e5.get('phase_diff_coherence', 0):.4f} "
            f"40m R={e40.get('phase_diff_coherence', 0):.4f}")
    report["I1_wrapped_phase"] = i1

    # ---- I2: coherence -----------------------------------------------------
    log("I2 coherence")
    cm = common.copy()
    for k in LEGS:
        cm &= coh[k] > 0
    i2 = {"distributions": {k: dist(coh[k][cm]) for k in LEGS}, "paired_vs_R_full": {}}
    for k in LEGS[1:]:
        dd = coh[k][cm] - coh["R_full"][cm]
        i2["paired_vs_R_full"][k] = {
            "median_diff": float(np.median(dd)), "std_diff": float(dd.std()),
            "p95_abs_diff": float(np.percentile(np.abs(dd), 95)),
            "pearson_r": float(np.corrcoef(coh[k][cm][::5], coh["R_full"][cm][::5])[0, 1])}
    report["I2_coherence"] = i2
    log(f"  {json.dumps(i2['paired_vs_R_full'])}")
    del ifg

    # ---- I3: ionosphere ----------------------------------------------------
    log("I3 ionosphere screens")
    with h5py.File(fr, "r") as h:
        io_f = h[IONO][()].astype(np.float32)
    with h5py.File(cr, "r") as h:
        io_c = h[IONO][()].astype(np.float32)
    ly, lx = 9, 8
    rf0, cf0 = AZ0 // ly, RG0 // lx
    ny9, nx8 = io_c.shape
    io_f_win = io_f[rf0:rf0 + ny9, cf0:cf0 + nx8]
    bl, bc = math.ceil(args.buffer / ly), math.ceil(args.buffer / lx)
    im = np.zeros(io_c.shape, bool)
    im[bl:ny9 - bl, bc:nx8 - bc] = True
    i3 = {"R_full_vs_R_crop_radar_9x8": screen_agreement(io_c, io_f_win, im),
          "R_sublook_misalignment": {"az_rows": AZ0 % ly, "rg_cols": RG0 % lx}}

    g_io_f = P / "trackG" / "ionosphere" / "dispersive_filtered.tif"
    g_io_c = PA / "trackG" / "ionosphere" / "dispersive_filtered.tif"
    gc_io = gdal.Open(str(g_io_c))
    ggt_c = gc_io.GetGeoTransform()
    ox, oy, _ = lattice_offset(g_io_f, ggt_c[0], ggt_c[3])
    gio_c = gc_io.GetRasterBand(1).ReadAsArray()
    gio_f = read_window(g_io_f, ox, oy, gc_io.RasterXSize, gc_io.RasterYSize)
    PX40 = ggt_c[1]
    c40 = ((np.arange(gc_io.RasterXSize) + 0.5) * PX40 + ggt_c[0])
    r40 = (ggt_c[3] - (np.arange(gc_io.RasterYSize) + 0.5) * PX40)
    m40 = np.outer((r40 >= Y0) & (r40 <= Y1), (c40 >= X0) & (c40 <= X1))
    i3["G_full_vs_G_crop_40m"] = screen_agreement(gio_c, gio_f, m40)

    # cross-track, on the 5 m AOI lattice (decimated 4 for the statistics)
    rows = ri[valid_lut]
    colsr = ci[valid_lut]
    scr = {}
    a = np.full((NY, NX), np.nan, np.float32)
    a[valid_lut] = io_c[np.minimum(rows // ly, ny9 - 1), np.minimum(colsr // lx, nx8 - 1)]
    scr["R_crop"] = a
    a = np.full((NY, NX), np.nan, np.float32)
    a[valid_lut] = io_f[np.minimum((rows + AZ0) // ly, io_f.shape[0] - 1),
                        np.minimum((colsr + RG0) // lx, io_f.shape[1] - 1)]
    scr["R_full"] = a
    yc = Y1 - (np.arange(NY) + 0.5) * PX
    xc = X0 + (np.arange(NX) + 0.5) * PX
    for lbl, arr, gt40 in (("G_crop", gio_c, ggt_c),
                           ("G_full", read_tif(g_io_f), gdal.Open(str(g_io_f)).GetGeoTransform())):
        rr = np.floor((gt40[3] - yc) / gt40[1]).astype(int)
        cc = np.floor((xc - gt40[0]) / gt40[1]).astype(int)
        scr[lbl] = arr[np.clip(rr, 0, arr.shape[0] - 1)][:, np.clip(cc, 0, arr.shape[1] - 1)]
    dm = (aoi & geo_ok)[::4, ::4]
    i3["cross_track_5m_lattice"] = {
        f"{p}__vs__{q}": screen_agreement(scr[p][::4, ::4], scr[q][::4, ::4], dm)
        for i, p in enumerate(LEGS) for q in LEGS[i + 1:]}
    i3["constants"] = {"rad_per_cycle_A": RAD_PER_CYCLE_A, "rad_per_cycle_B": RAD_PER_CYCLE_B,
                       "rad_per_joint_cycle": RAD_PER_JOINT_CYCLE, "rad2tecu": RAD2TECU}
    report["I3_ionosphere"] = i3
    log(f"  R full vs crop: {json.dumps(i3['R_full_vs_R_crop_radar_9x8'])}")
    log(f"  G full vs crop: {json.dumps(i3['G_full_vs_G_crop_40m'])}")

    (OUT / "comparison.json").write_text(json.dumps(report, indent=2))
    log(f"wrote {OUT / 'comparison.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
