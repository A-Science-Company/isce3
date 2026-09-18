#!/usr/bin/env python3
"""
NISAR coregistration module: one short case config in, a coregistered stack out.

    python nisar_coreg.py -c coreg_configs/<case>.yaml show        # resolved dates, reference, names, parameters; runs nothing
    python nisar_coreg.py -c coreg_configs/<case>.yaml status      # what is done
    python nisar_coreg.py -c coreg_configs/<case>.yaml progress    # running pairs: ISCE3 stage timeline, live progress, scratch
    python nisar_coreg.py -c coreg_configs/<case>.yaml run --dry-run
    python nisar_coreg.py -c coreg_configs/<case>.yaml run --detach                  # prepare -> crop (if crop) -> coreg, in tmux
    python nisar_coreg.py -c coreg_configs/<case>.yaml run --stage coreg --dates 20260620   # one unit (for job fan-out)
    options: --jobs N (units in parallel), --force (redo verified units), --dry-run, --detach

Case config (coreg_configs/<case>.yaml)
---------------------------------------
    case, workdir                  workdir holds L1_RSLC/ (inputs) and aux/dem/ (DEM)
    mode: RSLC | GSLC              RSLC: every secondary resampled onto the reference radar grid (geometry + dense offsets)
                                   GSLC: every date geocoded independently onto one pinned map grid (geometry only)
    crop: true | false, aoi_kml    RSLC: cut every RSLC to the AOI first (tools/rslc_subset.py); GSLC: clip the grid to the bbox
    reference_date: null           RSLC only; null = the middle of the selected dates (even count: the earlier middle one)
    start_date, end_date: null     select RSLCs already on disk (null = no bound); nothing is ever downloaded
    tag: (optional)                appended to the stack name; needed when overriding a parameter of an existing stack
Every processing parameter comes from coreg_configs/defaults.yaml (the validated values); a case config may override a
key by repeating its section. The parameters of a stack are recorded in <stack>/params.json and a run with different
values is refused.

Stages and units
----------------
prepare  once per case: stage the DEM if missing.
crop     RSLC + crop only; one unit per date -> <workdir>/crop/<aoi>/<date>.h5.
coreg    set-up once (render + validate the driver config; ingest, DEM check, ISCE3 runconfigs); then one unit per
         secondary (RSLC: ISCE3 insar to a 1x1 RIFG, verify, export, prune scratch) or per date (GSLC: geocode), and
         for GSLC a final gridgate.
Output (<workdir>/coreg/<stack>/): slc/<date>.slc+.hdr, geometry/{lon,lat,hgt}.rdr, ifg/RIFG_*.h5, offsets/ (RSLC) or
gslc/<date>_gslc_freq<F>.h5 (GSLC); status/<unit>.json manifests; params.json; stack_manifest.json; ISCE3 tree in isce3/.

Job semantics: a unit whose manifest says ok and whose outputs still match the recorded sizes is skipped; finished
outputs are never overwritten without --force; exit codes 0 ok, 1 unit failed, 2 config error, 3 missing prerequisite.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PY = sys.executable
EXIT_OK, EXIT_FAIL, EXIT_CONFIG, EXIT_PREREQ = 0, 1, 2, 3
B_PER_PX = 104.4 + 12.0          # measured ISCE3 scratch model (nisar_wf/trackr.py) + RIFG, per reference pixel


# ============================================================================ helpers
def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class Log:
    def __init__(self, path: Path | None):
        self.path = path
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg: str):
        line = f"{now()} {msg}"
        print(line, flush=True)
        if self.path:
            with open(self.path, "a") as fh:
                fh.write(line + "\n")


def sha1_file(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_rev() -> str:
    try:
        rev = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(HERE), "status", "--porcelain", "--", "."], capture_output=True, text=True).stdout.strip()
        return rev + ("+dirty" if dirty else "")
    except Exception:  # noqa: BLE001
        return "unknown"


def write_json(p: Path, obj: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    os.replace(tmp, p)


def read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def sizeof(p: Path | None) -> int:
    return p.stat().st_size if p and p.exists() else -1


def free_bytes(p: Path) -> int:
    return shutil.disk_usage(p).free


def run_cmd(cmd: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as fh:
        fh.write(f"### {now()} $ {' '.join(shlex.quote(c) for c in cmd)}\n")
        fh.flush()
        return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=HERE).returncode


def link_or_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if dst.stat().st_ino == src.stat().st_ino:
            return
        raise FileExistsError(f"{dst} exists and is a different file; refusing to overwrite")
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def unit_done(manifest: Path) -> bool:
    m = read_json(manifest)
    if not m or m.get("status") != "ok":
        return False
    return all(Path(p).exists() and Path(p).stat().st_size == s for p, s in m.get("outputs", {}).items())


def kml_bbox(kml: Path) -> list[float]:
    txt = kml.read_text()
    xs, ys = [], []
    for block in re.findall(r"<coordinates>(.*?)</coordinates>", txt, re.S):
        for tok in block.split():
            x, y = tok.split(",")[:2]
            xs.append(float(x)); ys.append(float(y))
    return [min(xs), min(ys), max(xs), max(ys)]


# ============================================================================ config
DEFAULTS = HERE / "coreg_configs" / "defaults.yaml"
CASE_KEYS = ("case", "workdir", "mode", "crop", "aoi_kml", "reference_date", "start_date", "end_date", "tag")


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def unknown_keys(over: dict, base: dict, prefix: str = "") -> list[str]:
    bad = []
    for k, v in over.items():
        if k not in base:
            bad.append(prefix + k)
        elif isinstance(v, dict) and isinstance(base[k], dict):
            bad += unknown_keys(v, base[k], f"{prefix}{k}.")
    return bad


class ConfigErr(Exception):
    pass


def norm_date(v, key: str) -> str | None:
    if v is None:
        return None
    s = str(v).replace("-", "")
    try:
        dt.datetime.strptime(s, "%Y%m%d")
    except ValueError:
        raise ValueError(f"{key}: {v!r} is not a date (YYYYMMDD or YYYY-MM-DD)") from None
    return s


class Coreg:
    def __init__(self, path: Path):
        self.path = path.resolve()
        user = yaml.safe_load(self.path.read_text()) or {}
        defaults = yaml.safe_load(DEFAULTS.read_text())
        errs = []
        over = {k: v for k, v in user.items() if k not in CASE_KEYS}
        bad = unknown_keys(over, defaults)
        if bad:
            errs.append(f"unknown key(s) {bad}; case keys are {list(CASE_KEYS)}, overridable sections {list(defaults)}")
        for k in ("case", "workdir", "mode"):
            if user.get(k) in (None, ""):
                errs.append(f"{k} is required")
        if errs:
            raise ConfigErr("coreg config errors:\n  " + "\n  ".join(errs))
        self.defaults = defaults
        self.eff = eff = deep_merge(defaults, over)
        self.overrides = over

        self.case = str(user["case"])
        self.workdir = Path(user["workdir"])
        self.mode = str(user["mode"]).upper()
        self.crop = bool(user.get("crop", False))
        self.aoi_kml = Path(user["aoi_kml"]).resolve() if user.get("aoi_kml") else None
        self.aoi_name = self.aoi_kml.stem if self.aoi_kml else None
        self.tag = str(user["tag"]) if user.get("tag") else None
        i = eff["inputs"]
        self.rslc_dir = self.workdir / i["rslc_dir"]
        self.tier, self.frequency, self.polarization = i["tier"], i["frequency"], i["polarization"]
        self.dem_path = self.workdir / eff["dem"]["path"].format(case=self.case)
        r = eff["run"]
        self.jobs, self.prune, self.min_free_gb = int(r["jobs"]), bool(r["prune_scratch"]), float(r["min_free_gb"])
        self.gslc_freqs = [str(f) for f in eff["gslc"]["frequencies"]]
        self.gslc_posting = float(eff["gslc"]["posting_m"])

        if self.mode not in ("RSLC", "GSLC"):
            errs.append(f"mode must be RSLC or GSLC (got {self.mode})")
        if not self.workdir.is_dir():
            errs.append(f"workdir does not exist: {self.workdir}")
        if self.crop and self.aoi_kml is None:
            errs.append("crop is true but aoi_kml is not set")
        if self.aoi_kml is not None and not self.aoi_kml.exists():
            errs.append(f"aoi_kml not found: {self.aoi_kml}")
        if self.tag is not None and not re.fullmatch(r"[A-Za-z0-9-]+", self.tag):
            errs.append(f"tag may contain only letters, digits and '-' (got {self.tag!r})")
        try:
            start, end = norm_date(user.get("start_date"), "start_date"), norm_date(user.get("end_date"), "end_date")
            ref = norm_date(user.get("reference_date"), "reference_date")
        except ValueError as e:
            errs.append(str(e))
            start = end = ref = None
        self.start_date, self.end_date = start, end

        # dates = RSLC files already on disk inside [start_date, end_date]; nothing is downloaded
        self.files: dict[str, Path] = {}
        if self.rslc_dir.is_dir():
            for f in sorted(self.rslc_dir.glob(f"NISAR_L1_{self.tier}_RSLC_*.h5")):
                m = re.search(r"_(\d{8})T\d{6}_", f.name)
                if not m:
                    continue
                if m.group(1) in self.files:
                    errs.append(f"two {self.tier} RSLCs for {m.group(1)}: {self.files[m.group(1)].name}, {f.name}")
                self.files[m.group(1)] = f
        else:
            errs.append(f"rslc_dir does not exist: {self.rslc_dir}")
        if start and end and start > end:
            errs.append(f"start_date {start} is after end_date {end}")
        self.dates = sorted(d for d in self.files if (start is None or d >= start) and (end is None or d <= end))
        if not self.dates:
            errs.append(f"no {self.tier} RSLC in {self.rslc_dir} between {start or 'the first'} and {end or 'the last'} date")
        elif self.mode == "RSLC" and len(self.dates) < 2:
            errs.append(f"RSLC coregistration needs at least two dates; selected {self.dates}")

        # reference: given, or the middle of the selected dates (for an even count, the earlier of the two middle ones).
        # The median date minimises the summed temporal baseline to the reference.
        self.reference_given = ref is not None
        self.reference = ref if ref else (self.dates[(len(self.dates) - 1) // 2] if self.dates else None)
        if self.mode == "RSLC" and ref and ref not in self.dates:
            errs.append(f"reference_date {ref} is not among the selected dates {self.dates}")
        if errs:
            raise ConfigErr("coreg config errors:\n  " + "\n  ".join(errs))
        self.secondaries = [d for d in self.dates if d != self.reference] if self.mode == "RSLC" else []

        area = self.aoi_name if self.crop else "fulltile"
        suffix = f"_{self.tag}" if self.tag else ""
        if self.mode == "RSLC":
            self.stack_id = f"RSLC_ref{self.reference}_{self.frequency}{self.polarization}_{area}{suffix}"
        else:
            self.stack_id = f"GSLC_{''.join(self.gslc_freqs)}{self.polarization}_{area}_{self.gslc_posting:g}m{suffix}"
        self.stack_dir = self.workdir / "coreg" / self.stack_id
        self.isce_root = self.stack_dir / "isce3"
        self.logdir = self.stack_dir / "logs"
        self.gen_cfg = self.stack_dir / ("track_r.yaml" if self.mode == "RSLC" else "track_g.yaml")
        b = eff["crop_buffers"]
        bsuf = "" if b == defaults["crop_buffers"] else f"_az{b['buffer_az_lines']}_rg{b['buffer_range_m']:g}m_px{b['buffer_px']}"
        self.crop_dir = self.workdir / "crop" / f"{self.aoi_name}{bsuf}" if (self.crop and self.mode == "RSLC") else None

    # parameters that change output bytes; recorded once per stack / crop directory and never silently changed
    def stack_params(self) -> dict:
        p = {"mode": self.mode, "tier": self.tier, "frequency": self.frequency, "polarization": self.polarization,
             "dem": {"path": str(self.dem_path)}, "crop": self.crop}
        if self.mode == "RSLC":
            p |= {"reference": self.reference, "rslc": self.eff["rslc"], "block_budget_mb": self.eff["run"]["block_budget_mb"]}
            if self.crop:
                p["crop_params"] = self.crop_params()
        else:
            p |= {"gslc": self.eff["gslc"], "aoi_bbox": kml_bbox(self.aoi_kml) if self.crop else None}
        return p

    def crop_params(self) -> dict:
        return {"aoi_kml_sha1": sha1_file(self.aoi_kml), "polarization": self.polarization, **self.eff["crop_buffers"]}

    def rslc(self, date: str) -> Path | None:
        return self.files.get(date)

    def units(self) -> list[str]:
        return self.secondaries if self.mode == "RSLC" else self.dates

    # crop (RSLC)
    def crop_out(self, d):
        return self.crop_dir / f"{d}.h5"

    def crop_manifest(self, d):
        return self.crop_dir / "status" / f"{d}.json"

    def stack_input(self, d):
        return self.crop_out(d) if (self.mode == "RSLC" and self.crop) else self.rslc(d)

    def manifest(self, d):
        return self.stack_dir / "status" / f"{d}.json"

    # ISCE3 paths (mirror nisar_wf/trackr.pair_paths and Config.gslc_output)
    def pair_tag(self):
        return f"{self.frequency}_{self.polarization}_1x1"

    def pair_scratch(self, sec):
        return self.isce_root / "scratch" / "trackR" / f"{self.reference}_{sec}_{self.pair_tag()}"

    def pair_product(self, sec):
        return self.isce_root / "pairs" / f"{self.reference}_{sec}" / "trackR" / f"RIFG_{self.reference}_{sec}_{self.pair_tag()}.h5"

    def gslc_product(self, d, f):
        return self.isce_root / "L2_GSLC" / f"{d}_gslc_freq{f}.h5"


def params_gate(params_file: Path, params: dict, what: str, log: Log, dry_run: bool) -> bool:
    """First use records the parameters; later runs must match them (output-identity rule)."""
    old = read_json(params_file)
    cur = json.loads(json.dumps(params, default=str))
    if old is None:
        if not dry_run:
            write_json(params_file, cur)
        return True
    if old == cur:
        return True
    diffs = [k for k in sorted(set(old) | set(cur)) if old.get(k) != cur.get(k)]
    log(f"{what}: parameters differ from the ones recorded in {params_file} ({diffs}); refusing to mix outputs. "
        f"Set `tag:` in the case config to write a separately named {what}, or restore the recorded values.")
    return False


# ============================================================================ generated ISCE3 configs
def isce_raw(cg: Coreg, case_name: str, out_root: Path, granules: list, frequencies: list[str]) -> dict:
    d = cg.eff["dem"]
    return {"case_name": case_name, "case_dir": str(cg.workdir), "out_root": str(out_root),
            "granules": [str(g) for g in granules], "frequencies": frequencies, "polarizations": [cg.polarization],
            "dem": {"path": str(cg.dem_path), "source": d["source"], "buffer_deg": float(d["buffer_deg"])}}


def write_generated(path: Path, raw: dict, who: str) -> list[str]:
    """Write the driver config and validate it with the driver's own loader; returns its warnings."""
    from nisar_wf.config import Config, ConfigError
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# generated by nisar_coreg.py ({who}) -- edit the case config or coreg_configs/defaults.yaml, not this file\n"
                    + yaml.safe_dump(raw, sort_keys=False))
    try:
        cfg = Config.from_yaml(path)
        return cfg.validate()
    except ConfigError as e:
        raise SystemExit(f"generated config {path} is invalid: {e}") from None


def prepare_cfg(cg: Coreg) -> Path:
    raw = isce_raw(cg, f"{cg.case}_prepare", cg.workdir / "coreg" / "_prepare", [cg.rslc(d) for d in cg.dates], [cg.frequency])
    p = cg.workdir / "coreg" / "_prepare" / f"prepare_{cg.case}.yaml"
    write_generated(p, raw, "prepare")
    return p


def rslc_stack_raw(cg: Coreg) -> dict:
    raw = isce_raw(cg, f"{cg.case}_{cg.stack_id}", cg.isce_root, [cg.stack_input(d) for d in [cg.reference] + cg.secondaries], [cg.frequency])
    r = cg.eff["rslc"]
    do = r["dense_offsets"]
    raw["track_r"] = {
        "frequency": cg.frequency, "polarization": cg.polarization, "product_type": "RIFG", "ionosphere_enabled": False,
        "looks": {cg.frequency: {"azimuth": 1, "range": 1}},
        "pairs": [[int(cg.reference), int(s)] for s in cg.secondaries],
        "min_free_gb": cg.min_free_gb, "block_budget_mb": float(cg.eff["run"]["block_budget_mb"]),
        "rdr2geo_threshold": float(r["rdr2geo"]["threshold"]), "rdr2geo_numiter": int(r["rdr2geo"]["numiter"]),
        "rdr2geo_extraiter": int(r["rdr2geo"]["extraiter"]),
        "geo2rdr_threshold": float(r["geo2rdr"]["threshold"]), "geo2rdr_maxiter": int(r["geo2rdr"]["maxiter"]),
        "coarse_lines_per_tile": int(r["coarse_resample"]["lines_per_tile"]),
        "coarse_columns_per_tile": int(r["coarse_resample"]["columns_per_tile"]),
        "dense_offsets_enabled": bool(do["enabled"]),
        **{k: int(do[k]) for k in ("window_range", "window_azimuth", "half_search_range", "half_search_azimuth", "skip_range", "skip_azimuth")},
        "fine_lines_per_tile": int(r["fine_resample"]["lines_per_tile"]),
        "fine_columns_per_tile": int(r["fine_resample"]["columns_per_tile"]),
        "crossmul_flatten": bool(r["crossmul"]["flatten"]), "crossmul_oversample": int(r["crossmul"]["oversample"]),
    }
    return raw


def gslc_stack_raw(cg: Coreg) -> dict:
    raw = isce_raw(cg, f"{cg.case}_{cg.stack_id}", cg.isce_root, [cg.rslc(d) for d in cg.dates], cg.gslc_freqs)
    raw["geogrid"] = {"aoi_lonlat": kml_bbox(cg.aoi_kml) if cg.crop else None,
                      "posting": {f: {"x": cg.gslc_posting, "y": cg.gslc_posting} for f in cg.gslc_freqs}}
    return raw


def driver(cg: Coreg) -> str:
    return str(HERE / ("run_track_r.py" if cg.mode == "RSLC" else "run_track_g.py"))


# ============================================================================ show / status
def unit_state(man: Path) -> str:
    if unit_done(man):
        return "done"
    return {"failed": "FAILED", "running": "running"}.get((read_json(man) or {}).get("status"), "-")


def cmd_show(cg: Coreg, a) -> int:
    """Print what a run would do: resolved dates, reference, names, and every parameter in effect."""
    print(f"case         {cg.case}\nmode         {cg.mode}\nworkdir      {cg.workdir}")
    print(f"crop         {'yes, ' + str(cg.aoi_kml) if cg.crop else 'no (full tile)'}")
    print(f"date range   {cg.start_date or 'first on disk'} .. {cg.end_date or 'last on disk'}  ->  {len(cg.dates)} dates: {' '.join(cg.dates)}")
    if cg.mode == "RSLC":
        how = "given" if cg.reference_given else f"middle of the {len(cg.dates)} selected dates"
        print(f"reference    {cg.reference} ({how})\nsecondaries  {' '.join(cg.secondaries)}")
    else:
        print("reference    not used: GSLC dates are geocoded independently onto one pinned grid, not registered to each other")
    print(f"stack        {cg.stack_dir}")
    if cg.crop_dir:
        print(f"crops        {cg.crop_dir}")
    print(f"DEM          {cg.dem_path} ({'present' if cg.dem_path.exists() else 'missing: prepare stages it'})")
    shown = {k: cg.eff[k] for k in ("inputs", "run")}
    shown |= {"rslc": cg.eff["rslc"], **({"crop_buffers": cg.eff["crop_buffers"]} if cg.crop else {})} if cg.mode == "RSLC" else {"gslc": cg.eff["gslc"]}
    print("\nparameters in effect (coreg_configs/defaults.yaml" + (f", overridden by the case config: {sorted(cg.overrides)})" if cg.overrides else ")"))
    print(yaml.safe_dump(shown, sort_keys=False, default_flow_style=None).rstrip())
    return EXIT_OK


def cmd_status(cg: Coreg, a) -> int:
    print(f"case {cg.case}   mode {cg.mode}   stack {cg.stack_id}")
    print(f"crop {'on (' + cg.aoi_name + ')' if cg.crop else 'off'}   "
          + (f"reference {cg.reference}{'' if cg.reference_given else ' (middle date)'}" if cg.mode == "RSLC" else "reference n/a (GSLC)"))
    setup = read_json(cg.stack_dir / "status" / "_setup.json") or {}
    print(f"prepare (DEM): {'done' if cg.dem_path.exists() else 'NOT DONE'}   "
          f"coreg set-up: {'done' if set(cg.units()) <= set(setup.get('units', [])) else 'NOT DONE'}")
    print(f"{'date':10s} {'role':10s} {'rslc':10s} {'crop':8s} {'coreg':10s}")
    for d in cg.dates:
        r = cg.rslc(d)
        cs = unit_state(cg.crop_manifest(d)) if (cg.crop and cg.mode == "RSLC") else "n/a"
        if cg.mode == "RSLC" and d == cg.reference:
            role, gs = "reference", "ref"
        else:
            role, gs = ("secondary" if cg.mode == "RSLC" else "date"), unit_state(cg.manifest(d))
        print(f"{d:10s} {role:10s} {sizeof(r) / 1e9:5.1f} GB   {cs:8s} {gs:10s}")
    print(f"free disk {free_bytes(cg.workdir) / 1e9:.0f} GB")
    return EXIT_OK


# ISCE3 insar stages in order: (label, scratch subdirectory whose creation marks the start, crop v2 duration in s).
# Crop v2 = the validated glof_bigger_aoi pair 20260714x20260726, one pair on a lightly loaded 8-core VM (directory birth times).
ISCE3_STAGES = [("rdr2geo", "rdr2geo", 1296), ("geo2rdr + product prep", "geo2rdr", 1610), ("coarse resample", "coarse_resample_slc", 118),
                ("dense offsets", "dense_offsets", 977), ("rubber sheet", "rubbersheet_offsets", 692),
                ("fine resample", "fine_resample_slc", 57), ("crossmul + RIFG", "crossmul", 129)]


def birth_time(p: Path) -> float | None:
    try:
        t = int(subprocess.run(["stat", "-c", "%W", str(p)], capture_output=True, text=True).stdout.strip() or 0)
        return float(t) if t > 0 else None
    except Exception:  # noqa: BLE001
        return None


def cmd_progress(cg: Coreg, a) -> int:
    """Per-unit detail: ISCE3 stage timeline (scratch directory creation times), current stage, live progress line, scratch size."""
    if cg.mode != "RSLC":
        return cmd_status(cg, a)
    now_s = time.time()
    fmt = lambda s: "-" if s is None else (f"{s / 60:5.1f} min" if s < 5400 else f"{s / 3600:5.2f} h")  # noqa: E731
    for u in cg.units():
        m = read_json(cg.manifest(u)) or {}
        st = "done" if unit_done(cg.manifest(u)) else m.get("status", "not started")
        print(f"\n== {cg.reference} x {u}: {st}" + (f" ({fmt(m.get('duration_s'))})" if st == "done" else ""))
        if st in ("done", "not started"):
            continue
        sd = cg.pair_scratch(u)
        starts = [(lab, birth_time(sd / sub), ref) for lab, sub, ref in ISCE3_STAGES]
        started = [s for s in starts if s[1] is not None]
        t_unit = dt.datetime.strptime(m["started"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp() if m.get("started") else None
        print(f"   {'stage':24s} {'started (UTC)':14s} {'took':>10s} {'crop v2':>10s}")
        for i, (lab, t0, ref) in enumerate(starts):
            if t0 is None:
                print(f"   {lab:24s} {'-':14s} {'':>10s} {fmt(ref):>10s}")
                continue
            nxt = next((s[1] for s in starts[i + 1:] if s[1] is not None), None)
            took = (nxt - t0) if nxt else None
            running = nxt is None and st == "running"
            label = fmt(took) if took is not None else (f"{fmt(now_s - t0)} so far" if running else "-")
            print(f"   {lab:24s} {dt.datetime.fromtimestamp(t0, dt.timezone.utc).strftime('%H:%M:%S'):14s} {label:>10s} {fmt(ref):>10s}")
        if st == "running" and started:
            cur = started[-1]
            left_ref = sum(ref for lab, t0, ref in starts if t0 is None)
            print(f"   now in: {cur[0]}; unit elapsed {fmt(now_s - t_unit) if t_unit else '-'}; stages still to come took {fmt(left_ref)} in crop v2")
        log = Path(m["log"]) if m.get("log") else None
        if log and log.exists():
            tail = subprocess.run(["tail", "-n", "40", str(log)], capture_output=True, text=True).stdout.splitlines()
            live = [ln.split("] ", 2)[-1] for ln in tail if re.search(r"progress|Processing chunks|block|Error|Traceback|EXIT", ln)]
            if live:
                print(f"   last progress line: {live[-1].strip()}")
            print(f"   log: {log}")
        size = sum(f.stat().st_size for f in sd.rglob("*") if f.is_file()) if sd.exists() else 0
        print(f"   scratch: {size / 1e9:.1f} GB in {sd}")
    print(f"\nfree disk {free_bytes(cg.workdir) / 1e9:.0f} GB")
    return EXIT_OK


# ============================================================================ stage: prepare
def stage_prepare(cg: Coreg, a, log: Log) -> int:
    """Stage the DEM once per case (from the full-tile RSLC footprints). Everything else is per stack."""
    if cg.dem_path.exists():
        log(f"prepare: DEM exists ({cg.dem_path}); skip")
        return EXIT_OK
    if a.dry_run:
        log(f"prepare: would stage the DEM {cg.dem_path} (ingest + dem over {len(cg.dates)} RSLCs)")
        return EXIT_OK
    cfg = prepare_cfg(cg)
    rc = run_cmd([PY, "-u", str(HERE / "run_track_r.py"), "--config", str(cfg), "--only", "ingest", "dem",
                  "--log-file", str(cg.logdir / f"prepare_{stamp()}.driver.log")], cg.logdir / f"prepare_{stamp()}.console.log")
    log(f"prepare EXIT={rc}")
    return EXIT_OK if rc == 0 else EXIT_FAIL


# ============================================================================ stage: crop (RSLC)
def crop_unit(cg: Coreg, d: str, a, log: Log) -> str:
    out, man = cg.crop_out(d), cg.crop_manifest(d)
    if unit_done(man) and not a.force:
        log(f"crop {d}: done, skip")
        return "skip"
    src = cg.rslc(d)
    if out.exists() and not a.force:
        log(f"crop {d}: {out} exists without a verified manifest; refusing to overwrite (--force)")
        return "fail"
    tmp = Path(str(out) + ".part.h5")
    b = cg.eff["crop_buffers"]
    cmd = [PY, "-u", str(HERE / "tools" / "rslc_subset.py"), "--rslc", str(src), "--out", str(tmp), "--kml", str(cg.aoi_kml),
           "--dem", str(cg.dem_path), "--polarizations", cg.polarization, "--buffer-az-lines", str(int(b["buffer_az_lines"])),
           "--buffer-range-m", str(float(b["buffer_range_m"])), "--buffer", str(int(b["buffer_px"]))]
    if a.dry_run:
        log(f"crop {d}: would run {' '.join(cmd)}")
        return "dry"
    out.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        tmp.unlink()
    if a.force and out.exists():
        out.unlink()
    ulog = cg.crop_dir / "logs" / f"{d}_{stamp()}.log"
    t0 = time.time()
    write_json(man, {"stage": "crop", "unit": d, "status": "running", "started": now(), "host": socket.gethostname(), "log": str(ulog)})
    rc = run_cmd(cmd, ulog)
    ok = rc == 0 and sizeof(tmp) > 0
    if ok:
        os.replace(tmp, out)
    write_json(man, {"stage": "crop", "unit": d, "status": "ok" if ok else "failed", "exit_code": rc, "finished": now(),
                     "duration_s": round(time.time() - t0, 1), "host": socket.gethostname(), "log": str(ulog),
                     "inputs": {str(src): sizeof(src), str(cg.aoi_kml): sizeof(cg.aoi_kml), str(cg.dem_path): sizeof(cg.dem_path)},
                     "params": cg.crop_params(), "outputs": {str(out): sizeof(out)} if ok else {},
                     "code": {"git": git_rev(), "tool": "tools/rslc_subset.py"}})
    log(f"crop {d}: {'ok' if ok else 'FAILED'} (EXIT={rc}, {time.time() - t0:.0f} s)")
    return "ok" if ok else "fail"


def stage_crop(cg: Coreg, a, log: Log) -> int:
    if not (cg.mode == "RSLC" and cg.crop):
        log("crop: not applicable (" + ("GSLC clips its geogrid instead" if cg.mode == "GSLC" else "crop disabled") + ")")
        return EXIT_OK
    if not cg.dem_path.exists():
        log(f"crop: DEM missing ({cg.dem_path}); run --stage prepare first")
        return EXIT_PREREQ
    dates = a.dates or cg.dates
    bad = [d for d in dates if d not in cg.dates]
    if bad:
        log(f"crop: {bad} are not among the selected dates {cg.dates}")
        return EXIT_CONFIG
    if not params_gate(cg.crop_dir / "params.json", cg.crop_params(), "crop directory", log, a.dry_run):
        return EXIT_CONFIG
    with cf.ThreadPoolExecutor(max_workers=max(1, a.jobs or cg.jobs)) as ex:
        res = list(ex.map(lambda d: crop_unit(cg, d, a, log), dates))
    log(f"crop summary: {dict(zip(dates, res))}")
    return EXIT_FAIL if "fail" in res else EXIT_OK


# ============================================================================ stage: coreg
def coreg_setup(cg: Coreg, a, log: Log) -> int:
    """Render and validate the driver config, then ingest + DEM (+ ISCE3 runconfigs for RSLC). Re-run when dates are added."""
    setup = cg.stack_dir / "status" / "_setup.json"
    prev = read_json(setup) or {}
    if set(cg.units()) <= set(prev.get("units", [])) and cg.gen_cfg.exists() and not a.force:
        return EXIT_OK
    if cg.mode == "RSLC":
        dates = [cg.reference] + cg.secondaries
        need = [d for d in dates if not unit_done(cg.crop_manifest(d))] if cg.crop else []
        if need and a.dry_run:
            raw = rslc_stack_raw(cg)
            raw["granules"] = [str(cg.rslc(d)) for d in dates]
            with tempfile.TemporaryDirectory() as td:
                warns = write_generated(Path(td) / cg.gen_cfg.name, raw, "dry run")
            log(f"coreg set-up: driver config validates ({len(warns)} warning(s){': ' + '; '.join(warns) if warns else ''}; "
                f"checked with full-tile granule paths because crops are missing for {need})")
        if need:
            log(f"coreg set-up: crops not ready for {need}; run --stage crop first")
            return EXIT_PREREQ
        raw, steps = rslc_stack_raw(cg), [(["ingest", "dem"], []), (["runconfig"], ["--no-disk-gate"])]
    else:
        raw, steps = gslc_stack_raw(cg), [(["ingest", "dem"], [])]
    if a.dry_run:
        with tempfile.TemporaryDirectory() as td:
            warns = write_generated(Path(td) / cg.gen_cfg.name, raw, f"coreg {cg.mode}, dry run")
        log(f"coreg set-up: driver config renders and validates ({len(warns)} warning(s){': ' + '; '.join(warns) if warns else ''}); "
            f"would write {cg.gen_cfg} and run {[s for s, _ in steps]}")
        return EXIT_OK
    warns = write_generated(cg.gen_cfg, raw, f"coreg {cg.mode}")
    for w in warns:
        log(f"coreg set-up: driver warning: {w}")
    for s, extra in steps:
        rc = run_cmd([PY, "-u", driver(cg), "--config", str(cg.gen_cfg), "--only", *s, *extra, "--log-file", str(cg.logdir / f"setup_{stamp()}.driver.log")],
                     cg.logdir / f"setup_{stamp()}.console.log")
        log(f"coreg set-up {'+'.join(s)} EXIT={rc}")
        if rc != 0:
            return EXIT_FAIL
    write_json(setup, {"stage": "coreg-setup", "status": "ok", "finished": now(), "mode": cg.mode, "config": str(cg.gen_cfg),
                       "units": sorted(set(cg.units()) | set(prev.get("units", []))), "code": {"git": git_rev()}})
    return EXIT_OK


def rslc_unit(cg: Coreg, sec: str, a, log: Log) -> str:
    man = cg.manifest(sec)
    if unit_done(man) and not a.force:
        log(f"coreg {sec}: done, skip")
        return "skip"
    if a.dry_run:
        log(f"coreg {sec}: would coregister onto {cg.reference}, verify, export, prune={cg.prune}")
        return "dry"
    st = read_json(cg.isce_root / "stack.json") or {}
    ref_shape = next((g.get("frequencies", {}).get(cg.frequency, {}).get("shape") for g in st.get("granules", []) if g.get("date") == cg.reference), None)
    need = (ref_shape[0] * ref_shape[1] * B_PER_PX if ref_shape else 0) + cg.min_free_gb * 1e9
    if free_bytes(cg.workdir) < need:
        log(f"coreg {sec}: needs {need / 1e9:.0f} GB free, have {free_bytes(cg.workdir) / 1e9:.0f} GB; not started")
        return "prereq"
    ulog = cg.logdir / f"coreg_{cg.reference}_{sec}_{stamp()}.log"
    t0 = time.time()
    write_json(man, {"stage": "coreg", "unit": sec, "status": "running", "started": now(), "host": socket.gethostname(), "log": str(ulog)})
    cmd = [PY, "-u", driver(cg), "--config", str(cg.gen_cfg), "--only", "insar", "--pair", cg.reference, sec, "--no-disk-gate",
           "--log-file", str(ulog.with_suffix(".driver.log"))] + (["--force"] if a.force else [])
    rc = run_cmd(cmd, ulog)
    sd, prod = cg.pair_scratch(sec), cg.pair_product(sec)
    fp = f"freq{cg.frequency}/{cg.polarization}"
    ref_slc, sec_slc = sd / f"crossmul/{fp}/reference.slc", sd / f"fine_resample_slc/{fp}/coregistered_secondary.slc"
    checks = {"exit_code_0": rc == 0, "rifg_product": sizeof(prod) > 0, "coregistered_slc": sizeof(sec_slc) > 0,
              "coregistered_size_equals_reference": sizeof(sec_slc) == sizeof(ref_slc) > 0}
    ok, outputs = all(checks.values()), {}
    if ok:
        o = cg.stack_dir
        todo = [(sec_slc, o / "slc" / f"{sec}.slc"), (Path(str(sec_slc).replace(".slc", ".hdr")), o / "slc" / f"{sec}.hdr"), (prod, o / "ifg" / prod.name)]
        if not (o / "slc" / f"{cg.reference}.slc").exists():
            todo += [(ref_slc, o / "slc" / f"{cg.reference}.slc"), (Path(str(ref_slc).replace(".slc", ".hdr")), o / "slc" / f"{cg.reference}.hdr")]
            for comp, name in (("x", "lon"), ("y", "lat"), ("z", "hgt")):
                src = sd / f"rdr2geo/freq{cg.frequency}/{comp}.rdr"
                todo += [(src, o / "geometry" / f"{name}.rdr"), (src.with_suffix(".hdr"), o / "geometry" / f"{name}.hdr")]
        for comp in ("az", "rg"):
            src = sd / f"rubbersheet_offsets/{fp}/culled_{comp}_offsets"
            for s in (src, Path(str(src) + ".hdr")):
                if s.exists():
                    todo += [(s, o / "offsets" / f"{sec}_{s.name}")]
        try:
            for s, dst in todo:
                if s.exists():
                    link_or_copy(s, dst)
                    outputs[str(dst)] = sizeof(dst)
        except Exception as e:  # noqa: BLE001
            ok, checks["export"] = False, f"failed: {e}"
    pruned = False
    if ok and cg.prune:
        kept = {Path(p).stat().st_ino for p in outputs}
        if all(p.stat().st_ino in kept for p in (sec_slc, prod)):
            shutil.rmtree(sd, ignore_errors=True)
            pruned = True
    write_json(man, {"stage": "coreg", "type": "RSLC", "unit": sec, "reference": cg.reference, "status": "ok" if ok else "failed",
                     "exit_code": rc, "checks": checks, "finished": now(), "duration_s": round(time.time() - t0, 1),
                     "host": socket.gethostname(), "log": str(ulog),
                     "inputs": {str(cg.stack_input(cg.reference)): sizeof(cg.stack_input(cg.reference)), str(cg.stack_input(sec)): sizeof(cg.stack_input(sec)),
                                str(cg.gen_cfg): sizeof(cg.gen_cfg)},
                     "outputs": outputs, "scratch_pruned": pruned, "code": {"git": git_rev(), "driver": "run_track_r.py --only insar"}})
    log(f"coreg {sec}: {'ok' if ok else 'FAILED'} (EXIT={rc}, {time.time() - t0:.0f} s, pruned={pruned}) {checks if not ok else ''}")
    return "ok" if ok else "fail"


def gslc_unit(cg: Coreg, d: str, a, log: Log) -> str:
    man = cg.manifest(d)
    if unit_done(man) and not a.force:
        log(f"coreg {d}: done, skip")
        return "skip"
    if a.dry_run:
        log(f"coreg {d}: would geocode {cg.gslc_freqs} to GSLC on the pinned grid")
        return "dry"
    ulog = cg.logdir / f"gslc_{d}_{stamp()}.log"
    t0 = time.time()
    write_json(man, {"stage": "coreg", "type": "GSLC", "unit": d, "status": "running", "started": now(), "log": str(ulog)})
    rc = 0
    for f in cg.gslc_freqs:
        rc = rc or run_cmd([PY, "-u", driver(cg), "--config", str(cg.gen_cfg), "--only", "gslc", "--frequencies", f, "--dates", d,
                            "--log-file", str(ulog.with_suffix(f".{f}.driver.log"))] + (["--force"] if a.force else []), ulog)
    prods = {f: cg.gslc_product(d, f) for f in cg.gslc_freqs}
    checks = {"exit_code_0": rc == 0, **{f"gslc_{f}": sizeof(p) > 0 for f, p in prods.items()}}
    ok, outputs = all(checks.values()), {}
    if ok:
        for f, p in prods.items():
            dst = cg.stack_dir / "gslc" / p.name
            link_or_copy(p, dst)
            outputs[str(dst)] = sizeof(dst)
    write_json(man, {"stage": "coreg", "type": "GSLC", "unit": d, "status": "ok" if ok else "failed", "exit_code": rc, "checks": checks,
                     "note": "GSLC dates are geocoded independently onto one pinned grid; they are not registered to each other",
                     "finished": now(), "duration_s": round(time.time() - t0, 1), "log": str(ulog),
                     "inputs": {str(cg.rslc(d)): sizeof(cg.rslc(d)), str(cg.gen_cfg): sizeof(cg.gen_cfg)}, "outputs": outputs,
                     "code": {"git": git_rev(), "driver": "run_track_g.py --only gslc"}})
    log(f"coreg {d}: {'ok' if ok else 'FAILED'} (EXIT={rc}, {time.time() - t0:.0f} s)")
    return "ok" if ok else "fail"


def stage_coreg(cg: Coreg, a, log: Log) -> int:
    if not params_gate(cg.stack_dir / "params.json", cg.stack_params(), "stack", log, a.dry_run):
        return EXIT_CONFIG
    rc = coreg_setup(cg, a, log)
    if rc != EXIT_OK:
        return rc
    units = a.dates or cg.units()
    bad = [u for u in units if u not in cg.units()]
    if bad:
        log(f"not units of this stack: {bad}")
        return EXIT_CONFIG
    fn = rslc_unit if cg.mode == "RSLC" else gslc_unit
    with cf.ThreadPoolExecutor(max_workers=max(1, a.jobs or cg.jobs)) as ex:
        res = list(ex.map(lambda u: fn(cg, u, a, log), units))
    log(f"coreg summary: {dict(zip(units, res))}")
    if a.dry_run or not all(unit_done(cg.manifest(u)) for u in cg.units()):
        return EXIT_FAIL if "fail" in res else (EXIT_PREREQ if "prereq" in res else EXIT_OK)
    if cg.mode == "GSLC":
        grc = run_cmd([PY, "-u", driver(cg), "--config", str(cg.gen_cfg), "--only", "gridgate", "--frequencies", *cg.gslc_freqs,
                       "--log-file", str(cg.logdir / f"gridgate_{stamp()}.driver.log")], cg.logdir / f"gridgate_{stamp()}.console.log")
        log(f"gridgate EXIT={grc}")
        if grc != 0:
            return EXIT_FAIL
    write_json(cg.stack_dir / "stack_manifest.json", {
        "stack_id": cg.stack_id, "type": cg.mode, "reference": cg.reference, "crop": cg.aoi_name if cg.crop else None,
        "dates": ([cg.reference] if cg.mode == "RSLC" else []) + cg.units(), "updated": now(), "config": str(cg.path),
        "units": {u: read_json(cg.manifest(u)) for u in cg.units()}})
    log(f"stack complete: {cg.stack_dir / 'stack_manifest.json'}")
    return EXIT_OK


# ============================================================================ main
def detach(argv, cg: Coreg, what: str) -> int:
    session = re.sub(r"[^A-Za-z0-9_-]", "_", f"coreg_{cg.case}_{what}")[:60]
    if subprocess.run(["tmux", "has-session", "-t", session], capture_output=True).returncode == 0:
        print(f"tmux session {session} already running; attach: tmux attach -t {session}")
        return EXIT_PREREQ
    cg.logdir.mkdir(parents=True, exist_ok=True)
    logf = cg.logdir / f"{what}_{stamp()}.detached.log"
    inner = " ".join(shlex.quote(x) for x in [PY, "-u", str(Path(__file__).resolve())] + [x for x in argv if x != "--detach"])
    subprocess.run(["tmux", "new-session", "-d", "-s", session, f"{inner} > {shlex.quote(str(logf))} 2>&1; echo EXIT=$? >> {shlex.quote(str(logf))}"], check=True)
    print(f"started: tmux session {session}\n  attach: tmux attach -t {session}\n  log:    {logf}")
    return EXIT_OK


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", required=True, type=Path)
    ap.add_argument("command", choices=["show", "status", "progress", "run"])
    ap.add_argument("--stage", choices=["prepare", "crop", "coreg"], help="run only this stage (default: all, in order)")
    ap.add_argument("--dates", nargs="+", help="units to process (crop: dates; RSLC coreg: secondaries; GSLC coreg: dates)")
    ap.add_argument("--jobs", type=int, default=None, help="units in parallel (default coreg.jobs)")
    ap.add_argument("--force", action="store_true", help="redo units even when verified done")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--detach", action="store_true", help="run inside a tmux session and return immediately")
    a = ap.parse_args(argv)
    try:
        cg = Coreg(a.config)
    except ConfigErr as e:
        print(e, file=sys.stderr)
        return EXIT_CONFIG
    argv = [str(cg.path) if x == str(a.config) else x for x in argv]      # absolute config path for --detach
    if a.command == "show":
        return cmd_show(cg, a)
    if a.command == "status":
        return cmd_status(cg, a)
    if a.command == "progress":
        return cmd_progress(cg, a)
    if a.detach:
        return detach(argv, cg, a.stage or "all")
    log = Log(None if a.dry_run else cg.logdir / f"run_{a.stage or 'all'}_{stamp()}.log")
    log(f"nisar_coreg {cg.mode} stack {cg.stack_id} (config {cg.path}, git {git_rev()})")
    stages = [a.stage] if a.stage else ["prepare", "crop", "coreg"]
    for s in stages:
        rc = {"prepare": stage_prepare, "crop": stage_crop, "coreg": stage_coreg}[s](cg, a, log)
        if rc != EXIT_OK:
            log(f"stopped at stage {s} (exit {rc})")
            return rc
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
