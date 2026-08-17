#!/usr/bin/env python3
"""
gslc_igram.py -- TRACK 1 interferogram: conjugate product of two GSLCs that
were geocoded onto the SAME pinned geogrid, multilooked on the map grid.

    python3 gslc_igram.py --ref ref.gslc.h5 --sec sec.gslc.h5 \\
        --pol HH --freq A --looks 10 10 --out t1_50m

Design notes
------------
* Both GSLCs are already flattened: geocodeSlc multiplies each sample by
  exp(+i * 4*pi*r_k/lambda) with r_k the geo2rdr slant range to the DEM
  surface at that map cell (cxx/isce3/geocode/geocodeSlc.cpp, flattenPhase).
  So ref * conj(sec) is ALREADY topo- and ellipsoid-flattened.  There is no
  separate flattening step and no reference scene.
* Order is ref * conj(sec) to match crossmul (refSlc * conj(secSlcUpsampled),
  cxx/isce3/signal/Crossmul.cpp:306) so the two tracks share a sign convention.
* Streams by row block: peak RSS is ~ (2 * block_rows * width * 8) bytes.
  With 4 GB free, block_rows=200 on a 60k-wide grid is ~190 MB.  Fine.
* Writes float32/complex64 GeoTIFFs with the exact geotransform of the
  multilooked grid, so compare_tracks.py can assert alignment rather than
  assume it.
"""
import argparse
import os

import numpy as np


def _open_gslc(path, freq, pol):
    import h5py
    h = h5py.File(path, "r")
    p = f"/science/LSAR/GSLC/grids/frequency{freq}"
    ds = h[f"{p}/{pol}"]
    x = h[f"{p}/xCoordinates"][:]
    y = h[f"{p}/yCoordinates"][:]
    try:
        epsg = int(h[f"{p}/projection"][()])
    except Exception:
        epsg = int(h[f"{p}/projection"].attrs.get("epsg_code", 0))
    return h, ds, x, y, epsg


def _as_c8(a):
    """GSLC may be complex64 or the NISAR complex32 (2 x float16) compound."""
    if a.dtype == np.complex64 or a.dtype == np.complex128:
        return a.astype(np.complex64)
    if a.dtype.names and set(a.dtype.names) >= {"r", "i"}:
        return (a["r"].astype(np.float32)
                + 1j * a["i"].astype(np.float32)).astype(np.complex64)
    return a.astype(np.complex64)


def run(ref, sec, freq, pol, ry, rx, out_prefix, block_rows=200,
        amp_out=True):
    from osgeo import gdal, osr
    gdal.UseExceptions()

    h1, d1, x1, y1, epsg = _open_gslc(ref, freq, pol)
    h2, d2, x2, y2, e2 = _open_gslc(sec, freq, pol)

    # -------- hard alignment assertion.  Do not "handle" a mismatch: fail.
    assert d1.shape == d2.shape, f"shape {d1.shape} vs {d2.shape}"
    assert epsg == e2, f"epsg {epsg} vs {e2}"
    assert np.allclose(x1, x2, atol=1e-6) and np.allclose(y1, y2, atol=1e-6), \
        ("GSLC grids differ -- pin top_left/bottom_right/output_epsg/"
         "output_posting in BOTH runconfigs (see common_grid.py)")

    ny, nx = d1.shape
    oy, ox = ny // ry, nx // rx
    px, py = float(x1[1] - x1[0]), float(y1[1] - y1[0])
    gt = (float(x1[0]) - px / 2, px * rx, 0.0,
          float(y1[0]) - py / 2, 0.0, py * ry)

    srs = osr.SpatialReference(); srs.ImportFromEPSG(epsg)
    drv = gdal.GetDriverByName("GTiff")
    co = ["COMPRESS=DEFLATE", "ZLEVEL=1", "TILED=YES", "BIGTIFF=IF_SAFER"]

    def mk(suffix, dt):
        p = f"{out_prefix}.{suffix}.tif"
        r = drv.Create(p, ox, oy, 1, dt, co)
        r.SetGeoTransform(gt); r.SetProjection(srs.ExportToWkt())
        return r

    r_ig = mk("igram", gdal.GDT_CFloat32)
    r_co = mk("coh", gdal.GDT_Float32)
    r_n = mk("nlooks", gdal.GDT_Float32)
    r_am = mk("amp", gdal.GDT_Float32) if amp_out else None

    blk = max(ry, (block_rows // ry) * ry)
    for r0 in range(0, oy * ry, blk):
        r1 = min(r0 + blk, oy * ry)
        a = _as_c8(d1[r0:r1, :ox * rx])
        b = _as_c8(d2[r0:r1, :ox * rx])
        good = np.isfinite(a) & np.isfinite(b) & (a != 0) & (b != 0)
        a = np.where(good, a, 0); b = np.where(good, b, 0)
        m, n = (r1 - r0) // ry, ox

        def blk2(z):
            return z.reshape(m, ry, n, rx).sum(axis=(1, 3))

        num = blk2(a * np.conj(b))
        p1 = blk2((a.real.astype(np.float64) ** 2 + a.imag.astype(np.float64) ** 2))
        p2 = blk2((b.real.astype(np.float64) ** 2 + b.imag.astype(np.float64) ** 2))
        cnt = blk2(good.astype(np.float32))
        den = np.sqrt(p1 * p2)
        with np.errstate(invalid="ignore", divide="ignore"):
            coh = np.where((den > 0) & (cnt > 0), np.abs(num) / den, np.nan)
            ig = np.where(cnt > 0, num / np.maximum(cnt, 1), np.nan)
            amp = np.where(cnt > 0, np.sqrt(np.sqrt(p1 * p2) / np.maximum(cnt, 1)),
                           np.nan)
        o = r0 // ry
        r_ig.GetRasterBand(1).WriteArray(ig.astype(np.complex64), 0, o)
        r_co.GetRasterBand(1).WriteArray(coh.astype(np.float32), 0, o)
        r_n.GetRasterBand(1).WriteArray(cnt.astype(np.float32), 0, o)
        if r_am:
            r_am.GetRasterBand(1).WriteArray(amp.astype(np.float32), 0, o)

    for r in (r_ig, r_co, r_n, r_am):
        if r is not None:
            r.GetRasterBand(1).SetNoDataValue(float("nan"))
            r.FlushCache()
    h1.close(); h2.close()
    print(f"{out_prefix}.{{igram,coh,nlooks,amp}}.tif  {ox} x {oy}  "
          f"posting {px*rx:g} x {abs(py*ry):g} m  EPSG:{epsg}")
    print(f"origin (ulx,uly) = ({gt[0]:.1f}, {gt[3]:.1f})")
    return dict(width=ox, length=oy, gt=gt, epsg=epsg)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--sec", required=True)
    ap.add_argument("--freq", default="A")
    ap.add_argument("--pol", default="HH")
    ap.add_argument("--looks", nargs=2, type=int, default=[10, 10],
                    metavar=("AZ", "RG"),
                    help="row (y) and column (x) block-average factors")
    ap.add_argument("--block-rows", type=int, default=200)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    run(a.ref, a.sec, a.freq, a.pol, a.looks[0], a.looks[1], a.out,
        a.block_rows)
