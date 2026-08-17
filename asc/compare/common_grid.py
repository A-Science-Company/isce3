#!/usr/bin/env python3
"""
common_grid.py -- force Track 1 (GSLC) and Track 2 (GUNW) onto ONE geogrid.

Why this is needed
------------------
Both workflows build their output grid with the SAME function,
nisar/workflows/geogrid.py :: create().  Read it once and the rule is simple:

  * if ANY of {output_epsg, top_left.x_abs, top_left.y_abs,
               bottom_right.x_abs, bottom_right.y_abs} is null, the grid is
    derived from isce3.product.bbox_to_geogrid(reference radar grid, orbit),
    i.e. from the DATA -- and the GSLC (one scene) and the GUNW (reference
    scene + margins) will NOT produce the same corners.
  * if ALL FIVE are set, geogrid.create takes the `else` branch:
        width  = round(|(end_x - start_x)/spacing_x|)
        length = round(|(end_y - start_y)/spacing_y|)
    and builds GeoGridParameters(start_x, start_y, spacing_x, spacing_y,
    width, length, epsg) verbatim.  Two workflows given identical five values
    and identical postings produce BIT-IDENTICAL grids.

  * x_snap / y_snap are applied AFTER that and will move your pinned corners.
    Leave BOTH null when you pin corners.  (Also: geogrid.create does
    `if x_snap <= 0 or y_snap <= 0` inside `if x_snap is not None or y_snap
    is not None` -- setting only one of the two raises TypeError on None.)

Usage
-----
    python3 common_grid.py --rslc <reference RSLC .h5> --epsg 32619 \\
        --posting 5 --coarse 50 --out gridpins
"""
import argparse
import json
import os
import re

import numpy as np


# --------------------------------------------------------------- grid pinning
def footprint_lonlat(rslc_h5):
    """Corner lon/lat from /science/LSAR/identification/boundingPolygon."""
    import h5py
    with h5py.File(rslc_h5, "r") as h:
        poly = h["/science/LSAR/identification/boundingPolygon"][()]
    if isinstance(poly, bytes):
        poly = poly.decode()
    pts = re.findall(r"(-?\d+\.?\d*) (-?\d+\.?\d*)(?: (-?\d+\.?\d*))?", poly)
    lon = np.array([float(p[0]) for p in pts])
    lat = np.array([float(p[1]) for p in pts])
    return lon, lat


def utm_epsg_for(lon, lat):
    zone = int(np.floor((np.median(lon) + 180) / 6) + 1)
    return (32600 if np.median(lat) >= 0 else 32700) + zone


def pin_grid(lon, lat, epsg, posting, margin_px=0):
    """Snapped, integer-sized bounding grid in `epsg` at `posting` metres.

    Corners are floored/ceiled to an exact multiple of `posting` so that any
    coarser grid whose posting divides evenly (50 m from 5 m, 100 m from 5 m)
    shares cell edges exactly -- no half-pixel offsets when you block-average.
    """
    from osgeo import osr
    osr.UseExceptions()
    src = osr.SpatialReference(); src.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = osr.SpatialReference(); dst.ImportFromEPSG(int(epsg))
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    xy = np.array(osr.CoordinateTransformation(src, dst).TransformPoints(
        list(zip(lon.tolist(), lat.tolist()))))
    x, y = xy[:, 0], xy[:, 1]

    m = margin_px * posting
    x0 = np.floor((x.min() - m) / posting) * posting
    x1 = np.ceil((x.max() + m) / posting) * posting
    y1 = np.ceil((y.max() + m) / posting) * posting
    y0 = np.floor((y.min() - m) / posting) * posting
    return dict(epsg=int(epsg), posting=float(posting),
                top_left_x=float(x0), top_left_y=float(y1),
                bottom_right_x=float(x1), bottom_right_y=float(y0),
                width=int(round((x1 - x0) / posting)),
                length=int(round((y1 - y0) / posting)))


def subset(grid, x0, y1, x1, y0):
    """Carve an AOI out of a pinned grid, keeping cell edges aligned."""
    p = grid["posting"]
    gx0, gy1 = grid["top_left_x"], grid["top_left_y"]
    sx0 = gx0 + np.round((x0 - gx0) / p) * p
    sy1 = gy1 - np.round((gy1 - y1) / p) * p
    sx1 = gx0 + np.round((x1 - gx0) / p) * p
    sy0 = gy1 - np.round((gy1 - y0) / p) * p
    g = dict(grid)
    g.update(top_left_x=float(sx0), top_left_y=float(sy1),
             bottom_right_x=float(sx1), bottom_right_y=float(sy0),
             width=int(round((sx1 - sx0) / p)),
             length=int(round((sy1 - sy0) / p)))
    return g


# ------------------------------------------------------------ yaml generation
_GSLC = """\
# ---- paste into the GSLC runconfig (Track 1) -------------------------------
# runconfig.groups.processing.geocode
            geocode:
                x_snap:                       # MUST stay null: snapping after
                y_snap:                       # pinning would move the corners
                output_epsg: {epsg}
                output_posting:
                    A:
                        x_posting: {posting:g}
                        y_posting: {posting:g}
                    B:
                        x_posting: {posting_b:g}
                        y_posting: {posting_b:g}
                top_left:
                    x_abs: {tlx:.1f}
                    y_abs: {tly:.1f}
                bottom_right:
                    x_abs: {brx:.1f}
                    y_abs: {bry:.1f}
# -> geogrid.create() else-branch: width={width}, length={length}
"""

_GUNW = """\
# ---- paste into the InSAR runconfig (Track 2) ------------------------------
# runconfig.groups.processing.geocode
            geocode:
                x_snap:                       # MUST stay null (see GSLC note)
                y_snap:
                output_epsg: {epsg}
                output_posting:               # -> unwrappedInterferogram grid
                    A:
                        x_posting: {coarse:g}
                        y_posting: {coarse:g}
                    B:
                        x_posting: {coarse_b:g}
                        y_posting: {coarse_b:g}
                wrapped_interferogram:        # -> wrappedInterferogram grid
                    interp_method: SINC
                    x_snap:
                    y_snap:
                    output_posting:           # SET TO THE COMPARISON POSTING.
                        A:                    # The RIFG is already multilooked
                            x_posting: {coarse:g}   # 11x11 (~52x49 m ground);
                            y_posting: {coarse:g}   # geocoding it to {posting:g} m only
                        B:                    # oversamples, then you would
                            x_posting: {coarse_b:g}   # average it straight back down.
                            y_posting: {coarse_b:g}   # Use {posting:g} m here ONLY if you
                                              # also lower crossmul looks.
                top_left:
                    x_abs: {tlx:.1f}
                    y_abs: {tly:.1f}
                bottom_right:
                    x_abs: {brx:.1f}
                    y_abs: {bry:.1f}
                lines_per_block: 1000
                interp_method: BILINEAR       # NOTE: only Track 2 has this.
                                              # NEAREST removes the smoothing
                                              # bias if you want a like-for-like
                                              # coherence-histogram comparison.
            radar_grid_cubes:
                output_epsg: {epsg}
                output_posting:
                    x_posting: 500
                    y_posting: 500
                x_snap:
                y_snap:
                top_left:
                    x_abs: {tlx:.1f}
                    y_abs: {tly:.1f}
                bottom_right:
                    x_abs: {brx:.1f}
                    y_abs: {bry:.1f}
# NOTE the top_left/bottom_right block is SHARED by output_posting and
# wrapped_interferogram; both grids therefore share the same origin and the
# wrapped grid nests exactly inside the {coarse:g} m unwrapped grid
# (({coarse:g}/{posting:g}) = {ratio:g}, integer -> exact block averaging).
"""

_WARP = """\
# ---- gdalwarp fallback (use only if you cannot re-run a workflow) ----------
# Exact target extent/size; -tap is NOT used because -te is already snapped.
TE="{tlx:.1f} {bry:.1f} {brx:.1f} {tly:.1f}"      # xmin ymin xmax ymax
TR="{coarse:g} {coarse:g}"

# phase: NEVER warp phase directly (wrap discontinuities). Warp the complex
# interferogram as two real bands, or warp cos/sin, then recombine:
gdalwarp -t_srs EPSG:{epsg} -te $TE -tr $TR -r cubic -ot Float32 \\
         -dstnodata nan cos_phi_in.tif cos_phi_{coarse:g}m.tif
gdalwarp -t_srs EPSG:{epsg} -te $TE -tr $TR -r cubic -ot Float32 \\
         -dstnodata nan sin_phi_in.tif sin_phi_{coarse:g}m.tif
# then  phi = arctan2(sin, cos)

# coherence / unwrapped phase / amplitude: `-r average` when DOWN-sampling
# (it is the only resampler that preserves the mean), `-r bilinear` otherwise.
gdalwarp -t_srs EPSG:{epsg} -te $TE -tr $TR -r average -ot Float32 \\
         -dstnodata nan coh_in.tif coh_{coarse:g}m.tif

# masks / connected components: nearest ONLY.
gdalwarp -t_srs EPSG:{epsg} -te $TE -tr $TR -r near -ot Byte \\
         mask_in.tif mask_{coarse:g}m.tif

# sanity: both outputs must report identical gdalinfo Origin / Pixel Size /
# Size lines.  Diff them, do not eyeball them:
#   diff <(gdalinfo t1.tif | sed -n '/Size is/p;/Origin/p;/Pixel Size/p') \\
#        <(gdalinfo t2.tif | sed -n '/Size is/p;/Origin/p;/Pixel Size/p')
"""


def emit(grid, coarse, posting_b=None, coarse_b=None):
    p = grid["posting"]
    posting_b = posting_b or p * 8      # freq B is 8x coarser in ground range
    coarse_b = coarse_b or coarse * 2
    ctx = dict(epsg=grid["epsg"], posting=p, posting_b=posting_b,
               coarse=coarse, coarse_b=coarse_b,
               tlx=grid["top_left_x"], tly=grid["top_left_y"],
               brx=grid["bottom_right_x"], bry=grid["bottom_right_y"],
               width=grid["width"], length=grid["length"],
               ratio=coarse / p)
    return _GSLC.format(**ctx), _GUNW.format(**ctx), _WARP.format(**ctx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rslc", required=True, help="reference RSLC h5")
    ap.add_argument("--epsg", type=int, default=None)
    ap.add_argument("--posting", type=float, default=5.0,
                    help="fine posting (m): GSLC + GUNW wrapped igram")
    ap.add_argument("--coarse", type=float, default=50.0,
                    help="comparison posting (m); must be an integer multiple "
                         "of --posting")
    ap.add_argument("--aoi", nargs=4, type=float, default=None,
                    metavar=("X0", "Y1", "X1", "Y0"),
                    help="optional AOI in output EPSG metres")
    ap.add_argument("--out", default="gridpins")
    a = ap.parse_args()

    assert abs(a.coarse / a.posting - round(a.coarse / a.posting)) < 1e-9, \
        "--coarse must be an integer multiple of --posting"

    lon, lat = footprint_lonlat(a.rslc)
    epsg = a.epsg or utm_epsg_for(lon, lat)
    g = pin_grid(lon, lat, epsg, a.posting)
    if a.aoi:
        g = subset(g, *a.aoi)

    os.makedirs(a.out, exist_ok=True)
    gs, gu, wp = emit(g, a.coarse)
    for name, txt in (("geocode_block.gslc.yaml", gs),
                      ("geocode_block.insar.yaml", gu),
                      ("gdalwarp_fallback.sh", wp)):
        open(os.path.join(a.out, name), "w").write(txt)
    json.dump(g, open(os.path.join(a.out, "grid.json"), "w"), indent=2)

    n = g["width"] * g["length"]
    print(json.dumps(g, indent=2))
    print(f"\nfine grid  : {g['width']} x {g['length']} = {n/1e6:.1f} Mpix"
          f"  ({n*8/1e9:.2f} GB complex64/pol/date)")
    nc = int(g["width"] * g["posting"] / a.coarse) * \
         int(g["length"] * g["posting"] / a.coarse)
    print(f"coarse grid: {n and int(g['width']*g['posting']/a.coarse)} x "
          f"{int(g['length']*g['posting']/a.coarse)} = {nc/1e6:.2f} Mpix"
          f"  ({nc*4/1e6:.0f} MB float32)")
    print(f"\nwrote {a.out}/{{geocode_block.gslc.yaml,geocode_block.insar.yaml,"
          f"gdalwarp_fallback.sh,grid.json}}")
    print(gs); print(gu)


if __name__ == "__main__":
    main()
