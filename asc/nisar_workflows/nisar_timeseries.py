#!/usr/bin/env python3
"""
NISAR time-series module: a coregistered RSLC stack (nisar_coreg.py) in, LOS displacement time series and velocity out.

    python nisar_timeseries.py -c ts_configs/<case>.yaml show        # dates, pairs, GUNW coverage, parameters; runs nothing
    python nisar_timeseries.py -c ts_configs/<case>.yaml status
    python nisar_timeseries.py -c ts_configs/<case>.yaml run --dry-run
    python nisar_timeseries.py -c ts_configs/<case>.yaml run --detach
    python nisar_timeseries.py -c ts_configs/<case>.yaml run --stage unwrap --pairs 20260620_20260702
    options: --force (redo verified units), --dry-run, --detach

Case config (ts_configs/<case>.yaml)
------------------------------------
    coreg_config        the nisar_coreg.py case config whose stack is used (RSLC mode)
    name                output name: <workdir>/timeseries/<stack>/<name>[_<tag>]/
    aoi_kml             unwrap and time-series area (radar window around its bounding box); null = the coreg AOI
    start_date/end_date dates of the stack to use (null = no bound)
    reference_lalo      [lat, lon] MintPy reference point; null = automatic (highest coherence)
    tag                 optional suffix, needed to change a parameter of an existing output
Parameters: ts_configs/defaults.yaml.

Stages (each runs in the conda environment that has its libraries)
------------------------------------------------------------------
geometry     isce3   radar window around the AOI; multilooked lat/lon/height, incidence and azimuth angles, slant range
                     (MintPy geometryRadar.h5); perpendicular baselines; per-date flattening range offsets, recomputed
                     with isce3 Geo2Rdr exactly as ISCE3 insar's geo2rdr step (the coreg module prunes its copy)
ifg          ts      per pair: s1 * conj(s2) * exp(-i 4 pi/lambda dr (off2 - off1)), multilooked, and its coherence.
                     Pairs that include the stack reference are checked against ISCE3's RIFG (gate qa.max_rifg_phase_diff_rad)
unwrap       ts      per pair: snaphu with tiles and overlap; then an unwrapping-error census over all closed triplets
corrections  ts      GUNW ionosphere, troposphere (wet + hydrostatic) and solid-earth-tide screens sampled at every pixel
                     (3-D cubes at the pixel height), then converted from date pairs to per-date screens by least squares
mintpy       ts      ifgramStack.h5 -> reference point -> [unwrap-error correction] -> network inversion -> subtract
                     SET, ionosphere, troposphere -> [DEM error] -> velocity -> geocode -> GeoTIFFs in export/

Sign convention: interferograms are s1 * conj(s2) (as ISCE3 and ISCE2), the convention MintPy's isce loader assumes and the
GUNW screens use, so the screens are subtracted. MintPy's LOS displacement is positive toward the satellite.

Job semantics as nisar_coreg.py: manifests per unit, verified units skipped, no overwrite without --force, exit codes
0 ok, 1 failed, 2 config error, 3 missing prerequisite.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import itertools
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from nisar_coreg import (EXIT_CONFIG, EXIT_FAIL, EXIT_OK, EXIT_PREREQ, ConfigErr, Coreg, Log, deep_merge,  # noqa: E402
                         git_rev, kml_bbox, norm_date, now, read_json, sha1_file, sizeof, stamp, unit_done,
                         unknown_keys, write_json)

DEFAULTS = HERE / "ts_configs" / "defaults.yaml"
CASE_KEYS = ("coreg_config", "name", "aoi_kml", "start_date", "end_date", "reference_lalo", "tag")
STAGES = ("geometry", "ifg", "unwrap", "corrections", "mintpy")
STAGE_ENV = {"geometry": "isce3_python", "ifg": "ts_python", "unwrap": "ts_python", "corrections": "ts_python", "mintpy": "ts_python"}
C_LIGHT = 299792458.0
FLATTEN_SIGN = -1.0      # ifg = s1 conj(s2) exp(FLATTEN_SIGN * 1j * 4 pi / lambda * dr * (off2 - off1)); verified by the RIFG gate


# ============================================================================ small raster helpers
ENVI_DTYPES = {1: "uint8", 2: "int16", 4: "float32", 5: "float64", 6: "complex64", 12: "uint16", 13: "uint32"}
ENVI_CODES = {v: k for k, v in ENVI_DTYPES.items()}


def envi_header(path: Path) -> dict:
    hdr = path.with_suffix(".hdr") if path.with_suffix(".hdr").exists() else Path(str(path) + ".hdr")
    kv = {}
    for line in hdr.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    return {"lines": int(kv["lines"]), "samples": int(kv["samples"]), "dtype": ENVI_DTYPES[int(kv["data type"])],
            "offset": int(kv.get("header offset", 0)), "byte order": int(kv.get("byte order", 0))}


def envi_memmap(path: Path, mode="r"):
    import numpy as np
    h = envi_header(path)
    assert h["byte order"] == 0, f"{path}: big-endian ENVI not supported"
    return np.memmap(path, dtype=h["dtype"], mode=mode, offset=h["offset"], shape=(h["lines"], h["samples"]))


def write_envi(path: Path, arr, description: str = ""):
    import numpy as np
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".part")
    np.ascontiguousarray(arr).astype(arr.dtype.newbyteorder("<"), copy=False).tofile(tmp)
    os.replace(tmp, path)
    path.with_suffix(".hdr").write_text(
        f"ENVI\ndescription = {{{description}}}\nsamples = {arr.shape[1]}\nlines   = {arr.shape[0]}\nbands   = 1\n"
        f"header offset = 0\nfile type = ENVI Standard\ndata type = {ENVI_CODES[str(arr.dtype)]}\ninterleave = bsq\nbyte order = 0\n")


def kml_polygon(kml: Path):
    txt = kml.read_text()
    block = re.findall(r"<coordinates>(.*?)</coordinates>", txt, re.S)[0]
    return [tuple(map(float, t.split(",")[:2])) for t in block.split()]


# ============================================================================ config
def build_pairs(dates: list[str], net: dict) -> list[tuple[str, str]]:
    if net.get("pairs"):
        pairs = []
        for p in net["pairs"]:
            d1, d2 = str(p).split("_")
            if d1 not in dates or d2 not in dates or d1 >= d2:
                raise ConfigErr(f"network.pairs: {p} is not an ordered pair of selected dates {dates}")
            pairs.append((d1, d2))
        return sorted(set(pairs))
    n = int(net.get("max_connections") or len(dates))
    maxbt = net.get("max_temporal_baseline_days")
    days = lambda a, b: (dt.datetime.strptime(b, "%Y%m%d") - dt.datetime.strptime(a, "%Y%m%d")).days  # noqa: E731
    return [(dates[i], dates[j]) for i in range(len(dates)) for j in range(i + 1, min(len(dates), i + 1 + n))
            if maxbt is None or days(dates[i], dates[j]) <= maxbt]


def connected(dates: list[str], pairs) -> bool:
    import numpy as np
    if len(dates) < 2:
        return True
    idx = {d: k for k, d in enumerate(dates)}
    A = np.zeros((len(pairs), len(dates)))
    for r, (a, b) in enumerate(pairs):
        A[r, idx[a]], A[r, idx[b]] = -1, 1
    return len(pairs) > 0 and np.linalg.matrix_rank(A[:, 1:]) == len(dates) - 1


class Ts:
    def __init__(self, path: Path):
        self.path = path.resolve()
        user = yaml.safe_load(self.path.read_text()) or {}
        defaults = yaml.safe_load(DEFAULTS.read_text())
        over = {k: v for k, v in user.items() if k not in CASE_KEYS}
        errs = []
        bad = unknown_keys(over, defaults)
        if bad:
            errs.append(f"unknown key(s) {bad}; case keys are {list(CASE_KEYS)}, overridable sections {list(defaults)}")
        for k in ("coreg_config", "name"):
            if not user.get(k):
                errs.append(f"{k} is required")
        if errs:
            raise ConfigErr("time-series config errors:\n  " + "\n  ".join(errs))
        self.eff = eff = deep_merge(defaults, over)
        self.overrides = over
        cpath = Path(user["coreg_config"])
        self.cg = cg = Coreg(cpath if cpath.is_absolute() else HERE / cpath)
        if cg.mode != "RSLC":
            errs.append("the coregistered stack must be mode RSLC (GSLC dates are not registered to each other)")
        self.name = str(user["name"])
        self.tag = str(user["tag"]) if user.get("tag") else None
        for k, v in (("name", self.name), ("tag", self.tag)):
            if v is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", v):
                errs.append(f"{k} may contain only letters, digits, '_' and '-' (got {v!r})")
        self.aoi_kml = Path(user["aoi_kml"]).resolve() if user.get("aoi_kml") else cg.aoi_kml
        if self.aoi_kml is None or not self.aoi_kml.exists():
            errs.append(f"aoi_kml not found: {self.aoi_kml}")
        try:
            start, end = norm_date(user.get("start_date"), "start_date"), norm_date(user.get("end_date"), "end_date")
        except ValueError as e:
            errs.append(str(e))
            start = end = None
        self.start_date, self.end_date = start, end
        self.dates = [d for d in sorted([cg.reference] + cg.secondaries) if (start is None or d >= start) and (end is None or d <= end)]
        if len(self.dates) < 2:
            errs.append(f"need at least two stack dates between {start} and {end}; stack has {sorted([cg.reference] + cg.secondaries)}")
        rl = user.get("reference_lalo")
        if rl is not None and not (isinstance(rl, (list, tuple)) and len(rl) == 2):
            errs.append("reference_lalo must be [lat, lon] or null")
        self.reference_lalo = [float(rl[0]), float(rl[1])] if rl is not None else None
        try:
            self.pairs = build_pairs(self.dates, eff["network"]) if len(self.dates) >= 2 else []
        except ConfigErr as e:
            errs.append(str(e))
            self.pairs = []
        if self.pairs and not connected(self.dates, self.pairs):
            errs.append(f"the interferogram network does not connect all dates: {self.pairs}")
        if errs:
            raise ConfigErr("time-series config errors:\n  " + "\n  ".join(errs))
        self.az_looks, self.rg_looks = int(eff["looks"]["azimuth"]), int(eff["looks"]["range"])
        self.out = cg.workdir / "timeseries" / cg.stack_id / (self.name + (f"_{self.tag}" if self.tag else ""))
        self.logdir = self.out / "logs"
        self.gunw_dir = cg.workdir / eff["corrections"]["gunw_dir"]

    def params(self) -> dict:
        e = self.eff
        return {"stack": self.cg.stack_id, "reference": self.cg.reference, "dates": self.dates, "pairs": [f"{a}_{b}" for a, b in self.pairs],
                "aoi_kml_sha1": sha1_file(self.aoi_kml), "aoi": e["aoi"], "looks": e["looks"], "unwrap": e["unwrap"],
                "corrections": e["corrections"], "flatten_sign": FLATTEN_SIGN}
        # The mintpy section is deliberately NOT locked: that stage is a re-derivation from the locked pair products, it is
        # regenerated as a whole (the previous run is moved to mintpy_replaced_<stamp>), and the settings it used are recorded
        # in its own manifest, in mintpy/mintpy.cfg and in qa/mintpy.json.
        # reference_lalo is deliberately NOT locked: it changes only the MintPy stage, which is regenerated as a whole
        # (the previous run is moved to mintpy_replaced_<stamp>) and records the point it used in its manifest and QA.

    # paths
    def d12(self, p):
        return f"{p[0]}_{p[1]}"

    def manifest(self, stage, unit=None):
        return self.out / "status" / (f"{stage}_{unit}.json" if unit else f"{stage}.json")

    def slc(self, d):
        return self.cg.stack_dir / "slc" / f"{d}.slc"

    def flatten_file(self, d):
        return self.out / "geometry" / "flatten" / f"{d}_range_offset.f32"

    def pair_dir(self, p):
        return self.out / "pairs" / self.d12(p)

    def rifg(self, p):
        ref = self.cg.reference
        if ref not in p:
            return None, False
        sec = p[1] if p[0] == ref else p[0]
        return self.cg.stack_dir / "ifg" / f"RIFG_{ref}_{sec}_{self.cg.pair_tag()}.h5", p[0] != ref   # (path, conjugate?)

    def gunw_pairs(self) -> dict:
        """(d1, d2) -> GUNW file, for GUNWs whose two dates are both time-series dates; tier by preference."""
        found = {}
        pref = self.eff["corrections"]["tier_preference"]
        for f in sorted(self.gunw_dir.glob("NISAR_L2_*_GUNW_*.h5")):
            m = re.match(r"NISAR_L2_(\w\w)_GUNW_.*?_(\d{8})T\d{6}_\d{8}T\d{6}_(\d{8})T\d{6}_\d{8}T\d{6}_", f.name)
            if not m or m.group(1) not in pref:
                continue
            tier, d1, d2 = m.groups()
            if d1 in self.dates and d2 in self.dates:
                cur = found.get((d1, d2))
                if cur is None or pref.index(tier) < pref.index(cur[0]):
                    found[(d1, d2)] = (tier, f)
        return {k: v[1] for k, v in sorted(found.items())}

    def any_correction(self):
        c = self.eff["corrections"]
        return c["ionosphere"] or c["troposphere"] or c["solid_earth_tides"]


def params_gate(ts: Ts, log: Log, dry_run: bool) -> bool:
    pfile = ts.out / "params.json"
    cur = json.loads(json.dumps(ts.params(), default=str))
    old = read_json(pfile)
    if old is None:
        if not dry_run:
            write_json(pfile, cur)
        return True
    if old == cur:
        return True
    # Keys the current schema no longer locks (e.g. settings moved to a re-derived stage) are not a mismatch: compare on the
    # keys this version records, and rewrite the file so the recorded schema follows the code.
    diffs = [k for k in sorted(cur) if old.get(k, cur[k]) != cur[k]]
    if not diffs:                       # same values under the current schema: migrate the file, do not refuse
        if not dry_run:
            write_json(pfile, cur)
        return True
    log(f"parameters differ from {pfile} in {diffs}; refusing to mix outputs. Use a different `name` or set `tag:`.")
    return False


# ============================================================================ show / status
def cmd_show(ts: Ts, a) -> int:
    cg = ts.cg
    print(f"stack        {cg.stack_dir}\nreference    {cg.reference} (stack reference grid)")
    print(f"dates        {len(ts.dates)}: {' '.join(ts.dates)}")
    days = lambda p: (dt.datetime.strptime(p[1], "%Y%m%d") - dt.datetime.strptime(p[0], "%Y%m%d")).days  # noqa: E731
    print(f"pairs        {len(ts.pairs)}: " + ", ".join(f"{ts.d12(p)} ({days(p)} d)" for p in ts.pairs))
    print(f"AOI          {ts.aoi_kml} (+{ts.eff['aoi']['margin_m']} m)")
    print(f"looks        {ts.az_looks} az x {ts.rg_looks} rg")
    g = ts.gunw_pairs()
    print("GUNW pairs   " + (", ".join(f"{a}_{b} ({f.name.split('_')[2]})" for (a, b), f in g.items()) or "none")
          + f"   -> {'connects all dates' if connected(ts.dates, list(g)) else 'DOES NOT connect all dates'}")
    print(f"reference pt {ts.reference_lalo or 'automatic'}")
    print(f"output       {ts.out}")
    missing = [d for d in ts.dates if d != cg.reference and not unit_done(cg.manifest(d))]
    print(f"coreg ready  {'yes' if not missing else 'NO, unfinished: ' + ' '.join(missing)}")
    print("\nparameters in effect (ts_configs/defaults.yaml" + (f", overridden: {sorted(ts.overrides)})" if ts.overrides else ")"))
    print(yaml.safe_dump({k: ts.eff[k] for k in ("aoi", "looks", "network", "unwrap", "corrections", "mintpy", "qa")},
                         sort_keys=False, default_flow_style=None).rstrip())
    return EXIT_OK


def state(man: Path) -> str:
    if unit_done(man):
        return "done"
    return {"failed": "FAILED", "running": "running"}.get((read_json(man) or {}).get("status"), "-")


def cmd_status(ts: Ts, a) -> int:
    print(f"output {ts.out}")
    print(f"geometry {state(ts.manifest('geometry'))}   corrections {state(ts.manifest('corrections'))}   mintpy {state(ts.manifest('mintpy'))}")
    print(f"{'pair':18s} {'ifg':8s} {'unwrap':8s}")
    for p in ts.pairs:
        print(f"{ts.d12(p):18s} {state(ts.manifest('ifg', ts.d12(p))):8s} {state(ts.manifest('unwrap', ts.d12(p))):8s}")
    return EXIT_OK


# ============================================================================ stage: geometry (isce3 env)
def stage_geometry(ts: Ts, a, log: Log) -> int:
    man = ts.manifest("geometry")
    if unit_done(man) and not a.force:
        log("geometry: done, skip")
        return EXIT_OK
    import h5py
    import isce3
    import numpy as np
    from nisar.products.readers import SLC

    cg, az, rg = ts.cg, ts.az_looks, ts.rg_looks
    t0 = time.time()
    write_json(man, {"stage": "geometry", "status": "running", "started": now(), "host": socket.gethostname()})
    gdir = ts.out / "geometry"
    gdir.mkdir(parents=True, exist_ok=True)
    geo = {n: envi_memmap(cg.stack_dir / "geometry" / f"{n}.rdr") for n in ("lon", "lat", "hgt")}
    L, W = geo["lon"].shape
    ref_slc = SLC(hdf5file=str(cg.stack_input(cg.reference)))
    rgrid, orbit = ref_slc.getRadarGrid(cg.frequency), ref_slc.getOrbit()
    assert (rgrid.length, rgrid.width) == (L, W), f"geometry {L}x{W} does not match the reference radar grid {rgrid.length}x{rgrid.width}"
    wavelength, dr, prf = rgrid.wavelength, rgrid.range_pixel_spacing, rgrid.prf
    epoch_shift = (dt.datetime.fromisoformat(str(rgrid.ref_epoch)[:26]) - dt.datetime.fromisoformat(str(orbit.reference_epoch)[:26])).total_seconds()

    # radar window around the AOI bbox (+margin), in whole look cells
    nL, nW = L // az, W // rg
    rows_c, cols_c = np.arange(nL) * az + az // 2, np.arange(nW) * rg + rg // 2
    lon_c = np.asarray(geo["lon"][rows_c][:, cols_c])
    lat_c = np.asarray(geo["lat"][rows_c][:, cols_c])
    w_, s_, e_, n_ = kml_bbox(ts.aoi_kml)
    m = float(ts.eff["aoi"]["margin_m"])
    dlat, dlon = m / 111320.0, m / (111320.0 * np.cos(np.deg2rad((s_ + n_) / 2)))
    sel = (lon_c >= w_ - dlon) & (lon_c <= e_ + dlon) & (lat_c >= s_ - dlat) & (lat_c <= n_ + dlat)
    if not sel.any():
        log("geometry: the AOI does not overlap the stack")
        write_json(man, {"stage": "geometry", "status": "failed", "error": "AOI outside stack", "finished": now()})
        return EXIT_FAIL
    rr, cc = np.where(sel.any(axis=1))[0], np.where(sel.any(axis=0))[0]
    r0, r1, c0, c1 = int(rr[0]), int(rr[-1]) + 1, int(cc[0]), int(cc[-1]) + 1
    R0, R1, C0, C1 = r0 * az, r1 * az, c0 * rg, c1 * rg
    ml_shape = (r1 - r0, c1 - c0)
    full_covered = bool(sel[r0:r1, c0:c1].mean() > 0)
    log(f"geometry: window rows {R0}:{R1} cols {C0}:{C1} (full res) -> {ml_shape[0]} x {ml_shape[1]} multilooked; "
        f"{100 * sel[r0:r1, c0:c1].mean():.0f}% of it inside the AOI bbox + margin")

    # multilooked lon/lat/hgt
    ml = {n: np.empty(ml_shape, np.float64) for n in geo}
    for i in range(ml_shape[0]):
        for n, mm in geo.items():
            ml[n][i] = np.asarray(mm[R0 + i * az:R0 + (i + 1) * az, C0:C1], dtype=np.float64).reshape(az, ml_shape[1], rg).mean(axis=(0, 2))
    lon, lat, hgt = (np.deg2rad(ml["lon"]), np.deg2rad(ml["lat"]), ml["hgt"])

    # look vectors from the reference orbit
    a_e, e2 = 6378137.0, 6.69437999014e-3
    sl, cl, so, co = np.sin(lat), np.cos(lat), np.sin(lon), np.cos(lon)
    Nrad = a_e / np.sqrt(1 - e2 * sl ** 2)
    tgt = np.stack([(Nrad + hgt) * cl * co, (Nrad + hgt) * cl * so, (Nrad * (1 - e2) + hgt) * sl], axis=-1)
    t_rows = rgrid.sensing_start + (R0 + np.arange(ml_shape[0]) * az + (az - 1) / 2) / prf + epoch_shift
    sat = np.empty((ml_shape[0], 3))
    vel = np.empty((ml_shape[0], 3))
    for i, t in enumerate(t_rows):
        p, v = orbit.interpolate(float(t))
        sat[i], vel[i] = p, v
    los = sat[:, None, :] - tgt
    rng = np.linalg.norm(los, axis=-1)
    u = los / rng[..., None]
    up = np.stack([cl * co, cl * so, sl], axis=-1)
    east = np.stack([-so, co, np.zeros_like(so)], axis=-1)
    north = np.stack([-sl * co, -sl * so, cl], axis=-1)
    inc = np.rad2deg(np.arccos(np.clip((u * up).sum(-1), -1, 1)))
    azang = np.rad2deg(np.arctan2(-(u * east).sum(-1), (u * north).sum(-1)))
    srange_cols = rgrid.starting_range + (C0 + np.arange(ml_shape[1]) * rg + (rg - 1) / 2) * dr
    range_resid = float(np.nanmedian(np.abs(rng - srange_cols[None, :])))
    log(f"geometry: |orbit-to-pixel range - radar-grid range| median {range_resid:.2f} m (zero-Doppler consistency)")

    # scene centre: heading, altitude, perpendicular baselines (ISCE2 convention)
    ic, jc = ml_shape[0] // 2, ml_shape[1] // 2
    ve = vel[ic] @ east[ic, jc], vel[ic] @ north[ic, jc]
    heading = float(np.rad2deg(np.arctan2(ve[0], ve[1])))
    earth_radius = float(np.linalg.norm(tgt[ic, jc]))
    height = float(np.linalg.norm(sat[ic]) - earth_radius)
    llh_c = np.array([lon[ic, jc], lat[ic, jc], hgt[ic, jc]])
    g2r = cg.eff["rslc"]["geo2rdr"]
    bperp, dates_meta = {}, {}
    for d in ts.dates:
        s = SLC(hdf5file=str(cg.stack_input(d)))
        o, g = s.getOrbit(), s.getRadarGrid(cg.frequency)
        if d == cg.reference:
            bperp[d] = 0.0
        else:
            tt, rr_ = isce3.geometry.geo2rdr(llh_c, isce3.core.Ellipsoid(), o, isce3.core.LUT2d(), g.wavelength, g.lookside,
                                             float(g2r["threshold"]), int(g2r["maxiter"]))
            sd = np.asarray(o.interpolate(tt)[0])
            drng, bb = rng[ic, jc], np.linalg.norm(sd - sat[ic])
            cost = (drng ** 2 + bb ** 2 - rr_ ** 2) / (2 * drng * bb)
            direction = np.sign(np.dot(np.cross(tgt[ic, jc] - sat[ic], sd - sat[ic]), vel[ic]))
            bperp[d] = float(direction * bb * np.sqrt(max(0.0, 1 - cost ** 2)))
        dates_meta[d] = {"sensing_start": str(g.ref_epoch) + f" + {g.sensing_start:.6f} s", "input": str(cg.stack_input(d)),
                         "starting_range": float(g.starting_range)}
    log(f"geometry: perpendicular baselines at scene centre (m, vs {cg.reference}): " + ", ".join(f"{d} {b:+.1f}" for d, b in bperp.items()))

    # flattening range offsets (isce3 Geo2Rdr, as ISCE3 insar's geo2rdr step; the reference needs none)
    tmp = gdir / "_geo2rdr_tmp"
    vrt = tmp / "topo.vrt"
    tmp.mkdir(parents=True, exist_ok=True)
    bands = "".join(
        f'  <VRTRasterBand dataType="Float64" band="{k + 1}">\n    <SimpleSource>\n'
        f'      <SourceFilename relativeToVRT="0">{cg.stack_dir / "geometry" / (n + ".rdr")}</SourceFilename>\n'
        f'      <SourceBand>1</SourceBand>\n      <SrcRect xOff="0" yOff="0" xSize="{W}" ySize="{L}" />\n'
        f'      <DstRect xOff="0" yOff="0" xSize="{W}" ySize="{L}" />\n    </SimpleSource>\n  </VRTRasterBand>\n'
        for k, n in enumerate(("lon", "lat", "hgt")))
    vrt.write_text(f'<VRTDataset rasterXSize="{W}" rasterYSize="{L}">\n  <SRS dataAxisToSRSAxisMapping="2,1">EPSG:4326</SRS>\n{bands}</VRTDataset>\n')
    flatten_stats = {}
    for d in ts.dates:
        if d == cg.reference:
            continue
        s = SLC(hdf5file=str(cg.stack_input(d)))
        od = tmp / d
        od.mkdir(exist_ok=True)
        t1 = time.time()
        obj = isce3.geometry.Geo2Rdr(s.getRadarGrid(cg.frequency), s.getOrbit(), isce3.core.Ellipsoid(), isce3.core.LUT2d(),
                                     float(g2r["threshold"]), int(g2r["maxiter"]), 1000)
        obj.geo2rdr(isce3.io.Raster(str(vrt)), str(od))
        off = np.memmap(od / "range.off", dtype="<f8", mode="r", shape=(L, W))[R0:R1, C0:C1].astype(np.float32)
        write_envi(ts.flatten_file(d), off, f"geo2rdr range offset (pixels) of {d} on the {cg.reference} grid, window {R0}:{R1},{C0}:{C1}")
        flatten_stats[d] = {"seconds": round(time.time() - t1, 1), "min_px": float(np.nanmin(off)), "max_px": float(np.nanmax(off))}
        shutil.rmtree(od)
        log(f"geometry: flatten offsets {d}: {flatten_stats[d]}")
    shutil.rmtree(tmp)

    # MintPy geometryRadar.h5
    with h5py.File(ref_slc.filename, "r") as f:
        sw = f[f"science/LSAR/RSLC/swaths/frequency{cg.frequency}"]
        brg, baz = float(sw["processedRangeBandwidth"][()]), float(sw["processedAzimuthBandwidth"][()])
        az_sp = float(sw["sceneCenterAlongTrackSpacing"][()])
        ident = f["science/LSAR/identification"]
        look = ident["lookDirection"][()].decode().lower()
        pass_dir = ident["orbitPassDirection"][()].decode().upper()
    # effective looks per sample exactly as ISCE3 unwrap.get_effective_looks (the validated 9x8 unwrap used it): azimuth
    # resolution from the platform speed at sensing_mid, not the ground speed, so it is ~10% below the ground-based value
    v_mid = np.linalg.norm(np.asarray(orbit.interpolate(rgrid.sensing_mid + epoch_shift)[1]))
    nlooks_factor = dr * az_sp / ((C_LIGHT / (2 * brg)) * (v_mid / baz))
    epoch = dt.datetime.fromisoformat(str(rgrid.ref_epoch)[:26])
    centre_time = epoch + dt.timedelta(seconds=float(t_rows[ic] - epoch_shift))
    meta = {
        "stack_dir": str(cg.stack_dir), "reference": cg.reference, "dates": ts.dates, "window_full": [R0, R1, C0, C1],
        "full_shape": [L, W], "ml_shape": list(ml_shape), "looks": [az, rg], "wavelength": wavelength, "range_spacing": dr,
        "azimuth_spacing_ground": az_sp, "prf": prf, "range_bandwidth": brg, "azimuth_bandwidth": baz,
        "nlooks_factor": nlooks_factor, "starting_range_ml": float(srange_cols[0]), "center_line_utc": (centre_time - centre_time.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds(),
        "heading": heading, "height": height, "earth_radius": earth_radius, "look_direction": look, "orbit_direction": pass_dir,
        "bperp": bperp, "range_residual_m": range_resid, "inputs": dates_meta, "flatten": flatten_stats,
        "starting_range_by_date": {d: dates_meta[d]["starting_range"] for d in ts.dates},
        "corners_latlon": [[float(ml["lat"][i, j]), float(ml["lon"][i, j])] for i, j in ((0, 0), (0, -1), (-1, 0), (-1, -1))],
    }
    write_json(gdir / "meta.json", meta)
    attrs = mintpy_attrs(meta, "geometry")
    gfile = gdir / "geometryRadar.h5"
    with h5py.File(str(gfile) + ".part", "w") as f:
        for n, arr in (("latitude", ml["lat"]), ("longitude", ml["lon"]), ("height", hgt), ("incidenceAngle", inc),
                       ("azimuthAngle", azang), ("slantRangeDistance", np.broadcast_to(srange_cols, ml_shape))):
            f.create_dataset(n, data=np.asarray(arr, dtype=np.float32), chunks=True, compression="lzf")
        f.attrs.update(attrs)
    os.replace(str(gfile) + ".part", gfile)
    outs = [gfile, gdir / "meta.json"] + [ts.flatten_file(d) for d in flatten_stats]
    write_json(man, {"stage": "geometry", "status": "ok", "finished": now(), "duration_s": round(time.time() - t0, 1),
                     "host": socket.gethostname(), "outputs": {str(p): sizeof(p) for p in outs}, "code": {"git": git_rev()},
                     "window_full": [R0, R1, C0, C1], "ml_shape": list(ml_shape), "window_touches_aoi": full_covered})
    log(f"geometry: ok ({time.time() - t0:.0f} s)")
    return EXIT_OK


def mintpy_attrs(meta: dict, file_type: str) -> dict:
    L, W = meta["ml_shape"]
    az, rg = meta["looks"]
    (la1, lo1), (la2, lo2), (la3, lo3), (la4, lo4) = meta["corners_latlon"]
    return {k: str(v) for k, v in {
        "FILE_TYPE": file_type, "LENGTH": L, "WIDTH": W, "WAVELENGTH": meta["wavelength"],
        "RANGE_PIXEL_SIZE": meta["range_spacing"] * rg, "AZIMUTH_PIXEL_SIZE": meta["azimuth_spacing_ground"] * az,
        "STARTING_RANGE": meta["starting_range_ml"], "EARTH_RADIUS": meta["earth_radius"], "HEIGHT": meta["height"],
        "HEADING": meta["heading"], "CENTER_LINE_UTC": meta["center_line_utc"], "ORBIT_DIRECTION": meta["orbit_direction"],
        "ANTENNA_SIDE": 1 if meta["look_direction"] == "left" else -1, "PLATFORM": "NISAR", "PROCESSOR": "isce",
        "ALOOKS": az, "RLOOKS": rg, "NCORRLOOKS": az * rg * meta["nlooks_factor"],
        "LAT_REF1": la1, "LON_REF1": lo1, "LAT_REF2": la2, "LON_REF2": lo2, "LAT_REF3": la3, "LON_REF3": lo3,
        "LAT_REF4": la4, "LON_REF4": lo4, "REF_DATE_STACK": meta["reference"],
        "SOURCE": f"nisar_timeseries.py from the ISCE3 coregistered stack {meta['stack_dir']}",
    }.items()}


# ============================================================================ stage: ifg (ts env)
def rifg_ml(path: Path, window, looks, conj: bool, pol: str, freq: str):
    import h5py
    import numpy as np
    R0, R1, C0, C1 = window
    az, rg = looks
    out = np.zeros(((R1 - R0) // az, (C1 - C0) // rg), np.complex64)
    with h5py.File(path, "r") as f:
        ds = f[f"science/LSAR/RIFG/swaths/frequency{freq}/interferogram/{pol}/wrappedInterferogram"]
        step = 100 * az
        for r in range(R0, R1, step):
            blk = np.nan_to_num(ds[r:min(R1, r + step), C0:C1])
            n = blk.shape[0] // az
            out[(r - R0) // az:(r - R0) // az + n] = blk.reshape(n, az, -1, rg).sum(axis=(1, 3))
    return np.conj(out) if conj else out


def ifg_unit(ts: Ts, p, meta: dict, a, log: Log) -> str:
    import numpy as np
    d12, man = ts.d12(p), ts.manifest("ifg", ts.d12(p))
    if unit_done(man) and not a.force:
        log(f"ifg {d12}: done, skip")
        return "skip"
    t0 = time.time()
    write_json(man, {"stage": "ifg", "unit": d12, "status": "running", "started": now()})
    R0, R1, C0, C1 = meta["window_full"]
    az, rg = meta["looks"]
    nl, nw = meta["ml_shape"]
    s = {d: envi_memmap(ts.slc(d)) for d in p}
    off = {d: (None if d == ts.cg.reference else envi_memmap(ts.flatten_file(d))) for d in p}
    k = FLATTEN_SIGN * 4 * np.pi / meta["wavelength"] * meta["range_spacing"]
    # Each date is cropped to its own window, so the two SLCs start at different slant ranges. The geo2rdr offset is measured
    # in the secondary's own grid, so the geometric range difference is off*dr + (start_sec - start_ref): the constant part
    # must be flattened too, or every pair carries a constant phase error (8 samples = half a cycle at L-band).
    sr = meta["starting_range_by_date"]
    const = FLATTEN_SIGN * 4 * np.pi / meta["wavelength"] * (sr[p[1]] - sr[p[0]])
    ifg = np.zeros((nl, nw), np.complex64)
    coh = np.zeros((nl, nw), np.float32)
    blk = int(ts.eff["run"]["block_lines_ml"])
    for i0 in range(0, nl, blk):
        i1 = min(nl, i0 + blk)
        rs = slice(R0 + i0 * az, R0 + i1 * az)
        a1 = np.asarray(s[p[0]][rs, C0:C1])
        a2 = np.asarray(s[p[1]][rs, C0:C1])
        doff = np.zeros(a1.shape, np.float64)
        for sign, d in ((-1, p[0]), (1, p[1])):
            if off[d] is not None:
                doff += sign * np.asarray(off[d][i0 * az:i1 * az], dtype=np.float64)
        prod = a1 * np.conj(a2) * np.exp(1j * (k * doff + const)).astype(np.complex64)
        shp = (i1 - i0, az, nw, rg)
        sp = prod.reshape(shp).sum(axis=(1, 3))
        p1 = (a1.real ** 2 + a1.imag ** 2).reshape(shp).sum(axis=(1, 3), dtype=np.float64)
        p2 = (a2.real ** 2 + a2.imag ** 2).reshape(shp).sum(axis=(1, 3), dtype=np.float64)
        den = np.sqrt(p1 * p2)
        with np.errstate(invalid="ignore", divide="ignore"):
            coh[i0:i1] = np.where(den > 0, np.abs(sp) / den, 0).astype(np.float32)
        ifg[i0:i1] = (sp / (az * rg)).astype(np.complex64)
    pd = ts.pair_dir(p)
    write_envi(pd / "ifg.int", ifg, f"flattened multilooked interferogram {d12} ({az}x{rg} looks), s1*conj(s2)")
    write_envi(pd / "coh.cor", np.clip(coh, 0, 1), f"coherence {d12} ({az}x{rg} looks)")
    checks = {}
    rpath, conj = ts.rifg(p)
    ok = True
    if rpath is not None:
        if not rpath.exists():
            checks["rifg"] = f"missing {rpath}"
            ok = False
        else:
            ref = rifg_ml(rpath, meta["window_full"], meta["looks"], conj, ts.cg.polarization, ts.cg.frequency)
            good = (coh > 0.5) & (np.abs(ref) > 0)
            dphi = np.abs(np.angle(ifg[good] * np.conj(ref[good])))
            med = float(np.median(dphi)) if dphi.size else float("nan")
            checks["rifg"] = {"file": str(rpath), "conjugated": conj, "pixels_coh_gt_0.5": int(good.sum()), "median_abs_dphi_rad": med,
                              "p95_abs_dphi_rad": float(np.percentile(dphi, 95)) if dphi.size else None}
            ok = dphi.size > 1000 and med < float(ts.eff["qa"]["max_rifg_phase_diff_rad"])
            log(f"ifg {d12}: vs ISCE3 RIFG median |dphi| {med:.3f} rad on {good.sum()} coherent pixels -> {'ok' if ok else 'FAILED'}")
    outs = {str(x): sizeof(x) for x in (pd / "ifg.int", pd / "coh.cor")}
    write_json(man, {"stage": "ifg", "unit": d12, "status": "ok" if ok else "failed", "finished": now(), "duration_s": round(time.time() - t0, 1),
                     "checks": checks, "start_range_phase_rad": float(np.angle(np.exp(1j * const))), "mean_coherence": float(coh[coh > 0].mean()) if (coh > 0).any() else 0.0,
                     "outputs": outs if ok else {}, "code": {"git": git_rev()}})
    log(f"ifg {d12}: {'ok' if ok else 'FAILED'} ({time.time() - t0:.0f} s, mean coherence {coh[coh > 0].mean():.2f})")
    return "ok" if ok else "fail"


def stage_ifg(ts: Ts, a, log: Log) -> int:
    meta = read_json(ts.out / "geometry" / "meta.json")
    if not unit_done(ts.manifest("geometry")) or meta is None:
        log("ifg: geometry stage not done")
        return EXIT_PREREQ
    pairs = select_pairs(ts, a)
    with cf.ThreadPoolExecutor(max_workers=max(1, int(ts.eff["run"]["jobs"]))) as ex:
        res = list(ex.map(lambda p: ifg_unit(ts, p, meta, a, log), pairs))
    log(f"ifg summary: {dict(zip([ts.d12(p) for p in pairs], res))}")
    return EXIT_FAIL if "fail" in res else EXIT_OK


def select_pairs(ts: Ts, a):
    if not getattr(a, "pairs", None):
        return ts.pairs
    want = set(a.pairs)
    bad = want - {ts.d12(p) for p in ts.pairs}
    if bad:
        raise ConfigErr(f"--pairs {sorted(bad)} are not in this network")
    return [p for p in ts.pairs if ts.d12(p) in want]


# ============================================================================ stage: unwrap (ts env)
def unwrap_unit(ts: Ts, p, meta: dict, a, log: Log) -> str:
    import numpy as np
    import snaphu
    d12, man = ts.d12(p), ts.manifest("unwrap", ts.d12(p))
    if unit_done(man) and not a.force:
        log(f"unwrap {d12}: done, skip")
        return "skip"
    if not unit_done(ts.manifest("ifg", d12)):
        log(f"unwrap {d12}: interferogram not done")
        return "prereq"
    t0 = time.time()
    write_json(man, {"stage": "unwrap", "unit": d12, "status": "running", "started": now()})
    pd = ts.pair_dir(p)
    ifg = np.array(envi_memmap(pd / "ifg.int"))
    coh = np.array(envi_memmap(pd / "coh.cor"))
    u = ts.eff["unwrap"]
    nlooks = float(u["nlooks"]) if u["nlooks"] else max(1.0, meta["looks"][0] * meta["looks"][1] * meta["nlooks_factor"])
    mask = (coh > 0) & np.isfinite(coh) & (np.abs(ifg) > 0)
    scratch = ts.out / "scratch" / f"snaphu_{d12}"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    ntiles = tuple(int(x) for x in u["ntiles"])
    overlap = tuple(int(x) for x in u["tile_overlap"]) if ntiles != (1, 1) else 0
    try:
        unw, cc = snaphu.unwrap(ifg, np.clip(coh, 0, 1), nlooks=nlooks, cost=u["cost"], init=u["init"], mask=mask,
                                min_conncomp_frac=float(u["min_conncomp_frac"]), ntiles=ntiles, tile_overlap=overlap,
                                nproc=int(u["nproc"]), single_tile_reoptimize=bool(u["single_tile_reoptimize"]),
                                regrow_conncomps=bool(u["regrow_conncomps"]), scratchdir=scratch, delete_scratch=True)
        err = None
    except Exception as e:  # noqa: BLE001
        unw = cc = None
        err = f"{type(e).__name__}: {e}"
    shutil.rmtree(scratch, ignore_errors=True)
    if err:
        write_json(man, {"stage": "unwrap", "unit": d12, "status": "failed", "error": err, "finished": now()})
        log(f"unwrap {d12}: FAILED {err}")
        return "fail"
    unw = np.where(mask, unw, 0).astype(np.float32)
    cc = np.where(mask, cc, 0).astype(np.uint32)
    write_envi(pd / "unw.unw", unw, f"snaphu unwrapped phase {d12} (radians)")
    write_envi(pd / "conncomp.cc", cc, f"snaphu connected components {d12}")
    # wrapped consistency: the unwrapped phase must re-wrap to the input phase
    rewrap = np.angle(np.exp(1j * (unw - np.angle(ifg))))[mask]
    stats = {"nlooks": nlooks, "ntiles": list(ntiles), "tile_overlap": overlap, "valid_fraction": float(mask.mean()),
             "components": int(cc.max()), "fraction_in_component_0": float(((cc == 0) & mask).sum() / max(1, mask.sum())),
             "max_abs_rewrap_residual_rad": float(np.abs(rewrap).max()) if rewrap.size else None}
    outs = {str(x): sizeof(x) for x in (pd / "unw.unw", pd / "conncomp.cc")}
    write_json(man, {"stage": "unwrap", "unit": d12, "status": "ok", "finished": now(), "duration_s": round(time.time() - t0, 1),
                     "stats": stats, "outputs": outs, "code": {"git": git_rev(), "snaphu": getattr(snaphu, "__version__", "?")}})
    log(f"unwrap {d12}: ok ({time.time() - t0:.0f} s) {stats}")
    return "ok"


def closure_census(ts: Ts, log: Log):
    """Unwrapping-error census: for each closed triplet, the integer number of cycles in unw12 + unw23 - unw13."""
    import numpy as np
    have = {p for p in ts.pairs if unit_done(ts.manifest("unwrap", ts.d12(p)))}
    rows = []
    for d1, d2, d3 in itertools.combinations(ts.dates, 3):
        tri = [(d1, d2), (d2, d3), (d1, d3)]
        if not all(t in have for t in tri):
            continue
        u = [np.array(envi_memmap(ts.pair_dir(t) / "unw.unw")) for t in tri]
        c = [np.array(envi_memmap(ts.pair_dir(t) / "conncomp.cc")) for t in tri]
        valid = (c[0] > 0) & (c[1] > 0) & (c[2] > 0)
        if valid.sum() < 1000:
            continue
        clo = u[0] + u[1] - u[2]
        clo = clo - np.median(clo[valid])
        ncyc = np.round(clo[valid] / (2 * np.pi))
        rows.append({"triplet": f"{d1}_{d2}_{d3}", "pixels": int(valid.sum()), "fraction_nonzero_cycles": float((ncyc != 0).mean()),
                     "residual_std_rad": float(np.std(clo[valid] - 2 * np.pi * ncyc))})
    write_json(ts.out / "qa" / "unwrap_closure.json", {"generated": now(), "triplets": rows})
    for r in rows:
        log(f"closure {r['triplet']}: {100 * r['fraction_nonzero_cycles']:.2f}% of {r['pixels']} pixels off by whole cycles")


def stage_unwrap(ts: Ts, a, log: Log) -> int:
    meta = read_json(ts.out / "geometry" / "meta.json")
    if meta is None:
        log("unwrap: geometry stage not done")
        return EXIT_PREREQ
    pairs = select_pairs(ts, a)
    res = [unwrap_unit(ts, p, meta, a, log) for p in pairs]      # sequential: snaphu already runs tiles in parallel
    log(f"unwrap summary: {dict(zip([ts.d12(p) for p in pairs], res))}")
    closure_census(ts, log)
    return EXIT_FAIL if "fail" in res else (EXIT_PREREQ if "prereq" in res else EXIT_OK)


# ============================================================================ stage: corrections (ts env)
def sample_gunw(path: Path, lat, lon, hgt, pol: str, freq: str, want: dict) -> dict:
    import h5py
    import numpy as np
    from pyproj import Transformer
    from scipy.interpolate import RegularGridInterpolator
    from scipy.ndimage import distance_transform_edt

    def axis_ok(ax, vals):
        return (ax[::-1], vals[..., ::-1]) if ax[0] > ax[-1] else (ax, vals)

    out = {}
    with h5py.File(path, "r") as f:
        g = f["science/LSAR/GUNW"]
        uw = g[f"grids/frequency{freq}/unwrappedInterferogram"]
        rgd = g["metadata/radarGrid"]
        if want.get("ionosphere"):
            epsg = int(uw["projection"][()])
            x, y = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
            xs, ys, v = uw["xCoordinates"][()], uw["yCoordinates"][()], uw[f"{pol}/ionospherePhaseScreen"][()].astype(np.float64)
            xs, v = axis_ok(xs, v)
            ysr, v = (ys[::-1], v[::-1]) if ys[0] > ys[-1] else (ys, v)
            val = RegularGridInterpolator((ysr, xs), v, bounds_error=False, fill_value=np.nan)((y, x))
            bad = ~np.isfinite(val)
            if bad.all():
                raise ValueError(f"{path.name}: ionosphere screen does not cover the window")
            if bad.any():
                idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
                val = val[tuple(idx)]
            out["ionosphere"], out["ionosphere_filled_fraction"] = val.astype(np.float32), float(bad.mean())
        cube_names = []
        if want.get("troposphere"):
            cube_names += ["wetTroposphericPhaseScreen", "hydrostaticTroposphericPhaseScreen"]
        if want.get("solid_earth_tides"):
            cube_names += ["slantRangeSolidEarthTidesPhase"]
        cube_names += ["perpendicularBaseline"]
        epsg = int(rgd["projection"][()])
        x, y = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
        xs, ys, hs = rgd["xCoordinates"][()], rgd["yCoordinates"][()], rgd["heightAboveEllipsoid"][()]
        hh = np.clip(hgt, hs.min(), hs.max())
        for n in cube_names:
            v = rgd[n][()].astype(np.float64)
            xs2, v = axis_ok(xs, v)
            ys2, v = (ys[::-1], v[:, ::-1]) if ys[0] > ys[-1] else (ys, v)
            hs2, v = (hs[::-1], v[::-1]) if hs[0] > hs[-1] else (hs, v)
            interp = RegularGridInterpolator((hs2, ys2, xs2), v, bounds_error=False, fill_value=np.nan)
            if n == "perpendicularBaseline":
                ic, jc = lat.shape[0] // 2, lat.shape[1] // 2
                out["bperp_centre"] = float(interp((hh[ic, jc], y[ic, jc], x[ic, jc])))
            else:
                out[n] = interp((hh, y, x)).astype(np.float32)
    if want.get("troposphere"):
        out["troposphere"] = out.pop("wetTroposphericPhaseScreen") + out.pop("hydrostaticTroposphericPhaseScreen")
    if want.get("solid_earth_tides"):
        out["solid_earth_tides"] = out.pop("slantRangeSolidEarthTidesPhase")
    return out


def stage_corrections(ts: Ts, a, log: Log) -> int:
    import h5py
    import numpy as np
    man = ts.manifest("corrections")
    c = ts.eff["corrections"]
    kinds = [k for k in ("ionosphere", "troposphere", "solid_earth_tides") if c[k]]
    if not kinds:
        log("corrections: all disabled; skip")
        return EXIT_OK
    if unit_done(man) and not a.force:
        log("corrections: done, skip")
        return EXIT_OK
    meta = read_json(ts.out / "geometry" / "meta.json")
    if meta is None:
        log("corrections: geometry stage not done")
        return EXIT_PREREQ
    gp = ts.gunw_pairs()
    if not connected(ts.dates, list(gp)):
        log(f"corrections: GUNW pairs {sorted(gp)} do not connect the dates {ts.dates}")
        return EXIT_PREREQ
    RG = "science/LSAR/GUNW/metadata/radarGrid"
    NEEDS = {"troposphere": [f"{RG}/wetTroposphericPhaseScreen", f"{RG}/hydrostaticTroposphericPhaseScreen"],
             "solid_earth_tides": [f"{RG}/slantRangeSolidEarthTidesPhase"],
             "ionosphere": [f"science/LSAR/GUNW/grids/frequency{ts.cg.frequency}/unwrappedInterferogram/{ts.cg.polarization}/ionospherePhaseScreen"]}
    dropped = {}
    for (d1, d2), fpath in gp.items():
        with h5py.File(fpath, "r") as f:
            for kind in list(kinds):
                if any(path not in f for path in NEEDS[kind]):
                    dropped.setdefault(kind, []).append(f"{d1}_{d2} ({fpath.name.split('_')[2]})")
    for kind, where in dropped.items():
        kinds.remove(kind)
        log(f"corrections: {kind} NOT available in {', '.join(where)}; that correction is skipped for this run")
    if not kinds:
        log("corrections: none of the requested layers are available in every GUNW of the network")
        write_json(man, {"stage": "corrections", "status": "ok", "finished": now(), "kinds": [], "dropped": dropped,
                         "gunw_pairs": {f"{a}_{b}": str(p) for (a, b), p in gp.items()}, "outputs": {}, "code": {"git": git_rev()}})
        return EXIT_OK
    t0 = time.time()
    write_json(man, {"stage": "corrections", "status": "running", "started": now()})
    with h5py.File(ts.out / "geometry" / "geometryRadar.h5", "r") as f:
        lat, lon, hgt = f["latitude"][()].astype(np.float64), f["longitude"][()].astype(np.float64), f["height"][()].astype(np.float64)
    screens, qa = {k: [] for k in kinds}, {}
    pairs = list(gp)
    for (d1, d2), fpath in gp.items():
        t1 = time.time()
        s = sample_gunw(fpath, lat, lon, hgt, ts.cg.polarization, ts.cg.frequency, {k: True for k in kinds})
        for k in kinds:
            screens[k].append(s[k])
        ours = meta["bperp"][d2] - meta["bperp"][d1]
        qa[f"{d1}_{d2}"] = {"gunw": fpath.name, "seconds": round(time.time() - t1, 1), "ionosphere_filled_fraction": s.get("ionosphere_filled_fraction"),
                            "bperp_centre_gunw_m": s["bperp_centre"], "bperp_centre_ours_m": ours,
                            **{f"{k}_std_rad": float(np.nanstd(s[k])) for k in kinds}}
        log(f"corrections: sampled {fpath.name} ({time.time() - t1:.0f} s); B_perp GUNW {s['bperp_centre']:+.1f} m vs ours {ours:+.1f} m")
    # pairs -> dates (first date = 0) by least squares; exact for a chain
    idx = {d: k for k, d in enumerate(ts.dates)}
    A = np.zeros((len(pairs), len(ts.dates)))
    for r, (d1, d2) in enumerate(pairs):
        A[r, idx[d1]], A[r, idx[d2]] = -1, 1
    Ainv = np.linalg.pinv(A[:, 1:])
    cdir = ts.out / "corrections"
    cdir.mkdir(parents=True, exist_ok=True)
    out = cdir / "per_date_phase.h5"
    with h5py.File(str(out) + ".part", "w") as f:
        f.attrs.update({"dates": json.dumps(ts.dates), "gunw_pairs": json.dumps({f"{a}_{b}": p.name for (a, b), p in gp.items()}),
                        "units": "radians, interferometric convention (subtract), first date = 0"})
        for k in kinds:
            S = np.stack(screens[k]).reshape(len(pairs), -1).astype(np.float64)
            per = np.zeros((len(ts.dates),) + lat.shape, np.float32)
            per[1:] = (Ainv @ S).reshape((len(ts.dates) - 1,) + lat.shape)
            f.create_dataset(k, data=per, chunks=True, compression="lzf")
    os.replace(str(out) + ".part", out)
    write_json(ts.out / "qa" / "corrections.json", {"generated": now(), "pairs": qa})
    write_json(man, {"stage": "corrections", "status": "ok", "finished": now(), "duration_s": round(time.time() - t0, 1),
                     "kinds": kinds, "dropped": dropped, "gunw_pairs": {f"{a}_{b}": str(p) for (a, b), p in gp.items()},
                     "outputs": {str(out): sizeof(out)}, "code": {"git": git_rev()}})
    log(f"corrections: ok ({time.time() - t0:.0f} s)")
    return EXIT_OK


# ============================================================================ stage: mintpy (ts env)
def mintpy_cli(tool: str, args: list[str], cwd: Path, log: Log) -> int:
    exe = Path(sys.executable).parent / tool
    cmd = [sys.executable, str(exe), *map(str, args)]
    log(f"mintpy: {tool} {' '.join(map(str, args))}")
    with open(cwd / "mintpy_commands.log", "a") as fh:
        fh.write(f"### {now()} $ {' '.join(shlex.quote(str(x)) for x in cmd)}\n")
        fh.flush()
        rc = subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT).returncode
    if rc != 0:
        log(f"mintpy: {tool} EXIT={rc} (see {cwd / 'mintpy_commands.log'})")
    return rc


def write_ifgram_stack(ts: Ts, meta: dict, path: Path):
    import h5py
    import numpy as np
    L, W = meta["ml_shape"]
    n = len(ts.pairs)
    with h5py.File(str(path) + ".part", "w") as f:
        f.create_dataset("date", data=np.array([[a.encode(), b.encode()] for a, b in ts.pairs], dtype="S8"))
        f.create_dataset("bperp", data=np.array([meta["bperp"][b] - meta["bperp"][a] for a, b in ts.pairs], np.float32))
        f.create_dataset("dropIfgram", data=np.ones(n, bool))
        dsu = f.create_dataset("unwrapPhase", (n, L, W), np.float32, chunks=(1, min(L, 256), min(W, 256)))
        dsc = f.create_dataset("coherence", (n, L, W), np.float32, chunks=(1, min(L, 256), min(W, 256)))
        dsk = f.create_dataset("connectComponent", (n, L, W), np.int16, chunks=(1, min(L, 256), min(W, 256)))
        for i, p in enumerate(ts.pairs):
            pd = ts.pair_dir(p)
            dsu[i] = envi_memmap(pd / "unw.unw")
            dsc[i] = envi_memmap(pd / "coh.cor")
            dsk[i] = np.minimum(envi_memmap(pd / "conncomp.cc"), 32767).astype(np.int16)
        attrs = mintpy_attrs(meta, "ifgramStack")
        attrs["UNIT"] = "radian"
        f.attrs.update(attrs)
    os.replace(str(path) + ".part", path)


def write_correction_ts(ts: Ts, meta: dict, per_date: Path, kind: str, path: Path, ref_yx):
    import h5py
    import numpy as np
    with h5py.File(per_date, "r") as f:
        ph = f[kind][()].astype(np.float64)
    disp = ph * (-meta["wavelength"] / (4 * np.pi))                 # MintPy: range change -> displacement toward satellite
    disp -= disp[:, ref_yx[0], ref_yx[1]][:, None, None]            # spatial reference = the MintPy reference point
    with h5py.File(str(path) + ".part", "w") as f:
        f.create_dataset("timeseries", data=disp.astype(np.float32), chunks=True)
        f.create_dataset("date", data=np.array([d.encode() for d in ts.dates], dtype="S8"))
        f.create_dataset("bperp", data=np.array([meta["bperp"][d] for d in ts.dates], np.float32))
        attrs = mintpy_attrs(meta, "timeseries")
        attrs.update({"UNIT": "m", "REF_DATE": ts.dates[0], "REF_Y": str(ref_yx[0]), "REF_X": str(ref_yx[1]),
                      "SOURCE": f"NISAR GUNW {kind} screens via nisar_timeseries.py"})
        f.attrs.update(attrs)
    os.replace(str(path) + ".part", path)


def stage_mintpy(ts: Ts, a, log: Log) -> int:
    import h5py
    import numpy as np
    man = ts.manifest("mintpy")
    if len(ts.pairs) < 2:
        log(f"mintpy: skipped, a time series needs at least 2 interferograms (have {len(ts.pairs)}); pair products are in {ts.out / 'pairs'}")
        return EXIT_OK
    if unit_done(man) and not a.force:
        log("mintpy: done, skip")
        return EXIT_OK
    meta = read_json(ts.out / "geometry" / "meta.json")
    need = [ts.d12(p) for p in ts.pairs if not unit_done(ts.manifest("unwrap", ts.d12(p)))]
    if meta is None or need:
        log(f"mintpy: prerequisites missing (geometry {'ok' if meta else 'missing'}, unwrapped pairs missing {need})")
        return EXIT_PREREQ
    if ts.any_correction() and not unit_done(ts.manifest("corrections")):
        log("mintpy: corrections stage not done")
        return EXIT_PREREQ
    t0 = time.time()
    md = ts.out / "mintpy"
    if md.exists():
        old = md.with_name(f"mintpy_replaced_{stamp()}")
        md.rename(old)
        log(f"mintpy: previous run moved to {old}")
    (md / "inputs").mkdir(parents=True)
    write_json(man, {"stage": "mintpy", "status": "running", "started": now()})
    m = ts.eff["mintpy"]
    write_ifgram_stack(ts, meta, md / "inputs" / "ifgramStack.h5")
    shutil.copy2(ts.out / "geometry" / "geometryRadar.h5", md / "inputs" / "geometryRadar.h5")
    ref = f"mintpy.reference.lalo = {ts.reference_lalo[0]}, {ts.reference_lalo[1]}" if ts.reference_lalo else "# reference point: chosen by nisar_timeseries.py (--row/--col)"
    (md / "mintpy.cfg").write_text(f"""# generated by nisar_timeseries.py
{ref}
mintpy.reference.minCoherence = {m['reference_min_coherence']}
mintpy.unwrapError.method = {m['unwrap_error_method']}
mintpy.networkInversion.weightFunc = {m['weight_func']}
mintpy.networkInversion.maskDataset = {m['mask_dataset']}
mintpy.networkInversion.maskThreshold = {m['mask_threshold']}
mintpy.networkInversion.minTempCoh = {m['min_temporal_coherence']}
mintpy.topographicResidual = {'yes' if m['dem_error'] else 'no'}
mintpy.timeFunc.polynomial = {m['velocity_polynomial']}
mintpy.timeFunc.uncertainty = residue
mintpy.compute.cluster = {'local' if int(ts.eff['run']['mintpy_workers']) > 1 else 'no'}
mintpy.compute.numWorker = {int(ts.eff['run']['mintpy_workers'])}
""")
    stk, geom, cfg = "inputs/ifgramStack.h5", "inputs/geometryRadar.h5", "mintpy.cfg"
    if mintpy_cli("temporal_average.py", [stk, "--dataset", "coherence", "-o", "avgSpatialCoh.h5"], md, log) != 0:
        write_json(man, {"stage": "mintpy", "status": "failed", "failed_step": "temporal_average.py", "finished": now()})
        return EXIT_FAIL
    if ts.reference_lalo:
        steps = [("reference_point.py", [stk, "-t", cfg, "-c", "avgSpatialCoh.h5", "--lookup", geom])]
    else:
        # MintPy's automatic choice is a RANDOM pixel above minCoherence, so reruns would differ; choose deterministically:
        # the highest 9x9-averaged coherence among pixels inside a snaphu component in every interferogram
        from scipy.ndimage import uniform_filter
        with h5py.File(md / "avgSpatialCoh.h5", "r") as f:
            coh = f["coherence"][()].astype(np.float64)
        with h5py.File(md / stk, "r") as f:
            allcc = np.all(f["connectComponent"][()] > 0, axis=0)
        score = np.where(allcc, uniform_filter(np.nan_to_num(coh), size=9), -1.0)
        ry, rx = np.unravel_index(int(np.argmax(score)), score.shape)
        if score[ry, rx] < float(m["reference_min_coherence"]):
            log(f"mintpy: no pixel reaches the 9x9-averaged coherence {m['reference_min_coherence']} (best {score[ry, rx]:.2f}); using the best")
        steps = [("reference_point.py", [stk, "-t", cfg, "--row", ry, "--col", rx])]
    if m["unwrap_error_method"] not in ("no", False, None):
        steps.append(("generate_mask.py", [stk, "--nonzero", "-o", "maskConnComp.h5", "--update"]))
        meth = m["unwrap_error_method"]
        if "bridging" in meth:
            steps.append(("unwrap_error_bridging.py", [stk, "--template", cfg, "--update"]))
        if "phase_closure" in meth:
            steps.append(("unwrap_error_phase_closure.py", [stk, "--template", cfg, "--update", "--cc-mask", "maskConnComp.h5"]
                          + (["-i", "unwrapPhase_bridging"] if "bridging" in meth else [])))
    steps.append(("ifgram_inversion.py", [stk, "-t", cfg, "--update"]))
    for tool, args in steps:
        if mintpy_cli(tool, args, md, log) != 0:
            write_json(man, {"stage": "mintpy", "status": "failed", "failed_step": tool, "finished": now()})
            return EXIT_FAIL
    with h5py.File(md / stk, "r") as f:
        ref_yx = (int(f.attrs["REF_Y"]), int(f.attrs["REF_X"]))
    with h5py.File(md / geom, "r") as f:
        ref_ll = (float(f["latitude"][ref_yx]), float(f["longitude"][ref_yx]))
    log(f"mintpy: reference point y/x {ref_yx}, lat/lon {ref_ll[0]:.5f} {ref_ll[1]:.5f}")

    ts_file, chain = "timeseries.h5", []
    order = [("solid_earth_tides", "SET"), ("ionosphere", "ion"), ("troposphere", "gunwTropo")]
    produced = (read_json(ts.manifest("corrections")) or {}).get("kinds", [])
    for kind, suffix in order:
        if not ts.eff["corrections"][kind] or kind not in produced:
            continue
        cfile = md / "inputs" / f"gunw_{suffix}.h5"
        write_correction_ts(ts, meta, ts.out / "corrections" / "per_date_phase.h5", kind, cfile, ref_yx)
        new = ts_file.replace(".h5", f"_{suffix}.h5")
        if mintpy_cli("diff.py", [ts_file, f"inputs/{cfile.name}", "-o", new, "--force"], md, log) != 0:
            write_json(man, {"stage": "mintpy", "status": "failed", "failed_step": f"diff {kind}", "finished": now()})
            return EXIT_FAIL
        ts_file = new
        chain.append(kind)
    if m["dem_error"]:
        new = ts_file.replace(".h5", "_demErr.h5")
        if mintpy_cli("dem_error.py", [ts_file, "-g", geom, "-t", cfg, "-o", new], md, log) != 0:
            write_json(man, {"stage": "mintpy", "status": "failed", "failed_step": "dem_error", "finished": now()})
            return EXIT_FAIL
        ts_file = new
    tcoh = m["min_temporal_coherence"]
    post = [("timeseries2velocity.py", [ts_file, "-t", cfg, "-o", "velocity.h5"]),
            ("timeseries2velocity.py", ["timeseries.h5", "-t", cfg, "-o", "velocity_uncorrected.h5"]),
            ("generate_mask.py", ["temporalCoherence.h5", "-m", tcoh, "-o", "maskTempCoh.h5"]),
            ("geocode.py", ["velocity.h5", "velocity_uncorrected.h5", "temporalCoherence.h5", "maskTempCoh.h5", ts_file,
                            "-l", geom, "--outdir", "geo"])]
    for tool, args in post:
        if mintpy_cli(tool, args, md, log) != 0:
            write_json(man, {"stage": "mintpy", "status": "failed", "failed_step": tool, "finished": now()})
            return EXIT_FAIL
    ex = ts.out / "export"
    ex.mkdir(exist_ok=True)
    first, last = ts.dates[0], ts.dates[-1]
    exports = [("geo/geo_velocity.h5", "velocity", f"{ts.name}_los_velocity_m_per_yr"),
               ("geo/geo_velocity.h5", "velocityStd", f"{ts.name}_los_velocity_std_m_per_yr"),
               ("geo/geo_velocity_uncorrected.h5", "velocity", f"{ts.name}_los_velocity_uncorrected_m_per_yr"),
               ("geo/geo_temporalCoherence.h5", "temporalCoherence", f"{ts.name}_temporal_coherence"),
               ("geo/geo_maskTempCoh.h5", "mask", f"{ts.name}_mask_temporal_coherence_{tcoh}"),
               (f"geo/geo_{ts_file}", last, f"{ts.name}_los_displacement_m_{first}_{last}")]
    for src, dset, name in exports:
        if mintpy_cli("save_gdal.py", [src, "-d", dset, "-o", str(ex / f"{name}.tif")], md, log) != 0:
            write_json(man, {"stage": "mintpy", "status": "failed", "failed_step": f"save_gdal {name}", "finished": now()})
            return EXIT_FAIL

    # QA summary inside the AOI polygon
    from matplotlib.path import Path as MplPath
    with h5py.File(md / geom, "r") as f:
        lat, lon = f["latitude"][()], f["longitude"][()]
    inside = MplPath(kml_polygon(ts.aoi_kml)).contains_points(np.c_[lon.ravel(), lat.ravel()]).reshape(lat.shape)
    with h5py.File(md / "velocity.h5", "r") as f:
        v = f["velocity"][()]
    with h5py.File(md / "velocity_uncorrected.h5", "r") as f:
        vu = f["velocity"][()]
    with h5py.File(md / "temporalCoherence.h5", "r") as f:
        tc = f["temporalCoherence"][()]
    good = inside & (tc >= float(tcoh)) & np.isfinite(v)
    pct = lambda x: {q: float(np.percentile(x, q)) * 1000 for q in (5, 50, 95)} if x.size else {}  # noqa: E731
    corr_mag = {}
    for kind, suffix in order:
        cf_ = md / "inputs" / f"gunw_{suffix}.h5"
        if cf_.exists():
            with h5py.File(cf_, "r") as f:
                d = f["timeseries"][()]
            corr_mag[kind] = {dd: float(np.nanstd(d[i][inside]) * 1000) for i, dd in enumerate(ts.dates)}
    qa = {"generated": now(), "reference_yx": ref_yx, "reference_latlon": ref_ll, "dates": ts.dates, "pairs": len(ts.pairs),
          "corrections_applied": chain, "final_timeseries": ts_file, "aoi_pixels": int(inside.sum()),
          "aoi_pixels_temporal_coherence_ok": int(good.sum()),
          "velocity_mm_per_yr_p5_p50_p95": pct(v[good]), "velocity_uncorrected_mm_per_yr_p5_p50_p95": pct(vu[good]),
          "correction_spatial_std_mm_in_aoi": corr_mag}
    write_json(ts.out / "qa" / "mintpy.json", qa)
    outs = [md / "timeseries.h5", md / ts_file, md / "velocity.h5"] + [ex / f"{n}.tif" for _, _, n in exports]
    write_json(man, {"stage": "mintpy", "status": "ok", "finished": now(), "duration_s": round(time.time() - t0, 1), "qa": qa,
                     "outputs": {str(p): sizeof(p) for p in outs}, "code": {"git": git_rev()}})
    log(f"mintpy: ok ({time.time() - t0:.0f} s); AOI velocity p5/p50/p95 mm/yr {qa['velocity_mm_per_yr_p5_p50_p95']}")
    return EXIT_OK


# ============================================================================ main
def prereq_coreg(ts: Ts, log: Log) -> bool:
    cg = ts.cg
    missing = [d for d in ts.dates if d != cg.reference and not unit_done(cg.manifest(d))]
    if missing or not ts.slc(cg.reference).exists():
        log(f"coregistered stack not ready: unfinished dates {missing}" + ("" if ts.slc(cg.reference).exists() else ", reference SLC missing"))
        return False
    return True


def run_stage_here(ts: Ts, stage: str, a, log: Log) -> int:
    fn = {"geometry": stage_geometry, "ifg": stage_ifg, "unwrap": stage_unwrap, "corrections": stage_corrections, "mintpy": stage_mintpy}[stage]
    try:
        return fn(ts, a, log)
    except ConfigErr as e:
        log(str(e))
        return EXIT_CONFIG
    except Exception as e:  # noqa: BLE001
        import traceback
        log(f"{stage}: FAILED {type(e).__name__}: {e}")
        traceback.print_exc()
        man = ts.manifest(stage)
        if (read_json(man) or {}).get("status") == "running":
            write_json(man, {"stage": stage, "status": "failed", "error": f"{type(e).__name__}: {e}", "finished": now()})
        return EXIT_FAIL


def detach(argv, ts: Ts, what: str) -> int:
    session = re.sub(r"[^A-Za-z0-9_-]", "_", f"ts_{ts.cg.case}_{ts.name}_{what}")[:60]
    if subprocess.run(["tmux", "has-session", "-t", session], capture_output=True).returncode == 0:
        print(f"tmux session {session} already running; attach: tmux attach -t {session}")
        return EXIT_PREREQ
    ts.logdir.mkdir(parents=True, exist_ok=True)
    logf = ts.logdir / f"{what}_{stamp()}.detached.log"
    inner = " ".join(shlex.quote(x) for x in [sys.executable, "-u", str(Path(__file__).resolve())] + [x for x in argv if x != "--detach"])
    subprocess.run(["tmux", "new-session", "-d", "-s", session, f"{inner} > {shlex.quote(str(logf))} 2>&1; echo EXIT=$? >> {shlex.quote(str(logf))}"], check=True)
    print(f"started: tmux session {session}\n  attach: tmux attach -t {session}\n  log:    {logf}")
    return EXIT_OK


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", required=True, type=Path)
    ap.add_argument("command", choices=["show", "status", "run", "_stage"])
    ap.add_argument("stage_name", nargs="?", help=argparse.SUPPRESS)
    ap.add_argument("--stage", choices=STAGES, help="run only this stage (default: all, in order)")
    ap.add_argument("--pairs", nargs="+", help="ifg/unwrap units to process, as YYYYMMDD_YYYYMMDD")
    ap.add_argument("--force", action="store_true", help="redo units even when verified done")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--detach", action="store_true", help="run inside a tmux session and return immediately")
    a = ap.parse_args(argv)
    try:
        ts = Ts(a.config)
    except ConfigErr as e:
        print(e, file=sys.stderr)
        return EXIT_CONFIG
    argv = [str(ts.path) if x == str(a.config) else x for x in argv]
    if a.command == "show":
        return cmd_show(ts, a)
    if a.command == "status":
        return cmd_status(ts, a)
    if a.command == "_stage":                                   # child process, already in the right environment
        log = Log(ts.logdir / f"run_{a.stage_name}_{stamp()}.log")
        return run_stage_here(ts, a.stage_name, a, log)
    if a.detach:
        return detach(argv, ts, a.stage or "all")
    log = Log(None if a.dry_run else ts.logdir / f"run_{a.stage or 'all'}_{stamp()}.log")
    log(f"nisar_timeseries {ts.name}: {len(ts.dates)} dates, {len(ts.pairs)} pairs, stack {ts.cg.stack_id} (git {git_rev()})")
    if not prereq_coreg(ts, log):
        return EXIT_PREREQ
    try:
        select_pairs(ts, a)
    except ConfigErr as e:
        log(str(e))
        return EXIT_CONFIG
    if ts.any_correction() and not connected(ts.dates, list(ts.gunw_pairs())):
        log(f"GUNW pairs {sorted(ts.gunw_pairs())} do not connect the dates {ts.dates}; disable corrections or add GUNWs")
        return EXIT_PREREQ
    if not params_gate(ts, log, a.dry_run):
        return EXIT_CONFIG
    for s in ([a.stage] if a.stage else list(STAGES)):
        py = ts.eff["envs"][STAGE_ENV[s]]
        if a.dry_run:
            log(f"{s}: would run in {py}" + (f" for pairs {[ts.d12(p) for p in select_pairs(ts, a)]}" if s in ("ifg", "unwrap") else ""))
            continue
        cmd = [py, "-u", str(Path(__file__).resolve()), "-c", str(ts.path), "_stage", s] + (["--pairs", *a.pairs] if a.pairs else []) + (["--force"] if a.force else [])
        t0 = time.time()
        ts.logdir.mkdir(parents=True, exist_ok=True)
        with open(ts.logdir / f"{s}_{stamp()}.console.log", "a") as fh:
            rc = subprocess.run(cmd, cwd=HERE, stdout=fh, stderr=subprocess.STDOUT).returncode
        log(f"{s}: EXIT={rc} ({time.time() - t0:.0f} s)")
        if rc != EXIT_OK:
            log(f"stopped at stage {s} (exit {rc}); details in {ts.logdir}")
            return rc
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
