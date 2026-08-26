"""
Stage G2 -- the grid gate.

Hard assertion that every GSLC in the stack is pixel-aligned: identical shape,
identical EPSG, identical geotransform, identical polarization set.

Why this stage exists as a gate rather than a comment. The isce+ course notebook
reads the SAME row/column index range from two GSLC files and cross-multiplies
them, having never compared shapes, origins, spacings or EPSG. Its markdown
asserts that "GSLC products for the same track and frame are geocoded to the
same grid" -- asserted, never tested, and the notebook was never executed. If
that assumption is violated, the result is not an error: it is a plausible-looking
interferogram of two different pieces of ground. There is no downstream check
that catches it, because coherence would simply be low and low coherence has a
hundred innocent explanations.

So: pin the grid in stage A, and refuse to proceed here unless the products
actually landed on it. Both ends of the hole.

Reads coordinate vectors (1-D, small) and raster headers only. No pixel data.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .config import Config
from .gslc import gslc_grid_info
from .ingest import load_stack
from .util import Logger, Result, StepFailed, write_sidecar

# Tolerances. Coordinates are metres in a projected CRS and are written from the
# same pinned doubles, so agreement should be exact; 1e-6 m (1 micron) allows for
# float32 storage of the coordinate vectors without admitting a real offset.
ATOL_COORD_M = 1e-6
ATOL_SPACING_M = 1e-9


def _fmt(value) -> str:
    if value is None:
        return "None"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def compare_grids(infos: dict[str, dict], freq: str) -> tuple[bool, list[str], list[str]]:
    """
    Compare every date's grid for one frequency against the reference date's.

    Returns (aligned, differences, checked_fields).
    """
    dates = sorted(infos)
    ref_date = dates[0]
    ref = infos[ref_date]
    diffs: list[str] = []

    # exact-match fields
    exact = ("shape", "epsg", "polarizations", "dtype")
    # numeric fields with a tolerance
    numeric = (
        ("x_first", ATOL_COORD_M),
        ("y_first", ATOL_COORD_M),
        ("x_last", ATOL_COORD_M),
        ("y_last", ATOL_COORD_M),
        ("x_spacing", ATOL_SPACING_M),
        ("y_spacing", ATOL_SPACING_M),
        ("nx", 0),
        ("ny", 0),
    )

    for date in dates[1:]:
        cur = infos[date]
        for field in exact:
            if ref.get(field) != cur.get(field):
                diffs.append(
                    f"freq {freq}  {field:<15s}  {ref_date}: {_fmt(ref.get(field))}"
                    f"   {date}: {_fmt(cur.get(field))}"
                )
        for field, atol in numeric:
            a, b = ref.get(field), cur.get(field)
            if a is None or b is None:
                if a is not b:
                    diffs.append(
                        f"freq {freq}  {field:<15s}  {ref_date}: {_fmt(a)}   {date}: {_fmt(b)}"
                    )
                continue
            if not np.allclose(float(a), float(b), rtol=0.0, atol=max(atol, 0)):
                diffs.append(
                    f"freq {freq}  {field:<15s}  {ref_date}: {_fmt(a)}   {date}: {_fmt(b)}"
                    f"   (delta {float(b) - float(a):+.9g})"
                )

    checked = list(exact) + [f for f, _ in numeric]
    return (not diffs), diffs, checked


def check_against_pin(infos: dict[str, dict], stack: dict, freq: str) -> list[str]:
    """
    Also verify the products match the PIN in stack.json, not merely each other.

    Two GSLCs can agree perfectly and still both be wrong -- e.g. if the posting
    silently fell back to the DEM spacing for both dates. Comparing against the
    independently-recorded pin catches that class of failure.
    """
    pin = stack["geogrid"]["per_frequency"].get(freq)
    if not pin:
        return [f"freq {freq}: stack.json carries no pinned geogrid to compare against"]
    problems: list[str] = []
    want = [int(pin["length"]), int(pin["width"])]
    want_dx, want_dy = float(pin["x_posting"]), float(pin["y_posting"])
    for date, info in sorted(infos.items()):
        if info["shape"] != want:
            problems.append(
                f"freq {freq} {date}: shape {info['shape']} != pinned {want}"
            )
        if info["x_spacing"] is not None and not np.isclose(
            abs(info["x_spacing"]), want_dx, rtol=0, atol=1e-6
        ):
            problems.append(
                f"freq {freq} {date}: x spacing {info['x_spacing']:.6f} != pinned {want_dx}"
            )
        if info["y_spacing"] is not None and not np.isclose(
            abs(info["y_spacing"]), want_dy, rtol=0, atol=1e-6
        ):
            problems.append(
                f"freq {freq} {date}: |y spacing| {abs(info['y_spacing']):.6f} != pinned {want_dy}"
            )
        if info["epsg"] is not None and int(info["epsg"]) != int(stack["geogrid"]["epsg"]):
            problems.append(
                f"freq {freq} {date}: EPSG {info['epsg']} != pinned {stack['geogrid']['epsg']}"
            )
    return problems


def run(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    started = time.time()
    res = Result(stage="gridgate")
    stack = load_stack(cfg)

    dates = stack["dates"]
    products = {d: cfg.gslc_output(d, cfg.freq_tag) for d in dates}

    missing = [str(p) for p in products.values() if not p.exists()]
    if missing:
        message = (
            "cannot run the grid gate; GSLC product(s) missing:\n"
            + "\n".join(f"    {m}" for m in missing)
            + f"\n  Run stage G1 first:\n"
            f"    python run_track_g.py --config {cfg.config_path} --only gslc"
        )
        # under --dry-run stage G1 would have produced these, so preview instead
        if not dry_run:
            raise StepFailed(message)
        log.warn(f"{len(missing)} GSLC product(s) not present yet; stage G1 would create them")
        for m in missing:
            log.info(f"    would inspect {Path(m).name}")
        res.skipped = True
        return res

    if len(dates) < 2:
        log.warn(
            f"only {len(dates)} date in the stack -- there is nothing to cross-compare. "
            f"Verifying the single product against the pin only."
        )

    if dry_run:
        for d, p in products.items():
            log.info(f"  would inspect {p.name}")
        res.skipped = True
        return res

    all_diffs: list[str] = []
    all_pin_problems: list[str] = []
    report: dict[str, dict] = {}

    for freq in cfg.frequencies:
        log.info(f"frequency {freq}: reading grid definitions ({len(dates)} product(s))")
        infos: dict[str, dict] = {}
        for date, path in products.items():
            info = gslc_grid_info(path, freq)
            infos[date] = info
            log.info(
                f"  {date}  shape {info['shape']}  EPSG {info['epsg']}  "
                f"origin ({info['x_first']:.3f}, {info['y_first']:.3f})  "
                f"spacing ({info['x_spacing']:.6f}, {info['y_spacing']:.6f})  "
                f"pols {info['polarizations']}"
            )

        aligned, diffs, checked = compare_grids(infos, freq)
        pin_problems = check_against_pin(infos, stack, freq)
        all_diffs += diffs
        all_pin_problems += pin_problems
        report[freq] = {
            "grids": infos,
            "aligned": aligned and not pin_problems,
            "differences": diffs,
            "pin_problems": pin_problems,
            "fields_checked": checked,
        }
        if aligned and not pin_problems:
            log.info(
                f"  freq {freq}: ALIGNED -- {len(checked)} field(s) match across "
                f"{len(dates)} date(s), and all match the pin in stack.json"
            )

    if all_diffs or all_pin_problems:
        lines = ["GRID GATE FAILED -- the GSLC products are NOT pixel-aligned.", ""]
        if all_diffs:
            lines.append("  Cross-date differences:")
            lines += [f"    {d}" for d in all_diffs]
            lines.append("")
        if all_pin_problems:
            lines.append("  Disagreements with the pinned geogrid in stack.json:")
            lines += [f"    {p}" for p in all_pin_problems]
            lines.append("")
        lines += [
            "  Forming an interferogram from these products would cross-multiply",
            "  DIFFERENT GROUND, producing a plausible-looking but meaningless result",
            "  rather than an error. Refusing to continue.",
            "",
            "  Fix: confirm top_left / bottom_right / output_posting are identical in",
            f"  every runconfig under {cfg.cfg_dir}, then regenerate:",
            f"    python run_track_g.py --config {cfg.config_path} --only gslc --force",
        ]
        write_sidecar(
            cfg.prov_dir / "gridgate.json",
            stage="gridgate",
            inputs={"products": {d: str(p) for d, p in products.items()}},
            outputs={},
            parameters={"frequencies": cfg.frequencies, "atol_coord_m": ATOL_COORD_M},
            started=started,
            extra={
                "status": "FAILED",
                "report": report,
                "differences": all_diffs,
                "pin_problems": all_pin_problems,
            },
        )
        raise StepFailed("\n".join(lines))

    log.info("")
    log.info(
        f"GRID GATE PASSED: {len(dates)} product(s) x {len(cfg.frequencies)} frequency(ies) "
        f"are pixel-aligned and match the pin"
    )

    write_sidecar(
        cfg.prov_dir / "gridgate.json",
        stage="gridgate",
        inputs={"products": {d: str(p) for d, p in products.items()}, "stack_json": str(cfg.stack_json)},
        outputs={"report": report},
        parameters={
            "frequencies": cfg.frequencies,
            "atol_coord_m": ATOL_COORD_M,
            "atol_spacing_m": ATOL_SPACING_M,
        },
        started=started,
        extra={"status": "PASSED"},
    )

    res.outputs = [str(cfg.prov_dir / "gridgate.json")]
    res.metrics = {
        "n_products": len(dates),
        "frequencies": cfg.frequencies,
        "aligned": True,
    }
    return res
