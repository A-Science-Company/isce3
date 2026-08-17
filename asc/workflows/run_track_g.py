#!/usr/bin/env python3
"""
Track G (GSLC) wrapper -- NISAR L1 RSLC -> L2 GSLC, coregistered by construction.

    python run_track_g.py --config configs/venezuela_t162_asc.yaml
    python run_track_g.py --config configs/venezuela_t162_asc.yaml --only ingest
    python run_track_g.py --config configs/venezuela_t162_asc.yaml --start-step gslc
    python run_track_g.py --config configs/venezuela_t162_asc.yaml --dry-run

Ergonomics follow the user's existing ISCE2 wrappers: a YAML `--config` supplies
every run parameter, numbered independently-runnable steps are selected with
`--only` / `--start-step` / `--stop-step`, resume is idempotent (completed work
is skipped unless `--force`), and everything is logged with a
"[Step n/N | pct | elapsed]" prefix to both the console and a per-run logfile,
alongside a machine-parsable time_summary.txt.

Step selection uses ONE semantics -- number, exact name, or unique
substring/prefix -- resolving the gen-1 (substring) vs gen-2 (numeric)
inconsistency in the ISCE2 scripts in favour of the more forgiving form with the
better error message.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from nisar_wf import dem as dem_stage  # noqa: E402
from nisar_wf import gridgate as gridgate_stage  # noqa: E402
from nisar_wf import gslc as gslc_stage  # noqa: E402
from nisar_wf import igram as igram_stage  # noqa: E402
from nisar_wf import ingest as ingest_stage  # noqa: E402
from nisar_wf import overlay as overlay_stage  # noqa: E402
from nisar_wf import qa as qa_stage  # noqa: E402
from nisar_wf import unwrap as unwrap_stage  # noqa: E402
from nisar_wf import watermask as watermask_stage  # noqa: E402
from nisar_wf.config import Config, ConfigError  # noqa: E402
from nisar_wf.util import (  # noqa: E402
    Logger,
    Result,
    StepFailed,
    fmt_s,
    log_duration,
    preload_isce3,
    utc_now,
)

# Import isce3/nisar NOW, while sys.argv is still untouched by us and can be
# scrubbed safely. pyre (an isce3 dependency) parses sys.argv inside its package
# __init__, so a later import -- after argparse has seen our flags -- crashes
# with a circular-import AttributeError. Doing it once here immunises the whole
# process, because Python caches modules.
preload_isce3()


# --------------------------------------------------------------------------
# step registry
# --------------------------------------------------------------------------
@dataclass
class Step:
    number: int
    name: str
    stage_id: str
    description: str
    func: Callable[..., Result]
    toggle: str


# Adding a stage is a one-line append here plus a module exposing
# run(cfg, log, force, dry_run) -> Result. See README "Slotting in the rest".
STEPS: list[Step] = [
    Step(1, "ingest", "A", "read RSLC metadata; write stack.json + PINNED geogrid",
         ingest_stage.run, "ingest"),
    Step(2, "dem", "B", "stage a WGS84-ellipsoidal DEM covering the AOI",
         dem_stage.run, "dem"),
    Step(3, "gslc", "G1", "render + validate runconfigs; geocode each date to GSLC",
         gslc_stage.run, "gslc"),
    Step(4, "gridgate", "G2", "assert every GSLC is pixel-aligned; fail loudly if not",
         gridgate_stage.run, "gridgate"),
    Step(5, "qa", "QA", "decimated-read quicklooks (never loads a full raster)",
         qa_stage.run, "qa"),
    # NOTE: igram is deliberately ahead of watermask. The water mask is built on
    # an existing product's grid so it is pixel-aligned by construction, and the
    # interferogram is that product -- on a fresh case, watermask first would
    # have nothing to build on.
    Step(6, "igram", "G3", "interferogram + coherence + per-date amplitude",
         igram_stage.run, "igram"),
    Step(7, "watermask", "W", "water mask via orthometric DEM (NASADEM route is broken)",
         watermask_stage.run, "watermask"),
    Step(8, "unwrap", "G4", "Goldstein filter -> phase-sigma coh -> water mask -> SNAPHU",
         unwrap_stage.run, "unwrap"),
    Step(9, "overlay", "G5", "folium HTML: amplitude/phase/coherence over satellite tiles",
         overlay_stage.run, "overlay"),
]


def resolve_step(token: str) -> Step:
    """
    Resolve a step by number, exact name, or unique substring/prefix.

    Raises with the full step list on any ambiguity or miss, so a typo never
    silently selects the wrong stage.
    """
    token = str(token).strip()
    if not token:
        raise ConfigError("empty step selector")

    if token.isdigit():
        n = int(token)
        for s in STEPS:
            if s.number == n:
                return s
        raise ConfigError(
            f"no step numbered {n}. Available:\n{format_steps()}"
        )

    low = token.lower()
    for s in STEPS:
        if s.name == low or s.stage_id.lower() == low:
            return s

    matches = [s for s in STEPS if low in s.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ConfigError(
            f"step selector '{token}' is ambiguous; it matches "
            f"{[m.name for m in matches]}. Be more specific."
        )
    raise ConfigError(
        f"step selector '{token}' matched 0 of {len(STEPS)} steps. Available:\n{format_steps()}"
    )


def format_steps() -> str:
    lines = []
    for s in STEPS:
        lines.append(f"    {s.number}  {s.name:<10s} [{s.stage_id:>2s}]  {s.description}")
    return "\n".join(lines)


def select_steps(args, cfg: Config, log: Logger) -> list[Step]:
    """Apply --only / --start-step / --stop-step, then the config's toggles."""
    if args.only:
        chosen = [resolve_step(tok) for tok in args.only]
        # de-duplicate, preserve pipeline order
        chosen = sorted({s.number: s for s in chosen}.values(), key=lambda s: s.number)
        log.info(f"--only: running {[s.name for s in chosen]}")
        return chosen

    start = resolve_step(args.start_step).number if args.start_step else STEPS[0].number
    stop = resolve_step(args.stop_step).number if args.stop_step else STEPS[-1].number
    if start > stop:
        raise ConfigError(
            f"--start-step ({start}) is after --stop-step ({stop}); nothing would run"
        )

    chosen = [s for s in STEPS if start <= s.number <= stop]
    if args.start_step:
        skipped = [s.name for s in STEPS if s.number < start]
        log.info(
            f"--start-step {args.start_step}: resuming at '{chosen[0].name}', "
            f"assuming outputs of {skipped} are already on disk"
        )

    # config toggles apply only to a range run, never to an explicit --only
    disabled = [s.name for s in chosen if not getattr(cfg.steps, s.toggle, True)]
    if disabled:
        log.info(f"disabled by config `steps:` -> {disabled}")
        chosen = [s for s in chosen if getattr(cfg.steps, s.toggle, True)]
    return chosen


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_track_g.py",
        description=(
            "Track G: NISAR L1 RSLC -> L2 GSLC on a pinned, shared geogrid.\n\n"
            "Steps:\n" + format_steps()
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  # what would run, without touching anything\n"
            "  run_track_g.py --config configs/venezuela_t162_asc.yaml --dry-run\n\n"
            "  # cheap metadata pass only; writes stack.json + the pinned geogrid\n"
            "  run_track_g.py --config configs/venezuela_t162_asc.yaml --only ingest\n\n"
            "  # resume after a crash in geocoding (steps 1-2 already done)\n"
            "  run_track_g.py --config configs/venezuela_t162_asc.yaml --start-step gslc\n\n"
            "  # regenerate the GSLCs from scratch\n"
            "  run_track_g.py --config configs/venezuela_t162_asc.yaml --only gslc --force\n\n"
            "  # everything up to and including the grid gate, no quicklooks\n"
            "  run_track_g.py --config configs/venezuela_t162_asc.yaml --stop-step gridgate\n"
        ),
    )
    p.add_argument(
        "--config", "-c", required=False, metavar="YAML",
        help="run configuration (see configs/venezuela_t162_asc.yaml). Required "
             "unless --list-steps is given.",
    )
    p.add_argument(
        "--only", nargs="+", metavar="STEP", default=None,
        help="run ONLY these steps, by number, name or unique prefix "
             "(e.g. --only ingest, --only 3 4). Bypasses the config's `steps:` toggles.",
    )
    p.add_argument(
        "--start-step", metavar="STEP", default=None,
        help="resume from this step; earlier steps are skipped and their outputs "
             "assumed present on disk",
    )
    p.add_argument(
        "--stop-step", metavar="STEP", default=None,
        help="stop after this step (inclusive)",
    )
    p.add_argument(
        "--force", action="store_true",
        help="recompute even when a stage's outputs already look complete",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="report what each step would do, including the exact commands, "
             "without running or writing anything",
    )
    p.add_argument(
        "--list-steps", action="store_true",
        help="print the step table and exit",
    )
    p.add_argument(
        "--log-file", metavar="PATH", default=None,
        help="append to this logfile instead of <out_root>/logs/track_g_<timestamp>.log",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="log to the file only, not the console",
    )
    # config overrides, matching the ISCE2 habit of exposing the knobs that
    # change between runs as flags while identity stays in the YAML
    p.add_argument(
        "--frequencies", nargs="+", metavar="F", default=None,
        help="override config `frequencies` (e.g. --frequencies B, --frequencies A B)",
    )
    p.add_argument(
        "--polarizations", nargs="+", metavar="P", default=None,
        help="override config `polarizations` (e.g. --polarizations HH HV)",
    )
    p.add_argument(
        "--dem-source", metavar="SRC", default=None,
        help="override config `dem.source` (NISAR | COP | NASA | 3DEP)",
    )
    return p


def overrides_from_args(args) -> dict:
    """CLI flags -> nested override dict merged over the YAML."""
    ov: dict = {}
    if args.frequencies:
        ov["frequencies"] = [f.upper() for f in args.frequencies]
    if args.polarizations:
        ov["polarizations"] = [p.upper() for p in args.polarizations]
    if args.dem_source:
        ov["dem"] = {"source": args.dem_source.upper()}
    return ov


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_steps:
        print("Track G steps:")
        print(format_steps())
        return 0

    if not args.config:
        parser.error("--config is required (or use --list-steps)")

    # ---------------- config ----------------
    try:
        cfg = Config.from_yaml(args.config, overrides=overrides_from_args(args))
        config_warnings = cfg.validate()
    except ConfigError as exc:
        print(f"{utc_now()}Z CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    # ---------------- logging ----------------
    if args.log_file:
        log_path = Path(args.log_file)
    else:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        log_path = cfg.log_dir / f"track_g_{stamp}.log"
    if not args.dry_run:
        cfg.mkdirs()
    else:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    log = Logger(log_path, quiet=args.quiet)
    log.banner(f"TRACK G (GSLC) -- {cfg.case_name}")
    log.kv("config", cfg.config_path)
    log.kv("case_dir", cfg.case_dir)
    log.kv("out_root", str(cfg.root))
    log.kv("frequencies", cfg.frequencies)
    log.kv("polarizations", cfg.polarizations)
    log.kv("dem source", f"{cfg.dem.source} (fallback {cfg.dem.fallback})")
    log.kv("flatten", cfg.gslc.flatten)
    log.kv("gpu_enabled", cfg.gslc.gpu_enabled)
    log.kv("logfile", str(log_path))
    log.kv("force", args.force)
    log.kv("dry_run", args.dry_run)
    log.kv("ARGS", vars(args))
    for w in config_warnings:
        log.warn(w)

    # ---------------- step selection ----------------
    try:
        steps = select_steps(args, cfg, log)
    except ConfigError as exc:
        log.error(str(exc))
        return 2
    if not steps:
        log.warn("no steps selected -- nothing to do")
        return 0

    log.info("")
    log.info(f"will run {len(steps)} step(s): {[s.name for s in steps]}")
    if args.dry_run:
        log.info("DRY RUN -- no products will be written")
    log.info("")

    # ---------------- execute ----------------
    results: list[tuple[Step, Result | None, str]] = []
    t_run = time.time()
    failed_step: Step | None = None
    failure: BaseException | None = None

    for idx, step in enumerate(steps, start=1):
        log.set_progress(idx, len(steps), step.name)
        log.info("-" * 70)
        log.info(f"-> START step {step.number} '{step.name}' [{step.stage_id}] -- {step.description}")
        t0 = time.time()
        try:
            result = step.func(cfg, log, force=args.force, dry_run=args.dry_run)
            dur = time.time() - t0
            state = "SKIPPED" if result.skipped else "OK"
            log.info(f"-> END   step {step.number} '{step.name}' [{state}] ({fmt_s(dur)})")
            if not args.dry_run:
                log_duration(cfg.time_summary, f"step{step.number}:{step.name}", dur)
            results.append((step, result, state))
        except (StepFailed, ConfigError) as exc:
            dur = time.time() - t0
            log.error(f"step {step.number} '{step.name}' FAILED after {fmt_s(dur)}")
            for line in str(exc).splitlines():
                log.error(f"  {line}")
            if not args.dry_run:
                log_duration(cfg.time_summary, f"step{step.number}:{step.name}:FAILED", dur)
            results.append((step, None, "FAILED"))
            failed_step, failure = step, exc
            break
        except KeyboardInterrupt:
            log.error(f"interrupted during step {step.number} '{step.name}'")
            results.append((step, None, "INTERRUPTED"))
            failed_step, failure = step, KeyboardInterrupt()
            break
        except Exception as exc:  # unexpected -- show the traceback, it is a bug
            dur = time.time() - t0
            log.error(f"step {step.number} '{step.name}' raised an UNEXPECTED {type(exc).__name__} "
                      f"after {fmt_s(dur)}")
            for line in traceback.format_exc().splitlines():
                log.error(f"  {line}")
            results.append((step, None, "ERROR"))
            failed_step, failure = step, exc
            break

    # ---------------- summary ----------------
    log.set_progress(0, 0)
    log.info("")
    log.banner("TRACK G SUMMARY")
    log.kv("case", cfg.case_name)
    log.kv("elapsed", fmt_s(time.time() - t_run))
    log.kv("frequencies / pols", f"{cfg.frequencies} / {cfg.polarizations}")

    for step, result, state in results:
        log.info(f"  step {step.number} {step.name:<10s} {state}")
        if result is None:
            continue
        for note in result.notes:
            log.info(f"       note: {note}")
        for key, value in result.metrics.items():
            log.info(f"       {key}: {value}")

    if failed_step is not None:
        log.info("")
        log.error(f"RUN FAILED at step {failed_step.number} '{failed_step.name}'")
        remaining = [s.name for s in steps if s.number > failed_step.number]
        if remaining:
            log.error(f"  not attempted: {remaining}")
        log.error(
            f"  after fixing, resume with:\n"
            f"    python {Path(__file__).name} --config {cfg.config_path} "
            f"--start-step {failed_step.name}"
        )
        log.kv("logfile", str(log_path))
        return 1

    log.info("")
    if args.dry_run:
        log.info("DRY RUN COMPLETE -- nothing was written")
    else:
        log.info("RUN COMPLETE")
        _next_steps(cfg, log, [s.name for s in steps])
    log.kv("logfile", str(log_path))
    if not args.dry_run:
        log.kv("time summary", str(cfg.time_summary))
    return 0


def _next_steps(cfg: Config, log: Logger, ran: list[str]) -> None:
    """Close the loop the way 0_params_setup.py does: print the literal next command."""
    if "overlay" in ran:
        log.info("")
        log.info("  Open the overlay in a browser. Every amplitude layer is HH (co-pol) --")
        log.info("  this product is HH-only at L2, there is no VV. Remaining stage:")
        log.info("    dolphin  phase linking / time series over an N-date stack")
    elif "unwrap" in ran:
        log.info("")
        log.info("  Next:")
        log.info(f"    python {Path(__file__).name} --config {cfg.config_path} --only overlay")
    elif "igram" in ran:
        log.info("")
        log.info("  Next:")
        log.info(f"    python {Path(__file__).name} --config {cfg.config_path} "
                 f"--only watermask unwrap overlay")
    elif "gridgate" in ran:
        log.info("")
        log.info("  The GSLC stack is verified pixel-aligned. Next:")
        log.info(f"    python {Path(__file__).name} --config {cfg.config_path} --only igram")
    elif "gslc" in ran:
        log.info("")
        log.info("  Next:")
        log.info(f"    python {Path(__file__).name} --config {cfg.config_path} --only gridgate")
    elif "ingest" in ran:
        log.info("")
        log.info(f"  Pinned geogrid written to {cfg.stack_json}. Next:")
        log.info(f"    python {Path(__file__).name} --config {cfg.config_path} --only dem")


if __name__ == "__main__":
    sys.exit(main())
