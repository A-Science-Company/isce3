"""
Stage G5 -- one folium HTML with every Track G raster as a toggleable layer
over Google Satellite.

Layers, in the order they appear in the control:

    Amplitude HH <ref>    dB    gray               pooled 2-98% over BOTH dates
    Amplitude HH <sec>    dB    gray               the same scale, so they compare
    Wrapped phase         rad   twilight_shifted   CYCLIC, fixed -pi..pi
    Coherence             0-1   viridis            fixed, never percentile-stretched
    Unwrapped phase       rad   RdBu_r             symmetric robust, conncomp>0 only
    [Phase-sigma coh]     0-1   viridis            the correlation snaphu actually saw

POLARIZATION -- READ THIS
-------------------------
There is no VV in this data and there never was. The NISAR granules are DHDH
(dual-pol HH + HV, L-band), and the L2 GSLCs on disk carry HH ONLY --
`listOfPolarizations` is [b'HH'] in both products, so at L2 there is no VV and
no HV either. Every amplitude layer here is HH, the CO-POL channel, which is the
correct substitute for a requested VV but is NOT VV: HH and VV differ measurably
over the same ground (Bragg scattering over water and bare soil is
polarization-dependent, and the HH/VV ratio is the whole basis of several
soil-moisture retrievals). Every layer name says "HH" for that reason, the map
carries a visible note saying VV is not present, and neither should be relabelled.

WHY THE WARP TARGET IS EPSG:3857 AND NOT EPSG:4326
---------------------------------------------------
folium's ImageOverlay takes lat/lon bounds, which invites warping to EPSG:4326
and handing over the geographic bbox. That is wrong by a visible amount here.

Leaflet places an ImageOverlay by projecting the SW and NE corners into the map
CRS -- Web Mercator -- and stretching the PNG LINEARLY between them. A
plate-carree image is linear in LATITUDE, and latitude is not linear in Mercator
y. The two agree only at the corners and diverge in between. Measured on this
scene (S 9.5106 to N 12.2204, a 2.71 deg span):

    EPSG:3857 (warp target)  drawn at 10.868233 N   error    -0.0 m
    EPSG:4326 (naive)        drawn at 10.871311 N   error  +340.3 m

340 m is 4.3 pixels at 80 m posting -- a systematic bow peaking at mid-swath,
exactly where you would be comparing a fringe against a coastline in the
basemap. Warping to 3857 makes the image linear in the space Leaflet draws it.
The lat/lon bounds handed to folium are then obtained by transforming the corners
OF THE 3857 RASTER back to 4326: same rectangle, just the labels Leaflet wants.

Every layer is warped onto ONE pre-computed 3857 grid (explicit outputBounds plus
width/height), never each with its own auto-computed grid, so all layers share
byte-identical bounds and register against each other in the browser.

Warp the DATA, then colour it. Never colour first and warp the PNG: RGB
interpolation of a cyclic colormap is meaningless and nothing downstream knows
which colours were nodata. The wrapped phase is warped as the COMPLEX
interferogram with np.angle taken afterwards, so resampling averages PHASORS
rather than angles -- averaging angles across the +pi/-pi branch cut gives a
value near zero that is near neither input.

WHY THE PNGs ARE WRITTEN AS INDEX ARRAYS AND NOT WITH plt.savefig
------------------------------------------------------------------
`plt.savefig(..., bbox_inches='tight', dpi=150)` re-measures the axes and crops
to them, and dpi/figsize decide the pixel count -- so the saved image is a
RESAMPLED, RE-CROPPED version of the array whose georeferenced bounds you then
declare. Close, but the pixel grid no longer corresponds 1:1 to the raster, and
the error is silent. Here each layer becomes an (H, W) index array written with
PIL, so output pixel (i, j) IS input element (i, j).

Paletted 8-bit, not RGBA, and that is lossless rather than a compromise: a
matplotlib colormap IS a 256-entry lookup table, so a single-colormap layer never
holds more than 256 distinct colours. Storing 4 bytes/px just hands the encoder
incompressible noise in three correlated channels. Measured on this scene: five
layers at ~87 MB RGBA become 39 MB paletted, and encoding drops from ~4.5 s to
~0.7 s per layer.

Transparency is carried the whole way as NaN (srcNodata=nan through the warp),
then alpha 0 wherever the value is not finite. The swath is a rotated
parallelogram: measured finite fraction 0.6675, so a THIRD of every image must be
transparent or the basemap sits under a big dark rectangle.

WHY branca.colormap IS NOT USED FOR THE LEGENDS
------------------------------------------------
Two concrete blockers, both verified:
  1. Every branca ColorMap renders `d3.select(".legend.leaflet-control")`, and
     d3.select returns the FIRST match -- so with several colormaps on one map
     all the SVGs append into the first legend div and the rest render empty.
  2. ColorMap.render() injects a CDN JavascriptLink for d3.min.js, a network
     dependency in a file whose whole point is being self-contained.
Instead each colorbar is rendered once with matplotlib into a small base64 PNG
and placed in one legend panel wired to Leaflet's overlayadd/overlayremove, so
the bar you see is the layer you are looking at.

SIZE
----
Layers are written as sidecar PNGs and added with `show=False` except the first,
so the browser creates an <img> only for the visible overlay and the rest
download on demand. Base64-embedding every layer at full resolution is NOT sane
(~52 MB of JS string, parsed on every load regardless of which layers are on);
`overlay.embed: true` is there for a portable single file and is only reasonable
together with `overlay.decimate: 2`.

Note that `folium.utilities.image_to_url` base64-inlines ANY string that is not a
recognised URL scheme -- it does `open(image, 'rb')` on it. So passing a relative
path INLINES it rather than linking it. To genuinely link, the overlay is
constructed with a valid-scheme placeholder and its `.url` set afterwards.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
from pathlib import Path

import numpy as np

from .config import Config
from .igram import pair_list, pair_paths
from .ingest import load_stack
from .unwrap import unwrap_paths
from .util import Logger, Result, StepFailed, human_bytes, write_sidecar

C_LIGHT = 299792458.0
CMAP_AMP = "gray"
CMAP_COH = "viridis"
CMAP_PHS = "twilight_shifted"   # CYCLIC: the colour at -pi equals the colour at +pi
CMAP_UNW = "RdBu_r"             # diverging, for a signed quantity about zero


# ------------------------------------------------------------------ warping
def mercator_grid(ref_tif: Path, decimate: int = 1):
    """
    ONE EPSG:3857 grid derived from a reference raster.

    Returns (outputBounds, width, height, folium_bounds) with folium_bounds
    [[S, W], [N, E]] of that exact 3857 rectangle, in degrees. `decimate`
    shrinks width/height but NOT the bounds, so every layer keeps the same
    footprint and the same corners regardless of resolution.
    """
    from osgeo import gdal, osr

    gdal.UseExceptions()
    probe = gdal.Warp("", str(ref_tif), format="VRT", dstSRS="EPSG:3857",
                      resampleAlg="near")
    gt = probe.GetGeoTransform()
    w, h = probe.RasterXSize // decimate, probe.RasterYSize // decimate
    minx, maxy = gt[0], gt[3]
    maxx = gt[0] + gt[1] * probe.RasterXSize
    miny = gt[3] + gt[5] * probe.RasterYSize
    probe = None

    # Both SRS need TRADITIONAL_GIS_ORDER or the transform returns lat/lon
    # swapped -- GDAL 3 honours the authority's axis order by default.
    merc = osr.SpatialReference()
    merc.ImportFromEPSG(3857)
    merc.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    wgs = osr.SpatialReference()
    wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(merc, wgs)
    west, north = tr.TransformPoint(minx, maxy)[:2]
    east, south = tr.TransformPoint(maxx, miny)[:2]
    return (minx, miny, maxx, maxy), w, h, [[south, west], [north, east]]


def warp(tif: Path, grid, resample: str = "near") -> np.ndarray:
    """Warp onto the pinned 3857 grid, NaN outside."""
    from osgeo import gdal

    gdal.UseExceptions()
    bounds, w, h, _ = grid
    ds = gdal.Warp("", str(tif), format="VRT", dstSRS="EPSG:3857",
                   outputBounds=bounds, width=w, height=h, resampleAlg=resample,
                   srcNodata=float("nan"), dstNodata=float("nan"))
    a = ds.ReadAsArray()
    ds = None
    return a


def warp_int(tif: Path, grid) -> np.ndarray:
    """Warp an integer label raster (0 = nodata) with nearest neighbour."""
    from osgeo import gdal

    gdal.UseExceptions()
    bounds, w, h, _ = grid
    ds = gdal.Warp("", str(tif), format="VRT", dstSRS="EPSG:3857",
                   outputBounds=bounds, width=w, height=h, resampleAlg="near",
                   srcNodata=0, dstNodata=0)
    a = ds.ReadAsArray()
    ds = None
    return a


# ----------------------------------------------------------------- PNG/legend
def layer_png(data: np.ndarray, mask: np.ndarray, cmap: str,
              vmin: float, vmax: float) -> bytes:
    """(H,W) float -> 8-bit PALETTED PNG bytes. `mask` True == keep."""
    import matplotlib

    matplotlib.use("Agg")
    from PIL import Image

    x = np.clip((data - vmin) / (vmax - vmin), 0.0, 1.0)
    x = np.nan_to_num(x, nan=0.0, posinf=1.0, neginf=0.0)
    idx = np.where(mask, np.round(x * 254.0).astype(np.uint8), np.uint8(255))

    # matplotlib.colormaps[...], not cm.get_cmap: the latter was removed in
    # matplotlib 3.9 and this env is on 3.11.
    lut = (matplotlib.colormaps[cmap](np.linspace(0, 1, 255))[:, :3] * 255).astype(np.uint8)
    pal = np.zeros((256, 3), np.uint8)
    pal[:255] = lut

    img = Image.fromarray(idx, mode="P")
    img.putpalette(pal.reshape(-1).tolist())
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True, transparency=255, compress_level=9)
    return buf.getvalue()


def colorbar_png(cmap: str, vmin: float, vmax: float, label: str,
                 ticks=None, ticklabels=None) -> str:
    """Small horizontal colorbar as a base64 PNG for the legend panel."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize

    fig, ax = plt.subplots(figsize=(3.0, 0.62), dpi=150)
    fig.subplots_adjust(bottom=0.52, top=0.86, left=0.06, right=0.94)
    cb = fig.colorbar(ScalarMappable(norm=Normalize(vmin, vmax), cmap=cmap),
                      cax=ax, orientation="horizontal")
    if ticks is not None:
        cb.set_ticks(ticks)
        if ticklabels is not None:
            cb.set_ticklabels(ticklabels)
    cb.ax.tick_params(labelsize=7, length=2, colors="#111")
    cb.set_label(label, fontsize=8, labelpad=2, color="#111")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


LEGEND_TMPL = """
<div id="ovl-legend" style="
     position:fixed; bottom:24px; left:24px; z-index:9999;
     background:rgba(255,255,255,.92); border:1px solid #999; border-radius:4px;
     padding:6px 8px 2px 8px; font:11px/1.35 system-ui,sans-serif;
     box-shadow:0 1px 6px rgba(0,0,0,.35); max-width:340px;">
  <div id="ovl-legend-body"></div>
  <div style="color:#555; padding:2px 2px 4px 2px;">%NOTE%</div>
</div>
<script>
(function () {
  var BARS = %BARS%;
  // Leaflet fires 'overlayadd' only for layers toggled AFTER load, never for
  // the one that starts on the map, so the initially-shown layer is seeded
  // from Python rather than discovered.
  var active = %INIT%;
  function draw() {
    document.getElementById('ovl-legend-body').innerHTML =
      active.map(function (n) {
        return BARS[n] ? '<img alt="" style="display:block;width:300px"'
                       + ' src="data:image/png;base64,' + BARS[n] + '">' : '';
      }).join('');
    document.getElementById('ovl-legend').style.display =
      active.length ? 'block' : 'none';
  }
  function hook() {
    var m = null;
    for (var k in window) {
      if (k.indexOf('map_') === 0 && window[k] instanceof L.Map) {
        m = window[k]; break;
      }
    }
    // The legend element is appended to <body> before folium's map script
    // runs, so the map global does not exist yet on the first call.
    if (!m) { setTimeout(hook, 100); return; }
    m.on('overlayadd', function (e) {
      if (active.indexOf(e.name) < 0) { active.push(e.name); draw(); }
    });
    m.on('overlayremove', function (e) {
      var i = active.indexOf(e.name);
      if (i >= 0) { active.splice(i, 1); draw(); }
    });
    draw();
  }
  draw();
  hook();
})();
</script>
"""


def plane_fit(u: np.ndarray, keep: np.ndarray, max_pts: int = 400000) -> dict:
    """
    Least-squares plane through the unwrapped phase, and the variance it explains.

    Reported rather than removed. A large planar term over a 12-day L-band pair
    is orbital/ionospheric in character, not ground motion, and saying so in the
    legend is the difference between a map and a misleading map.
    """
    rows, cols = np.nonzero(keep)
    if rows.size < 100:
        return {}
    if rows.size > max_pts:
        sel = np.random.default_rng(0).choice(rows.size, max_pts, replace=False)
        rows, cols = rows[sel], cols[sel]
    z = u[rows, cols].astype(np.float64)
    A = np.column_stack([cols.astype(np.float64), rows.astype(np.float64),
                         np.ones(rows.size)])
    coef, *_ = np.linalg.lstsq(A, z, rcond=None)
    resid = z - A @ coef
    var = float(z.var())
    # Spans are taken from the array's OWN shape, so they stay correct at any
    # decimation and on any grid -- the fit is done in warped-grid pixels.
    ny, nx = u.shape
    return {
        "slope_x_rad_per_px": float(coef[0]),
        "slope_y_rad_per_px": float(coef[1]),
        "total_x_rad": float(coef[0] * nx),
        "total_y_rad": float(coef[1] * ny),
        "variance_explained": float(1.0 - resid.var() / var) if var > 0 else 0.0,
        "residual_std_rad": float(resid.std()),
        "n_points": int(rows.size),
    }


def wavelength_m(cfg: Config, stack: dict, freq: str) -> float:
    """Radar wavelength from the GSLC's own centerFrequency; 0.2318 m if absent."""
    import h5py

    for d in stack["dates"]:
        p = cfg.gslc_output(d, cfg.freq_tag)
        if not p.exists():
            continue
        try:
            with h5py.File(str(p), "r") as h:
                fc = float(h[f"/science/LSAR/GSLC/grids/frequency{freq}/centerFrequency"][()])
            return C_LIGHT / fc
        except Exception:
            continue
    return 0.231768


def html_path(cfg: Config, ref: str, sec: str) -> Path:
    """
    Namespace the HTML by frequency and polarization, the way the rasters are.

    Without this, a freq-A run finds the freq-B `trackG_overlay.html` already on
    disk and silently SKIPs -- the rasters are `ifg_A_HH.*` vs `ifg_B_HH.*` and
    never collide, but a single fixed html_name does.
    """
    freq = cfg.igram_freq
    pol = cfg.igram_pol
    stem = Path(cfg.overlay.html_name).stem
    return pair_paths(cfg, ref, sec)["dir"] / f"{stem}_{freq}_{pol}.html"


# ------------------------------------------------------------------- builder
def build(cfg: Config, log: Logger, ref: str, sec: str, lam: float) -> dict:
    import folium
    from folium.raster_layers import ImageOverlay

    oc = cfg.overlay
    p = pair_paths(cfg, ref, sec)
    up = unwrap_paths(cfg, ref, sec)
    pol, freq = cfg.igram_pol, cfg.igram_freq
    out_html = html_path(cfg, ref, sec)

    for f in (p["igram"], p["coh"], p["amp_ref"], p["amp_sec"]):
        if not f.exists():
            raise StepFailed(
                f"missing overlay input: {f}\n"
                f"  Run stage G3 first:\n"
                f"    python run_track_g.py --config {cfg.config_path} --only igram"
            )

    # Prefer this pipeline's own unwrap products; fall back to any earlier
    # hand-run pair so an overlay can still be built before stage G4 has run.
    if up["unw"].exists() and up["conncomp"].exists():
        f_unw, f_cc, unw_src = up["unw"], up["conncomp"], "unwrap stage (filtered + phsig)"
    elif Path(f"{p['prefix']}.unw.tif").exists() and Path(f"{p['prefix']}.conncomp.tif").exists():
        f_unw = Path(f"{p['prefix']}.unw.tif")
        f_cc = Path(f"{p['prefix']}.conncomp.tif")
        unw_src = "pre-existing unwrap alongside the interferogram"
    else:
        f_unw = f_cc = None
        unw_src = None

    # --- one Mercator grid for everything --------------------------------
    grid = mercator_grid(p["coh"], decimate=oc.decimate)
    bounds, W, H, fb = grid
    # 'average' on the way down is a real multilook (and, on the complex
    # interferogram, a phasor average). At full resolution there is nothing to
    # average, so stay on 'near' and touch no sample twice.
    rs = "near" if oc.decimate == 1 else "average"
    log.info(f"  3857 grid {W} x {H} (decimate {oc.decimate}, resample '{rs}')")
    log.info(f"  folium bounds [[{fb[0][0]:.6f},{fb[0][1]:.6f}],"
             f"[{fb[1][0]:.6f},{fb[1][1]:.6f}]]")

    png_dir = out_html.parent / (out_html.stem + "_layers")
    if not oc.embed:
        png_dir.mkdir(parents=True, exist_ok=True)

    layers: list[tuple[str, str]] = []
    bars: dict[str, str] = {}
    sizes: dict[str, int] = {}

    def add(name, png_bytes, bar_b64, fname):
        sizes[name] = len(png_bytes)
        if oc.embed:
            src = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
        else:
            with open(png_dir / fname, "wb") as fh:
                fh.write(png_bytes)
            src = f"{png_dir.name}/{fname}"      # a URL, not an os.path
        layers.append((name, src))
        bars[name] = bar_b64
        log.info(f"    layer '{name}': {human_bytes(len(png_bytes))}")

    # --- 1-2. amplitude, both dates, ONE pooled scale --------------------
    a1 = warp(p["amp_ref"], grid, rs)
    a2 = warp(p["amp_sec"], grid, rs)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = 20.0 * np.log10(np.where(a1 > 0, a1, np.nan))
        d2 = 20.0 * np.log10(np.where(a2 > 0, a2, np.nan))
    pool = np.concatenate([d1[np.isfinite(d1)], d2[np.isfinite(d2)]])
    vlo, vhi = np.percentile(pool, oc.amplitude_percentiles)
    finite_frac = float(np.isfinite(d1).mean())
    log.info(f"  amplitude dB pooled {oc.amplitude_percentiles}%: {vlo:.2f} .. {vhi:.2f} "
             f"(finite fraction {finite_frac:.4f})")
    bar_amp = colorbar_png(CMAP_AMP, vlo, vhi, f"{pol} backscatter  (dB)")
    for dt, d in ((ref, d1), (sec, d2)):
        add(f"Amplitude {pol} {dt}  (dB)",
            layer_png(d, np.isfinite(d), CMAP_AMP, vlo, vhi), bar_amp,
            f"amp_{dt}.png")
    del a1, a2, pool

    # --- 3. wrapped phase: warp the COMPLEX field, angle afterwards -------
    ig = warp(p["igram"], grid, rs)
    ph = np.angle(ig)
    mask = np.isfinite(ig.real) & np.isfinite(ig.imag) & (ig != 0)
    add("Wrapped phase", layer_png(ph, mask, CMAP_PHS, -np.pi, np.pi),
        colorbar_png(CMAP_PHS, -np.pi, np.pi, "wrapped phase  (rad)",
                     ticks=[-np.pi, -np.pi / 2, 0, np.pi / 2, np.pi],
                     ticklabels=["-pi", "-pi/2", "0", "pi/2", "pi"]),
        "wrapped_phase.png")
    del ig, ph

    # --- 4. coherence -----------------------------------------------------
    co = warp(p["coh"], grid, rs)
    # Mark the decorrelated-signal floor: for fully decorrelated signal the
    # sample coherence MAGNITUDE has expectation sqrt(pi)/(2 sqrt(L)), so open
    # water sits here rather than at 0. L is the EFFECTIVE look count -- the same
    # number fed to snaphu -- derived from config so the tick cannot drift.
    n_eff = (cfg.unwrap.nlooks_nominal
             or (int(cfg.igram.looks_y) * int(cfg.igram.looks_x))) \
        / (cfg.unwrap.oversample_factor ** 2)
    floor = float(np.sqrt(np.pi) / (2 * np.sqrt(n_eff)))
    add("Coherence", layer_png(co, np.isfinite(co), CMAP_COH, 0.0, 1.0),
        colorbar_png(CMAP_COH, 0, 1, "coherence  |gamma|",
                     ticks=[0, floor, .4, .6, .8, 1],
                     ticklabels=["0", f"{floor:.2f}", "0.4", "0.6", "0.8", "1"]),
        "coherence.png")
    del co

    # --- 5. unwrapped phase ----------------------------------------------
    stats: dict = {"unwrapped_source": unw_src}
    if f_unw is not None:
        # 'near' even when decimating: averaging across a connected-component
        # boundary blends two independent 2*pi offsets into a value that is
        # neither, and labels obviously cannot be averaged either.
        unw = warp(f_unw, grid, "near")
        cc = warp_int(f_cc, grid)
        keep = (cc > 0) & np.isfinite(unw)
        if keep.any():
            lab, n = np.unique(cc[keep], return_counts=True)
            big = int(lab[np.argmax(n)])
            # Every connected component carries its own arbitrary integer-cycle
            # offset. Reference to the LARGEST component so the colours mean
            # something there; other components stay offset by an unknown cycle.
            refv = float(np.median(unw[keep & (cc == big)]))
            u = unw - refv
            lo, hi = np.percentile(u[keep], oc.unwrap_percentiles)
            v = float(max(abs(lo), abs(hi)))
            cm_per_cycle = 100.0 * lam / 2.0
            fit = plane_fit(u, keep)
            stats.update({"n_components": int(len(lab)), "largest_component": big,
                          "largest_component_px": int(n.max()),
                          "symmetric_range_rad": round(v, 4),
                          "symmetric_range_cm_los": round(v / (2 * np.pi) * cm_per_cycle, 2),
                          "plane_fit": fit})
            log.info(f"  unwrapped: {len(lab)} component(s), largest={big} "
                     f"({int(n.max())} px); symmetric range +/-{v:.2f} rad "
                     f"(+/-{v / (2 * np.pi) * cm_per_cycle:.1f} cm LOS)")
            if fit:
                log.info(f"  planar fit explains {fit['variance_explained'] * 100:.1f}% of the "
                         f"unwrapped variance "
                         f"({fit['slope_x_rad_per_px']:+.2e} rad/px x, "
                         f"{fit['slope_y_rad_per_px']:+.2e} rad/px y)")
            add("Unwrapped phase", layer_png(u, keep, CMAP_UNW, -v, v),
                colorbar_png(CMAP_UNW, -v, v,
                             "unwrapped phase  (rad, ref = largest component)",
                             ticks=[-v, -v / 2, 0, v / 2, v],
                             ticklabels=[f"{t:+.1f}" for t in (-v, -v / 2, 0, v / 2, v)]),
                "unwrapped_phase.png")
            del unw, cc, u
        else:
            log.warn("unwrapped raster has no pixels in any connected component; "
                     "layer skipped")
            f_unw = None
    else:
        log.warn(f"no unwrapped product for {ref}_{sec}; the unwrapped layer is skipped. "
                 f"Run: --only unwrap")

    # --- 6. optional: the correlation snaphu actually saw ------------------
    if oc.include_phsig and up["phsig"].exists():
        ps = warp(up["phsig"], grid, rs)
        add("Phase-sigma coherence (snaphu input)",
            layer_png(ps, np.isfinite(ps) & (ps > 0), CMAP_COH, 0.0, 1.0),
            colorbar_png(CMAP_COH, 0, 1,
                         "phase-sigma coherence (from the FILTERED ifg)"),
            "phsig.png")
        del ps

    # --- the map ----------------------------------------------------------
    ctr = [(fb[0][0] + fb[1][0]) / 2, (fb[0][1] + fb[1][1]) / 2]
    m = folium.Map(location=ctr, zoom_start=oc.zoom_start, tiles=oc.basemap_url,
                   attr=oc.basemap_attr, max_zoom=oc.max_zoom)
    for i, (name, src) in enumerate(layers):
        # folium.utilities.image_to_url base64-embeds ANY string that is not a
        # recognised URL scheme -- it does open(image,'rb') on it. A relative
        # path would therefore be INLINED, not linked. Hand the constructor a
        # valid-scheme placeholder it passes through, then set the real URL.
        ov = ImageOverlay(image="data:,", bounds=fb, opacity=oc.opacity,
                          name=name, show=(i == 0))
        ov.url = src
        ov.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds(fb)

    note = (f"NISAR L-band freq {freq}, pol <b>{pol}</b> (co-pol) &mdash; "
            f"<b>there is no VV in this product</b> (it is HH-only at L2; the "
            f"RSLC is dual-pol HH+HV). 1 cycle = {100 * lam / 2:.1f} cm LOS.")
    fit = stats.get("plane_fit") or {}
    if fit:
        note += (f"<br><b>Phase is not displacement.</b> A single plane explains "
                 f"{fit['variance_explained'] * 100:.0f}% of the unwrapped variance "
                 f"({fit['total_y_rad']:+.0f} rad N&ndash;S, "
                 f"{fit['total_x_rad']:+.1f} rad E&ndash;W across the scene); the rest is "
                 f"long-wavelength and non-planar. Treat as orbital/ionospheric until a "
                 f"ramp is removed and corrections are applied.")
    m.get_root().html.add_child(folium.Element(
        LEGEND_TMPL.replace("%BARS%", json.dumps(bars))
                   .replace("%INIT%", json.dumps([layers[0][0]]))
                   .replace("%NOTE%", note)))

    out_html.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_html))

    tot = sum(sizes.values())
    stats.update({
        "html": str(out_html), "html_bytes": os.path.getsize(out_html),
        "layers": [n for n, _ in layers], "layer_bytes": sizes,
        "total_png_bytes": tot, "embedded": oc.embed,
        "grid_3857": [W, H], "bounds": fb, "decimate": oc.decimate,
        "amplitude_db_range": [round(float(vlo), 3), round(float(vhi), 3)],
        "wavelength_m": lam,
    })
    if not oc.embed:
        stats["png_dir"] = str(png_dir)
    log.info(f"  {len(layers)} layer(s), {human_bytes(tot)} of PNG")
    log.info(f"  HTML {os.path.getsize(out_html):,} bytes -> {out_html}")
    return stats


def run(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    started = time.time()
    res = Result(stage="overlay")
    stack = load_stack(cfg)
    oc = cfg.overlay
    pairs = pair_list(cfg, stack)
    lam = wavelength_m(cfg, stack, cfg.igram_freq)

    if dry_run:
        for ref, sec in pairs:
            log.info(f"  would build {html_path(cfg, ref, sec)}")
            log.info(f"      layers: amplitude {ref}, amplitude {sec}, wrapped phase, "
                     f"coherence, unwrapped phase"
                     + (", phase-sigma coherence" if oc.include_phsig else ""))
            log.info(f"      opacity {oc.opacity}  decimate {oc.decimate}  "
                     f"embed {oc.embed}  basemap {oc.basemap_attr}")
        res.skipped = True
        return res

    todo = [(r, s) for r, s in pairs if force or not html_path(cfg, r, s).exists()]
    for r, s in pairs:
        if (r, s) not in todo:
            log.info(f"  {r}_{s}: {html_path(cfg, r, s).name} exists -- skipping "
                     f"(--force to rebuild)")
    if not todo:
        res.skipped = True
        res.outputs = [str(html_path(cfg, r, s)) for r, s in pairs]
        return res

    report: dict[str, dict] = {}
    outputs: list[str] = []
    for ref, sec in todo:
        log.info(f"  pair {ref}_{sec}  (wavelength {lam:.6f} m, "
                 f"1 cycle = {100 * lam / 2:.1f} cm LOS)")
        st = build(cfg, log, ref, sec, lam)
        report[f"{ref}_{sec}"] = st
        outputs.append(st["html"])

    write_sidecar(
        cfg.prov_dir / "overlay.json", "overlay",
        inputs={"pairs": {f"{r}_{s}": str(pair_paths(cfg, r, s)["dir"]) for r, s in todo}},
        outputs={"pairs": report},
        parameters={"opacity": oc.opacity, "decimate": oc.decimate, "embed": oc.embed,
                    "basemap": oc.basemap_attr, "include_phsig": oc.include_phsig,
                    "polarization": cfg.igram_pol,
                    "polarization_note": "HH (co-pol). There is no VV in this product; "
                                         "the L2 GSLCs are HH-only."},
        started=started,
    )

    first = report[list(report)[0]]
    res.outputs = outputs
    res.metrics = {
        "layers": len(first["layers"]),
        "html_bytes": first["html_bytes"],
        "png_total": human_bytes(first["total_png_bytes"]),
        "grid_3857": first["grid_3857"],
    }
    res.notes = [
        "amplitude layers are HH (co-pol) -- there is no VV in this product",
        f"unwrapped layer source: {first.get('unwrapped_source')}",
    ]
    return res
