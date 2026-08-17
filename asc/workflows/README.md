# Track G — NISAR RSLC → GSLC, coregistered by construction

A modular, automated Track G pipeline: two NISAR L1 RSLC granules in, two L2 GSLC
products out, geocoded onto a **single pinned grid** so they are pixel-aligned by
construction rather than by luck — then hard-verified that they actually are.

This is scoped to stages **A, B, G1, G2** plus QA. The interferogram, unwrapping
and Dolphin stages are deliberately not here yet; the interfaces they slot into
are described at the bottom.

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

**No water mask is staged.** `sardem --data-source NASA_WATER` is broken: the
SRTMSWBD tiles 404 at `e4ftl01.cr.usgs.gov`, and sardem substitutes zeros while
still exiting 0 — silently yielding an all-land mask that every downstream
masking step would consume as truth. If you need a water mask, source it
elsewhere and assert a non-trivial water fraction over a box known to contain
ocean. The `.wbd` grid also does not co-register with the DEM grid.

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
| 7.1 | coherence computed on complex-averaged SLCs, destroying speckle before the estimator sees it | G3, not yet written — accumulate `Σs₁s₂*`, `Σ|s₁|²`, `Σ|s₂|²` at full res, *then* ratio |
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
stack.json                     pinned geogrid + all granule metadata  ← consumed by every later stage
cfg/gslc_<date>_freq<F>.yaml   generated, schema-validated runconfigs
L2_GSLC/<date>_gslc_freq<F>.h5 the products
aux/dem/dem_<case>.tif         WGS84-ellipsoidal DEM
qa/*.png                       quicklooks
provenance/<stage>.json        per-stage sidecar: inputs, outputs, parameters, versions, timing
logs/track_g_<timestamp>.log   full run log
time_summary.txt               machine-parsable TSV of step durations
scratch/                       ISCE3 working space
```

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
    Step(5, "qa", "QA", "...", qa_stage.run, "qa"),
    Step(6, "ifg", "G3", "interferogram + coherence", ifg_stage.run, "ifg"),   # ← next
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

Planned next stages, and what each already has waiting for it:

* **G3 `ifg`** — conjugate product + coherence for the aligned pair. The grid gate
  has already guaranteed alignment, so it can index both files directly without
  re-checking. Use the corrected coherence estimator (course bug 7.1) and enforce
  `overlap % looks == 0` on block boundaries (bug 7.3). Perpendicular baseline
  belongs here, where the geometry cubes exist — stage A deliberately records only
  the temporal baseline rather than a mid-orbit position difference that would
  look like a B_perp two orders of magnitude too large.
* **`unwrap`** — phase unwrapping. Add `unwrapper` / `unwrap_method` under a new
  config section, following `DemConfig`'s validate-and-warn pattern.
* **`dolphin`** — phase linking / time series over an N-date stack. `stack.json`
  already carries every date and the shared grid, which is exactly the manifest a
  ministack driver needs; add `dolphin:` to the config and a step that reads the
  GSLC list from `stack["dates"]`.

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
