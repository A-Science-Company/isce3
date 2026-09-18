# Questions for the VM agent — completing the information transfer

Paste this into the Claude session on the processing VM (`/home/sharath/isce3`).

**Context.** Everything under `gs://s1-slc/nisar_workflow/` has been pulled down and read: `README.md`,
`code/CLAUDE.md`, `STATE.md`, all ten docs and the ~22,500-line code tree. From it we built two documents —
`asc/docs/LEARNINGS.md` (the consolidated learnings) and `asc/docs/ORCHESTRATOR_PROBES.md` (82 silent failures and
the assertion that catches each). The questions below are **only** the things the archive does not contain or does
not settle. Each answer corrects a specific line in one of those documents.

**How to answer.** From disk, quoting the file, line or command output that establishes it. Say **"not recorded"**
rather than reconstructing — this project's own withdrawn-claims ledger exists because confident reconstruction is
where the wrong claims came from. Where something is genuinely unknown, "unknown" is a complete answer.

**Priority markers:** 🔴 blocks reproduction · 🟠 corrects a documented claim · 🟡 completes the transfer.

Where a question could be answered by reading the archived code, we did that ourselves rather than asking — those
appear below already answered, marked "confirmed", and need only a yes/no if we got one wrong. What is left is
what the code cannot tell us: run history, intent, what was tried and abandoned, and the state of the machine.

---

## Part 0 — Please upload these (🔴)

**0.1 The three AOI definitions.** Every crop, both studies and both time series are defined by them, and none is
in the archive. Nothing can be reproduced without them:

```bash
gsutil cp /home/sharath/asf_slc/glof_exact_aoi.kml            gs://s1-slc/nisar_workflow/code/aoi/
gsutil cp /home/sharath/nisar_downloader/glof_bigger_aoi.kml  gs://s1-slc/nisar_workflow/code/aoi/
gsutil cp /home/sharath/nisar_downloader/nepal_glacier_zone.kml gs://s1-slc/nisar_workflow/code/aoi/
```

Then print each one's bbox, WKT and area, so the areas quoted in the docs (1642 km² for `glof_bigger_aoi`,
0.62 km² for the glacier zone) can be tied to an actual geometry. **Is there a fourth?** The glacier bbox + 100 %
buffer (1.66 × 2.84 km, 4.72 km²) — is that a file or computed in code?

**0.2 `nisar_downloader.py`** — referenced in the code, not archived. Ours or third-party? Superseded by
`tools/nisar_fetch.py`, or still needed?

**0.3 The report-PDF environment** — `~/.venvs/report-pdf`. A `requirements.txt` plus the Playwright/Chromium
version and the 16 apt libraries, or the reports cannot be rebuilt.

**0.4 Anything else outside `/home/sharath/isce3/`** that the pipeline reads. A `find` over the absolute paths in
the code would settle it.

---

## Part 1 — The container (🔴)

**1.1** The image at `asia-south1-docker.pkg.dev/iocl-poc-479616/isce2-trials/isce3:pair_wise` was built from the
git branch, which carries **one** patch (`resample_slc_v2`, upstream #372). The VM's `apply_patches.py` carries
**four**, three of them ours and unreleased (`generate_insar_mask` vectorised, `h5_prep` standalone product_dict
entries, `unwrap` disk-streaming). **Confirm the image cannot run the validated chain** — specifically, would it
hit the 2.886e9-iteration mask loop, and does `python -m nisar.workflows.unwrap` still KeyError without the
h5_prep patch?

**1.2** Were any of the three "ours" patches reported upstream? If not, is there a reason — do they encode a local
assumption upstream would reject?

**1.3** Is `apply_patches.py --check` still exiting 0 when a patch is **not** applied? That is the single check
`verify.sh` and the Docker build rely on, and it is probe 32/68/71 in our list.

---

## Part 2 — Numbers we could not settle from the archive (🟠)

Each of these is currently a hedge or a silent choice in `LEARNINGS.md`.

**2.1 The frame dimensions disagree — and it is a data question, not a code one.** The docs and the code both
say 53200 × 54244 (2886 Mpx; `config.py:695`, `trackr.py:52`). A second figure, **54720 × 54239** (2968 Mpx →
~345 GB), is recorded for "the Nepal frame". The disk gate hardcodes neither — it computes from the granule at
116.4 B/px. **Which dimensions do the actual granules report?** `h5py` on any one of the seven would settle it.

**2.2 The 7 h 38 m – 8 h 05 m figure** appears with two different labels — "the whole job at 9×8 looks" and
"building a full-tile ionosphere layer from scratch". Same run, or two? A reader will double-count.

**2.3 The conjugate control.** Conjugating the GUNW drops agreement to 0.22 — **from 0.807 (at 20 m) or from
0.978 (where γ > 0.7)?** Both baselines exist; they are different supports and neither is labelled.

**2.5 Strided HDF5 reads.** Our probe list says a strided read decompresses every chunk it touches and is nearly a
full-file read on a gzip+shuffle 512×512-chunked granule — yet Track R's QA *is* a strided read at
`step = max(1, sqrt(size/4e6))`. We resolved this as "acceptable for summary statistics, not for a quicklook
image". **Is that the actual reasoning, or is the Track R QA read a known performance bug?**

**2.6 Dry-run semantics.** One source says the dry run asserts file names and never excuses a missing file;
another records the opposite as a deliberate design decision (`dry_run` degrades hard preconditions to warnings,
because otherwise a whole-pipeline `--dry-run` is impossible). What is the actual rule?

**2.7 ENL 54 vs 53** — recorded as "close to the right answer for entirely the wrong reason" and redone. Which is
the final figure, and is the corrected derivation written down anywhere?

**2.8 359 GiB or 359 GB?** Both appear. Which unit.

---

## Part 3 — Shipped versus specified (🟠, highest value)

`OPERATIONS_AND_LESSONS.md` §12 reads as an automation contract. We have taken it as a **specification for the
orchestrator, not a description of running code** — this was the single biggest error in our first draft. Please
confirm or correct, per item: **is it implemented, or is it a rule we wrote down?**

| §12 item | status (verified in the archived code) |
|---|---|
| environment + overlay hash gate | NOT implemented — no `hashlib`/`sha256` in the tree |
| host record / drift detection | NOT implemented — no `boot_id` |
| boot-id equality START vs END | NOT implemented |
| per-stage RAM plan vs MemAvailable | NOT implemented — no `MemAvailable` |
| itemised disk bill for the whole DAG | partial — per-unit `min_free_gb` at 116.4 B/px only |
| product inventory + per-layer content gate | NOT implemented — no such function |
| provenance sidecar per product | partial — one per **stage**, overwritten per run |
| out-of-session monitoring | NOT implemented |
| temp name + **atomic rename** | **implemented** — `os.replace` in `dem.py`, `nisar_timeseries.py`, `util.py`, `nisar_coreg.py` |
| `params.json` freeze-and-refuse | **implemented** |
| stage env re-exec | **implemented** |
| four-code exit contract (0/1/2/3) | **implemented** |
| RSLC-mode + integer-decimation refusals | **implemented** |

**3.1** Any row wrong? We read these out of the code rather than assuming, but we cannot see run history.
**3.2** The `params.json` gate "migrates when only the recorded key set changed" (the MintPy section was removed
from it). Is that migration a hole that lets a changed *parameter* through, or is it strictly key-set-only?
**3.3** Is the tmux session name really the only mutual exclusion between runs? Two case configs naming the same
stack — what actually stops them colliding?

---

## Part 4 — The time-series module diverges from its design (🟠)

**4.1** PIPELINE_DESIGN Revision 2 specified **dolphin** (phase linking + snaphu + inversion) with **MintPy on the
GUNW stack as an independent check**. What shipped forms interferograms itself, unwraps with snaphu, and runs
MintPy's inversion on our own stack; dolphin appears only as an installed package. **Was that a deliberate
decision or drift?** And: is it correct that **nothing currently cross-checks the inversion**?

**4.2** The MintPy stage is recorded as exercised only on a synthetic stack with a dummy third date. Has it since
been exercised on real data end to end?

**4.3** The downstream tool contracts (dolphin's `compressed` reserved substring, `opera_utils.get_dates` reading
only the basename, dolphin not inferring the NISAR wavelength so outputs stay in radians without
`--input-options.wavelength 0.241963`, `mintpy.load.demFile` mandatory for `processor = nisar`, MintPy ANDing a
stack-wide common mask, `opera_utils` silently dropping every GUNW because its regex allows one datetime pair) —
**were these tested against the real tools, or inferred from reading their source?**

---

## Part 5 — Open scientific questions (🟡)

**5.1** The uniform **0.75 m slant-range misregistration** — further hypotheses or tests? Present on all seven
dates or only the one pair?
**5.2** **Which ionosphere level is physically right** — ours or the GUNW's, one joint cycle (0.232 TECU) apart?
Was any GIM/GNSS TEC comparison attempted?
**5.3** **Why are delivered GSLC samples spectrally white** (98 % of bins within 6 dB of peak, min −7.9 dB)? Was
JPL or the NISAR team asked? Known product property or suspected defect? The terrain-flattening explanation is
recorded as tested and not confirmed — what was the test?
**5.4** `rslc_subset.py --align-sideband-looks 8` with the range origin on a multiple of 64 is **unexercised**.
Ever run? Expected cost to test?
**5.5** Was **any** PIPELINE_DESIGN §5 lever tried after the doc was written — dense-offset skip 64–75, no dense
offsets for an ionosphere-only run, range decimation, GPU?
**5.6** The rubber-sheet residual is **chain-specific** (full-tile and crop build it from their own culled grids,
p95 0.034 lines apart) and the source says which chain carries the residual is *not established*. Still open?
**5.7** Was `snaphu.io.Raster` **file-backed I/O** ever tried as the fix for tiled-unwrap fragmentation? Our docs
present "force `single_tile_reoptimize`/`regrow_conncomps` false and accept unrelabelled components" as the only
option, but file-backed output would let the global pass stream from disk.
**5.8** The **water-floor coherence check** is called the highest-teeth QC available — but it was designed on a
38 %-ocean Venezuela frame and the Nepal AOI is landlocked. **What QC replaced it for a landlocked AOI?**

---

## Part 6 — Known debt: still open? (🟡)

**6.1** The **band resolver** still falls back silently — `config.py` returns another band's file when the
requested one is missing, and `qa.py` resolves with `frequencies[0]` then loops all bands on that one file with
the `KeyError` caught as a warning. Confirmed still open? Tracked anywhere?
**6.2** `compare_four_way.py` hard-codes `epsg=32645` (line 511) — confirmed. Is it worth generalising, or is
the comparison a one-off that will not be re-run on a new AOI?
**6.3** Still open: `slc_amp_overlay.py` pixel-centre, the `igram.py` 512-row seam, the TECU sign convention,
nominal-vs-effective snaphu looks, the `qa.py` per-date resolver. Which of these block reuse on a new AOI?
**6.4** Has the **GSLC path of `nisar_coreg.py`** ever executed?
**6.5** `pkill -f` self-match — recorded as recurring on Sep 8 and Sep 14 and "still unfixed". Fixed? If not,
what is the safe pattern?
**6.6** The destructive `--force` resume epilog is still printed (`run_track_r.py:173`) — confirmed. Is that
deliberate now, or still debt?
**6.7** `stack.json` was rewritten under a live run ("metadata-only, so it won't disturb the running chain") and
was safe only by luck. Is there any locking or versioning now?
**6.8** **Which documents do you know to be stale** but did not get to correct before the archive was cut? And is
anything in the withdrawn-claims ledger since re-established, or newly withdrawn but unrecorded?
**6.9** Which docs are **generated** (by `render_error_log.py`) and must not be hand-edited?

---

## Part 7 — Tacit knowledge, the part that dies with the session (🟡)

**7.1 What was tried and abandoned** that is in no document? `tools/legacy/` and `*_ABORTED_*` capture some of it —
what is missing?
**7.2** For `coreg_configs/defaults.yaml` and `ts_configs/defaults.yaml`, **per key**: validated by measurement, or
an ISCE3 default never varied? The header says "the one used by the validated runs", which is a weaker claim than
it reads as. Especially `dense_offsets` (window 64/64, half-search 20/20, skip 32/32) versus the GUNW production
values it differs from.
**7.3** The **rubbersheet** parameters are not settable by the driver and differ from GUNW production. Was that
difference ever measured, or is it an accepted unknown?
**7.4** `crop_buffers.buffer_az_lines: 1000` was sized for ISCE3's ionosphere Gaussian and kept "although the
ionosphere now comes from GUNW". **What should it be now?** Coregistration needs ~52 px. How much would the crops
shrink?
**7.5** Starting again on a fresh AOI tomorrow — what would you do differently, and what would you keep exactly?
**7.6** What is most likely to bite the next person that is **not** prominent in the documentation?

---

## Part 8 — VM state (🟡)

**8.1** Disk used/free, what is still running (tmux), what is safe to delete.
**8.2** Is the local tree still byte-identical to the verified archive, or has anything changed since?
**8.3** Credentials — the VM service account is read-only and a non-interactive write credential was still needed.
Resolved?
**8.4** Final VM spec: cores, RAM, swap state (recorded inactive since the Sep 6 resize reboot, re-enabling never
decided), disk.
**8.5** Only two of the seven RSLCs (0620, 0702) are recorded as CRC32C-verified; 0714/0726/0819 were **hard
links** and 0831/0912 came from ASF later. Are all seven checksum-verified against what was processed?

---

## Part 9 — For the batch orchestrator, the next piece of work (🟡)

**9.1** Actual **restart granularity**: if a run dies at stage N, what has to be redone in practice?
**9.2** What does each module **refuse**, and has any refusal ever fired wrongly?
**9.3** Is a GUNW guaranteed per pair? What should the orchestrator do when one is missing, or is UR (no
troposphere cubes)?
**9.4** Realistic **wall-clock and disk per pair** for the crop-first chain as the modules now run it, on a fresh
AOI, including crop and DEM staging.
**9.5** **Which OPS-nn failure modes do the two modules already guard against, and which still rely on the
operator remembering?** That second list is the orchestrator's actual backlog.
**9.6** We wrote 82 probes at `asc/docs/ORCHESTRATOR_PROBES.md`. If you can see that file: which are already
implemented, which are wrong for this pipeline, and what did we miss?
