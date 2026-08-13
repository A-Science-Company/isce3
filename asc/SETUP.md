# ISCE3 setup

How to get ISCE3 working locally for Sentinel-1 and NISAR processing.
Self-contained: nothing here depends on, or affects, our ISCE2 setup.

- **Branch:** `s1-nisar-setup`
- **Envs:** `isce3_env` (processing), `dolphin_env` (time series)
- **`isce2_env` is never touched by any command in this document.**

---

## 1. What ISCE3 is, and what it is not

ISCE3 is **a library, plus one mission's workflows** — not an application
framework. Two things follow from that, and both shape the setup:

**(a) `isce3` is the library; `nisar.workflows` is the only complete workflow set
that ships with it.** `nisar` rides inside the `isce3` conda package (there is no
separate `nisar` package on conda-forge). Those workflows only accept
NISAR-shaped inputs — `nisar/products/readers/Base/Base.py` hard-codes
`SCIENCE_PATH='/science/'` and `NISAR_SENSOR_LIST=['SSAR','LSAR']`.

**(b) ISCE3 does not process Sentinel-1 end-to-end.** It has no TOPS burst
reader, no burst stitching, no azimuth ESD. It has the *primitives* —
`isce3.geocode.geocode_slc()` takes `az_carrier`/`rg_carrier` LUT2ds and
`reramp=True`, the TOPS deramp/reramp machinery — but nothing under `nisar/`
composes them for TOPS. The Sentinel-1 workflow engine is **COMPASS**, a separate
package that calls isce3 internally, with **s1reader** as its sensor adapter.

```
Sentinel-1 SAFE/burst
   ├─ burst2safe   (burst2stack CLI)  -> assembles multi-burst .SAFE from ASF
   ├─ sentineleof  (eof CLI)          -> POEORB/RESORB .EOF orbits
   ├─ sardem                          -> Copernicus GLO-30 DEM GeoTIFF
   └─ s1reader                        -> SAFE XML -> Sentinel1BurstSlc, emitting
                                         isce3.product.RadarGridParameters,
                                         isce3.core.Orbit, isce3.core.LUT2d
        |
        v
   COMPASS   s1_cslc.py --grid {geo,radar}   <- the S1 workflow engine
        |
        ├─ --grid geo   -> CSLC/{burst_id}/{YYYYMMDD}/{burst_id}_{YYYYMMDD}.h5
        └─ --grid radar -> {burst_id}_{date}.slc.tif on the reference burst's grid

NISAR L1 RSLC .h5
        |
        v
   python -m nisar.workflows.{insar,gslc,gcov,focus}  <runconfig.yaml>
        |
        └─> RIFG / RUNW / GUNW / GSLC / GCOV .h5
```

Dependency direction is worth noting: **COMPASS depends on isce3 *and* on
`nisar`** — `compass/utils/geo_grid.py:8` does
`from nisar.workflows.geogrid import _grid_size`. The isce3 conda package is
mandatory even for pure Sentinel-1 work.

---

## 2. Install

Both envs are conda-forge binaries on **python 3.12**.

```bash
cd /home/sharath/Desktop/work/isce3
conda env create -f asc/env/isce3_env.yml     # isce3 0.25.12 + compass 0.5.6
conda env create -f asc/env/dolphin_env.yml   # dolphin 0.42.5 + mintpy
bash asc/env/verify.sh                        # must print ALL CHECKS PASSED
```

Nothing else is required — no `mamba` install (conda 26.5.3 here already defaults
to the libmamba solver), no channel edits (both ymls carry `nodefaults`).

### Why binaries and not a source build

The upstream [README](../README.md) offers `conda install -c conda-forge isce3-cpu`
(or `isce3-cuda`), and `docs/buildinstall.md` documents three tiers: conda-forge,
`pip install .`, and CMake. We use conda-forge because `develop` is `0.26.0-dev`
(unreleased) while conda-forge ships `0.25.12` with the same Python API, there is
**no compiler on this box** (`gcc`, `g++`, `make`, `cmake`, `ninja` all absent),
and RAM/swap headroom makes a pybind11 compile a coin-flip against the OOM killer.

If you ever do need a source build, the officially documented route is:

```bash
conda env create -f environment.yml   # upstream's dev env, name: isce3
conda activate isce3
pip install .                         # handles RPATH + site-packages via scikit-build-core
python3 -c 'import isce3; print(isce3.__version__)'
```

The CMake route needs `-DWITH_CUDA=OFF` here, `--parallel 4` (not `-j16`), and
manual `PYTHONPATH=$PREFIX/packages` + `LD_LIBRARY_PATH=$PREFIX/lib` afterwards,
because `cmake --install` does **not** put Python into `site-packages`.

### Why two envs, not one

`compass 0.5.6` pins `scipy >=1.0,<1.13`; `dolphin 0.42.5` wants `scipy >=1.12`.
Co-installing pins everything to exactly `scipy 1.12.0` and `numpy <2`. dolphin
never needs compass — it only reads rasters — so it gets its own env.

Note the `numpy <2` constraint is real regardless: COMPASS code still uses
`np.string_`, removed in NumPy 2.0. `isce3_env` sits on numpy 1.26.4.

### Why not the course's `isceplus2026.yml`

The isce+ course env (`2026-isceplus/S07_.../isceplus2026.yml`, env name
`earthscope_insar`) bundles isce2 + isce3 + compass + dolphin + mintpy + aria-tools
together. It *does* solve — but because `isce2 2.6.x` pins
`libgdal-core >=3.10.3,<3.11`, it resolves **isce3 to 0.24.4 (April 2025)**.
Excluding isce2 is what buys 0.25.12. Our ISCE2 work already has `isce2_env`, so
there is no reason to co-install.

### python must be 3.12, not 3.11

`nisar/workflows/troposphere.py` imports `pyaps3` at module scope;
`troposphere_runconfig.py` imports `pygrib`; `pyaps3` hard-depends on `pygrib`;
and **conda-forge has no py311 build of pygrib**. On 3.11 the env installs
perfectly and then `from nisar.workflows import insar` dies at runtime with
`ModuleNotFoundError: No module named 'pyaps3'`. `pyaps3` must also be listed
explicitly — the conda-forge `isce3` package does not pull it in, even though
upstream's `environment.yml` lists it.

---

## 3. Verifying completeness

`asc/env/verify.sh` checks versions, the CPU/CUDA build flag, the NISAR runconfig
defaults, the five CLIs, and an HDF5 round-trip.

For a full audit — every module of every installed package:

```bash
conda run -n isce3_env python -c "
import importlib, pkgutil, warnings; warnings.filterwarnings('ignore')
import nisar, isce3, compass, s1reader
bad = []
for pkg in (nisar, isce3, compass, s1reader):
    for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__+'.', onerror=bad.append):
        try: importlib.import_module(m.name)
        except Exception as e: bad.append(f'{m.name}: {type(e).__name__}')
print('FAILURES:', len(bad)); [print(' ', b) for b in sorted(set(bad))]
"
```

**Expected output: exactly one failure, `isce3.cuda`.** That is correct on this
machine — the installed build is `py312he11b1ec_0_cpu` and there is no NVIDIA GPU.
Anything else in that list is a real gap.

Current state: 331 modules swept, **330 import cleanly**, only `isce3.cuda` fails.

### Gaps that were found and closed

An AST scan of all 339 `.py` files across `nisar/`, `isce3/`, `compass/`,
`s1reader/` produced 30 distinct third-party imports; each was test-imported.
Five were missing from the initial env and are now in `isce3_env.yml`:

| Package | Imported by | Scope | Consequence if absent |
|---|---|---|---|
| `fiona` | `s1reader/utils/plot_bursts.py:5` | module-level, in a **re-raising** try block | `import s1reader.utils.plot_bursts` fails; kills `s1_info --plot` |
| `geopandas` | `s1reader/utils/plot_bursts.py:6` | module-level, same block | same |
| `s1etad` | `s1reader/s1_etad.py:12` | module-level, re-raises | all S1 ETAD correction reading unavailable |
| `boto3` | `nisar/workflows/stage_dem.py:785`, `stage_watermask.py:785` | function-level, caller catches `ImportError` | non-fatal — logs "proceeding without verifying connection"; also enables NISAR S3 access |
| `dem_stitcher` | course 3.3 NISAR GUNW DEM staging | notebook | GUNW DEM-stitching cell fails |

### Deliberately NOT installed

- **`raider-base`** — upstream's `environment.yml` lists it, but both call sites
  (`nisar/workflows/troposphere.py:154`, `compass/utils/lut.py:251`) are
  **function-level**, reached only when a runconfig selects `package: raider`
  (the `pyaps` branch works, and `pyaps3 0.3.7` is installed) or when COMPASS is
  given a non-null `weather_model_path`. Installing it pulls dask + scikit-learn
  and **downgrades jupyterlab 4.6→3.5, ipython 9→8, pandas 2.3→2.2,
  jsonschema 4→3**. If you need RAiDER, put it in a separate throwaway env.
- **`capella-reader`** — add when doing Capella work (`conda-forge`, 0.2.2).
- **`isce2` / `isce` / `isceobj`** — lives in `isce2_env`. Never co-install.
- **`contextily`** — basemaps in one dolphin notebook only.

---

## 4. Credentials

**One NASA Earthdata Login covers almost everything.** Create it at
<https://urs.earthdata.nasa.gov/home>, then accept the ASF DAAC license
agreements for Sentinel-1 and ALOS (SRTM/NASADEM need no agreement).

```bash
echo "machine urs.earthdata.nasa.gov login myUsername password myPassword" > $HOME/.netrc
chmod 600 $HOME/.netrc
```

The `chmod` is not optional — several tools refuse to run if the file is
world-readable.

That one file is consumed by: `burst2stack` (S1 burst download from ASF),
`eof --force-asf` (precise orbits), `compass.utils.iono.download_ionex` (CDDIS
IONEX/TEC), NASADEM water-mask download (LP DAAC), `asf_search`, NISAR product
downloads from `nisar.asf.earthdatacloud.nasa.gov`, and NISAR S3 direct access
(which exchanges the login for temporary `AWS_*` credentials via
`https://nisar.asf.earthdatacloud.nasa.gov/s3credentials`).

Verify it works before processing anything — a `wget` of any ASF granule that
returns `Username/Password Authentication Failed` means the file is wrong.

**No CDSE / Copernicus Dataspace account is needed** for this path: bursts and
orbits come from ASF, and the DEM comes from `sardem --data-source COP` (a public
AWS bucket).

Two optional extras, neither needed for the core S1/NISAR path:

- `~/.cdsapirc` — only if you use PyAPS/ERA5 tropospheric correction. Format:
  ```
  url: https://cds.climate.copernicus.eu/api
  key: your-personal-access-token
  ```
  Use the **Personal Access Token** from your CDS profile — the legacy CDS key
  gives `401 Authentication failed`. You must also accept the terms for
  "ERA5 hourly data on pressure levels".
- `~/.topoapi` — an OpenTopography API key, used by ARIA-tools and some course
  raster notebooks. Not used by the isce3/COMPASS TOPS path.

---

## 5. One-time auxiliary data

| Item | How |
|---|---|
| **OPERA S1 burst-ID database** (global, once) | `curl -L -o ~/data/s1-burst-db/opera-burst-bbox-only.sqlite3 https://github.com/opera-adt/burst_db/releases/download/v0.10.0/opera-burst-bbox-only.sqlite3` — **mandatory** for the geo path |
| **Copernicus GLO-30 DEM** | `sardem --bbox W S E N --output-type float32 --output-format GTiff --data-source COP -o dem.tif`, AOI buffered ~2° |
| **S1 precise orbits** | `eof --search-path SLC --save-dir orbits --force-asf` |
| **S1 burst SLCs** | `burst2stack --rel-orbit N --all-anns --pols VV --swaths IW2 --start-date … --end-date … --extent W S E N --output-dir SLC` (`--all-anns` is required — s1reader/COMPASS need the full annotation) |
| **IONEX TEC** | `python -c "from compass.utils.iono import download_ionex; download_ionex('20240701','TEC',sol_code='jpl')"` |
| **NASADEM water mask** | `download_nasadem_water_mask()` in the course `2.1` `utils.py` → `swbd_nasadem.wbd` (BYTE, 255=water) |
| **NISAR test products** | `wget` from `https://nisar.asf.earthdatacloud.nasa.gov/NISAR/NISAR_L1_RSLC_PROVISIONAL_V1/…` |

NISAR runconfig templates and schemas ship in this repo at
`share/nisar/defaults/{insar,gslc,gcov,focus,static}.yaml` and
`share/nisar/schemas/*.yaml`; a working minimal example is
`tests/data/insar_test.yaml`.

---

## 6. Modular processing modes

These are the natural seams to cut Dockerfiles along later. **COMPASS installs
only four console scripts** (`compass-0.5.6.dist-info/entry_points.txt`):

```
s1_cslc.py           s1_geocode_stack.py           s1_static_layers.py           validate_product.py
```

There is no `s1_rdr2geo` / `s1_geo2rdr` / `s1_resample` script — but each module
has a working `__main__`, and **all three load the same `s1_cslc_radar` runconfig**:

```bash
python -m compass.s1_rdr2geo   ref_radar.yaml
python -m compass.s1_geo2rdr   sec_radar.yaml
python -m compass.s1_resample  sec_radar.yaml
```

One YAML, three stages, shared volume — that is the containerisation seam.

### (a) Coregistration only

Not a separate entry point; it is mode (b) run stage-by-stage with the commands
above. Stopping after `s1_resample` gives you the coregistered SLC and nothing else.

### (b) Radar grid, explicit reference file

`s1_cslc.py --grid radar` (`compass/s1_cslc.py:24-36`):

```python
if grid_type == 'radar':
    cfg = RunConfig.load_from_yaml(run_config_path, 's1_cslc_radar')
    if cfg.is_reference:
        s1_rdr2geo.run(cfg)                        # reference: topo in radar coords
    else:
        s1_geo2rdr.run(cfg); s1_resample.run(cfg)  # secondary: resample onto ref grid
```

Reference control is `input_file_group.reference_burst.{is_reference, file_path}`
in `s1_cslc_radar.yaml`. Note `reference_burst` is `required=False` in the schema
but the defaults file supplies `is_reference: True` — **omitting it silently makes
the run a reference run.**

| Stage | Outputs |
|---|---|
| reference (`rdr2geo`) | `{out}/{burst}_{date}_{POL}.slc.tif` (CFloat32), `radar_grid.txt`, `x/y/z.tif`, `layover_shadow_mask.tif`, `topo.vrt`. `local_incidence_angle`, `los_east`, `los_north` are **off by default** |
| secondary (`geo2rdr` + `resample`) | `range.off`, `azimuth.off`, and `{out}/{burst}_{date}.slc.tif` — CFloat32 GeoTIFF **on the reference burst's radar grid** |

⚠️ **See the COMPASS 0.5.6 bug in §7 — this mode does not work out of the box.**

### (c) Geo grid, no reference file

`s1_cslc.py --grid geo`. Every burst is geocoded onto a fixed UTM geogrid, so
coregistration is to the *map* and no reference scene exists. `reference_burst`
is **absent from the geo schema entirely** — it is not merely optional.

Output: `{product}/{burst_id}/{YYYYMMDD}/{burst_id}_{YYYYMMDD}.h5`, data at
`/data/VV`, plus `.png` and `.json` sidecars.

`s1_geocode_stack.py` generates the per-burst-per-date runconfigs into
`CSLC/runconfigs/` and run scripts into `CSLC/run_files/` for you, so you rarely
hand-author the geo YAML. It does **not** wire in `tec_file` — post-process the
generated YAMLs if you want the ionospheric geolocation correction.

Static layers (LOS, incidence) come from the same CLI with
`primary_executable.product_type: CSLC_S1_STATIC`, or `s1_static_layers.py`.

### (d) Through interferograms

- **Sentinel-1: nothing installed forms an interferogram.** A grep across
  `compass/` for `interferogram|crossmul|conj` returns nothing. Options are
  dolphin (`dolphin_env`) for PS/DS time series, or your own conjugate-product +
  multilook + filter + unwrap step. `snaphu 0.4.1` (the Python binding, not the
  CLI) is installed in both envs for the unwrap.
- **NISAR: `python -m nisar.workflows.insar <runconfig.yaml>`** is the full
  pipeline and emits RIFG / RUNW / GUNW. Set
  `primary_executable.product_type: RIFG_RUNW_GUNW` and
  `intermediate_files_removal_enabled: False` to keep the radar-geometry products,
  not just the geocoded one.

### Container contract summary

| | (a) stage-wise coreg | (b) radar + ref | (c) geo, no ref | (d) NISAR InSAR |
|---|---|---|---|---|
| **Entry** | `python -m compass.s1_{rdr2geo,geo2rdr,resample}` | `s1_cslc.py --grid radar` | `s1_cslc.py --grid geo`, `s1_geocode_stack.py` | `python -m nisar.workflows.insar` |
| **Inputs** | 1 SAFE + 1 EOF per config | ref SAFE+EOF, sec SAFE+EOF | ≥1 SAFE + EOF (stack: dir of SAFEs) | 2 NISAR RSLC `.h5` |
| **Aux** | DEM | DEM | DEM **+ burst-DB sqlite3 (mandatory)**, TEC optional | DEM, orbit XML opt, TEC opt |
| **Reference artifact** | `radar_grid.txt` + `topo.vrt` | that dir as `reference_burst.file_path` | **none** | `reference_rslc_file` |
| **Output** | per-stage topo tifs / offsets / resampled tif | `…slc.tif` on ref grid + topo layers | `{burst}_{date}.h5` on UTM grid | `RIFG`/`RUNW`/`GUNW` `.h5` |

---

## 7. Known issues

**COMPASS 0.5.6: `--grid radar` secondary runs fail as shipped.**
`s1_geo2rdr.py:85` writes the offsets to `out_paths.output_directory`:
```python
geo2rdr_obj.geo2rdr(topo_raster, out_paths.output_directory)
```
but `s1_resample.py:71-73` reads them from `out_paths.scratch_directory`:
```python
offset_path = out_paths.scratch_directory
rg_off_raster = isce3.io.Raster(f'{offset_path}/range.off')
```
Since `product_path` and `scratch_path` are distinct runconfig keys, the resample
step fails. **Workaround: set `product_path == scratch_path`.** Alternatively run
the stages separately and copy `range.off`/`azimuth.off` between the dirs.
Second, smaller issue in the same path: the resampled output is keyed on
`file_name_stem` with no polarization, so a `dual-pol` run writes both pols to the
same `{burst}_{date}.slc.tif`.

**`sas_output_file` gets `os.makedirs`'d.** `runconfig.py:163` passes it to
`check_write_dir`. Give it a **directory** path, or COMPASS creates a directory
named after your intended file.

**Geo path with no burst DB gives `TypeError: os.path.isfile(None)`** rather than a
useful message. The burst database is mandatory, not optional.

**`h5py is running against HDF5 2.2.0 when it was built against 2.1.0`** on every
import. Every conda-forge `h5py 3.16.0` build declares `hdf5 >=2.1.0,<3.0a0` but
was compiled against 2.1.0, while `libgdal-hdf5` pulls 2.2.0. HDF5 keeps ABI
compatibility within a major version, and `verify.sh` round-trips a complex64
gzipped dataset to prove it. Pinning `hdf5=2.1` conflicts with
`libgdal-hdf5 3.12.4` — do not chase it.

**`gpu_enabled` must stay `False`.** `isce3.core.gpu_check.use_gpu()` *raises*
`ValueError("GPU processing was requested but not available")` on a CPU build. The
shipped defaults in `share/nisar/defaults/insar.yaml` and `gslc.yaml` are already
`False`, but never copy a JPL production runconfig without checking that key.

**NISAR runconfig validation is stricter than it looks.** Your YAML is validated
*alone* against the yamale schema **before** defaults are merged, so every schema
key not marked `required=False` is mandatory in *your* file — including
`runconfig.name`, `pge_name`, `primary_executable.product_type`, and
`debug_level_group.debug_switch`. Separately, `logging` *is* `required=False` in
the schema but `insar.py` dies with `KeyError: 'logging'` if you omit it; always
supply `logging: {path: <file>, write_mode: a}`.

**Upstream notice:** "ISCE3 is in early development - its features and interface
are subject to change." Known-flaky tests excluded in upstream CI: `stage_dem`
(needs AWS credentials) and `pybind.unwrap.phass` (occasional segfault).

---

## 8. This machine

- **No NVIDIA GPU** — AMD Radeon 680M integrated; no `nvidia-smi`, no `nvcc`.
  There is no `__cuda` virtual package and every `*_cuda` isce3 build requires
  `__cuda >=12`, so conda auto-selects `_cpu`. No flag needed. Practical cost:
  `dense_offsets` falls back from `isce3.cuda.matchtemplate.PyCuAmpcor` to
  `isce3.matchtemplate.PyCPUAmpcor`.
- **12 GB RAM, 16 cores, ~4 GB swap with almost none free.** RAM is the binding
  constraint, not cores. Start S1 CSLC at **2** concurrent workers (the course
  notebook uses 4); each holds a full burst in memory. For NISAR, a full-frame
  RSLC is ~20-25 GB on disk and `insar.py` works in blocks (`lines_per_block: 1000`)
  — lower before raising. These are estimates: measure your first real run with
  `/usr/bin/time -v` and read `Maximum resident set size`.
- **Import shadowing:** running `python` from the repo root is safe (there is no
  top-level `isce3/` directory here). The hazard is `cd python/packages` — which
  contains both `isce3/` and `nisar/` source trees — or putting that path on
  `PYTHONPATH`, where the source shadows the installed package and
  `import isce3.ext.isce3` fails.
