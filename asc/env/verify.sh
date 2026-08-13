#!/usr/bin/env bash
# Prove isce3_env and dolphin_env actually work.
#   bash asc/env/verify.sh
# Run from anywhere EXCEPT <repo>/python/packages -- that directory contains
# source isce3/ and nisar/ trees which shadow the installed packages.
set -uo pipefail

fail=0
hr() { printf '\n=== %s ===\n' "$1"; }

hr "isce3_env"
conda run --no-capture-output -n isce3_env python - <<'PY' || fail=1
import sys, os
print("python            ", sys.version.split()[0])

import isce3
import isce3.ext.isce3 as _x
print("isce3             ", isce3.__version__)
# Must be False on this box (no NVIDIA GPU). If True, the solver picked a
# _cuda build and every workflow with gpu_enabled will behave differently.
print("cuda in build     ", hasattr(_x, "cuda"), " <- must be False here")

import isce3.geometry, isce3.geocode, isce3.image, isce3.signal
import isce3.unwrap, isce3.matchtemplate, isce3.product, isce3.core
print("isce3 subpackages  ok")

# Smoke-test the compiled extension, not just the import.
ell = isce3.core.make_projection(4326).ellipsoid
print("ellipsoid xyz     ", ell.lon_lat_to_xyz([0.0, 0.0, 0.0]))

import nisar
from nisar.workflows import insar, gslc, gcov, h5_prep, dumpconfig, helpers
print("nisar package     ", os.path.dirname(nisar.__file__))

# The runconfig defaults/schemas must ship with the conda package, else
# RunConfig blows up at load time and you must copy share/nisar/ in by hand.
d = os.path.join(helpers.WORKFLOW_SCRIPTS_DIR, "defaults")
if os.path.isdir(d):
    print("runconfig defaults", sorted(os.listdir(d)))
else:
    print("runconfig defaults MISSING at", d)
    print("  -> cp -r share/nisar/{defaults,schemas} into", helpers.WORKFLOW_SCRIPTS_DIR)
    raise SystemExit(1)

import compass, s1reader, snaphu, asf_search, sardem
print("compass/s1reader   ok")

from osgeo import gdal
import numpy, scipy, h5py, yaml, pyproj, lxml.etree, skimage, rasterio
print("gdal              ", gdal.__version__)
print("numpy / scipy     ", numpy.__version__, scipy.__version__)
print("h5py / hdf5       ", h5py.__version__, h5py.version.hdf5_version)
print("pyproj            ", pyproj.__version__)
print("rasterio          ", rasterio.__version__)

# h5py 3.16 is compiled against HDF5 2.1.0 but libgdal-hdf5 pulls 2.2.0, so it
# emits a version-skew UserWarning on import. Prove the skew is benign rather
# than trusting the warning either way.
import tempfile, numpy as np
with tempfile.NamedTemporaryFile(suffix=".h5") as t:
    a = (np.arange(64, dtype=np.complex64).reshape(8, 8) * (1 + 2j))
    with h5py.File(t.name, "w") as f:
        f.create_dataset("/data/VV", data=a, compression="gzip")
    with h5py.File(t.name, "r") as f:
        b = f["/data/VV"][:]
    assert np.array_equal(a, b), "HDF5 round-trip MISMATCH"
print("hdf5 round-trip    ok (complex64 + gzip)")
PY

hr "isce3_env CLIs"
conda run --no-capture-output -n isce3_env bash -lc \
  'for c in s1_cslc.py s1_geocode_stack.py burst2stack eof sardem; do
     printf "%-22s %s\n" "$c" "$(command -v $c || echo MISSING)"
   done' || fail=1

hr "dolphin_env"
conda run --no-capture-output -n dolphin_env python - <<'PY' || fail=1
import sys
print("python            ", sys.version.split()[0])
import dolphin, opera_utils, isce3, mintpy, rasterio, snaphu
import numpy, scipy
print("dolphin           ", dolphin.__version__)
print("opera_utils       ", opera_utils.__version__)
print("isce3             ", isce3.__version__)
print("rasterio          ", rasterio.__version__)
print("numpy / scipy     ", numpy.__version__, scipy.__version__)
PY

hr "dolphin CLI"
conda run --no-capture-output -n dolphin_env dolphin config --help 2>&1 | head -5 || fail=1

hr "result"
if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "SOMETHING FAILED (see above)"; fi
exit "$fail"
