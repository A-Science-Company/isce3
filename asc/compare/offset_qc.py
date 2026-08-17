#!/usr/bin/env python3
"""
offset_qc.py -- geometric-coregistration quality, the diagnostic that says
WHICH track is wrong when the two disagree.

Track 2 (RSLC/insar.py): the rubbersheet field IS the measurement.  Read
    <scratch>/dense_offsets/freqA/<pol>/{dense_offsets,snr}     (ampcor raw)
    <scratch>/rubbersheet_offsets/freqA/<pol>/{range,azimuth}.off (culled+filled)
    <scratch>/geo2rdr/freqA/{range,azimuth}.off                  (pure geometry)
and ask three questions:
    (a) how big is the ampcor correction to pure geometry?   -> geometry health
    (b) how noisy is it after culling?                       -> coregistration
    (c) does the RANGE part look like a smooth field?        -> ionosphere

Track 1 (GSLC): there is no ampcor step, so ampcor the two GSLC AMPLITUDES
against each other with --gslc.  A GSLC pair that is model-coregistered
correctly has a peak at (0,0) in MAP metres.

    python3 offset_qc.py --scratch  scratch/ --pol HH --freq A
    python3 offset_qc.py --gslc ref.gslc.h5 sec.gslc.h5 --pol HH

Acceptance (derived in expected.py [6], sinc/oversampling model):
    residual RMS <= 0.10 RSLC samples  -> coherence loss <= 2.3%   ACCEPT
                    0.10 - 0.25        -> loss 2.3 - 14%           WARN
                    > 0.25             -> loss > 14%               REJECT
    0.10 samples = 0.31 m slant range = 0.45 m along-track ground
                 = 1/47 of the 4.72 m ground-range pixel
                 = 1/10 of the 4.45 m azimuth ground pixel
"""
import argparse
import glob
import os

import numpy as np

import expected as E

ACCEPT, WARN = 0.10, 0.25       # RSLC samples, RMS


def _read(path):
    from osgeo import gdal
    gdal.UseExceptions()
    d = gdal.Open(path)
    return d.GetRasterBand(1).ReadAsArray().astype(np.float64)


def _find(root, *pats):
    for p in pats:
        hits = sorted(glob.glob(os.path.join(root, p)))
        if hits:
            return hits[0]
    return None


def _mad(v):
    v = v[np.isfinite(v)]
    return float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size else np.nan


def _smooth(a, b):
    """Block-mean at scale `b` samples, expanded back -- a cheap low-pass."""
    ny, nx = (a.shape[0] // b) * b, (a.shape[1] // b) * b
    if ny == 0 or nx == 0:
        return np.full_like(a, np.nanmedian(a))
    m = np.nanmean(a[:ny, :nx].reshape(ny // b, b, nx // b, b), axis=(1, 3))
    out = np.repeat(np.repeat(m, b, axis=0), b, axis=1)
    full = np.full_like(a, np.nan)
    full[:ny, :nx] = out
    return np.where(np.isfinite(full), full, np.nanmedian(a))


def _decimate(a, b):
    ny, nx = (a.shape[0] // b) * b, (a.shape[1] // b) * b
    return np.nanmean(a[:ny, :nx].reshape(ny // b, b, nx // b, b), axis=(1, 3))


def robust(v, name, unit_m, osr, sample_m):
    """Robust location/scale + the coherence penalty implied by the scatter."""
    v = v[np.isfinite(v)]
    if v.size == 0:
        return f"  {name:26s}  NO VALID SAMPLES"
    med = np.median(v)
    mad = 1.4826 * np.median(np.abs(v - med))
    p16, p84 = np.percentile(v, [15.87, 84.13])
    rms = float(np.sqrt(np.mean((v - med) ** 2)))
    g = float(E.gamma_misreg(mad, osr))
    flag = "ACCEPT" if mad <= ACCEPT else ("WARN" if mad <= WARN else "REJECT")
    return (f"  {name:26s} n={v.size:9d}  median {med:+8.4f}  MAD {mad:7.4f}  "
            f"RMS {rms:7.4f} samples\n"
            f"  {'':26s} = {mad*sample_m:6.3f} m   "
            f"({(p84-p16)/2:6.4f} half-IQ)   gamma_misreg {g:6.4f}   {flag}")


def track2(scratch, freq, pol):
    L = []
    p = L.append
    fr = f"freq{freq}"
    p("=" * 78)
    p(f"TRACK 2 -- geometric coregistration QC  ({fr}/{pol})")
    p("=" * 78)

    g_rg = _find(scratch, f"geo2rdr/{fr}/range.off", f"**/geo2rdr/{fr}/range.off")
    g_az = _find(scratch, f"geo2rdr/{fr}/azimuth.off", f"**/geo2rdr/{fr}/azimuth.off")
    r_rg = _find(scratch, f"rubbersheet_offsets/{fr}/{pol}/range.off",
                 f"rubbersheet_offsets/{fr}/range.off",
                 f"**/rubbersheet_offsets/{fr}/**/range.off")
    r_az = _find(scratch, f"rubbersheet_offsets/{fr}/{pol}/azimuth.off",
                 f"rubbersheet_offsets/{fr}/azimuth.off",
                 f"**/rubbersheet_offsets/{fr}/**/azimuth.off")
    d_off = _find(scratch, f"dense_offsets/{fr}/{pol}/dense_offsets",
                  f"**/dense_offsets/{fr}/**/dense_offsets")
    d_snr = _find(scratch, f"dense_offsets/{fr}/{pol}/snr",
                  f"**/dense_offsets/{fr}/**/snr")

    for nm, f in (("geo2rdr range.off", g_rg), ("geo2rdr azimuth.off", g_az),
                  ("rubbersheet range.off", r_rg),
                  ("rubbersheet azimuth.off", r_az),
                  ("dense_offsets", d_off), ("dense snr", d_snr)):
        p(f"  {nm:26s} {f or '-- not found --'}")

    if not (r_rg and g_rg):
        p("\n  cannot compute the residual without both geo2rdr and rubbersheet")
        p("  offsets. If you deleted scratch, re-run with the offsets kept, or")
        p("  read pixelOffsets/{alongTrackOffset,slantRangeOffset} from the GUNW.")
        return "\n".join(L)

    dr = _read(r_rg) - _read(g_rg)
    da = _read(r_az) - _read(g_az)
    p("\n  RESIDUAL = rubbersheet - geo2rdr  (what ampcor added to pure geometry)")
    p("  TOTAL -- includes any smooth propagation field:")
    p(robust(dr, "slant-range residual", "m", E.OSR_R_A, E.DR_A))
    p(robust(da, "azimuth residual", "m", E.OSR_AZ, E.DA_GND))

    # split smooth field from misregistration noise: THIS is the discriminator
    p("\n  HIGH-PASS (smooth field removed at 64 samples ~ 300 m): this is the")
    p("  part that actually decorrelates. Judge ACCEPT/WARN/REJECT on THESE.")
    for arr, nm, osr, samp in ((dr, "slant-range hp", E.OSR_R_A, E.DR_A),
                               (da, "azimuth hp", E.OSR_AZ, E.DA_GND)):
        p(robust(arr - _smooth(arr, 64), nm, "m", osr, samp))
    p("\n  smooth part (total MAD vs high-pass MAD):")
    for arr, nm in ((dr, "range"), (da, "azimuth")):
        t = _mad(arr); h = _mad(arr - _smooth(arr, 64))
        p(f"    {nm:8s} total {t:.4f}  high-pass {h:.4f}  smooth "
          f"{np.sqrt(max(t*t-h*h,0)):.4f} samples "
          f"({'FIELD-dominated' if t > 1.5*h else 'noise-dominated'})")

    if d_snr:
        s = _read(d_snr)
        p(f"\n  ampcor SNR: median {np.nanmedian(s):.2f}  "
          f"frac > threshold(0.75) {np.nanmean(s > 0.75):.3f}")
        p("    < 0.5 of the grid above threshold means rubbersheet is mostly")
        p("    interpolating its own hole-fill, not measuring. Treat the fine")
        p("    resample as unverified.")

    # is the RANGE residual a smooth field?  -> ionosphere, not misregistration
    import igram_metrics as M
    DEC = 8                                             # reach km-scale lags
    post_rg = E.DR_A / np.sin(np.deg2rad(41.38)) * DEC   # ground metres/sample
    a = M.radial_acf(_decimate(dr, DEC) - np.nanmedian(dr), posting_m=post_rg,
                     max_lag_px=250)
    b = M.radial_acf(_decimate(da, DEC) - np.nanmedian(da),
                     posting_m=E.DA_GND * DEC, max_lag_px=250)
    p(f"\n  residual structure (radial ACF, {DEC}x decimated)")
    p(f"    range   e-fold {a['efold_m']/1000:7.2f} km   rho(lag1) {a['rho_lag1']:+.3f}")
    p(f"    azimuth e-fold {b['efold_m']/1000:7.2f} km   rho(lag1) {b['rho_lag1']:+.3f}")
    p("    RANGE smooth over >10 km with AZIMUTH white -> ionospheric group")
    p("    delay, not misregistration. The fine resample then shifts the")
    p("    secondary by a real propagation delay and Track 2's phase acquires")
    p("    (4*pi/lambda)*delta_r that Track 1 does not have.")
    p("    Test it: cross-correlate that field against the Track1-Track2")
    p("    residual map from compare_tracks.py:")
    p("      corr > +0.7  -> the disagreement IS the range-offset handling.")

    # implied phase term unique to Track 2
    ph = 4 * np.pi * dr * E.DR_A / E.LAMBDA
    ph = ph[np.isfinite(ph)]
    if ph.size:
        p(f"\n  implied Track-2-only phase term (4*pi/lambda)*delta_r:")
        p(f"    median {np.median(ph):+8.2f} rad   MAD "
          f"{1.4826*np.median(np.abs(ph-np.median(ph))):7.2f} rad   "
          f"p5-p95 {np.percentile(ph,5):+.1f} .. {np.percentile(ph,95):+.1f}")
        p("    compare directly with the ramp reported by compare_tracks.py [3].")
    return "\n".join(L)


def track1(ref, sec, pol, freq="A", win=64, search=8, step=256):
    """Ampcor-lite on the two GSLC amplitudes: map-space relative geolocation.

    Track 1 has NO measured coregistration; this manufactures one.  Peak must
    be at (0,0) metres.  Anything else is a per-date geolocation model error
    (orbit, DEM, TEC LUT, SET) and it decorrelates Track 1 exactly the same
    way a bad rubbersheet decorrelates Track 2.
    """
    import h5py
    L = []
    p = L.append
    p("=" * 78)
    p(f"TRACK 1 -- GSLC relative geolocation (amplitude cross-correlation)")
    p("=" * 78)
    with h5py.File(ref, "r") as h1, h5py.File(sec, "r") as h2:
        b = f"/science/LSAR/GSLC/grids/frequency{freq}"
        d1, d2 = h1[f"{b}/{pol}"], h2[f"{b}/{pol}"]
        x = np.asarray(h1[f"{b}/xCoordinates"], float)
        y = np.asarray(h1[f"{b}/yCoordinates"], float)
        post_x, post_y = abs(x[1] - x[0]), abs(y[1] - y[0])
        ny, nx = d1.shape
        offs = []
        for r0 in range(win, ny - win - 1, step):
            for c0 in range(win, nx - win - 1, step):
                a = np.abs(np.asarray(d1[r0:r0 + win, c0:c0 + win]))
                bb = np.abs(np.asarray(d2[r0 - search:r0 + win + search,
                                          c0 - search:c0 + win + search]))
                if not np.isfinite(a).all() or not np.isfinite(bb).all():
                    continue
                if a.std() < 1e-12 or bb.std() < 1e-12:
                    continue
                a = a - a.mean()
                best, bo = -np.inf, (0, 0)
                for dy in range(-search, search + 1):
                    for dx in range(-search, search + 1):
                        t = bb[search + dy:search + dy + win,
                               search + dx:search + dx + win]
                        t = t - t.mean()
                        den = np.sqrt((a * a).sum() * (t * t).sum())
                        if den <= 0:
                            continue
                        v = float((a * t).sum() / den)
                        if v > best:
                            best, bo = v, (dy, dx)
                if best > 0.2:
                    offs.append((bo[0], bo[1], best))
    if not offs:
        p("  no usable chips -- check pol/freq and that the GSLCs are not empty")
        return "\n".join(L)
    o = np.array(offs)
    for k, nm, post in ((0, "north/row", post_y), (1, "east/col", post_x)):
        v = o[:, k]
        med = np.median(v)
        mad = 1.4826 * np.median(np.abs(v - med))
        p(f"  {nm:10s} n={len(v):5d}  median {med:+7.3f} px = "
          f"{med*post:+7.3f} m   MAD {mad:6.3f} px = {mad*post:6.3f} m")
    p(f"  peak correlation: median {np.median(o[:,2]):.3f}")
    p(f"\n  ACCEPT if |median| < {0.10*E.RES_AZ_GND:.2f} m (0.10 resolution cells,")
    p(f"  = 2.3% coherence loss) in BOTH axes and MAD < 1 pixel.")
    p("  A non-zero median in EAST only -> slant-range model error (TEC LUT,")
    p("  DEM datum/geoid, atmospheric path). In NORTH only -> azimuth timing")
    p("  (orbit interpolation, azimuth TEC/SET LUT). In BOTH -> the DEM.")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", default=None)
    ap.add_argument("--gslc", nargs=2, default=None, metavar=("REF", "SEC"))
    ap.add_argument("--freq", default="A")
    ap.add_argument("--pol", default="HH")
    ap.add_argument("--step", type=int, default=256)
    a = ap.parse_args()
    if a.scratch:
        print(track2(a.scratch, a.freq, a.pol))
    if a.gslc:
        print(track1(a.gslc[0], a.gslc[1], a.pol, a.freq, step=a.step))
    if not a.scratch and not a.gslc:
        ap.error("give --scratch and/or --gslc")
