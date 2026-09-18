# Nepal GLOF four-workflow comparison — state as of 2026-09-15 ~10:00 UTC

This file is the resume point. Claims that were withdrawn or refuted are listed at the bottom so they are not
re-introduced. Evidence: `case_studies/nepal_glof/comparison_v2/comparison.json` (current) and
`case_studies/nepal_glof/comparison/verification/` (v1 verification, critic, error logs).

## Goal (user, 2026-09-14)

Build and compare four NISAR InSAR workflows, then automate a modular SLC → interferogram pipeline from the
learnings. **Full-tile RSLC is the quality benchmark.** Each workflow gets an in-depth document with its own
problems log, and the team gets an HTML report written in a research-note register.

## Status: all planned work is complete

The comparison runs have exited. Most local comparison data was deleted on 2026-09-15 (archive: gs://s1-slc/nisar_workflow/); the paths in the table below refer to the archive. PDF of the report: `/home/sharath/isce3/nepal_glof_four_workflow_report.pdf` (previous revisions `_rev1.pdf`, `_rev2.pdf`), built by `tools/report_pdf.py` in `~/.venvs/report-pdf`.

| item | where |
|---|---|
| R_full (WF1) | `pairs/20260714_20260726/trackR/`, scratch `scratch/trackR/20260714_20260726_A_HH_1x1/` |
| G_full (WF2) | `pairs/20260714_20260726/trackG/`, `L2_GSLC/` |
| R_crop v2 (WF3) | `L1_RSLC_AOI_v2/`, `aoi_v2/pairs/.../trackR/`, `aoi_v2/scratch/trackR/...` |
| G_crop v2 (WF4) | `aoi_v2/L2_GSLC/`, `aoi_v2/pairs/.../trackG/` |
| comparison v2 | `comparison_v2/comparison.json`, `supplement_nondispersive.json`, `layers/` (log `logs/compare_four_way_v2.log`) |
| figures | `comparison_v2/report/fig/*.webp`, `figures.json`, `footprints.json` (log `logs/report_figures_v2.log`) |
| alignment test | `comparison_v2/alignment/alignment.json` (v3; `_ABORTED_*` = invalid earlier attempts), `logs/alignment_test.log` |
| iono transfer / GUNW validation | `comparison_v2/iono_transfer/`, `comparison_v2/gunw_validation/`; GUNW product in `L2_GUNW/` (+ `gunw_runconfig.json`) |
| HTML report | `comparison_v2/report/nepal_glof_four_workflow_report.html` (built by `tools/build_report.py`; published as a private claude.ai artifact) |
| docs | `docs/README.md` index → WF1–WF4, COMPARISON, OPERATIONS_AND_LESSONS (fact-checked 2026-09-15) |

Superseded and kept as evidence: `L1_RSLC_AOI/` + `aoi/` + `comparison/` (v1, native-Doppler crop and A/B origin bugs),
`*_ABORTED_nativeDoppler/`, `tools/legacy/`, `docs/legacy/`.

## Findings (comparison v2)

- **Crop-first RSLC ≡ full tile.** Reference SLC bit-identical; coregistered secondary R 0.9925; dense offsets full − crop
  std 0.022 lines / 0.025 samples; wrapped phase R 0.9944 (5 m) / 0.9951 (40 m); coherence medians 0.6336 / 0.6336.
- **Crop-first RSLC ionosphere: shape yes, level no.** Residual 0.0027 TECU (r 0.9995); offset −72.96 rad = exactly one
  freq-B unwrapping cycle (A: 0 cycles, B: +1 cycle on 99.2%, remainders < 0.001 cycles). The v1 non-integer B error is gone.
  The non-dispersive median is +18.6 rad (R_full) vs −54.4 rad (R_crop); the GSLC tool's class criterion would keep R_full's level.
- **G_full ≡ G_crop.** Phase R 1.0000; amplitude p95 ≤ 0.055%; ionosphere +0.0002 TECU after the resolver's (−2, 0) vs (0, 0).
- **RSLC vs GSLC phase.** R 0.600 (5 m) / 0.892 (40 m). The difference ≈ −k·δ_az (k = 2π·f_dc/1520 Hz, median 3.975 rad/line;
  δ_az = R_full rubber-sheet azimuth correction); modelled R 0.939 (0.995 where γ > 0.7); free fit −4.00.
- **RSLC vs GSLC coherence.** GSLC ~5% lower, near multiplicative (ratios 0.964 / 0.957 / 0.945).
- **Both explained by registration (alignment_test v3, 2026-09-15).** GSLC dates are registered by geometry only. Geometry leaves 0.077 lines
  and 0.24 samples (the RSLC control recovers ISCE3's rubber sheet with slope 0.978, r 0.987). The benchmark rebuilt with geometry-only
  registration matches GSLC phase (R 0.996 where γ > 0.7) and coherence (median 0.607 vs 0.605). Carrier b = −1.00 [−1.01, −1.00].
  Delivered GSLC samples are spectrally white, so they cannot be realigned post hoc. The fix is corrections inside geocoding. See docs/COMPARISON.md §3.6.
- **RSLC vs GSLC ionosphere.** Offset −0.016 TECU, residual 0.014 TECU, r 0.987 (different supports and filters).
- **Crop + full-tile ionosphere (iono_transfer_test, 2026-09-15).** The sliced full-tile screen reproduces the benchmark correction exactly (R 0.9944, offset +0.003 rad).
  The crop's own screen plus the class rule lands one joint cycle (0.235 TECU) off, at the GUNW level. A full-tile ionosphere costs ≈7–7.5 h (estimate), because
  freq-A coregistration is full-resolution whatever the looks. Untested speed-ups (processing changes, ask first): no dense offsets, or a coarser offset skip, for the ionosphere run.
- **NISAR GUNW validation (gunw_validation, 2026-09-15).** Same RSLCs, ISCE3 0.25.16. RSLC wrapped R 0.978 (γ > 0.7) / 0.946 at 80 m, no offset; GSLC keeps the
  +0.30 rad registration signature. Unwrapped: one whole-cycle constant on 96%. Ionosphere shape 0.0135 TECU (r 0.985); level: R_full = GUNW + 0.986 joint cycles.

## Decision revised (user, 2026-09-15, afternoon) — see docs/PIPELINE_DESIGN.md "Revision 2"

Crop-first RSLC (coregistration, wrapped, coherence) + GUNW ionosphere/troposphere/SET corrections + dolphin time series
(MintPy on GUNW as a check). The unwrap AOI is glof_bigger_aoi.kml. Post-event dates are coregistered for event interferograms, not the time series.

### nepal_nisar_ascending (coregistration and both time series complete)
- `L1_RSLC/`: all 7 PR RSLCs present (0620, 0702 CRC32C-verified; 0714/0726/0819 hard links to nepal_glof/L1_RSLC; 0831, 0912 from ASF)
- `L2_GUNW/`: 0620/0702, 0702/0714, 0714/0726 (link), 0726/0819, 0819/0831 PR; 0831/0912 UR
- DEM `aux/dem/dem_nepal_nisar_ascending.tif`; crop `crop/glof_bigger_aoi/20260714.h5` (module smoke test)
- The ad-hoc chain (logs/run_stack_coreg.sh, `L1_RSLC_AOI/`) was stopped and removed at the user's request; it is replaced by
  `nisar_coreg.py` (docs/COREG_MODULE.md). Case config `coreg_configs/nepal_nisar_ascending_rslc.yaml`: RSLC, crop to
  glof_bigger_aoi, all 7 dates, reference = middle date **20260726** (the ad-hoc chain used 0714), parameters from
  `coreg_configs/defaults.yaml` (validated values).
- Coreg started 2026-09-15 11:24 UTC by the user (tmux `coreg_nepal_nisar_ascending_all`), reference 20260726; watch with
  `python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml progress`.
- Next, written and tested (docs/TIMESERIES_MODULE.md): `nisar_timeseries.py` with ts_configs/nepal_nisar_ascending_pre_event.yaml
  (0620-0819, 9 pairs, MintPy LOS velocity) and ts_configs/nepal_nisar_ascending_post_event.yaml (0819-0912, 3 pairs). Dedicated
  post-event products (coherence change, per-pair displacement GeoTIFFs) not written yet.
- nepal_glof local data deleted 2026-09-15 at the user's request (see case_studies/nepal_glof/ARCHIVE_MANIFEST.md); only
  comparison*, L2_GUNW, logs, cfg, provenance remain locally. The archive is gs://s1-slc/nisar_workflow/.
- **Both time series finished 2026-09-16** (pre_event 9 pairs, post_event 3 pairs), common reference 28.32844 N 85.37839 E.
  Event report: `case_studies/nepal_nisar_ascending/report/glof_event/` (HTML + analysis.json + 14 figures),
  PDF `/home/sharath/isce3/nepal_glof_nisar_event_report.pdf`, artifact https://claude.ai/artifact/EYMfnbBfpQQj7UBmasVdjp
  **Report v2 (2026-09-16) is focused on the glacier bbox + 100% buffer** (1.66 x 2.84 km, 4.72 km2, 4569 px): pre-event LOS
  velocity -133 +/- 203 mm/yr (383/4569 reliable px, only 13 significant at 2 sigma) -> no significant motion; steady motion
  above ~68 mm/2 months excluded on rock/moraine. Glacier polygon: 9 reliable px pre (sigma 639 mm/yr), 0 across the event.
  1.02 km2 of the box changed >3 dB across the event: darkening inside the NE polygon, brightening on the ring west/below.
  Focus GeoTIFFs: report/glof_event/export_focus/ (12 files, 47x75 px, EPSG:4326).
  **Report v3 (2026-09-16): relaxed inversion.** The blocker on the ice was MintPy's connectComponent mask (snaphu puts 99% of
  glacier pixels in component 0 in every ifg -> only 9/670 inverted; the temporal-coherence threshold was never binding). With
  `mintpy: {mask_dataset: "no", min_temporal_coherence: 0.3}` in both ts configs: 405/670 glacier px, 3124/4569 box px,
  glacier velocity -27 +/- 274 mm/yr (still not significant). Four consecutive pre-event intervals: glacier minus surrounding
  ring = +19.4, -19.7, +6.1, +10.2 mm (2.9, 3.3, 1.3, 1.4 sigma vs 300 random same-size patches), alternating sign, cumulative
  +16 +/- 12 mm (p 0.16) -> changing snow/ice scattering, not creep. Both variants kept per run: mintpy/ (strict, used for the
  wide-area statistics) and mintpy_relaxed/. The mintpy section is no longer in params.json (re-derivable stage); the params
  gate now migrates when only the recorded key set changed.
  Wider-AOI findings: no pre-event motion detectable above ~40 mm/2 months; glacier zone unmeasurable by phase (coherence 0.15-0.17,
  0 reliable pixels across the event); event detected in backscatter (near/far spread ratio 3.22 vs 1.08-1.28 for two control
  pairs; 22 clusters, 1.68 km2, descending 5590 -> 2870 m over 7 km). Coherence loss is NOT event-specific here (control pair
  loses 40 km2 vs 22 km2 across the event).
- **Bug found and fixed 2026-09-16 (nisar_timeseries.py):** the flattening omitted the crop start-range difference between
  dates. 8 range samples = half a cycle at L-band, so two pairs came out sign-inverted; pairs without the stack reference had
  no RIFG check and carried silent constant errors. Fixed by adding (start_sec - start_ref) to the flattening term; all four
  checkable pairs now agree with ISCE3 to <= 0.003 rad. Also: UR GUNWs carry no troposphere cubes (corrections stage now skips
  missing layers instead of crashing); reference_lalo removed from the params lock; params gate migrates a changed key set.
- conda env `insar_ts`: dolphin 0.42.5, MintPy 1.6.4, snaphu, GDAL 3.12

## Decision (user, 2026-09-15, morning; superseded) — see docs/PIPELINE_DESIGN.md

Production pipeline: **full-tile RSLC** → coregistration → wrapped interferogram + coherence → full-tile split-spectrum ionosphere
(9×8) → export; unwrap at fine looks only inside a user AOI, with the full-tile screen sliced onto it. Crop-first is not used in
production because each crop's ionosphere level is unreferenced.

## Archive (wrapped up 2026-09-18)

`gs://s1-slc/nisar_workflow/` now holds both studies end to end: **252 GB in 994 objects**, manifest at `README.md` there
(generated by `tools/archive_to_gcs.py`, which also prunes stray objects; `tools/verify_archive.py` checks every prefix
file-by-file against the local tree — all 22 prefixes verified).
- `code/nisar_workflows/` (drivers, modules, tools, configs, docs) + `code/env/` (conda exports of isce3_env and insar_ts)
- `reports/` both reports as PDF and self-contained HTML
- `nepal_glof/` study 1 (178 GB, from the 2026-09-15 upload plus comparisons/logs/configs)
- `nepal_nisar_ascending/` study 2 (74 GB): crop, coreg stack, timeseries (both MintPy variants + flatten offsets), L2_GUNW,
  DEM, report (analysis.json, 20 figures, focus GeoTIFFs), logs, stack.json
- Inputs: all seven **PR** RSLCs are in `gs://s1-slc/nepal/nisar/ascending/tile_1/SLC/` (0831 PR and 0912 PR uploaded
  2026-09-18; that prefix also has a UR copy of 0831 which the studies did NOT use).
- Gotcha found: `gcloud storage rsync --exclude` matched nothing (16.7 GB duplicate RIFGs and 31 .pyc went up); the tool now
  prunes after sync instead of relying on it.
- Local `case_studies/nepal_nisar_ascending/` (250 GB, incl. 174 GB of RSLCs now fully in GCS) has NOT been deleted.

## Archive (2026-09-15, study 1 only)

`gs://s1-slc/nisar_workflow/` (README.md there = `case_studies/nepal_glof/ARCHIVE_MANIFEST.md`): code + docs, reports, logs/configs,
comparisons, GUNW, GSLCs, pair products, full-tile RIFG. Input RSLCs are already in `gs://s1-slc/nepal/nisar/ascending/tile_1/SLC/`
(20260620 … 20260831). Upload script `logs/run_upload_gcs.sh`, log `logs/upload_gcs.log`.

## Next: Nepal GLOF event case study (event 2026-08-26)

NISAR RSLC acquisitions over the GLOF AOI (ASF search 2026-09-15; PR = provisional, UR = urgent response):

| geometry | AOI coverage | dates | pair spanning the event | GUNW |
|---|---|---|---|---|
| ascending track 98 frame 16 | 100% | 0620, 0702, 0714, 0726, 0819, 0831 (PR+UR), 0912 (PR+UR); 0807 missing | **20260819 → 20260831** | yes (PR, UR) |
| descending track 48 frame 74 | 100% | 0629, 0711, 0723, 0816, 0828 (PR+UR), 0909 (PR+UR); 0804 missing | **20260816 → 20260828** | yes (PR, UR) |
| descending track 149 frame 74 | 23% (misses the glacier zone) | 0624, 0706, 0718, 0811, 0823, 0904 | 20260823 → 20260904 | yes |

Pre-event reference pairs: ascending 0726→0819 (24 d); D48 0723→0816 (24 d). Post-event: ascending 0831→0912; D48 0828→0909.
Choice of geometry pending with the user.

## Open items (ask the user before running anything)

1. Re-geocode the GSLC secondary with the rubber-sheet field injected via ISCE3 gslc.py az_time_correction / srange_correction (processing change; ask).
2. The cause of the uniform 0.75 m slant-range misregistration, and of the white GSLC spectrum.
2b. Which ionosphere level (R_full vs GUNW, one joint cycle apart) is physically right: needs GIM/GNSS TEC.
3. Absolute ionosphere level: class check after ISCE3 plus an external TEC reference.
4. Crop range origin to multiples of 64 for an exact side band (`rslc_subset.py --align-sideband-looks 8`, unexercised).
5. R_crop 1×1 unwrap (offered, not started).
6. Tool debt: CMP-15 (`slc_amp_overlay.py` pixel-centre), CMP-17 (hard-coded EPSG/frequencies), igram.py 512-row seam,
   nominal vs effective snaphu looks, TECU sign convention, qa.py per-date resolver (WF4-05).

## Withdrawn or refuted claims — do not re-introduce

- **"The crop-first chain's interferograms are correct because the pairs that were checked matched ISCE3"** (2026-09-16):
  wrong as stated. Only pairs sharing the stack reference had a RIFG to check against. The flattening omitted the constant
  difference in starting range between each date's crop, which is half a phase cycle for an 8-sample difference at L-band, so
  two reference pairs came out sign-inverted and every pair without the reference carried an unchecked constant. Fixed by
  adding (start_sec - start_ref) to the flattening term; all four checkable pairs then agreed with ISCE3 to <= 0.003 rad.
  The lesson is about coverage, not about the threshold: the gate was right and reached 4 of 9 pairs.
- **"The glacier zone cannot be measured because its coherence is too low"** (2026-09-18): wrong cause. Coherence is low
  (0.15-0.19), but what removed the pixels was MintPy's default `maskDataset = connectComponent`: snaphu puts 99 % of the ice
  in component 0, so only 9 of 670 pixels were inverted at all, and lowering the temporal-coherence threshold from 0.7 to 0.1
  changed nothing. With the mask off, 405 of 670 invert. The conclusion (no resolvable motion) survives; the stated reason did
  not.

- "Cropped GSLC −1.227 TECU is within the degeneracy of full −1.428": whole-scene vs AOI medians. Same pixels agree to 0.0002 TECU (v2).
- "RUNW 1×1 carries an interpolated ionosphere screen": ionosphere was disabled there; the screen is zeros.
- "R−G phase difference is a planar ramp from reference-phase handling": refuted; it is the azimuth residual × Doppler carrier.
- "R_crop ionosphere +3.6 TECU is a (−7, −8) cycle ambiguity": wrong. v1 was −1 A, −2 B cycles plus the subset-bug non-integer B
  error; v2 is exactly one B cycle.
- "Relative X_DATASET paths make gdalwarp silently empty": misdiagnosis.
- "Validated TECU constant against Track R": circular.
- "0.7% agreement between R and G ionosphere validates the port": different supports, and G's level is a prior.
- "All four GSLCs verified complete": `L2_GSLC/20260726_gslc_freqB.h5` holds 26 of 193 datasets (HH image complete).
- "Nothing depends on a live process": false whenever a run is going.
- "gdalwarp -geoloc lookup is adequate for the comparison": it skipped chunks and had a +2.5 m bias; replaced by a KD-tree.
- Doppler-carrier constant from the 1909.6 Hz PRF: wrong; use the 1520 Hz line rate.
- "Misregistration predicts < 1% coherence loss, so the GSLC gap has another cause": wrong. That estimate considered azimuth only; the uniform 0.24-sample
  range misregistration costs ~5% (|rho_rg(0.25)| = 0.948).
- "GSLC inter-date offsets are ~0 (sub-pixel cross-correlation of GSLCs)": artefact. The GSLC samples are spectrally white, and a naive estimator
  also reads RSLC azimuth delays as -0.28x (wrapped Doppler band). Use the carrier-aware estimator on band-limited data only.

## Resume

    source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env
    cd /home/sharath/isce3/asc/nisar_workflows
    cat docs/README.md
