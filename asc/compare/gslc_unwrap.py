#!/usr/bin/env python3
"""
gslc_unwrap.py -- SNAPHU phase unwrapping of a gslc_igram.py pair, onto the
same grid, with the connected-component labels kept.

    python3 gslc_unwrap.py --prefix .../ifg_B_HH --nlooks 22 \\
        --ntiles 4 4 --nproc 8

Writes `<prefix>.unw.tif` (float32 radians) and `<prefix>.conncomp.tif`
(uint32 labels), both with the interferogram's exact geotransform.

nlooks is NOT the box size
--------------------------
gslc_igram.py averaged 16 x 2 = 32 samples per output cell, but the GSLC is
oversampled relative to its own resolution (freq B: 40 x 5 m posting under a
~34-46 m ground-range / ~4.5 m azimuth resolution cell), so neighbouring
samples are correlated and 32 boxcar samples are NOT 32 independent looks.

The equivalent number of looks is read off the coherence floor instead.  For
fully decorrelated signal the sample coherence magnitude has expectation
sqrt(pi)/(2*sqrt(L)); open water in this scene sits at 0.175-0.189, which
gives L ~= 22, not 32.  Passing 32 would tell SNAPHU the phase is more
trustworthy than it is and it would unwrap through noise.  Measure the floor
over a water box, then invert:  L = pi / (4 * median_coh_water**2).

Masking
-------
Only the valid swath is unwrapped -- the raster is a rotated parallelogram
inside a north-up bounding box and roughly a third of it is NaN.  SNAPHU
replaces NaN with zero silently, which would otherwise present the outside of
the swath as perfectly coherent zero-phase and let costs route through it.

Low-coherence pixels are deliberately NOT masked out.  SNAPHU's statistical
cost already down-weights them, and punching holes fragments the solution into
more connected components, each with its own independent 2*pi ambiguity.

Cost, measured
--------------
On this 3737 x 3787 pair, ntiles=(4,4)/nproc=8 unwraps all 16 tiles in about
6 minutes at ~250 MB per worker.  Then snaphu-py's DEFAULT
`single_tile_reoptimize=True` re-solves the WHOLE scene as one 14.2 Mpx tile,
single-threaded, at ~1.5 GB RSS -- that pass alone runs 20+ minutes and
dominates the wall clock.  It is what removes tile-seam discontinuities, so it
is left on by default, but `--no-reoptimize` is there when you are iterating
on colour scales rather than on the science.

Reading the output
------------------
Every connected component has an ARBITRARY integer-cycle offset.  Only pixels
sharing a label are mutually comparable; `conncomp == 0` means SNAPHU could not
place the pixel in any self-consistent region and its value is meaningless.
Display and any downstream use must mask on `conncomp > 0` and reference each
component separately (or restrict to the largest one).
"""
import argparse

import numpy as np


def run(prefix, nlooks=22.0, cost="smooth", init="mcf",
        ntiles=(4, 4), tile_overlap=256, nproc=8, scratchdir=None,
        single_tile_reoptimize=True):
    import snaphu
    from osgeo import gdal
    gdal.UseExceptions()

    ig_ds = gdal.Open(f"{prefix}.igram.tif")
    co_ds = gdal.Open(f"{prefix}.coh.tif")
    gt, proj = ig_ds.GetGeoTransform(), ig_ds.GetProjection()
    ig = ig_ds.ReadAsArray()
    co = co_ds.ReadAsArray()
    assert ig.shape == co.shape, f"{ig.shape} vs {co.shape}"

    valid = np.isfinite(ig) & np.isfinite(co)
    unw, cc = snaphu.unwrap(
        np.where(valid, ig, 0).astype(np.complex64),
        np.where(valid, co, 0).astype(np.float32),
        nlooks=float(nlooks), cost=cost, init=init,
        mask=valid.astype(np.uint8),
        ntiles=tuple(ntiles), tile_overlap=tile_overlap, nproc=nproc,
        scratchdir=scratchdir,
        single_tile_reoptimize=single_tile_reoptimize)

    unw = np.where(valid, unw, np.nan).astype(np.float32)
    cc = np.where(valid, cc, 0).astype(np.uint32)

    drv = gdal.GetDriverByName("GTiff")
    co_opts = ["COMPRESS=DEFLATE", "ZLEVEL=1", "TILED=YES", "BIGTIFF=IF_SAFER"]
    for suffix, arr, dt, nd in (("unw", unw, gdal.GDT_Float32, float("nan")),
                                ("conncomp", cc, gdal.GDT_UInt32, 0)):
        r = drv.Create(f"{prefix}.{suffix}.tif", ig.shape[1], ig.shape[0], 1,
                       dt, co_opts)
        r.SetGeoTransform(gt); r.SetProjection(proj)
        r.GetRasterBand(1).WriteArray(arr)
        r.GetRasterBand(1).SetNoDataValue(nd)
        r.FlushCache(); r = None

    lab, n = np.unique(cc[cc > 0], return_counts=True)
    order = np.argsort(n)[::-1]
    print(f"{prefix}.{{unw,conncomp}}.tif  {ig.shape[1]} x {ig.shape[0]}")
    print(f"  unwrapped fraction of valid swath: "
          f"{(cc > 0).sum() / max(valid.sum(), 1):.3f}")
    print(f"  {len(lab)} connected components; largest labels/pixels: "
          f"{[(int(lab[i]), int(n[i])) for i in order[:5]]}")
    return dict(unw=unw, conncomp=cc)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True,
                    help="path prefix, e.g. .../ifg_B_HH")
    ap.add_argument("--nlooks", type=float, default=22.0)
    ap.add_argument("--cost", default="smooth", choices=["defo", "smooth"])
    ap.add_argument("--init", default="mcf", choices=["mst", "mcf"])
    ap.add_argument("--ntiles", nargs=2, type=int, default=[4, 4])
    ap.add_argument("--tile-overlap", type=int, default=256)
    ap.add_argument("--nproc", type=int, default=8)
    ap.add_argument("--scratchdir", default=None,
                    help="SNAPHU tile scratch; point at the case scratch/ "
                         "dir if /tmp is small")
    ap.add_argument("--no-reoptimize", action="store_true",
                    help="skip the final whole-scene single-tile pass "
                         "(much faster, may leave tile-seam artefacts)")
    a = ap.parse_args()
    run(a.prefix, a.nlooks, a.cost, a.init, a.ntiles, a.tile_overlap,
        a.nproc, a.scratchdir,
        single_tile_reoptimize=not a.no_reoptimize)
