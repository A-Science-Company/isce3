# Running a NISAR case

One case directory, one `case.yaml`, one command:

```bash
conda activate isce3_env
cd ~/isce3/asc/nisar_workflows
python run_case.py -c ~/isce3/case_studies/<NAME>/case.yaml show     # the plan; touches nothing
python run_case.py -c ~/isce3/case_studies/<NAME>/case.yaml run --detach
```

`run_case.py` chooses which of the four NISAR workflows to run, generates the module configs, runs the modules in
order, and pushes the products to GCS. It does no processing of its own: the work, the logs, the per-unit manifests
and the parameter gates belong to `nisar_coreg.py` and `nisar_timeseries.py`, which are the validated modules from the
Nepal GLOF study. If you want to drive those directly instead, their reference is
[`nisar_workflows/README.md`](nisar_workflows/README.md).

> **Status.** The modules and every processing parameter below come from runs that were measured and checked against
> ISCE3's own products (see `nisar_workflows/docs/`). `run_case.py` itself is new: it has been exercised with `show`,
> `status`, `upload --dry-run` and `run --dry-run` over all eight workflow × mode combinations, and the modules it
> drives were re-run for real on the finished Nepal case after these changes (every unit skipped, no gate refused,
> no product touched). `run_case.py` has not yet driven a new case
> from empty to product. Expect the first real case to surface something; the modules refuse rather than overwrite,
> so the cost of that is time, not data.
>
> **The split-spectrum ionosphere: the method is validated, the module path is not.** ISCE3's `main_side_band` solve
> has been run on this data twice — on the full tile (WF1: a 5911 × 6780 screen) and on a crop (WF3:
> `RUNW_20260714_20260726_A_HH_1x1_unw9x8.h5`, 1367 × 2805) — and the defaults in `rslc.unwrap` / `rslc.ionosphere`
> are the values from that crop run's config. What is new is the plumbing: reaching it from a case config (the module
> used to pin `RIFG`, which made the stage unreachable), and consuming the screens in the time series. ISCE3's own
> `InsarRunConfig` accepts the generated runconfig and fills in the side band, and the screen-to-grid slice is
> unit-tested against the real crop geometry, but no product has come out of this path yet.

The older documents here — [`SETUP.md`](SETUP.md) (installing ISCE3) and [`WORKFLOWS.md`](WORKFLOWS.md) (the original
architecture study) — are history and design, not operating instructions.

---

## 1. Install

Skip to §3 if `bash asc/env/verify.sh` already prints `ALL CHECKS PASSED` **and**
`conda run -n isce3_env python asc/nisar_workflows/tools/apply_patches.py --check` prints `already applied` four
times. Those are two separate checks — `verify.sh` reports the patch status but does not fail on it, so an unpatched
environment still passes it.

### 1.1 conda, if the machine has none

Miniforge (conda-forge's own installer — no defaults channel, no licence question):

```bash
curl -L -O https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash Miniforge3-Linux-x86_64.sh -b -p $HOME/miniforge3      # -b = batch, no prompts
$HOME/miniforge3/bin/conda init bash && exec bash
conda config --show channels                                 # must be conda-forge only
```

This machine runs Miniforge3 26.5.3-0 (conda 26.5.3, mamba 2.5.0, libmamba solver). `releases/latest` is newer now
(26.7.2-0); that difference has never mattered here, but it is why the version you get may not match.

### 1.2 The two environments

```bash
cd ~/isce3
conda env create -f asc/env/isce3_env_export.yml    # ~2 GB, 403 packages, a few minutes
conda env create -f asc/env/insar_ts_export.yml     # 374 packages
```

| environment | holds | used by |
|---|---|---|
| `isce3_env` | python 3.12.14, isce3 0.25.12 (`_cpu`), gdal 3.12.4, numpy 1.26.4, scipy 1.12.0, snaphu-py 0.4.1, compass 0.5.6, sardem | `run_case.py`, `nisar_coreg.py`, both drivers, the subsetter, and the time series' `geometry` stage |
| `insar_ts` | python 3.11.16, mintpy 1.6.4, dolphin 0.42.5, snaphu-py 0.4.1, gdal 3.12.3, numpy 2.4.6, scipy 1.17.1 — **no isce3** | the `ifg`, `unwrap`, `corrections` and `mintpy` stages |

**Use the `_export` files, not `isce3_env.yml`.** The `*_export.yml` are pinned snapshots of the environments this
work was validated on and they reproduce it exactly — `isce3 0.25.12 py312h2b9bced_0_cpu`, `python 3.12.14`,
`mintpy 1.6.4`, `dolphin 0.42.5`, both verified to solve on 2026-09-27. The curated `asc/env/isce3_env.yml` is the
hand-written spec the env was originally built from; solved today it drifts to **isce3 0.25.17** and gdal 3.13.3,
which nothing here has been run on. (`asc/env/dolphin_env.yml` is an older, differently solved time-series stack
that no longer exists on this machine; `SETUP.md` still refers to it. Ignore it.)

A re-created environment is version-identical but not byte-identical: the exports pin versions, not build strings,
so ~56 packages — the compiler runtime and the whole BLAS chain among them — come back with newer builds.

**Why two.** Not because they conflict: `compass` wants `scipy <1.13` and `dolphin` wants `>=1.12`, and 1.12.x
satisfies both. Combining them pins the stack to numpy 1.x and drags isce3 off the validated 0.25.12, and the
pipeline hardcodes the split anyway. Keeping them apart costs about 3 GB.

**GPU.** None is needed and none is used. The `_cuda` builds of isce3 require the `__cuda` virtual package, so a
host without a GPU resolves to `_cpu` on its own. On a CUDA host the solver may pick either, so pin it:
`isce3=0.25.12=*_cpu`.

### 1.3 Patch the installed isce3

Four upstream files are overlaid. They matter for ISCE3's own unwrapping and masking at full resolution — the stock
`generate_insar_mask` is a Python double loop that needs ~58 GB on a full tile, and stock `unwrap` materialises the
interferogram and coherence as arrays (34.6 GB full tile at 1×1). **At the shipped 9×8 looks the stock code re-
multilooks first and reads ~46 MB**, so a cropped 9×8 run does not strictly need them — apply them anyway, because
nothing in the pipeline stops you moving to 1×1.

```bash
conda run -n isce3_env python asc/nisar_workflows/tools/apply_patches.py --check   # report only
conda run -n isce3_env python asc/nisar_workflows/tools/apply_patches.py           # apply
conda run -n isce3_env python asc/nisar_workflows/tools/apply_patches.py --revert  # undo
```

The script is idempotent and keeps the first file it replaces as `<name>.orig`. It refuses only when the installed
isce3 no longer exports a symbol a patched file imports, and it names which. It does **not** check that the file it
overwrites is stock — so after any isce3 upgrade, re-run `--check` and treat `.orig` as the pre-*first*-patch file,
not the pre-upgrade one.

### 1.4 Verify

```bash
bash asc/env/verify.sh        # ~14 s; must end in ALL CHECKS PASSED
```

It checks the isce3 version and that the build is not CUDA, imports nine isce3 subpackages and the nisar workflows,
runs a compiled-extension smoke test and a complex64 HDF5 round-trip, checks gdal/numpy/scipy/h5py/pyproj/rasterio
versions and that five CLIs are on PATH. It **prints** the patch status and the CUDA flag without failing on either,
so read those two lines yourself.

## 2. Before your first case

### 2.1 Point the config at your interpreters

`asc/nisar_workflows/ts_configs/defaults.yaml` hard-codes absolute paths to *this* machine's environments. On any
other machine, edit those two lines first or the time series fails at its first stage:

```yaml
envs:
  isce3_python: /home/sharath/miniforge3/envs/isce3_env/bin/python
  ts_python:    /home/sharath/miniforge3/envs/insar_ts/bin/python
```

```bash
B=$(conda info --base)
sed -i "s#/home/sharath/miniforge3#$B#" asc/nisar_workflows/ts_configs/defaults.yaml
```

A case config can override them per case (`envs:` in `case.yaml`), which is the better route if several people share
the checkout.

### 2.2 Earthdata credentials

The DEM is fetched from `urs.earthdata.nasa.gov`. Both `sardem` (`nisar_dem.py`) and our `nisar_wf/dem.py` read
`~/.netrc` through `expanduser`, so the file has to be in `$HOME` — a path cannot be passed to them.

```bash
cat >> ~/.netrc <<'EOF'
machine urs.earthdata.nasa.gov
    login YOUR_EARTHDATA_USERNAME
    password YOUR_EARTHDATA_PASSWORD
EOF
chmod 600 ~/.netrc
```

`chmod 600` is hygiene; nothing in this pipeline enforces it, so a world-readable file will run and quietly expose
your credentials. `run_case.py` can install the file for you from a bucket — set `netrc:` in the case config — and it
chmods what it writes. Check it took:

```bash
conda run -n isce3_env python -c "
import logging, sys; sys.path.insert(0, 'asc/nisar_workflows')
from nisar_wf.dem import check_earthdata_netrc
print('usable:', check_earthdata_netrc(logging.getLogger()))"
```

### 2.3 The rest

| | why | check |
|---|---|---|
| `gcloud` | only for `upload:` and for fetching inputs or a `netrc:` from a bucket. `gcloud auth login` is interactive, and the token expires | `gcloud version` |
| `tmux` | `--detach` starts the run in a tmux session and returns | `tmux -V` |
| disk | the finished 7-date Nepal case occupies **242 GB** (174 GB of it the input RSLCs, 14 GB GUNWs, 12 GB crops, 35 GB coreg, 7.5 GB time series). Budget ~350 GB per case | `df -h .` |
| RAM | 31 GB was enough for every crop workflow and for the full tile *with* the patches; a 3.9 GB box was killed | `free -g` |
| `PROJ_DATA` | **do not set it.** conda's activation scripts export it (and `GDAL_DATA`) for you, and both `conda activate` and `conda run -n` run them. Invoking `…/envs/isce3_env/bin/python` directly, without activation, is what breaks PROJ | `conda run -n isce3_env python -c "from pyproj import CRS; print(CRS.from_epsg(32645).name)"` |

## 3. Lay out the case

```
case_studies/<NAME>/
├── SLC/                     NISAR L1 RSLC granules (.h5). However they got here.
├── geometries/<NAME>.kml    the AOI polygon
├── case.yaml                the config below
├── _run/                    generated module configs — written by run_case.py, do not edit
├── aux/dem/                 DEM, staged automatically on the first run
├── crop/  coreg/  timeseries/    products
└── L2_GUNW/                 optional: NISAR GUNWs, only for the atmospheric corrections
```

Only `SLC/`, `geometries/` and `case.yaml` are yours to create. Everything else appears as the run proceeds.

```bash
NAME=nepal_demo
mkdir -p ~/isce3/case_studies/$NAME/{SLC,geometries}
gcloud storage cp 'gs://s1-slc/nepal/nisar/ascending/tile_1/SLC/*_PR_*.h5' ~/isce3/case_studies/$NAME/SLC/
cp my_aoi.kml ~/isce3/case_studies/$NAME/geometries/$NAME.kml
```

Two things about the SLCs:

- **The filename matters.** Dates are read from the granule name, which must look like
  `NISAR_L1_<tier>_RSLC_*_<YYYYMMDD>T<HHMMSS>_*.h5`. Renamed files are invisible; the error says so and lists them.
- **One tier at a time.** `inputs.tier` is `PR` (provisional) or `UR` (urgent response). A directory holding both
  copies of the same date is fine — only the configured tier is read. UR products carry no troposphere cubes.

**The AOI name is part of the product names** (`coreg/RSLC_ref20260726_AHH_<kml stem>/`). Renaming the KML renames
the stack, and the old one stays on disk. Pick the name once.

**`.netrc`.** The DEM is fetched from `urs.earthdata.nasa.gov`, whose credentials must be in `~/.netrc` — the ISCE3
code calls `expanduser("~/.netrc")`, so a path cannot be passed to it. Put the source in the config and the runner
installs it (mode 600) when `~/.netrc` is missing:

```yaml
netrc: gs://s1-slc/secrets/.netrc      # or a local path
```

An existing `~/.netrc` is never touched unless you pass `--force-netrc`.

## 4. The case config in full

Start from the annotated template, which lists every key with its default:

```bash
cp ~/isce3/asc/nisar_workflows/case_template.yaml ~/isce3/case_studies/$NAME/case.yaml
```

Everything except `workflow` and `mode` has a default. This is the whole file for a typical case:

```yaml
case: nepal_demo                        # name; default = the directory name
workflow: cropped_RSLC                  # RSLC | cropped_RSLC | GSLC | cropped_GSLC
mode: interferogram                     # coregistration | interferogram

slc_dir: SLC
aoi_kml: geometries/nepal_demo.kml
netrc: gs://s1-slc/secrets/.netrc

dates: {start: 20260620, end: 20260819, reference: null}   # null start/end = everything on disk
resolution_m: 40                                           # or looks: {azimuth: 9, range: 8}
network: {max_connections: 3, max_temporal_baseline_days: null, pairs: []}
corrections: {ionosphere: split_spectrum, troposphere: gunw, solid_earth_tides: gunw}

upload: {destination: gs://s1-slc/nisar_workflow/cases, enabled: true}
```

Two files are generated from it into `<case>/_run/` — `coreg.yaml` for `nisar_coreg.py` and `timeseries.yaml` for
`nisar_timeseries.py` — by `show`, `status` and `run`. **`upload` does not re-render them**: it uses what is there,
so run `show` after editing the config and before `upload`, or you will push the previous plan's product list.

| key | default | what it does |
|---|---|---|
| `workflow` | — | which of the four: `RSLC`, `cropped_RSLC`, `GSLC`, `cropped_GSLC` |
| `mode` | — | `coregistration` stops at the registered stack; `interferogram` goes on to interferogram + coherence |
| `slc_dir` | `SLC` | relative to the case directory, or absolute |
| `aoi_kml` | — | required for a cropped workflow and for any RSLC interferogram |
| `netrc` | `null` | `gs://…` or a local path, installed as `~/.netrc` if that is missing |
| `inputs` | `{tier: PR, frequency: A, polarization: HH}` | which granules and which band |
| `dates.start` / `.end` | `null` | selects files **already on disk**; nothing is ever downloaded |
| `dates.reference` | `null` | RSLC reference date; null = the middle date, which minimises the summed temporal baseline |
| `resolution_m` | `null` | the preset: metres on the ground, resolved to looks from this granule's own spacing |
| `looks` | `{azimuth: 9, range: 8}` | RSLC interferogram looks **and** the lattice the crop window is snapped to. Give this *or* `resolution_m` |
| `gslc` | `{posting_m: 5, looks_y: 8, looks_x: 8}` | GSLC map grid in metres, then looks on that grid (8×8 on 5 m = 40 m) |
| `network` | `{max_connections: 3, …}` | which pairs. `pairs: ["20260714_20260726", …]` overrides it |
| `corrections` | all `none` | per layer: `ionosphere: gunw \| split_spectrum \| none`, `troposphere`/`solid_earth_tides`: `gunw \| none`. `false`/`true` are shorthand for all-none/all-gunw |
| `tag` | `null` | appended to every product name, to run a variant beside a finished one |
| `upload.destination` | `null` | `gs://bucket/prefix`; null keeps everything local |
| `envs` | auto | interpreters, if the conda paths differ from this VM |
| `advanced.coreg` / `.timeseries` | `{}` | merged verbatim over the generated module configs — the escape hatch for any parameter the table above does not expose |

### Which settings you can change afterwards, and which you cannot

This is the part worth reading twice, because the code enforces it and the error messages assume you know it.

**In a product's name** — change it and you get a *new* product beside the old one, nothing is overwritten:

| setting | where it appears |
|---|---|
| reference date, frequency, polarisation, AOI name (cropped workflows), tier (when not `PR`), `tag` | the stack directory, e.g. `coreg/RSLC_ref20260726_AHH_glof_bigger_aoi` |
| crop buffers, alignment looks, tier, polarisation — each only when not the default | the crop directory, e.g. `crop/glof_bigger_aoi_UR` |
| interferogram looks | `ifg/RIFG_<ref>_<sec>_A_HH_1x1.h5` |
| GSLC posting and interferogram looks | the GSLC stack name and `ifg_A_HH_8x8.*.tif` |
| unwrap looks and product type | `runw/RUNW_<ref>_<sec>_A_HH_1x1_unw9x8.h5` |

**Locked in a `params.json`** — change it and the next run is *refused* (exit 2) rather than mixing bytes. Give the
case a `tag:`, or restore the value:

| file | locks |
|---|---|
| `coreg/<stack>/params.json` | mode, tier, frequency, polarisation, DEM path, crop, and all of `rslc` except `interferogram`, `unwrap` and `ionosphere` (those three are in product names instead). RSLC also locks the reference date and `run.block_budget_mb`; GSLC locks `gslc` (minus `interferogram`) and the AOI bbox instead |
| `crop/<aoi>/params.json` | the KML's SHA-1, polarisation, the three buffers, and the alignment looks and tier when not default |
| `coreg/<stack>/runw/params.json` | the unwrap and ionosphere solver settings that the RUNW filename does not carry |
| `timeseries/<stack>/<name>/params.json` | stack, reference, dates, pairs, AOI SHA-1, margin, looks, unwrap settings, correction sources, flattening sign |

**Free to change at any time**: `upload.*`, `envs.*`, `netrc`, `run.jobs`, `--jobs`, and everything under MintPy —
that stage is regenerated as a whole and moves the previous run to `mintpy_replaced_<stamp>`.

A few behaviours that are easy to get wrong:

- **A single date is fine for GSLC**, which geocodes it; RSLC needs at least two and refuses with exit 2.
- **`network.pairs` is fully validated only for RSLC.** The time series checks that every entry names selected dates
  and that the list connects them all. In GSLC mode only the `YYYYMMDD_YYYYMMDD` shape is checked, so a typo passes
  `show` and surfaces later — read the pair list `show` prints.
- **Renaming the KML** starts a new crop and a new stack for the `cropped_*` workflows. On an uncropped RSLC
  interferogram the stack is named `fulltile`, so only the KML's SHA-1 changes and the rename costs nothing.
- **`envs:` left null** falls back to the interpreter running `run_case.py` — but only for `nisar_coreg.py`, the
  drivers and the subsetter. The time series always resolves its interpreters from `ts_configs/defaults.yaml`
  (§2.1), which is why that file has to be right on a fresh machine.

**Ask before changing a processing parameter.** The values in `nisar_workflows/coreg_configs/defaults.yaml` and
`ts_configs/defaults.yaml` are the validated ones. `advanced:` exists so you *can* change them, not so you should.

### Resolution, per workflow

Give **either** `resolution_m` **or** the looks. Giving both is an error unless they agree — the looks are what ends
up in the product names and the parameter locks, and a "40 m" label could mean different looks on a different track,
so only the resolved integers are ever recorded.

For **RSLC**, `resolution_m` is turned into looks from the granule itself: azimuth from `sceneCenterAlongTrackSpacing`
and ground range from the slant spacing over sin(incidence), with the incidence averaged **over your AOI**. (A crop
keeps the whole swath's geolocation grid; averaging all of it shifts the answer ~4%, enough to change the rounded
looks.) On the Nepal ascending track that gives:

| `resolution_m` | looks (az × rg) | what you actually get |
|---|---|---|
| 20 | 4 × 4 | 17.8 m × 19.8 m |
| 40 | 9 × 8 | 40.1 m × 39.6 m ← the validated setting |
| 60 | 13 × 12 | 57.9 m × 59.4 m |
| 80 | 18 × 16 | 80.2 m × 79.2 m |
| 100 | 22 × 20 | 98.0 m × 99.0 m |

`show` prints the resolved looks, the resolution they really give, and the incidence it used. Note the ground pixel
is nominal at the AOI mean: incidence runs 37.5–40.7° across this AOI, so the real range pixel varies about ±3.5%.
The same looks snap the crop window, so the cropped multilook grid lands exactly on the full tile's — that coupling
is why looks are one setting and not two.

**Nothing here geocodes.** An RSLC product stays in radar coordinates with its geometry layers (`lon`, `lat`, `hgt`)
beside it, which is what you need to geocode it later, wherever and whenever you want.

For **GSLC** the geocoding is a spatial posting in metres (`posting_m: 5`) — that is pixel *spacing*, not resolution,
since the native frequency-A resolution is about 5.7 m — and the interferogram then takes looks on that map grid.
`resolution_m` there is simply `resolution_m / posting_m`, and it must come out a whole number or the run is refused
(a fractional look count would silently truncate the data while naming the file with the fraction).

## 5. Run it

```bash
cd ~/isce3/asc/nisar_workflows
C=~/isce3/case_studies/nepal_demo/case.yaml

python run_case.py -c $C show            # resolved dates, reference, products, the exact commands
python run_case.py -c $C run --dry-run   # the same, plus each module's own plan, still writing nothing
python run_case.py -c $C run --detach    # for real, in tmux
python run_case.py -c $C status          # per date, per pair, per product
python run_case.py -c $C upload          # push again later, or after --no-upload
```

| verb | what it does |
|---|---|
| `show` | resolves everything and prints it, including both modules' own `show`. The only thing it writes is the generated configs in `_run/`; add `--dry-run` and it writes nothing at all. |
| `run` | netrc → coregistration → (interferogram) → upload. Resumable: finished units are skipped. |
| `status` | what is done, what failed, what is missing, and the size of each product |
| `upload` | pushes the mode's products; exit 3 if a product does not exist yet |

Flags: `--detach` (tmux; prints the session and log path), `--dry-run`, `--no-upload`, `--force` (redo units already
verified done), `--force-netrc`, `--jobs N` (units in parallel).

Exit codes are the same everywhere: **0** ok, **1** a unit failed, **2** a config or parameter problem, **3** a
missing prerequisite. A shell caller can branch on them.

Long runs go in tmux. `--detach` does that for you:

```bash
python run_case.py -c $C run --detach
tmux attach -t case_nepal_demo_interferogram      # the name it prints
tail -f ~/isce3/case_studies/nepal_demo/_run/run_*.detached.log
python nisar_coreg.py -c ~/isce3/case_studies/nepal_demo/_run/coreg.yaml progress   # per-unit stage timeline
```

## 6. The four workflows × two modes

| workflow | mode | what runs | what you get |
|---|---|---|---|
| `RSLC` | coregistration | crop off; ISCE3 insar per pair on the full tile | secondaries resampled onto the reference radar grid, + geometry and offsets |
| `RSLC` | interferogram | + `nisar_timeseries.py --through ifg` | multilooked interferogram and coherence per pair, inside the AOI |
| `cropped_RSLC` | coregistration | every granule cut to the AOI first, then the same chain | the same, ~6× cheaper |
| `cropped_RSLC` | interferogram | the validated path — this is what the Nepal study ran | interferogram + coherence per pair |
| `GSLC` | coregistration | each date geocoded onto one pinned map grid | one GSLC per date. **Not coregistered** — registered by geometry only |
| `GSLC` | interferogram | + Track G step 6 | map-domain interferogram, coherence, per-date amplitude |
| `cropped_GSLC` | coregistration | granules cut to the AOI, geocoded onto the AOI grid | one small GSLC per date |
| `cropped_GSLC` | interferogram | + Track G step 6 | the WF4 product |

Two things worth knowing before choosing:

- **RSLC is the quality benchmark.** GSLC dates are aligned by geometry alone: about 5% coherence loss plus a carrier
  phase term (p95 ≈ 17 mm), and they cannot be realigned afterwards. Use GSLC when you want map-domain products fast,
  not when you want the best phase.
- **Cropping first is equivalent for RSLC** (phase correlation R = 0.994 against the full tile, coherence identical)
  and much cheaper. What it loses is the absolute ionosphere level, which is unreferenced per crop. Relative
  deformation is unaffected.

The four recipes, complete:

```bash
cd ~/isce3/asc/nisar_workflows
C=~/isce3/case_studies/nepal_demo/case.yaml

# WF1 full-tile RSLC   -> workflow: RSLC          + mode: coregistration or interferogram
# WF2 full-tile GSLC   -> workflow: GSLC          + mode: coregistration or interferogram
# WF3 cropped RSLC     -> workflow: cropped_RSLC  + mode: coregistration or interferogram
# WF4 cropped GSLC     -> workflow: cropped_GSLC  + mode: coregistration or interferogram
python run_case.py -c $C show && python run_case.py -c $C run --detach
```

Only two lines of `case.yaml` change between them. To run more than one on the same data, give each a `tag:` — or
just a different `workflow:`, since the workflow is already in the product name.

## 7. Ionosphere and troposphere

L-band phase carries a real ionospheric term, so an uncorrected NISAR interferogram is not a deformation measurement.
There are two places a correction can come from, and `corrections:` names which one per layer.

| layer | `gunw` | `split_spectrum` |
|---|---|---|
| ionosphere | the GUNW's own `ionospherePhaseScreen`. Free (seconds to sample), needs a GUNW covering every date pair | ours, solved from bands A and B. Needs no GUNW; costs about 1.5 h per pair |
| troposphere | the GUNW's wet + hydrostatic cubes — ECMWF HRES run through RAiDER, on a 500 m grid at 20 heights | not implemented |
| solid-earth tides | the GUNW's cube, same grid | not implemented |

**On "80 m" GUNW ionosphere.** The screen is stored on the 80 m interferogram grid, but that is sampling, not
resolution. Two different scales are involved and they are worth keeping apart:

- The producer's dispersive filter sets the *smallest* scale that survives: σ ≈ 245 m in range × 2.6 km in azimuth.
- The screen that comes out varies far more slowly than that, because the real ionosphere over a 345 × 338 km scene
  is dominated by a large-scale gradient. Measured on the product: the autocorrelation is still above 0.95 at 20 km
  and reaches 1/e at roughly 90 km.

So the 80 m grid is oversampling a field with tens of kilometres of structure. Computing it ourselves does **not**
buy a sharper screen — with ISCE3's defaults it is the other way round: ISCE3's Gaussian is σ 33 px on the solve
grid, about 6.6 km in range at 9 × 8 looks, i.e. coarser in range than the GUNW's own filter. What computing it
ourselves buys is independence from GUNW availability, and control of the smoothing — which is a setting:

```yaml
corrections: {ionosphere: split_spectrum}
ionosphere:
  sigma_range_m: 3000        # null = ISCE3's default of 33 pixels on the solve grid
  sigma_azimuth_m: 1000
```

ISCE3 takes the Gaussian in **pixels of the solve grid**, which for `main_side_band` is the frequency-B grid at your
unwrap looks — so one pixel is (azimuth looks × azimuth spacing) by (range looks × slant spacing × the A/B band
ratio, 8 here), and the physical smoothing moves with your looks. Giving it in metres lets `run_case.py` do that
conversion against the granule, and `show` prints both:

```
iono range   solve pixel 316.7 m ground; sigma 33 px = 10.45 km ground, kernel 100 px = 31.7 km  (ISCE3 default)
iono azimuth solve pixel 40.1 m; sigma 33 px = 1.32 km, kernel 100 px = 4.0 km  (ISCE3 default)
```

Mind the convention when comparing with the docs: `WF3_RSLC_CROPPED.md` quotes σ ≈ 6.6 km in **slant** range, which
is the same thing as 10.45 km on the ground at this incidence. Asking for less smoothing than one solve pixel is
refused (ISCE3's own bound is σ ≥ 1 px), and a kernel under 2σ truncates the Gaussian, which the driver warns about.
Going much below the screen's real scale mostly amplifies the ~17× noise that the dispersive separation introduces —
which is why the filter is not optional here.

**What a split-spectrum run does.** `corrections: {ionosphere: split_spectrum}` turns on the ISCE3 ionosphere inside
the coregistration: each pair becomes a RUNW instead of a RIFG, unwrapped at your interferogram looks, carrying
`ionospherePhaseScreen`. Because coregistration is a star — every pair is (reference, date) — each screen already
*is* that date's ionosphere relative to the reference, so a 5-date series needs 4 solves, not one per interferogram.
The time series then slices those screens straight onto its grid (no interpolation: same crop, same looks) and
subtracts them.

What it costs and what to know before turning it on:

- **~1.5 h per pair** on a crop (the measured WF3 crop chain through RIFG + 9×8 unwrap + ionosphere was 1 h 29 m),
  against ~80 min for the plain RIFG. Disk goes up by about 17 B per reference pixel, mostly the frequency-B sub-run.
- **Frequency A is required** (it solves A against the B side band). The crops already carry B, aligned.
- **A crop's screen level is offset** from the full tile's by exactly one frequency-B cycle (−5.35 TECU) while the
  shape agrees to r = 0.99945. The level is a constant, so it cancels once the series is referenced to a point —
  but do not compare absolute TEC between crops.
- When GUNWs are also present, the corrections stage **cross-checks** the two independent screens per pair and
  records the correlation in `qa/corrections.json`. A negative correlation there means a sign convention is wrong;
  it is logged loudly rather than absorbed.

## 8. What comes out

```
case_studies/<NAME>/
├── crop/<aoi>/<date>.h5                              cropped granules (cropped workflows)
├── coreg/RSLC_ref<date>_<F><P>_<aoi|fulltile>/
│   ├── slc/<date>.slc + .hdr                         the coregistered stack
│   ├── geometry/{lon,lat,hgt}.rdr                    reference-grid geometry
│   ├── ifg/RIFG_<ref>_<sec>_<F>_<P>_1x1.h5           ISCE3's own interferogram, the QA reference
│   ├── runw/RUNW_<ref>_<sec>_<F>_<P>_1x1_unw9x8.h5   only with split_spectrum: carries ionospherePhaseScreen
│   ├── runw/params.json                              the solver settings those screens were made with
│   ├── offsets/<sec>_culled_{az,rg}_offsets          the rubber sheet
│   ├── status/<unit>.json, params.json, stack_manifest.json
│   └── logs/
├── coreg/GSLC_<F><P>_<aoi|fulltile>_<posting>m/
│   ├── gslc/<date>_gslc_freq<F>.h5                   one per date
│   └── isce3/pairs/<ref>_<sec>/trackG/ifg_<F>_<P>_<ly>x<lx>.{igram,coh,nlooks,amp}.tif
└── timeseries/<stack>/ifg/
    ├── geometry/geometryRadar.h5, meta.json
    ├── pairs/<d1>_<d2>/{ifg.int, coh.cor} + .hdr     the RSLC interferogram product
    ├── status/, params.json, qa/
    └── logs/
```

`ifg.int` is a flat complex64 raster with an ENVI `.hdr` beside it — `gdalinfo`, numpy or MintPy all read it.
Interferograms are `s1·conj(s2)`, the ISCE3/ISCE2 convention.

**Everything that changes a product's bytes is in its name** — looks, frequency, polarisation, posting, AOI,
reference date, tier, tag. That rule is not decoration: silent clobbering has cost this project real time. It is also why
a run whose parameters differ from the recorded `params.json` is **refused** (exit 2) rather than mixed. Change a
parameter on purpose → set `tag:` and get a separate product.

## 9. Pushing to GCS

`upload.destination` mirrors the case tree under `<destination>/<case>/`, so the bucket layout is the layout on
disk and anything can be pulled straight back:

```
gs://s1-slc/nisar_workflow/cases/nepal_demo/_run/coreg.yaml
                                            /coreg/RSLC_ref…/slc/…          (mode: coregistration)
                                            /timeseries/RSLC_ref…/ifg/…     (mode: interferogram)
```

What goes up depends on the mode: **coregistration** pushes the coregistered stack (or the GSLCs), **interferogram**
pushes the interferograms plus the stack's `params.json` and `stack_manifest.json` for provenance. ISCE3 scratch is
never pushed. `run` uploads at the end; `--no-upload` skips it and `upload` does it later.

`gcloud auth login` is interactive — if the token has expired, the upload fails and you re-run it by hand.
Uploads never delete: re-running syncs new and changed files only.

## 10. What it costs

Measured on the 8-core / 31 GB VM, per the Nepal ascending case:

| | time | disk |
|---|---|---|
| crop, per date | ≈ 2.5 min | 1.8 GB per cropped granule |
| cropped RSLC coregistration, 7 dates / 6 pairs, `jobs: 2` | **4 h 14 m** (71–88 min per pair) | ≈ 32 GB scratch per running pair, ≈ 110 GB peak |
| RSLC interferograms, 5 dates / 9 pairs (`--through ifg`) | ≈ 6 min (3 geometry + 3 interferograms) | small |
| full-tile RSLC, per pair | ≈ 8 h 34 m to the interferogram | ≈ 300 GB scratch |
| cropped GSLC, 2 dates + interferogram | ≈ 65 min | ≈ 0.95 GB per date per band |
| full-tile GSLC, per date | ≈ 1 h 12 m (freq A) | ≈ 10 GB per date per band |

A coregistration unit refuses to start unless its scratch estimate plus 30 GB is free, and scratch is pruned as soon
as a unit's outputs are verified and hard-linked out.

## 11. When it stops

Read the exit code first, then the module's own log — the paths are in the manifests.

| symptom | what it means |
|---|---|
| `no PR RSLC in …` and a list of unmatched files | the granule names do not match the expected pattern, or the tier is wrong |
| `crop: DEM missing` | the prepare stage did not finish; usually `~/.netrc` (set `netrc:` in the config) |
| `parameters differ from the ones recorded in params.json` | you changed a processing parameter on a stack that exists. Restore it, or set `tag:` for a new one |
| `coreg set-up: crops not ready` | run again; the crop stage runs first and this is just the order |
| exit 1 on one unit | that unit failed, the rest continued. `status` shows which; re-running retries only it |
| `GUNW pairs … do not connect the dates` | only when `corrections: true`. Add the GUNWs to `L2_GUNW/` or turn corrections off |
| `tmux session … already running` | a detached run of the same case is alive; attach to it instead |

Nothing finished is ever overwritten. A unit counts as done only when its manifest says `ok` **and** every recorded
output still has exactly the recorded byte size; a partial output without a verified manifest is refused, not
replaced, and needs `--force`.

## 12. Beyond the case runner

The case runner covers coregistration and interferogram formation. Everything past that — unwrapping, the GUNW
ionosphere / troposphere / solid-earth-tide corrections, MintPy inversion, LOS velocity, geocoded exports — is
`nisar_timeseries.py`, driven by its own config. `mode: timeseries` is not wired into `run_case.py` yet; run the
module directly, against the config the case runner generated:

```bash
python nisar_timeseries.py -c ~/isce3/case_studies/nepal_demo/_run/timeseries.yaml show
python nisar_timeseries.py -c ~/isce3/case_studies/nepal_demo/_run/timeseries.yaml run --detach   # all five stages
```

Two facts that cost this project a week, and will not be re-learned:

- **MintPy's `maskDataset: connectComponent` silently drops ice.** snaphu puts glacier pixels in component 0, and
  the default mask removes them from every interferogram. Lowering the coherence threshold does not help.
- **MintPy's automatic reference point is random** above the coherence threshold, so two runs disagree. The module
  picks one deterministically; give `reference_lalo` a point you know is stable when the answer has to mean something.

Further reading, in the order it becomes useful:

| document | what it is |
|---|---|
| [`nisar_workflows/README.md`](nisar_workflows/README.md) | every module, driver and tool with its arguments — the reference under this page |
| [`nisar_workflows/docs/COREG_MODULE.md`](nisar_workflows/docs/COREG_MODULE.md) | the coregistration module in depth |
| [`nisar_workflows/docs/TIMESERIES_MODULE.md`](nisar_workflows/docs/TIMESERIES_MODULE.md) | stages, conventions, validation of the time series |
| [`nisar_workflows/docs/COMPARISON.md`](nisar_workflows/docs/COMPARISON.md) | the evidence behind "crop first" and "RSLC, not GSLC" |
| [`nisar_workflows/STATE.md`](nisar_workflows/STATE.md) | where the work stands, and the claims that were withdrawn |
| [`SETUP.md`](SETUP.md) | installing ISCE3 |
