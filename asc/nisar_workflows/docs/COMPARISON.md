# Four-way comparison: method, gates and results (comparison v2)

Tool: `tools/compare_four_way.py` (v2; v1 kept as `tools/legacy/compare_four_way_v1.py`).
Output: `case_studies/nepal_glof/comparison_v2/comparison.json` (EXIT=0, 2026-09-15 01:59:22Z, 898 s),
plus `supplement_nondispersive.json`. Figures: `tools/report_figures.py` → `comparison_v2/report/`.
Team report: `tools/build_report.py` → `comparison_v2/report/nepal_glof_four_workflow_report.html`.
Every number below was copied from those JSON files. Values from v1 are labelled as v1.

Benchmark: **R_full** (WF1). Alternatives: **R_crop** (WF3), **G_full** (WF2), **G_crop** (WF4).
Sign convention everywhere: `P__vs__Q` = P minus Q (phase `angle(P·conj Q)`, screens P − Q, cycles (P − Q)/2π),
with legs ordered R_full, R_crop, G_full, G_crop.

## 1. How to run

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env
cd /home/sharath/isce3/asc/nisar_workflows
tmux new-session -d -s cmpv2 "bash ../../case_studies/nepal_glof/logs/run_compare_v2.sh"
#  = python -u tools/compare_four_way.py --rcrop-root aoi_v2 --gcrop-root aoi_v2 \
#        --rcrop-granules L1_RSLC_AOI_v2 --out comparison_v2 --geoloc-tol 15
tmux new-session -d -s figv2 "bash ../../case_studies/nepal_glof/logs/run_report_figures_v2.sh"
python tools/build_report.py
```

Resources: ~15 min on 8 cores. Peak RAM stays well under 31 GB because radar windows are processed in place. Cached layers
are ~2 GB in `comparison_v2/layers/`.

Inputs (all read-only): the four legs' products, both RSLC scratch trees (`scratch/trackR/<tag>/` and
`aoi_v2/scratch/trackR/<tag>/`: `rdr2geo/freqA/{x,y}.rdr`, `crossmul/.../reference.slc`,
`fine_resample_slc/.../coregistered_secondary.slc`, `rubbersheet_offsets/.../resampled_az_offsets`, `RIFG.h5`,
`ionosphere/main_side_band/{RIFG,RUNW}.h5`), the RUNW 9×8 products, the GSLC ionosphere directories and the AOI KML.
**The comparison needs the RSLC scratch of both RSLC legs.** An automated pipeline that deletes scratch must run the
comparison first.

## 2. Method

### 2.1 Crop geometry check

`crop_origin()` locates each crop inside its tile by exact coordinate match (`rtol=0`, `atol = 1e-3 × spacing`), per date
and band. It then checks that `8·rg0_B − rg0_A` is identical on both dates. If not, ISCE3's side-band offsets are wrong
(see WF3).

| quantity | v2 value |
|---|---|
| reference A window (az0, rg0, lines, samples) | (7254, 6792, 12303, 22440) |
| secondary A window | (8001, 6784, 12303, 22440) |
| `8·rg0_B − rg0_A`, reference / secondary | 0 / 0 (consistent) |
| origin mod (9, 8) | (0, 0) → 9×8 grids align exactly (RUNW row 806, col 849) |
| side-band cell alignment | not exact (col offset 106.125 cells; needs origin % 64 = 0) → interpolated |

### 2.2 Common lattice and lookup

- Lattice: the G_crop 5 m UTM 45N grid snapped around the AOI envelope, 6498 × 10350 cells, AOI polygon 97.6% of the
  bbox (`aoi_polygon_mask.tif`).
- Lookup (`layers/lut_fullframe_rowcol.tif`: band 1 row, band 2 col, both **full-frame** reference indices; band 3
  residual in m). A KD-tree over R_crop's rdr2geo lon/lat, projected to UTM, gives the nearest radar sample per cell centre,
  within a 60 m search radius. It was built in ~60 s.
- It replaces `gdalwarp -geoloc`, which skipped whole output chunks (66% coverage, rectangular holes) and, with
  errorThreshold 0.125, carried a +2.54 m E / +2.60 m N bias. The rejected raster is kept as
  `layers/_REJECTED_gdalwarp_lut_chunk_skips.tif`.
- Geolocation mask: residual ≤ 15 m. Farther cells are layover or shadow, where no radar sample images that ground.

**Gates (the run refuses to continue on failure):**

| gate | rule | v2 |
|---|---|---|
| lookup non-empty | valid ≥ 5% | 98.04% |
| index verification | re-read x/y.rdr at the stored indices; max \|d_indep − residual\| ≤ 0.01 m | 1.9e-6 m over 50 000 samples |
| convention offset | \|mean dx\|, \|mean dy\| ≤ 1 m | −1.9 mm, −1.6 mm |
| window inside crop | all indices within the crop | rows 1218..11224, cols 5019..17626 (126 Mpx) |

Coverage: 98.0% of the AOI has a radar sample and 92.8% survives the geolocation mask (NW 95.4%, NE 94.0%, SW 93.2%,
SE 88.7%). Among retained cells the residual has p50 1.85 m and p95 5.64 m. v1 had NW 39.9% because of the native-Doppler crop
plus gdalwarp holes.

### 2.3 Metrics

- **Phase agreement** (`phase_agreement`): unit phasor of P·conj(Q) over the mask. It reports R = |mean|, constant offset
  = arg(mean), circular σ = √(−2 ln R), \|Δφ\| percentiles, fractions under 0.1 and 0.5 rad, a circular-ML plane
  (`fit_plane`: FFT-peak seed, then Nelder–Mead; never lowers R), per-quadrant values, and R binned by R_full flattened
  3×3 coherence (0–0.3, 0.3–0.5, 0.5–0.7, 0.7–1). It runs at 5 m and on 8×8 complex means (40 m).
- **RSLC layers** are built in the radar window and mapped through the lookup: flattened wrapped phase from `RIFG.h5`;
  flattened 3×3 coherence from that phase with the SLC powers; unflattened 3×3 coherence from reference × conj(secondary).
  ISCE3's own 1×1 coherence is ≡ 1 and unusable.
- **Dense offsets** (C1): the full-tile field is bilinearly interpolated onto the crop's grid positions. The 32-sample grids
  are offset by 0.6875 (az) and 0.25 (rg) steps, because 7254 % 32 ≠ 0. Neighbours that are zero or NaN invalidate the estimate.
- **Shifts** (C3, C4): phase correlation of log-amplitude on a 5×5 tile grid, upsample 20, i.e. 0.05-cell quantum.
- **Screens** (`screen_agreement`): median offset P − Q in rad, TECU and joint cycles; residual σ and p95; Pearson r.
- **Cycles** (`cycle_difference`): (P − Q)/2π split into modal whole cycles and a circular remainder.
- **Doppler carrier** (I1b): k = 2π·f_dc/f_line per cell. f_dc is the secondary Doppler LUT evaluated at each cell's reference
  radar position; f_line = 1/az_time_interval = 1520 Hz, **not** the 1909.6 Hz PRF. δ_az is R_full's
  `rubbersheet_offsets/freqA/HH/resampled_az_offsets`, block-averaged over lookup-valid cells. It tests
  exp(∓i·k·δ_az) plus a free scan of k over −8..8 rad/line.

Constants: f_A 1239.0 MHz, f_B 1293.5 MHz; one A cycle = +76.167 rad of dispersive phase, one B cycle = −72.958 rad, one
joint cycle = +3.209 rad; 1 rad = 0.07333 TECU; noise amplification 16.79×. The TECU sign convention has not been independently verified.

## 3. Results

### 3.1 Coregistration (R_full vs R_crop, radar domain)

| C1 dense offsets, full − crop | along track | slant range |
|---|---|---|
| n | 109 781 | 123 322 |
| median | −0.28 mm (−6.3e-5 lines) | +0.17 mm (+5.4e-5 samples) |
| std | 0.098 m (0.0220 lines) | 0.079 m (0.0253 samples) |
| p95 \|diff\| | 0.151 m (0.0339 lines) | 0.101 m (0.0322 samples) |
| R_full median offset (context) | 0.274 m (0.0615 lines) | 0.766 m (0.245 samples) |

Correlation-surface peak: full − crop median −0.0026, std 0.050.

| C2 (radar window, every 2nd sample) | reference SLC | coregistered secondary |
|---|---|---|
| bit-identical | **yes** | no |
| phase-difference coherence R | 1.000000 | 0.99246 |
| circular σ | 0 | 0.123 rad |
| \|Δφ\| p50 / p95 | 0 / 0 | 0.019 / 0.195 rad |
| amplitude rel. diff median / p95 | 0 / 0 | 0.58% / 8.26% |
| tile shift | 0 | 0 (at 0.05 px) |

Consistency: k × std(δ) = 3.975 × 0.022 ≈ 0.087 rad, about half the variance behind σ = 0.123 rad.

C3, GSLC amplitude full vs crop: median relative difference 0 on both dates, p95 0.041% (20260714) and 0.055% (20260726),
shift 0. C4, R_full vs G_full amplitude: shift 0/0 cells (20260714) and 0/−0.10 cells (20260726, MAD 0.05); log-amplitude
r = 0.813 and 0.788.

### 3.2 Wrapped phase

Common mask: 60 936 428 5 m cells, i.e. 92.8% of the AOI (936 209 40 m cells).

| pair | R 5 m | R 40 m | σ_c 40 m (rad) | offset 40 m (rad) | R after plane 40 m | R 40 m by γ 0–.3 / .3–.5 / .5–.7 / .7–1 |
|---|---|---|---|---|---|---|
| R_full − R_crop | 0.9944 | 0.9951 | 0.099 | +0.0035 | 0.9951 | 0.974 / 0.988 / 0.999 / 1.000 |
| G_full − G_crop | 1.0000 | 1.0000 | 0.000 | 0.000 | 1.0000 | 1.000 / 1.000 / 1.000 / 1.000 |
| R_full − G_full | 0.5996 | 0.8921 | 0.478 | −0.294 | 0.9163 | 0.584 / 0.807 / 0.945 / 0.950 |
| R_full − G_crop | 0.5996 | 0.8921 | 0.478 | −0.294 | 0.9163 | same as above to 1e-4 |
| R_crop − G_full | 0.5992 | 0.8916 | 0.479 | −0.298 | 0.9161 | 0.586 / 0.806 / 0.945 / 0.950 |
| R_crop − G_crop | 0.5992 | 0.8916 | 0.479 | −0.298 | 0.9161 | same as above to 1e-4 |

- R_full − R_crop: no ramp (0.009 rad across the AOI). Weakest quadrant NE (R 0.984 at 5 m, 0.986 at 40 m); others ≥ 0.995.
- G_full − G_crop: \|Δφ\| p95 0.5 mrad (5 m). Geocoding is pointwise.
- Sensitivity, R_full − G_full without the 15 m mask: 0.5857 (5 m), 0.8784 (40 m).
- 5 m RSLC-vs-GSLC values are a lower bound. The nearest radar sample sits ~1.85 m from the cell centre that the GSLC was interpolated to.

### 3.3 RSLC vs GSLC phase: Doppler-carrier attribution (I1b)

| quantity | value |
|---|---|
| line rate | 1520.0 Hz |
| secondary f_dc median | 961.6 Hz |
| k median | 3.975 rad/line |
| δ_az (R_full rubber-sheet azimuth) median / mean / p5 / p95 | 0.0534 / 0.0790 / −0.0319 / 0.2181 lines |
| baseline (40 m) R / offset / plane (x east, y south) | 0.8921 / −0.294 rad / (−0.0113, −0.0163) rad/km |
| model sign − : R / offset / plane | **0.9394** / +0.008 rad / (+0.0002, −0.0004) rad/km |
| model sign − : R by γ bin | 0.625 / 0.858 / 0.987 / 0.995 |
| model sign + : R | 0.7625 (worse) |
| free scan best k / R | −4.00 rad/line / 0.9394 |

Conclusion: φ_R − φ_G ≈ −k·δ_az to within the 0.05 rad/line scan step. The carrier term's p95 is 0.87 rad, about 17 mm of LOS.
**Resolved in §3.6:** δ_az is a real residual misregistration left by geometry-only registration. The GSLC keeps it; the RSLC removes it.

### 3.4 Coherence (I2, common cells where all four legs are > 0)

| leg | median | mean | p5 | p95 | frac > 0.5 |
|---|---|---|---|---|---|
| R_full (flattened) | 0.6336 | 0.6062 | 0.168 | 0.956 | 64.9% |
| R_crop | 0.6336 | 0.6062 | 0.168 | 0.956 | 64.9% |
| G_full | 0.6046 | 0.5809 | 0.159 | 0.927 | 62.3% |
| G_crop | 0.6046 | 0.5809 | 0.159 | 0.927 | 62.3% |
| R_full unflattened | 0.6341 | 0.6066 | 0.170 | 0.954 | 65.2% |

- Paired R_full − R_crop: median 0.0000, std 0.009, r 0.9994. Paired R_full − G_full: median +0.027, std 0.156, r 0.804.
- G_full / R_full at 40 m by R bin: 1.050 (0–0.3, bias floor 0.295), 0.964, 0.957, 0.945.
- Flattening effect on R_full (flat − unflat): median −0.004.
- **Resolved in §3.6:** the loss is geometry-only registration, mostly the uniform 0.24-sample range term (the earlier "< 1%"
  estimate considered only azimuth). A residual of under 1% belongs to geocoding itself.

### 3.5 Ionosphere (I3)

RSLC, identical 9×8 grid (exact slice), 1 752 512 cells in the radar data window:

| quantity | value |
|---|---|
| median screen R_full / R_crop | −17.294 rad (−1.268 TECU) / +55.669 rad (+4.082 TECU) |
| offset P − Q | **−72.962 rad** (−5.350 TECU) = one B cycle (−72.958) |
| residual σ / p95 | 0.0027 / 0.0055 TECU; r = 0.99945 |
| unwrapped A, full − crop | 0 cycles on 99.17%; remainder +0.0004 cycles |
| unwrapped B, full − crop | +1 cycle on 99.22%; remainder +0.0002 cycles (side band interpolated) |
| wrapped B agreement | R = 0.973, circular mean +0.0015 rad (478 450 cells) |
| side-band coherence median, R_full / R_crop | 0.590 / 0.591 (v1: 0.573 / 0.428) |
| non-dispersive median, R_full / R_crop (supplement) | +18.6 / −54.4 rad; G_full selected class +19.0 rad |

The crop's screen is the benchmark's plus exactly (m, n) = (0, −1) cycles. The v1 non-integer B error is gone. The
GSLC tool's class criterion (smallest \|median φ_nd\|) would keep R_full's level. That criterion is a prior, not a calibration.

GSLC, identical 40 m cells (1 050 728): median −1.2559 / −1.2562 TECU. Offset +0.00017 TECU, residual σ 0.0052 TECU,
r 0.9987. Raw unwraps differ by +2 A cycles (99.95%) and 0 B cycles. The resolver applied (m, n) = (−2, 0) to G_full and (0, 0)
to G_crop, so the agreement is produced by the resolver.

Cross-domain on the 5 m lattice (every 4th cell, 3 808 416 samples), offset and residual σ in TECU:

| pair | offset | residual σ | r |
|---|---|---|---|
| R_full − R_crop | −5.3507 | 0.0027 | 0.9994 |
| R_full − G_full | −0.0164 | 0.0142 | 0.9868 |
| R_full − G_crop | −0.0126 | 0.0142 | 0.9822 |
| R_crop − G_full | +5.3348 | 0.0143 | 0.9876 |
| G_full − G_crop | +0.0002 | 0.0051 | 0.9987 |

Supports differ (R: ~40 m × 200 m grid with ISCE3's pixel Gaussian; G: 40 m with a 10 km Gaussian).

### 3.6 Registration test (`tools/alignment_test.py` v3 → `comparison_v2/alignment/alignment.json`)

Hypothesis: the GSLC chain registers the dates by geometry only (the RSLC *coarse* state), so every RSLC–GSLC difference is the missing
rubber sheet. Run: 2026-09-15 07:16–07:30Z, EXIT=0, 840 s (log `logs/alignment_test.log`).

**Why it is done on RSLC data.** The delivered 5 m GSLC samples are spectrally white: a 256² chip has 98% of bins within 6 dB of peak and a
minimum of −7.9 dB, against 39% and −50.6 dB for an RSLC chip. Band-limited sub-sample estimation and Fourier shifting are therefore invalid
on the GSLCs. The first attempt returned exactly 0 on 52% of chips; its outputs are kept under `alignment/_ABORTED_*` and the tool under
`tools/legacy/alignment_test_v2_invalid_gslc_shift.py`. The cause of the whiteness was not established; a terrain-flattening explanation was
tested and not confirmed.

**Estimator.** Complex cross-correlation, Tukey taper, upsampled DFT ×100, applied **after demodulating both chips by their joint spectral
centroid**. The RSLC azimuth band is centred on the Doppler carrier (0.63 of the line rate) and wraps across Nyquist. Calibration with physical
sub-sample delays (demodulate → shift → remodulate):

| estimator | coherence | azimuth gain | range gain | rmse (samples) |
|---|---|---|---|---|
| carrier-aware | 1.0 | 0.998 | 0.999 | 0.0028 |
| carrier-aware | 0.6 | 1.000 | 0.998 | 0.0088 |
| naive (no demodulation) | 1.0 | −0.277 | 0.999 | — |
| naive (no demodulation) | 0.6 | −0.282 | 1.001 | — |

Trap for automation: a synthetic test built from FFT-bin shifts passes a naive estimator. Only a physical delay of the carrier-modulated
signal exposes the −0.28× azimuth bias.

**Control (R_crop scratch; 4611 of 7644 chips with coherence ≥ 0.5 and |d| < 1).**

| displacement | vs rubber sheet: OLS slope [95% CI, block bootstrap] | r | mean vs rubber mean |
|---|---|---|---|
| reference → geometry-only secondary, az | 0.978 [0.972, 0.983] | 0.987 | 0.0768 vs 0.0771 lines |
| reference → geometry-only secondary, rg | 0.579 [0.503, 0.656] (field nearly uniform) | 0.473 | 0.2395 vs 0.2437 samples |
| geometry-only → rubber-sheet secondary, az (all chips) | −1.020 [−1.023, −1.016] | −0.998 | |
| geometry-only → rubber-sheet secondary, rg (all chips) | −1.082 [−1.097, −1.067] | −0.982 | |
| reference → rubber-sheet secondary | residual −0.0009 lines az, −0.0119 samples rg | | |

Geometry leaves 0.077 lines (34 cm) along track and 0.24 samples (0.75 m) in slant range between dates.

**Closure.** Rc = R_full · unit(fine · conj(coarse)) is the benchmark with its rubber sheet removed.

| 40 m pair | R | offset (rad) | R, γ 0.5–0.7 | R, γ 0.7–1 |
|---|---|---|---|---|
| R_full − G_crop | 0.8921 | −0.294 | 0.945 | 0.9501 |
| R_full − Rc | 0.9305 | −0.306 | 0.953 | 0.9518 |
| **Rc − G_crop** | **0.9437** | **+0.006** | **0.988** | **0.9961** |

- Blocks (1.28 km): 93.7% of 1044 agree better with Rc than with R_full. Mean gain +0.0085 [0.0077, 0.0092]; Wilcoxon p underflows.
- Coherence medians: G 0.6046; geometry-only RSLC 0.6066; registered RSLC 0.6341. Registration explains 93% of the median gap.
- Ratios by R bin, G/R vs coarse/fine: 0.3–0.5 0.964 / 0.980; 0.5–0.7 0.957 / 0.959; 0.7–1 0.945 / 0.951. Block medians 0.952 / 0.961,
  paired mean −0.0035 [−0.0046, −0.0022]: a residual under 1% that belongs to geocoding.
- Wrapped-phase distribution, JS distance at 40 m: R_crop 0.004, G 0.092 and Rc 0.093 from R_full; G vs Rc 0.005.

**Carrier coefficient** (φ_R − φ_X = a + b·k·δ_az; CI by block bootstrap): G b = −1.00 [−1.01, −1.00]; Rc b = −0.98 [−0.99, −0.97].

**First-principles coherence loss.** |ρ(d)| from the measured RSLC spectra: ρ_az(0.05) = 0.998, ρ_rg(0.25) = 0.948. Predicted factor 0.944.
Observed 0.961 (coarse/fine) and 0.952 (G/R). These sit above the prediction, as the estimator's upward bias requires. The range term dominates.

**Sharpness.** 7×7 speckle contrast: R 1.086 vs G 1.089 (paired ratio 1.005). Intensity ACF FWHM: R 1.63 × 1.68 cells vs G 1.57 × 1.84 cells.
The nearest-neighbour lookup duplicates the east neighbour on 11.2% of cells and the south neighbour on 6.8% (0.83 unique radar samples per cell).
The GSLC is not blurrier in amplitude; the visual difference is coherence and registration phase.

**Aligning GSLCs.** Not possible on delivered samples. ISCE3 `nisar/workflows/gslc.py:201-202` passes `az_time_correction` and
`srange_correction` LUT2d to geocoding. They are built by `AzSrgCorrections` from TEC and tides only. Injecting the rubber-sheet field, in seconds and
metres on the secondary grid, is the direct test. **Not run: it changes processing and needs the user's go-ahead.**

### 3.7 Ionosphere transfer to the crop (`tools/iono_transfer_test.py` → `comparison_v2/iono_transfer/iono_transfer.json`)

Each screen is applied to the crop's interferogram and compared with R_full corrected by its own screen. The 9×8 screen is
interpolated bilinearly to the 1×1 cells; the comparison is on the 5 m lattice.

| screen applied to R_crop | screen − benchmark screen (rad) | corrected phase R, 5 m | offset (rad) |
|---|---|---|---|
| full-tile screen, sliced (exact: origin on the 9×8 lattice) | +0.000 | 0.9944 | +0.003 |
| crop's own screen | +72.962 (one B cycle) | 0.9938 | −2.426 |
| crop's own + class rule, (m,n) = (−1, 0) = rule's choice | −3.205 (one joint cycle) | 0.9938 | +3.088 |
| crop's own + (m,n) = (0, +1), the other minimal-norm member | +0.005 | 0.9938 | +0.014 |

- The sliced full-tile screen reproduces the benchmark correction exactly: 0.9944 equals the uncorrected crop-vs-tile agreement.
- The class rule (`gslc_ionosphere.py`: smallest |median φ_nd|, then minimal norm) finds the class. Its tie-break lands one joint cycle
  (0.235 TECU) from R_full, at the GUNW's level (§3.8).
- The 9×8 unwrapped phases of crop and tile differ by whole cycles on 0.83% of cells (unwrapping, not ionosphere). After removing them, MAD is 0.018 rad.
- Cost: a full-tile ionosphere still needs full-resolution freq-A coregistration. Journal stages: rdr2geo 5953 s, geo2rdr 398, prep 7482 (≈5800 of it
  the 1×1 DEM pass), coarse resample 3226, dense offsets 8667, rubber sheet 1929, fine resample 693, crossmul 1579 + 934. Estimated
  ≈7–7.5 h with 9×8 looks; not measured.

### 3.8 External validation against the NISAR L2 GUNW (`tools/gunw_validation.py` → `comparison_v2/gunw_validation/`)

Product: `L2_GUNW/NISAR_L2_PR_GUNW_025_098_A_016_026_4000_SH_20260714T233920_…_20260726T233919_…_P05023_N_F_J_001.h5` (2.34 GB, fetched with
`tools/nisar_fetch.py`; run configuration saved as `L2_GUNW/gunw_runconfig.json`). Same two RSLC granules, ISCE3 0.25.16, product 1.0.11. Settings:
- wrapped interferogram: 6×5 looks at 20 m;
- unwrapped phase and ionosphere: 16×13 looks at 80 m;
- ionosphere: `main_diff_ms_band` with unwrap correction;
- dense offsets: 96×64 window, skip 75;
- unwrapping: snaphu, single tile;
- DEM: NISAR v1.2;
- geolocation corrections: TEC and troposphere applied.

Our layers are averaged onto GUNW cells by cell centre (16 per 20 m cell, 256 per 80 m cell), restricted to the comparison mask.

| leg vs GUNW | wrapped R 20 m | R, γ 0.5–0.7 | R, γ > 0.7 | R 80 m | offset (rad) | unwrapped: cells at modal cycle | unwrapped MAD (rad) |
|---|---|---|---|---|---|---|---|
| R_full | 0.807 | 0.921 | 0.978 | 0.946 | +0.010 | 96.1% | 0.110 |
| R_crop | 0.807 | 0.921 | 0.978 | 0.946 | +0.007 | 96.2% | 0.111 |
| G_full | 0.763 | 0.880 | 0.940 | 0.914 | +0.306 | 96.2% | 0.263 |
| G_crop | 0.763 | 0.880 | 0.940 | 0.914 | +0.306 | 96.2% | 0.263 |

- The RSLC legs match GUNW without offset or ramp. The GSLC legs carry the registration signature (offset +0.30 rad, same plane), which independently confirms §3.6.
- Sign convention check: conjugating the GUNW drops agreement to 0.22.
- Coherence: GUNW 6×5 median 0.579; R_full 3×3 cell mean 0.600 (r 0.925); G 0.577 (r 0.891). Different estimators, so the levels are not comparable.
- GUNW connected components in the AOI: 1 component covering 79.8%.

| our screen − GUNW | offset (rad) | TECU | joint cycles | residual σ (TECU) | residual (mm) | r |
|---|---|---|---|---|---|---|
| R_full | +3.164 | +0.232 | +0.986 | 0.0135 | 3.5 | 0.985 |
| R_crop | +76.127 | +5.582 | +23.72 (= R_full + one B cycle) | 0.0140 | 3.7 | 0.984 |
| G_full | +3.399 | +0.249 | +1.059 | 0.0176 | 4.6 | 0.974 |
| G_crop | +3.393 | +0.249 | +1.057 | 0.0183 | 4.8 | 0.970 |

GUNW screen median −1.507 TECU in the AOI; its uncertainty median is 0.90 rad (0.066 TECU). The shapes agree well within that uncertainty.
The levels split by one joint cycle: {R_full, G_full, G_crop} vs {GUNW, crop + class rule} (the latter two within 0.04 rad of each other).
Class agreement with degenerate member choice is exactly what the split-spectrum algebra allows. Deciding the level needs external TEC.

## 4. Pipeline gates derived from this comparison

| gate | threshold used here | status on v2 |
|---|---|---|
| crop A/B alignment consistent across dates | exact | pass |
| crop origin mod looks (9, 8); for exact side band, range origin mod 64 | exact | 9×8 pass; 64 not met |
| reference SLC bit-identical, crop vs tile window | exact | pass |
| coregistered secondary R (radar window) | ≥ 0.99 | 0.9925 |
| dense-offset full − crop std | ≤ 0.05 lines / samples | 0.022 / 0.025 |
| wrapped phase R_full − R_crop, 40 m | ≥ 0.99 | 0.9951 |
| G_full − G_crop phase R | ≥ 0.9999 | 1.0000 |
| iono B-cycle remainder \|circular mean\| | ≤ 0.01 cycles | 0.0002 |
| iono whole-cycle offset vs reference solution | report; fail mosaics on ≠ 0 | 1 B cycle (R_crop) |
| lookup index verification / convention offset | ≤ 0.01 m / ≤ 1 m | pass |

The thresholds are this study's observed values with margin, not requirements from the literature. Recalibrate them on more pairs.

## 5. Open items

1. ~~Which chain carries δ_az~~ and ~~the cause of the GSLC coherence loss~~: resolved by §3.6 (geometry-only registration).
2. Re-geocode the GSLC secondary with rubber-sheet corrections injected via ISCE3's `az_time_correction` / `srange_correction` (direct test; not run).
3a. The cause of the uniform 0.75 m slant-range misregistration. Candidates: differential ionospheric group delay (≈0.26 m/TECU at f_A),
   differential troposphere, range timing.
3b. The cause of the white GSLC spectrum.
3. The absolute ionospheric level: add class checking after ISCE3 and an external TEC reference (GIM or GNSS).
4. Side-band exact alignment: snap future crop range origins to multiples of 64 (`rslc_subset.py --align-sideband-looks 8`,
   added after the v2 run and not yet exercised).
5. `compare_four_way.py` still hard-codes EPSG 32645, the frequencies and the 15 m tolerance default (CMP-17).
6. R_crop has no 1×1 unwrap; a 1×1 unwrapped-phase comparison has not been made.

## 6. Problems and errors log

<!-- ERROR-LOG:BEGIN -->
30 entries: 0 caught by the user, 21 were the assistant's own mistakes of judgement, 3 still open. Every entry cites its evidence; the full records are in `case_studies/nepal_glof/comparison/verification/`.

| id | problem | category | caught by | assistant error | status | cost |
|---|---|---|---|---|---|---|
| CMP-01 | Geocoding the R_crop RIFG with GDAL geolocation arrays gave a silently empty raster (relative paths) and lacked the pixel-centre convention | tooling | assistant | yes | fixed | 4m15s of wasted warp plus a re-run. |
| CMP-02 | Wrong prediction that the cropped dense-offset field would differ from the full one by the frame shift | geometry | assistant | yes | fixed | Minor. It would have produced a false subsetter-bug alarm if not tested against data. |
| CMP-03 | Ad-hoc warp spot-check read 0.00% nonzero and was blamed on relative X_DATASET paths; audit finds that diagnosis unsupported, yet it is recorded as a GDAL trap | tooling | assistant | yes | open | About 4 min plus a restarted warp. The larger cost is a false trap documented into the automation knowledge base, and the real cause of the 0% (missing AOI cove |
| CMP-04 | SRS 'EPSG:4326' in GEOLOCATION metadata prints 'ERROR 1: missing [' (non-fatal) | tooling | guardrail/tool check | no | fixed | Negligible. |
| CMP-05 | GDAL geolocation arrays assume corner coordinates; rdr2geo lon/lat are pixel centres, needing GEOREFERENCING_CONVENTION=PIXEL_CENTER | tooling | assistant | no | fixed | None; set before first use. |
| CMP-06 | Wrong prediction: RSLC dense-offset fields would differ by the crop frame shift | geometry | assistant | yes | fixed | Negligible; both were computed in one pass. |
| CMP-07 | Geolocation lookup warp took 1549 s (~26 min); the assistant suspected a stall and misread the clock | tooling | assistant | no | worked around | About 26 min once. |
| CMP-08 | Loop variable `b` shadowed the crop-buffer variable and crashed stage I3 after 30.7 min | resources | crash | yes | fixed | 30.7 min run lost, mitigated to about 4 min by caching. |
| CMP-09 | Radar shadow/layover: GDAL backmap fills holes with far-away radar pixels (p95 50 m, max 8.95 km), polluting RSLC-vs-GSLC statistics | tooling | assistant | no | fixed | One extra comparison run, and 7.1% of the AOI excluded from cross-track statistics. |
| CMP-10 | Nearest-neighbour geocoding caps 5 m RSLC-vs-GSLC phase agreement | geometry | assistant | no | by design | The 5 m cross-track agreement understates true agreement. |
| CMP-11 | R-vs-G phase difference labelled a 'planar ramp' from reference-phase/baseline handling; it is GSLC azimuth misregistration times the Doppler carrier | geometry | assistant | yes | fixed | Risk of misinforming the team report. |
| CMP-12 | screen_agreement argument order is the reverse of its result keys and log labels | geometry | assistant | yes | fixed | Mislabelled values reached the verification prompt and could reach the team report. |
| CMP-13 | nearest_cycle_combo attribution is meaningless: any constant can be matched by some (m_A, n_B) within ±8 | geometry | assistant | yes | fixed | Supported a wrong narrative (an ISCE3 absolute-cycle limitation) that was partly the assistant's own crop-tool bug (R_crop). |
| CMP-14 | G_crop products use NaN nodata; masks built with `!= 0` let NaN through, making the G_full-vs-G_crop phase histogram all NaN | geometry | assistant | yes | fixed | One report chart is empty or NaN, and common_mask_fraction_of_aoi is slightly overstated. |
| CMP-15 | PIXEL_CENTER fix not ported to slc_amp_overlay.py, which still geocodes with GDAL's corner convention | tooling | assistant | yes | open | Absolute geolocation of the delivered overlay is off by about half a look cell. Layer-to-layer comparisons are unaffected. |
| CMP-16 | compare_four_way.py caches ignore their inputs: stale layers survive --force-lut, and the tool writes a workflow product as a side effect | operations | assistant | yes | fixed | A rebuilt lookup would silently be combined with layers geocoded through the old one. |
| CMP-17 | compare_four_way.py hard-codes case-specific constants, blocking reuse in an automated pipeline | science | assistant | no | open | Porting to another frame or pair needs code edits and is error-prone. |
| CMP-18 | STATE.md, the resume document, still asserts withdrawn or false claims | judgement | assistant | yes | fixed | Anyone resuming from STATE.md, including an automation effort, would inherit four wrong facts. |
| CMP-19 | Doppler-carrier constant computed with the acquisition PRF instead of the radar-grid line rate | geometry | guardrail/tool check | no | fixed | The v1-era attribution numbers (offset -0.060 rad, 0.932, 0.987/0.994) were computed with a constant 25.6% too small. |
| CMP-20 | RUNW pixelOffsets are in metres; the v1 comparison labelled them pixels | geometry | guardrail/tool check | yes | fixed | Mislabelled statistics in v1; the conclusion (full vs crop ~0) survived. |
| CMP-21 | Opposite sign conventions for the same named pairs within one comparison.json | data management | guardrail/tool check | yes | fixed | A verifier reported the labels the wrong way round. |
| CMP-22 | Pre-run review of comparison tool v2 found 15 defects, including a wrong plane estimator | judgement | guardrail/tool check | yes | fixed | ~26 min of three reviewer agents; avoided a wrong or failed 45-min run. |
| CMP-23 | Legacy comparison tool asc/compare/compare_tracks.py is Venezuela-hardcoded; its water check cannot run on landlocked Nepal | other | assistant | no | worked around | None now. |
| CMP-24 | gdalwarp -geoloc lookup skipped output chunks and carried a half-cell bias | tooling | guardrail/tool check | no | fixed | One comparison attempt (EXIT=143) and ~30 min. |
| CMP-25 | Amplitude cross-correlation estimator pixel-locked sub-sample offsets | geometry | guardrail/tool check | yes | fixed | One aborted run (~6 min); outputs kept as alignment/_ABORTED_amplitude_estimator_alignment.json. |
| CMP-26 | Complex estimator without carrier demodulation read RSLC azimuth delays as -0.28x | geometry | guardrail/tool check | yes | fixed | One full alignment run (~15 min) invalid; outputs under alignment/_ABORTED_run2_nodemod_estimator/. |
| CMP-27 | Regression of chip offsets dominated by low-coherence integer-pixel outliers | science | assistant | yes | fixed | Diagnosis only. |
| CMP-28 | Sub-pixel registration and Fourier realignment attempted on delivered GSLCs with white spectra | judgement | assistant | yes | fixed | ~25 min of runs and a rewrite. |
| CMP-29 | Misregistration coherence loss first estimated as < 1% (azimuth only) | science | assistant | yes | fixed | A wrong statement in a published report revision (v1-v2). |
| CMP-30 | Unverified place name and a hand-typed number in the team report | resources | assistant | yes | fixed | Caught before forwarding. |

Cross-cutting operational problems (process supervision, reboots, logs, disk, agent harness) are in OPERATIONS_AND_LESSONS.md.

#### CMP-01 — Geocoding the R_crop RIFG with GDAL geolocation arrays gave a silently empty raster (relative paths) and lacked the pixel-centre convention

- **Symptom:** gdalwarp -geoloc ran 4m15s, exited 0 with 'Warning 1: Too many points (529 out of 529) failed to transform', and wrote a correctly georeferenced 10349x6498 raster that was '0.00%' non-zero. This first warp also set no GEOREFERENCING_CONVENTION=PIXEL_CENTER, so even a successful run would have been shifted by half a pixel (documented later in compare_four_way.py:66-67).
- **Root cause:** GDAL resolves X_DATASET/Y_DATASET in VRT GEOLOCATION metadata relative to the VRT's directory (the scratchpad), not the working directory. GDAL also defaults geolocation arrays to top-left corner coordinates, while rdr2geo x/y are pixel centres.
- **Fix:** Re-ran with absolute paths (L4405). compare_four_way.py later replaced this with a single nearest-neighbour row/col LUT warp using PIXEL_CENTER and per-pixel verification, cached in comparison/layers/lut_rowcol.tif and R_crop_ifg.tif.
- **Cost:** 4m15s of wasted warp plus a re-run.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L4394: 'Warning 1: Too many points (529 out of 529) failed to transform ... [exited with code 0]'. L4400: 'warped output nonzero: 0.00%' and 'X_DATASET': 'aoi/scratch/trackR/.../x.rdr'. L4404: 'the X_DATASET/Y_DATASET paths are relative'. compare_four_way.py:66-67.
- **Automation lesson:** The radar-to-map module should use absolute paths, set PIXEL_CENTER, and fail on a GDAL 'failed to transform' warning or a valid fraction below a threshold, instead of trusting exit code 0.

#### CMP-02 — Wrong prediction that the cropped dense-offset field would differ from the full one by the frame shift

- **Symptom:** The first draft of compare_four_way.py predicted the cropped RUNW pixelOffsets would equal the full-tile field minus the secondary frame shift (+824 lines, -13 samples) and planned to use that as a subsetter test. Measured: the raw difference is about 0 (median 0.0004 px, std 0.077 px along-track), and the shifted hypothesis is off by the whole shift.
- **Root cause:** The stored pixelOffsets are residuals relative to the geometric (geo2rdr) prediction, so they do not depend on the frame; the assistant assumed raw index differences.
- **Fix:** The tool tests both hypotheses and the docstring was corrected (compare_four_way.py:21-27).
- **Cost:** Minor. It would have produced a false subsetter-bug alarm if not tested against data.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L4587: 'I predicted the cropped offset field would differ from the full one by the frame shift (+824 lines, -13 samples) ... The data says otherwise.' comparison.json C1: alongTrackOffset raw_diff_median 0.00043, crop_eq_full_minus_shift median_px 824.0004.
- **Automation lesson:** When building a validation test on product semantics, check the semantics on a known case (for example full tile against itself) before relying on the prediction.

#### CMP-03 — Ad-hoc warp spot-check read 0.00% nonzero and was blamed on relative X_DATASET paths; audit finds that diagnosis unsupported, yet it is recorded as a GDAL trap

- **Symptom:** After `gdalwarp -geoloc` of the cropped RIFG: 'Warning 1: Too many points (529 out of 529) failed to transform, unable to compute output bounds.' and 'warped output nonzero: 0.00%'. The assistant concluded that 'GDAL resolves geolocation arrays relative to the VRT's directory ... not the CWD' and that relative paths 'silently produce an EMPTY output'.
- **Root cause:** The 0.00% check read window (0,0,2000,2000) (jsonl L4399). Audit: the final, correct lookup is 0.0% valid in exactly that window (91% in the centre), because it lies outside the crop footprint (previous entry). The check could not have detected success. Audit test in GDAL 3.12.4: relative-to-CWD X_DATASET/Y_DATASET warp correctly with a VRT in another directory (65.8% nonzero, identical to absolute paths), while paths relative to the VRT fail loudly ('ERROR 4: ../data/lon.tif: No such file or directory', rc=1). The first warp ran from the case dir where its relative paths resolve (jsonl L4386). The same 'Too many points (529 out of 529) failed to transform' warning appears in the SUCCESSFUL absolute-path lookup warp (compare_four_way_attempt2.log), so it is not a failure signal. The original output was lost from /tmp, so it cannot be re-checked.
- **Fix:** Absolute paths are still used (compare_four_way.py:455-456), which is harmless. The claimed trap remains as fact in compare_four_way.py:62-64 and STATE.md.
- **Cost:** About 4 min plus a restarted warp. The larger cost is a false trap documented into the automation knowledge base, and the real cause of the 0% (missing AOI coverage) was hidden.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** jsonl L4394, L4400, L4404, L4413 ('nonzero in centre: 54.01%' – read at (4000,3000)); audit: 'lut valid in window (0,0,2000,2000): 0.0', 'relative-to-CWD ... rc=0 nonzero=0.6577', 'relative-to-VRT ... rc=1 ERROR 4'; logs/compare_four_way_attempt2.log lines after 'building geolocation lookup'.
- **Automation lesson:** Validate warps with a whole-raster (decimated) valid fraction compared against the expected footprint, never a corner window. Don't record a root cause without a controlled A/B test, and correct the docstring and STATE.md.

#### CMP-04 — SRS 'EPSG:4326' in GEOLOCATION metadata prints 'ERROR 1: missing [' (non-fatal)

- **Symptom:** 'ERROR 1: missing [' on the absolute-path geoloc warp.
- **Root cause:** GDAL parses the geolocation SRS item as WKT and does not accept an authority string there. Audit test in GDAL 3.12.4: output is byte-for-byte the same valid fraction with 'EPSG:4326' or WKT (rc=0 both); the error is noise because GDAL falls back to WGS84. It would be silently wrong for non-WGS84 geolocation arrays.
- **Fix:** compare_four_way.py:452-458 passes full WKT.
- **Cost:** Negligible.
- **Caught by:** guardrail/tool check · **Status:** fixed
- **Evidence:** jsonl L4405 metadata 'SRS':'EPSG:4326', L4413 'ERROR 1: missing ['; audit 'absolute, EPSG:4326 string ... rc=0 nonzero=0.6577 ... ERROR 1: missing ['; compare_four_way.py:69-70 docstring.
- **Automation lesson:** Always write geolocation SRS as WKT from osr.ExportToWkt(). Treat GDAL ERROR lines as failures in wrappers (CPLSetErrorHandler or gdal.UseExceptions with log scanning).

#### CMP-05 — GDAL geolocation arrays assume corner coordinates; rdr2geo lon/lat are pixel centres, needing GEOREFERENCING_CONVENTION=PIXEL_CENTER

- **Symptom:** Risk of a silent half-pixel shift between geocoded RSLC and GSLC. With the convention set, the per-pixel lookup check gives mean dx -0.26 m, dy -0.07 m and p50 1.96 m.
- **Root cause:** GDAL's GEOLOCATION default is TOP_LEFT_CORNER. Audit synthetic test in GDAL 3.12.4 with pixel-centre lon/lat: without the key, chosen index minus true nearest averages -0.4 px (60% exact); with PIXEL_CENTER +0.1 px (90% exact, rounding ties).
- **Fix:** compare_four_way.py:458 sets GEOREFERENCING_CONVENTION=PIXEL_CENTER, and lines 477-531 verify every lookup pixel by projecting its rdr2geo lon/lat back to UTM.
- **Cost:** None; set before first use.
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** compare_four_way.py:65-68 docstring; comparison.json lookup_verification 'mean_dx_m_all': -0.2617, 'mean_dy_m_all': -0.0698; audit test output 'None mean(chosen - true nearest) = -0.4 ... PIXEL_CENTER ... = 0.1'.
- **Automation lesson:** The geocoding module should set the pixel convention explicitly and always run a lookup back-projection check (mean and p95 residual) as a QA gate.

#### CMP-06 — Wrong prediction: RSLC dense-offset fields would differ by the crop frame shift

- **Symptom:** The assistant planned a subsetter test on the assumption that cropped offsets equal full offsets minus the frame shift (+824 lines, -13 samples). Measured: raw difference median 0.0004 px, while the frame-shifted hypothesis is off by 824.0004 px.
- **Root cause:** RUNW pixelOffsets are frame-independent residuals relative to the geometric (geo2rdr) prediction, not raw index differences.
- **Fix:** The tool tests both hypotheses and the docstring records the measured result (compare_four_way.py:21-28).
- **Cost:** Negligible; both were computed in one pass.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** jsonl L4587: 'I predicted the cropped offset field would differ from the full one by the frame shift ... The data says otherwise'; compare_four_way.log C1 'raw_diff_median': 0.000433, 'crop_eq_full_minus_shift': {'median_px': 824.0004...}.
- **Automation lesson:** Encode product semantics (offsets are residuals against geometry) in the pipeline's data model, and test assumptions against data before building checks on them.

#### CMP-07 — Geolocation lookup warp took 1549 s (~26 min); the assistant suspected a stall and misread the clock

- **Symptom:** 'lookup warped in 1549s'. For minutes no output file existed; at 149 s the process was at 54.7% CPU and 1.57 GB RSS. The assistant: 'the main thread is at 100% CPU loading the lon/lat arrays ... a single-threaded phase ... (I'd also misread the clock; the run is only ~2.5 minutes in.)'.
- **Root cause:** GDAL builds the geolocation backmap from the 11219x14719 float64 lon/lat arrays in a single-threaded phase before any output is written. The warp used errorThreshold=0 (exact transform per pixel) and nearest neighbour over a 6498x10350 lattice. How much errorThreshold=0 contributes was never measured.
- **Fix:** The lookup is written once to comparison/layers/lut_rowcol.tif and reused. The rerun reached the lookup check in 102 s instead of 1628 s.
- **Cost:** About 26 min once.
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** logs/compare_four_way_attempt2.log '[ 1626.0s]   lookup warped in 1549s'; logs/compare_four_way.log '[  102.2s]   lookup check'; jsonl L4584 'PID ELAPSED %CPU RSS ... 3336 149 54.7 1572620'; L4587.
- **Automation lesson:** Build geocoding lookups once per (radar grid, DEM, map lattice), store them with an identity that encodes all inputs, and log the backmap phase explicitly so a silent phase is not taken for a stall. Benchmark errorThreshold (0 vs 0.125) before choosing.

#### CMP-08 — Loop variable `b` shadowed the crop-buffer variable and crashed stage I3 after 30.7 min

- **Symptom:** 'File "tools/compare_four_way.py", line 629, in main  bl, bc = math.ceil(b / ly), math.ceil(b / lx)  TypeError: unsupported operand type(s) for /: 'str' and 'int'' at 1843.6 s (13:11:15 -> 13:42:00, EXIT=1).
- **Root cause:** `b = args.buffer` (C2) was later rebound by `for a, b in pairs:` (I1, leg names), so I3 divided a string. All statistics were held in memory and comparison.json is written only at the very end (line 725), so the whole run's results were lost.
- **Fix:** Loop renamed to p/q with a comment (compare_four_way.py:630-632); I3 uses args.buffer directly (line 678). The cached lookup and map layers made the rerun 4 m 08 s.
- **Cost:** 30.7 min run lost, mitigated to about 4 min by caching.
- **Caught by:** crash · assistant error · **Status:** fixed
- **Evidence:** logs/compare_four_way_attempt2.log traceback; jsonl L4601 grep: '408:    b = args.buffer', '583:    for a, b in pairs:'; L4666: 'crashed in I3 on a variable-naming bug of mine'.
- **Automation lesson:** Split the comparison into stage functions with no shared mutable locals. Write each stage's results to disk as it finishes (per-stage JSON) so a late crash loses nothing. Run a linter such as pylint redefined-outer-name/flake8 before long runs.

#### CMP-09 — Radar shadow/layover: GDAL backmap fills holes with far-away radar pixels (p95 50 m, max 8.95 km), polluting RSLC-vs-GSLC statistics

- **Symptom:** The first lookup check (50k-pixel sample) showed mean dx/dy ~0.07/0.02 m but p95 51.8 m and max 4571 m. The per-pixel check found p95 50.46 m, max 8954 m.
- **Root cause:** In Himalayan relief, shadowed or laid-over ground has no unique radar sample. GDAL's geolocation backmap interpolates indices into those holes, pointing at radar pixels far from the map cell. Audit: 96% of excluded pixels lie >500 m from the crop-footprint edge, clustered around ~19k interior lookup holes (2.4% of the lattice). That is consistent with relief shadow/layover rather than swath-edge fill.
- **Fix:** Every lookup pixel is back-projected and kept only within --geoloc-tol 15 m (compare_four_way.py:477-531). This excludes 8.8% of valid pixels (7.14% of the AOI); kept p95 is 5.70 m. A no-mask sensitivity result is also written (I1_sensitivity_R_full_vs_G_full_WITHOUT_geoloc_mask). R_full vs G_full moved from 5 m 0.5980 / 40 m 0.8806 without the mask to 0.6194 / 0.8869 with it.
- **Cost:** One extra comparison run, and 7.1% of the AOI excluded from cross-track statistics.
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** logs/compare_four_way_attempt2.log 'p95_abs_m': 51.84, 'max_abs_m': 4571.5; logs/compare_four_way.log 'p95_abs_m_all': 50.46, 'max_abs_m_all': 8954.13, 'tolerance_m': 15.0, 'excluded_fraction_of_aoi': 0.0714, 'p95_abs_m_kept': 5.70; I1 'R_full__vs__G_full: 5m R=0.5980 40m R=0.8806' (attempt2) vs '0.6194 ... 0.8869' (final).
- **Automation lesson:** Any radar-to-map lookup must be validated per pixel against its own geolocation. Write the rdr2geo layover_shadow layer and use it as a mask, and report every mask's sensitivity next to the statistic it filters.

#### CMP-10 — Nearest-neighbour geocoding caps 5 m RSLC-vs-GSLC phase agreement

- **Symptom:** R_full vs G_full wrapped-phase agreement is 0.619 at 5 m but 0.887 at 40 m.
- **Root cause:** The lookup picks the nearest radar sample, up to half a pixel from the map-cell centre, while GSLC interpolates to the exact centre. At 5 m that decorrelates speckle. Verifier V2: agreement is 0.767 (<1 m lookup error), 0.655 (1-2 m) and 0.583 (2-3 m), after Doppler correction; 15 m looks give 0.819. R_full vs R_crop cannot detect this because both use the same lookup.
- **Fix:** Documented in the docstring (compare_four_way.py:72-77). 40 m is reported as the fair cross-track number and 5 m as a bound on the resampling cost.
- **Cost:** The 5 m cross-track agreement understates true agreement.
- **Caught by:** assistant · **Status:** by design
- **Evidence:** comparison.json I1; V2 verifier: 'The 5 m value of 0.619 is set mainly by where the nearest-neighbour lookup samples, not by a real phase difference ... 0.767 (<1 m), 0.655 (1-2 m) and 0.583 (2-3 m)'.
- **Automation lesson:** For native-resolution cross-geometry comparisons, geocode the RSLC interferogram with ISCE3 geocode (interpolating), or stratify statistics by lookup distance. Never compare nearest-neighbour output with an interpolated product pixel for pixel without saying so.

#### CMP-11 — R-vs-G phase difference labelled a 'planar ramp' from reference-phase/baseline handling; it is GSLC azimuth misregistration times the Doppler carrier

- **Symptom:** The assistant reported 'a ~1 rad planar ramp across the AOI whose cause I haven't established'. phase_agreement fits a plane and its comment calls it 'a reference-phase / baseline-handling difference'. report_figures writes f04_ramp_R_full__G_full.
- **Root cause:** Verifier V2: quadrant gradients disagree, and plane removal only lifts agreement to 0.913. Offset and ramp are almost entirely the GSLC secondary being misregistered in azimuth (rubbersheet minus geo2rdr median 0.098 lines), with geocoding restoring a Doppler carrier of about 3.15 rad per line (957 Hz / 1909.6 Hz PRF). Subtracting -3.15*daz with no fitted parameters moves the offset from -0.351 to -0.060 rad and 40 m agreement from 0.887 to 0.932. The physics belongs to G_full/G_crop (no data-driven coregistration in GSLC).
- **Fix:** Not yet reflected in compare_four_way.py or report_figures.py; the plane-fit fields and ramp figure remain. **Update:** comparison_v2 I1b (2026-09-15): the ML plane lifts 40 m agreement only 0.892 -> 0.916, while -k*daz with k = 2*pi*fdc/1520 Hz (median 3.975 rad/line) lifts it to 0.939 and removes offset and trend; a free fit peaks at -4.00. Which chain carries daz stays open (COMPARISON.md).
- **Cost:** Risk of misinforming the team report.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** compare_four_way.py:203-205; jsonl L4684; V2 verifier: 'What does not hold is calling the difference a "planar ramp" ... moves the offset from -0.351 to -0.060 rad; lifts 40 m coherence from 0.887 to 0.932'; logs/report_figures.log 'f04_ramp_R_full__G_full'.
- **Automation lesson:** Comparison modules should report residual structure neutrally (offset, fitted plane, tile-mean map) and not assign physical causes in code comments or figure names. Physical attribution needs a separate test with no fitted parameters, for example the Doppler × misregistration model.

#### CMP-12 — screen_agreement argument order is the reverse of its result keys and log labels

- **Symptom:** comparison.json key 'R_full_vs_R_crop_radar_9x8' and log line 'G full vs crop: {median_a_tecu: -1.245, median_b_tecu: -1.256}' read as a=full, b=crop, but a is the crop. Verifier V4: 'The claim has these two labels the wrong way round.'
- **Root cause:** compare_four_way.py:681 calls screen_agreement(io_c, io_f_win, im) and :695 screen_agreement(gio_c, gio_f, m40) under full-first names. constant_offset is therefore crop minus full, and median_a is the crop.
- **Fix:** None; the labels are still inverted in code and JSON. **Update:** v2 screen_agreement(p, q) returns offset_P_minus_Q_* keys and comparison.json carries a conventions block (P__vs__Q = P minus Q).
- **Cost:** Mislabelled values reached the verification prompt and could reach the team report.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** compare_four_way.py:681, :695, :722-723; logs/compare_four_way.log 'G full vs crop: {"n": 1050728, "median_a_tecu": -1.2450...'; V4 journal 'CROP -1.2450 TECU and FULL TILE -1.2559 TECU. The claim has these two labels the wrong way round.'
- **Automation lesson:** Use keyword arguments and self-describing result fields (median_R_full, median_R_crop, offset_crop_minus_full) instead of positional a/b. Add a unit test that swaps inputs and checks the sign.

#### CMP-13 — nearest_cycle_combo attribution is meaningless: any constant can be matched by some (m_A, n_B) within ±8

- **Symptom:** The log attributed the R_full-vs-R_crop +48.97 rad offset to 'nearest_cycle_combo': {'m_A': -7, 'n_B': -8, 'residual_rad': 1.525}. Verifier V1: 'Treat the "(m_A,n_B)=(-7,-8)" attribution as wrong'. Actual: freq A -1 cycle, freq B -2 cycles, plus a non-integer +0.279 freq-B cycle (-1.49 TECU) caused by the crop tool's freq-B misregistration.
- **Root cause:** compare_four_way.py:272-274 searches 17x17 integer combinations against a constant, with 76.17 and -72.96 rad steps whose sum is only 3.21 rad, so the grid is dense enough to fit almost anything. It assumes the offset is purely integer cycles and never checks the per-band unwrapped phases.
- **Fix:** None in code. **Update:** Removed in v2; cycle_difference() decomposes unwrapped A and B separately into modal whole cycles plus a circular remainder (v2: A 0 cycles on 99.2%, B +1 cycle on 99.2%, remainders < 0.001 cycles).
- **Cost:** Supported a wrong narrative (an ISCE3 absolute-cycle limitation) that was partly the assistant's own crop-tool bug (R_crop).
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** compare_four_way.py:272-281; logs/compare_four_way.log 'nearest_cycle_combo': {'m_A': -7, 'n_B': -8, 'residual_rad': 1.5252}; V1 journal corrected_statement.
- **Automation lesson:** Attribute ionosphere offsets by differencing each band's unwrapped phase per connected component (integer part and fractional part separately), not by fitting integer combinations to a scalar.

#### CMP-14 — G_crop products use NaN nodata; masks built with `!= 0` let NaN through, making the G_full-vs-G_crop phase histogram all NaN

- **Symptom:** logs/report_figures.log: 'RuntimeWarning: invalid value encountered in divide  d = np.angle(z * np.exp(-1j * np.angle(np.mean(z / np.abs(z)))))'. Audit: comparison/report/figures.json charts.phase_diff_hist.G_full__G_crop_5m has 90 of 90 bins NaN.
- **Root cause:** Audit: aoi/.../ifg_A_HH_1x1.igram.tif has nodata=nan (63% NaN in the top quarter of the AOI window), while G_full and the R layers use 0. compare_four_way.py:617 and report_figures.py:158-160 build `common` with `ifg[k] != 0`, which is True for NaN. Reproduced mask: common is 0.73277 of the AOI and includes 34,810 G_crop NaN pixels (0.07%). compare_four_way's phase_agreement re-masks isfinite (line 183), so comparison.json survives; report_figures' hist_phase does not, and one NaN poisons np.mean.
- **Fix:** None yet. **Update:** v2 reads every raster through c64()/f32() (nan_to_num) and phase_agreement additionally requires isfinite; report_figures v2 uses the same readers.
- **Cost:** One report chart is empty or NaN, and common_mask_fraction_of_aoi is slightly overstated.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** Audit output 'G_crop nodata nan nonfinite frac 0.630'; 'G_crop NaN inside common: 34810'; figures.json 'G_full__G_crop_5m n bins 90 nan bins 90'.
- **Automation lesson:** Standardise nodata across all products (one convention, recorded in metadata), and build validity masks with np.isfinite(x) & (x != 0) through one shared helper. Assert charts contain no NaN before publishing.

#### CMP-15 — PIXEL_CENTER fix not ported to slc_amp_overlay.py, which still geocodes with GDAL's corner convention

- **Symptom:** slc_amp_overlay.py geocode() sets X_DATASET/Y_DATASET/SRS but no GEOREFERENCING_CONVENTION, and its lon/lat are look-box means (cell-centre coordinates). The overlay layers are therefore registered about half a multilooked cell off the basemap (~20 m in azimuth at 9 looks), though consistently across the three layers.
- **Root cause:** The trap was found and fixed in compare_four_way.py (Sep 14) but never back-ported to the earlier tool that uses the same mechanism (Sep 4). The ad-hoc warps at jsonl L4386/L4405 also lacked it.
- **Fix:** None.
- **Cost:** Absolute geolocation of the delivered overlay is off by about half a look cell. Layer-to-layer comparisons are unaffected.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** slc_amp_overlay.py:209-217 metadata dict has no GEOREFERENCING_CONVENTION; compare_four_way.py:458 has it; audit synthetic test shows a -0.4 px bias without it.
- **Automation lesson:** Keep one shared geocode-by-geolocation-arrays function used by every tool. A trap fix must be grep-applied across the repository.

#### CMP-16 — compare_four_way.py caches ignore their inputs: stale layers survive --force-lut, and the tool writes a workflow product as a side effect

- **Symptom:** Latent. `cached(name, build)` returns any existing R_full_ifg/R_crop_ifg/R_full_coh/R_crop_coh.tif regardless of --force-lut (lines 541-547). geoloc_ok_tol15m.tif/.json are reused unless --force-lut, keyed only by tolerance (491-494). lut_rowcol.tif's name encodes neither pixel convention, errorThreshold nor source radar grid. The tool also writes aoi/pairs/.../trackR/coherence_A_HH_win3.tif if absent (567-573).
- **Root cause:** The caching added for reboot resilience uses filename existence as validity. This breaks the project's output identity rule: anything that changes a product's bytes must be in its filename.
- **Fix:** None. **Update:** v2 keys every cached layer and the lookup by manifest_of(input sizes + mtimes, lookup key, radar window, LAYER_VERSION); outputs go only under comparison_v2/.
- **Cost:** A rebuilt lookup would silently be combined with layers geocoded through the old one.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** compare_four_way.py:437-438, 491-494, 541-547, 567-573.
- **Automation lesson:** Key cache files by a hash of all inputs and parameters (source paths and mtimes, lattice, convention, tolerance). Invalidate dependents when a parent rebuilds. Comparison tools must never write into workflow product directories.

#### CMP-17 — compare_four_way.py hard-codes case-specific constants, blocking reuse in an automated pipeline

- **Symptom:** Not a failure yet, but the tool only works for this case.
- **Root cause:** Defaults and literals: --case, --kml (/home/sharath/asf_slc/glof_exact_aoi.kml), --tag (lines 293-296); EPSG:32645 (338, 461, 497); ionosphere looks `ly, lx = 9, 8` (674); asymmetric product names 'ifg_A_HH.igram.tif' vs 'ifg_A_HH_1x1.igram.tif' and 'amp_A_HH_{d}.tif' vs 'amp_A_HH_1x1_{d}.tif' (315-316, 589-590), a legacy of the full-tile products predating the looks-in-name prefix; F0/F1 (107).
- **Fix:** None.
- **Cost:** Porting to another frame or pair needs code edits and is error-prone.
- **Caught by:** assistant · **Status:** open
- **Evidence:** compare_four_way.py:107, 293-296, 315-316, 338, 461, 497, 589-590, 674.
- **Automation lesson:** Drive the comparison from the run manifests of the legs (product paths, looks, EPSG, center frequencies read from HDF5 metadata). Rename or alias legacy products so every leg follows one naming template.

#### CMP-18 — STATE.md, the resume document, still asserts withdrawn or false claims

- **Symptom:** STATE.md still says: ionosphere 'RSLC full -1.4390 / GSLC full -1.4284 <- agrees to 0.7% / GSLC cropped -1.2270 <- within the +/-0.235 TECU/cycle degeneracy'; that relative X_DATASET paths 'silently produce an EMPTY output'; leg 1 'RUNW 1x1 ... iono in RUNW'; that results cover 'the TRUE AOI'; and 'Nothing is held in memory, no process needs to stay alive' (written while a warp was running), with a re-create path pointing into /tmp.
- **Root cause:** STATE.md was written mid-analysis at 10:45 UTC and not updated as findings were corrected. At 13:56 the assistant deliberately deferred fixing it until the verifiers finished.
- **Fix:** None yet (pending the planned documentation pass). **Update:** STATE.md rewritten 2026-09-14 with a 'Withdrawn or refuted claims' list, and updated 2026-09-15 after comparison v2.
- **Cost:** Anyone resuming from STATE.md, including an automation effort, would inherit four wrong facts.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** STATE.md sections 'Results so far', 'WHAT IS LEFT' item 1, 'The four legs' table, header; jsonl L4702: 'I'm deliberately not touching `STATE.md` yet, even though it still contains the withdrawn ionosphere explanation'.
- **Automation lesson:** Generate state and resume documents from machine-readable run manifests and comparison.json, not by hand. Stamp every claim with the artifact it came from, so a retraction updates every document that uses it.

#### CMP-19 — Doppler-carrier constant computed with the acquisition PRF instead of the radar-grid line rate

- **Symptom:** A verifier's zero-parameter model used 2*pi*957/1909.635 = 3.15 rad/line; free fits gave 3.6-3.9.
- **Root cause:** geocodeSlc.cpp:473-474 uses radarGrid.prf(), which is the line rate 1/az_time_interval = 1520 Hz on these products, not the 1909.6 Hz acquisition PRF. Correct k = 2*pi*957/1520 = 3.96 rad/line.
- **Fix:** compare_four_way.py v2 uses 1/az_time_interval (verified RadarGridParameters.prf == 1520.0).
- **Cost:** The v1-era attribution numbers (offset -0.060 rad, 0.932, 0.987/0.994) were computed with a constant 25.6% too small.
- **Caught by:** guardrail/tool check · **Status:** fixed
- **Evidence:** critic.json contradictions #1; RadarGridParameters.prf check 2026-09-14
- **Automation lesson:** Name rates explicitly (acquisition PRF vs line rate) and take them from the object the algorithm uses.

#### CMP-20 — RUNW pixelOffsets are in metres; the v1 comparison labelled them pixels

- **Symptom:** comparison.json C1 reported median_px/std_px and tested a frame-shift hypothesis in mixed units.
- **Root cause:** pixelOffsets/HH/{alongTrackOffset,slantRangeOffset} attribute units='meters'.
- **Fix:** v2 reports metres and converts to radar samples with the RSLC spacings.
- **Cost:** Mislabelled statistics in v1; the conclusion (full vs crop ~0) survived.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** critic.json contradictions #6; product attributes
- **Automation lesson:** Read units from product attributes and carry them into every derived statistic.

#### CMP-21 — Opposite sign conventions for the same named pairs within one comparison.json

- **Symptom:** I3 radar block used crop-minus-full; the cross-track block used full-minus-crop; labels inverted.
- **Root cause:** Argument order of screen_agreement differed between call sites.
- **Fix:** v2: one convention, P__vs__Q = P minus Q, enumerated in leg order with the benchmark first; stated in the JSON.
- **Cost:** A verifier reported the labels the wrong way round.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** critic.json contradictions #7; verdicts.json V4
- **Automation lesson:** Encode the sign convention in the key name and assert it in tests.

#### CMP-22 — Pre-run review of comparison tool v2 found 15 defects, including a wrong plane estimator

- **Symptom:** Lag-1 gradient plane lowered agreement after 'removal' (0.887 -> 0.856 on real data, ML plane gives 0.913); I1b used R_crop's azimuth residual to model R_full; C1 offset grids 10 lines apart flagged 'exact'; np.allclose default rtol tolerated ~1300 lines; side-band cells 1/8 misaligned; ~16 GB peak memory; empty-LUT caching; cache keys blind to builder changes; results written only at the end.
- **Root cause:** New code written quickly under time pressure.
- **Fix:** All 20 fixes applied before the run; synthetic tests confirm plane recovery, coherence estimator and cycle decomposition.
- **Cost:** ~26 min of three reviewer agents; avoided a wrong or failed 45-min run.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** comparison/verification/compare_v2_code_review.json
- **Automation lesson:** Review statistics code with independent lenses and synthetic truth before running it on products.

#### CMP-23 — Legacy comparison tool asc/compare/compare_tracks.py is Venezuela-hardcoded; its water check cannot run on landlocked Nepal

- **Symptom:** igram_metrics.py:18 LAMBDA and expected.py baseline/incidence are Venezuela values.
- **Root cause:** Tool never generalised; superseded by tools/compare_four_way.py.
- **Fix:** Superseded; no water-floor check in the new tool.
- **Cost:** None now.
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** TRACK_R.md:469-475; critic.json missing_problems
- **Automation lesson:** Case constants belong in configuration, never in module globals.

#### CMP-24 — gdalwarp -geoloc lookup skipped output chunks and carried a half-cell bias

- **Symptom:** With errorThreshold 0.125 the geolocation-array warp left rectangular holes (66% AOI coverage) and a systematic +2.54 m E / +2.60 m N residual.
- **Root cause:** GDAL cannot compute a source window for some output chunks of a rotated, terrain-distorted swath and skips them; the transformer approximation adds a half-cell bias.
- **Fix:** Replaced by a KD-tree nearest-radar-sample lookup with an independent index re-verification (max 1.9e-6 m) and a mean-residual gate (|mean| <= 1 m); rejected raster kept as layers/_REJECTED_gdalwarp_lut_chunk_skips.tif.
- **Cost:** One comparison attempt (EXIT=143) and ~30 min.
- **Caught by:** guardrail/tool check · **Status:** fixed
- **Evidence:** logs/compare_four_way_v2_rejected_gdalwarp_lut.log; comparison_v2/comparison.json lookup_verification
- **Automation lesson:** Never trust a warp-based geolocation lookup without checking coverage and the mean residual vector.

#### CMP-25 — Amplitude cross-correlation estimator pixel-locked sub-sample offsets

- **Symptom:** Synthetic 0.3-sample shifts read as 0.08 (gain 0.10); the first alignment_test run stopped at the calibration stage.
- **Root cause:** Detected amplitude is not band-limited, so upsampled-DFT refinement locks to integer peaks.
- **Fix:** Complex cross-correlation of Tukey-tapered chips, upsample 100, no spectral whitening (gain 0.987 on synthetic chips).
- **Cost:** One aborted run (~6 min); outputs kept as alignment/_ABORTED_amplitude_estimator_alignment.json.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** logs/alignment_test_ABORTED_amplitude_estimator.log
- **Automation lesson:** Gate every offset estimator on a synthetic calibration before using it.

#### CMP-26 — Complex estimator without carrier demodulation read RSLC azimuth delays as -0.28x

- **Symptom:** RSLC control: geometry-only secondary vs rubber sheet gave azimuth slope -0.27 (r -0.14 with outliers, -0.88 without); range was correct.
- **Root cause:** The RSLC azimuth band is centred on the Doppler carrier (0.63 of the line rate) and wraps across Nyquist; a physical sub-sample delay leaves a phase step at the wrap that a plain correlator misreads. The synthetic test used FFT-bin shifts, which have no such step, so it passed.
- **Fix:** Demodulate both chips by their joint spectral centroid before correlating; calibrate with physical delays (demodulate, shift, remodulate): gain 0.998 az / 0.999 rg; naive estimator measured at -0.277.
- **Cost:** One full alignment run (~15 min) invalid; outputs under alignment/_ABORTED_run2_nodemod_estimator/.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** alignment/alignment.json E0_calibration (carrier_aware vs naive)
- **Automation lesson:** Calibrate registration estimators with physically realistic delays of carrier-modulated signals, not bin shifts.

#### CMP-27 — Regression of chip offsets dominated by low-coherence integer-pixel outliers

- **Symptom:** OLS slopes meaningless on the first control: 3.5% of chips jumped by up to +/-5 samples.
- **Root cause:** Correlation peaks in decorrelated chips pick whole-pixel side lobes.
- **Fix:** Chip selection (coherence >= 0.5, |displacement| < 1 sample), Theil-Sen alongside OLS, block bootstrap CIs.
- **Cost:** Diagnosis only.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** alignment/alignment.json E1_rslc_control selection
- **Automation lesson:** Use robust fits and quality gates on offset fields.

#### CMP-28 — Sub-pixel registration and Fourier realignment attempted on delivered GSLCs with white spectra

- **Symptom:** 52% of GSLC chips returned exactly zero shift; the realignment variants were not interpretable.
- **Root cause:** Every 256x256 GSLC chip has ~98% of its spectrum within 6 dB of peak (RSLC: ~39%); band-limited estimation and Fourier shifting are invalid on such samples. The spectra were not checked before designing the test.
- **Fix:** Registration measured on the RSLC (reference vs geometry-only vs rubber-sheet secondary) and the GSLC compared by closure; tool v2 kept as tools/legacy/alignment_test_v2_invalid_gslc_shift.py.
- **Cost:** ~25 min of runs and a rewrite.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** alignment/alignment.json E2_spectral_occupancy
- **Automation lesson:** Check spectral occupancy before any sub-pixel resampling or registration of geocoded SLCs; realign GSLCs inside geocoding (az_time_correction / srange_correction), not afterwards.

#### CMP-29 — Misregistration coherence loss first estimated as < 1% (azimuth only)

- **Symptom:** Report section 9 listed misregistration as too small to explain the ~5% GSLC coherence gap.
- **Root cause:** Only the 0.05-line azimuth residual was considered; the uniform 0.24-sample range residual, rho_rg(0.25) = 0.948, dominates.
- **Fix:** alignment_test E5: predicted factor 0.944 from measured spectra; report and COMPARISON.md corrected.
- **Cost:** A wrong statement in a published report revision (v1-v2).
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** alignment/alignment.json E5_predicted_coherence_loss
- **Automation lesson:** Evaluate every registration axis when predicting coherence loss.

#### CMP-30 — Unverified place name and a hand-typed number in the team report

- **Symptom:** The abstract named 'Imja-Lhotse' for the AOI; a 0.83% figure was typed from an ad-hoc check instead of read from JSON.
- **Root cause:** Writing prose from memory rather than from the evidence files.
- **Fix:** Place name removed; the number now reads comparison.json I3 modal whole cycles.
- **Cost:** Caught before forwarding.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** tools/build_report.py
- **Automation lesson:** Generate every number in a report from data files; do not name places that are not verified.

<!-- ERROR-LOG:END -->
