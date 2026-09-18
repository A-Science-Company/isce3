#!/usr/bin/env python3
"""
Figures and chart data for the four-way comparison report.

    python -u tools/report_figures.py

Reads the comparison layers cached by tools/compare_four_way.py plus the four
workflows' products; writes <case>/comparison/report/fig/*.webp and
<case>/comparison/report/figures.json. Read-only on every product.

DESIGN RULES THE FIGURES FOLLOW
-------------------------------
* Map panels carry NO baked-in text. Titles, colourbars, scale bars and polygon
  outlines are drawn by the HTML, so they follow the viewer's light/dark theme
  and stay sharp. figures.json carries each map's value range, colourbar stops
  and overlay geometry in panel-fraction coordinates.
* Pixels outside the comparison mask are transparent (WebP alpha), so the plate
  behind the image shows through instead of an arbitrary fill colour.
* Wrapped phase uses a CYCLIC colormap (matplotlib 'twilight'). Phase is
  periodic; a linear ramp would draw a false edge at +-pi.
* Differences and ionosphere use a diverging blue-grey-red ramp centred on 0.
  Coherence uses a single-hue sequential ramp (more coherent = darker).
* Display maps of 5 m products are 8x8 COMPLEX averages (40 m), matching the
  40 m statistics; the zoom panels show true 5 m pixels.
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
PAIR = "20260714_20260726"
TAG = "20260714_20260726_A_HH_1x1"
OUT = C / "comparison" / "report"
FIG = OUT / "fig"
FIG.mkdir(parents=True, exist_ok=True)
LAY = C / "comparison" / "layers"
P, PA = C / "pairs" / PAIR, C / "aoi" / "pairs" / PAIR
AOI_KML = Path("/home/sharath/asf_slc/glof_exact_aoi.kml")
GLACIER_KML = Path("/home/sharath/nisar_downloader/nepal_glacier_zone.kml")
K = 8                      # display multilook: 8 x 5 m = 40 m
WEBP_Q = 78

DIVERGING = LinearSegmentedColormap.from_list(
    "div", ["#1c5cab", "#6da7ec", "#f0efec", "#ee8a89", "#b8302f"])
COHERENCE = LinearSegmentedColormap.from_list(
    "coh", ["#f2f5f9", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
PHASE = colormaps["twilight"]
GREY = colormaps["gray"]

meta: dict = {"figures": {}, "charts": {}}


def log(m):
    print(m, flush=True)


def stops(cmap, n=11):
    return [to_hex(cmap(i / (n - 1))) for i in range(n)]


def save(name, rgba, **info):
    im = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=WEBP_Q, method=6)
    (FIG / f"{name}.webp").write_bytes(buf.getvalue())
    meta["figures"][name] = {"file": f"fig/{name}.webp", "px": [rgba.shape[1], rgba.shape[0]],
                             "kb": round(len(buf.getvalue()) / 1024), **info}
    log(f"  {name}: {rgba.shape[1]}x{rgba.shape[0]} {len(buf.getvalue()) / 1024:.0f} KB")


def colorize(values, cmap, vmin, vmax, mask):
    x = np.clip((values - vmin) / (vmax - vmin), 0, 1)
    rgba = (cmap(np.nan_to_num(x)) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(mask, 255, 0)
    return rgba


def ml_complex(a, k=K):
    return cfw.multilook(a.astype(np.complex64), k)


def ml_real(a, k=K):
    return cfw.multilook(a.astype(np.float32), k)


def poly_fraction(kml, x0, y1, width_m, height_m):
    """KML polygon ring(s) -> [[fx, fy], ...] in panel fractions (0..1, y down)."""
    ds = ogr.Open(str(kml))
    s = osr.SpatialReference(); s.ImportFromEPSG(4326)
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    t = osr.SpatialReference(); t.ImportFromEPSG(32645)
    t.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(s, t)
    rings = []
    for f in ds.GetLayer(0):
        g = f.GetGeometryRef().Clone()
        g.FlattenTo2D()
        g.Transform(tr)
        ring = g.GetGeometryRef(0)
        rings.append([[round((ring.GetX(i) - x0) / width_m, 5), round((y1 - ring.GetY(i)) / height_m, 5)]
                      for i in range(ring.GetPointCount())])
    return rings


def main() -> int:
    rep = json.loads((C / "comparison" / "comparison.json").read_text())
    X0, Y1 = rep["lattice"]["origin"]
    PX = rep["lattice"]["px_m"]
    NY, NX = rep["lattice"]["shape"]
    W_M, H_M = NX * PX, NY * PX
    AZ0, RG0 = rep["crop_origins"]["reference"]

    log("loading layers")
    aoi = cfw.read_tif(C / "comparison" / "aoi_polygon_mask.tif").astype(bool)
    geo_ok = cfw.read_tif(LAY / "geoloc_ok_tol15m.tif").astype(bool)
    lut = gdal.Open(str(LAY / "lut_rowcol.tif"))
    lr = lut.GetRasterBand(1).ReadAsArray()
    lc = lut.GetRasterBand(2).ReadAsArray()
    valid_lut = (lr >= 0) & (lc >= 0)
    ri, ci = lr.astype(np.int32), lc.astype(np.int32)
    del lr, lc

    Gf_ifg = P / "trackG" / "ifg_A_HH.igram.tif"
    Gc_ifg = PA / "trackG" / "ifg_A_HH_1x1.igram.tif"
    gcf, grf, _ = cfw.lattice_offset(Gf_ifg, X0, Y1)
    gcc, grc, _ = cfw.lattice_offset(Gc_ifg, X0, Y1)
    ifg = {
        "R_full": cfw.read_tif(LAY / "R_full_ifg.tif"),
        "R_crop": cfw.read_tif(LAY / "R_crop_ifg.tif"),
        "G_full": cfw.read_window(Gf_ifg, gcf, grf, NX, NY).astype(np.complex64),
        "G_crop": cfw.read_window(Gc_ifg, gcc, grc, NX, NY).astype(np.complex64),
    }
    common = aoi & valid_lut & geo_ok
    for k in ifg:
        common &= ifg[k] != 0
    common8 = ml_real(common) >= 0.75
    aoi8 = ml_real(aoi) >= 0.5

    # ---------------------------------------------------------------- frame
    log("F1 frame overview")
    famp = P / "trackG" / "ifg_A_HH_8x8.amp.tif"
    fds = gdal.Open(str(famp))
    fgt = fds.GetGeoTransform()
    fa = fds.GetRasterBand(1).ReadAsArray(buf_xsize=fds.RasterXSize // 8, buf_ysize=fds.RasterYSize // 8)
    fdb = 20 * np.log10(np.where(fa > 0, fa, np.nan))
    lo, hi = np.nanpercentile(fdb, [2, 98])
    fmask = np.isfinite(fdb)
    save("f01_frame_amp", colorize(fdb, GREY, lo, hi, fmask),
         extent_km=[fds.RasterXSize * fgt[1] / 1000, fds.RasterYSize * abs(fgt[5]) / 1000],
         aoi=poly_fraction(AOI_KML, fgt[0], fgt[3], fds.RasterXSize * fgt[1], fds.RasterYSize * abs(fgt[5])))

    # ---------------------------------------------------------------- AOI amplitude
    log("F2 AOI amplitude")
    amp = cfw.read_window(PA / "trackG" / "amp_A_HH_1x1_20260714.tif", gcc, grc, NX, NY).astype(np.float32)
    amp8 = np.sqrt(ml_real(np.where(amp > 0, amp ** 2, 0)))
    adb = 20 * np.log10(np.where(amp8 > 0, amp8, np.nan))
    lo, hi = np.nanpercentile(adb[aoi8], [2, 98])
    save("f02_aoi_amp", colorize(adb, GREY, lo, hi, np.isfinite(adb)),
         extent_km=[W_M / 1000, H_M / 1000],
         aoi=poly_fraction(AOI_KML, X0, Y1, W_M, H_M),
         glacier=poly_fraction(GLACIER_KML, X0, Y1, W_M, H_M),
         range_db=[float(lo), float(hi)])

    # ---------------------------------------------------------------- wrapped phase
    log("F3 wrapped phase, four legs (40 m display)")
    ifg8 = {k: ml_complex(v) for k, v in ifg.items()}
    for k, v in ifg8.items():
        save(f"f03_phase_{k}", colorize(np.angle(v), PHASE, -math.pi, math.pi, common8),
             extent_km=[W_M / 1000, H_M / 1000], cmap_stops=stops(PHASE, 17), vrange=[-math.pi, math.pi])

    log("F4 phase differences (40 m)")
    diffs = {"R_full__R_crop": ("R_full", "R_crop"), "G_full__G_crop": ("G_full", "G_crop"),
             "R_full__G_full": ("R_full", "G_full")}
    for name, (a, b) in diffs.items():
        d = np.angle(ifg8[a] * np.conj(ifg8[b]))
        save(f"f04_dphase_{name}", colorize(d, DIVERGING, -math.pi, math.pi, common8),
             extent_km=[W_M / 1000, H_M / 1000], cmap_stops=stops(DIVERGING), vrange=[-math.pi, math.pi])

    # the R-G ramp, evaluated from comparison.json, as its own map
    e40 = rep["I1_wrapped_phase"]["R_full__vs__G_full"]["40m"]
    yy, xx = np.mgrid[0:common8.shape[0], 0:common8.shape[1]]
    ramp = (e40["constant_offset_rad"] * 0 + e40["ramp_rad_per_km_x"] * xx * K * PX / 1000
            + e40["ramp_rad_per_km_y"] * yy * K * PX / 1000)
    ramp -= np.median(ramp[common8])
    rr = float(np.percentile(np.abs(ramp[common8]), 99))
    save("f04_ramp_R_full__G_full", colorize(ramp, DIVERGING, -1.0, 1.0, aoi8),
         extent_km=[W_M / 1000, H_M / 1000], cmap_stops=stops(DIVERGING), vrange=[-1.0, 1.0],
         ramp_abs_p99_rad=rr)

    # phase-difference histograms
    def hist_phase(a, b, m, bins=90):
        z = a[m] * np.conj(b[m])
        off = np.angle(z.mean() / 1)
        d = np.angle(z * np.exp(-1j * np.angle(np.mean(z / np.abs(z)))))
        h, e = np.histogram(d, bins=bins, range=(-math.pi, math.pi), density=True)
        return {"centers": [round(float(c), 4) for c in (e[:-1] + e[1:]) / 2],
                "density": [round(float(v), 5) for v in h]}
    sub = common[::2, ::2]
    meta["charts"]["phase_diff_hist"] = {
        "R_full__R_crop_5m": hist_phase(ifg["R_full"][::2, ::2], ifg["R_crop"][::2, ::2], sub),
        "R_full__G_full_5m": hist_phase(ifg["R_full"][::2, ::2], ifg["G_full"][::2, ::2], sub),
        "R_full__G_full_40m": hist_phase(ifg8["R_full"], ifg8["G_full"], common8),
        "G_full__G_crop_5m": hist_phase(ifg["G_full"][::2, ::2], ifg["G_crop"][::2, ::2], sub),
    }

    # ---------------------------------------------------------------- zoom, true 5 m
    log("F9 glacier-zone zoom at 5 m")
    gz = poly_fraction(GLACIER_KML, X0, Y1, W_M, H_M)[0]
    cx = int(np.mean([p[0] for p in gz]) * NX)
    cy = int(np.mean([p[1] for p in gz]) * NY)
    Z = 1000
    zx0, zy0 = max(0, cx - Z // 2), max(0, cy - Z // 2)
    zs = (slice(zy0, zy0 + Z), slice(zx0, zx0 + Z))
    zmask = common[zs]
    zx_m, zy1_m = X0 + zx0 * PX, Y1 - zy0 * PX
    zinfo = dict(extent_km=[Z * PX / 1000, Z * PX / 1000],
                 glacier=poly_fraction(GLACIER_KML, zx_m, zy1_m, Z * PX, Z * PX),
                 origin_utm=[zx_m, zy1_m])
    zdb = 20 * np.log10(np.where(amp[zs] > 0, amp[zs], np.nan))
    lo, hi = np.nanpercentile(zdb, [2, 98])
    save("f09_zoom_amp", colorize(zdb, GREY, lo, hi, np.isfinite(zdb)), **zinfo)
    for k in ("R_full", "G_full"):
        save(f"f09_zoom_phase_{k}", colorize(np.angle(ifg[k][zs]), PHASE, -math.pi, math.pi, zmask),
             cmap_stops=stops(PHASE, 17), vrange=[-math.pi, math.pi], **zinfo)
    zd = np.angle(ifg["R_full"][zs] * np.conj(ifg["G_full"][zs]))
    save("f09_zoom_dphase", colorize(zd, DIVERGING, -math.pi, math.pi, zmask),
         cmap_stops=stops(DIVERGING), vrange=[-math.pi, math.pi], **zinfo)
    del ifg

    # ---------------------------------------------------------------- coherence
    log("F5 coherence")
    coh = {
        "R_full": np.nan_to_num(cfw.read_tif(LAY / "R_full_coh.tif"), nan=0.0),
        "G_full": np.nan_to_num(cfw.read_window(P / "trackG" / "ifg_A_HH.coh.tif", gcf, grf, NX, NY).astype(np.float32), nan=0.0),
    }
    cm = common.copy()
    for v in coh.values():
        cm &= v > 0
    cm8 = ml_real(cm) >= 0.75
    coh8 = {k: ml_real(np.where(cm, v, 0)) / np.maximum(ml_real(cm), 1e-6) for k, v in coh.items()}
    for k, v in coh8.items():
        save(f"f05_coh_{k}", colorize(v, COHERENCE, 0, 1, cm8),
             extent_km=[W_M / 1000, H_M / 1000], cmap_stops=stops(COHERENCE), vrange=[0, 1])
    save("f05_dcoh_G_minus_R", colorize(coh8["G_full"] - coh8["R_full"], DIVERGING, -0.3, 0.3, cm8),
         extent_km=[W_M / 1000, H_M / 1000], cmap_stops=stops(DIVERGING), vrange=[-0.3, 0.3])
    bins = np.linspace(0, 1, 51)
    meta["charts"]["coherence_hist"] = {
        "centers": [round(float(c), 3) for c in (bins[:-1] + bins[1:]) / 2],
        **{k: [round(float(x), 4) for x in np.histogram(v[cm][::3], bins=bins, density=True)[0]]
           for k, v in coh.items()},
        "bias_floor_3x3": math.sqrt(math.pi) / (2 * 3),
    }
    del coh

    # ---------------------------------------------------------------- geolocation exclusion
    log("F8 shadow/layover exclusion")
    excl = aoi & valid_lut & ~geo_ok
    nocov = aoi & ~valid_lut
    f_ex, f_nc = ml_real(excl), ml_real(nocov)
    base = colorize(adb, GREY, *np.nanpercentile(adb[aoi8], [2, 98]), np.isfinite(adb))
    over = base.copy()
    sel = f_ex >= 0.5
    over[sel, :3] = (0.35 * over[sel, :3] + 0.65 * np.array([236, 131, 90])).astype(np.uint8)
    sel2 = f_nc >= 0.5
    over[sel2, :3] = (0.35 * over[sel2, :3] + 0.65 * np.array([137, 135, 129])).astype(np.uint8)
    save("f08_geoloc_exclusion", over, extent_km=[W_M / 1000, H_M / 1000],
         aoi=poly_fraction(AOI_KML, X0, Y1, W_M, H_M),
         excluded_fraction_of_aoi=float(excl.sum() / aoi.sum()),
         no_coverage_fraction_of_aoi=float(nocov.sum() / aoi.sum()))

    # ---------------------------------------------------------------- ionosphere
    log("F6 ionosphere screens on the 40 m display lattice")
    IONO = cfw.IONO
    with h5py.File(P / "trackR" / f"RUNW_{TAG}_unw9x8.h5", "r") as h:
        io_f = h[IONO][()].astype(np.float32)
    with h5py.File(PA / "trackR" / f"RUNW_{TAG}_unw9x8.h5", "r") as h:
        io_c = h[IONO][()].astype(np.float32)
    sy = np.arange(K // 2, NY - K // 2 + 1, K)[: common8.shape[0]]
    sx = np.arange(K // 2, NX - K // 2 + 1, K)[: common8.shape[1]]
    RI, CI = ri[np.ix_(sy, sx)], ci[np.ix_(sy, sx)]
    vl = valid_lut[np.ix_(sy, sx)] & geo_ok[np.ix_(sy, sx)] & aoi[np.ix_(sy, sx)]
    scr = {}
    a = np.full(RI.shape, np.nan, np.float32)
    a[vl] = io_c[np.minimum(RI[vl] // 9, io_c.shape[0] - 1), np.minimum(CI[vl] // 8, io_c.shape[1] - 1)]
    scr["R_crop"] = a
    a = np.full(RI.shape, np.nan, np.float32)
    a[vl] = io_f[np.minimum((RI[vl] + AZ0) // 9, io_f.shape[0] - 1),
                 np.minimum((CI[vl] + RG0) // 8, io_f.shape[1] - 1)]
    scr["R_full"] = a
    ycen = Y1 - (sy + 0.5) * PX
    xcen = X0 + (sx + 0.5) * PX
    for lbl, path in (("G_full", P / "trackG" / "ionosphere" / "dispersive_filtered.tif"),
                      ("G_crop", PA / "trackG" / "ionosphere" / "dispersive_filtered.tif")):
        ds = gdal.Open(str(path))
        gt = ds.GetGeoTransform()
        arr = ds.GetRasterBand(1).ReadAsArray()
        r40 = np.clip(np.floor((gt[3] - ycen) / abs(gt[5])).astype(int), 0, arr.shape[0] - 1)
        c40 = np.clip(np.floor((xcen - gt[0]) / gt[1]).astype(int), 0, arr.shape[1] - 1)
        scr[lbl] = np.where(vl, arr[np.ix_(r40, c40)], np.nan).astype(np.float32)
    rad2tecu = cfw.RAD2TECU
    tec = {k: v * rad2tecu for k, v in scr.items()}
    both = np.ones(RI.shape, bool)
    for v in tec.values():
        both &= np.isfinite(v) & (v != 0)
    for k, v in tec.items():
        save(f"f06_iono_{k}", colorize(v, DIVERGING, -3.0, 3.0, both),
             extent_km=[W_M / 1000, H_M / 1000], cmap_stops=stops(DIVERGING), vrange=[-3.0, 3.0])
    resid = {}
    for name, (p, q) in {"R_crop__R_full": ("R_crop", "R_full"), "G_crop__G_full": ("G_crop", "G_full"),
                         "G_full__R_full": ("G_full", "R_full")}.items():
        d = tec[p] - tec[q]
        off = float(np.median(d[both]))
        resid[name] = {"offset_tecu": off, "resid_std_tecu": float((d[both] - off).std())}
        save(f"f06_resid_{name}", colorize(d - off, DIVERGING, -0.1, 0.1, both),
             extent_km=[W_M / 1000, H_M / 1000], cmap_stops=stops(DIVERGING), vrange=[-0.1, 0.1],
             removed_offset_tecu=off)
    meta["charts"]["iono_levels"] = {
        k: {"median": float(np.median(v[both])), "p5": float(np.percentile(v[both], 5)),
            "p95": float(np.percentile(v[both], 95))} for k, v in tec.items()}
    meta["charts"]["iono_residuals"] = resid

    # ---------------------------------------------------------------- dense offsets
    log("F7 dense-offset differences")
    OFF = cfw.OFF
    with h5py.File(P / "trackR" / f"RUNW_{TAG}_unw9x8.h5", "r") as hf, \
            h5py.File(PA / "trackR" / f"RUNW_{TAG}_unw9x8.h5", "r") as hc:
        zf, sf = hf[f"{OFF}/zeroDopplerTime"][()], hf[f"{OFF}/slantRange"][()]
        zc, sc = hc[f"{OFF}/zeroDopplerTime"][()], hc[f"{OFF}/slantRange"][()]
        FF = {k: hf[f"{OFF}/HH/{k}"][()].astype(np.float64) for k in ("alongTrackOffset", "slantRangeOffset")}
        CC = {k: hc[f"{OFF}/HH/{k}"][()].astype(np.float64) for k in FF}
    ia = np.rint((zc - zf[0]) / (zf[1] - zf[0])).astype(int)
    fcol = (sc - sf[0]) / (sf[1] - sf[0])
    i0 = np.floor(fcol).astype(int)
    w = fcol - i0
    meta["charts"]["offset_diff_hist"] = {}
    for k in FF:
        full_at = FF[k][ia][:, i0] * (1 - w) + FF[k][ia][:, i0 + 1] * w
        m = np.isfinite(full_at) & np.isfinite(CC[k]) & (CC[k] != 0) & (full_at != 0)
        d = CC[k] - full_at
        save(f"f07_offdiff_{k}", colorize(d, DIVERGING, -0.2, 0.2, m),
             grid=[int(d.shape[1]), int(d.shape[0])], cmap_stops=stops(DIVERGING), vrange=[-0.2, 0.2],
             note="radar geometry (azimuth down, slant range across); one sample per 32 radar pixels")
        vals = np.clip(d[m], -0.5, 0.5)
        h, e = np.histogram(vals, bins=50, range=(-0.5, 0.5), density=True)
        meta["charts"]["offset_diff_hist"][k] = {
            "centers": [round(float(c), 4) for c in (e[:-1] + e[1:]) / 2],
            "density": [round(float(x), 4) for x in h]}
        save(f"f07_offfull_{k}", colorize(full_at, COHERENCE, *np.percentile(full_at[m], [2, 98]), m),
             grid=[int(d.shape[1]), int(d.shape[0])], cmap_stops=stops(COHERENCE),
             vrange=[float(x) for x in np.percentile(full_at[m], [2, 98])])

    (OUT / "figures.json").write_text(json.dumps(meta, indent=1))
    total = sum(v["kb"] for v in meta["figures"].values())
    log(f"wrote {len(meta['figures'])} figures, {total / 1024:.1f} MB total, and {OUT / 'figures.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
