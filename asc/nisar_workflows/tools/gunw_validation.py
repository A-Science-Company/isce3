#!/usr/bin/env python3
"""
Validate the four workflows against the NISAR L2 GUNW for the same pair, over the AOI.

    python -u tools/gunw_validation.py [--force]         # after compare_four_way.py v2

Read-only on every product. Writes <case>/comparison_v2/gunw_validation/gunw_validation.json and figures
<case>/comparison_v2/report/fig/f2x_*.webp with metadata in gunw_validation/figures_gunw.json.

GUNW layers used (frequency A, HH):
  wrappedInterferogram/HH/wrappedInterferogram, coherenceMagnitude      20 m, 6 az x 5 rg looks
  unwrappedInterferogram/HH/unwrappedPhase, coherenceMagnitude,
      connectedComponents, ionospherePhaseScreen(+Uncertainty)          80 m, 16 az x 13 rg looks
Our layers are aggregated onto the GUNW cells by cell centre: 16 of our 5 m cells per 20 m cell, 256 per
80 m cell, 4 GSLC 40 m cells per 80 m cell. A GUNW cell is used when >= 75% of its 5 m cells lie in the
comparison mask (AOI polygon, geolocation residual <= 15 m, all four interferograms non-zero) and the GUNW value
is finite. Complex quantities are averaged as complex numbers; unwrapped phase and screens as real means.
"""

from __future__ import annotations

import glob
import importlib.util
import io
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

_ARGV = sys.argv[1:]
sys.argv = [sys.argv[0]]

import h5py  # noqa: E402
from osgeo import gdal  # noqa: E402

gdal.UseExceptions()
TOOLS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cfw", TOOLS / "compare_four_way.py")
cfw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfw)

C = Path("/home/sharath/isce3/case_studies/nepal_glof")
PAIR, TAG = "20260714_20260726", "20260714_20260726_A_HH_1x1"
CMP = C / "comparison_v2"
OUT = CMP / "gunw_validation"
FIG = CMP / "report" / "fig"
LAY = CMP / "layers"
P, PV = C / "pairs" / PAIR, C / "aoi_v2" / "pairs" / PAIR
GUNW = Path(glob.glob(str(C / "L2_GUNW" / "NISAR_L2_*GUNW*_20260714T*_20260726T*.h5"))[0])
GA = "/science/LSAR/GUNW/grids/frequencyA"
RA = cfw.RUNW_A
T0 = time.time()
MM_PER_RAD = 299792458.0 / 1.239e9 / (4 * math.pi) * 1000
FIGMETA: dict = {"figures": {}, "charts": {}}


def log(m):
    print(f"[{time.time() - T0:6.1f}s] {m}", flush=True)


def hist(v, lo, hi, bins):
    h, e = np.histogram(v, bins=bins, range=(lo, hi), density=True)
    return {"centers": [round(float(c), 4) for c in (e[:-1] + e[1:]) / 2], "density": [round(float(x), 5) for x in h]}


def webp(name, rgba, **info):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "WEBP", quality=80, method=6)
    (FIG / f"{name}.webp").write_bytes(buf.getvalue())
    FIGMETA["figures"][name] = {"file": f"fig/{name}.webp", "px": [rgba.shape[1], rgba.shape[0]], "kb": round(len(buf.getvalue()) / 1024), **info}
    log(f"  figure {name} {rgba.shape[1]}x{rgba.shape[0]}")


def colorize(values, lo, hi, mask, cmap):
    x = np.clip((np.nan_to_num(values) - lo) / (hi - lo), 0, 1)
    rgba = (cmap(x) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(mask, 255, 0)
    return rgba


def main() -> int:
    from matplotlib import colormaps
    from matplotlib.colors import LinearSegmentedColormap, to_hex
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "gunw_validation.json").exists() and "--force" not in _ARGV:
        raise SystemExit("gunw_validation.json exists; pass --force")
    DIV = LinearSegmentedColormap.from_list("div", ["#1c5cab", "#6da7ec", "#f0efec", "#ee8a89", "#b8302f"])
    COH = LinearSegmentedColormap.from_list("coh", ["#f2f5f9", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
    PHASE = colormaps["twilight"]
    stops = lambda cm, n=11: [to_hex(cm(i / (n - 1))) for i in range(n)]

    rep = json.loads((CMP / "comparison.json").read_text())
    X0, Y1 = rep["lattice"]["origin"]
    PX = rep["lattice"]["px_m"]
    NY, NX = rep["lattice"]["shape"]
    AZ0, RG0 = rep["crop_geometry"]["reference_A"][:2]
    out = {"tool": "gunw_validation v1", "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "gunw": GUNW.name}

    # ------------------------------------------------------------ GUNW metadata
    with h5py.File(GUNW, "r") as h:
        def sv(p):
            x = h[p][()]
            return x.decode() if isinstance(x, bytes) else (x.tolist() if isinstance(x, np.ndarray) else x)
        PI_ = "/science/LSAR/GUNW/metadata/processingInformation"
        out["gunw_metadata"] = {
            "software_version": sv(f"{PI_}/algorithms/softwareVersion"),
            "reference_rslc": sv(f"{PI_}/inputs/l1ReferenceSlcGranules"), "secondary_rslc": sv(f"{PI_}/inputs/l1SecondarySlcGranules"),
            "ionosphere_algorithm": sv(f"{PI_}/algorithms/ionosphereEstimation/ionosphereAlgorithm"),
            "ionosphere_unwrapping_error_correction": sv(f"{PI_}/algorithms/ionosphereEstimation/unwrappingErrorCorrection"),
            "wrapped_looks_az_rg": [sv(f"{PI_}/parameters/wrappedInterferogram/frequencyA/numberOfAzimuthLooks"), sv(f"{PI_}/parameters/wrappedInterferogram/frequencyA/numberOfRangeLooks")],
            "unwrapped_looks_az_rg": [sv(f"{PI_}/parameters/unwrappedInterferogram/frequencyA/numberOfAzimuthLooks"), sv(f"{PI_}/parameters/unwrappedInterferogram/frequencyA/numberOfRangeLooks")],
            "geocoding_corrections_applied": {k: sv(f"{PI_}/parameters/geocoding/{k}") for k in ("azimuthIonosphericCorrectionApplied", "rangeIonosphericCorrectionApplied",
                                                                                                   "hydrostaticTroposphericCorrectionApplied", "wetTroposphericCorrectionApplied")},
            "dem_source": sv(f"{PI_}/inputs/demSource")[:120],
            "product_version": sv("/science/LSAR/identification/productVersion"),
            "processing_datetime": sv("/science/LSAR/identification/processingDateTime")}
        grids = {}
        for g in ("wrappedInterferogram", "unwrappedInterferogram"):
            x = h[f"{GA}/{g}/xCoordinates"][()]; y = h[f"{GA}/{g}/yCoordinates"][()]
            dx = float(h[f"{GA}/{g}/xCoordinateSpacing"][()]); dy = float(h[f"{GA}/{g}/yCoordinateSpacing"][()])
            grids[g] = {"x_edge0": float(x[0] - dx / 2), "y_edge0": float(y[0] - dy / 2), "dx": dx, "dy": dy, "nx": len(x), "ny": len(y)}
    out["gunw_grids"] = grids
    log(f"GUNW {out['gunw_metadata']['software_version']}: grids {grids}")

    # ------------------------------------------------------------ our lattice layers
    aoi = cfw.read_tif(CMP / "aoi_polygon_mask.tif").astype(bool)
    lut = LAY / "lut_fullframe_rowcol.tif"
    RI, CI, RES = cfw.read_tif(lut, 1), cfw.read_tif(lut, 2), cfw.read_tif(lut, 3)
    valid = (RI >= 0) & (CI >= 0)
    geo_ok = valid & (RES <= 15.0)
    RI, CI = RI.astype(np.int32), CI.astype(np.int32)
    del RES
    gcf, grf = cfw.lattice_offset(P / "trackG" / "ifg_A_HH.igram.tif", X0, Y1)
    gcc, grc = cfw.lattice_offset(PV / "trackG" / "ifg_A_HH_1x1.igram.tif", X0, Y1)
    ifg = {"R_full": cfw.read_tif(LAY / "R_full_ifg.tif"), "R_crop": cfw.read_tif(LAY / "R_crop_ifg.tif"),
           "G_full": cfw.c64(cfw.read_window(P / "trackG" / "ifg_A_HH.igram.tif", gcf, grf, NX, NY)),
           "G_crop": cfw.c64(cfw.read_window(PV / "trackG" / "ifg_A_HH_1x1.igram.tif", gcc, grc, NX, NY))}
    common = aoi & geo_ok
    for v in ifg.values():
        common &= v != 0

    def binning(g):
        gr = grids[g]
        cx = X0 + PX * (np.arange(NX) + 0.5)
        cy = Y1 - PX * (np.arange(NY) + 0.5)
        ci = np.floor((cx - gr["x_edge0"]) / gr["dx"]).astype(np.int64)
        ri = np.floor((cy - gr["y_edge0"]) / gr["dy"]).astype(np.int64)
        c_lo, c_hi, r_lo, r_hi = int(ci.min()), int(ci.max()) + 1, int(ri.min()), int(ri.max()) + 1
        nx_, ny_ = c_hi - c_lo, r_hi - r_lo
        ids = ((ri - r_lo)[:, None] * nx_ + (ci - c_lo)[None, :])
        return {"ids": ids, "win": (r_lo, r_hi, c_lo, c_hi), "shape": (ny_, nx_), "cells_per_axis": gr["dx"] / PX}

    def agg(b, arr, mask, complex_=False):
        n = b["shape"][0] * b["shape"][1]
        ids = b["ids"].ravel()
        m = mask.ravel()
        cnt = np.bincount(ids, m.astype(np.float64), n)
        if complex_:
            re = np.bincount(ids, np.where(m, arr.real.ravel(), 0), n)
            im = np.bincount(ids, np.where(m, arr.imag.ravel(), 0), n)
            val = (re + 1j * im) / np.maximum(cnt, 1)
        else:
            val = np.bincount(ids, np.where(m, arr.ravel(), 0).astype(np.float64), n) / np.maximum(cnt, 1)
        return val.reshape(b["shape"]), cnt.reshape(b["shape"])

    B20, B80 = binning("wrappedInterferogram"), binning("unwrappedInterferogram")
    full20 = (B20["cells_per_axis"]) ** 2
    full80 = (B80["cells_per_axis"]) ** 2
    with h5py.File(GUNW, "r") as h:
        r0, r1, c0, c1 = B20["win"]
        gw = h[f"{GA}/wrappedInterferogram/HH/wrappedInterferogram"][r0:r1, c0:c1]
        gwc = h[f"{GA}/wrappedInterferogram/HH/coherenceMagnitude"][r0:r1, c0:c1].astype(np.float64)
        r0, r1, c0, c1 = B80["win"]
        gu = h[f"{GA}/unwrappedInterferogram/HH/unwrappedPhase"][r0:r1, c0:c1].astype(np.float64)
        guc = h[f"{GA}/unwrappedInterferogram/HH/coherenceMagnitude"][r0:r1, c0:c1].astype(np.float64)
        gcc_ = h[f"{GA}/unwrappedInterferogram/HH/connectedComponents"][r0:r1, c0:c1]
        gio = h[f"{GA}/unwrappedInterferogram/HH/ionospherePhaseScreen"][r0:r1, c0:c1].astype(np.float64)
        giu = h[f"{GA}/unwrappedInterferogram/HH/ionospherePhaseScreenUncertainty"][r0:r1, c0:c1].astype(np.float64)
    out["aggregation"] = {"cells_5m_per_20m_cell": full20, "cells_5m_per_80m_cell": full80,
                          "gunw_windows_rows_cols": {"20m": list(B20["win"]), "80m": list(B80["win"])}}

    # ------------------------------------------------------------ wrapped phase at 20 m and 80 m
    log("wrapped interferogram")
    _, cnt20 = agg(B20, np.ones((NY, NX), np.float32), common)
    m20 = (cnt20 >= 0.75 * full20) & np.isfinite(gw) & (gw != 0)
    ours20 = {k: agg(B20, v, common, True)[0] for k, v in ifg.items()}
    r_coh = cfw.read_tif(LAY / "R_full_coh_flat.tif")
    rcoh20 = agg(B20, r_coh, common)[0]
    bins = ((0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.01))
    w = {}
    gw_c = np.nan_to_num(gw).astype(np.complex128)
    for k, z in ours20.items():
        e = cfw.phase_agreement(z, gw_c, m20, 0.02, quadrants=False)
        e["by_R_full_coherence"] = {f"{lo:.1f}-{min(hi, 1.0):.1f}": cfw.phase_agreement(z, gw_c, m20 & (rcoh20 >= lo) & (rcoh20 < hi), 0.02,
                                                                                         quadrants=False, plane=False).get("phase_diff_coherence") for lo, hi in bins}
        conj = cfw.phase_agreement(z, np.conj(gw_c), m20, 0.02, quadrants=False, plane=False)
        w[f"{k}__vs__GUNW_20m"] = {kk: e.get(kk) for kk in ("n", "phase_diff_coherence", "constant_offset_rad", "circular_std_rad", "planar_rad_per_km_x_east",
                                                           "planar_rad_per_km_y_south", "phase_diff_coherence_after_planar", "by_R_full_coherence")}
        w[f"{k}__vs__GUNW_20m"]["sign_check_vs_conjugate_R"] = conj.get("phase_diff_coherence")
        log(f"  {k} vs GUNW 20 m: R {e['phase_diff_coherence']:.4f} offset {e['constant_offset_rad']:+.3f}; (conj {conj.get('phase_diff_coherence'):.3f})")
    # 80 m: multilook both 4x4 on the 20 m grid
    k4 = 4
    ny4, nx4 = (m20.shape[0] // k4) * k4, (m20.shape[1] // k4) * k4
    ml = lambda a: a[:ny4, :nx4].reshape(ny4 // k4, k4, nx4 // k4, k4).mean(axis=(1, 3))
    g80w = ml(np.where(m20, gw_c, 0)); m80w = ml(m20.astype(float)) >= 0.75
    for k, z in ours20.items():
        e = cfw.phase_agreement(ml(np.where(m20, z, 0)), g80w, m80w, 0.08, quadrants=False, plane=False)
        w[f"{k}__vs__GUNW_80m_from_20m"] = {kk: e.get(kk) for kk in ("n", "phase_diff_coherence", "constant_offset_rad", "circular_std_rad")}
    out["wrapped"] = w
    FIGMETA["charts"]["wrapped_dphase_hist_20m"] = {}
    for k in ("R_full", "G_full"):
        u = ours20[k][m20] * np.conj(gw_c[m20])
        u = u[np.abs(u) > 0]
        u = u / np.abs(u)
        FIGMETA["charts"]["wrapped_dphase_hist_20m"][k] = hist(np.angle(u * np.exp(-1j * np.angle(u.mean()))), -math.pi, math.pi, 90)
    # display at 40 m (2x2 complex means of the 20 m cells), like the other report maps
    ny2, nx2 = (m20.shape[0] // 2) * 2, (m20.shape[1] // 2) * 2
    d2 = lambda a: a[:ny2, :nx2].reshape(ny2 // 2, 2, nx2 // 2, 2).mean(axis=(1, 3))
    m40 = d2(m20.astype(float)) >= 0.75
    g40 = d2(np.where(m20, gw_c, 0))
    ext20 = [m40.shape[1] * 0.04, m40.shape[0] * 0.04]
    webp("f20_gunw_wrapped_phase", colorize(np.angle(g40), -math.pi, math.pi, m40, PHASE), extent_km=ext20, cmap_stops=stops(PHASE, 17), vrange=[-math.pi, math.pi])
    webp("f20_R_full_minus_gunw_wrapped", colorize(np.angle(d2(np.where(m20, ours20["R_full"], 0)) * np.conj(g40)), -math.pi, math.pi, m40, DIV), extent_km=ext20,
         cmap_stops=stops(DIV), vrange=[-math.pi, math.pi])
    webp("f20_G_full_minus_gunw_wrapped", colorize(np.angle(d2(np.where(m20, ours20["G_full"], 0)) * np.conj(g40)), -math.pi, math.pi, m40, DIV), extent_km=ext20,
         cmap_stops=stops(DIV), vrange=[-math.pi, math.pi])

    # coherence (different estimators; report relation, not equality)
    g_coh = cfw.f32(cfw.read_window(P / "trackG" / "ifg_A_HH.coh.tif", gcf, grf, NX, NY))
    gcoh20 = agg(B20, g_coh, common)[0]
    mc = m20 & np.isfinite(gwc) & (gwc > 0)
    out["coherence_20m"] = {"GUNW_20m_6x5looks_median": float(np.median(gwc[mc])), "R_full_3x3_mean_over_cell_median": float(np.median(rcoh20[mc])),
                            "G_full_3x3_mean_over_cell_median": float(np.median(gcoh20[mc])),
                            "pearson_R_full_vs_GUNW": float(np.corrcoef(rcoh20[mc], gwc[mc])[0, 1]),
                            "pearson_G_full_vs_GUNW": float(np.corrcoef(gcoh20[mc], gwc[mc])[0, 1]),
                            "median_ratio_GUNW_over_R_full": float(np.median(gwc[mc] / np.maximum(rcoh20[mc], 1e-6))),
                            "median_ratio_GUNW_over_G_full": float(np.median(gwc[mc] / np.maximum(gcoh20[mc], 1e-6))),
                            "note": "GUNW coherence uses a 6x5-look estimator (lower bias); ours are 3x3 estimates averaged over the cell"}
    FIGMETA["charts"]["coherence_hist_20m"] = {"GUNW": hist(gwc[mc], 0, 1, 50), "R_full": hist(rcoh20[mc], 0, 1, 50), "G_full": hist(gcoh20[mc], 0, 1, 50)}
    webp("f21_gunw_coherence_20m", colorize(d2(np.where(mc, gwc, 0)) / np.maximum(d2(mc.astype(float)), 1e-6), 0, 1, d2(mc.astype(float)) >= 0.75, COH),
         extent_km=ext20, cmap_stops=stops(COH), vrange=[0, 1])
    log(f"  coherence: {out['coherence_20m']}")
    del ours20, gw, gw_c

    # ------------------------------------------------------------ unwrapped phase at 80 m
    log("unwrapped phase (80 m)")
    _, cnt80 = agg(B80, np.ones((NY, NX), np.float32), common)
    m80 = (cnt80 >= 0.75 * full80) & np.isfinite(gu) & (gu != 0)
    with h5py.File(P / "trackR" / f"RUNW_{TAG}_unw9x8.h5", "r") as hf, h5py.File(PV / "trackR" / f"RUNW_{TAG}_unw9x8.h5", "r") as hc:
        uf, sf = hf[f"{RA}/HH/unwrappedPhase"], hf[f"{RA}/HH/ionospherePhaseScreen"]
        rr9, cc9 = RI[valid] // 9, CI[valid] // 8
        rlo, rhi, clo, chi = int(rr9.min()), int(rr9.max()) + 1, int(cc9.min()), int(cc9.max()) + 1
        uf_w = uf[rlo:rhi, clo:chi].astype(np.float32); sf_w = sf[rlo:rhi, clo:chi].astype(np.float32)
        uc_all = hc[f"{RA}/HH/unwrappedPhase"][()].astype(np.float32); sc_all = hc[f"{RA}/HH/ionospherePhaseScreen"][()].astype(np.float32)
    lat = {}
    for name, arr, ro, co in (("R_full_unw", uf_w, rlo, clo), ("R_full_iono", sf_w, rlo, clo)):
        a = np.zeros((NY, NX), np.float32)
        a[valid] = arr[RI[valid] // 9 - ro, CI[valid] // 8 - co]
        lat[name] = a
    for name, arr in (("R_crop_unw", uc_all), ("R_crop_iono", sc_all)):
        a = np.zeros((NY, NX), np.float32)
        rr = np.clip((RI[valid] - AZ0) // 9, 0, arr.shape[0] - 1); cc = np.clip((CI[valid] - RG0) // 8, 0, arr.shape[1] - 1)
        a[valid] = arr[rr, cc]
        lat[name] = a
    yc = Y1 - PX * (np.arange(NY) + 0.5); xc = X0 + PX * (np.arange(NX) + 0.5)
    for leg, root in (("G_full", P), ("G_crop", PV)):
        for layer, fname in (("unw", "unw_A.tif"), ("iono", "dispersive_filtered.tif")):
            ds = gdal.Open(str(root / "trackG" / "ionosphere" / fname)); gt = ds.GetGeoTransform()
            ri = np.floor((gt[3] - yc) / abs(gt[5])).astype(int); ci = np.floor((xc - gt[0]) / gt[1]).astype(int)
            r_lo, r_hi = max(int(ri.min()), 0), min(int(ri.max()) + 1, ds.RasterYSize); c_lo, c_hi = max(int(ci.min()), 0), min(int(ci.max()) + 1, ds.RasterXSize)
            sub = ds.GetRasterBand(1).ReadAsArray(c_lo, r_lo, c_hi - c_lo, r_hi - r_lo).astype(np.float32)
            lat[f"{leg}_{layer}"] = sub[np.clip(ri - r_lo, 0, sub.shape[0] - 1)][:, np.clip(ci - c_lo, 0, sub.shape[1] - 1)]
    unw = {}
    for leg in ("R_full", "R_crop", "G_full", "G_crop"):
        a = lat[f"{leg}_unw"]
        mk = common & np.isfinite(a) & (a != 0)
        v80, c80 = agg(B80, a, mk)
        mm = m80 & (c80 >= 0.75 * full80)
        d = (v80 - gu)[mm] / (2 * math.pi)
        whole = np.round(d)
        vals, counts = np.unique(whole, return_counts=True)
        mode = vals[np.argmax(counts)]
        rem = d - mode
        unw[f"{leg}__vs__GUNW"] = {"n": int(mm.sum()), "modal_whole_cycles": int(mode), "fraction_at_mode": float(counts.max() / mm.sum()),
                                   "residual_after_mode_median_rad": float(np.median(rem) * 2 * math.pi),
                                   "residual_after_mode_mad_rad": float(np.median(np.abs(rem - np.median(rem))) * 2 * math.pi),
                                   "fraction_within_half_cycle_of_mode": float((np.abs(rem) < 0.5).mean()),
                                   "residual_std_within_half_cycle_rad": float(np.std(rem[np.abs(rem) < 0.5]) * 2 * math.pi)}
        if leg == "R_full":
            dmap = np.full(gu.shape, np.nan); dmap[mm] = (v80 - gu)[mm] - 2 * math.pi * mode
            FIGMETA["charts"]["unwrapped_diff_hist_R_full"] = hist(np.clip(dmap[mm], -3 * math.pi, 3 * math.pi), -3 * math.pi, 3 * math.pi, 120)
        log(f"  {leg}: mode {int(mode)} cycles on {counts.max() / mm.sum():.3f}; residual MAD {unw[f'{leg}__vs__GUNW']['residual_after_mode_mad_rad']:.3f} rad")
    comps, ccount = np.unique(gcc_[m80], return_counts=True)
    unw["GUNW_connected_components_in_AOI"] = {"n_components": int((comps > 0).sum()), "fraction_labelled": float(ccount[comps > 0].sum() / m80.sum()),
                                               "largest_component_fraction": float(ccount[comps > 0].max() / m80.sum()) if (comps > 0).any() else 0.0}
    out["unwrapped_80m"] = unw
    ext80 = [gu.shape[1] * 0.08, gu.shape[0] * 0.08]
    lim = float(np.nanpercentile(np.abs(dmap), 98))
    webp("f22_R_full_minus_gunw_unwrapped", colorize(dmap, -max(lim, 1.0), max(lim, 1.0), np.isfinite(dmap), DIV), extent_km=ext80,
         cmap_stops=stops(DIV), vrange=[-max(lim, 1.0), max(lim, 1.0)], units="rad after removing the modal whole-cycle offset")

    # ------------------------------------------------------------ ionosphere at 80 m
    log("ionosphere (80 m)")
    RAD2TECU, RB, RJ = cfw.RAD2TECU, cfw.RAD_PER_CYCLE_B, cfw.RAD_PER_JOINT_CYCLE
    io = {"GUNW_screen_median_rad": float(np.nanmedian(gio[m80 & np.isfinite(gio) & (gio != 0)])),
          "GUNW_screen_median_tecu": float(np.nanmedian(gio[m80 & np.isfinite(gio) & (gio != 0)]) * RAD2TECU),
          "GUNW_uncertainty_median_rad": float(np.nanmedian(giu[m80 & np.isfinite(giu)])),
          "note": "GUNW algorithm main_diff_ms_band with unwrapping-error correction; ours main_side_band (R) and the ported GSLC solve (G)"}
    anoms = {}
    mg = m80 & np.isfinite(gio) & (gio != 0)
    for leg in ("R_full", "R_crop", "G_full", "G_crop"):
        a = lat[f"{leg}_iono"]
        mk = common & np.isfinite(a) & (a != 0)
        v80, c80 = agg(B80, a, mk)
        mm = mg & (c80 >= 0.75 * full80)
        d = (v80 - gio)[mm]
        off = float(np.median(d))
        io[f"{leg}__vs__GUNW"] = {"n": int(mm.sum()), "offset_rad": off, "offset_tecu": off * RAD2TECU, "offset_in_B_cycles": off / RB,
                                  "offset_in_joint_cycles": off / RJ, "residual_std_rad": float((d - off).std()),
                                  "residual_std_tecu": float((d - off).std() * RAD2TECU), "residual_std_mm": float((d - off).std() * MM_PER_RAD),
                                  "pearson_r": float(np.corrcoef(v80[mm], gio[mm])[0, 1])}
        anoms[leg] = np.where(mm, v80 - gio - off, np.nan)
        log(f"  {leg}: offset {off:+.3f} rad ({off * RAD2TECU:+.3f} TECU, {off / RB:+.3f} B cycles, {off / RJ:+.3f} joint), "
            f"residual {io[f'{leg}__vs__GUNW']['residual_std_tecu']:.4f} TECU, r {io[f'{leg}__vs__GUNW']['pearson_r']:.4f}")
    out["ionosphere_80m"] = io
    med = float(np.median(gio[mg]))
    lim_i = float(np.percentile(np.abs(gio[mg] - med), 99))
    webp("f23_gunw_iono_anomaly", colorize((gio - med) * RAD2TECU, -lim_i * RAD2TECU, lim_i * RAD2TECU, mg, DIV), extent_km=ext80,
         cmap_stops=stops(DIV), vrange=[-lim_i * RAD2TECU, lim_i * RAD2TECU], median_tecu=med * RAD2TECU, units="TECU about median")
    webp("f23_R_full_minus_gunw_iono", colorize(anoms["R_full"] * RAD2TECU, -0.05, 0.05, np.isfinite(anoms["R_full"]), DIV), extent_km=ext80,
         cmap_stops=stops(DIV), vrange=[-0.05, 0.05], units="TECU after removing the median offset")
    webp("f23_G_full_minus_gunw_iono", colorize(anoms["G_full"] * RAD2TECU, -0.05, 0.05, np.isfinite(anoms["G_full"]), DIV), extent_km=ext80,
         cmap_stops=stops(DIV), vrange=[-0.05, 0.05], units="TECU after removing the median offset")

    (OUT / "gunw_validation.json").write_text(json.dumps(out, indent=2, default=str))
    (OUT / "figures_gunw.json").write_text(json.dumps(FIGMETA, indent=1))
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
