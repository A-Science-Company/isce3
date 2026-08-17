#!/usr/bin/env python3
"""
compare_tracks.py -- the harness.  Reads Track 1 (GSLC conjugate product) and
Track 2 (GUNW / RIFG-geocoded), forces them onto one grid, and prints the
comparison + a go/no-go verdict.

    python3 compare_tracks.py \\
        --t1 t1_50m                      `# prefix from gslc_igram.py` \\
        --t2 out/GUNW.h5 --t2-pol HH --t2-freq A \\
        --posting 50 --neff 84 \\
        --water water_mask.tif           `# optional, 1 = water` \\
        --dem  dem_50m.tif               `# optional, for relief strata` \\
        --inc  incidence_50m.tif         `# optional, for incidence strata` \\
        --report compare_report.txt

Everything the report prints has a predicted value in expected.py.  Run
`python3 expected.py` first and keep it next to the report.
"""
import argparse
import json
import os
import sys

import numpy as np

import igram_metrics as M
import expected as E


# ------------------------------------------------------------------ readers
def read_tif(path, band=1):
    from osgeo import gdal
    gdal.UseExceptions()
    d = gdal.Open(path)
    a = d.GetRasterBand(band).ReadAsArray()
    nd = d.GetRasterBand(band).GetNoDataValue()
    if nd is not None and np.isfinite(nd):
        a = np.where(a == nd, np.nan, a)
    return a, d.GetGeoTransform(), d.RasterXSize, d.RasterYSize, \
        d.GetProjection()


GUNW = "/science/LSAR/GUNW/grids/frequency{f}"


def read_gunw(path, freq="A", pol="HH", layer="wrapped"):
    """layer: 'wrapped' (complex, fine grid) or 'unwrapped' (phase, coarse)."""
    import h5py
    with h5py.File(path, "r") as h:
        base = GUNW.format(f=freq)
        if layer == "wrapped":
            g = h[f"{base}/wrappedInterferogram/{pol}"]
            c = np.asarray(g["wrappedInterferogram"]).astype(np.complex64)
            coh = np.asarray(g["coherenceMagnitude"]).astype(np.float32)
            extra = {}
        else:
            g = h[f"{base}/unwrappedInterferogram/{pol}"]
            ph = np.asarray(g["unwrappedPhase"]).astype(np.float32)
            coh = np.asarray(g["coherenceMagnitude"]).astype(np.float32)
            c = np.exp(1j * ph).astype(np.complex64)
            extra = {"unwrapped": ph}
            for k in ("connectedComponents", "mask", "ionospherePhaseScreen"):
                if k in g:
                    extra[k] = np.asarray(g[k])
        x = np.asarray(g["xCoordinates"], float)
        y = np.asarray(g["yCoordinates"], float)
        try:
            epsg = int(np.asarray(g["projection"]))
        except Exception:
            epsg = int(g["projection"].attrs.get("epsg_code", 0))
    px, py = x[1] - x[0], y[1] - y[0]
    gt = (x[0] - px / 2, px, 0.0, y[0] - py / 2, 0.0, py)
    return c, coh, gt, epsg, extra


# ------------------------------------------------------- grid reconciliation
def assert_or_reduce(c, coh, gt, target_post, name):
    """Block-average a finer grid down to `target_post`.  Refuses non-integer
    ratios -- a half-pixel shift between the tracks would be read as a ramp."""
    px = abs(gt[1])
    r = target_post / px
    if abs(r - round(r)) > 1e-6:
        raise SystemExit(f"{name}: posting {px} m does not divide "
                         f"{target_post} m evenly. Re-pin the geogrid.")
    r = int(round(r))
    if r == 1:
        return c, coh, gt
    cl, cnt = M.boxcar_complex(c, r, r)
    ny, nx = (coh.shape[0] // r) * r, (coh.shape[1] // r) * r
    cohl = np.nanmean(coh[:ny, :nx].reshape(ny // r, r, nx // r, r), axis=(1, 3))
    gt2 = (gt[0], gt[1] * r, 0.0, gt[3], 0.0, gt[5] * r)
    return cl, cohl.astype(np.float32), gt2


def crop_to_common(a_list, gts, shapes):
    """Intersect two north-up grids with identical posting; return slices."""
    (gx0, dx, _, gy0, _, dy) = gts[0]
    (hx0, hdx, _, hy0, _, hdy) = gts[1]
    if abs(dx - hdx) > 1e-6 or abs(dy - hdy) > 1e-6:
        raise SystemExit(f"posting mismatch {dx},{dy} vs {hdx},{hdy}")
    ox = (hx0 - gx0) / dx
    oy = (hy0 - gy0) / dy
    if abs(ox - round(ox)) > 1e-4 or abs(oy - round(oy)) > 1e-4:
        raise SystemExit(
            f"grids are offset by a NON-INTEGER number of pixels "
            f"({ox:.4f}, {oy:.4f}). Do not resample your way out of this -- "
            f"re-run with pinned top_left/bottom_right (common_grid.py).")
    ox, oy = int(round(ox)), int(round(oy))
    w = min(shapes[0][1] - max(ox, 0), shapes[1][1] + min(ox, 0))
    h = min(shapes[0][0] - max(oy, 0), shapes[1][0] + min(oy, 0))
    s0 = (slice(max(oy, 0), max(oy, 0) + h), slice(max(ox, 0), max(ox, 0) + w))
    s1 = (slice(max(-oy, 0), max(-oy, 0) + h), slice(max(-ox, 0), max(-ox, 0) + w))
    gt = (gx0 + max(ox, 0) * dx, dx, 0.0, gy0 + max(oy, 0) * dy, 0.0, dy)
    return s0, s1, gt, (h, w)


# ------------------------------------------------------------------- report
def fmt_hist(r, width=54):
    h = r["hist"] / max(r["hist"].sum(), 1)
    top = h.max() or 1
    out = []
    for i in range(0, 50, 2):                      # 0.00,0.04,...,0.96
        v = h[i] + h[i + 1]
        out.append(f"    {r['edges'][i]:.2f}-{r['edges'][i+2]:.2f} "
                   f"|{'#' * int(width * v / (2 * top)):<{width}}| {v*100:5.2f}%")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--t1", required=True, help="prefix from gslc_igram.py")
    ap.add_argument("--t2", required=True, help="GUNW .h5 or <prefix> of tifs")
    ap.add_argument("--t2-freq", default="A")
    ap.add_argument("--t2-pol", default="HH")
    ap.add_argument("--t2-layer", default="wrapped", choices=["wrapped", "unwrapped"])
    ap.add_argument("--posting", type=float, required=True,
                    help="common comparison posting, metres")
    ap.add_argument("--neff", type=float, default=84.0,
                    help="effective looks (expected.py [5]); sets the water floor")
    ap.add_argument("--coh-min", type=float, default=0.3,
                    help="coherence gate for the phase-agreement statistics")
    ap.add_argument("--water", default=None, help="1=water mask GeoTIFF")
    ap.add_argument("--dem", default=None)
    ap.add_argument("--inc", default=None)
    ap.add_argument("--report", default=None)
    a = ap.parse_args()

    L = []
    p = lambda *s: L.append(" ".join(str(x) for x in s))

    # ---- load ------------------------------------------------------------
    c1, gt1, w1, h1, _ = read_tif(f"{a.t1}.igram.tif")
    g1, _, _, _, _ = read_tif(f"{a.t1}.coh.tif")
    c1, g1, gt1 = assert_or_reduce(c1, g1, gt1, a.posting, "track1")

    if a.t2.endswith(".h5"):
        c2, g2, gt2, epsg2, extra = read_gunw(a.t2, a.t2_freq, a.t2_pol,
                                              a.t2_layer)
    else:
        c2, gt2, _, _, _ = read_tif(f"{a.t2}.igram.tif")
        g2, _, _, _, _ = read_tif(f"{a.t2}.coh.tif")
        extra = {}
    c2, g2, gt2 = assert_or_reduce(c2, g2, gt2, a.posting, "track2")

    s0, s1, gt, shp = crop_to_common([c1, c2], [gt1, gt2],
                                     [c1.shape, c2.shape])
    c1, g1 = c1[s0], g1[s0]
    c2, g2 = c2[s1], g2[s1]
    p("=" * 78)
    p(f"COMMON GRID  {shp[1]} x {shp[0]} @ {a.posting:g} m   "
      f"origin ({gt[0]:.1f}, {gt[3]:.1f})")
    p("=" * 78)

    def load_aux(path):
        if not path:
            return None
        v, gtv, _, _, _ = read_tif(path)
        if v.shape != shp:
            r = a.posting / abs(gtv[1])
            if abs(r - round(r)) < 1e-6 and round(r) > 1:
                r = int(round(r))
                ny, nx = (v.shape[0] // r) * r, (v.shape[1] // r) * r
                v = np.nanmean(v[:ny, :nx].reshape(ny // r, r, nx // r, r),
                               axis=(1, 3))
            v = v[:shp[0], :shp[1]]
            if v.shape != shp:
                pad = np.full(shp, np.nan)
                pad[:v.shape[0], :v.shape[1]] = v
                v = pad
        return v

    water = load_aux(a.water)
    dem = load_aux(a.dem)
    inc = load_aux(a.inc)

    land = np.ones(shp, bool) if water is None else ~(np.nan_to_num(water) > 0.5)
    valid = np.isfinite(c1) & np.isfinite(c2) & np.isfinite(g1) & np.isfinite(g2)

    # ---- 0. sign ---------------------------------------------------------
    hi = valid & land & (g1 > 0.5) & (g2 > 0.5)
    if hi.sum() < 100:
        hi = valid & land & (g1 > a.coh_min) & (g2 > a.coh_min)
    sg = M.relative_sign(np.where(hi, c1, np.nan), np.where(hi, c2, np.nan))
    p(f"\n[0] SIGN CONVENTION  (both workflows form ref*conj(sec))")
    p(f"    |<u1 conj(u2)>| = {sg['resultant_conj']:.4f}   "
      f"|<u1 u2>| = {sg['resultant_same']:.4f}   -> {sg['verdict']}")
    if sg["verdict"] != "conj":
        p("    !! Track 1 was built with the wrong operand order. Fix it before")
        p("       reading anything below.")
        c2 = np.conj(c2)

    # ---- 1. coherence ----------------------------------------------------
    p(f"\n[1] COHERENCE  (land only, N_eff assumed {a.neff:g})")
    r1 = M.coherence_report(g1, valid & land, "TRACK 1 GSLC")
    r2 = M.coherence_report(g2, valid & land, "TRACK 2 RSLC")
    p(f"    {'':14s}{'n':>10s}{'p05':>7s}{'p25':>7s}{'med':>7s}"
      f"{'p75':>7s}{'p95':>7s}{'mean':>7s}")
    for r in (r1, r2):
        p(f"    {r['name']:14s}{r['n']:10d}{r['p05']:7.3f}{r['p25']:7.3f}"
          f"{r['median']:7.3f}{r['p75']:7.3f}{r['p95']:7.3f}{r['mean']:7.3f}")
    p(f"\n    fraction above threshold")
    p(f"    {'':14s}" + "".join(f"{t:>8}" for t in r1["frac_above"]))
    for r in (r1, r2):
        p(f"    {r['name']:14s}"
          + "".join(f"{v:8.3f}" for v in r["frac_above"].values()))
    hd = M.hist_distance(r1, r2)
    p(f"\n    histogram distance: total-variation {hd['total_variation']:.4f}  "
      f"KS {hd['ks']:.4f}")
    p(f"    d(median) {hd['d_median']:+.4f}  d(p25) {hd['d_p25']:+.4f}  "
      f"d(p75) {hd['d_p75']:+.4f}       (t2 - t1)")
    p("    interpretation:  KS < 0.10 same population | 0.10-0.25 explainable by")
    p("    look/interpolation differences | > 0.25 one track is losing coherence")
    p(f"\n  TRACK 1 histogram\n{fmt_hist(r1)}")
    p(f"\n  TRACK 2 histogram\n{fmt_hist(r2)}")

    # ---- 2. water floor --------------------------------------------------
    if water is not None:
        p(f"\n[2] WATER FLOOR  (true gamma = 0; ML bias = sqrt(pi)/2/sqrt(N_eff))")
        wm = valid & (~land)
        for nm, g in (("TRACK 1", g1), ("TRACK 2", g2)):
            wf = M.water_floor_check(g[wm], a.neff)
            p(f"    {nm}: n={wf['n']:8d}  observed {wf['observed_mean']:.3f} "
              f"(med {wf['observed_median']:.3f})  expected {wf['expected']:.3f}"
              f"  delta {wf['delta']:+.3f}  {'PASS' if wf['pass_'] else 'FAIL'}")
        p("    a water coherence ABOVE the floor by >0.05 means the two dates are")
        p("    not independently sampled: suspect geolocation, geoid/DEM datum,")
        p("    or the same granule read twice.")
    else:
        p(f"\n[2] WATER FLOOR  -- skipped (no --water). The Caribbean occupies the")
        p(f"    north of this frame; supply a mask, it is the cheapest hard check.")

    # ---- 3. phase agreement ---------------------------------------------
    gate = valid & land & (g1 > a.coh_min) & (g2 > a.coh_min)
    w = np.where(gate, np.minimum(g1, g2) ** 2, 0.0)
    p(f"\n[3] PHASE AGREEMENT  (gate: land & both gamma > {a.coh_min:g}; "
      f"n = {int(gate.sum())} = {100*gate.mean():.1f}% of grid)")
    pd = M.phase_difference(np.where(gate, c1, np.nan),
                            np.where(gate, c2, np.nan),
                            w=w, posting_m=a.posting)
    if pd.get("n", 0) == 0:
        p("    NO OVERLAP ABOVE THE COHERENCE GATE -- one track failed. Stop.")
    else:
        p(f"    constant offset          {pd['const_rad']:+8.4f} rad "
          f"= {pd['const_los_mm']:+8.2f} mm LOS")
        p(f"    std after removing const {pd['demeaned_circ_std']:8.4f} rad "
          f"= {pd['demeaned_circ_std_deg']:6.1f} deg")
        p(f"    best-fit plane           {pd['ramp_x_rad_per_km']:+8.4f} rad/km East, "
          f"{pd['ramp_north_rad_per_km']:+8.4f} rad/km North")
        p(f"                             |grad| {pd['ramp_mag_rad_per_km']:.4f} rad/km"
          f" = {pd['ramp_mag_mm_per_km']:.3f} mm/km LOS")
        p(f"                             {pd['ramp_fringes_across_scene']:.2f} fringes"
          f" across the scene diagonal")
        p(f"    plane explains           {100*pd['var_frac_ramp']:5.1f}% of the "
          f"disagreement variance")
        p(f"    RESIDUAL after plane     {pd['resid_circ_std']:8.4f} rad "
          f"= {pd['resid_circ_std_deg']:6.1f} deg = {pd['resid_los_mm']:.2f} mm LOS")
        p(f"    residual resultant       {pd['resid_resultant']:8.4f}  "
          f"(1 = identical, 0 = unrelated)")

        # expected residual purely from look noise, both tracks independent-ish
        med_g = float(np.nanmedian(np.minimum(g1[gate], g2[gate])))
        exp_noise = float(np.sqrt(2) * E.crb_phase_std(med_g, a.neff))
        p(f"\n    expected residual from LOOK NOISE ALONE at gamma={med_g:.2f}, "
          f"N_eff={a.neff:g}:")
        p(f"      sqrt(2)*CRB = {exp_noise:.4f} rad = {np.degrees(exp_noise):.1f} deg")
        p(f"      observed / expected = {pd['resid_circ_std']/exp_noise:.2f}")
        p("      ratio ~1.0-1.4 : the tracks agree to within estimation noise.")
        p("      ratio > 2      : a real processing difference remains.")

        # ---- 4. is the residual a field or noise? -----------------------
        acf = M.radial_acf(pd["resid_map"], posting_m=a.posting)
        p(f"\n[4] RESIDUAL STRUCTURE (radial ACF of the post-plane residual)")
        p(f"    rho(1 pixel) = {acf['rho_lag1']:+.3f}   "
          f"e-folding length = {acf['efold_m']/1000:.2f} km")
        for i in (1, 2, 4, 8, 16, 32):
            if i < len(acf["rho"]):
                p(f"      lag {acf['lag_m'][i]/1000:6.2f} km  rho = {acf['rho'][i]:+.3f}")
        p("    e-fold <= 2 px  -> white: look noise + interpolation. Tracks agree.")
        p("    e-fold  0.1-2 km-> speckle/coregistration texture: check offsets (#5).")
        p("    e-fold  > 5 km  -> a FIELD. Ionosphere/troposphere/SET/orbit.")
        p("                       Cross-correlate it against the GUNW correction")
        p("                       layers before blaming either coregistration.")
        if "ionospherePhaseScreen" in extra:
            ip = extra["ionospherePhaseScreen"]
            if ip.shape == shp:
                r = M.cross_correlate(pd["resid_map"], ip.astype(float), w)
                p(f"    corr(residual, GUNW ionospherePhaseScreen) = {r:+.3f}")
                p("      |r| > 0.6 -> the disagreement IS the ionosphere handling.")

        # ---- 5. stratified ----------------------------------------------
        if inc is not None or dem is not None:
            p(f"\n[5] STRATIFIED  (median |residual| and coherence per stratum)")
        if inc is not None:
            p("    by INCIDENCE ANGLE:")
            bins = [30, 36, 42, 50]
            ab = np.abs(pd["resid_map"])
            for lab, n, med, iqr in M.stratify(ab, inc, bins):
                p(f"      inc {lab:12s} n={n:9d}  |resid| med {med:6.3f} rad "
                  f"IQR {iqr:6.3f}")
            for nm, g in (("t1", g1), ("t2", g2)):
                row = M.stratify(np.where(gate, g, np.nan), inc, bins)
                p(f"      gamma {nm}: " + "  ".join(
                    f"{lab}={med:.3f}" for lab, n, med, _ in row))
            p("      residual FLAT in incidence but the coherence NOT flat ->")
            p("      the ground-cell-size difference (Track 2's 11x11 slant-range")
            p("      cell is 62.7 m near / 46.7 m far vs Track 1's fixed 50 m map")
            p("      cell) is doing it. Expected, not a bug.")
        if dem is not None:
            relief = dem - np.nanmedian(dem)
            gy, gx = np.gradient(np.nan_to_num(dem), a.posting)
            slope = np.degrees(np.arctan(np.hypot(gx, gy)))
            p("    by TERRAIN SLOPE:")
            for lab, n, med, iqr in M.stratify(np.abs(pd["resid_map"]), slope,
                                               [0, 2, 5, 10, 20, 90]):
                p(f"      slope {lab:10s} n={n:9d}  |resid| med {med:6.3f} rad "
                  f"IQR {iqr:6.3f}")
            p("      residual growing with slope -> DEM/geometry. But note h_amb")
            p(f"      is {E.kz_and_hamb(41.38)[1]:.0f} m here: 50 m of DEM error is only")
            p(f"      {2*np.pi*50/E.kz_and_hamb(41.38)[1]:.2f} rad, so a slope-correlated")
            p("      residual above ~0.3 rad is layover/local-resolution, not height.")

    # ---- 6. go / no-go ---------------------------------------------------
    p(f"\n{'='*78}\n[6] GO / NO-GO\n{'='*78}")
    checks = []
    floor = M.water_floor_check(np.array([0.0]), a.neff)["expected"]

    def add(k, ok, got, want):
        checks.append((k, bool(ok), got, want))

    add("T1 coherent land fraction (gamma>0.3)", r1["frac_above"]["0.30"] > 0.35,
        f"{r1['frac_above']['0.30']:.3f}", "> 0.35")
    add("T2 coherent land fraction (gamma>0.3)", r2["frac_above"]["0.30"] > 0.35,
        f"{r2['frac_above']['0.30']:.3f}", "> 0.35")
    add("T1 median land coherence", 0.25 <= r1["median"] <= 0.80,
        f"{r1['median']:.3f}", "0.25 - 0.80")
    add("T2 median land coherence", 0.25 <= r2["median"] <= 0.80,
        f"{r2['median']:.3f}", "0.25 - 0.80")
    if water is not None:
        for nm, g in (("T1", g1), ("T2", g2)):
            wf = M.water_floor_check(g[valid & ~land], a.neff)
            add(f"{nm} water coherence at ML floor", wf["pass_"],
                f"{wf['observed_mean']:.3f}", f"{floor:.3f} +-0.02")
    add("sign convention", sg["verdict"] == "conj", sg["verdict"], "conj")
    if pd.get("n", 0):
        add("residual / look-noise ratio", pd["resid_circ_std"] / exp_noise < 2.0,
            f"{pd['resid_circ_std']/exp_noise:.2f}", "< 2.0")
        add("residual circ std", pd["resid_circ_std"] < 0.8,
            f"{pd['resid_circ_std']:.3f} rad", "< 0.8 rad")
        add("coherence KS distance", hd["ks"] < 0.25, f"{hd['ks']:.3f}", "< 0.25")
        add("track-to-track ramp", pd["ramp_fringes_across_scene"] < 1.0,
            f"{pd['ramp_fringes_across_scene']:.2f} fringes", "< 1.0 fringe")
    for k, ok, got, want in checks:
        p(f"    [{'PASS' if ok else 'FAIL'}] {k:42s} {got:>18s}  (want {want})")
    nfail = sum(1 for _, ok, _, _ in checks if not ok)
    p(f"\n    {len(checks)-nfail}/{len(checks)} checks passed."
      f"  VERDICT: {'GO' if nfail == 0 else 'NO-GO -- see FAIL lines'}")
    p("\n    triage order when something FAILs:")
    p("      water floor FAIL      -> geolocation / DEM datum / duplicated input.")
    p("                               Nothing else in this report means anything.")
    p("      sign FAIL             -> operand order in Track 1. One-line fix.")
    p("      ramp FAIL, residual   -> NOT a coregistration problem. Both tracks")
    p("        PASS                   coregistered fine; they differ in what long-")
    p("                               wavelength model was applied. Check TEC file,")
    p("                               SET flag, ionosphere_phase_correction.enabled.")
    p("      residual FAIL, ramp   -> a coregistration / resampling problem. Go to")
    p("        PASS                   offset_qc.py and the incidence strata.")
    p("      coherent fraction     -> that track did not coregister at all, or the")
    p("        FAIL on ONE track      DEM/orbit is wrong. Look at its amplitude")
    p("                               image before anything else.")
    p("      both tracks FAIL      -> the PAIR is bad (decorrelated), not the")
    p("        coherent fraction      software. Check the 12-day expectation in")
    p("                               expected.py [4].")

    txt = "\n".join(L)
    print(txt)
    if a.report:
        open(a.report, "w").write(txt + "\n")
        np.savez_compressed(os.path.splitext(a.report)[0] + ".npz",
                            resid=pd.get("resid_map", np.zeros(1)),
                            coh_t1=g1, coh_t2=g2, gt=np.array(gt))
        print(f"\nwrote {a.report} and {os.path.splitext(a.report)[0]}.npz")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
