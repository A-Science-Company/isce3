# WF1 — RSLC full tile (Track R): coregistration, interferogram, coherence, unwrapping, split-spectrum ionosphere

**Role in the comparison:** the quality **benchmark** (`R_full`). The other three workflows are judged as degradation relative to this one.

**Evidence convention.** Measured numbers cite a log, a product, or the verification files in
`case_studies/nepal_glof/comparison/verification/` (`errors_W1_rslc_full.json`, `verdicts.json`, `critic.json`).
Installed ISCE3/nisar source is cited as `nisar/…` or `isce3/…` relative to
`/home/sharath/miniforge3/envs/isce3_env/lib/python3.12/site-packages/`. Repository paths are relative to
`asc/nisar_workflows/`; case paths are relative to `case_studies/nepal_glof/` (written `C/`).
Anything not verified is marked **UNVERIFIED**; unresolved questions are marked **OPEN**.

---

## 1. Purpose, scope, and when to use it

WF1 runs ISCE3's integrated InSAR workflow (`nisar.workflows.insar`) on two **full** NISAR L1 RSLC granules and
keeps every product in **radar coordinates** of the reference acquisition. It is the only workflow here that:

- coregisters the secondary onto the reference with **data-driven dense offsets** (ampcor + rubbersheet) on top of
  the geometric geo2rdr solution;
- produces the native NISAR InSAR L1 products (RIFG, RUNW) with their full metadata;
- runs ISCE3's own split-spectrum ionosphere estimate (`main_side_band`) without any ported code.

**Use it** when the product must be the reference-quality answer, when downstream consumers expect RIFG/RUNW/GUNW,
or when a crop-first run (WF3) needs a benchmark.

**Do not use it** for a quick look over a small AOI (WF3 reproduces its wrapped phase to phase-difference coherence
≈0.99 at a fraction of the cost — see COMPARISON), or when products are wanted on a map lattice shared across many
dates (WF2).

Case run here: pair **20260714 (reference) × 20260726 (secondary)**, track 98, frame 16, ascending, left-looking,
frequency **A**, polarization **HH**, crossmul looks **1×1**.

## 2. Inputs

### 2.1 RSLC granules

| date | granule (in `C/L1_RSLC/`) | size |
|---|---|---|
| 20260714 | `NISAR_L1_PR_RSLC_025_098_A_016_4005_DHDH_A_20260714T233920_20260714T233954_P05023_F_F_J_001.h5` | 26.26 GB |
| 20260726 | `NISAR_L1_PR_RSLC_026_098_A_016_4005_DHDH_A_20260726T233919_20260726T233954_P05023_N_F_J_001.h5` | 27.01 GB |

Structure used by the chain (read from the granules, `/science/LSAR/RSLC/swaths`):

| dataset | freq A | freq B |
|---|---|---|
| `HH`, `HV` (complex64) | 53200 × 54244 | 53200 × 6781 |
| `slantRange` spacing | 3.1228 m | 24.9827 m |
| `zeroDopplerTime` | **one shared axis** (53200 lines, 1/1520 s) | same |
| `validSamplesSubSwath1` | (53200, 2), absolute sample indices | same |
| `inputDataExceptionMask` | uint8, full grid | full grid |

Properties the chain silently relies on, **verified on both granules**:

- Frequency B is an exact 8:1 range decimation of frequency A: identical starting slant range and
  `slantRangeA[8k] == slantRangeB[k]`.
- The products are **zero-Doppler**: `nisar/workflows/rdr2geo.py:64-65` and `geo2rdr.py:52-53` pass an empty
  `isce3.core.LUT2d()` ("NISAR RSLC products are always zero doppler"). The native Doppler centroid
  (`getDopplerCentroid`, ~957–983 Hz) is used only for carrier handling during resampling.
- `RadarGridParameters.prf` returns the **line rate** 1520 Hz (= 1/`az_time_interval`), not the acquisition PRF
  (1909.635 Hz).
- The granules carry **no TEC** datasets, and no IONEX reader exists in the installed packages
  (WF1-16 below), so no ionospheric geolocation correction is applied.

### 2.2 DEM

`C/aux/dem/dem_nepal_glof.tif`, WGS84-ellipsoidal heights, staged by `nisar_wf/dem.py` with a 0.15° buffer
(`configs/nepal_glof.yaml` `dem:` block). ISCE3's "DEM bounds" warning fires for any windowed DEM because
`Topo.cpp` brackets heights with the hard-coded globals −500/+9000 m — it is not evidence of insufficient coverage
(WF1-04).

### 2.3 Configuration

`configs/nepal_glof.yaml`, `track_r:` block (lines 472–688). **Caveat:** that block still sets
`frequency: B` (:478) and freq-A looks 1×1 / freq-B 9×1 in `looks:` (:501); the benchmark was run with
`--frequency A --looks 1 1` on the command line. The rendered runconfig ISCE3 actually consumed is
`C/cfg/insar_20260714_20260726_A_HH_1x1.yaml` (cited as `cfg:` below); the RUNW products also store it in
`runConfigurationContents`.

### 2.4 Preconditions to check before running

| check | how | pass |
|---|---|---|
| overlays installed | `python tools/apply_patches.py --check` | every patch "already applied" (section 7) |
| granule inventory | h5py walk of both granules | datasets of 2.1 present, A/B 8:1 relation holds |
| same track/frame/direction | `stack.json` from ingest | identical |
| disk | `--only runconfig` prints the bill (104.4 B/px model, 8.1) | free ≥ bill + `min_free_gb` (60) |
| RAM | section 8.1 | ≥ 31 GB for freq A 1×1 with the patches |
| no running job on the same scratch | `tmux ls`, `pgrep -af "[n]isar.workflows"` | none |

## 3. Outputs

| artifact | path pattern | format | measured here |
|---|---|---|---|
| stack description | `C/stack.json` | JSON | granules, dates, pinned geogrid (used by WF2) |
| rendered runconfig | `C/cfg/insar_{ref}_{sec}_{F}_{P}_{ly}x{lx}.yaml` | YAML | see `cfg:` |
| scratch tree | `C/scratch/trackR/{ref}_{sec}_{F}_{P}_{ly}x{lx}/` | ENVI + HDF5 | up to ~301 GB for freq A 1×1 (8.1); 46 GB of it deleted during the run (WF1-38) |
| wrapped interferogram | `…/scratch/trackR/<tag>/RIFG.h5` | NISAR RIFG | **28.57 GB**, freq A 53200 × 54244; `coherenceMagnitude` ≡ 1.0 at 1×1 |
| unwrapped, 1×1 | `C/pairs/{ref}_{sec}/trackR/RUNW_<tag>.h5` | NISAR RUNW | **17.38 GB**, 53200 × 54244; **ionosphere disabled → `ionospherePhaseScreen` all zeros** |
| unwrapped, 9×8 + ionosphere | `C/pairs/{ref}_{sec}/trackR/RUNW_<tag>_unw9x8.h5` | NISAR RUNW | **0.61 GB**, 5911 × 6780; carries the ionosphere screen |
| 3×3 coherence | `C/pairs/{ref}_{sec}/trackR/coherence_A_HH_win3.tif` | GeoTIFF (radar grid) | **10.13 GB**, median 0.5739, bias floor 0.2954 in metadata |
| ISCE3 journal | `C/logs/insar_<tag>.log` | text | appended across runs (`write_mode: a`, cfg:94) |
| wrapper log | `C/logs/track_r_<UTC stamp>.log` | text | one per invocation |

**Output-identity rule.** Everything that changes a product's bytes must be in its name:
`tag = {ref}_{sec}_{freq}_{pol}_{crossmul_ly}x{crossmul_lx}`, and the RUNW gains `_unw{ly}x{lx}` when the unwrap
looks differ from the crossmul looks (`nisar_wf/trackr.py:261-278`). Without that suffix the 9×8 pass would have
overwritten the 12.7 h 1×1 RUNW (WF1-32). **Gap still open:** the runconfig and journal names do not carry the
unwrap looks, so the 1×1 unwrap's runconfig was overwritten by the 9×8 pass and survives only inside the RUNW.

## 4. Processing chain

Driver stages (`python run_track_r.py --list-steps`): `ingest → dem → runconfig → insar → qa`. The `insar` stage
calls `nisar.workflows.insar` with the rendered runconfig. Runtimes are for the 31 GB / 8-core VM unless marked
"2c" (the earlier 3.9 GB / 2-core VM).

| # | stage | entry point | what it does | key settings used (`cfg:` line) | why | measured |
|---|---|---|---|---|---|---|
| 1 | ingest | `nisar_wf/ingest.py` | read identification/swath metadata; write `stack.json` | — | one description shared with WF2 | seconds |
| 2 | dem | `nisar_wf/dem.py` | stage an ellipsoidal DEM with buffer | `dem.buffer_deg 0.15` | Topo needs heights beyond the swath | ~20 s |
| 3 | runconfig | `nisar_wf/trackr.py` | render, yamale-validate, disk gate | `block_budget_mb 256` → rdr2geo 206, geo2rdr 309 lines/block (cfg:30,34) | block memory = lines × width; width depends on frequency | seconds |
| 4a | rdr2geo | `nisar/workflows/rdr2geo.py` | lon/lat/height per reference pixel | threshold 1e-7, numiter 25 (cfg:27-30) | zero-Doppler geometry | **1h39m**; 13h05m 2c |
| 4b | geo2rdr | `nisar/workflows/geo2rdr.py` | secondary range/azimuth offsets from orbit + DEM | threshold 1e-8, maxiter 25 (cfg:32-34) | geometric coregistration | **6.6 min**; 22 min 2c |
| 4c | coarse_resample | `nisar/workflows/resample_slc_v2.py` (**overlay**) | resample secondary with geo2rdr offsets | 1000 × 1000 tiles (cfg:36-37) | input to dense offsets | UNVERIFIED for the 8-core run |
| 4d | dense_offsets | `nisar/workflows/dense_offsets.py` (ampcor) | correlation offsets on a 32-sample grid | window 64×64, half-search 20×20, skip 32×32 (cfg:41-46) | data-driven residual registration | freq-A residual medians 0.566 m along-track (0.13 lines), 0.736 m range (0.236 samples) (`RUNW …/pixelOffsets`, verdicts V3) |
| 4e | rubbersheet | `nisar/workflows/rubbersheet.py` | cull, fill, filter offsets; stretch to full grid | defaults | smooth offset field for resampling | writes 32 B/px of full-grid rasters |
| 4f | fine_resample | `resample_slc_v2.py` | resample secondary with rubbersheet offsets | 100 lines/tile (cfg:51) | final coregistration | UNVERIFIED |
| 4g | crossmul | `nisar/workflows/crossmul.py` | `ref × conj(sec)`, flattening | looks 1×1, flatten true, oversample 2, 154 lines/block (cfg:56-60) | full-resolution interferogram | coherence **hard-coded 1.0** at 1×1 (`cxx/isce3/signal/Crossmul.cpp:378-388`) |
| 4h | RIFG writer | `nisar/products/insar/InSAR_L1_writer.py` + `utils.generate_insar_mask` (**overlay**) | write RIFG, masks, DEM layer | — | product assembly | the interferogram-grid DEM pass (`RIFG_ifgram_dem`) is a second full-resolution Topo at 1×1: **1h37m**; 13h33m 2c |
| 4i | phase_unwrap | `nisar/workflows/unwrap.py` (**overlay**) → snaphu | unwrap on a multilooked grid | 9×8 pass: looks rg 8 az 9, nlooks 44.57, ntiles 4×4, overlap 256, nproc 8, single_tile_reoptimize **false**, regrow_conncomps **false**, bridge on (cfg:63-80) | tiling bounds RAM; the two defaults re-solve the full grid | 9×8: ~23 min; 1×1: snaphu 12h40m wall / 93.9 CPU-h (journal 12.84 h) |
| 4j | ionosphere | `nisar/workflows/ionosphere.py` (`main_side_band`) | dispersive/non-dispersive split from A and B | dispersive filter on, coherence threshold 0.5, median 15 (cfg:81-91); Gaussian 100 px / σ 33 px (defaults) | two-band split spectrum | **27 min** (journal "Ionosphere in 1626.8 s") |
| 4k | baseline | `nisar/workflows/baseline.py` | perpendicular/parallel baseline cubes | — | metadata | seconds |
| 5 | qa | `nisar_wf/trackr.py` | decimated coherence summary | — | cheap sanity check | seconds |
| + | 3×3 coherence | `tools/slc_coherence.py` | moving-window coherence at full resolution | `--win 3` | ISCE3 has none at 1×1 | ~10 min |

As executed for the benchmark, the unwrap and ionosphere were not one integrated pass: the RIFG came from the
integrated run (8h34m, ended by the `nlooks` ValueError, WF1-21), the 1×1 RUNW from a standalone unwrap
(ionosphere disabled), and the 9×8 RUNW with ionosphere from a standalone unwrap pointed at the finished RIFG
(`phase_unwrap.crossmul_path`, cfg:67). The cropped leg (WF3) ran the integrated path; this is a recorded confound
(WF3 log).

### 4.1 Non-obvious behaviour, stage by stage

- **Product type gates stages.** The ionosphere stage runs only when `RUNW` or `GUNW` is among the outputs
  (`nisar/workflows/insar.py:120-124`); under `RIFG` it is skipped **silently**. The driver refuses
  `ionosphere_enabled: true` with `product_type: RIFG`. Troposphere is GUNW-only (`insar.py:150-152`).
- **Looks are applied at crossmul, the last expensive stage.** rdr2geo, geo2rdr, both resamples, dense offsets and
  rubbersheet run at full resolution whatever the looks. Looks **do** set the interferogram grid, and with it
  the `RIFG_ifgram_dem` Topo pass (11.5 GB and 1h37m at 1×1) and the size of the interferogram-grid mask
  (4h below) — so "looks don't change the cost" is false for those two.
- **Coherence at 1×1 is meaningless.** `Crossmul.cpp` estimates coherence only over the look box and writes 1.0
  when both looks are 1. The RIFG and the 1×1 RUNW therefore carry a constant 1.0 layer. `tools/slc_coherence.py`
  supplies a 3×3 moving-window estimate; its bias floor is √π/(2√N) = 0.295 for N = 9, so fully decorrelated
  ground reads ≈0.3, not 0. It is computed from the **unflattened** SLC pair (WF1-41); the comparison tool v2 also
  computes the flattened form from the RIFG product.
- **The 1×1 unwrap was solved on a degenerate cost model.** snaphu received the constant 1.0 coherence with
  `NCORRLOOKS 1.0`, so its statistical cost carried no decorrelation information (WF1-29). The 1×1 unwrapped
  phase is therefore of **unverified quality** in decorrelated areas, and its tile seams were never checked.
  No comparison in this project depends on it.
- **Unwrap `nlooks` must be set explicitly.** `nisar/workflows/unwrap.py:563` derives it from the crossmul grid,
  not the unwrap grid, and raised `nlooks must be >= 1, instead got 0.619…` for the 1×1/4×4 split. 44.57 is the
  effective number for 9×8 (72 × 0.619).
- **Dense offsets are residuals, stored in metres.** RUNW `pixelOffsets/HH/{alongTrackOffset,slantRangeOffset}`
  carry `units='meters'`, relative to the geometric prediction, on a grid starting 52 samples in with a 32-sample
  step.
- **The ionosphere side band is built from frequency A.** `ionosphere.py:122-237`
  (`decimate_freq_a_offset`) derives the freq-B pair's offsets by dividing the freq-A range offsets by 8, using
  **only the reference granule's** A/B slant ranges. It is correct for full granules (identical A/B origin on
  both dates) and silently wrong for any crop that changes the A-to-B origin relation between dates (WF3).
- **The ionosphere screen is solved on the decimated freq-B grid, not at 40 m.** The side-band products are
  5911 × 847 (~40 m azimuth × ~200 m range, verdicts V1). A and B are reconciled with `decimate_freq_a_array`
  and `interpolate_freq_b_array`; the 8:1 ratio does **not** put the bands on one grid. ISCE3's Gaussian is in
  pixels (100 px kernel, σ 33 px; `nisar/workflows/defaults/insar.yaml:317-321`), which on that grid is ~10 km σ in
  range but ~1.3 km in azimuth. The filtered screen is then interpolated to the interferogram grid.
- **No absolute cycle referencing.** `unwrapping_correction_with_filter` (`isce3/atmosphere/ionosphere_filter.py:1105-1117`),
  `compute_unwrapp_error` (`main_band_estimation.py:712-763`) and bridging (`isce3/unwrap/bridge_phase.py:16-78`)
  are all relative; a whole-cycle offset common to the inputs passes straight into the dispersive level. The
  **shape** of the screen is reproducible; its **absolute level is not** under a change of processing extent
  (verdicts V1; COMPARISON I3).
- **Noise amplification.** f₀ = 1.239 GHz and f₁ = 1.2935 GHz are 54.5 MHz apart; the 2×2 inversion amplifies
  unwrapped-phase noise into the dispersive term by √(a² + b²) = **16.79×** (a = f₁²/(f₁²−f₀²),
  b = f₀f₁/(f₁²−f₀²)); 16.83× measured on synthetic truth. The filter is not optional.

## 5. How to run it

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env
cd /home/sharath/isce3/asc/nisar_workflows
python tools/apply_patches.py --check              # must report every patch applied (section 7)

CFG=configs/nepal_glof.yaml
python run_track_r.py -c $CFG --only ingest
python run_track_r.py -c $CFG --only dem
python run_track_r.py -c $CFG --only runconfig --frequency A --looks 1 1   # render + validate + disk bill
python run_track_r.py -c $CFG --dry-run --frequency A --looks 1 1          # optional: what would run
python run_track_r.py -c $CFG --only insar --frequency A --looks 1 1       # the long stage
python run_track_r.py -c $CFG --only qa

C=/home/sharath/isce3/case_studies/nepal_glof
S=$C/scratch/trackR/20260714_20260726_A_HH_1x1
python -u tools/slc_coherence.py --ref $S/crossmul/freqA/HH/reference.slc \
   --sec $S/fine_resample_slc/freqA/HH/coregistered_secondary.slc --win 3 \
   --out $C/pairs/20260714_20260726/trackR/coherence_A_HH_win3.tif
```

With `product_type: RUNW` and the `phase_unwrap` block set to 9×8, the `insar` stage runs coregistration →
RIFG → unwrap → ionosphere in one pass, as WF3 did. The benchmark's split into standalone unwraps is history,
not a recommendation.

Run every long stage inside tmux with a wrapper that writes `EXIT=` markers with `set -o pipefail` and
`${PIPESTATUS[0]}`, uses `python -u`, and keeps the script under the case directory — templates and reasons in
OPERATIONS_AND_LESSONS.md section 5.

### 5.1 Resume and restart semantics (traps)

- **ISCE3 cannot resume.** `nisar/workflows/persistence.py:65` resets `success_msg_found` on every line while it
  scans the log backwards, so any real journal falls through to a full restart; in addition, without an explicit
  log file `runconfig.py:93-104` forces `restart`. After an OOM, 127 GB of completed rdr2geo + geo2rdr was redone
  (WF1-14). **Assume every restart re-runs from rdr2geo.**
- **`--force` deletes the pair's whole scratch tree** (`nisar_wf/trackr.py:684-688`, `shutil.rmtree`). The
  driver's own resume hint (`--start-step insar --force`) would have destroyed 127 GB of good scratch (WF1-13).
- **Skip-if-exists skips corrupt products.** Without `--force`, a pair whose output exists is skipped — including
  a product left structurally corrupt by a SIGKILL mid-write (`bad object header version number`; `h5clear -s`
  does not repair it). Delete a corrupt product by hand; do not pass `--force` unless the scratch is expendable.
- **`generate_dem_rdr` has no existence guard**, so every rerun regenerates the 11.5 GB `RIFG_ifgram_dem`
  (1h37m).

## 6. Parameter reference

| parameter | value used | where | alternatives | rationale |
|---|---|---|---|---|
| frequency / pol | A / HH | CLI `--frequency A`; `track_r.polarization HH` (:481) | B | A is 40 MHz, 8× finer range; granules are DHDH (no VV) |
| crossmul looks | 1 × 1 | CLI `--looks 1 1`; cfg:56-57 | 9×8 (~40 m) | keep the interferogram at native resolution for the comparison |
| product type | RUNW | `track_r.product_type` (:487); cfg:16 | RIFG (no unwrap, no ionosphere), GUNW | ionosphere is gated on RUNW/GUNW |
| block budget | 256 MB | `block_budget_mb` (:527) → cfg:30,34,60 | — | `lines_per_block` derived per frequency width; a fixed 1000 lines is 8× the memory on freq A |
| dense offsets | window 64×64, half-search 20×20, skip 32×32 | :544-549 → cfg:41-46 | larger windows | ISCE3 defaults; ~52 px dead band at crop edges |
| crossmul | flatten true, oversample 2, 154 lines/block | :558-561 → cfg:58-60 | common-band filters (off) | flattened interferogram; memory-bounded |
| unwrap looks | rg 8, az 9 | :590-591 → cfg:64-65 | 1×1 (see 4.1) | ~40 m cells; also sets the ionosphere side-band looks (`ionosphere.py:668-675`) |
| snaphu nlooks | 44.57 | :647 → cfg:71 | — | effective looks for 9×8 (72 × 0.619); must be explicit |
| snaphu tiling | ntiles 4×4, overlap 256, nproc 8 | :619-630 → cfg:72-78 | 1×1 pass used 32×32 | peak = per-tile × nproc at 385 B/px |
| snaphu reopt / regrow | false / false | :653-654 → cfg:79-80 | true | true re-solves or relabels the whole grid (69 GB at 1×1) |
| ionosphere method | main_side_band | :682 → cfg:83 | split_main_band | uses freq B; split_main_band needs two more full-resolution unwraps |
| dispersive filter | coherence 0.5, median 15 | :685-686 → cfg:90-91 | — | ISCE3 kernel stays 100 px / σ 33 px (pixel units) |
| disk gate | min free 60 GB, enforced | :516-518 | `--no-disk-gate` | refuse late ENOSPC |

## 7. Required patches/overlays and upstream bugs

The benchmark was produced on **ISCE3 0.25.12 with overlays**; a stock install cannot reproduce it.
`tools/apply_patches.py` installs each overlay only if the installed file is byte-identical to the expected
upstream version, keeps `<name>.orig`, and supports `--check` and `--revert`.

| overlay (target in site-packages) | problem in 0.25.12 | effect of the overlay | verified by |
|---|---|---|---|
| `nisar/workflows/resample_slc_v2.py` — isce3#372 (f42cea75) | secondary RSLC opened with h5py's default chunk cache; resampling re-reads chunks | reader sized to the dataset chunking | upstream PR; apply-time import checks |
| `nisar/products/insar/utils.py` (`generate_insar_mask`, via `tools/patches/insar_utils.py` + `insar_mask_vectorized.py`) | second call site (`InSAR_L1_writer.py:753`) builds the interferogram-grid mask with a pure-Python double loop: 2886 Mpx at 1×1 → 8.3 h, then OOM | vectorised range loop, preallocated uint32; 0.82 s/Mpx; falls back to stock when `num_sub_swaths != 1` | `tools/test_insar_mask_patch.py`: bit-identical to stock on real freq-B data |
| `nisar/workflows/h5_prep.py` | `RUNW_STANDALONE`/`GUNW_STANDALONE` in `h5_paths` but not `product_dict` → `KeyError` in the standalone unwrap | adds the two entries | standalone unwrap ran |
| `nisar/workflows/unwrap.py` | `open_raster()` reads the whole interferogram and coherence (34.6 GB at 1×1), defeating snaphu tiling | streams inputs through `snaphu.io.Raster`; normalises `HDF5:f:/grp` paths | peak RSS 2.6–5.1 GB during the 1×1 unwrap |

Upstream behaviours worked around in the driver rather than patched:

| behaviour | location | workaround |
|---|---|---|
| unwrap `nlooks` from the crossmul grid | `unwrap.py:563` | set `snaphu.nlooks` explicitly |
| standalone unwrap needs crossmul keys the unwrap runconfig never fills (`flatten_path`, `coregistered_slc_path`, …) | `crossmul.py:45` and friends | `trackr.py` emits all crossmul keys |
| `offsets_product.run(cfg, out_paths['ROFF'])` without a guard | `insar.py:64-66` | disable unless ROFF is produced |
| `list_of_frequencies` required; only the user file is schema-validated | `runconfig.py:110-111` | always emit; validate the rendered file |
| `bridge_unwrapped_phase` reads the whole unwrapped layer after snaphu (~26 GB at 1×1) | `unwrap.py:356` | bridge off for the 1×1 pass |
| Persistence cannot resume | `persistence.py:65`, `runconfig.py:93-104` | treat restarts as full |
| no sliding-window coherence at 1×1 | `Crossmul.cpp:378-388` | `tools/slc_coherence.py` |

## 8. Resource model

### 8.1 RAM

| consumer | scales with | freq A 1×1 |
|---|---|---|
| rdr2geo/geo2rdr blocks | lines_per_block × width (derived from 256 MB budget) | ≤ ~1.3 GB per block if not derived |
| `inputDataExceptionMask` read whole, both images | RSLC grid (not looks) | 2.89 GB × 2 = 5.77 GB — killed the 3.9 GB VM at 27h24m (WF1-11) |
| interferogram-grid mask (stock) | crossmul looks | 2.886e9 loop iterations, ~58 GB transient → patched |
| unwrap input read (stock) | unwrap grid | 34.6 GB → patched to streaming |
| snaphu | per tile × nproc, 385 B/px | 1×1 at [32,32]: small tiles; 9×8 at [4,4]/8: ~1.4 GB/tile |
| `single_tile_reoptimize` / `regrow_conncomps` | whole unwrap grid | 69 GB at 1×1 → must be false when tiled |

The benchmark ran on a 31 GB / 8-core VM with the overlays; the 3.9 GB / 2-core VM cannot run freq A 1×1.

### 8.2 Disk

Scratch per reference-grid pixel, measured on freq B and applied to freq A (`nisar_wf/trackr.py`
`_SCRATCH_BYTES_PER_PX`):

| stage | B/px | content |
|---|---|---|
| rdr2geo | 24.4 | x, y, z Float64 |
| geo2rdr | 16.0 | range, azimuth offsets Float64 |
| coarse_resample | 8.0 | coregistered secondary |
| dense_offsets | 8.0 | offsets/corr/snr + reference copy |
| rubbersheet | 32.0 | full-grid range/azimuth, resampled and culled |
| fine_resample | 8.0 | coregistered secondary |
| crossmul | 8.0 | reference SLC unpacked |
| baseline | 0.04 | |
| **total** | **104.4** | freq A 2886 Mpx → ~301 GB; plus `RIFG_ifgram_dem` 11.5 GB at 1×1 |

An earlier 40 B/px model was 2.6× low (WF1-05). Snaphu's Python bindings also write flat copies of their inputs to
scratch (the streaming patch moved 34.6 GB from RAM to disk).

### 8.3 Runtime (freq A, as measured)

| work | 8-core VM | 2-core VM |
|---|---|---|
| rdr2geo | 1h39m | 13h05m |
| geo2rdr | 6.6 min | 22 min |
| integrated run through RIFG (Sep 6–7) | 8h34m | — |
| `RIFG_ifgram_dem` Topo (inside the above) | 1h37m | 13h33m |
| unwrap 1×1, [32,32] | 12.84 h | — |
| unwrap 9×8 + ionosphere | ~23 min + 27 min | — |
| 3×3 coherence | ~10 min | — |

Excluded: roughly 32 h of failed attempts (OOM, restarts), itemised in section 11.

## 9. Validation gates for automation

| gate | how | pass | measured here |
|---|---|---|---|
| overlays | `apply_patches.py --check` + module hashes | all applied, hashes match manifest | all applied |
| disk bill | `--only runconfig` | bill ≤ free − 60 GB | 312.9 GiB vs 341.6 GiB free on the old disk (passed narrowly) |
| geometric offsets plausible | geo2rdr azimuth/range offset statistics | smooth, consistent with orbit phasing | +746.2 px azimuth, −1.477 px range, spread 0.018 px (TRACK_R.md:416-421; freq/run UNVERIFIED) |
| dense-offset residual | RUNW `pixelOffsets` medians/percentiles (metres → samples) | sub-pixel, spatially smooth | 0.566 m (0.13 lines), 0.736 m (0.236 samples) |
| RIFG completeness | h5py inventory; no NaN blocks | all datasets; finite phase | 28.57 GB, complete |
| coherence is real | fraction exactly 1.0 in the delivered coherence | 0 % (for looks > 1); at 1×1 use the 3×3 product | 1×1 RIFG: 100 % ≡ 1.0 (expected) |
| 3×3 coherence sane | median, fraction at/below floor | median ≫ 0.295 over stable terrain | median 0.5739 |
| unwrap components | `connectedComponents` count and coverage | stable terrain labelled | 32 components (1×1 RUNW) |
| ionosphere screen non-zero where expected | `ionospherePhaseScreen` ≠ 0 | non-zero wherever RUNW is valid | 9×8 RUNW: yes; **1×1 RUNW: all zeros (ionosphere disabled)** |
| ionosphere uncertainty | `ionospherePhaseScreenUncertainty` distribution | finite, not identically 0 | median 1.31 rad (large relative to the screen) |
| output identity | planned paths vs existing files | no planned path exists | checked before each launch |

## 10. Known limitations and open questions

- **OPEN** — absolute ionosphere level is unreferenced (4.1); the benchmark's own level is arbitrary to whole
  cycles. An external TEC reference (e.g. a NISAR TEC product via `isce3.atmosphere.tec_product`) is needed for any
  absolute TEC claim.
- **OPEN** — 1×1 unwrapped phase quality (degenerate cost model, unchecked seams).
- **OPEN** — the dispersive filter masks roughly half the pixels (coherence threshold 0.5 against a freq-B
  coherence median ≈0.50) and nearest-fills them; the screen is 100 % finite but its per-pixel reliability is not
  carried in the product beyond the uncertainty layer.
- **OPEN** — ISCE3 geo2rdr leaves a 0.236-sample range / 0.13-line azimuth residual that dense offsets remove;
  the GSLC chain registers its two dates to 0.02 px without dense offsets (verdicts V3). Cause not established.
- No troposphere: GUNW-only, ISCE3 does not download weather data (`troposphere_runconfig.py:41-45`); pyaps3
  0.3.7 is installed and needs `~/.cdsapirc` with a url and a new-style CDS token; RAiDER is not installed.
- 46 GB of benchmark intermediates (coarse resample, dense offsets) were deleted during the run and cannot be
  inspected without re-running coregistration (WF1-38).

## 11. Problems and errors log

<!-- ERROR-LOG:BEGIN -->
45 entries: 5 caught by the user, 23 were the assistant's own mistakes of judgement, 13 still open. Every entry cites its evidence; the full records are in `case_studies/nepal_glof/comparison/verification/`.

| id | problem | category | caught by | assistant error | status | cost |
|---|---|---|---|---|---|---|
| WF1-01 | User's look spec was wrong: 5x5 does not give about 40 m, and one looks pair can't suit both frequencies | other | assistant | no | fixed | None. Caught before any run. |
| WF1-02 | DEM datum-check heuristic gave a false positive on a landlocked frame | other | assistant | no | worked around | About 2 min of checking. |
| WF1-03 | Wrapper --dry-run failed because it required a runconfig that the dry run does not write | judgement | assistant | yes | fixed | About 1 min. |
| WF1-04 | Misread ISCE3 DEM-bounds warning led to an unnecessary rdr2geo restart | judgement | assistant | yes | fixed | The assistant reported about 27 min lost. The log shows the killed run had reached rdr2geo block 31/54 at 18:32:59 after starting at 17:30:10, so about 62 min o |
| WF1-05 | Scratch sizing model was 2.6x low (40 vs 104.4 B/px); freq A estimates were inconsistent | geometry | assistant | yes | fixed | No run lost. The earlier model would have under-protected a 301 GB freq A run on a disk with 341-365 GB free. |
| WF1-06 | ISCE3 has no coregistration-only product; the RIFG and freq-B-only scope surprised the user | geometry | user | no | by design | Communication only. Freq A work started a day later. |
| WF1-07 | Disk gate ignored the delivered product, and later refused a run that fit; RIFG size was over-estimated | resources | assistant | yes | fixed | About 2 min per refusal. The risk averted was an ENOSPC late in a 24 h run. |
| WF1-08 | lines_per_block 1000 (tuned on freq B) used 8x the memory on freq A and headed for OOM | resources | assistant | yes | fixed | About 3 min run discarded. The averted risk was an OOM hours into a 24 h run. |
| WF1-09 | At 1x1 the RIFG_ifgram_dem Topo pass is a second full-resolution rdr2geo (13.5 h on 2 cores) | judgement | assistant | yes | by design | About 13h33m on 2 cores (1h37m on 8 cores in the Sep 6 rerun). |
| WF1-10 | Assistant recommended switching to 4x4 to save 11 h; that run would have hit the same looks-independent OOM | resources | assistant | yes | worked around | None, thanks to the user's choice. The recommended path would have discarded the full-resolution RIFG and still died. |
| WF1-11 | OOM kill at 27h24m on the 3.9 GB box in generate_insar_mask (inputDataExceptionMask read whole for both images) | resources | crash | no | worked around | 27h24m of 2-core compute. rdr2geo + geo2rdr (127 GB) survived but were redone anyway (see Persistence). |
| WF1-12 | SIGKILL mid-write left the RIFG HDF5 structurally corrupt | resources | assistant | no | worked around | The partial product was lost (it was recomputed anyway). |
| WF1-13 | Wrapper resume semantics are dangerous: skip-if-exists would skip a corrupt product, and the documented resume uses --force, which rmtree's the whole scratch | judgement | assistant | yes | open | No loss occurred. Following the printed hint would have destroyed 127 GB of scratch. |
| WF1-14 | ISCE3 Persistence cannot resume, so 127 GB of rdr2geo + geo2rdr was redone | other | assistant | no | worked around | rdr2geo 1h39m + geo2rdr 6.6 min on 8 cores (13h05m + 22m of work on 2 cores that had survived the OOM). |
| WF1-15 | The ionosphere stage is silently skipped unless product_type is RUNW/GUNW | science | assistant | no | fixed | None. The averted risk was an 8.5 h run with no ionosphere. |
| WF1-16 | No TEC in the granules and no IONEX reader, so no ionospheric geolocation correction | other | assistant | no | by design | A residual sub-pixel to about 1 px differential geolocation error in both tracks. |
| WF1-17 | generate_insar_mask second call site: a pure-Python loop over the 2886 Mpx interferogram grid (8.3 h, then OOM on 31 GB) | resources | assistant | no | fixed | The 8.3 h + OOM was averted. Developing the patch took about 5 min. |
| WF1-18 | Assistant dismissed the correct mask diagnosis ('off by ~1000x') and launched a doomed run | resources | assistant | yes | fixed | 1h23m of 8-core compute (logs/track_r_20260906T174001Z.log 'failed with return code -15 after 1h23m'). Letting it continue would have cost about 3 h more of rdr |
| WF1-19 | snaphu tiling memory: [4,4] x nproc 8 = 40.2 GB, and default single_tile_reoptimize / regrow_conncomps re-solve the full grid (69 GB) | resources | assistant | yes | fixed | None. OOM averted. |
| WF1-20 | generate_dem_rdr has no existence guard, so the 11.5 GB RIFG_ifgram_dem was regenerated | other | assistant | no | open | 1h37m on 8 cores (13h33m-equivalent on 2 cores). |
| WF1-21 | ValueError 'nlooks must be >= 1, instead got 0.6189996726516942' at 8h34m (crossmul 1x1 / unwrap 4x4 split) | software | crash | no | worked around | No coregistration lost (RIFG 28.6 GB intact). Restart overhead of the standalone path. |
| WF1-22 | KeyError 'RUNW_STANDALONE': the standalone unwrap entry point is unusable in nisar 0.25.12 | software | crash | no | fixed | About 1 min. |
| WF1-23 | KeyError 'flatten_path', then KeyError 'coregistered_slc_path', in standalone unwrap | software | crash | no | fixed | Two restarts of the RUNW Topo passes, a few min each. |
| WF1-24 | phase_unwrap was multilooked 4x4 without asking the user | judgement | user | yes | fixed | About 27 min of 4x4 Topo work (unwrap_standalone.log truncated at block 24/65). The original 8.5 h run had also been configured at 4x4. |
| WF1-25 | Assistant wrongly said 1x1 unwrap was 'not possible on this box'; the blocker was ISCE3's whole-array open_raster read | tooling | user | yes | fixed | About 5 min to patch. The wrong 'not possible' claim, left uncorrected, would have removed the user's requested product. |
| WF1-26 | Snaphu tile overlap initially 128 px (570/608 m) with no global re-optimisation; seams never checked | judgement | user | yes | open | 1x1 unwrap: snaphu 12h40m wall / 93.9 CPU-h over 1024 tiles. The ISCE3 journal records phase unwrapping at 46233 s (12.84 h) including Topo. Seam quality of the |
| WF1-27 | bridge_unwrapped_phase does a whole-array read (~26 GB at 1x1) after snaphu; the 1x1 run was restarted and shipped unbridged | other | assistant | no | worked around | Restart at Topo block 111/259: 52 min. |
| WF1-28 | Ionosphere freq B looks are coupled to phase_unwrap looks; a 1x1 unwrap + ionosphere would solve on coherence == 1 with zero uncertainty | science | assistant | no | worked around | A second unwrap pass (about 23 min unwrap + 27 min Ionosphere per ISCE3 journal). |
| WF1-29 | 1x1 snaphu solve was fed a coherence == 1.0 layer and NCORRLOOKS 1.0, a degenerate cost model for the benchmark RUNW | science | assistant | yes | open | Unquantified quality risk (unwrapping errors in decorrelated areas) in the full-resolution benchmark unwrapped phase. Re-running with the 3x3 coherence as corr  |
| WF1-30 | The streaming unwrap moved the 34.6 GB from RAM to disk; free disk was projected to run out mid-snaphu | resources | assistant | no | worked around | About 30 min of analysis. A risk of ENOSPC about 8 h into the 12.7 h unwrap was averted. Intermediates were permanently deleted. |
| WF1-31 | ISCE3 crossmul has no sliding-window coherence; 1x1 coherence is hardcoded 1.0. The user had to ask for 3x3 | science | user | yes | fixed | About 10 min runtime plus tool development. The benchmark lacked a usable full-resolution coherence for 5 days. |
| WF1-32 | Output identity: the RUNW name lacked unwrap looks (a 9x8 pass would clobber the 12.7 h 1x1 RUNW); runconfig and log names still lack it | data management | assistant | yes | open | A 12.7 h product nearly lost. Reproducibility of the 1x1 unwrap config is now lost except from config comments and the transcript. |
| WF1-33 | Misunderstood ionosphere grid reconciliation (claimed the 8:1 ratio puts A and B on one grid); the wrong mechanism persists in config comments | science | assistant | yes | open | No compute lost. Wrong documentation remains, and it misstates the effective resolution of the ionosphere screen. |
| WF1-34 | Dispersive filter masks about half the pixels and nearest-fills them; a 100% finite screen with large relative uncertainty | other | assistant | no | open | The quantitative reliability of the benchmark ionosphere screen is limited and undocumented per pixel. |
| WF1-35 | The Sep 14 status report said the 1x1 RUNW carries the ionosphere screen at full resolution; it is zero-filled | science | assistant | yes | open | Misinformation to the user about the benchmark's ionosphere product and resolution; risk of downstream use of zeros as a correction. |
| WF1-36 | ISCE3's default ionosphere Gaussian kernel is in pixels, so on the 5911x847 grid it smooths about 10 km in range but only about 1.3 km in azimuth | science | assistant | no | open | Anisotropic smoothing of the benchmark ionosphere screen. Affects cross-track ionosphere comparisons. |
| WF1-37 | Documentation and config comments drifted from reality | science | assistant | yes | open | Anyone automating from these files would inherit wrong limits (1x1 unwrap impossible), wrong mechanisms (grid cancellation), wrong sizes, and a cross-contaminat |
| WF1-38 | Assistant irreversibly deleted 46 GB of benchmark scratch without asking, based on a disk projection it later admitted was double-counted | resources | assistant | yes | open | Full-tile dense-offset and coarse-resample intermediates of the benchmark are permanently lost; re-creating them means re-running hours of coregistration. |
| WF1-39 | Benchmark produced on a patched nisar: four installed overlays are provenance, not optional tweaks | judgement | guardrail/tool check | yes | fixed | No compute cost; a reproducibility gap: a stock ISCE3 0.25.12 cannot reproduce the benchmark (it OOMs or KeyErrors). |
| WF1-40 | Runconfig traps: offsets_product with a non-ROFF product_type, list_of_frequencies required, only the user file is schema-validated | software | assistant | no | fixed | Avoided failures; no measured cost. |
| WF1-41 | RSLC 3x3 coherence (tools/slc_coherence.py) is computed from an UNFLATTENED SLC pair | science | guardrail/tool check | yes | fixed | v1 comparison coherence statistics were biased against RSLC by an unmeasured amount (measured in comparison_v2). |
| WF1-42 | ISCE3 main_side_band ionosphere has no absolute cycle-level referencing | science | guardrail/tool check | no | open | Absolute ionosphere level not reproducible under a change of processing extent. |
| WF1-43 | 3x3 coherence bias floor (~0.295) is an interpretation trap | science | assistant | no | by design | None if respected. |
| WF1-44 | Ionosphere-only runtime misattributed the freq-B stages to the journal 'Ionosphere' timer | science | assistant | yes | fixed | Two slightly low estimates given to the user. |
| WF1-45 | Absolute ionosphere level differs from the NISAR GUNW by one joint cycle | science | guardrail/tool check | no | open | None. |

Cross-cutting operational problems (process supervision, reboots, logs, disk, agent harness) are in OPERATIONS_AND_LESSONS.md.

#### WF1-01 — User's look spec was wrong: 5x5 does not give about 40 m, and one looks pair can't suit both frequencies

- **Symptom:** The user asked for 'Track R ... 5x5 : azimuth x range settings which will get approx 40m'. On this track 5x5 gives 22.3 x 23.7 m on freq A and 22.3 x 190 m on freq B.
- **Root cause:** The native spacings differ about 8x in range between bands: A is 4.454 m az x 4.750 m ground range, B is 4.454 x 37.998 m. Freq B range resolution is 45.6 m (5 MHz), so its range_looks must stay 1. One (az, rg) pair cannot be right for both bands.
- **Fix:** Looks are now keyed per frequency (Looks/TrackRConfig in nisar_wf/config.py; configs/nepal_glof.yaml track_r.looks). B was set to 9x1 (40.1 x 38.0 m). A went 5x5, then 1x1 at the user's request.
- **Cost:** None. Caught before any run.
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** L114 user: 'lets use 5x5 ... approx 40m'; L170 assistant: '5x5 does not give 40 m on this data ... 9 x 1 | 40.1 x 4.7 m | 40.1 x 38.0 m'; L175 user answer 'freq B shall be 9x1 and A shall be 5x5'; TRACK_R.md:87-110
- **Automation lesson:** Take a target ground cell in metres per product and derive looks per frequency from the product's own spacing and bandwidth metadata. Refuse range_looks > 1 when native range spacing is already at or below range resolution.

#### WF1-02 — DEM datum-check heuristic gave a false positive on a landlocked frame

- **Symptom:** The DEM staging step raised a datum warning for the NISAR DEM (min 17.9 m ellipsoidal).
- **Root cause:** The heuristic expects ocean (height near 0) in the AOI. It was tuned for coastal Venezuela. This Himalayan frame has no ocean, and the Terai geoid undulation is -64.2 m, so 17.9 m ellipsoidal is 82 m orthometric, which is correct.
- **Fix:** Checked against the PROJ EGM2008 grid and accepted as a false positive. No code change.
- **Cost:** About 2 min of checking.
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** L212: 'That datum warning is tuned for a coastal AOI'; L222: 'The datum warning is a false positive: the heuristic assumes ocean in the AOI, and this frame is landlocked.'
- **Automation lesson:** Datum sanity checks must depend on terrain class (coastal vs landlocked) or compare against the geoid grid directly. Never assume ocean pixels exist.

#### WF1-03 — Wrapper --dry-run failed because it required a runconfig that the dry run does not write

- **Symptom:** track_r_20260903T172907Z.log: 'ERROR: step 4 insar FAILED after 0.0s ... runconfig missing: .../cfg/insar_20260714_20260726_B_HH_9x1.yaml Run the runconfig step first.'
- **Root cause:** run_insar checked that the runconfig exists even in dry-run mode. The dry run of the runconfig step deliberately writes nothing.
- **Fix:** nisar_wf/trackr.py:647-658 now reports intent only during a dry run and never demands the file.
- **Cost:** About 1 min.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** logs/track_r_20260903T172907Z.log; L366: 'Two defects to fix: a dry run shouldn't fail on a not-yet-written runconfig, and there's a dead variable.'
- **Automation lesson:** Run a whole-pipeline --dry-run test in CI. Dry-run paths must not depend on artefacts that earlier dry-run steps skip.

#### WF1-04 — Misread ISCE3 DEM-bounds warning led to an unnecessary rdr2geo restart

- **Symptom:** rdr2geo logged 'East/South limit may be insufficient for global height range' on every block. The assistant said DEM coverage was insufficient and recommended restarting with buffer_deg 0.15. After the restart the warning still fired.
- **Root cause:** Topo::computeDEMBounds (cxx/isce3/geometry/Topo.cpp:311) sizes the requested DEM window from _minH/_maxH. nisar.workflows.rdr2geo never sets these, so they stay at GLOBAL_MIN_HEIGHT=-500 / GLOBAL_MAX_HEIGHT=+9000 (cxx/isce3/core/Constants.h:53-56). That asks for about 0.112 deg of spread. DEMInterpolator.cpp:267-285 then clamps to the file edge and warns. Any windowed DEM triggers it. Separately, buffer_deg 0.1 had been copied from the Venezuela case (about 2 km relief). The assistant interpreted the warning without reading the source.
- **Fix:** DEM re-staged at buffer_deg 0.15 (true-relief margin 58%). The source-based explanation is in configs/nepal_glof.yaml:156-181 ('DO NOT chase this warning'). rdr2geo lon/lat/height were then checked to fall inside the DEM, so nothing real was clamped (L651).
- **Cost:** The assistant reported about 27 min lost. The log shows the killed run had reached rdr2geo block 31/54 at 18:32:59 after starting at 17:30:10, so about 62 min of 2-core rdr2geo was discarded. The DEM question waited 35 min for the user's answer while the run kept going. Plus a DEM re-stage.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L497 AskUserQuestion: 'Changing it means restarting the pair (~27 min of rdr2geo lost)'; L551: 'The warning still fires at 0.15°.'; L622: 'I was wrong about the DEM warning ... the restart wasn't necessary'; logs/track_r_20260903T173010Z.log last line 'Topo progress (block 31/54)' 18:32:59
- **Automation lesson:** Classify ISCE3 journal warnings against a source-verified allowlist before acting on them. Size the DEM buffer from max relief / tan(incidence) per site, not from another case. When a restart decision waits on a human, recompute the sunk cost at decision time.

#### WF1-05 — Scratch sizing model was 2.6x low (40 vs 104.4 B/px); freq A estimates were inconsistent

- **Symptom:** The disk gate modelled 40 B/px (rdr2geo + geo2rdr + two resampled SLCs). The measured freq B scratch was 104.6 B/px (35.1 GiB). Freq A scratch was quoted as about 321 GB, then about 170 GB, then 301 GB across Sep 3-4.
- **Root cause:** The model omitted dense_offsets (8 B/px, which also emits a reference.slc copy), rubbersheet (32 B/px of filtered and culled full-grid offsets, despite the decimated ampcor grid) and the crossmul reference.slc unpack (8 B/px). The mistaken intuition was 'offsets are tiny'. The looks-dependent RIFG_ifgram_dem term was added later (11.5 GB at freq A 1x1).
- **Fix:** nisar_wf/trackr.py _SCRATCH_BYTES_PER_PX itemised to 104.4 B/px plus an explicit ifgram_dem term. It now predicts 35.1 GiB against 35.1 GiB measured. TRACK_R.md:273-302.
- **Cost:** No run lost. The earlier model would have under-protected a 301 GB freq A run on a disk with 341-365 GB free.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L865: 'My disk gate modelled 40 bytes/px; the measured cost is 104.6 — I omitted dense_offsets (8) and rubbersheet_offsets (32).'; L891: 'Model now predicts 35.1 GiB against a measured 35.1 GiB.'; L170 '~321 GB', L496 '~170 GB', L1282 '301 GB'
- **Automation lesson:** Calibrate scratch models by measuring a real run (du per stage directory) and keep the per-stage table in code. Re-validate on the largest band and grid before trusting a model built on the small one.

#### WF1-06 — ISCE3 has no coregistration-only product; the RIFG and freq-B-only scope surprised the user

- **Symptom:** User: 'when did we do these RIFG wrapped ifg + coherence ? I thought we just did till coregistration ... does this coregistration go for both freqA and freqB ?' and then 'dude freqA is important'. Only freq B had been coregistered, and a RIFG had been produced.
- **Root cause:** The insar workflow offers ROFF (offsets only, no resampled SLC) or RIFG (coregistration + crossmul). There is no 'coregister and stop' type. list_of_frequencies processes only the listed band, and freq B had been chosen because freq A's 301 GB scratch did not fit.
- **Fix:** Explained, and the freq A 1x1 run was set up on Sep 4.
- **Cost:** Communication only. Freq A work started a day later.
- **Caught by:** user · **Status:** by design
- **Evidence:** L1263 user question; L1282: 'ISCE3's insar workflow has no \"coregister and stop\" product type ... RIFG was the cheapest route'; L1285 user: 'dude freqA is important'
- **Automation lesson:** Expose product_type and frequency choice as explicit, logged pipeline parameters. Report every product each stage writes, not just the headline one.

#### WF1-07 — Disk gate ignored the delivered product, and later refused a run that fit; RIFG size was over-estimated

- **Symptom:** (a) Sep 4: the gate modelled scratch only and would have approved freq A 1x1 showing 64 GB headroom when the real margin was 28.7 GiB (RIFG 32.3 GiB). (b) Sep 6 17:38: 'insufficient disk: Track R on frequency A needs 323.7 GiB of scratch but only 316.8 GiB is free', although 118-127 GiB of that scratch already existed and would be overwritten in place. (c) The RIFG at 1x1 was forecast at 32.3 GiB (gate) and about 58 GB (TRACK_R.md:299). The actual file is 28.6 GB.
- **Root cause:** The gate counted neither output products nor existing same-tag scratch. It also ignored HDF5 gzip + shuffle compression: the constant 1.0 coherence band compresses 226x and the wrapped interferogram 1.16x.
- **Fix:** The gate counts the output product and credits existing same-tag scratch (nisar_wf/trackr.py:574 'crediting ... of scratch already on disk'). The RIFG over-estimate was not corrected in TRACK_R.md.
- **Cost:** About 2 min per refusal. The risk averted was an ENOSPC late in a 24 h run.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L1351/L1362: 'my disk gate doesn't currently count the output product ... 312.9 GiB needed, 341.6 GiB free, margin 28.7 GiB'; logs/track_r_20260906T173856Z.log 'insufficient disk ... 323.7 GiB ... 316.8 GiB'; L1846: '127 GB of that scratch already exists'; /scratch/trackR/20260714_20260726_A_HH_1x1/RIFG.h5 = 28.57 GB (this audit); L1923 plan: 'RIFG at 1×1 ≈ 57.7 GB ... ~25.8 GB. gzip-1 + shuffle'
- **Automation lesson:** Disk budgeting = new scratch + outputs - reusable existing bytes, with compression factors measured per dataset type. Log the itemised bill and re-check free space between stages, not only at start.

#### WF1-08 — lines_per_block 1000 (tuned on freq B) used 8x the memory on freq A and headed for OOM

- **Symptom:** Two min into the first freq A run, available memory fell to 819 MB on the 3.9 GB box. rdr2geo at 1000 lines x 54244 x 8 B x 3 layers = 1302 MB per block (163 MB on freq B). The run was killed ('failed with return code -15 after 3m14s').
- **Root cause:** Block memory is lines x width, and width is a property of the frequency. A literal calibrated on freq B silently becomes 8x on freq A.
- **Fix:** Block heights are derived from the range width against block_budget_mb 256 (nisar_wf/trackr.py; configs/nepal_glof.yaml track_r comment): rdr2geo 206, geo2rdr 309, dense_offsets 309, crossmul 154 lines. Freq A 2-core RSS then peaked at 477 MB.
- **Cost:** About 3 min run discarded. The averted risk was an OOM hours into a 24 h run.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L1403: 'Available memory is 819 MB ... lines_per_block: 1000 now means rdr2geo holds ... 1.30 GB per block instead of 163 MB'; logs/track_r_20260904T124508Z.log 'failed with return code -15 after 3m14s'; L1426 derived heights
- **Automation lesson:** Never hard-code per-block line counts. Derive them from the product's width, dtype and layer count against a memory budget, and log the derived values.

#### WF1-09 — At 1x1 the RIFG_ifgram_dem Topo pass is a second full-resolution rdr2geo (13.5 h on 2 cores)

- **Symptom:** After rdr2geo (13h05m) and geo2rdr (22m) on 2 cores, there were no scratch writes for 40 min at 189% CPU. A 259-block Topo pass at 240 s/block was computing RIFG_ifgram_dem (11.5 GB).
- **Root cause:** The interferogram DEM layer is computed on the interferogram grid, whose size scales as 1/(az_looks x rg_looks). At 1x1 that grid is the full 2886 Mpx radar grid. Neither the user nor the assistant had budgeted for it.
- **Fix:** Added as an explicit looks-dependent term in the scratch model. The user chose to let 1x1 continue.
- **Cost:** About 13h33m on 2 cores (1h37m on 8 cores in the Sep 6 rerun).
- **Caught by:** assistant · assistant error · **Status:** by design
- **Evidence:** L1512: 'That layer is computed on the interferogram grid ... At 1×1 it's a second full-resolution Topo pass — as expensive as rdr2geo itself. That's a cost of the 1×1 choice neither of us anticipated.'; TRACK_R.md:317-320
- **Automation lesson:** The runtime/cost estimator must include every looks-dependent stage (ifgram DEM, mask, stats), not just the final raster size, and show the 1x1 vs multilook runtime delta before launch.

#### WF1-10 — Assistant recommended switching to 4x4 to save 11 h; that run would have hit the same looks-independent OOM

- **Symptom:** The AskUserQuestion recommended 'Switch to 4×4, reuse the 13.5 h already done (Recommended)'. The OOM that killed the run 10 h later was on the RSLC swath grid, independent of looks.
- **Root cause:** The recommendation was made without auditing what ran after the Topo pass (prepare_insar_hdf5 / generate_insar_mask) or its memory, on a 3.9 GB box.
- **Fix:** The user declined ('Let 1×1 run to completion'). The assistant acknowledged the error after the OOM.
- **Cost:** None, thanks to the user's choice. The recommended path would have discarded the full-resolution RIFG and still died.
- **Caught by:** assistant · assistant error · **Status:** worked around
- **Evidence:** L1513 option 'Switch to 4×4 ... (Recommended)'; L1518 user answer 'Let 1×1 run to completion'; L1632: 'This allocation is not looks-dependent ... my earlier recommendation was wrong. I proposed switching to 4×4 to save ~11 hours — it would have saved the Topo pass and then died at this identical point.'
- **Automation lesson:** Before recommending a mid-run reconfiguration, run a memory pre-flight of all remaining stages at the proposed settings on the actual box.

#### WF1-11 — OOM kill at 27h24m on the 3.9 GB box in generate_insar_mask (inputDataExceptionMask read whole for both images)

- **Symptom:** Kernel: 'Out of memory: Killed process 38290 (python) total-vm:12324044kB, anon-rss:3548012kB' at Sep 05 16:14:26, 17.5 min after the ifgram_dem Topo pass finished. Wrapper: 'failed with return code -9 after 27h24m'. Swap 100% exhausted.
- **Root cause:** nisar/products/insar/utils.py generate_insar_mask (stock utils.py.orig:548-551, `h5_obj[path][()].astype(np.uint8)`), called from InSAR_L1_writer.py:535, reads the full-swath uint8 inputDataExceptionMask (53200 x 54244 = 2.89 GB) for reference and secondary: 5.77 GB retained. Audit note: astype() always copies, so the transient peak is about 8.7 GB, not 5.77 GB. This allocation is not looks-dependent. The attribution came from timing and code reading, since SIGKILL leaves no traceback. The assistant's '0.4 GB short' figure compared virtual total-vm against RAM+swap, which is not a valid comparison.
- **Fix:** Hardware upgrade to 31 GB RAM / 8 cores (Sep 6). No code fix for this read. It is harmless at 31 GB.
- **Cost:** 27h24m of 2-core compute. rdr2geo + geo2rdr (127 GB) survived but were redone anyway (see Persistence).
- **Caught by:** crash · **Status:** worked around
- **Evidence:** L1568 kernel log; logs/track_r_20260904T124949Z.log 'failed with return code -9 after 27h24m'; L1614 utils.py source; L1625: 'freq A: ... x2 (ref+sec) = 5.77 GB'; L1656: 'process total-vm 12.3 GB <- 0.4 GB SHORT'
- **Automation lesson:** Run a memory pre-flight per stage from grid sizes (swath-grid masks, full-array reads) before launch. Record peak RSS per stage. Run stages as separate processes so a kill identifies the stage.

#### WF1-12 — SIGKILL mid-write left the RIFG HDF5 structurally corrupt

- **Symptom:** RIFG_20260714_20260726_A_HH_1x1.h5 (5.59 GB) failed h5py visititems with 'RuntimeError: Object visitation failed (bad object header version number)'. `h5clear -s` did not help.
- **Root cause:** The OOM SIGKILL hit while the writer was mid-write. The damage is structural, not just an unclosed-file flag.
- **Fix:** The corrupt file was deleted before the Sep 6 rerun.
- **Cost:** The partial product was lost (it was recomputed anyway).
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** L1606 traceback 'bad object header version number'; L1609: 'The RIFG is corrupt ... exactly what a SIGKILL mid-write leaves'; L1759: 'h5clear -s didn't help; it's structural'
- **Automation lesson:** Write products to a temp name and atomically rename on success. Verify output integrity (open + read every dataset's metadata) before a stage is marked done.

#### WF1-13 — Wrapper resume semantics are dangerous: skip-if-exists would skip a corrupt product, and the documented resume uses --force, which rmtree's the whole scratch

- **Symptom:** After the OOM the wrapper said 'after fixing, resume with: ... --start-step insar'. run_insar skips any pair whose output exists (it would skip the corrupt RIFG). The CLI epilog says 'resume after a crash inside the InSAR chain: run_track_r.py ... --start-step insar --force'. --force deletes the entire scratch tree, which at that point held 127 GB / 13.5 h of rdr2geo + geo2rdr.
- **Root cause:** nisar_wf/trackr.py:668 tests output existence, not validity. trackr.py:686-688 does `shutil.rmtree(p['scratch'])` on --force. run_track_r.py:172-173 recommends --force for resume. Combined with ISCE3's broken Persistence, no safe resume path exists.
- **Fix:** Documented only (TRACK_R.md:356-359: delete the corrupt product by hand, do not pass --force). The epilog hint and the code are unchanged.
- **Cost:** No loss occurred. Following the printed hint would have destroyed 127 GB of scratch.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** nisar_wf/trackr.py:668-669 'already present -- skipping'; trackr.py:684-688 '--force means start clean ... shutil.rmtree(p[\"scratch\"], ignore_errors=True)'; run_track_r.py:172-173 epilog; logs/track_r_20260904T124949Z.log 'after fixing, resume with'
- **Automation lesson:** Stage completion = validated output + success marker, not file existence. Make 'force' per-stage (invalidate one stage's outputs), never a blanket scratch wipe, and never print destructive commands as resume hints.

#### WF1-14 — ISCE3 Persistence cannot resume, so 127 GB of rdr2geo + geo2rdr was redone

- **Symptom:** Tested against the real log, Persistence marked all 18 steps to run. A 2-line log also failed. Only a strictly 1-line log resumed, and that skipped prepare_insar_hdf5, which creates the RIFG. The Sep 6 17:40 rerun truncated and rewrote x/y/z.rdr ('x/y/z.rdr are back to 2 bytes').
- **Root cause:** (1) nisar/workflows/persistence.py:65 resets success_msg_found = False on every line while scanning the log in reverse, and the loop runs to the top of the file. (2) nisar/workflows/runconfig.py:93-104 forces args.restart = True unless --log-file is passed, and applies logging.write_mode (:102) before Persistence reads the log. (3) Even when coerced, the resume skips prepare_insar_hdf5. The research plan first blamed write_mode 'w' alone, which is the wrong mechanism.
- **Fix:** Treat as no resume. Use standalone entry points (nisar.workflows.unwrap / ionosphere) against a finished RIFG instead. logging.write_mode set to 'a'.
- **Cost:** rdr2geo 1h39m + geo2rdr 6.6 min on 8 cores (13h05m + 22m of work on 2 cores that had survived the OOM).
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** persistence.py:65 'success_msg_found = False' (installed); runconfig.py:93-104; L1730: 'it marks every step True, including rdr2geo'; L1740: 'a single-line log resumes from coarse_resample. But it also skips prepare_insar_hdf5'; L1890: 'x/y/z.rdr are back to 2 bytes, recreated at 17:40 — rdr2geo is being redone'
- **Automation lesson:** Own stage state in the pipeline (per-stage done markers + output validation) and call ISCE3 stages individually. Never rely on nisar Persistence. Keep looks-independent geometry in a shared, identity-named cache.

#### WF1-15 — The ionosphere stage is silently skipped unless product_type is RUNW/GUNW

- **Symptom:** None observed in this workflow. Research found that with product_type RIFG the ionosphere stage no-ops with no error, no output and no warning.
- **Root cause:** nisar/workflows/insar.py:120-124 gates ionosphere on 'RUNW' in out_paths. Troposphere is likewise gated on GUNW (:150-152).
- **Fix:** product_type switched to RUNW. nisar_wf/config.py raises ConfigError if ionosphere_enabled is set with a product type that cannot reach it. The guard later fired correctly on the AOI config.
- **Cost:** None. The averted risk was an 8.5 h run with no ionosphere.
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** L1071: 'Ionosphere is gated on RUNW in out_paths (insar.py:120-124). Under RIFG it's skipped silently'; configs/nepal_glof.yaml track_r product_type comment; TRACK_R.md:51-57; L4191 guard fired on AOI run
- **Automation lesson:** Validate stage reachability from the requested outputs at config time and fail on unreachable requested stages.

#### WF1-16 — No TEC in the granules and no IONEX reader, so no ionospheric geolocation correction

- **Symptom:** At ionosphere config load ISCE3 logged 'IMAGEN TEC was not provided.' The absolute range shift at 20 TECU is about 6.9 m (about 2.2 freq A range pixels). The date-to-date differential (plausibly about 1 px) is uncorrected.
- **Root cause:** The granules carry no TEC datasets, and no IONEX reader exists in installed nisar/isce3. Split spectrum corrects ionospheric phase, not the geolocation/timing shift.
- **Fix:** None. Documented (TRACK_R.md:259-267).
- **Cost:** A residual sub-pixel to about 1 px differential geolocation error in both tracks.
- **Caught by:** assistant · **Status:** by design
- **Evidence:** L1916: 'IMAGEN TEC was not provided'; L1077 text: 'these granules carry no TEC data and there's no IONEX reader'
- **Automation lesson:** Treat the TEC source as an explicit ancillary input (NISAR TEC JSON via isce3.atmosphere.tec_product, or IONEX) and log when it is absent.

#### WF1-17 — generate_insar_mask second call site: a pure-Python loop over the 2886 Mpx interferogram grid (8.3 h, then OOM on 31 GB)

- **Symptom:** None observed in this workflow; found by research and proven from the freq B product: interferogram/mask (5911, 6781) vs pixelOffsets/mask (1659, 208). At freq A 1x1 the loop would run 2.886e9 iterations appending to a Python list: about 58 GB transient, measured 10.3 s/Mpx, so about 8.3 h and then OOM.
- **Root cause:** generate_insar_mask is called twice: InSAR_L1_writer.py:535 on the pixel-offsets grid and :753 (documented as :752) on the interferogram grid via igram_slant_range. The stock implementation (utils.py.orig) builds the mask with a per-pixel Python double loop into a list, then np.array int64, then uint32.
- **Fix:** tools/patches/insar_mask_vectorized.py (applied by tools/apply_patches.py): preallocated uint32 output with a vectorised range loop. Stock code kept as _generate_insar_mask_stock (installed utils.py:497) and used when num_sub_swaths != 1. Verified bit-identical on real freq B data by tools/test_insar_mask_patch.py, including both rounding conventions (int(v+0.5) vs round). 0.82 s/Mpx, about 40 min at freq A 1x1.
- **Cost:** The 8.3 h + OOM was averted. Developing the patch took about 5 min.
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** installed InSAR_L1_writer.py:535 and :753 (grep); L1943: 'interferogram/mask (5911, 6781) uint32 <- the INTERFEROGRAM grid'; L1988: 'Bit-identical on 2.71 Mpx ... stock: 10.3 s/Mpx × 2886 Mpx = 8.3 hours, then OOM; vectorised: 0.82 s/Mpx ≈ 40 min'; tools/patches/insar_mask_vectorized.py docstring; L2092 RUNW masks present after patched run
- **Automation lesson:** Profile every ISCE3 helper on the largest grid the pipeline allows (seconds per Mpx on a small band, extrapolated). Keep patches as registered, test-backed overlays with bit-identity regression tests.

#### WF1-18 — Assistant dismissed the correct mask diagnosis ('off by ~1000x') and launched a doomed run

- **Symptom:** On Sep 6 17:37 the assistant told the user a research agent's 2886 Mpx / 116 GB mask estimate was 'off by ~1000×' and launched freq A RUNW at 17:40. At 19:03 it had to kill that run in rdr2geo when the research re-flagged the second call site.
- **Root cause:** The assistant verified only the first call site (pixel-offsets grid, 2.82 M) and generalised from it without enumerating all call sites.
- **Fix:** Run stopped. The vectorised patch was written and verified, then relaunched at 19:09. The error was admitted to the user.
- **Cost:** 1h23m of 8-core compute (logs/track_r_20260906T174001Z.log 'failed with return code -15 after 1h23m'). Letting it continue would have cost about 3 h more of rdr2geo plus 8.3 h in the mask loop, then OOM.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L1819: 'one agent claimed 2886 M and a 116 GB accumulator, which is off by ~1000×. My original diagnosis holds'; L1943: 'I checked only the pixel-offsets call site earlier and told you the research was \"off by ~1000×\" — it wasn't, I was.'; L2046 'I got something wrong, and it would have cost you another failed run'
- **Automation lesson:** Verification of a memory or runtime claim must grep all call sites of the function and evaluate each on its actual grid. Treat a disagreement with an independent check as unresolved until reproduced, not as refuted.

#### WF1-19 — snaphu tiling memory: [4,4] x nproc 8 = 40.2 GB, and default single_tile_reoptimize / regrow_conncomps re-solve the full grid (69 GB)

- **Symptom:** None observed in this workflow; the risk was caught in config before snaphu ran. The assistant's config had ntiles [4,4] with nproc 8 on the 13300x13561 4x4 grid: 5.03 GB per process, 40.2 GB total, on a 31 GB box. Stock defaults single_tile_reoptimize: True and regrow_conncomps: True each work over the whole grid: 180.4 Mpx x 385 B = 69 GB.
- **Root cause:** snaphu peak RAM is per-tile x nproc at a measured 385 B/px. The re-optimisation flags silently restore single-tile memory and undo tiling.
- **Fix:** ntiles [8,8] (11.5 GB) for 4x4, [32,32] overlap 256 for 1x1 (1.85 GB x 8 = 14.8 GB), [4,4] for the 40 Mpx 9x8 grid. Both flags false. Consequence: connected components come from the tiled solve and its stitching.
- **Cost:** None. OOM averted.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L2017: '`ntiles [4,4]` × `nproc 8` = 40.2 GB — would have OOM'd. And the default `single_tile_reoptimize` adds a 69 GB full-grid pass.'; configs/nepal_glof.yaml track_r unwrap comments; TRACK_R.md:391-397
- **Automation lesson:** Compute ntiles from grid size, bytes/px, nproc and a RAM budget. Force single_tile_reoptimize/regrow_conncomps off whenever ntiles > 1, and record that the scientific solution depends on tiling.

#### WF1-20 — generate_dem_rdr has no existence guard, so the 11.5 GB RIFG_ifgram_dem was regenerated

- **Symptom:** RIFG_ifgram_dem.rdr (11,543,123,200 bytes, byte-exact size, Sep 5 mtime) was regenerated in the Sep 6 rerun (mtime moved to 22:33).
- **Root cause:** The nisar workflow recomputes the interferogram-grid DEM unconditionally.
- **Fix:** None. Noted as a candidate patch, not written.
- **Cost:** 1h37m on 8 cores (13h33m-equivalent on 2 cores).
- **Caught by:** assistant · **Status:** open
- **Evidence:** L2069: 'RIFG_ifgram_dem.rdr is 11,543,123,200 bytes ... still carrying its Sep 5 mtime'; L2092: 'The RIFG_ifgram_dem.rdr was regenerated (mtime moved to 22:33) ... confirming generate_dem_rdr has no existence guard ... That cost 1 h 37 m.'
- **Automation lesson:** Wrap expensive geometry products in an identity-keyed cache (DEM hash + radar grid + looks) checked before recompute.

#### WF1-21 — ValueError 'nlooks must be >= 1, instead got 0.6189996726516942' at 8h34m (crossmul 1x1 / unwrap 4x4 split)

- **Symptom:** The insar run died at the unwrap step after 8h34m. logs/track_r_20260906T190957Z.log: unwrap.py line 295 -> snaphu/_unwrap.py:317 'ValueError: nlooks must be >= 1, instead got 0.6189996726516942'.
- **Root cause:** When snaphu.nlooks is null, stock unwrap.py:563 (get_effective_looks; now :594 in the patched file) computes rg_spac*az_spac/(rg_res*az_res), reading spacings from the RIFG's interferogram group, i.e. the crossmul 1x1 grid, not the 4x4 grid being unwrapped. A 1x1 SLC is oversampled relative to its resolution cell, so the value is 0.619 < 1. At phase_unwrap 1x1 there is no valid nlooks. The later value 1.0 is a fiction that snaphu requires.
- **Fix:** snaphu.nlooks set explicitly: 9.904 (4x4), 1.0 (1x1 pass), 44.57 (9x8). Resumed through the standalone unwrap against the finished RIFG. The config comment still calls 1.0 'the honest floor', contradicting the later admission.
- **Cost:** No coregistration lost (RIFG 28.6 GB intact). Restart overhead of the standalone path.
- **Caught by:** crash · **Status:** worked around
- **Evidence:** logs/track_r_20260906T190957Z.log:1961-1972 traceback; unwrap.py.orig:563 'nlooks = rg_spac * az_spac / (rg_res * az_res)'; L2168; L2489: 'I set snaphu.nlooks: 1.0 and called it an \"honest floor\". It isn't. The true value is 0.6190'; configs/nepal_glof.yaml track_r: '1.0 is the honest floor: one independent look per sample'
- **Automation lesson:** Always pass nlooks explicitly, computed from the unwrap grid (effective looks per sample x az_looks x rg_looks). Assert nlooks >= 1 at config time, before any compute.

#### WF1-22 — KeyError 'RUNW_STANDALONE': the standalone unwrap entry point is unusable in nisar 0.25.12

- **Symptom:** `python -m nisar.workflows.unwrap` failed immediately with KeyError: 'RUNW_STANDALONE'.
- **Root cause:** nisar/workflows/h5_prep.py get_products_and_paths: h5_paths defines RUNW_STANDALONE/GUNW_STANDALONE, but product_dict (h5_prep.py.orig:102-110) lacks them, while unwrap.py sets product_type to exactly that string.
- **Fix:** tools/patches/h5_prep.py adds 'RUNW_STANDALONE': ['RUNW'] and 'GUNW_STANDALONE': ['GUNW'] (installed h5_prep.py:112-120), registered in tools/apply_patches.py with --check/--revert.
- **Cost:** About 1 min.
- **Caught by:** crash · **Status:** fixed
- **Evidence:** L2190: 'ISCE3 bug: h5_paths has RUNW_STANDALONE but product_dict doesn't — so the standalone unwrap path KeyErrors in 0.25.12.'; installed h5_prep.py:112-120 'ASC PATCH'
- **Automation lesson:** Smoke-test every standalone ISCE3 entry point the pipeline uses (unwrap, ionosphere, crossmul) on a tiny cropped product in CI.

#### WF1-23 — KeyError 'flatten_path', then KeyError 'coregistered_slc_path', in standalone unwrap

- **Symptom:** Standalone unwrap died with 'KeyError: flatten_path' (Sep 7 04:01). After that was fixed and it restarted, it died again with 'KeyError: coregistered_slc_path' (04:12).
- **Root cause:** crossmul.run() reads crossmul_params['flatten_path'] (crossmul.py:45) and ['coregistered_slc_path'] (crossmul.py:75-76). InsarRunConfig/CrossmulRunConfig/IonosphereRunConfig inject them (insar_runconfig.py:139, crossmul_runconfig.py:28, ionosphere_runconfig.py:348) but UnwrapRunConfig does not. Standalone entry points are under-tested relative to insar.py. The keys were found one crash at a time until the assistant enumerated all 7.
- **Fix:** The renderer always emits crossmul.flatten_path and crossmul.coregistered_slc_path (visible in cfg/insar_20260714_20260726_A_HH_1x1.yaml). All 7 keys read by crossmul.run() checked as satisfied.
- **Cost:** Two restarts of the RUNW Topo passes, a few min each.
- **Caught by:** crash · **Status:** fixed
- **Evidence:** L2234 notification 'KeyError: flatten_path'; L2245: 'crossmul.py:45 wants crossmul_params[flatten_path], which CrossmulRunConfig populates but UnwrapRunConfig doesn't'; L2298 'KeyError: coregistered_slc_path'; L2316: 'crossmul.run() reads seven keys, and coregistered_slc_path is the only one missing'
- **Automation lesson:** Before calling any ISCE3 stage function, statically enumerate the config keys it reads and validate the rendered runconfig against that list, instead of discovering keys by crashing.

#### WF1-24 — phase_unwrap was multilooked 4x4 without asking the user

- **Symptom:** User: 'dude you need to atleast ask before doing this, anyways we want to unwrap at 1,1 itself not multilook.'
- **Root cause:** The assistant set phase_unwrap 4x4 (Sep 6 17:37, for the 34.6 GB open_raster read) and told the user afterwards, although the user had repeatedly asked for 1x1.
- **Fix:** The 4x4 standalone unwrap was killed at 04:17 and reconfigured for 1x1 after the streaming patch.
- **Cost:** About 27 min of 4x4 Topo work (unwrap_standalone.log truncated at block 24/65). The original 8.5 h run had also been configured at 4x4.
- **Caught by:** user · assistant error · **Status:** fixed
- **Evidence:** L1073: 'Set crossmul 1×1, phase_unwrap 4×4.'; L2330 user quote; L2335: 'I set phase_unwrap to 4×4 and explained it afterwards rather than checking first. It's a config knob, not a fact about the data, so it was yours to decide.'
- **Automation lesson:** Resolution-changing parameters (looks at any stage) are user-owned. The pipeline must fail or ask, never silently degrade, when a requested resolution is not feasible.

#### WF1-25 — Assistant wrongly said 1x1 unwrap was 'not possible on this box'; the blocker was ISCE3's whole-array open_raster read

- **Symptom:** The assistant said freq A 1x1 unwrap needs 34.6 GB of RAM (23.1 GB complex64 igram + 11.5 GB coherence) and is impossible. User: 'how ???? ... isnt that the whole point of tiling that we can unwrap bigger aois.'
- **Root cause:** Stock nisar/workflows/unwrap.py:278-279 (unwrap.py.orig) calls open_raster() on the interferogram and coherence, loading them into numpy before snaphu. snaphu.unwrap() accepts any InputDataset protocol object, and snaphu.io.Raster streams from GDAL. Also, rasterio needs 'HDF5:<file>://<group>' while ISCE3 builds 'HDF5:<file>:/<group>'.
- **Fix:** tools/patches/unwrap.py wraps both inputs in snaphu.io.Raster and normalises the HDF5 path. Peak RSS to open both 1x1 inputs was 0.10 GB, and the 1x1 unwrap ran at 2.6-5.1 GB RSS.
- **Cost:** About 5 min to patch. The wrong 'not possible' claim, left uncorrected, would have removed the user's requested product.
- **Caught by:** user · assistant error · **Status:** fixed
- **Evidence:** L2294: 'Frequency A at 1×1 unwrap: not possible on this box'; L2330 user quote; L2349: 'You're right, and my \"not possible\" was wrong ... The 34.6 GB read is ISCE3's unwrap.py calling open_raster() into a numpy array'; L2396: 'the failing one is exactly the form ISCE3 builds (`:/` single slash vs `://`)'; L2400 'peak RSS 0.10 GB instead of 34.6 GB'
- **Automation lesson:** Distinguish library capability from wrapper limitation before declaring something infeasible. Pass file-backed datasets into snaphu and similar solvers.

#### WF1-26 — Snaphu tile overlap initially 128 px (570/608 m) with no global re-optimisation; seams never checked

- **Symptom:** The user asked whether tiles overlapped enough to resolve 2*pi offsets. The assistant had 128 px, which is thin at 1x1 with single_tile_reoptimize false. After it was raised to 256 px, snaphu still ended with 'SUGGESTION: Try increasing tile overlap and/or size if solution has edge artifacts'. The promised check of connected-component edges against the 32x32 tile grid was never done.
- **Root cause:** Overlap was chosen as a speed knob, not a correctness parameter. With reoptimisation off, the stitching over the overlap is the only inter-tile cycle resolution.
- **Fix:** tile_overlap [256,256] (about 15% of a 2174x2207 tile), confirmed in snaphu's own config (ROWOVRLP/COLOVRLP 256). No seam QA performed.
- **Cost:** 1x1 unwrap: snaphu 12h40m wall / 93.9 CPU-h over 1024 tiles. The ISCE3 journal records phase unwrapping at 46233 s (12.84 h) including Topo. Seam quality of the benchmark 1x1 RUNW is unverified.
- **Caught by:** user · assistant error · **Status:** open
- **Evidence:** L2330 user: 'I hope you made sure there was overlapping region between the tiles'; L2455: 'I'd set tile_overlap: [128, 128], which at 1×1 is only 570 m azimuth / 608 m range'; logs/unwrap_1x1.log:1320 'SUGGESTION: Try increasing tile overlap'; L1902: 'Worth checking whether component edges align suspiciously with the 32×32 tile grid.'; logs/insar_20260714_20260726_A_HH_1x1.log:72446 'Successfully ran phase unwrapping in 46233.266 seconds'
- **Automation lesson:** Size overlap from correlation length / tile size as a correctness parameter. Add an automatic seam QA: phase and conncomp discontinuity statistics along tile boundaries vs interior.

#### WF1-27 — bridge_unwrapped_phase does a whole-array read (~26 GB at 1x1) after snaphu; the 1x1 run was restarted and shipped unbridged

- **Symptom:** None observed in this workflow; research caught it before snaphu finished. With bridge at its default (enabled) the 1x1 unwrap would have been SIGKILLed after hours of snaphu (no swap, overcommit 1).
- **Root cause:** Stock unwrap.py.orig:324-325 (patched file :356): `unwrapped_phase = dst_h5[unw_path][()]`, then bridge_phase adds a bool mask and int32 labels: 11.5 + 2.9 + 11.5 GB on the 2886 Mpx grid.
- **Fix:** phase_unwrap.bridge.enabled false for the 1x1 pass, true for the 9x8 pass (0.16 GB). As a result, the benchmark 1x1 RUNW has no bridging of disconnected components.
- **Cost:** Restart at Topo block 111/259: 52 min.
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** unwrap.py.orig:324-325; L2489: 'unwrap.py:356 does unwrapped_phase = dst_h5[unw_path][()] ... ~26 GB on the 2886 Mpx grid, and it happens after snaphu has run for hours. Bridge is now off for the 1×1 pass. Caught it at Topo block 111/259, so it cost 52 min'
- **Automation lesson:** Memory pre-flight must cover post-processing steps (bridge, stats, copies), not just the solver. Stream or tile post-unwrap operations, or fail at config time on grids above a threshold.

#### WF1-28 — Ionosphere freq B looks are coupled to phase_unwrap looks; a 1x1 unwrap + ionosphere would solve on coherence == 1 with zero uncertainty

- **Symptom:** None observed in this workflow; found before running ionosphere at 1x1. Research traced that at phase_unwrap 1x1 the whole split-spectrum solve would run at 1x1: the coherence gate passes every pixel, and estimate_iono_std returns exactly 0.0.
- **Root cause:** ionosphere.py:668-675 copies phase_unwrap range/azimuth looks into crossmul for the freq B pair it builds. filter_mask_type defaults to ['coherence'] (defaults/insar.yaml:284), and 1x1 coherence is hardcoded 1.0. The two RUNWs must share azimuth length (ionosphere.py:1302-1304, :1566-1578), so freq A cannot stay 1x1 while the ionosphere pair is multilooked.
- **Fix:** Split into two RUNW passes from the same 1x1 RIFG: pass 1 phase_unwrap 1x1 with ionosphere off; pass 2 phase_unwrap 9x8 with ionosphere on (RUNW_..._1x1_unw9x8.h5). The 1x1 RUNW's ionospherePhaseScreen/Uncertainty datasets exist but are zero-filled (verified: mid-row min=med=max=0.0).
- **Cost:** A second unwrap pass (about 23 min unwrap + 27 min Ionosphere per ISCE3 journal).
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** installed ionosphere.py:668-675 (runw_rg_looks copied into crossmul); L2444; L2430 research: 'with γ=1 that is exactly 0.0. The delivered ionospherePhaseScreenUncertainty would be identically zero'; L2766: 'ionospherePhaseScreen all == 0.0 ... zero-filled placeholders'; audit h5py read of RUNW_20260714_20260726_A_HH_1x1.h5
- **Automation lesson:** Model the ionosphere stage as its own grid (separate looks) and its own product. Never emit zero-filled placeholder layers without an explicit 'not computed' attribute.

#### WF1-29 — 1x1 snaphu solve was fed a coherence == 1.0 layer and NCORRLOOKS 1.0, a degenerate cost model for the benchmark RUNW

- **Symptom:** snaphu config for the 1x1 run: 'NCORRLOOKS 1.0 STATCOSTMODE SMOOTH INITMETHOD MCF'. The RUNW 1x1 coherenceMagnitude is min = median = max = 1. The 3x3 coherence (median 0.574) was computed only on Sep 8, after the unwrap had finished, and was never used as snaphu input.
- **Root cause:** At phase_unwrap 1x1, unwrap.py uses the RIFG's coherence (Crossmul.cpp hardcodes 1.0) as snaphu's correlation input, and copies it into the RUNW. So snaphu's statistical cost had no information on where the scene is decorrelated. Research warned of this. The assistant disclosed the nlooks fiction and 'don't gate on coherence' but not that the solver itself ran without coherence information.
- **Fix:** None. The 1x1 RUNW (32 connected components) stands as produced.
- **Cost:** Unquantified quality risk (unwrapping errors in decorrelated areas) in the full-resolution benchmark unwrapped phase. Re-running with the 3x3 coherence as corr input would cost about 13-15 h.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** L2526 snaphu config 'NCORRLOOKS 1.0 ... STATCOSTMODE SMOOTH'; L2727 'coherenceMagnitude (53200, 54244) float32 min=median=max = 1'; L2430 research: 'if you disabled bridge, you'd get a ... solution over fully-speckled phase with a coherence layer of all 1.0'; logs/coherence_win3.log (Sep 8 05:24); audit h5py read (RUNW 1x1 coherence mid-row 1.0)
- **Automation lesson:** The unwrap stage must take coherence from a validated estimator (moving window at full resolution) and refuse a constant or degenerate coherence input (std == 0).

#### WF1-30 — The streaming unwrap moved the 34.6 GB from RAM to disk; free disk was projected to run out mid-snaphu

- **Symptom:** Free disk dropped from 111 GB to 71 GB in about 15 min. snaphu-py wrote flat copies (snaphu.igram.*.c8 23.1 GB, snaphu.corr.*.f4 11.5 GB, then unw + conncomp 11.5 GB each) plus about 78 MB per tile. Projected need was 128 GB against 98 GB free. The 'short by 31 GB' figure double-counted, because snaphu-py rmtree's its scratch at the end (snaphu/_util.py:269).
- **Root cause:** snaphu.io.Raster streaming avoids RAM, but snaphu-py stages flat binary inputs and outputs in a scratch dir on the same filesystem as the 359 GB Track R scratch.
- **Fix:** Reclaimed 30 GB of freq B 9x1 geometry layers (kept its coregistered SLC pair), coarse_resample_slc (23.1 GB, unused because unwrap.py:127 selects 'fine') and dense_offsets (23.2 GB, already consumed by rubbersheet). The user grew the disk to 700 GB. Note: the full-tile ampcor scratch outputs no longer exist.
- **Cost:** About 30 min of analysis. A risk of ENOSPC about 8 h into the 12.7 h unwrap was averted. Intermediates were permanently deleted.
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** L2538: 'Disk dropped from 111 GB to 71 GB in ~15 minutes'; L2543: 'snaphu-py writes flat binary copies of its inputs'; L2588: 'reclaimed 44 GB by deleting ... coarse_resample_slc (23.1 GB) ... dense_offsets (23.2 GB)'; L2609: 'My earlier \"short by 31 GB\" double-counted'; audit ls: scratch/trackR/20260714_20260726_A_HH_1x1 has no coarse_resample_slc or dense_offsets
- **Automation lesson:** Point the solver scratch dir at a budgeted location and include the solver's flat input/output copies and tile files in the disk model. Declare which intermediates each later stage consumes so cleanup is safe and automatic.

#### WF1-31 — ISCE3 crossmul has no sliding-window coherence; 1x1 coherence is hardcoded 1.0. The user had to ask for 3x3

- **Symptom:** User: 'dude but coherence was supposed to be calculated on 3x3 interval right ?' The RIFG and RUNW 1x1 coherenceMagnitude are identically 1.0 (the RIFG dataset compresses 226x).
- **Root cause:** cxx/isce3/signal/Crossmul.cpp:378-388: when multilooking is disabled, 'fill coherence with ones (no need to compute result) coherence = 1.0'. crossmul_options has no window parameter. The assistant had known since Sep 3 that 1x1 coherence is degenerate but only framed it as 'ignore that band' or 'use looks'. It never proposed the standard moving-window estimator, though nisar_wf/igram.py already had coherence_window.
- **Fix:** tools/slc_coherence.py: ISCE2 moving-window estimator on the coregistered pair (crossmul/freqA/HH/reference.slc + fine_resample_slc/.../coregistered_secondary.slc), streamed with a halo, BIAS_FLOOR in metadata. Validated on synthetic data (gamma=0 -> 0.289 vs predicted 0.295). Output coherence_A_HH_win3.tif 10.13 GB, median 0.5739, 17.6% at or below floor. Documented at TRACK_R.md:112-191.
- **Cost:** About 10 min runtime plus tool development. The benchmark lacked a usable full-resolution coherence for 5 days.
- **Caught by:** user · assistant error · **Status:** fixed
- **Evidence:** L1377: 'Just don't read anything into that coherence layer'; L2730 user; L2750: 'Crossmul.cpp:378-388 ... ISCE3 hardcodes coherence to 1.0 ... Your own Track G code already does it right'; cxx/isce3/signal/Crossmul.cpp:382-383 (verified); logs/coherence_win3.log 'median 0.5739'
- **Automation lesson:** The pipeline's coherence stage must be an explicit, estimator-configurable module (window size, bias floor recorded). Never accept a product layer whose values are constant.

#### WF1-32 — Output identity: the RUNW name lacked unwrap looks (a 9x8 pass would clobber the 12.7 h 1x1 RUNW); runconfig and log names still lack it

- **Symptom:** Before the 9x8 pass, the output name keyed only on crossmul looks, so RUNW_..._A_HH_1x1.h5 would have been overwritten. After the fix, cfg/insar_20260714_20260726_A_HH_1x1.yaml (mtime Sep 8 05:30) contains the 9x8 pass config (phase_unwrap 9x8, ntiles [4,4], bridge on, ionosphere on). The 1x1 pass's runconfig (ntiles [32,32], nlooks 1.0, bridge off, ionosphere off) no longer exists on disk. logs/insar_20260714_20260726_A_HH_1x1.log accumulates at least 5 runs in append mode (both 'phase unwrapping in 46233 s' and '1389.9 s').
- **Root cause:** The pair tag carries crossmul looks only. trackr.py:261-272 now adds _unw{az}x{rg} to the output, but trackr.py:277-278 still name the runconfig and logfile from the bare tag.
- **Fix:** RUNW output identity fixed (RUNW_20260714_20260726_A_HH_1x1_unw9x8.h5). Runconfig and log identity not fixed.
- **Cost:** A 12.7 h product nearly lost. Reproducibility of the 1x1 unwrap config is now lost except from config comments and the transcript.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** L2823: 'the output name keys off crossmul looks (still 1×1), so a 9×8 unwrap would overwrite RUNW_...A_HH_1x1.h5'; nisar_wf/trackr.py:261-272 and :277-278; cfg/insar_20260714_20260726_A_HH_1x1.yaml content (audit); logs/insar_20260714_20260726_A_HH_1x1.log:72446 and :72994
- **Automation lesson:** Every artefact that encodes a parameter (product, runconfig, log, scratch) must carry all output-changing parameters in its name or content hash. Refuse to overwrite an existing identity without an explicit version bump.

#### WF1-33 — Misunderstood ionosphere grid reconciliation (claimed the 8:1 ratio puts A and B on one grid); the wrong mechanism persists in config comments

- **Symptom:** The assistant told the user that freq A 9x8 (5911x6780) and freq B 9x1 (5911x6781) land on the same grid. The freq B RUNW actually came out 5911 x 847.
- **Root cause:** ionosphere.py:668-675 applies the same 9x8 looks to freq B, whose range grid is already 8x coarser. ISCE3 reconciles explicitly: decimate_freq_a_array (ionosphere.py:226 offsets, :547 data, :1602 mask) solves on the freq B grid, and interpolate_freq_b_array (:356) brings the screen back to the freq A grid. The dispersive solve therefore runs at about 302 m range x 40 m azimuth posting.
- **Fix:** Corrected to the user on Sep 8. Not corrected in configs/nepal_glof.yaml (track_r comment still says 'both give 6780/6781 range samples, because the 8:1 range-spacing ratio exactly cancels'). TRACK_R.md not updated (STATE.md:70-71 lists it as TODO).
- **Cost:** No compute lost. Wrong documentation remains, and it misstates the effective resolution of the ionosphere screen.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** L2335/L2489: 'the two bands land on the same grid ... The 8:1 range-spacing ratio exactly cancels'; L2885: 'The freq B RUNW is 5911 × 847, not 5911 × 6780'; L2123: 'I had this wrong earlier'; audit h5py: scratch/.../ionosphere/main_side_band/RUNW.h5 frequencyB unwrappedPhase (5911, 847); configs/nepal_glof.yaml track_r phase_unwrap comment
- **Automation lesson:** Assert grid shapes after each stage against predicted shapes and fail on mismatch. Generate documentation from observed metadata, not from the design narrative.

#### WF1-34 — Dispersive filter masks about half the pixels and nearest-fills them; a 100% finite screen with large relative uncertainty

- **Symptom:** filter_coherence_threshold 0.5 against a freq B coherence median of 0.503. ionospherePhaseScreen is 100% finite (median -19.63 rad = -1.44 TECU, std 3.17 rad), and its uncertainty median is 1.31 rad (about 40% of the screen's spatial std). A 'RuntimeWarning: All-NaN slice encountered' came from ionosphere_filter.py:1044 (benign).
- **Root cause:** Data and method trap. The 16.79x noise amplification for f0=1.239 GHz / f1=1.2935 GHz forces aggressive masking. Gaps are filled by 'nearest' (defaults/insar.yaml:292), and the product has no measured-vs-filled flag.
- **Fix:** None. Reported with caveats: interpret only the large-scale gradient; the -378 mm median is an arbitrary bulk offset.
- **Cost:** The quantitative reliability of the benchmark ionosphere screen is limited and undocumented per pixel.
- **Caught by:** assistant · **Status:** open
- **Evidence:** L2909: 'filter_coherence_threshold: 0.5 against a frequency B coherence whose median is 0.503 means roughly half the pixels fail that gate'; L2917: 'uncertainty ... median 1.31 rad against a screen whose spatial variation is 3.17 rad std ... a large fraction of this screen is interpolated, not measured'
- **Automation lesson:** Always export the ionosphere validity/fill mask alongside the screen, and report the measured fraction as a QA metric with a threshold.

#### WF1-35 — The Sep 14 status report said the 1x1 RUNW carries the ionosphere screen at full resolution; it is zero-filled

- **Symptom:** The status table said 'Ionosphere (split-spectrum, A+B) ✅ ionospherePhaseScreen 53200×54244, median −19.63 rad' and 'Confirmed just now — the RUNW carries ionospherePhaseScreen ... at full 1×1 resolution'. STATE.md:18 also lists 'RUNW 1x1 17.38 GB ... iono in RUNW'.
- **Root cause:** The check listed dataset names and shapes only, not values. ISCE3 creates zero-filled ionosphere placeholders in a RUNW with ionosphere disabled. The real screen is in RUNW_20260714_20260726_A_HH_1x1_unw9x8.h5 at 5911x6780 (solved at 5911x847). The assistant had itself established on Sep 8 that the 1x1 screens are all zeros.
- **Fix:** None yet. Found in this audit: 1x1 RUNW ionospherePhaseScreen and Uncertainty mid-row min = median = max = 0.0; unw9x8 screen mid-row median -20.05 rad.
- **Cost:** Misinformation to the user about the benchmark's ionosphere product and resolution; risk of downstream use of zeros as a correction.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** L3437 check printed only names/shapes; L3441 status text; L2766: 'ionospherePhaseScreen all == 0.0 ... zero-filled placeholders'; STATE.md:18; audit h5py values
- **Automation lesson:** Status and QA checks must read values (finite fraction, std > 0, non-constant) not just dataset presence. Product manifests should record which layers were actually computed.

#### WF1-36 — ISCE3's default ionosphere Gaussian kernel is in pixels, so on the 5911x847 grid it smooths about 10 km in range but only about 1.3 km in azimuth

- **Symptom:** The R_full ionosphere screen was filtered with kernel_range/azimuth 100 px and sigma 33 px (not overridden) on a grid of about 40 m azimuth x about 302 m range.
- **Root cause:** defaults/insar.yaml:317-321 expresses kernel and sigma in pixel counts. The main_side_band solve grid is strongly anisotropic, so the physical smoothing is about 10 km (range) vs 1.3 km (azimuth), an artefact of the radar grid, not the physics.
- **Fix:** None for Track R. The Track G port specifies sigma in km (--sigma-km).
- **Cost:** Anisotropic smoothing of the benchmark ionosphere screen. Affects cross-track ionosphere comparisons.
- **Caught by:** assistant · **Status:** open
- **Evidence:** L3618: 'Track R solved on a 5911×847 grid ... ISCE3's 100 px / sigma 33 kernel was physically ~10 km sigma in range but only 1.3 km in azimuth'; installed defaults/insar.yaml:317-321
- **Automation lesson:** Specify filter scales in physical units and convert per grid from the actual posting. Log the effective km scales in product metadata.

#### WF1-37 — Documentation and config comments drifted from reality

- **Symptom:** TRACK_R.md:203-212 still says 1x1 unwrap 'exceeds a 31 GB box' and '4×4 is the configured value'. TRACK_R.md:299 says freq A RIFG '~58 GB @ 1×1' (actual 28.6 GB). TRACK_R.md:13-16 and 459-467 say Track G 'not yet run'. TRACK_R.md:362 and patches/insar_mask_vectorized.py cite InSAR_L1_writer.py ':752' (installed :753). TRACK_R.md:386 says mask patch '~17 GB peak' while the patch docstring says '11.5 GB'. configs/nepal_glof.yaml track_r keeps 'frequency: B', '5 x 5 <- configured', '1.0 is the honest floor', a '4x4 is a MEMORY constraint' block, the wrong 8:1-cancels grid claim, and a hard-coded full-tile unwrap_crossmul_path (inherited by the AOI config).
- **Root cause:** The config and the doc were edited incrementally across many passes by appending comments, without reconciling earlier statements. The ionosphere, grid-reconciliation and Track G sections were deferred.
- **Fix:** None yet. STATE.md:69-71 lists documentation as remaining work.
- **Cost:** Anyone automating from these files would inherit wrong limits (1x1 unwrap impossible), wrong mechanisms (grid cancellation), wrong sizes, and a cross-contaminating unwrap_crossmul_path.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** TRACK_R.md:203-212, :299, :362, :386, :459-467; configs/nepal_glof.yaml:472+ track_r block comments; installed InSAR_L1_writer.py:753; RIFG.h5 28.57 GB (audit)
- **Automation lesson:** Generate parameter docs from the resolved config and measured run metadata. Keep one authoritative value per parameter in machine-readable form. Pass-specific settings belong in per-pass config files, not accumulated comments in a shared YAML.

#### WF1-38 — Assistant irreversibly deleted 46 GB of benchmark scratch without asking, based on a disk projection it later admitted was double-counted

- **Symptom:** During the 1x1 snaphu run: 'I reclaimed 44 GB by deleting two verified-dead directories in the freq A scratch' (coarse_resample_slc 23.1 GB, dense_offsets 23.2 GB). Twenty minutes later: 'My earlier "short by 31 GB" double-counted — it added the RUNW on top of a peak that gets cleaned first.'
- **Root cause:** The projection ignored that snaphu-py rmtree's its tile scratch (_util.py:269). The deletion was decided unilaterally, not offered as an option. (The user had to be asked about deleting a granule, but scratch was deleted without asking.)
- **Fix:** None; the directories are gone (audit: full-tile scratch now holds only RIFG.h5, crossmul, fine_resample_slc, geo2rdr, ionosphere, rdr2geo, rubbersheet_offsets, unwrap). The comparison later used RUNW pixelOffsets, so no downstream step has needed them so far.
- **Cost:** Full-tile dense-offset and coarse-resample intermediates of the benchmark are permanently lost; re-creating them means re-running hours of coregistration.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** jsonl L2587 (deletion), L2608 ('My earlier "short by 31 GB" double-counted'), L2618 ('I'll also leave `geo2rdr` (46.2 GB) alone rather than reclaim it on a guess').
- **Automation lesson:** Treat benchmark intermediates as protected. Any deletion needs a user-approved retention policy or explicit confirmation. Model disk usage with per-tool cleanup semantics, and check projections against measured df trends before acting.

#### WF1-39 — Benchmark produced on a patched nisar: four installed overlays are provenance, not optional tweaks

- **Symptom:** TRACK_R.md described the chain as 'stock nisar.workflows.insar'; the installed nisar differs from stock in four files.
- **Root cause:** tools/apply_patches.py installs tools/patches/{resample_slc_v2.py (isce3#372, h5py chunk-cache re-reads; installed Sep 3 15:29), insar_utils.py + insar_mask_vectorized.py (generate_insar_mask; Sep 6 19:07), h5_prep.py and unwrap.py (Sep 7)} over site-packages (critic.json, checked with cmp).
- **Fix:** Record overlay state as provenance in every run (file hashes of the installed modules), and document them in WF1 section 7.
- **Cost:** No compute cost; a reproducibility gap: a stock ISCE3 0.25.12 cannot reproduce the benchmark (it OOMs or KeyErrors).
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** critic.json missing_problems 'Benchmark produced on a patched nisar'; tools/apply_patches.py:39-44
- **Automation lesson:** Pin and hash every overlaid module; refuse to run if the installed hashes differ from the manifest.

#### WF1-40 — Runconfig traps: offsets_product with a non-ROFF product_type, list_of_frequencies required, only the user file is schema-validated

- **Symptom:** Documented in nisar_wf/trackr.py comments but never recorded as problems.
- **Root cause:** insar.py:64-66 calls offsets_product.run(cfg, out_paths['ROFF']) with no guard (KeyError 'ROFF' when product_type lacks ROFF); runconfig.py:110-111 calls .keys() on the user list_of_frequencies; yamale validates the user file, not the merged defaults.
- **Fix:** trackr.py forces offsets_product.enabled false unless ROFF is produced, always emits list_of_frequencies, and validates the rendered runconfig.
- **Cost:** Avoided failures; no measured cost.
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** nisar_wf/trackr.py:323-330, :450-453; critic.json missing_problems
- **Automation lesson:** Render the full runconfig, validate the merged result, and assert product_type/stage compatibility before launch.

#### WF1-41 — RSLC 3x3 coherence (tools/slc_coherence.py) is computed from an UNFLATTENED SLC pair

- **Symptom:** Topographic and flat-earth phase inside each 3x3 window lowers |sum a b*|; GSLC coherence is computed from flattened GSLCs, so the two were not like for like.
- **Root cause:** slc_coherence.py reads crossmul/reference.slc and fine_resample_slc/coregistered_secondary.slc and applies no flattening (grep finds none).
- **Fix:** tools/compare_four_way.py v2 computes both forms from the same radar window: flattened (|sum RIFG| / sqrt(sum|a|^2 sum|b|^2)) and unflattened, and uses the flattened form as the benchmark. slc_coherence.py should take the RIFG or a flatten phase.
- **Cost:** v1 comparison coherence statistics were biased against RSLC by an unmeasured amount (measured in comparison_v2).
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** critic.json contradictions #20 and missing_problems; compare_four_way.py v2 box_coherence/rslc_layers
- **Automation lesson:** Estimate coherence from the flattened interferogram product, and state the estimator inputs in the product metadata.

#### WF1-42 — ISCE3 main_side_band ionosphere has no absolute cycle-level referencing

- **Symptom:** Cropped and full-tile RSLC runs over the same pixels gave screens whose SHAPE agrees (residual 0.026 TECU) but whose LEVEL differed by +3.59 TECU (v1); unwrapped freq A differed by exactly -1 cycle and freq B by -2 whole cycles.
- **Root cause:** unwrapping_correction_with_filter (ionosphere_filter.py:1105-1117), compute_unwrapp_error (main_band_estimation.py:712-763) and bridging (bridge_phase.py:16-78) are all relative/local; an absolute (m,n) common to the inputs and the filtered reference passes through (verdicts.json V1 (f)).
- **Fix:** None upstream. Treat absolute TEC from main_side_band as unreferenced; reference it externally (TEC product) or resolve the cycle class explicitly (as tools/gslc_ionosphere.py does, with its own limits).
- **Cost:** Absolute ionosphere level not reproducible under a change of processing extent.
- **Caught by:** guardrail/tool check · **Status:** open
- **Evidence:** verdicts.json V1; comparison_v2/comparison.json I3 (re-measured with the corrected crop)
- **Automation lesson:** Never ship an absolute TEC level from a two-band solve without an external reference or an explicit, logged cycle resolution.

#### WF1-43 — 3x3 coherence bias floor (~0.295) is an interpretation trap

- **Symptom:** Fully decorrelated ground reads ~0.3, not 0, in a 3x3 moving-window coherence.
- **Root cause:** A magnitude-of-sum estimator is biased high for small N: floor sqrt(pi)/(2 sqrt(N)) = 0.295 for N=9 (tools/slc_coherence.py:40-50).
- **Fix:** Floor written into GeoTIFF metadata; comparison reports distributions against it.
- **Cost:** None if respected.
- **Caught by:** assistant · **Status:** by design
- **Evidence:** tools/slc_coherence.py:40-50; TRACK_R.md:173-191
- **Automation lesson:** Carry the estimator N and bias floor with every coherence product; threshold against the floor, not zero.

#### WF1-44 — Ionosphere-only runtime misattributed the freq-B stages to the journal 'Ionosphere' timer

- **Symptom:** Two answers put the ionosphere layer from scratch at 7-7.5 h / 7.25-8 h.
- **Root cause:** 'successfully ran Ionosphere in 1626.8 s' covers only solve, filter and write (scratch rasters 06:28-06:55); the freq-B rdr2geo, geo2rdr, prep, resample, crossmul and unwrap (2309 s) are timed separately.
- **Fix:** Corrected to 7 h 38 m - 8 h 05 m in the stage-timing page and to the user.
- **Cost:** Two slightly low estimates given to the user.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** logs/insar_20260714_20260726_A_HH_1x1.log lines 72995-85420; ionosphere scratch mtimes
- **Automation lesson:** Verify what a journal timer encloses before summing stage times.

#### WF1-45 — Absolute ionosphere level differs from the NISAR GUNW by one joint cycle

- **Symptom:** R_full screen - GUNW screen = +3.164 rad (+0.986 joint cycles, 0.232 TECU); shapes agree to 0.0135 TECU.
- **Root cause:** Split-spectrum degeneracy along m = n; ISCE3 main_side_band has no absolute referencing, GUNW uses main_diff_ms_band with unwrapping-error correction.
- **Fix:** None; documented. Relative deformation is unaffected; absolute TEC needs GIM/GNSS.
- **Cost:** None.
- **Caught by:** guardrail/tool check · **Status:** open
- **Evidence:** gunw_validation/gunw_validation.json ionosphere_80m
- **Automation lesson:** Record (m, n) and the estimator for every screen; never mix screens from different estimators in a mosaic.

<!-- ERROR-LOG:END -->

## 12. Automation contract

**Preconditions.** Section 2.4 gates pass; overlay hashes match; both granules have identical track, frame and
direction; the DEM covers the swath with buffer; no other job writes the pair's scratch.

**Postconditions.** RIFG and RUNW exist and pass the inventory and finiteness gates of section 9; the delivered
coherence is either a looks > 1 estimate or accompanied by the 3×3 product; the ionosphere layer is non-zero if
and only if ionosphere was enabled; the runconfig used is stored inside each product.

**Idempotency and caching.** ISCE3 itself is not resumable; the orchestrator must treat each `insar` invocation
as atomic from rdr2geo, never pass `--force` automatically, and detect corrupt outputs by opening them rather than
by existence. Key every product and log by the full identity (crossmul looks, unwrap looks, frequency, pol,
ionosphere on/off, overlay manifest hash).

**Failure handling.** Classify exits: OOM (`return code -9`), SIGTERM (`-15`), Python exceptions (traceback in
the wrapper log). On OOM, do not retry unchanged; check section 8.1 consumers. On a mid-write kill, delete the
partial HDF5 before any retry.

**Monitoring.** Heartbeat from the ISCE3 journal (stage lines and block counters), RSS and free disk every few
minutes, and an `EXIT=` marker; alert on no journal progress for longer than the slowest block interval, and on free
disk falling toward the remaining scratch bill.
