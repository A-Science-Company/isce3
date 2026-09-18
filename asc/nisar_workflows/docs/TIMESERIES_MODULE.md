# Time-series module (`nisar_timeseries.py`)

Takes a coregistered RSLC stack from `nisar_coreg.py` and produces LOS displacement time series and velocity over an AOI.
Unwrapping uses snaphu with tiles and overlap. Ionosphere, troposphere and solid-earth tides come from the NISAR GUNWs.
The inversion is MintPy's. Job semantics are the same as the coreg module: manifests per unit, verified units skipped,
nothing overwritten without `--force`, exit codes 0/1/2/3.

## Trigger (after the coregistration of the selected dates has finished)

```bash
cd /home/sharath/isce3/asc/nisar_workflows
C=ts_configs/nepal_nisar_ascending_pre_event.yaml
python nisar_timeseries.py -c $C show            # dates, pairs, GUNW coverage, whether the coreg is ready, parameters
python nisar_timeseries.py -c $C run --dry-run
python nisar_timeseries.py -c $C run --detach    # all stages, in tmux
python nisar_timeseries.py -c $C status
```

The command works from any conda environment: each stage re-launches itself in the environment named in
`ts_configs/defaults.yaml` (`isce3_env` for geometry, `insar_ts` for the rest).

## Case config

```yaml
coreg_config: coreg_configs/nepal_nisar_ascending_rslc.yaml
name: pre_event
aoi_kml: /home/sharath/nisar_downloader/glof_bigger_aoi.kml
start_date: 20260620
end_date: 20260819
reference_lalo: null        # [lat, lon] of a stable point; null = MintPy picks the highest-coherence pixel
```

Output: `<workdir>/timeseries/<coreg stack>/<name>/`. Parameters are in `ts_configs/defaults.yaml` (commented). The values used are
locked in `params.json`; a changed value needs a new `name` or a `tag`.

## Stages

| stage | env | what | output |
|---|---|---|---|
| geometry | isce3 | Radar window = AOI bounding box + 2 km. Multilooked lat/lon/height, incidence and azimuth angles, slant range. Perpendicular baselines. Per-date flattening range offsets recomputed with isce3 `Geo2Rdr`, exactly as ISCE3 insar's geo2rdr step (the coreg module prunes its copy). | `geometry/geometryRadar.h5`, `meta.json`, `flatten/<date>_range_offset.f32` |
| ifg | ts | Per pair: s1·conj(s2)·exp(−i·4π/λ·Δr·(off2−off1)), summed over 9×8 looks, plus coherence \|Σ s1 s2*\| / √(Σ\|s1\|² Σ\|s2\|²). Pairs that include the stack reference are checked against ISCE3's RIFG. | `pairs/<d1>_<d2>/ifg.int`, `coh.cor` |
| unwrap | ts | snaphu, 3×3 tiles, 150-pixel overlap, nlooks = looks × 0.619 (ISCE3's effective-looks formula). Then a census of whole-cycle closure errors over every triplet. | `unw.unw`, `conncomp.cc`, `qa/unwrap_closure.json` |
| corrections | ts | For each GUNW whose two dates are in the series: ionosphere (2-D, 80 m) and wet + hydrostatic troposphere and solid-earth tides (3-D cubes sampled at each pixel's height). Pair screens are converted to per-date screens by least squares (exact for a chain). | `corrections/per_date_phase.h5`, `qa/corrections.json` |
| mintpy | ts | ifgramStack.h5 → reference point → [unwrap-error correction] → inversion → subtract SET, ionosphere, troposphere → [DEM error] → velocity (corrected and uncorrected) → temporal-coherence mask → geocode → GeoTIFFs. Skipped with a message for fewer than 2 pairs. | `mintpy/`, `export/*.tif`, `qa/mintpy.json` |

Conventions:
- Interferograms are s1·conj(s2), as in ISCE3/ISCE2. That is the convention MintPy's isce loader assumes and the one the GUNW screens use, so the screens are subtracted.
- A GUNW troposphere cube is −(d_ref − d_sec)·4π/λ (ISCE3 `troposphere.py`).
- MintPy displacement is positive toward the satellite. For left-looking ascending NISAR the line of sight points up and to the east.
- The ionosphere level of each GUNW pair is arbitrary, but it is a constant and cancels when the series is referenced to a point.

## Network (pre-event case)

Dates 0620, 0702, 0714, 0726, 0819; `max_connections: 3` gives 9 pairs, 12–48 days:
0620_0702, 0620_0714, 0620_0726, 0702_0714, 0702_0726, 0702_0819, 0714_0726, 0714_0819, 0726_0819. The GUNW chain
0620→0702→0714→0726→0819 (all PR) connects every date.

## Validation (2026-09-15, crop v2 pair 20260714 × 20260726, same crop window)

| check | result |
|---|---|
| recomputed flattening range offsets vs ISCE3 `geo2rdr/range.off` | max 1.2e-7 px (float32 precision) |
| orbit-to-pixel range vs radar-grid range | median 0.04 mm (zero-Doppler geometry consistent) |
| effective looks at 9×8 | 44.567 (validated run: 44.57) |
| our 9×8 interferogram vs ISCE3 RIFG 1×1 multilooked (gate) | median \|Δφ\| 0.002 rad on 1.09 M coherent pixels |
| our 9×8 interferogram / coherence vs ISCE3's own 9×8 crossmul | phase 0.0024 rad; coherence median 0.0018, p95 0.008 |
| tiled snaphu (3×3, overlap 150) vs ISCE3 RUNW 9×8 (snaphu 4×4, overlap 256) | same cycle on all but 0.001% of 1.63 M pixels; residual std 0.02 rad |
| GUNW B⊥ at scene centre vs ours | +1.29 m vs +1.49 m |
| GUNW ionosphere sampled vs our crop-v2 screen | same sign and shape; after the constant offset, std of difference 0.19 rad (≈ 3.7 mm) vs screen std 1.15 rad |
| MintPy stage, end to end to the GeoTIFFs | exercised on the test stack with a dummy third date (links to 0726 data), since MintPy's inversion fails on a single interferogram. Velocity values from that test are meaningless. |
| sign of the GUNW corrections | on the real 12-day pair, subtracting SET + ionosphere + troposphere lowers the displacement spread from 22.3 to 15.2 mm |
| MintPy with 4 dask workers vs serial | identical to 1.3e-7 m on pixels unwrapped in every interferogram; inversion 11 min → 2.5 min |
| export | GeoTIFF, EPSG:4326, ≈ 37 m × 39 m pixels |

**Reference point.** MintPy's automatic choice (`maxCoherence`) picks a random pixel above the coherence threshold, so reruns
would differ. When `reference_lalo` is null, the module instead takes the highest 9×9-averaged coherence among pixels inside a
snaphu component in every interferogram, and passes it to MintPy as row/column. For a physically meaningful result, give
`reference_lalo` a point known to be stable (bedrock away from the glacier and the flood path).

Timings on the test pair (shared CPU while the coreg ran):
- Geo2Rdr: 4.5 min per date.
- Interferogram: 1.6 min per pair.
- snaphu: 34 s per pair.
- GUNW sampling: 13 s per pair.
- MintPy: about 4 min.

Estimated for the pre-event case: ≈ 20 min of geometry, ≈ 15 min of interferograms, ≈ 6 min of unwrapping and ≈ 5–10 min of MintPy.

## Post-event analysis

Kept separate, as decided. `ts_configs/nepal_nisar_ascending_post_event.yaml` (dates 0819, 0831, 0912) runs three pairs through
the same stages:
- 0819_0831 and 0819_0912 span the event; 0831_0912 is post-event only.
- Each gets an interferogram, coherence and unwrapped phase, plus the GUNW corrections (0819_0831 PR, 0831_0912 UR).
- MintPy also runs. Its per-date displacement relative to 0819 is the useful product; a velocity across a step event has no physical meaning.

A dedicated post-event module is not written yet. Candidates: geocoded coherence-change maps (pre-event pair coherence vs
event-spanning pair), per-pair corrected LOS displacement GeoTIFFs, and amplitude-change maps.
