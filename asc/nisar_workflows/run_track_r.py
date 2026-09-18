#!/usr/bin/env python3
"""
Track R (RSLC) wrapper -- NISAR L1 RSLC -> coregistered RIFG, in RADAR coordinates.

    python run_track_r.py --config configs/nepal_glof.yaml
    python run_track_r.py --config configs/nepal_glof.yaml --only runconfig
    python run_track_r.py --config configs/nepal_glof.yaml --dry-run
    python run_track_r.py --config configs/nepal_glof.yaml --frequency B --looks 9 1

Track R is the conventional reference-scene chain -- the direct analogue of the
ISCE2 Sentinel-1 workflow -- and the counterpart to `run_track_g.py`'s
geocode-to-a-pinned-grid. It shares that script's config, its `stack.json` and
its DEM; only steps 3-5 are Track R's own.

The two facts that govern its cost:

  * The REFERENCE image is never resampled. Only the secondary is.
  * Looks are consumed at `crossmul`, the LAST stage. Everything upstream runs
    at full radar-grid resolution, so raising looks reduces neither RAM, nor
    scratch, nor runtime. The runconfig step reports the bill and refuses to
    start without the disk.

Ergonomics deliberately match run_track_g.py: one YAML `--config`, numbered
independently-runnable steps selected with `--only` / `--start-step` /
`--stop-step`, idempotent resume unless `--force`, and a
"[Step n/N | pct | elapsed]" prefix to both console and logfile.
"""

from __future__ import annotations

import argparse
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
from nisar_wf import ingest as ingest_stage  # noqa: E402
from nisar_wf import trackr as trackr_stage  # noqa: E402
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

# Import isce3/nisar NOW, while sys.argv is still untouched. pyre (an isce3
# dependency) parses sys.argv inside its package __init__, so a later import --
# after argparse has seen our flags -- crashes with a circular-import
# AttributeError. Doing it once here immunises the whole process.
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


# Steps 1-2 are shared with Track G and read/write the same artifacts, so a case
# already ingested for Track G resumes straight into step 3.
STEPS: list[Step] = [
    Step(1, "ingest", "A", "read RSLC metadata; write stack.json (shared with Track G)",
         ingest_stage.run, "ingest"),
    Step(2, "dem", "B", "stage a WGS84-ellipsoidal DEM covering the AOI (shared)",
         dem_stage.run, "dem"),
    Step(3, "runconfig", "R1", "render + schema-validate one insar runconfig per pair; disk gate",
         trackr_stage.run_runconfig, "runconfig"),
    Step(4, "insar", "R2", "rdr2geo -> geo2rdr -> resample -> dense offsets -> crossmul",
         trackr_stage.run_insar, "insar"),
    Step(5, "qa", "R3", "decimated-read coherence summary per pair",
         trackr_stage.run_qa, "qa"),
]


def resolve_step(token: str) -> Step:
    """Resolve a step by number, exact name, or unique substring/prefix."""
    token = str(token).strip()
    if not token:
        raise ConfigError("empty step selector")

    if token.isdigit():
        n = int(token)
        for s in STEPS:
            if s.number == n:
                return s
        raise ConfigError(f"no step numbered {n}. Available:\n{format_steps()}")

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
    return "\n".join(
        f"    {s.number}  {s.name:<10s} [{s.stage_id:>2s}]  {s.description}" for s in STEPS
    )


def select_steps(args, cfg: Config, log: Logger) -> list[Step]:
    """Apply --only / --start-step / --stop-step."""
    if args.only:
        chosen = [resolve_step(tok) for tok in args.only]
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
    return chosen


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_track_r.py",
        description=("Track R: NISAR L1 RSLC -> coregistered RIFG in radar coordinates.\n\n"
                     "Steps:\n" + format_steps()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  # what would run, and what it would cost, without touching anything\n"
            "  run_track_r.py --config configs/nepal_glof.yaml --dry-run\n\n"
            "  # render and validate the runconfigs, and print the disk bill\n"
            "  run_track_r.py --config configs/nepal_glof.yaml --only runconfig\n\n"
            "  # one pair only, freq B at 9 az x 1 rg\n"
            "  run_track_r.py --config configs/nepal_glof.yaml --pair 20260714 20260726\n\n"
            "  # resume after a crash inside the InSAR chain\n"
            "  run_track_r.py --config configs/nepal_glof.yaml --start-step insar --force\n"
        ),
    )
    p.add_argument("--config", "-c", required=False, metavar="YAML",
                   help="run configuration; Track R reads its `track_r:` block. "
                        "Required unless --list-steps is given.")
    p.add_argument("--only", nargs="+", metavar="STEP", default=None,
                   help="run ONLY these steps, by number, name or unique prefix")
    p.add_argument("--start-step", metavar="STEP", default=None,
                   help="resume from this step; earlier outputs assumed present")
    p.add_argument("--stop-step", metavar="STEP", default=None,
                   help="stop after this step (inclusive)")
    p.add_argument("--force", action="store_true",
                   help="recompute even when outputs look complete; also clears the "
                        "pair's scratch tree before re-running the InSAR chain")
    p.add_argument("--dry-run", action="store_true",
                   help="report what each step would do, including the disk bill, "
                        "without running or writing anything")
    p.add_argument("--list-steps", action="store_true", help="print the step table and exit")
    p.add_argument("--log-file", metavar="PATH", default=None,
                   help="append to this logfile instead of <out_root>/logs/track_r_<stamp>.log")
    p.add_argument("--quiet", action="store_true", help="log to the file only")

    # config overrides -- the knobs that change between runs
    p.add_argument("--frequency", metavar="F", default=None,
                   help="override track_r.frequency (A or B)")
    p.add_argument("--polarization", metavar="P", default=None,
                   help="override track_r.polarization (default HH)")
    p.add_argument("--looks", nargs=2, type=int, metavar=("AZ", "RG"), default=None,
                   help="override the looks for the selected frequency, AZIMUTH then "
                        "RANGE (e.g. --looks 9 1). Applied at crossmul.")
    p.add_argument("--product-type", metavar="T", default=None,
                   help="override track_r.product_type (RIFG | RUNW | GUNW | RIFG_RUNW_GUNW). "
                        "RIFG is the whole coregistration chain and stops before snaphu.")
    p.add_argument("--pair", nargs=2, metavar=("REF", "SEC"), action="append", default=None,
                   help="process only this pair; repeatable. Overrides track_r.pairs.")
    p.add_argument("--no-disk-gate", action="store_true",
                   help="warn instead of refusing when scratch would not fit. "
                        "You are asserting you have checked the disk yourself.")
    return p


def overrides_from_args(args) -> dict:
    """CLI flags -> nested override dict merged over the YAML."""
    ov: dict = {}
    tr: dict = {}
    if args.frequency:
        tr["frequency"] = args.frequency.upper()
    if args.polarization:
        tr["polarization"] = args.polarization.upper()
    if args.product_type:
        tr["product_type"] = args.product_type.upper()
    if args.pair:
        tr["pairs"] = [[str(a), str(b)] for a, b in args.pair]
    if args.no_disk_gate:
        tr["enforce_disk_gate"] = False
    if args.looks:
        # Applies to the frequency actually selected, so --looks and --frequency
        # compose. Without --frequency this targets the config's frequency, which
        # main() resolves after the first load.
        tr["_looks_cli"] = [int(args.looks[0]), int(args.looks[1])]
    if tr:
        ov["track_r"] = tr
    return ov


def _apply_looks_override(cfg: Config, looks_cli: list[int] | None) -> None:
    """
    Fold --looks into the per-frequency table for the selected frequency.

    Kept out of the dataclass so the YAML stays the single description of both
    frequencies: overriding one band must not silently blank the other.
    """
    if not looks_cli:
        return
    from nisar_wf.config import Looks

    cfg.track_r.looks[cfg.track_r.frequency] = Looks(int(looks_cli[0]), int(looks_cli[1]))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_steps:
        print("Track R steps:")
        print(format_steps())
        return 0

    if not args.config:
        parser.error("--config is required (or use --list-steps)")

    # ---------------- config ----------------
    overrides = overrides_from_args(args)
    looks_cli = overrides.get("track_r", {}).pop("_looks_cli", None)
    try:
        cfg = Config.from_yaml(args.config, overrides=overrides)
        _apply_looks_override(cfg, looks_cli)
        config_warnings = cfg.validate()
    except ConfigError as exc:
        print(f"{utc_now()}Z CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    if not cfg.track_r.enabled:
        print(f"{utc_now()}Z track_r.enabled is false in {args.config}; nothing to do",
              file=sys.stderr)
        return 0

    tr = cfg.track_r
    lk = tr.looks_for(tr.frequency)

    # ---------------- logging ----------------
    if args.log_file:
        log_path = Path(args.log_file)
    else:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        log_path = cfg.log_dir / f"track_r_{stamp}.log"
    if not args.dry_run:
        cfg.mkdirs()
    else:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    log = Logger(log_path, quiet=args.quiet)
    log.banner(f"TRACK R (RSLC COREGISTRATION) -- {cfg.case_name}")
    log.kv("config", cfg.config_path)
    log.kv("case_dir", cfg.case_dir)
    log.kv("out_root", str(cfg.root))
    log.kv("frequency / pol", f"{tr.frequency} / {tr.polarization}")
    log.kv("looks (az x rg)", f"{lk.azimuth} x {lk.range}   [applied at crossmul]")
    log.kv("product_type", tr.product_type)
    log.kv("dense_offsets", tr.dense_offsets_enabled)
    log.kv("dem", str(cfg.dem_path))
    log.kv("gpu_enabled", tr.gpu_enabled)
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
                log_duration(cfg.time_summary, f"trackR-step{step.number}:{step.name}", dur)
            results.append((step, result, state))
        except (StepFailed, ConfigError) as exc:
            dur = time.time() - t0
            log.error(f"step {step.number} '{step.name}' FAILED after {fmt_s(dur)}")
            for line in str(exc).splitlines():
                log.error(f"  {line}")
            if not args.dry_run:
                log_duration(cfg.time_summary, f"trackR-step{step.number}:{step.name}:FAILED", dur)
            results.append((step, None, "FAILED"))
            failed_step = step
            break
        except KeyboardInterrupt:
            log.error(f"interrupted during step {step.number} '{step.name}'")
            results.append((step, None, "INTERRUPTED"))
            failed_step = step
            break
        except Exception as exc:  # unexpected -- show the traceback, it is a bug
            dur = time.time() - t0
            log.error(f"step {step.number} '{step.name}' raised an UNEXPECTED "
                      f"{type(exc).__name__} after {fmt_s(dur)}")
            for line in traceback.format_exc().splitlines():
                log.error(f"  {line}")
            results.append((step, None, "ERROR"))
            failed_step = step
            break

    # ---------------- summary ----------------
    log.set_progress(0, 0)
    log.info("")
    log.banner("TRACK R SUMMARY")
    log.kv("case", cfg.case_name)
    log.kv("elapsed", fmt_s(time.time() - t_run))
    log.kv("frequency / pol", f"{tr.frequency} / {tr.polarization}")
    log.kv("looks (az x rg)", f"{lk.azimuth} x {lk.range}")

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
            f"    python {Path(__file__).name} --config {cfg.config_path}"
            f" --start-step {failed_step.name}"
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
    """Print the literal next command, as run_track_g.py does."""
    name = Path(__file__).name
    if "qa" in ran:
        log.info("")
        log.info("  Coregistration is done and the RIFG is on the reference radar grid.")
        log.info("  The reference was never resampled; only the secondary was.")
        log.info("  Compare against Track G's geocoded pair with:")
        log.info("    python ../compare/compare_tracks.py --help")
    elif "insar" in ran:
        log.info("")
        log.info("  Next:")
        log.info(f"    python {name} --config {cfg.config_path} --only qa")
    elif "runconfig" in ran:
        log.info("")
        log.info("  Runconfigs written and schema-validated. Next:")
        log.info(f"    python {name} --config {cfg.config_path} --only insar")
    elif "dem" in ran:
        log.info("")
        log.info("  Next:")
        log.info(f"    python {name} --config {cfg.config_path} --only runconfig")
    elif "ingest" in ran:
        log.info("")
        log.info(f"  stack.json written to {cfg.stack_json}. Next:")
        log.info(f"    python {name} --config {cfg.config_path} --only dem")


if __name__ == "__main__":
    sys.exit(main())
