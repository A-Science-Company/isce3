# WF4 — GSLC from the cropped granules

**Role in the comparison:** `G_crop`. Tests whether geocoding the AOI-subset granules (WF3's subsetter) reproduces
the full-tile GSLC products (WF2) over the AOI, and how both compare with the RSLC benchmark (WF1).

**Evidence convention.** As in the other workflow documents (`errors_W4_gslc_crop.json`,
`errors_supplement.json`, `verdicts.json` V4, `critic.json`). Quality results are in COMPARISON.md.

---

## 1. Purpose, scope, and when to use it

WF4 is WF2 run on the subset granules produced by `tools/rslc_subset.py` (WF3 section 4.1). GSLC geocoding is a
**per-pixel** operation — each map cell is computed from the radar samples around its own geo2rdr position — so a
GSLC made from a crop that contains those samples is, cell for cell, the same computation as the full-tile GSLC.
The v1 comparison measured exactly that: phase-difference coherence 1.0000 and per-date amplitudes identical to
0.05 % p95 over the covered area; the v2 re-measurement over the whole AOI is in COMPARISON.

**Use it** whenever the product is wanted on a map lattice over a small AOI: it has WF2's quality at a small
fraction of WF2's cost. It inherits WF2's limits against RSLC (no data-driven coregistration, lower coherence,
ionosphere level from a prior).

Case (v2): the subset granules of WF3 v2, both frequencies at 5 m, pair 20260714 × 20260726.

## 2. Inputs

- `C/L1_RSLC_AOI_v2/{date}_aoi.h5` (WF3 section 3) — zero-Doppler window, A/B aligned, provenance attributes;
- `C/aoi_v2/aux/dem/` staged by the shared ingest/DEM step;
- `configs/nepal_glof_aoi_v2.yaml` (the GSLC and igram blocks are the full-tile values; `frequencies [A, B]`).

Preconditions: WF3's section-9 geometry gates passed on the granules; the shared `C/aoi_v2/stack.json` pins a
geogrid **for both bands**, generated **once**, before either cropped leg starts (WF4-01, WF4-02).

## 3. Outputs

| artifact | path | measured (v2) |
|---|---|---|
| pinned lattice | `C/aoi_v2/stack.json` | 294000–417000 E, 3091000–3174000 N; 16600 × 24600 at 5 m (both bands) |
| GSLCs | `C/aoi_v2/L2_GSLC/{date}_gslc_freq{A,B}.h5` | 0.96 / 0.95 GB each |
| interferogram 1×1 | `C/aoi_v2/pairs/20260714_20260726/trackG/ifg_A_HH_1x1.{igram,coh,amp,nlooks}.tif` | igram 1.78 GB; coherence median 0.6303 over its extent |
| per-date amplitudes 1×1 | `…/amp_A_HH_1x1_{date}.tif` | 0.85 GB each |
| interferograms 8×8 | `…/ifg_{A,B}_HH_8x8.*` | 2075 × 3075 at 40 m |
| ionosphere | `…/trackG/ionosphere/*` (as WF2) | filtered median −1.2493 TECU over its extent; cycle offsets (0, 0); mask 31.8 % |

Products are named exactly as the corresponding WF2 products **with looks in the name** (the full tile's 1×1 names
predate that rule). They differ from WF2 by directory (`aoi_v2/`) and by the lattice pin.

The v1 G_crop products (from the native-Doppler, unaligned granules; lattice 13400 × 16800) are kept in `C/aoi/`.

## 4. Processing chain

Identical stages and settings to WF2 section 4. What differs is only the input extent and therefore the pinned
lattice and the cost:

| stage | v2 measurement (concurrent with WF3 on 8 cores) |
|---|---|
| GSLC freq A, 20260714 / 20260726 | 14m49s / 14m40s (425 geocoding blocks per date) |
| GSLC freq B, 20260714 / 20260726 | 13m19s / 9m39s |
| gridgate (both bands, both dates) | 3 s — "GRID GATE PASSED: 2 product(s) x 2 frequency(ies) are pixel-aligned and match the pin" |
| igram A 1×1 (3×3 sliding coherence) | 3m22s, ~384 MiB peak per 512-row block |
| igram A 8×8 / B 8×8 | 43 s / 47 s |
| ionosphere (`tools/gslc_ionosphere.py`, same arguments as WF2) | 7m27s: snaphu 1m59s (A, 23 components, 51.3 % labelled) and 1m49s (B, 19 components, 53.8 %), then solve and filter |
| **total** | **~65 min** |

The lattice is pinned from the subset granules' footprint polygons (snap 1000 m). Because WF2's lattice was pinned
with the same snap, the two lattices are aligned at every 5 m and 40 m cell — which makes the full-vs-crop
comparison pixel-exact without resampling (checked by `lattice_offset` in the comparison tool).

### 4.1 What the ionosphere did on the crop

- Coherence masks: A > 0.5 on 35.3 %, B > 0.5 on 39.7 %, both and unwrapped 30.7 %, 31.8 % after the 15×15 median.
- Cycle resolution chose class d = 0 and its minimal-norm member (0, 0); the full tile chose d = −2 → (−2, 0).
- Verification of the v1 crop (V4) showed why different offsets give the same answer: in every connected-component
  pair the raw full-tile unwrap was exactly +2 cycles in A and 0 in B relative to the crop, so after each run's
  offset the inputs were identical and the raw dispersive layers matched (median difference ~0). The residual
  (0.012 TECU std) comes from the 10 km Gaussian seeing different data near crop edges and holes, and shrinks with
  distance from them. The **absolute** level is still a prior (WF2 4.2).

## 5. How to run it

After WF3's subset, ingest and DEM steps (run once):

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env
cd /home/sharath/isce3/asc/nisar_workflows
CFG=configs/nepal_glof_aoi_v2.yaml
C=/home/sharath/isce3/case_studies/nepal_glof
for F in A B; do python run_track_g.py -c $CFG --only gslc --frequencies $F; done
python run_track_g.py -c $CFG --only gridgate --frequencies A B
python run_track_g.py -c $CFG --only igram --frequencies A B --igram-freq A --looks 1 1 --dates 20260714 20260726
python run_track_g.py -c $CFG --only igram --frequencies A B --igram-freq A --looks 8 8 --dates 20260714 20260726
python run_track_g.py -c $CFG --only igram --frequencies A B --igram-freq B --looks 8 8 --dates 20260714 20260726
python -u tools/gslc_ionosphere.py --pair-dir $C/aoi_v2/pairs/20260714_20260726/trackG \
   --freq-a-prefix ifg_A_HH_8x8 --freq-b-prefix ifg_B_HH_8x8 --nlooks 64 --coherence-threshold 0.5 \
   --median-filter-size 15 --sigma-km 10 --ntiles 4 4 --nproc 4 --cycle-search 3
```

The v2 run chained these in `C/logs/run_aoi_v2.sh` with `EXIT=` checks after every stage, in parallel with WF3 and
after one shared ingest. The v1 run was not chained: after the grid gate failed nothing noticed, and the leg sat
idle for 68 min until the user asked (OPERATIONS_AND_LESSONS.md).

## 6. Parameter reference

All science parameters are WF2's (WF2 section 6) so that G_full and G_crop are computed identically. The only
deliberate difference from WF2's own history is that the cropped ionosphere used the fixed uncertainty code
(4.1 does not depend on it).

## 7. Required patches and upstream issues

None beyond WF2. The GDAL ERROR 5 before `referenceTerrainHeight` recurs in every cropped GSLC and that layer is
all NaN here too (WF2-10); the crosstalk warnings that appear ~3 s earlier are unrelated (critic.json).

## 8. Resource model

| item | v2 |
|---|---|
| GSLCs | ~0.95 GB per date per band (vs ~10 GB full tile) |
| geocoding time | ~52 min for four products while sharing the CPU with WF3 (vs ~4.5 h for the full tile) |
| 1×1 interferogram | 3m22s (vs 26m49s) |
| ionosphere | 7.5 min (vs ~72 min) |
| disk for the leg | 3.5 GB GSLCs + 5.4 GB pair products |

## 9. Validation gates for automation

| gate | pass | v2 |
|---|---|---|
| input granules passed WF3's geometry gates | yes | yes |
| one shared `stack.json`, both bands pinned, generated before any leg starts | yes | yes |
| lattice aligned with the full-tile lattice | integer offsets at 5 m and 40 m | yes (comparison v2) |
| grid gate per (date, band) | passed | passed |
| GSLC inventory complete (dataset counts equal to a reference GSLC) | yes | all four: 193 datasets with `identification`, equal to a complete full-tile GSLC (checked 2026-09-15) |
| interferogram and coherence sane | as WF2 | coherence median 0.6303 vs floor 0.2954 |
| ionosphere layers finite where defined | yes | yes (uncertainty fix applied) |
| cycle table logged | yes | class d = 0 → (0, 0) |

## 10. Known limitations and open questions

- Everything in WF2 section 10 applies.
- **OPEN** — the minimal-norm prior happened to land full and crop on the same point of the joint-cycle line here
  because freq B agreed to 0 cycles; had snaphu put B one cycle apart, the two could differ by 0.235 TECU or more
  (V4). An external TEC reference or an explicit consistency rule across overlapping runs is needed for automation.
- The cropped leg does not need WF3's large ionosphere buffer for its own geocoding, but it inherits the lattice the
  buffered granules define.

## 11. Problems and errors log

<!-- ERROR-LOG:BEGIN -->
9 entries: 0 caught by the user, 7 were the assistant's own mistakes of judgement, 3 still open. Every entry cites its evidence; the full records are in `case_studies/nepal_glof/comparison/verification/`.

| id | problem | category | caught by | assistant error | status | cost |
|---|---|---|---|---|---|---|
| WF4-01 | stack.json was regenerated while the R_crop chain was running | geometry | assistant | yes | worked around | None, verified. |
| WF4-02 | AOI config copied with frequencies [A], so the shared stack.json had no freq B geogrid pin | science | guardrail/tool check | yes | worked around | About 1 minute. There was a risk of disturbing a live Track R run through the shared state file, but it did not happen. |
| WF4-03 | freqAB.h5 resolver bug the assistant saw at 03:17Z and deliberately left in gridgate.py | resources | guardrail/tool check | yes | fixed | A run failure after about 15 min of GSLC compute. The assistant credited this bug with the ~70 min idle, but see the next entry: the idle would have happened an |
| WF4-04 | First resolver fix was incomplete: gridgate read frequency B from the freq A file | judgement | guardrail/tool check | yes | fixed | About 30 s and one extra failed run (time_summary.txt: two step4:gridgate:FAILED rows). |
| WF4-05 | Resolver cleanup left latent bugs: qa.py same per-date bug, silent cross-band fallback, gslc.py still keyed by freq_tag | software | assistant | yes | open | Unknown. Risk of silently missing QA for band B, misleading errors, and a needless re-geocode (~15 min on the crop, hours on the full tile). |
| WF4-06 | Independent per-band snaphu unwraps: crop and full tile got different integer cycles and resolver classes (d=0 vs d=-2) | geometry | assistant | no | by design | None in compute. It is a latent scientific risk: absolute TEC can differ between runs by 0.235 TECU per joint cycle, or by 5.59 TECU per band-A cycle if a class |
| WF4-07 | Assistant compared a whole-scene TEC median with an AOI median and called the -1.227 vs -1.428 TECU gap 'within the degeneracy' | science | assistant | yes | open | The user was given a wrong scientific explanation for ~3 h, and a stale claim sits in the resume document that other agents read. |
| WF4-08 | Untriaged non-fatal ISCE3/nisar/GDAL errors and warnings in every AOI GSLC run | tooling | assistant | no | open | Unknown. Possibly incorrect crosstalk calibration metadata in the GSLCs. Log noise hides real warnings such as the overflow above. |
| WF4-09 | dispersive_sigma_filtered.tif was 100% +inf | science | guardrail/tool check | yes | fixed | Unusable uncertainty layers in G_full and G_crop v1. |

Cross-cutting operational problems (process supervision, reboots, logs, disk, agent harness) are in OPERATIONS_AND_LESSONS.md.

#### WF4-01 — stack.json was regenerated while the R_crop chain was running

- **Symptom:** While R_crop was in rdr2geo (block 9/15), the assistant ran `run_track_g.py --only ingest --frequencies A B --force` on the same aoi/stack.json so that the Track G crop could pin freq B.
- **Root cause:** Track R and Track G share one stack.json per out_root, and the assistant mutated it under a live run on the reasoning that it was 'metadata-only'.
- **Fix:** Backed up the old stack.json first and verified that top_left, bottom_right and the freq A per_frequency block were byte-identical. The running chain had already rendered its runconfig.
- **Cost:** None, verified.
- **Caught by:** assistant · assistant error · **Status:** worked around
- **Evidence:** L4238: 'let me regenerate the stack -- metadata-only, so it won't disturb the running chain.' L4240: 'top_left same: True ... freq A same: True'.
- **Automation lesson:** Treat shared manifests as immutable while any stage that reads them is running. Version them (stack.v2.json) or lock them, and give each leg a snapshot.

#### WF4-02 — AOI config copied with frequencies [A], so the shared stack.json had no freq B geogrid pin

- **Symptom:** Dry run at 08:26:41Z: "ERROR: step 3 'gslc' FAILED ... stack.json has no pinned geogrid for frequency B (has ['A']). The config's `frequencies` changed since ingest ran; regenerate with: --only ingest --force"
- **Root cause:** The assistant built configs/nepal_glof_aoi.yaml at ~08:1xZ by string-replacing values in nepal_glof.yaml (JSONL L~16951 of the flattened transcript). It left `frequencies: [A]` (nepal_glof_aoi.yaml:71) even though the planned cropped-GSLC ionosphere needs both bands. Track R ingest (08:16-08:18Z) then pinned only A. The copied comments are stale too: lines 67-70 still say "FREQUENCY B FIRST" and "freq A at 5 m posting is a ~29 GB/pol output", which are full-tile facts. The guard at nisar_wf/ingest.py:655-661 caught the mismatch.
- **Fix:** Backed up stack.json to the /tmp scratchpad as stack_A_only.json. Ran `run_track_g.py --config configs/nepal_glof_aoi.yaml --only ingest --frequencies A B --force` (track_g_20260914T082701Z.log). Checked that the freq A geogrid, top_left and bottom_right were unchanged, and B was added on the same 13400x16800 5 m lattice. The config file was never corrected: every later W4 command passes --frequencies explicitly. The regeneration overwrote the shared stack.json in place while the Track R AOI insar chain (tmux aoiins) was still running and reading from the same file. The assistant said it was "metadata-only, so it won't disturb the running chain" without checking. Only the geogrid block was compared, not the whole file. The backup was lost in the 12:52Z reboot.
- **Cost:** About 1 minute. There was a risk of disturbing a live Track R run through the shared state file, but it did not happen.
- **Caught by:** guardrail/tool check · assistant error · **Status:** worked around
- **Evidence:** L4230 (08:26:41Z) dry-run ERROR text above; L4239-L4240: "top_left same: True ... freq A same: True  A: 13400 x 16800 @ 5.0x5.0 m  B: 13400 x 16800 @ 5.0x5.0 m"; L4238: "metadata-only, so it won't disturb the running chain"; nepal_glof_aoi.yaml:71 `frequencies: [A]`; run_track_r.py:80 "write stack.json (shared with Track G)"; L4484 after reboot: scratchpad "total 0".
- **Automation lesson:** Derive the frequency set from what the downstream products need (split-spectrum ionosphere needs A and B) and check it when the config is created, not at stage 3. Never copy a config by string substitution. Generate it from a schema and fail on stale or contradictory comments and values. Version shared state files (stack.json) with the frequency set in the filename or a content hash. Do not overwrite them in place while another track has a run open against the same out_root, and keep backups inside the case tree, not /tmp.

#### WF4-03 — freqAB.h5 resolver bug the assistant saw at 03:17Z and deliberately left in gridgate.py

- **Symptom:** aoi_gslc.log 09:11:31Z: "ERROR: step 4 'gridgate' FAILED ... cannot run the grid gate; GSLC product(s) missing: .../aoi/L2_GSLC/20260714_gslc_freqAB.h5 .../20260726_gslc_freqAB.h5". This happened after all four GSLCs had been built with EXIT=0.
- **Root cause:** Config.gslc_output(date, cfg.freq_tag) names files by the joined frequency SET. The GSLC wrapper ran each band separately (`for F in A B; ... --frequencies $F`), so only *_freqA.h5 and *_freqB.h5 exist. Gridgate was then called with `--frequencies A B` and looked for freqAB. During the full-tile Track G work at 03:17Z, the assistant's grep showed this exact call pattern at `nisar_wf/gridgate.py:145` and `nisar_wf/qa.py:440` (L3505), but it patched only igram.py. It later admitted it had noted "same issue exists in gridgate.py and qa.py, but for now fix igram.py". Two more factors hid the bug: (a) the 08:26 dry run validated `--only gslc --frequencies A B` as ONE combined run, which would write freqAB.h5 and pass gridgate, but the executed wrapper ran per band, so the path that was validated was not the path that ran; (b) gridgate's --dry-run branch treats missing products as "stage G1 would create them" (gridgate.py:165-168), so no dry run could catch a naming mismatch. Running bands separately was not needed on the crop (1.7 GiB per band) and was carried over from the memory-bounded full-tile run.
- **Fix:** At 10:20:26Z added one resolver, Config.resolve_gslc(date, freq) (nisar_wf/config.py ~996-1027), and routed gridgate, qa, overlay and igram through it instead of four separate lookups.
- **Cost:** A run failure after about 15 min of GSLC compute. The assistant credited this bug with the ~70 min idle, but see the next entry: the idle would have happened anyway.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** aoi_gslc.log:9469-9474; L4312 (10:20:08Z): "Same `freqAB` bug I fixed in `igram.py` but explicitly deferred for `gridgate.py` -- that was a mistake"; L4341: "explicitly noted 'same issue exists in `gridgate.py` and `qa.py`, but for now fix `igram.py`'. That was the wrong call"; L3505 (03:17:24Z) grep output listing nisar_wf/gridgate.py:145 and qa.py:440; L4229 dry run `--only gslc --frequencies A B --dry-run` vs L4267 wrapper `--frequencies $F`.
- **Automation lesson:** When a bug is found at one call site, grep every call site and fix them all, or leave a failing test. Never defer a fix knowingly. Output identity must be one function used by both writer and readers, keyed per (date, band). Dry runs must exercise the same invocation plan the real run will use, and a dry run must check file NAMING against the plan, not excuse missing files as 'would be created'.

#### WF4-04 — First resolver fix was incomplete: gridgate read frequency B from the freq A file

- **Symptom:** 10:20:40Z re-run: "ERROR: step 4 'gridgate' FAILED after 0.0s ... 20260714_gslc_freqA.h5 has no /science/LSAR/GSLC/grids/frequencyB"
- **Root cause:** The 10:20:26Z patch set `products = {d: cfg.resolve_gslc(d, cfg.frequencies[0]) for d in dates}`, one file per DATE resolved on band A. The assistant did not read gridgate's body, where `for freq in cfg.frequencies:` then calls gslc_grid_info(path, freq) on that same path. With bands geocoded separately, band B lives in a different file.
- **Fix:** At 10:20:58Z gridgate.py was rewritten to resolve per (date, frequency): `products_bf = {(d, f): cfg.resolve_gslc(d, f) ...}` (gridgate.py:153), with `missing` computed over all (date, band) files (:157) and the per-freq loop reading `products_bf[(date, freq)]` (:193-194). At 10:21:03Z: "GRID GATE PASSED: 2 product(s) x 2 frequency(ies) are pixel-aligned and match the pin" (both bands 13400x16800, EPSG 32645, origin 313002.5/3165997.5 pixel-centre).
- **Cost:** About 30 s and one extra failed run (time_summary.txt: two step4:gridgate:FAILED rows).
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** L4317-L4318 first patch (`cfg.resolve_gslc(d, cfg.frequencies[0])`); L4322 error text; L4329: "checks for frequency B inside the freq A file. The gate needs to resolve per-frequency"; L4333-L4334 fix and PASS; C/aoi/time_summary.txt rows 2026-09-14T09:11:31 and 10:20:40 `step4:gridgate:FAILED`.
- **Automation lesson:** Read the whole consumer before patching its input lookup. Test the gate against both layouts, a combined freqAB file and split per-band files. Product lookups must take the band as a required argument, never a default of frequencies[0].

#### WF4-05 — Resolver cleanup left latent bugs: qa.py same per-date bug, silent cross-band fallback, gslc.py still keyed by freq_tag

- **Symptom:** Not triggered in W4 (the qa, watermask, unwrap and overlay stages were never run on the AOI; C/aoi/qa/ is empty). Found in this post-hoc audit.
- **Root cause:** (1) nisar_wf/qa.py:440 still does `path = cfg.resolve_gslc(date, cfg.frequencies[0])` and then loops `for freq in cfg.frequencies` reading every band from that one file. The KeyError is caught at qa.py:454 and only logged as a warning, so freq B quicklooks would be silently skipped with split per-band files. (2) Config.resolve_gslc (config.py:1023-1026) falls back to ANY existing band's file when the requested band's file is missing. Asking for B when only freqA.h5 exists returns freqA.h5, and the real 'freqB.h5 missing' becomes a confusing 'no frequencyB' error. (3) gslc.py:125 and :414 still name output and check completeness via gslc_output(date, cfg.freq_tag). A combined `--frequencies A B` run on a case with per-band files would not see them as complete and would re-geocode both bands into a new freqAB.h5.
- **Fix:** None applied. **Update:** gridgate now resolves per (date, frequency); qa.py still resolves one band per date.
- **Cost:** Unknown. Risk of silently missing QA for band B, misleading errors, and a needless re-geocode (~15 min on the crop, hours on the full tile).
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** nisar_wf/qa.py:440 `path = cfg.resolve_gslc(date, cfg.frequencies[0])` and :443-455 loop with `except (KeyError, OSError, ValueError) ... log.warn`; nisar_wf/config.py:1023-1027 fallback loop; nisar_wf/gslc.py:125 and :414 `cfg.gslc_output(date, cfg.freq_tag)`.
- **Automation lesson:** One product registry keyed by (date, band, pol) for writer, skip-check and every reader, with no fallback to a different band. A missing input must raise with the exact expected filename. Non-fatal warnings in QA stages must still count as stage failures in the summary.

#### WF4-06 — Independent per-band snaphu unwraps: crop and full tile got different integer cycles and resolver classes (d=0 vs d=-2)

- **Symptom:** Crop: "CHOSEN m=+0 (A), n=+0 (B) (minimal-norm representative of d=+0)". Full tile (trackG_ionosphere_v3.log:24): "CHOSEN m=-2 (A), n=+0 (B) (minimal-norm representative of d=-2)". ionosphere.json: full "cycles A=-2 B=0", crop "cycles A=0 B=0".
- **Root cause:** snaphu's absolute integer reference is arbitrary per run and extent. The crop was also unwrapped with different tiling (ntiles [2,2], 6 and 5 connected components, 47.7% and 60.4% labelled) than the full tile (ntiles [4,4], 20 and 18 components). The V4 verifier found the raw band A unwraps differ by exactly +2 cycles (full minus crop) in every component pair, and band B by 0, so the resolver correctly compensated. However, agreement in absolute level along the joint-cycle line (0.235 TECU per joint cycle) held only because band B happened to agree and the minimal-norm tie-break picked (-2,0) over (-1,1) or (0,2). The tool also applies one global (m, n) per band, although snaphu's integer ambiguity is per connected component.
- **Fix:** No change needed for this pair. Over identical pixels, full and crop GSLC ionosphere agree to +0.0063 TECU (residual std 0.0123 TECU, r=0.9875; comparison.json I3_ionosphere/G_full_vs_G_crop_40m). The absolute level remains a prior, not a measurement (the log prints "ABSOLUTE TEC IS THEREFORE UNCERTAIN BY ~0.24 TECU per cycle").
- **Cost:** None in compute. It is a latent scientific risk: absolute TEC can differ between runs by 0.235 TECU per joint cycle, or by 5.59 TECU per band-A cycle if a class is mis-resolved.
- **Caught by:** assistant · **Status:** by design
- **Evidence:** aoi_igram.log [3/4] block: "searched 49 candidates ... DEGENERACY: adding 2*pi to BOTH bands moves the answer by only +0.235 TECU ... CHOSEN m=+0 (A), n=+0 (B)"; aoi_igram.log: "ntiles=[2, 2] ... -> 6 connected components, 47.7% of grid labelled"; trackG_ionosphere.log:79-82 "ntiles=[4, 4] ... 20 connected components"; L4373: "GSLC full tile: -1.4284 TECU ... cycles A=-2 B=0 / GSLC cropped: -1.2270 TECU ... cycles A=0 B=0"; L4807 V4 verdict: "full minus crop is exactly +2 cycles in band A and 0 cycles in band B ... agree on the absolute level ... only because band B happened to agree ... and because the minimal-norm tie-break chose (-2,0)".
- **Automation lesson:** Treat per-run cycle offsets as run-specific. Never compare absolute TEC across runs, or across full vs crop, without a shared reference (isce3.atmosphere.tec_product or an independent measurement). Keep unwrap tiling identical across legs that will be compared, or record it as a scientific parameter. Resolve integer offsets per connected component, not one global value per band.

#### WF4-07 — Assistant compared a whole-scene TEC median with an AOI median and called the -1.227 vs -1.428 TECU gap 'within the degeneracy'

- **Symptom:** At 10:33Z and 10:36Z the assistant told the user: "The cropped value sits 0.20 TECU away, which is inside the ±0.235 TECU-per-cycle degeneracy I flagged earlier; it resolved to class d=0 where the full tile resolved to d=−2. So the discrepancy is the known absolute-phase ambiguity, not a processing difference -- and it's a fair check that the uncertainty I quoted was honestly sized". It wrote the same claim into STATE.md.
- **Root cause:** This was the assistant's judgement error. -1.4284 TECU is the full-tile median over its whole valid area. -1.2270 is the crop median over the crop grid. They cover different ground. At 08:58Z the assistant had itself written "I'm not reporting the cropped ionosphere number against the full-tile one yet -- the full-tile median is over the whole scene and the cropped one over the AOI, so they aren't comparable", then did exactly that 95 min later. In the same 10:36 message it correctly warned that the GSLC full vs crop coherence medians (0.6152 vs 0.6018) were over different masks, but did not apply that to the TEC numbers. The explanation was also internally inconsistent. A class difference d=0 vs d=-2 means two band-A cycles (5.59 TECU each), not the 0.235 TECU joint-cycle degeneracy, which moves along constant d. Per V4, the change of region alone accounts for 0.178 of the 0.201 TECU gap: the full tile gives -1.2500 over the crop window, and filtering and mask account for the other 0.023.
- **Fix:** Withdrawn at 13:51Z after compare_four_way.py I3 compared identical pixels: "Correction. ... That compared a whole-scene median against an AOI median -- different ground. Over identical pixels, full and cropped GSLC ionosphere agree to 0.006 TECU ... my earlier explanation was wrong." STATE.md:45 still carries the withdrawn claim. At 13:56Z the assistant deliberately deferred fixing it, and it is still stale on disk.
- **Cost:** The user was given a wrong scientific explanation for ~3 h, and a stale claim sits in the resume document that other agents read.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** L4289 (08:58Z) "they aren't comparable until I restrict the full-tile screen to the same pixels"; L4376 (10:33Z) "within the ±0.235 TECU/cycle degeneracy I flagged, and it landed in class d=0 rather than d=−2"; L4389 (10:36Z) "a fair check that the uncertainty I quoted was honestly sized rather than optimistic"; STATE.md:45 "GSLC cropped -1.2270 TECU <- within the +/-0.235 TECU/cycle degeneracy"; L4684 (13:51Z) correction; L4702 "still contains the withdrawn ionosphere explanation"; L4807 V4 "change of region accounts for 0.178 of the 0.201 TECU gap".
- **Automation lesson:** Cross-run statistics must be computed only over an explicit common pixel set (the same lattice window AND the intersection of valid masks), and the comparison function should refuse unpaired medians. Any claim written to a state or report file should come from a stored comparison result, not a hand-quoted number. When a claim is withdrawn, fix every file that carries it in the same step.

#### WF4-08 — Untriaged non-fatal ISCE3/nisar/GDAL errors and warnings in every AOI GSLC run

- **Symptom:** Once per GSLC (4x in aoi_gslc.log, lines 2217, 4545, 6939, 9287): "ERROR 5: tmp*.vrt, band 1: Access window out of range in RasterIO(). Requested (18,136) of size 44x25 on raster of 265x1." It follows "BaseL2WriterSingleInput.py:2037: UserWarning: Geolocating one dimensional dataset: /science/LSAR/RSLC/metadata/calibrationInformation/crosstalk/txHorizontalCrosspol ... (rg. vector)". The logs also show: "<unknown>:1: SyntaxWarning: invalid escape sequence '\d'"; "WARNING existing metadata entry description for .../GSLC/grids/frequencyA/mask 'GSLC mask' does not match product specification description"; "processing type in the runconfig is set to 'None' ... Defaulting to 'Custom'"; and on every Track G invocation the Track R config warnings "unwrap.ntiles [10, 17] DIVERGES from the course" and "track_r.product_type 'RUNW' runs phase unwrapping".
- **Root cause:** The nisar L2 writer geocodes 1-D calibration vectors (crosstalk, length 265) through a 2-D GDAL VRT path, and GDAL reports an out-of-range read window. The run still exits 0. The same ERROR 5 appears in the full-tile GSLC logs (trackG_gslc.log x2, trackG_gslcB.log x1, track_g_20260908T093017Z.log x2), so this is generic ISCE3 0.25.12 behaviour, not crop-specific. It is unconfirmed whether the resulting GSLC calibrationInformation/crosstalk metadata is correct. The config-validation warnings are shared config sections printed regardless of which track runs.
- **Fix:** None. Never examined in the session (the transcript has no discussion of 'Access window').
- **Cost:** Unknown. Possibly incorrect crosstalk calibration metadata in the GSLCs. Log noise hides real warnings such as the overflow above.
- **Caught by:** assistant · **Status:** open
- **Evidence:** C/aoi/logs/aoi_gslc.log:2217-2218, 1717-1723, 2347, 4545, 4675, 6939, 9287; aoi_gslc.log:17-18 config warnings; grep -c 'Access window out of range': aoi_gslc.log 4, logs/trackG_gslc.log 2, logs/trackG_gslcB.log 1.
- **Automation lesson:** Keep an allowlist of known benign upstream messages, each with a written justification, and fail or flag on anything outside it. Validate the metadata datasets the warning names against the input RSLC. Only print config warnings for the track being run.

#### WF4-09 — dispersive_sigma_filtered.tif was 100% +inf

- **Symptom:** RuntimeWarning: overflow encountered in cast; the filtered uncertainty layer was +inf everywhere.
- **Root cause:** The 15x15 median-conditioned mask re-admits pixels with coherence 0 or below threshold, where sigma is infinite; one infinity poisons the 250 px Gaussian.
- **Fix:** tools/gslc_ionosphere.py now defines sigma only where both coherences exceed the threshold (NaN elsewhere). Applied in the v2 G_crop run; G_full's uncertainty layer predates the fix.
- **Cost:** Unusable uncertainty layers in G_full and G_crop v1.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** errors_W4 entry; tools/gslc_ionosphere.py sig_ok
- **Automation lesson:** Validate every output layer for finiteness and range before declaring success.

<!-- ERROR-LOG:END -->

## 12. Automation contract

**Preconditions.** Subset granules keyed and gated (WF3 section 12); one ingest per crop key, completed before any
leg reads it.

**Postconditions.** Grid gate passed for every (date, band); lattice alignment with any full-tile product it will be
compared to is verified by integer offsets; ionosphere run records its cycle table.

**Identity and caching.** As WF2, with the subset key in every product key.

**Failure handling.** Chain every stage in one supervised wrapper with `EXIT=` checks; a failed gate stops the leg
and raises an alert rather than leaving it idle.

**Monitoring.** As WF2, plus an idle detector: no stage start within N minutes of the previous stage's `EXIT=`.
