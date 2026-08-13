# ASC ISCE3 setup — Sentinel-1 and NISAR

Working notes for moving our InSAR processing from our ISCE2 fork onto ISCE3.
Branch: `s1-nisar-setup`.

## The one thing to internalise

**ISCE2 is an application framework. ISCE3 is a library plus one mission's workflows.**

|                  | ISCE2                                                                                                    | ISCE3                                                                                          |
| ---------------- | -------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Shape            | `topsApp.py` / `stripmapApp.py` / `stackSentinel.py` generate and run shell `run_files`; state in `.xml`  | A C++/pybind11 library (`isce3.*`) plus a NISAR-only SAS package (`nisar.workflows.*`), one YAML |
| Sensor plumbing  | `isceobj/Sensor/*.py` + `Factories.py` plugin registry                                                    | No registry. You construct four objects and hand them to library calls                          |
| Orchestration    | Framework owns the pipeline                                                                               | **You** own the pipeline                                                                        |

**`isce3` does not process Sentinel-1 end-to-end.** It has no TOPS burst reader,
no burst stitching, no azimuth ESD. It has the *primitives* — `isce3.geocode.geocode_slc()`
takes `az_carrier`/`rg_carrier` LUT2ds and `reramp=True`, which is the TOPS
deramp/reramp machinery COMPASS relies on. Nothing under `python/packages/nisar/`
composes them for TOPS.

The seam:

```
Sentinel-1 SAFE/burst
   ├─ burst2safe   (burst2stack CLI)  -> assembles multi-burst .SAFE from ASF
   ├─ sentineleof  (eof CLI)          -> POEORB/RESORB .EOF
   ├─ sardem                          -> Copernicus GLO-30 DEM GeoTIFF
   └─ s1reader                        -> parses SAFE XML into Sentinel1BurstSlc,
                                         emitting isce3.product.RadarGridParameters,
                                         isce3.core.Orbit, isce3.core.LUT2d
        |
        v
   COMPASS  (s1_cslc.py --grid geo, s1_geocode_stack.py)
        |   <-- THIS is the workflow engine for S1. It calls isce3 internally.
        v
   CSLC HDF5:  CSLC/{burst_id}/{YYYYMMDD}/{burst_id}_{YYYYMMDD}.h5,  data at /data/VV
        |
        ├─> dolphin (PS/DS time series) — reads it natively
        └─> pairwise ifgs — hand-rolled NumPy in the course utils.py, NOT a CLI
```

`isce3` is the engine block, `s1reader` is the sensor adapter, `compass` is the car.
`nisar.workflows` is a *different* car on the same engine, and it only accepts
NISAR-shaped fuel — `nisar/products/readers/Base/Base.py` hard-codes
`SCIENCE_PATH='/science/'` and `NISAR_SENSOR_LIST=['SSAR','LSAR']`.

One leak, worth knowing before assuming "isce3 core is sensor-agnostic":
`python/packages/isce3/splitspectrum/splitspectrum.py:10` does
`from nisar.workflows.focus import cosine_window`. It is the only isce3→nisar
import in the tree, but it means `isce3.splitspectrum` needs the `nisar` package.

## Environments

Two envs, both conda-forge binaries. **`isce2_env` is not touched.**

```bash
conda env create -f asc/env/isce3_env.yml     # isce3 0.25.12 + compass 0.5.6
conda env create -f asc/env/dolphin_env.yml   # dolphin 0.42.5 + mintpy
bash asc/env/verify.sh                        # proves both work
```

Why two: `compass 0.5.6` requires `scipy >=1.0,<1.13`; `dolphin 0.42.5` wants
`scipy >=1.12`. Co-installing pins the whole stack to exactly `scipy 1.12.0` and
`numpy <2`. dolphin never needs compass — it only reads rasters.

Why not the course's `isceplus2026.yml`: it *does* solve, but because `isce2`
pins `libgdal-core >=3.10.3,<3.11` it resolves `isce3` to **0.24.4** (2025-04-30).
Excluding `isce2` is what buys us isce3 0.25.12.

### Two traps found while building these (both cost a rebuild)

1. **`python=3.11` silently breaks `nisar.workflows.insar`.** `nisar/workflows/troposphere.py`
   does `import pyaps3` at module scope, and `troposphere_runconfig.py` imports
   `pygrib`. `pyaps3` hard-depends on `pygrib`, and **conda-forge has no py311
   build of pygrib** — only py312/313/314. So on 3.11 the env installs perfectly
   and then `from nisar.workflows import insar` dies with
   `ModuleNotFoundError: No module named 'pyaps3'`. Both envs are therefore on
   **python 3.12**. `pyaps3` is listed explicitly in `isce3_env.yml` — the
   conda-forge `isce3` package does not pull it in, even though upstream
   `environment.yml` lists it.
2. **`rasterio` is not actually incompatible with isce3 0.25.12** — it was the
   py311 pin that made it unsolvable. At py312, `rasterio 1.5.0` co-installs with
   `libgdal-core 3.12.4` fine, so it is back in `isce3_env.yml`.

### Known benign warning

`h5py is running against HDF5 2.2.0 when it was built against 2.1.0`. Every
conda-forge `h5py 3.16.0` build declares `hdf5 >=2.1.0,<3.0a0` but was compiled
against 2.1.0, while `libgdal-hdf5` pulls in 2.2.0. HDF5 keeps ABI compatibility
within a major version, and `verify.sh` round-trips a file to prove it. Pinning
`hdf5=2.1` would conflict with `libgdal-hdf5 3.12.4` — do not chase it.

### Machine-specific facts (this box)

- **No NVIDIA GPU** — AMD Radeon 680M iGPU, no `nvidia-smi`/`nvcc`. There is no
  `__cuda` virtual package, and every `*_cuda` isce3 build requires `__cuda >=12`,
  so the `_cpu` variant is auto-selected. No flag needed.
- **12 GB RAM, ~4 GB swap with almost none free.** This is the binding constraint,
  not the 16 cores. See "RAM budget" below.
- **conda 26.5.3 with libmamba already the default solver.** Do *not* install
  mamba/micromamba — the course README's advice predates this.
- **No compiler** — `gcc`, `g++`, `make`, `cmake`, `ninja` are all absent.

### Why not build from source

`develop` @ `0.26.0-dev` is unreleased; conda-forge ships `0.25.12` with an
identical Python API and the same `nisar.workflows` entry points. There is no
compiler on the box, and a pybind11/Eigen compile at any meaningful `-j` is a
coin-flip against the OOM killer given the swap situation.

Build from source only when you need a fix on `develop` that is not in 0.25.12,
or you start patching C++:

```bash
conda env create -f environment.yml          # the upstream dev env, not ours
cmake -B build -G Ninja -DWITH_CUDA=OFF -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX -DCMAKE_PREFIX_PATH=$CONDA_PREFIX \
      -DISCE3_FETCH_DEPS=NO .
cmake --build build --parallel 4             # NOT -j16 on this box
```

`CMAKE_BUILD_TYPE` defaults to `RelWithDebInfo`; `Release` is what you want.
`cmake --install` puts Python in `$CONDA_PREFIX/packages`, *not* `site-packages` —
you then need `conda env config vars set PYTHONPATH=$CONDA_PREFIX/packages
LD_LIBRARY_PATH=$CONDA_PREFIX/lib`. Or use `pip install .`, which handles
RPATH/site-packages via scikit-build-core (`CMakeLists.txt:95-99` flips
`ISCE3_FETCH_DEPS` OFF under SKBUILD, so all deps must already be in the env).

### Shadowing hazard

There is no top-level `isce3/` directory in this repo, so running `python` from
the repo root is safe. The hazard is only `cd python/packages` (which contains
both `isce3/` and `nisar/`) or putting that path on `PYTHONPATH` — there the
source tree shadows the installed package and `import isce3.ext.isce3` fails.

## The two pipelines

### Sentinel-1

No config file to author for CSLC generation — `s1_geocode_stack.py` writes the
per-burst-per-date runconfigs into `CSLC/runconfigs/` plus `CSLC/run_files/run_*.sh`.
You post-process those YAMLs to inject `tec_file`.

```bash
conda activate isce3_env
WORK=/data/s1/kilauea; mkdir -p $WORK/{SLC,orbits,DEM,TEC,CSLC}; cd $WORK

# one-time, shared across experiments: OPERA burst database
curl -L -o ../s1-burst-db/opera-burst-bbox-only.sqlite3 \
  https://github.com/opera-adt/burst_db/releases/download/v0.10.0/opera-burst-bbox-only.sqlite3

# 1. burst-level SLC download.  --all-anns is REQUIRED (s1reader/COMPASS need
#    full annotation for all bursts).
burst2stack --rel-orbit 124 --all-anns --pols VV --swaths IW2 \
    --start-date 2024-07-01 --end-date 2024-10-11 \
    --extent -155.50 19.15 -154.95 19.55 --output-dir SLC

# 2. orbits (--force-asf avoids needing CDSE credentials)
eof --search-path SLC --save-dir orbits --force-asf

# 3. DEM, padded 2 deg lon / 1 deg lat beyond the AOI
sardem --bbox -158 18 -152 21 --output-type float32 --output-format GTiff \
    --data-source COP -o DEM/cop_dem.tif

# 4. ionospheric TEC (JPL IONEX, one per date)
python -c "
from compass.utils.iono import download_ionex
for d in ['20240701','20240713']: download_ionex(d, 'TEC', sol_code='jpl')"

# 5. generate runconfigs + run scripts for the whole stack
s1_geocode_stack.py -s SLC -d DEM/cop_dem.tif -o orbits -w CSLC \
    -dx 10 -dy 20 --common-bursts-only --unzipped \
    --burst-db-file ../s1-burst-db/opera-burst-bbox-only.sqlite3 \
    --bbox -155.50 19.15 -154.95 19.55

# 5b. inject tec_file into each CSLC/runconfigs/*.yaml
#     s1_geocode_stack.py does NOT wire in TEC.  See 2.1 stack notebook.

# 6. run.  Start at 2 concurrent on this box, not the notebook's 4.
for f in CSLC/run_files/run_*.sh; do chmod +x "$f"; "$f"; done
# or per burst:  s1_cslc.py --grid geo CSLC/runconfigs/geo_runconfig_20240701_t124_264305_iw2.yaml

# 7. static layers: same CLI, one YAML key flipped
#    copy a CSLC runconfig, set primary_executable.product_type: CSLC_S1_STATIC
```

Output: `CSLC/{burst_id}/{YYYYMMDD}/{burst_id}_{YYYYMMDD}.h5`, data at `/data/VV`,
on a fixed UTM geogrid per burst.

#### COMPASS also has a radar-grid path — probably the one we want first

`s1_cslc.py` takes `-g/--grid {geo,radar}`, and the `radar` branch is a classic
reference/secondary coregistration, *not* geocoding (`compass/s1_cslc.py:24-36`):

```python
if grid_type == 'radar':
    cfg = RunConfig.load_from_yaml(run_config_path, 's1_cslc_radar')
    if cfg.is_reference:
        s1_rdr2geo.run(cfg)                      # reference burst: topo in radar coords
    else:
        s1_geo2rdr.run(cfg); s1_resample.run(cfg)  # secondary: resample onto ref grid
```

This matters a lot for us, for two reasons:

- **Reference control is preserved verbatim.** `compass/defaults/s1_cslc_radar.yaml`
  has `input_file_group.reference_burst.{is_reference, file_path}` — the direct
  analogue of our `--reference_slc` / `REFERENCE_DATE`. It is only the *geo* path
  that dissolves the reference-date concept.
- **The output format already matches our dolphin glue.** `s1_resample.py:81-86`
  writes `{out_paths.output_directory}/{out_paths.file_name_stem}.slc.tif` as a
  **CFloat32 GeoTIFF on the reference burst's radar grid** — which is exactly what
  `sharath_dolphin`'s coreg stage produces today (`coregistered_slc/<date>.slc.tif`,
  CFloat32, no CRS, radar grid). Our existing `convert_merged_slcs_to_tif` output
  contract carries over nearly unchanged.

What COMPASS does *not* do on this path is form interferograms — it stops at the
resampled SLC. So `filt_fine.int/.cor/.unw` in radar coords is still glue we write.

Runconfig: `compass/schemas/s1_cslc_radar.yaml` (contract) and
`compass/defaults/s1_cslc_radar.yaml` (commented defaults), both inside the
installed package. Note the radar path uses `RunConfig`, the geo path `GeoRunConfig` —
the two YAMLs are **not** interchangeable, and `s1_geocode_stack.py` only generates
the geo flavour.

**Interferograms — two answers, know which you want.**

*(i) Pairwise (our `radar_multi_resolution` analogue).* There is **no CLI**. The
course does it in hand-rolled NumPy in `../2026-isceplus/2.1_ISCE3_TOPS_Processing/utils.py`
(3802 lines, 53 exported names, no tests, no versioning, no conda package — if we
build on it, we own it). Because both dates are already on the *same* UTM burst
grid, interferogram formation is a literal conjugate product — no resampling, no
ESD, no cross-correlation:

```python
import utils as ut
date12_list = ut.generate_ifgram_pairs('CSLC', 'interferograms', n_connections=2)
ut.generate_stitched_ifgrams(cslc_dir='CSLC', date12_list=date12_list,
                             output_dir='interferograms', bbox_wsen=wsen, buffer=0.05,
                             coh_win=5, lks_y=azlks, lks_x=rglks)      # -> {d1}_{d2}/mli.int.tif
ut.filter_tif(ifg, filt_ifg, alpha=0.5)                                # Goldstein, psize=32
ut.generate_phsig_coh_tif(filt_ifg)
ut.unwrap_single_ifgram(ifg, coh, unw, water_mask='DEM/swbd_nasadem.wbd',
                        nlooks=azlks*rglks/1.2**2, cost_mode='smooth', init_method='mcf')
ut.compute_baselines_for_bursts(...); ut.merge_baselines(...)          # MintPy "Bperp (m):" format
```

Goldstein filter, phase-sigma coherence and multilooking are pure NumPy/scipy
reimplementations, not ISCE bindings. Unwrapping is `snaphu.unwrap()` from
`snaphu-py`. Downstream target is MintPy — `grep -rn -i dolphin ../2026-isceplus/2.1_*/` returns zero hits.

*(ii) PS/DS time series (our `sharath_dolphin` analogue).* Skip interferograms;
hand the CSLCs to dolphin. Near-zero glue, because COMPASS filenames match
dolphin's `OPERA_BURST_RE = r"[tT](?P<track>\d{3})[-_](?P<burst_id>\d{6})[-_](?P<subswath>iw[1-3])"`
and carry an 8-digit date:

```bash
conda activate dolphin_env
dolphin config --slc-files 'CSLC/t*_*_iw*/*/t*_*_iw*_2*.h5' \
    --subdataset /data/VV \
    --input-options.wavelength 0.05546576 \
    --sx 6 --sy 3 --ms 16 \
    --interferogram-network.max-bandwidth 3 \
    --unwrap-options.unwrap-method snaphu \
    --unwrap-options.n-parallel-jobs 2 \
    --work-directory dolphin --outfile dolphin/dolphin_config.yaml
dolphin run dolphin/dolphin_config.yaml
```

Pass `--input-options.wavelength` explicitly anyway: dolphin auto-sets the S1
wavelength *only* when `get_burst_id()` succeeds on the filename. Lose the burst
pattern and `timeseries/*.tif` and `velocity.tif` are silently in **radians**, not metres.

**Credentials.** One NASA Earthdata Login in `~/.netrc` covers ASF (burst2safe,
`eof --force-asf`, asf_search), LP DAAC (NASADEM water mask) and CDDIS (IONEX).
No ESA/CDSE account is needed for this path.

### NISAR

One runconfig YAML per workflow. Templates:
`share/nisar/defaults/{insar,gslc,gcov,focus,static}.yaml` (full commented defaults),
`share/nisar/schemas/*.yaml` (the yamale contract), and a working minimal example
at `tests/data/insar_test.yaml`.

```bash
conda activate isce3_env
python -m nisar.workflows.dumpconfig insar > insar_runconfig.yaml   # then edit
python -m nisar.workflows.insar insar_runconfig.yaml
```

**Runconfig mechanics that will bite you:**

- Your YAML is validated *alone* against the yamale schema **before** defaults are
  merged (`runconfig.py`: `yamale.validate(schema, data)` then `helpers.deep_update`).
  So **every schema key not marked `required=False` is mandatory in *your* file**,
  including `runconfig.name`, `pge_name`, `primary_executable.product_type`, and
  `debug_level_group.debug_switch`. A "default GUNW" for `product_type` never gets a
  chance to apply — validation fails first.
- `logging` is `required=False` in the schema and absent from the defaults file, but
  if you omit it `insar.py` dies with `KeyError: 'logging'`. Always supply
  `logging: {path: <file>, write_mode: a}`.
- Set `product_type: RIFG_RUNW_GUNW` and `intermediate_files_removal_enabled: False`
  to keep the radar-geometry products, not just the geocoded one.
- `gpu_enabled` defaults to `False` in the shipped `insar.yaml` and `gslc.yaml`, so
  we are safe — but never copy a JPL production runconfig without checking that key.
  `isce3.core.gpu_check.use_gpu()` **raises** if it is True on a non-CUDA build.

## Capability mapping from our ISCE2 fork

| Our ISCE2 capability | ISCE3-world equivalent | Status |
| --- | --- | --- |
| **Pairwise interferogram** (`stackSentinel.py -W interferogram`) | S1: no CLI — `generate_stitched_ifgrams`/`filter_tif`/`generate_phsig_coh_tif`/`unwrap_single_ifgram` in course `utils.py`. NISAR: `insar.py` with `product_type: RIFG_RUNW_GUNW` | **S1: NEEDS GLUE** (copy+own `utils.py`). **NISAR: DIRECT** |
| **Controllable reference** (`--reference_slc`, `-m YYYYMMDD`) | **Geo path:** gone as a concept — COMPASS geocodes every burst onto a fixed UTM geogrid, so coregistration is to the *map*, not to a scene; any date is "reference" at ifg-formation time. **Radar path:** preserved exactly — `input_file_group.reference_burst.{is_reference, file_path}` in `s1_cslc_radar.yaml`. NISAR `insar.py` takes explicit `reference_rslc_file`/`secondary_rslc_file` | **DIRECT** on the radar path; **NEEDS GLUE** (but simpler) on the geo path, where the geogrid replaces reference-date bookkeeping and "download the reference zip alongside every pair" becomes unnecessary |
| **Radar-coordinate output** (`filt_fine.{int,cor,unw}` + `.xml`/`.vrt`) | **`s1_cslc.py --grid radar`** — see below. Reference burst → `s1_rdr2geo`; secondary → `s1_geo2rdr` + `s1_resample`, emitting `{output_directory}/{file_name_stem}.slc.tif`, CFloat32 GeoTIFF on the *reference burst's radar grid*. NISAR: RIFG/RUNW/ROFF HDF5 are true range-Doppler. Generic sensor: `Rdr2Geo`/`Geo2Rdr`/`ResampSlc` (the Capella S08 recipe) | **DIRECT** for the coregistered radar-grid SLC stack. **NEEDS GLUE** for the radar-coord *interferogram* products (`filt_fine.int/.cor/.unw`) — COMPASS stops at the resampled SLC. The ENVI/BIL + `.xml`/`.vrt` sidecar contract has no analogue anywhere; everything is GeoTIFF or HDF5 |
| **Geocoded output** (`gdalwarp -geoloc` via `lat/lon.rdr`) | Native. CSLC/GUNW/GSLC/GCOV are already map-projected (UTM, not 4326) | **DIRECT**, and strictly better — one fewer resampling. Consumers expecting EPSG:4326 need a `gdalwarp -t_srs EPSG:4326` |
| **Coregistered stack** (`-W slc -C NESD`) | `s1_geocode_stack.py` + `s1_cslc.py --grid geo`. Geocoding onto a common grid *is* the coregistration | **DIRECT** (different mechanism, same deliverable). All `misreg/`, `ESD/`, `coreg_secondarys/` handling becomes dead code |
| **Dolphin handoff** | COMPASS CSLC HDF5 straight into `dolphin config --subdataset /data/VV`; burst id and date parse from the filename | **DIRECT**, much less glue: no `convert_merged_slcs_to_tif`, no `geom_reference/*.rdr.tif`, no `_crop_stack_to_aoi`. Use `--output-options.bounds` instead of pre-cropping. **Caveat:** LOS/incidence now comes from `static_layers_{burst_id}.h5`, which `dolphin run` does *not* consume — post-process with `opera_utils.geometry.stitch_geometry_layers` |
| **Capella stripmap** (`isceobj/Sensor/Capella.py` + `Factories.py`) | `capella-reader` + `capella_reader.adapters.isce3` + the 505-line `../2026-isceplus/S08_.../capella_isce3_utils.py` reference pipeline | **NEEDS GLUE**, but upstream work is done. Our `Capella.py` becomes obsolete — `capella-reader` reads the same `TIFFTAG_IMAGEDESCRIPTION` JSON. What we write is the *pipeline*, since `stripmapApp.py` has no isce3 counterpart |
| **Multi-resolution** (`--resolution_specs`, per-res working dirs, separate VH pass) | Posting is a runconfig field: COMPASS `processing.geocoding.{x,y}_posting` (`-dx`/`-dy`); NISAR `processing.crossmul.{range,azimuth}_looks`. Polarization is `processing.polarization` — no separate pass, no pol-specific burst collisions | **NEEDS GLUE** for the DSL and directory orchestration (ours either way), but the mechanism is cleaner |
| **NESD / ESD azimuth coregistration** | Does not exist in isce3 or COMPASS at all | **NO EQUIVALENT.** On the *geo* path it is not needed — geocoding sidesteps it. On the *radar* path, coregistration is geometry-only (orbit + DEM via `s1_geo2rdr`), with no spectral-diversity azimuth refinement. If our results depend on NESD-level azimuth accuracy, this is the gap to measure |
| **Ionosphere** (`--param_ion`, `filt.ion`) | S1: `tec_file` in the CSLC runconfig applies an IONEX *geolocation* correction — **not** topsStack's split-spectrum ionospheric phase. NISAR: `processing.ionosphere_phase_correction.enabled` does real split-spectrum | **S1: NO EQUIVALENT.** **NISAR: DIRECT** |
| **`snaphu_metadata.json` + snaphu CLI** | `snaphu.unwrap(ifg, corr, nlooks=…, cost=…, init=…, ntiles=…, nproc=…)`. All the `ALTITUDE`/`EARTHRADIUS`/`LAMBDA`/`NCORRLOOKS` bookkeeping via `isceobj.Planet`/`Orbit` disappears | **DIRECT**, simpler |

## RAM budget on this box

12 GB total with effectively no swap headroom. The OOM killer has nothing to
absorb a spike. These are **estimates, not measurements** — measure the first
real run with `/usr/bin/time -v` and read `Maximum resident set size`.

- **S1 CSLC** — the course notebook uses `MAX_CONCURRENT = 4`. Start at **2**.
  Every worker holds a full burst in memory.
- **NISAR** — a full-frame RSLC granule is ~20-25 GB on disk; `insar.py` works in
  blocks (`lines_per_block: 1000`). Lower before raising. `intermediate_files_removal_enabled: False`
  keeps the radar products but costs scratch.
- **dolphin** — `--n-parallel-bursts 2`, `--unwrap-options.n-parallel-jobs 2`,
  `--worker-settings.threads-per-worker 4`. `snaphu_options.ntiles` splits the
  unwrap, which is the other memory spike.

If you turn off `dense_offsets` to save time, note `insar_runconfig.py` then forces
`rubbersheet.enabled: False` and consequently `fine_resample.enabled: False`, and
`crossmul` falls back to the coarse-resampled secondary. That is a real quality
trade, not a free win.

## Capella on ISCE3

There is no `Sensor/` registry, no `Factories.py`, no `createCapella`. Adding a
sensor is a *library integration*: you produce four objects.

| Object | Encodes | Needed for |
| --- | --- | --- |
| `isce3.product.RadarGridParameters` | start time, line spacing, near range, range spacing, look side, size, ref epoch | everything |
| `isce3.core.Orbit` | ECEF position+velocity state vectors + epoch | everything |
| `isce3.core.LUT2d` | Doppler centroid vs (slant range, azimuth time) | resampling, non-zero-Doppler geometry |
| `isce3.core.Attitude` | platform quaternions | RTC, ENU look vectors, antenna pattern |

The pattern (from `s1-reader`, which the S08 notebook holds up as the precedent):
parse vendor metadata into a plain dataclass importing *nothing* from isce3, then
give it `as_isce3_radargrid()` / `get_orbit()` / `get_attitude()` / `get_doppler_lut2d()`.
`capella-reader` follows it exactly — and `import capella_reader` alone does *not*
pull the adapter in; you must `import capella_reader.adapters.isce3` explicitly.

Gotchas, all things our ISCE2 `Capella.py` also had to handle:

- `prf` in `RadarGridParameters` is **the grid's azimuth line rate**
  (`1/delta_line_time`), not the physical PRF — tens of Hz vs ~9900 Hz. Our ISCE2
  reader already does `PRF = 1/image.image_geometry.delta_line_time`, so it maps over.
- Orbit state vectors must be **exactly uniformly spaced**; sub-nanosecond jitter is rejected.
- `orbit.reference_epoch` **must equal** `radar_grid.ref_epoch`.
- Quaternion convention differs: Capella stores scalar-first Hamilton ECEF→antenna;
  isce3 wants radar-frame→ECEF, so the adapter conjugates and composes with a fixed
  90° rotation about +Z.

Validate before spending compute — round-trip a vendor GCP:

```python
az_time, slant_range = isce3.geometry.geo2rdr(
    [np.deg2rad(gcp.x), np.deg2rad(gcp.y), gcp.z],
    ellipsoid=isce3.core.make_projection(4326).ellipsoid,
    orbit=orbit, doppler=isce3.core.LUT2d(),
    wavelength=radar_grid.wavelength, side=radar_grid.lookside)
row = (az_time - radar_grid.sensing_start) * radar_grid.prf
col = (slant_range - radar_grid.starting_range) / radar_grid.range_pixel_spacing
# compare to gcp.row / gcp.col — need agreement below 1 pixel
```

Reported S08 result on a 3-day Capella X-band pair over Mexico City: mean coherence
0.08 (geometry only) → 0.36 (geometry + cross-correlation). dolphin logs
`Unable to parse OPERA bursts` and falls back to generic handling — expected, harmless.

**Do not** route Capella through `nisar.workflows` — that needs a spec-conformant
NISAR RSLC HDF5 writer. `share/nisar/examples/alos2_to_nisar_l1.py` is the reference
if we ever want it; it is a large lift.

## Corrections to intuitions carried over from ISCE2

- **`.full` suffix.** `contrib/stack/topsStack/mergeBursts.py:408-416` sets
  `suffix = '.full'` and clears it to `''` when `range_looks == 1 and azimuth_looks == 1`.
  `.full` is the *pre-multilook intermediate*; it exists only when looks > 1. The
  inline comments in our `2_geocoding.py:275` and `:821` say the opposite and are wrong.
- **`-V True` on the `-W slc` path is inert** — `stackSentinel.py:709-714` hardcodes
  `virtual='False'` inside `slcStack`. So `convert_merged_slcs_to_tif` is an
  ENVI→GeoTIFF conversion of an already-materialised binary, not the materialisation
  of a virtual stack.
- **`reference_info.json` is produced by `run_dolphin_wrapper.py` (Step 11)**, not by
  `1_coregister_slcs.py`. Running the coreg step standalone yields no `reference_info.json`.

## Sequencing

Each step produces something checkable.

1. **Create both envs, run `asc/env/verify.sh`.** Proves `isce3.__version__`,
   `hasattr(isce3.ext.isce3, "cuda") == False`, the NISAR runconfig defaults are
   present, and the five CLIs are on PATH.
2. **isce3 self-test with no data** — construct a projection and a `Rdr2Geo` against
   any DEM GeoTIFF. Proves the compiled extension and `libisce3.so` load.
3. **Reproduce the single-pair S1 notebook on the course AOI** (Kīlauea, track 124,
   IW2+IW3, `wsen = (-155.50, 19.15, -154.95, 19.55)`). Use the course AOI, not a
   production tile — we are testing the toolchain, not the science.
4. **Stack notebook → straight to dolphin**, skipping the hand-rolled pairwise path.
   Shortest route to a result comparable with a `sharath_dolphin` run; validates the
   whole seam in one shot.
5. **Run the same AOI through `sharath_dolphin` on ISCE2 and diff** `velocity.tif`,
   temporal coherence, PS counts. This is the decision point for whether the S1
   time-series migration is real. Expect differences from geocoding-vs-radar-grid
   resampling; expect *not* to see systematic ramps.
6. **Spike `s1_cslc.py --grid radar` on the same AOI.** Author one
   `s1_cslc_radar.yaml` with `is_reference: True` for the reference date and one with
   `is_reference: False` + `file_path` for a secondary. *Verifiable:* a CFloat32
   `*.slc.tif` on the reference burst's radar grid. This is the closest thing to our
   current `radar_multi_resolution` / `sharath_dolphin` coreg contract, and it keeps
   explicit reference control — check it before committing to the geo-only path.
7. **Only now the pairwise interferogram path.** Copy `utils.py` into a repo we
   control; compare `filt_mli.unw.tif` against our ISCE2 `phase_unwrapped.tif` for the
   same pair. Accept that we trade a maintained CLI for ~600 lines of NumPy we own.
8. **NISAR standing start** — one L1 RSLC pair, `tests/data/insar_test.yaml` as the
   base, `product_type: RIFG_RUNW_GUNW`, `gpu_enabled: False`. Success looks like
   three HDF5s and a log ending `successfully ran INSAR in N seconds`.
9. **NISAR GSLC → dolphin**, pinning `geocode.top_left`/`bottom_right`/`output_posting`/
   `output_epsg` across all dates, and symlinking outputs to names carrying 8-digit dates.
10. **Decide Capella with a spike, not a design** — the GCP round-trip above on one of
   our own scenes. A day, not a sprint, and it tells us whether `capella-support` can retire.
11. **Containerise last.** The `isce2-docker/Dockerfile` pattern transfers directly;
    swap the package list for `asc/env/isce3_env.yml` and **drop the hand-built
    snaphu v2.0.6 stage** — conda-forge `snaphu 0.4.1` is the Python-binding package
    that `snaphu.unwrap()` uses.
