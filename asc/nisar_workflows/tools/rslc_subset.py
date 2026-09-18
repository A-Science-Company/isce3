#!/usr/bin/env python3
"""
Crop a NISAR L1 RSLC granule to an AOI, producing a smaller but fully valid RSLC.

    python tools/rslc_subset.py \
        --rslc L1_RSLC/NISAR_..._20260714T233920_..._001.h5 \
        --out  L1_RSLC_AOI/20260714_aoi.h5 \
        --kml  /home/sharath/asf_slc/glof_exact_aoi.kml \
        --dem  aux/dem/dem_nepal_glof.tif \
        --polarizations HH          # aligned, zero-Doppler, physical buffers by default

WHY THIS EXISTS
---------------
ISCE3 has NO way to restrict the RADAR-DOMAIN extent of the InSAR workflow.
Verified across the installed source: every stage sizes itself from
`slc.getRadarGrid(freq)` and runs the full grid --

    rdr2geo.py:81          radargrid = slc.getRadarGrid(freq)
    resample_slc_v2.py:168  out_length = ref_radar_grid.length
    crossmul.py:127-130     ifg sized from ref_radar_grid // looks
    InSAR_L1_writer.py:277  RIFG/RUNW sized from the raw RSLC dataset shape

The one documented AOI option, `processing.geocode.top_left/bottom_right`
(schemas/insar.yaml:1138-1149, consumed at geogrid.py:84-110 ->
geocode_insar.py:633-636), restricts the GEOCODED OUTPUT only. It is
"process full, then crop" executed inside the SAS -- it saves the geocode tail,
not the expensive body. `processing.input_subset` is frequency/polarization
only. `dense_offsets.start_pixel_range/azimuth` windows the offsets stages
alone.

So "crop first" has exactly one entry point: make the RSLC itself smaller.
Because every stage derives its extent from the granule's own metadata, a
correctly-built subset granule makes the entire chain run small with NO
workflow changes at all.

WHY THIS IS SAFE -- what does and does not depend on the image grid
-------------------------------------------------------------------
Checked on a real granule: NOTHING under /science/LSAR/RSLC/metadata has an axis
of 53200 / 54244 / 6781. The geolocationGrid is a coarse COORDINATE-REFERENCED
cube (20 heights x 540 az x 349 rg) carrying its own slantRange and
zeroDopplerTime axes, so it stays valid under an image crop as long as the crop
lies inside it -- which it must, since it spans the scene. Orbit and attitude
are time-referenced and likewise unaffected.

That leaves a short, closed list of things to change:

  swaths/frequency{A,B}/<pol>             crop [az0:az1, rg0:rg1]
  swaths/frequency{A,B}/inputDataExceptionMask   same crop
  swaths/frequency{A,B}/slantRange        crop [rg0:rg1]
  swaths/frequency{A,B}/validSamplesSubSwath*   crop rows, SHIFT values by -rg0
  swaths/zeroDopplerTime                  crop [az0:az1]
  identification/zeroDopplerStartTime/EndTime   recompute
  identification/boundingPolygon          recompute

THE AZIMUTH WINDOW MUST BE SHARED BY BOTH FREQUENCIES
-----------------------------------------------------
`zeroDopplerTime` lives at swaths/ level, ABOVE frequencyA and frequencyB, so
the two bands share one azimuth axis by construction. Cropping them to
different azimuth windows would silently desynchronise the bands and corrupt the
split-spectrum ionosphere solve, which requires them pixel-aligned. This tool
therefore takes the UNION of the per-frequency azimuth brackets and applies it
to both. Range is cropped per frequency, because each band has its own
slantRange axis (freq A is 8x freq B in range here).

FREQUENCY A/B RANGE ALIGNMENT -- a bug in the first version of this tool
-----------------------------------------------------------------------
On NISAR DHDH granules frequency B is an exact 8:1 decimation of frequency A in
range: both start at the same slant range and slantRangeA[8k] == slantRangeB[k]
(verified on both dates here). Every range-dependent relation between the bands
therefore rests on the crop keeping rg0_B == rg0_A / 8.

ISCE3 depends on this silently. The main_side_band ionosphere builds the
frequency-B pair's offsets by dividing the frequency-A range offsets by 8, using
ONLY the reference granule's A/B slant ranges
(nisar/workflows/ionosphere.py:122-237, decimate_freq_a_offset). It assumes every
date has the same A-to-B range-origin relation.

The first version of this tool buffered each band's range window independently.
For this pair that gave the reference B origin 855 against A 10697, and the
secondary B 854 against A 10684 -- A-to-B relations of 482.125 and 481.500 B
pixels. ISCE3 therefore mis-registered frequency B by 0.625 B pixels (15.6 m):
freq-B coherence fell from 0.573 to 0.428, and the wrong flattening put a
non-integer +1.75 rad (measured; the flattening error alone predicts +1.64) on the
B interferogram, worth about -1.49 TECU in the ionosphere screen. Frequency-A
WRAPPED phase and coherence were unaffected (coherence 0.522 in both runs); the
freq-A UNWRAPPED phase did differ from the full tile by exactly -1 cycle, which is
the separate absolute-ambiguity problem. Found by adversarial verification of the
cropped-RSLC ionosphere result.

ZERO DOPPLER -- a second bug in the first version
-------------------------------------------------
The crop window and footprint polygon were computed with the NATIVE Doppler
centroid (getDopplerCentroid, ~957-983 Hz). NISAR RSLCs are zero-Doppler
products: ISCE3's own rdr2geo.py:64-65 and geo2rdr.py:52-53 pass an empty
LUT2d() with the comment "NISAR RSLC products are always zero doppler". With the
native Doppler the AOI centre maps ~3300 lines (~15 km) too early in azimuth:
the crop covered lines 5012..15231 where the AOI needs 8262..18556, missing the
north of the AOI (its NW third was 7.5% covered) and wasting the south. Both the
window and the polygon now use zero Doppler.

The fix, now the default: ONE range window is computed on the finest band,
snapped to a multiple of lcm(band ratio, range looks), and every other band's
window is that window divided by its ratio exactly -- checked against the
slant-range arrays. The azimuth origin is snapped to a multiple of the azimuth
looks, so the cropped 9x8 unwrap/ionosphere grid is pixel-aligned with the full
tile's (the legacy crop was sub-look misaligned by 3 rows and 1 column).
`--no-align` reproduces the legacy crop for provenance only.

BUFFER -- set by the WIDEST spatial operator in the chain, not by coregistration
-------------------------------------------------------------------------------
Every stage with spatial support degrades pixels near a crop edge:

    dense_offsets  window 64 + half_search 20  -> ~52 px dead band
    rubbersheet    filters the offset field
    resample       interpolation kernel (~8 px)
    ionosphere     Gaussian 100 px kernel / 33 px sigma on ISCE3's decimated grid
                   = ~20 km x 4 km kernel, ~6.6 km sigma in range

Coregistration alone would be satisfied by ~500 px. The ionosphere filter is the
binding constraint, so the buffers are physical: --buffer-range-m 12500 (slant,
~2 sigma; = 500 freq-B px = ~4000 freq-A px) and --buffer-az-lines 1000 (~4.5 km,
one kernel width). For a crop that stops at the interferogram, far smaller
buffers suffice. Compare only the AOI interior either way.

HEIGHT BRACKET
--------------
`get_radar_bbox` defaults to the global -500..9000 m bracket. This tool reads the
actual DEM min/max over the AOI instead (here 1295..7892 m). Measured effect on
this AOI is modest -- the default widens the freq-A footprint by ~5.6% (4.96% vs
4.70% of the grid) -- but it is free and honest.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

_ARGV = sys.argv[1:]
sys.argv = [sys.argv[0]]

import h5py  # noqa: E402
import isce3  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402

gdal.UseExceptions()
ogr.UseExceptions()

RSLC = "/science/LSAR/RSLC"
SWATHS = f"{RSLC}/swaths"
IDENT = "/science/LSAR/identification"


def aoi_from_kml(kml: str) -> tuple[float, float, float, float, object]:
    """Return (xmin, xmax, ymin, ymax) in EPSG:4326 plus the geometry."""
    ds = ogr.Open(kml)
    if ds is None:
        raise SystemExit(f"cannot open {kml}")
    lyr = ds.GetLayer(0)
    geom = None
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        geom = g.Clone() if geom is None else geom.Union(g)
    if geom is None:
        raise SystemExit(f"{kml} has no geometry")
    e = geom.GetEnvelope()
    return e[0], e[1], e[2], e[3], geom


def dem_bracket(dem: str, lon0, lon1, lat0, lat1, pad=0.02) -> tuple[float, float]:
    """Actual DEM min/max over the AOI -- far tighter than -500..9000."""
    d = gdal.Open(dem)
    gt = d.GetGeoTransform()
    c0 = max(0, int((lon0 - pad - gt[0]) / gt[1]))
    c1 = min(d.RasterXSize, int((lon1 + pad - gt[0]) / gt[1]) + 1)
    r0 = max(0, int((gt[3] - (lat1 + pad)) / abs(gt[5])))
    r1 = min(d.RasterYSize, int((gt[3] - (lat0 - pad)) / abs(gt[5])) + 1)
    a = d.GetRasterBand(1).ReadAsArray(c0, r0, c1 - c0, r1 - r0).astype(float)
    nod = d.GetRasterBand(1).GetNoDataValue()
    m = np.isfinite(a) & (a > -1000)
    if nod is not None:
        m &= (a != nod)
    if not m.any():
        raise SystemExit("DEM has no valid samples over the AOI")
    return float(a[m].min()), float(a[m].max())


def radar_bounds(slc, freq: str, geo, orbit, hmin, hmax, margin):
    # ZERO Doppler. NISAR RSLCs are focused to zero-Doppler geometry -- ISCE3's
    # own rdr2geo.py:64-65 and geo2rdr.py:52-53 pass an empty LUT2d() with the
    # comment "NISAR RSLC products are always zero doppler". The native Doppler
    # centroid (getDopplerCentroid, ~957-983 Hz here) is for carrier handling
    # during resampling, NOT for geometry. The first version of this tool passed
    # the native Doppler, which slid the crop ~3300 lines (~15 km) along track:
    # it missed the north of the AOI (NW third only 7.5% covered) and wasted the
    # south.
    dop = isce3.core.LUT2d()
    rg = slc.getRadarGrid(freq)
    bb = isce3.geometry.get_radar_bbox(geo, rg, orbit, min_height=hmin,
                                       max_height=hmax, doppler=dop,
                                       margin=margin)
    return (bb.first_azimuth_line, bb.last_azimuth_line,
            bb.first_range_sample, bb.last_range_sample, rg)


def copy_attrs(src, dst):
    for k, v in src.attrs.items():
        dst.attrs[k] = v


def crop_dataset(src_grp, dst_grp, name, az=None, rg=None, block=2048, log=print):
    """Copy one dataset, optionally cropping axis 0 by `az` and axis 1 by `rg`."""
    src = src_grp[name]
    if az is None and rg is None:
        src_grp.copy(name, dst_grp)
        return
    if src.ndim == 1:
        sl = az if az is not None else rg
        data = src[sl[0]:sl[1]]
        d = dst_grp.create_dataset(name, data=data, dtype=src.dtype)
        copy_attrs(src, d)
        return
    a0, a1 = az
    r0, r1 = rg
    out_shape = (a1 - a0, r1 - r0)
    chunks = src.chunks if src.chunks else None
    if chunks:
        chunks = tuple(min(c, s) for c, s in zip(chunks, out_shape))
    d = dst_grp.create_dataset(name, shape=out_shape, dtype=src.dtype,
                               chunks=chunks,
                               compression=src.compression,
                               compression_opts=src.compression_opts)
    copy_attrs(src, d)
    nb = math.ceil((a1 - a0) / block)
    for i in range(nb):
        s = a0 + i * block
        e = min(a1, s + block)
        d[s - a0:e - a0, :] = src[s:e, r0:r1]
        if nb > 1 and (i % max(1, nb // 10) == 0 or i == nb - 1):
            log(f"      {name}: {100 * (e - a0) / (a1 - a0):5.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rslc", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--kml", default=None)
    ap.add_argument("--bbox", nargs=4, type=float, default=None,
                    metavar=("LON0", "LON1", "LAT0", "LAT1"))
    ap.add_argument("--dem", required=True)
    ap.add_argument("--buffer-az-lines", type=int, default=1000,
                    help="azimuth buffer in lines on every side (default 1000, ~4.5 km: "
                         "covers ISCE3's ionosphere Gaussian kernel of 100 px x ~40 m "
                         "on its decimated grid)")
    ap.add_argument("--buffer-range-m", type=float, default=12500.0,
                    help="range buffer in SLANT metres on every side (default 12500, "
                         "~2 sigma of ISCE3's ionosphere Gaussian, 33 px x ~200 m on "
                         "the freq-B grid; = 500 freq-B px)")
    ap.add_argument("--buffer", type=int, default=500,
                    help="extra pixels on every side (default 500, ~5x the "
                         "dense_offsets dead band)")
    ap.add_argument("--margin", type=int, default=50,
                    help="get_radar_bbox internal margin (default 50)")
    ap.add_argument("--frequencies", nargs="+", default=None,
                    help="default: all present in the granule")
    ap.add_argument("--polarizations", nargs="+", default=None,
                    help="default: all present. Restricting these also rewrites "
                         "listOfPolarizations so the product stays self-consistent.")
    ap.add_argument("--bounds", nargs=4, type=int, default=None,
                    metavar=("AZ0", "AZ1", "RG0_A", "RG1_A"),
                    help="force the frequency-A window; B is scaled by the "
                         "range-sample ratio. Use to make two dates share a "
                         "window exactly.")
    ap.add_argument("--align-rg-looks", type=int, default=8,
                    help="snap the range origin to a multiple of lcm(band ratio, this); "
                         "set to the unwrap/ionosphere range looks (default 8)")
    ap.add_argument("--align-sideband-looks", type=int, default=8,
                    help="range looks ISCE3 applies to the freq-B side band (= phase_unwrap "
                         "range looks). The range origin is snapped to a multiple of "
                         "ratio x this (64 here) so the side-band look cells align with the "
                         "full tile too. v2 crops (made before this option) snapped to 8 only.")
    ap.add_argument("--align-az-looks", type=int, default=9,
                    help="snap the azimuth origin to a multiple of this; set to the "
                         "unwrap/ionosphere azimuth looks (default 9)")
    ap.add_argument("--no-align", action="store_true",
                    help="LEGACY: crop each band's range independently. Corrupts "
                         "ISCE3's freq-B side-band offsets; for reproduction only.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(_ARGV)

    from nisar.products.readers import SLC

    src_path = Path(args.rslc)
    out_path = Path(args.out)
    if out_path.exists():
        raise SystemExit(f"{out_path} exists; refusing to overwrite")

    # ---- AOI ---------------------------------------------------------------
    if args.kml:
        lon0, lon1, lat0, lat1, _ = aoi_from_kml(args.kml)
    elif args.bbox:
        lon0, lon1, lat0, lat1 = args.bbox
    else:
        raise SystemExit("need --kml or --bbox")
    print(f"AOI lon {lon0:.5f}..{lon1:.5f}  lat {lat0:.5f}..{lat1:.5f}")

    hmin, hmax = dem_bracket(args.dem, lon0, lon1, lat0, lat1)
    print(f"AOI DEM bracket {hmin:.0f}..{hmax:.0f} m "
          f"(vs the -500..9000 default, which over-widens range in relief)")

    # AOI -> UTM geogrid for get_radar_bbox
    s = osr.SpatialReference(); s.ImportFromEPSG(4326)
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    zone = int((lon0 + lon1) / 2 // 6) + 31
    epsg = 32600 + zone if (lat0 + lat1) / 2 >= 0 else 32700 + zone
    t = osr.SpatialReference(); t.ImportFromEPSG(epsg)
    t.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(s, t)
    pts = [tr.TransformPoint(x, y)[:2] for x in (lon0, lon1) for y in (lat0, lat1)]
    xmin = min(p[0] for p in pts); xmax = max(p[0] for p in pts)
    ymin = min(p[1] for p in pts); ymax = max(p[1] for p in pts)
    geo = isce3.product.GeoGridParameters(
        start_x=xmin, start_y=ymax, spacing_x=5.0, spacing_y=-5.0,
        width=max(1, int((xmax - xmin) / 5)), length=max(1, int((ymax - ymin) / 5)),
        epsg=epsg)
    print(f"AOI in EPSG:{epsg}: x {xmin:.0f}..{xmax:.0f}  y {ymin:.0f}..{ymax:.0f}")

    slc = SLC(hdf5file=str(src_path))
    orbit = slc.getOrbit()

    with h5py.File(src_path, "r") as h:
        freqs = args.frequencies or [k[-1] for k in sorted(h[SWATHS].keys())
                                     if k.startswith("frequency")]
        widths = {f: h[f"{SWATHS}/frequency{f}/slantRange"].shape[0] for f in freqs}
        n_lines = h[f"{SWATHS}/zeroDopplerTime"].shape[0]

    # ---- radar bounds ------------------------------------------------------
    per_freq = {}
    az_lo, az_hi = [], []
    for f in freqs:
        a0, a1, r0, r1, rg = radar_bounds(slc, f, geo, orbit, hmin, hmax, args.margin)
        per_freq[f] = [a0, a1, r0, r1]
        az_lo.append(a0); az_hi.append(a1)
        print(f"freq {f}: az {a0}..{a1}  rg {r0}..{r1}  "
              f"({(a1 - a0) * (r1 - r0) / 1e6:.1f} Mpx = "
              f"{100 * (a1 - a0) * (r1 - r0) / (rg.length * rg.width):.2f}% of "
              f"{rg.length}x{rg.width})")

    # zeroDopplerTime is SHARED by both frequencies -- one azimuth window only.
    AZ0 = max(0, min(az_lo) - args.buffer)
    AZ1 = min(n_lines, max(az_hi) + args.buffer)
    if args.bounds:
        AZ0, AZ1 = args.bounds[0], args.bounds[1]
        per_freq[freqs[0]][2], per_freq[freqs[0]][3] = args.bounds[2], args.bounds[3]
        base = freqs[0]
        for f in freqs[1:]:
            sc = widths[f] / widths[base]
            per_freq[f][2] = int(round(args.bounds[2] * sc))
            per_freq[f][3] = int(round(args.bounds[3] * sc))
        print(f"FORCED bounds: az {AZ0}..{AZ1}")

    print(f"\nlegacy azimuth window (union + --buffer {args.buffer} px; the aligned "
          f"mode below recomputes it with physical buffers): "
          f"{AZ0}..{AZ1}  ({AZ1 - AZ0} lines of {n_lines})")

    windows = {}
    if args.no_align:
        # LEGACY behaviour, kept only to reproduce the first cropped run. Each
        # frequency's range window is buffered independently, so the freq-B
        # origin is NOT tied to the freq-A origin -- see "FREQUENCY A/B RANGE
        # ALIGNMENT" in the module docstring for why that corrupts the ionosphere.
        print("WARNING: --no-align reproduces the legacy crop, whose freq-B range "
              "origin is independent of freq A; ISCE3's side-band offsets will be "
              "wrong unless every date happens to share the same A/B relation.")
        for f in freqs:
            _, _, r0, r1 = per_freq[f]
            if not args.bounds:
                r0 = max(0, r0 - args.buffer)
                r1 = min(widths[f], r1 + args.buffer)
            windows[f] = (AZ0, AZ1, r0, r1)
    else:
        with h5py.File(src_path, "r") as h:
            sr = {f: h[f"{SWATHS}/frequency{f}/slantRange"][()] for f in freqs}
        base = min(freqs, key=lambda f: sr[f][1] - sr[f][0])      # finest range sampling
        ratios = {}
        for f in freqs:
            q = (sr[f][1] - sr[f][0]) / (sr[base][1] - sr[base][0])
            n = int(round(q))
            if abs(q - n) > 1e-6 or abs(sr[f][0] - sr[base][0]) > 1e-3 or \
                    not np.allclose(sr[base][::n][:len(sr[f])], sr[f][:len(sr[base][::n])], atol=1e-3):
                raise SystemExit(
                    f"frequency {f} is not an integer-decimated copy of frequency {base} "
                    f"(spacing ratio {q:.9f}, start {sr[f][0]:.3f} vs {sr[base][0]:.3f}); "
                    f"aligned cropping is undefined -- use --no-align knowingly")
            ratios[f] = n
        # One range window on the base frequency that covers every band's need.
        # Buffers are PHYSICAL, chosen by the widest spatial operator in the
        # chain. Coregistration needs only ~52 px (dense_offsets window/2 +
        # half_search); the ionosphere Gaussian needs kilometres. A per-band
        # pixel buffer is what broke the A/B alignment in the first version.
        buf_rg = int(math.ceil(args.buffer_range_m / (sr[base][1] - sr[base][0])))
        AZ0 = max(0, min(az_lo) - args.buffer_az_lines)
        AZ1 = min(n_lines, max(az_hi) + args.buffer_az_lines)
        rg_lo = max(0, min([per_freq[base][2]] + [per_freq[f][2] * ratios[f] for f in freqs]) - buf_rg)
        rg_hi = min(widths[base],
                    max([per_freq[base][3]] + [per_freq[f][3] * ratios[f] for f in freqs]) + buf_rg)
        print(f"buffers: azimuth {args.buffer_az_lines} lines; range {args.buffer_range_m:g} m "
              f"slant = {buf_rg} {base}-samples")
        q_rg = math.lcm(*ratios.values(), args.align_rg_looks,
                        *[r * args.align_sideband_looks for r in ratios.values() if r > 1])
        q_az = args.align_az_looks
        RG0 = (rg_lo // q_rg) * q_rg
        RG1 = min(-(-rg_hi // q_rg) * q_rg, (widths[base] // q_rg) * q_rg)
        AZ0 = (AZ0 // q_az) * q_az
        AZ1 = min(-(-AZ1 // q_az) * q_az, (n_lines // q_az) * q_az)
        print(f"ALIGNED: range origin snapped to multiples of {q_rg} base-frequency "
              f"samples (lcm of band ratios {ratios} and range looks "
              f"{args.align_rg_looks}); azimuth to multiples of {q_az}")
        for f in freqs:
            r0, r1 = RG0 // ratios[f], RG1 // ratios[f]
            if abs(sr[f][r0] - sr[base][RG0]) > 1e-3:
                raise SystemExit(f"alignment check failed for {f}: "
                                 f"{sr[f][r0]:.4f} vs {sr[base][RG0]:.4f}")
            windows[f] = (AZ0, AZ1, r0, r1)
    for f in freqs:
        _, _, r0, r1 = windows[f]
        print(f"  freq {f}: az {AZ0}..{AZ1}  rg {r0}..{r1}  "
              f"-> {AZ1 - AZ0} x {r1 - r0} = {(AZ1 - AZ0) * (r1 - r0) / 1e6:.1f} Mpx "
              f"({100 * (AZ1 - AZ0) * (r1 - r0) / (n_lines * widths[f]):.2f}% of full)")

    if args.dry_run:
        print("\nDRY RUN -- nothing written")
        return 0

    # ---- bounding polygon, computed BEFORE any file is opened for write ----
    # slc.getRadarGrid() reopens the granule through the nisar reader. Doing that
    # while an h5py write handle is live fails with "SWMR read access flag not
    # the same for file that is already open", so the WKT must be built first.
    bounding_wkt = None
    try:
        _b = freqs[0]
        _a0, _a1, _r0, _r1 = windows[_b]
        _rg = slc.getRadarGrid(_b).offset_and_resize(_a0, _r0, _a1 - _a0, _r1 - _r0)
        _dop = isce3.core.LUT2d()      # zero Doppler, as for the window
        _dem = isce3.geometry.DEMInterpolator(float((hmin + hmax) / 2))
        _w = isce3.geometry.get_geo_perimeter_wkt(_rg, orbit, _dop, _dem)
        # get_geo_perimeter_wkt returns a DENSIFIED perimeter ~2090 chars long.
        # The stock boundingPolygon dataset is fixed-width |S2087, so writing the
        # dense string into the original dtype truncates it by 3 characters --
        # silently dropping the closing "))" and producing WKT that every parser
        # rejects with "Expected word but encountered end of stream". Simplify to
        # a corner polygon and let h5py size the dtype to the actual string.
        from shapely import wkt as _swkt
        _poly = _swkt.loads(_w)
        bounding_wkt = _poly.simplify(0.001).wkt
        print(f"  boundingPolygon computed from the cropped radar grid "
              f"({len(_w)} chars densified -> {len(bounding_wkt)} simplified)")
    except Exception as e:
        print(f"  WARNING: could not compute boundingPolygon ({e})")

    # ---- write -------------------------------------------------------------
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nwriting {out_path}")
    with h5py.File(src_path, "r") as src, h5py.File(out_path, "w") as dst:
        copy_attrs(src, dst)

        # Everything outside swaths is grid-independent -- copy verbatim.
        def copy_tree(node_path):
            grp = src[node_path]
            parent = dst.require_group(str(Path(node_path).parent))
            parent.copy(grp, Path(node_path).name)

        for top in src["/science/LSAR"].keys():
            p = f"/science/LSAR/{top}"
            if top == "RSLC":
                continue
            copy_tree(p)
        print("  copied identification + non-RSLC groups")

        dst.require_group(f"{RSLC}")
        src[RSLC].copy("metadata", dst[RSLC])
        print("  copied RSLC/metadata verbatim "
              "(geolocationGrid is coordinate-referenced, orbit is time-referenced)")

        dsw = dst.require_group(SWATHS)
        copy_attrs(src[SWATHS], dsw)
        # Provenance ON the product: a subset granule otherwise carries the source
        # granule's identity and nothing says where it was cut from.
        dsw.attrs["subset_tool"] = "asc/nisar_workflows/tools/rslc_subset.py"
        dsw.attrs["subset_source_granule"] = src_path.name
        dsw.attrs["subset_geometry_doppler"] = "zero"
        dsw.attrs["subset_band_aligned"] = int(not args.no_align)
        dsw.attrs["subset_azimuth_origin"] = int(AZ0)
        for f in freqs:
            dsw.attrs[f"subset_range_origin_frequency{f}"] = int(windows[f][2])

        for name, obj in src[SWATHS].items():
            if isinstance(obj, h5py.Dataset):
                if name == "zeroDopplerTime":
                    crop_dataset(src[SWATHS], dsw, name, az=(AZ0, AZ1))
                    print(f"  cropped zeroDopplerTime -> {AZ1 - AZ0}")
                else:
                    src[SWATHS].copy(name, dsw)

        for f in freqs:
            a0, a1, r0, r1 = windows[f]
            sg = src[f"{SWATHS}/frequency{f}"]
            dg = dsw.require_group(f"frequency{f}")
            copy_attrs(sg, dg)
            pols_present = [k for k in sg.keys()
                            if isinstance(sg[k], h5py.Dataset) and sg[k].ndim == 2
                            and sg[k].dtype.kind == "c"]
            pols = args.polarizations or pols_present
            pols = [p for p in pols if p in pols_present]
            print(f"  frequency{f}: pols {pols}  window az {a0}..{a1} rg {r0}..{r1}")
            for name, obj in sg.items():
                if not isinstance(obj, h5py.Dataset):
                    continue
                if name in pols_present:
                    if name not in pols:
                        continue
                    crop_dataset(sg, dg, name, az=(a0, a1), rg=(r0, r1))
                elif name == "inputDataExceptionMask":
                    crop_dataset(sg, dg, name, az=(a0, a1), rg=(r0, r1))
                elif name == "slantRange":
                    crop_dataset(sg, dg, name, rg=(r0, r1))
                elif name.startswith("validSamplesSubSwath"):
                    # [start, stop) range-sample bounds per azimuth line, in
                    # ABSOLUTE sample indices -- they must be re-based to the
                    # new range origin and clipped to the new width, or every
                    # downstream validity test is wrong.
                    v = obj[a0:a1, :].astype(np.int64)
                    v = np.clip(v - r0, 0, r1 - r0)
                    d = dg.create_dataset(name, data=v.astype(obj.dtype))
                    copy_attrs(obj, d)
                    print(f"      {name}: rebased by -{r0}, clipped to {r1 - r0}")
                elif name == "listOfPolarizations":
                    d = dg.create_dataset(
                        name, data=np.array([p.encode() for p in pols],
                                            dtype=obj.dtype))
                    copy_attrs(obj, d)
                else:
                    sg.copy(name, dg)

        # ---- identification: times ----------------------------------------
        zdt = dst[f"{SWATHS}/zeroDopplerTime"]
        units = zdt.attrs.get("units", b"")
        units = units.decode() if isinstance(units, bytes) else str(units)
        epoch = units.split("since")[-1].strip() if "since" in units else None
        if epoch:
            import datetime as _dt
            e = _dt.datetime.fromisoformat(epoch.replace("Z", ""))
            t0 = e + _dt.timedelta(seconds=float(zdt[0]))
            t1 = e + _dt.timedelta(seconds=float(zdt[-1]))
            for key, val in (("zeroDopplerStartTime", t0), ("zeroDopplerEndTime", t1)):
                p = f"{IDENT}/{key}"
                if p in dst:
                    old = dst[p][()]
                    dt = dst[p].dtype
                    del dst[p]
                    # Always 9 fractional digits. `isoformat()` drops the
                    # fraction entirely when microsecond == 0, and appending
                    # "000" to that produced a malformed "...:22000".
                    dst.create_dataset(
                        p, data=np.bytes_(val.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000"),
                        dtype=dt)
            print(f"  identification times -> {t0.isoformat()} .. {t1.isoformat()}")

        # ---- identification: boundingPolygon -------------------------------
        p = f"{IDENT}/boundingPolygon"
        if bounding_wkt and p in dst:
            # NOTE: do NOT reuse the source dtype -- it is fixed-width |S2087 and
            # will silently truncate. Let h5py size it to the string.
            del dst[p]
            dst.create_dataset(p, data=np.bytes_(bounding_wkt))
            print(f"  boundingPolygon -> {bounding_wkt[:60]}...")
        elif p in dst:
            print(f"  WARNING: boundingPolygon left STALE -- it still describes "
                  f"the FULL scene, which will mislead any footprint check")

    sz = out_path.stat().st_size
    print(f"\nwrote {out_path}  {sz / 1e9:.2f} GB "
          f"(source {src_path.stat().st_size / 1e9:.2f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
