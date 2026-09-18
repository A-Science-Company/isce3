#!/usr/bin/env python3
"""
Geocode Track R's coregistered RSLC amplitudes to dB and build a Leaflet overlay.

    python tools/slc_amp_overlay.py --case /path/to/case_studies/nepal_glof \
        --pair 20260714 20260726 --looks 9 1 --out /tmp/overlay

Produces a SELF-CONTAINED FOLDER -- index.html plus one PNG per layer, read at
runtime by relative URL -- suitable for `gsutil -m rsync -r` to a bucket.

THREE LAYERS, AND WHY THE THIRD ONE IS THE POINT
------------------------------------------------
  1. reference          <scratch>/crossmul/freqB/HH/reference.slc
  2. secondary COREG    <scratch>/fine_resample_slc/freqB/HH/coregistered_secondary.slc
  3. secondary RAW      the untouched L1 RSLC, straight out of the HDF5

All three are rendered through the REFERENCE's rdr2geo lon/lat. For 1 and 2 that
is correct by construction: resampling put the secondary on the reference radar
grid, so reference pixel [i,j] and coregistered pixel [i,j] see the same ground.

For 3 it is deliberately WRONG, and that is the whole point. Applying the
reference's geolocation to the raw secondary is exactly the "no coregistration"
assumption, so layer 3 renders displaced from layer 1 by the offset geo2rdr
measured (~746 px azimuth = ~3.3 km here). Toggling 2 against 3 shows what
coregistration actually did.

POLARIZATION: HH, AND IT IS NOT RELABELLED
------------------------------------------
These granules are DHDH -- HH and HV only, on both frequencies. There is no VV
anywhere in this product. HH is the correct co-pol substitute where a VV
amplitude is wanted, and every label in the output says HH, matching the
convention already fixed in nisar_wf/overlay.py. The HTML carries a visible note.

WHY THE WARP TARGET IS EPSG:3857
--------------------------------
Leaflet places an ImageOverlay by projecting the SW/NE corners into Web Mercator
and stretching the PNG LINEARLY between them. A plate-carree (4326) image is
linear in latitude, and latitude is not linear in Mercator y -- the two agree
only at the corners. Warping to 3857 makes the image linear in the space Leaflet
draws it; the lat/lon bounds handed to Leaflet are then the 3857 rectangle's
corners transformed back to 4326. Same reasoning, and the same measured ~340 m
mid-swath error, as nisar_wf/overlay.py.

Every layer is warped onto ONE pre-computed 3857 grid, so all three register
against each other in the browser byte-for-byte.

The dB stretch is POOLED across all three layers. Stretching each to its own
percentiles would make them incomparable, which defeats the comparison.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

# Scrub argv before anything can import pyre via isce3 -- pyre parses sys.argv
# inside its package __init__ and a CLI with flags crashes it.
_ARGV = sys.argv[1:]
sys.argv = [sys.argv[0]]

import h5py  # noqa: E402
from osgeo import gdal, osr  # noqa: E402

gdal.UseExceptions()

FREQ = "B"
POL = "HH"


# --------------------------------------------------------------------------
# multilook
# --------------------------------------------------------------------------
def _look_power(block: np.ndarray, az_looks: int, rg_looks: int) -> np.ndarray:
    """Mean |z|^2 over each (az_looks x rg_looks) box of one block."""
    L, W = block.shape
    L -= L % az_looks
    W -= W % rg_looks
    b = block[:L, :W]
    p = (b.real.astype(np.float32) ** 2 + b.imag.astype(np.float32) ** 2)
    return p.reshape(L // az_looks, az_looks, W // rg_looks, rg_looks).mean(axis=(1, 3))


def _look_mean(block: np.ndarray, az_looks: int, rg_looks: int) -> np.ndarray:
    L, W = block.shape
    L -= L % az_looks
    W -= W % rg_looks
    b = block[:L, :W].astype(np.float64)
    return b.reshape(L // az_looks, az_looks, W // rg_looks, rg_looks).mean(axis=(1, 3))


class SlcSource:
    """Uniform block reader over an ENVI raster or an RSLC HDF5 dataset."""

    def __init__(self, spec: str, h5_path: str | None = None):
        self.h5 = None
        if h5_path:
            self.h5 = h5py.File(h5_path, "r")
            self.d = self.h5[spec]
            self.shape = self.d.shape
        else:
            self.ds = gdal.Open(spec)
            self.shape = (self.ds.RasterYSize, self.ds.RasterXSize)

    def read(self, r0: int, nrows: int, ncols: int) -> np.ndarray:
        if self.h5 is not None:
            return self.d[r0:r0 + nrows, :ncols]
        a = self.ds.GetRasterBand(1).ReadAsArray(0, r0, ncols, nrows)
        return a

    def close(self):
        if self.h5 is not None:
            self.h5.close()


def multilook_db(src: SlcSource, rows: int, cols: int,
                 az_looks: int, rg_looks: int, label: str) -> np.ndarray:
    """
    Streamed multilook to dB. Never holds more than one block of the SLC.

    dB = 10*log10(<|z|^2>) -- power averaged over the look box, THEN logged.
    Averaging in dB instead would bias the result low.
    """
    out_rows = rows // az_looks
    out_cols = cols // rg_looks
    out = np.empty((out_rows, out_cols), dtype=np.float32)

    block_looks = max(1, 4096 // az_looks)          # ~4096 input lines per read
    step = block_looks * az_looks
    done = 0
    for r0 in range(0, out_rows * az_looks, step):
        n = min(step, out_rows * az_looks - r0)
        blk = src.read(r0, n, cols)
        p = _look_power(blk, az_looks, rg_looks)
        out[done:done + p.shape[0]] = p
        done += p.shape[0]
        print(f"    [{label}] {done}/{out_rows} look-rows", flush=True)
    del blk

    with np.errstate(divide="ignore", invalid="ignore"):
        out = 10.0 * np.log10(out, out=out)
    out[~np.isfinite(out)] = np.nan
    return out


def multilook_geoloc(path: Path, rows: int, cols: int,
                     az_looks: int, rg_looks: int, label: str) -> np.ndarray:
    """Multilook a Float64 rdr2geo layer with the same boxes as the amplitude."""
    ds = gdal.Open(str(path))
    band = ds.GetRasterBand(1)
    out_rows, out_cols = rows // az_looks, cols // rg_looks
    out = np.empty((out_rows, out_cols), dtype=np.float64)

    block_looks = max(1, 4096 // az_looks)
    step = block_looks * az_looks
    done = 0
    for r0 in range(0, out_rows * az_looks, step):
        n = min(step, out_rows * az_looks - r0)
        blk = band.ReadAsArray(0, r0, cols, n)
        m = _look_mean(blk, az_looks, rg_looks)
        out[done:done + m.shape[0]] = m
        done += m.shape[0]
    print(f"    [{label}] {done}/{out_rows} look-rows", flush=True)
    ds = None
    return out


# --------------------------------------------------------------------------
# geocoding via GDAL geolocation arrays
# --------------------------------------------------------------------------
def write_tif(path: Path, arr: np.ndarray, dtype: int) -> Path:
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(str(path), arr.shape[1], arr.shape[0], 1, dtype,
                    options=["TILED=YES", "COMPRESS=DEFLATE", "ZLEVEL=1"])
    ds.GetRasterBand(1).WriteArray(arr)
    ds.FlushCache()
    ds = None
    return path


def geocode(amp_tif: Path, lon_tif: Path, lat_tif: Path, out_tif: Path,
            res_deg: float, bounds: tuple) -> Path:
    """
    Warp a radar-coordinate raster to EPSG:4326 using lon/lat geolocation arrays.

    GDAL's GEOLOCATION metadata domain is the mechanism that makes this possible
    without a per-pixel resampling of our own: the warper inverts the lon/lat
    mapping itself.

    `bounds` IS NOT OPTIONAL, and this is the trap in the whole tool. Left to
    estimate the output extent itself, GDAL samples the geolocation arrays
    coarsely, and a SAR swath is a rotated parallelogram whose corners that
    sampling misses. Measured on this scene at 0.004 deg:

        auto bounds       542 x 408   lon [83.9254, 86.0934]   92.0% valid
        explicit bounds   802 x 672   lon [83.3839, 86.5919]   65.3% valid

    The auto box is INSIDE the swath -- the high "valid" fraction is the tell,
    not a good sign. 65.3% is what a parallelogram inscribed in its own bounding
    box should give (~64% from the granule footprint polygon), and the corners
    of the scene only survive in that second row.
    """
    src = gdal.Open(str(amp_tif))
    vrt = gdal.GetDriverByName("VRT").CreateCopy("", src)
    src = None
    vrt.SetMetadata({
        "X_DATASET": str(lon_tif), "X_BAND": "1",
        "Y_DATASET": str(lat_tif), "Y_BAND": "1",
        "PIXEL_OFFSET": "0", "LINE_OFFSET": "0",
        "PIXEL_STEP": "1", "LINE_STEP": "1",
        "SRS": 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
               'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]',
    }, "GEOLOCATION")

    gdal.Warp(str(out_tif), vrt, dstSRS="EPSG:4326", geoloc=True,
              xRes=res_deg, yRes=res_deg, outputBounds=bounds,
              resampleAlg="bilinear",
              dstNodata=float("nan"), multithread=True,
              creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "ZLEVEL=1"],
              warpMemoryLimit=256)
    vrt = None
    return out_tif


def mercator_grid(ref_tif: Path, max_px: int):
    """ONE EPSG:3857 grid, long edge capped at max_px. Bounds never change."""
    probe = gdal.Warp("", str(ref_tif), format="VRT", dstSRS="EPSG:3857",
                      resampleAlg="near")
    gt = probe.GetGeoTransform()
    W, H = probe.RasterXSize, probe.RasterYSize
    minx, maxy = gt[0], gt[3]
    maxx = gt[0] + gt[1] * W
    miny = gt[3] + gt[5] * H
    probe = None

    scale = min(1.0, max_px / max(W, H))
    w, h = max(1, int(W * scale)), max(1, int(H * scale))

    # Both SRS need TRADITIONAL_GIS_ORDER or the transform returns lat/lon
    # swapped: GDAL 3 honours the authority's axis order by default.
    merc = osr.SpatialReference(); merc.ImportFromEPSG(3857)
    merc.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    wgs = osr.SpatialReference(); wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(merc, wgs)
    west, north = tr.TransformPoint(minx, maxy)[:2]
    east, south = tr.TransformPoint(maxx, miny)[:2]
    return (minx, miny, maxx, maxy), w, h, [[south, west], [north, east]]


def warp_to_grid(tif: Path, grid) -> np.ndarray:
    bounds, w, h, _ = grid
    ds = gdal.Warp("", str(tif), format="VRT", dstSRS="EPSG:3857",
                   outputBounds=bounds, width=w, height=h,
                   resampleAlg="bilinear",
                   srcNodata=float("nan"), dstNodata=float("nan"))
    a = ds.ReadAsArray()
    ds = None
    return a


# --------------------------------------------------------------------------
# PNG
# --------------------------------------------------------------------------
def layer_png(data: np.ndarray, path: Path, vmin: float, vmax: float) -> Path:
    """
    Greyscale RGBA PNG, transparent where the data is NaN.

    Warp the DATA then colour it -- never colour first and warp the PNG, which
    would interpolate across colour boundaries.
    """
    from PIL import Image

    valid = np.isfinite(data)
    x = np.clip((data - vmin) / (vmax - vmin), 0.0, 1.0)
    x[~valid] = 0.0
    v = (x * 255.0).astype(np.uint8)
    rgba = np.dstack([v, v, v, np.where(valid, 255, 0).astype(np.uint8)])
    Image.fromarray(rgba, mode="RGBA").save(path, optimize=True)
    return path


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
HTML = r"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{margin:0;height:100%;background:#111;font:13px/1.45 system-ui,sans-serif}
  #map{position:absolute;inset:0}
  /* The panel owns the top-right corner, so the layer switcher lives INSIDE it.
     Leaflet's own L.control.layers renders at .leaflet-top.leaflet-right and
     would sit underneath this box -- three layers you could never reach. */
  .panel{position:absolute;top:10px;right:10px;z-index:1000;background:rgba(20,20,22,.95);
         color:#e8e8ea;padding:12px 14px;border-radius:8px;width:320px;
         box-shadow:0 2px 14px rgba(0,0,0,.6)}
  .panel h3{margin:0 0 10px;font-size:14px;font-weight:600}
  .layers{margin:0 0 11px}
  .layers .hd{font-size:11px;text-transform:uppercase;letter-spacing:.6px;
              color:#8a8a95;margin-bottom:5px}
  .ly{display:flex;align-items:flex-start;gap:8px;padding:6px 8px;border-radius:5px;
      cursor:pointer;border:1px solid transparent}
  .ly:hover{background:#26262b}
  .ly.on{background:#1d3350;border-color:#3d6ea8}
  .ly input{margin:2px 0 0}
  .ly .tx{font-size:12.5px}
  .ly .sub{display:block;color:#96969f;font-size:11px;margin-top:1px}
  .ly kbd{margin-left:auto;font:11px ui-monospace,monospace;color:#8a8a95;
          border:1px solid #444;border-radius:3px;padding:0 4px}
  .hint{font-size:11px;color:#8a8a95;margin:6px 0 0}
  table{border-collapse:collapse;font-size:12px;width:100%;margin-top:4px}
  td{padding:1px 6px 1px 0;vertical-align:top}
  td:first-child{color:#9aa;white-space:nowrap}
  .note{margin-top:10px;padding:7px 8px;background:#3a2f12;border-left:3px solid #d0a022;
        border-radius:3px;font-size:11.5px;color:#f0e2bd}
  .bar{margin-top:11px}
  .cap{font-size:11px;color:#9aa;margin-bottom:3px}
  .grad{height:11px;border-radius:2px;background:linear-gradient(90deg,#000,#fff);
        border:1px solid #444}
  .ticks{display:flex;justify-content:space-between;font-size:11px;color:#9aa;margin-top:2px}
  .op{width:100%;margin-top:9px}
</style></head><body>
<div id="map"></div>
<div class="panel">
  <h3>__TITLE__</h3>
  <div class="layers">
    <div class="hd">Layer</div>
    <div id="lys"></div>
    <div class="hint">Press <b>1</b>/<b>2</b>/<b>3</b>, or <b>space</b> to blink
      between coregistered and raw.</div>
  </div>
  <table>__META__</table>
  <div class="bar">
    <div class="cap">shared stretch &mdash; all three layers</div>
    <div class="grad"></div>
    <div class="ticks"><span>__VMIN__ dB</span><span>__VMAX__ dB</span></div>
  </div>
  <input class="op" type="range" min="0" max="1" step="0.02" value="1" id="op">
  <div class="note">__NOTE__</div>
</div>
<script>
var BOUNDS = __BOUNDS__, LAYERS = __LAYERS__;
var map = L.map('map', {preferCanvas:true});
L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
  {maxZoom:20, attribution:'Google Satellite'}).addTo(map);
map.fitBounds(BOUNDS);

// Sidecar PNGs by RELATIVE url -- nothing inlined. All three are added once and
// shown by opacity, so switching is instant and never refetches.
var ovs = LAYERS.map(function(L_){
  return L.imageOverlay(L_.file, BOUNDS, {opacity:0}).addTo(map);
});
var cur = 0, alpha = 1;

var box = document.getElementById('lys');
LAYERS.forEach(function(L_, i){
  var row = document.createElement('label');
  row.className = 'ly' + (i === 0 ? ' on' : '');
  var parts = L_.name.split(/,\s*/);
  row.innerHTML = '<input type="radio" name="ly"' + (i === 0 ? ' checked' : '') + '>' +
    '<span class="tx">' + parts[0] +
    (parts.length > 1 ? '<span class="sub">' + parts.slice(1).join(', ') + '</span>' : '') +
    '</span><kbd>' + (i + 1) + '</kbd>';
  row.onclick = function(){ show(i); };
  box.appendChild(row);
});

function show(i){
  cur = i;
  ovs.forEach(function(o, k){ o.setOpacity(k === i ? alpha : 0); });
  [].forEach.call(box.children, function(el, k){
    el.classList.toggle('on', k === i);
    el.querySelector('input').checked = (k === i);
  });
}
show(0);

document.getElementById('op').addEventListener('input', function(e){
  alpha = +e.target.value; show(cur);
});
document.addEventListener('keydown', function(e){
  if (e.key >= '1' && e.key <= String(LAYERS.length)) { show(+e.key - 1); }
  // space blinks the two secondary layers against each other -- the fastest way
  // to see the coregistration shift
  else if (e.code === 'Space') { e.preventDefault(); show(cur === 1 ? 2 : 1); }
});
L.control.scale({imperial:false}).addTo(map);
</script></body></html>
"""


def write_html(out_dir: Path, layers, bounds, vmin, vmax, meta, title, note):
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in meta.items())
    html = (HTML
            .replace("__TITLE__", title)
            .replace("__META__", rows)
            .replace("__BOUNDS__", json.dumps(bounds))
            .replace("__LAYERS__", json.dumps(layers))
            .replace("__VMIN__", f"{vmin:.1f}")
            .replace("__VMAX__", f"{vmax:.1f}")
            .replace("__NOTE__", note))
    p = out_dir / "index.html"
    p.write_text(html, encoding="utf-8")
    return p


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", help="case directory (holds scratch/, L1_RSLC/)")
    ap.add_argument("--pair", nargs=2, metavar=("REF", "SEC"))
    ap.add_argument("--html-only", action="store_true",
                    help="rebuild index.html from <out>/manifest.json; PNGs and "
                         "their shared stretch are left exactly as they are")
    ap.add_argument("--looks", nargs=2, type=int, default=[9, 1], metavar=("AZ", "RG"))
    ap.add_argument("--max-px", type=int, default=8000,
                    help="cap the 3857 grid's long edge (default 8000)")
    ap.add_argument("--out", required=True, help="output folder for index.html + PNGs")
    ap.add_argument("--keep-tif", action="store_true", help="keep the geocoded GeoTIFFs")
    ap.add_argument("--floor-db", type=float, default=40.0,
                    help="treat pixels more than this many dB below the scene "
                         "median as fill, not data (default 40)")
    ap.add_argument("--reuse", action="store_true",
                    help="reuse multilooked rasters already in <out>/_work "
                         "(the expensive part; safe when only the warp changed)")
    args = ap.parse_args(_ARGV)
    if not args.html_only and not (args.case and args.pair):
        ap.error("--case and --pair are required unless --html-only is given")

    # --html-only: rebuild index.html from the manifest of a previous run. The
    # PNGs and their stretch are untouched, so wording/layout changes cost
    # seconds instead of re-reading three 2.9 GB SLCs.
    if args.html_only:
        man = json.loads((Path(args.out) / "manifest.json").read_text())
        html = write_html(Path(args.out), man["layers"], man["bounds"],
                          man["vmin"], man["vmax"], man["meta"],
                          man["title"], man["note"])
        print(f"rebuilt {html} from manifest "
              f"(stretch {man['vmin']:.2f} .. {man['vmax']:.2f} dB, unchanged)")
        return 0

    case = Path(args.case)
    ref, sec = args.pair
    az_looks, rg_looks = args.looks
    tag = f"{FREQ}_{POL}_{az_looks}x{rg_looks}"
    scratch = case / "scratch" / "trackR" / f"{ref}_{sec}_{tag}"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"
    work.mkdir(exist_ok=True)

    if not scratch.is_dir():
        print(f"ERROR: no Track R scratch at {scratch}", file=sys.stderr)
        return 2

    ref_slc = scratch / "crossmul" / f"freq{FREQ}" / POL / "reference.slc"
    coreg_slc = scratch / "fine_resample_slc" / f"freq{FREQ}" / POL / "coregistered_secondary.slc"
    lon_rdr = scratch / "rdr2geo" / f"freq{FREQ}" / "x.rdr"
    lat_rdr = scratch / "rdr2geo" / f"freq{FREQ}" / "y.rdr"
    for p in (ref_slc, coreg_slc, lon_rdr, lat_rdr):
        if not p.exists():
            print(f"ERROR: missing {p}", file=sys.stderr)
            return 2

    stack = json.loads((case / "stack.json").read_text())
    raw_h5 = next(g["path"] for g in stack["granules"] if g["date"] == sec)

    d = gdal.Open(str(ref_slc))
    ROWS, COLS = d.RasterYSize, d.RasterXSize
    d = None
    # the raw secondary is one column narrower; clip everything to the common width
    with h5py.File(raw_h5, "r") as h:
        raw_shape = h[f"/science/LSAR/RSLC/swaths/frequency{FREQ}/{POL}"].shape
    COLS = min(COLS, raw_shape[1])
    ROWS = min(ROWS, raw_shape[0])
    print(f"radar grid {ROWS} x {COLS}, looks {az_looks} x {rg_looks} "
          f"-> {ROWS // az_looks} x {COLS // rg_looks}")

    # ---- geolocation, multilooked with the same boxes as the amplitude ----
    print("multilooking geolocation arrays")
    lon_tif, lat_tif = work / "lon.tif", work / "lat.tif"
    if lon_tif.exists() and lat_tif.exists() and args.reuse:
        print("  reusing existing lon/lat rasters")
        lon = gdal.Open(str(lon_tif)).ReadAsArray()
        lat = gdal.Open(str(lat_tif)).ReadAsArray()
    else:
        lon = multilook_geoloc(lon_rdr, ROWS, COLS, az_looks, rg_looks, "lon")
        lat = multilook_geoloc(lat_rdr, ROWS, COLS, az_looks, rg_looks, "lat")
        write_tif(lon_tif, lon, gdal.GDT_Float64)
        write_tif(lat_tif, lat, gdal.GDT_Float64)

    # The output extent MUST be stated, not left to GDAL -- see geocode().
    geo_bounds = (float(np.nanmin(lon)), float(np.nanmin(lat)),
                  float(np.nanmax(lon)), float(np.nanmax(lat)))
    # ground cell -> a degree posting that does not throw resolution away
    res_deg = abs(float(np.nanmedian(np.diff(lat[:, lat.shape[1] // 2]))))
    res_deg = max(res_deg, 1e-5)
    del lon, lat
    print(f"  geocode posting {res_deg:.6f} deg (~{res_deg * 111320:.0f} m)")
    print(f"  geocode bounds  lon [{geo_bounds[0]:.4f}, {geo_bounds[2]:.4f}]  "
          f"lat [{geo_bounds[1]:.4f}, {geo_bounds[3]:.4f}]")

    sources = [
        ("reference",   f"{ref} reference (never resampled)",      str(ref_slc),   None),
        ("coregistered", f"{sec} secondary, COREGISTERED",          str(coreg_slc), None),
        ("uncoregistered", f"{sec} secondary, RAW (no coregistration)",
         f"/science/LSAR/RSLC/swaths/frequency{FREQ}/{POL}", raw_h5),
    ]

    geo_tifs, arrays = [], {}
    for key, label, spec, h5p in sources:
        amp_tif = work / f"{key}_radar.tif"
        if amp_tif.exists() and args.reuse:
            print(f"[{key}] reusing multilooked amplitude")
        else:
            print(f"[{key}] multilooking amplitude -> dB")
            src = SlcSource(spec, h5p)
            db = multilook_db(src, ROWS, COLS, az_looks, rg_looks, key)
            src.close()
            write_tif(amp_tif, db, gdal.GDT_Float32)
            del db
        print(f"[{key}] geocoding via geolocation arrays")
        gt = geocode(amp_tif, lon_tif, lat_tif, work / f"{key}_geo.tif",
                     res_deg, geo_bounds)
        geo_tifs.append((key, label, gt))

    # ---- one shared 3857 grid ----
    grid = mercator_grid(geo_tifs[0][2], args.max_px)
    bounds_m, W, H, latlon_bounds = grid
    print(f"3857 grid {W} x {H}; bounds {latlon_bounds}")

    for key, label, gt in geo_tifs:
        arrays[key] = warp_to_grid(gt, grid)

    # ---- pooled stretch across all three ----
    #
    # THE FILL FLOOR. Exactly-zero SLC samples give 10*log10(0) = -inf and are
    # already NaN, but the fill region is not exactly zero after resampling:
    # fine_resample's sinc kernel rings across the data/fill boundary and leaves
    # values around 1e-7, i.e. about -134 dB. Those are finite, so a naive
    # percentile over "finite" pixels puts vmin at -134 and washes the whole
    # image out. They are numerically nonzero and physically nothing.
    #
    # The floor is taken RELATIVE to the scene median rather than as a fixed dB
    # number, so it travels to other scenes and other calibrations. 40 dB below
    # the median is 10,000x down in power -- far below radar shadow or calm
    # water, which sit ~20-30 dB below median, and far above the ringing.
    pool_all = np.concatenate([a[np.isfinite(a)][::37] for a in arrays.values()])
    med = float(np.median(pool_all))
    floor = med - args.floor_db
    pool = pool_all[pool_all > floor]
    print(f"scene median {med:.2f} dB -> fill floor {floor:.2f} dB "
          f"({100 * (pool_all <= floor).mean():.2f}% of finite pixels discarded)")
    vmin, vmax = (float(x) for x in np.percentile(pool, [2.0, 98.0]))
    print(f"pooled dB stretch: {vmin:.2f} .. {vmax:.2f}")
    del pool, pool_all

    # Below the floor is not dark data, it is absence -- render it transparent.
    for k in arrays:
        below = np.isfinite(arrays[k]) & (arrays[k] <= floor)
        if below.any():
            print(f"  [{k}] {100 * below.mean():.2f}% below floor -> transparent")
            arrays[k][below] = np.nan

    layers = []
    for key, label, _ in geo_tifs:
        png = out_dir / f"{key}.png"
        layer_png(arrays[key], png, vmin, vmax)
        mb = png.stat().st_size / 1e6
        print(f"  {png.name}  {mb:.1f} MB")
        layers.append({"name": label, "file": png.name})
    del arrays

    meta = {
        "track / frame": f"{stack['track']} / {stack['frame']}",
        "pass": f"{stack['orbit_pass_direction']}, look {stack['look_direction']}",
        "frequency": f"{FREQ} (5 MHz)",
        "polarization": f"{POL} (co-pol)",
        "looks (az x rg)": f"{az_looks} x {rg_looks}",
        "reference": ref,
        "secondary": sec,
        "grid": f"{W} x {H} EPSG:3857",
        "units": "dB, 10*log10(&lt;|z|&sup2;&gt;)",
    }
    note = (f"Polarization is <b>{POL}</b>, the co-pol channel of this dual-pol "
            f"<b>DHDH</b> (HH + HV) acquisition. All three layers use one pooled "
            f"stretch, so grey level means the same dB in each.<br><br>"
            f"The <b>uncoregistered</b> layer is drawn through the <i>reference's</i> "
            f"geolocation on purpose: that is the no-coregistration assumption, so it "
            f"sits ~3.3 km off in azimuth. Toggle it against the coregistered layer to "
            f"see what coregistration corrected.")
    title = f"NISAR T{stack['track']}F{stack['frame']} &middot; {POL} amplitude (dB)"
    (out_dir / "manifest.json").write_text(json.dumps(
        {"layers": layers, "bounds": latlon_bounds, "vmin": vmin, "vmax": vmax,
         "meta": meta, "title": title, "note": note}, indent=2), encoding="utf-8")
    html = write_html(out_dir, layers, latlon_bounds, vmin, vmax, meta, title, note)
    print(f"wrote {html}")

    if not args.keep_tif:
        shutil.rmtree(work, ignore_errors=True)
    print(f"\nfolder ready: {out_dir}")
    for p in sorted(out_dir.iterdir()):
        print(f"  {p.name}  {p.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
