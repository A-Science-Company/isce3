# ISCE3 workflow architecture — Venezuela NISAR RSLC pair

Design document for the modular processing chain we build on top of the
environment in [`SETUP.md`](SETUP.md). Written before the first full run, to be
amended as bugs surface.

- **Branch:** `s1-nisar-setup`
- **Envs:** `isce3_env` (isce3 0.25.12, compass 0.5.6, snaphu-py 0.4.1, sardem, python 3.12)
  and `dolphin_env` (dolphin 0.42.5, mintpy 1.6.4, opera_utils 0.25.6)
- **Machine:** 16 cores, 12.7 GiB RAM (~3.9 GiB free right now), no NVIDIA GPU,
  94 GB free on `/` — everything is on one partition.

Everything below was verified against the installed packages and the two HDF5
files on disk. Where a number is derived rather than read, it says so. Where
something is genuinely unknown it is marked **UNVERIFIED** with the command that
would settle it.

---

## 1. The data

Two granules, 52.6 GB total, in
`/home/sharath/Desktop/work/isce3/case_studies/venezuela_descending/`:

```
NISAR_L1_PR_RSLC_022_162_A_007_4005_DHDH_A_20260613T100656_20260613T100731_P05023_N_F_J_001.h5   26.31 GB
NISAR_L1_PR_RSLC_023_162_A_007_4005_DHDH_A_20260625T100655_20260625T100730_P05023_N_F_J_002.h5   26.30 GB
```

### 1.1 The directory name is wrong: these are ASCENDING

**Do this before anything else.** Three independent checks agree:

1. Granule field 7 is `A`.
2. `/science/LSAR/identification/orbitPassDirection` = `Ascending` in both files.
3. The bounding polygon's latitude increases monotonically along azimuth
   (9.54 → 12.21 N), and mid-scene `Vz` is strongly positive.

```bash
mv /home/sharath/Desktop/work/isce3/case_studies/venezuela_descending \
   /home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc
```

Every path in this document assumes that rename has happened. Define once per
shell:

```bash
export CASE=/home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc
```

**Also: `lookDirection = Left`.** NISAR L-SAR looks left. The platform is at
lon −63.20 while the swath centre is at lon −68.30 — the antenna points west
while flying north. Any code that hardcodes a right-looking convention
(`side='right'`, `isce3.core.LookSide.Right`) will silently produce garbage. The
`nisar.workflows` chain reads this from the product and is safe; anything we
write by hand is not.

### 1.2 Decoded filename

Field-by-field, each cross-checked against `/science/LSAR/identification`.

| Field | Ref | Sec | Meaning |
|---|---|---|---|
| 1 `NISAR` | NISAR | NISAR | mission |
| 2 `L1` | L1 | L1 | processing level |
| 3 `PR` | PR | PR | processing type = Production |
| 4 `RSLC` | RSLC | RSLC | range-Doppler single-look complex |
| 5 cycle | **022** | **023** | consecutive mission cycles |
| 6 track | 162 | 162 | relative orbit |
| 7 direction | **A** | **A** | **Ascending** |
| 8 frame | 007 | 007 | frame number (`frameNumber = 7`) |
| 9 bandwidth | 4005 | 4005 | **40 MHz on freq A, 05 MHz on freq B** |
| 10 pol | DHDH | DHDH | **dual-pol H-transmit (HH+HV) on both bands** |
| 11 mode | A | A | single Acquisition mode (`isMixedMode = False`) |
| 12 start | 20260613T100656 | 20260625T100655 | UTC |
| 13 end | 20260613T100731 | 20260625T100730 | UTC |
| 14 CRID | P05023 | P05023 | composite release id |
| 15 accuracy | **N** | **N** | orbit/pointing accuracy code |
| 16 coverage | F | F | Full frame (`isFullFrame = True`) |
| 17 SDS | J | J | JPL |
| 18 counter | 001 | 002 | product counter |

On field 15: the schema enumerates the legal values
(`primary_executable.product_accuracy: enum('P','M','N','F')`,
`schemas/insar.yaml:86`) but nothing in the products states what the letters
mean. What is verified: the value is `N`, and
`/science/LSAR/RSLC/metadata/orbit/orbitType` is **`MOE`** (medium/mid-ephemeris),
not `POE`. **These are non-precise orbits.** That is the operationally relevant
fact regardless of the letter legend.

### 1.3 Frequencies, polarizations, grids

Read directly from the HDF5. Note the dataset is
`processedCenterFrequency`, **not** `centerFrequency` (a common mis-cite).

| | Frequency A | Frequency B |
|---|---|---|
| Shape (az × rg) | **54720 × 52649** (ref) / **54720 × 52650** (sec) | 54720 × 6582 (both) |
| Polarizations | HH, HV | HH, HV |
| `processedCenterFrequency` | **1239.0 MHz** | 1293.5 MHz |
| Wavelength | **0.241963 m** | 0.231768 m |
| Range bandwidth | **40 MHz** | 5 MHz |
| Slant range spacing | 3.12284 m | 24.98270 m |
| Slant range start | 882 289.2039 m | 882 289.2039 m (identical) |
| Slant range end | 1 046 700.3844 m (ref) | 1 046 700.3844 m |
| Ground range spacing @ centre | 4.725 m | 37.800 m |
| Azimuth spacing @ centre | 4.456 m | 4.456 m |
| Pixels | 2.881 Gpx | 0.360 Gpx |
| One 8-byte full-res raster | **23.05 GB** | 2.88 GB |

Two irregularities to carry forward:

- **The secondary freq A is one sample wider** (52 650 vs 52 649), ending at
  1 046 703.5072 m. Harmless for the workflow — the secondary is resampled onto
  the reference grid — but it means the two files are not byte-comparable and any
  hand-written `ref * conj(sec)` on raw arrays is wrong.
- **`listOfPolarizations` order differs**: ref freq A is `['HH','HV']`, secondary
  freq A is `['HV','HH']`. Never index by position; index by name.

**PRF — two different numbers, do not conflate.**
`nominalAcquisitionPRF = 1909.6437 Hz` is the dithered raw acquisition rate
(`isDithered = True`). The RSLC has been presummed (`azimuthPresumming: BLU`)
onto a uniform grid with `zeroDopplerTimeSpacing = 6.578947e-4 s`, i.e.
**1520.0 Hz**. `54720 / 1520 = 36.0 s`. Use 1520 Hz for all
azimuth-pixel ↔ time arithmetic.

Other verified metadata: zero-Doppler product (`isce3.core.LUT2d()` is the
correct grid Doppler for `geo2rdr`); one sub-swath; incidence 33.16°–47.35° at
h ≈ 0, widening to 33.16°–48.13° across the full −500…9000 m geolocation cube;
`azimuthCompression: time-domain backprojection`; RFI tone-rank detect and
mitigate; `hasInputDataException = 0` and an all-zero `inputDataExceptionMask`.

`validSamplesSubSwath1` on freq A has a median valid window of **50 981 of
52 649 samples (96.8%)**, with the start index varying 1479–8435 (reference) as
the dithered PRF moves the transmit blanking. There are isolated bad lines:
reference line 9078 is 44 037 samples wide; secondary lines 9382 (27 996) and
9115 (46 562). Three lines out of 54 720 — negligible, but they are why the
workflow honours the valid-sample mask.

### 1.4 Extent and where it actually is

Bounding polygon (reference): lon **−69.605 … −66.843**, lat **9.526 … 12.214**.
Bbox 301 km E–W × 295 km N–S; the actual frame is a rotated parallelogram,
**≈243.5 km along-track × ≈252 km across-track**, centroid **10.880 N, 68.222 W**.
The along-track figure is confirmed independently: 35.999 s × 6767 m/s mean
`groundTrackVelocity` = 243.6 km.

**Land/water split: ≈62% land, ≈38% Caribbean Sea.** Computed by splitting the
polygon at its two zero-height boundary crossings (near/east edge at
−66.9914, 10.7085; far/west edge at −69.5792, 11.5762) — land 3.166 deg²,
sea 1.921 deg², summing exactly to the parent area 5.087 deg². Counting
negative-height vertices (18 of 41) badly overstates the water because ten of
them are packed along the short north edge. The coastline runs diagonally: sea
covers ~80% of the eastern/near edge but only ~10% of the western/far edge.

Land coverage: the Venezuelan Coastal Range and northern llanos —
Aragua, Carabobo, Yaracuy, Lara, Falcón, Cojedes, edges of Guárico and
Portuguesa. Confirmed inside the polygon by point-in-polygon test:
**Lake Valencia, Valencia, Maracay, Puerto Cabello**. Confirmed **outside**:
Barquisimeto, Caracas, Coro, Maracaibo, Curaçao, Bonaire, Aruba, Los Roques.
The near-range boundary at lat 10.488 is lon −66.955; Caracas centre
(−66.879) is ~8 km outside the frame. **Do not plan a Caracas study on this
frame.**

A water mask is still worth wiring in — 38% of the frame will decorrelate
completely and SNAPHU will otherwise spend effort unwrapping noise and can seed
errors that leak onto land.

### 1.5 The pair

| | Reference (cycle 022) | Secondary (cycle 023) |
|---|---|---|
| `zeroDopplerStartTime` | 2026-06-13T10:06:56.000000000 | 2026-06-25T10:06:55.000000000 |
| `zeroDopplerEndTime` | 2026-06-13T10:07:31.999342105 | 2026-06-25T10:07:30.999342105 |
| Duration | 35.999342 s | 35.999342 s |
| Absolute orbit | 4586 | 4759 (Δ = 173) |
| `isUrgentObservation` | False | True |

**Temporal baseline: 12 days minus exactly 1.000 s** (1 036 799.000 s). The
day-epoch offset between the two files' time units *is* exactly 1 036 800 s;
the acquisition start times differ by one second less. Same track, same frame,
consecutive cycles, 173 orbits — a textbook-valid NISAR repeat pair.

**Footprint overlap:** intersection 5.043 deg² = **99.13% of the reference
frame**, IoU 98.26%.

**Perpendicular baseline: |B⊥| ≈ 31 m**, range 26.5–35.5 m across the frame
(recomputed by geo2rdr against each orbit on the true radar grid, `LookSide.Left`,
zero-Doppler LUT, h = 0: 32.60 / 29.22 / 26.54 at scene start near/mid/far;
35.36 / 31.77 / 28.91 at scene end). Total baseline |B| ≈ 43–47 m, almost
entirely along-track. Sign is a convention choice.

Note the near/mid/far slant ranges are **882.289 / 964.495 / 1046.700 km** — the
*radar grid*. The `metadata/geolocationGrid/slantRange` cube spans
879 782.94 … 1 049 206.65 m, which is a padded lookup grid, not the swath.

Consequences of B⊥ ≈ 31 m:

- Critical baseline ≈ **27.4 km** → geometric decorrelation ≈ **0.11%, i.e. nil**.
- **Height of ambiguity ≈ 2508 m** → a 100 m DEM error contributes ~0.04 fringe.
  **DEM accuracy is nearly irrelevant to the phase.** The DEM's job here is to be
  *identical and stationary* between the two dates, not accurate.
- Correspondingly, this pair is **useless for topographic reconstruction**.
- The entire coherence budget is temporal + volumetric + ionospheric. At 10° N
  latitude, L-band, over tropical terrain: expect usable coherence on
  bare/urban/sparse ground, degraded over the forested interior, and
  **ionosphere as a first-order error term, not a refinement.**

### 1.6 Split-spectrum ionosphere is available

Frequency B exists in both granules with 5 MHz bandwidth, centred 54.5 MHz above
freq A (1239.0 → 1293.5 MHz). Both bands share the same slant-range **start**
(882 289.2039 m) and extent, but freq B is exactly **8× decimated in range**
(24.98270 vs 3.12284 m spacing) — so a common grid is not free. ISCE3 handles
this: `nisar/workflows/ionosphere.py:24-25` imports
`decimate_freq_a_array` and `interpolate_freq_b_array` from
`isce3.signal.interpolate_by_range` and uses them to move between the two
grids (`ionosphere.py:226`, `:356`).

So `spectral_diversity: main_side_band` and `main_diff_ms_band` are both
mechanically viable on this pair. The caveat is resolution, not plumbing: a
5 MHz side band gives ~30 m slant / ~37.8 m ground-range resolution, so the
dispersive estimate is coarse and depends on the default `dispersive_filter`
(100×100 kernel) to be usable.

The shipped default is `main_diff_low_high_subband`, which splits freq A's own
band and does **not** use freq B. Requirement asymmetry, enforced at config load
in `ionosphere_runconfig.py`:

- `split_main_band` / `main_diff_low_high_subband`: frequency B must **not**
  appear in `ionosphere_phase_correction.list_of_frequencies` (raises `ValueError`).
- `main_side_band` / `main_diff_ms_band`: frequency B **is** required, in both RSLCs.

### 1.7 What two acquisitions can and cannot do

**Can, honestly:**

- One wrapped and one unwrapped interferogram, coherence, connected components.
- A coseismic / co-eruptive LOS displacement map: `d_LOS = −λ·φ/(4π)`, with
  λ = 0.241963 m ⇒ **one fringe = 12.10 cm of LOS range change**. (The course's
  C-band examples are 2.8 cm/fringe; a given deformation makes ~4.3× *fewer*
  fringes here. That is a feature — it buys unwrappability across steep gradients.)
- Per-pair correction layers: ionosphere screen, wet + hydrostatic troposphere,
  solid Earth tides. All of these are per-pair by construction.
- Pixel offsets (range + azimuth), which survive decorrelation and large
  gradients and give the azimuth component that phase cannot.
- The whole course-2.2 modeling chain: quadtree → `.okinv` → Okada/Mogi
  forward model → Powell inversion. Single-pair by design.
- MintPy `load_data` on one GUNW — chiefly as a **geometry generator**.

**Runs but is meaningless at N=2 (silent, not an error):**

- Amplitude dispersion / PS selection: σ/μ over two samples.
- dolphin temporal coherence: it is phase-linking misfit, and a rank-1 fit to a
  2×2 covariance matrix is exact. It will report ~1.0 everywhere.
- Phase linking / EMI / EVD: degenerates to the plain interferogram.
- Timeseries inversion: dolphin correctly *skips* it
  (`dolphin/timeseries.py`, "only single reference interferograms exist").
  MintPy's is exactly determined; `temporalCoherence.h5` = 1 everywhere.
- Velocity: two points define a line with zero residual. It is `d_LOS / Δt`
  dressed up as a rate.

**Strictly impossible:**

- SBAS or any network inversion; network modification; residual-RMS ranking.
- Phase-closure unwrap-error correction — needs triplets, so ≥3 acquisitions.
  MintPy returns "No triangles found from ifgramStack file!".
- DEM-error / topographic residual estimation — needs B⊥ diversity over time
  (and at B⊥ ≈ 31 m there is nothing to estimate anyway).
- Seasonal or nonlinear time functions.
- **LOS decomposition into E/U — that needs two *geometries*, not two dates.**
  One track gives one equation and three unknowns.

The highest-value second acquisition is therefore **a descending pass over the
same ground**, not a third date on track 162.

---

## 2. Architecture

### 2.1 The organising principle

`nisar.workflows` is already modular in the way we need, and the modularity has a
specific shape worth designing around rather than fighting:

**Every InSAR submodule accepts the same runconfig.** `RubbersheetRunConfig`,
`ResampleSlcRunConfig`, `GeocodeInsarRunConfig`, `InsarIonosphereRunConfig`,
`InsarTroposphereRunConfig`, `InsarSolidEarthTidesRunConfig` all call
`super().__init__(args, 'insar')`. So

```bash
python -m nisar.workflows.insar     runconfig.yaml     # everything
python -m nisar.workflows.rdr2geo   runconfig.yaml     # just that stage
python -m nisar.workflows.geo2rdr   runconfig.yaml
python -m nisar.workflows.crossmul  runconfig.yaml
python -m nisar.workflows.unwrap    runconfig.yaml
```

are all legal against **one file**. Stage isolation is free; we do not need to
build a runner. What we *do* need to build is everything on either side of that
box — ingest, DEM staging, geometry export, model-ready export — plus the
sidecar metadata that stops the pieces from silently disagreeing.

Two rules that fall out of this and that the rest of the design obeys:

1. **The runconfig is the interface.** One YAML per pair, checked into the case
   study directory next to its outputs. Stage boundaries are file boundaries, not
   function-call boundaries.
2. **Nothing infers wavelength or sign convention.** Both travel explicitly in a
   JSON sidecar at every hand-off. This is not bureaucracy: the course's kite
   notebook hardcodes `wavel = 0.0555`, and pointing that at NISAR makes every
   displacement wrong by a factor of 4.36.

Caveat on standalone runs: some stages impose requirements the pipeline supplies
implicitly. `GeocodeInsarRunConfig.yaml_check` demands
`processing.geocode.runw_path` exist as a file. And resume-from-logfile
(`Persistence`) reads `run_steps` out of the log — `--restart` sets them all True.
`ResampleSlcRunConfig.yaml_check` hard-fails if `scratch/geo2rdr/freqX/range.off`
is missing, so deleting scratch forecloses re-entry.

### 2.2 The pipeline

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ INPUTS (read-only, never modified, never renamed)                        │
 │   case_studies/venezuela_t162_asc/L1_RSLC/NISAR_L1_PR_RSLC_*.h5   52.6 GB │
 └──────────────────────────────────────────────────────────────────────────┘
        │
   ┌────▼──────────┐        ┌──────────────────┐
   │ A. INGEST     │        │ B. AUX STAGING   │   (independent of A; run in
   │ h5py+shapely  │        │ sardem           │    parallel)
   │ → stack.json  │        │ → dem.tif        │
   │   pair.json   │        │ → watermask.wbd  │
   └────┬──────────┘        └────────┬─────────┘
        │                            │
        │   ┌────────────────────────┘
        │   │
   ┌────▼───▼───────────────────────────────────────────────────────────────┐
   │ C. RSLC WINDOW CUT   [OPTIONAL, NET-NEW CODE, needed only for freq A]  │
   │    h5py → cropped RSLC pair on a reduced radar grid                    │
   └────┬───────────────────────────────────────────────────────────────────┘
        │
   ┌────▼───────────────────────────────────────────────────────────────────┐
   │ D. INSAR  —  python -m nisar.workflows.insar  cfg/insar_<tag>.yaml     │
   │                                                                         │
   │   D1 rdr2geo    (ref RSLC + DEM)            → scratch/rdr2geo/*.rdr     │
   │   D2 geo2rdr    (sec RSLC + topo.vrt)       → scratch/geo2rdr/*.off     │
   │   D3 coarse_resample                        → coregistered_secondary.slc│
   │   D4 dense_offsets   [OFF by choice]        → ampcor residual field     │
   │   D5 rubbersheet     [OFF by choice]        → summed offsets            │
   │   D6 fine_resample   [OFF by choice]                                    │
   │   D7 crossmul        → RIFG  (wrapped ifg + coherence, 11×11 looks)     │
   │   D8 filter_interferogram  [default no_filter]                          │
   │   D9 unwrap (snaphu) → RUNW  (unwrapped phase, conncomp, mask)          │
   │   D10 split_spectrum + ionosphere → RUNW ionospherePhaseScreen          │
   │   D11 geocode_insar  → GUNW  (RIFG then RUNW, twice)                    │
   │   D12 troposphere    [needs weather files + internet]                   │
   │   D13 solid_earth_tides  [unconditional whenever GUNW exists]           │
   │   D14 baseline       → B∥ / B⊥ cubes in every product                   │
   └────┬─────────────────────────┬──────────────────────┬───────────────────┘
        │ RIFG/RUNW               │ GUNW                 │ ROFF/GOFF
        │ (radar geometry)        │ (geocoded)           │ (offsets, alt path)
        │                         │
   ┌────▼──────────┐        ┌─────▼──────────────────────────────────────────┐
   │ E. GEOMETRY   │        │ F. CORRECTION APPLY                            │
   │ get_product_  │        │ φ_corr = φ − φ_iono − φ_wet − φ_hydro − φ_SET   │
   │ geometry.py   │        │ (screens ship as separate layers; the workflow  │
   │ or GUNW cube  │        │  NEVER subtracts them for you)                 │
   │ + DEM interp  │        └─────┬──────────────────────────────────────────┘
   │ → inc / azi   │              │
   └────┬──────────┘              │
        │                         │
        └──────────┬──────────────┘
                   │
   ┌───────────────▼─────────────────────────────────────────────────────────┐
   │ G. MODEL-READY EXPORT  ("contract C")                                   │
   │    5 single-band EPSG:4326 rasters on ONE grid, band 1:                 │
   │    unw_ll.tif  coh_ll.tif  inc_ll.tif  azi_ll.tif  mask_ll.tif          │
   │    + model.json  { wavelength_m, sign_convention, los convention }      │
   └───────────────┬─────────────────────────────────────────────────────────┘
                   │
   ┌───────────────▼─────────────────────────────────────────────────────────┐
   │ H. MODELING   kite quadtree → .okinv → Okada / Mogi → Powell inversion  │
   └─────────────────────────────────────────────────────────────────────────┘

  ══════════════════ N-IMAGE EXTENSIONS (structurally present, inert at N=2) ══

   ┌─────────────────────────┐        ┌──────────────────────────────────────┐
   │ I. GSLC BRANCH          │        │ J. GUNW → MintPy                     │
   │ nisar.workflows.gslc    │        │ smallbaselineApp.py <case>.txt       │
   │ per date, PINNED geogrid│        │ mintpy.load.processor = nisar        │
   │        ↓                │        │ mintpy.load.unwFile = pairs/*/GUNW/* │
   │ dolphin config / run    │        │ mintpy.load.demFile = aux/dem/*.tif  │
   │ (dolphin_env)           │        │ (dolphin_env)                        │
   └─────────────────────────┘        └──────────────────────────────────────┘
        needs N≥8 to mean anything          needs N≥3 for closure,
        (PS, phase linking, tcoh)           N≥5 for a useful network
```

### 2.3 Stage contracts

Each box below is independently runnable and independently containerisable.
"Container" = one image per env (`isce3_env` or `dolphin_env`), inputs mounted
read-only, `scratch/` and the output directory mounted read-write, the runconfig
mounted read-only.

---

**A. INGEST** — env `isce3_env`, net-new code.

| | |
|---|---|
| IN | `L1_RSLC/*.h5` (2 files) |
| OUT | `stack.json`, `pairs/<date12>/pair.json` |
| CMD | `python asc/tools/ingest_rslc.py $CASE` |
| CFG | none (arguments only) |
| CONTRACT | Emits `wavelength_m`, `frequency`, `polarization`, radar grid shape, orbit type, bounding polygon, temporal + perpendicular baseline. Fails loudly if the two granules differ in track/frame/direction/bandwidth mode. |

`stack.json` is the identity file — everything downstream reads it and nothing
re-derives what it contains.

```json
{
  "schema": "insar-stack/1",
  "site": "venezuela", "platform": "NISAR",
  "track": 162, "direction": "A", "frame": 7,
  "look_side": "Left",
  "frequency": "A", "polarization": "HH",
  "processed_center_frequency_hz": 1239000000.0,
  "wavelength_m": 0.241963,
  "prf_hz": 1520.0,
  "radar_grid": {"length": 54720, "width": 52649,
                 "slant_range_start_m": 882289.2039,
                 "slant_range_spacing_m": 3.1228381},
  "freq_b": {"present": true, "bandwidth_hz": 5000000.0,
             "processed_center_frequency_hz": 1293500000.0,
             "range_decimation_vs_a": 8},
  "aoi_bbox_wgs84": [-69.75, 9.40, -66.70, 12.35],
  "acquisitions": [
    {"date": "20260613", "role": "reference", "cycle": 22,
     "granule": "NISAR_L1_PR_RSLC_022_162_A_007_4005_DHDH_A_20260613T100656_20260613T100731_P05023_N_F_J_001.h5",
     "orbit_type": "MOE", "absolute_orbit": 4586},
    {"date": "20260625", "role": "secondary", "cycle": 23,
     "granule": "NISAR_L1_PR_RSLC_023_162_A_007_4005_DHDH_A_20260625T100655_20260625T100730_P05023_N_F_J_002.h5",
     "orbit_type": "MOE", "absolute_orbit": 4759}
  ]
}
```

---

**B. AUX STAGING** — env `isce3_env`, `sardem`.

| | |
|---|---|
| IN | bbox from `stack.json` |
| OUT | `aux/dem/dem_t162_f007.tif`, `aux/water/watermask_t162_f007.wbd` |
| CMD | see §6 step 2 |
| CFG | none |
| CONTRACT | DEM is **ellipsoidal height, WGS84** (never geoid), float32 GTiff, EPSG:4326, covering the union polygon + ~10 km. Its EPSG becomes the default `geocode.output_epsg`. |

Per-track, not per-pair, not per-date. Adding dates never re-runs this.

---

**C. RSLC WINDOW CUT** — env `isce3_env`, **net-new code, does not exist in
ISCE3.** Only needed for a frequency-A run (see §7 risk 1).

| | |
|---|---|
| IN | `L1_RSLC/*.h5`, an azimuth-line range and a range-sample range |
| OUT | `L1_RSLC_crop/*.h5` on a reduced radar grid |
| CMD | `python asc/tools/crop_rslc.py --az 0 30000 --rg 0 26000 <in.h5> <out.h5>` |
| CONTRACT | Must consistently slice `swaths/frequency{A,B}/{HH,HV}`, `swaths/frequency{A,B}/slantRange`, `swaths/zeroDopplerTime`, `validSamplesSubSwath1` (and shift its indices), while leaving `metadata/orbit`, `metadata/attitude` and `metadata/geolocationGrid` intact and updating `identification/{zeroDopplerStartTime, zeroDopplerEndTime, boundingPolygon}`. Frequency B must be cut at exactly 1/8 the range indices of frequency A or the sideband ionosphere method breaks. |

There is no crop utility anywhere in the installed `nisar` package
(`grep -rn "def crop\|def subset" .` returns nothing under `nisar/`). This is
real work, and it is the reason §6 sequences frequency B first.

---

**D. INSAR** — env `isce3_env`, stock ISCE3.

| | |
|---|---|
| IN | 2 RSLC `.h5`, `dem_file`, optionally `water_mask_file`, optionally external orbit XMLs |
| OUT | `pairs/<date12>/{RIFG,RUNW,GUNW}/*.h5` plus `scratch/` |
| CMD | `python -m nisar.workflows.insar cfg/insar_<tag>.yaml` |
| CFG | one YAML, schema `nisar/workflows/schemas/insar.yaml` |
| CONTRACT | Output product type is decided by `primary_executable.product_type` alone. Intermediates land in `scratch_path` for `GUNW`; with `RIFG_RUNW_GUNW` all three are delivered next to `sas_output_file` with the type prepended. |

Runconfig keys that are **schema-required** — must be in *our* file, since only
our file is validated (`runconfig.py:52-62`), not the merged one:

`runconfig.name`, `groups.pge_name_group.pge_name`,
`input_file_group.{reference_rslc_file, secondary_rslc_file}`,
`dynamic_ancillary_file_group.dem_file`,
`product_path_group.{product_path, scratch_path, sas_output_file}`,
`primary_executable.product_type`, `debug_level_group.debug_switch`, and —
whenever the optional `logging:` block is supplied at all — `logging.path`
(`schemas/insar.yaml:928`).

Two more are *de facto* required by code:

- `processing.input_subset.list_of_frequencies` —
  `runconfig.py:110-114` does `.keys()` on it unconditionally; omitting it is a
  `KeyError`, not a validation error. Frequencies in the defaults but absent from
  *our* file are deleted.
- `logging` as a whole — `insar.py:187` reads `cfg['logging']['path']`, and
  `defaults/insar.yaml` has no `logging` key at all. Omit the block entirely and
  you get a `KeyError` at the very end of config load.

`product_type` → stages, from the `'X' in out_paths` guards in `insar.py`:

| `product_type` | delivered | stages run |
|---|---|---|
| `RIFG` | RIFG | D1–D8, D14 |
| `RUNW` | RUNW (RIFG → scratch) | + D9, D10 |
| `GUNW` | GUNW (RIFG, RUNW → scratch) | + D11, D12, D13 |
| `RIFG_RUNW_GUNW` | all three, prefixed | same as GUNW |
| `ROFF` / `GOFF` / `ROFF_GOFF` | offsets only | D1–D3, D4/D7-as-offsets, D14 — **no interferogram at all** |

**Trap:** `offsets_product` (D-alt) has **no** `'ROFF' in out_paths` guard
(`insar.py:65-67`), unlike every neighbouring stage. Setting
`offsets_product.enabled: True` with `product_type: GUNW` validates cleanly at
config load and then raises `KeyError: 'ROFF'` at runtime. The config-load
interlock only disables `offsets_product` when `dense_offsets` is *also* on, so
`dense_offsets: False, offsets_product: True, product_type: GUNW` is a reachable
crash.

---

**E. GEOMETRY EXPORT** — env `isce3_env` (option 1) or `dolphin_env` (option 2).

| | |
|---|---|
| IN | one RSLC (or the GUNW), the DEM |
| OUT | `geometry/los_incidence_angle.tif`, `geometry/los_azimuth_angle.tif`, `geometry/geometry.json` |
| CMD (opt 1) | `python -m nisar.workflows.get_product_geometry <rslc.h5> --dem <dem.tif> --freq B --od geometry/ --out-inc-angle --out-line-of-sight` |
| CMD (opt 2) | `smallbaselineApp.py <case>.txt --dostep load_data` then `save_gdal.py inputs/geometryGeo.h5 -d incidenceAngle -o ...` |
| CONTRACT | Per-**track**, never per-pair. Angles in degrees, ISCE convention (incidence from vertical at the target; azimuth CCW from north, target→satellite). Written into `geometry.json`. |

Option 1 caveat, verified in the source: for an **L1** input,
`get_product_geometry.py` dispatches to `get_geolocation_grid`, which allocates
rasters at the **full radar grid** (`shape = [1, radar_grid.length,
radar_grid.width]`, line 451). On frequency A that is 23 GB *per layer*, and with
no `--out-*` flag it writes **all eleven layers**. Use it on frequency B
(2.88 GB/layer) and always pass explicit `--out-*` flags. `--spacing-x/--spacing-y`
only take effect for L2 inputs.

Option 2 is the geocoded route and is what the course uses: MintPy's
`prep_nisar` warps the DEM onto the GUNW grid and uses it as the *height*
coordinate for a `RegularGridInterpolator` over the 3-D
`GUNW/metadata/radarGrid` cube. That is exactly why
`mintpy.load.demFile` is mandatory for `processor = nisar` and cannot be `auto`.
Running `load_data` on a **single** GUNW purely to obtain
`inputs/geometryGeo.h5` is legitimate and cheap.

Note MintPy *derives* azimuth rather than reading it:
`az = degrees(arctan2(-losy, -losx))`.

---

**F. CORRECTION APPLY** — env `isce3_env`, net-new code (trivial raster algebra).

| | |
|---|---|
| IN | GUNW |
| OUT | `pairs/<date12>/corrected/unw_corrected.tif`, updated `pair.json` |
| CONTRACT | **The workflow never subtracts the correction screens.** They ship as separate layers and applying them is ours. Record what was applied in `pair.json.corrections_applied`. |

Layer paths in the GUNW:

```
/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/{HH,HV}/ionospherePhaseScreen
/science/LSAR/GUNW/metadata/radarGrid/wetTroposphericPhaseScreen
/science/LSAR/GUNW/metadata/radarGrid/hydrostaticTroposphericPhaseScreen
/science/LSAR/GUNW/metadata/radarGrid/slantRangeSolidEarthTidesPhase
/science/LSAR/GUNW/metadata/radarGrid/perpendicularBaseline
```

The `radarGrid` entries are 3-D `(height, y, x)` cubes and must be sliced through
the DEM before they are 2-D screens. The ionosphere screen is already a 2-D grid.

Sign convention, to be pinned to a test the first time we run it:
**φ = φ_secondary − φ_reference; positive φ = range increase = motion away from
the satellite = subsidence; ΔR = −(λ/4π)·φ.**

---

**G. MODEL-READY EXPORT** — env `isce3_env`, net-new (thin gdalwarp wrapper).

| | |
|---|---|
| IN | GUNW (or RUNW + geometry) |
| OUT | `model/<date12>/{unw,coh,inc,azi,mask}_ll.tif` + `model.json` |
| CONTRACT | **Contract C.** Five single-band rasters, EPSG:4326, identical grid, data in **band 1**. `model.json` carries `wavelength_m`, `sign_convention`, `los_vector_convention`, and the reference point. |

This is the stable seam. Any producer that satisfies contract C — the ISCE3 pair
here, a downloaded L2 GUNW, or a slice of a dolphin timeseries later — plugs into
stage H unchanged. The course's kite notebook already has an `isce3` branch that
reads band 1 and expects exactly `los_incidence_angle_ll.tif` /
`los_azimuth_angle_ll.tif`; the naming above exists so that cell runs unmodified.

---

**H. MODELING** — env: needs `kite` + `okada_wrapper` (not yet installed; see §7).

| | |
|---|---|
| IN | contract C |
| OUT | `model/<date12>/*.okinv`, inversion results |
| CONTRACT | `.okinv` is 7 columns: `x_km y_km los_disp_m ux uy uz point_id`. A `_ll.okinv` twin carries lon/lat. |

Three conventions collide here and all three must be written down:
ISCE (incidence + azimuth-from-N), kite (`theta = radians(90 − inc)`,
`phi = radians(90 + azi)`, CCW from **east**, origin **lower-left** hence
`np.flip(arr, 0)`), and okapy (`sign_convention = ±1` selecting range-change vs
ground-displacement). Also: `los_penalty_fault` subtracts `mean(data − model)`
before the squared misfit — skipping that zero-level nuisance parameter dominates
the penalty.

---

**I. GSLC → dolphin** and **J. GUNW → MintPy** — env `dolphin_env`.

Structurally present now, inert at N=2. Their contracts are in §5(ii). The
interfaces that must stay stable so an N-image stack drops in later without
rework:

1. **The geogrid.** If we ever generate GSLCs ourselves, `defaults/gslc.yaml`
   leaves `x_snap`/`y_snap` empty, and with both `None` the snapping block in
   `nisar/workflows/geogrid.py:282-300` is skipped entirely — the origin falls
   out of `isce3.product.bbox_to_geogrid` on *that scene's* footprint. Different
   dates then get different origins. **Pin `geocode.top_left` / `bottom_right`
   (or the snap values) in `stack.json` and pass the identical geogrid to every
   date.** dolphin's `_assert_images_same_size` checks x/y **size only** — not
   geotransform, not EPSG — so a shifted origin passes validation and silently
   produces garbage. This is the single biggest footgun in the whole design.
2. **Filenames.** ASF granule names are never renamed. For anything we derive,
   the **first 8-digit run in the basename is the acquisition date**, `%Y%m%d`.
   Dates in parent directories are invisible to `opera_utils.get_dates`, which
   only looks at `Path(f).name`. And **`compressed` is a reserved substring** —
   dolphin does `"compressed" in str(f).lower()` and parses those files with
   `dates[:3]` instead of `dates[:1]`.
3. **Pair naming.** Always `YYYYMMDD_YYYYMMDD` = reference_secondary. That single
   string is simultaneously MintPy's `date12`, dolphin's interferogram filename,
   and the course's ISCE3 pair-directory convention.
4. **Geometry is per-track.** It lives in `geometry/`, never under `pairs/`.
   Copying incidence angle into every pair directory is how a 2 → N refactor turns
   into a rewrite.
5. **The AOI window.** Cheap to choose, expensive to change. Fix it once,
   generously, and make sure it reaches stable ground for a reference point.

Dolphin specifics worth recording now: it needs `--subdataset`
(`/science/LSAR/GSLC/grids/frequencyA/HH`) and **it will not infer our
wavelength** — `_displacement.py` only auto-sets it for recognised OPERA
Sentinel-1 burst IDs. Without `--input-options.wavelength 0.241963` the
`timeseries/` outputs stay in **radians**. It requires no geometry layers at all
and emits none.

MintPy specifics: only two paths matter — `mintpy.load.unwFile` (a glob) and
`mintpy.load.demFile` (a real path). `corFile`, `connCompFile`, `incAngleFile`,
`azAngleFile`, `waterMaskFile` all stay `auto` because they live inside the GUNW.
Date pairs come from `referenceZeroDopplerStartTime` /
`secondaryZeroDopplerStartTime` in the HDF5, not the filename — so GUNW filenames
are far less load-bearing than GSLC filenames. And the loader takes a
**stack-wide common extent** (`max(wests), max(souths), min(easts), min(norths)`)
and ANDs a common mask across all inputs: adding one badly-overlapping GUNW
silently shrinks the entire stack.

---

## 3. Directory layout

```
case_studies/venezuela_t162_asc/
├── stack.json                        # identity file; everything reads it
│
├── L1_RSLC/                          # ASF granule names, NEVER renamed
│   ├── NISAR_L1_PR_RSLC_022_162_A_007_4005_DHDH_A_20260613T100656_..._001.h5
│   └── NISAR_L1_PR_RSLC_023_162_A_007_4005_DHDH_A_20260625T100655_..._002.h5
│
├── L1_RSLC_crop/                     # stage C output, only if we crop freq A
│   ├── ref_20260613_az0-30000_rg0-26000.h5
│   └── sec_20260625_az0-30000_rg0-26000.h5
│
├── aux/                              # per-track, never per-date
│   ├── dem/dem_t162_f007.tif         # sardem, WGS84 ellipsoidal, float32
│   ├── water/watermask_t162_f007.wbd
│   └── orbits/                       # external POE XMLs if they ever appear
│
├── cfg/                              # one runconfig per run, checked in
│   ├── insar_freqB_geomonly.yaml
│   ├── insar_freqB_full.yaml
│   └── insar_freqA_crop.yaml
│
├── scratch/                          # DELETABLE. Set scratch_path here explicitly.
│   ├── rdr2geo/  geo2rdr/  coarse_resample_slc/  fine_resample_slc/
│   ├── dense_offsets/  rubbersheet_offsets/  crossmul/  unwrap/  ionosphere/
│   └── RIFG.h5  RUNW.h5              # NOT deleted by intermediate_files_removal
│
├── pairs/
│   └── 20260613_20260625/            # YYYYMMDD_YYYYMMDD = ref_sec
│       ├── pair.json
│       ├── RIFG/  RUNW/  GUNW/
│       ├── logs/insar.log
│       └── corrected/unw_corrected.tif
│
├── geometry/                         # per-TRACK static layers
│   ├── los_incidence_angle.tif       los_incidence_angle_ll.tif
│   ├── los_azimuth_angle.tif         los_azimuth_angle_ll.tif
│   ├── height.tif  slant_range.tif  layover_shadow_mask.tif
│   └── geometry.json
│
├── model/
│   └── 20260613_20260625/
│       ├── unw_ll.tif  coh_ll.tif  inc_ll.tif  azi_ll.tif  mask_ll.tif
│       ├── venezuela_asc.okinv  venezuela_asc_ll.okinv
│       └── model.json
│
├── L2_GSLC/                          # inert until we produce or download GSLCs
│   └── subset.json                   # the row/col window, written ONCE
├── ts_dolphin/                       # = dolphin --work-directory
└── ts_mintpy/
    ├── venezuela_t162_asc.txt        # template name = parent dir name
    └── inputs/{ifgramStack,geometryGeo,ionStack,tropoStack,setStack}.h5
```

A future descending stack is a **sibling root**
(`case_studies/venezuela_t???_dsc/`), not a subdirectory. Decomposition reads
`model.json` from both.

`.gitignore`: `case_studies/**/*.h5`, `case_studies/**/scratch/`,
`case_studies/**/aux/`. The JSON sidecars, the runconfigs, and the logs are small
and **should be committed** — they are the record of what was run.

---

## 4. Coregistration: what we are actually doing

The framing that motivated this project — "data-driven coregistration, no single
reference file, using absolute truths from the DEM and from the signal after
removing iono/tropo corrections" — describes something real that the course does
teach. But it inverts two of its three properties, and the inversion matters
because it changes which failure modes we should be watching for.

### 4.1 Three corrections to the framing

**(1) The geocode-to-absolute-grid model is explicitly *model*-driven, not
data-driven.** The course says so in as many words
(`2.1_ISCE3_TOPS_Processing/S1_GSLC_burst.ipynb`, md cell 29):

> This workflow coregister each burst SLC into a pre-defined geographic grid
> **using geometry with model-driven refinements**. This is different from the
> traditional workflow, which first coregister the secondary SLC into the
> reference SLC using geometry with **data-driven refinements such as
> cross-correlation or enhanced spectral diversity**.

The one genuinely data-driven hook in ISCE3's geocoded path is
`gslc.yaml`'s `reference_gslc` field, whose implementation
(`geocode_corrections.py:287-297`) is a stub that logs
*"Data-driven timing correction for GSLC is not implemented."*

**(2) There is still a reference — it just isn't an image.** The reference is
DEM + precise orbit + a static frame/burst geogrid definition. "No reference
scene" is right; "no reference file" is not.

**(3) The iono/tropo/SET terms are removed as geometric *timing shifts*, not
from the signal.** COMPASS passes them as `az_time_correction` /
`srange_correction` into `isce3.geocode.geocode_slc`, which documents them as
*"geo2rdr azimuth additive correction, in seconds"* and *"geo2rdr slant range
additive correction, in meters"*. They perturb **where geo2rdr lands in the radar
grid**. COMPASS does not pass `flatten_with_corrected_srange` (default `False`),
so topographic flattening uses the *uncorrected* slant range and **the
atmospheric phase survives into the CSLC**. The course draws the line explicitly
(md cell 33): geo-registration vs phase compensation, the latter *"preserving
deformation, atmosphere, and other geophysical signals."* Removing atmospheric
*phase* is a different pipeline stage entirely — that is stage F above, and it is
what the GUNW correction layers are for.

### 4.2 What `nisar.workflows.insar` actually does — the classic model

`insar.py:43-101` runs
`rdr2geo → geo2rdr → coarse_resample → dense_offsets → rubbersheet →
fine_resample → crossmul`, geocoding only at the very end. **For an RSLC pair
NISAR uses geometry-first, radar-coordinate, ampcor-refined coregistration
against a reference scene.** The geocode-to-absolute-grid model is confined to
the CSLC/GSLC (L2) path. Both ship in the same environment; they are not
successive versions of one pipeline.

**Tier 1 — geometric, from orbit + DEM only.**

`rdr2geo` runs on the **reference** RSLC: for each reference pixel it intersects
the zero-Doppler/range surface with the DEM (`threshold 1e-7`, `numiter 25`,
`extraiter 10`), writing `x.rdr`, `y.rdr`, `z.rdr` as **Float64** plus a
`topo.vrt`. Grid Doppler is hardcoded `isce3.core.LUT2d()` — correct, because
NISAR RSLCs are zero-Doppler. Orbit comes from the RSLC unless
`reference_orbit_file` is supplied, in which case the external XML is cropped to
the RSLC span.

`geo2rdr` runs on the **secondary** with the secondary orbit, takes the
reference's `topo.vrt`, and inverts each ground point back to the secondary's
azimuth-time / slant-range (`threshold 1e-8`, `maxiter 25`). The difference is
the offset: `range.off`, `azimuth.off`, Float64, one value per reference pixel.

**That is the coregistration. No image data is used at all.** Note that
`geo2rdr` in the InSAR path applies **no** correction LUTs — its runconfig block
contains only `threshold`, `maxiter`, `lines_per_block`.

`coarse_resample` (`resample_slc_v2`, not the legacy `resample_slc` module —
`insar.py:10,58,85`) memory-maps those offsets and calls
`isce3.image.v2.resample_slc.resample_slc_blocks` with the secondary's radar grid
and its **Doppler centroid**, so the interpolation is Doppler-aware. Output
`scratch/coarse_resample_slc/freqX/{pol}/coregistered_secondary.slc`, ENVI
complex64, on the reference grid.

**Tier 2 — data-driven refinement, on by default.**

`dense_offsets` is amplitude cross-correlation. Crucially it correlates the
**reference RSLC against the coarse-resampled secondary**, so it measures only
the *residual* after geometry — which is why 64×64 templates with ±20-pixel
search suffice. CPU path is `isce3.matchtemplate.PyCPUAmpcor` (confirmed present;
`isce3.cuda.matchtemplate` is not, we have no GPU). Defaults: `window 64×64`,
`half_search 20`, `skip 32` (so the field is decimated ~32×), frequency-domain
correlation, `slc_oversampling_factor 2`, correlation surface oversampled ×64.

`rubbersheet` culls outliers (`median_filter`, threshold 0.75), fills holes
(`fill_smoothed`), smooths (5×5 boxcar), writes the physical-unit offsets into
RIFG `pixelOffsets`, and then — the step that matters —
`gdal.Translate`s the culled offsets back up to full reference-grid size and
**adds them to the geometric offsets** (`rubbersheet.py:224-241` for the polyfit
variant, `:405-422` for the interpolation variant, which is the default since
`polyfitting.enabled: false`). Result:
`scratch/rubbersheet_offsets/freqX/{pol}/{range,azimuth}.off` = geometric +
refined, a drop-in replacement.

`fine_resample` is the same resampler pointed at those summed offsets. It picks
`HH` if present else `VV` — **one co-pol offset field is applied to all
polarizations**.

`crossmul` then multiplies reference × conj(fine-resampled secondary) at 11×11
looks. Note that even with fine resampling, **flattening still uses the
*geometric* range offsets** (`crossmul.py:104`, reading
`geo2rdr/freqX/range.off`), plus a starting-range shift between the grids.

**Config-load interlocks** (hard `ValueError`s, in `insar_runconfig.py:32-49`):

```
(dense_offsets OR offsets_product) → rubbersheet → fine_resample
```

To run geometry-only you must turn off all four explicitly:

```yaml
dense_offsets:   {enabled: False}
offsets_product: {enabled: False}
rubbersheet:     {enabled: False}
fine_resample:   {enabled: False}
```

Related: if both `dense_offsets` and `offsets_product` are on, `dense_offsets`
wins and `offsets_product` is silently disabled with a warning. `rdr2geo`'s
`write_x/write_y/write_z` are **force-set back to True** with a warning if
disabled, so they are not toggles. And the co-pol guard fires only when
`(offsets_product OR dense_offsets) AND process_single_co_pol_offset` — in a
geometry-only run it never fires, so a cross-pol-only frequency would slip
through.

### 4.3 Which corrections are applied where

Two categories that are easy to conflate: **geolocation LUTs applied during
geocoding** versus **phase screens written as separate GUNW layers**.

| Correction | Runconfig key | Default | Aux input | Stage | Affects |
|---|---|---|---|---|---|
| Solid Earth tides (geolocation) | `processing.correction_luts.solid_earth_tides_enabled` | **True** | none | during geocode | **slant-range geolocation** |
| Solid Earth tides (phase cube) | *none — unconditional* | always with GUNW | none | D13 | GUNW aux layer |
| TEC ionosphere (geolocation) | *implicit: `tec_file != null`* | off | TEC JSON | during geocode | **az + slant-range geolocation** |
| Split-spectrum ionosphere | `processing.ionosphere_phase_correction.enabled` | **False** | none | D10, pre-geocode | RUNW/GUNW aux **phase** layer |
| Troposphere | `processing.troposphere_delay.enabled` | **False** | 2 weather files, ≤24 h | D12, post-geocode | GUNW aux **phase** layer |
| Static troposphere | — | — | — | — | **not implemented** in the InSAR workflow |
| Bistatic delay | — | — | — | — | **not present** — TOPS-specific, COMPASS only |
| Azimuth FM-rate mismatch | — | — | — | — | **not present** — TOPS-specific, COMPASS only |

Points worth internalising:

- **The last two rows are the answer to "what about bistatic delay and FM-rate
  mismatch?"** They are Sentinel-1 TOPS focusing artefacts. NISAR RSLCs are
  focused by time-domain backprojection with no TOPS steering, so those terms do
  not exist here. In COMPASS they are default-on but fully optional — one flag,
  `correction_luts.enabled`, produces or zeroes all seven LUTs together.
- **Solid Earth tides appear twice under different keys and do different things.**
  The geolocation LUT (default on, ~5 km decimated radar grid) shifts where
  geo2rdr lands. The phase cube (`solid_earth_tides.py:391-396`, converted with
  `*= -4π/λ`) is written to `radarGrid/slantRangeSolidEarthTidesPhase` in radians
  and is **not** subtracted from `unwrappedPhase`.
- **The ionosphere screen is not subtracted either.** It is written to RUNW
  `ionospherePhaseScreen` + `...Uncertainty` and geocoded into GUNW. Applying it
  is stage F.
- The `radarGrid` cubes are two-height only for SET
  (`solid_earth_tides.py:327-328`, `indices = [0, -1]`, interpolated back to the
  full heights list at `:422-427`). For **baseline** it is mode-dependent:
  `top_bottom` (the default) uses only bottom/top; `3D_full` computes at every
  height (`baseline.py:559-565`).
- The NISAR **GSLC** path, for contrast, is much thinner than COMPASS — and
  thinner still with stock defaults. `defaults/gslc.yaml:32` leaves `tec_file`
  empty, so `correct_tec` is False and the
  `not correct_tec and correct_set` branch populates only the slant-range LUT.
  **With stock defaults the GSLC path applies solid Earth tides in slant range
  only and zero azimuth correction.**

### 4.4 Residual failure modes

Why geometry-to-a-fixed-grid works at all is not that the DEM is "absolute
truth" — it isn't; Copernicus GLO-30 has metre-level errors. It works because
those errors are **common-mode and cancel differentially**. A DEM height error
`dh` displaces a geocoded pixel horizontally by `dh·cot θ`, but both dates use
the same DEM at nearly the same incidence, so the differential misregistration is
`Δ = dh·B⊥ / (R·sin²θ)`. For this pair — `dh = 10 m`, `B⊥ = 31 m`,
`R = 964.5 km`, `θ = 41.4°` — that is **≈ 0.7 mm**, order 10⁻⁴ pixel. The DEM's
job is to be identical and stationary, not accurate.

*(That derivation is ours, not quoted from the code, but it is what makes the
whole design viable.)*

What actually threatens us, ranked for this pair:

1. **Ionosphere.** 10° N, L-band, equatorial anomaly. This is the dominant error
   term and is not optional. It affects phase (a large low-frequency screen) and,
   through TEC, azimuth geolocation. We have frequency B, so the split-spectrum
   route is open — but the estimate will be coarse (5 MHz side band) and the
   default `dispersive_filter` matters.
2. **MOE orbits.** `orbitType = MOE`, not `POE`. At B⊥ ≈ 31 m the orbital
   contribution to phase is small, but MOE residuals can still put a
   long-wavelength ramp into the GUNW. If POE XMLs ever appear on ASF, wire them
   into `orbit_files.{reference,secondary}_orbit_file`.
3. **Temporal decorrelation.** 12-day L-band over tropical vegetation. L-band
   penetrates and is far more forgiving than C-band, but the forested interior
   will still be soft. This is what determines whether the phase path or the
   offset path is the right one — and we will not know until step 6 of §6.
4. **Water.** 38% of the frame. Handled by the water mask.
5. **Silent bias.** Nothing in the geometry-only path *measures* the residual
   misregistration — that is exactly what `dense_offsets` exists for. Turning it
   off (which §6 does, for disk reasons) trades a measured residual for an
   unmeasured one. Mitigation: run `dense_offsets` **once** on a cropped window
   purely as a diagnostic, read the mean/median residual offsets, and confirm
   they are sub-pixel before trusting the geometry-only full run. That is
   step 7 in §6.
6. **DEM error where the surface actually moved** — glaciers, dune fields, large
   subsidence, forest growth — is not common-mode. Not a concern on this frame.
7. **Layover and shadow.** Steep terrain in the Coastal Range. The workflow can
   emit a layover/shadow mask; use it.

---

## 5. Workflow catalogue

### (i) Runs today, with these two granules

| # | Workflow | Purpose | Course source | Entry point | In → Out | Feasible here? |
|---|---|---|---|---|---|---|
| 1 | **RSLC inspection** | HDF5 tree, granule parse, spectra, valid-sample decode, orbit plots | 3.3 `NISAR_RSLC_Tutorial.ipynb` | `h5py` script | RSLC → JSON + PNGs | **Yes.** Minutes, ~0 disk. Already partly done for §1. |
| 2 | **Baseline + coherence budget** | B⊥, h_amb, critical baseline, predicted γ | 1.3 | `isce3.geometry.geo2rdr` script | RSLC ×2 → table | **Yes.** Done — see §1.5. |
| 3 | **DEM + water mask staging** | ellipsoidal-height DEM over the AOI | S01, 2.1, 3.3 | `sardem` | bbox → GTiff | **Yes**, ~150 MB, minutes. |
| 4 | **Geometry-only RIFG, freq B** | wrapped ifg + coherence, cheapest true product | S08 chain, run by ISCE3 | `python -m nisar.workflows.insar` | RSLC ×2 + DEM → RIFG | **Yes.** ~17 GB scratch. **Start here.** |
| 5 | **Full RUNW/GUNW, freq B** | unwrapped, geocoded, with SET + iono | 2.1 back half + 3.3 GUNW | same | → GUNW | **Yes.** ~26 GB scratch. |
| 6 | **Dense-offset diagnostic** | measure residual misregistration after geometry | 2.1 / S08 | `python -m nisar.workflows.dense_offsets` | cropped RSLC ×2 → offset field | **Yes** on a crop. CPU ampcor; see risk 3. |
| 7 | **Split-spectrum ionosphere** | dispersive/non-dispersive separation | *no course code exists* — ISCE3 native | `ionosphere_phase_correction.enabled: True`, `spectral_diversity: main_side_band` | RSLC ×2 (A+B) → screen | **Yes**, freq B confirmed present. Adds a mini InSAR chain per sub-band — budget 2× time and disk. |
| 8 | **Troposphere from a weather model** | wet + hydrostatic screens | 3.1/3.4 concept; ISCE3 native (pyAPS/RAiDER) | `troposphere_delay.enabled: True` | GUNW + 2 GRIB/NetCDF → screens | **Conditional.** Needs ERA5 files within 24 h of each acquisition, `worker.internet_access: True`, and a CDS API key. Not yet set up. |
| 9 | **Correction application + LOS map** | φ − iono − tropo − SET → ΔR metres | 3.3 GUNW, 3.4 | net-new (stage F) | GUNW → GeoTIFF | **Yes.** Trivial once GUNW exists. |
| 10 | **Mask decode + QC** | bit-field decode, conncomp stats, coherence histogram, rewrap-for-display | 3.3 GUNW `parse_mask` | net-new | GUNW → plots | **Yes.** |
| 11 | **Fringe-count sanity check** | manual far-field-inward verification before trusting the unwrap | 1.4 (no code — it is a ritual) | eyes | unwrapped map | **Yes**, and mandatory. **12.10 cm per fringe** at λ = 0.241963 m. |
| 12 | **Quadtree → `.okinv`** | millions of pixels → a few hundred points with LOS vectors | 2.2 kite workflow | `kite` | contract C → `.okinv` | **Yes**, once `kite` is installed. |
| 13 | **Okada inversion** | rectangular dislocation, Powell + MC restarts | 2.2 `okapy.py` | `okapy` + `okada_wrapper` | `.okinv` → fault params | **Yes**, once installed. Needs an actual event in the frame. |
| 14 | **Mogi inversion** | point pressure source, volcanic | 1.2 | pure numpy | contract C → source params | **Yes.** No volcano in this frame, so academic here. |
| 15 | **Pixel offsets (ROFF/GOFF)** | range + azimuth offsets; survives decorrelation; gives azimuth | 3.3 GUNW `pixelOffsets`; S06 concept | `product_type: ROFF` or `GOFF` | RSLC ×2 → offsets | **Yes**, but see the `out_paths` trap in §2.3. Note ROFF/GOFF runs form **no interferogram at all**. |
| 16 | **autoRIFT offset tracking** | independent NCC template matching on amplitude | S06 | `autoRIFT` | amplitude pair → velocity field | **Yes**, but `autoRIFT` is not installed. The fallback if #5 shows coherence collapse. |
| 17 | **Geometry layer export** | incidence + azimuth rasters for modeling | 2.1 / 3.3 | `get_product_geometry.py` or MintPy `load_data` | RSLC/GUNW + DEM → GeoTIFF | **Yes** on freq B. On freq A the L1 path allocates full-radar-grid rasters — 23 GB each. |
| 18 | **Full-resolution freq A GUNW** | the actual 40 MHz science product | — | `nisar.workflows.insar` | → GUNW | **Not on this disk without cropping.** See §7 risk 1. |

### (ii) Needs a stack (N ≥ 3, realistically N ≥ 8)

Park these. The architecture in §2 keeps their inputs stable so they drop in
later, but running them on 2 acquisitions produces numbers that look fine and
mean nothing.

| Workflow | Why it needs N > 2 | Source | Minimum N |
|---|---|---|---|
| Phase linking (EVD/EMI/MLE) | estimator is built on an N×N sample covariance; at N=2 it *is* the interferogram | 4.3, 5.3 | ~8, ideally 15+ |
| PS selection by amplitude dispersion | σ/μ over N samples; 5.3 already crushes the threshold to 0.05 at N=8 | 5.3 | ~15 |
| Temporal coherence as a quality metric | it is phase-linking misfit; a rank-1 fit to a 2×2 matrix is exact | 4.3, 5.3 | ~10 |
| Closure phase / triplet consistency | needs triangles | 4.3, 5.1 | **3** |
| Unwrap-error correction by phase closure | uses network redundancy; MintPy errors "No triangles found" | 5.1 | **3** |
| SBAS network inversion | nothing to invert with one interferogram | 4.4, 5.1 | 4+ |
| Network modification, reference-date selection, residual-RMS ranking | all defined over a network | 5.1 | 5+ |
| Velocity / seasonal / time-function fitting | 2 points define a line with zero DOF | 4.4, 5.1 | 8+ |
| DEM-error (topographic residual) estimation | regresses phase against *temporal variation* of B⊥ | 4.4, 5.1 | 10+, and pointless at B⊥ ≈ 31 m |
| Empirical phase–elevation tropospheric correction | needs many epochs to separate signal from delay | 4.4 | 10+ |
| DISP-S1 rebasing | definitionally multi-ministack, and Sentinel-1 only | 5.2 | n/a |
| Offset *stacks* for velocity dynamics | a single offset field is fine (#15); stacking is not | S06 | 5+ |

**The dolphin and MintPy entry points below are correct and will run today — they
just will not tell you anything.** Recording them now so the N-image drop-in is a
config change, not a design change:

```bash
# dolphin, once L2_GSLC/ has ≥8 dates on a pinned geogrid
conda run -n dolphin_env dolphin config \
  --slc-files "$CASE/L2_GSLC/"*.h5 \
  --subdataset /science/LSAR/GSLC/grids/frequencyA/HH \
  --input-options.wavelength 0.241963 \
  --sy 4 --sx 4 \
  --ps-options.amp-dispersion-threshold 0.25 \
  --phase-linking.half-window.y 7 --phase-linking.half-window.x 7 \
  --unwrap-options.unwrap-method WHIRLWIND \
  --work-directory "$CASE/ts_dolphin" \
  --outfile "$CASE/ts_dolphin/dolphin_config.yaml"
conda run -n dolphin_env dolphin run "$CASE/ts_dolphin/dolphin_config.yaml"
```

Note `--phase-linking.nearest-n-coherence` appears in the course notebook but
**does not exist** in our conda-forge dolphin 0.42.5 — that notebook installs a
git branch. `--phase-linking.write-closure-phase`, `--phase-linking.write-crlb`
and `--unwrap-options.unwrap-method WHIRLWIND` do exist.

```cfg
# ts_mintpy/venezuela_t162_asc.txt — once pairs/ has ≥3 GUNWs
mintpy.load.processor      = nisar
mintpy.load.unwFile        = ../pairs/*/GUNW/NISAR*.h5
mintpy.load.corFile        = auto
mintpy.load.connCompFile   = auto
mintpy.load.demFile        = ../aux/dem/dem_t162_f007.tif
mintpy.load.incAngleFile   = auto
mintpy.load.azAngleFile    = auto
mintpy.load.waterMaskFile  = auto

mintpy.reference.lalo           = 10.20, -68.60
mintpy.ionosphericDelay.method  = split_spectrum
mintpy.troposphericDelay.method = opera
mintpy.deramp                   = no
mintpy.topographicResidual      = no
```

`demFile` is **mandatory** for `processor = nisar` and cannot be `auto` —
`load_data.py:635-650` raises `ValueError` otherwise, because the geometry layers
are a 3-D metadata cube that needs the DEM as its height coordinate.

### (iii) Needs a second geometry (a descending track)

| Workflow | Why | Source | What it needs |
|---|---|---|---|
| **E/U decomposition** | one LOS observation, three unknowns. Two geometries give a 2×2 solvable system for East and Up (North is unresolvable and is dropped). | 4.2 | **One ascending + one descending pair over the same ground and the same event**, plus a **common reference point on stable ground in both.** |

This is the single highest-value acquisition we could add — a stronger product
than a 20-date stack on track 162 alone. The design matrix, from
`4.2/decompose_mintpy_velocities.ipynb`:

```python
sign_convention = -1   # +1 range change, -1 ground displacement (MintPy default)
los = np.array([ sin(radians(azi))*sin(radians(inc)),
                -cos(radians(azi))*sin(radians(inc)),
                -cos(radians(inc))]) * sign_convention
A = np.array([[dsc_los[0], dsc_los[2]],
              [asc_los[0], asc_los[2]]])          # East and Up columns only
W = np.diag([1/dsc_err**2, 1/asc_err**2])
m = inv(A.T @ W @ A) @ (A.T @ W @ d)
```

**Open question:** does a descending NISAR frame covering the same ground
actually exist and is it downloadable? See §7 risk 8.

---

## 6. Execution plan

Ordered so that each step produces something verifiable, and so that everything
cheap happens before the 26 GB compute. Every step has a checkpoint that must
pass before moving on.

```bash
export CASE=/home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc
export ISCE3=/home/sharath/Desktop/work/isce3
```

---

### Step 0 — rename, and reorganise into the layout

*Cost: seconds. Disk: 0 (same filesystem).*

```bash
cd $ISCE3/case_studies
mv venezuela_descending venezuela_t162_asc
cd venezuela_t162_asc
mkdir -p L1_RSLC aux/dem aux/water aux/orbits cfg scratch pairs geometry model logs
mv NISAR_L1_PR_RSLC_*.h5 L1_RSLC/
```

**Checkpoint:** `ls -la $CASE/L1_RSLC/` shows two `.h5` files totalling 52.6 GB
and `df -h /` is unchanged.

---

### Step 1 — ingest and write `stack.json`

*Cost: ~1 min. Disk: kB.*

Write `asc/tools/ingest_rslc.py` to emit the `stack.json` in §2.3 by reading
`identification`, `swaths/frequency{A,B}`, and `metadata/orbit`. It must assert
that both granules agree on track, frame, direction, bandwidth mode and look side.

**Checkpoint:** `stack.json` exists and contains
`"wavelength_m": 0.241963`, `"direction": "A"`, `"look_side": "Left"`,
`"freq_b": {"present": true, ...}`. If any of those is wrong, stop — every
downstream number depends on them.

---

### Step 2 — stage the DEM and the water mask

*Cost: 10–30 min (network-bound). Disk: ~150 MB.*

The RSLCs were focused against **NISAR DEM v1.2, derived from Copernicus
GLO-30 (COP-DEM_GLO-30-DGED/2023_1), referenced to the WGS84 ellipsoid**
(`processingInformation/inputs/demSource`). Matching that exactly avoids a
systematic geolocation bias.

```bash
conda run -n isce3_env sardem \
  --bbox -69.75 9.40 -66.70 12.35 \
  --data-source NISAR \
  --output-format GTiff --output-type float32 \
  -o $CASE/aux/dem/dem_t162_f007.tif
```

`sardem --data-source NISAR` pulls
`https://nisar.asf.earthdatacloud.nasa.gov/NISAR/DEM/v1.2/EPSG4326/EPSG4326.vrt`
over public Earthdata HTTPS and correctly skips geoid conversion. Fallback if
Earthdata auth to that endpoint fails — plain Copernicus, and **do not pass
`--keep-egm`**; the default EGM→WGS84 conversion is what ISCE3 needs:

```bash
conda run -n isce3_env sardem \
  --bbox -69.75 9.40 -66.70 12.35 --data-source COP \
  --output-format GTiff --output-type float32 \
  -o $CASE/aux/dem/dem_t162_f007_cop.tif
```

Water mask:

```bash
conda run -n isce3_env sardem \
  --bbox -69.75 9.40 -66.70 12.35 --data-source NASA_WATER \
  -o $CASE/aux/water/watermask_t162_f007.wbd
```

**Do not use `nisar/workflows/stage_dem.py`** — it reads
`/vsis3/nisar-dem/...`, a JPL-internal S3 bucket requiring AWS credentials, and
its own source notes that internal buckets are not reachable over plain requests.

**Checkpoint:**

```bash
conda run -n isce3_env gdalinfo -stats $CASE/aux/dem/dem_t162_f007.tif | \
  grep -E "Size is|Pixel Size|EPSG|Minimum|Maximum"
```

Expect **10980 × 10620**, pixel size ≈ 0.000277778°, EPSG:4326, and a height
range with sea-surface values near **−20 to −32 m** (regional geoid undulation is
≈ −40 m, so ellipsoidal sea surface is negative) and a maximum around
**1100–1200 m**. If the ocean reads ≈ 0 you have a geoid-referenced DEM and the
geolocation will be systematically wrong.

---

### Step 3 — hand-write the frequency-B runconfig

*Cost: minutes. Disk: kB.*

There is no `dumpconfig` subcommand for InSAR — verified:

```
$ python -m nisar.workflows.dumpconfig --help
usage: nisar.workflows.dumpconfig [-h] {gslc,gcov} ...
```

So this is written by hand. `cfg/insar_freqB_geomonly.yaml`:

```yaml
runconfig:
  name: venezuela_t162_asc_freqB_geomonly

  groups:
    pge_name_group:
      pge_name: INSAR_L_PGE

    input_file_group:
      reference_rslc_file: /home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc/L1_RSLC/NISAR_L1_PR_RSLC_022_162_A_007_4005_DHDH_A_20260613T100656_20260613T100731_P05023_N_F_J_001.h5
      secondary_rslc_file: /home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc/L1_RSLC/NISAR_L1_PR_RSLC_023_162_A_007_4005_DHDH_A_20260625T100655_20260625T100730_P05023_N_F_J_002.h5

    dynamic_ancillary_file_group:
      dem_file: /home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc/aux/dem/dem_t162_f007.tif
      water_mask_file: /home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc/aux/water/watermask_t162_f007.wbd

    product_path_group:
      product_path: /home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc/pairs/20260613_20260625
      scratch_path: /home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc/scratch
      sas_output_file: /home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc/pairs/20260613_20260625/RIFG/RIFG_20260613_20260625_freqB.h5

    primary_executable:
      product_type: RIFG

    debug_level_group:
      debug_switch: false

    worker:
      gpu_enabled: False
      internet_access: False
      intermediate_files_removal_enabled: False

    processing:
      input_subset:
        list_of_frequencies:
          B: [HH]

      rdr2geo:
        lines_per_block: 256
      geo2rdr:
        lines_per_block: 256

      # geometry-only: all four must be off together (insar_runconfig.py:32-49)
      dense_offsets:   {enabled: False}
      offsets_product: {enabled: False}
      rubbersheet:     {enabled: False}
      fine_resample:   {enabled: False}

      coarse_resample:
        lines_per_tile: 256
        columns_per_tile: 4096

      crossmul:
        range_looks: 3
        azimuth_looks: 11
        flatten: True
        lines_per_block: 256

      baseline:
        mode: top_bottom

    logging:
      path: /home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc/logs/insar_freqB_geomonly.log
      write_mode: w
```

Note the looks: on frequency B the ground-range spacing is 37.8 m and azimuth is
4.456 m, so `range_looks: 3, azimuth_looks: 11` gives ~113 m × 49 m — closer to
square than the default 11×11 would be. Revisit after seeing the output.

**Checkpoint:** config load succeeds without running anything —

```bash
cd $CASE && conda run -n isce3_env python -c "
from nisar.workflows.insar_runconfig import InsarRunConfig
from nisar.workflows.yaml_argparse import YamlArgparse
import sys; sys.argv=['x','cfg/insar_freqB_geomonly.yaml']
c=InsarRunConfig(YamlArgparse().parse()); c.geocode_common_arg_load(); c.yaml_check()
print('OK', list(c.cfg['processing']['input_subset']['list_of_frequencies']))
"
```

Expect `OK ['B']`. Any `ValueError` here is a config bug, caught for free.

---

### Step 4 — dry-run the geometry stages alone

*Cost: **UNVERIFIED**, estimate 20–60 min. Disk: ~14.4 GB.*

Run just the two geometry stages, standalone, before committing to the chain:

```bash
cd $CASE
conda run -n isce3_env python -m nisar.workflows.rdr2geo cfg/insar_freqB_geomonly.yaml
du -sh scratch/rdr2geo
conda run -n isce3_env python -m nisar.workflows.geo2rdr cfg/insar_freqB_geomonly.yaml
du -sh scratch/geo2rdr
```

Expected sizes: `rdr2geo` = 3 × 2.88 GB = **8.64 GB**; `geo2rdr` = 2 × 2.88 GB =
**5.76 GB**.

**Checkpoint:** inspect the offsets. They should be small and smooth.

```bash
conda run -n isce3_env python -c "
import numpy as np
for n in ['range','azimuth']:
    a=np.fromfile(f'scratch/geo2rdr/freqB/{n}.off',dtype=np.float64).reshape(54720,6582)
    a=a[::37,::5]
    print(n, 'median %.4f  p5 %.4f  p95 %.4f  nan %d' % (
        np.nanmedian(a), np.nanpercentile(a,5), np.nanpercentile(a,95), np.isnan(a).sum()))
"
```

Sanity: with a 12-day repeat, MOE orbits and |B| ≈ 45 m, the **range** offset
should be a smooth field of order a few tenths of a pixel to a few pixels and the
**azimuth** offset smaller still. A median of tens of pixels, a wildly
non-smooth field, or all-NaN means the DEM, the look side, or the orbit is wrong
— stop and debug there, not later.

---

### Step 5 — full frequency-B RIFG

*Cost: **UNVERIFIED**, estimate 1–3 h. Disk peak ~17.3 GB scratch.*

```bash
cd $CASE
conda run -n isce3_env python -m nisar.workflows.insar cfg/insar_freqB_geomonly.yaml
```

(It will re-enter at `coarse_resample` using the scratch from step 4, provided
`logs/insar_freqB_geomonly.log` is intact. Add `--restart` to force everything.)

**Checkpoint:** open the RIFG and look at the coherence.

```bash
conda run -n isce3_env python -c "
import h5py,numpy as np
p='pairs/20260613_20260625/RIFG/RIFG_20260613_20260625_freqB.h5'
h=h5py.File(p,'r')
g=h['/science/LSAR/RIFG/swaths/frequencyB/interferogram/HH']
print(list(g))
c=g['coherenceMagnitude'][::7,::7]
print('shape',g['coherenceMagnitude'].shape)
print('coh: median %.3f  frac>0.3 %.3f  frac>0.5 %.3f' % (
      np.nanmedian(c), np.nanmean(c>0.3), np.nanmean(c>0.5)))
"
```

**This is the decision point for the whole project.** Interpretation:

- median coherence over land **> 0.4** → the phase path is viable; continue to
  step 6 and plan the freq A crop.
- median **0.2–0.4** → marginal. Continue, but expect unwrapping trouble and
  budget the offset path (#15/#16) as a parallel product.
- median **< 0.2 over land** → 12-day L-band has decorrelated over this terrain.
  Pivot to pixel offsets (`product_type: GOFF`) and autoRIFT, and do not spend
  disk on freq A phase.

The ~38% ocean will read near zero regardless — mask it before computing these
statistics for real.

---

### Step 6 — frequency-B GUNW with ionosphere

*Cost: **UNVERIFIED**, estimate 3–8 h (the ionosphere stage re-runs a reduced
InSAR chain per sub-band pair). Disk peak ~35 GB.*

Copy the config to `cfg/insar_freqB_full.yaml` and change:

```yaml
    primary_executable:
      product_type: GUNW
    product_path_group:
      sas_output_file: .../pairs/20260613_20260625/GUNW/GUNW_20260613_20260625_freqB.h5
    processing:
      input_subset:
        list_of_frequencies:
          A: [HH]          # required: main_side_band needs BOTH bands
          B: [HH]
      ionosphere_phase_correction:
        enabled: True
        spectral_diversity: main_side_band
        lines_per_block: 256
      phase_unwrap:
        algorithm: snaphu
        snaphu:
          cost_mode: smooth
          initialization_method: mcf
          ntiles: [4, 4]
          nproc: 8
      geocode:
        lines_per_block: 256
        output_posting:
          B: {x_posting: 100, y_posting: 100}
```

**WARNING: adding frequency A here reintroduces the full 2.881 Gpx grid** for
`rdr2geo`/`geo2rdr`/`coarse_resample` on band A. That is the 138 GB problem from
§7 risk 1 and it will **not fit**. Two ways out, and we must pick one before
running:

- **(a)** Use `spectral_diversity: main_diff_low_high_subband` (the shipped
  default), which splits frequency **A's own** band and requires B to be absent
  from `ionosphere_phase_correction.list_of_frequencies`. Still needs freq A in
  `input_subset` — same disk problem.
- **(b)** Do the ionosphere run on the **cropped** pair from step 8, where both
  bands fit.

So: **run step 6 without ionosphere first** (`enabled: False`), get a clean
GUNW, then come back for ionosphere on the crop.

**Checkpoint:** the GUNW exists and carries the expected layers.

```bash
conda run -n isce3_env python -c "
import h5py
h=h5py.File('pairs/20260613_20260625/GUNW/GUNW_20260613_20260625_freqB.h5','r')
h.visit(lambda n: print(n) if any(k in n for k in
  ['unwrappedPhase','coherenceMagnitude','connectedComponents','mask',
   'SolidEarthTides','TroposphericPhaseScreen','ionospherePhaseScreen',
   'perpendicularBaseline']) else None)
"
```

Expect `slantRangeSolidEarthTidesPhase` and `perpendicularBaseline` present
unconditionally, `ionospherePhaseScreen` only if step 6 was run with it enabled,
and **no** tropospheric screens (that stage is off).

Then **step 6b, the manual QC that no script replaces**: plot the unwrapped
phase, start in the far field where deformation is zero, count fringes inward at
**12.10 cm per fringe**, never trace through an incoherent zone, and treat
regions either side of any discontinuity as independent zero-anchored profiles.
If the fringe count implies a displacement that is physically implausible for
this scene, the unwrap is wrong regardless of what the connected components say.

---

### Step 7 — dense-offset residual diagnostic

*Cost: **UNVERIFIED** — this is the no-GPU risk (§7 risk 3). Run it on frequency
B, where the grid is 8× smaller.*

Copy to `cfg/insar_freqB_dense.yaml`, set `dense_offsets.enabled: True` (leaving
`rubbersheet`/`fine_resample` off is **not allowed** — the interlock only forbids
rubbersheet-without-dense_offsets, not the reverse, so `dense_offsets: True,
rubbersheet: False, fine_resample: False` is legal), then:

```bash
time conda run -n isce3_env python -m nisar.workflows.dense_offsets cfg/insar_freqB_dense.yaml
```

**Checkpoint:** read the mean and median of the residual offsets.

```bash
conda run -n isce3_env python -c "
from osgeo import gdal; import numpy as np
d=gdal.Open('scratch/dense_offsets/freqB/HH/dense_offsets')
snr=gdal.Open('scratch/dense_offsets/freqB/HH/snr').ReadAsArray()
a=d.ReadAsArray(); m=snr>5
for i,n in enumerate(['azimuth','range']):
    print(n, 'median %.4f px, MAD %.4f px, n_good %d' % (
      np.median(a[i][m]), np.median(np.abs(a[i][m]-np.median(a[i][m]))), m.sum()))
"
```

If the median residual is **well under 0.1 pixel**, geometry-only coregistration
is confirmed adequate for this pair and the disk-saving choice in steps 3–6 is
justified. If it is a large constant, that is a bulk timing/range bias and we
must enable `rubbersheet` + `fine_resample` for the real run — which costs the
disk we do not have on frequency A, and makes the crop mandatory.

---

### Step 8 — write the RSLC crop tool and cut a frequency-A window

*Cost: development time, plus ~15 GB for the cropped pair.*

This is the net-new code from stage C. Target the land-dominated southern portion
plus a range crop, so that frequency A fits. See §7 risk 1 for the arithmetic; a
50% azimuth × 50% range cut brings the full-chain scratch to ~52 GB, which fits
in 94 GB with room for outputs.

Frequency B must be cut at exactly 1/8 the frequency-A range indices, or the
sideband ionosphere method will not line up.

**Checkpoint:** the cropped RSLCs open cleanly and are self-consistent —

```bash
conda run -n isce3_env python -c "
from nisar.products.readers import SLC
s=SLC(hdf5file='L1_RSLC_crop/ref_20260613_az0-30000_rg0-26000.h5')
rg=s.getRadarGrid('A')
print(rg.length, rg.width, rg.starting_range, rg.prf, rg.lookside)
print(s.getOrbit().start_datetime, s.getOrbit().end_datetime)
"
```

The orbit must still bracket the (shortened) data take. If `getRadarGrid` throws
or the orbit no longer spans the azimuth times, the crop is malformed.

---

### Step 9 — frequency-A GUNW on the crop, with ionosphere

*Cost: **UNVERIFIED**, estimate 8–24 h. Disk peak ~52 GB with all offset stages
on, ~35 GB geometry-only.*

Same runconfig shape, pointed at `L1_RSLC_crop/`, with
`list_of_frequencies: {A: [HH], B: [HH]}` and
`ionosphere_phase_correction: {enabled: True, spectral_diversity: main_side_band}`.
Crossmul at `range_looks: 11, azimuth_looks: 11` → ~52 m ground range × 49 m
azimuth. Geocode posting 80–100 m to match, not 20 m (a 20 m GUNW over this frame
would be 226 Mpx per layer, ~15–20 GB total).

**Checkpoint:** compare the freq-A and freq-B unwrapped phase over the same
ground. They are independent measurements at different resolutions; where both
are coherent they must agree to within the ionospheric difference between
1239.0 and 1293.5 MHz. Large disagreement means one of them is mis-unwrapped.

---

### Step 10 — corrections, geometry export, contract C

*Cost: minutes. Disk: <1 GB.*

```bash
# geometry, once, for the whole track (freq B keeps the rasters small)
conda run -n isce3_env python -m nisar.workflows.get_product_geometry \
  $CASE/L1_RSLC/NISAR_L1_PR_RSLC_022_162_A_007_..._001.h5 \
  --dem $CASE/aux/dem/dem_t162_f007.tif \
  --freq B --od $CASE/geometry \
  --out-inc-angle --out-line-of-sight
```

Always pass explicit `--out-*` flags — with none, it writes **all eleven** layers.

Then stage F (subtract the screens), then stage G (gdalwarp everything to
EPSG:4326 on one grid and write `model.json`).

**Checkpoint:** all five contract-C rasters have identical size, geotransform and
projection, and `model.json` carries `wavelength_m: 0.241963` and an explicit
`sign_convention`.

```bash
for f in unw coh inc azi mask; do
  conda run -n isce3_env gdalinfo $CASE/model/20260613_20260625/${f}_ll.tif \
    | grep -E "Size is|Origin|Pixel Size" | tr '\n' ' '; echo "  <- $f"
done
```

---

### Step 11 — modeling, if there is anything to model

*Cost: minutes once installed.*

Requires `kite` and `okada_wrapper`, neither of which is in `isce3_env` today.
Install into a **third** env rather than perturbing the two verified ones.

Only worth doing if step 6b turns up a real deformation signal. Otherwise stop at
step 10 with a validated coseismic-ready pipeline and no event.

---

### Step 12 — record and commit

Commit `stack.json`, `pair.json`, `geometry.json`, `model.json`, every file in
`cfg/`, and the logs. Not the `.h5` files, not `scratch/`, not `aux/`.

Amend §7 of this document with what actually broke.

---

## 7. Risks and open questions

Ranked by how likely they are to stop us.

### 1. BLOCKER — Disk. Frequency A at full frame does not fit — by a factor of ~2.2.

The scratch dtypes are hardcoded and verified in the source:
`rdr2geo` x/y/z are `gdal.GDT_Float64` (`rdr2geo.py:99-101`);
`geo2rdr` offsets are `GDT_Float64`, flat ISCE format (`Geo2rdr.cpp:37-40`);
resampled SLCs are `GDT_CFloat32`; rubbersheet writes full
`ref_radar_grid.width × .length` at `GDT_Float64`. All at **full resolution**,
uncompressed, unchunked.

Stock runconfig, frequency A, one co-pol:

| Scratch item | Size |
|---|---|
| `rdr2geo/freqA/{x,y,z}` Float64 | 69.15 GB |
| `geo2rdr/freqA/{range,azimuth}.off` Float64 | 46.10 GB |
| `coarse_resample_slc` CFloat32 | 23.05 GB |
| `rubbersheet_offsets` Float64 ×2 | 46.10 GB |
| `fine_resample_slc` CFloat32 | 23.05 GB |
| `dense_offsets` (skip 32) | 0.09 GB |
| **Total** | **≈ 207 GB** |

Against **94 GB free**. And `intermediate_files_removal_enabled` defaults to
`False`, so nothing is reclaimed as you go. Turning it on does not save you
either: the pre-rubbersheet peak alone (rdr2geo 69 + geo2rdr 46 + coarse 23 =
**138 GB**) already exceeds 94 GB, and even with aggressive removal the peak
during `geo2rdr` is 69 + 46 = **115 GB**.

Scaling, for planning:

| Configuration | Peak scratch | Fits in 94 GB? |
|---|---|---|
| Freq B, geometry-only | **17.3 GB** | yes, comfortably |
| Freq B, full chain | **25.9 GB** | yes |
| Freq A, geometry-only, full frame | 138 GB | no |
| Freq A, full chain, full frame | 207 GB | no |
| Freq A crop 50% az × 50% rg, full chain | **≈ 52 GB** | yes |
| Freq A crop 55% az × 100% rg, geometry-only | ≈ 76 GB | tight |

**Mitigations, in order:** (a) frequency B first — it exercises the entire
RIFG→RUNW→GUNW chain at 1/8 the cost; (b) crop for frequency A; (c) free disk or
attach external storage. Full-frame frequency A realistically wants **~250 GB
free**.

Also: always set `product_path_group.scratch_path` explicitly. The default is
`.`, so scratch lands wherever you happened to invoke from.

And one interaction to avoid entirely: on this **installed** build, `insar.py:49-53`
deletes `scratch/rdr2geo` immediately after `geo2rdr` (the source tree defers it
to the end). But `ionosphere.run` at stage 13 symlinks `{scratch}/rdr2geo` into
its own scratch. **Never set `intermediate_files_removal_enabled: True` while
running ionosphere correction on 0.25.12** — you get a dangling symlink. There is
a second, undocumented removal too: `insar.py:89-93` deletes
`coarse_resample_slc` right after fine resampling and **before** crossmul, so
with removal on you cannot fall back to the coarse-resampled SLC.

### 2. BLOCKER — RAM. 12.7 GiB total, ~3.9 GiB free right now.

`free -m` currently reports total 13 016 MiB, used 9 078, available **3 937**,
swap 4 095 MiB with 1 509 used. Something is holding 9 GB.

`lines_per_block: 1000` against a 52 650-sample frequency-A line is 421 MB per
full-width 8-byte buffer; `rdr2geo` holds ~5–6 of them (~2.5 GB) and crossmul is
worse (two SLC blocks plus deramp/FFT workspace, several GB). At
`lines_per_block: 256` one CFloat32 block is 108 MB, `rdr2geo` totals ~0.65 GB and
resample ~0.43 GB — comfortable. All the runconfigs in §6 set 256.

Note the resample stages use `lines_per_tile` / `columns_per_tile`, **not**
`lines_per_block`, and `columns_per_tile: 0` means "span all columns" — set it
explicitly (4096).

**Mitigation:** free RAM before starting, and prefer `ntiles: [4,4]` with
`nproc: 8` for snaphu so the 16 cores get used without a single huge allocation.

### 3. HIGH — No GPU. `dense_offsets` runs on `PyCPUAmpcor`, throughput unknown.

Confirmed: `isce3.matchtemplate.PyCPUAmpcor` exists; `isce3.cuda.matchtemplate`
does not (no NVIDIA hardware). The CPU ampcor path with 64×64 windows, ±20 search
and ×64 correlation-surface oversampling across a decimated grid of
1710 × 1645 (freq A) or 1710 × 206 (freq B) points is the one stage whose runtime
we cannot bound from the code.

**UNVERIFIED: how long does CPU dense_offsets take on this grid?**
Settle it with step 7 of §6 on frequency B, and extrapolate by pixel count
(freq A is ~8× more correlation windows).

If it is intolerable, the geometry-only path is the fallback — justified here by
B⊥ ≈ 31 m and a 12-day repeat — at the cost of losing the residual measurement.
That is risk 5 in §4.4, and step 7 exists specifically to bound it.

### 4. HIGH — DEM sourcing for NISAR is untested on this machine.

`~/.netrc` has a `urs.earthdata.nasa.gov` entry and `sardem` is installed, but
`--data-source NISAR` has never been exercised here.

**UNVERIFIED: does `sardem --data-source NISAR` authenticate and download for
this bbox?**

```bash
conda run -n isce3_env sardem --bbox -67.0 10.0 -66.9 10.1 \
  --data-source NISAR --output-format GTiff -o /tmp/dem_probe.tif && \
  gdalinfo -stats /tmp/dem_probe.tif | head -20
```

A tiny probe bbox settles it in under a minute. If it fails, fall back to
`--data-source COP` (without `--keep-egm`) and accept that we are one DEM
version away from what focused the products — which, at h_amb ≈ 2508 m, changes
the phase by essentially nothing but does introduce a small absolute geolocation
offset.

### 5. RESOLVED — Frequency B exists — resolved, and split-spectrum is viable.

Both granules carry frequency B (5 MHz, 1293.5 MHz centre, HH+HV). ISCE3 handles
the 8× range decimation via `decimate_freq_a_array` / `interpolate_freq_b_array`.
`main_side_band` and `main_diff_ms_band` are both open to us.

Residual concerns, not blockers: the 5 MHz side band gives a **coarse** dispersive
estimate; the stage re-runs a reduced InSAR chain per sub-band pair, so budget
roughly 2× the time and disk; and adding frequency A to `input_subset` for a
sideband run reintroduces the full 2.881 Gpx grid (risk 1).

**UNVERIFIED: is the 5 MHz side band good enough to estimate the ionospheric
screen over this frame at all?** Settle by comparing against the equatorial TEC
gradient magnitude implied by an independent IONEX map for 2026-06-13 and
2026-06-25, and by checking whether the estimated screen is smooth or
noise-dominated.

### 6. HIGH — The RSLC crop tool does not exist and has to be written correctly.

`grep -rn "def crop\|def subset" nisar/` returns nothing. There is no spatial
subsetting in the InSAR runconfig either. Stage C is real net-new code with a
long list of datasets that must stay mutually consistent (see §2.3), and getting
it subtly wrong produces a product that opens fine and geolocates wrong.

**Mitigation:** validate with the `nisar.products.readers.SLC` round-trip in
step 8, and cross-check the cropped product's `boundingPolygon` against a
`rdr2geo` run on a handful of corner pixels.

### 7. MEDIUM — Troposphere needs infrastructure we do not have.

`troposphere_delay.enabled: True` requires both
`troposphere_weather_model_files.{reference,secondary}_troposphere_file`, and
`troposphere_runconfig.py` validates hard: both files must exist, and the weather
model's valid time must be **within 24 h** of each RSLC's `zeroDopplerStartTime`.
`pyaps` requires GRIB and accepts ERA5/ERAINT/HRES/NARR/MERRA;
`raider` requires NetCDF; `delay_direction: line_of_sight_raytracing` forces
`raider`. It also needs `worker.internet_access: True` (default `False`) and a
CDS API key.

None of that is set up. Not a blocker — the tropospheric screen is a GUNW aux
layer we can add later, and for a single pair the dominant atmosphere signal is
ionospheric anyway at L-band.

### 8. MEDIUM — Is there a descending frame over the same ground?

The single highest-value addition (§5(iii)) is an ascending+descending pair, not
more dates.

**UNVERIFIED: does a NISAR descending frame covering ~10.9 N, 68.2 W exist and
is it downloadable?**

```bash
conda run -n dolphin_env python -c "
from opera_utils.nisar import search
r = search(short_name='NISAR_L1_RSLC_PROVISIONAL_V1', orbit_direction='D')
print(len(r))
"
```

(The exact `short_name` for the provisional/beta L1 collection needs confirming
against ASF — the course uses `NISAR_L2_GSLC_BETA_V1` for GSLCs.) An ASF/CMR
polygon search over the frame's WKT with `flightDirection=DESCENDING` is the
direct route.

### 9. MEDIUM — Modeling dependencies are not installed.

`kite`, `okada_wrapper` and `autoRIFT` are all absent. Each pulls a nontrivial
dependency tree (pyrocko for kite, a Fortran/C extension for okada_wrapper,
opencv for autoRIFT). Install into a **third** env — `isce3_env` and
`dolphin_env` are verified and should not be perturbed.

Not a blocker until step 11, and step 11 is conditional on there being an event
worth modeling.

### 10. MEDIUM — Runtime is entirely unbounded.

Every time estimate in §6 is a guess. There is no benchmark anywhere for
`nisar.workflows.insar` on 16 CPU cores with no GPU at this grid size.

**UNVERIFIED: end-to-end wall time for the frequency-B chain.** Step 4 gives the
first real data point (`rdr2geo` on 0.360 Gpx), from which the rest scales
roughly by pixel count — except the ionosphere stage, which re-runs the chain,
and snaphu, which scales with the multilooked grid and tiling.

Run everything under `time`, and record the numbers back into this section.

### 11. SETTLED — Things that are settled and should not be relitigated

- **Orbit direction is Ascending, look side is Left.** Three independent checks.
- **B⊥ ≈ 31 m, h_amb ≈ 2508 m.** DEM accuracy barely matters for the phase.
- **Temporal baseline is 12 days minus 1 s.** A valid repeat pair.
- **12.10 cm per fringe.** λ = 0.241963 m from `processedCenterFrequency`.
  Every C-band constant in the course notebooks (`wavel = 0.0555`,
  `scaling = -4π/5.6*100`, 2.8 cm/fringe) is wrong here by 4.36×.
- **ISCE3/NISAR rasters put data in band 1**, not band 2.
- **The correction screens are never subtracted for us.** Stage F is ours.
- **`nisar.workflows.insar` uses the classic reference-scene model by default**,
  not the geocode-to-absolute-grid model. Both ship in `isce3_env`; they are not
  successive versions of one pipeline.
