"""
Shared plumbing: structured logging, provenance sidecars, subprocess running,
snapping helpers.

Logging follows the ISCE2 wrapper convention the user already lives with:
  UTC ISO-8601 timestamp + optional "[Step n/N | pct | elapsed]" prefix,
  written to BOTH stdout and a per-run logfile, plus a machine-parsable
  time_summary.txt TSV of step durations.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


# --------------------------------------------------------------------------
# isce3 import guard
# --------------------------------------------------------------------------
@contextlib.contextmanager
def clean_argv():
    """
    Temporarily reduce sys.argv to just the program name.

    Required around any `import isce3` / `import nisar`. ISCE3 depends on pyre,
    whose package __init__ calls `executive.activate()`, which PARSES sys.argv
    at import time. Our own CLI flags (e.g. `--config foo.yaml`) are then fed to
    pyre's CommandLineParser, which tries to log a warning through a
    still-initialising `journal` module and dies with:

        AttributeError: partially initialized module 'journal' has no attribute
        'warning' (most likely due to a circular import)

    The failure is argv-dependent, so it appears only when the workflow is
    driven from the command line -- exactly where it matters.
    """
    saved = sys.argv
    sys.argv = [saved[0]] if saved else ["python"]
    try:
        yield
    finally:
        sys.argv = saved


def preload_isce3() -> None:
    """
    Import isce3 and nisar once, early, with a scrubbed argv.

    Python caches modules, so doing this before any CLI parsing immunises every
    later `import isce3` / `from nisar... import ...` anywhere in the process.
    """
    with clean_argv():
        import isce3  # noqa: F401
        import nisar  # noqa: F401


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------
def utc_now() -> str:
    """ISO-8601 UTC timestamp, second-of-arc precision, trailing Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def fmt_s(seconds: float) -> str:
    """Human duration: 4.2s / 3m12s / 1h04m."""
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h{int((seconds % 3600) // 60):02d}m"


def human_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PiB"


# --------------------------------------------------------------------------
# logger
# --------------------------------------------------------------------------
class Logger:
    """
    Console + file logger with an optional step-progress prefix.

    Deliberately tiny and dependency-free: the ISCE2 workflow's `write_log` is
    the ergonomic being matched, not the `logging` module's handler graph.
    """

    def __init__(self, log_file: str | os.PathLike | None = None, quiet: bool = False):
        self.log_file = Path(log_file) if log_file else None
        self.quiet = quiet
        self.t0 = time.time()
        self.current_step = 0
        self.total_steps = 0
        self._step_name = ""
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            # append: a resumed run must not clobber the crashed run's log
            with open(self.log_file, "a", encoding="utf-8") as fh:
                fh.write(f"\n{'=' * 78}\n{utc_now()}Z run started: {' '.join(sys.argv)}\n")

    # -- progress ---------------------------------------------------------
    def set_progress(self, current: int, total: int, name: str = "") -> None:
        self.current_step = current
        self.total_steps = total
        self._step_name = name

    def _prefix(self) -> str:
        if self.total_steps <= 0:
            return ""
        pct = int(self.current_step / self.total_steps * 100)
        return f"[Step {self.current_step}/{self.total_steps} | {pct}% | {fmt_s(time.time() - self.t0)}] "

    # -- emit -------------------------------------------------------------
    def log(self, message: str = "") -> None:
        line = f"{utc_now()}Z {self._prefix()}{message}"
        if not self.quiet:
            print(line, flush=True)
        if self.log_file:
            with open(self.log_file, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # convenience levels -- all one stream, prefixed, like the ISCE2 scripts
    def info(self, message: str = "") -> None:
        self.log(message)

    def warn(self, message: str) -> None:
        self.log(f"WARNING: {message}")

    def error(self, message: str) -> None:
        self.log(f"ERROR: {message}")

    def banner(self, title: str) -> None:
        self.log("=" * 70)
        self.log(title)
        self.log("=" * 70)

    def kv(self, key: str, value: Any, indent: int = 2) -> None:
        self.log(f"{' ' * indent}{key:<34s} {value}")


# --------------------------------------------------------------------------
# step timing sidecar
# --------------------------------------------------------------------------
def log_duration(summary_path: Path, step: str, duration_s: float) -> None:
    """Append one row to the machine-parsable time_summary.txt TSV."""
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if not summary_path.exists():
        with open(summary_path, "w", encoding="utf-8") as fh:
            fh.write("timestamp\tstep\tduration\n")
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write(f"{utc_now()}Z\t{step}\t{fmt_s(duration_s)}\n")


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------
def env_versions() -> dict:
    """Version fingerprint recorded into every provenance sidecar."""
    out: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "executable": sys.executable,
    }
    for mod in ("isce3", "numpy", "h5py", "shapely", "yamale", "yaml", "matplotlib"):
        try:
            with clean_argv():  # isce3 imports pyre, which parses argv
                m = __import__(mod)
            out[mod] = getattr(m, "__version__", "unknown")
        except Exception:
            out[mod] = "not-installed"
    try:
        from osgeo import gdal

        out["gdal"] = gdal.__version__
    except Exception:
        out["gdal"] = "not-installed"
    try:
        import nisar_wf

        out["nisar_wf"] = nisar_wf.__version__
    except Exception:
        pass
    return out


def write_json(path: Path, payload: dict) -> Path:
    """Atomic JSON write -- a crashed run must never leave a half-parsed sidecar."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False, default=str)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_sidecar(
    path: Path,
    stage: str,
    inputs: dict,
    outputs: dict,
    parameters: dict,
    started: float,
    extra: dict | None = None,
) -> Path:
    """
    Provenance sidecar for one stage: what went in, what came out, with which
    parameters, under which library versions, and how long it took.
    """
    payload = {
        "stage": stage,
        "status": "complete",
        "started_utc": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(time.time() - started, 3),
        "inputs": inputs,
        "outputs": outputs,
        "parameters": parameters,
        "versions": env_versions(),
        "argv": sys.argv,
    }
    if extra:
        payload.update(extra)
    return write_json(path, payload)


# --------------------------------------------------------------------------
# subprocess
# --------------------------------------------------------------------------
class StepFailed(RuntimeError):
    """A stage failed in a way that must abort the run."""


def run_cmd(
    cmd: Sequence[str],
    log: Logger,
    cwd: str | os.PathLike | None = None,
    env: dict | None = None,
    tag: str | None = None,
    check: bool = True,
) -> int:
    """
    Run a subprocess, streaming its combined output into our log line-by-line.

    Streaming (rather than capture_output) matters here: `nisar.workflows.gslc`
    runs for tens of minutes and its journal output is the only progress signal.
    """
    tag = tag or Path(cmd[0]).name
    log.info(f"Command: {' '.join(str(c) for c in cmd)}")
    t0 = time.time()
    proc = subprocess.Popen(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log.info(f"  [{tag}] {line.rstrip()}")
    rc = proc.wait()
    dur = time.time() - t0
    if rc != 0:
        msg = f"{tag} failed with return code {rc} after {fmt_s(dur)}"
        log.error(msg)
        if check:
            raise StepFailed(msg)
    else:
        log.info(f"  [{tag}] exit 0 in {fmt_s(dur)}")
    return rc


def require_tool(name: str) -> str:
    """Resolve an executable or fail with an actionable message."""
    path = shutil.which(name)
    if not path:
        raise StepFailed(
            f"required executable '{name}' not found on PATH. "
            f"Activate the processing environment first (conda activate isce3_env)."
        )
    return path


# --------------------------------------------------------------------------
# geometry / snapping helpers
# --------------------------------------------------------------------------
def snap_floor(value: float, snap: float) -> float:
    """Largest multiple of `snap` <= value."""
    import math

    return math.floor(float(value) / snap) * snap


def snap_ceil(value: float, snap: float) -> float:
    """Smallest multiple of `snap` >= value."""
    import math

    return math.ceil(float(value) / snap) * snap


def grid_size(start: float, stop: float, spacing: float) -> int:
    """
    Number of pixels between two absolute coordinates.

    Mirrors `nisar.workflows.geogrid._grid_size` EXACTLY -- int(round(abs(...)))
    -- so the shape recorded in stack.json is the shape ISCE3 will actually
    write. That equality is what lets the grid gate check a prediction rather
    than merely compare two unknowns.
    """
    return int(round(abs((stop - start) / spacing)))


def free_disk_bytes(path: str | os.PathLike) -> int:
    st = os.statvfs(str(path))
    return st.f_bavail * st.f_frsize


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------
@dataclass
class Result:
    """Uniform stage return value consumed by the wrapper's summary block."""

    stage: str
    skipped: bool = False
    outputs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def decode(value: Any) -> Any:
    """h5py scalars/arrays -> plain python, bytes -> str."""
    import numpy as np

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray):
        if value.dtype.kind == "S":
            return [v.decode("utf-8", errors="replace") for v in value.ravel().tolist()]
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value
