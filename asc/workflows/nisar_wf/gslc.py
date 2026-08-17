"""
Stage G1 -- RSLC -> GSLC generation.

For each acquisition date this stage:
  1. renders a `gslc` runconfig from the PINNED geogrid in stack.json,
  2. VALIDATES it against the installed yamale schema *before* running anything,
  3. invokes `nisar.workflows.gslc` as a subprocess,
  4. verifies the output grid matches what the geogrid predicted,
  5. writes a provenance sidecar.

This stage has no analogue in the isce+ course, which only ever *reads*
pre-existing L2 GSLC granules -- so the runconfig below is written against the
installed schema rather than ported from a notebook.

Two ISCE3 behaviours drive the design:

  * `x_snap` / `y_snap` are deliberately left NULL. With all four corners plus
    both postings supplied, `geogrid.create` takes its deterministic
    `_grid_size` path and our pinned corners survive verbatim. Setting a snap
    instead re-snaps the grid; worse, `geogrid.create` evaluates `x_snap <= 0`
    before testing for None, so supplying only one of the pair raises a
    TypeError from deep inside ISCE3. Stage A already snapped the corners, so
    there is nothing left to gain here and correctness to lose.

  * `nisar.workflows.gslc.__main__` DELETES `sas_output_file` if it exists
    before running. Idempotency therefore has to be decided here, before the
    subprocess is launched -- once ISCE3 starts, the previous product is gone.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import yaml

from .config import Config, ConfigError
from .ingest import load_stack
from .util import (
    Logger,
    Result,
    StepFailed,
    clean_argv,
    fmt_s,
    free_disk_bytes,
    human_bytes,
    run_cmd,
    write_sidecar,
)

GSLC_GRID = "/science/LSAR/GSLC/grids"


# --------------------------------------------------------------------------
# runconfig construction
# --------------------------------------------------------------------------
def build_runconfig(cfg: Config, stack: dict, granule: dict, log: Logger) -> dict:
    """
    Assemble the runconfig dict for one date from the pinned geogrid.

    Key nesting is taken from the installed schema, notably
    `processing.geocode.output_posting.{A,B}.{x_posting,y_posting}` -- the
    per-frequency level is easy to omit by accident and is the single most
    likely place to silently mis-nest.
    """
    gg = stack["geogrid"]
    date = granule["date"]
    epsg = int(gg["epsg"])

    # Fresh dicts per use-site. The same corner values go into both `geocode`
    # and `radar_grid_cubes`, and reusing one object would make PyYAML emit an
    # anchor/alias pair (&id001 / *id001). That is valid YAML and ISCE3 reads it
    # fine, but it makes the runconfig hostile to hand-inspection and couples two
    # blocks that a reader would expect to edit independently.
    def _top_left() -> dict:
        return {
            "x_abs": float(gg["top_left"]["x_abs"]),
            "y_abs": float(gg["top_left"]["y_abs"]),
        }

    def _bottom_right() -> dict:
        return {
            "x_abs": float(gg["bottom_right"]["x_abs"]),
            "y_abs": float(gg["bottom_right"]["y_abs"]),
        }

    # frequency -> pol list, restricted to what this granule actually carries
    list_of_frequencies: dict[str, list[str]] = {}
    for freq in cfg.frequencies:
        available = granule["frequencies"][freq]["polarizations"]
        pols = [p for p in cfg.polarizations if p in available]
        if not pols:
            raise ConfigError(
                f"none of the requested polarizations {cfg.polarizations} exist in "
                f"frequency {freq} of {granule['filename']} (has {available})"
            )
        list_of_frequencies[freq] = pols

    # output_posting needs BOTH A and B keys present in the schema, but only the
    # selected frequencies get real values; the rest stay null and unused.
    output_posting: dict[str, Any] = {}
    for freq in ("A", "B"):
        if freq in cfg.frequencies:
            per = gg["per_frequency"][freq]
            output_posting[freq] = {
                "x_posting": float(per["x_posting"]),
                "y_posting": float(per["y_posting"]),
            }
        else:
            output_posting[freq] = {"x_posting": None, "y_posting": None}

    cube = gg["radar_grid_cube"]

    dyn: dict[str, Any] = {
        "dem_file": str(cfg.dem_path),
        "dem_file_description": f"WGS84 ellipsoidal DEM staged by nisar_wf from {cfg.dem.source}",
        "orbit_file": cfg.gslc.orbit_files.get(date) or None,
        "tec_file": cfg.gslc.tec_files.get(date) or None,
    }

    out_h5 = cfg.gslc_output(date, cfg.freq_tag)
    scratch = cfg.scratch_dir / f"gslc_{date}_freq{cfg.freq_tag}"

    runconfig = {
        "runconfig": {
            "name": f"gslc_{cfg.case_name}_{date}",
            "groups": {
                "pge_name_group": {"pge_name": "GSLC_L_PGE"},
                "input_file_group": {"input_file_path": str(granule["path"])},
                "dynamic_ancillary_file_group": dyn,
                "product_path_group": {
                    "product_path": str(cfg.gslc_dir),
                    "scratch_path": str(scratch),
                    "sas_output_file": str(out_h5),
                },
                "primary_executable": {"product_type": "GSLC"},
                "debug_level_group": {"debug_switch": bool(cfg.gslc.debug_switch)},
                "worker": {
                    "internet_access": bool(cfg.gslc.internet_access),
                    "gpu_enabled": bool(cfg.gslc.gpu_enabled),
                },
                "processing": {
                    "input_subset": {"list_of_frequencies": list_of_frequencies},
                    "geocode": {
                        "output_epsg": epsg,
                        "output_posting": output_posting,
                        # x_snap / y_snap intentionally absent -- see module docstring.
                        # The installed defaults/gslc.yaml carries them as nulls and
                        # ISCE3 deep-merges our runconfig over that, so geogrid.create
                        # sees None for both and skips its re-snap branch entirely.
                        "top_left": _top_left(),
                        "bottom_right": _bottom_right(),
                    },
                    "radar_grid_cubes": {
                        "heights": [float(h) for h in cube["heights"]],
                        "output_epsg": epsg,
                        "output_posting": {
                            "x_posting": float(cube["posting"]),
                            "y_posting": float(cube["posting"]),
                        },
                        "top_left": _top_left(),
                        "bottom_right": _bottom_right(),
                    },
                    "geo2rdr": {
                        "threshold": float(cfg.gslc.geo2rdr.threshold),
                        "maxiter": int(cfg.gslc.geo2rdr.maxiter),
                    },
                    "blocksize": {
                        "x": int(cfg.gslc.blocksize.x),
                        "y": int(cfg.gslc.blocksize.y),
                    },
                    "flatten": bool(cfg.gslc.flatten),
                    "correction_luts": {
                        "solid_earth_tides_enabled": bool(cfg.gslc.solid_earth_tides)
                    },
                },
                "output": {
                    "data_type": cfg.gslc.data_type,
                    "compression_enabled": bool(cfg.gslc.compression_enabled),
                    "compression_level": int(cfg.gslc.compression_level),
                    "chunk_size": [int(c) for c in cfg.gslc.chunk_size],
                    "shuffle": bool(cfg.gslc.shuffle),
                    "fs_strategy": cfg.gslc.fs_strategy,
                    "fs_page_size": int(cfg.gslc.fs_page_size),
                },
            },
        }
    }
    return runconfig


def write_runconfig(cfg: Config, runconfig: dict, date: str) -> Path:
    path = cfg.cfg_dir / f"gslc_{date}_freq{cfg.freq_tag}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# generated by nisar_wf stage G1 -- do not edit by hand\n")
        fh.write(f"# regenerate: python run_track_g.py --config {cfg.config_path} --only gslc --force\n")
        yaml.safe_dump(runconfig, fh, default_flow_style=False, sort_keys=False, indent=4)
    return path


def validate_runconfig_text(text: str, path: Path, log: Logger) -> None:
    """
    Validate against the INSTALLED yamale schema before spending hours geocoding.

    `nisar.workflows.dumpconfig.validate_runconfig` is the same entry point the
    PGE uses, so passing here means the runconfig is structurally acceptable to
    ISCE3 -- catching mis-nesting and bad enums in milliseconds.
    """
    try:
        # scrubbed argv: nisar imports pyre, which parses sys.argv at import
        # time and dies on our CLI flags (see util.clean_argv)
        with clean_argv():
            from nisar.workflows.dumpconfig import validate_runconfig
    except ImportError as exc:  # pragma: no cover
        raise StepFailed(
            f"cannot import nisar.workflows.dumpconfig ({exc}). "
            f"Is the isce3_env environment active?"
        ) from exc

    try:
        validate_runconfig("gslc", text, verbose=False)
    except TypeError:
        # older signature without the verbose keyword
        validate_runconfig("gslc", text)
    except Exception as exc:
        raise StepFailed(
            f"runconfig FAILED yamale validation: {path}\n"
            f"  {type(exc).__name__}: {exc}\n"
            f"  The runconfig was NOT executed. Inspect the file above; the most common\n"
            f"  cause is a mis-nested key under processing.geocode.output_posting, which\n"
            f"  must be output_posting.<A|B>.<x_posting|y_posting>."
        ) from exc
    log.info(f"  runconfig validated against the installed gslc schema: {path.name}")


# --------------------------------------------------------------------------
# output inspection / idempotency
# --------------------------------------------------------------------------
def gslc_grid_info(path: Path, freq: str) -> dict:
    """
    Read an existing GSLC's grid definition. Coordinate arrays are 1-D and small;
    the complex rasters are never read, only their `.shape` header.
    """
    grid = f"{GSLC_GRID}/frequency{freq}"
    with h5py.File(path, "r") as f:
        if grid not in f:
            raise StepFailed(f"{path.name} has no {grid}")
        g = f[grid]
        x = g["xCoordinates"][:]
        y = g["yCoordinates"][:]

        pols = []
        if "listOfPolarizations" in g:
            raw = g["listOfPolarizations"][()]
            pols = sorted(
                v.decode() if isinstance(v, bytes) else str(v)
                for v in (raw if isinstance(raw, np.ndarray) else [raw])
            )

        shape = None
        dtype = None
        for pol in pols:
            if pol in g:
                shape = list(g[pol].shape)
                dtype = str(g[pol].dtype)
                break

        epsg = None
        if "projection" in g:
            proj = g["projection"]
            try:
                epsg = int(proj[()])
            except (TypeError, ValueError):
                pass
            if epsg is None and "epsg_code" in proj.attrs:
                epsg = int(proj.attrs["epsg_code"])

        return {
            "path": str(path),
            "frequency": freq,
            "shape": shape,
            "dtype": dtype,
            "polarizations": pols,
            "epsg": epsg,
            "x_first": float(x[0]),
            "x_last": float(x[-1]),
            "y_first": float(y[0]),
            "y_last": float(y[-1]),
            "x_spacing": float(np.mean(np.diff(x))) if x.size > 1 else None,
            "y_spacing": float(np.mean(np.diff(y))) if y.size > 1 else None,
            "nx": int(x.size),
            "ny": int(y.size),
        }


def output_is_complete(cfg: Config, stack: dict, out_h5: Path, log: Logger) -> bool:
    """
    Is an existing GSLC usable, i.e. present with the grid the pin predicted?

    Checked cheaply (headers + coordinate vectors only) so a resumed run costs
    milliseconds rather than re-geocoding.
    """
    if not out_h5.exists() or out_h5.stat().st_size == 0:
        return False
    gg = stack["geogrid"]["per_frequency"]
    try:
        for freq in cfg.frequencies:
            info = gslc_grid_info(out_h5, freq)
            expect_w = int(gg[freq]["width"])
            expect_l = int(gg[freq]["length"])
            if info["shape"] != [expect_l, expect_w]:
                log.warn(
                    f"  existing {out_h5.name} freq {freq} shape {info['shape']} != "
                    f"pinned {[expect_l, expect_w]}; will regenerate"
                )
                return False
            missing = [p for p in cfg.polarizations if p not in info["polarizations"]]
            if missing:
                log.warn(
                    f"  existing {out_h5.name} freq {freq} lacks pol(s) {missing}; will regenerate"
                )
                return False
    except (StepFailed, OSError, KeyError) as exc:
        log.warn(f"  existing {out_h5.name} unreadable ({exc}); will regenerate")
        return False
    return True


def verify_output(cfg: Config, stack: dict, out_h5: Path, log: Logger) -> dict:
    """After a run, assert the product landed on the pinned grid."""
    gg = stack["geogrid"]["per_frequency"]
    infos: dict[str, dict] = {}
    for freq in cfg.frequencies:
        info = gslc_grid_info(out_h5, freq)
        infos[freq] = info
        expect_w = int(gg[freq]["width"])
        expect_l = int(gg[freq]["length"])
        if info["shape"] != [expect_l, expect_w]:
            raise StepFailed(
                f"GSLC {out_h5.name} freq {freq} has shape {info['shape']} but the pinned "
                f"geogrid predicted {[expect_l, expect_w]}.\n"
                f"  The grid pin did not take effect -- do NOT use this product for "
                f"interferometry. Check that top_left/bottom_right/output_posting survived "
                f"into {cfg.cfg_dir}."
            )
        log.info(
            f"  freq {freq}: {info['shape'][0]} x {info['shape'][1]} px, EPSG {info['epsg']}, "
            f"pols {info['polarizations']}, spacing "
            f"{info['x_spacing']:.3f} x {info['y_spacing']:.3f} m  [matches pin]"
        )
    return infos


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def run(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    started = time.time()
    res = Result(stage="gslc")
    stack = load_stack(cfg)

    # DEM is a hard precondition -- fail before rendering anything.
    # Under --dry-run it is only a warning: stage B would have created it, and
    # aborting here would defeat the point of previewing the whole pipeline.
    if not cfg.dem_path.exists():
        message = (
            f"DEM not found: {cfg.dem_path}\n"
            f"  Stage G1 cannot geocode without it. Run stage B first:\n"
            f"    python run_track_g.py --config {cfg.config_path} --only dem\n"
            f"  Or point `dem.path` at an existing WGS84-ELLIPSOIDAL DEM."
        )
        if not dry_run:
            raise StepFailed(message)
        log.warn(f"DEM not present yet ({cfg.dem_path}); stage B would create it")

    granules = stack["granules"]
    log.info(f"generating GSLC for {len(granules)} date(s), frequencies {cfg.frequencies}, "
             f"pols {cfg.polarizations}")

    # rough cost/space forecast: an unpleasant surprise here costs hours
    total_bytes = sum(
        int(stack["geogrid"]["per_frequency"][f]["uncompressed_bytes_all_pols"])
        for f in cfg.frequencies
    ) * len(granules)
    free = free_disk_bytes(cfg.root)
    log.info(
        f"output forecast: {human_bytes(total_bytes)} uncompressed across all dates "
        f"({human_bytes(free)} free on the target filesystem)"
    )
    if total_bytes > free * 0.9:
        message = (
            f"insufficient disk: forecast {human_bytes(total_bytes)} of GSLC output but only "
            f"{human_bytes(free)} free. Reduce posting, drop a polarization, or free space."
        )
        if not dry_run:
            raise StepFailed(message)
        log.warn(message)

    per_date: list[dict] = []
    generated = 0
    skipped = 0

    for granule in granules:
        date = granule["date"]
        out_h5 = cfg.gslc_output(date, cfg.freq_tag)
        log.info("")
        log.info(f"--- date {date} -> {out_h5.name}")

        runconfig = build_runconfig(cfg, stack, granule, log)
        cfg_path = write_runconfig(cfg, runconfig, date)
        text = cfg_path.read_text(encoding="utf-8")
        # validate ALWAYS, even when skipping the run: a stale-but-invalid
        # runconfig on disk is worth surfacing immediately
        validate_runconfig_text(text, cfg_path, log)

        if not force and output_is_complete(cfg, stack, out_h5, log):
            log.info(f"  GSLC already complete on the pinned grid: {out_h5}")
            log.info(f"  SKIP (use --force to regenerate)")
            skipped += 1
            per_date.append(
                {
                    "date": date,
                    "runconfig": str(cfg_path),
                    "output": str(out_h5),
                    "status": "skipped",
                    "grids": {f: gslc_grid_info(out_h5, f) for f in cfg.frequencies},
                }
            )
            continue

        if dry_run:
            log.info(f"  would run: {sys.executable} -m nisar.workflows.gslc {cfg_path}")
            log.info(f"  would write: {out_h5}")
            per_date.append(
                {"date": date, "runconfig": str(cfg_path), "output": str(out_h5),
                 "status": "dry-run"}
            )
            continue

        scratch = Path(runconfig["runconfig"]["groups"]["product_path_group"]["scratch_path"])
        scratch.mkdir(parents=True, exist_ok=True)
        out_h5.parent.mkdir(parents=True, exist_ok=True)

        t0 = time.time()
        log.info(f"  invoking nisar.workflows.gslc (this is the long step)")
        run_cmd(
            [sys.executable, "-m", "nisar.workflows.gslc", str(cfg_path)],
            log,
            tag=f"gslc/{date}",
        )
        dur = time.time() - t0

        if not out_h5.exists():
            raise StepFailed(
                f"nisar.workflows.gslc exited 0 but {out_h5} does not exist. "
                f"Check the log above and the scratch dir {scratch}."
            )
        log.info(f"  produced {out_h5.name} ({human_bytes(out_h5.stat().st_size)}) in {fmt_s(dur)}")
        grids = verify_output(cfg, stack, out_h5, log)
        generated += 1
        per_date.append(
            {
                "date": date,
                "runconfig": str(cfg_path),
                "output": str(out_h5),
                "status": "generated",
                "elapsed_s": round(dur, 1),
                "output_bytes": out_h5.stat().st_size,
                "grids": grids,
            }
        )

    if not dry_run:
        write_sidecar(
            cfg.prov_dir / "gslc.json",
            stage="gslc",
            inputs={
                "stack_json": str(cfg.stack_json),
                "dem_file": str(cfg.dem_path),
                "granules": [g["path"] for g in granules],
            },
            outputs={"per_date": per_date},
            parameters={
                "frequencies": cfg.frequencies,
                "polarizations": cfg.polarizations,
                "flatten": cfg.gslc.flatten,
                "solid_earth_tides": cfg.gslc.solid_earth_tides,
                "data_type": cfg.gslc.data_type,
                "compression_level": cfg.gslc.compression_level,
                "chunk_size": cfg.gslc.chunk_size,
                "blocksize": {"x": cfg.gslc.blocksize.x, "y": cfg.gslc.blocksize.y},
                "gpu_enabled": cfg.gslc.gpu_enabled,
                "geogrid": stack["geogrid"],
            },
            started=started,
            extra={"n_generated": generated, "n_skipped": skipped},
        )

    res.skipped = generated == 0 and skipped > 0
    res.outputs = [d["output"] for d in per_date]
    res.metrics = {"generated": generated, "skipped": skipped, "dates": [d["date"] for d in per_date]}
    return res
