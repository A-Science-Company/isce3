"""
Stage G3 -- interferogram, coherence, and per-date amplitude.

Promotes asc/compare/gslc_igram.py and asc/compare/gslc_amp.py into a single
stage. Both GSLCs were geocoded onto the SAME pinned geogrid and stage G2 has
already proved it, so this stage cross-multiplies them by index without
re-deriving any geometry.

WHY THERE IS NO FLATTENING STEP
-------------------------------
Both GSLCs are already flattened: geocodeSlc multiplies every sample by
exp(+i * 4*pi*r_k/lambda), with r_k the geo2rdr slant range to the DEM surface
at that map cell (cxx/isce3/geocode/geocodeSlc.cpp, flattenPhase). So
ref * conj(sec) is ALREADY topo- and ellipsoid-flattened. `gslc.flatten` is
hard-required true by the config layer precisely so this holds.

Order is ref * conj(sec), matching ISCE3 Crossmul (refSlc * conj(secSlc),
cxx/isce3/signal/Crossmul.cpp:306), so this track shares a sign convention with
every other tool in the house.

THE COHERENCE ESTIMATOR (course bug 7.1)
----------------------------------------
The course notebook averages the complex SLCs first and then forms coherence
from the averaged fields, which destroys the speckle the estimator is supposed
to measure. Here the three sums

    sum(s1 * conj(s2))      sum(|s1|^2)      sum(|s2|^2)

are accumulated at FULL resolution inside each look box, and the ratio

    gamma = |sum(s1 conj s2)| / sqrt(sum|s1|^2 * sum|s2|^2)

is taken only afterwards. That is the standard multilook estimator. Its
magnitude has a positive bias for finite looks -- E[|gamma|] -> sqrt(pi)/(2 sqrt(L))
for fully decorrelated signal -- which is why open water in this scene floors
near 0.19 rather than 0, and why the unwrap stage reads its effective look
count off that floor rather than trusting the nominal box size.

PER-DATE AMPLITUDE, AND WHY IT IS COMPUTED HERE
-----------------------------------------------
The pair's `.amp` band is sqrt(sqrt(P1*P2)/n) -- the GEOMETRIC MEAN of the two
dates, one raster for the pair. It cannot answer "what did this pixel look like
on the 13th versus the 25th", which is exactly what the overlay needs. So each
date's own multilooked amplitude is written too:

    A = sqrt( sum(|s|^2) / n_valid )

the square root of MEAN POWER, not the mean of amplitudes -- averaging |s|
biases low and is not the multilook speckle estimator.

Both come out of the SAME streaming pass as the interferogram. Running the two
loose scripts separately costs three full reads of a 3.4 GiB raster; this costs
one. The per-date amplitudes use each date's OWN validity mask (a sample can be
valid on one date and fill on the other), while the interferogram and coherence
use the JOINT mask -- so the amplitudes are per-date facts and the interferogram
is a pair fact, which is what each is for.

MEMORY
------
Never loads a full GSLC -- that would be 3.4 GiB per date. Streams by row block;
the block working set is roughly

    block_rows * nx * (8 bytes complex x 2 dates + ~16 bytes of float work)

= ~237 MiB at block_rows=1024 on the 59800 x 7575 freq-B grid. MEASURED whole-
process peak including GDAL's write buffers and the coherence read-back:
**1.0 GB RSS, 1 m 48 s** for the full six-raster pass.

`block_rows` should stay a multiple of BOTH the row look factor AND the HDF5
chunk row size (512 here). Otherwise every block straddles a chunk boundary and
gzip re-inflates the same chunk twice. 1024 is 64 x 16 looks and 2 x 512 rows.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .config import Config
from .ingest import load_stack
from .util import Logger, Result, StepFailed, fmt_s, human_bytes, write_sidecar

GSLC_GRID = "/science/LSAR/GSLC/grids/frequency{freq}"

# GeoTIFF creation options used for every product this stage writes. The course
# helper (utils.save_tiff) passes NO creation options at all -- uncompressed,
# untiled, no nodata. Compression is free here and tiling is what makes the
# overlay's windowed warp cheap.
CREATE_OPTS = ["COMPRESS=DEFLATE", "ZLEVEL=1", "TILED=YES", "BIGTIFF=IF_SAFER"]


def _as_c8(a: np.ndarray) -> np.ndarray:
    """GSLC may be complex64 or the NISAR complex32 (2 x float16) compound."""
    if a.dtype == np.complex64 or a.dtype == np.complex128:
        return a.astype(np.complex64)
    if a.dtype.names and set(a.dtype.names) >= {"r", "i"}:
        return (a["r"].astype(np.float32) + 1j * a["i"].astype(np.float32)).astype(np.complex64)
    return a.astype(np.complex64)


def _open_gslc(path: Path, freq: str, pol: str):
    """Open one GSLC and return (file, dataset, x, y, epsg, center_frequency)."""
    import h5py

    h = h5py.File(str(path), "r")
    p = GSLC_GRID.format(freq=freq)
    if p not in h:
        h.close()
        raise StepFailed(f"{path.name} has no {p} -- is this a GSLC for frequency {freq}?")
    if pol not in h[p]:
        avail = [k for k in h[p].keys() if getattr(h[p][k], "ndim", 0) == 2]
        h.close()
        raise StepFailed(
            f"polarization {pol!r} is not present in {path.name}\n"
            f"  frequency {freq} carries 2-D datasets: {avail}\n"
            f"  The GSLC only contains what the G1 runconfig asked geocode to produce,\n"
            f"  which is `polarizations:` in the config -- not everything the RSLC held."
        )
    ds = h[f"{p}/{pol}"]
    x = h[f"{p}/xCoordinates"][:]
    y = h[f"{p}/yCoordinates"][:]
    try:
        epsg = int(h[f"{p}/projection"][()])
    except Exception:
        epsg = int(h[f"{p}/projection"].attrs.get("epsg_code", 0))
    try:
        fc = float(h[f"{p}/centerFrequency"][()])
    except Exception:
        fc = None
    return h, ds, x, y, epsg, fc


def multilook_grid(x: np.ndarray, y: np.ndarray, ny: int, nx: int, ry: int, rx: int):
    """
    Output shape and geotransform of the block-averaged grid.

    Trailing partial blocks are TRUNCATED, never padded -- the same convention as
    the course's multilook_ifg (`nr - nr % az_looks`). The geotransform origin is
    the upper-left CORNER of the first contributing fine pixel: the coordinate
    vectors are pixel CENTRES, so half a fine pixel is subtracted before scaling.
    """
    oy, ox = ny // ry, nx // rx
    px, py = float(x[1] - x[0]), float(y[1] - y[0])
    gt = (float(x[0]) - px / 2.0, px * rx, 0.0,
          float(y[0]) - py / 2.0, 0.0, py * ry)
    return oy, ox, gt


def form_pair(ref_path: Path, sec_path: Path, freq: str, pol: str,
              ry: int, rx: int, prefix: Path, amp_paths: dict[str, Path],
              block_rows: int, log: Logger) -> dict:
    """
    Stream both GSLCs once and write igram / coh / nlooks / amp + per-date amps.

    Returns a dict of grid facts and measured statistics.
    """
    from osgeo import gdal, osr

    gdal.UseExceptions()

    h1, d1, x1, y1, epsg, fc = _open_gslc(ref_path, freq, pol)
    h2, d2, x2, y2, e2, _ = _open_gslc(sec_path, freq, pol)
    try:
        # Hard alignment assertion. Stage G2 has already proved this; re-checking
        # costs microseconds and means this stage is still safe if run standalone.
        # Do NOT "handle" a mismatch by resampling -- a silently reprojected
        # interferogram is a plausible-looking picture of two different places.
        if d1.shape != d2.shape:
            raise StepFailed(
                f"GSLC shapes differ: {d1.shape} vs {d2.shape}. Run the grid gate:\n"
                f"    python run_track_g.py --config <cfg> --only gridgate"
            )
        if epsg != e2:
            raise StepFailed(f"GSLC EPSG differs: {epsg} vs {e2}")
        if not (np.allclose(x1, x2, atol=1e-6) and np.allclose(y1, y2, atol=1e-6)):
            raise StepFailed(
                "GSLC coordinate vectors differ -- the products are NOT pixel-aligned.\n"
                "  Pin top_left / bottom_right / output_posting identically in both\n"
                "  runconfigs and regenerate (--only gslc --force)."
            )

        ny, nx = d1.shape
        oy, ox, gt = multilook_grid(x1, y1, ny, nx, ry, rx)
        if oy < 1 or ox < 1:
            raise StepFailed(
                f"looks {ry} x {rx} are larger than the {ny} x {nx} grid; output would be empty"
            )

        srs = osr.SpatialReference()
        srs.ImportFromEPSG(epsg)
        wkt = srs.ExportToWkt()
        drv = gdal.GetDriverByName("GTiff")

        def mk(path: Path, dt):
            r = drv.Create(str(path), ox, oy, 1, dt, CREATE_OPTS)
            r.SetGeoTransform(gt)
            r.SetProjection(wkt)
            r.GetRasterBand(1).SetNoDataValue(float("nan"))
            return r

        r_ig = mk(Path(f"{prefix}.igram.tif"), gdal.GDT_CFloat32)
        r_co = mk(Path(f"{prefix}.coh.tif"), gdal.GDT_Float32)
        r_nl = mk(Path(f"{prefix}.nlooks.tif"), gdal.GDT_Float32)
        r_am = mk(Path(f"{prefix}.amp.tif"), gdal.GDT_Float32)
        r_a1 = mk(amp_paths["ref"], gdal.GDT_Float32) if amp_paths else None
        r_a2 = mk(amp_paths["sec"], gdal.GDT_Float32) if amp_paths else None

        blk = max(ry, (block_rows // ry) * ry)
        est = blk * nx * 8 * 2 + blk * nx * 16
        log.info(f"  streaming {ny} x {nx} in {blk}-row blocks "
                 f"(~{human_bytes(est)} peak per block)")

        t0 = time.time()
        n_blocks = (oy * ry + blk - 1) // blk
        coh_sum = 0.0
        coh_n = 0
        for bi, r0 in enumerate(range(0, oy * ry, blk)):
            r1 = min(r0 + blk, oy * ry)
            a = _as_c8(d1[r0:r1, :ox * rx])
            b = _as_c8(d2[r0:r1, :ox * rx])

            # Per-date validity. NISAR fill is NaN+NaNj; geocode writes exact 0
            # outside the acquisition extent. Both must be excluded from the sum
            # AND from the count, so a look box straddling the swath edge is
            # normalised by the samples it actually had.
            g1 = np.isfinite(a) & (a != 0)
            g2 = np.isfinite(b) & (b != 0)
            joint = g1 & g2

            m, n = (r1 - r0) // ry, ox

            def blk2(z):
                return z.reshape(m, ry, n, rx).sum(axis=(1, 3))

            # ---- per-date amplitude: each date's OWN mask ----------------
            if r_a1 is not None:
                for g, arr, band in ((g1, a, r_a1), (g2, b, r_a2)):
                    pw = np.where(g, arr.real.astype(np.float32) ** 2
                                  + arr.imag.astype(np.float32) ** 2, 0.0)
                    s = blk2(pw)
                    # The count is summed as INTEGER, so s/c is evaluated in
                    # float64 and only the result is rounded to float32. Counting
                    # in float32 instead costs a last-bit error on ~0.01% of
                    # pixels (max relative 1.19e-07, exactly one float32 ULP).
                    c = blk2(g)
                    with np.errstate(invalid="ignore", divide="ignore"):
                        amp1 = np.where(c > 0, np.sqrt(s / np.maximum(c, 1)), np.nan)
                    band.GetRasterBand(1).WriteArray(
                        amp1.astype(np.float32), 0, r0 // ry)
                    del pw, s, c, amp1

            # ---- pair products: the JOINT mask --------------------------
            a = np.where(joint, a, 0)
            b = np.where(joint, b, 0)
            num = blk2(a * np.conj(b))
            p1 = blk2(a.real.astype(np.float64) ** 2 + a.imag.astype(np.float64) ** 2)
            p2 = blk2(b.real.astype(np.float64) ** 2 + b.imag.astype(np.float64) ** 2)
            cnt = blk2(joint.astype(np.float32))
            den = np.sqrt(p1 * p2)
            with np.errstate(invalid="ignore", divide="ignore"):
                coh = np.where((den > 0) & (cnt > 0), np.abs(num) / den, np.nan)
                ig = np.where(cnt > 0, num / np.maximum(cnt, 1), np.nan)
                amp = np.where(cnt > 0, np.sqrt(np.sqrt(p1 * p2) / np.maximum(cnt, 1)), np.nan)

            o = r0 // ry
            r_ig.GetRasterBand(1).WriteArray(ig.astype(np.complex64), 0, o)
            r_co.GetRasterBand(1).WriteArray(coh.astype(np.float32), 0, o)
            r_nl.GetRasterBand(1).WriteArray(cnt.astype(np.float32), 0, o)
            r_am.GetRasterBand(1).WriteArray(amp.astype(np.float32), 0, o)

            fin = np.isfinite(coh)
            coh_sum += float(coh[fin].sum())
            coh_n += int(fin.sum())

            if bi % 10 == 0 or bi == n_blocks - 1:
                log.info(f"    block {bi + 1}/{n_blocks}  rows {r0}-{r1}  "
                         f"({fmt_s(time.time() - t0)})")

        for r in (r_ig, r_co, r_nl, r_am, r_a1, r_a2):
            if r is not None:
                r.FlushCache()
        r_ig = r_co = r_nl = r_am = r_a1 = r_a2 = None
        log.info(f"  formed in {fmt_s(time.time() - t0)}")
    finally:
        h1.close()
        h2.close()

    return {
        "width": ox, "length": oy, "geotransform": list(gt), "epsg": epsg,
        "center_frequency_hz": fc,
        "wavelength_m": (299792458.0 / fc) if fc else None,
        "posting_m": [abs(gt[1]), abs(gt[5])],
        "mean_coherence": (coh_sum / coh_n) if coh_n else None,
        "valid_fraction": coh_n / float(oy * ox),
    }


def coherence_stats(coh_path: Path) -> dict:
    """
    Median coherence and the >0.3 fraction, read back from the written raster.

    This reads the whole MULTILOOKED raster (56 MB at 80 m), never a GSLC. The
    "never load a full raster" rule is about the 3.4 GiB L2 products.
    """
    from osgeo import gdal

    gdal.UseExceptions()
    ds = gdal.Open(str(coh_path))
    a = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    fin = np.isfinite(a)
    if not fin.any():
        return {"median": None, "frac_gt_0.3": None, "valid_fraction": 0.0}
    v = a[fin]
    return {
        "median": round(float(np.median(v)), 4),
        "frac_gt_0.3": round(float((v > 0.3).mean()), 4),
        "valid_fraction": round(float(fin.mean()), 4),
        # For fully decorrelated signal E[|gamma|] = sqrt(pi)/(2 sqrt(L)); water
        # sitting on this floor is what the unwrap stage's nlooks is read from.
        "decorrelated_floor_L32": round(float(np.sqrt(np.pi) / (2 * np.sqrt(32.0))), 4),
    }


def pair_list(cfg: Config, stack: dict) -> list[tuple[str, str]]:
    """Explicit `igram.pairs`, else every consecutive date pair."""
    if cfg.igram.pairs:
        out = []
        known = set(stack["dates"])
        for p in cfg.igram.pairs:
            if len(p) != 2:
                raise StepFailed(f"igram.pairs entry {p} must be [reference, secondary]")
            ref, sec = str(p[0]), str(p[1])
            for d in (ref, sec):
                if d not in known:
                    raise StepFailed(
                        f"igram.pairs references date {d}, which is not in the stack "
                        f"({sorted(known)}). Re-run ingest, or fix the config."
                    )
            out.append((ref, sec))
        return out
    dates = list(stack["dates"])
    return [(dates[i], dates[i + 1]) for i in range(len(dates) - 1)]


def pair_paths(cfg: Config, ref: str, sec: str) -> dict:
    """Every path this stage owns for one pair. Also used by unwrap and overlay."""
    ic = cfg.igram
    freq, pol = cfg.igram_freq, cfg.igram_pol
    d = cfg.root / ic.pair_dir_template.format(ref=ref, sec=sec)
    prefix = d / ic.prefix_template.format(freq=freq, pol=pol)
    return {
        "dir": d,
        "prefix": prefix,
        "igram": Path(f"{prefix}.igram.tif"),
        "coh": Path(f"{prefix}.coh.tif"),
        "nlooks": Path(f"{prefix}.nlooks.tif"),
        "amp": Path(f"{prefix}.amp.tif"),
        "amp_ref": d / f"amp_{freq}_{pol}_{ref}.tif",
        "amp_sec": d / f"amp_{freq}_{pol}_{sec}.tif",
    }


def expected_outputs(cfg: Config, ref: str, sec: str) -> list[Path]:
    p = pair_paths(cfg, ref, sec)
    out = [p["igram"], p["coh"], p["nlooks"], p["amp"]]
    if cfg.igram.per_date_amplitude:
        out += [p["amp_ref"], p["amp_sec"]]
    return out


def run(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    started = time.time()
    res = Result(stage="igram")
    stack = load_stack(cfg)
    ic = cfg.igram
    freq, pol = cfg.igram_freq, cfg.igram_pol
    ry, rx = int(ic.looks_y), int(ic.looks_x)

    pairs = pair_list(cfg, stack)
    if not pairs:
        raise StepFailed(
            f"no interferometric pair can be formed from {len(stack['dates'])} date(s). "
            f"At least two GSLCs are needed."
        )

    log.info(f"frequency {freq}  polarization {pol}  looks {ry} x {rx} "
             f"(rows x cols)  -> {len(pairs)} pair(s)")

    # ---- preconditions -------------------------------------------------
    products = {d: cfg.gslc_output(d, cfg.freq_tag) for d in stack["dates"]}
    missing = [str(p) for d, p in products.items() if not p.exists()]
    if missing and not dry_run:
        raise StepFailed(
            "cannot form an interferogram; GSLC product(s) missing:\n"
            + "\n".join(f"    {m}" for m in missing)
            + f"\n  Run stage G1 first:\n"
              f"    python run_track_g.py --config {cfg.config_path} --only gslc"
        )

    if dry_run:
        for ref, sec in pairs:
            log.info(f"  would form {ref} x conj({sec}) at {ry} x {rx} looks")
            for f in expected_outputs(cfg, ref, sec):
                log.info(f"      -> {f}")
            log.info(f"      reading {products.get(ref, '<missing>')}")
            log.info(f"      reading {products.get(sec, '<missing>')}")
        if missing:
            log.warn(f"{len(missing)} GSLC(s) absent; stage G1 would create them first")
        res.skipped = True
        return res

    # ---- idempotency ---------------------------------------------------
    todo = []
    for ref, sec in pairs:
        want = expected_outputs(cfg, ref, sec)
        have = [f for f in want if f.exists()]
        if len(have) == len(want) and not force:
            log.info(f"  {ref}_{sec}: all {len(want)} product(s) present -- skipping "
                     f"(--force to rebuild)")
        else:
            if have and not force:
                log.info(f"  {ref}_{sec}: {len(want) - len(have)} of {len(want)} product(s) "
                         f"missing -- rebuilding the pair")
            todo.append((ref, sec))
    if not todo:
        res.skipped = True
        res.outputs = [str(f) for r, s in pairs for f in expected_outputs(cfg, r, s)]
        res.notes = [f"{len(pairs)} pair(s) already formed"]
        return res

    # ---- form ----------------------------------------------------------
    report: dict[str, dict] = {}
    outputs: list[str] = []
    for ref, sec in todo:
        p = pair_paths(cfg, ref, sec)
        p["dir"].mkdir(parents=True, exist_ok=True)
        log.info(f"  pair {ref} x conj({sec})  ->  {p['dir']}")
        info = form_pair(
            products[ref], products[sec], freq, pol, ry, rx, p["prefix"],
            {"ref": p["amp_ref"], "sec": p["amp_sec"]} if ic.per_date_amplitude else {},
            ic.block_rows, log,
        )
        info["coherence"] = coherence_stats(p["coh"])
        log.info(f"    grid {info['length']} x {info['width']}  "
                 f"posting {info['posting_m'][0]:g} x {info['posting_m'][1]:g} m  "
                 f"EPSG:{info['epsg']}  origin "
                 f"({info['geotransform'][0]:.1f}, {info['geotransform'][3]:.1f})")
        c = info["coherence"]
        log.info(f"    coherence: median {c['median']}  >0.3 {c['frac_gt_0.3']}  "
                 f"valid {c['valid_fraction']}  (decorrelated floor at L=32 is "
                 f"{c['decorrelated_floor_L32']})")
        report[f"{ref}_{sec}"] = info
        outputs += [str(f) for f in expected_outputs(cfg, ref, sec)]

    write_sidecar(
        cfg.prov_dir / "igram.json", "igram",
        inputs={"gslc": {d: str(p) for d, p in products.items()},
                "stack_json": str(cfg.stack_json)},
        outputs={"pairs": report},
        parameters={"frequency": freq, "polarization": pol,
                    "looks_y": ry, "looks_x": rx,
                    "block_rows": ic.block_rows,
                    "per_date_amplitude": ic.per_date_amplitude,
                    "estimator": "sum(s1 conj s2) / sqrt(sum|s1|^2 sum|s2|^2), "
                                 "accumulated at full resolution",
                    "amplitude_estimator": "sqrt(mean power), per-date validity mask"},
        started=started,
    )

    res.outputs = outputs
    res.metrics = {
        "pairs": len(todo),
        "looks": f"{ry}x{rx}",
        "grid": f"{report[list(report)[0]]['length']}x{report[list(report)[0]]['width']}",
        "coherence_median": report[list(report)[0]]["coherence"]["median"],
    }
    res.notes = [f"polarization {pol} (this product is HH-only; there is no VV)"]
    return res
