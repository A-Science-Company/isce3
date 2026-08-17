"""
QA -- decimated-read quicklooks.

Hard rule: this module NEVER loads a full raster. The granules are ~24.5 GiB
each and only ~4 GB of RAM is free, so every read is either a block-wise
multilook (for full-frame overviews) or a small centre window.

Two notes on method, both departures from the isce+ course notebooks:

  * The course's "decimation" is really SUBSETTING -- a 4000x4000 centre window
    read at stride (1,1). Its only strided read is (10,10) on the small derived
    products. For a full-frame overview of a multi-GB GSLC a naive `[::10,::10]`
    is actively bad: the rasters are gzip+shuffle chunked at 512x512, so a
    strided read still DECOMPRESSES every chunk it touches, costing nearly a
    full-file read while discarding 99% of the result. Block-wise multilook
    reads each chunk once and uses all of it, and it averages POWER, which is
    the correct incoherent estimator for speckle.

  * Look factors are derived from GROUND spacing, not pixel counts, so the
    overview has near-square ground pixels. This matters for freq B, whose grid
    is 40 x 5 m: equal pixel decimation would render an 8:1 stretched image.

Course bugs deliberately not reproduced (see WORKFLOWS extraction section 7):
  7.4  `20*log10(abs(slc))` with no epsilon -> -inf -> percentile collapses.
       An epsilon is used, and non-finite samples are masked first.
  7.5  `RdBu` for wrapped phase -- diverging, so it invents a discontinuity at
       +-pi. A cyclic colormap (`twilight_shifted`) is used instead.
  7.7  Hardcoded `vmin=0, vmax=1` for amplitude, an unverified assumption about
       NISAR calibration. Percentile clipping is used everywhere.
  7.9  No NaN handling. NISAR fill is NaN+NaNj, which propagates through mean
       and percentile; every statistic here is computed over finite samples only.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import h5py
import numpy as np

import matplotlib

matplotlib.use("Agg")  # headless: no display on this machine
import matplotlib.pyplot as plt  # noqa: E402

from .config import Config
from .ingest import load_stack
from .util import Logger, Result, fmt_s, human_bytes, write_sidecar

GSLC_GRID = "/science/LSAR/GSLC/grids"
RSLC_SWATHS = "/science/LSAR/RSLC/swaths"

EPS = 1e-10  # keeps 20*log10 finite on exact zeros (course bug 7.4)
CENTRE_WINDOW = 1024  # half-size of the centre window, in pixels


# --------------------------------------------------------------------------
# complex reading
# --------------------------------------------------------------------------
def _as_complex64(arr: np.ndarray) -> np.ndarray:
    """
    Normalise a NISAR complex array to complex64.

    NISAR can store rasters as `complex32` (half-precision pairs), which h5py
    surfaces as a compound dtype with fields ('r', 'i') rather than a native
    complex type. Handle both so QA works regardless of the product's
    `output.data_type`.
    """
    if arr.dtype.names and set(arr.dtype.names) >= {"r", "i"}:
        return (arr["r"].astype(np.float32) + 1j * arr["i"].astype(np.float32)).astype(
            np.complex64
        )
    return arr.astype(np.complex64, copy=False)


def choose_looks(
    nrow: int,
    ncol: int,
    row_spacing_m: float,
    col_spacing_m: float,
    max_pixels: int,
) -> tuple[int, int]:
    """
    Look factors giving an overview with near-square GROUND pixels whose long
    edge is about `max_pixels`.
    """
    extent_row = abs(row_spacing_m) * nrow
    extent_col = abs(col_spacing_m) * ncol
    target_ground = max(extent_row, extent_col) / float(max_pixels)
    looks_r = max(1, int(round(target_ground / abs(row_spacing_m))))
    looks_c = max(1, int(round(target_ground / abs(col_spacing_m))))
    # never produce a degenerate overview
    looks_r = min(looks_r, max(1, nrow // 8))
    looks_c = min(looks_c, max(1, ncol // 8))
    return looks_r, looks_c


def block_multilook_power(
    dset: h5py.Dataset,
    looks_r: int,
    looks_c: int,
    max_read_bytes: int,
    log: Logger | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Full-frame amplitude overview by block-wise POWER averaging.

    Returns (amplitude, valid_fraction), both float32 of shape
    (nrow // looks_r, ncol // looks_c). Non-finite input samples are excluded
    from each look cell rather than poisoning it; cells with no finite samples
    come back as NaN.

    Peak memory is bounded by `max_read_bytes`: bands are sized so one read of
    (band_rows * looks_r) x ncol never exceeds it.
    """
    nrow, ncol = dset.shape
    orow, ocol = nrow // looks_r, ncol // looks_c
    if orow < 1 or ocol < 1:
        raise ValueError(
            f"look factors ({looks_r}, {looks_c}) exceed the raster {nrow} x {ncol}"
        )

    itemsize = max(dset.dtype.itemsize, 8)
    row_bytes = ncol * itemsize
    band_rows = max(1, int(max_read_bytes // max(row_bytes * looks_r, 1)))
    band_rows = min(band_rows, orow)

    power = np.zeros((orow, ocol), dtype=np.float64)
    count = np.zeros((orow, ocol), dtype=np.int64)
    trim_c = ocol * looks_c

    n_bands = (orow + band_rows - 1) // band_rows
    if log:
        log.info(
            f"      overview: looks ({looks_r}, {looks_c}) -> {orow} x {ocol}; "
            f"{n_bands} band(s) of <= {human_bytes(band_rows * looks_r * row_bytes)}"
        )

    for bi, r0 in enumerate(range(0, orow, band_rows)):
        r1 = min(orow, r0 + band_rows)
        nb = r1 - r0
        block = _as_complex64(dset[r0 * looks_r : r1 * looks_r, :trim_c])
        pw = np.abs(block) ** 2
        del block
        finite = np.isfinite(pw)
        np.copyto(pw, 0.0, where=~finite)
        power[r0:r1] = (
            pw.reshape(nb, looks_r, ocol, looks_c).sum(axis=(1, 3), dtype=np.float64)
        )
        count[r0:r1] = finite.reshape(nb, looks_r, ocol, looks_c).sum(axis=(1, 3))
        del pw, finite

    with np.errstate(invalid="ignore", divide="ignore"):
        mean_power = np.where(count > 0, power / np.maximum(count, 1), np.nan)
    amplitude = np.sqrt(mean_power).astype(np.float32)
    valid_fraction = (count / float(looks_r * looks_c)).astype(np.float32)
    return amplitude, valid_fraction


def centre_window(dset: h5py.Dataset, half: int = CENTRE_WINDOW) -> tuple[np.ndarray, tuple]:
    """
    Small centre window at stride 1 -- the course's cell-13 idiom, clamped.

    Used to inspect PHASE, which a multilooked overview necessarily destroys.
    A 2048 x 2048 complex64 window is 32 MiB.
    """
    nrow, ncol = dset.shape
    r_c, c_c = nrow // 2, ncol // 2
    r0, r1 = max(0, r_c - half), min(nrow, r_c + half)
    c0, c1 = max(0, c_c - half), min(ncol, c_c + half)
    return _as_complex64(dset[r0:r1, c0:c1]), (r0, r1, c0, c1)


# --------------------------------------------------------------------------
# statistics and scaling
# --------------------------------------------------------------------------
def amplitude_stats(amp: np.ndarray) -> dict:
    """Statistics over FINITE samples only (course bug 7.9)."""
    finite = np.isfinite(amp) & (amp > 0)
    if not finite.any():
        return {"valid_fraction": 0.0}
    v = amp[finite].astype(np.float64)
    db = 20.0 * np.log10(v)
    return {
        "valid_fraction": float(np.isfinite(amp).mean()),
        "nonzero_fraction": float(finite.mean()),
        "amp_min": float(v.min()),
        "amp_max": float(v.max()),
        "amp_mean": float(v.mean()),
        "amp_median": float(np.median(v)),
        "amp_std": float(v.std()),
        "db_p5": float(np.percentile(db, 5)),
        "db_p50": float(np.percentile(db, 50)),
        "db_p95": float(np.percentile(db, 95)),
    }


def to_db(amp: np.ndarray) -> np.ndarray:
    """Amplitude -> dB with an epsilon, NaN preserved (course bug 7.4)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return 20.0 * np.log10(np.abs(amp) + EPS)


def db_limits(db: np.ndarray) -> tuple[float, float]:
    """Percentile clipping over finite samples (course bug 7.7)."""
    finite = db[np.isfinite(db)]
    if finite.size == 0:
        return (-1.0, 1.0)
    lo, hi = np.percentile(finite, [5, 95])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return (float(finite.min()), float(max(finite.max(), finite.min() + 1)))
    return float(lo), float(hi)


# --------------------------------------------------------------------------
# plotting
# --------------------------------------------------------------------------
def _panel_db(ax, db, extent, title, xlabel, ylabel, aspect):
    vmin, vmax = db_limits(db)
    im = ax.imshow(
        db, cmap="gray", vmin=vmin, vmax=vmax, extent=extent, aspect=aspect,
        interpolation="nearest", origin="upper",
    )
    ax.set_title(title, fontsize=10)
    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_ylabel(ylabel, fontsize=8)
    ax.tick_params(labelsize=7)
    cb = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
    cb.set_label("amplitude (dB)", fontsize=8)
    cb.ax.tick_params(labelsize=7)


def _panel_phase(ax, phase, extent, title, xlabel, ylabel, aspect):
    # cyclic colormap: RdBu (the course's choice) is diverging and fabricates a
    # discontinuity at +-pi (course bug 7.5)
    im = ax.imshow(
        phase, cmap="twilight_shifted", vmin=-np.pi, vmax=np.pi, extent=extent,
        aspect=aspect, interpolation="nearest", origin="upper",
    )
    ax.set_title(title, fontsize=10)
    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_ylabel(ylabel, fontsize=8)
    ax.tick_params(labelsize=7)
    cb = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
    cb.set_label("phase (rad)", fontsize=8)
    cb.ax.tick_params(labelsize=7)


def plot_product(
    out_png: Path,
    suptitle: str,
    overview_db: np.ndarray,
    overview_extent: tuple | None,
    win_db: np.ndarray,
    win_phase: np.ndarray,
    map_coords: bool,
    dpi: int,
) -> Path:
    """
    Three panels: full-frame amplitude overview, centre-window amplitude,
    centre-window phase.

    Phase is shown only for the centre window because multilooking necessarily
    destroys it -- and a single SLC's phase is speckle-random anyway, so the
    panel's job is to confirm the data is genuinely complex and non-degenerate.
    """
    xlabel, ylabel = ("easting (m)", "northing (m)") if map_coords else ("range (px)", "azimuth (px)")
    # 'equal' is correct for a map grid; the course used 'auto' nearly everywhere
    aspect = "equal" if map_coords else "auto"

    fig, axes = plt.subplots(1, 3, figsize=(16.5, 6.0))
    _panel_db(
        axes[0], overview_db, overview_extent,
        "amplitude, full frame (block multilook)", xlabel, ylabel, aspect,
    )
    _panel_db(
        axes[1], win_db, None,
        f"amplitude, centre {win_db.shape[0]}x{win_db.shape[1]} (stride 1)",
        "range (px)" if not map_coords else "x (px)",
        "azimuth (px)" if not map_coords else "y (px)",
        "equal",
    )
    _panel_phase(
        axes[2], win_phase, None,
        "phase, centre window (speckle-random by design)",
        "range (px)" if not map_coords else "x (px)",
        "azimuth (px)" if not map_coords else "y (px)",
        "equal",
    )
    fig.suptitle(suptitle, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return out_png


def plot_date_comparison(
    out_png: Path, suptitle: str, panels: list[tuple[str, np.ndarray, tuple | None]], dpi: int
) -> Path:
    """Side-by-side amplitude overviews -- a fast eyeball check that two dates
    cover the same ground on the same grid."""
    n = len(panels)
    fig, axes = plt.subplots(1, n, figsize=(7.5 * n, 7.0))
    if n == 1:
        axes = [axes]
    for ax, (title, db, extent) in zip(axes, panels):
        _panel_db(ax, db, extent, title, "easting (m)", "northing (m)", "equal")
    fig.suptitle(suptitle, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return out_png


# --------------------------------------------------------------------------
# per-product drivers
# --------------------------------------------------------------------------
def qa_gslc(cfg: Config, date: str, path: Path, freq: str, pol: str, log: Logger) -> dict:
    """Quicklook one GSLC frequency/polarization in map coordinates."""
    grid = f"{GSLC_GRID}/frequency{freq}"
    with h5py.File(path, "r") as f:
        g = f[grid]
        if pol not in g:
            raise KeyError(f"{path.name} has no {grid}/{pol}")
        dset = g[pol]
        x = g["xCoordinates"][:]
        y = g["yCoordinates"][:]
        dx = float(np.mean(np.diff(x))) if x.size > 1 else 1.0
        dy = float(np.mean(np.diff(y))) if y.size > 1 else -1.0

        looks_r, looks_c = choose_looks(
            dset.shape[0], dset.shape[1], dy, dx, cfg.qa.max_pixels
        )
        t0 = time.time()
        amp, valid = block_multilook_power(
            dset, looks_r, looks_c, cfg.qa.max_read_bytes, log
        )
        log.info(f"      overview built in {fmt_s(time.time() - t0)}")
        win, box = centre_window(dset)

        # map extent follows the course's convention, from the decimated coords
        xs = x[: amp.shape[1] * looks_c : looks_c]
        ys = y[: amp.shape[0] * looks_r : looks_r]
        extent = (float(xs[0]), float(xs[-1]), float(ys[-1]), float(ys[0]))

    stats = amplitude_stats(amp)
    stats["overview_shape"] = list(amp.shape)
    stats["looks"] = [looks_r, looks_c]
    stats["centre_window_box"] = list(box)
    stats["mean_valid_fraction"] = float(np.nanmean(valid))

    out_png = cfg.qa_dir / f"gslc_{date}_freq{freq}_{pol}.png"
    plot_product(
        out_png,
        f"GSLC {cfg.case_name}  {date}  frequency {freq}  {pol}\n"
        f"{dset.shape[0]} x {dset.shape[1]} px @ {abs(dx):g} x {abs(dy):g} m  "
        f"| overview looks {looks_r} x {looks_c}  "
        f"| valid {stats['mean_valid_fraction'] * 100:.1f}%",
        to_db(amp),
        extent,
        to_db(np.abs(win)),
        np.angle(win),
        map_coords=True,
        dpi=cfg.qa.dpi,
    )
    log.info(f"      wrote {out_png.name}")
    stats["png"] = str(out_png)
    return {"stats": stats, "db": to_db(amp), "extent": extent}


def qa_rslc(cfg: Config, granule: dict, freq: str, pol: str, log: Logger) -> dict:
    """Quicklook one input RSLC frequency/polarization in radar coordinates."""
    path = Path(granule["path"])
    entry = granule["frequencies"][freq]
    az = float(entry.get("sceneCenterAlongTrackSpacing") or 1.0)
    gr = float(entry.get("sceneCenterGroundRangeSpacing") or entry.get("slantRangeSpacing") or 1.0)

    with h5py.File(path, "r") as f:
        dset = f[f"{RSLC_SWATHS}/frequency{freq}/{pol}"]
        looks_r, looks_c = choose_looks(dset.shape[0], dset.shape[1], az, gr, cfg.qa.max_pixels)
        t0 = time.time()
        amp, valid = block_multilook_power(dset, looks_r, looks_c, cfg.qa.max_read_bytes, log)
        log.info(f"      overview built in {fmt_s(time.time() - t0)}")
        win, box = centre_window(dset)
        shape = list(dset.shape)

    stats = amplitude_stats(amp)
    stats["overview_shape"] = list(amp.shape)
    stats["looks"] = [looks_r, looks_c]
    stats["centre_window_box"] = list(box)
    stats["mean_valid_fraction"] = float(np.nanmean(valid))

    out_png = cfg.qa_dir / f"rslc_{granule['date']}_freq{freq}_{pol}.png"
    plot_product(
        out_png,
        f"RSLC {cfg.case_name}  {granule['date']}  frequency {freq}  {pol}\n"
        f"{shape[0]} x {shape[1]} px  ground {az:.3f} az x {gr:.3f} rg m  "
        f"| overview looks {looks_r} x {looks_c}",
        to_db(amp),
        None,
        to_db(np.abs(win)),
        np.angle(win),
        map_coords=False,
        dpi=cfg.qa.dpi,
    )
    log.info(f"      wrote {out_png.name}")
    stats["png"] = str(out_png)
    return {"stats": stats}


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def run(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    started = time.time()
    res = Result(stage="qa")

    if not cfg.qa.enabled:
        log.info("qa.enabled is false in the config -- SKIP")
        res.skipped = True
        return res

    stack = load_stack(cfg)
    cfg.qa_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"gslc": {}, "rslc": {}}
    produced: list[str] = []

    if dry_run:
        log.info(f"  would write quicklooks to {cfg.qa_dir}")
        res.skipped = True
        return res

    # ---------------- GSLC products ----------------
    comparison: dict[str, list] = {}
    for date in stack["dates"]:
        path = cfg.gslc_output(date, cfg.freq_tag)
        if not path.exists():
            log.warn(f"GSLC for {date} not found ({path.name}); skipping its quicklook")
            continue
        log.info(f"GSLC {date}: {path.name}")
        for freq in cfg.frequencies:
            for pol in cfg.polarizations:
                out_png = cfg.qa_dir / f"gslc_{date}_freq{freq}_{pol}.png"
                if out_png.exists() and not force:
                    log.info(f"    freq {freq} {pol}: quicklook exists, SKIP (--force to redo)")
                    continue
                log.info(f"    freq {freq} {pol}")
                try:
                    out = qa_gslc(cfg, date, path, freq, pol, log)
                except (KeyError, OSError, ValueError) as exc:
                    log.warn(f"    freq {freq} {pol} quicklook failed: {exc}")
                    continue
                report["gslc"].setdefault(date, {})[f"{freq}_{pol}"] = out["stats"]
                produced.append(out["stats"]["png"])
                comparison.setdefault(f"{freq}_{pol}", []).append(
                    (f"{date}  freq {freq} {pol}", out["db"], out["extent"])
                )

    # side-by-side date comparison per freq/pol
    for key, panels in comparison.items():
        if len(panels) < 2:
            continue
        out_png = cfg.qa_dir / f"gslc_compare_{key}.png"
        plot_date_comparison(
            out_png,
            f"GSLC amplitude comparison  {cfg.case_name}  ({key})  -- same pinned grid",
            panels,
            cfg.qa.dpi,
        )
        log.info(f"  wrote {out_png.name}")
        produced.append(str(out_png))

    # ---------------- input RSLCs ----------------
    if cfg.qa.rslc_quicklook:
        for granule in stack["granules"]:
            log.info(f"RSLC {granule['date']}: {granule['filename']}")
            for freq in cfg.frequencies:
                for pol in cfg.polarizations:
                    out_png = cfg.qa_dir / f"rslc_{granule['date']}_freq{freq}_{pol}.png"
                    if out_png.exists() and not force:
                        log.info(f"    freq {freq} {pol}: quicklook exists, SKIP")
                        continue
                    log.info(f"    freq {freq} {pol}")
                    try:
                        out = qa_rslc(cfg, granule, freq, pol, log)
                    except (KeyError, OSError, ValueError) as exc:
                        log.warn(f"    freq {freq} {pol} quicklook failed: {exc}")
                        continue
                    report["rslc"].setdefault(granule["date"], {})[f"{freq}_{pol}"] = out["stats"]
                    produced.append(out["stats"]["png"])

    if not produced:
        log.warn("no quicklooks were produced (nothing new to do, or all inputs missing)")

    write_sidecar(
        cfg.prov_dir / "qa.json",
        stage="qa",
        inputs={"stack_json": str(cfg.stack_json)},
        outputs={"figures": produced, "report": report},
        parameters={
            "max_pixels": cfg.qa.max_pixels,
            "max_read_bytes": cfg.qa.max_read_bytes,
            "frequencies": cfg.frequencies,
            "polarizations": cfg.polarizations,
            "method": "block-wise power multilook; ground-spacing-derived looks",
        },
        started=started,
    )

    res.outputs = produced
    res.metrics = {"n_figures": len(produced)}
    return res
