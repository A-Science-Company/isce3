# NISAR ISCE3 workflows: documentation index

Case: Nepal GLOF, NISAR track 98 frame 16, pair 20260714 × 20260726, HH (`case_studies/nepal_glof`).
Purpose: the evidence base for automating a modular NISAR SLC → interferogram pipeline. Full-tile RSLC is the quality benchmark.

| document | what it covers |
|---|---|
| [COREG_MODULE.md](COREG_MODULE.md) | **`nisar_coreg.py`**: config-driven coregistration module (RSLC/GSLC, optional crop, reference date), stages, outputs, job semantics |
| [glof_event report](../../case_studies/nepal_nisar_ascending/report/glof_event/nepal_glof_nisar_event_report.html) | **Event study**: NISAR analysis of the 26 Aug 2026 outburst (pre-event limits, coherence, backscatter change, controls); built by `tools/glof_event_analysis.py` + `tools/build_glof_event_report.py` |
| [VM_HANDOVER_ANSWERS.md](VM_HANDOVER_ANSWERS.md) | **Handover Q&A (2026-09-18)**: answers from disk to the 60 questions in `vm_handover_questions.md` — AOI geometry, granule dimensions, what shipped vs what was specified, per-key parameter provenance, VM state, orchestrator backlog |
| [TIMESERIES_MODULE.md](TIMESERIES_MODULE.md) | **`nisar_timeseries.py`**: coregistered stack → pair network → tiled snaphu → GUNW ionosphere/troposphere/tides → MintPy LOS velocity |
| [PIPELINE_DESIGN.md](PIPELINE_DESIGN.md) | **The decided production pipeline** (full-tile RSLC → wrapped + coherence + ionosphere → export; AOI unwrap), stage budget, export layout, untested levers |
| [WF1_RSLC_FULL_TILE.md](WF1_RSLC_FULL_TILE.md) | Benchmark: ISCE3 `insar` on the full RSLC tile (1×1 interferogram, 9×8 unwrap + split-spectrum ionosphere); stages, runconfig, resources, gates, problems log |
| [WF2_GSLC_FULL_TILE.md](WF2_GSLC_FULL_TILE.md) | GSLC geocoding of both dates and bands on a pinned 5 m grid, map-domain interferogram, ported ionosphere; product defects; problems log |
| [WF3_RSLC_CROPPED.md](WF3_RSLC_CROPPED.md) | Crop-first RSLC: `tools/rslc_subset.py` invariants (zero Doppler, A/B lattice, look alignment, buffers) and the ISCE3 chain on the subset; problems log |
| [WF4_GSLC_CROPPED.md](WF4_GSLC_CROPPED.md) | GSLC on the AOI grid from the start; timings; problems log |
| [COMPARISON.md](COMPARISON.md) | The four-way comparison: method, gates, v2 results, open items, problems log |
| [OPERATIONS_AND_LESSONS.md](OPERATIONS_AND_LESSONS.md) | Running long jobs on the VM: supervision, logs, resources, patches, automation contract, working with an AI agent; problems log |

Team report (HTML, figures, equations): `case_studies/nepal_glof/comparison_v2/report/nepal_glof_four_workflow_report.html`,
built by `tools/build_report.py`; PDF `/home/sharath/isce3/nepal_glof_four_workflow_report.pdf` (`tools/report_pdf.py`, venv `~/.venvs/report-pdf`).
Stage timings flow chart: `case_studies/nepal_glof/comparison_v2/report/rslc_1x1_stage_timings.html` (`tools/build_timing_flow.py`).
Resume state: [../STATE.md](../STATE.md).

Analysis tools added after the comparison: `tools/alignment_test.py` (registration closure test), `tools/iono_transfer_test.py`
(ionosphere screens applied to a crop), `tools/gunw_validation.py` (our outputs vs the NISAR GUNW), `tools/nisar_fetch.py` (ASF search and
local download).

Problems logs are generated. Edit the sources in `case_studies/nepal_glof/comparison/verification/` or the rules in
`tools/render_error_log.py`, then re-render with `python tools/render_error_log.py --doc {WF1,WF2,WF3,WF4,COMPARISON,OPS} --into docs/<file>`.
