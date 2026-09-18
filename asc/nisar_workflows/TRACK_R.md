> **Superseded (2026-09-15).** This early write-up predates the four-workflow study. Use
> [docs/WF1_RSLC_FULL_TILE.md](docs/WF1_RSLC_FULL_TILE.md) (full-tile RSLC) and
> [docs/WF3_RSLC_CROPPED.md](docs/WF3_RSLC_CROPPED.md) (crop-first RSLC) instead; see [docs/README.md](docs/README.md).
> Its status note ("Track G not yet run") and any unverified numbers here are stale.

# Track R — RSLC coregistration, interferogram, ionospheric correction

The conventional reference-scene InSAR chain in **radar coordinates**, driven by
`run_track_r.py` over stock `nisar.workflows.insar`. This is the direct analogue
of the ISCE2 Sentinel-1 workflow, and the counterpart to Track G
(`run_track_g.py`), which geocodes each date onto a pinned map grid instead.

Everything below with a number attached was **measured on this case**
(`case_studies/nepal_glof`, NISAR track 98 frame 16), not estimated. File:line
references are into the installed `isce3 0.25.12` / `nisar` package unless the
path says otherwise.

> **Status.** Coregistration, interferogram and ionosphere are covered here.
> Troposphere and the Track G (GSLC) workflow are researched but not yet run —
> see [Not yet written up](#not-yet-written-up) for exactly what is known and
> what is missing.

---

## 1. What the chain does

```
rdr2geo → geo2rdr → coarse_resample → dense_offsets → rubbersheet
        → fine_resample → crossmul → [filter → unwrap → ionosphere] → [geocode]
```

Two properties define it:

**The reference image is never resampled.** Only the secondary is, twice —
once geometrically (`coarse_resample`, from `geo2rdr` offsets) and once
data-driven (`fine_resample`, from rubbersheeted `dense_offsets`). Track G
resamples *both* dates onto a map grid; Track R resamples one. That is the
scientific difference between the tracks.

**Looks are consumed at `crossmul`, the last stage.** Every stage before it runs
at full radar-grid resolution. Raising `looks` reduces neither RAM, nor scratch,
nor runtime — only the size of the final interferogram. This is the single most
expensive misconception available here; `estimate_scratch()` reports the bill
explicitly and the disk gate refuses to start without room.

### Product type decides which stages run

From the `'X' in out_paths` guards in `insar.py`:

| `product_type` | delivered | stages |
|---|---|---|
| `RIFG` | RIFG | coregistration + crossmul. **No unwrap.** |
| `RUNW` | RUNW (RIFG → scratch) | + filter, unwrap, **ionosphere** |
| `GUNW` | GUNW (RIFG, RUNW → scratch) | + geocode, **troposphere**, solid earth tides |

**The ionosphere stage is gated on `'RUNW' in out_paths`**
(`insar.py:120-124`). Under `product_type: RIFG` it is skipped **silently** —
no error, no output, no warning. Reaching split-spectrum therefore *requires*
unwrapping. Likewise troposphere is gated on `'GUNW'` (`insar.py:150-152`).

`nisar_wf/config.py` raises a `ConfigError` if `ionosphere_enabled` is set with
a product type that cannot reach it, rather than letting it no-op.

---

## 2. Running it

```bash
cd asc/nisar_workflows

# what it would do, and what it would cost — writes nothing
python run_track_r.py --config configs/nepal_glof.yaml --dry-run

# render + schema-validate the runconfigs; prints the disk bill
python run_track_r.py --config configs/nepal_glof.yaml --only runconfig

# one pair, frequency A
python run_track_r.py --config configs/nepal_glof.yaml \
    --frequency A --pair 20260714 20260726 --only insar

# coherence summary from a decimated read
python run_track_r.py --config configs/nepal_glof.yaml --only qa
```

Steps 1–2 (`ingest`, `dem`) are shared with Track G and read the same
`stack.json` and DEM, so a case already ingested resumes straight into step 3.

Long runs belong in tmux — see [Operational notes](#7-operational-notes).

---

## 3. Choosing looks

`looks` is keyed **per frequency** in `configs/nepal_glof.yaml`. This is not
stylistic: A and B share an azimuth spacing but differ ~8× in range, so a single
`(azimuth, range)` pair cannot be correct for both.

Native spacings for this track, at the 41.1° scene-centre incidence:

| | azimuth | ground range | range *resolution* |
|---|---|---|---|
| freq A (40 MHz) | 4.454 m | 4.750 m | 5.7 m |
| freq B (5 MHz) | 4.454 m | 37.998 m | 45.6 m |

Ground cell = `azimuth_looks × 4.454` by `range_looks × ground range spacing`.
Note the resolution column: **freq B's 38 m posting already oversamples its
45.6 m resolution**, so `range_looks` must stay 1 there — no look setting
recovers finer range detail than the bandwidth allows.

| looks (az × rg) | freq A | freq B |
|---|---|---|
| 1 × 1 | 4.5 × 4.8 m | 4.5 × 38.0 m |
| 5 × 5 | 22.3 × 23.7 m | 22.3 × 190.0 m |
| **9 × 1** | 40.1 × 4.8 m | **40.1 × 38.0 m** |
| **9 × 8** | **40.1 × 38.0 m** | — |

### The 1×1 coherence trap

**At 1×1 the coherence band is identically 1.0.** The estimator has one sample
per cell, so `|a·conj(b)| / sqrt(|a|²·|b|²)` is 1 by construction. Measured
against `isce3.signal.Crossmul` on synthetic data with true γ = 0.5:

| looks | coherence min | median | max |
|---|---|---|---|
| **1 × 1** | **1.0000** | **1.0000** | **1.0000** |
| 5 × 5 | 0.2178 | 0.5428 | 0.7373 |
| 9 × 1 | 0.0946 | 0.5538 | 0.8531 |

A 1×1 RIFG is an amplitude-and-wrapped-phase product. Its coherence layer
carries no information. That is acceptable when the coregistered SLC pair is the
deliverable — but nothing downstream that reads coherence will work.

**This does not block unwrapping or ionosphere**, because `phase_unwrap` has its
own looks — see below.

### ISCE3 has no sliding-window coherence — and how to get one

This is not a tuning gap, it is absent. `cxx/isce3/signal/Crossmul.cpp:378-388`:

```cpp
} else {
    ifgRaster.setBlock(ifgram, 0, rowStart, ncols, blockRowsData);
    // fill coherence with ones (no need to compute result)
    coherence = 1.0;
```

When `_multiLookEnabled` is false — both looks 1 — ISCE3 **hardcodes 1.0 and
skips the computation**. The multilooked branch above it averages only over the
look box:

```cpp
coherence[i] = std::abs(ifgramMultiLooked[i]) /
               std::sqrt(refPowerLooked[i] * secPowerLooked[i]);
```

`crossmul_options` exposes `range_looks`, `azimuth_looks`, `flatten`,
`flatten_path`, `coregistered_slc_path`, `oversample`, `lines_per_block` and the
two common-band filters — **no window parameter exists**. Averaging is available
only through looks, which decimates.

The standard ISCE2 estimator is a MOVING window, which keeps full resolution:

```
gamma[i,j] = |SUM_w a·conj(b)| / sqrt(SUM_w |a|² · SUM_w |b|²)
```

`tools/slc_coherence.py` implements exactly that against a coregistered pair.
Track G's `nisar_wf/igram.py` already had it as `coherence_window`; this brings
the same estimator to Track R.

```bash
python tools/slc_coherence.py \
    --ref <scratch>/crossmul/freqA/HH/reference.slc \
    --sec <scratch>/fine_resample_slc/freqA/HH/coregistered_secondary.slc \
    --win 3 --out coherence_A_HH_win3.tif
```

**The bias floor matters more than the window choice.** A magnitude-of-a-sum
estimator is biased high for small N, with floor `sqrt(pi)/(2*sqrt(N))`.
Validated against synthetic pairs of known coherence:

| true γ | win 3 | win 5 | win 7 |
|---|---|---|---|
| **0.00** | **0.289** | **0.170** | **0.120** |
| 0.30 | 0.396 | 0.333 | 0.317 |
| 0.50 | 0.555 | 0.519 | 0.510 |
| 0.80 | 0.822 | 0.808 | 0.804 |
| 1.00 | 1.000 | 1.000 | 1.000 |

against predicted floors of 0.295 / 0.177 / 0.126 — agreement within 2%.

So **a 3×3 coherence does not reach 0 over decorrelated ground; it settles near
0.3.** Reading "0.3 along the flood path" as partial correlation would be wrong —
for a 3×3 window that *is* the noise floor. The tool prints the floor and writes
it into the GeoTIFF metadata as `BIAS_FLOOR`. Use a larger window where the low
tail matters.

---

## 4. Unwrapping, and why its looks are separate

`processing.phase_unwrap.range_looks` / `azimuth_looks` are **independent of
`crossmul`'s**. `unwrap.py:122-135` re-runs `crossmul` from the coregistered
SLCs at these factors, so the RUNW grid gets a **freshly computed coherence** —
it does not decimate the RIFG's. That is what lets the RIFG stay at 1×1 while
the unwrapped product still has a real coherence for snaphu to work on.

It is also a hard memory constraint. `unwrap.py:278-279` calls `open_raster()`
on the **whole** interferogram and coherence:

| freq A phase_unwrap looks | RUNW grid | RAM for the read |
|---|---|---|
| 1 × 1 | 53200 × 54244 | 23.1 + 11.5 = **34.6 GB** |
| **4 × 4** | 13300 × 13561 | 1.4 + 0.7 = **2.2 GB** |

At 1×1 this exceeds a 31 GB box. 4×4 is the configured value and gives a
~17.8 × 19.0 m RUNW cell.

snaphu's stock defaults are `ntiles: [1,1]`, `nproc: 1`,
`single_tile_reoptimize: True`. On a 180 Mpx grid that solves single-tile on one
core. The config sets `[8,8]` / `nproc: 8` with both re-optimisation flags off —
see [Operational notes](#7-operational-notes) for the memory arithmetic that
forces it. Note that tiling is a **scientific** change, not just a speed flag:
per-tile reoptimisation changes the solution and the connected-component
labelling.

---

## 5. Ionospheric correction — split spectrum

### Method choice

Four `spectral_diversity` options exist. **`main_side_band` is the right one for
NISAR DHDH data**, and the reason is a property of the products:

- A and B share a starting range, measured **878242.006 m on both**
- their range spacings are in an **exact 8:1 ratio** (24.9827 / 3.1228)
- `zeroDopplerTime` is a **single shared dataset**, so azimuth indices map 1:1

so decimating A onto B is exact at any look factor. `split_main_band` instead
splits A's own 40 MHz into sub-bands, costing two extra full-resolution unwraps
and ~92 GB of sub-band SLC HDF5s, and is noisier for this geometry.

### Only frequency A is listed

```yaml
ionosphere_phase_correction:
  list_of_frequencies: {A: [HH]}
```

`ionosphere.py` builds the frequency B pair **itself**, by decimating frequency
A's `geo2rdr` / `rubbersheet` offsets. **A second Track R run on frequency B is
not required** — a single insar run covers both bands.

### The noise caveat, stated plainly

Frequency A is 40 MHz and B is 5 MHz, and their centres are only 54.5 MHz apart
(1.239 vs 1.2935 GHz). The dispersive / non-dispersive inversion amplifies
unwrapped-phase noise by roughly **17×** for this pair. The dispersive filter is
**not optional** — unfiltered, the correction is worse than no correction. It is
enabled by default in our config with a 0.5 coherence threshold and a 15-pixel
median filter.

### No TEC file exists

These granules carry **no TEC datasets**, and no IONEX reader exists anywhere in
the installed `nisar` or `isce3`. So neither track gets an ionospheric
*geolocation* correction. At 20 TECU and f₀ = 1.239 GHz the absolute range shift
is ≈ 6.9 m ≈ 2.2 freq-A range pixels; the date-to-date differential is a
fraction of that but plausibly ~1 pixel, and is uncorrected on both tracks. This
is a property of the data on disk, not something a config fixes. Split spectrum
corrects the ionospheric **phase**; it does not recover that geolocation shift.

---

## 6. Costs, measured

### Scratch — 104.4 bytes per reference-grid pixel

Measured on freq B (53200 × 6781 = 360.7 Mpx → 37.7 GB), itemised:

| stage | B/px | what |
|---|---|---|
| rdr2geo | 24.4 | x, y, z as Float64 |
| geo2rdr | 16.0 | range + azimuth offsets, Float64 |
| coarse_resample | 8.0 | coregistered secondary, complex64 |
| dense_offsets | 8.0 | offset/corr/snr + a reference.slc copy |
| **rubbersheet** | **32.0** | filtered range+azimuth at FULL grid, plus culled |
| fine_resample | 8.0 | coregistered secondary, complex64 |
| crossmul | 8.0 | reference.slc unpacked from the RSLC HDF5 |
| baseline | 0.04 | |

Plus one **looks-dependent** term, `RIFG_ifgram_dem` (Float32 on the
*interferogram* grid): 0.16 GB at freq B 9×1, **11.5 GB at freq A 1×1**.

An earlier version of this model counted only rdr2geo + geo2rdr + the two
resampled SLCs — 40 B/px, **2.6× low**. `dense_offsets` and `rubbersheet` are
decimated in their *own* products (208 × 1659 here) but still emit full-grid
rasters alongside them, so "the offsets are tiny" is the wrong intuition.

| | reference grid | scratch | RIFG |
|---|---|---|---|
| freq B | 360.7 Mpx | 37.7 GB | 0.66 GB @ 9×1 |
| freq A | 2886 Mpx | 301 GB | ~58 GB @ 1×1 |

The disk gate credits scratch already present for the same pair+tag, since a
re-run overwrites it in place rather than adding to it.

### Runtime

freq B, full chain, **2 cores** — 3 h 07 m total:

| stage | time |
|---|---|
| rdr2geo | 99 min |
| geo2rdr | 3.7 min |
| coarse_resample | ~35 min |
| dense_offsets (ampcor) | 37 min |
| rubbersheet | 6 min |
| fine_resample + crossmul | ~26 min |

freq A on 2 cores: rdr2geo **13 h 05 m**, geo2rdr 22 min, and the
`RIFG_ifgram_dem` Topo pass a further **13 h 33 m** at 1×1 — that second pass is
as expensive as rdr2geo itself, because at 1×1 the interferogram grid *is* the
radar grid. At 4×4 it would be ~50 min. Peak RSS 477 MB–1.28 GB.

### Block sizing is derived, not fixed

Block memory is `lines × width`, and width is a property of the *frequency*. The
stock `lines_per_block: 1000` costs 163 MB of rdr2geo on freq B and **1302 MB on
freq A** — same number, 8× the memory. `nisar_wf/trackr.py` derives block
heights from the range width against `block_budget_mb` (default 256), so one
config is correct for both bands.

---

## 7. Operational notes

### Traps that cost real time here

**`Persistence` cannot resume.** ISCE3's resume mechanism reads the previous
logfile, and it is broken two independent ways:

1. `persistence.py:65` resets `success_msg_found` on **every line** while
   scanning the log in reverse, and the loop runs to the top of the file. Unless
   the file's *first* line is a success marker, it falls through to a full
   restart. Verified: a 2-line log fails, a strictly 1-line log works. Real
   ISCE3 logs never start with a success line.
2. `runconfig.py:102` applies `logging.write_mode` **before** `Persistence`
   reads the log, so `write_mode: 'w'` truncates the very file the resume
   depends on. Our config now sets `'a'`.

Tested against a real logfile with `geo2rdr` completed: it marked **all 18
steps** as needing to run. Even when coerced to work it skips
`prepare_insar_hdf5`, which *creates* the RIFG. **Assume no resume.**

**A SIGKILL mid-write corrupts the output HDF5** beyond repair — `h5clear -s`
does not help; it is structural (`bad object header version number`), not an
unclosed-file marker. Delete and re-run.

**`--force` deletes the whole scratch tree.** Without it, the wrapper skips a
pair whose output already exists — including a *corrupt* one. For a failed run:
delete the corrupt product by hand, and do not pass `--force` unless you intend
to lose the scratch.

**`generate_insar_mask` has two separate memory problems, on two different
grids.** It is called twice (`InSAR_L1_writer.py:535` and `:752`) and the call
sites are *not* equivalent — checking only the first one is how this was
initially misdiagnosed here.

1. It reads `inputDataExceptionMask` **whole for both images** — 2.89 GB each on
   freq A, **5.77 GB** together. This sits on the RSLC swath grid, so it is
   **not** looks-dependent and reducing looks does not help. This is what killed
   the run on the old 3.9 GB box; it is harmless on 31 GB.
2. The second call site (`:752`) builds the **interferogram**-grid mask with a
   pure-Python double loop appending one int per output pixel to a list. Its
   size is set by `crossmul` looks. Proven from the products themselves:

   ```
   interferogram/mask   (5911, 6781)   <- freq B @ 9x1,  40 Mpx   fine
   pixelOffsets/mask    (1659,  208)                             cheap
   ```

   At freq A **1×1** that first grid becomes 53200 × 54244 = **2886 Mpx**:
   2.886e9 Python-loop iterations and ~58 GB of transient list + int64 array.
   Measured 10.3 s/Mpx → **8.3 hours**, then OOM even on 31 GB.

`tools/patches/insar_mask_vectorized.py` fixes (2) by preallocating the uint32
output and vectorising the range loop, leaving only the azimuth loop in Python.
Measured 0.82 s/Mpx (**~40 min** for freq A 1×1, ~17 GB peak) and verified
**bit-identical** against the stock implementation on real freq B data by
`tools/test_insar_mask_patch.py`. Apply with `python tools/apply_patches.py`;
the stock code is preserved as `_generate_insar_mask_stock` and still used when
`num_sub_swaths != 1`.

**snaphu memory is per-TILE × nproc**, at a measured 385 bytes/pixel. On the
13300 × 13561 RUNW grid, `ntiles: [4,4]` with `nproc: 8` is 5.03 GB per process
and **40.2 GB** in total — an OOM. `[8,8]` is 1.44 GB each, 11.5 GB total.
Worse, `single_tile_reoptimize` and `regrow_conncomps` both **default to True**
and each re-solves or relabels over the whole grid: 180.4 Mpx × 385 B = **69
GB**. Both must be false whenever tiling is on. The price is that connected
components come from the tiled solve and its stitching, so seams matter more.

### Long runs

```bash
tmux new-session -d -s trackA "... python run_track_r.py ... | tee log"
tmux attach -t trackA        # Ctrl-b d to detach
loginctl enable-linger $USER # so the user slice survives logout
```

Use `set -o pipefail` and report `${PIPESTATUS[0]}` — `cmd | tee log; echo $?`
reports **tee's** status and will show `EXIT=0` for a failed run.

---

## 8. Verifying the result

Checks that caught real problems here, in order of value:

**Offsets against orbit geometry.** `geo2rdr` measured +746.2 px azimuth for
this pair. Naive timestamp arithmetic predicts +1520.0 px (the frames start
1.000 s apart at 1520 Hz PRF); the 774-px gap is **3.4 km of along-track orbital
phasing**, which is exactly what geo2rdr exists to resolve from orbit state
vectors rather than clocks. Range offset −1.477 px, spread 0.018 px across the
whole frame.

**ampcor residual.** After geometry-only `coarse_resample`, `dense_offsets`
found only **0.06 px range / 0.03 px azimuth** of remaining misregistration,
with 93.2% of 345,072 windows above 0.3 correlation and a 3.0% outlier rate.
Geometry alone got the secondary to within a twentieth of a pixel.

**Independent image-domain check.** Cross-correlating the *rendered* geocoded
amplitudes: coregistered vs reference = **(0, 0) px**; uncoregistered =
**3.35 km**, against geo2rdr's 3.32 km — two entirely separate paths agreeing
within 1%.

**Coherence.** freq B 9×1 RIFG: median **0.5832**, 84.7% above 0.3, 61.5% above
0.5, and **0.000%** at exactly 1.0 — confirming a non-degenerate estimator.

`tools/slc_amp_overlay.py` builds a three-layer Leaflet overlay (reference,
coregistered, raw) on one shared EPSG:3857 grid with a pooled dB stretch, which
is how the 3.35 km check above was made visible. Two traps it documents:
GDAL's geolocation-array warp silently clips a rotated swath unless
`outputBounds` is stated explicitly (the tell is a suspiciously *high* valid
fraction), and sinc resampling leaves ~1e-7 ringing in the fill region that
survives a NaN check as −134 dB and destroys a percentile stretch.

---

## Not yet written up

**Tropospheric correction.** Researched, not run. Known: the stage is
InSAR-only and **GUNW-only** (`insar.py:150-152`), so it is downstream of
unwrap *and* geocode. ISCE3 **does not download** anything —
`troposphere_runconfig.py:41-45` raises if the two GRIB files are absent.
`pyaps3 0.3.7` is installed and ships its own ERA5 fetch (`ECMWFdload`), needing
a `~/.cdsapirc` with **both** a `url:` and a new-style CDS Personal Access Token
(a plain UUID — the legacy `UID:KEY` colon form routes to the retired
`/api/v2` endpoint). RAiDER is **not** installed, so `package: raider` and
`delay_direction: line_of_sight_raytracing` are unavailable. To be added here
once run.

**Track G (GSLC) workflow.** `run_track_g.py` exists and ingest + DEM have run;
`L2_GSLC/` is empty. Known: the GSLC path exposes only `tec_file`,
`reference_gslc` and `correction_luts.solid_earth_tides_enabled`, and all are
**geometric timing shifts** applied during geo2rdr — there is no ionosphere-phase
or troposphere code anywhere in it. Because `gslc.py` never passes
`flatten_with_corrected_srange`, GSLC flattening uses the *uncorrected*
geometric range, so ionospheric and tropospheric **phase survives intact** into a
GSLC × GSLC interferogram. Correcting Track G is therefore net-new code, not a
flag. To be added here once run.

**Track R vs Track G comparison.** `asc/compare/compare_tracks.py` exists and its
tests pass, but it is map-domain: it assumes Track R has already been geocoded
onto the same pinned UTM lattice and it does not resample. It is also still
hardcoded to the Venezuela pair (`igram_metrics.py:18` `LAMBDA` is freq-A
Venezuela; `expected.py`'s baseline and incidence are Venezuela). Nepal is
landlocked, so the water-floor check — the one with the most teeth — cannot run
and needs an honest substitute. To be added here once a comparison AOI is set.
