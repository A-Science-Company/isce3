#!/usr/bin/env python3
"""
Figures and chart data for the four-way comparison report (v2).

    python -u tools/report_figures.py          # after tools/compare_four_way.py (v2)

Reads <case>/comparison_v2/ (comparison.json, aoi mask, cached layers) and the four
workflows' products; writes <case>/comparison_v2/report/fig/*.webp and
<case>/comparison_v2/report/figures.json. Read-only on every product.

Rules the figures follow
------------------------
* Map panels carry NO baked-in text: titles, colourbars, scale bars and outlines are
  drawn by the HTML from figures.json, so they follow the viewer's theme.
* Cells outside a panel's mask are transparent.
* Wrapped phase uses a CYCLIC colormap ('twilight'); differences, ionosphere and
  residual fields a diverging blue-grey-red ramp centred on 0; coherence a
  single-hue sequential ramp (more coherent = darker).
* 5 m products are shown as 8x8 complex (or masked real) averages = 40 m, matching
  the 40 m statistics; the glacier-zone zoom shows true 5 m cells.
* Sign convention matches comparison.json: "P minus Q" with P the benchmark-side leg.
"""

from __future__ import annotations

import importlib.util
import io
import json
import math
import sys
from pathlib import Path

import numpy as np

_ARGV = sys.argv[1:]
sys.argv = [sys.argv[0]]

import h5py  # noqa: E402
from matplotlib import colormaps  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, to_hex  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402
from PIL import Image  # noqa: E402

gdal.UseExceptions()
ogr.UseExceptions()

TOOLS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cfw", TOOLS / "compare_four_way.py")
cfw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfw)

C = Path("/home/sharath/isce3/case_studies/nepal_glof")
PAIR, TAG = "20260714_20260726", "20260714_20260726_A_HH_1x1"
CMP = C / "comparison_v2"
OUT = CMP / "report"
FIG = OUT / "fig"
FIG.mkdir(parents=True, exist_ok=True)
LAY = CMP / "layers"
P, PV = C / "pairs" / PAIR, C / "aoi_v2" / "pairs" / PAIR
AOI_KML = Path("/home/sharath/asf_slc/glof_exact_aoi.kml")
GLACIER_KML = Path("/home/sharath/nisar_downloader/nepal_glacier_zone.kml")
K, WEBP_Q = 8, 76
IDENT = "/science/LSAR/identification"

DIVERGING = LinearSegmentedColormap.from_list("div", ["#1c5cab", "#6da7ec", "#f0efec", "#ee8a89", "#b8302f"])
COHERENCE = LinearSegmentedColormap.from_list("coh", ["#f2f5f9", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
PHASE, GREY = colormaps["twilight"], colormaps["gray"]
meta: dict = {"figures": {}, "charts": {}}


def log(m):
    print(m, flush=True)


def stops(cmap, n=11):
    return [to_hex(cmap(i / (n - 1))) for i in range(n)]


def save(name, rgba, **info):
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "WEBP", quality=WEBP_Q, method=6)
    (FIG / f"{name}.webp").write_bytes(buf.getvalue())
    meta["figures"][name] = {"file": f"fig/{name}.webp", "px": [rgba.shape[1], rgba.shape[0]],
                             "kb": round(len(buf.getvalue()) / 1024), **info}
    log(f"  {name}: {rgba.shape[1]}x{rgba.shape[0]} {len(buf.getvalue()) / 1024:.0f} KB")


def colorize(values, cmap, vmin, vmax, mask):
    x = np.clip((np.nan_to_num(values) - vmin) / (vmax - vmin), 0, 1)
    rgba = (cmap(x) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(mask, 255, 0)
    return rgba


def ml_c(a):
    return cfw.multilook(np.nan_to_num(a.astype(np.complex64)), K)


def ml_masked(a, m):
    num = cfw.multilook(np.where(m, np.nan_to_num(a), 0).astype(np.float32), K)
    den = cfw.multilook(m.astype(np.float32), K)
    return np.where(den > 0, num / np.maximum(den, 1e-6), np.nan), den


def poly_fraction_from_wkt(wkts, x0, y1, w_m, h_m):
    s = osr.SpatialReference(); s.ImportFromEPSG(4326); s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    t = osr.SpatialReference(); t.ImportFromEPSG(32645); t.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(s, t)
    rings = []
    for w in wkts:
        g = ogr.CreateGeometryFromWkt(w)
        g.FlattenTo2D(); g.Transform(tr)
        r = g.GetGeometryRef(0)
        rings.append([[round((r.GetX(i) - x0) / w_m, 5), round((y1 - r.GetY(i)) / h_m, 5)] for i in range(r.GetPointCount())])
    return rings


def kml_wkts(kml):
    ds = ogr.Open(str(kml))
    return [f.GetGeometryRef().ExportToWkt() for f in ds.GetLayer(0)]


def granule_polygon(path):
    with h5py.File(path, "r") as h:
        v = h[f"{IDENT}/boundingPolygon"][()]
    return v.decode() if isinstance(v, bytes) else str(v)


def hist(values, lo, hi, bins):
    h, e = np.histogram(values, bins=bins, range=(lo, hi), density=True)
    return {"centers": [round(float(c), 4) for c in (e[:-1] + e[1:]) / 2], "density": [round(float(x), 5) for x in h]}


def main() -> int:
    rep = json.loads((CMP / "comparison.json").read_text())
    X0, Y1 = rep["lattice"]["origin"]
    PX = rep["lattice"]["px_m"]
    NY, NX = rep["lattice"]["shape"]
    W_M, H_M = NX * PX, NY * PX
    ext = [W_M / 1000, H_M / 1000]
    AZ0, RG0 = rep["crop_geometry"]["reference_A"][:2]
    meta["lattice"] = {"origin": [X0, Y1], "px_m": PX, "shape": [NY, NX], "extent_km": ext}

    log("loading layers")
    aoi = cfw.read_tif(CMP / "aoi_polygon_mask.tif").astype(bool)
    lut = LAY / "lut_fullframe_rowcol.tif"
    RI, CI, RES = cfw.read_tif(lut, 1), cfw.read_tif(lut, 2), cfw.read_tif(lut, 3)
    valid = (RI >= 0) & (CI >= 0)
    geo_ok = valid & (RES <= 15.0)
    RI, CI = RI.astype(np.int32), CI.astype(np.int32)
    Gf, Gc = P / "trackG" / "ifg_A_HH.igram.tif", PV / "trackG" / "ifg_A_HH_1x1.igram.tif"
    gcf, grf = cfw.lattice_offset(Gf, X0, Y1)
    gcc, grc = cfw.lattice_offset(Gc, X0, Y1)
    ifg = {"R_full": cfw.read_tif(LAY / "R_full_ifg.tif"), "R_crop": cfw.read_tif(LAY / "R_crop_ifg.tif"),
           "G_full": cfw.c64(cfw.read_window(Gf, gcf, grf, NX, NY)),
           "G_crop": cfw.c64(cfw.read_window(Gc, gcc, grc, NX, NY))}
    common = aoi & geo_ok
    for v in ifg.values():
        common &= v != 0
    common8 = cfw.multilook(common.astype(np.float32), K) >= 0.75
    aoi8 = cfw.multilook(aoi.astype(np.float32), K) >= 0.5
    aoi_rings = poly_fraction_from_wkt(kml_wkts(AOI_KML), X0, Y1, W_M, H_M)
    glacier_rings = poly_fraction_from_wkt(kml_wkts(GLACIER_KML), X0, Y1, W_M, H_M)

    # ------------------------------------------------------------ frame + footprints
    log("F1 frame overview with v1 and v2 crop footprints")
    fds = gdal.Open(str(P / "trackG" / "ifg_A_HH_8x8.amp.tif"))
    fgt = fds.GetGeoTransform()
    fa = fds.GetRasterBand(1).ReadAsArray(buf_xsize=fds.RasterXSize // 8, buf_ysize=fds.RasterYSize // 8)
    fdb = 20 * np.log10(np.where(fa > 0, fa, np.nan))
    lo, hi = np.nanpercentile(fdb, [2, 98])
    fw_m, fh_m = fds.RasterXSize * fgt[1], fds.RasterYSize * abs(fgt[5])
    save("f01_frame_amp", colorize(fdb, GREY, lo, hi, np.isfinite(fdb)), extent_km=[fw_m / 1000, fh_m / 1000],
         aoi=poly_fraction_from_wkt(kml_wkts(AOI_KML), fgt[0], fgt[3], fw_m, fh_m),
         crop_v1=poly_fraction_from_wkt([granule_polygon(C / "L1_RSLC_AOI" / "20260714_aoi.h5")], fgt[0], fgt[3], fw_m, fh_m),
         crop_v2=poly_fraction_from_wkt([granule_polygon(C / "L1_RSLC_AOI_v2" / "20260714_aoi.h5")], fgt[0], fgt[3], fw_m, fh_m))

    # ------------------------------------------------------------ AOI amplitude + coverage
    log("F2 AOI amplitude, coverage v1 vs v2")
    amp = cfw.f32(cfw.read_window(PV / "trackG" / "amp_A_HH_1x1_20260714.tif", gcc, grc, NX, NY))
    amp8 = np.sqrt(cfw.multilook(amp ** 2, K))
    adb = 20 * np.log10(np.where(amp8 > 0, amp8, np.nan))
    alo, ahi = np.nanpercentile(adb[aoi8], [2, 98])
    save("f02_aoi_amp", colorize(adb, GREY, alo, ahi, np.isfinite(adb)), extent_km=ext, aoi=aoi_rings,
         glacier=glacier_rings, range_db=[float(alo), float(ahi)])

    base = colorize(adb, GREY, alo, ahi, np.isfinite(adb))

    def coverage_panel(valid_m, ok_m, name, **extra):
        f_nc = cfw.multilook((aoi & ~valid_m).astype(np.float32), K)
        f_ex = cfw.multilook((aoi & valid_m & ~ok_m).astype(np.float32), K)
        img = base.copy()
        s1 = f_ex >= 0.5
        img[s1, :3] = (0.3 * img[s1, :3] + 0.7 * np.array([236, 131, 90])).astype(np.uint8)
        s2 = f_nc >= 0.5
        img[s2, :3] = (0.25 * img[s2, :3] + 0.75 * np.array([110, 108, 104])).astype(np.uint8)
        save(name, img, extent_km=ext, aoi=aoi_rings,
             aoi_no_radar_sample=float((aoi & ~valid_m).sum() / aoi.sum()),
             aoi_excluded_shadow_layover=float((aoi & valid_m & ~ok_m).sum() / aoi.sum()), **extra)

    coverage_panel(valid, geo_ok, "f02b_coverage_v2")
    v1lut = C / "comparison" / "layers" / "lut_rowcol.tif"
    v1ok = C / "comparison" / "layers" / "geoloc_ok_tol15m.tif"
    if v1lut.exists() and v1ok.exists() and gdal.Open(str(v1lut)).RasterYSize == NY:
        v1valid = cfw.read_tif(v1lut, 1) >= 0
        coverage_panel(v1valid, cfw.read_tif(v1ok).astype(bool) & v1valid, "f02b_coverage_v1")
        ny2, nx2 = NY // 2, NX // 2
        quads = {"NW": (slice(0, ny2), slice(0, nx2)), "NE": (slice(0, ny2), slice(nx2, NX)),
                 "SW": (slice(ny2, NY), slice(0, nx2)), "SE": (slice(ny2, NY), slice(nx2, NX))}
        v1okm = cfw.read_tif(v1ok).astype(bool)
        meta["charts"]["coverage_by_quadrant"] = {
            "v1_gdalwarp_native_doppler": {k: float((aoi[s] & v1okm[s]).sum() / aoi[s].sum()) for k, s in quads.items()},
            "v2_kdtree_zero_doppler": rep["lookup_verification"]["aoi_coverage_by_quadrant"]}

    # ------------------------------------------------------------ wrapped phase
    log("F3 wrapped phase, four legs")
    ifg8 = {k: ml_c(v) for k, v in ifg.items()}
    for k, v in ifg8.items():
        save(f"f03_phase_{k}", colorize(np.angle(v), PHASE, -math.pi, math.pi, common8), extent_km=ext,
             cmap_stops=stops(PHASE, 17), vrange=[-math.pi, math.pi])

    log("F4 phase differences")
    for p, q in (("R_full", "R_crop"), ("G_full", "G_crop"), ("R_full", "G_full")):
        save(f"f04_dphase_{p}__vs__{q}", colorize(np.angle(ifg8[p] * np.conj(ifg8[q])), DIVERGING, -math.pi, math.pi, common8),
             extent_km=ext, cmap_stops=stops(DIVERGING), vrange=[-math.pi, math.pi])

    # Doppler-carrier model, as in comparison I1b
    i1b = rep.get("I1b_doppler_carrier_attribution", {})
    daz = cfw.read_tif(LAY / "R_full_dense_azimuth_residual_lines.tif")
    d8, vf = ml_masked(daz, valid)
    k_med = i1b.get("k_rad_per_line_median")
    if k_med is not None:
        sp, sm = i1b.get("model_sign_+", {}), i1b.get("model_sign_-", {})
        sign = +1 if sp.get("phase_diff_coherence", 0) >= sm.get("phase_diff_coherence", 0) else -1
        corr = np.exp(-1j * sign * k_med * np.nan_to_num(d8)).astype(np.complex64)
        save("f04_dphase_R_full__vs__G_full_doppler_model", colorize(np.angle(ifg8["R_full"] * corr * np.conj(ifg8["G_full"])),
             DIVERGING, -math.pi, math.pi, common8), extent_km=ext, cmap_stops=stops(DIVERGING),
             vrange=[-math.pi, math.pi], sign=sign, k_rad_per_line=k_med)
        dv = float(np.nanpercentile(np.abs(d8[common8]), 98))
        save("f04b_azimuth_residual", colorize(d8, DIVERGING, -dv, dv, common8), extent_km=ext,
             cmap_stops=stops(DIVERGING), vrange=[-dv, dv], units="lines")

        z8 = ifg8["R_full"][common8] * corr[common8] * np.conj(ifg8["G_full"][common8])
        z8 = z8[np.abs(z8) > 0] / np.abs(z8[np.abs(z8) > 0])
        meta["charts"]["phase_diff_hist_R_full__vs__G_full_40m_doppler_model"] = hist(
            np.angle(z8 * np.exp(-1j * np.angle(z8.mean()))), -math.pi, math.pi, 90)

    mask5 = common[::2, ::2]
    phist = {}
    for p, q in (("R_full", "R_crop"), ("G_full", "G_crop"), ("R_full", "G_full")):
        z = ifg[p][::2, ::2][mask5] * np.conj(ifg[q][::2, ::2][mask5])
        z = z[np.abs(z) > 0]
        z = z / np.abs(z)
        phist[f"{p}__vs__{q}_5m"] = hist(np.angle(z * np.exp(-1j * np.angle(z.mean()))), -math.pi, math.pi, 90)
        z8 = ifg8[p][common8] * np.conj(ifg8[q][common8])
        z8 = z8[np.abs(z8) > 0] / np.abs(z8[np.abs(z8) > 0])
        phist[f"{p}__vs__{q}_40m"] = hist(np.angle(z8 * np.exp(-1j * np.angle(z8.mean()))), -math.pi, math.pi, 90)
    meta["charts"]["phase_diff_hist"] = phist

    # ------------------------------------------------------------ zoom (true 5 m)
    log("F9 glacier-zone zoom at 5 m")
    gz = glacier_rings[0]
    cx, cy = int(np.mean([p[0] for p in gz]) * NX), int(np.mean([p[1] for p in gz]) * NY)
    Z = 1000
    zx0, zy0 = max(0, min(NX - Z, cx - Z // 2)), max(0, min(NY - Z, cy - Z // 2))
    zs = (slice(zy0, zy0 + Z), slice(zx0, zx0 + Z))
    zx_m, zy1_m = X0 + zx0 * PX, Y1 - zy0 * PX
    zinfo = dict(extent_km=[Z * PX / 1000, Z * PX / 1000],
                 glacier=poly_fraction_from_wkt(kml_wkts(GLACIER_KML), zx_m, zy1_m, Z * PX, Z * PX))
    zdb = 20 * np.log10(np.where(amp[zs] > 0, amp[zs], np.nan))
    zlo, zhi = np.nanpercentile(zdb, [2, 98])
    save("f09_zoom_amp", colorize(zdb, GREY, zlo, zhi, np.isfinite(zdb)), **zinfo)
    for k in ("R_full", "G_full"):
        save(f"f09_zoom_phase_{k}", colorize(np.angle(ifg[k][zs]), PHASE, -math.pi, math.pi, common[zs]),
             cmap_stops=stops(PHASE, 17), vrange=[-math.pi, math.pi], **zinfo)
    save("f09_zoom_dphase", colorize(np.angle(ifg["R_full"][zs] * np.conj(ifg["G_full"][zs])), DIVERGING,
                                     -math.pi, math.pi, common[zs]),
         cmap_stops=stops(DIVERGING), vrange=[-math.pi, math.pi], **zinfo)
    del ifg

    # ------------------------------------------------------------ coherence
    log("F5 coherence")
    coh = {"R_full": cfw.read_tif(LAY / "R_full_coh_flat.tif"),
           "R_full_unflat": cfw.read_tif(LAY / "R_full_coh_unflat.tif"),
           "G_full": cfw.f32(cfw.read_window(P / "trackG" / "ifg_A_HH.coh.tif", gcf, grf, NX, NY))}
    cm = common.copy()
    for v in coh.values():
        cm &= v > 0
    coh8 = {k: ml_masked(v, cm)[0] for k, v in coh.items()}
    cm8 = cfw.multilook(cm.astype(np.float32), K) >= 0.75
    for k in ("R_full", "G_full"):
        save(f"f05_coh_{k}", colorize(coh8[k], COHERENCE, 0, 1, cm8), extent_km=ext, cmap_stops=stops(COHERENCE), vrange=[0, 1])
    save("f05_dcoh_R_full__vs__G_full", colorize(coh8["R_full"] - coh8["G_full"], DIVERGING, -0.2, 0.2, cm8),
         extent_km=ext, cmap_stops=stops(DIVERGING), vrange=[-0.2, 0.2])
    save("f05_flattening_effect_R_full", colorize(coh8["R_full"] - coh8["R_full_unflat"], DIVERGING, -0.2, 0.2, cm8),
         extent_km=ext, cmap_stops=stops(DIVERGING), vrange=[-0.2, 0.2])
    meta["charts"]["coherence_hist"] = {k: hist(v[cm][::3], 0, 1, 50) for k, v in coh.items()}
    meta["charts"]["coherence_hist"]["bias_floor_3x3"] = math.sqrt(math.pi) / 6
    del coh

    # ------------------------------------------------------------ ionosphere
    log("F6 ionosphere screens")
    IO = f"{cfw.RUNW_A}/HH/ionospherePhaseScreen"
    sy = np.arange(K // 2, NY, K)[: common8.shape[0]]
    sx = np.arange(K // 2, NX, K)[: common8.shape[1]]
    ri, ci = RI[np.ix_(sy, sx)], CI[np.ix_(sy, sx)]
    vl = (valid & aoi & geo_ok)[np.ix_(sy, sx)]
    scr = {}
    with h5py.File(P / "trackR" / f"RUNW_{TAG}_unw9x8.h5", "r") as h:
        io_f = h[IO][()].astype(np.float32)
    with h5py.File(PV / "trackR" / f"RUNW_{TAG}_unw9x8.h5", "r") as h:
        io_c = h[IO][()].astype(np.float32)
    a = np.full(ri.shape, np.nan, np.float32)
    a[vl] = io_f[np.clip(ri[vl] // 9, 0, io_f.shape[0] - 1), np.clip(ci[vl] // 8, 0, io_f.shape[1] - 1)]
    scr["R_full"] = a
    a = np.full(ri.shape, np.nan, np.float32)
    a[vl] = io_c[np.clip((ri[vl] - AZ0) // 9, 0, io_c.shape[0] - 1), np.clip((ci[vl] - RG0) // 8, 0, io_c.shape[1] - 1)]
    scr["R_crop"] = a
    yc, xc = Y1 - (sy + 0.5) * PX, X0 + (sx + 0.5) * PX
    for lbl, path in (("G_full", P / "trackG" / "ionosphere" / "dispersive_filtered.tif"),
                      ("G_crop", PV / "trackG" / "ionosphere" / "dispersive_filtered.tif")):
        ds = gdal.Open(str(path)); gt = ds.GetGeoTransform()
        arr = ds.GetRasterBand(1).ReadAsArray()
        r40 = np.clip(np.floor((gt[3] - yc) / abs(gt[5])).astype(int), 0, arr.shape[0] - 1)
        c40 = np.clip(np.floor((xc - gt[0]) / gt[1]).astype(int), 0, arr.shape[1] - 1)
        scr[lbl] = np.where(vl, arr[np.ix_(r40, c40)], np.nan).astype(np.float32)
    tec = {k: v * cfw.RAD2TECU for k, v in scr.items()}
    both = np.ones(ri.shape, bool)
    for v in tec.values():
        both &= np.isfinite(v) & (v != 0)
    med = {k: float(np.median(v[both])) for k, v in tec.items()}
    anom = {k: v - med[k] for k, v in tec.items()}
    lim = float(max(np.percentile(np.abs(v[both]), 99) for v in anom.values()))
    lim = math.ceil(lim * 20) / 20
    for k, v in anom.items():
        save(f"f06_iono_{k}", colorize(v, DIVERGING, -lim, lim, both), extent_km=ext, cmap_stops=stops(DIVERGING),
             vrange=[-lim, lim], median_tecu=med[k], units="TECU about own median")
    resid = {}
    RL = 0.05
    for p, q in (("R_full", "R_crop"), ("G_full", "G_crop"), ("R_full", "G_full")):
        d = tec[p] - tec[q]
        off = float(np.median(d[both]))
        resid[f"{p}__vs__{q}"] = {"offset_tecu": off, "resid_std_tecu": float((d[both] - off).std())}
        save(f"f06_resid_{p}__vs__{q}", colorize(d - off, DIVERGING, -RL, RL, both), extent_km=ext,
             cmap_stops=stops(DIVERGING), vrange=[-RL, RL], removed_offset_tecu=off)
    meta["charts"]["iono_levels"] = {k: {"median": float(np.median(v[both])), "p5": float(np.percentile(v[both], 5)),
                                         "p95": float(np.percentile(v[both], 95))} for k, v in tec.items()}
    meta["charts"]["iono_residuals"] = resid

    # ------------------------------------------------------------ dense offsets
    log("F7 dense-offset differences (metres, bilinear onto the crop grid)")
    OFF = cfw.OFF
    with h5py.File(P / "trackR" / f"RUNW_{TAG}_unw9x8.h5", "r") as hf, h5py.File(PV / "trackR" / f"RUNW_{TAG}_unw9x8.h5", "r") as hc:
        zf, sf = hf[f"{OFF}/zeroDopplerTime"][()], hf[f"{OFF}/slantRange"][()]
        zc, sc = hc[f"{OFF}/zeroDopplerTime"][()], hc[f"{OFF}/slantRange"][()]
        FF = {k: hf[f"{OFF}/HH/{k}"][()].astype(np.float64) for k in ("alongTrackOffset", "slantRangeOffset")}
        CC = {k: hc[f"{OFF}/HH/{k}"][()].astype(np.float64) for k in FF}
    frow = (zc - zf[0]) / ((zf[-1] - zf[0]) / (len(zf) - 1))
    fcol = (sc - sf[0]) / ((sf[-1] - sf[0]) / (len(sf) - 1))
    j0 = np.clip(np.floor(frow).astype(int), 0, len(zf) - 2); i0 = np.clip(np.floor(fcol).astype(int), 0, len(sf) - 2)
    wr, wc = (frow - j0)[:, None], fcol - i0
    meta["charts"]["offset_diff_hist"] = {}
    for k in FF:
        F_ = FF[k]
        full_at = (F_[j0][:, i0] * (1 - wc) + F_[j0][:, i0 + 1] * wc) * (1 - wr) + (F_[j0 + 1][:, i0] * (1 - wc) + F_[j0 + 1][:, i0 + 1] * wc) * wr
        nb = [F_[j0][:, i0], F_[j0][:, i0 + 1], F_[j0 + 1][:, i0], F_[j0 + 1][:, i0 + 1]]
        full_at = np.where(np.logical_and.reduce([np.isfinite(x) & (x != 0) for x in nb]), full_at, np.nan)
        m = np.isfinite(full_at) & np.isfinite(CC[k]) & (CC[k] != 0)
        d = full_at - CC[k]
        save(f"f07_offdiff_{k}", colorize(d, DIVERGING, -0.3, 0.3, m), grid=[int(d.shape[1]), int(d.shape[0])],
             cmap_stops=stops(DIVERGING), vrange=[-0.3, 0.3], units="m",
             note="radar geometry: azimuth down, slant range across; one sample per 32 radar samples")
        meta["charts"]["offset_diff_hist"][k] = hist(np.clip(d[m], -0.6, 0.6), -0.6, 0.6, 60)

    (OUT / "figures.json").write_text(json.dumps(meta, indent=1))
    total = sum(v["kb"] for v in meta["figures"].values())
    log(f"wrote {len(meta['figures'])} figures, {total / 1024:.1f} MB, and {OUT / 'figures.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
