#!/usr/bin/env python3
"""
test_metrics.py -- self-test for igram_metrics.py on synthetic data whose
answer is known.  Run this BEFORE trusting the harness on real products.

    python3 test_metrics.py
"""
import numpy as np
import igram_metrics as M
import expected as E

rng = np.random.default_rng(20260817)
FAIL = []


def check(name, got, want, tol):
    ok = abs(got - want) <= tol
    print(f"  {'PASS' if ok else 'FAIL'}  {name:38s} got {got:11.4f}  "
          f"want {want:9.4f} +-{tol:g}")
    if not ok:
        FAIL.append(name)


def make_pair(ny, nx, gamma):
    """Two circular-Gaussian SLCs with prescribed true coherence `gamma`."""
    a = (rng.normal(size=(ny, nx)) + 1j * rng.normal(size=(ny, nx))) / np.sqrt(2)
    n = (rng.normal(size=(ny, nx)) + 1j * rng.normal(size=(ny, nx))) / np.sqrt(2)
    b = gamma * a + np.sqrt(np.maximum(1 - gamma ** 2, 0)) * n
    return a.astype(np.complex64), b.astype(np.complex64)


print("=" * 74)
print("A. coherence estimator + water bias floor")
print("=" * 74)
NY = NX = 900
for g_true in (0.0, 0.3, 0.6):
    s1, s2 = make_pair(NY, NX, g_true)
    _, gam, _ = M.coherence_from_slcs(s1, s2, 9, 9)     # N = 81 independent
    if g_true == 0.0:
        wf = M.water_floor_check(gam, 81)
        check("water floor E[gamma|0]", wf["observed_mean"], wf["expected"], 0.02)
    else:
        # ML estimator is biased high; bias ~ (1-g^2)/(2N) at first order
        check(f"gamma_hat (true {g_true})", float(np.median(gam)),
              g_true + (1 - g_true ** 2) / (2 * 81), 0.02)

print()
print("=" * 74)
print("B. Cramer-Rao phase noise reproduced by the estimator")
print("=" * 74)
for g_true in (0.3, 0.6):
    s1, s2 = make_pair(NY, NX, g_true)
    ig, gam, _ = M.coherence_from_slcs(s1, s2, 9, 9)
    st = M.circ_stats(np.angle(ig))
    check(f"sigma_phi (gamma {g_true}, N=81)", st["circ_std"],
          float(E.crb_phase_std(g_true, 81)), 0.04)

print()
print("=" * 74)
print("C. sign detection")
print("=" * 74)
s1, s2 = make_pair(400, 400, 0.8)
ig, _, _ = M.coherence_from_slcs(s1, s2, 4, 4)
r = M.relative_sign(ig, ig)
print(f"  verdict={r['verdict']}  conj={r['resultant_conj']:.3f} "
      f"same={r['resultant_same']:.3f}")
if r["verdict"] != "conj":
    FAIL.append("relative_sign conj")
r = M.relative_sign(ig, np.conj(ig))
print(f"  flipped input -> verdict={r['verdict']}  "
      f"conj={r['resultant_conj']:.3f} same={r['resultant_same']:.3f}")
if r["verdict"] == "conj":
    FAIL.append("relative_sign flip detect")

print()
print("=" * 74)
print("D. constant + ramp recovery from a wrapped difference")
print("=" * 74)
NY, NX, POST = 300, 400, 50.0
yy, xx = np.mgrid[0:NY, 0:NX]
TRUE_CONST = 0.7                                   # rad
TRUE_GX_KM, TRUE_GY_KM = 0.25, -0.11               # rad/km
gx = TRUE_GX_KM * POST / 1000.0                    # rad/pixel
gy = TRUE_GY_KM * POST / 1000.0
NOISE = 0.30                                       # rad

base = np.exp(1j * rng.uniform(-np.pi, np.pi, (NY, NX))).astype(np.complex64)
c1 = base
c2 = base * np.exp(-1j * (TRUE_CONST + gx * xx + gy * yy
                          + rng.normal(0, NOISE, (NY, NX))))
res = M.phase_difference(c1, c2.astype(np.complex64), posting_m=POST)
check("plane intercept (rad)", res["plane_intercept_rad"], TRUE_CONST, 0.05)
check("raw circular mean (rad)", res["const_rad"],
      TRUE_CONST + gx * (NX - 1) / 2 + gy * (NY - 1) / 2, 0.05)
check("ramp_x (rad/km)", res["ramp_x_rad_per_km"], TRUE_GX_KM, 0.01)
check("ramp_y (rad/km)", res["ramp_y_rad_per_km"], TRUE_GY_KM, 0.01)
check("residual circ std (rad)", res["resid_circ_std"], NOISE, 0.05)
check("var frac explained by ramp", res["var_frac_ramp"],
      1 - NOISE ** 2 / (NOISE ** 2 + np.var(gx * xx + gy * yy)), 0.06)
print(f"  ramp across scene = {res['ramp_fringes_across_scene']:.2f} fringes, "
      f"{res['ramp_mag_mm_per_km']:.2f} mm/km LOS")

print()
print("  multi-fringe ramp (FFT prescan must catch this):")
gx2 = 6 * 2 * np.pi / NX                           # 6 fringes across x
c2b = base * np.exp(-1j * (gx2 * xx + rng.normal(0, 0.2, (NY, NX))))
r2 = M.phase_difference(c1, c2b.astype(np.complex64), posting_m=POST)
check("6-fringe ramp recovered (fringes)", r2["ramp_fringes_across_scene"],
      6.0, 0.25)

print()
print("=" * 74)
print("E. ramp vs noise discrimination (radial ACF)")
print("=" * 74)
white = rng.normal(0, 1, (256, 256))
a = M.radial_acf(white, posting_m=POST)
print(f"  white noise      : e-fold {a['efold_m']:8.0f} m  rho(lag1)={a['rho_lag1']:+.3f}")
if a["efold_m"] > 2 * POST:
    FAIL.append("acf white")

k = np.hanning(61)[:, None] * np.hanning(61)[None, :]
sm = np.fft.irfft2(np.fft.rfft2(white) * np.fft.rfft2(k / k.sum(),
                                                      s=white.shape)).real
b = M.radial_acf(sm, posting_m=POST)
print(f"  smoothed (30 px) : e-fold {b['efold_m']:8.0f} m  rho(lag1)={b['rho_lag1']:+.3f}")
if not (5 * POST < b["efold_m"] < 60 * POST):
    FAIL.append("acf smooth")

field = np.sin(2 * np.pi * xx[:256, :256] / 200.0)
c = M.radial_acf(field, posting_m=POST)
print(f"  km-scale field   : e-fold {c['efold_m']:8.0f} m  rho(lag1)={c['rho_lag1']:+.3f}")
if c["efold_m"] < 10 * POST:
    FAIL.append("acf field")

print()
print("=" * 74)
print("F. coherence histogram distance")
print("=" * 74)
s1, s2 = make_pair(600, 600, 0.5)
_, gA, _ = M.coherence_from_slcs(s1, s2, 9, 9)
_, gB, _ = M.coherence_from_slcs(s1, s2, 9, 9)
rA = M.coherence_report(gA, name="track1")
rB = M.coherence_report(gB, name="track2")
d = M.hist_distance(rA, rB)
print(f"  identical data  : TV={d['total_variation']:.4f} KS={d['ks']:.4f} "
      f"dmedian={d['d_median']:+.4f}")
if d["total_variation"] > 0.05:
    FAIL.append("hist identical")

s3, s4 = make_pair(600, 600, 0.42)
_, gC, _ = M.coherence_from_slcs(s3, s4, 9, 9)
rC = M.coherence_report(gC, name="track2'")
d2 = M.hist_distance(rA, rC)
print(f"  gamma 0.50 vs 0.42: TV={d2['total_variation']:.4f} KS={d2['ks']:.4f} "
      f"dmedian={d2['d_median']:+.4f}")
if d2["ks"] < 0.15:
    FAIL.append("hist different")

print()
print("=" * 74)
print("G. misregistration -> coherence loss (matches expected.py budget)")
print("=" * 74)
NY = NX = 1024
osr = E.OSR_R_A
for mu in (0.0, 0.1, 0.25, 0.5):
    w = np.zeros((NY, NX), complex)
    # band-limited complex scene: white spectrum over 1/osr of the band
    half = int(NX / (2 * osr))
    sp = np.zeros((NY, NX), complex)
    sp[:, :half] = (rng.normal(size=(NY, half)) + 1j * rng.normal(size=(NY, half)))
    sp[:, -half:] = (rng.normal(size=(NY, half)) + 1j * rng.normal(size=(NY, half)))
    s = np.fft.ifft2(sp)
    f = np.fft.fftfreq(NX)
    sh = np.fft.ifft2(np.fft.fft2(s) * np.exp(-2j * np.pi * f[None, :] * mu))
    num = np.abs((s * np.conj(sh)).sum())
    den = np.sqrt((np.abs(s) ** 2).sum() * (np.abs(sh) ** 2).sum())
    check(f"gamma_misreg(mu={mu})", float(num / den),
          float(E.gamma_misreg(mu, osr)), 0.02)

print()
print("=" * 74)
print("FAILURES:", FAIL if FAIL else "none")
print("=" * 74)
raise SystemExit(1 if FAIL else 0)
