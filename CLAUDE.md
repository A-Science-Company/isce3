# Working context for Claude Code

This is an upstream **ISCE3 checkout** (branch `s1-nisar-setup`) that also carries our own NISAR InSAR work. The upstream
code is untouched; our work lives in two places:

- `asc/nisar_workflows/` — the pipeline: drivers, modules, tools, configs, docs
- `case_studies/` — the data and results of two studies

**Read `asc/nisar_workflows/STATE.md` first.** It is the resume point: what is done, what is running, what was decided, and a
list of withdrawn claims that must not be re-introduced. Then `asc/nisar_workflows/docs/README.md` indexes everything else.

## Environments

| env | what for |
|---|---|
| `conda activate isce3_env` | ISCE3 0.25.12 processing, coregistration, geometry |
| `conda activate insar_ts` | MintPy 1.6.4, snaphu-py 0.4.1, dolphin 0.42.5, GDAL |
| `~/.venvs/report-pdf/bin/python` | report PDF rendering (Playwright) |

`nisar_timeseries.py` launches each stage in the right environment itself, so it runs from either.

## The two modules

```bash
cd asc/nisar_workflows
python nisar_coreg.py      -c coreg_configs/<case>.yaml   show|status|progress|run [--detach]
python nisar_timeseries.py -c ts_configs/<case>.yaml      show|status|run [--stage X] [--detach]
```

`show` prints the resolved plan and every parameter without running anything — use it before any run. Documentation:
`docs/COREG_MODULE.md`, `docs/TIMESERIES_MODULE.md`. Processing parameters live in `coreg_configs/defaults.yaml` and
`ts_configs/defaults.yaml`; the values actually used are frozen in a `params.json` beside every product, and a run whose
parameters differ is refused rather than mixed.

## Where the data is

| what | where |
|---|---|
| Archive of everything (both studies, code, reports) | `gs://s1-slc/nisar_workflow/` — its `README.md` is the manifest |
| Input NISAR L1 RSLCs (7 granules, provisional tier) | `gs://s1-slc/nepal/nisar/ascending/tile_1/SLC/` — take the `_PR_` files, not the `_UR_` copy of 0831 |
| Study 2 working tree (local) | `case_studies/nepal_nisar_ascending/` — crop, coreg, timeseries, L2_GUNW, report |
| Study 1 (local, mostly deleted after archiving) | `case_studies/nepal_glof/` — comparisons, GUNW, logs only |

Archive tooling: `tools/archive_to_gcs.py` (upload, prune, regenerate the manifest) and `tools/verify_archive.py` (compare
every prefix with the local tree). Both are re-runnable and safe to repeat.

## How the user works

- **Ask before changing any processing parameter.** Validated values are validated; propose, don't switch.
- **Output identity rule:** anything that changes a product's bytes belongs in its filename. Silent clobbering has bitten this
  project repeatedly. See the memory entry `nisar-output-identity-rule`.
- **Never overwrite finished products.** Modules refuse without `--force`; keep it that way.
- Long runs go in tmux (`--detach` does this), never in the foreground.
- Commit or push only when asked.
- `gcloud auth login` is interactive: when the token expires, ask the user to run it.
- Credentials are never baked into images; mount `~/.netrc`.

## Gotchas already paid for

- **Flattening across crops.** Each date is cropped to its own window, so the two images of a pair start at different slant
  ranges; that constant must be flattened along with the geo2rdr offset. 8 range samples is half a phase cycle at L-band and
  inverts the interferogram. Caught by comparing against ISCE3's own RIFG — keep that check.
- **Snaphu puts ice in component 0**, so MintPy's default `maskDataset = connectComponent` silently drops the glacier from
  every interferogram. The temporal-coherence threshold is not the binding constraint there.
- **`gcloud storage rsync --exclude` matched nothing** in practice; prune after syncing instead of trusting it.
- **Urgent-response (UR) GUNWs carry no troposphere cubes.** Check layers per product before assuming a correction exists.
- **MintPy's automatic reference point is random** above the coherence threshold, so reruns differ. We pick it deterministically.
- Native-Doppler radar windows moved early crops by ~15 km: always use zero Doppler for radar-domain windows.

## Status

Both studies are finished, archived and verified (2026-09-18). Reports: `nepal_glof_four_workflow_report.pdf` and
`nepal_glof_nisar_event_report.pdf` in the repo root, with sources under each study's `report/`. Open threads are listed at
the end of `STATE.md` — the main ones are the descending track (48/74) for a second look direction, amplitude offset tracking
for glacier motion where phase fails, and a provisional GUNW for 31 Aug – 12 Sep to fill the missing troposphere correction.
