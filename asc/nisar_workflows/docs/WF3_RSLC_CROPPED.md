# WF3 — RSLC cropped to the AOI first: subset granules, then the ISCE3 InSAR chain

**Role in the comparison:** `R_crop`. Tests whether cropping the RSLC granules **before** coregistration
reproduces the full-tile benchmark (WF1) over the AOI, at a fraction of the cost.

**Evidence convention.** As in WF1/WF2 (`errors_W3_rslc_crop.json`, `errors_supplement.json`, `verdicts.json`
V1, `critic.json`, `compare_v2_code_review.json`). Quality results are in COMPARISON.md; this document covers
how the workflow works, what it costs, and what went wrong building it.

---

## 1. Purpose, scope, and when to use it

ISCE3's InSAR workflow has **no radar-domain area-of-interest option**. Every stage sizes itself from the granule's
own radar grid: `rdr2geo.py:81` (`slc.getRadarGrid(freq)`), `resample_slc_v2.py:168` (`out_length =
ref_radar_grid.length`), `crossmul.py:127-130`, `InSAR_L1_writer.py:277`. The options that look like an AOI do not
reduce the expensive work:

| option | what it actually restricts | source |
|---|---|---|
| `processing.geocode.top_left/bottom_right` | the **geocoded output** only (GUNW/GOFF); every radar stage still runs the full grid | `schemas/insar.yaml:1138-1149`, `geogrid.py:84-110`, `geocode_insar.py:633-636` |
| `processing.input_subset` | frequency and polarization selection | schema |
| `dense_offsets.start_pixel_range/azimuth`, `offset_width/length` | the dense-offset stage only (and `start_pixel_azimuth` is gated on `start_pixel_range`, WF3-03) | `dense_offsets.py:170-174` |

So crop-first has exactly one entry point: **make the granule smaller**. `tools/rslc_subset.py` writes a subset
RSLC that is an exact window of the original, with the metadata adjusted so ISCE3 accepts it unchanged; the
normal WF1 chain then runs on it.

**Use it** when the AOI is a small fraction of the frame and RSLC-quality coregistration is needed.
**Do not use it** for absolute ionosphere levels without an external reference (section 10).

Case (v2, the valid run): AOI `/home/sharath/asf_slc/glof_exact_aoi.kml` ("bigger_aoi", 51.75 × 32.49 km, a
4-corner polygon filling 97.6 % of its bbox), pair 20260714 × 20260726, freq A HH, crossmul 1×1, unwrap 9×8,
ionosphere on — the benchmark's science settings.

## 2. Inputs

- the two full granules of WF1 (WF1 section 2.1) and `C/aux/dem/dem_nepal_glof.tif`;
- the AOI KML (bbox or polygon; the tool uses the bbox, the comparison applies the polygon);
- `configs/nepal_glof_aoi_v2.yaml` (granules, out_root `C/aoi_v2`, `frequencies [A, B]`, `track_r.frequency A`,
  snaphu [4,4]/nproc 8/overlap 256; everything else as the benchmark config).

### 2.1 Why the crop is a bbox, not the polygon

A crop is a rectangular window in (line, sample); a lon/lat polygon maps to a curved region in radar coordinates,
which can only be honoured by masking — and masking saves no compute. The AOI polygon is applied at comparison time
instead, so statistics describe the true AOI.

### 2.2 Preconditions

| check | pass |
|---|---|
| full granules satisfy WF1 2.1 (A/B share starting range; `slantRangeA[8k] == slantRangeB[k]`; zero-Doppler) | verified by the tool before writing |
| AOI inside the frame, and the zero-Doppler radar bbox covers it per quadrant | `get_radar_bbox` with `LUT2d()`; coverage reported by the comparison |
| free disk for two ~1.8 GB granules and ~34 GB of scratch | yes |

## 3. Outputs

| artifact | path | measured (v2) |
|---|---|---|
| subset granules | `C/L1_RSLC_AOI_v2/{date}_aoi.h5` | 1.80 GB each (from 26.26 / 27.01 GB) |
| subset provenance | attributes on `/science/LSAR/RSLC/swaths`: `subset_tool`, `subset_source_granule`, `subset_geometry_doppler` = "zero", `subset_band_aligned` = 1, `subset_azimuth_origin`, `subset_range_origin_frequency{A,B}` | reference 7254 / 6792 / 849; secondary 8001 / 6784 / 848 |
| stack + DEM | `C/aoi_v2/stack.json`, `C/aoi_v2/aux/dem/` | geogrid pin 294000–417000 E, 3091000–3174000 N (16600 × 24600 at 5 m) |
| runconfig | `C/aoi_v2/cfg/insar_20260714_20260726_A_HH_1x1.yaml` | |
| scratch | `C/aoi_v2/scratch/trackR/20260714_20260726_A_HH_1x1/` | 34 GB (with the side-band ionosphere sub-run) |
| RIFG | `…/scratch/…/RIFG.h5` | 12303 × 22440 |
| RUNW 9×8 with ionosphere | `C/aoi_v2/pairs/20260714_20260726/trackR/RUNW_20260714_20260726_A_HH_1x1_unw9x8.h5` | 73.4 MB, 1367 × 2805 |
| logs | `C/logs/aoi_v2.log`, `aoi_v2_trackR.log`; ISCE3 journal `C/aoi_v2/logs/insar_…log` | |

The v1 run (native-Doppler, unaligned crop) is kept as evidence in `C/L1_RSLC_AOI/` and `C/aoi/`; an aborted
intermediate rerun in `C/L1_RSLC_AOI_ALIGNED_ABORTED_nativeDoppler/` and `C/aoi_aligned_ABORTED_nativeDoppler/`.

**Output identity.** Cropped products carry the same file names as the full-tile products and differ by directory
and by the subset attributes in the granule. A name-level identity for crops (AOI hash, crop origin, tool version)
is still **OPEN** (WF3-15).

## 4. Processing chain

### 4.1 The subsetter (`tools/rslc_subset.py`)

Steps, in order:

1. **AOI → UTM bbox** from the KML; **height bracket** from the DEM over the AOI (1295–7892 m here) instead of
   `get_radar_bbox`'s global −500…9000 m default.
2. **Radar bbox per band** with `isce3.geometry.get_radar_bbox(geo, radar_grid, orbit, hmin, hmax,
   doppler=LUT2d(), margin=50)` — **zero Doppler**.
3. **One azimuth window** (union over bands) — `zeroDopplerTime` sits above `frequencyA`/`frequencyB`, so the bands
   share one azimuth axis; buffered by `--buffer-az-lines 1000`.
4. **One range window on the finest band** (union of every band's bbox expressed in freq-A samples), buffered by
   `--buffer-range-m 12500` (slant) converted to samples (4003 freq-A samples).
5. **Snap** the range origin to a multiple of lcm(band ratio, range looks, band ratio × side-band looks) and the
   azimuth origin to a multiple of the azimuth looks. Every other band's window is the freq-A window divided by its
   ratio **exactly**, checked against the slant-range arrays. (v2 was made before the side-band term existed and
   snapped to 8, not 64 — section 10.)
6. **Footprint polygon** from the cropped radar grid (`offset_and_resize`, zero Doppler, mid-bracket height),
   simplified before writing — computed **before** any write handle is opened.
7. **Write** the subset HDF5: groups outside `swaths` copied verbatim; inside `swaths`, crop the closed list below;
   recompute identification times and polygon; write provenance attributes.

The closed list of grid-dependent datasets (checked on a real granule: nothing under `RSLC/metadata` has an axis of
53200/54244/6781; `geolocationGrid` is a coarse coordinate-referenced cube with its own axes, and orbit/attitude are
time-referenced):

| dataset | operation |
|---|---|
| `swaths/frequency{A,B}/{HH,HV}` | crop `[az0:az1, rg0:rg1]` (block-wise) |
| `swaths/frequency{A,B}/inputDataExceptionMask` | same crop |
| `swaths/frequency{A,B}/slantRange` | crop `[rg0:rg1]` |
| `swaths/frequency{A,B}/validSamplesSubSwath*` | crop rows; **rebase values by −rg0 and clip to the new width** (they are absolute sample indices) |
| `swaths/frequency{A,B}/listOfPolarizations` | rewritten when polarizations are dropped |
| `swaths/zeroDopplerTime` | crop `[az0:az1]` |
| `identification/zeroDopplerStartTime/EndTime` | recompute; always 9 fractional digits |
| `identification/boundingPolygon` | recompute from the cropped grid; new dtype sized to the string |

### 4.2 The three geometry invariants (each was a real bug)

**(a) Zero Doppler.** The first version placed the window with the native Doppler centroid (~957–983 Hz). On
these zero-Doppler products that moved the AOI centre ~3300 lines (~15 km) along track: the crop covered lines
5012–15231 where the AOI needs 8262–18556, so the north of the AOI was missed (its NW third was 7.5 % covered)
and all v1 cropped statistics described ~73 % of the AOI (WF3-28).

**(b) One A/B range origin relation on every date.** ISCE3's side band for the ionosphere is built by dividing the
freq-A range offsets by 8 using only the **reference** granule's slant ranges
(`nisar/workflows/ionosphere.py:122-237`, `decimate_freq_a_offset`). The first version buffered each band's range
window independently, giving A-to-B relations of 482.125 B px on the reference and 481.500 on the secondary.
ISCE3 therefore mis-registered freq B by 0.625 B px (15.6 m): freq-B coherence fell from 0.573 to 0.428 and the B
interferogram carried a non-integer +1.75 rad (flattening error alone predicts +1.64), worth about −1.49 TECU in
the screen. Freq-A wrapped phase and coherence were unaffected (verdicts V1; WF3-08). v2 satisfies
`8·rg0_B − rg0_A = 0` on both dates.

**(c) Origins on every downstream look grid.** With origins that are not multiples of the looks, multilooked
products land sub-look misaligned with the full tile (v1: 3 azimuth rows, 1 range column at 9×8), and the side band
(8 range looks on the B grid) needs the freq-A origin on a multiple of 64. v2 origins are multiples of 9 and 8; its
side-band cells are ⅛ cell (25 m) from the full tile's, and the dense-offset grid (start 52, step 32 samples) is 10
lines apart — the comparison interpolates both (WF3-31).

### 4.3 Buffers are set by the widest spatial operator

| operator | spatial support |
|---|---|
| dense offsets | window 64 + half-search 20 → ~52 px dead band |
| rubbersheet | offset-field filtering |
| resampling | ~8 px interpolation kernel |
| ionosphere (ISCE3) | Gaussian 100 px / σ 33 px on the decimated freq-B grid ≈ 20 km × 4 km kernel, σ ≈ 6.6 km in range |

Coregistration alone would be satisfied by ~500 px; the ionosphere filter is the binding constraint, hence
12.5 km slant range (~2σ, = 500 freq-B px) and 1000 lines azimuth. For a crop that stops at the interferogram,
much smaller buffers suffice. The price: the v2 crop is **276 Mpx, 9.6 % of the radar grid** (the AOI itself is
1.77 % of the geocoded frame but ~4.9 % of the radar grid, because relief and the skew of a map rectangle stretch
its radar footprint).

### 4.4 The InSAR chain on the subset

Identical to WF1 section 4 with `product_type RUNW` in one integrated run: rdr2geo → geo2rdr → coarse resample →
dense offsets → rubbersheet → fine resample → crossmul 1×1 → RIFG → unwrap 9×8 → ionosphere (`main_side_band`) →
baseline. Stage times from the ISCE3 journal (`C/aoi_v2/logs/insar_…log`), with the GSLC leg (WF4) running
concurrently on the same 8 cores:

| stage | seconds |
|---|---|
| rdr2geo | 1292 |
| RIFG preparation incl. the 1×1 interferogram-grid DEM pass | 1425 |
| resample (coarse; fine) | 119; 57 |
| crossmul | 129 (+73) |
| side band: rdr2geo / prepare / resample / crossmul | 88 / 102 / 8 / 11 |
| Ionosphere | 165 |
| **INSAR total** | **5342 (1h29m)** |

The v1 crop (165 Mpx, not concurrent) ran the same chain in 33 min; the full tile took 8h34m through RIFG alone
(WF1 8.3).

**Confound.** The benchmark's 9×8 ionosphere came from the standalone unwrap entry point run against the finished
full-tile RIFG, while the crop ran the integrated workflow (WF3-32).

## 5. How to run it

```bash
source /home/sharath/miniforge3/etc/profile.d/conda.sh && conda activate isce3_env
cd /home/sharath/isce3/case_studies/nepal_glof
T=/home/sharath/isce3/asc/nisar_workflows
for d in 20260714 20260726; do
  python -u $T/tools/rslc_subset.py --rslc $(ls L1_RSLC/*_${d}T*.h5) --out L1_RSLC_AOI_v2/${d}_aoi.h5 \
      --kml /home/sharath/asf_slc/glof_exact_aoi.kml --dem aux/dem/dem_nepal_glof.tif --polarizations HH
done                                   # add --dry-run first to print windows without writing

cd $T
CFG=configs/nepal_glof_aoi_v2.yaml
python run_track_r.py -c $CFG --only ingest
python run_track_r.py -c $CFG --only dem
python run_track_r.py -c $CFG --only runconfig
python run_track_r.py -c $CFG --only insar
```

`rslc_subset.py` refuses to overwrite an existing output, verifies the A/B relation before writing, and prints the
per-band bboxes, buffers, snapped windows and the fraction of the full grid. `--no-align` reproduces the legacy
crop for provenance only.

The v2 run used one wrapper (`C/logs/run_aoi_v2.sh`) that subsets both dates, ingests **once**, stages the DEM,
then runs this chain and WF4's chain in parallel — so `stack.json` is never regenerated under a running consumer
(WF4-01).

## 6. Parameter reference

| parameter | value | rationale |
|---|---|---|
| `--buffer-az-lines` | 1000 | one ionosphere kernel width in azimuth (~4.5 km) |
| `--buffer-range-m` | 12500 | ~2σ of ISCE3's ionosphere Gaussian in range |
| `--align-rg-looks` | 8 | unwrap/ionosphere range looks |
| `--align-az-looks` | 9 | unwrap/ionosphere azimuth looks |
| `--align-sideband-looks` | 8 (snap to 64; added after v2) | freq-B side-band range looks |
| `--margin` | 50 | `get_radar_bbox` internal margin |
| `--polarizations` | HH | halves the write; `listOfPolarizations` rewritten |
| height bracket | from the DEM over the AOI | tighter than −500…9000 m (the default widens the footprint ~5.6 % here) |
| everything in `track_r` | as the benchmark (WF1 section 6) | isolate the effect of cropping |

## 7. Required patches and upstream issues

No additional overlays: the WF1 overlays apply. Upstream facts this workflow depends on or works around:
no radar-domain AOI option (section 1); zero-Doppler geometry (`rdr2geo.py:64-65`, `geo2rdr.py:52-53`);
side band derived from the reference granule only (`ionosphere.py:122-237`); `dense_offsets` gates
`start_pixel_azimuth` on `start_pixel_range` (WF3-03, latent); fixed-width `identification/boundingPolygon`
(`|S2087`) that truncates a longer WKT silently.

## 8. Resource model

| item | v2 value |
|---|---|
| subset, per date | ~2.3 min; 1.80 GB |
| radar grid | 12303 × 22440 = 276 Mpx (9.6 % / 9.3 % of the two full grids) |
| scratch | 34 GB (≈104 B/px × 276 Mpx plus the ionosphere side-band sub-run, which the disk-gate model omits, WF3-22) |
| InSAR chain | 1h29m sharing 8 cores with WF4 |
| RAM | well within 31 GB (the WF1 memory hazards scale with the grid) |

Against WF1: ~10× fewer pixels, ~9× less scratch, and hours instead of ~21 h of compute for coregistration and the
1×1 unwrap — but not the 56× that the AOI's 1.77 % of the map frame suggests.

## 9. Validation gates for automation

| gate | how | pass | v2 |
|---|---|---|---|
| exact window | crop `zeroDopplerTime`/`slantRange` equal a slice of the full axes to 1e-3 sample (not `np.allclose` defaults) | exact | exact (comparison v2) |
| A/B relation | `8·rg0_B − rg0_A` equal on every date | 0 on both | 0 / 0 |
| look-grid snapping | `az0 % 9`, `rg0 % 8` (and `% 64` for the side band) | 0 | 0, 0 (64: no) |
| zero-Doppler coverage | fraction of the AOI polygon covered per quadrant | ≥ 0.95 each | reported in comparison v2 |
| footprint polygon | parses as WKT; lies inside the source footprint | yes | yes |
| identification times | ISO string with 9 fractional digits | yes | `2026-07-14T23:39:24.772368000` |
| provenance attributes | all `subset_*` present | yes | yes |
| ingest pin | geogrid derived from the subset footprint, not the full scene | smaller than the full pin | 16600 × 24600 vs 60600 × 62800 |
| inherited paths | `unwrap_crossmul_path` null; no path points into the full-tile tree | yes | yes |
| RUNW sanity | ionosphere screen finite and non-zero; side-band coherence close to the full tile's | yes | see COMPARISON I3 |

## 10. Known limitations and open questions

- **OPEN — absolute ionosphere level.** ISCE3 `main_side_band` has no absolute cycle referencing (WF1 4.1). In v1
  the cropped screen differed from the full tile by +3.59 TECU in level (−1 cycle in A, −2 whole cycles in B, plus
  the −1.49 TECU non-integer part from bug (b)) while its shape agreed (residual 0.026 TECU). The v2 re-measurement
  with the bug fixed is in COMPARISON I3.
- **OPEN — no 1×1 unwrapped phase** for the crop (the benchmark has one) (WF3-25).
- v2 side band ⅛ cell from the full tile's cells (fixed for future crops; comparison interpolates).
- Entry-point confound for the ionosphere comparison (WF3-32).
- Crop products lack a name-level identity (WF3-15); the disk gate omits the ionosphere sub-run (WF3-22).

## 11. Problems and errors log

<!-- ERROR-LOG:BEGIN -->
33 entries: 1 caught by the user, 23 were the assistant's own mistakes of judgement, 6 still open. Every entry cites its evidence; the full records are in `case_studies/nepal_glof/comparison/verification/`.

| id | problem | category | caught by | assistant error | status | cost |
|---|---|---|---|---|---|---|
| WF3-01 | ISCE3 has no radar-domain AOI option, so the only way to crop first is to make the granule smaller | geometry | assistant | no | worked around | Design constraint rather than lost time. The subsetter took about 15 min to write and run. |
| WF3-02 | The crop-feasibility finding was lost in compaction and re-derived by a 68-agent sweep; 9 verifiers died at the session limit | geometry | assistant | yes | worked around | About 2.5 h of wall clock between the sweep launch and the resumed build (with the session-limit stall in between), a large amount of agent quota, and 9 lost ve |
| WF3-03 | Latent ISCE3 bug: dense_offsets gates start_pixel_azimuth on start_pixel_range | software | assistant | no | open | None so far. This is the one partial radar-windowing knob the sweep found, so it would bite any pipeline that tried to use it. |
| WF3-04 | Crop savings overstated: 1.77% of the map grid was reported as a 56x reduction, but the radar footprint is 4.7-5.7% | geometry | assistant | yes | fixed | A wrong number stayed in front of the user for about 1.5 h; no processing was lost. |
| WF3-05 | get_radar_bbox defaults to a -500..9000 m height bracket; replaced with the AOI's DEM range | other | assistant | no | fixed | Negligible. |
| WF3-06 | zeroDopplerTime is shared by both frequencies, so there can be only one azimuth window | geometry | assistant | no | fixed | None; it was designed around before it could bite. |
| WF3-07 | validSamplesSubSwath holds absolute sample indices, so it must be rebased and clipped | other | assistant | no | fixed | None; designed in. |
| WF3-08 | Pixel buffer applied per band breaks the shared A/B starting range; ISCE3 zero-fills about 34% of freq B columns | science | assistant | yes | fixed | About 34% of freq B processing wasted. Possible influence on the R_crop ionosphere is not quantified. |
| WF3-09 | Each date cropped to its own window, and the crop origin is not written into the product | geometry | guardrail/tool check | no | fixed | Extra verification work; a potential source of confusion in any comparison. |
| WF3-10 | Stale full-scene boundingPolygon: an HDF5 SWMR flag conflict let the subset finish with EXIT=0 | judgement | guardrail/tool check | yes | fixed | Led directly to the wrong geogrid in the next entry; about 6 minutes of diagnosis and repair. |
| WF3-11 | Stale polygon made ingest silently pin a full-scene 60400x62800 geogrid | geometry | assistant | no | worked around | About 2 minutes. Had it gone unnoticed, the downstream Track G crop would have geocoded a 28 GiB full-scene grid. |
| WF3-12 | The assistant's own boundingPolygon repair truncated the WKT into the fixed-width \|S2087 dtype; ingest then failed three times | judgement | crash | yes | fixed | About 2 minutes and three failed ingests. |
| WF3-13 | A failed ingest left the stale stack.json, and grep filters hid the ERROR lines, so the assistant misdiagnosed | judgement | assistant | yes | open | About 1.5 minutes and a wrong line of investigation. |
| WF3-14 | Cropped granules were patched in place by ad-hoc scripts; the current tool has never run end-to-end | geometry | assistant | yes | fixed | No direct loss; a reproducibility gap in the input to the whole workflow. |
| WF3-15 | Output identity: cropped products differ from the full-tile products only by directory, and carry the source granule's identity | geometry | assistant | yes | open | None yet. This is the failure mode the rule says has 'silently clobbered products six times'. |
| WF3-16 | Latent: the subsetter's rewrite of zeroDopplerStart/EndTime truncates to microseconds and would break when microseconds are zero | software | assistant | yes | fixed | None observed. The precision loss (<1 us) is negligible against the 658 us line spacing. |
| WF3-17 | Creating the AOI config with str.replace hit a commented line and left a corrupted comment behind | judgement | assistant | yes | worked around | About 1 minute. A near miss for cross-contaminating the workflow 1 tree. |
| WF3-18 | The AOI config still defaults to frequency B and 9x1 looks; the real parameters lived only in CLI flags in /tmp scripts | judgement | assistant | yes | fixed | Reproducibility risk: re-running from the config alone would run freq B at 9x1. |
| WF3-19 | The launch script asked for product_type RIFG, which cannot reach ionosphere; the config guard rail refused | science | guardrail/tool check | yes | fixed | About 1.5 minutes. The guard rail (nisar_wf/config.py) prevented a silent run without ionosphere. |
| WF3-20 | unwrap_crossmul_path inherited from the full-tile config pointed at the full-scene RIFG; the claimed consequence was overstated | judgement | assistant | yes | fixed | About 1 minute. The overclaim is now documented as a trap in STATE.md. |
| WF3-21 | Snaphu tiling changed from the benchmark ([4,4]/nproc 8 to [2,2]/nproc 4) without an observed failure | geometry | assistant | yes | fixed | Adds a confound to the R_full-versus-R_crop unwrapped and ionosphere comparison, including the open +3.6 TECU item. No runtime issue. |
| WF3-22 | The disk-gate scratch model leaves out the ionosphere sub-run | resources | assistant | no | open | None this time. The gap would matter on a tight disk or a larger crop. |
| WF3-23 | Red flag missed: the R_crop ionosphere read +32.6 rad against the benchmark's -19.6 rad and was reported as success | science | assistant | yes | worked around | About 5 h during which a suspect result was presented as complete, and the first STATE.md summary omitted it. |
| WF3-24 | Crop origin not snapped to multiples of looks or dense-offset skip, so multilooked and offset grids are sub-sample misaligned | geometry | assistant | yes | worked around | The 9x8 RUNW and ionosphere comparisons carry a sub-look shift, and the full-tile offset field cannot be reproduced exactly. |
| WF3-25 | Resolution parity gap: R_crop has no 1x1 unwrapped phase, unlike the benchmark | science | user | yes | open | None of the reported comparisons use 1x1 unwrapped phase, but the leg does not fully mirror the benchmark. |
| WF3-26 | Crop origin not a multiple of the looks, so the 9x8 RSLC products are sub-look misaligned and the full-vs-crop ionosphere comparison is not pixel-exact | science | assistant | yes | fixed | The I3 R_full-vs-R_crop ionosphere comparison carries a 3-row/1-col sub-look shift. It is a small part of the observed difference, but it makes that comparison  |
| WF3-27 | Cropped granules do not cover the NW ~18-20% of the user's AOI; the comparison silently dropped it while being reported as 'the true AOI' | geometry | assistant | yes | fixed | All four-way statistics describe ~73% of the requested AOI, and the glacier-lake NW part may be missing from every comparison. The report would overstate covera |
| WF3-28 | Crop window placed with the NATIVE Doppler centroid on a zero-Doppler product: the north of the AOI was missed | geometry | guardrail/tool check | yes | fixed | v1 cropped legs (R_crop 33 min, G_crop ~22 min) and their comparison invalidated for the north of the AOI; an aligned rerun aborted after 38 min. |
| WF3-29 | Aligned rerun changed freq-A extent as well as alignment, and inherited the native-Doppler bug: aborted | software | guardrail/tool check | yes | fixed | ~38 min of compute; 21 GB of aborted outputs kept as evidence. |
| WF3-30 | Stopping the aborted run with pkill -f killed the agent's own shell (exit 144) -- a known trap repeated | judgement | crash | yes | fixed | One failed command; no data loss. |
| WF3-31 | Freq-B side-band look cells not aligned for crops snapped to multiples of 8 only | geometry | guardrail/tool check | yes | fixed | Diagnostic comparability only. |
| WF3-32 | Different ISCE3 entry points for the full-tile and cropped ionosphere runs | science | guardrail/tool check | no | open | Unquantified confound. |
| WF3-33 | Cycle-class rule on a crop recovers the class but its tie-break lands one joint cycle from the full tile | geometry | guardrail/tool check | no | by design | None. |

**Recorded elsewhere, relevant here:** +3.59 TECU R_full vs R_crop ionosphere offset -- decomposed in this log (A/B origin bug + whole cycles) and re-measured in COMPARISON.

Cross-cutting operational problems (process supervision, reboots, logs, disk, agent harness) are in OPERATIONS_AND_LESSONS.md.

#### WF3-01 — ISCE3 has no radar-domain AOI option, so the only way to crop first is to make the granule smaller

- **Symptom:** There is no runconfig knob that windows the radar grid. Every stage sizes itself from slc.getRadarGrid(freq). The only documented AOI option (processing.geocode.top_left/bottom_right) limits the geocoded output only.
- **Root cause:** Upstream design. rdr2geo.py:81 and geo2rdr.py:72 call slc.getRadarGrid(freq) on the whole grid. schemas/insar.yaml:101-109 shows input_subset selects frequency/polarization only. dense_offsets start_pixel_range/azimuth (insar.yaml:336-339, dense_offsets.py:169-175) windows only the offsets stage. geocode top_left/bottom_right (insar.yaml:1138-1149) runs after the full-grid stages.
- **Fix:** Wrote tools/rslc_subset.py, which writes a smaller, valid RSLC: it crops the image, mask, slantRange, zeroDopplerTime and validSamples and rewrites the identification fields, then feeds that granule to the unmodified chain. Primitives were re-checked by hand at 08:06-08:07: RadarGridParameters.offset_and_resize, isce3.geometry.get_radar_bbox, nisar.products.writers.SLC, and the absence of any grid-shaped dataset under RSLC/metadata.
- **Cost:** Design constraint rather than lost time. The subsetter took about 15 min to write and run.
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** L1300 (2026-09-04): 'rdr2geo.py:81 and geo2rdr.py:72 both call slc.getRadarGrid(freq) and take the full grid. No runconfig knob crops it.' L3974: 'There is no crop first mechanism in ISCE3 ... restricts the geocoded output only.' L4018: 'metadata datasets with a 53200/54244/6781 axis' returned none. rslc_subset.py:14-31.
- **Automation lesson:** Make crop-first its own pipeline module that emits a self-consistent L1 RSLC, and keep downstream stages unaware of it. Do not look for a runconfig option; none exists in ISCE3 0.25.12.

#### WF3-02 — The crop-feasibility finding was lost in compaction and re-derived by a 68-agent sweep; 9 verifiers died at the session limit

- **Symptom:** On 2026-09-04 the assistant had already worked out that no crop knob exists and that offset_and_resize plus writers.SLC make a subsetter buildable. After compaction only the user's question survived in the summary. On 09-14 a 68-agent research sweep was launched (04:38) to answer the same question. The journal shows 68 started, 59 results, 9 failed. The session limit hit at 05:36, and nothing moved on this workflow until the user came back at 08:02.
- **Root cause:** The compaction summary kept the user's quote ('can we crop the imagery to our aoi') but dropped the technical answer from L1329. The assistant then chose a large parallel sweep over grepping the full transcript, which the summary itself pointed to. That sweep ran into the session quota.
- **Fix:** The key primitives and code paths were re-verified by hand before building anything (L4001-L4021). The unverified geometry claims were not used.
- **Cost:** About 2.5 h of wall clock between the sweep launch and the resumed build (with the session-limit stall in between), a large amount of agent quota, and 9 lost verifier results.
- **Caught by:** assistant · assistant error · **Status:** worked around
- **Evidence:** L1329 (09-04): 'no crop knob exists ... cropping is very buildable ... I'd build the crop instead.' Summary at L3384 keeps only the user quote. L3991: Counter({'started': 68, 'result': 59, 'failed': 9}). L3954: 'You've hit your session limit · resets 8am (UTC)'. L3974: '9 verification agents in the geometry angle died on a session limit'.
- **Automation lesson:** Save design findings (API primitives, file:line) in a durable project document such as STATE.md or TRACK_R.md when they are found, not only in chat. Before any expensive multi-agent research, check existing notes and the transcript.

#### WF3-03 — Latent ISCE3 bug: dense_offsets gates start_pixel_azimuth on start_pixel_range

- **Symptom:** Not hit in the run. In dense_offsets.py the azimuth start pixel is used only when start_pixel_range is set, so setting start_pixel_azimuth alone is silently ignored.
- **Root cause:** Copy-paste error at nisar/workflows/dense_offsets.py:173-175: `referenceStartPixelDownStatic = cfg['start_pixel_azimuth'] if cfg['start_pixel_range'] is not None else margin + halfSearchRangeDown`.
- **Fix:** None applied. Workflow 3 does not use the dense_offsets window lever.
- **Cost:** None so far. This is the one partial radar-windowing knob the sweep found, so it would bite any pipeline that tried to use it.
- **Caught by:** assistant · **Status:** open
- **Evidence:** dense_offsets.py:169-175 (found while verifying this log, not in the session). The sweep listed 'processing.dense_offsets.{start_pixel_range, start_pixel_azimuth, ...}' as a real windowing option (L3994).
- **Automation lesson:** If an automated pipeline sets dense_offsets start pixels, always set both, or patch the condition to test start_pixel_azimuth.

#### WF3-04 — Crop savings overstated: 1.77% of the map grid was reported as a 56x reduction, but the radar footprint is 4.7-5.7%

- **Symptom:** The assistant told the user 'AOI bbox 67.2 Mpx, 1.77% ... A 56x reduction'. The real freq A radar footprint was 4.70% (tight, AOI-local height bracket), 4.86% in the tool's run, and 5.72% after the union and 500 px buffer, about a 17x reduction.
- **Root cause:** The saving was estimated on the geocoded 5 m map grid. Terrain relief (1295-7892 m) and the skew of a map rectangle in radar geometry enlarge the footprint, and the buffer adds more.
- **Fix:** Corrected at L4038 and L4066 after get_radar_bbox was run on the real granule.
- **Cost:** A wrong number stayed in front of the user for about 1.5 h; no processing was lost.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L3832: '| AOI bbox | 67.2 Mpx | 1.77% | A 56x reduction.' L4031: 'freq A [AOI-local] ... 135.6 Mpx = 4.70% of 53200x54244'. rslc_subset.log: 'freq A: az 4512..15731 rg 10697..25416 -> 11219 x 14719 = 165.1 Mpx (5.72% of full)'.
- **Automation lesson:** Size crop jobs in radar coordinates with get_radar_bbox plus the buffer, before quoting savings or running the disk gate. Never use map-grid pixel counts.

#### WF3-05 — get_radar_bbox defaults to a -500..9000 m height bracket; replaced with the AOI's DEM range

- **Symptom:** The default bracket widens the radar window. Measured on this AOI, freq A range grew from 11258..24770 to 10977..25227: 4.96% of the grid instead of 4.70%, about 5.6% more pixels. The assistant called this 'inflate the box badly', which overstates a modest effect.
- **Root cause:** The isce3.geometry.get_radar_bbox signature has min_height=-500.0, max_height=9000.0 by default.
- **Fix:** rslc_subset.py:127-142 and 235 read the DEM min/max over the AOI plus 0.02 deg and pass them to get_radar_bbox (rslc_subset.py:148). Small leftover: the docstring (rslc_subset.py:82) says 1378..7892 m, but the tool's padded read gives 1295..7892 m.
- **Cost:** Negligible.
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** L4026: 'min_height ... = -500.0, max_height ... = 9000.0'. L4031: 'freq A [AOI-local] az 5168..15206 ... rg 11258..24770 = 135.6 Mpx = 4.70%' against '[global default] ... rg 10977..25227 = 143.2 Mpx = 4.96%'. L4029: 'would inflate the box badly'.
- **Automation lesson:** Pass a DEM-derived height bracket to get_radar_bbox and log both footprints. Keep the buffer as the real safety margin, not the height bracket.

#### WF3-06 — zeroDopplerTime is shared by both frequencies, so there can be only one azimuth window

- **Symptom:** This is a data trap. zeroDopplerTime sits at swaths/ level, above frequencyA and frequencyB, so both bands share one azimuth axis. Cropping each band to its own azimuth bracket would desynchronize them and corrupt the split-spectrum ionosphere.
- **Root cause:** NISAR RSLC layout (L1_RSLC swaths tree): one zeroDopplerTime (53200,) and a separate slantRange per frequency.
- **Fix:** rslc_subset.py:277-279 takes the union of the per-frequency azimuth brackets and applies it to both bands. Range is cropped per frequency.
- **Cost:** None; it was designed around before it could bite.
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** L1317 (09-04): 'zeroDopplerTime: n=53200 ... (SHARED by both freqs)'. rslc_subset.py:55-63. rslc_subset.log: 'shared azimuth window (union + 500 px buffer): 4512..15731'. L4081: freq A and freq B sensing_start are identical (85162.9684).
- **Automation lesson:** The subsetter must emit one azimuth window per granule for all frequencies, and a validation step should assert that the cropped zeroDopplerTime equals the source slice for every band.

#### WF3-07 — validSamplesSubSwath holds absolute sample indices, so it must be rebased and clipped

- **Symptom:** Copying validSamplesSubSwath1 unchanged would leave every validity test wrong. The values index the full 54244- or 6781-sample swath, not the crop.
- **Root cause:** The RSLC product stores [start, stop) valid sample bounds per line as absolute indices (writers/SLC.py:566-568).
- **Fix:** rslc_subset.py:391-400 crops the rows to the azimuth window, subtracts rg0 and clips to [0, width]. Verified: min 0, max 14719, which equals the width.
- **Cost:** None; designed in.
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** rslc_subset.log: 'validSamplesSubSwath1: rebased by -10697, clipped to 14719' and 'rebased by -855, clipped to 2804'. L4081: 'validSamples: min 0 max 14719 (width 14719) -> in range: True'.
- **Automation lesson:** Have the crop module rebase every absolute-index dataset. Add a post-crop test that 0 <= validSamples <= width and that the valid fraction on a mid row matches the source.

#### WF3-08 — Pixel buffer applied per band breaks the shared A/B starting range; ISCE3 zero-fills about 34% of freq B columns

- **Symptom:** Not noticed in the session. The 500 px buffer is in each band's native pixels: 1.56 km for A but 12.5 km for B, so B's window is 8.72% of its grid against 5.72% for A. After cropping, A starts at 911647.0 m and B at 899602.2 m, whereas the full-tile granules share 878242.006 m, the property TRACK_R.md section 5 relies on. ISCE3's decimate_freq_a_array fills B columns outside A's range span with zero offsets. In the AOI scratch, geo2rdr/freqB/azimuth.off and rubbersheet_offsets/freqB/HH/azimuth.off have 964 of 2804 mid-row columns set to 0 (only columns 483-2322 are non-zero). Coherence in those freq B edge strips is 0.123 against 0.425 inside, and almost none of those pixels are in connected components.
- **Root cause:** rslc_subset.py:293-299 adds args.buffer in native range pixels per frequency. The zero-filling is in isce3/signal/interpolate_by_range.py:39-68, called from nisar/workflows/ionosphere.py:222-229. It pads outside A's slant-range span with constant 0, so those B pixels are resampled with no geometric shift (the true azimuth offset is about -78 px) and decorrelate.
- **Fix:** None. The ionosphere is interpolated back to A's range span only (interpolate_freq_b_array), so the direct effect may be small, but the garbage strips do go through freq B unwrapping and filtering. Not tested. **Update:** v2 (2026-09-14): one range window on freq A, freq-B origin = freq-A origin / 8 on every date; verified B0*8 - A0 = 0 for both dates in comparison_v2 crop_geometry.
- **Cost:** About 34% of freq B processing wasted. Possible influence on the R_crop ionosphere is not quantified.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L4081: 'freq A: radar grid 11219 x 14719 start_range 911647.0' / 'freq B: radar grid 11219 x 2804 start_range 899602.2'. rslc_subset.log: 'freq B: az 4512..15731 rg 855..3659 -> ... (8.72% of full)'. Verified in this log: geo2rdr/freqB/azimuth.off 'zeros 964 first nonzero col [483] last nonzero col [2322]'. ionosphere/main_side_band/RUNW.h5 freq B coherence 'cols 0-60: 0.123 / cols 60-289: 0.425 / cols 289-350: 0.123'. Found while verifying this log, not in the session.
- **Automation lesson:** Buffer in metres, or derive B's range window from A's (same start slant range, A width/8) so both bands keep a common starting range. Assert |startA - startB| < one B pixel before running main_side_band.

#### WF3-09 — Each date cropped to its own window, and the crop origin is not written into the product

- **Symptom:** The secondary crop starts at (5336, 10684) with shape 11213x14718; the reference starts at (4512, 10697) with shape 11219x14719. Ingest warned 'frequency A RSLC shapes differ across dates'. The origins exist only in the subset log; comparison code had to recover them from the coordinate arrays.
- **Root cause:** rslc_subset.py computes the window per granule from that granule's own orbit and Doppler (lines 266-299). The --bounds option that forces a shared window was not used. No crop-origin or source-granule attribute is written.
- **Fix:** Tolerated. Coregistration handles the frame shift (+824 az, -13 rg), and C1 confirmed the stored offsets are frame-independent residuals. Origins were recovered from coordinate arrays (exact-slice-match=True) and recorded in comparison.json. **Update:** v2 writes subset_azimuth_origin, subset_range_origin_frequency{A,B}, subset_geometry_doppler, subset_band_aligned and subset_source_granule as attributes on /science/LSAR/RSLC/swaths. Each date still gets its own window (required: the dates' geometries differ).
- **Cost:** Extra verification work; a potential source of confusion in any comparison.
- **Caught by:** guardrail/tool check · **Status:** fixed
- **Evidence:** L4115: 'WARNING: frequency A RSLC shapes differ across dates ({(11219, 14719), (11213, 14718)})'. L4494: '20260726 freq A: az0 5336 rg0 10684 size 11213x14718 exact-slice-match=True'. comparison.json crop_origins: reference [4512,10697], secondary [5336,10684].
- **Automation lesson:** Write crop provenance into the output HDF5 as attributes: source granule, az0/rg0 per frequency, buffer, height bracket, tool version. Downstream stages should read origins from there and not re-derive them.

#### WF3-10 — Stale full-scene boundingPolygon: an HDF5 SWMR flag conflict let the subset finish with EXIT=0

- **Symptom:** Both subsets printed 'WARNING: boundingPolygon not updated (Unable to synchronously open file (SWMR read access flag not the same for file that is already open)); the stale value describes the FULL scene' and still exited 0.
- **Root cause:** Inside the write block the tool called slc.getRadarGrid(), whose nisar reader reopens the SOURCE granule with h5py.File(..., swmr=True) (nisar/products/readers/Base/Base.py:170 and others). rslc_subset already had the same source open through h5py.File(src_path, 'r') without SWMR (now rslc_subset.py:337), and HDF5 refuses a second open with different SWMR flags. The comment at rslc_subset.py:309-311 and the L4079 explanation blame the write handle; the conflict is actually on the source file.
- **Fix:** The WKT is now computed before any h5py handle is opened (rslc_subset.py:308-332).
- **Cost:** Led directly to the wrong geogrid in the next entry; about 6 minutes of diagnosis and repair.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** logs/rslc_subset.log, both dates: 'WARNING: boundingPolygon not updated (Unable to synchronously open file (SWMR read access flag not the same ...' followed by '=== ... 20260714 EXIT=0 ==='. L4079: 'boundingPolygon failed because I called slc.getRadarGrid while h5py held the file open'.
- **Automation lesson:** Treat any metadata-rewrite failure in the crop module as fatal (non-zero exit), not a WARNING. Never call nisar readers on a file that is also open through raw h5py.

#### WF3-11 — Stale polygon made ingest silently pin a full-scene 60400x62800 geogrid

- **Symptom:** The first ingest on the cropped granules succeeded ('step 1 ingest OK') but derived 'AOI lon/lat union: [83.3723, 27.3642, 86.6052, 30.1092]' and pinned 60400x62800 px (28.3 GiB) instead of the AOI grid. The only warning was about shapes differing between dates.
- **Root cause:** nisar_wf/ingest.py:187 takes the footprint from identification/boundingPolygon, which still described the full scene. Ingest has no check that the footprint agrees with the radar grid size or the granule's slantRange/zeroDopplerTime extent.
- **Fix:** Repaired the polygon in both granules; after the truncation fix below, ingest pinned 13400x16800 (1.7 GiB). The assistant later wrote that this was 'Caught because ingest's own error surfaced it', which is inaccurate: this ingest succeeded, the assistant spotted the lon range in the log, and the ingest errors that followed came from the assistant's own truncated repair.
- **Cost:** About 2 minutes. Had it gone unnoticed, the downstream Track G crop would have geocoded a 28 GiB full-scene grid.
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** aoi/logs/track_r_20260914T081613Z.log: 'PINNED ... top_left (148000.0, 3332000.0) bottom_right (462000.0, 3030000.0)' then 'step 1 ingest OK'. L4122: 'the AOI union came out as the full scene (83.37-86.61 lon)'. L4216: 'Caught because ingest's own error surfaced it.'
- **Automation lesson:** Ingest should cross-check the footprint against the radar grid (for example with get_geo_perimeter_wkt on the actual grid) and fail if the areas differ by more than a few percent. Never trust identification metadata in a derived product.

#### WF3-12 — The assistant's own boundingPolygon repair truncated the WKT into the fixed-width |S2087 dtype; ingest then failed three times

- **Symptom:** After the in-place repair, ingest failed with 'shapely.errors.GEOSException: ParseException: Expected word but encountered end of stream' at 08:16:44, 08:16:53 and 08:17:18. The stored string was cut at 2087 characters, dropping the closing '))'.
- **Root cause:** Both the tool patch (L4086) and the ad-hoc repair (L4123) reused the source dtype: `dt = dst[p].dtype; ... create_dataset(p, data=np.bytes_(wkt), dtype=dt)`. The source dataset is fixed-width |S2087, sized exactly to its own 2087-character WKT, and the new densified WKT from get_geo_perimeter_wkt is 2090 characters (2081 for the second date). Correction to the seed list and STATE.md:81-83: this truncation did NOT produce the full-scene geogrid. That came from the stale SWMR polygon. The truncation caused a hard parse failure.
- **Fix:** Simplified the polygon to 270/272 characters with shapely simplify(0.001) and let h5py size the dtype (L4168). The tool was patched to match (rslc_subset.py:320-328, 430-437).
- **Cost:** About 2 minutes and three failed ingests.
- **Caught by:** crash · assistant error · **Status:** fixed
- **Evidence:** L4159: '20260714_aoi.h5 dtype=|S2087 itemsize=2087 stored_len=2087 value tail: ...4499.9999999994' against the source tail '...4973.37744107172))'. L4169: 'full wkt 2090 chars -> simplified 270 chars'. aoi/logs/track_r_20260914T081644Z.log and 081653Z.log: 'ERROR: step 1 ingest raised an UNEXPECTED GEOSException'.
- **Automation lesson:** When rewriting HDF5 string datasets, never reuse the source fixed-length dtype. Size it to the new value, then read it back and parse it before closing the file.

#### WF3-13 — A failed ingest left the stale stack.json, and grep filters hid the ERROR lines, so the assistant misdiagnosed

- **Symptom:** After two ingest --force runs failed, the assistant printed stack.json and concluded 'The geogrid is still full-scene. Let me check what ingest actually derives the AOI from', then spent time on aoi_lonlat. The commands had filtered the output through `grep -E "AOI lon/lat|PINNED|posting|freq [AB]:|track |temporal|WARNING"` and `grep -E "AOI lon/lat|PINNED|-> 6|px |GiB"`, which dropped the ERROR lines. The stack.json printed was the one left over from the 08:16:13 run.
- **Root cause:** nisar_wf/ingest.py writes stack.json only on success (ingest.py:599) and --force does not delete the old file first, so a failed ingest leaves the previous product in place. The assistant's grep patterns excluded ERROR lines.
- **Fix:** rm -f aoi/stack.json and an unfiltered re-run, which exposed the GEOSException (L4147-L4154).
- **Cost:** About 1.5 minutes and a wrong line of investigation.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** L4131 output shows no PINNED line, then 'tl {'x_abs': 148000.0, 'y_abs': 3332000.0}'. L4135: 'The geogrid is still full-scene.' track_r_20260914T081653Z.log: 'ERROR: RUN FAILED at step 1 'ingest''.
- **Automation lesson:** With --force a stage should delete or invalidate its output before running and write atomically (temp file then rename). Monitoring and wrapper scripts must always pass ERROR/Traceback lines through any filter and check the exit code, not the presence of an old artifact.

#### WF3-14 — Cropped granules were patched in place by ad-hoc scripts; the current tool has never run end-to-end

- **Symptom:** The files L1_RSLC_AOI/{20260714,20260726}_aoi.h5 were written at 08:12-08:14 by a tool version that left the polygon stale. They were then changed in place twice with h5py 'r+' scripts (08:16 truncated, 08:18 simplified), and the tool was patched afterwards. rslc_subset.log ends at 08:14:31; no run of the fixed tool exists. The repair used DEMInterpolator(4500.0) and the cropped file's own freq A grid. The tool now uses (hmin+hmax)/2 = 4593.5 m and offset_and_resize on the source grid, so re-running it would not reproduce the files on disk.
- **Root cause:** Fixing the data by hand instead of re-running the fixed tool, which takes about 90 s per date.
- **Fix:** None. The granules are valid (downstream products verified), but their bytes do not trace back to any committed tool version. **Update:** v2 granules were produced by tools/rslc_subset.py end to end (logs/aoi_v2.log) with no manual patching.
- **Cost:** No direct loss; a reproducibility gap in the input to the whole workflow.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L4122: 'let me patch the two existing files in place rather than re-cropping'. L4123 and L4168: h5py.File(f, "r+") with DEMInterpolator(4500.0). logs/rslc_subset.log last line '=== 2026-09-14T08:14:31Z BOTH SUBSETS DONE ==='. rslc_subset.py:318: DEMInterpolator(float((hmin + hmax) / 2)).
- **Automation lesson:** After a tool fix, regenerate the derived products with the fixed tool; never hand-patch pipeline outputs. Record a tool hash in the output so a mismatch can be detected.

#### WF3-15 — Output identity: cropped products differ from the full-tile products only by directory, and carry the source granule's identity

- **Symptom:** The cropped granules are named 20260714_aoi.h5, which records neither window nor buffer. Inside, identification/granuleId is still the full granule's ID and isFullFrame is still True. The workflow 3 deliverables use exactly the same filenames as workflow 1 (RUNW_20260714_20260726_A_HH_1x1_unw9x8.h5, scratch RIFG.h5, coherence names) and are kept apart only by out_root=<case>/aoi.
- **Root cause:** The subsetter copies the identification group verbatim except for times and polygon (rslc_subset.py:346-351, 409-440). The AOI isolation was designed as 'a SEPARATE tree', which the user's standing output identity rule says not to rely on.
- **Fix:** None. No clobbering happened, because out_root differs and the config header documents the separation.
- **Cost:** None yet. This is the failure mode the rule says has 'silently clobbered products six times'.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** The memory rule nisar-output-identity-rule.md: 'enumerate the planned output paths and test them against what is already on disk rather than assuming directory separation is enough.' L4100: 'a separate out_root, so I can keep the cropped run fully isolated'. Read in this log: L1_RSLC_AOI/20260714_aoi.h5 granuleId 'NISAR_L1_PR_RSLC_025_098_A_016_4005_..._001', isFullFrame 'True'.
- **Automation lesson:** Put the crop identity (AOI name or hash, window, buffer) into both granule and product filenames. Set isFullFrame=False and write a derived granuleId or a sourceGranuleId attribute. Test planned output paths for collisions across all legs before launching.

#### WF3-16 — Latent: the subsetter's rewrite of zeroDopplerStart/EndTime truncates to microseconds and would break when microseconds are zero

- **Symptom:** Source times have nanosecond precision ('2026-07-14T23:39:54.999342105'). Rewritten values are microsecond isoformat plus a literal '000' ('...23:39:30.348684000'). If a crop boundary falls on a whole microsecond, datetime.isoformat() drops the fractional part and the tool would write '2026-07-14T23:39:22000', a malformed string, into the fixed |S29 dtype.
- **Root cause:** rslc_subset.py:419-427: `data=np.bytes_(val.isoformat() + "000"), dtype=dt`, using Python datetime (microsecond resolution) and reusing the fixed-width source dtype, the same pattern as the polygon bug.
- **Fix:** None applied. **Update:** Now formatted with strftime('%Y-%m-%dT%H:%M:%S.%f') + '000' (always 9 fractional digits); v2 granules read e.g. 2026-07-14T23:39:24.772368000.
- **Cost:** None observed. The precision loss (<1 us) is negligible against the 658 us line spacing.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** rslc_subset.py:419-427. Read in this log: cropped zeroDopplerStartTime '2026-07-14T23:39:22.968421000' dtype |S29, against the source '2026-07-14T23:39:20.000000000'. Found while verifying this log.
- **Automation lesson:** Format NISAR time strings with isoformat(timespec='microseconds'), or better compute them from zeroDopplerTime with isce3.core.DateTime at full precision, and validate by parsing them back.

#### WF3-17 — Creating the AOI config with str.replace hit a commented line and left a corrupted comment behind

- **Symptom:** The first str.replace('out_root: null', ..., 1) replaced the comment '# out_root: null -> write products alongside the granules' and left the active key 'out_root: null'. Had this gone unnoticed, the AOI run would have written into the full-tile case tree. The active key was then fixed with an anchored regex, but the comment at configs/nepal_glof_aoi.yaml:54 still reads '# out_root: /home/sharath/isce3/case_studies/nepal_glof/aoi -> write products alongside the granules, in case_dir:'. The header 'Identical to nepal_glof.yaml except for three things' is also stale; the file now differs in six places.
- **Root cause:** An unanchored text replace on YAML with the count limited to 1 hits the first match, which here was a comment.
- **Fix:** re.subn(r'(?m)^out_root: null$', ...) replaced one active line; the assistant checked with grep '^out_root'.
- **Cost:** About 1 minute. A near miss for cross-contaminating the workflow 1 tree.
- **Caught by:** assistant · assistant error · **Status:** worked around
- **Evidence:** L4105: '56:out_root: null' after writing. L4108: 'The out_root replacement hit a commented line first.' L4110: 'active out_root lines replaced: 1'. The diff of configs shows the corrupted comment at line 54.
- **Automation lesson:** Generate leg configs by loading YAML, overriding keys, validating and dumping, never by text substitution. Assert that the resolved out_root and all output paths differ from every other leg's before running.

#### WF3-18 — The AOI config still defaults to frequency B and 9x1 looks; the real parameters lived only in CLI flags in /tmp scripts

- **Symptom:** Dry-run and ingest on nepal_glof_aoi.yaml report 'frequency / pol B / HH' and 'looks (az x rg) 9 x 1'. The freq A 1x1 run came only from --frequency A --looks 1 1 in scratchpad scripts (run_aoi_insar.sh), and those scripts were wiped when the VM rebooted.
- **Root cause:** configs/nepal_glof_aoi.yaml:498 `frequency: B`, inherited from the full-tile config. The workflow's scientific parameters were split between the config and ephemeral launch scripts.
- **Fix:** None in the config. The emitted runconfig aoi/cfg/insar_20260714_20260726_A_HH_1x1.yaml records what actually ran. **Update:** configs/nepal_glof_aoi_v2.yaml sets frequencies [A, B] and track_r.frequency A with a fresh header; the v1 config is unchanged as a record.
- **Cost:** Reproducibility risk: re-running from the config alone would run freq B at 9x1.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L4110 and L4148: 'frequency / pol B / HH', 'looks (az x rg) 9 x 1'. L4213: `python -u run_track_r.py --config configs/nepal_glof_aoi.yaml --only insar --frequency A --looks 1 1`. L4484: scratchpad 'total 0' after reboot. configs/nepal_glof_aoi.yaml:498.
- **Automation lesson:** A leg's config must fully describe the run. Disallow CLI overrides of scientific parameters in production mode, or write the effective parameters back into a per-run manifest next to the outputs.

#### WF3-19 — The launch script asked for product_type RIFG, which cannot reach ionosphere; the config guard rail refused

- **Symptom:** 'CONFIG ERROR: track_r.ionosphere_enabled is true but product_type is 'RIFG'. insar.py:120-124 gates the ionosphere stage on 'RUNW' in out_paths, so it would be SKIPPED SILENTLY' and insar EXIT=2 at 08:19:14.
- **Root cause:** The assistant's run_aoi_trackr.sh passed --product-type RIFG even though the goal was coregistration, interferogram and ionosphere, and the config already said product_type: RUNW. Underneath is the upstream trap: insar.py gates ionosphere on RUNW and skips it silently.
- **Fix:** Relaunched without the override (run_aoi_insar.sh, --only insar, product_type RUNW from the config).
- **Cost:** About 1.5 minutes. The guard rail (nisar_wf/config.py) prevented a silent run without ionosphere.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** aoi/logs/aoi_trackr.log: '=== insar EXIT=2 ==='. L4182: `--start-step runconfig --frequency A --looks 1 1 --product-type RIFG`. L4190: 'The insar step stopped on a guard rail I built'.
- **Automation lesson:** Keep the config validator that rejects incompatible stage combinations, and derive product_type from the requested deliverables (ionosphere implies RUNW) instead of taking it as an independent flag.

#### WF3-20 — unwrap_crossmul_path inherited from the full-tile config pointed at the full-scene RIFG; the claimed consequence was overstated

- **Symptom:** The copied config contained unwrap_crossmul_path: <case>/scratch/trackR/20260714_20260726_A_HH_1x1/RIFG.h5, the workflow 1 RIFG. The assistant said 'the AOI run would unwrap the full-scene interferogram' and 'report it as the AOI result', and STATE.md:84-86 repeats this. That consequence does not hold for the chain that ran: nisar_wf/trackr.py:692 runs `python -m nisar.workflows.insar`, and insar.py:109-110 calls unwrap.run(cfg, out_paths['RIFG'], out_paths['RUNW']), which ignores phase_unwrap.crossmul_path. Only the standalone `python -m nisar.workflows.unwrap` entry point reads it (unwrap.py:663-668, unwrap_runconfig.py:23-43), and ionosphere.py never does.
- **Root cause:** The AOI config was cloned wholesale from a config that held a resume-specific key (config.py:765-767: 'Path to an existing RIFG, for running python -m nisar.workflows.unwrap standalone'). The consequence was then asserted without tracing insar.py.
- **Fix:** Set to null in configs/nepal_glof_aoi.yaml:632-636, and confirmed the full-tile config was untouched. Nulling it was correct; the stated risk applies only to a standalone unwrap resume.
- **Cost:** About 1 minute. The overclaim is now documented as a trap in STATE.md.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L4192: '141: unwrap_crossmul_path: /home/sharath/isce3/case_studies/nepal_glof/scratch/trackR/20260714_20260726_A_HH_1x1/RIFG.h5'. L4195: 'Left as-is, the AOI run would unwrap the full-scene interferogram.' Code: insar.py:109-110, unwrap.py:663-668, nisar_wf/trackr.py:692 (checked while verifying this log).
- **Automation lesson:** Keep resume and standalone keys out of base configs; pass them per invocation. Put the stock-chain versus standalone semantics of phase_unwrap.crossmul_path in the pipeline docs, and verify claimed consequences against the call graph before documenting them.

#### WF3-21 — Snaphu tiling changed from the benchmark ([4,4]/nproc 8 to [2,2]/nproc 4) without an observed failure

- **Symptom:** Described as fixing 'over-tiling', yet the new config comment itself says '4x4 tiling gives ~312 x 460 tiles -- plenty' and then sets [2,2]. The wrapper warns that tiling is 'a scientific change, not a performance flag'. The crop's freq A 9x8 unwrap produced 5 connected components; the full tile's 9x8 RUNW had 19.
- **Root cause:** A scientific unwrap parameter was retuned on the test leg while the benchmark kept its own value, which breaks the controlled full-versus-crop comparison.
- **Fix:** None. The configs still differ: nepal_glof_aoi.yaml:643-657 against nepal_glof.yaml:619-630, and the emitted runconfigs differ at ntiles and nproc. **Update:** v2 config uses [4,4] / nproc 8 / overlap 256, identical to the benchmark 9x8 unwrap.
- **Cost:** Adds a confound to the R_full-versus-R_crop unwrapped and ionosphere comparison, including the open +3.6 TECU item. No runtime issue.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L4199 patch: '# 4x4 tiling gives ~312 x 460 tiles -- plenty ... unwrap_ntiles: [2, 2]', 'unwrap_nproc: 4'. aoi/logs/aoi_insar.log:186: 'Assembled connected components (5)'. L2864 (full 9x8): 'connectedComponents 19 distinct'. Wrapper WARNING: 'per-tile reoptimisation changes the solution and the connected-component labelling'.
- **Automation lesson:** In a comparison experiment, freeze every scientific parameter across legs and change only the variable under test. Choose tiling by grid size with a fixed rule shared by all legs, or unwrap both at the same tile geometry.

#### WF3-22 — The disk-gate scratch model leaves out the ionosphere sub-run

- **Symptom:** Estimated 'scratch 16.7 GiB + RIFG 1.8 GiB = 18.5 GiB'. Measured 20.7 GiB (21203 MiB) in total, of which ionosphere/main_side_band is 1735 MiB: about 19.3 GiB of scratch against the 16.7 GiB estimate (+16%).
- **Root cause:** nisar_wf/trackr.py:163-202 estimate_scratch counts coregistration stages and RIFG_ifgram_dem but has no term for the freq B ionosphere chain (rdr2geo, geo2rdr, offsets, resample, crossmul, unwrap for freq B), which runs whenever ionosphere is enabled.
- **Fix:** None. Harmless here because 367.7 GiB was free.
- **Cost:** None this time. The gap would matter on a tight disk or a larger crop.
- **Caught by:** assistant · **Status:** open
- **Evidence:** L4205: 'reference grid 165 Mpx -> scratch 16.7 GiB + RIFG 11219 x 14719 (165 Mpx) 1.8 GiB = 18.5 GiB'. Measured in this log: du of aoi/scratch/trackR/20260714_20260726_A_HH_1x1 = 21203 MiB, ionosphere/ = 1735 MiB.
- **Automation lesson:** The scratch estimator must include the ionosphere freq B sub-chain (scaled by the B grid size, which can exceed A's share after cropping) and be recalibrated against measured runs.

#### WF3-23 — Red flag missed: the R_crop ionosphere read +32.6 rad against the benchmark's -19.6 rad and was reported as success

- **Symptom:** At 08:55 the verification printed 'ionospherePhaseScreen median +32.5939 p5 +30.128 p95 +35.416' (+2.39 TECU). The benchmark's full-tile median, quoted at 08:03, is -19.63 rad (-1.439 TECU), so the two differ by about 52 rad and have opposite signs. The assistant reported 'The entire chain ran ... ionosphere present' and deferred any comparison. STATE.md (10:45) lists ionosphere 'three independent paths' and leaves R_crop out. The discrepancy surfaced only about 5 h later, in comparison stage I3 (13:51).
- **Root cause:** The product check tested for the dataset's presence, not for plausibility against a known reference. The argument that the scene median and AOI median 'aren't comparable' cannot explain a 52 rad sign flip; the whole-scene p5-p95 spread of the GSLC ionosphere is only -1.75 to -0.92 TECU.
- **Fix:** None until the comparison. It is now recorded as an open finding (next entries). **Update:** Caught by adversarial verification (verdicts.json V1), which decomposed the offset; v2 re-measures it in COMPARISON.
- **Cost:** About 5 h during which a suspect result was presented as complete, and the first STATE.md summary omitted it.
- **Caught by:** assistant · assistant error · **Status:** worked around
- **Evidence:** L4262: 'ionospherePhaseScreen median +32.5939'. L3974: 'Track R (RSLC, radar) | -19.63 rad | -1.4390'. L4266: 'The entire chain ran ... ionosphere present.' L4289: 'I'm not reporting the cropped ionosphere number against the full-tile one yet'. STATE.md:42-45.
- **Automation lesson:** Every stage needs a value-level QA gate against a reference or physical range, for example that the ionosphere median over the overlap agrees with the benchmark within N TECU and the sign agrees. A leg must not be marked complete on dataset presence alone.

#### WF3-24 — Crop origin not snapped to multiples of looks or dense-offset skip, so multilooked and offset grids are sub-sample misaligned

- **Symptom:** The reference crop origin (4512, 10697) gives 4512 % 9 = 3 and 10697 % 8 = 1, so the 9x8 RUNW look boxes are misaligned by 3 azimuth rows and 1 range column against the full tile and cannot be compared pixel-exactly (only the 1x1 RIFG can). The dense-offset sampling grid (skip 32) is aligned in azimuth (4512 % 32 = 0) but offset by 10697 % 32 = 9 px in range (range_subsample_misalignment_steps = 0.28125). The assistant attributed the 0.115 rad crop-versus-full scatter to 'the dense-offset grid starting at a different absolute pixel' and rubbersheet support; that attribution was never isolated.
- **Root cause:** rslc_subset.py:278-299 takes the window as bbox minus buffer with no snapping to lcm(unwrap looks, crossmul looks, dense_offsets skip).
- **Fix:** None in the tool. The comparison works around it with 1x1 radar-domain comparisons and reports R_sublook_misalignment {az_rows 3, rg_cols 1} in comparison.json I3. **Update:** v2 origins are multiples of 9 (azimuth) and 8 (range), so the 9x8 grids align exactly; the dense-offset grid (start 52, step 32) and the freq-B side band (needs 64) still do not align for v2 -- the comparison interpolates both; future crops snap to 64.
- **Cost:** The 9x8 RUNW and ionosphere comparisons carry a sub-look shift, and the full-tile offset field cannot be reproduced exactly.
- **Caught by:** assistant · assistant error · **Status:** worked around
- **Evidence:** L4273: 'RUNW 9x8: az0 % 9 = 3 rg0 % 8 = 1 -> look boxes MISALIGNED by 3 az rows, 1 rg cols (sub-look)'. comparison.json C1_dense_offsets: 'range_subsample_misalignment_steps': 0.2812500024546125, 'azimuth_index_exact': true. L4494: full pixelOffsets slantRange first 878404.393 m, crop 911809.392 m, step 99.93 m.
- **Automation lesson:** Snap the crop origin (and preferably the size) to multiples of lcm(azimuth looks, dense_offsets skip) and lcm(range looks, skip) for every stage whose output will be compared to a full-tile run. Write the snapped origin into the product.

#### WF3-25 — Resolution parity gap: R_crop has no 1x1 unwrapped phase, unlike the benchmark

- **Symptom:** The full-tile RSLC has unwrapped phase at 1x1 (17.38 GB) and 9x8. The cropped RSLC was run only with the config's 9x8 phase_unwrap, so it has no 1x1 RUNW. This surfaced only when the user asked whether all four runs were at 1x1/5 m.
- **Root cause:** The crop leg was launched with the ionosphere-oriented 9x8 unwrap setting and not mirrored to the benchmark's product set.
- **Fix:** None. The assistant offered a 1x1 crop unwrap (estimated under 1 h) after the verification pass.
- **Cost:** None of the reported comparisons use 1x1 unwrapped phase, but the leg does not fully mirror the benchmark.
- **Caught by:** user · assistant error · **Status:** open
- **Evidence:** L4705 (user): 'dude so didnt we do the full tile RSLC, GSLC and cropped RSLC and GSLC at azimuth, range settings at 1,1 ...' L4714: 'RSLC crop has unwrapped phase only at 9x8 -- I ran it with the config's phase_unwrap 9x8 setting ... and didn't do a separate 1x1 unwrap'.
- **Automation lesson:** Define the product set for each leg from the benchmark's product manifest, and fail the comparison stage when a leg is missing a product the benchmark has.

#### WF3-26 — Crop origin not a multiple of the looks, so the 9x8 RSLC products are sub-look misaligned and the full-vs-crop ionosphere comparison is not pixel-exact

- **Symptom:** 'RUNW 9x8: az0 % 9 = 3   rg0 % 8 = 1  -> look boxes MISALIGNED by 3 az rows, 1 rg cols (sub-look)'. The 1x1 RIFG compares exactly.
- **Root cause:** rslc_subset.py chose the crop origin (4512, 10697) from the AOI plus buffer without snapping to the unwrap/ionosphere look grid (9 az x 8 rg). compare_four_way.py:674-682 compares the 9x8 ionosphere screens with integer division `AZ0 // ly`, a fractional-look offset.
- **Fix:** The comparison records `R_sublook_misalignment` in comparison.json (az_rows 3, rg_cols 1). rslc_subset.py gained --align-rg-looks and azimuth snapping to multiples of 9 (dry run at jsonl L4852). The cropped RSLC chain has not been re-run with it yet. **Update:** v2 origins: reference (7254, 6792) = (806x9, 849x8); secondary (8001, 6784).
- **Cost:** The I3 R_full-vs-R_crop ionosphere comparison carries a 3-row/1-col sub-look shift. It is a small part of the observed difference, but it makes that comparison not pixel-exact.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** jsonl L4273 output; L4281: 'the crop origin must be a multiple of the look factors'; V1 verifier: 'crop minus full[501,1337] is exactly 3.000 azimuth rows and 1.000 range column'.
- **Automation lesson:** Snap every crop origin to the least common multiple of all downstream look factors and band-ratio factors. Assert alignment in the comparison stage and refuse non-exact multilooked comparisons.

#### WF3-27 — Cropped granules do not cover the NW ~18-20% of the user's AOI; the comparison silently dropped it while being reported as 'the true AOI'

- **Symptom:** An unexplained 'valid in both: 81.9%' in the GSLC full-vs-crop check (jsonl L4368). The lookup is valid on only 79.5% of the lattice, and comparison.json common_mask_fraction_of_aoi is 0.733. Yet the assistant told the user the results were 'restricted to the true AOI only'. Audit, within the AOI polygon: G_full valid 100%, G_crop 82.9%, R_full and R_crop 80.4%. The crop's radar footprint covers 81.9% of the AOI lattice bbox, and top-left 2560x1536 output chunks are 0-6% valid.
- **Root cause:** The rslc_subset.py radar window (freq A az 4512..15731) is too short. Audit: the AOI's NW corner (328475 E, 3148925 N) maps to full-reference radar pixel az ≈18480, rg ≈22520, well past line 15731. The window came from isce3.geometry.get_radar_bbox with a DEM height bracket; why it came out short was not established. That is the R_crop workflow. In compare_four_way.py, R_full is geocoded through the CROP's rdr2geo lookup (lines 437-458, 559) and all legs are intersected (615-618), so the gap is inherited by every leg and never logged per leg.
- **Fix:** None yet. The planned ALIGNED re-crop dry run (jsonl L4852: 'az 4509..15732') would reproduce the same azimuth gap. **Update:** Root cause was the native-Doppler window (see the zero-Doppler entry); v2 coverage per quadrant is reported in comparison_v2 lookup_verification.
- **Cost:** All four-way statistics describe ~73% of the requested AOI, and the glacier-lake NW part may be missing from every comparison. The report would overstate coverage.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** Audit: lut_rowcol.tif chunk validity row0 '0.00 0.06 0.45 0.87 1.00'; crop footprint polygon covers 0.819 of lattice; 'NW nearest full radar az 18480 rg 22520 dist m 18.8' vs 'crop window az 4512..15731'; logs/rslc_subset.log 'AOI in EPSG:32645: x 328022..380543 y 3115803..3148925'; jsonl L4389: 'restricted to the true AOI only'.
- **Automation lesson:** After cropping, geocode the crop's footprint (rdr2geo edges) and assert it contains the AOI polygon plus buffer, or fail. The comparison module must report per-leg coverage of the AOI and refuse to call a statistic AOI-wide below a threshold.

#### WF3-28 — Crop window placed with the NATIVE Doppler centroid on a zero-Doppler product: the north of the AOI was missed

- **Symptom:** v1 crop covered azimuth lines 5012..15231 where the AOI needs 8262..18556; the NW third of the AOI was 7.5% covered and all v1 cropped statistics described ~73% of the AOI.
- **Root cause:** tools/rslc_subset.py passed slc.getDopplerCentroid() to isce3.geometry.get_radar_bbox and get_geo_perimeter_wkt; ISCE3's own rdr2geo.py:64-65 and geo2rdr.py:52-53 use an empty LUT2d() ('NISAR RSLC products are always zero doppler'). AOI centre moved ~3300 lines (~15 km).
- **Fix:** Zero Doppler for window and polygon; v2 granules L1_RSLC_AOI_v2/ and all cropped legs rebuilt (aoi_v2/).
- **Cost:** v1 cropped legs (R_crop 33 min, G_crop ~22 min) and their comparison invalidated for the north of the AOI; an aligned rerun aborted after 38 min.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** critic.json contradictions #17; get_radar_bbox native vs zero Doppler test 2026-09-14 (az 5012..15231 vs 8262..18556)
- **Automation lesson:** Geometry for zero-Doppler products always uses zero Doppler; assert AOI coverage of the crop (per quadrant) before processing.

#### WF3-29 — Aligned rerun changed freq-A extent as well as alignment, and inherited the native-Doppler bug: aborted

- **Symptom:** The first 'aligned' rerun widened freq A to freq B's buffered window x 8 (251.8 Mpx vs 165) and still used the native-Doppler window.
- **Root cause:** Fix for the A/B alignment was made before the Doppler bug was known; buffer defined per band in pixels.
- **Fix:** Stopped at 37m54s, outputs moved to aoi_aligned_ABORTED_nativeDoppler/ and L1_RSLC_AOI_ALIGNED_ABORTED_nativeDoppler/; v2 crop uses zero Doppler and physical buffers.
- **Cost:** ~38 min of compute; 21 GB of aborted outputs kept as evidence.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** logs/aoi_aligned_trackr.log; critic.json contradictions #18
- **Automation lesson:** Re-verify all geometry assumptions before a rerun that is meant to test one fix; change one variable at a time or say plainly that you did not.

#### WF3-30 — Stopping the aborted run with pkill -f killed the agent's own shell (exit 144) -- a known trap repeated

- **Symptom:** pkill -f 'configs/nepal_glof_aoi_aligned.yaml' matched the invoking command line and killed it.
- **Root cause:** Pattern present in the agent's own argv; the same class was logged earlier (errors_W5 pkill/pgrep self-match).
- **Fix:** Re-ran with self-excluding patterns ('[n]epal_glof_aoi_aligned'); tmux session already killed.
- **Cost:** One failed command; no data loss.
- **Caught by:** crash · assistant error · **Status:** fixed
- **Evidence:** 2026-09-14 ~14:57 UTC tool call exit 144
- **Automation lesson:** Stop jobs by PID or session name, never by a pattern the caller's own command line contains.

#### WF3-31 — Freq-B side-band look cells not aligned for crops snapped to multiples of 8 only

- **Symptom:** v2 reference crop RG0_A = 6792, RG0_B = 849; the side band is 8 range looks on the B grid, so crop cells sit 1/8 cell (25 m) from the full-tile cells.
- **Root cause:** Exact side-band alignment needs RG0_A % 64 == 0 (band ratio 8 x side-band looks 8).
- **Fix:** rslc_subset.py gained --align-sideband-looks (default 8 -> snap to 64) for future crops; compare_four_way.py v2 interpolates the full-tile side-band layers onto the crop cell centres. v2 products were not regenerated (A/B origin consistency across dates, which ISCE3 needs, holds).
- **Cost:** Diagnostic comparability only.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** compare_v2_code_review.json (geometry lens); L1_RSLC_AOI_v2 slantRange
- **Automation lesson:** Snap crop origins to the least common multiple of every look grid any downstream product uses.

#### WF3-32 — Different ISCE3 entry points for the full-tile and cropped ionosphere runs

- **Symptom:** Full-tile 9x8 RUNW came from the standalone unwrap path (product_type RUNW_STANDALONE, crossmul_path to the finished RIFG); the crop ran the integrated insar workflow.
- **Root cause:** Operational history: the full-tile coregistration was not repeated for the 9x8 pass.
- **Fix:** Recorded as a confound for the R_full vs R_crop ionosphere comparison.
- **Cost:** Unquantified confound.
- **Caught by:** guardrail/tool check · **Status:** open
- **Evidence:** critic.json missing_problems; runConfigurationContents in both RUNWs (verdicts.json V1 (c))
- **Automation lesson:** Benchmark and test legs must use the same entry point and stage set.

#### WF3-33 — Cycle-class rule on a crop recovers the class but its tie-break lands one joint cycle from the full tile

- **Symptom:** Crop screen + rule (m,n) = (-1,0) is -3.205 rad from R_full; the other minimal-norm member (0,+1) would match to 0.005 rad.
- **Root cause:** Minimal-norm members tie; the |median non-dispersive| tie-break is a prior.
- **Fix:** Use the sliced full-tile screen for AOIs (reproduces the benchmark exactly).
- **Cost:** None.
- **Caught by:** guardrail/tool check · **Status:** by design
- **Evidence:** iono_transfer/iono_transfer.json
- **Automation lesson:** Take the ionosphere level for every AOI from one full-tile solve per pair.

<!-- ERROR-LOG:END -->

## 12. Automation contract

**Preconditions.** Full granules pass WF1's input gates; AOI and DEM available; the subset tool version and its
alignment/buffer parameters recorded.

**Postconditions.** Every section-9 gate passes **before** the InSAR chain starts; the subset granule carries its
provenance attributes; the crop config inherits no path from another run.

**Identity and caching.** Key subset granules by (source granule, AOI geometry hash, buffers, snapping, Doppler
mode, tool version); key everything downstream by the subset key plus the WF1 identity. Never reuse a subset made
under a different key.

**Failure handling.** The subsetter refuses to overwrite; a failed write leaves a partial file that must be deleted.
A geometry gate failure stops the pipeline — never "process anyway and mask later".

**Monitoring.** As WF1, plus the subset tool's printed windows and coverage fractions in the run log.
