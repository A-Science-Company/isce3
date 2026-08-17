# Track G — NISAR RSLC → GSLC → interferogram → unwrapped phase

A modular, automated Track G pipeline: two NISAR L1 RSLC granules in, an
unwrapped interferogram and a browsable map out. The two GSLCs are geocoded onto
a **single pinned grid** so they are pixel-aligned by construction rather than by
luck — then hard-verified that they actually are.

This runs end to end: **A, B, G1, G2, QA, G3, W, G4, G5**. Only the Dolphin
time-series stage is still outstanding; the interface it slots into is described
at the bottom.

```
                                     ┌─ stack.json  ← the single source of truth
                                     │   (pinned geogrid: EPSG, corners, posting)
  RSLC ×N ──[A ingest]───────────────┘
                │
                └──[B dem]──── DEM (WGS84 ellipsoidal)
                                     │
                     ┌───────────────┴──────────────┐
                     ▼                              ▼
              [G1 gslc] per date            (runconfig validated
              runconfig → geocode            against the installed
                     │                        yamale schema first)
                     ▼
              [G2 gridgate]  ── hard assert: shape, EPSG, geotransform, pols
                     │            identical across dates AND equal to the pin
                     ▼
                   [QA]  decimated-read quicklooks
                     │
                     ▼
              [G3 igram]  ── one streaming pass over both GSLCs:
                     │        igram + coherence + per-date amplitude
                     ├──────────────┐
                     ▼              │
              [W watermask]         │  built on G3's grid, so it is
              orthometric DEM       │  pixel-aligned by construction
                     │              │
                     └──────┬───────┘
                            ▼
              [G4 unwrap]  Goldstein → phase-sigma coherence →
                     │      water mask → zero the ifg → SNAPHU
                     ▼
              [G5 overlay]  folium HTML over satellite tiles
```

---

## Quick start

```bash
conda activate isce3_env
cd /home/sharath/Desktop/work/isce3/asc/workflows

# see the steps
python run_track_g.py --list-steps

# preview the whole pipeline; writes nothing, but DOES validate every runconfig
python run_track_g.py --config configs/venezuela_t162_asc.yaml --dry-run

# cheap metadata pass: writes stack.json + the pinned geogrid (~0.1 s)
python run_track_g.py --config configs/venezuela_t162_asc.yaml --only ingest

# the DEM (~430 MB, needs Earthdata credentials in ~/.netrc)
python run_track_g.py --config configs/venezuela_t162_asc.yaml --only dem

# the long one: geocode both dates
python run_track_g.py --config configs/venezuela_t162_asc.yaml --only gslc

# verify alignment, then quicklook
python run_track_g.py --config configs/venezuela_t162_asc.yaml --only gridgate qa

# interferogram + coherence + per-date amplitude (~1m50s, one pass, 1.0 GB peak)
python run_track_g.py --config configs/venezuela_t162_asc.yaml --only igram

# water mask, then unwrap (SNAPHU single-tile is the long pole here)
python run_track_g.py --config configs/venezuela_t162_asc.yaml --only watermask unwrap

# the browsable map
python run_track_g.py --config configs/venezuela_t162_asc.yaml --only overlay

# or just run everything
python run_track_g.py --config configs/venezuela_t162_asc.yaml
```

---

## Steps

| # | name | stage | what it does |
|---|------|-------|--------------|
| 1 | `ingest`   | A  | Reads both RSLCs' metadata (h5py + shapely, **no raster access**). Writes `stack.json`: per-granule dates, orbit direction, look side, frequencies, pols, dims, spacings, bounding polygon — plus the **pinned geogrid**. |
| 2 | `dem`      | B  | Stages a WGS84-**ellipsoidal** DEM covering the AOI via `sardem`, NISAR route with a Copernicus fallback. Idempotent: skips if an existing DEM already covers the AOI. |
| 3 | `gslc`     | G1 | Renders one runconfig per date from the pinned geogrid, **validates it against the installed yamale schema before running anything**, then invokes `nisar.workflows.gslc`. Verifies the product landed on the pinned grid. |
| 4 | `gridgate` | G2 | Hard assert that every GSLC is pixel-aligned: shape, EPSG, geotransform `allclose`, pol set — and that all of them match the pin. Fails loudly with a field-by-field diff. |
| 5 | `qa`       | QA | Decimated-read quicklooks. **Never loads a full raster.** |
| 6 | `igram`    | G3 | Conjugate product, multilook coherence and **per-date amplitude**, all from ONE streaming pass over both GSLCs. Never loads a full raster. |
| 7 | `watermask`| W  | Water mask on an existing product's grid, from the DEM converted to **orthometric** height. Replaces the course's broken NASADEM route. Refuses to emit a degenerate mask. |
| 8 | `unwrap`   | G4 | Goldstein filter → phase-sigma coherence → water mask → zero the interferogram → SNAPHU. A faithful port of the isce+ course chain, with its divergences listed. |
| 9 | `overlay`  | G5 | One folium HTML with every raster as a toggleable layer over Google Satellite tiles. |

> **`igram` is step 6 and `watermask` is now step 7** (it was 6). The water mask
> is built *on the interferogram's grid* so that it is pixel-aligned by
> construction, which means the interferogram has to exist first. On a fresh case
> the old ordering had nothing to build against. Selection is by name far more
> often than by number, so `--only watermask` is unaffected.

Steps are selected by number, exact name, or unique prefix:

```bash
--only ingest            # one step
--only 3 4               # several
--start-step gslc        # resume after a crash; earlier outputs assumed on disk
--stop-step gridgate     # stop early
```

Everything is idempotent — re-running skips completed work — until you pass
`--force`.

---

## Configuration

One YAML drives the whole run. See `configs/venezuela_t162_asc.yaml`, which is
ready to go against the real granules and is heavily commented.

Ergonomics match the existing ISCE2 gen-2 wrapper: the YAML holds run identity
and parameters, unknown keys **warn** rather than fail (so a config written for a
newer version still runs), and a few flags that change run-to-run can override
the YAML on the command line:

```bash
--frequencies A B        # override `frequencies`
--polarizations HH HV    # override `polarizations`
--dem-source COP         # override `dem.source`
```

### The two config errors that matter

The config layer refuses to run rather than produce a quietly-wrong product:

* **`geogrid.posting.<F>.{x,y}: null`** → rejected. A blank posting makes ISCE3
  inherit the DEM's ~30 m spacing for a *complex* SLC, irreversibly decimating it.
* **`gslc.flatten: false`** → rejected. Flattening strips the range carrier phase;
  leaving it in is irreversible and every downstream interferogram carries a huge
  ramp.

---

## Why the grid is pinned, and why there is a gate

Stage A computes the union AOI of both footprints, derives the UTM zone
(`point_to_epsg` → 32619 here), projects **every densified polygon vertex** (not
just the bbox corners — UTM is non-linear), snaps the envelope outward to a
1000 m multiple, and writes those absolute corners into `stack.json`. Both dates'
runconfigs then get the *same* `top_left` / `bottom_right` / `output_posting`.

`x_snap` / `y_snap` are deliberately left **null**. With all four corners plus
both postings supplied, `nisar.workflows.geogrid.create` takes its deterministic
`_grid_size` path and the pinned corners survive verbatim. Setting a snap instead
re-snaps the grid — and worse, `geogrid.create` evaluates `x_snap <= 0` *before*
testing for `None`, so supplying only one of the pair raises a `TypeError` from
deep inside ISCE3. Stage A has already snapped, so there is nothing to gain and
correctness to lose. (The installed `defaults/gslc.yaml` carries both keys as
nulls and ISCE3 deep-merges our runconfig over that, so omitting them is safe.)

Because `grid_size()` mirrors ISCE3's `_grid_size` exactly, `stack.json` records
the shape ISCE3 *will* write. Stage G2 therefore checks a **prediction**, not
merely whether two unknowns happen to agree.

That distinction catches a failure the obvious check misses: if the posting
silently fell back to the DEM spacing, both dates would be equally wrong and a
pure cross-comparison would pass. Verified behaviour:

```
### both dates identical BUT posting fell back to DEM ~30 m
GRID GATE FAILED — the GSLC products are NOT pixel-aligned.
  Disagreements with the pinned geogrid in stack.json:
    freq B 20260613: shape [9967, 10100] != pinned [59800, 7575]
    freq B 20260613: x spacing 30.000000 != pinned 40.0
    ...
```

The gate exists because the isce+ course notebook reads the *same index range*
from two GSLCs and cross-multiplies them, having never compared shapes, origins,
spacings or EPSG. Its markdown asserts that same-track/frame GSLCs share a grid —
asserted, never tested, and the notebook was never executed. When that assumption
breaks, the result is not an error: it is a plausible-looking interferogram of two
different pieces of ground, and low coherence has a hundred innocent explanations.
So: pin at one end, refuse at the other.

---

## Vertical datum

ISCE3 geocoding needs **ellipsoidal** heights. A geoid-referenced DEM biases
geolocation by the local undulation — about **−20 m** over this AOI (range −9 m in
the SW to −38 m in the NE), far larger than the accuracy this pipeline exists to
achieve. Both routes deliver ellipsoidal heights:

| source | datum | ocean | credentials |
|---|---|---|---|
| `NISAR` (default) | native WGS84 ellipsoidal | real values, ≈ −20 m | **required** — `~/.netrc` entry for `urs.earthdata.nasa.gov` |
| `COP` (fallback) | EGM2008 → WGS84 by sardem | **nodata == 0 by design** | none |

The datum check is **scoped by source**, because on the COP route sardem passes
`-srcnodata 0 -dstnodata 0` to preserve ocean through the geoid conversion. Ocean
reading 0 there is expected, not a datum bug — applying the NISAR test would flag
a perfectly good DEM. Coverage is verified, never an exact pixel count: sardem
snaps bounds outward to source pixel edges at `(k+0.5)/3600`, so a bbox on whole
1/3600 multiples yields N+1 pixels per dimension.

The NISAR route's credential requirement is a hard precondition enforced *before*
any network call, so a missing login fails in milliseconds rather than midway.

### Known upstream breakage

**`sardem --data-source NASA_WATER` is broken.** The SRTMSWBD tiles 404 at
`e4ftl01.cr.usgs.gov`, and sardem substitutes zeros while still exiting 0 —
silently yielding an all-land mask that every downstream masking step would
consume as truth. The `.wbd` grid also does not co-register with the DEM grid.

Stage 7 `watermask` replaces it. See **Water mask** below.

---

## The interferogram (stage G3)

One streaming pass over both GSLCs produces six rasters on the multilooked grid:

```
ifg_B_HH.igram.tif     complex64   ref * conj(sec), block-averaged
ifg_B_HH.coh.tif       float32     multilook coherence magnitude
ifg_B_HH.nlooks.tif    float32     valid samples per look box
ifg_B_HH.amp.tif       float32     sqrt(sqrt(P1*P2)/n) — the PAIR's geometric mean
amp_B_HH_20260613.tif  float32     per-date amplitude, sqrt(mean power)
amp_B_HH_20260625.tif  float32     per-date amplitude
```

At 16 × 2 looks the 59800 × 7575 GSLC grid becomes **3737 × 3787 at 80 × 80 m**,
EPSG:32619, origin (434000, 1351000). Measured: **1 m 48 s, 1.0 GB peak RSS**.

### Why there is no flattening step

Both GSLCs are already flattened — `geocodeSlc` multiplies every sample by
`exp(+i·4πr_k/λ)` with `r_k` the geo2rdr slant range to the DEM surface at that
map cell. So `ref · conj(sec)` is *already* topo- and ellipsoid-flattened.
`gslc.flatten` is hard-required true by the config layer precisely so this holds.
Order is `ref · conj(sec)`, matching ISCE3 `Crossmul`, so this track shares a
sign convention with everything else in the house.

### The coherence estimator (course bug 7.1)

The course averages the complex SLCs and *then* forms coherence from the averaged
fields, which destroys the speckle the estimator exists to measure. Here the three
sums `Σs₁s₂*`, `Σ|s₁|²`, `Σ|s₂|²` are accumulated at **full resolution** inside
each look box and the ratio is taken only afterwards.

Measured on this pair: **median 0.2714, 45.05 % of valid pixels above 0.3,
67.25 % of the bounding box in-swath.** That median is the whole-scene figure
including water; the land/water split is in the Water mask section below.

### One pass, not three

The per-date amplitudes are computed in the same loop as the interferogram.
Running the two loose scripts separately costs **three** full reads of a 3.4 GiB
raster; this costs one. The distinction that matters is the validity mask:

* interferogram and coherence use the **joint** mask (a sample must be valid on
  both dates), because they are pair facts;
* each per-date amplitude uses **that date's own** mask, because it is a per-date
  fact — a sample can be valid on one date and fill on the other.

Verified: all six rasters come out **bit-identical** to the products the two
loose scripts wrote. One subtlety was worth fixing to get there — the valid-sample
count must be summed as an **integer** so that `Σpower / n` is evaluated in
float64 before being rounded to float32. Counting in float32 instead costs a
last-bit error on ~0.01 % of pixels (max relative 1.19e-07, exactly one float32
ULP).

### The pair `.amp` band is not per-date backscatter

`ifg_B_HH.amp.tif` is `sqrt(sqrt(P1·P2)/n)` — the **geometric mean of the two
dates**, one raster for the pair. It cannot answer "what did this pixel look like
on the 13th versus the 25th", which is exactly what the overlay needs, hence the
separate per-date rasters. Those use

```
A = sqrt( Σ|s|² / n_valid )
```

the square root of **mean power**, not the mean of amplitudes — averaging `|s|`
biases low and is not the multilook speckle estimator.

---

## Water mask

The course derives water from NASADEM's height band:

```python
water = ((h >> 15) & 1) | (h == -32768) | (h <= 0)   # flag | void | at-or-below sea level
```

Bit 15 is unrecoverable here — it lives in NASADEM, which is what 404s. What
*is* recoverable is the term that carries the information over this AOI: the
sea-level threshold. Stage 7 rebuilds it from the DEM we already stage.

### The vertical-datum trap

`h <= 0` is an **orthometric** test. NASADEM is EGM96-referenced, so 0 m *is*
sea level. Our DEM is WGS84 **ellipsoidal** — it has to be, ISCE3 geocoding
requires it (see *Vertical datum* above). Over this AOI the geoid undulation
runs −9.4 m in the SW to −33.3 m in the NE, so local sea level sits near
**−28 m ellipsoidal, not 0**.

Applying `h <= 0` unchanged is wrong in the worst direction — it drowns every
coastal pixel below roughly +30 m orthometric. Measured on this scene:

| | water fraction of swath |
|---|---|
| naive `h_ellipsoidal <= 0` | 49.84 % |
| correct `H_orthometric <= 1 m` | 47.23 % |

The difference is **248,650 pixels = 1,591 km² of real land**, median true
elevation 8.4 m, median amplitude −8.05 dB (land-bright). That is precisely the
coastal plain a deformation study cares most about.

So the stage converts to orthometric first (`EPSG:4979` → `EPSG:9518`, EGM2008)
and *then* applies the course's threshold with the course's semantics intact.

### Why the threshold is +1 m and not 0 m

The DEM's ocean is filled at exactly mean sea level, so the round trip back to
orthometric lands it on H = 0.00 with sub-metre scatter (the DEM's internal
geoid model vs. the EGM2008 2.5′ grid). Thresholding at exactly 0 slices that
population down the middle:

```
H <= 0.00 m : deep water captured  49.74% | solid land false-water 0.01%
H <= 0.25 m :                      98.09% |                        0.02%
H <= 1.00 m :                      98.21% |                        0.06%   <- default
H <= 2.00 m :                      98.23% |                        0.26%
H <= 3.00 m :                      98.24% |                        0.40%
```

Capture is flat from 0.25 m to 3 m, so the result is insensitive to the exact
value — the threshold sits in a real valley, not on a slope. At 0 m it is a
per-pixel coin flip.

### The silent failure this stage refuses to have

pyproj's EGM2008 transform needs the grid `us_nga_egm08_25.tif`. If it is absent
and `PROJ_NETWORK` is off, PROJ does **not** fail — it falls back to a "ballpark
vertical transformation" that returns the input height **unchanged**. Measured
here before the grid was installed:

```
OFFLINE z = [0. 0. 0.]        <- ballpark: no geoid applied at all
```

That is the same silent-wrong-answer bug as sardem's zero substitution, and it
would quietly turn this stage into the naive ellipsoidal threshold. So the
transform is **probed against a known EGM2008 value** (N at 0°,0° = 17.225 m)
before it is trusted, and the stage raises rather than return a plausible wrong
mask. One-time setup, ~80 MB, then fully offline:

```bash
python -m pyproj sync --file us_nga_egm08_25
```

### Verification

Three independent checks, all run:

* **Coastline** — the mask boundary tracks the visible coastline in
  `qa/gslc_compare_B_HH.png`, bays and inlets included (`qa/watermask_B_HH.png`).
* **Amplitude** — an independent −12.96 dB split of the freq B HH amplitude
  gives 45.00 % water against the DEM route's 47.23 %: **93.97 % pixel
  agreement, IoU 87.70 %**.
* **Coherence** — the mask partitions the coherence populations *better* than
  the amplitude split does:

  | | land median | land > 0.3 | water median |
  |---|---|---|---|
  | amplitude split | 0.432 | 71.2 % | 0.175 |
  | this mask | **0.448** | **74.7 %** | 0.173 |

  Water sits on the estimator floor `sqrt(pi)/(2*sqrt(22)) = 0.189` either way,
  which is what open water should do.

### A dB-convention trap worth knowing

`gslc_igram.py` writes `amp = sqrt(sqrt(p1*p2)/n)` — a **magnitude**. True dB is
therefore `20*log10(amp)`. Feeding a magnitude to `10*log10` halves every dB
value, which is the origin of the **−6.48 dB** threshold quoted for this case:
its true-dB equivalent is **−12.96 dB**, and −12.96 dB is exactly the 45th
percentile of the amplitude distribution. In other words that threshold was a
percentile in disguise, not a physical sea/land boundary — it cannot validate a
water fraction, because the water fraction is what defined it. The config warns
if `watermask.amplitude_db` is given a value above −8 dB.

### Polarization

**This product is DHDH: the polarizations are HH and HV. There is no VV.** Where
a VV amplitude is called for, **HH is the correct substitute** — it is the
co-pol channel, and co-pol is what the low-backscatter-over-water assumption
relies on. HV is cross-pol and sits several dB lower over land, which would move
any amplitude threshold. Everything above is measured on **HH**.

### Usage

```bash
python run_track_g.py --config configs/venezuela_t162_asc.yaml --only watermask
```

```yaml
watermask:
  method: dem_orthometric                 # primary; 'amplitude' is the fallback
  reference_raster: pairs/20260613_20260625/trackG/ifg_B_HH.amp.tif
  geoid_crs: EPSG:9518                    # WGS84 + EGM2008 height
  sea_level_margin_m: 1.0
  include_inland: false                   # true also catches Lake Valencia
  ocean_probe: [600000.0, 1200000.0, 680000.0, 1260000.0]
```

Output `aux/watermask/watermask_<case>.tif` — uint8, **1 = water, 0 = land,
255 = outside the radar swath** (255 matches the GSLC `mask` layer's own fill
value, so the two compose without translation). Built on the reference product's
grid, so it is pixel-aligned by construction. 9.6 s, ~700 MB peak.

Two assertions stand between this stage and a bad product: the water fraction
must land inside a sane band (an all-land mask is the NASADEM signature), and
`ocean_probe` — a box declared to be open ocean — must come out >95 % water over
its **in-swath** pixels. That last qualifier matters: the swath is a rotated
parallelogram inside a north-up bounding box, so a plausible Caribbean box near
the NE corner is half nodata. A box that was 99.95 % water over its valid pixels
first reported 48.96 % and tripped the assertion, which is why coverage and
water fraction are now checked separately.

### Routes considered and rejected

| route | verdict |
|---|---|
| **DEM → orthometric, threshold** | **chosen.** Offline after a one-time 80 MB grid, 9.6 s, no speckle (0.0000 % false water on land above 50 m), sharp coastline. Blind to inland water above sea level. |
| amplitude threshold | **fallback.** No extra data at all, and it *does* catch inland water. But 3.58 % false water on land above 50 m (radar shadow, smooth surfaces), it mistakes wind-calm sea state and the far-range swath edge for land, and its threshold has no physical anchor. |
| GSHHG / Natural Earth polygons | **rejected.** Not usable offline: no vector coastline ships in `isce3_env`, and the only GSHHG on this machine is GMT's *binned* `.nc` in the `.gmtsar` env, which fiona/GDAL cannot read (`not recognized as being in a supported file format`). Both sources download fine (NE ocean 3.2 MB, GSHHG shapefiles 149 MB) but that is a new network dependency for a worse coastline than the DEM already gives — vector shorelines are static and would not match this DEM's own ocean fill. |
| GSLC `mask` layer | **rejected — it does not mark water at all.** Read in full, it holds exactly three values: `1` (67.42 %, valid subswath-1 sample), `255` (32.11 %, outside the acquisition extent), `0` (0.47 %, a partially-focused RSLC pixel entered the interpolation window). Its own description says "Mask indicating the subswath number representing valid GSLC samples". Open water is *inside* the swath and marked `1`, identically to land. Useful only as the in-swath/outside test, which the reference raster's nodata already provides. |

---

## Unwrapping (stage G4)

A faithful port of the isce+ course chain (`utils.py`, Zhenli Tang / Zhang
Yunjun, July 2026), in the course's own order:

```
multilook            ← already done, in stage G3
Goldstein filter     → ifg_B_HH.filt.int.tif
phase-sigma coh      → ifg_B_HH.filt.phsig.coh.tif    FROM THE FILTERED IFG
water mask           → zeroed INTO the interferogram, not passed to snaphu
snaphu.unwrap        → ifg_B_HH.filt.unw.tif + .filt.unw.conncomp.tif
```

The `.filt.` infix mirrors the course's own `filt_mli.*` naming and keeps these
products distinct from any hand-run unwrap sitting next to them: this chain's
input is the *filtered* interferogram and its correlation is *phsig*, so it is a
different product from an unwrap driven by the boxcar coherence.

### Which coherence feeds snaphu — not the one you would guess

**The phase-sigma coherence, not the boxcar coherence.** `ifg_B_HH.coh.tif` — the
`|Σs₁s₂*|/sqrt(Σ|s₁|²·Σ|s₂|²)` estimator that stage G3 writes and that the QA
numbers quote — is the **display product**; the course never unwraps with it. Both course
drivers pass `filt_mli.phsig.coh.tif` as `corr`. Three corroborating facts from
the course code:

* the boxcar coherence is produced at **full resolution** and explicitly *not*
  multilooked, so it is not even on the interferogram's grid — a shape mismatch
  would be immediate;
* the stack driver passes `save_full_res=False`, under which the boxcar branch is
  skipped **entirely** and `coh_list` comes back empty. That pipeline runs end to
  end without ever computing a boxcar coherence;
* in the single-pair notebook the boxcar `coh` appears only in display calls.

So stage G4 generates phsig from the filtered interferogram and feeds *that*.
Measured here, phsig separates the scene far more sharply than the boxcar
coherence does:

| | phsig | boxcar coherence |
|---|---|---|
| unmasked land | **0.863** | 0.43 |
| water | **0.092** | 0.175 |
| outside the swath | 0.003 | NaN |
| median over all nonzero | 0.515 | 0.271 |

That gap is expected, and the reason is the next section.

### phsig is computed from the FILTERED interferogram, on purpose

This inflates phsig relative to a true coherence — Goldstein filtering reduces
local phase variance, and phsig is *defined* as an inversion of phase variance:

```
coh = 1 / sqrt(2·nlks·var + 1)
```

Do not "improve" this by feeding it the unfiltered interferogram. snaphu's cost
model was tuned against exactly this convention in ISCE2. It is also why the
numbers in the table above are not comparable across columns: they are different
estimators of different things, one of them measured on a noise-suppressed field.
phsig is not "better" coherence — it is a different quantity that snaphu's cost
model expects.

phsig is also computed **before** water masking, on the unmasked filtered field
— the mask never touches the correlation array.

### The nlooks asymmetry — the thing that silently breaks a port

Two different quantities, fed differently:

| consumer | value | what it is |
|---|---|---|
| `estimate_phsig_correlation` | `nlks = ry·rx` = **32** | the NOMINAL look count |
| `snaphu.unwrap` | `nlooks = ry·rx / 1.2²` = **22.22** | the EFFECTIVE (independent) look count |

The 1.2 per dimension (1.44 total) is the ISCE2 convention for converting a
nominal look count into an equivalent number of *independent* looks: an SLC is
oversampled ~20 % relative to its true resolution in each dimension, so adjacent
samples are correlated and a nominal N-sample average does not deliver N
independent samples. snaphu uses `nlooks` only inside its statistical cost model,
mapping correlation onto expected phase variance — understating it makes snaphu
trust the coherence *less* and produce a smoother, more conservative solution, so
`/1.44` errs in the safe direction.

**Why this is a trap.** In the course's 4 × 2 configuration those two numbers are
8 and 5.56 — and `generate_phsig_coh_tif`'s *default* `nlks` is also 8, so the
stack notebook can omit the argument and still be correct **by coincidence of the
4 × 2 configuration**. At our 16 × 2 = 32 looks the coincidence breaks: anyone
copying the notebook's bare `generate_phsig_coh_tif(out_file)` inherits a silent
`nlks=8` and badly overestimates coherence. Both numbers are passed explicitly
here, derived from `igram.looks_y × igram.looks_x` so they cannot drift apart,
and both are recorded in the provenance sidecar.

**One caveat written down rather than silently inherited.** The 1.2 oversampling
factor is a property of a *slant-range* SLC. Our inputs are geocoded GSLCs on a
40 × 5 m posting, where inter-pixel correlation is set by the geocoding
resampling kernel, not by the original range/azimuth oversampling. 1.44 is not
derived for this geometry. It is kept because it errs conservative and because it
agrees with the measurement: `32/1.44 = 22.2`, and the observed water coherence
floor inverts through `E[|γ|] = sqrt(π)/(2√L)` to `L ≈ 22`. Two independent
routes to the same number.

### The mask is applied by zeroing the interferogram

Not via snaphu's `mask=` argument. The course is explicit — *"invalid regions are
zeroed in igram, no separate mask needed"*. Masked pixels therefore reach snaphu
as zero-magnitude complex paired with a **nonzero** correlation, because the
course does not zero `corr` either. That is faithful and it is kept; switching to
`mask=` changes connected-component labelling.

Measured on this pair: **64.51 % of the grid masked** — 4,635,353 px outside the
swath plus 4,494,562 px of water, against a swath water fraction of **47.23 %**.

### Deliberate divergences from the course code

Everything that changes behaviour, listed rather than buried:

| divergence | why |
|---|---|
| **NaN → 0 before filtering** | The course's nodata test is `arr == no_data_value`, and `nan == 0` is False. A single NaN poisons its whole 32×32 FFT patch and, through the 50 % overlap, a 48×48 neighbourhood. **32.75 % of this grid is NaN** — not a corner case, it would wreck the entire frame edge. |
| **phsig `batch_size` 500 → 5000**, index arrays built with `repeat`/`tile` as int32 rather than two int64 meshgrids | Pure speed and memory. **Verified numerically identical** — same iteration order, same arithmetic, `maxdiff 0.0` against the course's batch size. Took phsig from a projected several minutes to **39.6 s**. |
| **conncomp filename constructed from the prefix** | The course derives it with `str.replace('.unw.tif', ...)`. If the output does not end in `.unw.tif` the replace is a no-op and **the conncomp overwrites the unwrapped phase**. |
| **conncomp written UInt16** | The course's two drivers disagree — UInt16 in the stack path, Int32 in the notebook. UInt16 is the leaner and MintPy reads either. |
| **explicit GDAL dtype on write** | The course's `save_tiff` auto-maps numpy dtypes and lets anything unmapped (complex128, float16, int64, **bool**) fall through to Float32 silently. |
| **DEFLATE + tiled + nodata** | The course passes no creation options at all. No numerical change. |
| **water-mask producer substituted** | See below. The *consumer* contract — warp a categorical mask onto the target grid, nearest neighbour — is kept intact. |

`multilook_tif`'s multi-band loop is deliberately **not** ported: it calls
`save_tiff` once per band with the same output path, and `save_tiff` does
`drv.Create` each time, truncating the file. Only the last band survives.

### Why the water-mask producer had to be replaced

`download_nasadem_water_mask` is unusable here and dangerous in a specific way:
it pre-fills its mosaic with **255 = WATER** and `continue`s past any tile that
404s or errors. The LP DAAC `lp-prod-protected` route needs a token-bearing
redirect that plain `requests` basic auth often does not satisfy, so the failure
mode is that *every* tile 404s, the function **exits 0**, and it writes a
100 %-water mask — which would then mask the entire scene at unwrap time, with no
error anywhere. Its classifier is independently wrong for us too: `h <= 0` is an
orthometric test applied to our ellipsoidal DEM, and `(h >> 15) & 1` is just the
int16 **sign bit**, not a water flag (NASADEM's actual water body data is a
separate `NASADEM_SWB` product).

So stage 7 supplies the mask and stage G4 consumes it, and G4 **refuses to
unwrap** if the mask covers more than `unwrap.water_max_fraction` (default 60 %)
of the swath. An all-water mask is the signature of that failure, and this
assertion is the thing standing between it and a silently empty product.

`water_mask_on_grid` resolves the mask in three steps, all of which end in the
same contract the course's `load_water_mask` provides — a categorical mask
resampled onto the target grid with **nearest neighbour**:

1. the stage-7 product, warped onto this grid if it was built on another;
2. **primary** — the DEM converted to orthometric height and thresholded, built
   in memory (this is what stage 7 would have written);
3. **fallback** — an amplitude threshold, which needs no ancillary data at all.

Route 2 is verified against route 1: forced to build in memory, it agrees with
the stage-7 product **100.0000 %** over the swath (47.23 % water either way,
839 MB peak, 6.5 s), with the geoid probe reporting N(0,0) = 17.2251 m and an
undulation range of −33.28 … −9.42 m over the AOI.

### Cost

| step | measured |
|---|---|
| Goldstein filter, α=0.5, psize=32 | **5.9 s** (~55 k 32×32 FFTs) |
| phase-sigma coherence, 5×5, batch 5000 | **39.6 s** (2825 batches) |
| water mask (reused from stage 7) | 0.1 s |
| **SNAPHU, single tile, 14.2 Mpx** | **33 m 10 s** |
| whole stage | **34 m 00 s, 5.3 GB peak RSS** |

The **single-tile SNAPHU solve is 98 % of this stage** — everything before it
takes 46 s combined. Measured on this 14.2 Mpx grid: **33 m 10 s**, single-
threaded at 100 % of one core, holding a steady **~4.8 GB RSS** (5.3 GB for the
whole process). On a 12.7 GB box that fits, but with little to spare — watch it
if you raise the resolution or add a frequency.

Worth knowing where the time goes, because it is a direct consequence of course
fidelity: the mask is applied by **zeroing the interferogram**, not via snaphu's
`mask=`. Those 9.1 M masked pixels (64.5 % of the grid) therefore stay in the
network as ordinary nodes with zero phase, so snaphu solves a 14.2 Mpx problem to
recover 5.0 Mpx of answer. Passing `mask=` instead would be far faster and is
what `asc/compare/gslc_unwrap.py` does — but it changes the connected-component
labelling, so the faithful port keeps the zeroing.

Note that `single_tile_reoptimize` has **no effect** at `ntiles: [1, 1]` —
snaphu-py guards it with `if (not single_tile) and single_tile_reoptimize`, so
there is no second pass here. The ~5 GB and the wall clock are one cold solve,
not two.

`unwrap.ntiles: [4, 4]` with `nproc: 8` is far faster and lighter on this grid,
but per-tile reoptimisation **changes the solution and the connected-component
labelling**. That is a scientific change, not a performance flag, so the default
is the course's single tile and the config layer warns if you change it.

### Measured result, and whether it is trustworthy

```
grid                    3737 x 3787 = 14,152,019 px
outside the swath        4,635,353
water                    4,494,562
masked (union)           9,129,915  = 64.51% of the grid
unmasked (unwrappable)   5,022,104
connected components     1
largest component        4,946,500 px = 98.49% of unmasked
unwrapped phase range    -32.584 .. +15.939 rad   (span 48.5 rad = 89.5 cm LOS)
p2 .. p98                -20.611 .. +4.190 rad
```

**One component covering 98.49 % of the unwrappable area is a good result** — the
solution is a single self-consistent surface, so the whole land area shares one
integer-cycle reference and no inter-component ambiguity has to be resolved. The
75,604 pixels snaphu declined to place are the ones it should decline: their
median phsig is **0.116**, against **0.865** on the pixels it did place.

The decisive check is that the unwrapped phase actually rewraps onto its input:

```
|angle( exp(i*unw) * conj(filt/|filt|) )| over the largest component
    median 0.000002 rad   mean 0.000004   max 0.000047
    fraction within 1e-4 rad: 100.000%
```

So `unw ≡ arg(filt) (mod 2π)` everywhere it is defined — the unwrapping added
only integer cycles, which is exactly the property that makes it an unwrapping
rather than a smoothing.

### But it is not displacement, and the map says so

```
best-fit plane  -4.2e-05 rad/px x   -6.8e-03 rad/px y
   ->  -0.16 rad across 3787 cols,  -25.41 rad across 3737 rows
variance explained by a plane: 47.8%
residual std 4.593 rad = 8.5 cm
```

A ~25 rad north–south gradient with essentially **zero** range component, over a
12-day L-band pair at 10° N. That is orbital/ionospheric in character, not ground
motion, and the non-planar remainder (8.5 cm) is the broad concentric structure
visible in the wrapped layer. Until a ramp is removed and tropospheric/ionospheric
corrections are applied, this is an interferogram, not a displacement map. The
overlay legend states this, computed from the raster at build time rather than
hardcoded.

---

## The overlay (stage G5)

One folium HTML with every raster as a toggleable layer over Google Satellite
tiles, in this order:

```
Amplitude HH <ref>    dB    gray               pooled 2-98% over BOTH dates
Amplitude HH <sec>    dB    gray               the same scale, so they compare
Wrapped phase         rad   twilight_shifted   CYCLIC, fixed -pi..pi
Coherence             0-1   viridis            fixed, never percentile-stretched
Unwrapped phase       rad   RdBu_r             symmetric robust, conncomp>0 only
Phase-sigma coherence 0-1   viridis            the correlation snaphu actually saw
```

The last layer is additive (`overlay.include_phsig`, default true) and is there
because the correlation that drove the unwrapping is *not* the coherence on
display — see above.

### Polarization: there is no VV

**The granules are DHDH (HH + HV) and the L2 GSLCs carry HH only** —
`listOfPolarizations` is `[b'HH']` in both products, so at L2 there is no VV *and
no HV*. Where a VV amplitude is asked for, **HH is the correct substitute**: it
is the co-pol channel, and co-pol is what the low-backscatter-over-water
assumption depends on. But HH is not VV — the two differ measurably over the same
ground, and the HH/VV ratio is itself the basis of several soil-moisture
retrievals.

So every amplitude layer is named `Amplitude HH <date>`, the map carries a
visible note saying VV is not present, and neither should be relabelled.

### Why the warp target is EPSG:3857 and not EPSG:4326

Leaflet places an `ImageOverlay` by projecting the SW/NE corners into **Web
Mercator** and stretching the PNG *linearly* between them. A plate-carrée image
is linear in **latitude**, and latitude is not linear in Mercator y — the two
agree only at the corners and diverge in between. Measured on this scene:

```
EPSG:3857 (warp target)  drawn at 10.868233 N   error    -0.0 m
EPSG:4326 (naive)        drawn at 10.871311 N   error  +340.3 m
```

340 m is **4.3 pixels at 80 m**, a systematic bow peaking at mid-swath — exactly
where you would be comparing a fringe against a coastline in the basemap. Every
layer is warped onto **one** pre-computed 3857 grid (explicit `outputBounds` plus
width/height), never each with its own auto-computed grid, so all layers share
byte-identical bounds and register against each other in the browser.

Warp the **data**, then colour it — never colour first and warp the PNG. The
wrapped phase is warped as the **complex** interferogram with `np.angle` taken
afterwards, so resampling averages *phasors* rather than angles; averaging angles
across the ±π branch cut gives a value near zero that is near neither input.

### PNGs are index arrays, not `plt.savefig`

`plt.savefig(..., bbox_inches='tight', dpi=150)` re-measures the axes and crops to
them, and dpi/figsize decide the pixel count — so the saved image is a
**resampled, re-cropped** version of the array whose georeferenced bounds you
then declare. Close, but the pixel grid no longer corresponds 1:1 to the raster,
and the error is silent. Here each layer becomes an `(H, W)` index array written
with PIL, so output pixel `(i, j)` **is** input element `(i, j)`.

Paletted 8-bit rather than RGBA, and that is lossless rather than a compromise: a
matplotlib colormap **is** a 256-entry lookup table, so a single-colormap layer
never holds more than 256 distinct colours. Storing 4 bytes/px just hands the
encoder incompressible noise in three correlated channels. Measured: five layers
at ~87 MB RGBA become **39 MB** paletted, and encoding drops from ~4.5 s to
~0.7 s per layer.

Transparency is carried the whole way as NaN (`srcNodata=nan` through the warp),
then alpha 0 wherever the value is not finite. The swath is a rotated
parallelogram — **a third of every image must be transparent** or the basemap
sits under a big dark rectangle.

### Why `branca.colormap` is not used for the legends

Two concrete blockers, both verified:

1. Every branca `ColorMap` renders `d3.select(".legend.leaflet-control")`, and
   `d3.select` returns the **first** match — so with several colormaps on one map
   all the SVGs append into the first legend div and the rest render empty. This
   is the blocking one: we have six layers.
2. `ColorMap.render()` injects a **CDN** `JavascriptLink` for `d3.min.js` — one
   more third-party origin to reach at load time.

Instead each colorbar is rendered once with matplotlib into a small base64 PNG
and placed in one legend panel wired to Leaflet's `overlayadd`/`overlayremove`,
so the bar you see is the layer you are looking at. Leaflet never fires
`overlayadd` for the layer that starts on the map, so the initial one is seeded
from Python.

**Being precise about "self-contained":** the *data* is. Every raster is a local
PNG and no pixel comes off the network. The page is not offline-capable, though —
folium's own base template loads Leaflet, Bootstrap and Font Awesome from
jsDelivr/cdnjs, and the basemap tiles are Google's. Avoiding branca removes one
further CDN origin and, more importantly, the `d3.select` collision; it does not
make the file standalone. Verified in the built page: `d3.min.js` absent, nine
folium-supplied CDN URLs present.

### Size

Layers are written as sidecar PNGs and added with `show=False` except the first,
so the browser creates an `<img>` only for the visible overlay and the rest
download on demand. Base64-embedding every layer at full resolution is **not**
sane — that would be ~57 MB of JS string, parsed on every load regardless of
which layers are on. `overlay.embed: true` is there for a portable single file
and is only reasonable together with `overlay.decimate: 2`.

Measured, six layers at full resolution (3793 × 3759 in EPSG:3857):

```
trackG_overlay.html            57,240 bytes
trackG_overlay_layers/         42.7 MiB total
    wrapped_phase.png           9,500,500
    coherence.png               8,854,948
    amp_20260613.png            8,708,645
    amp_20260625.png            8,636,132
    phsig.png                   6,906,352
    unwrapped_phase.png         2,195,220
build time 6.2 s, 755 MB peak RSS
```

The HTML itself is 57 KB because the only base64 in it is the legend colorbars.

One trap worth knowing: `folium.utilities.image_to_url` base64-inlines **any**
string that is not a recognised URL scheme — it does `open(image, 'rb')` on it.
So passing a relative path *inlines* it rather than linking it. To genuinely
link, the overlay is constructed with a valid-scheme placeholder and its `.url`
set afterwards.

### The map says what the phase is not

The unwrapped phase is **not displacement**, and the legend says so. A
least-squares plane is fitted at build time over `conncomp > 0` and the variance
it explains is reported in the note — a large planar term over a 12-day L-band
pair at 10° N is orbital/ionospheric in character, not ground motion. This is
computed from the actual raster rather than hardcoded, so it stays true if the
chain is re-run with different settings.

Every connected component also carries its own **arbitrary integer-cycle
offset**; only pixels sharing a label are mutually comparable. The layer is
referenced to the median of the **largest** component so the colours mean
something there, and `conncomp == 0` is fully transparent.

---

## Memory discipline

The granules are ~24.5 GiB each and only ~4 GB of RAM is free, so **no code path
loads a full raster**. Full-frame overviews use block-wise **power** multilook
(the correct incoherent estimator for speckle) with the band size derived from a
byte budget (`qa.max_read_bytes`, default 256 MiB).

A naive `[::10,::10]` would be actively bad here: the rasters are gzip+shuffle
chunked at 512×512, so a strided read still *decompresses every chunk it touches*
— nearly a full-file read, with 99% of the result discarded. Block multilook reads
each chunk once and uses all of it.

Look factors come from **ground spacing**, not pixel counts, so overviews have
near-square ground pixels. This matters for freq B, whose 40 × 5 m grid would
render 8:1 stretched under equal pixel decimation.

Measured: full-frame QA of both granules (freq B HH, 2.88 GB each) took 15 s each
at **830 MB peak RSS**.

### Course bugs deliberately not reproduced

| ref | bug | here |
|---|---|---|
| 7.1 | coherence computed on complex-averaged SLCs, destroying speckle before the estimator sees it | stage G3 — `Σs₁s₂*`, `Σ|s₁|²`, `Σ|s₂|²` accumulated at full res, ratio taken *afterwards* |
| 7.2 | no grid gate | stage G2 |
| 7.4 | `20·log10(abs)` with no epsilon → `-inf` → percentile collapse | epsilon + non-finite masked first |
| 7.5 | `RdBu` for wrapped phase (diverging → false discontinuity at ±π) | cyclic `twilight_shifted` |
| 7.7 | hardcoded `vmin=0, vmax=1` on an unverified calibration assumption | percentile clipping everywhere |
| 7.8 | freq-B range ratio hardcoded `//4` (wrong for our `4005` mode, which is 8×) | derived from `slantRangeSpacing` / shapes |
| 7.9 | no NaN handling (NISAR fill is `NaN+NaNj`) | every statistic over finite samples only |

---

## Outputs

Everything lands under `out_root` (defaults to `case_dir`):

```
stack.json                      pinned geogrid + all granule metadata  ← consumed by every later stage
cfg/gslc_<date>_freq<F>.yaml    generated, schema-validated runconfigs
L2_GSLC/<date>_gslc_freq<F>.h5  the products
aux/dem/dem_<case>.tif          WGS84-ellipsoidal DEM
aux/watermask/watermask_<case>.tif   1=water 0=land 255=outside swath
qa/*.png                        quicklooks
provenance/<stage>.json         per-stage sidecar: inputs, outputs, parameters, versions, timing
logs/track_g_<timestamp>.log    full run log
time_summary.txt                machine-parsable TSV of step durations
scratch/                        ISCE3 working space

pairs/<ref>_<sec>/trackG/
    ifg_<F>_<pol>.igram.tif             complex64  ref * conj(sec)
    ifg_<F>_<pol>.coh.tif               float32    multilook coherence  (display/QA)
    ifg_<F>_<pol>.nlooks.tif            float32    valid samples per look box
    ifg_<F>_<pol>.amp.tif               float32    the PAIR's geometric-mean amplitude
    amp_<F>_<pol>_<date>.tif            float32    per-date amplitude, one per date
    ifg_<F>_<pol>.filt.int.tif          complex64  Goldstein-filtered
    ifg_<F>_<pol>.filt.phsig.coh.tif    float32    phase-sigma coh  → THIS is what snaphu ate
    ifg_<F>_<pol>.filt.unw.tif          float32    unwrapped phase, radians
    ifg_<F>_<pol>.filt.unw.conncomp.tif uint16     connected components (0 = not placed)
    trackG_overlay.html                 the map
    trackG_overlay_layers/*.png         its layers (linked, not embedded)
```

Two naming notes. `.coh.tif` is the **display** coherence and
`.filt.phsig.coh.tif` is the one that **drove the unwrapping** — they are
different estimators of different things and the filenames are the only thing
distinguishing them, so keep the `.filt.` infix. And the amplitude rasters are
**HH**: this product has no VV.

Every stage writes a provenance sidecar recording what it did, with library
versions and the exact argv — so a product can always be traced back to the
parameters that made it.

---

## Slotting in the rest

Adding a stage is a one-line append to `STEPS` in `run_track_g.py` plus a module
exposing a single function:

```python
def run(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    ...
```

```python
STEPS: list[Step] = [
    ...
    Step(8, "unwrap", "G4", "...", unwrap_stage.run, "unwrap"),
    Step(9, "overlay", "G5", "...", overlay_stage.run, "overlay"),
    Step(10, "dolphin", "TS", "phase linking / time series",
         dolphin_stage.run, "dolphin"),   # ← next
]
```

The contract every stage follows, and which makes them composable:

* read parameters from `Config`, never from the CLI directly;
* read upstream facts from `stack.json` via `ingest.load_stack(cfg)` — which
  raises an actionable error if a prerequisite has not run;
* be idempotent: detect completed work and return `Result(skipped=True)` unless
  `force`;
* honour `dry_run` by reporting what *would* happen, including exact commands,
  and degrading hard preconditions to warnings (an upstream step would have
  satisfied them);
* raise `StepFailed` with a multi-line, actionable message on real failures;
* write a provenance sidecar via `util.write_sidecar`.

Still outstanding, and what each already has waiting for it:

* **`dolphin`** — phase linking / time series over an N-date stack. `stack.json`
  already carries every date and the shared grid, which is exactly the manifest a
  ministack driver needs; add `dolphin:` to the config and a step that reads the
  GSLC list from `stack["dates"]`.
* **perpendicular baseline** — belongs in G3, where the geometry cubes exist.
  Stage A deliberately records only the *temporal* baseline rather than a
  mid-orbit position difference, which would look like a B_perp two orders of
  magnitude too large. `stack["baseline"]["perpendicular_baseline_m"]` is
  currently `null` and says why.
* **ramp removal / tropospheric correction** — the unwrapped phase here is
  dominated by a long-wavelength non-tectonic signal (see *The overlay*). Until
  that is removed, this product is an interferogram, not a displacement map, and
  the overlay legend says so.

Because the toggles in `steps:` and the `--only` / `--start-step` selectors are
generic over the registry, new stages get resume, dry-run, logging, timing and
provenance for free.

---

## Notes on this data

Auto-discovered from `case_dir`, sorted by acquisition date (earliest becomes the
reference):

```
track 162  frame 7  Ascending  look Left    12-day repeat
reference  20260613 (cycle 022)
secondary  20260625 (cycle 023)
AOI        lon/lat [-69.6048, 9.5255, -66.8431, 12.2138], centroid (-68.2240, 10.8696)
EPSG       32619 (UTM 19N, auto-derived)
pinned     top_left (434000, 1351000)  bottom_right (737000, 1052000)   snap 1000 m
freq B     posting 40 × 5 m  →  59800 × 7575 px  (3.4 GiB/pol uncompressed)
looks      16 × 2            →   3737 × 3787 px at 80 × 80 m
pols       HH ONLY at L2     (the RSLC is DHDH; there is no VV, and no HV was geocoded)
lambda     0.231768 m        →  1 fringe = 11.6 cm line-of-sight
```

The config selects **frequency B first**: same swath and azimuth sampling as
freq A but 8× fewer range samples (6582 vs 52649), so it exercises the whole
pipeline at roughly ⅛ the cost. Freq A at 5 m posting is a ~29 GB/pol output.

Freq B's posting is deliberately **anisotropic** (40 × 5 m). Its 5 MHz range
bandwidth gives ~34–46 m ground range while azimuth stays at ~4.46 m; for this
near-polar ascending pass range lies close to easting and azimuth close to
northing, so the resolution cell's bounding box is about 40 m in x and 13 m in y.
Posting 40 × 5 m samples the complex signal without aliasing it. `y: 10.0` halves
the output and still clears Nyquist, but with far less headroom.

One quirk worth knowing: the two granules list polarizations in **different
orders** (`['HH','HV']` vs `['HV','HH']`) and their freq-A range widths differ by
one sample (52649 vs 52650). Stage A sorts pol lists so comparisons never depend
on file ordering, and the pinned geogrid makes the freq-A width difference
irrelevant to the output.
