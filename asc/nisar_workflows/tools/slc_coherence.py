#!/usr/bin/env python3
"""
Moving-window coherence from a coregistered RSLC pair -- the ISCE2 estimator.

    python tools/slc_coherence.py \
        --ref  <scratch>/crossmul/freqA/HH/reference.slc \
        --sec  <scratch>/fine_resample_slc/freqA/HH/coregistered_secondary.slc \
        --win 3 --out coherence_3x3.tif

WHY THIS EXISTS
---------------
ISCE3's crossmul has NO sliding-window coherence. It averages only over the
multilook box, and when both looks are 1 it does not even try
(cxx/isce3/signal/Crossmul.cpp:378-388):

    } else {
        ifgRaster.setBlock(ifgram, 0, rowStart, ncols, blockRowsData);
        // fill coherence with ones (no need to compute result)
        coherence = 1.0;

so a 1x1 RIFG/RUNW ships `coherenceMagnitude` identically 1.0. Verified on this
case: the 53200 x 54244 RUNW coherence band has min = median = max = 1.

`crossmul_options` in the installed schema exposes only range_looks,
azimuth_looks, flatten, flatten_path, coregistered_slc_path, oversample,
lines_per_block and the two common-band filters. There is no window parameter to
set. Averaging is available ONLY through looks, which decimates.

This tool restores the standard estimator, over a MOVING window, so the output
keeps the full input resolution:

    gamma[i,j] = | SUM_w  a * conj(b) |  /  sqrt( SUM_w |a|^2 * SUM_w |b|^2 )

with w a win x win box centred on each pixel. That is the same form
nisar_wf/igram.py already applies on the Track G side via `coherence_window`,
and the same one ISCE2 uses when forming an interferogram.

ESTIMATOR BIAS -- read before interpreting low coherence
--------------------------------------------------------
A magnitude-of-a-sum estimator is biased high for small N. The floor is
sqrt(pi) / (2 * sqrt(N)):

    win 3 -> N =  9  -> floor 0.295
    win 5 -> N = 25  -> floor 0.177
    win 7 -> N = 49  -> floor 0.126

So a 3x3 coherence does NOT reach 0 over fully decorrelated ground; it settles
near 0.3. Do not read "0.3 over the flood path" as partial correlation -- for a
3x3 window that IS the noise floor. The tool prints the floor for the chosen
window and writes it into the GeoTIFF metadata.

Invalid samples (zero fill, NaN) contribute nothing: they are zeroed in all
three accumulators, and because the ratio is |sum| / sqrt(sum * sum) the sample
COUNT cancels, so no separate normalisation by valid-pixel count is needed.
Pixels with no valid neighbours come out NaN.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

# Scrub argv before anything can import pyre via isce3 -- pyre parses sys.argv
# inside its package __init__ and a CLI with flags crashes it.
_ARGV = sys.argv[1:]
sys.argv = [sys.argv[0]]

from osgeo import gdal  # noqa: E402
from scipy.ndimage import uniform_filter  # noqa: E402

gdal.UseExceptions()


def box_sum(a: np.ndarray, win: int) -> np.ndarray:
    """
    Moving-window SUM over a win x win box.

    uniform_filter computes the MEAN over the window; multiplying by win**2
    recovers the sum. mode='constant', cval=0 means samples off the array edge
    contribute zero -- which is what we want, since zeros are also how invalid
    samples are represented here.
    """
    return uniform_filter(a, size=win, mode="constant", cval=0.0) * (win * win)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True, help="reference SLC (ENVI or GDAL-readable)")
    ap.add_argument("--sec", required=True, help="coregistered secondary SLC")
    ap.add_argument("--out", required=True, help="output coherence GeoTIFF (float32)")
    ap.add_argument("--win", type=int, default=3,
                    help="moving-window size, odd (default 3, ISCE2-style)")
    ap.add_argument("--block-rows", type=int, default=2048,
                    help="rows per streamed block (default 2048)")
    ap.add_argument("--also-ifg", metavar="PATH", default=None,
                    help="optionally also write the window-averaged complex "
                         "interferogram (complex64) to this path")
    args = ap.parse_args(_ARGV)

    win = int(args.win)
    if win < 1 or win % 2 == 0:
        print(f"--win must be odd and >= 1 (got {win})", file=sys.stderr)
        return 2
    half = win // 2

    ref_ds = gdal.Open(args.ref)
    sec_ds = gdal.Open(args.sec)
    L, W = ref_ds.RasterYSize, ref_ds.RasterXSize
    if (sec_ds.RasterYSize, sec_ds.RasterXSize) != (L, W):
        print(f"shape mismatch: ref {L}x{W} vs sec "
              f"{sec_ds.RasterYSize}x{sec_ds.RasterXSize}. The secondary must "
              f"already be resampled onto the reference grid.", file=sys.stderr)
        return 2

    floor = math.sqrt(math.pi) / (2.0 * math.sqrt(win * win))
    print(f"grid {L} x {W} = {L * W / 1e6:.0f} Mpx")
    print(f"window {win} x {win}  (N = {win * win}), estimator floor "
          f"sqrt(pi)/(2*sqrt(N)) = {floor:.3f}")
    print(f"output {args.out}  ({L * W * 4 / 1e9:.1f} GB float32)")

    drv = gdal.GetDriverByName("GTiff")
    opts = ["TILED=YES", "COMPRESS=DEFLATE", "ZLEVEL=1", "BIGTIFF=YES"]
    out_ds = drv.Create(args.out, W, L, 1, gdal.GDT_Float32, options=opts)
    out_band = out_ds.GetRasterBand(1)
    out_band.SetNoDataValue(float("nan"))

    ifg_band = None
    if args.also_ifg:
        ifg_ds = drv.Create(args.also_ifg, W, L, 1, gdal.GDT_CFloat32, options=opts)
        ifg_band = ifg_ds.GetRasterBand(1)

    ref_band, sec_band = ref_ds.GetRasterBand(1), sec_ds.GetRasterBand(1)
    step = max(1, int(args.block_rows))
    done = 0

    for r0 in range(0, L, step):
        n = min(step, L - r0)
        # Read with a `half`-row halo so the window is complete at block seams.
        # Without it every block boundary would get an edge-biased row.
        rr0 = max(0, r0 - half)
        rr1 = min(L, r0 + n + half)
        nr = rr1 - rr0

        a = ref_band.ReadAsArray(0, rr0, W, nr)
        b = sec_band.ReadAsArray(0, rr0, W, nr)

        # Invalid -> 0 in every accumulator, so it contributes nothing. The
        # sample count cancels in the ratio, so no count normalisation needed.
        bad = ~np.isfinite(a) | ~np.isfinite(b) | (a == 0) | (b == 0)
        if bad.any():
            a = np.where(bad, 0, a)
            b = np.where(bad, 0, b)

        ab = a * np.conj(b)
        s_re = box_sum(ab.real.astype(np.float32), win)
        s_im = box_sum(ab.imag.astype(np.float32), win)
        s_aa = box_sum((a.real.astype(np.float32) ** 2
                        + a.imag.astype(np.float32) ** 2), win)
        s_bb = box_sum((b.real.astype(np.float32) ** 2
                        + b.imag.astype(np.float32) ** 2), win)

        denom = np.sqrt(s_aa * s_bb)
        with np.errstate(divide="ignore", invalid="ignore"):
            coh = np.sqrt(s_re * s_re + s_im * s_im) / denom
        coh[denom <= 0] = np.nan
        # Numerical overshoot at the last ulp; a true value cannot exceed 1.
        np.clip(coh, 0.0, 1.0, out=coh)

        lo = r0 - rr0
        out_band.WriteArray(coh[lo:lo + n].astype(np.float32), 0, r0)
        if ifg_band is not None:
            ifg_band.WriteArray(
                (s_re[lo:lo + n] + 1j * s_im[lo:lo + n]).astype(np.complex64), 0, r0)

        done += n
        print(f"  {done}/{L} rows ({100 * done / L:.1f}%)", flush=True)

    out_ds.SetMetadata({
        "COHERENCE_WINDOW": str(win),
        "ESTIMATOR": "abs(sum(a*conj(b))) / sqrt(sum(|a|^2)*sum(|b|^2)), moving window",
        "BIAS_FLOOR": f"{floor:.4f}",
        "BIAS_FLOOR_NOTE": (
            f"sqrt(pi)/(2*sqrt({win * win})); fully decorrelated ground settles "
            f"near this value, NOT at 0"),
        "REFERENCE_SLC": args.ref,
        "SECONDARY_SLC": args.sec,
    })
    out_band.FlushCache()
    out_ds = None
    if ifg_band is not None:
        ifg_band.FlushCache()
        ifg_ds = None

    # Report from a decimated read -- never re-open the whole raster.
    d = gdal.Open(args.out)
    s = max(1, int(math.sqrt(L * W / 2e6)))
    a = d.GetRasterBand(1).ReadAsArray(buf_xsize=W // s, buf_ysize=L // s)
    g = a[np.isfinite(a)]
    print(f"\ncoherence: median {np.median(g):.4f}  "
          f"p5 {np.percentile(g, 5):.4f}  p95 {np.percentile(g, 95):.4f}")
    print(f"  >0.3 {100 * (g > 0.3).mean():.1f}%   "
          f">0.5 {100 * (g > 0.5).mean():.1f}%   "
          f"at/below floor {100 * (g <= floor * 1.05).mean():.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
