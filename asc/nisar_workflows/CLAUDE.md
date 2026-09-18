# NISAR workflows — working context

Full context is in the repository root: **`../../CLAUDE.md`** (environments, data locations, conventions, gotchas).
The resume point for the work itself is **`STATE.md`** in this directory, and **`docs/README.md`** indexes the rest.

Quick orientation:

- `nisar_coreg.py` + `coreg_configs/` — crop and coregistration (docs/COREG_MODULE.md)
- `nisar_timeseries.py` + `ts_configs/` — interferograms, unwrapping, GUNW corrections, MintPy (docs/TIMESERIES_MODULE.md)
- `run_track_r.py` / `run_track_g.py` + `nisar_wf/` — the ISCE3 drivers underneath both modules
- `tools/` — analysis, report builders, archive upload and verification
- `configs/` — science templates for the drivers (the modules generate their own configs; do not hand-edit the generated ones)

Run `show` on any config before running anything: it prints the resolved plan and every parameter, and touches nothing.
