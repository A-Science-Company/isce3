#!/usr/bin/env python3
"""
expected.py -- closed-form expectations for the Venezuela T162 F007 NISAR pair.

Everything here is analytic; no data is read.  Run it first so that every
number in the comparison report has a predicted value to be checked against.

    python3 expected.py

Ground truth constants are the measured values for
  NISAR_L1_PR_RSLC_0{22,23}_162_A_007_4005_DHDH_A_2026061{3,25}...
"""
import numpy as np

# ----------------------------------------------------------------- constants
C          = 299792458.0
LAMBDA     = 0.241963          # m, processedCenterFrequency 1.239 GHz
F0         = C / LAMBDA        # 1.2390e9 Hz
BPERP      = 31.0              # m, measured
DT_DAYS    = 12.0

# frequency A
BW_A       = 40.0e6
DR_A       = 3.1228381041666666    # slant range SAMPLE spacing (m)
RES_R_A    = C / (2 * BW_A)        # slant range RESOLUTION (m)
OSR_R_A    = RES_R_A / DR_A        # 1.2000  range oversampling

PRF_EFF    = 1.0 / 6.578947368421052e-4   # 1520.0 Hz  (zeroDopplerTimeSpacing)
BW_AZ      = 1261.7874667708181           # processedAzimuthBandwidth, Hz
OSR_AZ     = PRF_EFF / BW_AZ              # 1.2046  azimuth oversampling
VG         = 6766.6                       # ground-track velocity, m/s
DA_GND     = VG / PRF_EFF                 # 4.452 m azimuth ground sample spacing
RES_AZ_GND = VG / BW_AZ                   # 5.363 m azimuth ground resolution

# frequency B
BW_B       = 5.0e6
DR_B       = 24.9827
RES_R_B    = C / (2 * BW_B)

INC        = {'near': 33.22, 'mid': 41.38, 'far': 47.39}     # deg
GRD_R_A    = {'near': 5.699, 'mid': 4.724, 'far': 4.243}     # m
GRD_R_B    = {'near': 45.595, 'mid': 37.793, 'far': 33.945}  # m

H_SC, R_E  = 747e3, 6371e3


def geometry(inc_deg):
    """look angle, slant range for a spherical earth."""
    i = np.deg2rad(inc_deg)
    th = np.arcsin(R_E * np.sin(i) / (R_E + H_SC))
    rho = np.sqrt((R_E + H_SC) ** 2 + R_E ** 2
                  - 2 * (R_E + H_SC) * R_E * np.cos(i - th))
    return np.rad2deg(th), rho


def critical_baseline(inc_deg, res_r=RES_R_A, p=2):
    th, rho = geometry(inc_deg)
    return LAMBDA * rho * np.tan(np.deg2rad(inc_deg)) / (p * res_r)


def kz_and_hamb(inc_deg, bperp=BPERP, p=2):
    """vertical wavenumber (rad/m) and height of ambiguity (m)."""
    th, rho = geometry(inc_deg)
    kz = 2 * np.pi * p * bperp / (LAMBDA * rho * np.sin(np.deg2rad(th)))
    return kz, 2 * np.pi / kz


def gamma_volume(hv, inc_deg='mid', bperp=BPERP):
    kz, _ = kz_and_hamb(INC[inc_deg] if isinstance(inc_deg, str) else inc_deg, bperp)
    return np.sinc(kz * hv / 2 / np.pi)


def gamma_temporal(sigma_m, inc_deg=41.38, lam=LAMBDA):
    """Zebker-Villasenor Gaussian scatterer-motion model (Correlation.ipynb)."""
    th = np.deg2rad(inc_deg)
    return np.exp(-0.5 * (4 * np.pi / lam) ** 2 * (sigma_m ** 2 * np.sin(th) ** 2))


def gamma_misreg(mu_samples, osr):
    """Coherence loss from misregistration of `mu` RSLC SAMPLES, one axis.
    Rectangular signal spectrum of relative width 1/osr -> |sinc(mu/osr)|."""
    return np.abs(np.sinc(np.asarray(mu_samples, float) / osr))


def n_eff(rg_looks, az_looks, osr_r=OSR_R_A, osr_az=OSR_AZ):
    """Independent looks after a boxcar of (az_looks x rg_looks) SAMPLES."""
    return rg_looks * az_looks / (osr_r * osr_az)


def crb_phase_std(gamma, N):
    """Cramer-Rao bound on interferometric phase std, rad (Correlation.ipynb)."""
    g = np.asarray(gamma, float)
    return 1.0 / np.sqrt(2 * N) * np.sqrt((1 - g ** 2) / g ** 2)


def coh_bias_at_zero(N):
    """E[|gamma_hat|] when the true coherence is 0 -- the WATER FLOOR."""
    from math import lgamma, log, sqrt, pi, exp
    return exp(0.5 * log(pi) + lgamma(N) - log(2.0) - lgamma(N + 0.5))


def phase_to_los_mm(phi_rad):
    return phi_rad * LAMBDA / (4 * np.pi) * 1000.0


def iono_range_delay(tecu, f=F0):
    """One-way group delay in metres for `tecu` TECU (1 TECU = 1e16 el/m^2)."""
    return 40.31 * (tecu * 1e16) / f ** 2


def iono_phase(tecu, f=F0):
    """Two-way differential interferometric phase (rad) for a dTEC of `tecu`."""
    return 4 * np.pi * iono_range_delay(tecu, f) / LAMBDA


# ----------------------------------------------------------------------- main
def report():
    L = []
    p = L.append
    p("=" * 78)
    p("EXPECTED VALUES -- Venezuela T162 F007, cycles 022/023, B_perp = 31 m")
    p("=" * 78)

    p("\n[1] SAMPLING / RESOLUTION")
    p(f"  freq A  slant-range resolution {RES_R_A:6.3f} m, sample {DR_A:6.3f} m"
      f"  -> oversampling {OSR_R_A:.4f}")
    p(f"  freq A  azimuth  resolution {RES_AZ_GND:6.3f} m, sample {DA_GND:6.3f} m"
      f"  -> oversampling {OSR_AZ:.4f}")
    p(f"  freq B  slant-range resolution {RES_R_B:6.2f} m, sample {DR_B:6.2f} m"
      f"  -> oversampling {RES_R_B/DR_B:.4f}")
    p("  => freq A ground cell is 4.72 x 4.45 m (mid) : essentially SQUARE (1.06)")

    p("\n[2] BASELINE DECORRELATION AND TOPOGRAPHIC SENSITIVITY  (B_perp = 31 m)")
    p(f"  {'':6s} {'look':>6s} {'rho(km)':>8s} {'b_crit(km)':>11s} "
      f"{'gamma_geom':>11s} {'kz(rad/m)':>10s} {'h_amb(m)':>9s}")
    for k, inc in INC.items():
        th, rho = geometry(inc)
        bc = critical_baseline(inc)
        kz, ha = kz_and_hamb(inc)
        p(f"  {k:6s} {th:6.2f} {rho/1e3:8.1f} {bc/1e3:11.1f} "
          f"{1-BPERP/bc:11.4f} {kz:10.5f} {ha:9.0f}")
    p("  => geometric decorrelation is <0.2% everywhere. NOT a failure mode here.")
    p(f"  => h_amb ~2200 m at mid swath: a 50 m DEM error costs only "
      f"{2*np.pi*50/2212:.3f} rad = {np.rad2deg(2*np.pi*50/2212):.1f} deg.")
    p("     ANY track-to-track difference larger than ~0.15 rad is NOT the DEM.")

    p("\n[3] VOLUME DECORRELATION (tropical canopy)")
    for hv in (10, 20, 30, 40):
        p(f"  canopy {hv:3d} m -> gamma_v = {gamma_volume(hv):.4f}")
    p("  => negligible at 31 m baseline. Tropical coherence loss here is TEMPORAL,")
    p("     not volumetric. Do not blame the baseline.")

    p("\n[4] TEMPORAL DECORRELATION, 12 days, L-band vs C/X (Zebker-Villasenor)")
    p(f"  {'sigma_c':>8s} {'X(3.1cm)':>10s} {'C(5.6cm)':>10s} {'L(24.2cm)':>10s}")
    for s in (0.005, 0.01, 0.015, 0.02, 0.03, 0.05):
        p(f"  {s*100:6.1f}cm {gamma_temporal(s,lam=0.031):10.3f} "
          f"{gamma_temporal(s,lam=0.0555):10.3f} {gamma_temporal(s):10.3f}")
    p("  => L-band survives 2-3 cm of scatterer motion where C-band is already dead.")
    p("     Expected 12-day L-band gamma:  bare/urban 0.6-0.85, ")
    p("     savanna/pasture 0.4-0.6, closed tropical forest 0.25-0.45, water <0.15")

    p("\n[5] LOOKS AND PHASE NOISE")
    ne_11 = n_eff(11, 11)
    p(f"  Track 2 crossmul 11x11 samples -> ground cell "
      f"{11*GRD_R_A['mid']:.1f} x {11*DA_GND:.1f} m, N_eff = {ne_11:.1f}")
    p(f"  Track 1 GSLC 5 m posting, 10x10 boxcar -> 50 x 50 m, "
      f"N_eff ~ {100/(RES_R_A/np.sin(np.deg2rad(41.38))/5.0 * RES_AZ_GND/5.0):.1f}")
    p("  (match N_eff, NOT window size, before comparing coherence histograms)")
    p(f"\n  {'gamma':>6s} {'sig_phi(N=84)':>14s} {'LOS mm':>8s} {'sig_phi(N=25)':>14s} {'LOS mm':>8s}")
    for g in (0.15, 0.2, 0.3, 0.4, 0.5, 0.7, 0.85):
        s84, s25 = crb_phase_std(g, 84), crb_phase_std(g, 25)
        p(f"  {g:6.2f} {s84:14.3f} {phase_to_los_mm(s84):8.1f} "
          f"{s25:14.3f} {phase_to_los_mm(s25):8.1f}")
    p(f"\n  COHERENCE BIAS FLOOR (true gamma = 0, i.e. open water):")
    for N in (25, 49, 84, 121, 200):
        p(f"    N_eff = {N:4d} -> E[gamma_hat] = {coh_bias_at_zero(N):.3f}")
    p("  => over the Caribbean, BOTH tracks must land within +-0.02 of this floor.")
    p("     A water coherence well above the floor means a geolocation/DEM/geoid bug.")

    p("\n[6] MISREGISTRATION BUDGET (Track 2 rubbersheet acceptance)")
    p(f"  {'mu (samples)':>13s} {'gamma_rg':>9s} {'gamma_az':>9s} {'product':>9s} "
      f"{'slant m':>8s} {'az gnd m':>9s}")
    for mu in (0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0):
        gr, ga = gamma_misreg(mu, OSR_R_A), gamma_misreg(mu, OSR_AZ)
        p(f"  {mu:13.2f} {gr:9.4f} {ga:9.4f} {gr*ga:9.4f} "
          f"{mu*DR_A:8.3f} {mu*DA_GND:9.3f}")
    p("  => ACCEPT if residual RMS <= 0.10 samples both axes (0.31 m slant range,")
    p("     0.45 m along-track): coherence penalty <= 2.3%.")
    p("     WARN 0.10-0.25 samples.  REJECT > 0.25 samples (>=14% loss).")
    p("     0.10 samples is 1/47 of the 4.72 m ground-range pixel and")
    p("     1/10 of the 4.45 m azimuth ground pixel -- ampcor on 64x64 windows")
    p("     at gamma>0.3 routinely achieves 0.02-0.05 samples, so this is not tight.")

    p("\n[7] IONOSPHERE -- the dominant expected TRACK-TO-TRACK difference")
    p(f"  scene is 9.5-12.2 N, 66.8-69.6 W : equatorial anomaly crest region")
    p(f"  acquisition 10:07 UTC = 06:07 local -> post-sunrise TEC build-up")
    p(f"  {'dTEC (TECU)':>12s} {'d-range (m)':>12s} {'phase (rad)':>12s} {'LOS mm equiv':>14s}")
    for t in (1, 2, 5, 10, 20):
        dr = iono_range_delay(t)
        p(f"  {t:12d} {dr:12.3f} {iono_phase(t):12.1f} {phase_to_los_mm(iono_phase(t)):14.1f}")
    p("  => 1 TECU of DIFFERENTIAL TEC = 13.6 rad = 2.2 fringes at L-band.")
    p("     Track 1 (GSLC) applies a TEC model as a slant-range/azimuth TIMING SHIFT.")
    p("     Track 2 (insar.py) applies NOTHING by default")
    p("     (processing.ionosphere_phase_correction.enabled: False).")
    p("     THIS IS THE #1 REASON THE TWO TRACKS WILL DISAGREE. Control it:")
    p("     either give BOTH the same TEC file, or give NEITHER one.")

    p("\n[8] SOLID EARTH TIDES")
    p("  GSLC applies SET as a geometric shift (correction_luts.solid_earth_tides_enabled: True).")
    p("  GUNW carries SET as a *layer*, not applied to the phase.")
    p("  Differential SET over 12 d: ~1-10 cm LOS, wavelength >> frame.")
    for cm in (1, 3, 10):
        p(f"    {cm:2d} cm LOS -> {4*np.pi*cm/100/LAMBDA:6.2f} rad "
          f"(mostly common-mode; across a 300 km frame expect the RAMP part)")
    p("  => expect a residual PLANE of order 0.3-2 rad across the frame, not noise.")

    p("\n[9] RESAMPLING KERNEL COUNT  (the kernel is the SAME in both tracks:")
    p("    isce3::core::Sinc2dInterpolator<complex<float>>(SINC_LEN=8,")
    p("    SINC_SUB=8192) -- geocodeSlc.cpp:638 and image/Resample.cpp:26.")
    p("    What differs is HOW MANY TIMES it is applied.)")
    p("  Track 1: 1 sinc-8 resample per date, straight onto the map grid.")
    p("           Reference is resampled too, so both dates are treated alike.")
    p("           No post-hoc interpolation of the interferogram.")
    p("  Track 2: reference 0 resamples, secondary 2 (coarse_resample then")
    p("           fine_resample) -> the pair is treated ASYMMETRICALLY;")
    p("           crossmul upsamples x2 (oversample: 2); then geocode_insar")
    p("           interpolates the multilooked REAL layers with")
    p("           interp_method: BILINEAR and the wrapped igram with SINC.")
    p("  => Track 2 has ~1-2% extra interpolation coherence loss on the secondary,")
    p("     plus BILINEAR smoothing of coherence/unwrapped phase at geocode time.")
    p("     Bilinear at 80 m from ~50 m radar cells LOWERS the coherence variance")
    p("     and RAISES low-coherence pixels: expect Track 2's coherence histogram")
    p("     to be NARROWER and its low tail LIGHTER than Track 1's, by ~0.02-0.05")
    p("     in the quartiles, for identical underlying data.  That is not a bug.")

    p("\n[10] DISK BUDGET -- why the comparison must be scoped")
    npix_A = 54720 * 52649
    npix_B = 54720 * 6582
    p(f"  freq A radar grid = {npix_A/1e9:.3f} Gpix")
    p(f"    rdr2geo x,y,z float64      3 x {npix_A*8/1e9:5.1f} = {3*npix_A*8/1e9:6.1f} GB")
    p(f"    geo2rdr range/azimuth.off  2 x {npix_A*8/1e9:5.1f} = {2*npix_A*8/1e9:6.1f} GB")
    p(f"    resampled SLC complex64    1 x {npix_A*8/1e9:5.1f} = {npix_A*8/1e9:6.1f} GB")
    p(f"    PEAK (rdr2geo still on disk during geo2rdr) = {5*npix_A*8/1e9:.0f} GB "
      f"vs 94 GB free  -> DOES NOT FIT")
    p(f"  freq B radar grid = {npix_B/1e9:.3f} Gpix")
    p(f"    same stages peak = {5*npix_B*8/1e9:.1f} GB  -> FITS COMFORTABLY")
    p("  => run Track 2 on freq B full-frame for the end-to-end shakeout, and on an")
    p("     AZIMUTH-CROPPED freq A sub-frame for the real radiometric comparison.")
    p(f"     A 1/6 azimuth crop (9120 lines) of freq A peaks at "
      f"{5*9120*52649*8/1e9:.0f} GB.")
    return "\n".join(L)


if __name__ == "__main__":
    print(report())
