#!/usr/bin/env python3
"""
gslc_amp.py -- per-date multilooked amplitude from ONE GSLC, on the exact
same grid gslc_igram.py writes.

    python3 gslc_amp.py --gslc 20260613_gslc_freqB.h5 --freq B --pol HH \\
        --looks 16 2 --out amp_B_HH_20260613

Why this exists
---------------
The interferogram's `.amp` band is  sqrt(sqrt(P1*P2)/n)  -- the GEOMETRIC MEAN
of the two dates.  It is one raster for the pair, so it cannot answer "what did
this pixel look like on the 13th vs the 25th".  For a per-date backscatter layer
the only honest source is each GSLC's own raster, multilooked the same way.

Grid arithmetic is copied verbatim from gslc_igram.py:

    oy, ox = ny // ry, nx // rx
    gt = (x[0] - px/2, px*rx, 0, y[0] - py/2, 0, py*ry)

so the output is bit-identical in shape and geotransform to
`<pair>.igram/.coh/.amp/.nlooks.tif`.  Do not "improve" it independently --
every overlay layer has to land on the same pixel grid or the folium stack
will not register.

Estimator
---------
Amplitude is the square root of the MEAN POWER over the look box, not the mean
of the amplitudes:

    A = sqrt( sum(|s|^2) / n_valid )

Averaging |s| instead biases low and is not the multilook speckle estimator.
Invalid samples (NISAR fill is NaN+NaNj; geocode writes exact 0 outside the
swath) are excluded from BOTH the sum and the count, so a look box that
straddles the swath edge is normalised by the samples it actually had, and a
box with no valid samples comes out NaN rather than 0.

Memory
------
Streams by row block: peak is ~ block_rows * nx * 8 bytes for the complex read
plus the same again for the float32 power.  Measured on the 59800 x 7575 freq-B
GSLC: block_rows=1024 -> 15 s and 309 MB peak RSS per date.  Never reads the
whole raster (that would be 3.4 GiB, and this box has ~2.4 GB free under load).

`block_rows` should stay a multiple of BOTH the look factor `ry` AND the HDF5
chunk row size (512 here) -- otherwise every block straddles a chunk boundary
and gzip re-inflates the same chunk twice.
"""
import argparse
import os

import numpy as np


def _as_c8(a):
    """GSLC may be complex64 or the NISAR complex32 (2 x float16) compound."""
    if a.dtype == np.complex64 or a.dtype == np.complex128:
        return a.astype(np.complex64)
    if a.dtype.names and set(a.dtype.names) >= {"r", "i"}:
        return (a["r"].astype(np.float32)
                + 1j * a["i"].astype(np.float32)).astype(np.complex64)
    return a.astype(np.complex64)


def run(gslc, freq, pol, ry, rx, out_path, block_rows=1024):
    import h5py
    from osgeo import gdal, osr
    gdal.UseExceptions()

    h = h5py.File(gslc, "r")
    p = f"/science/LSAR/GSLC/grids/frequency{freq}"
    if pol not in h[p]:
        avail = [k for k in h[p].keys() if h[p][k].ndim == 2]
        raise SystemExit(
            f"polarization {pol!r} is not in {gslc}\n"
            f"  frequency{freq} carries: {avail}\n"
            f"  (the RSLC may be quad/dual-pol; the GSLC only has what the "
            f"runconfig asked geocode to produce)")
    d = h[f"{p}/{pol}"]
    x = h[f"{p}/xCoordinates"][:]
    y = h[f"{p}/yCoordinates"][:]
    try:
        epsg = int(h[f"{p}/projection"][()])
    except Exception:
        epsg = int(h[f"{p}/projection"].attrs.get("epsg_code", 0))

    ny, nx = d.shape
    oy, ox = ny // ry, nx // rx
    px, py = float(x[1] - x[0]), float(y[1] - y[0])
    gt = (float(x[0]) - px / 2, px * rx, 0.0,
          float(y[0]) - py / 2, 0.0, py * ry)

    srs = osr.SpatialReference(); srs.ImportFromEPSG(epsg)
    drv = gdal.GetDriverByName("GTiff")
    r = drv.Create(out_path, ox, oy, 1, gdal.GDT_Float32,
                   ["COMPRESS=DEFLATE", "ZLEVEL=1", "TILED=YES",
                    "BIGTIFF=IF_SAFER"])
    r.SetGeoTransform(gt)
    r.SetProjection(srs.ExportToWkt())

    blk = max(ry, (block_rows // ry) * ry)
    for r0 in range(0, oy * ry, blk):
        r1 = min(r0 + blk, oy * ry)
        a = _as_c8(d[r0:r1, :ox * rx])
        good = np.isfinite(a) & (a != 0)
        pw = np.where(good, a.real.astype(np.float32) ** 2
                      + a.imag.astype(np.float32) ** 2, 0.0)
        m = (r1 - r0) // ry
        s = pw.reshape(m, ry, ox, rx).sum(axis=(1, 3))
        c = good.reshape(m, ry, ox, rx).sum(axis=(1, 3))
        with np.errstate(invalid="ignore", divide="ignore"):
            amp = np.where(c > 0, np.sqrt(s / np.maximum(c, 1)), np.nan)
        r.GetRasterBand(1).WriteArray(amp.astype(np.float32), 0, r0 // ry)

    r.GetRasterBand(1).SetNoDataValue(float("nan"))
    r.FlushCache()
    r = None
    h.close()
    print(f"{out_path}  {ox} x {oy}  posting {px*rx:g} x {abs(py*ry):g} m  "
          f"EPSG:{epsg}  origin ({gt[0]:.1f}, {gt[3]:.1f})")
    return dict(width=ox, length=oy, gt=gt, epsg=epsg)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gslc", required=True)
    ap.add_argument("--freq", default="B")
    ap.add_argument("--pol", default="HH")
    ap.add_argument("--looks", nargs=2, type=int, default=[16, 2],
                    metavar=("AZ", "RG"),
                    help="row (y) and column (x) block factors; must match "
                         "the ones gslc_igram.py was run with")
    ap.add_argument("--block-rows", type=int, default=1024)
    ap.add_argument("--out", required=True, help="output .tif path")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    run(a.gslc, a.freq, a.pol, a.looks[0], a.looks[1], a.out, a.block_rows)
