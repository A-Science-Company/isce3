#!/usr/bin/env python3
"""
igram_metrics.py -- pure-numpy metrics for comparing two geocoded
interferograms of the SAME pair on the SAME grid.

No ISCE3 import.  numpy only (scipy optional, not required).
Every function is memory-conscious: nothing allocates more than a few copies
of the input, and the drivers in compare_tracks.py stream by block.

Conventions
-----------
An interferogram is carried as a COMPLEX array  c = A * exp(i*phi).
Never carry bare phase across an interpolation or an average: the wrap
discontinuity turns a +pi/-pi neighbour pair into 0 instead of pi.
"""
import numpy as np

LAMBDA = 0.241963      # m


# --------------------------------------------------------------- multilooking
def boxcar_complex(c, ry, rx, weights=None):
    """Block-average a complex interferogram by (ry, rx).  Edge blocks dropped.

    Returns (looked_complex, valid_count).  This is the ONLY correct way to
    multilook: sum the complex numerator, do not average phase.
    """
    ny, nx = c.shape
    ny, nx = (ny // ry) * ry, (nx // rx) * rx
    c = c[:ny, :nx]
    good = np.isfinite(c)
    z = np.where(good, c, 0)
    w = good.astype(np.float32) if weights is None else \
        np.where(good, weights[:ny, :nx], 0).astype(np.float32)
    num = (z * w).reshape(ny // ry, ry, nx // rx, rx).sum(axis=(1, 3))
    cnt = w.reshape(ny // ry, ry, nx // rx, rx).sum(axis=(1, 3))
    out = np.where(cnt > 0, num / np.maximum(cnt, 1e-30), np.nan + 0j)
    return out, cnt


def coherence_from_slcs(s1, s2, ry, rx):
    """Multilooked complex igram AND coherence from two co-gridded SLCs.

        gamma_hat = |sum s1 s2*| / sqrt(sum|s1|^2 * sum|s2|^2)

    (Correlation.ipynb, maximum-likelihood estimator.)  This is what Track 1
    must do on the map grid; Track 2's crossmul does the same in radar
    coordinates.  Feed it the SAME (ry, rx) that gives the same N_eff.
    """
    ny, nx = s1.shape
    ny, nx = (ny // ry) * ry, (nx // rx) * rx
    s1, s2 = s1[:ny, :nx], s2[:ny, :nx]
    good = np.isfinite(s1) & np.isfinite(s2)
    a = np.where(good, s1, 0)
    b = np.where(good, s2, 0)

    def blk(x):
        return x.reshape(ny // ry, ry, nx // rx, rx).sum(axis=(1, 3))

    num = blk(a * np.conj(b))
    p1 = blk((a.real ** 2 + a.imag ** 2).astype(np.float64))
    p2 = blk((b.real ** 2 + b.imag ** 2).astype(np.float64))
    n = blk(good.astype(np.float32))
    den = np.sqrt(p1 * p2)
    gam = np.where(den > 0, np.abs(num) / np.maximum(den, 1e-30), np.nan)
    igram = np.where(n > 0, num / np.maximum(n, 1), np.nan + 0j)
    return igram.astype(np.complex64), gam.astype(np.float32), n


# ----------------------------------------------------------- coherence report
COH_EDGES = np.round(np.arange(0.0, 1.0001, 0.02), 4)
COH_THRESH = (0.15, 0.2, 0.3, 0.4, 0.5, 0.7)


def coherence_report(gam, mask=None, name=""):
    g = gam[np.isfinite(gam)] if mask is None else gam[mask & np.isfinite(gam)]
    if g.size == 0:
        return dict(name=name, n=0)
    hist, _ = np.histogram(g, bins=COH_EDGES)
    q = np.percentile(g, [5, 25, 50, 75, 95])
    return dict(
        name=name, n=int(g.size),
        mean=float(g.mean()), std=float(g.std()),
        p05=float(q[0]), p25=float(q[1]), median=float(q[2]),
        p75=float(q[3]), p95=float(q[4]),
        frac_above={f"{t:.2f}": float((g > t).mean()) for t in COH_THRESH},
        hist=hist.astype(np.int64), edges=COH_EDGES)


def hist_distance(r1, r2):
    """L1 (total-variation) and Kolmogorov-Smirnov distance between the two
    coherence histograms.  Both are in [0,1]; interpret with the table in
    compare_tracks.py."""
    h1 = r1["hist"] / max(r1["hist"].sum(), 1)
    h2 = r2["hist"] / max(r2["hist"].sum(), 1)
    tv = 0.5 * np.abs(h1 - h2).sum()
    ks = np.abs(np.cumsum(h1) - np.cumsum(h2)).max()
    return dict(total_variation=float(tv), ks=float(ks),
                d_median=float(r2["median"] - r1["median"]),
                d_p25=float(r2["p25"] - r1["p25"]),
                d_p75=float(r2["p75"] - r1["p75"]))


# ------------------------------------------------------------ phase agreement
def circ_stats(dphi, w=None):
    """Circular mean / resultant length / circular std of a wrapped field."""
    d = np.asarray(dphi)
    m = np.isfinite(d)
    if w is None:
        w = m.astype(np.float64)
    else:
        w = np.where(m, w, 0)
    z = np.where(m, np.exp(1j * np.nan_to_num(d)), 0)
    S = (z * w).sum()
    W = w.sum()
    if W <= 0:
        return dict(n=0)
    R = np.abs(S) / W
    R = min(float(R), 1 - 1e-12)
    return dict(n=int(m.sum()),
                circ_mean=float(np.angle(S)),
                resultant=float(R),
                circ_std=float(np.sqrt(-2 * np.log(R))),
                circ_std_deg=float(np.degrees(np.sqrt(-2 * np.log(R)))),
                los_mm=float(np.sqrt(-2 * np.log(R)) * LAMBDA / (4 * np.pi) * 1e3))


def relative_sign(c1, c2, w=None, tile=8):
    """Which of  c1*conj(c2)  or  c1*c2  is the coherent combination?

    Both tracks form ref*conj(sec) (crossmul: refSlc * conj(secSlcUpsampled)),
    so the answer SHOULD be 'conj'.  Test it anyway -- a flipped sign is the
    single most common silent error when hand-rolling Track 1.

    Measured on `tile` x `tile` blocks, NOT globally: a real long-wavelength
    ramp (ionosphere, orbit) drives the global resultant of BOTH combinations
    to ~0 and the global test then answers at random.  Inside an 8-pixel tile
    a 1 rad/km ramp spans <0.5 rad, so the local resultant is unaffected.
    """
    w = np.ones_like(c1, float) if w is None else np.nan_to_num(w)
    m = np.isfinite(c1) & np.isfinite(c2)
    u1 = np.where(m, np.exp(1j * np.angle(np.where(m, c1, 1))), 0)
    u2 = np.where(m, np.exp(1j * np.angle(np.where(m, c2, 1))), 0)
    w = np.where(m, w, 0.0)
    ny, nx = (c1.shape[0] // tile) * tile, (c1.shape[1] // tile) * tile
    if ny == 0 or nx == 0:
        tile, ny, nx = 1, c1.shape[0], c1.shape[1]

    def tiled(z):
        s = (z * w)[:ny, :nx].reshape(ny // tile, tile, nx // tile, tile)
        return np.abs(s.sum(axis=(1, 3)))

    wt = w[:ny, :nx].reshape(ny // tile, tile, nx // tile, tile).sum(axis=(1, 3))
    ok = wt > 0
    if not ok.any():
        return dict(resultant_conj=np.nan, resultant_same=np.nan,
                    verdict="NO DATA")
    r_conj = float((tiled(u1 * np.conj(u2))[ok] / wt[ok]).mean())
    r_same = float((tiled(u1 * u2)[ok] / wt[ok]).mean())
    return dict(resultant_conj=r_conj, resultant_same=r_same,
                verdict="conj" if r_conj >= r_same else "SAME -- SIGN FLIPPED")


def _ramp_search(dz, w, max_side=512, pad=2, refine=21, sub=16):
    """Ramp estimate by maximising |sum w * dz * exp(-2pi i (fx x + fy y))|.

    A plain LSQ on wrapped phase cannot see a ramp of more than half a fringe
    across the scene; this can see hundreds.  Three stages:
      1. block-average to <= `max_side` per axis (bounds the FFT: a full-frame
         6000 x 5900 grid would otherwise need a 24k x 24k transform),
      2. zero-padded FFT peak -> 1/(pad*side) cycles per coarse pixel,
      3. direct search on a `refine` x `refine` grid at 1/sub of an FFT bin.
    Returns (fx, fy) in cycles per ORIGINAL pixel; y is the ROW axis.
    """
    z = np.where(np.isfinite(dz), dz, 0) * np.nan_to_num(w)
    ny, nx = z.shape
    by = max(1, int(np.ceil(ny / max_side)))
    bx = max(1, int(np.ceil(nx / max_side)))
    cy, cx = ny // by, nx // bx
    if cy == 0 or cx == 0:
        return 0.0, 0.0
    zc = z[:cy * by, :cx * bx].reshape(cy, by, cx, bx).sum(axis=(1, 3))

    py, px = pad * cy, pad * cx
    F = np.abs(np.fft.fft2(zc, s=(py, px)))
    j, i = np.unravel_index(np.argmax(F), F.shape)
    j = j - py if j > py // 2 else j
    i = i - px if i > px // 2 else i
    fyc, fxc = j / py, i / px                    # cycles / coarse pixel

    # stage 3: local direct search
    dfy, dfx = 1.0 / (pad * cy), 1.0 / (pad * cx)
    yy, xx = np.mgrid[0:cy, 0:cx]
    off = (np.arange(refine) - refine // 2) / sub
    best, bfy, bfx = -1.0, fyc, fxc
    for oy in off:
        ey = np.exp(-2j * np.pi * (fyc + oy * dfy) * yy)
        for ox in off:
            v = np.abs((zc * ey * np.exp(-2j * np.pi * (fxc + ox * dfx) * xx)).sum())
            if v > best:
                best, bfy, bfx = v, fyc + oy * dfy, fxc + ox * dfx
    return bfx / bx, bfy / by                    # cycles / ORIGINAL pixel


def phase_difference(c1, c2, w=None, posting_m=50.0, use_conj=True,
                     fft_prescan=True):
    """Full agreement report for two co-gridded complex interferograms.

    Returns the constant, the best-fit plane (the "ramp"), and what is left.
    The three numbers that matter:
        ramp_rad_per_km      -- systematic; blame geometry / iono / SET
        resid_circ_std       -- random; blame noise / interpolation / looks
        var_frac_ramp        -- how much of the disagreement the plane explains
    """
    d = c1 * (np.conj(c2) if use_conj else c2)
    with np.errstate(invalid="ignore"):
        dz = np.exp(1j * np.angle(d))
    m = np.isfinite(d) & (np.abs(d) > 0)
    if w is None:
        w = m.astype(np.float64)
    w = np.where(m, w, 0.0)
    if w.sum() == 0:
        return dict(n=0)

    dz = np.where(m, dz, 0)

    # 1) coarse ramp from the spectrum, so the LSQ starts inside +-pi
    ny, nx = dz.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    fx = fy = 0.0
    if fft_prescan and min(ny, nx) >= 16:
        fx, fy = _ramp_search(dz, w)
        dz = dz * np.exp(-2j * np.pi * (fx * xx + fy * yy))

    # 2) refine: weighted LSQ plane on the (now small) residual phase,
    #    iterated twice so outliers do not drag the plane
    ph = np.angle(dz)
    A = np.column_stack([np.ones(m.sum()), xx[m].ravel(), yy[m].ravel()])
    b = ph[m].ravel()
    ww = w[m].ravel()
    coef = np.zeros(3)
    for _ in range(3):
        r = b - A @ coef
        r = np.angle(np.exp(1j * r))            # rewrap residual
        sw = np.sqrt(ww)
        dc, *_ = np.linalg.lstsq(A * sw[:, None], (r + A @ coef) * sw, rcond=None)
        coef = dc
    c0, cx, cy = coef

    # total ramp = fft part + lsq part, in rad per PIXEL
    gx = 2 * np.pi * fx + cx
    gy = 2 * np.pi * fy + cy

    model = c0 + gx * xx + gy * yy
    resid = np.angle(np.exp(1j * (np.angle(c1 * (np.conj(c2) if use_conj else c2))
                                  - model)))
    resid = np.where(m, resid, np.nan)

    raw = circ_stats(np.angle(d), w)
    dm = circ_stats(np.angle(d) - raw["circ_mean"], w)
    rs = circ_stats(resid, w)

    km = posting_m / 1000.0
    grad = np.hypot(gx, gy) / km
    var_raw = dm["circ_std"] ** 2
    var_res = rs["circ_std"] ** 2
    # the plane evaluated at pixel (0,0) and at the weighted scene centre
    xc = float((xx * w).sum() / w.sum())
    yc = float((yy * w).sum() / w.sum())
    return dict(
        n=int(m.sum()),
        const_rad=float(raw["circ_mean"]),          # circular mean of the RAW
        const_los_mm=float(raw["circ_mean"] * LAMBDA / (4 * np.pi) * 1e3),
        plane_intercept_rad=float(np.angle(np.exp(1j * c0))),
        plane_center_rad=float(np.angle(np.exp(1j * (c0 + gx * xc + gy * yc)))),
        # after removing ONLY the constant
        demeaned_circ_std=float(dm["circ_std"]),
        demeaned_circ_std_deg=float(dm["circ_std_deg"]),
        demeaned_resultant=float(dm["resultant"]),
        # the plane. x = +column (East for a north-up grid),
        #            y = +ROW  (SOUTH for a north-up grid: negate for North)
        ramp_x_rad_per_km=float(gx / km),
        ramp_y_rad_per_km=float(gy / km),
        ramp_north_rad_per_km=float(-gy / km),
        ramp_mag_rad_per_km=float(grad),
        ramp_mag_mm_per_km=float(grad * LAMBDA / (4 * np.pi) * 1e3),
        ramp_across_scene_rad=float(np.hypot(gx * nx, gy * ny)),
        ramp_fringes_across_scene=float(np.hypot(gx * nx, gy * ny) / (2 * np.pi)),
        # what is left
        resid_circ_std=float(rs["circ_std"]),
        resid_circ_std_deg=float(rs["circ_std_deg"]),
        resid_resultant=float(rs["resultant"]),
        resid_los_mm=float(rs["los_mm"]),
        var_frac_ramp=float(max(0.0, 1 - var_res / max(var_raw, 1e-12))),
        resid_map=resid)


# ------------------------------------------------- is the residual structured?
def radial_acf(field, posting_m=50.0, max_lag_px=None):
    """Radially averaged autocorrelation of a (nan-holed) real field.

    Wiener-Khinchin with a validity mask, so holes do not fake correlation.
    Returns lag (m) and rho(lag), plus the e-folding length.

        e-fold <= 2 px   -> white:      interpolation / thermal / look noise
        e-fold  3-20 px  -> speckle-ish or residual-coregistration texture
        e-fold >  1 km   -> a FIELD:    ionosphere, troposphere, SET, orbit
    """
    f = np.where(np.isfinite(field), field, 0.0)
    m = np.isfinite(field).astype(np.float64)
    f = f - (f.sum() / max(m.sum(), 1)) * m
    ny, nx = f.shape
    py, px = 1 << int(np.ceil(np.log2(2 * ny))), 1 << int(np.ceil(np.log2(2 * nx)))
    F = np.fft.rfft2(f, s=(py, px))
    M = np.fft.rfft2(m, s=(py, px))
    num = np.fft.irfft2(F * np.conj(F), s=(py, px))
    den = np.fft.irfft2(M * np.conj(M), s=(py, px))
    acf = np.where(den > 0.5, num / np.maximum(den, 1e-30), np.nan)
    acf = np.fft.fftshift(acf)
    cy, cx = py // 2, px // 2
    L = max_lag_px or min(ny, nx, 200) // 2
    L = int(max(1, min(L, ny - 1, nx - 1, cy, cx)))
    sub = acf[cy - L:cy + L + 1, cx - L:cx + L + 1]
    yy, xx = np.mgrid[0:sub.shape[0], 0:sub.shape[1]]
    yy, xx = yy - L, xx - L
    r = np.hypot(yy, xx).ravel()
    v = sub.ravel()
    ok = np.isfinite(v)
    bins = np.arange(0, L + 1.5, 1.0)
    idx = np.digitize(r[ok], bins) - 1
    prof = np.full(len(bins) - 1, np.nan)
    for k in range(len(prof)):
        s = v[ok][idx == k]
        if s.size:
            prof[k] = s.mean()
    prof = prof / prof[0] if np.isfinite(prof[0]) and prof[0] != 0 else prof
    lag_m = (bins[:-1] + 0.5) * posting_m
    below = np.where(prof < np.exp(-1.0))[0]
    efold = float(lag_m[below[0]]) if below.size else float(lag_m[-1])
    return dict(lag_m=lag_m, rho=prof, efold_m=efold,
                rho_lag1=float(prof[1]) if len(prof) > 1 else np.nan)


def cross_correlate(a, b, w=None):
    """Weighted Pearson r between two real fields (e.g. the two tracks'
    unwrapped phase, or the track difference vs a candidate explanation)."""
    m = np.isfinite(a) & np.isfinite(b)
    if w is not None:
        m &= np.isfinite(w)
        ww = w[m]
    else:
        ww = np.ones(m.sum())
    if m.sum() < 10:
        return np.nan
    x, y = a[m], b[m]
    W = ww.sum()
    mx, my = (ww * x).sum() / W, (ww * y).sum() / W
    cxy = (ww * (x - mx) * (y - my)).sum() / W
    sx = np.sqrt((ww * (x - mx) ** 2).sum() / W)
    sy = np.sqrt((ww * (y - my) ** 2).sum() / W)
    return float(cxy / (sx * sy)) if sx > 0 and sy > 0 else np.nan


# -------------------------------------------------------- stratified breakdown
def stratify(values, strat, bins, labels=None, stat="median"):
    """Break a metric down by a covariate (incidence, relief, land-cover).

    The single most useful diagnostic when the two tracks disagree: a
    difference that is FLAT in incidence but strong in relief is a DEM /
    geometry problem; flat in relief but strong in incidence is a geocoding
    or ground-range-cell-size problem.
    """
    out = []
    for k in range(len(bins) - 1):
        m = (strat >= bins[k]) & (strat < bins[k + 1]) & np.isfinite(values)
        v = values[m]
        lab = labels[k] if labels else f"[{bins[k]:g},{bins[k+1]:g})"
        if v.size == 0:
            out.append((lab, 0, np.nan, np.nan))
            continue
        s = np.median(v) if stat == "median" else v.mean()
        out.append((lab, int(v.size), float(s), float(np.percentile(v, 75)
                                                      - np.percentile(v, 25))))
    return out


def water_floor_check(gam_water, n_eff, tol=0.02):
    """Over open water the true coherence is 0; the ML estimator is biased to
    E[|gamma|] ~ sqrt(pi)/(2 sqrt(N_eff)).  Both tracks must land there.
    Above it -> the estimator is seeing correlated noise, i.e. the two dates
    are NOT independently sampled (a geolocation, geoid or DEM bug, or a
    repeated read of the same file)."""
    from math import lgamma, log, pi, exp
    expect = exp(0.5 * log(pi) + lgamma(n_eff) - log(2.0) - lgamma(n_eff + 0.5))
    g = gam_water[np.isfinite(gam_water)]
    med = float(np.median(g)) if g.size else np.nan
    avg = float(g.mean()) if g.size else np.nan
    return dict(n=int(g.size), expected=float(expect),
                observed_mean=avg, observed_median=med,
                delta=float(avg - expect) if g.size else np.nan,
                pass_=bool(g.size and abs(avg - expect) < tol))
