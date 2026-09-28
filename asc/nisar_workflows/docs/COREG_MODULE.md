# Coregistration module (`nisar_coreg.py`)

One short case config in, one coregistered stack out. Built to run on the VM now and as batch jobs later: every unit is
idempotent, writes a JSON manifest, never overwrites a finished output without `--force`, and returns a meaningful exit code.
The module only reads RSLCs already on disk; downloading is a separate step (`tools/nisar_fetch.py`).

## Trigger

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env
cd /home/sharath/isce3/asc/nisar_workflows
C=coreg_configs/nepal_nisar_ascending_rslc.yaml
python nisar_coreg.py -c $C show                       # dates, reference, names, every parameter in effect; runs nothing
python nisar_coreg.py -c $C status                     # what is done
python nisar_coreg.py -c $C progress                   # running pairs: ISCE3 stage timeline vs crop v2, live progress line, scratch size
python nisar_coreg.py -c $C run --dry-run              # plan; validates the generated driver config
python nisar_coreg.py -c $C run --detach               # prepare -> crop -> coreg, in tmux
python nisar_coreg.py -c $C run --stage crop --dates 20260620 --detach
python nisar_coreg.py -c $C run --stage coreg --dates 20260831
```

`--detach` prints the tmux session (`tmux attach -t coreg_<case>_<stage>`) and the log path.

## Case config (`coreg_configs/<case>.yaml`)

```yaml
case: nepal_nisar_ascending
workdir: /home/sharath/isce3/case_studies/nepal_nisar_ascending   # holds L1_RSLC/ and aux/dem/
mode: RSLC                 # RSLC | GSLC
crop: true
aoi_kml: /home/sharath/nisar_downloader/glof_bigger_aoi.kml
reference_date: null       # null -> middle date
start_date: null           # null -> earliest RSLC on disk
end_date: null             # null -> latest RSLC on disk
```

| key | meaning |
|---|---|
| `mode` | `RSLC`: every secondary is resampled onto the reference radar grid, using geometry plus dense offsets (rubber sheet). `GSLC`: every date is geocoded independently onto one pinned map grid, using geometry only. GSLC dates are not registered to each other and lose ~5% coherence (COMPARISON §3.6). |
| `crop`, `aoi_kml` | Cut each RSLC to the AOI before coregistering or geocoding it (`tools/rslc_subset.py`, zero Doppler). In GSLC mode the output grid is clipped to the AOI bounding box as well — together, that is the validated WF4 pass. |
| `reference_date` | RSLC only. `null` = the middle of the selected dates (for an even count, the earlier of the two middle dates). The median date minimises the summed temporal baseline to the reference. |
| `start_date`, `end_date` | Select from the RSLCs already in `L1_RSLC/` (inclusive; `null` = no bound). `YYYYMMDD` or `YYYY-MM-DD`. Nothing is downloaded. |
| `tag` (optional) | Appended to the stack name. Needed to override a parameter for a stack that already exists. |

No coregistration stage uses looks — rdr2geo through fine resample all run at full resolution — so `rslc.interferogram.looks`
only sets the RIFG each pair also writes, and it defaults to 1×1. Leave it there unless you know why you are moving it: 1×1 is
what `nisar_timeseries.py` multilooks and compares its own interferogram against, and that gate turns itself off at any other
value. The looks are in the product name either way (`ifg/RIFG_<ref>_<sec>_<F>_<P>_<az>x<rg>.h5`).

## Parameters (`coreg_configs/defaults.yaml`)

All processing values live in one file, set to the values of the validated Nepal GLOF runs. A case config may override
any key by repeating its section, e.g. `rslc: {dense_offsets: {skip_range: 64}}`. `show` prints the values in effect.

| section | what | validated value |
|---|---|---|
| `inputs` | RSLC directory, tier, band, polarisation | `L1_RSLC`, PR, A, HH |
| `dem` | DEM path, source, buffer | `aux/dem/dem_<case>.tif`, NISAR (ellipsoidal), 0.15° |
| `envs` | interpreter for the drivers and the subsetter | `null` = the one running the module (must be isce3) |
| `crop_buffers` | radar window margin around the AOI | 1000 azimuth lines, 12.5 km slant range, +500 px |
| `crop_buffers.align_*` | looks the crop window origin and size are snapped to, so the cropped multilook grid lands on the full tile's | 9 azimuth, 8 range — keep equal to the interferogram looks |
| `rslc.interferogram` | looks of the RIFG written per pair | 1×1 |
| `rslc.rdr2geo` / `geo2rdr` | geometry solver tolerances | 1e-7 / 25 iterations + 10 extra; 1e-8 / 25 |
| `rslc.coarse_resample` | tile size | 1000 × 1000 |
| `rslc.dense_offsets` | ampcor window / half search / skip, range × azimuth | 64 × 64 / 20 × 20 / 32 × 32 |
| rubber sheet | ISCE3 installed defaults (not settable here) | median 9×9, threshold 0.75, fill 1 × 3, linear, boxcar 5×5 |
| `rslc.fine_resample` | tile height | 100 lines |
| `rslc.crossmul` | flatten, oversample (the 1×1 RIFG) | true, 2 |
| `rslc.unwrap` | ISCE3's snaphu, only needed because the ionosphere solve consumes unwrapped phase | off; 9 × 8, nlooks 44.57, tiles [4,4] overlap [256,256] nproc 8 (crop v2) |
| `rslc.ionosphere` | split-spectrum solve (`main_side_band`), dispersive filter | off; filter on, coherence 0.5, median 15 |
| `gslc` | GSLC bands and posting | A, 5 m |
| `gslc.interferogram` | map-domain interferogram after geocoding: on/off, looks on the map grid, coherence window, pairs | off; 8×8 (40 m on the 5 m grid); 5; consecutive dates |
| `run` | parallel units, prune scratch, disk margin, block memory | 2, true, 30 GB, 256 MB |

### Validated dense offsets vs NISAR GUNW production

| setting | ours (ISCE3 defaults, validated) | GUNW |
|---|---|---|
| window, range × azimuth | 64 × 64 | 64 × 96 |
| half search | 20 × 20 | 32 × 32 |
| skip (offset grid spacing) | 32 × 32 ≈ 100 m slant × 143 m along track | 75 × 75 ≈ 234 m × 334 m |
| correlation surface oversampling | 64 | 16 |
| rubber-sheet threshold / fill / interpolation / azimuth filter | 0.75 / 1 × 3 / linear / none | 3 / 15 × 13 / idw / mean 31 |

Estimated effect of switching to GUNW's values (not measured):
- **Time.** Dense offsets ≈ 2.7× faster: 5.5× fewer correlations, each with a search chip about 2× larger.
  - Crop pair: ≈ 16 → 6 min, about 12% of the pair.
  - Full tile: 2 h 24 m → ≈ 52 min.
  - The rubber sheet may be slower (15 fill passes, IDW), which eats part of the saving.
- **Quality.** The correction field is ≈ 2.3× coarser: ≈ 1.7 × 1.2 km after the boxcar, against ≈ 0.7 × 0.5 km for ours.
  - Short-wavelength misregistration on steep slopes (local DEM error) is followed less well.
  - Cost of misregistration: 0.05 sample ≈ 0.2% coherence, 0.1 sample ≈ 0.8%. In azimuth, 0.05 line also adds ≈ 0.2 rad through the Doppler carrier.
  - GUNW's larger window and heavier filling give fewer holes in decorrelated areas.
  - Bulk agreement with GUNW was R 0.978, but that comparison changes many settings at once.

We keep the validated values. A like-for-like crop A/B test (one secondary, `tag: gunwoffsets`) would measure the difference in about an hour.

## Stages

| stage | runs | unit | output |
|---|---|---|---|
| `prepare` | once per case | — | the DEM, if missing |
| `crop` | `crop: true`, either mode | date | `workdir/crop/<aoi>/<date>.h5`, `status/<date>.json`, `params.json` |
| `coreg` set-up | once, again when dates are added | — | `coreg/<stack>/track_r.yaml` or `track_g.yaml` (generated, validated), ISCE3 ingest/runconfig, `status/_setup.json` |
| `coreg` | always | RSLC: secondary; GSLC: date | see below |
| `igram` | GSLC, when `gslc.interferogram.enabled` | pair | `isce3/pairs/<ref>_<sec>/trackG/ifg_<F>_<P>_<ly>x<lx>.{igram,coh,nlooks,amp}.tif`, `status/igram_<d1>_<d2>.json`. Track G step 6; in RSLC mode the stage is a no-op and interferograms come from `nisar_timeseries.py` |

`coreg/<stack>/` (e.g. `RSLC_ref20260726_AHH_glof_bigger_aoi`):
- `slc/<date>.slc` + `.hdr`: coregistered SLCs on the reference grid, the reference included (GDAL-readable ENVI)
- `geometry/{lon,lat,hgt}.rdr` + `.hdr`: reference-grid geolocation (rdr2geo)
- `ifg/RIFG_<ref>_<sec>_A_HH_1x1.h5`: ISCE3 wrapped interferogram per pair
- `runw/RUNW_<ref>_<sec>_A_HH_1x1_unw9x8.h5` with `rslc.ionosphere.enabled`: the unwrapped product carrying
  `science/LSAR/RUNW/swaths/frequencyA/interferogram/HH/ionospherePhaseScreen`, plus `runw/params.json` recording the
  solver settings (the filename carries the product type and the unwrap grid but not the filter parameters, so those
  get their own gate). The screens are what `nisar_timeseries.py` consumes for `corrections.ionosphere: split_spectrum`
- The ionosphere needs frequency A and a side band; the crops already carry frequency B, range-aligned. It is refused
  in GSLC mode, without frequency A, or when the unwrap looks disagree with the crop's alignment looks. `unwrap.nlooks`
  must be set: ISCE3 otherwise derives it from the 1×1 crossmul grid and snaphu aborts at 0.619
- `offsets/<sec>_culled_{az,rg}_offsets`: dense-offset fields behind the rubber sheet
- `params.json`: the parameters this stack was made with; `status/<unit>.json` (checks, inputs, outputs with sizes, git revision, timing); `stack_manifest.json`; `logs/`
- `isce3/`: ISCE3 working tree; per-pair scratch is removed after verification when `run.prune_scratch` is true
- `gslc/<date>_gslc_freq<F>.h5` for GSLC stacks (plus gridgate after all dates)

## Output identity

- **Stack name.** It carries mode, reference, band, polarisation, AOI (or `fulltile`), GSLC posting, the tier when it is not the default `PR`, and the optional `tag`.
- **Crop directory name.** It carries the AOI and, only when they differ from the defaults, the buffers, the alignment looks (`_al<az>x<rg>`), the tier and the polarisation. Every one of those changes the bytes of a crop, and without them in the name a tier or polarisation switch would silently reuse the other one's crops.
- **Interferogram names.** The RSLC RIFG carries its looks, and so does the GSLC map-domain interferogram, so changing looks writes a new product instead of overwriting one. Those two keys are therefore *not* part of the `params.json` lock — everything else is.
- **Parameter lock.** Each stack and crop directory records its parameters in `params.json` on first use. A later run with different values stops with exit 2 instead of mixing outputs.
- **Adding dates** to an existing reference is allowed: same parameters, more units.

## Exit codes and job fan-out

`0` ok · `1` a unit failed · `2` config error · `3` missing prerequisite (crop not done, not enough disk).

For a batch system:
1. Run `--stage prepare` once.
2. Run `--stage crop --dates D` per date.
3. Run `--stage coreg --dates <first secondary>` once; it performs the set-up.
4. Run `--stage coreg --dates D` per remaining secondary.

## Verification (RSLC unit)

A unit is `ok` only if:
- ISCE3 exits 0;
- the RIFG exists;
- the coregistered SLC exists with the same size as the reference SLC.

Outputs are hard-linked into the stack directory before any scratch is deleted.

## Nepal ascending: expected cost

The crop is 12 303 × 22 464 samples (freq A), the same window as the validated crop v2. Timings from crop v2 on the shared 8-core VM:
- **Time.** About 80 min per pair (rdr2geo 22 min, product prep 24 min, dense offsets 16 min, rubber sheet 12 min, the rest ≈ 7 min). Six secondaries with 2 in parallel: roughly 5–6 h, plus ≈ 3 min per crop.
- **Disk.**
  - Scratch: ≈ 32 GB per running pair.
  - Kept per secondary: coregistered SLC 2.2 GB + RIFG.
  - Kept once: reference SLC and geometry ≈ 9 GB.
  - Crops: 7 × 1.8 GB.
  - Peak with 2 pairs running: ≈ 110 GB (154 GB free on 2026-09-15).

## Status

- **Run for real:** the whole cropped-RSLC path, 7 dates and 6 secondaries, 4 h 14 m (`case_studies/nepal_nisar_ascending`), and re-run afterwards with every unit correctly skipped.
- **Checked against crop v2:** the rendered ISCE3 runconfig has the same coregistration settings. Only paths, memory-derived block heights and the product type (RIFG; no unwrap or ionosphere) differ.
- **Tested but not run for real (2026-09-27):** the GSLC path, cropped or not, and the `igram` stage. Both are exercised by `show` and `--dry-run` — which validate the generated driver config with the driver's own loader — but no GSLC has been geocoded through this module.
