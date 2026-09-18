"""
Vectorised replacement for nisar.products.insar.utils.generate_insar_mask.

WHY
---
The stock implementation builds the mask with a pure-Python double loop that
appends one Python int per output pixel to a list, then materialises it:

    mask = []
    for i in azi_idx_arr:
        for j in rg_idx_arr:
            ...
            mask.append(mask_id)
    return np.array(mask).reshape(...).astype(np.uint32)

It is called TWICE (nisar/products/insar/InSAR_L1_writer.py:535 and :752). The
first call is on the pixel-offsets grid and is harmless. The second is on the
INTERFEROGRAM grid, whose size is set by `crossmul` looks:

    freq B @ 9x1   ->  5911 x  6781 =    40 Mpx    (fine)
    freq A @ 1x1   -> 53200 x 54244 =  2886 Mpx    (fatal)

At 1x1 on frequency A that is 2.886e9 loop iterations and a 2.886e9-element
Python list, followed by an int64 numpy array (23 GB) and a uint32 copy
(11.5 GB). It exhausts a 31 GB box and takes hours. This is a distinct problem
from the full-swath `inputDataExceptionMask` reads (2.89 GB per image), which
are what killed the 3.9 GB box earlier.

WHAT THIS CHANGES
-----------------
Nothing observable. The output is bit-identical -- see
tools/test_insar_mask_patch.py, which checks this implementation against the
stock one on real frequency B data. The rewrite:

  * preallocates the uint32 output instead of accumulating a list, removing the
    list and the int64 intermediate entirely (11.5 GB peak instead of ~58 GB);
  * vectorises the inner range loop with numpy, keeping only the azimuth loop
    in Python (53200 iterations rather than 2.886e9).

SEMANTICS PRESERVED EXACTLY -- both rounding conventions differ and both matter:
  * the sub-swath lookup uses  int(v + 0.5)   (truncation toward zero)
  * the exception-mask lookup uses  round(v)  (half-to-even)
so they are reproduced with np.trunc and np.rint respectively, not with one
shared rule.

Assumes the single-sub-swath layout these products use (numberOfSubSwaths == 1
on both frequencies); for more sub-swaths it falls back to the stock path so it
can never silently produce a wrong answer.
"""

from __future__ import annotations

import numpy as np
from osgeo import gdal


def generate_insar_mask(ref_rslc_obj,
                        sec_rslc_obj,
                        ref_rslc_h5_obj,
                        sec_rslc_h5_obj,
                        range_offset_path,
                        azimuth_offset_path,
                        freq,
                        azi_idx_arr,
                        rg_idx_arr):
    """
    Drop-in replacement. Same signature, same return: uint32 array of shape
    (len(azi_idx_arr), len(rg_idx_arr)).
    """
    ref_swath = ref_rslc_obj.getSwathMetadata(freq)
    sec_swath = sec_rslc_obj.getSwathMetadata(freq)
    ref_subswaths = ref_swath.sub_swaths()
    sec_subswaths = sec_swath.sub_swaths()

    # More than one sub-swath is outside what the vectorised lookup below
    # models. Defer rather than guess.
    if ref_subswaths.num_sub_swaths != 1 or sec_subswaths.num_sub_swaths != 1:
        from nisar.products.insar.utils import generate_insar_mask as _stock
        return _stock(ref_rslc_obj, sec_rslc_obj, ref_rslc_h5_obj,
                      sec_rslc_h5_obj, range_offset_path, azimuth_offset_path,
                      freq, azi_idx_arr, rg_idx_arr)

    src_range_offset = gdal.Open(range_offset_path)
    src_azimuth_offset = gdal.Open(azimuth_offset_path)
    range_offset_band = src_range_offset.GetRasterBand(1)
    azimuth_offset_band = src_azimuth_offset.GetRasterBand(1)

    def _load_exception_mask(h5_obj, rslc_obj, swath):
        path = f"{rslc_obj.SwathPath}/frequency{freq}/inputDataExceptionMask"
        return h5_obj[path][()].astype(np.uint8) if path in h5_obj \
            else np.zeros((swath.lines, swath.samples), dtype=np.uint8)

    ref_exc = _load_exception_mask(ref_rslc_h5_obj, ref_rslc_obj, ref_swath)
    sec_exc = _load_exception_mask(sec_rslc_h5_obj, sec_rslc_obj, sec_swath)

    # [start, stop) valid range-sample bounds per azimuth line
    ref_valid = np.asarray(ref_subswaths.get_valid_samples_array(1))
    sec_valid = np.asarray(sec_subswaths.get_valid_samples_array(1))

    azi = np.asarray(azi_idx_arr).astype(np.int64)
    rg = np.asarray(rg_idx_arr).astype(np.int64)
    n_az, n_rg = azi.size, rg.size

    out = np.zeros((n_az, n_rg), dtype=np.uint32)

    ref_lines, ref_samples = ref_swath.lines, ref_swath.samples
    sec_lines, sec_samples = sec_swath.lines, sec_swath.samples

    # Range indices inside the reference swath; everything else stays 0.
    rg_ok = (rg >= 0) & (rg < ref_samples)
    rg_in = rg[rg_ok]
    if rg_in.size == 0:
        return out

    for row, i in enumerate(azi):
        # Azimuth index outside the reference grid -> whole row is 0
        if i < 0 or i >= ref_lines:
            continue

        # One row of offsets, full reference width (matches the stock read)
        rg_off_row = range_offset_band.ReadAsArray(0, int(i), ref_samples, 1)[0]
        az_off_row = azimuth_offset_band.ReadAsArray(0, int(i), ref_samples, 1)[0]
        rg_off = rg_off_row[rg_in]
        az_off = az_off_row[rg_in]

        # -- reference sub-swath number -------------------------------------
        # get_sample_sub_swath: 1 when start <= j < stop for that line, else 0.
        r0, r1 = ref_valid[i, 0], ref_valid[i, 1]
        ref_num = ((rg_in >= r0) & (rg_in < r1) & (r0 <= r1)).astype(np.int64)

        # -- secondary sub-swath number -- int(v + 0.5), truncation ---------
        s_az = np.trunc(i + az_off + 0.5).astype(np.int64)
        s_rg = np.trunc(rg_in + rg_off + 0.5).astype(np.int64)
        s_in = (s_az >= 0) & (s_az < sec_lines) & \
               (s_rg >= 0) & (s_rg < sec_samples)
        sec_num = np.zeros_like(ref_num)
        if s_in.any():
            az_c = s_az[s_in]
            v0 = sec_valid[az_c, 0]
            v1 = sec_valid[az_c, 1]
            rc = s_rg[s_in]
            sec_num[s_in] = ((rc >= v0) & (rc < v1) & (v0 <= v1)).astype(np.int64)

        subswath_id = 10 * ref_num + sec_num

        # -- reference exception mask ---------------------------------------
        ref_id = ref_exc[i, rg_in].astype(np.uint32) << 16

        # -- secondary exception mask -- round(v), half-to-even -------------
        e_az = np.rint(i + az_off).astype(np.int64)
        e_rg = np.rint(rg_in + rg_off).astype(np.int64)
        e_in = (e_az >= 0) & (e_az < sec_lines) & \
               (e_rg >= 0) & (e_rg < sec_samples)
        sec_id = np.zeros(rg_in.size, dtype=np.uint32)
        if e_in.any():
            sec_id[e_in] = sec_exc[e_az[e_in], e_rg[e_in]].astype(np.uint32) << 8

        out[row, rg_ok] = (subswath_id.astype(np.uint32) | ref_id | sec_id)

    del ref_exc, sec_exc
    return out
