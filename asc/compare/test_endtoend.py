#!/usr/bin/env python3
"""
test_endtoend.py -- build two synthetic "tracks" as GeoTIFFs on the pinned
grid, with a KNOWN ramp, a KNOWN coherence difference and a water body, then
drive compare_tracks.py over them.  Proves the whole pipe runs.

    python3 test_endtoend.py [workdir]
"""
import os
import subprocess
import sys

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()
rng = np.random.default_rng(7)

WORK = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cmp_selftest"
os.makedirs(WORK, exist_ok=True)

# pinned grid taken from common_grid.py on the real reference RSLC, cropped
EPSG, POST = 32619, 50.0
X0, Y0 = 434080.0, 1350740.0
NX, NY = 600, 500                       # 30 x 25 km
GT = (X0, POST, 0.0, Y0, 0.0, -POST)

yy, xx = np.mgrid[0:NY, 0:NX]

# --- a truth field: 2 fringes of "deformation" + a topo-correlated blob -----
defo = 2 * 2 * np.pi * np.exp(-(((xx - 300) / 90.) ** 2 + ((yy - 250) / 70.) ** 2))

# --- terrain / land cover ---------------------------------------------------
dem = 800 * np.exp(-(((xx - 420) / 150.) ** 2 + ((yy - 150) / 120.) ** 2))
water = (yy < 60).astype(np.float32)                    # Caribbean to the north
inc = np.linspace(33.2, 47.4, NX)[None, :] * np.ones((NY, 1))

gamma_true = np.where(water > 0, 0.0,
                      np.clip(0.55 - 0.30 * np.exp(-((xx - 150) / 200.) ** 2)
                              + 0.15 * (dem / 800.), 0.05, 0.9))

# --- the two tracks ---------------------------------------------------------
# Track 2 additionally carries an unmodelled ionospheric ramp (Track 1's GSLC
# applied a TEC model, Track 2 did not) : 1.5 rad/km ~ 0.11 TECU of dTEC.
RAMP_X_RAD_KM, RAMP_Y_RAD_KM = 0.9, -0.4
ramp = (RAMP_X_RAD_KM * xx * POST / 1000.0
        + RAMP_Y_RAD_KM * yy * POST / 1000.0)
CONST = 0.35

N_EFF = 84.0


def synth(extra_phase, gamma, extra_decorr=1.0):
    g = np.clip(gamma * extra_decorr, 1e-6, 0.999)
    sig = 1.0 / np.sqrt(2 * N_EFF) * np.sqrt((1 - g ** 2) / g ** 2)
    ph = np.where(g > 0.02, extra_phase + rng.normal(0, np.minimum(sig, 3.0)),
                  rng.uniform(-np.pi, np.pi, g.shape))
    ghat = np.clip(g + (1 - g ** 2) / (2 * N_EFF)
                   + rng.normal(0, (1 - g ** 2) / np.sqrt(2 * N_EFF)), 0.0, 1.0)
    # true gamma = 0 -> |ghat|^2 ~ Beta(1, N-1): the ML bias floor, not zero
    null = np.sqrt(rng.beta(1.0, N_EFF - 1, size=g.shape))
    ghat = np.where(g > 0.02, ghat, null)
    return (ghat * np.exp(1j * ph)).astype(np.complex64), ghat.astype(np.float32)


c1, g1 = synth(defo, gamma_true, 1.00)
c2, g2 = synth(defo + CONST + ramp, gamma_true, 0.94)   # T2 loses 6%: 2 resamples


def write(path, arr, dt):
    d = gdal.GetDriverByName("GTiff").Create(path, NX, NY, 1, dt,
                                             ["COMPRESS=DEFLATE"])
    d.SetGeoTransform(GT)
    s = osr.SpatialReference(); s.ImportFromEPSG(EPSG)
    d.SetProjection(s.ExportToWkt())
    d.GetRasterBand(1).WriteArray(arr)
    d.GetRasterBand(1).SetNoDataValue(float("nan"))
    d.FlushCache()


write(f"{WORK}/t1.igram.tif", c1, gdal.GDT_CFloat32)
write(f"{WORK}/t1.coh.tif", g1, gdal.GDT_Float32)
write(f"{WORK}/t2.igram.tif", c2, gdal.GDT_CFloat32)
write(f"{WORK}/t2.coh.tif", g2, gdal.GDT_Float32)
write(f"{WORK}/water.tif", water, gdal.GDT_Float32)
write(f"{WORK}/dem.tif", dem.astype(np.float32), gdal.GDT_Float32)
write(f"{WORK}/inc.tif", inc.astype(np.float32), gdal.GDT_Float32)

print(f"synthetic truth: const {CONST:+.3f} rad, ramp "
      f"({RAMP_X_RAD_KM:+.3f}, {RAMP_Y_RAD_KM:+.3f}) rad/km, "
      f"|grad| {np.hypot(RAMP_X_RAD_KM, RAMP_Y_RAD_KM):.3f} rad/km, "
      f"T2 coherence x0.94\n")

cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "compare_tracks.py"),
       "--t1", f"{WORK}/t1", "--t2", f"{WORK}/t2",
       "--posting", "50", "--neff", "84", "--coh-min", "0.3",
       "--water", f"{WORK}/water.tif", "--dem", f"{WORK}/dem.tif",
       "--inc", f"{WORK}/inc.tif", "--report", f"{WORK}/report.txt"]
r = subprocess.run(cmd)
print(f"\ncompare_tracks.py exit = {r.returncode} "
      f"(1 is EXPECTED here: the synthetic Track 2 carries a deliberate ramp)")
