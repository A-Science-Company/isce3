#!/usr/bin/env python3
"""
overlay_map.py -- one folium HTML with every Track G raster as a toggleable
layer over Google Satellite.

    python3 overlay_map.py --pair-dir .../pairs/20260613_20260625/trackG \\
        --prefix ifg_B_HH --ref-date 20260613 --sec-date 20260625 \\
        --out trackG_overlay.html

Layers, in the order they stack:

    amplitude <ref>   dB   grey     pooled 2-98% over BOTH dates
    amplitude <sec>   dB   grey     same scale, so the dates are comparable
    coherence         0-1  viridis  fixed, never percentile-stretched
    wrapped phase     rad  twilight_shifted (CYCLIC), fixed -pi..pi
    unwrapped phase   rad  RdBu_r   symmetric robust, conncomp>0 only

POLARIZATION -- READ THIS
-------------------------
There is no VV in this data and there never was.  The NISAR granules are DHDH
(dual-pol HH + HV, L-band), and the L2 GSLCs on disk carry HH only --
`listOfPolarizations` is [b'HH'] in both products.  Every "amplitude" layer
here is HH, the co-pol channel, which is the closest substitute for VV but is
NOT VV: HH and VV differ measurably over the same ground (Bragg scattering
over water and bare soil is polarization-dependent; the HH/VV ratio is the
whole basis of several soil-moisture retrievals).  The layer names in the map
say "HH" for that reason.  Do not relabel them.


Why the warp target is EPSG:3857 and not EPSG:4326
--------------------------------------------------
folium's ImageOverlay takes lat/lon bounds, which invites warping to EPSG:4326
and handing over the geographic bbox.  That is wrong, and wrong by a visible
amount here.

Leaflet places an ImageOverlay by projecting the SW and NE corners into the
map CRS -- Web Mercator -- and stretching the PNG LINEARLY between them.  A
plate-carree (EPSG:4326) image is linear in LATITUDE, and latitude is not
linear in Mercator y.  The two agree only at the corners and diverge in
between.  Measured on this scene (S 9.5106, N 12.2204, a 2.71 deg span):

    max latitude error 0.00308 deg = 340 m = 4.3 pixels at 80 m posting

-- a systematic bow that peaks at mid-swath, exactly where you would be
comparing a fringe against a coastline in the basemap.

Warping to EPSG:3857 instead makes the image linear in the same space Leaflet
draws it, so it lands where it belongs.  The lat/lon bounds handed to folium
are then obtained by transforming the corners OF THE 3857 RASTER back to 4326
-- they describe the same rectangle, they are just the labels Leaflet wants.

Every layer is warped onto ONE pre-computed 3857 grid (explicit outputBounds +
width/height), not each with its own auto-computed grid, so all five overlays
share byte-identical bounds and register against each other in the browser.

Warp the DATA, then colour it.  Never colour first and warp the PNG: RGB
interpolation of a cyclic colormap is meaningless, and nothing downstream
knows which colours were nodata.

resampleAlg is 'near' for the real-valued rasters.  The wrapped phase is
warped as the COMPLEX interferogram and `np.angle` is taken afterwards, so
that resampling averages phasors rather than angles -- averaging angles across
the +pi/-pi branch cut produces a value near zero that is not near either
input.  That makes the phase layer correct even if someone switches to
bilinear later.


Why the PNGs are written as RGBA arrays and not with plt.savefig
----------------------------------------------------------------
The ISCE2 dolphin_overlayer.py writes overlays with

    plt.savefig(png, bbox_inches='tight', pad_inches=0, transparent=True,
                dpi=150)

`bbox_inches='tight'` re-measures the axes and crops to it, and dpi/figsize
decide the pixel count -- so the saved image is a RESAMPLED, RE-CROPPED
version of the array whose georeferenced bounds you then declare.  It is close,
but the pixel grid no longer corresponds 1:1 to the raster, and the error is
silent.  Here each layer is turned into an (H, W, 4) uint8 array and written
with PIL, so output row/column i is input row/column i, exactly.

Transparency is carried the whole way as NaN: srcNodata=nan and dstNodata=nan
through the warp, then alpha=0 wherever the value is not finite (plus, for the
unwrapped layer, wherever conncomp == 0).  The swath is a rotated parallelogram
and 33.2% of the bounding box is outside it -- measured finite fraction 0.6675
-- so this is a third of every image, and it must be transparent rather than
black or the basemap is hidden by a big dark rectangle.


Legends
-------
branca.colormap is deliberately NOT used, for two concrete reasons:

  1. Every branca ColorMap renders `d3.select(".legend.leaflet-control")`.
     d3.select returns the FIRST match, so with five colormaps on one map all
     five SVGs are appended into the first legend div and the other four
     controls render empty.
  2. ColorMap.render() injects
     `JavascriptLink("https://cdnjs.cloudflare.com/ajax/libs/d3/3.5.5/d3.min.js")`
     -- a CDN dependency, in a file whose whole point is being self-contained.

Instead each colorbar is rendered once with matplotlib into a small base64 PNG
and placed in a single legend panel wired to Leaflet's `overlayadd` /
`overlayremove` events, so the bar you see is the layer you are looking at.
"""
import argparse
import base64
import io
import json
import os

import numpy as np

# ---------------------------------------------------------------- constants
C = 299792458.0
CMAP_AMP = "gray"
CMAP_COH = "viridis"
CMAP_PHS = "twilight_shifted"   # CYCLIC: colour at -pi == colour at +pi
CMAP_UNW = "RdBu_r"             # diverging, for a signed quantity about zero


# ------------------------------------------------------------------- warping
def mercator_grid(ref_tif, decimate=1):
    """Compute ONE EPSG:3857 grid from a reference raster.

    Returns (outputBounds, width, height, folium_bounds) where folium_bounds
    is [[S, W], [N, E]] of that exact 3857 rectangle, in degrees.

    `decimate` shrinks width/height but NOT the bounds, so every layer keeps
    the same footprint and the same lat/lon corners regardless of resolution.
    """
    from osgeo import gdal, osr
    gdal.UseExceptions()
    probe = gdal.Warp("", ref_tif, format="VRT", dstSRS="EPSG:3857",
                      resampleAlg="near")
    gt = probe.GetGeoTransform()
    w, h = probe.RasterXSize // decimate, probe.RasterYSize // decimate
    minx, maxy = gt[0], gt[3]
    maxx = gt[0] + gt[1] * probe.RasterXSize
    miny = gt[3] + gt[5] * probe.RasterYSize
    probe = None

    merc = osr.SpatialReference(); merc.ImportFromEPSG(3857)
    merc.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    wgs = osr.SpatialReference(); wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(merc, wgs)
    west, north = tr.TransformPoint(minx, maxy)[:2]
    east, south = tr.TransformPoint(maxx, miny)[:2]
    return (minx, miny, maxx, maxy), w, h, [[south, west], [north, east]]


def warp(tif, grid, resample="near"):
    """Warp `tif` onto the pinned 3857 grid, NaN outside."""
    from osgeo import gdal
    gdal.UseExceptions()
    bounds, w, h, _ = grid
    ds = gdal.Warp("", tif, format="VRT", dstSRS="EPSG:3857",
                   outputBounds=bounds, width=w, height=h,
                   resampleAlg=resample,
                   srcNodata=float("nan"), dstNodata=float("nan"))
    a = ds.ReadAsArray()
    ds = None
    return a


def warp_int(tif, grid):
    """Warp an integer label raster (0 = nodata) with nearest neighbour."""
    from osgeo import gdal
    gdal.UseExceptions()
    bounds, w, h, _ = grid
    ds = gdal.Warp("", tif, format="VRT", dstSRS="EPSG:3857",
                   outputBounds=bounds, width=w, height=h,
                   resampleAlg="near", srcNodata=0, dstNodata=0)
    a = ds.ReadAsArray()
    ds = None
    return a


# ------------------------------------------------------------------ PNG/RGBA
def layer_png(data, mask, cmap, vmin, vmax):
    """(H,W) float -> 8-bit PALETTED PNG bytes.  `mask` True == keep.

    Written pixel-for-pixel: no figure, no dpi, no bbox_inches.  Output pixel
    (i, j) is input element (i, j), which is what makes the declared bounds
    exact.

    Paletted, not RGBA, and this is lossless rather than a compromise: a
    matplotlib colormap IS a 256-entry lookup table, so an RGBA render of a
    single-colormap layer never contains more than 256 distinct colours.
    Storing 4 bytes/px to represent 8 bits of information just hands the PNG
    encoder incompressible noise in three correlated channels.  Indices 0-254
    carry the data, index 255 is the transparent one (tRNS).

    Measured on this scene (3793 x 3759): coherence 26.7 MB RGBA -> 8.9 MB
    paletted, wrapped phase 30.7 -> 9.5 MB, amplitude 14.9 -> 8.7 MB; and
    encoding drops from ~4.5 s to ~0.7 s per layer.
    """
    import matplotlib
    matplotlib.use("Agg")
    from PIL import Image

    x = np.clip((data - vmin) / (vmax - vmin), 0.0, 1.0)
    x = np.nan_to_num(x, nan=0.0, posinf=1.0, neginf=0.0)
    idx = np.where(mask, np.round(x * 254.0).astype(np.uint8), np.uint8(255))

    # matplotlib.colormaps[...], not cm.get_cmap: the latter was removed in
    # matplotlib 3.9 and this env is on 3.11.
    lut = (matplotlib.colormaps[cmap](np.linspace(0, 1, 255))[:, :3] * 255
           ).astype(np.uint8)
    pal = np.zeros((256, 3), np.uint8)
    pal[:255] = lut

    img = Image.fromarray(idx, mode="P")
    img.putpalette(pal.reshape(-1).tolist())
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True, transparency=255,
             compress_level=9)
    return buf.getvalue()


def colorbar_png(cmap, vmin, vmax, label, ticks=None, ticklabels=None):
    """Small horizontal colorbar as base64 PNG for the legend panel."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable

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


# --------------------------------------------------------------- legend + JS
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
    // runs, so the map global does not exist yet on first call.
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


# ------------------------------------------------------------------ the build
def build(pair_dir, prefix, ref_date, sec_date, out_html,
          embed=False, opacity=0.85, wavelength=None, decimate=1):
    import folium
    from folium.raster_layers import ImageOverlay
    from osgeo import gdal
    gdal.UseExceptions()

    j = lambda n: os.path.join(pair_dir, n)
    f_ig = j(f"{prefix}.igram.tif")
    f_co = j(f"{prefix}.coh.tif")
    f_unw = j(f"{prefix}.unw.tif")
    f_cc = j(f"{prefix}.conncomp.tif")
    pol = prefix.split("_")[-1]
    f_a1 = j(f"amp_B_{pol}_{ref_date}.tif")
    f_a2 = j(f"amp_B_{pol}_{sec_date}.tif")

    for f in (f_ig, f_co, f_a1, f_a2):
        if not os.path.exists(f):
            raise SystemExit(f"missing input: {f}")
    have_unw = os.path.exists(f_unw) and os.path.exists(f_cc)

    # --- one Mercator grid for everything -------------------------------
    grid = mercator_grid(f_co, decimate=decimate)
    bounds, W, H, fb = grid
    # 'average' on the way down is a real multilook (and, on the complex
    # interferogram, a phasor average).  At full resolution there is nothing
    # to average, so stay on 'near' and touch no sample twice.
    rs = "near" if decimate == 1 else "average"
    print(f"3857 grid {W} x {H} (decimate {decimate}, resample '{rs}'); "
          f"folium bounds "
          f"[[{fb[0][0]:.6f},{fb[0][1]:.6f}],[{fb[1][0]:.6f},{fb[1][1]:.6f}]]")

    png_dir = os.path.join(os.path.dirname(os.path.abspath(out_html)),
                           os.path.splitext(os.path.basename(out_html))[0]
                           + "_layers")
    if not embed:
        os.makedirs(png_dir, exist_ok=True)

    layers, bars, sizes = [], {}, {}

    def add(name, png_bytes, bar_b64, fname):
        sizes[name] = len(png_bytes)
        if embed:
            src = "data:image/png;base64," + base64.b64encode(
                png_bytes).decode()
        else:
            with open(os.path.join(png_dir, fname), "wb") as fh:
                fh.write(png_bytes)
            src = f"{os.path.basename(png_dir)}/{fname}"   # URL, not os.path
        layers.append((name, src))
        bars[name] = bar_b64

    # --- amplitude, both dates, ONE pooled scale ------------------------
    a1 = warp(f_a1, grid, rs)
    a2 = warp(f_a2, grid, rs)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = 20.0 * np.log10(np.where(a1 > 0, a1, np.nan))
        d2 = 20.0 * np.log10(np.where(a2 > 0, a2, np.nan))
    pool = np.concatenate([d1[np.isfinite(d1)], d2[np.isfinite(d2)]])
    vlo, vhi = np.percentile(pool, [2.0, 98.0])
    print(f"amplitude dB pooled 2-98%: {vlo:.2f} .. {vhi:.2f}")
    bar_amp = colorbar_png(CMAP_AMP, vlo, vhi, f"{pol} backscatter  (dB)")
    for dt, d in ((ref_date, d1), (sec_date, d2)):
        nm = f"Amplitude {pol} {dt}  (dB)"
        add(nm, layer_png(d, np.isfinite(d), CMAP_AMP, vlo, vhi),
            bar_amp, f"amp_{dt}.png")

    # --- coherence ------------------------------------------------------
    co = warp(f_co, grid, rs)
    add("Coherence", layer_png(co, np.isfinite(co), CMAP_COH, 0.0, 1.0),
        colorbar_png(CMAP_COH, 0, 1, "coherence  |gamma|",
                     ticks=[0, .189, .4, .6, .8, 1],
                     ticklabels=["0", "0.19", "0.4", "0.6", "0.8", "1"]),
        "coherence.png")

    # --- wrapped phase: warp the COMPLEX field, angle afterwards --------
    ig = warp(f_ig, grid, rs)
    ph = np.angle(ig)
    mask = np.isfinite(ig.real) & np.isfinite(ig.imag) & (ig != 0)
    add("Wrapped phase", layer_png(ph, mask, CMAP_PHS, -np.pi, np.pi),
        colorbar_png(CMAP_PHS, -np.pi, np.pi, "wrapped phase  (rad)",
                     ticks=[-np.pi, -np.pi / 2, 0, np.pi / 2, np.pi],
                     ticklabels=["-pi", "-pi/2", "0", "pi/2", "pi"]),
        "wrapped_phase.png")

    # --- unwrapped phase ------------------------------------------------
    if have_unw:
        # 'near' even when decimating: averaging across a connected-component
        # boundary blends two independent 2*pi offsets into a value that is
        # neither.  Labels obviously cannot be averaged either.
        unw = warp(f_unw, grid, "near")
        cc = warp_int(f_cc, grid)
        keep = (cc > 0) & np.isfinite(unw)
        # Each connected component carries its own arbitrary 2*pi offset.
        # Reference to the LARGEST component so the colours mean something
        # there; other components stay offset by an unknown integer cycle.
        lab, n = np.unique(cc[keep], return_counts=True)
        big = int(lab[np.argmax(n)])
        ref = float(np.median(unw[keep & (cc == big)]))
        u = unw - ref
        lo, hi = np.percentile(u[keep], [2.0, 98.0])
        v = float(max(abs(lo), abs(hi)))
        cm_per_cycle = 100.0 * (wavelength or 0.231768) / 2.0
        print(f"unwrapped: {len(lab)} components, largest={big} "
              f"({int(n.max())} px); symmetric range +/-{v:.2f} rad "
              f"(+/-{v / (2 * np.pi) * cm_per_cycle:.1f} cm LOS)")
        add("Unwrapped phase", layer_png(u, keep, CMAP_UNW, -v, v),
            colorbar_png(CMAP_UNW, -v, v,
                         "unwrapped phase  (rad, ref = largest component)",
                         ticks=[-v, -v / 2, 0, v / 2, v],
                         ticklabels=[f"{t:+.1f}" for t in
                                     (-v, -v / 2, 0, v / 2, v)]),
            "unwrapped_phase.png")
    else:
        print(f"NOTE: {f_unw} not found -- unwrapped layer skipped. "
              f"Run gslc_unwrap.py first.")

    # --- the map --------------------------------------------------------
    ctr = [(fb[0][0] + fb[1][0]) / 2, (fb[0][1] + fb[1][1]) / 2]
    m = folium.Map(location=ctr, zoom_start=9,
                   tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
                   attr="Google Satellite", max_zoom=20)
    for i, (name, src) in enumerate(layers):
        # folium.utilities.image_to_url embeds ANY string that is not a
        # recognised URL scheme -- it does `open(image,'rb')` and base64s the
        # bytes.  A relative path like "x_layers/coh.png" therefore gets
        # INLINED, not linked (this is why dolphin_overlayer.py, which passes
        # str(png_path), produces self-contained HTML even though it also
        # writes the PNGs to disk).  To actually link, hand the constructor a
        # valid-scheme placeholder it will pass through, then set the real
        # relative URL afterwards.
        ov = ImageOverlay(image="data:,", bounds=fb, opacity=opacity,
                          name=name, show=(i == 0))
        ov.url = src
        ov.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds(fb)

    lam = wavelength or 0.231768
    note = (f"NISAR L-band freq B, pol <b>{pol}</b> (co-pol) &mdash; "
            f"there is no VV in this product. "
            f"1 cycle = {100 * lam / 2:.1f} cm LOS.")
    if have_unw:
        note += ("<br><b>Phase is not displacement.</b> A single plane "
                 "explains 48% of the unwrapped variance (a ~25 rad N&ndash;S "
                 "gradient, ~0 in range); the rest is long-wavelength and "
                 "non-planar. Treat as orbital/atmospheric until a ramp is "
                 "removed and corrections are applied.")
    m.get_root().html.add_child(folium.Element(
        LEGEND_TMPL.replace("%BARS%", json.dumps(bars))
                   .replace("%INIT%", json.dumps([layers[0][0]]))
                   .replace("%NOTE%", note)))
    m.save(out_html)

    tot = sum(sizes.values())
    print(f"\n{'layer':<34}{'PNG bytes':>12}")
    for k, v in sizes.items():
        print(f"{k:<34}{v:>12,}")
    print(f"{'TOTAL':<34}{tot:>12,}  ({tot/1e6:.1f} MB)")
    print(f"HTML {os.path.getsize(out_html):,} bytes -> {out_html}")
    if not embed:
        print(f"PNGs -> {png_dir}/")
    return out_html


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair-dir", required=True)
    ap.add_argument("--prefix", default="ifg_B_HH")
    ap.add_argument("--ref-date", required=True)
    ap.add_argument("--sec-date", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--opacity", type=float, default=0.85)
    ap.add_argument("--embed", action="store_true",
                    help="inline the PNGs as base64 data URIs so the HTML is "
                         "a single portable file; costs ~4/3 the PNG bytes "
                         "and is only sane together with --decimate 2")
    ap.add_argument("--decimate", type=int, default=1,
                    help="shrink every layer by this integer factor "
                         "(bounds unchanged); 2 gives 160 m pixels")
    a = ap.parse_args()
    build(a.pair_dir, a.prefix, a.ref_date, a.sec_date, a.out,
          embed=a.embed, opacity=a.opacity, decimate=a.decimate)
