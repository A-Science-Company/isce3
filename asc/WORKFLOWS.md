# ISCE3 workflow architecture — Venezuela NISAR RSLC pair

Design document for the modular processing chain we build on top of the
environment in [`SETUP.md`](SETUP.md). Written before the first full run, to be
amended as bugs surface.

- **Branch:** `s1-nisar-setup`
- **Envs:** `isce3_env` (isce3 0.25.12, compass 0.5.6, snaphu-py 0.4.1, sardem, python 3.12)
  and `dolphin_env` (dolphin 0.42.5, mintpy 1.6.4, opera_utils 0.25.6)
- **Machine:** 16 cores, 12.7 GiB RAM (~3.9 GiB free right now), no NVIDIA GPU.
  **Usable free disk: 81.8 GiB (87.84 GB)** — `df -B1` Available and
  `statvfs.f_bavail × f_frsize` agree. Earlier drafts of this document used
  94 GB; that was `f_bfree`, which includes root-reserved space the workflow
  cannot touch. Every margin below is against **81.8 GiB**, and everything is on
  one partition.

Everything below was verified against the installed packages and the two HDF5
files on disk. Where a number is derived rather than read, it says so. Where
something is genuinely unknown it is marked **UNVERIFIED** with the command that
would settle it.

The plan carries **two coregistration tracks** — Track G (GSLC, model-driven,
geocode-to-a-fixed-grid) and Track R (RSLC, conventional, reference-scene with
cross-correlation refinement) — built in parallel and compared. §2.2 is the
one-page version; §4.5 is the head-to-head; §7 sequences them. Neither track has
been run against real data yet: source-level claims and measurements off the
granules are verified, execution is not.

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

### 2.2 Two coregistration tracks, built and run in parallel

ISCE3 ships **two structurally different ways of coregistering this pair**, and
this design builds both. They are not successive versions of one pipeline; they
are different answers to the same question, and running both is the only way to
find out which one this frame actually needs.

| | **Track G — model-driven** | **Track R — conventional** |
|---|---|---|
| Entry point | `nisar.workflows.gslc` (once per date) | `nisar.workflows.insar` (once per pair) |
| Product | GSLC ×2, then a hand-written conjugate product | RIFG → RUNW → GUNW |
| Reference | **a pinned map geogrid** + DEM + orbit | **the reference RSLC's radar grid** |
| Coregistration mechanism | each date is geocoded independently to the *same* grid; registration is a by-product | secondary is resampled onto the reference grid via geometric offsets, then refined by ampcor |
| Cross-correlation | **none** | `dense_offsets` → `rubbersheet` → `fine_resample` |
| Iono / SET handling | applied as **timing shifts** during geocoding (geolocation only) | not applied at all during coregistration; shipped as **phase screens** in the GUNW |
| Scratch cost | **~56 KB** (a 5 km-decimated correction grid, only if SET is on) | 40–80 B per full-radar-grid pixel |
| Cost driver | **output posting and AOI size** | **input radar-grid size** — nothing in the runconfig changes it |
| Frequency A, full frame | fits (59.5 GiB of *output*, and an AOI shrinks it linearly) | **does not fit at any setting** (§8 risk 1) |
| Scales to a stack | yes, directly — this is the dolphin input path | no; each pair pays the geometry cost again |
| Silent failure mode | unpinned geogrid ⇒ the two dates land on different origins and nothing downstream notices | unmeasured residual misregistration when `dense_offsets` is off |

The two tracks share **stage A (ingest)**, **stage B (DEM/aux)**, and everything
downstream of the interferogram — correction apply, geometry export, contract C,
modeling, and the N-image extensions. What differs is only the middle.

**Track G is cheap and must run first.** Its cost is set by the *output* grid, so
an AOI makes it arbitrarily small; Track R's cost is set by the *input* radar
grid, which no runconfig key can reduce. That inversion is the single most
important scheduling fact in this document and it drives the ordering in §7.

**The one thing Track G must get right.** `nisar/workflows/geogrid.py:181-229`:
if *any* of `output_epsg`, `top_left.{x_abs,y_abs}`, `bottom_right.{x_abs,y_abs}`
is null, the grid falls out of
`isce3.product.bbox_to_geogrid(radar_grid, orbit, …)` — **that scene's** radar
grid and orbit. Measured on our two granules at EPSG:32619, 5 m:

```
cycle 022 freqA : start_x=426081.736  start_y=1350906.316  w=62267  l=59565
cycle 023 freqA : start_x=426516.902  start_y=1348887.613  w=62266  l=59564
                  Δx = 435.17 m (87.03 px)   Δy = 2018.70 m (403.74 px)   Δw=1  Δl=1
```

Non-integer pixel offsets, so the two grids are not even alignable by cropping,
and freq A vs freq B of the *same* granule already disagree by 4.08 m. `x_snap` /
`y_snap` do **not** fix this — they give a common lattice, not a common array
(worked example in §4.5). All five keys must be pinned explicitly, identically,
for every date. They live in `stack.json` and nothing re-derives them.

### 2.3 The pipeline

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
        └──────────┬─────────────────┘
                   │
        stack.json.geogrid  =  ONE pinned grid  (epsg + 4 corners + posting)
        BOTH tracks consume it verbatim.  Nothing re-derives it.
                   │
     ┌─────────────┴─────────────────────────────────┐
     │                                               │
════ TRACK G ═══════════════════════════   ════ TRACK R ══════════════════════
 model-driven, geocode-to-fixed-grid        conventional, reference-scene
 cost ∝ OUTPUT posting × AOI                cost ∝ INPUT radar grid
     │                                               │
┌────▼──────────────────────────────────┐   ┌────────▼──────────────────────────┐
│ G1. GSLC per date  (×2, independent)  │   │ C. RSLC WINDOW CUT                │
│   nisar.workflows.gslc  gslc_<d>.yaml │   │  [NET-NEW CODE; freq A only]      │
│   • geo2rdr per geo-block             │   │  h5py → cropped RSLC pair         │
│   • SET as slant-range timing shift   │   └────────┬──────────────────────────┘
│   • TEC as az+rg timing shift (if     │            │
│     tec_file given; off by default)   │   ┌────────▼──────────────────────────┐
│   • flatten: true  ⇒ topo removed     │   │ D. INSAR                          │
│     per date with UNcorrected srange  │   │  python -m nisar.workflows.insar  │
│   scratch ≈ 56 KB.  NO rdr2geo grid.  │   │                                   │
└────┬──────────────────────────────────┘   │  D1 rdr2geo (ref + DEM)  Float64  │
     │ L2_GSLC/<date>_gslc.h5               │  D2 geo2rdr (sec + topo) Float64  │
     │                                      │  D3 coarse_resample               │
┌────▼──────────────────────────────────┐   │  D4 dense_offsets   ── ampcor ──┐ │
│ G2. GRID GATE  (hard assert)          │   │  D5 rubbersheet     ← residual ─┘ │
│   shape == shape, epsg == epsg,       │   │  D6 fine_resample                 │
│   geotransform allclose 1e-6          │   │  D7 crossmul   → RIFG             │
│   FAILS ⇒ the geogrid was not pinned  │   │  D8 filter_interferogram          │
└────┬──────────────────────────────────┘   │  D9 unwrap (snaphu) → RUNW        │
     │                                      │  D10 split_spectrum + ionosphere  │
┌────▼──────────────────────────────────┐   │  D11 geocode_insar → GUNW         │
│ G3. CONJUGATE PRODUCT  [NET-NEW]      │   │  D12 troposphere  [needs weather] │
│   ref · conj(sec) on the common grid  │   │  D13 solid_earth_tides            │
│   → multilook → coherence → filter    │   │  D14 baseline                     │
│   → snaphu → unw / conncomp           │   └────────┬───────────┬──────────────┘
│   NO resample. NO baseline. NO        │            │ RIFG/RUNW │ GUNW
│   flattening — flatten:true did it.   │            │ (radar)   │ (geocoded)
└────┬──────────────────────────────────┘            │           │
     │                                               │           │
     └──────────────────┬────────────────────────────┴───────────┘
                        │
   ┌────────────────────▼────────────────────────────────────────────────────┐
   │ K. TRACK COMPARISON   asc/compare/  [NET-NEW]                           │
   │   both tracks resampled to ONE 50 m map grid, then:                     │
   │   • coherence histograms + KS distance      • water-floor check (γ→0)   │
   │   • sign convention (conj, on 8×8 tiles)    • ramp / residual split     │
   │   • residual circular std vs Cramér–Rao     • offset QC: which is wrong │
   │   Difference budget: iono ≫ SET ≫ resampling count ≫ interp method     │
   └────────────────────┬────────────────────────────────────────────────────┘
                        │
        ┌───────────────┴────────────────────────────────────┐
        │                                                    │
   ┌────▼─────────────┐   ┌──────────────────────────────────▼─────────────┐
   │ E. GEOMETRY      │   │ F. CORRECTION APPLY                            │
   │ get_product_     │   │ φ_corr = φ − φ_iono − φ_wet − φ_hydro − φ_SET  │
   │ geometry.py      │   │ (screens ship as separate layers; the          │
   │ or GUNW cube     │   │  workflow NEVER subtracts them for you).       │
   │ + DEM interp     │   │  Track G ships NO screens — its corrections    │
   │ → inc / azi      │   │  were geolocation-only, so F is a no-op there. │
   └────────┬─────────┘   └──────────────────┬─────────────────────────────┘
            │                                │
            └──────┬─────────────────────────┘
                   │
   ┌───────────────▼─────────────────────────────────────────────────────────┐
   │ G. MODEL-READY EXPORT  ("contract C")                                   │
   │    5 single-band EPSG:4326 rasters on ONE grid, band 1:                 │
   │    unw_ll.tif  coh_ll.tif  inc_ll.tif  azi_ll.tif  mask_ll.tif          │
   │    + model.json  { wavelength_m, sign_convention, los convention,       │
   │                    track: "G" | "R" }                                   │
   └───────────────┬─────────────────────────────────────────────────────────┘
                   │
   ┌───────────────▼─────────────────────────────────────────────────────────┐
   │ H. MODELING   kite quadtree → .okinv → Okada / Mogi → Powell inversion  │
   └─────────────────────────────────────────────────────────────────────────┘

  ══════════════════ N-IMAGE EXTENSIONS (structurally present, inert at N=2) ══

   ┌─────────────────────────┐        ┌──────────────────────────────────────┐
   │ I. TRACK G → dolphin    │        │ J. TRACK R → MintPy                  │
   │ L2_GSLC/*_gslc.h5       │        │ smallbaselineApp.py <case>.txt       │
   │ (already on the pinned  │        │ mintpy.load.processor = nisar        │
   │  geogrid — this is why  │        │ mintpy.load.unwFile = pairs/*/GUNW/* │
   │  G2 is a hard gate)     │        │ mintpy.load.demFile = aux/dem/*.tif  │
   │ dolphin config / run    │        │ (dolphin_env)                        │
   │ (dolphin_env)           │        │                                      │
   └─────────────────────────┘        └──────────────────────────────────────┘
        needs N≥8 to mean anything          needs N≥3 for closure,
        (PS, phase linking, tcoh)           N≥5 for a useful network
```

Read the diagram as a scheduling statement, not just a topology: Track G's whole
column can be executed on an AOI in an afternoon and leaves ~56 KB of scratch
behind; Track R's column commits 16.1 GiB on frequency B and cannot be run at all
on frequency A without stage C.

### 2.4 Stage contracts

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
  "geogrid": {
    "output_epsg": 32619,
    "top_left":     {"x_abs": 434400.0, "y_abs": 1348800.0},
    "bottom_right": {"x_abs": 736200.0, "y_abs": 1054800.0},
    "x_snap": 600.0, "y_snap": 600.0,
    "posting": {"A": {"x": 5.0, "y": 5.0}, "B": {"x": 40.0, "y": 5.0}},
    "comparison_posting_m": 50.0,
    "_note": "Pinned once. Every GSLC runconfig and the geocode_insar block copy these verbatim. See 4.5."
  },
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
| CMD | see §7 step 2 |
| CFG | none |
| CONTRACT | DEM is **ellipsoidal height, WGS84** (never geoid), float32 GTiff, EPSG:4326, covering the union polygon + ~10 km. Its EPSG becomes the default `geocode.output_epsg`. |

Per-track, not per-pair, not per-date. Adding dates never re-runs this.

**The DEM must exist before either track runs, and it does not yet.**
`ls $CASE/aux/dem` → *No such file or directory*; the case-study directory holds
only the two RSLC `.h5` files. `dumpconfig` will **not** catch this — it does
`Path(dem_file).expanduser().resolve()`, which does not require existence — and
neither does yamale schema validation, which never touches the filesystem. The
failure surfaces later, at `helpers.check_dem` / `isce3.io.Raster(dem_file)`.
Note that once the geogrid is fully pinned (EPSG + both postings + all four
corners), `geogrid.py:132-174` is skipped entirely, so the DEM's own grid and
EPSG do **not** affect the output geogrid — only its *coverage* of the pinned
box matters.

---

**G1. GSLC PER DATE** — env `isce3_env`, stock ISCE3. **Track G.**

| | |
|---|---|
| IN | one RSLC `.h5`, `dem_file`, the pinned geogrid from `stack.json` |
| OUT | `L2_GSLC/<YYYYMMDD>_gslc.h5` |
| CMD | `python -m nisar.workflows.gslc cfg/gslc_<date>.yaml` |
| CFG | one YAML per date, schema `nisar/workflows/schemas/gslc.yaml`; generated by `python -m nisar.workflows.dumpconfig gslc <rslc> -d <dem> …` then hand-edited |
| CONTRACT | Both dates **must** carry byte-identical `output_epsg`, `output_posting`, `top_left`, `bottom_right`. Output filename **must** contain a `%Y%m%d` date (dolphin filters on it). `flatten: true`. `list_of_frequencies` **must** be set explicitly. |

Five things that are specific to this workflow and are easy to get wrong:

- **`dumpconfig` leaves `list_of_frequencies` blank, and blank means A+B ×
  HH+HV** (`runconfig.py:179-186`). For our DHDH products that is four
  full-resolution rasters per date — 105.8 GiB for the pair against 81.8 GiB
  free. This is the single biggest disk trap in Track G. Always write
  `{A: [HH]}`.
- **Set `x_snap` and `y_snap` together or not at all.** `geogrid.py:282-287`
  tests `if x_snap is not None or y_snap is not None:` and then immediately
  `if x_snap <= 0`; setting only one is a `TypeError`. And `x_snap` must be an
  exact multiple of *every* processed frequency's `x_posting` (`geogrid.py:289`,
  checked per frequency) — with A=5 and B=40 that forces a multiple of 40. **600**
  divides 5, 10, 20, 30, 40 and 50 and is what `stack.json` pins.
- **`radar_grid_cubes.x_snap` / `y_snap` are inert.** `runconfig.py:342-344`
  unconditionally overwrites them with the cube posting after the merge, and does
  the same for the `calibration_information` and `processing_information` groups.
  Do not put them in the runconfig; if you do, annotate them as ignored.
- **`reference_gslc` is a no-op stub.** `geocode_corrections.py:283-297` calls
  `_compute_offset_luts()`, whose entire body logs *"Data-driven timing
  correction for GSLC is not implemented."*, and `gslc.py:50` computes
  `apply_data_driven_correction` and never uses it. Leave it blank; the `ampcor`
  runconfig block is dead weight for GSLC.
- **For GSLC and GCOV, `flag_none_is_valid` is False** (`runconfig.py:77`). A
  blank key in our runconfig inherits the packaged default rather than clearing
  it. You cannot "unset" a default by blanking it.

`output.data_type: complex64_zero_mantissa` (the default) is plain `complex64`
with the low 22 mantissa bits zeroed so gzip bites. Keep it. The alternative,
`complex32`, halves the file but stores `{float16, float16}` components —
11 bits of mantissa and a half-float path through GDAL's netCDF driver that
dolphin depends on. (Both options commit a compound named type at the file root,
`h5_prep.py:75-87`, so that is *not* the differentiator; component width is.)

---

**G2. GRID GATE** — env `isce3_env`, net-new, ~30 lines. **Track G.**

| | |
|---|---|
| IN | the two GSLCs |
| OUT | pass/fail, nothing written |
| CONTRACT | Hard assert on `shape`, `epsg`, and geotransform (`allclose`, atol 1e-6). Run before **every** pair. |

```python
GRID  = "/science/LSAR/GSLC/grids/frequencyA"
# xCoordinates/yCoordinates are pixel CENTRES -> shift half a pixel for the GT
gt = (float(x[0]) - dx/2.0, dx, 0.0, float(y[0]) - dy/2.0, 0.0, dy)
```

This exists because **dolphin does not check it**: `_readers.py:1057`
(`_assert_images_same_size`) compares x/y size only, and `_readers.py:805-820`
takes the geotransform from the **first file** and stamps it onto the whole VRT.
Two GSLCs with the same shape and different origins produce no error and a
silently misregistered time series.

---

**G3. CONJUGATE PRODUCT** — env `isce3_env`, net-new. **Track G.**

| | |
|---|---|
| IN | two grid-gated GSLCs |
| OUT | `pairs/<date12>/trackG/{ifg,coh,unw,conncomp}.tif` |
| CMD | `python asc/compare/gslc_igram.py --ref … --sec … --looks 8 8` |
| CONTRACT | `ref · conj(sec)` on the common grid. **No resampling, no baseline, no flattening** — `flatten: true` already removed `4π·r_k/λ` from each date, so the conjugate product removes `4π(r₁−r₂)/λ` by construction. |

**There is no ISCE3 CLI for this and it is not an oversight.**
`crossmul.py` reads `reference_rslc_file` / `secondary_rslc_file` and
`crossmul_runconfig.py:29-40` resolves `coregistered_slc_path` against the InSAR
scratch tree of resampled **radar-coordinate** SLCs; `filter_interferogram.py`
and `unwrap.py` both instantiate `RIFGGroupsPaths()` and operate on the RIFG
HDF5. Of the 40 modules in `nisar/workflows` with a `__main__`, the only one that
reads a GSLC is `gslc_point_target_analysis.py`. Forming an interferogram from
two GSLCs is ours to write.

Two required departures from the course's `utils.ifgram_and_coherence()`:
**row-block streaming** (a full-frame 5 m freq-A GSLC is 26.4 GiB against ~4 GiB
free RAM, and the reference implementation reads both arrays whole), and
**coherence from multilooked sums rather than a sliding boxcar**, so the
coherence grid matches the interferogram grid and `snaphu.nlooks` is meaningful.

```python
a = dref[r0:r1, :].astype(np.complex64)
b = dsec[r0:r1, :].astype(np.complex64)
bad = ~np.isfinite(a) | ~np.isfinite(b)      # GSLC invalid fill is NaN+NaNj
a[bad] = 0; b[bad] = 0
ifg = a * np.conj(b)
num = _look(ifg, az_looks, rg_looks)                                        # SUM
pr  = _look((a.real**2 + a.imag**2).astype(np.float32), az_looks, rg_looks)
ps  = _look((b.real**2 + b.imag**2).astype(np.float32), az_looks, rg_looks)
coh = np.abs(num) / np.sqrt(pr * ps)          # ISCE3 crossmul normalisation
```

What the result contains, given `flatten: true` on both dates: **deformation +
differential atmosphere (tropo + iono) + differential SET + DEM-error residual +
noise.** Topographic and flat-Earth fringes are gone. At B⊥ ≈ 31 m the DEM-error
term is 0.025 rad per 10 m of height error — negligible.

**Never set `flatten: false`.** NISAR's GSLC writer passes neither
`carrier_phase_block` nor `flatten_phase_block` to the HDF5 (COMPASS does write
them as `/data/azimuth_carrier_phase` and `/data/flattening_phase`; NISAR's
`gslc.py:190-203` does not), so the flattening phase is applied and never
recorded. `flatten: false` is irreversible from the product.

---

**C. RSLC WINDOW CUT** — env `isce3_env`, **net-new code, does not exist in
ISCE3.** Only needed for a frequency-A run on **Track R** (see §8 risk 1).
Track G needs no crop: `block_generator` (`gslc.py:159-164`) only reads the RSLC
blocks that map into the AOI geogrid, so shrinking the AOI shrinks the work.

| | |
|---|---|
| IN | `L1_RSLC/*.h5`, an azimuth-line range and a range-sample range |
| OUT | `L1_RSLC_crop/*.h5` on a reduced radar grid |
| CMD | `python asc/tools/crop_rslc.py --az 0 30000 --rg 0 26000 <in.h5> <out.h5>` |
| CONTRACT | Must consistently slice `swaths/frequency{A,B}/{HH,HV}`, `swaths/frequency{A,B}/slantRange`, `swaths/zeroDopplerTime`, `validSamplesSubSwath1` (and shift its indices), while leaving `metadata/orbit`, `metadata/attitude` and `metadata/geolocationGrid` intact and updating `identification/{zeroDopplerStartTime, zeroDopplerEndTime, boundingPolygon}`. Frequency B must be cut at exactly 1/8 the range indices of frequency A or the sideband ionosphere method breaks. |

There is no crop utility anywhere in the installed `nisar` package
(`grep -rn "def crop\|def subset" .` returns nothing under `nisar/`). This is
real work, and it is the reason §7 sequences frequency B first.

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

**K. TRACK COMPARISON** — env `isce3_env`, net-new, lives in `asc/compare/`.

| | |
|---|---|
| IN | Track G's `pairs/<date12>/trackG/*`, Track R's RIFG/GUNW, both on the pinned grid |
| OUT | `pairs/<date12>/compare/report.txt`, `compare.json`, difference rasters |
| CMD | `python asc/compare/compare_tracks.py --pair 20260613_20260625 --posting 50` |
| CONTRACT | Both tracks resampled to **one** 50 m map grid before any statistic is computed. Emits a go/no-go table, not a narrative. |

Both tracks form `ref · conj(sec)` and both flatten to the **same DEM**, so the
comparison is apples to apples. What **must agree**: deformation and residual
topography, to within look noise. What **may legitimately differ**, ranked by
expected magnitude on this frame:

| # | Difference | Mechanism | Magnitude here |
|---|---|---|---|
| 1 | **Ionosphere** | Track G applies `tec_file` as a slant-range/azimuth **timing shift**; Track R's `ionosphere_phase_correction` is off by default and ships a **phase** layer when on | 1 TECU dTEC = 13.6 rad = 2.2 fringes. 10° N, post-sunrise EIA ⇒ 1–10 TECU plausible ⇒ **14–140 rad, smooth**. Dominant. |
| 2 | **Solid Earth tides** | Track G applies SET as a geometric shift during geocoding; Track R runs `solid_earth_tides.run()` *after* geocoding as a **layer**, never applied | ~0.3–2 rad plane across a 300 km frame |
| 3 | **Resampling count** | kernel is identical (`Sinc2dInterpolator(8, 8192)`); Track G is 1 pass/date and symmetric, Track R is 0 on the reference and 2 on the secondary | ~1–2% extra coherence loss on Track R |
| 4 | **Post-hoc map interpolation** | Track R only: `geocode.interp_method` (default `BILINEAR`) smooths coherence and unwrapped phase; Track G never interpolates a product | Track R histogram narrower, quartiles shift 0.02–0.05. Set `NEAREST` for a like-for-like histogram. |
| 5 | **Coherence support** | Track R estimates γ in radar coords (a fixed sample window ⇒ a *varying* ground cell across the swath); Track G over a fixed map cell | ±25% N_eff across the swath ⇒ systematic incidence-dependent difference |

What must **not** differ, so if it does it is not these: geometric decorrelation
(b_crit 18.5/27.2/36.6 km near/mid/far ⇒ γ_geom > 0.998), volume decorrelation
(40 m canopy ⇒ γ_v = 0.9995), and DEM error. On `h_amb = λ·r·sinθ / (2·B⊥)` with
B⊥ = 31 m: **1887 / 2488 / 3006 m** near/mid/far — the mid figure is §1.5's
≈2500 m. A **50 m** DEM error is therefore **0.13 rad**, so anything above
~0.15 rad is not the DEM.

Three design points in the metric library that are not obvious:

1. **Never carry bare phase.** Sum the complex numerator; a ±π neighbour pair
   averaged as phase gives 0 instead of π.
2. **The ramp search is not a least-squares fit.** A weighted LSQ on wrapped
   phase cannot see more than half a fringe across the scene, and the expected
   ionospheric term is *tens* of fringes. Use block-average → zero-padded FFT
   peak → direct 21×21 refinement at 1/16 bin, *then* LSQ.
3. **Sign detection must be local.** A real ramp drives the global resultant of
   both `c1·conj(c2)` and `c1·c2` toward zero, and a global test then answers at
   random. Measure on 8×8 tiles.

Interpretation thresholds:

| statistic | agree | explainable | real difference |
|---|---|---|---|
| coherence KS distance | < 0.10 | 0.10–0.25 | > 0.25 |
| `resid_circ_std / (√2·CRB)` | 1.0–1.4 | 1.4–2.0 | > 2.0 |
| ramp across the scene | < 1 fringe | 1–3 | > 3 |
| residual ACF e-folding | ≤ 2 px = noise | 0.1–2 km = coregistration texture | > 5 km = a **field** |

**Which track is wrong, when they disagree.** For Track R the measurement is
free: `rubbersheet − geo2rdr` *is* the residual. Judge on the **high-pass** MAD,
not the total — the smooth part is a field, not misregistration.

| residual MAD (samples) | slant range | along-track | γ_misreg | verdict |
|---|---|---|---|---|
| 0.05 | 0.156 m | 0.223 m | 0.994 | ACCEPT |
| **0.10** | **0.312 m** | **0.445 m** | **0.977** | **ACCEPT (limit)** |
| 0.25 | 0.781 m | 1.113 m | 0.860 | WARN/REJECT |
| 0.50 | 1.561 m | 2.226 m | 0.546 | REJECT |

**The discriminator:** a range residual that is smooth over kilometres while the
azimuth residual is white is **ionospheric group delay, not misregistration**.
Print the implied Track-R-only phase `(4π/λ)·δr` and correlate it against the
ramp term; `r > 0.7` closes the case.

Track G has no ampcor step, so manufacture one: chip-wise amplitude
cross-correlation between the two GSLCs, reported in **map metres**. The peak
must be at (0,0); accept `|median| < 0.54 m` in both axes. Non-zero in **East
only** ⇒ the slant-range model (TEC LUT, geoid/DEM datum); **North only** ⇒
azimuth timing (orbit, azimuth LUT); **both** ⇒ the DEM.

**The water-floor check has the most teeth of anything here.** Liquid water has
true γ = 0, so the multilook estimator's expectation is not 0 but
**`√π / (2√N_eff)`**. Compute the target from *each track's own* N_eff — they
will differ:

| configuration | N_eff | floor |
|---|---|---|
| Track R, freq B, `2, 17` looks (ILN = 0.6918·k_r·k_a) | 23.5 | **0.183** |
| Track G, 5 m posting, 8×8 boxcar | 53 | **0.122** |
| Track G, 5 m posting, 10×10 boxcar | 82 | **0.098** |

Each track must land within ±0.02 of *its* floor. More than 0.05 above it means
the two dates are not independently sampled: geolocation, geoid/DEM datum, or the
same granule read twice.

Also pin, or the comparison is void: same `dem_file`, same orbit source, **same
`tec_file` or neither**, matching `solid_earth_tides_enabled` /
`ionosphere_phase_correction.enabled`, same frequency and polarization.

**UNVERIFIED: none of `asc/compare/` has been run against real ISCE3 output** —
it has been exercised end to end on synthetic pairs only.

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

**H. MODELING** — env: needs `kite` + `okada_wrapper` (not yet installed; see §8).

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

Structurally present now, inert at N=2. Their contracts are in §6(ii). The
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
│   ├── gslc_20260613.yaml            # TRACK G — one per DATE
│   ├── gslc_20260625.yaml            #   identical geogrid block in both
│   ├── insar_freqB_geomonly.yaml     # TRACK R — one per PAIR
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
│       ├── RIFG/  RUNW/  GUNW/       # TRACK R products
│       ├── trackG/                   # TRACK G products (net-new, stage G3)
│       │   └── ifg.tif coh.tif unw.tif conncomp.tif
│       ├── compare/                  # stage K
│       │   ├── report.txt  compare.json
│       │   └── diff_phase.tif  diff_coh.tif
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
├── L2_GSLC/                          # TRACK G output, one file per DATE
│   ├── 20260613_gslc.h5              # basename MUST contain %Y%m%d (dolphin)
│   └── 20260625_gslc.h5
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

**We build both mechanisms, not one.** §4.1–4.3 dissect them; §4.4 lists the
failure modes they share; **§4.5 puts them side by side** and is the section to
read if you only read one.

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
   offset path is the right one — and we will not know until step 5 (Track G) and step 8 (Track R) of §7.
4. **Water.** 38% of the frame. Handled by the water mask.
5. **Silent bias.** Nothing in the geometry-only path *measures* the residual
   misregistration — that is exactly what `dense_offsets` exists for. Turning it
   off (which §7 does on Track R, for disk reasons) trades a measured residual for an
   unmeasured one. Mitigation: run `dense_offsets` **once** on a cropped window
   purely as a diagnostic, read the mean/median residual offsets, and confirm
   they are sub-pixel before trusting the geometry-only full run. That is
   step 9 in §7.
6. **DEM error where the surface actually moved** — glaciers, dune fields, large
   subsidence, forest growth — is not common-mode. Not a concern on this frame.
7. **Layover and shadow.** Steep terrain in the Coastal Range. The workflow can
   emit a layover/shadow mask; use it.

### 4.5 Track G vs Track R, head to head

Everything above, condensed to the comparison that matters.

| | **Track G** (`nisar.workflows.gslc` ×2 + stage G3) | **Track R** (`nisar.workflows.insar` ×1) |
|---|---|---|
| **Reference** | A pinned map geogrid (EPSG + 4 corners + posting) plus the DEM and each date's own orbit. **No reference image.** | The reference RSLC's radar grid. A specific file, byte for byte. |
| **How the secondary is aligned** | It isn't, as such. Each date is independently inverted from map coordinates to *its own* radar grid by `geo2rdr` per geo-block, and sampled. Alignment is a consequence of both landing on the same output lattice. | Explicitly: `rdr2geo` on the reference gives ground coordinates per reference pixel; `geo2rdr` inverts those into the secondary's az-time/slant-range; the difference is `range.off` / `azimuth.off`; `resample_slc_v2` interpolates the secondary onto the reference grid. |
| **Where cross-correlation enters** | **Nowhere.** There is no ampcor step and no data-driven hook — `reference_gslc` logs "not implemented". | `dense_offsets`: amplitude cross-correlation of the **reference RSLC against the coarse-resampled secondary**, so it measures only the *residual* after geometry — which is why 64×64 windows with ±20 px search suffice. Then `rubbersheet` culls/fills/smooths and **adds** the residual to the geometric offsets; `fine_resample` re-resamples against the sum. |
| **Interpolation kernel** | `Sinc2dInterpolator(8, 8192)` (`geocodeSlc.cpp:638`) | `Sinc2dInterpolator(8, 8192)` (`image/Resample.cpp:26`) — **identical** |
| **Resampling passes** | 1 per date, symmetric | 0 on the reference, 2 on the secondary (coarse + fine), plus `crossmul.oversample: 2`. Asymmetric. |
| **Iono / SET** | **Timing shifts.** `AzSrgCorrections` builds LUTs on a 5 km-decimated grid and passes them as `az_time_correction` (seconds) / `srange_correction` (metres) into `geocode_slc`; they perturb **which radar sample is picked**. | **Not applied during coregistration at all.** `geo2rdr`'s InSAR runconfig block contains only `threshold`, `maxiter`, `lines_per_block`. SET and iono arrive later as **GUNW phase layers**, and the workflow never subtracts them. |
| **Troposphere** | **Not implemented.** `grep -n tropo gslc.py geocode_corrections.py gslc_runconfig.py defaults/gslc.yaml schemas/gslc.yaml` → no matches. | `troposphere_delay` exists (D12), needs weather files + internet. |
| **What is removed from the phase** | `4π·r_geom/λ` per date, using the **uncorrected** slant range — `flattenWithCorrectedSRng` is hard-wired `False` (three hits, all inside `isce3/geocode/geocode_slc.py`; neither `nisar/workflows/gslc.py:190-203` nor `compass/s1_geocode_slc.py:202-221` passes it). | `4π·Δr/λ` from the **geometric** `geo2rdr/freqX/range.off` (`crossmul.py:104`) — *not* the rubbersheet offsets that drove the resampling. |
| **Consequence** | GSLC phase = `φ_scatterer − 4π·δ/λ` with δ the **full** propagation excess: ionosphere, troposphere, SET, and real motion. SET/TEC are geolocation-only. **All atmospheric phase survives**, which is what you want, and makes atmospheric correction entirely a downstream problem. | The residual `(4π/λ)·δr` between the rubbersheet and geo2rdr range offsets enters Track R's phase and **only** Track R's phase. That difference is measurable — it is exactly `rubbersheet − geo2rdr` (stage K). |

**The geogrid is Track G's whole coregistration problem, and it is silent.**
Measured on our granules (§2.2): unpinned, the two dates differ by 435.17 m in X
and 2018.70 m in Y, at non-integer pixel offsets, with shapes differing by 1×1.

`x_snap` / `y_snap` alone do **not** fix it. `geogrid.py:294-300` snaps
`start_x` with `floor`, `start_y` with `ceil`, and the ends the other way — so
both origins become multiples of the snap and pixel *boundaries* coincide, but
the extents still differ. With `y_snap = 5000` on the measured values:

```
date 1:  ceil(1350906.316 / 5000) * 5000 = 1355000
date 2:  ceil(1348887.613 / 5000) * 5000 = 1350000     -> 5 km, 1000 rows apart
```

Snap gives a common lattice, not a common array. **All five keys, explicitly,
identically.** Then `geogrid.py:255-262` is pure arithmetic on our numbers and
nothing date-dependent enters:

```python
width  = _grid_size(end_x, start_x, spacing_x)     # np.round
length = _grid_size(end_y, start_y, spacing_y)
geogrid = isce3.product.GeoGridParameters(start_x, start_y, spacing_x, spacing_y,
                                          width, length, epsg)
```

**Failure modes, per track.**

Track G:

1. **Partially-pinned geogrid** → misregistration that nothing downstream
   catches. Mitigation: gate G2, run before every pair; and never rely on
   dolphin, which checks size and not geotransform.
2. **Blank `list_of_frequencies`** → A+B × HH+HV = 105.8 GiB for the pair,
   against 81.8 GiB free.
3. **Blank `output_posting`** → `geogrid.py:132-173` copies the spacing straight
   off the DEM when `output_epsg == dem_raster.get_epsg()`. A 1-arcsec DEM would
   silently give a 30 m GSLC, which for a **complex** SLC is catastrophically
   under-Nyquist: the spectrum aliases and the phase is corrupted.
4. **No residual measurement exists.** Track G has no ampcor step at all, so the
   only way to know it worked is to manufacture one (stage K's chip-wise
   amplitude correlation between the two GSLCs, judged in map metres).
5. **`PROJ_DATA` is unset in `isce3_env`.** `proj.db` ships at
   `$CONDA_PREFIX/share/proj/proj.db` but nothing exports the variable, so
   `osr.SpatialReference().ImportFromEPSG(32619)` fails **soft** — non-zero
   return, `GetAttrValue('AUTHORITY', 1)` is `None`, and you get GeoTIFFs with no
   CRS. `pyproj` and `isce3` are unaffected (own data paths), which is why the
   `bbox_to_geogrid` measurements above succeeded while spraying PROJ errors.
   Export it in `activate.d` and record it in `SETUP.md`.

Track R:

6. **Disk.** A hard blocker on frequency A at any setting (§8 risk 1).
7. **Unmeasured residual** when `dense_offsets` is off for disk reasons — see
   §4.4 item 5 and step 9 of §7.
8. **`intermediate_files_removal_enabled: True` is unsafe** with ionosphere
   correction (it deletes `scratch/rdr2geo`, which `ionosphere.py:795-801,
   1010-1017` then symlinks to) and with any coregistration-only run
   (`insar.py:112-118` and `:126-131` are unconditional and delete
   `coarse_resample_slc` and `geo2rdr` — the deliverables).

---

## 5. Looks, ground geometry, and what multilooking does not buy

### 5.1 Native ground geometry across the swath

Slant-range spacing is constant; ground-range spacing is not. Incidence runs
**33.22° near / 41.38° mid / 47.39° far** (the geolocation cube in §1.3 quotes
33.16–47.35 at h ≈ 0; the difference is the height layer used and is immaterial
here). Ground range spacing is `slant_spacing / sin θ`:

| | near 33.22° | mid 41.38° | far 47.39° |
|---|---|---|---|
| **Freq A** ground range (3.1228 m slant) | **5.699 m** | **4.724 m** | **4.243 m** |
| **Freq B** ground range (24.9827 m slant) | **45.595 m** | **37.793 m** | **33.945 m** |
| Azimuth ground (both frequencies) | 4.452 m | 4.452 m | 4.452 m |
| **Freq A** range : azimuth | 1.28 | **1.06** | 0.95 |
| **Freq B** range : azimuth | 10.24 | **8.49** | 7.62 |

Azimuth ground spacing = ground-track velocity **6766.6 m/s** ×
`zeroDopplerTimeSpacing` **6.578947e-4 s** = **4.452 m**. The granule's own
`sceneCenterAlongTrackSpacing` is 4.4560 m (implying v_g = 6773.1 m/s); the two
disagree by 0.09%, which changes nothing below. **Use 4.452 throughout and do not
mix the two.**

The two conclusions that drive everything in this section:

- **Frequency A is nearly square natively** (1.06 at mid swath, 1.28 → 0.95
  across the swath). Square looks are right for it: `k_r = k_a`.
- **Frequency B is 8.5:1 anisotropic.** Square looks are *wrong* for it. Frequency
  B wants **range : azimuth looks ≈ 1 : 8**.

(Frequency B's slant-range *spacing* is exactly 8× frequency A's. Its *sample
count* ratio is 52649 / 6582 = **7.99893**, not 8, because of the odd width —
which is why the crop tool in stage C must cut B at exactly ⌊A/8⌋ indices and not
assume the counts divide.)

### 5.2 Recommended looks

Independent-look count. `d/ρ` per axis: range `d_r/ρ_r = 0.8333` for **both**
frequencies (identical by construction — `ρ_r = c/2B` and the spacing is 0.8333ρ
in both bands), azimuth `d_a/ρ_a = 1261.79 Hz / 1520 Hz = 0.8301` from
`processedAzimuthBandwidth` and the azimuth output sample rate. So, per
`defaults/insar.yaml:756-765`:

> **ILN = 0.6918 · k_r · k_a**, for both frequencies.

The data are ~1.2× oversampled on both axes, so you lose ~31% of your nominal
looks. Do not pass `k_r·k_a` to snaphu.

| Freq | `range_looks : azimuth_looks` | ground pixel (rg × az) | rg:az | ILN | RIFG dims (az × rg) | RIFG GB/pol |
|---|---|---|---|---|---|---|
| **A** | 11 : 11 *(ISCE3 default)* | 51.96 × 48.97 m | 1.06 | 83.7 | 4974 × 4786 | 0.27 |
| **A** | **16 : 17** ← recommended | **75.58 × 75.68 m** | **1.00** | **188** | 3218 × 3290 | 0.12 |
| A | 7 : 7 | 33.07 × 31.16 m | 1.06 | 33.9 | 7817 × 7521 | 0.66 |
| A | 3 : 3 *(proposed — do not)* | 14.17 × 13.36 m | 1.06 | **6.2** | 18240 × 17549 | **3.58** |
| **B** | 1 : 8 | 37.79 × 35.62 m | 1.06 | 5.5 | 6840 × 6582 | 0.50 |
| **B** | **2 : 17** ← recommended | **75.59 × 75.68 m** | **1.00** | **23.5** | 3218 × 3291 | 0.12 |
| B | 3 : 25 | 113.38 × 111.30 m | 1.02 | 51.9 | 2188 × 2194 | 0.05 |
| B | 3 : 11 *(this document's earlier choice)* | 113.38 × 48.97 m | 2.32 | 22.8 | 4974 × 2194 | 0.10 |
| B | 3 : 3 *(proposed — do not)* | 113.38 × 13.36 m | **8.49** | 6.2 | 18240 × 2194 | 0.45 |

**Recommendation: A = `16, 17` and B = `2, 17`.** Both land on the same
~75.6 × 75.7 m ground pixel, which makes the two frequencies directly comparable
and gives ILN 188 and 23.5. Match the `geocode.output_posting` to it (75 or 80 m,
not 20 m — a 20 m GUNW over this frame is ~226 Mpx per layer).

If you prefer the standard NISAR-ish posting, `A = 11, 11` (52 × 49 m, ILN 84) is
fine — but the *matching* frequency-B setting is then `1, 11` (37.8 × 49.0 m) or
`1, 8` for squareness. It is **not** `11, 11`, which on frequency B would be
415 × 49 m.

`3, 3` is a poor choice on both counts: on frequency B it gives a 8.5:1
anisotropic pixel (113.4 × 13.4 m), and on either frequency ILN 6.2 gives a
strongly *biased* coherence estimate — the multilook estimator's floor for true
γ = 0 is `√π/(2√N)`, which at N = 6 is 0.36, so decorrelated ground reads as
"marginally coherent".

### 5.3 The `3,3` disk-saving premise is wrong, and here is where it breaks

**Multilooking does not reduce coregistration scratch by one byte.** It cannot,
because of *when* it happens.

`insar.py` stage order, verbatim:

```
bandpass_insar → rdr2geo → geo2rdr → [rm rdr2geo] → prepare_insar_hdf5
  → coarse_resample (resample_slc_v2) → dense_offsets → offsets_product
  → rubbersheet → [rm offsets_product, dense_offsets] → fine_resample
  → [rm coarse_resample_slc] → crossmul → filter_interferogram → …
```

**Crossmul runs after every coregistration stage.** And `range_looks` /
`azimuth_looks` are consumed **only** at `crossmul.py:40-69`, plus
`geocode_insar.py`, `ionosphere.py` and `gcov.py`. **VERIFIED: they are not read
by `rdr2geo`, `geo2rdr`, `resample_slc_v2`, `dense_offsets` or `rubbersheet`.**
Every one of those stages writes at the **full reference radar grid**, and
`crossmul` is the first thing in the chain that produces anything smaller.

So the peak — which occurs at `rubbersheet` or `fine_resample`, before crossmul
ever runs — is completely unaffected by the looks setting.

**It is worse than neutral: `3,3` *costs* disk.** On frequency A the RIFG grows
from 0.27 GB/pol at `11,11` to **3.58 GB/pol** at `3,3`, a 13× increase, because
the multilooked grid is 13× larger.

**And there is no radar-grid decimation option to fall back on.** `grep -nE
'decimat|skip|stride' rdr2geo.py geo2rdr.py` in the installed 0.25.12 InSAR path
returns **nothing**. `dense_offsets.skip_range` / `skip_azimuth` decimate the
*offset* grid only (worth ~0.09 GB on frequency A) and do nothing to the
full-grid rasters.

### 5.4 The levers that actually work

Baseline = stock defaults on this pair (`product_type: GUNW`, both frequencies,
both pols, all offset stages on, removal off): **peak 386.55 GiB**.

| Rank | Lever | Runconfig key | New peak | Saved | What you lose |
|---|---|---|---|---|---|
| **0** | **Run Track G instead** | — | **59.5 GiB of *output*, ~56 KB scratch** | n/a | nothing structural; cost becomes a function of posting and AOI rather than of the input radar grid |
| 1 | **Crop the RSLC** | none — stage C, net-new code | linear in pixels | up to ~100% | scene extent only |
| 2 | **Frequency B instead of A** | `input_subset.list_of_frequencies: {B: [...]}` — *delete* the `A:` key; `runconfig.py:109-114` removes any default frequency absent from our file | **42.95 GiB** | **343.6** | 5 MHz vs 40 MHz ⇒ ~30 m range resolution. Interferometry itself is unaffected: B⊥ ≈ 31 m over 12 days is a geometry problem, not a bandwidth one. |
| 3 | **Disable the offset chain** | `dense_offsets`, `offsets_product`, `rubbersheet`, `fine_resample` — **all four `enabled: False` together** | **217.33 GiB** | **169.2** | no residual coregistration, no `pixelOffsets` layers, crossmul falls back to the coarse-resampled SLC. Geometric coregistration only. |
| 4 | `intermediate_files_removal_enabled: True` | `worker.` | **241.53 GiB** | **145.0** | **unsafe** with ionosphere and with coreg-only runs — see §4.5 item 8 |
| 5 | **Single pol** | `list_of_frequencies: {B: [HH]}` | 314.10 GiB | 72.4 | no HV interferogram. `process_single_co_pol_offset: True` already restricts *offsets* to HH, so this only affects coarse/fine/crossmul (3 × 8 B/px). |
| 6 | **Leave the six optional `rdr2geo` layers off** (they are off by default) | `rdr2geo.write_{incidence,heading,local_incidence,local_psi,simulated_amplitude,layover_shadow}` | — | avoids **+21 B/px = 56.34 GiB** on freq A, 7.04 GiB on freq B | nothing. **Nothing in the InSAR path ever reads them** — only `geo2rdr.py:89` (`topo.vrt`) and `ionosphere.py:800,1016` (a symlink) consume `scratch/rdr2geo`, and `layoverShadowMask.rdr` is never read despite the comment at `defaults/insar.yaml:376-378` claiming otherwise. |
| 7 | Larger `dense_offsets.skip_range/skip_azimuth` | `: 64` | −0.09 GiB | negligible | coarser offset field |
| — | **`crossmul.range_looks/azimuth_looks: 3,3`** | — | **0 GiB saved** | **negative** — RIFG grows 0.27 → 3.58 GB/pol on freq A | — |

Two floors you cannot move: `rdr2geo`'s `write_x/write_y/write_z` are silently
**forced back to True** (`insar_runconfig.py:51-64`) because
`Geo2rdr::geo2rdr` reads bands 1/2/3 of `topo.vrt` as x/y/height, so 24 B/px is a
hard floor; and `rdr2geo.write_height` (`schemas/insar.yaml:918`) is a **dead
key** — absent from the defaults, from the `layers` dict in `rdr2geo.py:99-106`,
and from the check list in `rdr2geo_runconfig.py:19`. Setting it does nothing.

(`bandpass_insar` is a no-op for this pair: `identification/isMixedMode` is the
byte string `b'False'` — not an integer — and both granules are DHDH at 40/5 MHz,
so `check_range_bandwidth_overlap` returns `{}`.)

### 5.5 `phase_unwrap` looks do **not** compose with `crossmul` looks

They are alternatives, not multipliers. `RUNW_writer.py:38-48` says so:

```python
unwrap_rg_looks = proc_cfg["phase_unwrap"]["range_looks"]
unwrap_az_looks = proc_cfg["phase_unwrap"]["azimuth_looks"]
# NOTE: unwrap looks here are the total looks on the RSLC, not on top of the RIFG
if (unwrap_az_looks != 1) or (unwrap_rg_looks != 1):
    self.igram_range_looks = unwrap_rg_looks
    self.igram_azimuth_looks = unwrap_az_looks
```

Mechanically (`unwrap.py:120-133`): if either unwrap look is > 1, it **re-runs
crossmul from scratch** off the full-resolution SLCs with `dump_on_disk=True`,
writing `crossmul/freq{F}/{pol}/wrapped_igram_rg{R}_az{A}` (CFloat32) and
`coherence_rg{R}_az{A}` (Float32), and unwraps *those* — the RIFG contents are
ignored. Cost: a second full crossmul pass, another `copy_raster` of
`reference.slc` at 8 B/px, plus 12 B per multilooked pixel.

**Keep `phase_unwrap.range_looks: 1` / `azimuth_looks: 1` (the defaults) and put
all looks in `crossmul`,** unless you deliberately want a fine wrapped RIFG plus
a coarser RUNW. `ionosphere.py:664-693` does exactly this collapse for its own
sub-workflow, moving the unwrap looks into crossmul and resetting unwrap to 1,1 —
so the ionosphere pass never pays for the double crossmul, but the main pass
still does.

### 5.6 Looks on Track G

Track G has no `crossmul` and no runconfig looks — multilooking happens in our
own `gslc_igram.py` (stage G3), on the map grid, after the conjugate product.

- **Freq A at 5 m posting: `az_looks = rg_looks = 8`** → a 40 m product,
  7545 × 7350 over the full pinned box (443 MB complex64, which fits in RAM for
  the filter/unwrap stage).
- **Freq B at 40 × 5 m posting: `rg_looks = 1, az_looks = 8`** → the same 40 m
  square grid.

**`snaphu.nlooks` is not `az_looks × rg_looks`.** It wants the *equivalent* number
of independent looks:

```
ENL = N · (pixel area) / (resolution-cell area),   capped at N
```

With `ρ_gr = c/2B / sin θ = 3.747 / sin 41.38° = 5.669 m` and
`ρ_a = v_g / B_az = 6766.6 / 1261.79 = 5.363 m`, the resolution cell is
**30.40 m²** against a 25 m² pixel, so **ENL ≈ 64 × 25 / 30.40 ≈ 53**.

That is self-consistent with §5.2: native spacing area 4.724 × 4.452 = 21.02 m²
over 30.40 m² is 0.691 — the same 0.6918 that gives `ILN = 0.6918 k_r k_a`.

**Do not compute this from
`isce3.product.get_radar_grid_nominal_ground_spacing`.** That function returns
nominal ground *spacing* (4.456 / 4.725 / 37.800 m), not resolution, and feeding
spacing into the formula inverts the ratio. An earlier draft did exactly that and
got 54 — close to the right answer, for entirely the wrong reason.

**UNVERIFIED: ρ_a.** 5.363 m follows from `processedAzimuthBandwidth`
= 1261.79 Hz. A 12 m antenna would suggest something nearer 6.5 m, which would
give ENL ≈ 43 instead of 53. The bandwidth-derived figure is used here because it
is the one that reproduces the measured 0.8301 azimuth oversampling ratio.
Settle it by measuring the impulse response width on a bright point target with
`nisar.workflows.gslc_point_target_analysis`.

---

## 6. Workflow catalogue

### (i) Runs today, with these two granules

| # | Workflow | Purpose | Course source | Entry point | In → Out | Feasible here? |
|---|---|---|---|---|---|---|
| 1 | **RSLC inspection** | HDF5 tree, granule parse, spectra, valid-sample decode, orbit plots | 3.3 `NISAR_RSLC_Tutorial.ipynb` | `h5py` script | RSLC → JSON + PNGs | **Yes.** Minutes, ~0 disk. Already partly done for §1. |
| 2 | **Baseline + coherence budget** | B⊥, h_amb, critical baseline, predicted γ | 1.3 | `isce3.geometry.geo2rdr` script | RSLC ×2 → table | **Yes.** Done — see §1.5. |
| 3 | **DEM + water mask staging** | ellipsoidal-height DEM over the AOI | S01, 2.1, 3.3 | `sardem` | bbox → GTiff | **Yes**, ~150 MB, minutes. |
| 3a | **TRACK G — GSLC per date** | model-driven coregistration onto a pinned geogrid | 2.1 `S1_GSLC_burst.ipynb` (COMPASS analogue) | `python -m nisar.workflows.gslc` | RSLC + DEM → GSLC | **Yes.** ~56 KB scratch; cost is *output* size. **Start here.** |
| 3b | **TRACK G — grid gate** | prove both dates landed on the same array | none — ours | `asc/compare/common_grid.py` | 2 GSLCs → pass/fail | **Yes.** Seconds. Mandatory before 3c. |
| 3c | **TRACK G — conjugate product** | interferogram + coherence with no resampling at all | 2.1 `utils.ifgram_and_coherence` | `asc/compare/gslc_igram.py` | 2 GSLCs → ifg/coh/unw | **Yes.** No ISCE3 CLI exists — see §2.4 stage G3. |
| 3d | **Track comparison** | decide which track to trust, and why they differ | none — ours | `asc/compare/compare_tracks.py` | both tracks → report | **Yes**, once both tracks have produced a pair. |
| 4 | **Geometry-only RIFG, freq B** | wrapped ifg + coherence, cheapest true product | S08 chain, run by ISCE3 | `python -m nisar.workflows.insar` | RSLC ×2 + DEM → RIFG | **Yes.** ~17 GB scratch. Track R starts here. |
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
| 15 | **Pixel offsets (ROFF/GOFF)** | range + azimuth offsets; survives decorrelation; gives azimuth | 3.3 GUNW `pixelOffsets`; S06 concept | `product_type: ROFF` or `GOFF` | RSLC ×2 → offsets | **Yes**, but see the `out_paths` trap in §2.4. Note ROFF/GOFF runs form **no interferogram at all**. |
| 16 | **autoRIFT offset tracking** | independent NCC template matching on amplitude | S06 | `autoRIFT` | amplitude pair → velocity field | **Yes**, but `autoRIFT` is not installed. The fallback if #5 shows coherence collapse. |
| 17 | **Geometry layer export** | incidence + azimuth rasters for modeling | 2.1 / 3.3 | `get_product_geometry.py` or MintPy `load_data` | RSLC/GUNW + DEM → GeoTIFF | **Yes** on freq B. On freq A the L1 path allocates full-radar-grid rasters — 23 GB each. |
| 18 | **Full-resolution freq A GUNW** | the actual 40 MHz science product | — | `nisar.workflows.insar` | → GUNW | **Track R: not on this disk without cropping.** See §8 risk 1. **Track G reaches frequency A at full frame** for 59.5 GiB of output and ~56 KB of scratch — that is the whole argument for building it. |

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

Track G's products are already in the shape dolphin wants: subdataset path
`/science/LSAR/GSLC/grids/frequencyA/HH` is what `h5_prep.py:540-541` emits;
`xCoordinates`/`yCoordinates` are attached as dimension scales with
`grid_mapping` set, so GDAL's netCDF driver reads
`NETCDF:"<file>":"//science/…/HH"` (`dolphin/io/_core.py:285`) directly. Strides
and window follow the posting — at 5 m × 5 m / 40 MHz use `--sy 4 --sx 4` with
`half_window 7, 7`; at 5 m × 10 m / 20 MHz, `--sy 4 --sx 2` with `7, 5`. For
frequency B at 40 × 5 m, `--sy 4 --sx 1` and asymmetric half-windows to keep a
comparable ground-area SHP window. One frequency and one polarization for the
**entire** stack — `discover_gslc_stacks.py:120-127` makes polarization part of
the stack key, so a frame imaged in three pol modes is three shallow stacks, not
one deep one.

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
actually exist and is it downloadable? See §8 risk 8.

---

## 7. Execution plan

Ordered so that each step produces something verifiable, and so that everything
cheap happens before the expensive compute. Every step has a checkpoint that must
pass before moving on.

**Track G runs first, and the reason is not preference — it is arithmetic.**
Track G's cost is set by the *output* geogrid, so an AOI shrinks it linearly and
it leaves ~56 KB of scratch behind; Track R's cost is set by the *input* radar
grid, which no runconfig key can reduce, and it commits 16.1 GiB on frequency B
before it produces anything. Track G therefore reaches a real interferogram —
including on **frequency A at full resolution**, which Track R cannot touch on
this disk — before Track R has finished `rdr2geo`. Do it in that order and the
first coherence number arrives days earlier and costs almost nothing.

Steps 3–5 are Track G. Steps 6–9 are Track R. Step 10 compares them, and is the
step the whole document exists to reach.

**Disk sequencing matters.** 81.8 GiB free, with 49 GiB of RSLC that must stay.
Full-frame Track G at 5 m is 59.5 GiB of output; full-frame Track R on
frequency B peaks at 16.1 GiB. Those two together are 75.6 GiB and leave nothing.
So: **run Track G on the AOI first** (a 60 × 60 km box at 5 m is 1.07 GB per pol
per date), keep the full-frame Track G run for after Track R's scratch has been
reclaimed, and never have both live at once.

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
mkdir -p L1_RSLC L2_GSLC aux/dem aux/water aux/orbits cfg scratch geometry model logs
mkdir -p pairs/20260613_20260625/{RIFG,RUNW,GUNW,trackG,compare,corrected}
mv NISAR_L1_PR_RSLC_*.h5 L1_RSLC/
```

**Checkpoint:** `ls -la $CASE/L1_RSLC/` shows two `.h5` files totalling 52.6 GB
and `df -h /` is unchanged.

---

### Step 1 — ingest and write `stack.json`

*Cost: ~1 min. Disk: kB.*

Write `asc/tools/ingest_rslc.py` to emit the `stack.json` in §2.4 by reading
`identification`, `swaths/frequency{A,B}`, and `metadata/orbit`. It must assert
that both granules agree on track, frame, direction, bandwidth mode and look side.

It must also **compute and freeze the geogrid**, because both tracks consume it
and nothing may re-derive it. Reproject each granule's `boundingPolygon` to the
target EPSG, intersect, and snap outward to the 600 m lattice. Measured:

```
cycle 022:  x [434081.3, 735715.0]  y [1054964.1, 1350739.6]
cycle 023:  x [434516.2, 736405.8]  y [1052948.3, 1348720.8]
intersection, snapped out to 600 m:
  top_left     (434400.0, 1348800.0)
  bottom_right (736200.0, 1054800.0)          301.8 x 294.0 km
```

Every candidate posting divides both extents exactly, so `_grid_size`'s
`np.round` has nothing to round and the snap step is a no-op. At 5 m that is
**60360 × 58800**, byte-identical on both dates. EPSG:32619 (UTM 19N): the frame
spans −69.60 … −66.84, entirely inside zone 19.

**Checkpoint:** `stack.json` exists and contains
`"wavelength_m": 0.241963`, `"direction": "A"`, `"look_side": "Left"`,
`"freq_b": {"present": true, ...}`, and a `geogrid` block with all five pinned
keys. If any of those is wrong, stop — every downstream number depends on them.

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

### Step 3 — TRACK G: generate and hand-edit the two GSLC runconfigs

*Cost: minutes. Disk: kB.*

Unlike InSAR, GSLC **does** have a config generator — but it fills in less than
you would hope, and it silently leaves the most expensive key blank.

```bash
D=$CASE/L1_RSLC
for d in 20260613:022:001 20260625:023:002; do
  IFS=: read date cyc ctr <<< "$d"
  conda run -n isce3_env python -m nisar.workflows.dumpconfig gslc \
    $D/NISAR_L1_PR_RSLC_${cyc}_162_A_007_4005_DHDH_A_${date}T*_P05023_N_F_J_${ctr}.h5 \
    -d $CASE/aux/dem/dem_t162_f007.tif \
    -o $CASE/L2_GSLC/${date}_gslc.h5 \
    --scratch-path $CASE/scratch/gslc_${date} \
    -e 32619 \
    --a-spacing 5 5 --b-spacing 40 5 \
    --x-snap 600 --y-snap 600 \
    --top-left 559800 1230000 --bottom-right 620400 1170000 \
    --flatten \
    --validate --out-runconfig $CASE/cfg/gslc_${date}.yaml
done
```

`dumpconfig.py:59-71` makes only the positional `rslc_file` and `-d/--dem`
required. What it fills in, verified by diffing its output against
`defaults/gslc.yaml`:

| key | filled by | source |
|---|---|---|
| `input_file_group.input_file_path` | positional arg (resolved to absolute at `:545`, written at `:613`) | dumpconfig.py |
| `dynamic_ancillary_file_group.dem_file` | `-d`, absolute, **existence not checked** | :617-618 |
| `product_path_group.sas_output_file` | `-o` | :630-632 |
| `product_path_group.scratch_path` | `--scratch-path` | :634-636 |
| `geocode.output_epsg` **and** `radar_grid_cubes.output_epsg` | `-e` (both together) | :707-709 |
| `geocode.output_posting.{A,B}.{x,y}_posting` | `--a-spacing` / `--b-spacing` | :724-733 |
| `geocode.x_snap`/`y_snap` **and** `radar_grid_cubes.x_snap`/`y_snap` | `--x-snap`/`--y-snap` (both together) | :662-672 |
| `geocode.top_left.*`, `bottom_right.*` | `--top-left` / `--bottom-right` | :674-688 |
| `processing.flatten` | `--flatten` / `--no-flattening` | :738-739 |
| `worker.internet_access`, `worker.gpu_enabled` | always, `False`/`False` | :741-742 |

What it leaves blank and **we must edit in by hand**:

- **`processing.input_subset.list_of_frequencies`** — blank means *all*
  frequencies and *all* polarizations. Write `{A: [HH]}`. This is the difference
  between 59.5 GiB and 105.8 GiB.
- `orbit_file`, `tec_file` — blank is correct for now (orbit comes from inside
  the RSLC, `gslc.py:117`; no TEC JSON means no ionospheric geolocation
  correction, `geocode_corrections.py:273`).
- `reference_gslc` — leave blank permanently; it is a stub.
- `radar_grid_cubes.{heights, output_posting, top_left, bottom_right}` — only
  `output_epsg` is touched. Blank `heights` defaults to
  `arange(-1000, 9001, 500)` = 23 levels (`runconfig.py:309-310`), and blank cube
  posting defaults to 500 m — 0.38 GB of cube per date. Seven heights at 1000 m
  posting is 0.028 GB and is plenty here.
- `output.*` and `processing.blocksize` — defaults only.

Also: `--nisar-defaults` computes `snap = Decimal(0)` (`dumpconfig.py:357/365`)
and then never writes it, because only the `elif x_snap is not None` branch
writes (`:664-666`). So `--nisar-defaults` leaves the snaps blank.

The blocks to hand-edit into each `cfg/gslc_<date>.yaml`, **identical in both
files except `input_file_path` and `sas_output_file`**:

```yaml
    dynamic_ancillary_file_group:
      dem_file: /home/sharath/.../aux/dem/dem_t162_f007.tif   # MUST cover the whole box
      orbit_file:                    # blank -> orbit from inside the RSLC
      tec_file:                      # blank -> NO ionospheric geolocation correction
      reference_gslc:                # LEAVE BLANK. Stub; logs "not implemented".

    processing:
      input_subset:
        list_of_frequencies:
          A: [HH]                    # MUST BE EXPLICIT. Blank = A+B x HH+HV.

      geocode:
        output_epsg: 32619           # MUST — identical on both dates
        output_posting:
          A: {x_posting: 5.0,  y_posting: 5.0}    # MUST
          B: {x_posting: 40.0, y_posting: 5.0}    # MUST — do NOT square B up
        x_snap: 600.0                # MUST set both or neither (geogrid.py:282-287)
        y_snap: 600.0                # and 600 divides both 5 and 40 (geogrid.py:289)
        top_left:     {x_abs: 559800.0, y_abs: 1230000.0}   # MUST — identical
        bottom_right: {x_abs: 620400.0, y_abs: 1170000.0}   # MUST — identical

      radar_grid_cubes:
        heights: [-500.0, 0.0, 500.0, 1000.0, 1500.0, 2000.0, 3000.0]
        output_epsg: 32619
        output_posting: {x_posting: 1000.0, y_posting: 1000.0}
        top_left:     {x_abs: 559800.0, y_abs: 1230000.0}
        bottom_right: {x_abs: 620400.0, y_abs: 1170000.0}
        # NOTE: do not set x_snap/y_snap here — runconfig.py:342-344 overwrites
        # them with the cube posting regardless of what you write.

      geo2rdr: {threshold: 1.0e-8, maxiter: 25}
      blocksize: {x: 1024, y: 1024}  # MUST (schema). GEO blocks, not radar.
      flatten: true                  # MUST. Irreversible if false — see §2.4 G3.
      correction_luts:
        solid_earth_tides_enabled: true    # default; range only

    output:
      data_type: complex64_zero_mantissa   # keep — see §2.4 G1
      compression_enabled: true
      compression_level: 1
      chunk_size: [512, 512]
      shuffle: true

    worker:
      gpu_enabled: false             # MUST — no GPU on this box
```

Do **not** raise `blocksize` above 1024. It is a *geo* block (8 MB/pol), but the
radar bounding box that `geo2rdr` must pull for it grows super-linearly with an
oblique geometry.

**Checkpoint:** schema validation passes for both, and — separately — the DEM
actually exists.

```bash
for f in $CASE/cfg/gslc_2026*.yaml; do
  conda run -n isce3_env python -c "
from nisar.workflows.dumpconfig import validate_runconfig
print('$f', validate_runconfig('gslc', open('$f').read()))"
done
ls -l $CASE/aux/dem/dem_t162_f007.tif        # yamale never checks this
conda run -n isce3_env python -c "
import yaml,sys
a,b=[yaml.safe_load(open(f))['runconfig']['groups']['processing']['geocode']
     for f in ['$CASE/cfg/gslc_20260613.yaml','$CASE/cfg/gslc_20260625.yaml']]
for k in ['output_epsg','output_posting','top_left','bottom_right','x_snap','y_snap']:
    assert a[k]==b[k], (k,a[k],b[k])
print('geogrid blocks identical: OK')"
```

**`validate_runconfig` is yamale on a string and never touches the filesystem**
(`dumpconfig.py:834-879`). It will happily bless a runconfig pointing at a DEM
that does not exist; the failure then surfaces at `helpers.check_dem`
(`runconfig.py:156`) or `isce3.io.Raster` (`gslc.py:58`). Check the DEM by hand.

---

### Step 4 — TRACK G: run `gslc` for both dates

*Cost: **UNVERIFIED**. Disk: ~2.6 GB for the AOI (1.16 GB/pol/date + 0.145 GB
mask/date), ~56 KB scratch.*

```bash
cd $CASE
for d in 20260613 20260625; do
  time conda run -n isce3_env python -m nisar.workflows.gslc cfg/gslc_${d}.yaml
done
du -sh scratch/gslc_20260613 L2_GSLC
```

The two runs are completely independent — no reference, no pair, no shared
state. That is the whole point of Track G, and it is why it parallelises across
dates and Track R does not.

**Scratch really is ~56 KB.** `grep -n "scratch\|rdr2geo\|topo" gslc.py` returns
nothing; the only scratch the workflow writes is in
`geocode_corrections.py:318-324`, on a **5 km-decimated** radar grid
(`_get_decimated_radar_grid`, `:21-63`): a 48 × 49 grid, `{x,y,z}.rdr` as GTiff
Float64, 18.4 KiB each — and only when `solid_earth_tides_enabled: true`.

**Output size scales with the box and the posting**, not with the input radar
grid. For the **full** pinned box (301.8 × 294.0 km):

| posting | width × length | 1 pol/date | HH ×2 dates | mask ×2 dates | **total ×2 dates** |
|---|---|---|---|---|---|
| **5 × 5 (freq A)** | 60360 × 58800 | 26.44 GiB | 52.89 GiB | 6.61 GiB | **59.50 GiB** |
| **40 × 5 (freq B)** | 7545 × 58800 | 3.31 GiB | 6.61 GiB | 0.83 GiB | **7.44 GiB** |
| 5 × 5, HH+HV | 60360 × 58800 | — | 105.77 GiB | 6.61 GiB | **112.4 GiB — does not fit** |

The mask is **not optional and not per-polarization**: `h5_prep.py:539,561-569`
creates one full-size `np.ubyte` layer per frequency per file unconditionally.
Budget it; earlier drafts of this document did not.

Realised on-disk will be well under the raw figure — 31% of the pinned box is
outside the footprint and `block_generator` (`gslc.py:159-164`) skips blocks with
no radar data, so those HDF5 chunks are never allocated, and
`complex64_zero_mantissa` + gzip-1 + shuffle compresses the rest. **Budget the
raw number and treat the saving as headroom.**

For the AOI in step 3 (60.6 × 60.0 km ⇒ 12120 × 12000), one pol per date is
1.08 GiB and the pair with masks is **~2.6 GiB**. Start there.

**Checkpoint:** both files exist, carry the expected subdataset, and have the
grid we asked for.

```bash
conda run -n isce3_env python -c "
import h5py
for d in ['20260613','20260625']:
    f=h5py.File(f'L2_GSLC/{d}_gslc.h5','r')
    g=f['/science/LSAR/GSLC/grids/frequencyA']
    print(d, f['/science/LSAR/GSLC/grids/frequencyA/HH'].shape,
          f['/science/LSAR/GSLC/grids/frequencyA/HH'].dtype,
          int(g['projection'][()]), float(g['xCoordinates'][0]), float(g['yCoordinates'][0]))
"
```

Both lines must be **identical apart from the date**. If they are not, the
geogrid was not pinned and step 5 will tell you so in stronger terms.

---

### Step 5 — TRACK G: grid gate, then the interferogram

*Cost: minutes. Disk: <1 GB at 8×8 looks.*

```bash
cd $CASE
conda run -n isce3_env python $ISCE3/asc/compare/gslc_igram.py \
  --ref L2_GSLC/20260613_gslc.h5 \
  --sec L2_GSLC/20260625_gslc.h5 \
  --subds /science/LSAR/GSLC/grids/frequencyA/HH \
  --az-looks 8 --rg-looks 8 --snaphu-nlooks 53 \
  --out pairs/20260613_20260625/trackG/
```

The script runs the **hard grid gate first** (§2.4 G2) and refuses to proceed on
a mismatch. Then, per row block: `ref · conj(sec)` → multilook by summing the
complex numerator → coherence as `|Σ ifg| / √(Σ|a|² · Σ|b|²)` (the ISCE3 crossmul
normalisation) → Goldstein → snaphu → GeoTIFF with a CRS.

`--snaphu-nlooks 53`, not 64 — see §5.6.

**Before importing `osgeo`, export `PROJ_DATA`** or the GeoTIFFs come out with no
CRS and no error:

```bash
export PROJ_DATA=$(conda run -n isce3_env python -c "import sys,os;print(os.path.join(sys.prefix,'share','proj'))")
```

**Checkpoint:** this is the **first real coherence number in the project**, and
it costs ~2.6 GB.

```bash
conda run -n isce3_env python -c "
from osgeo import gdal; import numpy as np
c=gdal.Open('pairs/20260613_20260625/trackG/coh.tif').ReadAsArray()
m=np.isfinite(c)&(c>0)
print('coh: median %.3f  frac>0.3 %.3f  frac>0.5 %.3f' % (
      np.median(c[m]), np.mean(c[m]>0.3), np.mean(c[m]>0.5)))
"
gdalinfo pairs/20260613_20260625/trackG/unw.tif | grep -E "Size is|Origin|Pixel Size|EPSG"
```

Interpretation — the same decision the old plan deferred to a 17 GB Track R run:

- median over land **> 0.4** → the phase path is viable for both tracks.
- **0.2–0.4** → marginal; continue, but budget the offset path as a parallel
  product.
- **< 0.2 over land** → 12-day L-band has decorrelated here. Pivot to pixel
  offsets and do not spend Track R's disk on phase.

Sanity anchor from the Zebker–Villasenor model at θ = 41.4°, λ = 0.241963 m
(γ_geom > 0.998 and γ_v ≈ 0.9995 here, so γ ≈ γ_snr·γ_t): σ_c = 1.0 cm → 0.943,
1.5 cm → 0.876, 2.0 cm → 0.790, 3.0 cm → 0.588, 5.0 cm → 0.229. Expected 12-day
medians: **bare/urban 0.6–0.85, savanna/pasture 0.4–0.6, closed tropical forest
0.25–0.45, water at the multilook floor.** A frame median of 0.35–0.55 with >35%
of land above 0.3 is a healthy result. The ~38% ocean reads at the floor
regardless — mask it before computing these statistics for real.

---

### Step 6 — TRACK R: hand-write the frequency-B runconfig

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
      intermediate_files_removal_enabled: False   # see §4.5 item 8

    processing:
      input_subset:
        list_of_frequencies:
          B: [HH]

      rdr2geo:
        lines_per_block: 256
        # leave every optional layer off: +21 B/px if enabled, and nothing
        # in the InSAR path ever reads them (§5.4 rank 6)
      geo2rdr:
        lines_per_block: 256

      # geometry-only: all four must be off together (insar_runconfig.py:32-49)
      dense_offsets:   {enabled: False}
      offsets_product: {enabled: False}
      rubbersheet:     {enabled: False}
      fine_resample:   {enabled: False}

      coarse_resample:
        lines_per_tile: 256
        columns_per_tile: 4096      # 0 means "all columns" — set it explicitly

      crossmul:
        range_looks: 2              # freq B is 8.5:1 anisotropic — see §5.2
        azimuth_looks: 17           # 2,17 -> 75.6 x 75.7 m, ILN 23.5
        flatten: True
        lines_per_block: 256

      phase_unwrap:
        range_looks: 1              # keep at 1,1 — otherwise crossmul runs
        azimuth_looks: 1            # a SECOND time from scratch (§5.5)

      baseline:
        mode: top_bottom

    logging:
      path: /home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc/logs/insar_freqB_geomonly.log
      write_mode: w
```

The looks are `2, 17`, not the `3, 11` an earlier draft used and not the `3, 3`
that was proposed for disk reasons. `2, 17` gives a **square** 75.6 × 75.7 m
ground pixel and ILN 23.5; `3, 11` gives 113 × 49 m (2.3:1) and `3, 3` gives
113 × 13 m (8.5:1). See §5.2, and §5.3 for why none of this affects disk.

**Coregistration-only variant.** If what you want is the coregistered secondary
and the offset fields *as deliverables* — not an interferogram — the mechanism is
`primary_executable.product_type: ROFF`. `h5_prep.get_products_and_paths`
(`h5_prep.py:92-141`) maps `'ROFF' → {'ROFF': path}` and nothing else, and every
stage from rubbersheet onward is gated on `'RIFG'`/`'RUNW'`/`'GUNW'` being in
`out_paths`. So rdr2geo, geo2rdr, `prepare_insar_hdf5` and `coarse_resample` run;
crossmul, unwrap and geocode are skipped. Peak **16.1 GiB**; end state 8.05 GiB of
geometry plus 2.68 GiB of coregistered SLC. Two things to know:

- `intermediate_files_removal_enabled` **must stay False** here.
  `insar.py:112-118` and `:126-131` are unconditional and delete
  `coarse_resample_slc` and `geo2rdr` at the end — i.e. exactly the deliverables.
- `prepare_insar_hdf5` for ROFF is not free: the product is ~**85 MiB** (82% of
  it a fully-populated `metadata/geolocationGrid` cube at (20, 556, 339)), and
  ROFFWriter runs an isce3 Topo pass over the pixel-offsets grid, leaving
  `scratch/rdr2geo/freqB/ROFF_offsets_dem.rdr` (1.38 MB, "Total convergence:
  344612 out of 344612" — exactly the 1706 × 202 offset grid). Negligible against
  16.1 GiB, but it is not a hollow skeleton.

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

Expect `OK ['B']`. Any `ValueError` here is a config bug, caught for free. Two
things this will catch that are easy to hit: `helpers.check_log_dir_writable`
(`helpers.py:334-350`) does `os.access(dirname, os.W_OK)` and fails with
`PermissionError` if `logs/` does not exist — **nothing creates it for you** —
and `prep_geocode_cfg` (`runconfig.py:248`) opens the DEM with
`isce3.io.Raster(...).get_epsg()` whenever `output_epsg` is None. `mkdir -p` the
`pairs/20260613_20260625/{RIFG,RUNW,GUNW}` and `logs/` directories first.

---

### Step 7 — TRACK R: dry-run the geometry stages alone

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
**5.76 GB**. Both coexist during `geo2rdr` (it reads `topo.vrt` while writing the
offsets), which is the 40 B/px geometry-only floor in §8 risk 1.

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

Do **not** delete `scratch/rdr2geo` before `geo2rdr` finishes:
`Geo2rdrRunConfig.yaml_check` calls
`check_mode_directory_tree(topo_path, 'rdr2geo', frequencies)` first. Deleting it
*after* drops the running peak from 16.1 to 13.4 GiB.

---

### Step 8 — TRACK R: full frequency-B RIFG

*Cost: **UNVERIFIED**, estimate 1–3 h. Disk peak ~17.3 GB (16.1 GiB) scratch.*

```bash
cd $CASE
conda run -n isce3_env python -m nisar.workflows.insar cfg/insar_freqB_geomonly.yaml
```

(It will re-enter at `coarse_resample` using the scratch from step 7, provided
`logs/insar_freqB_geomonly.log` is intact. Add `--restart` to force everything.)

**Checkpoint:** open the RIFG and look at the coherence — then compare it against
the number step 5 already gave you.

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

Expect shape **(3218, 3291)** at `2, 17` looks. The two tracks' coherence
medians should agree to within a few hundredths over the same ground; a large
gap is step 10's problem, not a reason to stop here.

Note that `scratch/RIFG.h5` and `scratch/RUNW.h5` (or `scratch/ROFF.h5` for
GOFF) live **inside** `scratch_path`, are compressed HDF5 — so
`output.compression_enabled` does affect scratch contents, contrary to the
"scratch is all flat binary" simplification — and are **never** removed by
`intermediate_files_removal_enabled`, which only `rmtree`s the named stage
directories. Roughly 12 B per multilooked pixel per pol before compression.

---

### Step 9 — TRACK R: dense-offset residual diagnostic

*Cost: **UNVERIFIED** — this is the no-GPU risk (§8 risk 3). Run it on frequency
B, where the grid is 8× smaller.*

This is the step that measures what Track R's geometry-only path is *not*
measuring, and it is also the input to step 10's "which track is wrong"
question.

Copy to `cfg/insar_freqB_dense.yaml`, set `dense_offsets.enabled: True` (leaving
`rubbersheet`/`fine_resample` off is **allowed** — the interlock forbids
rubbersheet-without-dense_offsets, not the reverse), then:

```bash
time conda run -n isce3_env python -m nisar.workflows.dense_offsets cfg/insar_freqB_dense.yaml
```

The offset grid is `((54720-104)//32, (6582-104)//32)` = **1706 × 202** for
frequency B (defaults win 64, half_search 20, skip 32, margin 0 ⇒
`margin_rg = margin_az = 104`); frequency A would be 1706 × 1642.

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

Judge on the **high-pass** MAD, against the table in §2.4 stage K: 0.10 samples
is the accept limit (γ_misreg 0.977), 0.25 is a warn, 0.50 is a reject. Ampcor on
64×64 windows at γ > 0.3 routinely reaches 0.02–0.05, so the limit is not tight.

If the median residual is well under 0.1 pixel, geometry-only coregistration is
confirmed adequate for this pair and the disk-saving choice in step 6 is
justified. If it is a **large constant**, that is a bulk timing/range bias and
the real run needs `rubbersheet` + `fine_resample` — which costs disk we do not
have on frequency A, and makes the crop mandatory.

If it is **smooth over kilometres in range while azimuth is white**, that is
ionospheric group delay, not misregistration. Do not "fix" it with rubbersheet;
carry it to step 10 and correlate it against the track-to-track ramp.

---

### Step 10 — compare the two tracks

*Cost: minutes. Disk: <1 GB.*

This is the step the document exists to reach. Both tracks now have an
interferogram over the same ground, formed by different mechanisms.

```bash
cd $CASE
conda run -n isce3_env python $ISCE3/asc/compare/compare_tracks.py \
  --pair 20260613_20260625 --posting 50 \
  --trackG pairs/20260613_20260625/trackG \
  --trackR pairs/20260613_20260625/RIFG/RIFG_20260613_20260625_freqB.h5 \
  --water aux/water/watermask_t162_f007.wbd \
  --out pairs/20260613_20260625/compare
```

Both tracks land on **one** 50 m map grid first. Match `N_eff`, not window size:
Track R at `2, 17` on freq B is ILN 23.5 in a 75.6 × 75.7 m cell; Track G at 5 m
posting with a 10×10 boxcar is ~82 in a 50 × 50 m cell. Either re-look Track G to
match or record the difference — it is expected difference #5 in §2.4 stage K,
not a failure.

When resampling: **never warp phase.** Warp cos/sin (or the complex pair) and
recombine; use `-r average` for coherence when downsampling — it is the only
resampler that preserves the mean — and `-r near` for masks and connected
components. `-te` is already snapped, so no `-tap`.

**Checkpoint:** the go/no-go table. Illustrative output, from the synthetic
end-to-end test (which carries a deliberate 4.58-fringe unmodelled-ionosphere
ramp and correctly catches it):

```
[PASS] T_G coherent land fraction (gamma>0.3)        0.770  (want > 0.35)
[PASS] T_R coherent land fraction (gamma>0.3)        0.725  (want > 0.35)
[PASS] T_G median land coherence                     0.426  (want 0.25 - 0.80)
[PASS] T_R median land coherence                     0.401  (want 0.25 - 0.80)
[PASS] T_G water coherence at ML floor               0.097  (want 0.097 +-0.02)
[PASS] T_R water coherence at ML floor               0.097  (want 0.097 +-0.02)
[PASS] sign convention                                conj  (want conj)
[PASS] residual / look-noise ratio                    0.97  (want < 2.0)
[PASS] residual circ std                         0.197 rad  (want < 0.8 rad)
[PASS] coherence KS distance                         0.078  (want < 0.25)
[FAIL] track-to-track ramp                    4.58 fringes  (want < 1.0 fringe)
```

Triage order, and it matters:

1. **Water floor** first. If either track is more than 0.05 above
   `√π/(2√N_eff)`, the two dates are not independently sampled and nothing else
   in the report means anything.
2. **Sign** second. Measured on 8×8 tiles, never globally.
3. Then: **ramp FAIL + residual PASS** = model asymmetry (TEC/SET applied as a
   timing shift on Track G, as an unapplied layer on Track R). **Not** a
   coregistration problem, and expected on this frame — 1 TECU is 2.2 fringes.
4. **residual FAIL + ramp PASS** = coregistration. Go back to step 9's offset QC.
5. **One track's coherent fraction FAIL** = that track did not coregister.
6. **Both FAIL** = the pair is decorrelated. That is the terrain, not the
   software.

Cramér–Rao floors at N_eff = 84, for reading the residual: γ = 0.2 → 0.378 rad
(7.3 mm); 0.3 → 0.245 (4.7 mm); 0.5 → 0.134 (2.6 mm); 0.7 → 0.079 (1.5 mm). At a
smaller N_eff they scale as `1/√N_eff`, so Track R at `2, 17` (N_eff 23.5) is
~1.9× looser than these figures. The water-floor target likewise comes from each
track's own N_eff — see the table in §2.4 stage K; the `0.097` in the sample
output above is the synthetic's N_eff = 84 value, not a universal constant.

**The acceptance test with real teeth**, if both tracks pass: quadtree-decompose
both and run the same Okada inversion (step 14). Two interferograms that agree to
0.2 rad residual but recover different fault parameters have a systematic that
the pixel statistics missed.

---

### Step 11 — TRACK R: frequency-B GUNW

*Cost: **UNVERIFIED**, estimate 1–3 h without ionosphere. Disk peak ~26 GB.*

Copy the config to `cfg/insar_freqB_full.yaml` and change:

```yaml
    primary_executable:
      product_type: GUNW
    product_path_group:
      sas_output_file: .../pairs/20260613_20260625/GUNW/GUNW_20260613_20260625_freqB.h5
    processing:
      phase_unwrap:
        algorithm: snaphu
        snaphu:
          cost_mode: smooth
          initialization_method: mcf
          ntiles: [4, 4]
          nproc: 8
      geocode:
        lines_per_block: 256
        interp_method: NEAREST        # for a like-for-like coherence histogram
                                      # against Track G; BILINEAR (the default)
                                      # narrows it — see §2.4 stage K, diff #4
        output_epsg: 32619            # pin all five, same as Track G
        x_snap:                       # BOTH blank — snapping after pinning
        y_snap:                       # would move the corners
        top_left:     {x_abs: 559800.0, y_abs: 1230000.0}
        bottom_right: {x_abs: 620400.0, y_abs: 1170000.0}
        output_posting:
          B: {x_posting: 80, y_posting: 80}
```

80 m posting matches the `2, 17` crossmul cell (75.6 × 75.7 m). Do not geocode a
75 m multilooked RIFG to 20 m and then average it back down.

**Ionosphere is deferred, deliberately.** `spectral_diversity: main_side_band`
requires frequency **A** in `input_subset`, and that reintroduces the full
2.881 Gpx grid for `rdr2geo`/`geo2rdr`/`coarse_resample` — the blocker in §8
risk 1. Two ways out:

- **(a)** `spectral_diversity: main_diff_low_high_subband` (the shipped default)
  splits frequency **A's own** band and requires B to be *absent* from
  `ionosphere_phase_correction.list_of_frequencies` — but still needs freq A in
  `input_subset`. Same disk problem.
- **(b)** Run the ionosphere pass on the **cropped** pair from step 12, where
  both bands fit.

Take (b). And never combine `ionosphere_phase_correction.enabled: True` with
`intermediate_files_removal_enabled: True` on 0.25.12 — `insar.py:49-53` deletes
`scratch/rdr2geo` right after `geo2rdr`, and `ionosphere.py:795-801, 1010-1017`
then symlinks to it.

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
unconditionally, `ionospherePhaseScreen` only if ionosphere was enabled, and
**no** tropospheric screens (that stage is off).

Then **step 11b, the manual QC that no script replaces**, and do it on **both**
tracks: plot the unwrapped phase, start in the far field where deformation is
zero, count fringes inward at **12.10 cm per fringe**, never trace through an
incoherent zone, and treat regions either side of any discontinuity as
independent zero-anchored profiles. A good interferogram has *contiguous,
closable* fringes; incoherent patches must be identifiable as patches, not
salt-and-pepper everywhere. At L-band you can count every fringe rather than
every fifth — and conversely, many fringes over a non-deforming area is
atmosphere or ionosphere, not signal, which is exactly why step 10 separates the
ramp term before judging agreement.

---

### Step 12 — the RSLC crop tool, and frequency A on both tracks

*Cost: development time, plus ~15 GB for the cropped pair.*

**Track G reaches frequency A without any of this.** The full pinned box at 5 m
is 59.50 GiB of output and ~56 KB of scratch; an AOI shrinks it linearly. If all
you want is a full-resolution frequency-A interferogram, run steps 3–5 again with
the full box (after Track R's scratch is reclaimed) and stop.

The crop tool is needed for **Track R** on frequency A, and for the ionosphere
pass. It is stage C: net-new code, no equivalent anywhere in ISCE3 (searched
`nisar/workflows/` — 69 modules, nothing subsets an RSLC; `bandpass_insar.py` and
`split_spectrum.py` rewrite the *spectrum* at full extent;
`isce3.core.crop_external_orbit` crops an orbit XML; `$CONDA_PREFIX/bin` has
nothing). `nisar/products/writers/SLC.py` is a full RSLC writer with
`update_swath`, `set_orbit`, `set_attitude`, `copy_identification` — the right
library to build on, but it has no crop entry point.

What a correct crop of azimuth rows `[a0:a1)` and range columns `[r0:r1)` must
do:

| | dataset | action |
|---|---|---|
| A | `swaths/zeroDopplerTime` | slice `[a0:a1]`. **This is at the `swaths` level, shared by A and B** (`Serialization.h:116` reads it from `group`, not `fgroup`) — so an azimuth crop applies to *both* frequencies' images, or frequency B must be deleted. Preserve the `units` attribute verbatim; `getRefEpoch` parses the reference epoch from it. |
| B | `swaths/frequency{F}/slantRange` | slice `[r0:r1]`; `slantRangeSpacing` unchanged |
| C | `swaths/frequency{F}/{HH,HV}` | slice `[a0:a1, r0:r1]`, keep `complex64`, gzip, (512,512) chunks |
| D | `swaths/frequency{F}/inputDataExceptionMask` | same 2-D slice (`uint8`) |
| E | `validSamplesSubSwath{i}` | slice rows `[a0:a1]` **and shift values by `-r0`, then clip to `[0, r1-r0]`**. `Serialization.h:139-148` **hard-throws** if `image_dims[0] != t_array.size()`. The values are *absolute column indices* and drift line to line — measured: freq A `[1491, 52472]` → `[1610, 52593]`; freq B `[0, 6544]` → `[16, 6559]`. `rubbersheet.subswath_mask_apply_enabled` defaults True and consumes this. |
| F | `metadata/orbit/*`, `metadata/attitude/*` | **copy unchanged.** 25 state vectors and 172 attitude records span the whole take; `check_radargrid_orbit_tec` requires the orbit to *bracket* the grid, so trimming risks failing that check for zero benefit. |
| G | `metadata/processingInformation/parameters/frequency{F}/*` | **copy unchanged.** A coarse 171×108 `LUT2d`; trimming risks putting the crop outside its support. |
| H | `identification/{zeroDopplerStartTime, zeroDopplerEndTime}` | rewrite to match the new `zeroDopplerTime[0]`/`[-1]`; **recompute `boundingPolygon`** with `isce3.geometry.get_geo_perimeter_wkt` |
| I | `metadata/geolocationGrid/*` | slice the (20, 556, 339) cubes on axes 1 and 2, or regenerate. **Not consumed by the 0.25.12 InSAR path** — `InSAR_L1_writer.py:60-118` *recomputes* it from the cropped radar grid; the copy path (`h5_prep.py:296-302`) is only reachable from the legacy `h5_prep.run`. But a stale cube fails RSLC spec validation. |
| J | dropping frequency B | also delete `metadata/processingInformation/parameters/frequencyB`, `metadata/calibrationInformation/frequencyB`, `metadata/RFI/frequencyB`, and set `identification/listOfFrequencies` to `[b'A']` |
| K | `metadata/calibrationInformation/*`, `metadata/RFI/*`, `processingInformation/{algorithms,inputs}` | not read by the InSAR path — copy or drop freely |

Implementation shape: ~150 lines of h5py, no ISCE3 dependency for the bulk.
`shutil.copy` is wrong (26 GB); open the source read-only, create the destination
with `h5py`, `visititems` everything not in the crop set with attrs copied
verbatim (the largest non-image dataset is `RFI/.../hitCount`, 149×105×1024
float32 ≈ 64 MB), and stream the images in ~512-line azimuth blocks.

**Sizing the crop.** Peak = `B_per_px × N_px`, with `B_per_px` = **40**
(geometry only), **64** (full chain, 1 pol, removal on), **80** (2 pol). Against
81.8 GiB free with the RSLCs resident, target ≤ 75 GiB working:

| Freq A configuration | max px | % of frame | e.g. |
|---|---|---|---|
| geometry only (rdr2geo + geo2rdr coexist) | 2.01 Gpx | 69.9% | 46000 az × 43700 rg |
| full chain, 1 pol, removal ON | 1.26 Gpx | 43.7% | 36000 az × 34900 rg |
| full chain, 2 pol, removal ON | 1.01 Gpx | 34.9% | 32000 az × 31500 rg |

In practice pick an AOI, not a fraction. A **16384 × 16384** crop is 0.268 Gpx ⇒
**16.0 GiB** peak for the full chain at 1 pol — the same budget as full-frame
frequency B, and it fits alongside a freq-B run. It spans 73.0 km along-track ×
51.2 km of *slant* range, which is **≈77.4 km on the ground** (16384 × 4.725 m,
not 16384 × 3.1228 m — do not size an AOI off the slant spacing).

Frequency B must be cut at exactly ⌊A/8⌋ range indices, or the sideband
ionosphere method will not line up. Note the sample counts do **not** divide
evenly: 52649 / 6582 = 7.99893.

**Checkpoint:** the cropped RSLCs open cleanly and are self-consistent. This one
call is the acceptance test — if the sub-swath arrays are wrong, `getRadarGrid`
throws `RuntimeError` with the "valid-samples arrays for sub-swath" message.

```bash
conda run -n isce3_env python -c "
from nisar.products.readers import SLC
s=SLC(hdf5file='L1_RSLC_crop/ref_20260613_az0-16384_rg0-16384.h5')
rg=s.getRadarGrid('A')
print(rg.length, rg.width, rg.sensing_start, rg.starting_range, rg.lookSide)
print(s.getOrbit().start_datetime, s.getOrbit().end_datetime)
s.getDopplerCentroid(frequency='A')
"
```

The orbit must still bracket the (shortened) data take.

Then run Track R on the crop, with
`list_of_frequencies: {A: [HH], B: [HH]}`,
`ionosphere_phase_correction: {enabled: True, spectral_diversity: main_side_band}`,
and `crossmul: {range_looks: 16, azimuth_looks: 17}` (§5.2) with geocode posting
80 m to match.

**Checkpoint:** compare the freq-A and freq-B unwrapped phase over the same
ground. They are independent measurements at different resolutions; where both
are coherent they must agree to within the ionospheric difference between
1239.0 and 1293.5 MHz. Large disagreement means one of them is mis-unwrapped.

---

### Step 13 — corrections, geometry export, contract C

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

Then stage F (subtract the screens) and stage G (gdalwarp everything to
EPSG:4326 on one grid and write `model.json`). Note the asymmetry: **Track R's
GUNW carries correction screens to subtract; Track G's product carries none**,
because its SET/TEC corrections were geolocation-only and its troposphere was
never modelled. `model.json` must record `"track": "G"` or `"R"` so a consumer
knows which.

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

### Step 14 — modeling, if there is anything to model

*Cost: minutes once installed.*

Requires `kite` and `okada_wrapper`, neither of which is in `isce3_env` today.
Install into a **third** env rather than perturbing the two verified ones.

Only worth doing if step 11b turns up a real deformation signal. Otherwise stop
at step 13 with a validated coseismic-ready pipeline and no event.

If both tracks passed step 10, run the inversion on **both** contract-C exports
and compare recovered fault parameters. That is the acceptance test the pixel
statistics cannot give you. It is also the point at which the quadtree's own
argument applies: the data are highly spatially correlated, so do the comparison
at 50 m and not at 5 m — which is why step 10's residual test is a radial
autocorrelation, measuring exactly the correlation length quadtree exploits.

---

### Step 15 — record and commit

Commit `stack.json`, `pair.json`, `geometry.json`, `model.json`, every file in
`cfg/` (both tracks), the step-10 `compare.json`, and the logs. Not the `.h5`
files, not `scratch/`, not `aux/`.

Amend §8 of this document with what actually broke.

---


## 8. Risks and open questions

Ranked by how likely they are to stop us.

### 1. BLOCKER (Track R only) — Disk. Frequency A on Track R does not fit, by ~3.4×.

**Scope first: this risk is Track R's, not the project's.** Track G's cost is
*output* size — 59.50 GiB for the full pinned box at 5 m on both dates, ~56 KB of
scratch, and linear in the AOI. Track R's cost is *input* radar-grid size, which
no runconfig key reduces. Frequency A at full resolution is therefore reachable
today via Track G and unreachable via Track R. That asymmetry is the reason §7
runs Track G first.

The scratch dtypes are hardcoded and verified in the source:
`rdr2geo` x/y/z are `gdal.GDT_Float64` (`rdr2geo.py:99-101`);
`geo2rdr` offsets are `GDT_Float64`, flat ISCE format (`Geo2rdr.cpp:37-40`);
resampled SLCs are `GDT_CFloat32`; rubbersheet writes full
`ref_radar_grid.width × .length` at `GDT_Float64`. All at **full resolution**,
uncompressed, unchunked.

Stock runconfig, frequency A, one co-pol, nothing removed
(2 880 953 280 px; decimal GB):

| Scratch item | B/px | Size |
|---|---|---|
| `rdr2geo/freqA/{x,y,z}` Float64 | 24 | 69.15 GB |
| `geo2rdr/freqA/{range,azimuth}.off` Float64 | 16 | 46.10 GB |
| `coarse_resample_slc` CFloat32 | 8 | 23.05 GB |
| **`rubbersheet_offsets` Float64 ×4** | **32** | **92.19 GB** |
| `fine_resample_slc` CFloat32 | 8 | 23.05 GB |
| `crossmul/freqA/{pol}/reference.slc` CFloat32 | 8 | 23.05 GB |
| `dense_offsets` (11 bands, skip 32) + offset-grid rasters | — | 0.16 GB |
| **Total** | | **≈ 299.8 GB (279.20 GiB)** |

**Two corrections to the version of this table that earlier drafts carried.**
First, rubbersheet is **32 B/px, not 16**: `rubbersheet.py:405-422` writes
*four* full-reference-grid Float64 rasters per offset pol —
`resampled_{az,rg}_offsets` **and** `azimuth.off`/`range.off` (the latter via
`sum_gdal_rasters`, whose `np.float64` default applies) — on top of the two
offset-grid `culled_*` rasters. That correction alone moves the old 207 GB total
to **253.6 GB**. Second, the old table omitted the `crossmul` `reference.slc`
copy (`crossmul.py:152-153`, inside the per-pol loop) and the offset-grid
rasters; adding those gives the 299.8 GB above. The two are independent errors,
not one.

**Peaks, not sums.** Per-pixel-of-full-grid, independent of frequency:

| Configuration | Peak B/px | Where the peak occurs |
|---|---|---|
| geometry only (rdr2geo + geo2rdr coexist) | **40** | during `geo2rdr` — it reads `topo.vrt` while writing the offsets |
| full chain, 1 pol, removal ON | **64** | at `rubbersheet` (16 geo2rdr + 8 coarse + 8 dense + 32 rubbersheet) |
| full chain, 2 pol, removal ON | **80** | at `fine_resample`, before the coarse dir is dropped |

Scaling, against **81.8 GiB usable free** (not the 94 GB earlier drafts
used — that figure was `f_bfree`, which includes root-reserved space):

| Configuration | Peak, removal OFF | Peak, removal ON | Fits in 81.8 GiB? |
|---|---|---|---|
| **Track G, full box, freq A, HH, 2 dates** | **59.50 GiB output, ~56 KB scratch** | n/a | **yes** |
| Track G, 60 × 60 km AOI, freq A, HH, 2 dates | 2.6 GiB | n/a | yes, trivially |
| Track G, full box, freq A, **HH+HV** | 112.4 GiB | n/a | **no — never leave `list_of_frequencies` blank** |
| Track R, freq B, coreg-only (`product_type: ROFF`) | **16.10 GiB** | *destroys the outputs — do not* | yes |
| Track R, freq B, geometry-only RIFG | **17.3 GB / 16.1 GiB** | — | yes, comfortably |
| Track R, freq B, HH, full chain | **34.90 GiB** | 21.49 GiB | yes |
| Track R, freq B, HH+HV, full chain | 42.95 GiB | 26.84 GiB | yes |
| Track R, freq A, geometry-only, full frame | **107.32 GiB** | — | **no** |
| Track R, freq A, HH, full chain, full frame | **279.20 GiB** | 171.87 GiB | **no** |
| Track R, stock defaults (GUNW, A+B, HH+HV) | **386.55 GiB** | 241.53 GiB | **no** |
| Track R, freq A crop 16384 × 16384, full chain 1 pol | **16.0 GiB** | — | yes |

Running trace, stock defaults with removal ON (GiB live):
`rdr2geo 72.44 → geo2rdr 120.74 → [rm rdr2geo] 48.30 → coarse 96.59 →
dense 120.87 → rubbersheet 217.51 → [rm dense] 193.23 → fine 241.53 ← PEAK →
[rm coarse] 193.23 → crossmul 241.53`.

**Frequency A does not fit on Track R at any setting.** Even the bare geometry
pair is 40 B/px = 107.32 GiB against 81.8 GiB free. Cropping is mandatory and no
runconfig key changes that. The levers that *do* work, ranked, are in §5.4.

**Mitigations, in order:** (a) **Track G**, which sidesteps the whole problem;
(b) frequency B on Track R — it exercises the entire RIFG→RUNW→GUNW chain at
~1/8 the cost; (c) crop for frequency A on Track R; (d) free disk or attach
external storage. Full-frame frequency A on Track R realistically wants
**~300 GB free**.

Also: always set `product_path_group.scratch_path` explicitly. The default is
`.`, so scratch lands wherever you happened to invoke from. And note that
`scratch/RIFG.h5` / `scratch/RUNW.h5` (or `scratch/ROFF.h5` for GOFF) live inside
`scratch_path`, are compressed HDF5 rather than flat binary, and are **never**
removed by `intermediate_files_removal_enabled` — that flag only `rmtree`s the
named stage directories.

And one interaction to avoid entirely: on this **installed** build, `insar.py:49-53`
deletes `scratch/rdr2geo` immediately after `geo2rdr` (the source tree defers it
to the end). But `ionosphere.run` at stage 13 symlinks `{scratch}/rdr2geo` into
its own scratch. **Never set `intermediate_files_removal_enabled: True` while
running ionosphere correction on 0.25.12** — you get a dangling symlink. There is
a second, undocumented removal too: `insar.py:89-93` deletes
`coarse_resample_slc` right after fine resampling and **before** crossmul, so
with removal on you cannot fall back to the coarse-resampled SLC. And
`insar.py:112-118` / `:126-131` are unconditional: on a coregistration-only run
they delete `coarse_resample_slc` and `geo2rdr`, which *are* the deliverables.

### 1b. SETTLED — Multilooking cannot reduce coregistration disk. Do not re-propose it.

Recorded here because it is an intuitive idea, it was proposed for exactly the
right reason (risk 1), and it is wrong in a way that is invisible from the
runconfig.

**The proposal:** set `crossmul.range_looks: 3, azimuth_looks: 3` instead of
`1, 1` to cut the disk cost of coregistration by ~9×.

**Why it cannot work.** `range_looks` / `azimuth_looks` are consumed **only** at
`crossmul.py:40-69`, `geocode_insar.py`, `ionosphere.py` and `gcov.py`.
**VERIFIED: they are not read by `rdr2geo`, `geo2rdr`, `resample_slc_v2`,
`dense_offsets` or `rubbersheet`** — every stage that produces the big scratch.
And `crossmul` runs *after* all of them in `insar.py`'s stage order. The peak
occurs at `rubbersheet`/`fine_resample`, before crossmul exists. **Zero bytes
saved.**

**It is worse than neutral.** On frequency A the RIFG grows from 0.27 GB/pol at
`11, 11` to **3.58 GB/pol** at `3, 3` — a 13× increase in output.

**And the obvious fallback is not there either.** `grep -nE 'decimat|skip|stride'
rdr2geo.py geo2rdr.py` in the installed 0.25.12 InSAR path returns **nothing**.
There is no radar-grid decimation option. `dense_offsets.skip_range/skip_azimuth`
decimate the *offset* grid only — worth ~0.09 GB on frequency A.

**Separately, `3, 3` is a bad looks choice on its own merits**, independent of
disk: on frequency B it produces a **113.4 × 13.4 m** pixel (8.5:1 anisotropic,
because freq B's ground-range spacing is 37.8 m against 4.45 m in azimuth), and
on either frequency ILN = 6.2 gives a strongly biased coherence estimate. The
correct settings are **A: `16, 17`** and **B: `2, 17`** — see §5.2. Never square
up frequency B.

**The levers that do reduce disk are ranked in §5.4.** The three that matter:
run **Track G** (cost becomes a function of output posting, not input grid),
**crop** the RSLC, and use **frequency B**.

### 2. BLOCKER — RAM. 12.7 GiB total, ~3.9 GiB free right now.

`free -m` currently reports total 13 016 MiB, used 9 078, available **3 937**,
swap 4 095 MiB with 1 509 used. Something is holding 9 GB.

`lines_per_block: 1000` against a 52 650-sample frequency-A line is 421 MB per
full-width 8-byte buffer; `rdr2geo` holds ~5–6 of them (~2.5 GB) and crossmul is
worse (two SLC blocks plus deramp/FFT workspace, several GB). At
`lines_per_block: 256` one CFloat32 block is 108 MB, `rdr2geo` totals ~0.65 GB and
resample ~0.43 GB — comfortable. All the runconfigs in §7 set 256.

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
Settle it with step 9 of §7 on frequency B, and extrapolate by pixel count
(freq A is ~8× more correlation windows).

If it is intolerable, the geometry-only path is the fallback — justified here by
B⊥ ≈ 31 m and a 12-day repeat — at the cost of losing the residual measurement.
That is risk 5 in §4.4, and step 9 exists specifically to bound it.

**Track G is unaffected**: it has no ampcor step at all. But that cuts both ways
— Track G also has no *measurement* of its own residual, which is why stage K
manufactures one by chip-wise amplitude correlation between the two GSLCs. If
CPU ampcor turns out to be prohibitive, Track G becomes the primary path and the
comparison in step 10 loses its independent arbiter; say so explicitly rather
than quietly trusting the surviving track.

### 4. HIGH — The DEM does not exist yet, and NISAR sourcing is untested here.

**`$CASE/aux/dem/` does not exist.** The case-study directory currently holds
only the two RSLC `.h5` files. Nothing in either track can run until this is
staged, and **nothing will warn you early**: `dumpconfig` resolves `dem_file` to
an absolute path without requiring existence (`:617-618`), and yamale schema
validation never touches the filesystem. The failure surfaces at
`helpers.check_dem` (`runconfig.py:156`), at `isce3.io.Raster(dem_file)`
(`gslc.py:58`), or — for Track R with `output_epsg` unset — at
`prep_geocode_cfg` (`runconfig.py:248`). Every runconfig quoted in §7 is
schema-valid and un-runnable until the DEM is on disk.

Once the geogrid is fully pinned, the DEM's own grid and EPSG are irrelevant to
the output geogrid (`geogrid.py:132-174` is skipped); only its **coverage** of
the pinned 301.8 × 294.0 km box matters. Cover it generously.

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
long list of datasets that must stay mutually consistent (see §2.4), and getting
it subtly wrong produces a product that opens fine and geolocates wrong.

**Mitigation:** validate with the `nisar.products.readers.SLC` round-trip in
step 12, and cross-check the cropped product's `boundingPolygon` against a
`rdr2geo` run on a handful of corner pixels.

**Scope:** this is a **Track R** requirement only. Track G needs no crop —
`block_generator` (`gslc.py:159-164`) reads only the RSLC blocks that map into
the AOI geogrid, so shrinking the AOI shrinks the work. If the crop tool proves
harder than expected, frequency A is still reachable, via Track G.

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

The single highest-value addition (§6(iii)) is an ascending+descending pair, not
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

Not a blocker until step 14, and step 14 is conditional on there being an event
worth modeling.

### 10. MEDIUM — Runtime is entirely unbounded.

Every time estimate in §7 is a guess. There is no benchmark anywhere for
`nisar.workflows.insar` on 16 CPU cores with no GPU at this grid size.

**UNVERIFIED: end-to-end wall time for the frequency-B chain.** Step 7 gives the
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
  successive versions of one pipeline. **We build both** — Track R and Track G,
  §2.2 — and compare them (§2.4 stage K, §7 step 10).
- **Multilooking saves no coregistration disk.** Looks are consumed only at
  `crossmul`, which runs after every coregistration stage. `3,3` saves 0 bytes
  and costs 3.3 GB of extra RIFG on frequency A. There is no radar-grid
  decimation option in 0.25.12. See risk 1b and §5.3.
- **Frequency A is near-square natively (1.06 at mid swath); frequency B is
  8.5:1 anisotropic.** Freq B wants range:azimuth looks ≈ 1:8, never 1:1. §5.1.
- **`k_r · k_a` is not the independent-look count.** ILN = 0.6918 · k_r · k_a for
  both frequencies; the data are ~1.2× oversampled on both axes. §5.2.
- **Usable free disk is 81.8 GiB, not 94 GB.** The larger figure was `f_bfree`,
  which counts root-reserved space.
- **`x_snap`/`y_snap` do not pin a geogrid.** They give a common lattice, not a
  common array. All five keys — EPSG plus four corners — or nothing. §4.5.
- **`flatten: true` on GSLC is not optional.** NISAR's writer discards
  `flatten_phase_block`, so `flatten: false` is irreversible from the product.

Two open items that the two-track structure adds:

**UNVERIFIED: `asc/compare/` has never been run against real ISCE3 output.**
Every script in it is self-tested end to end, but only on synthetic pairs. The
first real run is step 10, and the most likely failure is a units or layer-path
mismatch when reading the RIFG, not a numerical one.

**UNVERIFIED: how far apart will the two tracks actually land on this frame?**
The dominant predicted difference is ionospheric — Track G applies TEC as a
timing shift (and only if a `tec_file` is supplied, which by default it is not),
while Track R ships it as an unapplied phase layer. At 10° N, post-sunrise, 1–10
TECU of differential TEC is 14–140 rad of smooth ramp. If step 10 reports a ramp
of several fringes with a passing residual, that is the expected answer, not a
bug — but it means neither track's *absolute* phase is usable until stage F runs.
