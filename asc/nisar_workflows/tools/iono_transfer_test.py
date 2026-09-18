#!/usr/bin/env python3
"""
Can a crop-first RSLC interferogram be ionosphere-corrected to the full-tile benchmark's result?

    python -u tools/iono_transfer_test.py          # after compare_four_way.py v2

Read-only. Writes <case>/comparison_v2/iono_transfer/iono_transfer.json.

Options applied to the crop-first (R_crop) interferogram, each compared with the benchmark
(R_full interferogram corrected by R_full's own ISCE3 main_side_band screen):

  A   full-tile screen, sliced to the crop (exact: the crop origin is on the 9x8 look lattice)
  C   the crop's own screen, as ISCE3 produced it
  D   the crop's own screen after the GSLC tool's cycle-class rule (tools/gslc_ionosphere.py
      resolve_cycle_offsets: class d = m - n by smallest |median non-dispersive|, then minimal norm,
      ties by |median non-dispersive|); both minimal-norm members of the class are reported

Two levels: the 9x8 unwrapped phase (where ISCE3 stores the screen), and the 1x1 wrapped interferogram on the
5 m comparison lattice with the screens bilinearly interpolated from 9x8 cell centres.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

_ARGV = sys.argv[1:]
sys.argv = [sys.argv[0]]

import h5py  # noqa: E402
from scipy.ndimage import map_coordinates  # noqa: E402

TOOLS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cfw", TOOLS / "compare_four_way.py")
cfw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfw)

C = Path("/home/sharath/isce3/case_studies/nepal_glof")
TAG = "20260714_20260726_A_HH_1x1"
CMP = C / "comparison_v2"
OUT = CMP / "iono_transfer"
RUNW_F = C / "pairs/20260714_20260726/trackR" / f"RUNW_{TAG}_unw9x8.h5"
RUNW_C = C / "aoi_v2/pairs/20260714_20260726/trackR" / f"RUNW_{TAG}_unw9x8.h5"
RA = cfw.RUNW_A
T0 = time.time()
MM_PER_RAD = 299792458.0 / 1.239e9 / (4 * math.pi) * 1000


def log(m):
    print(f"[{time.time() - T0:6.1f}s] {m}", flush=True)


def stats(d):
    off = float(np.median(d))
    r = d - off
    return {"n": int(d.size), "median_offset_rad": off, "median_offset_mm": off * MM_PER_RAD,
            "residual_std_rad": float(r.std()), "residual_std_mm": float(r.std() * MM_PER_RAD),
            "residual_p95_abs_rad": float(np.percentile(np.abs(r), 95)),
            "offset_in_2pi_cycles": off / (2 * math.pi)}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "iono_transfer.json").exists() and "--force" not in _ARGV:
        raise SystemExit("iono_transfer.json exists; pass --force")
    rep = json.loads((CMP / "comparison.json").read_text())
    AZ0, RG0 = rep["crop_geometry"]["reference_A"][:2]
    WR0, WR1, WC0, WC1 = rep["radar_data_window_crop_frame"]
    r0, c0 = AZ0 // 9, RG0 // 8
    assert AZ0 % 9 == 0 and RG0 % 8 == 0, "crop origin not on the 9x8 lattice"
    RAD_A, RAD_B = cfw.RAD_PER_CYCLE_A, cfw.RAD_PER_CYCLE_B
    out = {"tool": "iono_transfer_test v1", "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "mm_per_rad": MM_PER_RAD, "rad_per_cycle": {"A": RAD_A, "B": RAD_B, "joint": RAD_A + RAD_B}}

    # ------------------------------------------------------------ 9x8 unwrapped level
    log("9x8 unwrapped")
    with h5py.File(RUNW_C, "r") as hc, h5py.File(RUNW_F, "r") as hf:
        un_c = hc[f"{RA}/HH/unwrappedPhase"][()].astype(np.float64)
        sc_c = hc[f"{RA}/HH/ionospherePhaseScreen"][()].astype(np.float64)
        n, w = un_c.shape
        un_f = hf[f"{RA}/HH/unwrappedPhase"][r0:r0 + n, c0:c0 + w].astype(np.float64)
        sc_f = hf[f"{RA}/HH/ionospherePhaseScreen"][r0:r0 + n, c0:c0 + w].astype(np.float64)
        sc_full_frame = hf[f"{RA}/HH/ionospherePhaseScreen"][()].astype(np.float32)
    m9 = np.zeros((n, w), bool)
    m9[WR0 // 9:WR1 // 9, WC0 // 8:WC1 // 8] = True
    m9 &= np.isfinite(un_c) & np.isfinite(un_f) & np.isfinite(sc_c) & np.isfinite(sc_f) & (un_c != 0) & (un_f != 0) & (sc_c != 0) & (sc_f != 0)
    bench = un_f - sc_f

    # cycle-class rule on the crop alone
    nd0 = float(np.median((un_c - sc_c)[m9]))
    table = []
    for mm in range(-3, 4):
        for nn in range(-3, 4):
            nd = nd0 + 2 * math.pi * mm - (RAD_A * mm + RAD_B * nn)
            table.append({"m": mm, "n": nn, "median_nondispersive_rad": nd, "abs": abs(nd)})
    best_d = min(table, key=lambda t: t["abs"])
    d_class = best_d["m"] - best_d["n"]
    in_class = sorted([t for t in table if t["m"] - t["n"] == d_class], key=lambda t: (abs(t["m"]) + abs(t["n"]), t["abs"]))
    minimal = [t for t in in_class if abs(t["m"]) + abs(t["n"]) == abs(in_class[0]["m"]) + abs(in_class[0]["n"])]
    out["cycle_rule"] = {"median_nondispersive_crop_rad": nd0, "class_d": d_class, "selected": {"m": in_class[0]["m"], "n": in_class[0]["n"]},
                         "minimal_norm_members": [{"m": t["m"], "n": t["n"], "median_nondispersive_rad": t["median_nondispersive_rad"]} for t in minimal],
                         "top5_by_abs_nondispersive": [{k: t[k] for k in ("m", "n", "median_nondispersive_rad")} for t in sorted(table, key=lambda t: t["abs"])[:5]]}
    variants = {"A_full_tile_screen_sliced": (un_c, sc_f), "C_crop_own_screen": (un_c, sc_c)}
    for t in minimal:
        variants[f"D_crop_screen_cycles_m{t['m']:+d}_n{t['n']:+d}"] = (un_c + 2 * math.pi * t["m"], sc_c + RAD_A * t["m"] + RAD_B * t["n"])
    out["unwrapped_9x8_minus_benchmark"] = {}
    for name, (u, s) in variants.items():
        out["unwrapped_9x8_minus_benchmark"][name] = stats(((u - s) - bench)[m9])
        out["unwrapped_9x8_minus_benchmark"][name]["screen_minus_benchmark_screen"] = stats((s - sc_f)[m9])
        log(f"  {name}: corrected-phase offset {out['unwrapped_9x8_minus_benchmark'][name]['median_offset_rad']:+.3f} rad, "
            f"residual {out['unwrapped_9x8_minus_benchmark'][name]['residual_std_rad']:.4f} rad "
            f"({out['unwrapped_9x8_minus_benchmark'][name]['residual_std_mm']:.2f} mm)")
    out["unwrapped_9x8_uncorrected_crop_minus_full"] = stats((un_c - un_f)[m9])

    # ------------------------------------------------------------ 1x1 wrapped on the 5 m lattice
    log("1x1 wrapped on the 5 m lattice")
    aoi = cfw.read_tif(CMP / "aoi_polygon_mask.tif").astype(bool)
    lut = CMP / "layers" / "lut_fullframe_rowcol.tif"
    RI, CI, RES = cfw.read_tif(lut, 1), cfw.read_tif(lut, 2), cfw.read_tif(lut, 3)
    valid = (RI >= 0) & (CI >= 0)
    common = aoi & valid & (RES <= 15.0)
    del RES
    rf, rc = cfw.read_tif(CMP / "layers" / "R_full_ifg.tif"), cfw.read_tif(CMP / "layers" / "R_crop_ifg.tif")
    common &= (rf != 0) & (rc != 0)
    rows, cols = RI[common].astype(np.float64), CI[common].astype(np.float64)

    def sample(screen, row_origin, col_origin):
        return map_coordinates(screen, [(rows - row_origin * 9 - 4) / 9, (cols - col_origin * 8 - 3.5) / 8], order=1, mode="nearest")

    s_bench = sample(sc_full_frame, 0, 0)
    zb = rf[common] * np.exp(-1j * s_bench)
    NY, NX = common.shape
    screens = {"A_full_tile_screen_sliced": (sample(sc_full_frame, 0, 0), 0),
               "C_crop_own_screen": (sample(sc_c.astype(np.float32), r0, c0), 0)}
    for t in minimal:
        screens[f"D_crop_screen_cycles_m{t['m']:+d}_n{t['n']:+d}"] = (sample((sc_c + RAD_A * t["m"] + RAD_B * t["n"]).astype(np.float32), r0, c0), 0)
    out["wrapped_1x1_vs_benchmark"] = {}
    B = np.zeros((NY, NX), np.complex64)
    B[common] = zb
    B8 = cfw.multilook(B, 8)
    m8 = cfw.multilook(common.astype(np.float32), 8) >= 0.75
    for name, (s, _) in screens.items():
        Z = np.zeros((NY, NX), np.complex64)
        Z[common] = rc[common] * np.exp(-1j * s)
        e5 = cfw.phase_agreement(B, Z, common, 0.005, quadrants=False, plane=False)
        e40 = cfw.phase_agreement(B8, cfw.multilook(Z, 8), m8, 0.04, quadrants=False, plane=False)
        out["wrapped_1x1_vs_benchmark"][name] = {"5m": {k: e5[k] for k in ("n", "phase_diff_coherence", "constant_offset_rad", "circular_std_rad")},
                                                 "40m": {k: e40[k] for k in ("n", "phase_diff_coherence", "constant_offset_rad", "circular_std_rad")}}
        log(f"  {name}: 5 m R {e5['phase_diff_coherence']:.4f} offset {e5['constant_offset_rad']:+.3f} rad; 40 m R {e40['phase_diff_coherence']:.4f}")
    Z = np.zeros((NY, NX), np.complex64)
    Z[common] = rc[common]
    B0 = np.zeros((NY, NX), np.complex64)
    B0[common] = rf[common]
    e5 = cfw.phase_agreement(B0, Z, common, 0.005, quadrants=False, plane=False)
    out["wrapped_1x1_uncorrected_R_full_vs_R_crop_5m"] = {k: e5[k] for k in ("n", "phase_diff_coherence", "constant_offset_rad")}
    (OUT / "iono_transfer.json").write_text(json.dumps(out, indent=2))
    log(f"wrote {OUT / 'iono_transfer.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
