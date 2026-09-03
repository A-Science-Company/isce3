# NISAR ISCE3 — working status and plan

**Read this first.** It is a handover for an agent with no prior context. It records what has
been done, what is *verified* versus *assumed*, the traps that already cost hours, and the
remaining plan. Written 2026-08-31 on branch `s1-nisar-setup` of the `A-Science-Company/isce3`
fork.

Companion documents, all in this repo:

| File | What |
|---|---|
| `asc/SETUP.md` | how the environment was built, standalone |
| `asc/docs/nisar-environment-setup.html` | the same, formatted for Confluence |
| `asc/WORKFLOWS.md` | 3045-line workflow architecture and execution plan |
| `asc/nisar_workflows/README.md` | the stage contract and how to add a stage |

---

## 1. What this project is

Build ISCE3-based InSAR processing for NISAR, as a **standalone capability**. This is *not* a
migration off ISCE2 — ISCE2 stays in production and `isce2_env` is never modified. The end goal
is automated batch processing matching the existing ISCE2 orchestrator (§7).

The case study is a **coseismic pair over Venezuela**:

- NISAR L1 RSLC, track 162, frame 7, **ascending**, `lookDirection = Left`
- 2026-06-13 and 2026-06-25, 12-day baseline, B⊥ ≈ 31 m
- Brackets the **Mw 7.5 San Sebastián doublet of 2026-06-24** (USGS `us6000t7zp`, 10.4351°N
  68.4716°W, 10 km depth), preceded 39 s earlier by an Mw 7.2 at 10.436°N 68.528°W
- Right-lateral strike-slip, ~150 × 20 km rupture, propagated **east** from the epicentres
- Frequencies A (40 MHz, 3.12 m slant) and B (5 MHz, 24.98 m slant); pols HH + HV

---

## 2. Where things stand

### Done and verified

**Track G — geocode-to-pinned-grid coregistration.** Ran end to end at 5 m on frequency A over a
186 × 89 km AOI covering the rupture:

| stage | result |
|---|---|
| ingest | pinned geogrid, EPSG 32619 |
| DEM | NISAR DEM v1.2, ellipsoidal, datum verified from ocean pixels |
| GSLC ×2 | grid gate **ALIGNED**, 12 fields match |
| interferogram | coherence median **0.3311** |
| Goldstein + phase-sigma | phsig median **0.5748** |
| water mask | 31.18% of swath |
| unwrap | ran — **but see the open problem below** |
| COG viewer | 7 layers, 3.9 GB, browser range-requests them |

**Environment.** `isce3_env` (isce3 0.25.12 CPU, compass 0.5.6, python 3.12.13) and
`dolphin_env`. 330 of 331 modules import; the one failure is `isce3.cuda`, correct on a CPU build.

### Open

1. **Unwrap fragmentation (blocking).** The tiled unwrap produced **32 connected components of
   near-identical ~1.05 Mpx size** — that is the tile grid, not geophysics. Largest holds 3.6% of
   labelled pixels; the untiled 16×16 run gave 1 component at 98.6%. Each component carries its own
   arbitrary 2π offset, so **only pixels sharing a label are comparable** and the unwrapped field
   is not usable as deformation. Cause and fix in §6.
2. **`L1_RSLC/` is empty.** The 49 GB of granules were cleared for space. Re-download first (§8).
3. **Disk at 93%** — 26 GB free of 380 GB.

---

## 3. The plan

Ordered so each step produces something checkable. Steps 1–5 are the user's; step 0 is inserted
because comparing a fragmented field would measure our bug rather than the tracks.

**0. Fix the Track G unwrap.** Use `snaphu.io.Raster` file-backed I/O so the global
connected-component pass can stream from disk. Done when one component covers a large majority
of valid pixels.

**1. Track R — RSLC coregistration and interferogram.** `python -m nisar.workflows.insar`. This
is the classic reference-scene chain in *radar coordinates* at native resolution — the direct
analogue of the ISCE2 Sentinel-1 workflow. See §4.

**2. Atmospheric and ionospheric corrections on both tracks.** See §5. Note up front: most of
these are **InSAR-only**, so "both tracks" is not achievable as stated.

**3. Compare Track G against Track R.** Same pair, two coregistration philosophies. Reconcile
onto one grid, then compare coherence and unwrapped phase.

**4. Validate against the official GUNW.** A GUNW **does exist** for this pair — see §6.

**5. Automate.** Match the `orch_v3.py` contract in §7.

---

## 4. Track R — what it is and what it costs

The stage order, all in **radar coordinates** until the last step:

```
rdr2geo → geo2rdr → coarse_resample → dense_offsets → rubbersheet
        → fine_resample → crossmul → filter → unwrap → geocode_insar
```

**`rdr2geo` does not reproject your imagery.** It computes, for each pixel of the *reference radar
grid*, the ground point that pixel sees. Its outputs are sized to the radar grid
(`rdr2geo.py:29`: `isce3.io.Raster(out_path, radargrid.width, radargrid.length, ...)`). It is the
same computation ISCE2 calls `topo`. Resampling lands on the reference radar grid
(`resample_slc_v2.py:166-170`), and `geocode_insar` is the only step that leaves radar geometry —
and it is optional.

So Track R gives what Track G cannot: **the reference image is never resampled**, and looks are set
in radar geometry via `processing.crossmul.range_looks` / `azimuth_looks` — genuinely the ISCE2
knob. It also has `dense_offsets` (ampcor) + `rubbersheet`, the data-driven refinement Track G has
no equivalent for.

**The cost, and why the VM is needed.** `rdr2geo` writes x/y/z as **Float64 at full radar-grid
resolution regardless of looks** — 69 GB for those three alone on full-frame frequency A, and
~300 GB peak scratch through the chain. Multilooking does *not* help: looks are consumed only at
`crossmul`, which runs after every coregistration stage. There is no radar-grid decimation option
in 0.25.12.

Interlock to know: disabling `dense_offsets` forces `rubbersheet` off, which forces
`fine_resample` off, and `crossmul` falls back to the coarse-resampled secondary.

---

## 5. Corrections — what actually exists

Verified against installed code. **Two of these contradict what was believed earlier in the
project.**

### The organising distinction

Geolocation corrections are `isce3.core.LUT2d` timing shifts (metres of range, seconds of
azimuth) that change *where geo2rdr lands*. Phase corrections are datacubes in radians emitted as
*separate layers you subtract*. They are not interchangeable.

| Correction | Track | Default | Notes |
|---|---|---|---|
| Ionosphere split-spectrum | **InSAR only** | `spectral_diversity: main_diff_low_high_subband` | 4 options in `schemas/insar.yaml:767` — **not** `split_main_band` as assumed |
| TEC geolocation | **both** | `tec_file` empty | see below |
| Troposphere | **InSAR only** | `weather_model_type: ERA5` | **zero presence in the GSLC path** |
| Solid earth tides (range) | **both** | on | applied as a slant-range LUT |
| Solid earth tides (azimuth) | **neither** | — | see below |

### Correction 1 — TEC is not obtainable from a public source

`tec_file` needs an **IMAGEN TEC JSON**, definitively not IONEX. It is a NISAR
project-generated L1 ancillary delivered *alongside the RSLC*, not a public GNSS product. If the
RSLC bundle did not ship one, there is no TEC geolocation correction available, and the workflow
**proceeds silently without it** (`correct_tec = tec_file is not None`).

This matters at L-band: range delay scales as K/f², so the same TEC produces ~19× the shift at
1.239 GHz versus C-band.

### Correction 2 — azimuth solid-earth tides are discarded in *both* tracks

Earlier in this project this was described as a GSLC-path limitation. That was wrong.
`geocode_corrections.py:330` throws the azimuth component away, and that module is **shared** —
`geocode_insar.py:26,627-628` calls the same `get_az_srg_corrections`. So neither track applies
azimuth SET, and no `azimuthSolidEarthTides` dataset is ever written. The only azimuth timing
correction either track applies is TEC.

### Troposphere requires you to supply the weather model

There is **no download step** — no `cdsapi` call anywhere in `troposphere.py`. You must provide
`troposphere_weather_model_files.{reference,secondary}_troposphere_file`, and
`troposphere_runconfig.py:41-45` raises if either is missing.

### Consequence for step 2 of the plan

"Corrections on both tracks" is not achievable as stated. Realistically:

- **Track R (InSAR)** can have split-spectrum ionosphere (both frequencies present, so
  `main_side_band` is viable), troposphere if weather files are supplied, and range SET.
- **Track G (GSLC)** can have range SET and — only if an IMAGEN TEC JSON exists — TEC. No
  troposphere at all.

So the comparison at step 3 is not like-for-like unless corrections are disabled on both, or the
asymmetry is stated explicitly in the result.

---

## 6. The two things to fix or check next

### The unwrap fragmentation

Both snaphu post-processing options run a **full-grid single-tile pass** after tiling:

- `single_tile_reoptimize` — *"re-optimize the unwrapped phase using a single tile"*
- `regrow_conncomps` — *"labels will be re-computed using a single tile"*

Both default to **True**. With them on, tiling bounds memory right up until the last step asks for
~65 GB (measured: snaphu is linear at **385 bytes/pixel**). With them off, components are never
globally relabelled and you get one component per tile.

The fix is `snaphu.io.Raster`: *"Data access is performed lazily — the raster contents are not
stored in memory unless/until explicitly accessed."* `unwrap()` accepts `unw=` and `conncomp=`
output datasets, so the global pass can stream from the GeoTIFFs already on disk instead of taking
numpy arrays.

### The GUNW comparison is viable

**An official GUNW exists for this pair.** Search it with either:

```python
asf.search(dataset=asf.DATASET.NISAR, processingLevel="GUNW",
           relativeOrbit=162, frame=7, start="2026-06-01", end="2026-07-01")
```

or raw CMR with `short_name=NISAR_L2_GUNW_PROVISIONAL_V1`.

**Do not use `opera_utils` for GUNW.** Its `NISAR_SDS_FILE_REGEX` allows exactly one
start/end datetime pair; a GUNW name has two, so every GUNW is silently dropped by an
`except (ValueError, KeyError): continue`. This was tested, not assumed.

One result already in hand: our `ref * conj(sec)` convention (`igram.py:267`) and the GUNW
**correlate at +0.999** — same sign convention. The absolute sign against spec D-102272 remains
UNVERIFIED offline.

Note the GUNW is produced with the full Track R chain — dense offsets (64×96 window, ±32 search,
skip 75, 16× oversampling) plus rubbersheet and fine_resample — whereas our Track G is geometry
only. Differences between them are expected and informative, not automatically our error.

---

## 7. The automation target

`orch_v3.py` (1368 lines, `~/Desktop/work/isce2-orchestrator/`) is a **GCP Batch job submitter**.
It processes nothing itself: it lists a GCS bucket, turns each SLC pair into one Batch task
environment, and submits a job whose tasks all run the same container command.

```
orch_v3.py ──lists GCS──> gs://s1-slc/<roi>/tile_N/SLC/*.zip
     │  one Batch job, N tasks, parallelism = N
     ▼
GCP Batch task ──runs──> isce2 image ──> run_interferogram_wrapper.py --slc1 $SLC1 --slc2 $SLC2
```

**Three enumeration modes**, checked in order: `--pair "tile:date1,date2"` (explicit),
`--from_json` (replay a previous job), and the default **GCS scan → date-sorted consecutive
pairs**. The last is the batch mode.

**The reference file** is `--reference_slc "tile_id:filename"`, repeatable, surfacing as a
`REFERENCE_SLC` task env var.

**Per-task env vars** — this is the interface a NISAR orchestrator must produce:

```
SLC1, SLC2, TILE_ID, EVENT_ID, INPUT_BUCKET, OUTPUT_BUCKET,
ROI_NAME, OUTPUT_FOLDER, RESOLUTION_SPECS
optional: KML_FILE, KML_GCS_BLOB, REFERENCE_SLC
```

**Other conventions**: `--resolution` defaults to `["10m,unwrap=true", "4m,unwrap=false"]`;
`--monitor` retries SPOT failures on STANDARD VMs; `PROJECT_ID`, `LOCATION` and `IMAGE_URI` are
**module constants at lines 19-25**, changeable only by editing the file.

**Gap worth closing when we build the NISAR equivalent**: `orch_v3.py` has no output-existence
check, so a re-run reprocesses everything. The mechanism already exists in the repo —
`orch_unwrap.py:43-67` is a working `--skip_done` probe.

---

## 7b. Switching site — Nepal GLOF

The AOI moves from Venezuela to a **Nepal GLOF / flash-flood event**. The workflow is
site-agnostic: ingest derives track, frame, orbit direction, look side, dates, EPSG and the
pinned geogrid **from the granules themselves**. Nothing about Venezuela is hardcoded in the
code — only in `configs/venezuela_t162_asc.yaml`.

Use `configs/nepal_glof.yaml` (already in the repo) and set `case_dir`, then `aoi_lonlat` once
you have looked at the data.

Data: `gs://s1-slc/nepal/nisar/ascending/tile_1/`

```bash
gsutil -m cp 'gs://s1-slc/nepal/nisar/ascending/tile_1/**/*RSLC*.h5' \
    <case_dir>/L1_RSLC/
```

**The science is different, and it changes what matters.** Venezuela was coseismic: the signal was
deformation, read from unwrapped phase. A GLOF's strongest signatures are **amplitude change**
(channel scour, new deposits, inundation) and **coherence loss** (surface disturbance between
passes). Both come out of stages 6–7 and need no unwrapping.

Two consequences:

- **The open unwrap problem (§2) is not blocking for a first look at Nepal.** Run through
  `watermask` and read amplitude and coherence.
- **Low coherence along the flood path is the signal, not a defect.** Do not pick the AOI by
  maximising coherence — that mistake was made on Venezuela and selected quiet terrain 100 km from
  the rupture. Pick the AOI from what the imagery shows: source lake, flood path, depositional
  reach.

Also unknown until the granules are inspected: the temporal baseline, whether the pair actually
brackets the event, and whether frequency B exists. Stage A (`ingest`) reports all of these in
seconds and costs nothing — run it first.

---

## 8. Getting running on a new machine

```bash
# 1. environment
cd <repo>
conda env create -f asc/env/isce3_env.yml
conda env create -f asc/env/dolphin_env.yml
bash asc/env/verify.sh                      # must print ALL CHECKS PASSED

# 2. credentials — one Earthdata login covers everything
echo "machine urs.earthdata.nasa.gov login USER password PASS" > ~/.netrc && chmod 600 ~/.netrc

# 3. get the granules  (L1_RSLC/ is empty in both case dirs)
#    Nepal:
gsutil -m cp 'gs://s1-slc/nepal/nisar/ascending/tile_1/**/*RSLC*.h5' \
  <repo>/case_studies/nepal_glof/L1_RSLC/
#    Venezuela:
gsutil -m cp \
  'gs://s1-slc/venezuela/ascending/nisar/tile_1/SLC/NISAR_L1_PR_RSLC_02*_162_A_007_4005_DHDH_A_2026*.h5' \
  <repo>/case_studies/venezuela_t162_asc/L1_RSLC/

# 4. run Track G  (ingest first -- it is instant and reports what the pair IS)
cd asc/nisar_workflows
python run_track_g.py --config configs/nepal_glof.yaml --list-steps
python run_track_g.py --config configs/nepal_glof.yaml --only ingest
python run_track_g.py --config configs/nepal_glof.yaml --start-step dem --stop-step watermask
```

Steps: `1 ingest · 2 dem · 3 gslc · 4 gridgate · 5 qa · 6 igram · 7 watermask · 8 unwrap ·
9 overlay`. Flags: `--only`, `--start-step`, `--stop-step`, `--force`, `--dry-run`, `--list-steps`.

---

## 9. Hard-won knowledge — do not rediscover these

**Measured constants** (not estimates):

| | |
|---|---|
| snaphu memory | **385 bytes/pixel**, linear across five grid sizes |
| snaphu with tiling | peak tracks **tile size × nproc**, not total grid |
| GSLC runtime | 3m26s for a 144 Mpx pair; 47m58s for 1690 Mpx |
| freq A ground spacing | 4.724 m range (mid-swath) × 4.456 m azimuth — near-square |
| freq B ground spacing | 37.79 m range × 4.456 m azimuth — 8.49:1 anisotropic |
| disk at 1×1 looks | 72.4 GiB of downstream products for a 186 × 89 km AOI |

**Traps that fail silently:**

- **python 3.11** installs fine, then `from nisar.workflows import insar` dies — `pyaps3` needs
  `pygrib`, and conda-forge has no py311 pygrib build.
- **Importing isce3 parses `sys.argv`** — `pyre.__init__` calls `executive.activate()` at import
  time, so any CLI with flags breaks. Scrub argv before importing.
- **Coherence at 1×1 looks is exactly 1.0 everywhere.** The boxcar estimator over a single sample
  is degenerate. Use a sliding window; the estimator bias floor is √π/(2√N).
- **The NASADEM water-mask route returns an all-water mask and exits 0.**
- **A missing PROJ EGM2008 grid** turns the ellipsoid↔geoid conversion into a no-op that still
  looks like a DEM. Probe against the published 17.2 m at (0°,0°).
- **Multilooking does not reduce coregistration scratch** — looks apply at `crossmul`, after every
  coregistration stage.

**The stale-identity pattern.** Four separate bugs shared one shape: *an output identity that
omitted something which changes the output*, so a stale artifact passed an `exists()` check — the
overlay HTML not namespaced by frequency, interferogram idempotency ignoring the geogrid, and two
config literals (`reference_raster`, `ocean_probe`) left over from an earlier frequency and AOI.
When adding a stage, make its output identity include everything that changes its output, and
prefer comparing actual raster geometry over trusting a filename.

**Everything NISAR here is our own construction.** The isce+ course never ran
`nisar.workflows.insar` or `nisar.workflows.gslc` — its NISAR notebooks only *read* existing
products (`NISAR_RSLC_Tutorial.ipynb`: 42 cells, zero generation calls, 19 × `h5py.File`). The
method was ported from the Sentinel-1 COMPASS path, which the course *does* run, in
`--grid geo` mode. There is no worked example to check NISAR generation against.

---

## 10. Open questions

- Does the RSLC bundle include an IMAGEN TEC JSON? If not, TEC geolocation correction is
  unavailable on both tracks. Settle by listing the granule bundle.
- Where do ERA5 weather model files come from for the troposphere stage? The workflow has no
  downloader.
- Track R on frequency A needs an RSLC crop tool that does not exist. Writing one means subsetting
  `swaths/frequencyX/*`, `zeroDopplerTime`, `validSamplesSubSwath`, the `metadata/geolocationGrid`
  cubes, orbit, attitude and the identification bounding polygon consistently.
- Absolute phase sign against NISAR spec D-102272 — verified only relatively (+0.999 correlation
  with the GUNW).
