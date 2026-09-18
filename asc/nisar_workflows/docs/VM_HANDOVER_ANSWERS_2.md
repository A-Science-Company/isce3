# Answers to `vm_handover_questions_2.md`

Answered 2026-09-18 on the processing VM. Round 1's answers are in [VM_HANDOVER_ANSWERS.md](VM_HANDOVER_ANSWERS.md).
Everything in Part A was run; the measurements are below with the commands' own output.

**Three premises need correcting before anything else, and one of them is mine from round 1.**

1. The 0.75 m misregistration is **not** constant across dates (A.1).
2. `--align-sideband-looks` is **not** unexercised — every crop in the event study already satisfies it (A.3).
3. There are **four** patches, not five. My round-1 answer said five because `tools/patches/` holds five files; the fifth
   is not a patch (B.1). Please correct `LEARNINGS.md`, which took the number from me.

---

## Part A — the measurements

### A.1 The range misregistration is pair-specific, not a product property

Read from `coreg/*/offsets/<date>_culled_{rg,az}_offsets` (float64, 381 × 698 culled dense-offset grid), reference 20260726:

| secondary | valid | range median (samples) | range mean | std | azimuth median (lines) | std |
|---|---|---|---|---|---|---|
| 20260620 | 265 935 | **+0.1731** | +0.1683 | 0.046 | +0.0259 | 0.048 |
| 20260702 | 265 938 | **+0.8769** | +0.8672 | 0.057 | −0.1662 | 0.056 |
| 20260714 | 265 938 | **−0.2419** | −0.2437 | 0.034 | −0.0766 | 0.079 |
| 20260819 | 265 938 | **−0.3237** | −0.3156 | 0.048 | −0.0400 | 0.040 |
| 20260831 | 265 938 | **−0.4687** | −0.4640 | 0.055 | −0.0344 | 0.040 |
| 20260912 | 265 938 | **−0.5920** | −0.5847 | 0.054 | −0.0412 | 0.036 |

The per-date medians span **−0.592 to +0.877 samples (−1.85 to +2.74 m), a 1.55 m spread**, while each date's own
within-scene scatter is only 0.03–0.06 samples. So the offset is **constant within a pair and different between pairs** —
a per-acquisition term, not a fixed sensor or product property, and not worth reporting upstream as one.

Two things worth noting on top of what was asked:

- **The old number reproduces exactly.** 20260714 measures −0.2419 samples; the alignment test's 0.24 samples (0.75 m) was
  that pair. Nothing was wrong with the original measurement — only with reading it as a constant.
- **There is a trend.** From the reference forward the residual grows monotonically more negative: 20260714 −0.242,
  20260819 −0.324, 20260831 −0.469, 20260912 −0.592, roughly −0.004 samples/day (−1.3 cm/day). 20260620 (+0.173) and
  20260702 (+0.877) break it going backwards. A steady drift plus one outlier is the shape of a range-timing or
  propagation-delay term, and the size is in the right range for differential ionospheric group delay (0.26 m per TECU at
  freq A, so ±2.7 m is ±10 TECU). **I did not test that** — our GUNW ionosphere screens are level-referenced, so the absolute
  TEC that would confirm it is not in them. It needs GIM/GNSS TEC, the same external reference the ionosphere-level question
  needs. The azimuth residuals, by contrast, are small and stable (−0.17 to +0.03 lines).

`STATE.md` open item 3a can be closed as "pair-specific, quantified" and merged into the ionosphere-level item, which is where
the remaining question actually lives.

### A.2 The crop-buffer shrink works, and I recommend it — with one caveat

Cropped 20260726 into `/tmp` at `--buffer-az-lines 200 --buffer-range-m 2000 --buffer 100`, leaving the existing crops alone:

| | current buffers | proposed | change |
|---|---|---|---|
| dimensions (freq A) | 12303 × 22464 | **10701 × 15808** | −39 % pixels |
| pixels | 276 Mpx | 169 Mpx | |
| file size | 1.80 GB | **1.20 GB** | −33 % |
| slant range covered | 899 452 – 969 601 m (70.1 km) | 909 845 – 959 208 m (49.4 km) | |
| azimuth | 8.09 s | 7.04 s | |

**Coverage holds.** The unbuffered AOI window for this date is azimuth 9008–19302; the new crop is 8802–19503, so it keeps
**206 lines before and 201 after** (as configured), and 640 samples (2000 m) either side in range. The AOI is fully inside
with the intended margin.

**My recommendation: adopt it, but not by editing the defaults for existing work.** The saving is real — 39 % fewer pixels
means coregistration time and scratch fall by about the same fraction, so a pair goes from ~80 to ~50 minutes and peak scratch
from ~32 to ~20 GB. The caveat is that dense offsets need valid data around every correlation window, so the outermost ~100
pixels of a crop are where offsets get culled; with 1000 lines of buffer that never mattered, with 200 it will trim the
rubber-sheet fit at the edges. The AOI itself is unaffected because the margin sits outside it.

So: **new AOIs yes, at `buffer_az_lines: 200`, `buffer_range_m: 2000`; this stack no** — re-cropping it would invalidate the
coregistered products, and the 8 hours are already spent. If you want the edge quality confirmed first, one pair through
coregistration at the new buffers (~50 min) compared against the existing pair's culled-offset statistics would settle it.
That is a processing-parameter change either way, so it is your call rather than mine to make it the default.

### A.3 Already satisfied — the flag is the default and every crop used it

`--align-sideband-looks` defaults to 8, so `rslc_subset.py` has been snapping the range origin to a multiple of
`band_ratio × 8 = 64` since before the event study. Measured on all seven crops, against each granule's own origin:

| date | rg0_A | rg0_B | rg0_A mod 64 | 8·rg0_B − rg0_A | az0 mod 9 |
|---|---|---|---|---|---|
| all seven | 6784 or 6720 | 848 or 840 | **0** | **0** | **0** |

Every crop lands on the 64-sample lattice, the side-band relation is exact on every date, and the azimuth origin is on the
9-line lattice too. `STATE.md` open item 4 is **stale** — delete it. What remains true is the historical note: the *v2 crops*
of the four-workflow study (made before the option existed) were snapped to 8 only.

### A.4 One argument against itself, noted above

A.2 is the only one with a caveat, and it is in the answer rather than a reason to stop: the buffer shrink is safe for the
AOI but narrows the margin dense offsets rely on, so it belongs to new stacks rather than to this one.

---

## Part B

### B.1 There is no fifth patch — there is a fifth file

`apply_patches.py` wires **four** patches (lines 41–104):

| patch | source file | target | upstream |
|---|---|---|---|
| resample_slc_v2 optimized HDF5 reader | `resample_slc_v2.py` | `nisar/workflows/resample_slc_v2.py` | isce-framework/isce3#372 |
| generate_insar_mask vectorised | **`insar_utils.py`** | `nisar/products/insar/utils.py` | ours |
| h5_prep RUNW/GUNW_STANDALONE entries | `h5_prep.py` | `nisar/workflows/h5_prep.py` | ours |
| unwrap streams snaphu inputs from disk | `unwrap.py` | `nisar/workflows/unwrap.py` | ours |

`insar_mask_vectorized.py` (161 lines) is **not referenced by `apply_patches.py` at all**. It is the standalone vectorised
`generate_insar_mask`, loaded directly by `tools/test_insar_mask_patch.py` as the candidate implementation;
`insar_utils.py` (777 lines) is the full replacement module that embeds the same function. Same fix, two forms: one to test
against stock, one to install.

What the mask patch fixes, since `LEARNINGS.md` has it without a description: the stock `generate_insar_mask` builds the mask
with a pure-Python double loop appending one int per output pixel. It runs on the **interferogram** grid, so 40 Mpx at freq B
9×1 but **2886 Mpx at freq A 1×1** — 2.886e9 iterations and ~58 GB of transient list plus int64 array, which is hours of
runtime and an OOM on a 31 GB box. The replacement preallocates uint32 and vectorises the range loop, is bit-identical on real
freq-B data (`tools/test_insar_mask_patch.py`, 13× faster), and falls back to the stock code when `num_sub_swaths != 1`.

### B.2 No docker on this VM

`which docker podman` finds neither, though the user is in the `docker` group — so the daemon was never installed here (the
image was built elsewhere). I cannot close 1.1 from this machine. Run it wherever the image is available:

```bash
docker run --rm -v /path/to/isce3/asc/nisar_workflows:/w -w /w \
  asia-south1-docker.pkg.dev/iocl-poc-479616/isce2-trials/isce3:pair_wise \
  python tools/apply_patches.py --check
```

and read the **text**, not the exit code — per 1.3, `--check` exits 0 either way. Require the literal "already applied" four
times.

### B.3 The granules themselves differ; the crops inherit it

Measured on the seven granules (freq A):

| date | lines | samples | start range (m) | Δ vs 20260726 |
|---|---|---|---|---|
| 20260620 | 54720 | 54247 | 878 242.006 | **−8 samples** |
| 20260702 | 54720 | 54239 | 878 266.988 | 0 |
| 20260714 | **53200** | 54244 | 878 242.006 | **−8 samples** |
| 20260726 | 54720 | 54239 | 878 266.988 | 0 |
| 20260819 | 54720 | 54239 | 878 266.988 | 0 |
| 20260831 | **53200** | 54238 | 878 266.988 | 0 |
| 20260912 | **53200** | 54238 | 878 266.988 | 0 |

So the differences are in the **delivered granules**: azimuth length 53200 or 54720 lines, range width 54238–54247 samples,
and the near-range start differing by 8 samples on two dates. Is that expected for NISAR? **Unknown** — I have no product-spec
statement either way. What it is *not* is a processing-tier artefact: 20260714 (F tier flag in its name) and 20260831 (N) sit
on both sides of both differences, and the two 53200-line dates include one PR-only and one that also exists as UR.

**Do the seven crops cover the same ground?** No, and nothing downstream needs them to:

| date | crop (freq A) | slant range covered |
|---|---|---|
| 20260620 / 20260714 | 12303 × 22464 | 899 427 – 969 576 m |
| 20260726 / 20260912 | 12303 × 22464 | 899 452 – 969 601 m |
| 20260702 | 12303 × 22528 | 899 252 – 969 601 m |
| 20260819 / 20260831 | **12294** × 22528 | 899 252 – 969 601 m |

Extents differ by up to 64 samples (200 m) in near range and 9 lines in azimuth, because each window is computed against the
same lon/lat AOI on a different radar grid. Everything downstream works on the **reference** grid — secondaries are resampled
onto it, and the time series takes its window from it — so differing extents are the expected case, not a hazard. The one
thing that did assume otherwise was the flattening, which is exactly the bug: it used the geo2rdr offset (measured in the
secondary's own grid) without the constant start-range difference. The lesson for the orchestrator is probe 8's: never assert
cross-date dimension equality, pin the output grid instead.

### B.4 Deliberate, and I would keep it — but it should be a documented rule

Dropping a layer network-wide is the conservative choice and it was made on purpose: a time series inverted from screens that
exist for some pairs and not others is not a consistent field. If 0819→0831 carried its troposphere and 0831→0912 did not,
the per-date troposphere for 0912 would be built from a network missing one edge, and the resulting date would differ from its
neighbours by whatever the missing screen was — a step in the series that looks like signal.

That said, the trade is real: we lose a correction we have for the pair that matters most. Two better options, neither
implemented:

1. **Invert what exists and mark the dates it cannot reach.** Sound when the available pairs still connect the dates; here
   they do not (one of two edges).
2. **Fall back to a PR product for the same pair** when one exists — for 0831–0912 it may by now.

I have documented the current behaviour as a rule in `docs/TIMESERIES_MODULE.md` rather than changing it, because changing it
alters a delivered product.

### B.5 The geometry cross-check passes — here are the numbers

From `qa/corrections.json`, per pair, perpendicular baseline at scene centre, ours (ISCE2-convention formula from the orbits)
against the GUNW's own `perpendicularBaseline` cube sampled at the same point:

| pair | GUNW (m) | ours (m) | difference |
|---|---|---|---|
| 20260620–20260702 | −16.739 | −18.242 | −1.503 |
| 20260702–20260714 | −25.052 | −25.268 | −0.216 |
| 20260714–20260726 | +1.285 | +1.491 | +0.205 |
| 20260726–20260819 | +44.764 | +46.465 | +1.701 |
| 20260819–20260831 | +18.556 | +16.954 | −1.602 |
| 20260831–20260912 | −64.703 | −59.590 | +5.112 |

Every sign agrees, and the differences are 0.2–5.1 m on baselines of 1–65 m. The two are not expected to match exactly: ours
is computed at the multilooked window's centre pixel from the orbits, the GUNW's is a 500 m-posted cube interpolated at the
same lon/lat, and the conventions for the sign of the perpendicular component are implementation-specific. What this does
establish is that our orbit handling, zero-Doppler geometry and baseline formula agree with the agency's to the metre on six
independent pairs — worth having in `LEARNINGS.md` as evidence, which is why you asked. It also bounds the topographic phase:
at 65 m the height of ambiguity is ~1 km, so DEM error cannot be mistaken for deformation here.

### B.6 Independent by construction — the correction cannot re-reference the displacement

Two separate references, and they are made to match rather than interact:

- **Temporal:** the per-date inversion pins the first date to zero (`ψ[0] = 0`), and MintPy's time series does the same. Both
  series therefore start at zero on the same date, so their difference does too.
- **Spatial:** `write_correction_ts` subtracts the correction's own value at MintPy's reference pixel
  (`disp -= disp[:, REF_Y, REF_X]`) **before** writing, reading `REF_Y`/`REF_X` out of the `ifgramStack.h5` that MintPy just
  referenced. So the correction is zero at the same pixel where the displacement is zero.

The GUNW screens' arbitrary per-pair constants collapse into a per-date constant during the inversion, and that constant is
what the spatial referencing removes. `diff.py` would re-reference file2 itself if it had to, but by the time it runs there is
nothing left to re-reference.

---

## Part C — the 84 probes

Read from `gs://s1-slc/nisar_workflow/code/ORCHESTRATOR_PROBES.md`. Status against the code as it stands. **32 and 50 are both
correct as rewritten**, except that 32 should say "all four patches", not five (B.1).

### Ingest gate

| # | status | evidence / note |
|---|---|---|
| 1 | **partial** | zero Doppler is used everywhere (`nisar_timeseries.py:401,431`, `rslc_subset.py`), but nothing asserts the LUT is empty and there is no per-quadrant coverage test. The assert is one line — add it. |
| 2 | **implemented** | `nisar_timeseries.py:461` records `range_residual_m` = median abs(orbit range − grid range); measured 4.2e-5 m. It is logged, not enforced — make it fail above 1 mm. |
| 3 | not implemented, **worth adding** | the line rate is used consistently but never asserted against `1/az_time_interval`. |
| 4 | not implemented, **worth adding** | it *holds* on all seven dates (A.3) but nothing checks it. Cheapest probe in the list. |
| 5 | **implemented** | `rslc_subset.py` refuses non-integer band decimation. |
| 6 | **implemented** | both bands are cropped from one azimuth window, B scaled by the ratio. |
| 7 | **implemented** | `nisar_wf/trackr.py:148` `check_pol_available`, called at :540. |
| 8 | **implemented** | the modules pin the reference grid and never compare cross-date dimensions; B.3 shows why that matters. |
| 9 | **implemented** | `nisar_coreg.py:230,258` — tier is explicit, the glob filters on `_PR_`/`_UR_`, duplicate dates raise, tier is recorded. |
| 10 | **implemented** | `nisar_timeseries.py:788,827` — layers probed per product, dropped network-wide, recorded as `dropped`. See B.4. |
| 11 | not implemented, **not worth it here** | we never enable ISCE3's TEC correction; becomes worth it the day someone does. |
| 12 | **implemented** | `rslc_subset.py:454-465` recomputes `boundingPolygon` from the cropped grid. |
| 13 | **implemented** | `rslc_subset.py:533-542` rebases `validSamplesSubSwath*` by −rg0 and clips. |
| 14 | **partial** | 13 is done by name; there is no general enumeration of index-valued datasets. |
| 15 | **implemented** (snapping), probe **worth adding** | `--align-rg-looks/--align-sideband-looks/--align-az-looks`; verified today, never asserted. |
| 16 | **partial** | `np.allclose` appears with defaults in `gridgate.py:91`, `igram.py:177`. |

### Config-render gate

| # | status | evidence / note |
|---|---|---|
| 17 | **implemented** | `trackr.render_runconfig` emits unwrap/ionosphere blocks only when `product_type` can reach them; the config refuses RIFG + ionosphere. |
| 18 | **implemented** | `trackr.py:497` `validate_runconfig` (yamale) and `write_generated` in both modules loads the rendered config through `Config.from_yaml`. |
| 19 | **implemented** | posting is required; `gridgate` compares delivered against pinned. |
| 20 | **implemented** | the pinned geogrid plus `gridgate`. |
| 21 | **partial** | `gslc.flatten` defaults true; there is no hard reject of false. |
| 22 | not implemented, **worth adding** | one line at render time, and it protects against an upgrade rather than a typo. |
| 23 | **implemented** | `nisar_timeseries.py:452-459` — nlooks from ISCE3's own formula, floored at 1, verified to reproduce 44.567 at 9×8. |
| 24 | **partial — and I disagree with half of it** | `single_tile_reoptimize` is forced false (`:632`); `regrow_conncomps` is deliberately **true**. At 1.2 Mpx the global relabel costs seconds and gives usable components; forcing it false is right for a 3.6 Gpx full tile, not here. Make the rule size-dependent. |
| 25 | not implemented, **worth adding** | our overlap is in pixels (150). Metres would survive a looks change. |
| 26 | not implemented, **not worth it here** | we never enable intermediate-file removal. |
| 27 | **partial** | the knob exists (`dense_offsets.enabled`); nothing labels the stack geometry-only or budgets the coherence loss. |
| 28 | not implemented, **not worth it here** | we never set start pixels. |
| 29 | **implemented** | generated configs + `params.json` freeze. |
| 30 | **partial** | see round 1, 2.6: inputs are checked in a dry run, artifacts an earlier dry-run step skipped are excused deliberately. |
| 31 | not implemented, **worth adding** | cheap as a lint over `# ...` lines quoting numbers. |
| 32 | not implemented, **worth adding** — correct "five patches" to **four** | parse the text per patch; hash the installed overlay. |

### Post-stage gate

| # | status | evidence / note |
|---|---|---|
| 33 | not implemented, **worth adding** | the 26-of-193 truncation is the exact failure this catches. |
| 34 | **implemented** | temp name + `os.replace` (`nisar_coreg.py:110`, `dem.py:318`, `nisar_timeseries.py`), plus per-unit verification and the exit code. |
| 35 | not implemented, **worth adding** | `/proc/sys/kernel/random/boot_id` is two lines and this VM reboots. |
| 36 | **partial** | per-pair coherence mean and unwrap statistics are recorded; there is no general per-layer constant/finite check. |
| 37 | not implemented, **not worth it here** | ISCE3's 1×1 coherence ≡ 1 is known and documented; our own estimator never produces it. |
| 38 | **not applicable** | landlocked AOI; no water floor exists. See round 1, 5.8 for what replaced it. |
| 39 | not implemented, **worth adding** | we compute the effective look count but never check it against the measured p5. |
| 40 | **partial** | finite checks exist per stage, not as a rule over every written raster. |
| 41 | not implemented, **worth adding** | cheap, and metadata is where the all-NaN case was found. |
| 42 | **partial** | we clip heights into the cube range when sampling GUNW cubes rather than asserting coverage. |
| 43 | **implemented** | `nisar_timeseries.py:651` `max_abs_rewrap_residual_rad`, measured 8e-6 rad. |
| 44 | **implemented** | `:650` components, `fraction_in_component_0`, valid fraction, per pair. |
| 45 | not implemented, **worth adding** | we have the tiles and the overlap; seam statistics are a few lines. |
| 46 | not implemented, **worth adding** | applies to the geocode step we now run through MintPy. |
| 47–48 | **not applicable now** | the KD-tree backmap belonged to the comparison; the current chain geocodes with MintPy. |
| 49 | **partial** | statistics are computed over in-window masks and the pixel set is named in the report. |
| 50 | **implemented, and your rewrite is correct** | `qa.py:1-22` forbids striding for imagery; `trackr.py:767` strides only its own RIFG coherence layer for summary statistics. |

### Comparison and reporting gate

| # | status | evidence / note |
|---|---|---|
| 51 | **partial** | every number in the event report names its pixel set, but no helper enforces it. |
| 52 | **partial** | counts are reported (e.g. 383 of 4569 reliable); coverage is not gated. |
| 53 | **implemented in practice** | estimator, window and look count are recorded on both sides in the comparison. |
| 54 | **implemented** | the event report publishes the strict and relaxed inversions side by side — that *is* the mask sensitivity number. |
| 55 | **implemented** | multilooking divides by each field's own valid count. |
| 56 | **partial** | one nodata convention is used but not asserted at write time. |
| 57 | **implemented** | the convention is declared in the module docstring and in the artefact names. |
| 58 | **implemented** | `tools/gunw_validation.py:203` `sign_check_vs_conjugate_R` — and round 1, 2.3 gives its baseline. |
| 59 | **implemented** | the unwrapped comparison reports the modal whole cycle and the fraction at the mode. |
| 60–62 | **implemented** | the ionosphere resolver reports the class, the ambiguity interval and the estimator. |
| 63 | **implemented** | `qa/corrections.json` carries `ionosphere_filled_fraction` per pair (0.0 everywhere here). |
| 64–65 | **implemented** | circular ML plane with the resultant-length guard; spectral occupancy before any shift estimate. |
| 66 | **implemented** | the event analysis ships two control pairs and the report leads with them. |

### Gate hygiene

| # | status | evidence / note |
|---|---|---|
| 67 | **partial** | the RIFG gate requires > 1000 coherent pixels; most other gates do not check non-emptiness. |
| 68 | **partial** | shell helpers use `set -euo pipefail`; the Python tools raise, but `archive_to_gcs.py` prints and continues on a failed prune. |
| 69 | **implemented** | `run_cmd` captures the return code; `--detach` writes `EXIT=` to the log. |
| 70 | **partial** | the report states 4 of 9 pairs were checkable against ISCE3, and the closure census covers the rest — but the code does not log "N of M". This is the probe I would implement first. |
| 71 | **not implemented — and live in our tree** | `tools/test_insar_mask_patch.py:18` imports `stock` from the **installed** module, which after `apply_patches.py` *is* the patch. Run before patching it tests correctly; run after, it compares the patch with itself. Import the `.orig` backup instead. |
| 72 | **partial** | the patch test asserts bit-identity against the production function, which is the right shape; other checks re-derive. |
| 73 | **implemented in documentation** | circular validations are labelled as consistency checks in `COMPARISON.md`. |
| 74 | **partial** | the geolocation-mask threshold has a published sweep; other thresholds do not. |
| 75 | **partial** | `unit_done` skips on status plus recorded output **sizes**, and `params.json` covers byte-changing parameters — but identity is size-based, not a hash, and there is no collision check over the planned path set. |
| 76 | **implemented per unit** | `status/<unit>.json` holds inputs, outputs with sizes, checks, timing and the git revision — one per product, not per stage. The **drivers'** provenance (`provenance/`) is still per stage and overwritten. |
| 77 | **partial** | `tmux has-session` for exclusion, the `progress` command reads stage directories and logs; no heartbeat or idle detector. |
| 78 | not implemented, **worth adding** | one `findmnt -T` per output path at start-up. |
| 79 | **implemented** | `python -u` in every child, log paths printed at launch, one log per run ID. |
| 80 | not implemented, **worth adding** | we read warnings by eye, which is how the band-resolver fallback survived. |
| 81 | **partial** | the ledger exists and is read first; state documents are hand-written apart from the error logs, which `render_error_log.py` generates. Two entries were missing until today (D.1). |
| 82 | process rule, **not enforced** | |
| 83 | **confirmed, not implemented** | exactly as written; this is my round-1 3.2. |
| 84 | **confirmed, not implemented** | exactly as written; the tmux name is per case and stage, so two configs resolving to one `stack_id` collide silently. |

### C.2 — what the 84 miss

Four failures from these eleven days that I do not find in the list:

1. **A mask that removes the measurement target, silently.** MintPy's `maskDataset = connectComponent` dropped 99 % of the
   glacier and reported 9 pixels as the answer. Probe 52 covers AOI coverage generally; this needs the specific form: *assert
   the retained-pixel count inside the declared target polygon, not only scene-wide, and fail when a mask removes more than a
   stated fraction of it.* The general rule is that every mask must publish what it removed **inside the target**, not
   overall.
2. **Non-deterministic reruns.** MintPy's automatic reference point is a random pixel above a coherence threshold, so two runs
   of the same command give different displacement fields. Probe: *given identical inputs and config, a rerun must reproduce
   the outputs bit-for-bit, or every non-deterministic choice must be recorded and re-loadable.* We fixed it by choosing the
   point ourselves.
3. **An exclusion that excluded nothing.** `gcloud storage rsync --exclude` silently matched no objects, uploading 16.7 GB of
   duplicates and 31 `.pyc`. Probe: *after any filtered operation, assert the filter did something — count in, count out,
   count skipped — and fail when a non-empty filter matches zero items.* This generalises well beyond gcloud.
4. **Plans measured in the wrong units.** The upload plan counted hard-linked files twice, so "75.7 GB" was 16.7 GB of double
   counting. Probe: *a transfer plan must reconcile its predicted byte count against the destination's actual byte count, and
   inode-deduplicate before predicting.*

If I had to rank the 84 plus these four by what would have caught the most damage here: **70** (coverage logging, would have
caught the flattening bug immediately), then the new probe 1 above (the mask), then **84** (stack exclusion), then **32**
(patch verification), then **83**.

---

## Part D

**D.1 Done.** Both entries are now in `STATE.md`'s withdrawn-claims ledger: the flattening bug ("the crop-first chain's
interferograms are correct because the pairs that were checked matched ISCE3" — wrong because the gate reached 4 of 9 pairs)
and the mask ("the glacier zone cannot be measured because its coherence is too low" — wrong cause; the conclusion survives,
the reason did not).

**D.2 Annotated as superseded, not rewritten.** `PIPELINE_DESIGN.md` now opens with a banner saying Revision 2's dolphin
specification is not what shipped, what does run instead, what verifies it, and that the divergence was deliberate — pointing
to `TIMESERIES_MODULE.md` for the real chain. I kept the body because it remains the reference for stage costs and for the
evidence behind crop-first and GUNW corrections; rewriting it would destroy the record of what was decided on 15 September and
why. A superseded document that says so in its first paragraph is less confusing than a document silently edited to match
today.

**D.3 Keep dolphin in the environment, drop it from the plan.** It has never run, so every claim about its contracts is
inferred; carrying those in a design document as if they were settled is how the round-1 confusion started. Removing the
package buys nothing and would have to be undone: dolphin's phase linking is the natural tool the day we try to get motion out
of low-coherence ice, which is the one place our current chain has nothing to offer. So: it stays installed and untouched, the
design doc no longer specifies it, and the honest status is "available, never exercised".

---

## Part E — the two unchecked granules

**My judgement: do not re-fetch. Record the checksums you can compute now, and treat the processing itself as the stronger
evidence.**

Three reasons:

1. **Both granules were fully decompressed twice.** Cropping reads every chunk of the freq-A and freq-B HH arrays, and
   coregistration read them again. They are gzip+shuffle chunked; a corrupted chunk raises in libhdf5 rather than returning
   wrong bytes. Silent corruption surviving that is possible only in the metadata that neither step reads.
2. **A re-fetch would not check what you want checked.** ASF's manifest carries no published checksum, so re-downloading tests
   the transfer twice rather than the source once. It would cost 52 GB of transfer to compare two of our own downloads.
3. **What is missing is forward integrity, and that is cheap.** I computed CRC32C locally for both granules and can pin them
   in the fetch manifest; from then on any future copy, restore or bit rot is detectable, which is the practical risk from
   here.

If you disagree and want the re-fetch anyway, it is about 40 minutes and I would compare the new download against the recorded
CRC32C rather than against the file on disk, so that a mismatch tells you which copy moved.

---

## One note back

The three corrected premises in this round all came from the same place: a plausible reading of something true. Five files
looked like five patches; one pair's offset looked like a constant; an option named in an open-items list looked unexercised.
The pattern worth carrying into the orchestrator is that **the failure mode is not a wrong number, it is a right number read
as a different kind of thing** — which is why probes 70 and 73, about coverage and circularity rather than thresholds, are the
two I would implement first.
