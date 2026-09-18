# NISAR InSAR production pipeline: design decision (2026-09-15)

> **SUPERSEDED IN PART (2026-09-18).** Revision 2 below specifies dolphin for the time series with MintPy on the GUNW stack
> as an independent check. **That is not what shipped.** `nisar_timeseries.py` forms its own interferograms from the
> coregistered stack, unwraps them with snaphu in tiles, applies the GUNW corrections and inverts with MintPy; dolphin has
> never been run in this project and nothing independently cross-checks the inversion. What the chain does verify is
> interferogram formation (against ISCE3's own RIFG, on every pair sharing the stack reference) and unwrapping consistency
> (whole-cycle closure over triplets). The decision was deliberate — a crop-first single-reference stack with GUNW
> corrections made a plain pair network sufficient and directly verifiable — but it was never written back here.
> For what actually runs, read [TIMESERIES_MODULE.md](TIMESERIES_MODULE.md). The sections below remain the reference for
> stage costs and for the evidence behind the crop-first and GUNW-corrections choices.


> **Revision 2 (2026-09-15, later the same day) — supersedes the decision below.** Full-tile processing
> proved too storage- and compute-heavy for routine use: one full-tile pair needs ≈ 333 GiB scratch and ≈ 10 h. The user
> chose instead:
>
> 1. **Crop-first RSLC** to the AOI (`tools/rslc_subset.py`, zero Doppler) for coregistration, the wrapped interferogram and
>    coherence. This leg was shown equivalent to the full tile (phase R 0.994, coherence identical; COMPARISON §3.1–3.2).
> 2. **Ionosphere, troposphere (wet + hydrostatic) and solid-earth tides from the NISAR GUNW** of each pair, not computed by us.
>    GUNW's ionosphere matches our full-tile screen in shape to 0.0135 TECU; its level differs by one joint cycle, a
>    constant that cancels after referencing (COMPARISON §3.8). The troposphere and tides come as 3-D cubes
>    (`metadata/radarGrid/{wet,hydrostatic}TroposphericPhaseScreen`, `slantRangeSolidEarthTidesPhase`, 500 m posting, 20
>    heights), evaluated at each pixel's position and DEM height.
> 3. **Unwrapping and time series inside the AOI** with dolphin (phase linking + snaphu + inversion) on a single-reference
>    coregistered stack, with MintPy on the GUNW stack as an independent check. Unwrap AOI: `glof_bigger_aoi.kml` (1642 km²).
>    The rule was to fall back to the glacier-zone bbox + 100% buffer if it were too big; it is not (the crop chain took
>    1 h 29 m, 34 GB). The glacier zone alone (0.62 km²) is too small to reference against.
> 4. **Post-event scenes** are coregistered onto the same reference grid for event interferograms and coherence, but kept
>    out of the time series.
>
> First case: ascending T98/F16; pre-event 0620, 0702, 0714, 0726, 0819; post-event 0831, 0912. Coregistration runs
> through the module `nisar_coreg.py` with `coreg_configs/nepal_nisar_ascending_rslc.yaml` (reference = middle date 20260726,
> validated parameters in `coreg_configs/defaults.yaml`; see [COREG_MODULE.md](COREG_MODULE.md)). The sections below record the
> earlier full-tile decision and its evidence. They remain the reference for what each stage costs.

Status: **decided** by the user after the Nepal GLOF four-workflow study. This document is the specification the automation is built
from. The evidence behind each choice is in [COMPARISON.md](COMPARISON.md), [WF1_RSLC_FULL_TILE.md](WF1_RSLC_FULL_TILE.md) and the team report
(`case_studies/nepal_glof/comparison_v2/report/`). Stage timings are in `comparison_v2/report/rslc_1x1_stage_timings.html`.

## 1. Decision

**Process every pair on the full RSLC tile, through coregistration, the wrapped interferogram with coherence, and the split-spectrum
ionosphere. Export those products. Unwrap at fine looks only inside a user-supplied AOI.**

Why, in one line each:

| choice | evidence |
|---|---|
| RSLC, not GSLC | GSLC dates are registered by geometry only: ~5% coherence loss and a carrier phase term, p95 ≈ 17 mm. They cannot be realigned after delivery (COMPARISON §3.6). |
| full tile for the ionosphere | A crop's screen matches the shape (0.003 TECU) but its absolute level is unreferenced. It landed one freq-B cycle (5.35 TECU) off, and the class rule lands one joint cycle off. A single full-tile solve per pair gives every AOI the same level, and slicing it onto an AOI reproduces the benchmark exactly (§3.7). |
| wrapped + coherence on the full tile | Coregistration is full-resolution whatever the looks, so the full-tile run is paid anyway. Crossmul adds only 18–42 min. |
| unwrap only in an AOI | The full-tile 1×1 unwrap costs 14 h 46 m (prep 1 h 56 m + snaphu 12 h 50 m), more than everything else combined. The 9×8 full-tile unwrap the ionosphere needs costs 23 min. |

Not chosen: crop-first RSLC. It is interferometrically equivalent (phase R 0.994, coherence identical) and ~6× cheaper, but gives an unreferenced
ionosphere level per crop. It remains valid for one-off AOI work where only relative deformation matters.

## 2. Stages

```
RSLC ref + sec (full tile, freq A + B)
  │
  ├─ ingest, DEM staging, runconfig (+ disk bill gate, overlay check)                         seconds–minutes
  │
  ├─ ISCE3 insar, freq A, product_type RUNW
  │    rdr2geo                        1 h 39 m   full resolution
  │    geo2rdr                           6.6 m
  │    product prep                   2 h 05 m   at 1×1 looks (DEM pass 1 h 37 m); ~4 m at 9×8
  │    coarse resample                  54 m
  │    dense offsets                  2 h 24 m   skip 32 (tunable, §5)
  │    rubber sheet                     32 m
  │    fine resample                    12 m
  │    crossmul at export looks       42 m (1×1) / 18 m (9×8)          → RIFG: wrapped phase
  │    unwrap freq A at 9×8             23 m      (needed by the ionosphere)
  │    ionosphere main_side_band:
  │       freq-B rdr2geo 13 m, geo2rdr 41 s, prep 15 m, resample 7 m, crossmul 2 m, unwrap 50 s
  │       solve + Gaussian filter + unwrap-error correction + write   27 m → RUNW 9×8: ionospherePhaseScreen
  │
  ├─ coherence: tools/slc_coherence.py --win 3 on the coregistered SLCs            ~10 m
  │    (ISCE3 writes coherence ≡ 1 at 1×1; skip this if export looks > 1)
  │
  ├─ export to GCS (§4); QA summary
  │
  └─ optional: AOI unwrap
       subset RIFG (+ coherence) to the AOI radar window → snaphu at the chosen looks
       → slice the full-tile 9×8 screen to the AOI and interpolate it to the AOI looks
       → corrected unwrapped phase
```

Budget on the 8-core / 31 GB VM, measured stage by stage and not yet run as one job: **≈ 10 h 25 m** with 1×1 export
(8 h 34 m to the RIFG + 1 h 50 m unwrap-9×8 and ionosphere), plus ~10 min of coherence. With 9×8 export, ≈ 7 h 38 m – 8 h 05 m.
Scratch disk ≈ 301 GB for freq A at 1×1, plus the side band.

## 3. Settings that are fixed by evidence

| setting | value | reason |
|---|---|---|
| frequencies | A (interferogram), A + B (ionosphere) | main_side_band needs both |
| ionosphere method | `main_side_band`, dispersive filter on, coherence threshold 0.5, median 15 | benchmark configuration; shape validated against GUNW (0.0135 TECU) |
| unwrap looks for the ionosphere | 9 az × 8 rg | sets the side-band grid (40 m × 200 m slant); effective screen ≈ 3 km × 25 km (Gaussian σ 33 px) |
| dense offsets | enabled | removes the geometry-only misregistration (0.077 lines, 0.24 samples) |
| Doppler for any radar-domain window | zero (`isce3.core.LUT2d()`) | native Doppler moved v1 crops ~15 km |
| snaphu for large grids | tiles [4,4], nproc 8, `single_tile_reoptimize` false, `regrow_conncomps` false | defaults re-solve the whole grid (69 GB at 1×1) |
| patched nisar overlays | `tools/apply_patches.py --check` must pass | stock unwrap and mask code OOM at full tile |

## 4. Export layout (proposed)

Per pair, under `gs://s1-slc/<project>/nisar/<direction>/tile_<n>/pairs/<ref>_<sec>/`:

| file | content | size (this pair) |
|---|---|---|
| `RIFG_<ref>_<sec>_A_HH_<looks>.h5` | wrapped interferogram, masks, offsets (NISAR RIFG) | 28.6 GB at 1×1 |
| `RUNW_<ref>_<sec>_A_HH_unw9x8.h5` | 9×8 unwrapped phase, **ionospherePhaseScreen**, uncertainty, connected components | 0.61 GB |
| `coherence_A_HH_win3.tif` | 3×3 coherence on the reference radar grid (needed at 1×1) | 10.1 GB |
| `aoi/<aoi_name>/…` | AOI unwrapped phase and ionosphere-corrected phase, when requested | depends on AOI |
| `provenance.json`, `runconfig.yaml`, logs, QA | identity, versions, (m, n) of the ionosphere solve, gates | small |

Naming follows the output-identity rule: anything that changes bytes (looks, frequency, polarisation, method, version) is in the filename.

## 5. Levers not yet tested (each needs a go-ahead; processing changes)

| lever | expected effect | test |
|---|---|---|
| dense-offset skip 64–75 (GUNW uses 75, window 96×64) | dense offsets 2 h 24 m → ~30–40 min (estimate) | crop run, compare registration residuals and ionosphere |
| no dense offsets for an ionosphere-only run | −3.1 h; ~5% coherence loss; carrier phase should largely cancel in φ_d | crop run, compare screen to benchmark |
| range-decimate SLCs before coregistration | coregistration cost ∝ samples | needs a pre-processing tool |
| use the NISAR GUNW ionosphere instead of computing it | −7.5 h; shape agrees to 0.0135 TECU, level differs by one joint cycle | check GUNW availability per pair; decide on level convention |
| GPU dense offsets | large speed-up | needs a GPU VM |

## 6. Known limitations carried into the pipeline

- **Absolute ionosphere level.** It is unreferenced. The benchmark and GUNW differ by one joint cycle (0.232 TECU). Relative deformation is unaffected;
  mosaics must use screens from one estimator. Absolute TEC needs GIM or GNSS.
- **Uniform range misregistration.** The 0.75 m slant-range misregistration between dates has no identified cause; the rubber sheet removes it.
- **Coherence at 1×1.** It is unusable from ISCE3 (≡ 1); the 3×3 tool must run while the coregistered SLCs are still in scratch.
- **AOI unwrap tool.** It is not written yet: subset RIFG + coherence to a radar window (zero Doppler), run snaphu, slice and interpolate the screen.
- **Pipeline credentials.** The pipeline needs a non-interactive credential with write access to the archive bucket. The VM's default service
  account is read-only (OPERATIONS log).
