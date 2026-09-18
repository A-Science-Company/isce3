# Second questionnaire for the VM agent

Round 1 (`vm_handover_questions.md`) was answered in full at `asc/nisar_workflows/docs/VM_HANDOVER_ANSWERS.md`, and
`LEARNINGS.md` and `ORCHESTRATOR_PROBES.md` have been corrected from it. Thank you — the granule-size answer in
particular turned a documentation nit into a design rule.

This round is shorter and different in kind. Round 1 asked what you knew; this one mostly asks you to **run things
you already said were cheap**, plus a few questions your answers raised.

Both documents are now in the archive, so 9.6 is unblocked:
`gs://s1-slc/nisar_workflow/code/{LEARNINGS.md,ORCHESTRATOR_PROBES.md}`.

---

## Part A — Things you said were cheap, and the machine is idle (🔴)

You reported 8 cores, 674 GB free, nothing running, no tmux sessions. That makes this the moment. All three are
read-only or single-date, and none touches a finished product.

**A.1 Settle the 0.75 m misregistration across all seven dates.** You said the culled offsets are already on disk at
`coreg/*/offsets/<date>_culled_rg_offsets` and six more measurements are "a few minutes of reading". Please read
them. **Is the 0.24-sample range offset constant across dates, or pair-specific?** That single number decides
whether it is a sensor/product property worth reporting upstream or a per-pair artefact — and it is currently the
oldest open item in `STATE.md`.

**A.2 Test the crop-buffer shrink.** You gave the numbers: `buffer_az_lines` 1000 → 200, `buffer_range_m`
12500 → ~2000, ~40 % fewer pixels. Crop **one** date at the new buffers into a scratch path (do not touch the
existing crops), and report: output dimensions, file size, and whether the AOI polygon is still fully covered with
margin. If that looks right, one pair through coregistration (~80 min) would confirm the edge quality. **Treat this
as a proposal, not an instruction — it changes a processing parameter, so say if you disagree.**

**A.3 The cheap `--align-sideband-looks` check.** You costed the short version at under 10 minutes: crop two dates
with the flag and compare the freq-B look-cell alignment. Does the origin land on a multiple of 64, and does
`8·rg0_B − rg0_A` come out 0 on both dates?

**A.4** If any of A.1–A.3 argues against itself once you start, stop and say why rather than pushing through.

---

## Part B — Questions your answers raised (🟠)

**B.1 There is a fifth patch we did not know about.** `insar_utils.py` was not in the four we had. **What does it
fix, and what breaks without it?** We have it listed in `LEARNINGS.md` as one of five, with no description.

**B.2 Can you run `apply_patches.py --check` inside the container?** Is docker available on the VM? That would
close 1.1 properly — right now `LEARNINGS.md` says the image is *unverified*, which is honest but unsatisfying.
If docker is not available there, say so and we will run it locally.

**B.3 Why do granules differ in size between dates on the same track and frame?** Is that expected for NISAR, or a
processing-tier artefact? And the operational half: since each date is cropped to its own window against the same
lon/lat AOI, **do all seven crops cover the same ground area?** The flattening bug came from the *range origin*
differing between dates; we want to know whether the *extent* differs too, and whether anything downstream assumes
it does not.

**B.4 The corrections stage drops a layer for the whole network.** Reading `nisar_timeseries.py`: if any GUNW in
the network lacks a layer, `kinds.remove(kind)` drops that correction for **every** pair. So the UR 0831–0912
product with no troposphere cubes removes troposphere from the entire post-event series — including 0819→0831,
which has a PR GUNW that does carry it. **Is that deliberate (consistency across the network) or
over-conservative?** If deliberate, it is worth stating in the docs as a rule, because it is not obvious.

**B.5 You already ran a geometry cross-check we do not have the result of.** The corrections stage logs
`bperp_centre_gunw_m` against `bperp_centre_ours_m` per pair into `qa/corrections.json`. **What are the values?**
That is an independent check of our geometry against the agency's, it has already executed, and nothing in the
documentation reports it. If they agree closely it belongs in `LEARNINGS.md` as evidence; if they do not, that is
more important still.

**B.6 The per-date correction inversion pins the first date to zero.** Does that interact with MintPy's reference
point, or are the two references independent? We want to be sure a correction is not silently re-referencing the
displacement field.

---

## Part C — The 84 probes (🟠)

**C.1** `gs://s1-slc/nisar_workflow/code/ORCHESTRATOR_PROBES.md` — 84 probes, each one observed silent failure and
the assertion that would catch it. Please go through them against the code and mark each:

- **implemented** (give the file:line)
- **not implemented, worth adding**
- **not implemented, not worth it here** (say why)
- **wrong** — the probe misunderstands this pipeline

Two are new since your answers: **83** (the `params.json` added-key hole) and **84** (stack mutual exclusion).
Probes **32** and **50** were rewritten from your 1.3 and 2.5 answers — check we got them right.

**C.2 What did we miss?** You have the failure history we only read about. Which silent failures from the eleven
days are not in those 84?

---

## Part D — Ledger and documentation hygiene (🟡)

**D.1** You identified two findings that belong in the withdrawn-claims ledger and are not there — the flattening
bug and the connected-component mask. **Please add them**, since you have the evidence and the file.

**D.2** `PIPELINE_DESIGN.md` Revision 2 still specifies dolphin with MintPy-on-GUNW as the check, which is not
what shipped, and you called it the most misleading document in the tree. **Correct it to describe what shipped,
or annotate it as superseded?** Your call — you know which is less confusing for the next reader.

**D.3 Is dolphin still in the plan?** It has never been run, its contracts are inferred from source, and it is
carried in `insar_ts`. Keep it as a stated future option, or remove it from the environment and the docs so nobody
assumes it was used?

---

## Part E — Data provenance (🟡)

**E.1** 20260831 and 20260912 are size-verified only, no checksum — and they are the post-event pair, the two
granules carrying the event signal. **Worth re-fetching with CRC32C, or is `Content-Length` sufficient given they
processed cleanly?** A judgement call; we would rather you made it than we did.

---

## One process note

Round 1's most useful answers were the ones where you corrected the premise of the question — the two AOIs being
one polygon, both frame figures being real, five patches rather than four. Please keep doing that. If a question
here assumes something false, the assumption is the answer.
