# Answers to `vm_handover_questions.md`

Answered 2026-09-18 on the processing VM (`/home/sharath/isce3`), from disk. Evidence is quoted as `file:line` or command
output. Where the machine does not record something, the answer is **not recorded** — reconstructing it is exactly how this
project's withdrawn-claims list got its entries.

`ORCHESTRATOR_PROBES.md` and `LEARNINGS.md` are **not on this VM** (`asc/docs/` holds only `nisar-environment-setup.html`), so
question 9.6 cannot be answered here and the Part 3 table is verified against the code rather than against your text.

---

## Part 0 — uploads (done)

**0.1 The three AOI files** are now at `gs://s1-slc/nisar_workflow/code/aoi/`. Geometry, measured from the files:

| file | vertices | bbox (W S E N) | extent | area |
|---|---|---|---|---|
| `glof_exact_aoi.kml` | 5 | 85.24840 28.16248 85.77993 28.45562 | 52.11 × 32.41 km | 1637.43 km² |
| `glof_bigger_aoi.kml` | 5 | identical to the above | identical | identical |
| `nepal_glacier_zone.kml` | 15 | 85.52122 28.27849 85.52970 28.29133 | 0.83 × 1.42 km | 0.61 km² |

**`glof_exact_aoi.kml` and `glof_bigger_aoi.kml` are the same polygon** — identical coordinate lists, verified
string-for-string; only the KML wrapper differs (2677 vs 2497 bytes). Any document that contrasts an "exact" with a "bigger"
AOI is describing one geometry under two names. The repo-root copy `isce3/glof_bigger_aoi.kml` is also identical.

The areas quoted in the docs are slightly off: **1637.43 km²**, not 1642, and **0.61 km²**, not 0.62.

**Is there a fourth?** No file. The glacier bbox + 100 % buffer is computed in code, at `nisar_timeseries.py` in `Ctx.__init__`
(`tools/glof_event_analysis.py` does the same): take the glacier polygon's bounding box, double it about its centre. That
yields 1.66 × 2.84 km, 4.72 km², 4569 pixels at 40 m posting.

**0.2 `nisar_downloader.py`** is **ours** (47 KB, written for this project; its docstring describes it as the NISAR adaptation
of the Sentinel-1 SLC pipeline). It downloads from ASF and stages straight to GCS one granule at a time. It is **not**
superseded: `tools/nisar_fetch.py` is the local-download counterpart of the same ASF query (no GCS staging, adds `--tier
PR|UR`). Both are still used — the downloader for bulk staging, the fetch tool for pulling single granules onto the VM. Now
uploaded to `code/nisar_downloader.py`.

**0.3 The report-PDF environment** is at `code/env/report_pdf_requirements.txt`: playwright 1.62.0, pillow 12.3.0, pypdf
6.18.1, greenlet 3.5.6, pyee 13.0.1. Browser: chromium build **1234** (headless shell), installed with `python -m playwright
install chromium`. The 16 Debian 13 packages are listed in that file, taken from `/var/log/apt/history.log`.

**0.4 Everything the code reads outside `/home/sharath/isce3/`** (from `grep -rhoE "/home/sharath/[^ ]+"` over the tree):
the three KMLs, `nisar_downloader/nisar_downloader.py`, the two conda interpreters
(`miniforge3/envs/{isce3_env,insar_ts}/bin/python`), `~/.venvs/report-pdf/bin/python`, and one **stale** path,
`/home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc`, left over from the earlier Sentinel-1 work and not
reachable from any NISAR code path.

---

## Part 1 — the container

**1.1 Not verified, and the question contains a false premise about what the VM carries.** `tools/patches/` holds **five**
patches, not four: `resample_slc_v2.py`, `insar_mask_vectorized.py`, `h5_prep.py`, `unwrap.py`, `insar_utils.py`. All five
report "already applied" against the installed isce3 today.

Whether the image can run the chain I **cannot** establish from here — that needs pulling the image and running
`apply_patches.py --check` inside it. What I can say is that a green Docker build does **not** prove the patches are in it,
because of 1.3: `--check` exits 0 when a patch is missing. So treat the image as unverified until someone runs the check
inside it. The failure modes it would hit if the patches are absent are documented (the mask loop in WF3's log, the unwrap
`KeyError` in WF1's), but I have not reproduced either inside the container.

**1.2 Not reported upstream.** The patch entries themselves carry the intent as a note — `apply_patches.py` prints
"none -- ours; report upstream" for the three. No record of a reason for not doing it, and no evidence that any encodes a
local assumption; they were written to unblock runs and never revisited.

**1.3 Confirmed — your probe is right.** `tools/apply_patches.py:189-192`: under `--check`, a patch that is not applied prints
"NOT applied (run without --check to apply)" and `continue`s **without touching `rc`**. `rc` becomes 1 only in the REFUSING
branch (line 186), when the installed isce3 lacks a symbol the patch needs. So `--check` exits 0 both when everything is
applied and when nothing is.

---

## Part 2 — the numbers

**2.1 Settled, and both figures are real: the granules differ in size between dates.** Read from the seven granules:

| date | freq A | freq B |
|---|---|---|
| 20260620 | 54720 × 54247 | 54720 × 6781 |
| 20260702 | 54720 × 54239 | 54720 × 6780 |
| 20260714 | **53200 × 54244** | 53200 × 6781 |
| 20260726 | **54720 × 54239** | 54720 × 6780 |
| 20260819 | 54720 × 54239 | 54720 × 6780 |
| 20260831 | 53200 × 54238 | 53200 × 6780 |
| 20260912 | 53200 × 54238 | 53200 × 6780 |

There is no single "frame dimension". 53200 × 54244 is the **20260714** granule (the one the four-workflow study used as
reference, which is why it is in the comments), 54720 × 54239 is 20260726 and others. `nepal_glof/stack.json` records both.
The disk gate computing from the granule is therefore the correct design, not a missing hardcode. The "54244 samples" in
`config.py:695` is that one granule's range width, used to justify a block-budget heuristic.

**2.2 One run, two labels — and the labels are both defensible, so do not double-count.** `docs/PIPELINE_DESIGN.md:79-80`
budgets the **whole pair at 9×8 export** at ≈ 7 h 38 m – 8 h 05 m. `docs/WF1_RSLC_FULL_TILE.md:841` records the same number as
the correction of an earlier **ionosphere-only** estimate. Both are the same total because coregistration dominates and the
ionosphere needs it anyway: an ionosphere-only job and a full 9×8 job share everything except crossmul at export looks.

**2.3 From 0.807.** `comparison_v2/gunw_validation/gunw_validation.json`: under `wrapped/R_full__vs__GUNW_20m`,
`phase_diff_coherence = 0.8065` and `sign_check_vs_conjugate_R = 0.2171` — the same support, the whole 20 m overlap. The
0.978 figure is `by_R_full_coherence/0.7-1.0`, a different subset, and is not the conjugate test's baseline.

**2.5 Both are true of different modules, and your resolution is right for the one that strides.** `nisar_wf/qa.py:1-22` (Track
G QA) forbids strided reads and uses block-wise multilook, for exactly the reason your probe states. `nisar_wf/trackr.py:767`
(Track R QA) does stride: `step = max(1, int(np.sqrt(d.size / 4e6)))`. The difference that reconciles them is **what** is
read: Track R strides the **coherence layer of its own RIFG product** for summary statistics (median, percentiles), not a
24.5 GB granule, and the comment above it says "a stride, not a read-then-subsample". So: deliberate, cheap enough on a
product, and not a pattern to copy for imagery. `igram.py:342` uses the same idiom on its own output.

**2.6 The rule is narrower than either source.** A dry run never writes (`params_gate` skips its write; the drivers log "would
write"/"would run"). Hard preconditions on **inputs** still fail in a dry run — `nisar_timeseries.py` returns `EXIT_PREREQ`
for an unfinished coregistration, and the coreg module does the same for missing crops. What is degraded is only the artifact
that an **earlier dry-run step would have created**: `trackr.py:646-654` tolerates the missing runconfig, with the comment
"that would make `--dry-run` over the whole pipeline impossible". So: inputs are checked, intermediate products the dry run
itself skipped are not.

**2.7 Not recorded on this VM.** The only ENL discussion in the archived docs is `WF2_GSLC_FULL_TILE.md:494` (compute ENL from
bandwidth, posting and window; never assume looks equal box size). No 54-vs-53 figures and no corrected derivation exist in
these files.

**2.8 GiB.** `OPERATIONS_AND_LESSONS.md:529` is the measurement — `du` on the pair scratch, itemised, totalling 359 GiB. The
"359 GB" at line 800 is the same measurement stated loosely in narrative.

---

## Part 3 — shipped versus specified

Your table is right except one row, and the correction is about evidence rather than conclusion:

- **"environment + overlay hash gate — NOT implemented — no `hashlib`/`sha256` in the tree"**: the gate is indeed not
  implemented, but `hashlib` *is* used — `nisar_coreg.py` takes a SHA-1 of the AOI KML into `params.json`, and
  `tools/compare_four_way.py` uses it too. `sha256` appears nowhere. Correct the evidence, keep the verdict.

Everything else I verified as you have it: no `boot_id`, no `MemAvailable`, disk billing is per-unit at 116.4 B/px only,
provenance is per stage and overwritten, no out-of-session monitoring; `os.replace` in `dem.py`, `util.py`, `nisar_coreg.py`,
`nisar_timeseries.py`; `params.json` freeze-and-refuse, stage env re-exec, the 0/1/2/3 exit contract and the RSLC-mode and
integer-decimation refusals all implemented.

**3.2 It is key-set-only in one direction, and it does have a hole.** The comparison is
`diffs = [k for k in sorted(cur) if old.get(k, cur[k]) != cur[k]]`:

- a **changed value** of a key the current schema locks → refused, as intended;
- a key **removed** from the schema → no longer checked, silently (that is the migration you saw, for `reference_lalo` and
  `mintpy`);
- a key **added** to the schema → `old.get(k, cur[k])` defaults to the current value, so an older `params.json` that predates
  the key **passes**. If someone adds a parameter to the lock, existing products will not be re-checked against it.

That third case is a real hole and is not documented anywhere else. It fired wrongly twice before I narrowed it (both times
refusing a legitimate re-run because the recorded key set had changed), which is why the migration exists at all.

**3.3 The tmux session name is the only exclusion, and it is per case and stage.** `coreg_<case>_<stage>` and
`ts_<case>_<name>_<stage>`. Two different case configs that resolve to the same `stack_id` would write to the same directory
with nothing stopping them: no lock file, no PID file, no atomic claim on the stack. What limits the damage is downstream —
unit manifests, `params.json`, and the refusal to overwrite finished outputs — so a collision is detectable afterwards rather
than prevented. For the orchestrator this is the first thing to add.

---

## Part 4 — the time-series module versus its design

**4.1 Deliberate, and yes, nothing cross-checks the inversion.** dolphin is installed and has **never been run** in this
project. The module forms its own interferograms from the coregistered stack, unwraps with snaphu, and inverts with MintPy.
The reason: with a crop-first single-reference stack and GUNW corrections, a plain pair network was sufficient and directly
verifiable, whereas dolphin's phase linking expects a different input contract and would have added an untested dependency in
the middle of the chain. MintPy-on-GUNW as an independent check was specified and never built.

What does exist as verification is narrower than a cross-check of the inversion: the RIFG gate checks **interferogram
formation** against ISCE3 on every pair that shares the stack reference, and the triplet closure census checks **unwrapping**
consistency. The inversion itself is unchecked.

**4.2 Yes — on real data, twice per run.** Pre-event (9 pairs, 5 dates) and post-event (3 pairs, 3 dates), each inverted
strictly and then with the connected-component mask off, on 2026-09-16 and 2026-09-18. Outputs are in
`timeseries/*/mintpy/` and `timeseries/*/mintpy_relaxed/`. The synthetic-stack note you read is superseded.

**4.3 Inferred, not tested.** Neither dolphin nor `opera_utils` has ever executed here, so every claim about their contracts
is from reading their source, not from running them. MintPy was exercised only through our own path — we write
`ifgramStack.h5` and `geometryRadar.h5` ourselves, so `mintpy.load.demFile` and `processor = nisar` were never used either.
Treat that whole list as unverified.

---

## Part 5 — open scientific questions

**5.1 One pair only.** The 0.75 m (0.24 sample) figure is from the alignment test on 20260714 × 20260726
(`COMPARISON.md:247`); it was never re-measured across the seven dates. It is now cheap to settle: the coregistered stack
keeps the culled offsets per pair at `coreg/*/offsets/<date>_culled_rg_offsets`, so six more measurements are a few minutes of
reading. Not done. No further hypotheses were tested beyond the candidates listed at `COMPARISON.md:354`.

**5.2 Unresolved, and no comparison was attempted.** No GIM or GNSS TEC was ever fetched. It remains open item 2b in
`STATE.md`.

**5.3 Nobody was asked.** No contact with JPL or the NISAR team. The terrain-flattening hypothesis was tested by computing the
spectra of delivered GSLC samples and looking for the expected structure; it was not confirmed, and the result is recorded as
"hypothesis not confirmed" rather than "flattening ruled out". Whether the whiteness is a product property or a defect is
**unknown**.

**5.4 Never run.** Cost to test: one crop per date at ~2.5 min, plus one pair through coregistration (~80 min) to see the
side-band effect. The cheap version — crop two dates and compare the freq-B look-cell alignment — is under 10 minutes.

**5.5 None of them.** No dense-offset skip test, no ionosphere-only run without dense offsets, no range decimation, no GPU.
The §5 table is still entirely untested.

**5.6 Still open.** Nothing was added after the comparison; which chain carries the residual is still unestablished.

**5.7 Never tried.** `snaphu.io.Raster` file-backed I/O was not attempted; the documented workaround is the only path that was
exercised. Worth trying before the next full-tile unwrap.

**5.8 Nothing replaced it directly — the checks that exist are different in kind.** For the landlocked AOI the QC that
actually catches errors is: the RIFG comparison gate (catches interferogram-formation and flattening errors, and did),
whole-cycle closure over triplets (catches unwrapping errors), the temporal-coherence mask (catches unreliable pixels), and
for the event analysis the random-patch null test (catches over-interpretation). None of them is an absolute-radiometry check
like the water floor, so an absolute calibration error would still pass unnoticed.

---

## Part 6 — debt

**6.1 Confirmed open**, tracked only as item 6 in `STATE.md`'s open list. Both halves still stand: the band fallback in
`config.py` and the `qa.py` resolver.

**6.2 Confirmed** — `tools/compare_four_way.py:478` and `:511` hardcode EPSG 32645. The comparison is a one-off for that pair;
generalising is only worth it if the four-workflow comparison is re-run on another AOI, which is not planned.

**6.3 What blocks reuse on a new AOI:** the `qa.py` per-date resolver and the band resolver (both produce wrong-but-plausible
output rather than failing). The rest do not block: `slc_amp_overlay.py` pixel-centre and the `igram.py` 512-row seam are
cosmetic; the TECU sign convention matters only if you recompute the ionosphere rather than taking GUNW's; nominal-vs-effective
snaphu looks matters only if you unwrap through ISCE3 rather than through `nisar_timeseries.py`, which computes the effective
figure itself.

**6.4 Never executed.** There is no `coreg/GSLC_*` directory anywhere. The GSLC path of `nisar_coreg.py` has only ever been
exercised as `show` and `--dry-run`.

**6.5 Fixed in practice, not in habit.** `WF3_RSLC_CROPPED.md` WF3-30 records the fix: self-excluding patterns
(`'[n]epal_glof_aoi_aligned'`). The lesson line is the safe rule — stop jobs by PID or tmux session name, never by a pattern
your own command line contains.

**6.6 Confirmed still printed** (`run_track_r.py` epilog, the `--start-step insar --force` line). Still debt: `--force` there
will re-run a stage over existing outputs.

**6.7 No locking, no versioning.** `stack.json` is rewritten in place by ingest. The safety that exists is that it is written
via `os.replace` (atomic swap, so no torn file), but a concurrent reader can still see the old or new content with nothing
declaring which.

**6.8 Known stale:** `PIPELINE_DESIGN.md` Revision 2 still specifies dolphin with MintPy-on-GUNW as the check, which is not
what shipped (see 4.1) — that is the most misleading document in the tree. `docs/README.md`'s header still describes the
repository as one case study and one pair. `STATE.md`'s title line still says "as of 2026-09-15" though its content runs to
09-18. Nothing in the withdrawn-claims ledger has been re-established. Two items **should** be added to it and are not: the
flattening bug (a whole class of pairs was silently wrong until the RIFG gate caught it) and the finding that the
connected-component mask, not the coherence threshold, was what removed the glacier.

**6.9 Generated, do not hand-edit:** the error-log sections of `WF1_RSLC_FULL_TILE.md`, `WF2_GSLC_FULL_TILE.md`,
`WF3_RSLC_CROPPED.md`, `WF4_GSLC_CROPPED.md`, `COMPARISON.md` and `OPERATIONS_AND_LESSONS.md` are written by
`tools/render_error_log.py --doc <NAME> --into <file>`, which substitutes between markers. Prose outside the markers is hand-
written and safe to edit.

---

## Part 7 — tacit knowledge

**7.1 What is not in any document:** the GSLC leg was carried as far as it was mostly to answer "can we skip coregistration
entirely", and the answer (no — geometry-only registration costs 5 % coherence and a carrier term) is recorded, but the
intermediate attempts to *repair* delivered GSLCs are not: resampling them post hoc was tried in analysis and abandoned once
the spectra came back white, which is why `alignment_test_v2` sits in `tools/legacy/`. Also undocumented: the first event-study
attempt ran an ad-hoc shell chain (`logs/run_stack_coreg.sh`) that was killed and replaced by the coreg module — the script
survives in `case_studies/nepal_nisar_ascending/logs/`, its outputs were deleted.

**7.2 Provenance per key, honestly:**

| key | status |
|---|---|
| `rslc.dense_offsets` (64×64, ±20, skip 32) | **ISCE3 defaults, never varied.** Used by every validated run, so "validated" means "the runs we checked used these", not "we compared alternatives". The GUNW production values differ (64×96, ±32, skip 75) and the difference was estimated, never measured. |
| `rslc.rdr2geo` / `geo2rdr` tolerances | ISCE3 defaults, never varied |
| `coarse/fine_resample` tiles, `crossmul` flatten/oversample | ISCE3 defaults, never varied |
| rubbersheet | ISCE3 defaults, **not settable through the driver at all** (see 7.3) |
| `looks 9×8` | **chosen**: matches the ionosphere solve grid from the comparison and gives ~40 m square ground pixels |
| `unwrap.nlooks: null` | **derived** by ISCE3's own formula; validated to reproduce 44.57 at 9×8 |
| `crop_buffers` | **chosen** for the ISCE3 ionosphere kernel (see 7.4) |
| `run.jobs`, `min_free_gb`, `block_budget_mb`, `mintpy_workers` | operational, tuned to this 8-core/31 GB VM |
| `mintpy.*` | MintPy defaults except `mask_dataset` and `min_temporal_coherence`, both changed deliberately on 09-18 |

**7.3 Never measured.** The driver does not pass rubbersheet settings at all, so ISCE3's defaults apply; GUNW production uses
different ones (threshold 3 vs 0.75, 15 fill iterations vs 1, IDW vs linear, a 31-sample azimuth mean). The difference is an
accepted unknown.

**7.4 They can shrink a lot.** The buffers exist for ISCE3's ionosphere Gaussian (σ 33 px on the freq-B grid, kernel 100 px on
the decimated azimuth grid). With the ionosphere coming from GUNW, what actually needs margin is dense offsets: window 64 +
search ±20 plus the rubber-sheet filter, so roughly 100–150 px in each direction, say 200 to be safe. That would take
`buffer_az_lines` 1000 → 200 and `buffer_range_m` 12500 → ~2000, shrinking the crop from 12303 × 22464 to about 10900 × 15700
— **roughly 40 % fewer pixels**, so crops near 1.1 GB instead of 1.8 GB and coregistration proportionally faster. Untested; it
changes a processing parameter, so it needs a decision, and the AOI edge quality should be checked after.

**7.5 Starting on a fresh AOI tomorrow, keep exactly:** crop-first; the middle date as reference; the RIFG comparison gate
(it caught the one bug that would have invalidated everything); `params.json` freeze-and-refuse; per-unit manifests with
skip-if-done; GUNW corrections rather than computing the ionosphere. **Do differently:** shrink the crop buffers (7.4); set
`mask_dataset: "no"` from the start and filter on temporal coherence, since the default silently removed the very target;
choose and record the reference point before the first run rather than after; add the missing-GUNW-layer check to `show`
rather than discovering it mid-run; and decide up front whether a second geometry (descending) is in scope, because a single
look direction limits the conclusion far more than any processing choice.

**7.6 Most likely to bite next, and least prominent in the docs:** that a "done" pipeline can be quietly wrong in a way only a
cross-check catches. Two examples from three days: the flattening constant (every pair without the stack reference was wrong,
and nothing in the run's own output looked unusual) and the component mask (405 recoverable glacier pixels reported as 9). In
both cases the module exited 0, the manifests said ok, and the products opened fine. The habit worth transferring is not any
particular parameter — it is that every derived product needs one independent check, and the archive's gates are only as good
as the things they happen to compare.

---

## Part 8 — VM state

**8.1** 8 cores, 31 GB RAM, **no swap active**. Disk `/dev/sda1` 985 GB: **271 GB used, 674 GB free**. The VM was rebooted
this morning (uptime 8 minutes when I checked); **nothing is running**, no tmux sessions. Safe to delete:
`case_studies/nepal_nisar_ascending/L1_RSLC/` (174 GB — all seven granules are in GCS with matching sizes), and
`coreg/RSLC_ref20260714_AHH_glof_bigger_aoi/` (a stale directory from the module smoke test; it contains only two log files).

**8.2 Identical, verified today.** `tools/verify_archive.py` compares every prefix's file list and byte total against the
local tree; all 22 prefixes matched after the last sync. Re-run it after any local change.

**8.3 Not resolved.** The VM's default service account is read-only; writing still needs `gcloud auth login` as the user, and
that token expired twice during this work (most recently this morning). No non-interactive write credential exists.

**8.4** As 8.1. Swap has been inactive since the Sep 6 resize reboot and re-enabling was never decided.

**8.5 Five of seven, not two.** `case_studies/nepal_nisar_ascending/logs/fetch_rslc.log` records `VERIFIED ... crc32c` for
20260620, 20260702, 20260714, 20260726 and 20260819 (the GCS transfers). 20260831 and 20260912 came from ASF through
`tools/nisar_fetch.py`, which verifies **size against Content-Length only** — no checksum. So two granules are size-verified
rather than checksum-verified, and those two are the post-event pair.

---

## Part 9 — for the orchestrator

**9.1 Restart granularity, in practice:** crop = per date; coregistration = per pair; time series = per stage, with pairs as
units inside `ifg` and `unwrap`, and `mintpy` as one indivisible stage. A completed unit is skipped by its manifest, so a
crash costs only the unit in flight — but that unit restarts **from zero**, because ISCE3's own scratch is not resumable in
our wiring. Worst case is therefore one pair, ~80 minutes. The `mintpy` stage always re-runs whole (2–3 minutes).

**9.2 What each module refuses:** unknown config key; mode not RSLC/GSLC; reference date outside the selected range; fewer
than two dates; missing crops; not enough disk (per-unit estimate + margin); parameters differing from `params.json`; an
interferogram whose comparison against ISCE3's RIFG exceeds the gate; a GUNW network that does not connect all dates; an
existing output without `--force`. **One refusal has fired wrongly**: the params gate, twice, when the recorded key set
changed rather than a value (3.2). No other false refusal is recorded.

**9.3 No, a GUNW is not guaranteed.** They are produced per consecutive pair, and the tier matters: the urgent-response
product for 31 Aug – 12 Sep has **no troposphere cubes**. The corrections stage now inspects each GUNW for the layers it
needs, drops the ones missing anywhere in the network, records what it dropped in the manifest, and continues. For the
orchestrator the rule should be the same: check per layer per product, degrade explicitly, never assume a correction exists
because the file does.

**9.4 Measured on this VM (8 cores, 2 units in parallel), crop-first chain, ~1637 km² AOI:**

| step | wall clock | disk |
|---|---|---|
| DEM staging (once per case) | minutes | 0.55 GB |
| crop | 2.5 min per date | 1.8 GB per date |
| coregistration | 72–88 min per pair | ~32 GB scratch per running pair; kept: 2.2 GB SLC + 2.8 GB RIFG per secondary, 9 GB geometry once |
| time series: geometry | 4.5 min per date | 0.6 GB per date |
| interferogram | 1–1.6 min per pair | ~40 MB per pair |
| unwrap | 35–60 s per pair | ~20 MB per pair |
| GUNW corrections | 13–20 s per pair | 32–96 MB |
| MintPy (4 dask workers) | ~3 min | ~0.9 GB |

A seven-date AOI end to end is therefore about **9 hours**, dominated entirely by coregistration, and peaks near 110 GB of
disk with two pairs running.

**9.5 Already guarded in the modules:** disk before starting a unit; refusing to overwrite; parameter drift; missing inputs;
missing GUNW layers; interferogram correctness against ISCE3; unwrapping consistency (closure census); environment selection
per stage. **Still relying on the operator:** that only one run touches a stack at a time (3.3); that the reference point is
sensible rather than merely coherent; that the AOI actually overlaps the stack (checked, but only after geometry runs); that
`apply_patches --check` was believed (1.3); that the GCS token is valid before a long archive job; that a cross-check exists
for anything newly derived (7.6). That second list is the orchestrator's backlog.

**9.6 Cannot answer** — `asc/docs/ORCHESTRATOR_PROBES.md` is not on this VM. Upload it (or put it in the archive under
`code/`) and I will go through the 82 probes against the code.
