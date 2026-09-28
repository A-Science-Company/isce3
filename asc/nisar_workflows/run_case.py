#!/usr/bin/env python3
"""
One case, one config: run a NISAR workflow end to end from case_studies/<NAME>/case.yaml.

    python run_case.py -c case_studies/<NAME>/case.yaml show      # the plan and every parameter; touches nothing
    python run_case.py -c case_studies/<NAME>/case.yaml run       # do it
    python run_case.py -c case_studies/<NAME>/case.yaml run --detach   # the same, in tmux
    python run_case.py -c case_studies/<NAME>/case.yaml status    # what is done
    python run_case.py -c case_studies/<NAME>/case.yaml upload    # push the mode's products to GCS

The case directory is the working directory of the run:

    case_studies/<NAME>/ SLC/              the L1 RSLCs, however they got there (gcloud, ASF, copy)
                         geometries/*.kml  the AOI
                         case.yaml         this config
                         _run/             the generated module configs (do not edit)
                         crop/ coreg/ timeseries/ aux/   the products

workflow x mode is the 4 x 2 matrix:

    RSLC | cropped_RSLC          coregistration -> nisar_coreg.py            (coregistered SLCs on the reference grid)
                                 interferogram  -> + nisar_timeseries.py --through ifg   (multilooked ifg + coherence)
    GSLC | cropped_GSLC          coregistration -> nisar_coreg.py mode GSLC  (each date geocoded onto one pinned grid)
                                 interferogram  -> + stage igram             (map-domain ifg + coherence)

This file only plans, generates the two module configs and shells out; the modules own the work, the logs, the
per-unit manifests and the parameter gates. Nothing here reprocesses or overwrites anything they refuse to.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nisar_coreg as NC                                    # noqa: E402  planning only; execution is by subprocess
import nisar_timeseries as NT                               # noqa: E402

HERE = Path(__file__).resolve().parent
EXIT_OK, EXIT_FAIL, EXIT_CONFIG, EXIT_PREREQ = 0, 1, 2, 3

CORR_SOURCES = {"ionosphere": ("gunw", "split_spectrum", "none"),
                "troposphere": ("gunw", "none"), "solid_earth_tides": ("gunw", "none")}

WORKFLOWS = {"RSLC": ("RSLC", False), "cropped_RSLC": ("RSLC", True),
             "GSLC": ("GSLC", False), "cropped_GSLC": ("GSLC", True)}
MODES = ("coregistration", "interferogram")

CASE_DEFAULTS = {
    "case": None,                 # name; default = the case directory name
    "workflow": None,             # RSLC | cropped_RSLC | GSLC | cropped_GSLC
    "mode": None,                 # coregistration | interferogram
    "slc_dir": "SLC",             # relative to the case directory, or absolute
    "aoi_kml": None,              # required for the cropped workflows and for any RSLC interferogram
    "netrc": None,                # gs://... or a local path, copied to ~/.netrc (600) when that file is missing
    "inputs": {"tier": "PR", "frequency": "A", "polarization": "HH"},
    "dates": {"start": None, "end": None, "reference": None},
    "resolution_m": None,         # the preset: metres on the ground, resolved to looks from the granule's own spacing
    "looks": {"azimuth": 9, "range": 8},        # RSLC: interferogram looks AND the crop lattice they must land on
    "gslc": {"posting_m": 5, "looks_y": 8, "looks_x": 8},    # GSLC: metres on the ground, then looks on that grid
    "network": {"max_connections": 3, "max_temporal_baseline_days": None, "pairs": []},
    # where each correction comes from. false is shorthand for all none, true for all gunw.
    "corrections": {"ionosphere": "none", "troposphere": "none", "solid_earth_tides": "none"},
    "tag": None,                  # appended to the product names, to run a variant beside a finished one
    "upload": {"destination": None, "enabled": True},        # gs://bucket/prefix; null destination = stay local
    "envs": {"isce3_python": None, "ts_python": None},
    "advanced": {"coreg": {}, "timeseries": {}},             # merged verbatim over the generated module configs
}


class CaseErr(Exception):
    pass


def log(msg):
    print(msg, flush=True)


def sh(cmd, dry=False, check=True):
    if dry:
        log("    would run: " + " ".join(shlex.quote(str(c)) for c in cmd))
        return 0
    log("    " + " ".join(shlex.quote(str(c)) for c in cmd))
    rc = subprocess.run([str(c) for c in cmd]).returncode
    if rc != 0 and check:
        raise CaseErr(f"command failed (exit {rc}): {' '.join(str(c) for c in cmd)}")
    return rc


def du(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


# ============================================================================ the case
class Case:
    def __init__(self, path: Path):
        self.path = path.resolve()
        if not self.path.exists():
            raise CaseErr(f"case config not found: {self.path}")
        user = yaml.safe_load(self.path.read_text()) or {}
        # `advanced` is a pass-through to the module configs: its contents are checked by the modules, not here
        bad = NC.unknown_keys({k: v for k, v in user.items() if k != "advanced"}, CASE_DEFAULTS)
        if not isinstance(user.get("advanced", {}), dict) or set(user.get("advanced", {})) - {"coreg", "timeseries"}:
            bad += ["advanced.* (only advanced.coreg and advanced.timeseries exist)"]
        errs = [f"unknown key(s) {bad}; the keys are {list(CASE_DEFAULTS)}"] if bad else []
        c = NC.deep_merge(CASE_DEFAULTS, user)
        self.user, self.c = user, c
        self.dir = self.path.parent
        self.name = str(c["case"] or self.dir.name)

        wf = str(c["workflow"] or "")
        if wf not in WORKFLOWS:
            errs.append(f"workflow must be one of {list(WORKFLOWS)} (got {wf!r})")
        self.workflow = wf
        self.coreg_mode, self.crop = WORKFLOWS.get(wf, ("RSLC", False))
        self.mode = str(c["mode"] or "")
        if self.mode not in MODES:
            errs.append(f"mode must be one of {list(MODES)} (got {self.mode!r})")

        self.slc_dir = self.abs(c["slc_dir"])
        self.aoi_kml = self.abs(c["aoi_kml"]) if c["aoi_kml"] else None
        if self.aoi_kml is not None and not self.aoi_kml.exists():
            errs.append(f"aoi_kml not found: {self.aoi_kml} (put the KML in {self.dir / 'geometries'})")
        if self.aoi_kml is None and self.crop:
            errs.append(f"workflow {wf} crops to an AOI, so aoi_kml is required")
        if self.aoi_kml is None and self.mode == "interferogram" and self.coreg_mode == "RSLC":
            errs.append("an RSLC interferogram is formed inside an AOI window, so aoi_kml is required")
        if not self.slc_dir.is_dir():
            errs.append(f"slc_dir does not exist: {self.slc_dir}")
        raw_c = user.get("corrections", c["corrections"])
        if isinstance(raw_c, bool):                       # false = all none, true = all gunw
            raw_c = {k: ("gunw" if raw_c else "none") for k in CORR_SOURCES}
        elif not isinstance(raw_c, dict):
            errs.append(f"corrections must be true, false, or a map of layer -> source (got {raw_c!r})")
            raw_c = {}
        self.corr = {k: str(raw_c.get(k, "none")) for k in CORR_SOURCES}
        self.c["corrections"] = self.corr
        for k, allowed in CORR_SOURCES.items():
            if self.corr[k] not in allowed:
                errs.append(f"corrections.{k}: {self.corr[k]!r} is not one of {list(allowed)}"
                            + ("; a troposphere straight from a weather model is not implemented - the GUNW's own "
                               "troposphere IS a weather model (ECMWF HRES via RAiDER)" if k == "troposphere" else ""))
        on = [k for k in CORR_SOURCES if self.corr[k] != "none"]
        if self.coreg_mode == "GSLC" and on:
            errs.append(f"corrections {on} apply to the RSLC time series only (GSLC dates are not coregistered)")
        if on and self.mode != "interferogram":
            errs.append(f"corrections {on} are applied by the time series, so mode must be interferogram")
        if self.corr["ionosphere"] == "split_spectrum" and self.c["inputs"]["frequency"] != "A":
            errs.append("a split-spectrum ionosphere needs inputs.frequency A (it solves A against the B side band)")
        if errs:
            raise CaseErr("case config errors:\n  " + "\n  ".join(errs))

        self.run_dir = self.dir / "_run"
        self.coreg_cfg = self.run_dir / "coreg.yaml"
        self.ts_cfg = self.run_dir / "timeseries.yaml"
        self.ts_name = "ifg"
        self._cg = self._ts = self._tmp = None
        self.derived = {}
        self.resolve_looks()

    # ---- the resolution preset -------------------------------------------------------------
    def granule(self) -> Path:
        """Any granule of the configured tier: the spacings this preset reads are the same on every date."""
        g = sorted(self.slc_dir.glob(f"NISAR_L1_{self.c['inputs']['tier']}_RSLC_*.h5"))
        if not g:
            raise CaseErr(f"resolution_m has to read the pixel spacing from a granule, and no "
                          f"NISAR_L1_{self.c['inputs']['tier']}_RSLC_*.h5 is in {self.slc_dir}")
        return g[0]

    def ground_spacing(self):
        """(azimuth m, ground range m, how) for this track, read from the granule and the AOI.

        Azimuth is sceneCenterAlongTrackSpacing. Ground range is the slant spacing over sin(incidence), and the
        incidence must be taken over the AOI: a crop keeps the whole swath's geolocation grid, and averaging all of
        it moves the answer by about 4% - enough to change the rounded look counts.
        """
        import h5py
        import numpy as np
        fr = self.c["inputs"]["frequency"]
        with h5py.File(self.granule(), "r") as h:
            sw = h[f"science/LSAR/RSLC/swaths/frequency{fr}"]
            sr, az = float(sw["slantRangeSpacing"][()]), float(sw["sceneCenterAlongTrackSpacing"][()])
            g = h["science/LSAR/RSLC/metadata/geolocationGrid"]
            epsg = int(g["epsg"][()])
            if epsg != 4326:
                raise CaseErr(f"the granule's geolocation grid is EPSG {epsg}, not 4326, so resolution_m cannot mask "
                              f"it with the AOI's lon/lat box. Give looks explicitly instead")
            hs = g["heightAboveEllipsoid"][()]
            k = int(np.argmin(np.abs(hs - float(np.median(hs)))))      # any mid level; verified insensitive
            inc, cx, cy = g["incidenceAngle"][k], g["coordinateX"][k], g["coordinateY"][k]
        w, s_, e, n = NC.kml_bbox(self.aoi_kml)
        sel = (cx >= w) & (cx <= e) & (cy >= s_) & (cy <= n)
        if int(sel.sum()) < 4:
            raise CaseErr(f"the AOI {self.aoi_kml.name} covers {int(sel.sum())} nodes of the granule's geolocation "
                          f"grid (they are ~700 m apart), too few to resolve looks. Give looks explicitly instead")
        i = float(np.mean(inc[sel]))
        return az, sr / float(np.sin(np.radians(i))), {
            "incidence_deg": round(i, 4), "incidence_min_deg": round(float(inc[sel].min()), 4),
            "incidence_max_deg": round(float(inc[sel].max()), 4), "geolocation_nodes": int(sel.sum()),
            "azimuth_spacing_m": round(az, 6), "slant_range_spacing_m": round(sr, 6)}

    def resolve_looks(self) -> None:
        """resolution_m -> integer looks. Only the looks are ever recorded or named: the metres depend on the AOI and
        the granule, so two cases could carry the same '40 m' label and not be the same product."""
        res = self.c["resolution_m"]
        if res is None:
            return
        res = float(res)
        if res <= 0:
            raise CaseErr(f"resolution_m must be positive (got {res:g})")
        if self.coreg_mode == "GSLC":
            post = float(self.c["gslc"]["posting_m"])
            n = res / post
            if abs(n - round(n)) > 1e-9 or round(n) < 1:
                near = sorted({max(1, int(n)) * post, (int(n) + 1) * post})
                raise CaseErr(f"resolution_m {res:g} is not a whole number of {post:g} m grid cells ({n:.3f} looks). "
                              f"Nearest legal values: {' or '.join(f'{x:g}' for x in near)} m")
            got = {"looks_y": int(round(n)), "looks_x": int(round(n))}
            given = {k: v for k, v in (self.user.get("gslc") or {}).items() if k in got}
            if given and given != {k: got[k] for k in given}:
                raise CaseErr(f"resolution_m {res:g} resolves to {got}, but the config also sets {given}; "
                              f"give one or the other, or make them agree")
            self.c["gslc"].update(got)
            self.derived = {"resolution_m": res, **got, "posting_m": post}
            return
        az_m, rg_m, how = self.ground_spacing()
        got = {"azimuth": max(1, int(res / az_m + 0.5)), "range": max(1, int(res / rg_m + 0.5))}    # half-up
        if "looks" in self.user and self.user["looks"] != got:
            raise CaseErr(f"resolution_m {res:g} resolves to looks {got}, but the config also sets "
                          f"{self.user['looks']}; give one or the other, or make them agree")
        self.c["looks"] = got
        self.derived = {"resolution_m": res, **got, **how,
                        "actual_azimuth_m": round(got["azimuth"] * az_m, 2),
                        "actual_ground_range_m": round(got["range"] * rg_m, 2)}

    def abs(self, p) -> Path:
        q = Path(str(p)).expanduser()
        return q if q.is_absolute() else (self.dir / q)

    # ---- the two generated module configs ------------------------------------------------------
    def coreg_raw(self) -> dict:
        c, lk = self.c, self.c["looks"]
        raw = {
            "case": self.name, "workdir": str(self.dir), "mode": self.coreg_mode, "crop": self.crop,
            "aoi_kml": str(self.aoi_kml) if self.aoi_kml else None,
            "reference_date": c["dates"]["reference"], "start_date": c["dates"]["start"], "end_date": c["dates"]["end"],
            "tag": c["tag"],
            "inputs": {"rslc_dir": os.path.relpath(self.slc_dir, self.dir), **c["inputs"]},
        }
        if self.coreg_mode == "RSLC":
            # the crop window is snapped to the looks the interferogram will use, so the two lattices agree. A GSLC
            # interferogram multilooks the map grid instead, so there is no radar lattice to register and the
            # subsetter keeps its own defaults.
            raw["crop_buffers"] = {"align_az_looks": int(lk["azimuth"]), "align_rg_looks": int(lk["range"])}
        if c["envs"]["isce3_python"]:
            raw["envs"] = {"isce3_python": c["envs"]["isce3_python"]}
        if self.corr["ionosphere"] == "split_spectrum":
            # our own screen: ISCE3 unwraps on the same lattice the interferogram uses, so the time series can slice
            # the screen straight onto its grid instead of interpolating it
            raw.setdefault("rslc", {})
            raw["rslc"] |= {"ionosphere": {"enabled": True},
                            "unwrap": {"enabled": True, "azimuth": int(lk["azimuth"]), "range": int(lk["range"])}}
        if self.coreg_mode == "GSLC":
            g = c["gslc"]
            raw["gslc"] = {"posting_m": g["posting_m"], "frequencies": [c["inputs"]["frequency"]],
                           "interferogram": {"enabled": self.mode == "interferogram",
                                             "looks_y": int(g["looks_y"]), "looks_x": int(g["looks_x"]),
                                             "pairs": [str(p) for p in c["network"]["pairs"]]}}
        return NC.deep_merge(raw, c["advanced"]["coreg"] or {})

    def ts_raw(self) -> dict:
        c = self.c
        raw = {
            "coreg_config": str(self.coreg_cfg), "name": self.ts_name, "aoi_kml": str(self.aoi_kml),
            "start_date": c["dates"]["start"], "end_date": c["dates"]["end"], "reference_lalo": None, "tag": c["tag"],
            "looks": {"azimuth": int(c["looks"]["azimuth"]), "range": int(c["looks"]["range"])},
            "network": dict(c["network"]),
            "corrections": dict(self.corr),
        }
        envs = {k: v for k, v in c["envs"].items() if v}
        if envs:
            raw["envs"] = envs
        return NC.deep_merge(raw, c["advanced"]["timeseries"] or {})

    def write_configs(self, dry=False) -> None:
        """Render the two module configs. A dry run writes them to a scratch directory instead of the case tree, so
        what it validates is this case.yaml and never a stale _run/ left by an earlier command."""
        if dry and self._tmp is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="run_case_")
            self.coreg_cfg, self.ts_cfg = Path(self._tmp.name) / "coreg.yaml", Path(self._tmp.name) / "timeseries.yaml"
            log(f"  dry run: the generated configs go to {self._tmp.name}, not {self.run_dir}")
        for path, raw, want in ((self.coreg_cfg, self.coreg_raw(), True),
                                (self.ts_cfg, self.ts_raw(), self.needs_ts())):
            if not want:
                continue
            text = (f"# generated by run_case.py from {self.path} -- edit the case config, not this file\n"
                    + yaml.safe_dump(raw, sort_keys=False, default_flow_style=None))
            path.parent.mkdir(parents=True, exist_ok=True)
            if not (path.exists() and path.read_text() == text):
                path.write_text(text)

    def needs_ts(self) -> bool:
        return self.coreg_mode == "RSLC" and self.mode == "interferogram"

    # ---- planning: the modules' own classes resolve dates, names and output paths ---------------
    def coreg(self) -> NC.Coreg:
        if self._cg is None:
            if not self.coreg_cfg.exists():
                self.write_configs()
            self._cg = NC.Coreg(self.coreg_cfg)
        return self._cg

    def ts(self) -> NT.Ts:
        if self._ts is None:
            if not self.ts_cfg.exists():
                self.write_configs()
            self._ts = NT.Ts(self.ts_cfg)
        return self._ts

    def python(self) -> str:
        return str(self.c["envs"]["isce3_python"]
                   or yaml.safe_load(NC.DEFAULTS.read_text())["envs"]["isce3_python"] or sys.executable)

    # ---- what to run ---------------------------------------------------------------------------
    def steps(self, a) -> list[tuple[str, list[str]]]:
        py, out = self.python(), []
        common = ["--force"] if a.force else []
        common += ["--jobs", str(a.jobs)] if a.jobs else []
        out.append(("coreg", [py, "-u", str(HERE / "nisar_coreg.py"), "-c", str(self.coreg_cfg), "run"] + common))
        if self.needs_ts():
            ts = [py, "-u", str(HERE / "nisar_timeseries.py"), "-c", str(self.ts_cfg), "run"]
            out.append(("timeseries", ts + ["--through", "ifg"] + (["--force"] if a.force else [])))
            if any(v != "none" for v in self.corr.values()):
                # the corrections stage needs the geometry and the screens, not the unwrapped phase, so it is run on
                # its own rather than by widening --through (which would pull in the expensive unwrap). The screens
                # are delivered beside the interferograms; the time series is what subtracts them.
                out.append(("corrections", ts + ["--stage", "corrections"] + (["--force"] if a.force else [])))
        return out

    # ---- what a finished run leaves behind, and what gets pushed -------------------------------
    def products(self) -> list[Path]:
        """The paths this workflow x mode produces, in the order they are uploaded. Scratch is never included."""
        # always the real _run/ paths: on a dry run self.coreg_cfg points into a scratch directory, which is not
        # under the case directory and has no place in an upload listing
        cg, items = self.coreg(), [self.run_dir / "coreg.yaml"]
        if self.coreg_mode == "RSLC":
            keep = ["slc", "geometry", "offsets", "ifg", "status", "logs", "params.json", "stack_manifest.json", "track_r.yaml"]
            items += [cg.stack_dir / k for k in keep]
            if self.mode == "interferogram":
                items = [self.run_dir / "coreg.yaml", self.run_dir / "timeseries.yaml", self.ts().out,
                         cg.stack_dir / "params.json", cg.stack_dir / "stack_manifest.json"]
                if self.corr["ionosphere"] == "split_spectrum":
                    items += [cg.stack_dir / "runw"]      # our own screens, the input to the correction
        else:
            keep = ["gslc", "status", "logs", "params.json", "stack_manifest.json", "track_g.yaml"]
            items += [cg.stack_dir / k for k in keep]
            if self.mode == "interferogram":
                items += [cg.isce_root / "pairs"]
        return items

    def destination(self) -> str | None:
        d = self.c["upload"]["destination"]
        return f"{str(d).rstrip('/')}/{self.name}" if d else None


# ============================================================================ credentials
def ensure_netrc(spec, dry=False, force=False) -> None:
    """nisar_wf/dem.py reads ~/.netrc through expanduser, so the file has to be in $HOME; a path cannot be passed."""
    home = Path.home() / ".netrc"
    if spec is None:
        if not home.exists():
            log(f"  note: {home} is missing; DEM staging from urs.earthdata.nasa.gov will fail (set netrc: in the case config)")
        return
    if home.exists() and not force:
        log(f"  netrc: {home} already present, keeping it (--force-netrc to replace it from {spec})")
        return
    if dry:
        log(f"  would install {spec} as {home} (mode 600)")
        return
    src = str(spec)
    if src.startswith("gs://"):
        sh(["gcloud", "storage", "cp", src, str(home)])
    else:
        shutil.copyfile(Path(src).expanduser(), home)
    home.chmod(0o600)
    if "urs.earthdata.nasa.gov" not in home.read_text():
        log(f"  warning: {home} has no urs.earthdata.nasa.gov entry; the NISAR DEM source needs one")
    log(f"  netrc: installed {src} -> {home} (600)")


# ============================================================================ upload
def upload(case: Case, dry=False) -> int:
    dest = case.destination()
    if not dest:
        log("upload: no upload.destination in the case config; products stay local")
        return EXIT_OK
    missing, total, rc = [], 0, EXIT_OK
    for p in case.products():
        if not p.exists():
            missing.append(str(p.relative_to(case.dir)))
            continue
        rel = p.relative_to(case.dir)
        target = f"{dest}/{rel}"
        size = du(p)
        total += size
        log(f"  {rel}  {size / 1e9:.2f} GB -> {target}")
        cmd = (["gcloud", "storage", "cp", str(p), target] if p.is_file()
               else ["gcloud", "storage", "rsync", "-r", str(p), target])
        if sh(cmd, dry=dry, check=False) != 0:
            rc = EXIT_FAIL
    if missing:
        log(f"  not uploaded, this workflow has not produced them yet: {missing}")
        rc = rc or EXIT_PREREQ      # an upload that silently skips the product is worse than a non-zero exit
    log(f"upload: {total / 1e9:.2f} GB to {dest}" + (" (dry run)" if dry else ""))
    if not dry and rc == EXIT_OK:
        r = subprocess.run(["gcloud", "storage", "du", "-s", dest], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.split():
            log(f"  remote total now {int(r.stdout.split()[0]) / 1e9:.2f} GB at {dest}")
    return rc


# ============================================================================ commands
def cmd_show(case: Case, a) -> int:
    c = case.c
    lk, g = c["looks"], c["gslc"]
    print(f"case         {case.name}   ({case.dir})")
    print(f"workflow     {case.workflow}  ->  nisar_coreg mode {case.coreg_mode}, crop {'on' if case.crop else 'off'}")
    print(f"mode         {case.mode}" + ("   (GSLC: 'coregistration' means geocoding; the dates are not registered to each other)"
                                         if case.coreg_mode == "GSLC" else ""))
    print(f"SLCs         {case.slc_dir}")
    print(f"AOI          {case.aoi_kml}")
    if case.coreg_mode == "RSLC":
        print(f"looks        {lk['azimuth']} az x {lk['range']} rg  (interferogram, and the lattice the crop is snapped to)")
    else:
        res_y, res_x = g["posting_m"] * g["looks_y"], g["posting_m"] * g["looks_x"]
        print(f"posting      {g['posting_m']} m map grid; interferogram looks {g['looks_y']}y x {g['looks_x']}x "
              f"-> {res_y:g} m x {res_x:g} m on the ground")
    if case.derived:
        d = case.derived
        if case.coreg_mode == "RSLC":
            print(f"resolution   {d['resolution_m']:g} m asked -> {d['azimuth']} az x {d['range']} rg looks "
                  f"= {d['actual_azimuth_m']:g} m x {d['actual_ground_range_m']:g} m "
                  f"(incidence {d['incidence_deg']:g} deg over {d['geolocation_nodes']} AOI nodes, "
                  f"{d['incidence_min_deg']:g}-{d['incidence_max_deg']:g} across it)")
        else:
            print(f"resolution   {d['resolution_m']:g} m asked -> {d['looks_y']} x {d['looks_x']} looks "
                  f"on the {d['posting_m']:g} m grid")
    src = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in case.corr.items() if v != "none") or "off"
    print(f"corrections  {src}")
    print(f"netrc        {c['netrc'] or '(not managed here; ~/.netrc must already exist)'}")
    print(f"upload       {case.destination() or '(local only)'}")
    print(f"\ngenerated configs\n  {case.coreg_cfg}" + (f"\n  {case.ts_cfg}" if case.needs_ts() else ""))
    case.write_configs(dry=a.dry_run)
    print("\n--- nisar_coreg show ---------------------------------------------------------")
    sys.stdout.flush()
    rc = NC.main(["-c", str(case.coreg_cfg), "show"])
    if rc != EXIT_OK:
        return rc                      # the module already said what is wrong; do not repeat it from here
    if case.needs_ts():
        print("\n--- nisar_timeseries show ----------------------------------------------------")
        sys.stdout.flush()
        rc = NT.main(["-c", str(case.ts_cfg), "show"])
        if rc != EXIT_OK:
            return rc
    print("\nsteps")
    for name, cmd in case.steps(a):
        print(f"  {name:11s} {' '.join(shlex.quote(x) for x in cmd)}")
    print("products" + (f" (uploaded to {case.destination()})" if case.destination() else " (kept local)"))
    for p in case.products():
        print(f"  {p.relative_to(case.dir)}")
    return rc


def cmd_status(case: Case, a) -> int:
    case.write_configs()
    rc = NC.main(["-c", str(case.coreg_cfg), "status"])
    if case.needs_ts():
        print()
        rc = NT.main(["-c", str(case.ts_cfg), "status"]) or rc
    print("\nproducts")
    for p in case.products():
        print(f"  {'ok  ' if p.exists() else '--  '}{p.relative_to(case.dir)}  {du(p) / 1e9:.2f} GB")
    return rc


def cmd_run(case: Case, a) -> int:
    log(f"run_case {case.name}: {case.workflow} / {case.mode} (git {NC.git_rev()})")
    case.write_configs(dry=a.dry_run)
    ensure_netrc(case.c["netrc"], dry=a.dry_run, force=a.force_netrc)
    for name, cmd in case.steps(a):
        log(f"  step {name}")
        rc = sh(cmd + (["--dry-run"] if a.dry_run else []), check=False)
        if rc != EXIT_OK:
            log(f"stopped at step {name} (exit {rc}); the module's logs say why")
            return rc
    if a.no_upload or not case.c["upload"]["enabled"]:
        log("upload: skipped")
        return EXIT_OK
    rc = upload(case, dry=a.dry_run)
    # a dry run has produced nothing, so "the products are not there yet" is the expected state, not a failure
    return EXIT_OK if (a.dry_run and rc == EXIT_PREREQ) else rc


def detach(argv, case: Case) -> int:
    session = re.sub(r"[^A-Za-z0-9_-]", "_", f"case_{case.name}_{case.mode}")[:60]
    if subprocess.run(["tmux", "has-session", "-t", session], capture_output=True).returncode == 0:
        log(f"tmux session {session} already running; attach: tmux attach -t {session}")
        return EXIT_PREREQ
    logf = case.dir / "_run" / f"run_{NC.stamp()}.detached.log"
    logf.parent.mkdir(parents=True, exist_ok=True)
    inner = " ".join(shlex.quote(x) for x in [case.python(), "-u", str(Path(__file__).resolve())]
                     + [x for x in argv if x != "--detach"])
    subprocess.run(["tmux", "new-session", "-d", "-s", session,
                    f"{inner} > {shlex.quote(str(logf))} 2>&1; echo EXIT=$? >> {shlex.quote(str(logf))}"], check=True)
    log(f"started: tmux session {session}\n  attach: tmux attach -t {session}\n  log:    {logf}")
    return EXIT_OK


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", required=True, type=Path, help="case_studies/<NAME>/case.yaml")
    ap.add_argument("command", choices=["show", "status", "run", "upload"])
    ap.add_argument("--dry-run", action="store_true", help="resolve and print everything, run nothing")
    ap.add_argument("--detach", action="store_true", help="run inside a tmux session and return immediately")
    ap.add_argument("--no-upload", action="store_true", help="run but keep the products local")
    ap.add_argument("--force", action="store_true", help="passed to the modules: redo units already verified done")
    ap.add_argument("--force-netrc", action="store_true", help="replace ~/.netrc from the configured source")
    ap.add_argument("--jobs", type=int, default=None, help="units in parallel (default: the module's own setting)")
    a = ap.parse_args(argv)
    try:
        case = Case(a.config)
    except (CaseErr, NC.ConfigErr) as e:
        print(e, file=sys.stderr)
        return EXIT_CONFIG
    argv = [str(case.path) if x == str(a.config) else x for x in argv]
    try:
        if a.command == "show":
            return cmd_show(case, a)
        if a.command == "status":
            return cmd_status(case, a)
        if a.command == "upload":
            return upload(case, dry=a.dry_run)
        if a.detach:
            return detach(argv, case)
        return cmd_run(case, a)
    except (CaseErr, NC.ConfigErr, NT.ConfigErr) as e:
        print(e, file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
