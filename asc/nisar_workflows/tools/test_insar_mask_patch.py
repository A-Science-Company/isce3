#!/usr/bin/env python
"""Prove the vectorised generate_insar_mask is bit-identical to the stock one.

Runs both against REAL frequency B data from the nepal_glof case, over a strip
of the interferogram grid, and compares byte for byte.
"""
import sys, time
sys.argv = [sys.argv[0]]
import importlib.util
from pathlib import Path
import numpy as np, h5py

CASE = Path('/home/sharath/isce3/case_studies/nepal_glof')
S = CASE / 'scratch/trackR/20260714_20260726_B_HH_9x1'
HERE = Path(__file__).resolve().parent

from nisar.products.readers import SLC
from nisar.products.insar.utils import generate_insar_mask as stock

spec = importlib.util.spec_from_file_location('vec', HERE / 'patches/insar_mask_vectorized.py')
vec = importlib.util.module_from_spec(spec); spec.loader.exec_module(vec)

import json
stack = json.loads((CASE / 'stack.json').read_text())
g = {x['date']: x['path'] for x in stack['granules']}
ref = SLC(hdf5file=g['20260714']); sec = SLC(hdf5file=g['20260726'])
rh = h5py.File(g['20260714'], 'r', libver='latest', swmr=True)
sh = h5py.File(g['20260726'], 'r', libver='latest', swmr=True)

rg_off = str(S / 'geo2rdr/freqB/range.off')
az_off = str(S / 'geo2rdr/freqB/azimuth.off')

grid = ref.getRadarGrid('B')
# Sample the interferogram grid the way InSAR_L1_writer does: 9x1 looks
az_idx = np.round(np.arange(0, 53200, 9)[:400]).astype(float)
rg_idx = np.round(np.arange(0, 6781, 1)).astype(float)
print(f'test grid: {len(az_idx)} az x {len(rg_idx)} rg = {len(az_idx)*len(rg_idx)/1e6:.2f} Mpx')

t0 = time.time(); a = stock(ref, sec, rh, sh, rg_off, az_off, 'B', az_idx, rg_idx); t_stock = time.time()-t0
t0 = time.time(); b = vec.generate_insar_mask(ref, sec, rh, sh, rg_off, az_off, 'B', az_idx, rg_idx); t_vec = time.time()-t0

print(f'stock      {t_stock:7.2f} s   shape {a.shape} {a.dtype}')
print(f'vectorised {t_vec:7.2f} s   shape {b.shape} {b.dtype}   speedup {t_stock/max(t_vec,1e-9):.0f}x')
same = np.array_equal(a, b)
print(f'\nBIT-IDENTICAL: {same}')
if not same:
    d = np.argwhere(a != b)
    print(f'  {len(d)} differing pixels of {a.size}')
    for r, c in d[:5]:
        print(f'   [{r},{c}] stock={a[r,c]} vec={b[r,c]}')
    sys.exit(1)
print(f'unique mask values: {sorted(np.unique(a).tolist())}')
rh.close(); sh.close()
