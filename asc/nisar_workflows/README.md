# NISAR InSAR workflows

A config-driven NISAR InSAR pipeline built on the ISCE3 checkout it sits inside: NISAR L1 RSLC granules on disk go in; a coregistered SLC stack, wrapped and unwrapped interferograms, GUNW ionosphere/troposphere/tide corrections and a MintPy LOS displacement time series and velocity map come out. For a whole case in one config and one command, start at [`../README.md`](../README.md) (`run_case.py`); this page is the reference underneath it. Two modules do the work — `nisar_coreg.py`, which takes one short case config and drives the ISCE3 Track R / Track G drivers underneath, and `nisar_timeseries.py`, which takes its own case config and runs its stages directly against isce3, snaphu and MintPy. No granule is ever downloaded, nothing overwrites a finished product, and every stage records the parameters it used.

## What is here

| path | what it is |
|---|---|
| `run_case.py` | **The case runner.** One `case_studies/<NAME>/case.yaml` in — `workflow` (RSLC / cropped_RSLC / GSLC / cropped_GSLC) × `mode` (coregistration / interferogram), a resolution and a source per correction — and it generates both module configs, runs them in order and pushes the products to GCS. Subcommands `show`, `status`, `run`, `upload`. Reference: [`../README.md`](../README.md). |
| `nisar_coreg.py` | **Module 1.** Case config in, coregistered stack out. Stages `prepare` (DEM) → `crop` → `coreg` → `igram` (GSLC only). Subcommands `show`, `status`, `progress`, `run`. |
| `nisar_timeseries.py` | **Module 2.** Coregistered RSLC stack in, LOS displacement time series and velocity out. Stages `geometry` → `ifg` → `unwrap` → `corrections` → `mintpy`. Subcommands `show`, `status`, `run`. |
| `run_track_r.py`, `run_track_g.py` | The ISCE3 drivers `nisar_coreg.py` invokes, both usable standalone. Track R (`mode: RSLC`): RSLC → coregistered RIFG in radar coordinates, stages `ingest`, `dem`, `runconfig`, `insar`, `qa`. Track G (`mode: GSLC`): RSLC → L2 GSLC on a pinned, shared geogrid, plus interferogram, water mask, unwrap and a folium overlay. Both are built from `nisar_wf/`. |
| `tools/` | `nisar_fetch.py` (ASF search + download), `rslc_subset.py` (crop), `apply_patches.py` (overlay upstream ISCE3 fixes), comparison and report builders, `archive_to_gcs.py` / `verify_archive.py`. |
| `case_template.yaml` | The annotated starting point for a `case_studies/<NAME>/case.yaml`: every `run_case.py` key with its default and what it does. |
| `coreg_configs/`, `ts_configs/` | Case configs for the two modules, each beside a `defaults.yaml` holding every processing parameter at its validated value — coreg: crop buffers, `rslc`/`gslc`, `run`; time series: looks, network, snaphu, corrections, MintPy and the per-stage interpreters. `configs/` holds the science configs for the *drivers*, including `_template.yaml`; the modules generate their own driver config into the stack directory, so do not hand-edit a generated one. |
| `docs/`, `STATE.md` | `docs/README.md` is the index; `COREG_MODULE.md` and `TIMESERIES_MODULE.md` are the module references, `PIPELINE_DESIGN.md`, `COMPARISON.md`, `WF1`–`WF4` and `OPERATIONS_AND_LESSONS.md` the evidence base. `STATE.md` is the resume point: what is finished, what was decided, and the claims that were withdrawn and must not be reintroduced. |

## Environments

| environment | for |
|---|---|
| `isce3_env` | ISCE3 0.25.12: geometry, coregistration, geocoding. Required for `nisar_coreg.py`, `run_track_r.py`, `run_track_g.py`, and the `geometry` stage of the time series. |
| `insar_ts` | MintPy 1.6.4, snaphu-py 0.4.1, dolphin 0.42.5, GDAL 3.12. Required for the `ifg`, `unwrap`, `corrections` and `mintpy` stages. |
| `~/.venvs/report-pdf/bin/python` | Playwright/Chromium, only for printing a report HTML to PDF (`tools/report_pdf.py`). Nothing in the pipeline needs it. |

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh
conda activate isce3_env     # module 1, the drivers, and the geometry stage
conda activate insar_ts      # ifg, unwrap, corrections, mintpy
cd /home/sharath/isce3/asc/nisar_workflows
```

`nisar_timeseries.py` re-execs each stage in the interpreter named under `envs:` in `ts_configs/defaults.yaml` (`isce3_python` for `geometry`, `ts_python` for the rest), so it can be launched from either conda env. `nisar_coreg.py` does *not* re-exec: it spawns the drivers and the subsetter with `envs.isce3_python` from `coreg_configs/defaults.yaml`, which is `null` by default and then means the interpreter that started it — so either set that key or launch the module from `isce3_env`. Both modules' `show` and `status` need only PyYAML.

## Running the pipeline

Preconditions: the L1 RSLC granules are already in `<workdir>/L1_RSLC/` (named `NISAR_L1_<tier>_RSLC_*_<YYYYMMDD>T*.h5`), the matching L2 GUNWs are in `<workdir>/L2_GUNW/` if you want the atmospheric corrections, and `~/.netrc` has `urs.earthdata.nasa.gov` so the `prepare` stage can download the DEM. No granule is ever downloaded: use `tools/nisar_fetch.py` for RSLCs and GUNWs.

### 1. Crop and coregister the stack — `nisar_coreg.py`

```bash
# plan only, writes nothing: resolved dates, reference date, stack name, every parameter in effect
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml show
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml run --dry-run

# the run: prepare (DEM) -> crop every date -> coregister every secondary, in tmux; then watch it
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml run --detach
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml progress
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml status
tmux attach -t coreg_nepal_nisar_ascending_all
```

Measured for the 7-date Nepal ascending case on an 8-core / 31 GB VM (`run.jobs: 2`, 2026-09-15): **4 h 14 m wall**, of which ≈ 2.5 min per crop and 71–88 min per secondary. Scratch is ≈ 32 GB per running pair, ≈ 110 GB peak with two in parallel; a unit refuses to start with less than `run.min_free_gb` (30) spare. Output is `<workdir>/coreg/RSLC_ref<date>_<freq><pol>_<aoi>/` with `slc/<date>.slc`, `geometry/{lon,lat,hgt}.rdr`, `ifg/RIFG_*.h5`, `offsets/`, `params.json` and `stack_manifest.json`.

### 2. Time series and LOS velocity — `nisar_timeseries.py`

```bash
# dates, pair network, GUNW coverage per pair, whether the coreg stack is ready. Writes nothing.
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml show

# geometry -> ifg -> unwrap -> corrections -> mintpy, each in its own environment, in tmux
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml run --detach
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml status

python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_post_event.yaml show
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_post_event.yaml run --detach

ls /home/sharath/isce3/case_studies/nepal_nisar_ascending/timeseries/RSLC_ref20260726_AHH_glof_bigger_aoi/pre_event/export/
```

Measured: pre-event, 5 dates and 9 pairs, **12 min wall**; post-event, 3 dates and 3 pairs, **7 min wall**. The two are separate series on purpose; the useful post-event product is per-date displacement relative to 20260819. The exports are GeoTIFFs in EPSG:4326 at ≈ 37 × 39 m: `pre_event_los_velocity_m_per_yr.tif` and its `_std` and `_uncorrected` companions, `pre_event_los_displacement_m_<first>_<last>.tif`, `pre_event_temporal_coherence.tif` and the temporal-coherence mask at the configured threshold (`pre_event_mask_temporal_coherence_0.3.tif` for the shipped config). MintPy's own HDF5 products are alongside in `mintpy/`. The extra `mintpy_relaxed/` and `_0.7` mask in this tree are kept from an earlier strict run that used the defaults; the module writes only `mintpy/`, moving any previous run to `mintpy_replaced_<stamp>`.

## Conventions that apply to every module

**`show` before `run`.** Both modules resolve the plan — dates, reference, output names, every parameter in effect, and for the time series the pair network and GUNW coverage — and print it without touching anything. `nisar_coreg.py run --dry-run` goes one step further and renders and validates the generated ISCE3 driver config without writing it; `nisar_timeseries.py run --dry-run` reports which interpreter each stage would use and which pairs it would process. **`--detach` for anything long.** It starts the run in a tmux session and returns immediately, printing the session name and the log path. Sessions are `coreg_<case>_<stage>` and `ts_<case>_<name>_<stage>` (`<stage>` is `all` for a full run). Re-running `--detach` while that session exists refuses with exit 3 rather than starting a second copy.

**Parameters are frozen, not merged.** Every processing value comes from `coreg_configs/defaults.yaml` or `ts_configs/defaults.yaml`; a case config overrides a key by repeating its section. The values that change a product's bytes are written to `params.json` in the crop directory, the stack directory and the time-series output directory on first use, and a later run whose values differ **stops with exit 2** instead of mixing outputs. To change one of those, set `tag:` in the case config (or a new `name:` for a time series) and get a separately named output. The `mintpy:` section and `reference_lalo` are deliberately not locked: that stage is re-derived from the frozen pair products, so changing them just re-runs it, moving the previous `mintpy/` to `mintpy_replaced_<stamp>`.

**Finished outputs are never overwritten.** Each unit — a crop date, a coregistered secondary, a pair's interferogram or unwrapped phase — writes a JSON manifest under `status/`. A unit whose manifest says `ok` and whose outputs still match the recorded sizes is skipped. `--force` is the only way past that, and it is per unit:

```bash
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml run --stage coreg --dates 20260831 --force
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml run --stage unwrap --pairs 20260714_20260726 --force
```

**Exit codes are the same contract in both modules:**

| code | meaning |
|---|---|
| `0` | ok |
| `1` | a unit failed |
| `2` | config error — unknown key, bad date range, or a parameter that differs from the frozen `params.json` |
| `3` | missing prerequisite — crop not done, coregistration not finished, not enough free disk, tmux session already running |

**Logs** land next to the product, never in the repo: `<workdir>/coreg/<stack>/logs/`, `<workdir>/crop/<aoi>/logs/` and `<workdir>/timeseries/<stack>/<name>/logs/`, holding `run_<stage>_<stamp>.log` (the module), per-unit logs, `*.driver.log` / `*.console.log` (ISCE3) and `<stage>_<stamp>.detached.log` (tmux stdout, ending in `EXIT=<code>`). Per-unit manifests with inputs, outputs and sizes, git revision, host and duration are in `status/*.json` beside each.

**Ask before changing a processing parameter.** The values in the two `defaults.yaml` files are validated against the full-tile RSLC benchmark and the NISAR GUNW; `docs/COMPARISON.md` and `docs/COREG_MODULE.md` record what each was measured against.

## nisar_coreg.py — crop and coregistration

One case config in, one coregistered stack out, from NISAR L1 RSLCs that are already on disk (it never downloads an RSLC — the only thing it fetches is the DEM, in `prepare`). With `rslc.ionosphere.enabled` it also runs ISCE3's split-spectrum solve, and each pair's product becomes a RUNW carrying `ionospherePhaseScreen` (`runw/`, gated on being finite and not identically zero). **RSLC mode** resamples every secondary date onto the reference date's radar grid — ISCE3 `rdr2geo → geo2rdr → coarse resample → dense offsets → rubber sheet → fine resample → crossmul` — giving coregistered SLCs, the reference geometry rasters and a 1×1 RIFG per pair. **GSLC mode** geocodes every date independently onto one pinned map grid using geometry only: pixel-aligned by construction, not registered to each other. Full detail: `docs/COREG_MODULE.md`. The module launches its subprocesses with `envs.isce3_python` (default `null` = the interpreter that started it), so unless that key is set it does **not** switch environments for you — activate first. `prepare` downloads a DEM from `urs.earthdata.nasa.gov` (`dem.source: NISAR`, ellipsoidal heights), so `~/.netrc` must hold Earthdata credentials the first time a case is run.

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env
cd /home/sharath/isce3/asc/nisar_workflows
python nisar_coreg.py -c <case config> {show|status|progress|run} [options]
```

| subcommand | what it does |
|---|---|
| `show` | Prints the resolved plan — selected dates, reference and how it was chosen, stack and crop directory names, DEM presence, and the parameters in effect: the `inputs`, `run` and `rslc` sections plus `crop_buffers` (`gslc` in GSLC mode), with the list of case-config overrides. The `dem` section is not among them — `show` prints only the resolved DEM path and whether it exists. Runs nothing, writes nothing. |
| `status` | One line per date: role, input RSLC size, crop state, coreg state — each `done`, `running`, `FAILED` or `-`. Also whether the DEM and the coreg set-up are done, and free disk. |
| `progress` | Detail for unfinished units: ISCE3 stage timeline from scratch-directory birth times, current stage, last progress line from the unit log, scratch size. RSLC only; in GSLC mode it falls back to `status`. |
| `run` | Executes the stages. Default order `prepare → crop → coreg → igram`. |

| option | what it does | default |
|---|---|---|
| `-c PATH`, `--config PATH` | The case config (`coreg_configs/<case>.yaml`). Required for every subcommand. | — |
| `--stage {prepare,crop,coreg,igram}` | Run only this stage. Stops at the first stage that does not exit 0. `igram` is GSLC only and a no-op in RSLC mode. | all four, in order |
| `--dates D [D ...]` | Units to process. `crop`: dates. RSLC `coreg`: secondaries (the reference is not a unit — passing it is a config error). GSLC `coreg`: dates. | all units of the stage |
| `--jobs N` | Units in parallel, within one stage (thread pool). | `run.jobs` from the merged config — `2` in `coreg_configs/defaults.yaml` |
| `--force` | Redo units whose manifest says done and clear the pair's ISCE3 scratch before re-running. Also re-runs the coreg set-up. For a `coreg` unit the module does **not** delete the stack's existing outputs, and the export step refuses to replace a file it did not just write (`… exists and is a different file; refusing to overwrite`), so re-running a finished secondary fails at export (exit 1, `checks.export`) unless you first remove that unit's `slc/<sec>.slc`, `slc/<sec>.hdr`, `ifg/RIFG_<ref>_<sec>_*.h5` and `offsets/<sec>_*`. Only `crop` overwrites its own output under `--force`. | off |
| `--dry-run` | Report what each stage would do, render and validate the generated driver config, write nothing. | off |
| `--detach` | Start the same command inside a tmux session and return immediately. Only affects `run`. | off |
| `-h`, `--help` | The usage block plus the module docstring. | — |

### Case config

Only these nine keys are case keys. Any other top-level key must name a section of `coreg_configs/defaults.yaml` (`envs`, `inputs`, `dem`, `crop_buffers`, `rslc`, `gslc`, `run`) and is treated as an override; anything else is rejected with exit 2.

| key | meaning | `null` / absent means |
|---|---|---|
| `case` | Case name. Goes into the DEM filename (`aux/dem/dem_<case>.tif`) and the tmux session name. | required — error |
| `workdir` | Holds `L1_RSLC/` (inputs), `aux/dem/`, and the `crop/` and `coreg/` outputs. Must exist. | required — error |
| `mode` | `RSLC` or `GSLC`. | required — error |
| `crop` | Cut every RSLC to the AOI first (`tools/rslc_subset.py`, zero Doppler) before coregistering or geocoding it. In GSLC mode it *also* clips the output geogrid to the AOI bounding box, which is what the validated WF4 pass did. | `false` — full tile; the stack name says `fulltile` |
| `aoi_kml` | KML whose coordinates give the AOI. Must exist; its stem names the crop directory and appears in the stack name. | no AOI. With `crop: true` this is an error |
| `reference_date` | RSLC only. Must be one of the selected dates. | the middle of the selected dates; for an even count, the earlier of the two middle ones |
| `start_date` | Lower bound (inclusive) on the RSLCs already in `L1_RSLC/`. `YYYYMMDD` or `YYYY-MM-DD`. | earliest on disk |
| `end_date` | Upper bound (inclusive). | latest on disk |
| `tag` | Suffix appended to the stack name; `[A-Za-z0-9-]` only. Needed to override a parameter of a stack that already exists. | no suffix |

`coreg_configs/nepal_nisar_ascending_rslc.yaml` sets `case: nepal_nisar_ascending`, `workdir: /home/sharath/isce3/case_studies/nepal_nisar_ascending`, `mode: RSLC`, `crop: true`, `aoi_kml: /home/sharath/nisar_downloader/glof_bigger_aoi.kml`, and leaves the three date keys `null`. With the seven PR granules on disk that resolves to reference `20260726` and the stack name `RSLC_ref20260726_AHH_glof_bigger_aoi`.

### Parameters and the params.json lock

Every processing value lives in `coreg_configs/defaults.yaml`, set to the values of the validated Nepal GLOF runs. A case config overrides one by repeating its section, e.g. `rslc: {dense_offsets: {skip_range: 64, skip_azimuth: 64}}`. `show` prints the merged result and names the sections you overrode. On first use the module writes the effective values to `<stack>/params.json` (and, for crops, `crop/<aoi>/params.json`). A later run whose values differ is refused: it names the differing keys and exits 2 rather than mixing bytes from two settings into one directory. Two ways forward: set `tag:` in the case config — the stack becomes `RSLC_ref…_glof_bigger_aoi_<tag>` and is built from scratch beside the old one — or restore the recorded values.

Crop buffers are the exception only for the crop directory: when `crop_buffers` differ from the defaults the crop directory name grows a suffix (`glof_bigger_aoi_az<lines>_rg<m>m_px<px>`), so crops cannot collide. The stack name does not carry them — they are recorded inside `<stack>/params.json` as `crop_params` — so changing them for a stack that already exists is still refused with exit 2 until you set a `tag:`. Adding dates to an existing stack with unchanged parameters is allowed: the set-up runs again in full (the driver config is regenerated for every pair, and ingest + DEM check + runconfigs re-run for the whole stack — all cheap), and only the new secondaries are actually processed, since verified units are skipped.

### Stages and outputs

| stage | when | unit | produces |
|---|---|---|---|
| `prepare` | once per case; skipped if the DEM exists | — | `aux/dem/dem_<case>.tif` (via `run_track_r.py --only ingest dem`) |
| `crop` | `crop: true`, either mode | date | `crop/<aoi>/<date>.h5`, plus `status/<date>.json`, `params.json`, `logs/` |
| `coreg` set-up | once, again when dates are added | — | the generated, validated driver config `coreg/<stack>/track_r.yaml` (`track_g.yaml` for GSLC), ISCE3 ingest + DEM check + runconfigs, `status/_setup.json` |
| `coreg` | always | RSLC: one secondary. GSLC: one date | the stack tree below. GSLC adds a final `gridgate` after all dates pass |
| `igram` | GSLC with `gslc.interferogram.enabled: true` | one pair (one driver call for all of them) | `isce3/pairs/<ref>_<sec>/trackG/ifg_<F>_<P>_<ly>x<lx>.{igram,coh,nlooks,amp}.tif`, `status/igram_<d1>_<d2>.json`. RSLC interferograms come from `nisar_timeseries.py` instead |

```
<workdir>/coreg/RSLC_ref20260726_AHH_glof_bigger_aoi/
├── slc/<date>.slc + .hdr                        coregistered SLCs on the reference grid (ENVI), reference included
├── geometry/{lon,lat,hgt}.rdr + .hdr            reference-grid geolocation from rdr2geo
├── ifg/RIFG_20260726_20260620_A_HH_1x1.h5       one wrapped 1x1 interferogram per pair
├── offsets/20260620_culled_{az,rg}_offsets + .hdr    the rubber-sheet offset fields
└── track_r.yaml (generated driver config — edit the case config, not this), params.json,
    status/<unit>.json, status/_setup.json, stack_manifest.json, logs/, isce3/ (scratch deleted
    after verification when run.prune_scratch is true) — see docs/COREG_MODULE.md
```

GSLC stacks write `gslc/<date>_gslc_freq<F>.h5` instead of `slc/`, `geometry/`, `ifg/` and `offsets/`.

### Worked examples

`show` resolves dates, reference, names and parameters without touching anything. A dry run additionally renders the driver config and validates it with the driver's own loader (`nisar_wf.config.Config`), and prints what each unit would do; it does not reach the ISCE3 runconfigs, which are the files checked against the installed yamale schema during the real `coreg` set-up, and if the set-up has already covered every unit it skips the render entirely. If the crops are not there yet it validates against the full-tile granule paths, says so, and exits 3. `status` and `progress` are the views to run from a second shell while a run is going.

```bash
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml show
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml run --dry-run
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml status
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml progress
```

`progress` prints each unfinished unit's stage timeline against a fixed `crop v2` reference column measured on the validated `20260714 × 20260726` pair on this 8-core VM — `rdr2geo 21.6 min`, `geo2rdr + product prep 26.8`, `coarse resample 2.0`, `dense offsets 16.3`, `rubber sheet 11.5`, `fine resample 0.9`, `crossmul + RIFG 2.1` (`ISCE3_STAGES` in `nisar_coreg.py`).

The full run — `prepare`, then a crop per date, then a coreg unit per secondary, two at a time. Roughly 3 min per crop and 80 min per pair on this VM, so about 5–6 h for six secondaries; run it detached. `--detach` prints the session name and the log path, and refuses with exit 3 if a session of that name is already alive. Running one unit at a time is also how you would fan the work out to a batch system (crop first, then the coreg unit; the first coreg unit performs the set-up).

```bash
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml run --detach
tmux attach -t coreg_nepal_nisar_ascending_all
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml run --stage crop --dates 20260831
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml run --stage coreg --dates 20260831 --jobs 1
```

To rebuild a unit, remove that unit's outputs from the stack first — the module clears the ISCE3 scratch but never replaces an existing exported file:

```bash
S=/home/sharath/isce3/case_studies/nepal_nisar_ascending/coreg/RSLC_ref20260726_AHH_glof_bigger_aoi
rm -f $S/slc/20260620.slc $S/slc/20260620.hdr $S/ifg/RIFG_20260726_20260620_A_HH_1x1.h5 $S/offsets/20260620_*
python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml run --stage coreg --dates 20260620 --force
```

### When is a unit done, and what the exit codes mean

A unit is skipped as done when its manifest (`status/<unit>.json`) says `"status": "ok"` **and** every path under its `outputs` still exists at exactly the recorded byte size. A missing or resized output makes the unit run again; a partial output with no verified manifest is refused rather than overwritten, and needs `--force`. An RSLC unit is only marked `ok` when all four checks pass: ISCE3 exited 0; the RIFG exists and is non-empty; the coregistered secondary SLC exists and is non-empty; and it is byte-for-byte the same size as the reference SLC. Outputs are hard-linked into the stack directory before any scratch is deleted. A unit refuses to start when free disk is below its scratch estimate (reference pixels × 116.4 B) plus `run.min_free_gb` (30 GB).

| exit | meaning |
|---|---|
| `0` | everything requested succeeded (or was already done) |
| `1` | a unit failed — read `status/<unit>.json` (`checks`) and the log it names |
| `2` | config error: unknown key, missing `case`/`workdir`/`mode`, bad date, reference outside the selection, a `--dates` value that is not a unit, or a `params.json` mismatch |
| `3` | missing prerequisite: DEM not staged, crops not done, not enough free disk, or a tmux session of that name already running |

## nisar_timeseries.py — interferograms, unwrapping, corrections, time series

Takes a finished coregistered RSLC stack from `nisar_coreg.py` and produces, over one AOI: multilooked interferograms and coherence, snaphu-unwrapped phase, GUNW ionosphere / troposphere / solid-earth-tide screens, a MintPy LOS displacement time series and velocity, and geocoded GeoTIFFs. Derivations, conventions and the validation record: `docs/TIMESERIES_MODULE.md`. What it needs before it will run:

- **A coregistered stack in `mode: RSLC`.** Every selected date except the reference must have an `ok` coreg manifest and the reference `slc/<date>.slc` must exist, or the run exits `3`. A GSLC stack is rejected at config time (exit `2`).
- **An AOI KML.** The processing window is that polygon's bounding box grown by `aoi.margin_m` (2000 m).
- **NISAR L2 GUNWs** in `<workdir>/L2_GUNW/` (`corrections.gunw_dir`, relative to the coreg workdir) whose pairs *connect every selected date*, if any correction is enabled. Otherwise the run exits `3` before any stage starts — including when you asked for one unrelated `--stage`. Set the three `corrections` booleans to `false` to run without GUNWs.
- **Either conda environment.** The module re-execs each stage in the interpreter that has its libraries, so `isce3_env` and `insar_ts` both work as the launch environment. A `python` outside those two envs does not: the miniforge base interpreter has no `yaml`, and `/usr/bin/python3` has `yaml` but no `numpy`.

### CLI

```bash
conda activate isce3_env
cd /home/sharath/isce3/asc/nisar_workflows
python nisar_timeseries.py -c ts_configs/<case>.yaml {show|status|run} [options]
```

| subcommand | does |
|---|---|
| `show` | Prints the resolved plan and every parameter in effect; runs nothing, writes nothing. Dates, pairs with temporal baselines, AOI + margin, looks, which GUNWs were matched and whether they connect all dates, reference point, output directory, and whether the coregistration is ready. Run this first, always. |
| `status` | One line for the three whole-run stages (geometry, corrections, mintpy), then one line per pair with its ifg and unwrap state: `done`, `running`, `FAILED` or `-`, read from the manifests in `status/`. |
| `run` | Executes the stages. Default is all five in order; stops at the first stage that returns non-zero. `--through <stage>` runs from the first stage up to and including that one — `--through ifg` is the "interferogram only" run, and it needs no GUNWs because the corrections gate applies only when the corrections stage is selected. |
| `_stage` | Internal. The parent re-invokes itself with this to run one stage inside the stage's own interpreter. Do not call it by hand. |

| option | argument | default | meaning |
|---|---|---|---|
| `-c`, `--config` | path | **required** | Case config. Relative paths resolve against the current directory; the resolved absolute path is what the child stages are given. |
| `--stage` | `geometry` \| `ifg` \| `unwrap` \| `corrections` \| `mintpy` | all five, in that order | Run only this stage. |
| `--pairs` | one or more `YYYYMMDD_YYYYMMDD` | the whole resolved network | Restricts the **ifg and unwrap** units. A pair that is not in the resolved network is a config error (exit `2`). The other three stages ignore it, so combine it with `--stage`. |
| `--force` | flag | off | Redo units whose manifest says `ok` and whose outputs still match the recorded sizes. Without it those units are skipped. |
| `--dry-run` | flag | off | Per stage, prints the interpreter it would use and (for ifg/unwrap) the selected pairs. Writes nothing — not even `params.json`. |
| `--detach` | flag | off | Re-runs the same command inside a new tmux session named `ts_<case>_<name>_<stage-or-all>` and returns at once. Refuses with exit `3` if that session already exists. |
| `-h`, `--help` | flag | | Usage plus the module docstring. |

Exit codes, same as `nisar_coreg.py`: `0` ok, `1` a unit failed, `2` config error, `3` missing prerequisite.

### The case config

Only these seven keys are case keys; everything else in the file must be a section name from `ts_configs/defaults.yaml`, which it overrides. An unrecognised key is a config error, not a warning. Two cases ship, both carrying the relaxed `mintpy: {mask_dataset: "no", min_temporal_coherence: 0.3}` override: `ts_configs/nepal_nisar_ascending_pre_event.yaml` (20260620–20260819) and `..._post_event.yaml` (20260819–20260912). Copy one.

| key | required | `null` / omitted means | notes |
|---|---|---|---|
| `coreg_config` | yes | — | The `nisar_coreg.py` case config whose stack is consumed. A relative path resolves against the module directory. Must be `mode: RSLC`. |
| `name` | yes | — | Output directory name. `[A-Za-z0-9_-]+` only. |
| `aoi_kml` | no | the coregistration's AOI | Unwrap and time-series area. Its SHA-1 is locked in `params.json`, so editing the KML in place invalidates the output. |
| `start_date` | no | no lower bound | Selects among the dates already in the stack; nothing is fetched. |
| `end_date` | no | no upper bound | Fewer than two dates in range is a config error. |
| `reference_lalo` | no | automatic, chosen deterministically (see below) | `[lat, lon]` of the MintPy reference point. Not locked in `params.json`. |
| `tag` | no | no suffix | Output becomes `<name>_<tag>`. This is how you keep a second variant of a *locked* parameter instead of colliding with the first. |

**Output identity.** `params.json` beside the output locks `stack`, `reference`, `dates`, `pairs`, `aoi_kml_sha1`, `aoi`, `looks`, `unwrap`, `corrections` and `flatten_sign`. A run whose values differ is refused with the differing keys named — change `name` or add `tag`. The `mintpy` section and `reference_lalo` are deliberately *not* locked: that stage is a re-derivation from the locked pair products and is regenerated whole.

### The five stages

| stage | interpreter (`envs.*` in `ts_configs/defaults.yaml`; the parent only orchestrates) | reads | writes |
|---|---|---|---|
| `geometry` | `isce3_python` | stack `geometry/{lon,lat,hgt}.rdr`, the reference RSLC's radar grid and orbit, the AOI KML | `geometry/geometryRadar.h5` (MintPy layout, multilooked), `geometry/meta.json` (window, ml shape, wavelength, spacings, `nlooks_factor`, per-date B⊥ and starting ranges), `geometry/flatten/<date>_range_offset.f32` from isce3 `Geo2Rdr` |
| `ifg` | `ts_python` | the two `slc/<date>.slc` of each pair, the flattening offsets, `meta.json`; plus `ifg/RIFG_*.h5` from the stack for the gate | `pairs/<d1>_<d2>/ifg.int` and `coh.cor` (+ `.hdr`), ENVI, `looks`-multilooked |
| `unwrap` | `ts_python` | `ifg.int`, `coh.cor` | `pairs/<d1>_<d2>/unw.unw`, `conncomp.cc` (+ `.hdr`), `qa/unwrap_closure.json` |
| `corrections` | `ts_python` | every GUNW in `L2_GUNW/` whose two dates are both selected, `geometryRadar.h5` | `corrections/per_date_phase.h5` (per-date screens, radians, first date = 0), `qa/corrections.json` |
| `mintpy` | `ts_python` | all `unw.unw` / `coh.cor` / `conncomp.cc`, `geometryRadar.h5`, `per_date_phase.h5` | `mintpy/` (tree below), `export/*.tif`, `qa/mintpy.json` |

- Interferograms are `s1 * conj(s2)`, the convention MintPy's isce loader assumes and the one the GUNW screens use, so the screens are **subtracted**; MintPy's LOS displacement is positive toward the satellite. Flattening subtracts both the geo2rdr range offset *and* the constant difference in starting slant range between the two crops.
- `mintpy` is skipped with a message, not an error, when the network has fewer than two pairs. Re-running it moves any existing `mintpy/` to `mintpy_replaced_<UTC stamp>/` first, so it never merges two runs.
- **Reference point.** MintPy's automatic `maxCoherence` picks a random pixel above the threshold, so reruns differ. With `reference_lalo: null` the module instead takes the highest 9×9-averaged coherence among pixels that are inside a snaphu component in *every* interferogram, and passes it to MintPy as `--row/--col`. For a physically meaningful series, give it a point you know is stable.

### Parameters — `ts_configs/defaults.yaml`

Repeat a section in the case config to override it. Values are the validated ones; ask before changing any of them.

| group | keys and defaults | change freely | do not change without measuring |
|---|---|---|---|
| `aoi` | `margin_m: 2000` | Grow it if the deformation runs past the KML. It is locked, so a change needs a new `name`/`tag`. | Shrinking it below the unwrapping's needs: snaphu wants context around the signal. |
| `looks` | `azimuth: 9`, `range: 8` (≈ 40 × 39 m) | — | Everything. 9×8 is validated against ISCE3's own 9×8 crossmul (phase 0.0024 rad) and is the grid the ionosphere unwrap was checked on; `nlooks_factor` 0.619 gives 44.57 effective looks, which is what snaphu is calibrated with here. Locked. |
| `network` | `max_connections: 3`, `max_temporal_baseline_days: null`, `pairs: []` | This is the normal knob, but the resolved pair list is locked in `params.json`, so changing it on an existing output is refused (exit `2`, differing key `pairs`) and needs a new `name`/`tag`. `max_connections` pairs each date with its next N; `pairs: ["YYYYMMDD_YYYYMMDD", …]` overrides both other keys. | Any network that does not connect all dates is refused at config time (rank check on the design matrix). More connections cost one interferogram plus one snaphu run each. |
| `unwrap` | `cost: smooth`, `init: mcf`, `ntiles: [3, 3]`, `tile_overlap: [150, 150]`, `nproc: 8`, `single_tile_reoptimize: false`, `regrow_conncomps: true`, `min_conncomp_frac: 0.01`, `nlooks: null` | Nothing here is free: the **whole section is locked**, so even raising `nproc` for a bigger machine trips the params gate and needs a `tag`. | `ntiles`/`tile_overlap` — 3×3 with 150-pixel overlap was checked against ISCE3's RUNW (snaphu 4×4, overlap 256): same cycle on all but 0.001 % of 1.63 M pixels. `nlooks: null` derives 44.57 from the grid; a hand-set value miscalibrates snaphu's cost function silently. |
| `corrections` | `gunw_dir: L2_GUNW`, `tier_preference: [PR, UR]`, `ionosphere/troposphere/solid_earth_tides: true` | Turning a correction off to process without GUNWs. Locked, so it needs a `tag` to keep both. | `tier_preference` — PR before UR is deliberate; UR products carry no troposphere cubes. Dropping a correction you do have measurably widens the spread (15.2 → 22.3 mm on the validated 12-day pair). |
| `mintpy` | `reference_min_coherence: 0.85`, `unwrap_error_method: "no"`, `weight_func: var`, `mask_dataset: connectComponent`, `mask_threshold: 0.5`, `min_temporal_coherence: 0.7`, `dem_error: false`, `velocity_polynomial: 1` | **All of it.** This section is not locked; iterate with `run --stage mintpy --force` and compare. `mask_dataset: "no"` with `min_temporal_coherence: 0.3` is the "relaxed" variant both shipped cases now carry. | Know what `mask_dataset: connectComponent` does before trusting a result: snaphu puts ice in component 0, so the default mask silently removes the glacier from every interferogram (9 of 670 pixels inverted, here). `dem_error: true` needs more than five dates to separate DEM error from deformation. A velocity across a step event is not physical whatever the polynomial says. |
| `qa` | `max_rifg_phase_diff_rad: 0.2` | — | This is the gate that catches wrong flattening. Measured agreement is 0.002–0.003 rad, so 0.2 is already 60× slack. Raising it to make a failing pair pass hides a geometry bug. It sits in `qa`, which is not locked — which is exactly why it must not be edited casually. |
| `run` | `block_lines_ml: 100`, `jobs: 2`, `mintpy_workers: 4` | All three. Resource knobs only, not locked, no effect on the numbers. `jobs` is interferograms in parallel (unwrapping is sequential because snaphu already parallelises its tiles over `unwrap.nproc`). | `mintpy_workers` was checked against serial: identical to 1.3e-7 m, inversion 11 min → 2.5 min. Raising it past the core count only adds contention. |

### Built-in checks

1. **RIFG comparison gate (ifg stage).** For every pair that includes the stack reference, ISCE3's own `RIFG_*.h5` is multilooked over the identical window (conjugated when the reference is the secondary) and compared with ours. The unit passes only if more than 1000 pixels have coherence > 0.5 *and* the median `|Δφ|` over them is below `qa.max_rifg_phase_diff_rad`. A failure is written as `status: failed` with an empty `outputs` map, so it is not treated as done, and the stage exits `1`. This is the check that caught the crop start-range flattening bug; keep it.
2. **Closure census (end of every unwrap stage).** For each date triplet whose three pairs are all unwrapped and share at least 1000 pixels in a non-zero connected component, it computes `unw12 + unw23 − unw13`, removes the median, and reports the fraction of pixels off by whole 2π cycles plus the residual std, into `qa/unwrap_closure.json` and the log. A report, not a gate.
3. **GUNW layer-availability drop rule (corrections stage).** Before sampling, every GUNW in the network is probed for the exact datasets each requested correction needs. **A layer missing from any one GUNW is dropped for the whole run**, logged, and recorded under `dropped` in `status/corrections.json` — a series inverted from screens present on some edges and absent on others is not a consistent field. Real cost: the UR product for 31 Aug – 12 Sep has no troposphere cubes, so the post-event run loses the troposphere correction it does have for 19–31 Aug. If every requested layer is missing from at least one GUNW of the network, the stage records `kinds: []` and succeeds; MintPy then subtracts nothing.

### Worked examples

`show` is free, writes nothing, and prints the plan the run will follow. **Pre-event, all five stages, detached** takes about 12 min end to end on this VM (measured: 3 min geometry, 3 min interferograms, 3.5 min unwrapping, 20 s corrections, 2.5 min MintPy). `--detach` prints the session name and the log path:

```bash
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml show
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml run --dry-run
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml run --detach
tmux attach -t ts_nepal_nisar_ascending_pre_event_all
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml status
tail -f /home/sharath/isce3/case_studies/nepal_nisar_ascending/timeseries/RSLC_ref20260726_AHH_glof_bigger_aoi/pre_event/logs/all_*.detached.log
# post-event: 3 dates, 3 pairs; 0819_0831 and 0819_0912 span the 26 Aug event, 0831_0912 does not
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_post_event.yaml show
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_post_event.yaml run --detach
# finished stages are skipped, so a stage only re-runs with --force; --pairs restricts ifg and unwrap
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml run --stage mintpy --force
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_post_event.yaml run --stage ifg --pairs 20260819_20260831 --force
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_post_event.yaml run --stage unwrap --pairs 20260819_20260831 --force
```

Three traps. `export/*.tif` are named from `name` alone, so a re-run overwrites the previous GeoTIFFs; only the temporal-coherence mask carries its threshold in the filename, which is why `export/` in the shipped pre-event case holds both `pre_event_mask_temporal_coherence_0.7.tif` (strict) and `pre_event_mask_temporal_coherence_0.3.tif` (relaxed) while the velocity and displacement TIFFs are the relaxed run's only. A re-unwrapped pair leaves `mintpy/` stale until you re-run that stage too. And `--pairs` without `--stage` still runs geometry, corrections and MintPy over the whole network — MintPy will exit `3` listing the pairs it is still missing.

### Output tree

```
<workdir>/timeseries/<coreg stack id>/<name>[_<tag>]/
├── params.json       the locked parameter set; a differing run is refused
├── geometry/         geometryRadar.h5, meta.json, flatten/<date>_range_offset.f32
├── pairs/<d1>_<d2>/  ifg.int, coh.cor, unw.unw, conncomp.cc, each with its .hdr (ENVI)
├── corrections/      per_date_phase.h5 — per-date ionosphere / troposphere / solid_earth_tides screens, radians
├── mintpy/           inputs/ (ifgramStack.h5, geometryRadar.h5, gunw_SET.h5, gunw_ion.h5, gunw_gunwTropo.h5), mintpy.cfg (generated from the mintpy: section), mintpy_commands.log, timeseries.h5 → timeseries_SET.h5 → _ion.h5 → _ion_gunwTropo.h5 (corrections subtracted in that order), velocity.h5, velocity_uncorrected.h5, temporalCoherence.h5, maskTempCoh.h5, avgSpatialCoh.h5, numInvIfgram.h5, geo/
├── export/           GeoTIFFs, EPSG:4326, ≈ 37 × 39 m: <name>_los_velocity_m_per_yr.tif, _los_velocity_std_m_per_yr.tif, _los_velocity_uncorrected_m_per_yr.tif, _temporal_coherence.tif, _mask_temporal_coherence_<threshold>.tif, _los_displacement_m_<first>_<last>.tif
├── qa/               unwrap_closure.json, corrections.json, mintpy.json
├── status/           geometry.json, corrections.json, mintpy.json, ifg_<pair>.json, unwrap_<pair>.json
└── logs/             run_<stage|all>_<stamp>.log, <stage>_<stamp>.console.log, <what>_<stamp>.detached.log
```

The module writes exactly one MintPy directory, `mintpy/`. **`mintpy_relaxed/` in the two shipped case studies is a hand-renamed copy**: the strict inversion was run first, then the relaxed one (`mask_dataset: "no"`, `min_temporal_coherence: 0.3`), and the two directories were renamed afterwards so both survive. Because the case configs now carry the relaxed overrides, `run --stage mintpy --force` today reproduces the *relaxed* variant into `mintpy/` and pushes the current strict one to `mintpy_replaced_<stamp>/`. `status/mintpy.json` and `qa/mintpy.json` describe the relaxed run, the later of the two.

## run_track_r.py and run_track_g.py — the ISCE3 drivers underneath

`nisar_coreg.py` is a wrapper; these two step runners hold the ISCE3 knowledge. `run_track_r.py` takes NISAR L1 RSLCs to a coregistered RIFG in **radar** coordinates (rdr2geo → geo2rdr → resample → dense offsets → crossmul); `run_track_g.py` geocodes them to L2 GSLCs on a pinned, shared **geo**grid, then interferogram → water mask → unwrap → overlay. The module picks one by the case config's `mode:` (`RSLC` → Track R, `GSLC` → Track G), writes a generated driver config, and calls the driver one step at a time. `nisar_timeseries.py` does **not** use these drivers. Both run start to finish in `isce3_env`; neither needs `insar_ts`. Every command below runs after `conda activate isce3_env`, from `/home/sharath/isce3/asc/nisar_workflows`.

### When to call a driver directly instead of the module

Use `nisar_coreg.py` for anything routine — it does the bookkeeping (per-unit manifests, `params.json`, skip-if-verified, scratch pruning, tmux) that the drivers do not. Go to a driver directly when:

- **You want one step, not a stage.** The module's `coreg` stage bundles ingest + DEM + runconfig + insar. `--only runconfig` gets you the disk bill and the validated runconfigs and nothing else; adding `--dry-run` gets the bill alone — it prints `would write ...` per pair and renders nothing.
- **You are debugging a crash inside the ISCE3 chain.** `--start-step insar --force` resumes with a clean scratch tree; the module would re-derive the whole stack identity first.
- **The config is one the modules do not generate.** The interferogram, the water mask, unwrapping and the folium overlay live only in `configs/` and are reachable only from a driver. `nisar_coreg.py` generates a Track G config that covers `ingest`, `dem`, `gslc` and `gridgate` (it writes the `geogrid:` block itself) and emits nothing past the grid gate.
- **You are overriding a knob per invocation.** `--looks`, `--frequency`, `--product-type`, `--dem-source` are CLI-only. The modules take the frequency (`inputs.frequency`) and the DEM source (`dem.source`) from `coreg_configs/defaults.yaml` and refuse a stack whose parameters changed; looks and product type are not exposed there at all — `nisar_coreg.py` hardcodes 1x1 looks and `RIFG`.

**Step selectors (both drivers).** `--only`, `--start-step` and `--stop-step` all take a **number, an exact step name, a stage id, or a unique substring of the name**: `--only insar`, `--only 4` and `--only r2` are the same step. Selectors are de-duplicated and re-sorted into pipeline order, so `--only insar ingest` runs ingest first. An ambiguous or unmatched selector is a hard error (exit 2), so a typo never silently selects the wrong stage: an unmatched selector prints the whole step table, an ambiguous one lists the steps it matched and says `Be more specific.` (`--only g` on Track G matches `ingest`, `gslc`, `gridgate` and `igram`.) Exit codes: `0` ok, `1` a step failed, `2` config or step-selector error. Neither driver needs a config to print its step table: `python run_track_r.py --list-steps`, `python run_track_g.py --list-steps`.

### run_track_r.py

Steps: **1** `ingest` [ A] read RSLC metadata, write `stack.json` · **2** `dem` [ B] stage a WGS84-ellipsoidal DEM covering the AOI · **3** `runconfig` [R1] render + schema-validate one insar runconfig per pair, disk gate · **4** `insar` [R2] rdr2geo → geo2rdr → resample → dense offsets → crossmul · **5** `qa` [R3] decimated-read coherence summary per pair. Steps 1–2 are shared with Track G and write the same `stack.json`, so a case already ingested for Track G resumes straight into step 3.

| flag | what it does | default |
|---|---|---|
| `--config YAML`, `-c` | run configuration; Track R reads its `track_r:` block | required unless `--list-steps` |
| `--only STEP [STEP ...]` | run ONLY these steps | all five, in order |
| `--start-step STEP` | resume here; earlier outputs assumed present on disk | `ingest` |
| `--stop-step STEP` | stop after this step, inclusive | `qa` |
| `--force` | recompute even when outputs look complete; also clears the pair's scratch tree before re-running the InSAR chain | off |
| `--dry-run` | report what each step would do, including the disk bill, writing nothing | off |
| `--list-steps` | print the step table and exit | — |
| `--log-file PATH` | append here instead of `<out_root>/logs/track_r_<stamp>.log` | derived |
| `--quiet` | log to the file only | console + file |
| `--frequency F` | override `track_r.frequency` (`A` or `B`) | config; dataclass `B` |
| `--polarization P` | override `track_r.polarization` | config; dataclass `HH` |
| `--looks AZ RG` | override the looks for the **selected** frequency, azimuth then range. Applied at crossmul, so it composes with `--frequency` and leaves the other band's entry untouched | config; dataclass `A: 5x5`, `B: 9x1` |
| `--product-type T` | override `track_r.product_type`. Help lists `RIFG \| RUNW \| GUNW \| RIFG_RUNW_GUNW`; the validator also accepts `ROFF`, `GOFF`, `ROFF_GOFF` | config; dataclass `RIFG` |
| `--pair REF SEC` | process only this pair; repeatable. Overrides `track_r.pairs` | config `track_r.pairs`; empty = every consecutive date pair |
| `--no-disk-gate` | warn instead of refusing when scratch would not fit | gate enforced (`track_r.enforce_disk_gate: true`) |

```bash
STACK=/home/sharath/isce3/case_studies/nepal_nisar_ascending/coreg/RSLC_ref20260726_AHH_glof_bigger_aoi
python run_track_r.py --config $STACK/track_r.yaml --only runconfig --dry-run
python run_track_r.py --config $STACK/track_r.yaml --only insar --pair 20260726 20260714 --no-disk-gate --log-file $STACK/logs/insar_20260726_20260714.log
```

Track R's step selection ignores the config's `steps:` toggles entirely — its on/off switch is `track_r.enabled`, and `enabled: false` exits 0 with "nothing to do"; `run_insar` skips any pair whose RIFG already exists unless `--force` is given. Above, the first command prints the disk bill and writes nothing — a dry run renders and validates no runconfig — reporting per pair `scratch 27.9 GiB + RIFG 12303 x 22464 (276 Mpx) 3.1 GiB = 31.0 GiB`, then `disk needed 186.0 GiB ... across 6 pair(s)`. The second is what `nisar_coreg.py` issues per unit; against this stack it is a no-op, since the RIFG exists and the pair is skipped. Adding `--force` would redo it and wipe its scratch first; do not do that to an archived product.

**The disk gate** lives in Track R step 3 (`runconfig`) only. Before writing anything it estimates, per pair, the ISCE3 scratch (`rdr2geo` + `geo2rdr` + `rubbersheet` + `dense_offsets` + coarse and fine `resample` + `crossmul`'s unpacked reference SLC + `baseline`: a measured 104.4 B per reference pixel, plus 4 B per interferogram pixel for the ifgram DEM) plus the delivered RIFG, **sums that over every pair in the pair list**, and compares it with free space on `out_root` (`case_dir` only when `out_root: null`; the module-generated config points it at `<stack>/isce3`). Scratch already on disk for the same pair+tag is credited, since a re-run overwrites those rasters in place. Two thresholds, and only one stops you: `free < needed` **refuses** (`StepFailed`, exit 1) when `track_r.enforce_disk_gate` is true and warns otherwise — looks do not help, they are applied at crossmul, after every coregistration stage has already written at full radar-grid resolution; `free - needed < track_r.min_free_gb` is a **warning only**, dataclass default 60.0 GB, set to 30.0 by the module-generated config from `run.min_free_gb` in `coreg_configs/defaults.yaml`. `--no-disk-gate` is legitimate when the summed figure is not the real peak. That is the normal case under `nisar_coreg.py`: it runs `run.jobs` pairs at a time (default 2) and prunes each pair's scratch once its outputs are verified and hard-linked out (`run.prune_scratch: true`), so occupancy peaks at a couple of pairs, not at the sum of all of them. The module therefore passes `--no-disk-gate` on both the `runconfig` and the `insar` calls it issues, and enforces its own per-unit check instead — it will not start a unit unless `reference pixels x 116.4 B + run.min_free_gb` is free. It is also legitimate when scratch lives on a different filesystem from `case_dir`, which the gate does not model. It is not legitimate as a way past a genuine shortfall: ISCE3 will fail hours in, mid-`rdr2geo`.

### run_track_g.py

Steps: **1** `ingest` [ A] read RSLC metadata, write `stack.json` + PINNED geogrid · **2** `dem` [ B] stage a WGS84-ellipsoidal DEM covering the AOI · **3** `gslc` [G1] render + validate runconfigs, geocode each date to GSLC · **4** `gridgate` [G2] assert every GSLC is pixel-aligned, fail loudly if not · **5** `qa` [QA] decimated-read quicklooks (never loads a full raster) · **6** `igram` [G3] interferogram + coherence + per-date amplitude · **7** `watermask` [ W] water mask via orthometric DEM (NASADEM route is broken) · **8** `unwrap` [G4] Goldstein filter → phase-sigma coh → water mask → SNAPHU · **9** `overlay` [G5] folium HTML: amplitude/phase/coherence over satellite tiles. `igram` sits deliberately ahead of `watermask`: the mask is built on an existing product's grid so it is pixel-aligned by construction, and the interferogram is that product.

| flag | what it does | default |
|---|---|---|
| `--config YAML`, `-c` | run configuration | required unless `--list-steps` |
| `--only STEP [STEP ...]` | run ONLY these steps. **Bypasses the config's `steps:` toggles** | all nine, in order |
| `--start-step STEP` | resume here; earlier steps skipped, outputs assumed present | `ingest` |
| `--stop-step STEP` | stop after this step, inclusive | `overlay` |
| `--force` | recompute even when a stage's outputs already look complete | off |
| `--dry-run` | report what each step would do, including the exact commands, writing nothing | off |
| `--list-steps` | print the step table and exit | — |
| `--log-file PATH` | append here instead of `<out_root>/logs/track_g_<timestamp>.log` | derived |
| `--quiet` | log to the file only, not the console | console + file |
| `--frequencies F [F ...]` | override `frequencies` | config; dataclass `[B]` |
| `--polarizations P [P ...]` | override `polarizations` | config; dataclass `[HH]` |
| `--dates YYYYMMDD [...]` | geocode only these dates. Sets `gslc.dates`, so it scopes **step 3 only**; per-date and independent, so more can be added later without redoing these, and the pinned geogrid is unaffected | config `gslc.dates`; empty = every date in `stack.json` |
| `--igram-freq F` | override `igram.freq` (`A` or `B`). Needed to form the two bands separately for the ionosphere solve | config; null → `frequencies[0]` |
| `--looks LY LX` | override `igram.looks_y`/`looks_x`. The looks are part of the output filename, so a different setting never overwrites an existing product | config; dataclass `16 x 2` |
| `--dem-source SRC` | override `dem.source` — `NISAR`, `COP`, `NASA` or `3DEP` | config; dataclass `NISAR` |

```bash
python run_track_g.py --config configs/nepal_glof.yaml --only ingest --dry-run
python run_track_g.py --config configs/nepal_glof.yaml --only ingest dem
```

Track G has no `--pair`, no `--product-type` and **no `--no-disk-gate`** — Track R's is the only gate with a bypass; Track G's `gslc` step still refuses (`StepFailed`) when its forecast output exceeds 90% of the free space on `out_root`, and nothing on the CLI overrides that. Unlike Track R, a range run (no `--only`) is filtered by the config's `steps:` block, and disabled steps are logged as `disabled by config steps: -> [...]`. Above, the dry run writes nothing; against `nepal_glof`, whose `stack.json` already exists, it reports `stack.json already present with 3 granule(s) ... SKIP (use --force to regenerate)` and exits 0. The second writes `stack.json` + the pinned geogrid, then stages the DEM (~430 MB, needs urs.earthdata.nasa.gov credentials in `~/.netrc`). Its logfile lands in `/home/sharath/isce3/case_studies/nepal_glof/logs/` because that config sets `out_root: null`, which resolves `out_root` to `case_dir`.

### The science config in `configs/`

`configs/*.yaml` is the **hand-written** config: one file carrying both `track_r:` and Track G's top-level blocks (`frequencies`, `geogrid`, `igram`, `unwrap`, `steps`, `dem`, …), with the reasoning for every value in comments beside it. Each driver reads only the parts it needs. `ingest` auto-discovers `NISAR_L1_*RSLC*.h5` under `<case_dir>/L1_RSLC/` when `granules: []`, and derives track, frame, orbit direction, look side, dates, EPSG and the pinned geogrid from the products; for a new case only `case_dir`, `geogrid.aoi_lonlat` and `frequencies`/`looks` normally need changing.

| file | what it is |
|---|---|
| `_template.yaml` | the annotated starting point; copy it for a new case |
| `nepal_glof.yaml` | Study 1, the four-workflow comparison |
| `nepal_glof_aoi.yaml`, `nepal_glof_aoi_v2.yaml` | AOI-cropped variants; `_v2` holds the benchmark science settings |
| `nepal_nisar_ascending.yaml` | Study 2's science settings, built from `_aoi_v2` |
| `venezuela_t162_asc.yaml` | the original Track G case. Its `case_dir` points at `/home/sharath/Desktop/work/...`, which does not exist on this machine — reference material, not runnable here |

**Do not hand-edit a generated config.** `nisar_coreg.py` writes its own driver config at `<workdir>/coreg/<stack>/track_r.yaml` (or `track_g.yaml`), headed `# generated by nisar_coreg.py (coreg RSLC) -- edit the case config or coreg_configs/defaults.yaml, not this file`. It is rewritten on every set-up pass and validated with the driver's own loader, so edits are lost and the stack's `params.json` no longer describes what ran. Change `coreg_configs/<case>.yaml` or `coreg_configs/defaults.yaml` instead. **`configs/nepal_glof.yaml` no longer reproduces the archived comparison.** Its `igram:` block now carries `looks_y: 8`, `looks_x: 8` (frequency A, ~37.8 m range x 35.6 m azimuth, ILN 44.3); the four-workflow study archived in `gs://s1-slc/nisar_workflow/nepal_glof/` ran this same file at `looks 1 x 1` with `coherence_window: 3`, and that history survives only as the `# HISTORY --` comment immediately below the looks — restore both values to reproduce it. The 8x8 setting is frequency-A specific: freq B's native cell is 37.79 x 4.456 m, so 8x8 there gives 302 x 36 m — use roughly 1:8 instead. `track_r.looks` in the same file is unrelated and still `A: 1x1`, `B: 9x1`.

## The four workflows this pipeline came from

Before the two modules existed, four workflows were built and compared on one pair (20260714 × 20260726, track 98
frame 16, HH) to decide how the production chain should be shaped: RSLC and GSLC, each on the full tile and cropped to
an AOI first. The recipes below are what it takes to run each one today; the evidence behind them, with per-stage
measurements and a problems log, is in `docs/WF1_RSLC_FULL_TILE.md`, `docs/WF2_GSLC_FULL_TILE.md`,
`docs/WF3_RSLC_CROPPED.md`, `docs/WF4_GSLC_CROPPED.md` and `docs/COMPARISON.md`.

If you only want a deformation time series over an AOI, you do not need any of them — use the two modules above,
which are what the comparison concluded with. Read this part when you need a benchmark-quality single pair, a
geocoded product, or to understand why the chain is shaped the way it is.

### WF1 — RSLC on the full tile (the quality benchmark)

ISCE3's `nisar.workflows.insar` on two **uncropped** NISAR L1 RSLCs, products left in the reference's radar geometry. Driver: `run_track_r.py`. Pair **20260714 × 20260726**, track 98, frame 16, ascending, frequency **A**, polarization
**HH**, crossmul **1×1**; freq-A grid 53200 × 54244 (2886 Mpx). Quick looks belong in WF3. Detail: `docs/WF1_RSLC_FULL_TILE.md`.

#### Config and preconditions

- `configs/nepal_glof.yaml`, `track_r:` (line 476); rendered runconfig consumed: `case_studies/nepal_glof/cfg/insar_20260714_20260726_A_HH_1x1.yaml`. It still says `frequency: B` (482) and keys looks `A: {azimuth: 1, range: 1}`, `B:
  {azimuth: 9, range: 1}` (511–512), so **`--frequency A --looks 1 1` on the command line is mandatory** — without it you get the freq-B leg.
- `igram.looks_y / looks_x` are `8 / 8` (273–274) with `coherence_window: 5` (264). A WF1 run never reads that block, but the same file drives **WF2**, whose interferogram stage does (WF3/WF4 use `nepal_glof_aoi_v2.yaml`); the
  comparison ran it at **1×1 with `coherence_window: 3`**, so set those back by hand.
- `unwrap_crossmul_path` is pinned to the full-tile RIFG (616) and the driver emits it verbatim as `phase_unwrap.crossmul_path`. `nisar.workflows.insar` ignores that key (`insar.py:109-110`), so it is harmless on the integrated path
  and matters only if you resume via the standalone `python -m nisar.workflows.unwrap`; null it when cloning this config.
- **Disk gate** `min_free_gb: 60.0`, `enforce_disk_gate: true` (520, 522); `--only runconfig` bills the 104.4 B/px scratch model (per-stage table in the doc). On the old disk it billed **312.9 GiB** against **341.6 GiB** free and did
  not count the output product (WF1-05); a later launch there was refused at 323.7 GiB needed vs 316.8 GiB free. The disk is now 700 GB, so treat the bill as a floor; `--no-disk-gate` downgrades the refusal to a warning.
- **RAM ≥ 31 GB** for freq A 1×1 *with* the patches (it ran on a 31 GB / 8-core VM; the 3.9 GB / 2-core VM was killed at 27h24m).
- **ISCE3 0.25.12 with four overlays** — `insar/utils.py`, `workflows/unwrap.py`, `workflows/h5_prep.py`, `workflows/resample_slc_v2.py` (isce3#372); stock OOMs or raises `KeyError`. `tools/apply_patches.py` installs each only against
  byte-identical upstream, keeps `<name>.orig`, is re-runnable.

#### Run it

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env && cd /home/sharath/isce3/asc/nisar_workflows
python tools/apply_patches.py --check        # must report every overlay already applied
CFG=configs/nepal_glof.yaml
python run_track_r.py -c $CFG --only ingest
python run_track_r.py -c $CFG --only dem
python run_track_r.py -c $CFG --only runconfig --frequency A --looks 1 1   # render + validate + disk bill
python run_track_r.py -c $CFG --dry-run --frequency A --looks 1 1          # optional: what would run
python run_track_r.py -c $CFG --only insar --frequency A --looks 1 1       # the long stage — tmux this
python run_track_r.py -c $CFG --only qa
C=/home/sharath/isce3/case_studies/nepal_glof; S=$C/scratch/trackR/20260714_20260726_A_HH_1x1   # full-res coherence, which ISCE3 does not produce
python -u tools/slc_coherence.py --ref $S/crossmul/freqA/HH/reference.slc \
   --sec $S/fine_resample_slc/freqA/HH/coregistered_secondary.slc --win 3 --out $C/pairs/20260714_20260726/trackR/coherence_A_HH_win3.tif
```

`--looks` is **AZIMUTH then RANGE**; `--list-steps` prints the step table without a config. Other driver flags: `--start-step`, `--stop-step`, `--polarization`, `--product-type`, `--pair REF SEC` (repeatable), `--log-file`, `--quiet`,
`--force`, `--no-disk-gate`. With `product_type: RUNW` and `phase_unwrap` at 9×8, `--only insar` runs coregistration → RIFG → unwrap → ionosphere in one pass; run it in tmux (wrapper templates in `docs/OPERATIONS_AND_LESSONS.md`
section 5). Coherence at 1×1 is a constant 1.0 (`Crossmul.cpp:378-388`), which is why the last command exists.

- **Never `--force` to resume**: it `shutil.rmtree`s the pair's whole scratch (`nisar_wf/trackr.py:684-688`), though the CLI epilog suggests `--start-step insar --force`. ISCE3 cannot resume anyway — `persistence.py:65` resets
  `success_msg_found` while scanning the log backwards, and because the driver invokes `python -m nisar.workflows.insar` without ISCE3's own `-l/--log-file` argument, `runconfig.py:93-104` sets `restart` unconditionally (the driver's
  `--log-file` controls the wrapper log only). Assume every restart re-runs from rdr2geo, and delete by hand any product a SIGKILL left structurally corrupt — without `--force` it is skipped as finished.
- **Set `snaphu.nlooks` explicitly when crossmul and unwrap looks differ**: left null, `unwrap.py:563` derives it from the crossmul grid — `ValueError: nlooks must be >= 1, instead got 0.6189996726516942`, **8h34m into the run**. For
  9×8 the value is **44.57**.
- **snaphu `single_tile_reoptimize` and `regrow_conncomps` must be `false` when tiled** (69 GB at 1×1). Peak is per-tile × nproc at 385 B/px: on the 1×1 grid [16,16] × nproc 8 would be 40.2 GB and [32,32] is 1.85 GB/proc → 14.8 GB,
  which is why the 1×1 pass used [32,32]; the 40.2 GB WF1-19 records for [4,4] × 8 is on the 13300 × 13561 4×4 grid. The 9×8 pass at [4,4]/nproc 8 is ~1.4 GB/tile.

**Measured cost.** 8-core wall (31 GB / 8-core VM): rdr2geo **1h39m** and geo2rdr **6.6 min** are stages *inside* the **8h34m** integrated run through RIFG — do not add them on top. Unwrap 1×1 tiles [32,32] **12.84 h**; unwrap 9×8
**~23 min**; 3×3 coherence **~10 min**. The ionosphere journal timer of **27 min** covers solve, filter and write only — the freq-B stages it also runs add 2309 s, and the doc's corrected end-to-end figure for the ionosphere layer is
**7h38m–8h05m** (WF1-44). The doc gives no total and the rows are not all disjoint: budget a full day of 8-core wall time, plus the ~32 h of failed attempts excluded from the table. Scratch: 2886 Mpx → **~301 GB** by the 104.4 B/px
model, plus **11.5 GB** for `RIFG_ifgram_dem`; the tree measured **359 GB** while snaphu ran (WF1-30), and 46 GB of it (coarse_resample + dense_offsets) was deleted during the run.

**Outputs** under `case_studies/nepal_glof/`, `<tag>` = `20260714_20260726_A_HH_1x1`: `scratch/trackR/<tag>/RIFG.h5` (**28.57 GB**); `pairs/20260714_20260726/trackR/RUNW_<tag>.h5` (**17.38 GB**, `ionospherePhaseScreen` all zeros —
ionosphere was disabled on that pass); `RUNW_<tag>_unw9x8.h5` (**0.61 GB**, 5911 × 6780, carries the screen); `coherence_A_HH_win3.tif` (**10.13 GB**); `logs/insar_<tag>.log` (ISCE3 journal, appended across runs).

### WF2 — GSLC on the full tile

Track G, role `G_full`. Each RSLC date is geocoded **independently** onto one pinned UTM lattice by `nisar.workflows.gslc`; interferogram, coherence and amplitudes are then formed in map coordinates as pixel-wise products of the two GSLCs (`nisar_wf/igram.py`). There is no data-driven coregistration anywhere in this path, and ISCE3's GSLC path has no ionosphere, troposphere or unwrapping — split-spectrum ionosphere is a port, `tools/gslc_ionosphere.py`, run outside the driver. Case as run: pair **20260714 × 20260726**, frequencies A and B both at **5 m × 5 m**, EPSG:32645, HH. Rationale, defect ledger and full measurements: `docs/WF2_GSLC_FULL_TILE.md`.

#### Config

| file | status |
|---|---|
| `configs/nepal_glof.yaml` | the config WF2 used, and still the Track G full-tile config |
| `configs/nepal_glof_aoi.yaml`, `configs/nepal_glof_aoi_v2.yaml` | the cropped variants (WF3/WF4); `_v2` is the one actually used |
| `configs/nepal_nisar_ascending.yaml` | the later event study, not this comparison |
| `configs/venezuela_t162_asc.yaml`, `configs/_template.yaml` | the ancestor case and the clean template |

- To reproduce the comparison, revert two keys in `configs/nepal_glof.yaml`: `igram.looks_y: 8` / `igram.looks_x: 8` (`:273-274`) and `igram.coherence_window: 5` (`:264`) back to **1 × 1 looks with `coherence_window: 3`** (history in the comment at `:276-282`) — at 1 × 1 the look box has fewer samples than the window, so `igram.py` falls back to a **sliding** window, and 5 gives a 5 × 5 sliding window (bias floor 0.177) instead of the 3 × 3 (floor 0.295) that matches `tools/slc_coherence.py`; `--looks 1 1` does **not** override the window. Also `frequencies: [A]` (`:51`): the geogrid is pinned **per frequency**, so ingest must be told `A B` or the freq-B run fails instantly with "stack.json has no pinned geogrid for frequency B". Two traps the config still carries (WF2-01, open): `watermask.ocean_probe` (`:327`) is a Caribbean box in **EPSG:32619** while the grid is 32645, and `unwrap.ntiles: [10, 17]` (`:390`) was sized for a 662 Mpx grid — harmless only because `watermask`, `unwrap` and `overlay` never ran on Track G, so fix them before running those three steps.

#### Run

- Ran on this case: `ingest`, `dem`, `gslc`, `qa`, `igram`, plus `gridgate` for **freq A only** on the full tile; `watermask`, `unwrap` (full-resolution Track G unwrapping, 3805 Mpx) and `overlay` have **never** run here. Preconditions: WF2 needs no overlays of its own, but run `conda run -n isce3_env python tools/apply_patches.py --check` anyway since the environment is shared with Track R; `nisar_wf/gslc.py:394` forecasts uncompressed output for all requested bands × dates and raises `StepFailed` above 90 % of free space (a warning on a dry run), while `Config.validate` warns below 10 GiB free; every date must share track/frame/direction (`stack.json`) and the DEM must cover the pinned lattice. Zero Doppler does not apply — WF2 defines no radar-domain window (`geogrid.aoi_lonlat: null`).

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env && cd /home/sharath/isce3/asc/nisar_workflows && CFG=configs/nepal_glof.yaml
python run_track_g.py -c $CFG --dry-run                                     # plan and exact commands, writes nothing
python run_track_g.py -c $CFG --only ingest --frequencies A B --force       # pin the geogrid for BOTH bands
python run_track_g.py -c $CFG --only dem
for F in A B; do python run_track_g.py -c $CFG --only gslc --frequencies $F --dates 20260714 20260726; done
python run_track_g.py -c $CFG --only gridgate --frequencies A B --dates 20260714 20260726 && python run_track_g.py -c $CFG --only qa --frequencies A B --dates 20260714 20260726
python run_track_g.py -c $CFG --only igram --frequencies A B --igram-freq A --looks 1 1 --dates 20260714 20260726
python run_track_g.py -c $CFG --only igram --frequencies A B --igram-freq A --looks 8 8 --dates 20260714 20260726
python run_track_g.py -c $CFG --only igram --frequencies A B --igram-freq B --looks 8 8 --dates 20260714 20260726
python -u tools/gslc_ionosphere.py --pair-dir /home/sharath/isce3/case_studies/nepal_glof/pairs/20260714_20260726/trackG --freq-a-prefix ifg_A_HH_8x8 --freq-b-prefix ifg_B_HH_8x8 --nlooks 64 --coherence-threshold 0.5 --median-filter-size 15 --sigma-km 10 --ntiles 4 4 --nproc 4 --cycle-search 3
```

- Driver flags used above, all in `run_track_g.py`'s argparse: `--config/-c`, `--only`, `--force`, `--dry-run`, `--frequencies`, `--dates`, `--igram-freq`, `--looks LY LX`. Also available: `--start-step`, `--stop-step`, `--list-steps`, `--polarizations`, `--dem-source`, `--log-file`, `--quiet`.
- `--dates` reaches every date-enumerating stage through `Config.selected_dates()`; before that fix only `gslc.py` honoured it (WF2-02). Never regenerate `stack.json` (`--only ingest --force`) while another chain that reads it is running, and verify the freq-A pin comes out byte-identical — a moved grid invalidates every GSLC already written. Run long stages under tmux with `python -u` and `EXIT=` markers, one log per run; poll `tmux has-session` and do **not** infer liveness with `pgrep -f` / `pkill -f`, which match the monitoring shell itself (WF2-07, WF2-03, WF2-18). Ionosphere outputs carry no parameters in their names, so re-runs into the same pair directory overwrite each other (WF2-26, open); `unw_A.tif` / `unw_B.tif` are reused whenever they exist unless `--force` is passed (`gslc_ionosphere.py:178`). Pass `--force`, or move the previous `ionosphere/` aside, before any parameter change.

#### Results, cost and open defects

- The pin: `ingest` derives the AOI, projects it, snaps outward to `geogrid.snap: 1000` m (`:73`) and writes the result into `stack.json` per frequency — top_left (148000, 3332000), bottom_right (462000, 3029000), 60600 × 62800 at 5 m, both bands, EPSG 32645. It must be pinned because everything downstream is pixel-wise across dates and bands and nothing resamples afterwards. `gridgate` asserts shape, EPSG, origin and spacing against the pin per (date, band) and fails loudly, but on the full tile it ran for freq A only: full-tile freq-B conformance was never gated directly and rests on the 8 × 8 interferogram geotransforms being identical, `(148000, 40, 0, 3332000, 0, −40)` for both bands (WF2-11, WF2-32).
- Frequency B is geocoded onto frequency A's 5 m lattice, not its native ~38 m ground range — roughly **7.6× oversampling in range**, ~2 h and ~20 GB for the pair against ~3.3 GB/date at native posting — because the ionosphere solve is elementwise only if A and B share the lattice. The `posting.B` comments contradict each other (WF2-31, open); the value in effect is 5 × 5 m.
- Cost, measured: ingest/gridgate/qa seconds, dem ~20 s, gslc freq A ~1h12m per date (2h23m for two), gslc freq B on the 5 m lattice 1h04m per date, igram 1×1 26m49s at peak ~981 MiB per 512-row block, igram 8×8 ~7.5 min per band, ionosphere unwrap 20m27s (A) + 21m48s (B) then ~30 min of solve and filter (snaphu 4×4 tiles at nproc 4, ~1.5 GB per tile). Disk: ~10 GB per GSLC (32 % of uncompressed, gzip 1); for the pair ~40 GB of GSLCs + ~53 GB of 1×1 rasters + ~1 GB at 40 m. `coherence_stats` once read the whole 15.2 GB coherence raster and was OOM-killed *after* the interferogram had formed (WF2-06); it now reads decimated, capped at ~4 Mpx, and any new whole-raster read at 1×1 will repeat this.
- `L2_GSLC/20260726_gslc_freqB.h5` **is not a conformant GSLC**: the VM went down mid-finalise on 2026-09-08, leaving **26 of 193 datasets** and no `identification` group (no orbit, attitude, calibrationInformation, centerFrequency, inputDataExceptionMask). The image is intact — per-row fill matches the other date to within ≤0.07, data through row 60094 of 60600, coordinates and projection complete — so interferograms built from it are sound, but anything consuming GSLC **metadata** breaks; it was declared complete at the time on an image-fill check of five rows (WF2-11, open). The gate is dataset count equal to a reference GSLC (193 here) **and** `identification` present; never trust image fill, file existence or a log alone, and write to a temp name and rename on success.

```bash
python -c "import h5py,sys
for p in sys.argv[1:]:
    with h5py.File(p) as f:
        n=[0]; f.visititems(lambda k,v: n.__setitem__(0, n[0]+isinstance(v,h5py.Dataset)))
        print(p, n[0], 'identification' in f['/science/LSAR'])
" /path/to/*_gslc_freq*.h5
```

- Two more defects in **every** GSLC, both open: `referenceTerrainHeight` is **100 % NaN** (95142/95142 in a (303, 314) float32 array), from an upstream read window running off the source in `BaseL2WriterSingleInput.py` — image data unaffected, the layer invalid; and `radar_grid_cubes.heights` spans −500…3000 m over terrain reaching 7892 m, so metadata cubes are extrapolated above 3000 m (impact unverified). Also open: freq-B 8×8 coherence is over-counted on the oversampled lattice (p5 0.1673 vs freq A's 0.1105 against a theoretical N=64 floor of 0.1108), so its 0.609 median is not comparable to A's 0.530 (WF2-19); the 3×3 sliding coherence is computed per 512-row block without a halo, biasing 0.4 % of rows by ~+0.05 (WF2-29); and the ionosphere's absolute level rests on a prior — the cycle resolver fixes the class d = m − n but not the member, leaving ±0.235 TECU per joint cycle, absolute sign also open (WF2-22, WF2-23).
- **The finding that decides whether to take this route.** GSLC dates are registered by geometry only — `geocode_corrections.py:287-297` says "not implemented" for data-driven coregistration, so Track G has no dense-offset / rubber-sheet stage. Against the RSLC benchmark, wrapped-phase agreement is **0.887 at 40 m** (0.94–0.95 where coherence > 0.5) and coherence is **~5–6 % lower**. The registration test (`tools/alignment_test.py` v3, `docs/COMPARISON.md` §3.6) attributes both to the missing rubber sheet: geometry leaves 0.077 lines (34 cm) along track and 0.24 samples (0.75 m) in slant range, and an RSLC benchmark rebuilt with geometry-only registration matches the GSLC (phase R 0.9437 at 40 m, coherence median 0.607 against 0.605), so registration explains 93 % of the coherence gap. The residual is a **carrier phase term**, not a ramp: k = 2π·f_dc/1520 Hz ≈ 3.96 rad/line (the 1520 Hz line rate, not the 1909.6 Hz PRF). It **cannot be fixed after the fact**: delivered 5 m GSLC samples are spectrally white (a 256² chip has 98 % of bins within 6 dB of peak against 39 % for an RSLC chip), so band-limited sub-sample estimation and Fourier shifting are invalid on GSLCs, and an early "GSLC inter-date offsets are ~0" result was an artefact of exactly this and must not be re-quoted (WF2-33, open). The fix is corrections applied **inside** geocoding (`az_time_correction` / `srange_correction`); budget the ~5 % coherence loss and the carrier term as the price of this route.
- Products, under `case_studies/nepal_glof/`: `stack.json` (the pinned geogrid, per frequency); `L2_GSLC/{date}_gslc_freq{A,B}.h5`, four products, one of them the truncated file above, with runconfigs rendered to `cfg/gslc_{date}_freq{F}.yaml`; and in `pairs/20260714_20260726/trackG/` the 40 m `ifg_{A,B}_HH_8x8.{igram,coh,amp,nlooks}.tif` (7575 × 7850, both bands, pixel-aligned) and `ionosphere/` — `unw_{A,B}.tif`, `conncomp_{A,B}.tif`, `dispersive{,_filtered,_tecu,_sigma,_sigma_filtered}.tif`, `non_dispersive.tif`, `mask.tif`, `ionosphere.json` — whose filtered layer is valid on 94.4 % of the grid against a 29.6 % raw mask, because the Gaussian extrapolates up to ~1000 px into no-data: re-mask to the swath before applying it (WF2-25, open).
- The 1×1 (5 m) products are `ifg_A_HH.{igram,coh,amp,nlooks}.tif` and `amp_A_HH_{date}.tif` — **legacy names without looks** (WF2-27, open): they predate the `ifg_{freq}_{pol}_{ly}x{lx}` rule, and `tools/compare_four_way.py` hardcodes both spellings (`G_full_ifg` / `G_crop_ifg` at `:429-430`, the coherence pair at `:791-792`), so re-running `--looks 1 1` will not find them and will recompute ~52 GB. `provenance/{stage}.json` is per stage, not per product, so each is overwritten by the last run of that stage and there is none for the headline 1×1 product or the freq-B GSLCs (WF2-28, open). No water mask, no full-resolution unwrapped phase, no overlay.

### WF3 — RSLC cropped to an AOI first (what the production chain uses)

Cut each L1 RSLC down to the AOI **before** anything else runs, then feed the unmodified ISCE3 InSAR chain the smaller granule. ISCE3 has no radar-domain AOI option — every stage sizes itself from `slc.getRadarGrid(freq)` — so crop-first has exactly one entry point: make the granule smaller, which `tools/rslc_subset.py` does. **`nisar_coreg.py` supersedes the hand-driven subsetter + driver path below; use the module.** Mechanism, invariants and error history: `docs/WF3_RSLC_CROPPED.md`.

Interferometrically the crop is the full tile (COMPARISON.md §3.1–3.2, v2 run): reference SLC bit-identical, coregistered secondary R 0.99246, wrapped phase R 0.9944 at 5 m / 0.9951 at 40 m. The cropped InSAR chain ran in **5342 s = 1h29m** against the full tile's **8h34m through RIFG alone** (WF3 §4.4, WF1 §8.3) — a saving of at least ~5.8×, and conservatively so: the crop figure includes the unwrap and ionosphere that the full-tile figure excludes, and the crop shared its 8 cores with the WF4 leg. Relief and the skew of a map rectangle in radar geometry already put the AOI (1.77 % of the map frame) at ~4.9 % of the radar grid, and the ionosphere-sized buffers take the actual crop to 276 Mpx = **9.6 %** (WF3-04). **One caveat decided the pipeline:** a crop reproduces ionosphere *shape* but not *level*: residual 0.0027 TECU (r 0.9995) with an offset of exactly one freq-B unwrapping cycle (STATE.md, "Findings (comparison v2)"; COMPARISON I3), and the crop's own cycle-class rule lands one joint cycle from the full tile (WF3-33). The production decision (STATE.md revision 2, 2026-09-15) is therefore crop-first for **coregistration, wrapped phase and coherence**, with ionosphere/troposphere/SET taken from the **GUNW** products by `nisar_timeseries.py`.

#### Run it today — the module (use this)

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env
cd /home/sharath/isce3/asc/nisar_workflows
C=coreg_configs/nepal_nisar_ascending_rslc.yaml
python nisar_coreg.py -c $C show                 # resolved plan + every parameter; runs nothing
python nisar_coreg.py -c $C run --dry-run        # validates the generated ISCE3 driver config
python nisar_coreg.py -c $C run --detach         # prepare -> crop -> coreg, in tmux
python nisar_coreg.py -c $C progress             # live stage timeline vs crop v2, scratch size
python nisar_coreg.py -c $C status
```

Per-unit, for a batch system: `--stage prepare` once, then `--stage crop --dates <date>` per date, then `--stage coreg --dates <secondary>` (the first one performs the stack set-up). Other flags: `--jobs N`, `--force`, `--dates`. Exit codes: `0` ok, `1` a unit failed, `2` config error, `3` missing prerequisite. The case config turns the crop on:

```yaml
mode: RSLC
crop: true
aoi_kml: /home/sharath/nisar_downloader/glof_bigger_aoi.kml
reference_date: null      # null -> middle date (20260726 for this case)
```

The module calls `tools/rslc_subset.py` itself with `--buffer-az-lines` / `--buffer-range-m` / `--buffer` from `coreg_configs/defaults.yaml:crop_buffers` (1000 lines, 12500 m, 500 px) and with `--align-az-looks` / `--align-rg-looks` from the same section (9 and 8, the tool's own defaults, so the window is unchanged) — module crops also get the 64-multiple side-band snap that the v2 crops did not. Those two keys exist so that a case using different interferogram looks snaps its crop to the same lattice; `run_case.py` sets them from the case's `looks:` for exactly that reason, and a non-default value is added to the crop directory name. It writes `crop/<aoi>/<date>.h5`, a `params.json` per crop directory and per stack, and refuses a later run whose parameters differ (exit 2). Looks are a setting but default to 1×1 (`rslc.interferogram.looks`), which is what the time-series module's RIFG gate compares against; a different value writes a differently named product (`ifg/RIFG_<ref>_<sec>_<F>_<P>_<az>x<rg>.h5`) and switches that gate off. **The module stops at RIFG** — unwrap and ionosphere are `nisar_timeseries.py`. Budget: crop ~2.3 min and **1.80 GB** per date (smoke test: 0714 in 157 s); the v2 chain used **34 GB** of scratch including the ionosphere side-band sub-run and stayed well within **31 GB** of RAM; the module expects ≈80 min per pair, six secondaries 2-at-a-time ≈5–6 h, ≈32 GB scratch per running pair and ≈110 GB peak with 2 pairs (154 GB free on 2026-09-15). Module figures are expectations carried over from crop v2 (COREG_MODULE.md); the actual 7-date wall time is not recorded.

#### Run it the historical way — subsetter + driver

This is a transcript of the v2 comparison leg (WF3 §5), not a command to type: the nepal_glof RSLCs and DEM were deleted locally on 2026-09-15 (STATE.md), so restore them from `gs://s1-slc/nepal/nisar/ascending/tile_1/SLC/` and `gs://s1-slc/nisar_workflow/nepal_glof/` first.

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env
cd /home/sharath/isce3/case_studies/nepal_glof; T=/home/sharath/isce3/asc/nisar_workflows
for d in 20260714 20260726; do
  python -u $T/tools/rslc_subset.py --rslc $(ls L1_RSLC/*_${d}T*.h5) --out L1_RSLC_AOI_v2/${d}_aoi.h5 \
      --kml /home/sharath/asf_slc/glof_exact_aoi.kml --dem aux/dem/dem_nepal_glof.tif --polarizations HH
done                                   # add --dry-run first to print the windows without writing
cd $T; CFG=configs/nepal_glof_aoi_v2.yaml
python run_track_r.py -c $CFG --only ingest
python run_track_r.py -c $CFG --only dem
python run_track_r.py -c $CFG --only runconfig      # renders + schema-validates; prints the disk bill
python run_track_r.py -c $CFG --only insar
python run_track_r.py -c $CFG --only qa             # optional; the v2 run did not run it
```

`run_track_r.py` flags that matter here: `--dry-run`, `--list-steps`, `--start-step` / `--stop-step`, `--force` (also clears the pair's scratch), `--pair REF SEC`, `--frequency`, `--polarization`, `--looks AZ RG`, `--product-type`, `--no-disk-gate`, `--log-file`, `--quiet`.

`rslc_subset.py` refuses to overwrite an existing output and verifies the A/B relation before writing. Its other flags: `--bbox LON0 LON1 LAT0 LAT1` (instead of `--kml`), `--bounds AZ0 AZ1 RG0_A RG1_A` (force one window across dates), `--frequencies`, `--margin`, `--align-rg-looks`, `--align-az-looks`, `--align-sideband-looks`, and `--no-align` (legacy per-band range crop, provenance only — it corrupts the freq-B side band). The invariants it holds and what breaks when each is violated are tabulated in `docs/WF3_RSLC_CROPPED.md` §4: zero Doppler, one azimuth window for both bands, the exact A/B range lattice (`ionosphere.py:122-237`, `decimate_freq_a_offset`, derives the freq-B offsets by dividing the freq-A range offsets by 8 using only the **reference** granule's A/B slant ranges, so every date must share one A-to-B origin relation), look-grid alignment, buffers sized by the ionosphere Gaussian, and the absolute-index rewrites — including `boundingPolygon` recomputed before any HDF5 handle on the **source** granule is opened, the SWMR clash being on the source rather than the output (WF3-10).

#### Preconditions and traps

- **Patches.** ISCE3 0.25.12 stock has not been used for this leg: WF3 adds no overlays of its own but inherits WF1's, and the vectorised `generate_insar_mask` overlay is what keeps a 1×1 interferogram grid tractable. Apply them before the `insar` step: `conda run -n isce3_env python tools/apply_patches.py` (`--check` to report, `--revert` to undo). It overlays only when the installed file is byte-identical to the expected upstream version, and keeps `<name>.orig`.
- **Zero Doppler for any radar-domain window**, with `--dem` supplying the height bracket (1295–7892 m here) instead of the −500…9000 m default: native Doppler slid the v1 window ~3300 lines ≈ **15 km** along track, and every v1 statistic described ~73 % of the AOI (WF3-27, WF3-28). Assert AOI coverage per quadrant (≥ 0.95) after cropping, before processing.
- **Each date is cropped to its own window**, so the two images of a pair start at different slant ranges. Any downstream flattening must include `(start_sec − start_ref)` alongside the geo2rdr offset — 8 range samples is half a phase cycle at L-band and inverts the interferogram. Fixed in `nisar_timeseries.py` on 2026-09-16; keep the ISCE3-RIFG cross-check that caught it.
- **Config traps.** `product_type` must be `RUNW` if you want ionosphere: `insar.py:120-124` gates the stage on `'RUNW' in out_paths` and skips it **silently** under `RIFG`; the validator refuses the combination (WF3-19), leave that guard in. `unwrap_crossmul_path` must be `null` in a cloned config (WF3-20). Don't retune snaphu per leg — v2 matches the benchmark's `[4,4]` / nproc 8 / overlap 256, v1 quietly used `[2,2]`/nproc 4 and got 5 connected components against the full tile's 19 (WF3-21). `configs/nepal_glof.yaml` now carries `looks_y: 8 / looks_x: 8` with `coherence_window: 5`: **set it back to 1×1 / window 3 before reproducing the comparison.** `configs/nepal_glof_aoi.yaml` is the v1 leg and invalid as science (native-Doppler window, per-band range buffering, still defaults to `frequency: B` and 9×1 looks, WF3-18) — kept as evidence, do not run it. **Disk gate:** `runconfig` estimates scratch and refuses to start below `track_r.min_free_gb` (60.0 GB in both glof configs); `enforce_disk_gate: false` or `--no-disk-gate` downgrades it to a warning. Known gap (WF3-22, open): the model omits the freq-B ionosphere sub-run — 16.7 GiB estimated against 19.3 GiB measured. Add headroom on a tight disk.
- **Operational.** A failed `ingest` leaves the previous `stack.json` in place even with `--force`, and filtering the log can hide the ERROR lines — check the exit code, not the presence of an artifact (WF3-13, open). Output identity is still open (WF3-15): cropped granules are named `<date>_aoi.h5`, keep the source `granuleId` and `isFullFrame: True`, and the products carry the same filenames as the full-tile ones, separated only by `out_root` — the module's `crop/<aoi>/` + `params.json` naming is the fix, the historical tree is not protected, so enumerate planned output paths before launching a second leg. Never stop a run with `pkill -f <config path>`: the pattern matches your own shell's argv and kills it (WF3-30) — stop by PID or tmux session name.

### WF4 — GSLC on the AOI grid from the start

`G_crop`: Track G run so the output lattice covers only the AOI. Geocoding is per-pixel, so an AOI-sized GSLC is cell for cell the same computation as the full-tile GSLC (WF2). Detail — and the `qa`, `watermask`, `unwrap`, `overlay` stages, which exist on Track G but were never run on the AOI leg: `docs/WF4_GSLC_CROPPED.md`. Two routes to the lattice:

- **as run (v2)** — WF2 on subset granules from `tools/rslc_subset.py`; the lattice is pinned from the *subset* footprints, `geogrid.aoi_lonlat: null`. Every number below is from this route.
- **straight from the full granules** — set `geogrid.aoi_lonlat: [west, south, east, north]` and ingest intersects it with the footprint and pins the grid to it (`nisar_wf/ingest.py:383`). No RSLC crop needed, since `gslc.py` iterates geogrid blocks and reads only the samples they cover. Prefer it if you only want GSLCs, but **no timings are recorded** for it.

#### Configs

| file | status |
|---|---|
| `configs/nepal_glof_aoi_v2.yaml` | the v2 G_crop leg. `out_root <case>/aoi_v2`, `frequencies: [A, B]`, `igram.looks_y/x: 1`, `coherence_window: 3`, granules listed explicitly from `L1_RSLC_AOI_v2/` |
| `configs/nepal_glof_aoi.yaml` | v1, historical: still says `frequencies: [A]` (line 71), so every v1 command passed `--frequencies` explicitly (WF4-02); its granules came from the native-Doppler, unaligned subsetter. Evidence only — do not build on it |
| `configs/nepal_glof.yaml` | full-tile benchmark (WF2), now `igram.looks_y: 8` / `looks_x: 8` with `coherence_window: 5`. The four-workflow comparison ran at `looks 1 x 1` with `coherence_window: 3` (history at `configs/nepal_glof.yaml:276-282`) — set it back to reproduce |

- **Preconditions.** `conda run -n isce3_env python tools/apply_patches.py --check` (`--revert` to undo); none beyond WF2 are needed, but the check is seconds.
- **Zero Doppler for any radar-domain window.** Native Doppler slid the v1 crop ~15 km along track. Irrelevant on the `aoi_lonlat` route.
- **Disk gate.** `nisar_wf/gslc.py:394-402` raises `StepFailed` if forecast uncompressed GSLC bytes exceed 90 % of free space on `out_root`; no bypass on Track G, warn-only under `--dry-run`.
- **One shared `stack.json` per `out_root`**, pinned for both bands and immutable while any stage that reads it is running (WF4-01, WF4-02). Ingest and DEM are shared with the Track R crop leg — run them once, for both bands, before either leg starts. Subset granules, if used, must have passed WF3 §9's geometry gates.

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env
cd /home/sharath/isce3/asc/nisar_workflows
CFG=configs/nepal_glof_aoi_v2.yaml
C=/home/sharath/isce3/case_studies/nepal_glof
python run_track_g.py -c $CFG --dry-run
python run_track_g.py -c $CFG --only ingest --frequencies A B
python run_track_g.py -c $CFG --only dem
for F in A B; do python run_track_g.py -c $CFG --only gslc --frequencies $F; done
python run_track_g.py -c $CFG --only gridgate --frequencies A B
python run_track_g.py -c $CFG --only igram --frequencies A B --igram-freq A --looks 1 1 --dates 20260714 20260726
python run_track_g.py -c $CFG --only igram --frequencies A B --igram-freq A --looks 8 8 --dates 20260714 20260726
python run_track_g.py -c $CFG --only igram --frequencies A B --igram-freq B --looks 8 8 --dates 20260714 20260726
python -u tools/gslc_ionosphere.py --pair-dir $C/aoi_v2/pairs/20260714_20260726/trackG --freq-a-prefix ifg_A_HH_8x8 --freq-b-prefix ifg_B_HH_8x8 --nlooks 64 --coherence-threshold 0.5 --median-filter-size 15 --sigma-km 10 --ntiles 4 4 --nproc 4 --cycle-search 3
```

- For the `aoi_lonlat` route, set it in the config, point `granules:` at the full RSLCs and run the same sequence. It is read at **ingest**, so changing it means `--only ingest --force` and a rebuild of everything downstream.
- `--only` takes number, exact name or unique prefix and **bypasses the config's `steps:` toggles**; `--start-step` / `--stop-step` give a range instead. `--force` recomputes when outputs already look complete; `--list-steps` prints the step table and exits; `--log-file PATH` and `--quiet` redirect logging.
- `--frequencies`, `--polarizations`, `--dates`, `--igram-freq`, `--looks LY LX`, `--dem-source` override the YAML. `--looks` is in the output filename, so a different setting never overwrites an existing product.
- Chain the stages in one wrapper with `EXIT=` checks after every stage — the v2 run did (`C/logs/run_aoi_v2.sh`), v1 did not and sat idle 68 min after a silent gate failure.

**Cost (v2, concurrent with WF3 on 8 cores).** ~65 min wall clock: ~52 min geocoding four products, gridgate 3 s, igram A 1×1 3m22s (~384 MiB peak per 512-row block), 8×8 43 s / 47 s, ionosphere 7m27s. Disk 3.5 GB GSLCs + 5.4 GB pair products. Against WF2's full tile that is about a fifth of the geocoding time (~52 min vs ~4.5 h for the same four products) and a tenth of the disk (~0.95 GB vs ~10 GB each).

**Equivalence.** G_full and G_crop agree: phase R **1.0000** at 5 m and at 40 m, |Δφ| p95 **0.5 mrad** — numbers in `docs/COMPARISON.md`. Over identical 40 m cells the ionosphere agrees to **+0.0063 TECU** (residual std **0.0123 TECU**, r **0.9875**; WF4-06, `docs/WF4_GSLC_CROPPED.md:218`). No resampling is involved: the crop lattice is pinned with the same `snap: 1000.0` as the full tile. G_crop inherits every WF2 limitation against the RSLC benchmark — geometry-only registration, lower coherence, an absolute ionosphere that is a prior.

- **Never compare absolute TEC across runs, full vs crop included.** snaphu's integer reference is arbitrary per run and extent: a run can differ by **0.235 TECU per joint cycle**, or **5.59 TECU per band-A cycle** if a class is mis-resolved (WF4-06, logged as *by design*; the automation gap it leaves — an external TEC reference or a cross-run consistency rule — is the OPEN item in §10). Keep unwrap tiling identical across legs you will compare.
- **Never compare medians over different ground.** The WF4 log records the −1.2270 (crop) vs −1.4284 (full-scene) TECU claim as stale at `STATE.md:45`, but STATE.md has since been corrected: the claim now appears only in its withdrawn-claims list (`STATE.md:168`), with the correction that the same pixels agree to 0.0002 TECU.
- **Per-band and combined GSLC runs produce different filenames.** `--only gslc --frequencies A B` writes one `*_freqAB.h5`; the per-band loop writes `*_freqA.h5` and `*_freqB.h5`. `qa.py:440` still resolves one band per date, so freq-B quicklooks are silently skipped; `Config.resolve_gslc` falls back to any existing band; `gslc.py` still keys naming and completeness on `cfg.freq_tag` (WF4-05, open). Validate the `--dry-run` plan against the invocation you will actually execute.
- **Interferogram filenames do not encode the geogrid.** `igram.py:528-540` re-checks existing products against the pin and rebuilds on a mismatch, but an `exists()` check alone passes on products describing different ground.
- Known noise: **GDAL ERROR 5 before `referenceTerrainHeight`** once per GSLC, that layer all NaN, run still exits 0 — generic ISCE3 0.25.12 behaviour, untriaged (WF4-08, open). **`dispersive_sigma_filtered.tif` was 100 % +inf** in G_full and G_crop v1; fixed in `tools/gslc_ionosphere.py` and applied in v2, but G_full's uncertainty layer predates the fix (WF4-09).

### Which workflow to run

Four workflows were compared on one pair (20260714 × 20260726, track 98 frame 16, freq A HH): full-tile RSLC (WF1), crop-first RSLC (WF3), full-tile GSLC (WF2), crop-first GSLC (WF4). Production kept WF3's chain and wrapped it in the two modules: `nisar_coreg.py` calls the same drivers underneath (`run_track_r.py`, `run_track_g.py`) with generated configs, while `nisar_timeseries.py` forms its own interferograms, unwraps with snaphu and inverts with MintPy.

Driver commands assume `conda activate isce3_env` and `cd /home/sharath/isce3/asc/nisar_workflows`. `nisar_coreg.py` must be run from `isce3_env`; `nisar_timeseries.py` re-launches each stage in the environment named in `ts_configs/defaults.yaml`, so it runs from either.

| goal | workflow | command |
|---|---|---|
| **LOS deformation time series over an AOI** (the default; multi-date) | crop-first RSLC stack (`nisar_coreg.py`) → pair network, tiled snaphu, GUNW corrections, MintPy (`nisar_timeseries.py`) | `python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml show`<br>`python nisar_coreg.py -c coreg_configs/nepal_nisar_ascending_rslc.yaml run --detach`<br>`python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml show`<br>`python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml run --detach` |
| **Benchmark-quality single pair** (reference answer; native NISAR RIFG/RUNW with full metadata, ISCE3's own split-spectrum ionosphere) | WF1, Track R on the full tile | `python run_track_r.py -c configs/nepal_glof.yaml --only ingest`<br>`python run_track_r.py -c configs/nepal_glof.yaml --only dem`<br>`python run_track_r.py -c configs/nepal_glof.yaml --only runconfig --frequency A --looks 1 1`<br>`python run_track_r.py -c configs/nepal_glof.yaml --only insar --frequency A --looks 1 1`<br>`python run_track_r.py -c configs/nepal_glof.yaml --only qa` |
| **One pair at benchmark science settings over an AOI** (the coreg module runs the coregistration half of this chain; it stops at the 1×1 RIFG and runs no unwrap or ionosphere) | WF3, crop first with `tools/rslc_subset.py`, then the same Track R chain | per date, from `cd /home/sharath/isce3/case_studies/<case>` with `T=/home/sharath/isce3/asc/nisar_workflows`: `python $T/tools/rslc_subset.py --rslc L1_RSLC/<granule>.h5 --out L1_RSLC_AOI_v2/<date>_aoi.h5 --kml <aoi>.kml --dem aux/dem/dem_<case>.tif --polarizations HH` (add `--dry-run` first)<br>then from `$T`: `python run_track_r.py -c configs/nepal_glof_aoi_v2.yaml --only ingest` → `--only dem` → `--only runconfig` → `--only insar` |
| **Geocoded product for map-domain work** (one lattice shared across dates, amplitude/coherence/phase in UTM) | WF4 (crop) or WF2 (full tile), Track G | `python run_track_g.py -c configs/nepal_glof_aoi_v2.yaml --only ingest --frequencies A B`<br>`python run_track_g.py -c configs/nepal_glof_aoi_v2.yaml --only dem`<br>`for F in A B; do python run_track_g.py -c configs/nepal_glof_aoi_v2.yaml --only gslc --frequencies $F; done`<br>`python run_track_g.py -c configs/nepal_glof_aoi_v2.yaml --only gridgate --frequencies A B`<br>`python run_track_g.py -c configs/nepal_glof_aoi_v2.yaml --only igram --frequencies A B --igram-freq A --looks 8 8 --dates 20260714 20260726` |
| **Ionosphere screen whose level can be referenced** | a single full-tile RSLC `main_side_band` solve per pair (WF1, `product_type: RUNW`, unwrap 9×8) sliced onto the AOI — or, in production, the GUNW's screen | full tile: `python run_track_r.py -c configs/nepal_glof.yaml --only insar --frequency A --looks 1 1` (ionosphere is gated on RUNW/GUNW; under `RIFG` it is skipped silently)<br>GUNW route: `python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml run --stage corrections` |

Track G's `watermask`, `unwrap` and `overlay` steps exist in the driver but were never run for Nepal.

**What each costs** (8-core / 31 GB VM; per-stage figures measured, whole-pair and whole-stack totals are sums or projections — see the per-workflow sections):

| workflow | time | disk |
|---|---|---|
| coreg module, crop stack | ≈ 80 min per pair; six secondaries two-at-a-time ≈ 5–6 h, plus ≈ 3 min per crop | scratch ≈ 32 GB per running pair; peak ≈ 110 GB with two pairs |
| time-series module (pre-event, 5 dates / 9 pairs) | ≈ 20 min geometry + ≈ 15 min interferograms + ≈ 6 min unwrapping + ≈ 5–10 min MintPy | not recorded as a total |
| WF3 crop pair (RSLC, RIFG 1×1 + unwrap 9×8 + ionosphere) | 1 h 29 m (5342 s, sharing 8 cores with WF4); subset ≈ 2.3 min per date | 34 GB scratch; 1.80 GB per subset granule |
| WF1 full-tile pair | 8 h 34 m through the RIFG; unwrap 9×8 ≈ 23 min + ionosphere 27 min; the 1×1 unwrap alone 12.84 h. Whole-pair budget ≈ 10 h 25 m with 1×1 export, ≈ 7 h 38 m – 8 h 05 m with 9×8 | ≈ 301 GB scratch for freq A at 1×1, plus the side band (the rev-2 banner quotes ≈ 333 GiB and ≈ 10 h per full-tile pair) |
| WF2 full-tile GSLC pair | freq A 2 h 23 m for two dates, freq B 1 h 04 m per date; igram 1×1 26 m 49 s; ionosphere ≈ 42 min snaphu + ≈ 30 min solve | ≈ 10 GB per date per band; ≈ 40 GB GSLCs + ≈ 53 GB of 1×1 rasters + ≈ 1 GB at 40 m |
| WF4 crop GSLC pair | ≈ 65 min total (geocoding ≈ 52 min for four products while sharing the CPU with WF3; igram A 1×1 3 m 22 s; ionosphere 7 m 27 s) | ≈ 0.95 GB per date per band; 3.5 GB GSLCs + 5.4 GB pair products |

#### The three findings behind those choices

Numbers, method and figures: `docs/COMPARISON.md` §3.

1. **Crop-first is interferometrically the full tile** (§3.1–3.2, §3.4). Wrapped-phase agreement R_full − R_crop **0.9944 at 5 m, 0.9951 at 40 m**, constant offset **+0.0035 rad**, no ramp; the reference SLC is **bit-identical** in the crop window. Cropping buys ≈ 10× fewer pixels and ≈ 9× less scratch.
2. **GSLC dates are registered by geometry only** (§3.6, §3.8). Geometry leaves **0.077 lines (34 cm)** along track and **0.24 samples (0.75 m)** in slant range; R_full − G_crop is **0.8921 at 40 m with a −0.294 rad offset**. It cannot be fixed after delivery: a 256² delivered GSLC chip has **98 % of its spectrum within 6 dB of peak** (39 % for an RSLC chip). Use GSLC for map-domain products, not for the best phase.
3. **A crop's ionosphere has the right shape and an unreferenced level** (§3.5, §3.7, §3.8). R_crop's screen is the benchmark's plus exactly one freq-B cycle: **−72.962 rad = −5.350 TECU**, r **0.99945**. Never use a crop's own screen as an absolute level. Hence the production chain takes ionosphere, troposphere and solid-earth tides from the GUNW, where the constant cancels once the series is referenced to a point; absolute TEC still needs GIM or GNSS — open item.

Not validated end to end: dolphin was specified in PIPELINE_DESIGN rev 2 and has never been run here, and nothing independently cross-checks the MintPy inversion. What is checked: interferogram formation against ISCE3's own RIFG (median |Δφ| **0.002 rad** on 1.09 M coherent pixels) and whole-cycle unwrap closure over every triplet (`qa/unwrap_closure.json`).

#### Preconditions that will bite

- **Patches.** `python tools/apply_patches.py --check` must report every overlay applied before any Track R run; stock ISCE3 0.25.12's unwrap and interferogram-grid mask code OOMs at full tile. `--revert` puts the stock files back.
- **Disk gate.** `run_track_r.py --only runconfig` prints the scratch bill (104.4 B/px model) and refuses to start when the bill exceeds free space; if it fits but leaves less than `track_r.min_free_gb` (60 GB) free it warns instead. The benchmark fitted narrowly and warned: 312.9 GiB bill against 341.6 GiB free. `--no-disk-gate` downgrades the refusal to a warning and is an assertion that you checked the disk yourself. The model omits the ionosphere side-band sub-run.
- **Zero Doppler for any radar-domain window.** Placing a crop with the native Doppler centroid (~957–983 Hz) moved the v1 window **~3300 lines (~15 km)** along track. `rslc_subset.py` passes `LUT2d()`; keep it that way.
- **Crop geometry invariants.** `8·rg0_B − rg0_A` must be identical on both dates or ISCE3's side band mis-registers freq B; origins must be multiples of the look factors, and an exact side band also needs a range origin that is a multiple of 64 (`--align-sideband-looks 8`, default today, added after the v2 run and **not yet exercised**).
- **Restarts.** ISCE3 cannot resume: assume every restart re-runs from rdr2geo. `run_track_r.py --force` **deletes the pair's whole scratch tree**; without `--force` a pair whose output exists is skipped even when that output was corrupted by a SIGKILL — delete it by hand.
- Run long stages in tmux (`--detach` on the modules does it) with `python -u` and `EXIT=` markers.

#### Which configs exist, and which are history

Driver configs in `configs/`: `_template.yaml` (start here for a new site), `nepal_glof.yaml` (full tile, WF1/WF2), `nepal_glof_aoi_v2.yaml` (the valid crop, WF3/WF4), `nepal_nisar_ascending.yaml` (a full-tile driver config for the event site, never run past `runconfig`; the event study went crop-first through `nisar_coreg.py`, which generates its own driver config), `venezuela_t162_asc.yaml` (the Track G original), and `nepal_glof_aoi.yaml` — the **v1 crop, native-Doppler and A/B-unaligned, kept as evidence only; do not run it**. Module configs are `coreg_configs/{defaults,nepal_nisar_ascending_rslc}.yaml` and `ts_configs/{defaults,nepal_nisar_ascending_pre_event,nepal_nisar_ascending_post_event}.yaml`; do not hand-edit generated driver configs.

Two traps if you reproduce the four-workflow comparison from `configs/nepal_glof.yaml`:

- Its `igram:` block now carries **`looks_y: 8`, `looks_x: 8`, `coherence_window: 5`** (≈ 40 m, for event work); the comparison ran it at **1×1 with `coherence_window: 3`** (HISTORY comment below the looks). Set both back — `--looks 1 1` changes the looks but not the coherence window.
- Its `track_r:` block still sets `frequency: B` (line 482) with looks keyed per frequency; the benchmark used `--frequency A --looks 1 1` on the command line, which is why those flags appear in every WF1 command above.

## Tools — data, preparation and environment
Run from `asc/nisar_workflows`; `conda run -n <env> python ...` throughout. `tools/rslc_subset.py` and `tools/test_insar_mask_patch.py` scrub `sys.argv` before importing ISCE3, because pyre parses `sys.argv` inside its own `__init__` and crashes on an unrecognised flag; `tools/slc_coherence.py` and `tools/slc_amp_overlay.py` carry the same scrub defensively even though they only reach GDAL and h5py — that is why they look slightly unusual at the top. `nisar_coreg.py` runs `rslc_subset.py` as its `crop` stage; run these directly for one-off work, diagnosis, or a new machine.

### `tools/nisar_fetch.py` — search ASF for NISAR products and download them
Queries the ASF param endpoint over an AOI polygon. Any env with `requests` (`isce3_env` works). Needs a `urs.earthdata.nasa.gov` entry in `~/.netrc`, else it exits before any network call.

```bash
# search only — prints the matching granules as JSON and stops
conda run -n isce3_env python tools/nisar_fetch.py --kml /home/sharath/nisar_downloader/glof_bigger_aoi.kml \
  --level RSLC --start 2026-06-01 --end 2026-09-30 --search-only
# download — the exact query that fetched the last granule of the ascending stack
conda run -n isce3_env python tools/nisar_fetch.py --kml /home/sharath/nisar_downloader/glof_bigger_aoi.kml \
  --level RSLC --start 2026-09-12 --end 2026-09-12 --ref-date 20260912 --sec-date 20260912 \
  --track 98 --frame 16 --tier PR --out /home/sharath/isce3/case_studies/nepal_nisar_ascending/L1_RSLC
```

| flag | required | default | notes |
|---|---|---|---|
| `--kml` | yes | — | first `<coordinates>` block becomes the `intersectsWith` polygon; the ring is closed for you |
| `--level` | no | `GUNW` | ASF `processingLevel`, e.g. `RSLC`, `GUNW` |
| `--start` / `--end` | yes | — | `YYYY-MM-DD`. `--end` is **inclusive**: the code adds one day before querying |
| `--ref-date` / `--sec-date` | for download | — | `YYYYMMDD`, matched as a substring of the filename |
| `--track` / `--frame` | no | — | matched as `_098_` / `_016_`, i.e. zero-padded to three digits |
| `--tier` | no | none | `PR` or `UR`; keeps only names containing `_PR_` or `_UR_` |
| `--out` | for download | — | created if missing; also receives `fetch_manifest.json` |
| `--search-only` | no | off | print the rows and return 0 without downloading |

Without `--search-only`, `--ref-date`, `--sec-date` and `--out` are effectively required — a `None` in the substring test raises `TypeError`; for a single-date product such as an RSLC set both dates to that date. `--tier PR` is how the ascending stack avoids the duplicate `_UR_` copy of 2026-08-31. **It refuses to overwrite a complete file**: matching remote `content-length` prints `exists, complete`, a *different* size exits rather than resuming, and interrupts resume from `<name>.h5.part` by HTTP Range. Local counterpart of `/home/sharath/nisar_downloader/nisar_downloader.py`, which stages to GCS instead.

### `tools/rslc_subset.py` — crop an L1 RSLC to the AOI, producing a smaller but fully valid RSLC
The only entry point for "crop first", since every ISCE3 stage sizes itself from `slc.getRadarGrid(freq)`. Rewrites the swath arrays, identification times and bounding polygon; leaves geolocation cube, orbit and attitude untouched. Needs `isce3_env`.

```bash
conda run -n isce3_env python tools/rslc_subset.py \
  --rslc /home/sharath/isce3/case_studies/nepal_nisar_ascending/L1_RSLC/NISAR_L1_PR_RSLC_030_098_A_016_4005_DHDH_A_20260912T233918_20260912T233952_P05023_N_F_J_001.h5 \
  --out /tmp/20260912_aoi.h5 --kml /home/sharath/nisar_downloader/glof_bigger_aoi.kml \
  --dem /home/sharath/isce3/case_studies/nepal_nisar_ascending/aux/dem/dem_nepal_nisar_ascending.tif \
  --polarizations HH --dry-run
```

| flag | required | default | what it does |
|---|---|---|---|
| `--rslc` | yes | — | input L1 RSLC granule |
| `--out` | yes | — | output HDF5; refuses to overwrite |
| `--kml` / `--bbox LON0 LON1 LAT0 LAT1` | one of | — | the AOI; without either it exits `need --kml or --bbox` |
| `--dem` | yes | — | read for the AOI min/max height instead of the global −500..9000 m bracket |
| `--buffer-az-lines` / `--buffer-range-m` / `--buffer` | no | `1000` / `12500.0` / `500` | per side: azimuth lines (~4.5 km); range in **slant metres** (= 500 freq-B px); extra pixels |
| `--margin` | no | `50` | `get_radar_bbox` internal margin |
| `--frequencies` | no | all present | e.g. `A B` |
| `--polarizations` | no | all present | restricting also rewrites `listOfPolarizations` |
| `--bounds AZ0 AZ1 RG0_A RG1_A` | no | — | seeds the freq-A window; B is scaled by the range-sample ratio. **Honoured verbatim only with `--no-align`.** In the default aligned mode the azimuth pair is discarded (AZ0/AZ1 are recomputed from this date's geometry plus `--buffer-az-lines`) and the range pair is only a seed, widened by `--buffer-range-m` and then snapped to the alignment multiple |
| `--align-rg-looks` / `--align-sideband-looks` / `--align-az-looks` | no | `8` / `8` / `9` | range origin snaps to a multiple of `lcm(band ratios, this)`; side-band range looks fold `ratio × this` into the same lcm; azimuth origin snaps to a multiple of this |
| `--no-align` | no | off | **legacy, reproduction only** |
| `--dry-run` | no | off | print the plan, write nothing |

- `--dry-run` prints the plan and stops with `DRY RUN -- nothing written`. **There is no `--force`**: an existing `--out` is a hard `refusing to overwrite`.
- **Zero Doppler is mandatory and there is no flag for it.** NISAR RSLCs are zero-Doppler products; the first version used the native Doppler centroid and put the crop ~15 km too early in azimuth. Window and bounding polygon both use zero Doppler now.
- **The alignment flags exist because ISCE3 silently assumes `rg0_B == rg0_A / 8`** (`decimate_freq_a_offset` in `nisar/workflows/ionosphere.py`). With the DHDH ratio of 8 and the defaults, the range origin snaps to multiples of `lcm(8, 8, 8×8) = 64` freq-A samples and azimuth to multiples of 9, keeping the cropped 9×8 grid aligned with the full tile's.
- **The buffers are set by the ionosphere Gaussian, not by coregistration.** If your crop stops at the interferogram, far smaller buffers suffice. Compare only the AOI interior either way.
- Crops of different dates do not cover identical ground: the delivered granules themselves differ, so each window is computed against the same lon/lat AOI on a different radar grid, and everything downstream works on the *reference* grid. `--bounds` pins the window exactly only when combined with `--no-align`, which is the legacy path that breaks freq-B alignment; in the default aligned mode the azimuth bounds are recomputed per date and the range bounds are re-buffered and re-snapped, so the four numbers are a seed, not a guarantee.
- `nisar_coreg.py` already runs this per date as its `crop` stage with `crop_buffers` from `coreg_configs/defaults.yaml` (`buffer_az_lines: 1000`, `buffer_range_m: 12500`, `buffer_px: 500`), frozen in `crop/<aoi>/params.json` — see `docs/COREG_MODULE.md`. Do not hand-crop into a directory the module owns.

### `tools/fetch_cop_dem.py` — Copernicus GLO-30 as a WGS84-**ellipsoidal** float32 GeoTIFF
Pulls GLO-30 from the Microsoft Planetary Computer STAC catalog (signed anonymously), mosaics, and warps `EPSG:4326+3855` → `EPSG:4979`.

```bash
conda run -n isce2_env python tools/fetch_cop_dem.py \
  --bbox "27.1796 30.2779 83.1882 86.7660" --out /tmp/dem_cop_nepal.tif
```

| flag | required | default | notes |
|---|---|---|---|
| `--bbox` | yes | — | **`'S N W E'`**, space separated, quoted — the ISCE2 script's convention, not GDAL's |
| `--out` | yes | — | output GeoTIFF; skips with a message if it exists |
| `--keep-geoid` | no | off | also writes `*_egm2008.tif` with the untouched geoid heights |
| `--force` | no | off | redo an existing output |

- **`isce2_env` does not exist on this VM** and no env here has `pystac_client` / `planetary_computer`, so it cannot run as shipped. The bbox above is the extent of the DEM already staged at `/home/sharath/isce3/case_studies/nepal_nisar_ascending/aux/dem/dem_nepal_nisar_ascending.tif`.
- **This is not what the pipeline uses.** `nisar_coreg.py --stage prepare` stages the DEM through `run_track_r.py`'s `dem` stage (`nisar_wf/dem.py`, `sardem`).
- It fails early if PROJ cannot do the vertical transform: it asks PROJ for the undulation at a hard-coded probe point (lon -68.2, lat 10.9 — the original Venezuela AOI, `fetch_cop_dem.py:60`; it is **not** derived from `--bbox`) and exits if the answer is ~0 m, because `gdalwarp` would otherwise hand back geoid heights labelled as ellipsoidal. Fix with `conda install -c conda-forge proj-data` or `PROJ_NETWORK=ON`.

### `tools/apply_patches.py`

Overlay upstream Python fixes onto the installed isce3/nisar packages
Copies four pure-Python replacement files over the conda-forge install, keeping each original as `<name>.orig`. Must run in **the environment being patched** — `isce3_env`.

```bash
conda run -n isce3_env python tools/apply_patches.py --check
conda run -n isce3_env python tools/apply_patches.py
conda run -n isce3_env python tools/apply_patches.py --revert
# to check the container image rather than this VM (docker is not installed here)
docker run --rm -v /home/sharath/isce3/asc/nisar_workflows:/w -w /w asia-south1-docker.pkg.dev/iocl-poc-479616/isce2-trials/isce3:pair_wise python tools/apply_patches.py --check
```

`--check` and `--revert` are mutually exclusive; with neither, it applies. Before overlaying, it checks that the patch source and the target both exist and that every symbol the patch imports already exists in the installed package (`check_requires`). It compares the installed file only against the patch itself, to detect "already applied" — there is no automated check that the patch is Python-only or that the installed file is the untouched upstream version, so do not run it over a site-packages tree something else has already edited.

| patch source | target | what it fixes |
|---|---|---|
| `tools/patches/resample_slc_v2.py` | `nisar/workflows/resample_slc_v2.py` | secondary opened with h5py's default chunk cache, so resampling re-read chunks from disk (isce-framework/isce3#372) |
| `tools/patches/insar_utils.py` | `nisar/products/insar/utils.py` | stock `generate_insar_mask` is a pure-Python double loop: 2886 Mpx at freq A 1×1, ~58 GB transient — hours and an OOM on a 31 GB box. Replacement preallocates uint32 and vectorises the range loop, falling back to stock when `num_sub_swaths != 1` |
| `tools/patches/h5_prep.py` | `nisar/workflows/h5_prep.py` | `RUNW_STANDALONE`/`GUNW_STANDALONE` missing from `product_dict` → `KeyError`, so `python -m nisar.workflows.unwrap <cfg>` could not run at all |
| `tools/patches/unwrap.py` | `nisar/workflows/unwrap.py` | interferogram + coherence materialised as numpy arrays: 34.6 GB at freq A, impossible on a 31 GB box. `snaphu.io.Raster` is file-backed; measured peak RSS 0.10 GB. Also normalises `HDF5:f:/grp` to `HDF5:f://grp` |

- **`--check` exits 0 even when a patch is missing.** A not-applied patch prints `NOT applied (run without --check to apply)` and continues; `rc = 1` is reserved for a missing source, a missing target, or a missing symbol. Require the literal **`already applied` four times**.
- `tools/patches/insar_mask_vectorized.py` is a **fifth file but not a fifth patch** — `apply_patches.py` never references it; it is the standalone function `test_insar_mask_patch.py` loads as the candidate. Same fix, two forms.
- `--revert` restores from `.orig` and deletes the backup; with no `.orig` it prints `nothing to revert`.

### `tools/slc_coherence.py` — moving-window coherence from a coregistered RSLC pair
Restores the ISCE2-style estimator that ISCE3's crossmul does not have: at 1×1 looks crossmul fills the coherence band with 1.0 (`cxx/isce3/signal/Crossmul.cpp:378-388`), so a 1×1 RIFG/RUNW ships min = median = max = 1. Needs `isce3_env`.

```bash
conda run -n isce3_env python tools/slc_coherence.py \
  --ref /home/sharath/isce3/case_studies/nepal_nisar_ascending/coreg/RSLC_ref20260726_AHH_glof_bigger_aoi/slc/20260726.slc \
  --sec /home/sharath/isce3/case_studies/nepal_nisar_ascending/coreg/RSLC_ref20260726_AHH_glof_bigger_aoi/slc/20260831.slc \
  --out /tmp/coh_20260726_20260831_3x3.tif --win 3
```

| flag | required | default | notes |
|---|---|---|---|
| `--ref` | yes | — | reference SLC, ENVI or any GDAL-readable complex raster |
| `--sec` | yes | — | secondary, **already resampled onto the reference grid** |
| `--out` | yes | — | float32 GeoTIFF, tiled, DEFLATE, BIGTIFF, NoData = NaN |
| `--win` | no | `3` | window size, must be odd |
| `--block-rows` | no | `2048` | rows per streamed block |
| `--also-ifg PATH` | no | — | also write the window-averaged complex interferogram (complex64) |

- **The estimator is biased high for small N** — floor `sqrt(pi)/(2*sqrt(N))`: 0.295 at `--win 3`, 0.177 at 5, 0.126 at 7. Fully decorrelated ground settles near that floor, **not** at 0. Written to GeoTIFF metadata as `BIAS_FLOOR` alongside `COHERENCE_WINDOW`, `ESTIMATOR` and the two input paths.
- A shape mismatch between `--ref` and `--sec` is a clean exit 2, not a crash; so is an even `--win`. The output is a GeoTIFF in name only — the input is a radar-grid raster with no geotransform. On the 22 464 × 12 303 ascending crop that is 276 Mpx ≈ 1.1 GB of float32.
- **There is no overwrite guard** — GDAL `Create` replaces an existing `--out` silently. Name outputs by their window.

### `tools/slc_amp_overlay.py` — Leaflet overlay showing what coregistration actually did
Geocodes three amplitude layers to dB through the *reference's* `rdr2geo` lon/lat and writes a self-contained folder (`index.html` plus one PNG per layer), suitable for `rsync -r` to a bucket. Needs `isce3_env`.

```bash
conda run -n isce3_env python tools/slc_amp_overlay.py \
  --case /home/sharath/isce3/case_studies/nepal_nisar_ascending \
  --pair 20260714 20260726 --looks 9 1 --out /tmp/overlay_20260714_20260726
# rebuild only the HTML from a previous run's manifest.json, leaving the PNGs and their shared stretch untouched
conda run -n isce3_env python tools/slc_amp_overlay.py --html-only --out /tmp/overlay_20260714_20260726
```

| flag | required | default | notes |
|---|---|---|---|
| `--case` | unless `--html-only` | — | case directory; must hold `stack.json`, `scratch/`, `L1_RSLC/` |
| `--pair REF SEC` | unless `--html-only` | — | two `YYYYMMDD` dates |
| `--looks AZ RG` | no | `9 1` | also selects the scratch directory name |
| `--out` | yes | — | output folder for `index.html` + PNGs (+ `_work/`, `manifest.json`) |
| `--max-px` | no | `8000` | cap on the EPSG:3857 grid's long edge |
| `--floor-db` | no | `40.0` | pixels this many dB below the scene median are treated as fill, not data |
| `--keep-tif` | no | off | keep the intermediate geocoded GeoTIFFs |
| `--reuse` | no | off | reuse multilooked rasters already in `<out>/_work` — safe when only the warp changed |
| `--html-only` | no | off | rebuild `index.html` from `<out>/manifest.json`; needs only `--out` |

- **The third layer is deliberately wrong, and that is the point.** Layers 1 and 2 are the reference and the *coregistered* secondary; layer 3 is the raw L1 secondary rendered through the reference's geolocation, so it draws displaced by whatever geo2rdr measured (~746 px azimuth ≈ 3.3 km on the GLOF pair). Toggling 2 against 3 is the demonstration.
- **Frequency and polarization are module constants, not flags**: `FREQ = "B"`, `POL = "HH"` near the top of the file.
- **It reads the legacy Track R scratch layout**, `<case>/scratch/trackR/<ref>_<sec>_B_HH_<az>x<rg>/`, and exits 2 with `ERROR: no Track R scratch at ...` if absent. `<case>/scratch/` is empty because `nisar_coreg.py` no longer writes there: its per-pair scratch goes to `<case>/coreg/<stack>/isce3/scratch/trackR/` (nisar_coreg.py:292, 337-338), and `run.prune_scratch: true` in `coreg_configs/defaults.yaml` deletes it there after verification. The coregistration module cannot produce the layout this tool needs even with pruning off: it runs frequency A at 1x1 looks, so its scratch is `<stack>/isce3/scratch/trackR/<ref>_<sec>_A_HH_1x1`, while this tool hard-codes freq B and expects `<case>/scratch/trackR/<ref>_<sec>_B_HH_9x1`. Use it only against a preserved legacy Track R run (`run_track_r.py` at 9x1 on freq B), restored from `gs://s1-slc/nisar_workflow/` if necessary.

### `tools/make_cogs.py` — Cloud Optimized GeoTIFFs for the browser viewer
Converts a Track G pair's geocoded outputs into EPSG:3857 COGs with internal overviews, plus a manifest with 2/98-percentile stretches, for `tools/viewer.html`. Needs `isce3_env`.

```bash
conda run -n isce3_env python tools/make_cogs.py \
  --pair-dir /home/sharath/isce3/case_studies/nepal_glof/pairs/20260714_20260726/trackG --freq A --pol HH
```

| flag | required | default | notes |
|---|---|---|---|
| `--pair-dir` | yes | — | the Track G pair directory; the dates are parsed from its name or its parent's |
| `--freq` / `--pol` | no | `A` / `HH` | substituted into the source filenames |
| `--out` | no | `<pair-dir>/cog` | output directory |
| `--force` | no | off | rebuild layers whose output already exists |

- **It is tied to the legacy Track G pair layout, which no longer exists locally** — there is no `pairs/` tree under either case study, so the command above needs the archive restored from `gs://s1-slc/nisar_workflow/` first.
- It looks for seven fixed filenames inside `--pair-dir`: `amp_{f}_{p}_{d1}.tif`, `amp_{f}_{p}_{d2}.tif`, `ifg_{f}_{p}.filt.int.tif`, `ifg_{f}_{p}.coh.tif`, `ifg_{f}_{p}.filt.phsig.coh.tif`, `ifg_{f}_{p}.filt.unw.tif`, `ifg_{f}_{p}.filt.unw.conncomp.tif`. Anything missing is a `SKIP` line, not an error, so a near-empty `cog/` means the naming did not match — check that before blaming the tool.
- The wrapped interferogram is complex and JS readers cannot consume it, so it is split into a real `phase` band here. Amplitudes are converted to dB; coherence/phsig/unw/conncomp pass through as-is.

### `tools/test_insar_mask_patch.py` — prove the vectorised `generate_insar_mask` is bit-identical
Runs stock and vectorised `generate_insar_mask` against real freq-B data from the `nepal_glof` case over a strip of the interferogram grid (400 az × 6781 rg ≈ 2.71 Mpx) and compares byte for byte. Needs `isce3_env`.

```bash
conda run -n isce3_env python tools/test_insar_mask_patch.py
```

- **Takes no arguments.** Prints both runtimes, the speedup (~13×), `BIT-IDENTICAL: True/False` and the unique mask values; exits 1 and lists the first five differing pixels if they disagree.
- **Run it BEFORE `apply_patches.py`, not after.** It imports the reference as `from nisar.products.insar.utils import generate_insar_mask as stock` — from the **installed** module, so once the mask patch is applied the test compares the patch with itself and passes trivially. This is probe 71 and it is still live in our tree; the fix is to import the `.orig` backup instead. All four patches are currently applied on this VM, so a run here proves nothing until you `--revert`.
- **Its inputs are archived away.** It hard-codes `/home/sharath/isce3/case_studies/nepal_glof`, reading `stack.json` and `scratch/trackR/20260714_20260726_B_HH_9x1/geo2rdr/freqB/{range,azimuth}.off`. `stack.json` is still there but the L1 RSLCs and the scratch are not, so today it fails with `FileNotFoundError`. Restore those prefixes from `gs://s1-slc/nisar_workflow/`, or repoint `CASE` and `S` at an equivalent freq-B pair.

## Tools — analysis, reporting and the archive

Standalone scripts, no case config: paths are hard-coded to the two case studies and the flags below are the only things you can change. Run them from the package root (`cd /home/sharath/isce3/asc/nisar_workflows && conda activate isce3_env`) — only the relative `docs/…` and `tools/…` paths in the examples need it, since the cross-imports of `report_kit.py`, `nisar_timeseries.py` and `compare_four_way.py` resolve from each script's own location. `isce3_env` covers every tool except `report_pdf.py`.
### Event study (study 2): `glof_event_analysis.py` → `build_glof_event_report.py`
```bash
python tools/glof_event_analysis.py
python tools/glof_event_analysis.py --only f18 f19 f20
python tools/build_glof_event_report.py
python tools/build_glof_event_report.py --quality 90
```
#### `tools/glof_event_analysis.py`

Every number and figure the event report uses, from the coregistered stack and the two time-series runs; nothing downstream recomputes
| flag | default | meaning |
|---|---|---|
| `--only f1 … f20` | all 20 | run only these figure ids |

Reads (all fixed in the source): `case_studies/nepal_nisar_ascending/coreg/RSLC_ref20260726_AHH_glof_bigger_aoi/`; `case_studies/nepal_nisar_ascending/timeseries/RSLC_ref20260726_AHH_glof_bigger_aoi/{pre_event,post_event}/`, including each run's `qa/mintpy.json`, `qa/unwrap_closure.json` and `status/corrections.json`; `/home/sharath/nisar_downloader/glof_bigger_aoi.kml` and `/home/sharath/nisar_downloader/nepal_glacier_zone.kml`. Writes under `case_studies/nepal_nisar_ascending/report/glof_event/`: `analysis.json` (`stats.provenance` is rewritten on every run, so the QA JSONs must exist even for a one-figure run); `fig/f1_study_area.png` … `fig/f20_glacier_vs_ring.png`; and `amp_change_db.npy`, an amplitude-change cache written by f7 and reloaded by f9, f12, f13 and f17 instead of recomputing — so a `--only` run of any of those four needs f7 to have run at least once. There is **no `--force`** (the docstring advertises one, the argparse block does not have it); a `--only` run merges into the existing `analysis.json`, so figures you did not rerun keep their previous entries.
#### `tools/build_glof_event_report.py`

Formats `analysis.json` into one self-contained HTML file, every figure re-encoded to WebP and inlined as a data URI
| flag | default | meaning |
|---|---|---|
| `--quality N` | `82` | WebP quality for each embedded figure |

Reads `report/glof_event/analysis.json` and `report/glof_event/fig/*.png`; writes `report/glof_event/nepal_glof_nisar_event_report.html`. Figure order is a fixed list inside the builder and the prose indexes `stats` by key, so run the full analysis first — a missing figure or stat raises `KeyError`.
### Four-workflow study (study 1): `compare_four_way.py` → `report_figures.py` → `build_report.py`
Surviving locally: `comparison_v2/`, `comparison/` (the v1 tree and its verification evidence), `L2_GUNW/`, `logs/`, `cfg/`, `provenance/` and `stack.json`. `report_figures.py` and `build_report.py` abort today with a GDAL `No such file or directory` on a Track G `.tif`; `compare_four_way.py` fails earlier still, with a bare `StopIteration` from `next((C / "L1_RSLC").glob(...))` at line 427, because the input granule directory is gone as well. Restore `pairs/` and `aoi_v2/` from `gs://s1-slc/nisar_workflow/nepal_glof/` and the input granules into `L1_RSLC/` from `gs://s1-slc/nepal/nisar/ascending/tile_1/SLC/`; the ISCE3 scratch (`scratch/` and `aoi_v2/scratch/`) was never archived — see `case_studies/nepal_glof/ARCHIVE_MANIFEST.md` — so it has to be regenerated from the RSLCs and the archived runconfigs. The already-built outputs (`comparison.json`, `report/figures.json`, the HTML) are intact.
```bash
python -u tools/compare_four_way.py
python -u tools/compare_four_way.py --rcrop-root aoi_v2 --gcrop-root aoi_v2 --out comparison_v2
python -u tools/report_figures.py
python tools/build_report.py
```
#### `tools/compare_four_way.py`

The v2 comparison itself: RSLC vs GSLC, full tile vs cropped-first, over the AOI, one sign convention (`P__vs__Q` always means P minus Q, benchmark first); read-only on every workflow product
| flag | default | meaning |
|---|---|---|
| `--case` / `--kml` | `/home/sharath/isce3/case_studies/nepal_glof` / `/home/sharath/asf_slc/glof_exact_aoi.kml` | case root / AOI polygon |
| `--pair` / `--tag` | `20260714_20260726` / `20260714_20260726_A_HH_1x1` | pair to compare / Track R product tag (picks the RUNW and the scratch tree) |
| `--rcrop-root` / `--gcrop-root` | `aoi_v2` / `aoi_v2` | subtree holding the cropped-RSLC / cropped-GSLC leg |
| `--rcrop-granules` / `--out` | `L1_RSLC_AOI_v2` / `comparison_v2` | directory of the cropped granules (`<date>_aoi.h5`) / output subtree under `--case` |
| `--geoloc-tol` | `15.0` | geolocation residual in metres above which a pixel leaves the comparison mask |

Reads the four legs' interferograms and RUNW HDF5s, the Track R scratch SLCs and the cropped granules. Writes `<case>/<out>/comparison.json` and caches expensive layers under `<case>/<out>/layers/`, each with an input manifest — a cache whose inputs changed is rebuilt, never silently reused, which is why there is no `--force`. (The docstring also names a `summary.md`; the code does not write one.)

**`tools/report_figures.py`** — figures and chart data for the four-way report; no flags. Reads `comparison_v2/{comparison.json, aoi_polygon_mask.tif, layers/}` and the four workflows' rasters. Writes `comparison_v2/report/fig/*.webp` and `comparison_v2/report/figures.json`. Map panels carry no baked-in text: titles, colourbars, scale bars and outlines are drawn by the HTML from `figures.json`, so they follow the viewer's theme.

**`tools/build_report.py`** — assembles the single self-contained HTML report; no flags. Every quantitative statement is formatted from JSON at build time, so it must run after `compare_four_way.py`, `report_figures.py`, `alignment_test.py`, `gunw_validation.py` and `iono_transfer_test.py`. Reads `comparison_v2/{comparison.json, supplement_nondispersive.json}`, `comparison_v2/alignment/{alignment.json, figures_alignment.json}`, `comparison_v2/iono_transfer/iono_transfer.json`, `comparison_v2/gunw_validation/{gunw_validation.json, figures_gunw.json}`, `comparison_v2/report/{figures.json, fig/*.webp, footprints.json}`, and opens a Track G amplitude GeoTIFF directly to draw the frame footprints. Writes `comparison_v2/report/nepal_glof_four_workflow_report.html`.
### Printing either report: `tools/report_pdf.py`

The one tool that does not run in a conda env; it needs Playwright and its Chromium build, which live in `~/.venvs/report-pdf`. Positional arguments, not flags
| argument | default | meaning |
|---|---|---|
| `[html]` / `[pdf]` (1st / 2nd positional) | `case_studies/nepal_glof/comparison_v2/report/nepal_glof_four_workflow_report.html` / the html path with a `.pdf` suffix | page to print / output file |
| `--force` | off | replace an existing PDF (it refuses otherwise) |
```bash
~/.venvs/report-pdf/bin/python tools/report_pdf.py --force
~/.venvs/report-pdf/bin/python tools/report_pdf.py /home/sharath/isce3/case_studies/nepal_nisar_ascending/report/glof_event/nepal_glof_nisar_event_report.html /home/sharath/isce3/nepal_glof_nisar_event_report.pdf --force
```
Prints through the page's own `@media print` stylesheet (A4, light palette, 16/18/15/15 mm margins), re-encoding every embedded WebP as JPEG (max 1100 px, quality 84) first — Chromium stores WebP losslessly in PDFs, which cost about 45 MB here. Needs network for the Google Fonts and falls back to the CSS stacks; the closing line reports the output size, which font families loaded and how many images were broken. The page footer is hard-coded to `Nepal GLOF four-workflow InSAR study · comparison v2`, so it appears on the event-study PDF as well.
### Verification and single-question experiments (study 1)
`gunw_validation.py`, `alignment_test.py` and `iono_transfer_test.py` are read-only, each answers one question and caches the answer. None of those three uses argparse: they match the literal string `--force` in `sys.argv` and ignore everything else, so `--help` prints nothing useful and simply runs the tool (or the "exists; pass `--force`" refusal). All three read Track R products from `case_studies/nepal_glof/pairs/`, `aoi_v2/` or `aoi_v2/scratch/`, which are no longer on disk — `pairs/` and `aoi_v2/` can be restored from the archive, the scratch trees must be regenerated by re-running the Track R chain. (`gslc_ionosphere.py` below is different: it has a real argparse interface and writes products.)
```bash
python -u tools/gunw_validation.py
python -u tools/gunw_validation.py --force
python -u tools/alignment_test.py
python -u tools/alignment_test.py --force
python -u tools/iono_transfer_test.py
python -u tools/iono_transfer_test.py --force
python tools/gslc_ionosphere.py --pair-dir /home/sharath/isce3/case_studies/nepal_glof/pairs/20260714_20260726/trackG --freq-a-prefix ifg_A_HH_8x8 --freq-b-prefix ifg_B_HH_8x8
```
**`tools/gunw_validation.py`** — validates all four workflows against the NISAR L2 GUNW of the same pair, over the AOI, aggregating our 5 m cells onto the GUNW's 20 m and 80 m cells by cell centre (a GUNW cell counts when ≥ 75 % of its cells are in the comparison mask). Reads the comparison layers (it imports `compare_four_way.py` as a module) and `case_studies/nepal_glof/L2_GUNW/NISAR_L2_*GUNW*_20260714T*_20260726T*.h5` — present locally. Writes `comparison_v2/gunw_validation/{gunw_validation.json, figures_gunw.json}` and `comparison_v2/report/fig/f2x_*.webp`.

**`tools/alignment_test.py`** — asks whether the RSLC-vs-GSLC difference is a registration difference: runs the estimator where it is valid, on RSLC data, comparing the reference against the geometry-only (`coarse_resample`) and rubber-sheet (`fine_resample`) secondaries, then placing the GSLC against both. Reads `aoi_v2/scratch/trackR/<tag>/{coarse,fine}_resample_slc/freqA/HH/coregistered_secondary.slc` and the comparison layers. Writes `comparison_v2/alignment/{alignment.json, figures_alignment.json, E1_rslc_control_chips.npz}` and `comparison_v2/report/fig/f12_*.webp`, `f13_*.webp`; `build_report.py` consumes the first two, so this runs before it.

**`tools/iono_transfer_test.py`** — asks whether a crop-first interferogram can be ionosphere-corrected to the full-tile benchmark's result, comparing three options (full-tile screen sliced to the crop; the crop's own ISCE3 screen; the crop's screen after the GSLC tool's cycle-class rule) at 9×8 unwrapped phase and on the 1×1 wrapped lattice. Reads the full-tile and cropped `RUNW_<tag>_unw9x8.h5`. Writes `comparison_v2/iono_transfer/iono_transfer.json`. No figures.
#### `tools/gslc_ionosphere.py`

Split-spectrum ionospheric phase screen for a **geocoded** (Track G) pair, driven by hand because ISCE3 has no ionosphere support in the GSLC path; both bands are unwrapped here first (snaphu), since the solve needs unwrapped phase. Needs `isce3_env`: it imports `isce3.atmosphere` and `snaphu`
| flag | default | meaning |
|---|---|---|
| `--pair-dir` / `--out-dir` | *required* / `<pair-dir>/ionosphere` | Track G pair directory holding the two band interferograms / output directory |
| `--freq-a-prefix` / `--freq-b-prefix` | `ifg_A_HH_8x8` / `ifg_B_HH_8x8` | main-band / side-band file prefix inside `--pair-dir` |
| `--f0` / `--f1` | igram sidecar JSON, else `1.239e9` / `1.2935e9` | freq A / freq B centre frequency [Hz] |
| `--nlooks` | `64.0` | equivalent independent looks in the coherence (the 8×8 box) |
| `--coherence-threshold` / `--median-filter-size` / `--sigma-km` | `0.5` / `15` / `10.0` | mask threshold, applied to both bands / median filter for mask conditioning / Gaussian filter sigma, in kilometres |
| `--cycle-search` | `3` | half-width of the integer 2π offset search per band (→ 7×7 candidates); `0` trusts snaphu's absolute level, which on this case was wrong by 2 cycles = 11.2 TECU |
| `--cycle-offsets M N` | off | force the 2π offsets for A and B instead of searching |
| `--ntiles` / `--nproc` | `4 4` / `4` | snaphu tiling / snaphu processes |
| `--force` | off | recompute a cached unwrap/solve |

Writes into `--out-dir`: `unw_A.tif`, `unw_B.tif`, `conncomp_A.tif`, `conncomp_B.tif`, `dispersive.tif`, `dispersive_filtered.tif`, `dispersive_tecu.tif`, `dispersive_sigma.tif`, `dispersive_sigma_filtered.tif`, `non_dispersive.tif`, `mask.tif` and `ionosphere.json`. A completed unwrap is reused unless `--force` is given.
### Regenerating the error logs inside the workflow docs: `tools/render_error_log.py`

It **generates** the text between `<!-- ERROR-LOG:BEGIN -->` and `<!-- ERROR-LOG:END -->` in six documents; that block is machine-written and must not be hand-edited, so edit the JSON evidence and re-render instead
| flag | default | meaning |
|---|---|---|
| `--doc {COMPARISON,OPS,WF1,WF2,WF3,WF4}` | *required, always* | which log to render — `WF1`…`WF4` → `docs/WF1_RSLC_FULL_TILE.md`, `docs/WF2_GSLC_FULL_TILE.md`, `docs/WF3_RSLC_CROPPED.md`, `docs/WF4_GSLC_CROPPED.md`; `COMPARISON` → `docs/COMPARISON.md`; `OPS` → `docs/OPERATIONS_AND_LESSONS.md`. Argparse enforces it even when you only want `--counts` |
| `--into PATH` / `--counts` | print to stdout / off | rewrite the marked block in this file in place / print entry counts per home and exit |
```bash
python tools/render_error_log.py --doc WF1 --counts
python tools/render_error_log.py --doc WF1 > /tmp/wf1_log.md
python tools/render_error_log.py --doc WF1 --into docs/WF1_RSLC_FULL_TILE.md
```
Reads `case_studies/nepal_glof/comparison/verification/`: `errors_W1_rslc_full.json` … `errors_W5_ops_and_comparison.json` (the evidence-backed transcript sweep), `errors_supplement.json` and `critic.json`. The critic's de-duplication is applied as MOVES and DROPS keyed on a substring of each entry title, so every entry appears in exactly one document and the others carry a one-line cross-reference — which is why you re-render **all six** after editing the evidence, not just the one you changed. With `--into`, a target missing the markers is an error and nothing is written. Pure stdlib; any Python 3 runs it.
### The archive: `tools/archive_to_gcs.py` and `tools/verify_archive.py`

Both drive the `gcloud` CLI and need a valid login (`gcloud auth login` is interactive: when the token expires, ask the user to run it). Both are pure stdlib, re-runnable and safe to repeat
```bash
python tools/archive_to_gcs.py --dry-run
python tools/archive_to_gcs.py
python tools/archive_to_gcs.py --manifest
python tools/verify_archive.py
```
#### `tools/archive_to_gcs.py`

Mirrors the work to `gs://s1-slc/nisar_workflow/` and regenerates the manifest that describes it
| flag | default | meaning |
|---|---|---|
| `--dry-run` / `--manifest` | off / off | print the sizes and the exact `gcloud` commands, write nothing / only regenerate and upload `README.md`, skipping the upload and the prune |

A full run, in order: (1) sizes every entry of the built-in plan — this tree, `CLAUDE.md`, `AGENTS.md`, `asc/env/`, both reports as PDF and HTML, and the two case-study trees; a local path that does not exist is printed as `MISSING, skipped` and does not fail the run. (2) uploads: `gcloud storage cp` for single files, `gcloud storage rsync -r` for directories. (3) prunes objects that do not belong, `code/nisar_workflows/**/__pycache__/**` and `nepal_nisar_ascending/coreg/**/isce3/pairs/**/*.h5` (the RIFG copies that were hard links locally), because `rsync --exclude` did not match these reliably. (4) reads the bucket back with `gcloud storage ls -l -r` and builds the manifest from what is actually there; an empty read-back aborts with `could not read the bucket back (is the gcloud login valid?)`. (5) uploads the manifest to `gs://s1-slc/nisar_workflow/README.md` and `gs://s1-slc/nisar_workflow/nepal_nisar_ascending/report/ARCHIVE_README.md`, and leaves a local copy at `case_studies/nepal_nisar_ascending/report/ARCHIVE_README.md`. `--dry-run` stops after step 1; `--manifest` skips steps 2 and 3. The manifest prose quotes live numbers out of `case_studies/nepal_nisar_ascending/report/glof_event/analysis.json`, so `glof_event_analysis.py` must have run before either a full upload or `--manifest`. The seven input RSLCs are **not** copied into this archive — they stay in `gs://s1-slc/nepal/nisar/ascending/tile_1/SLC/`.

**`tools/verify_archive.py`** — compares the bucket against the local tree, prefix by prefix; no argparse, so any argument (including `--help`) is ignored and the full comparison runs. Walks the same plan as `archive_to_gcs.py` and, for each prefix, builds both file-name sets and diffs them: it reports how many files are missing remotely and how many are extra, names the first three of each, and additionally requires the byte totals to agree within 1 KiB. Honours the same exclude patterns, so the pruned objects are not counted as missing. Read-only on the bucket and on the local tree. Exits `0` when every prefix matches, `1` otherwise, with `all prefixes verified` or `N prefix(es) do not match` as the last line.

## Where to go next

| you want | read |
|---|---|
| what is finished, what was decided, what was withdrawn | `STATE.md` |
| the index to every document | `docs/README.md` |
| the two modules in depth | `docs/COREG_MODULE.md`, `docs/TIMESERIES_MODULE.md` |
| why the pipeline is shaped this way | `docs/PIPELINE_DESIGN.md` (superseded in part — read its banner), `docs/COMPARISON.md` |
| running long jobs on a VM, and the failure log | `docs/OPERATIONS_AND_LESSONS.md` |
| questions a later reader asked, answered from disk | `docs/VM_HANDOVER_ANSWERS.md`, `docs/VM_HANDOVER_ANSWERS_2.md` |
| the archived products and how to restore them | `gs://s1-slc/nisar_workflow/README.md` |

Working context for an agent or a person picking this up — environments, data locations, conventions, the mistakes
already paid for — is in `../../CLAUDE.md` and this directory's `CLAUDE.md`.
