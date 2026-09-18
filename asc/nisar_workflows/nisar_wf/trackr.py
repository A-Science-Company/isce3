"""
Track R -- RSLC coregistration in RADAR coordinates via `nisar.workflows.insar`.

The whole chain is one stock ISCE3 invocation; what is ours is the runconfig and
the guard rails around it:

    rdr2geo -> geo2rdr -> coarse_resample -> dense_offsets -> rubbersheet
            -> fine_resample -> crossmul   [-> filter -> unwrap -> geocode]

Two facts shape every decision in this module.

**The reference image is never resampled.** Only the secondary is. That is what
Track R buys over Track G, whose geocode-to-a-pinned-grid resamples both dates.

**Looks are consumed at `crossmul`, the LAST stage.** Every stage before it runs
at the full radar-grid resolution, so raising `looks` reduces neither RAM, nor
scratch, nor runtime -- only the size of the final interferogram. `rdr2geo`
alone writes x/y/z as Float64 over the entire reference grid regardless. This is
the single most expensive misconception available here, so `estimate_scratch()`
reports it explicitly and the disk gate refuses to start without the room.

Output identity includes frequency, polarization AND looks. That is deliberate:
the plan records four separate bugs whose shared shape was "an output identity
that omitted something which changes the output", so a stale artifact passed an
`exists()` check. Change the looks and you get a different file, not a silent
reuse.
"""

from __future__ import annotations


import shutil
from pathlib import Path
from typing import Any

import yaml

from .config import Config
from .util import (
    Logger,
    Result,
    StepFailed,
    free_disk_bytes,
    human_bytes,
    read_json,
    run_cmd,
    write_json,
)

# Scratch cost per REFERENCE-grid pixel, per stage, in bytes.
#
# MEASURED, not derived, on nepal_glof 20260714x20260726 freq B (53200 x 6781 =
# 360,749,200 px -> 37.7 GB of scratch). An earlier version of this model counted
# only rdr2geo + geo2rdr + the two resampled SLCs (40 B/px) and was 2.6x low,
# because two whole stages write full-grid rasters of their own:
#
#   rdr2geo              24.4   x, y, z as Float64 (defaults write no other layer)
#   geo2rdr              16.0   range.off + azimuth.off, Float64
#   coarse_resample_slc   8.0   coregistered_secondary.slc, complex64
#   dense_offsets         8.0   offset/corr/snr/covariance + a reference.slc copy
#   rubbersheet_offsets  32.0   filtered range+azimuth at FULL grid, plus culled
#   fine_resample_slc     8.0   coregistered_secondary.slc, complex64
#   crossmul              8.0   reference.slc flattened out of the RSLC HDF5
#   baseline              0.04
#                       ------
#                       104.4
#
# Note that dense_offsets and rubbersheet are decimated in their OWN products
# (208 x 1659 here) but still emit full-grid rasters alongside them, which is
# why "the offsets are tiny" is the wrong intuition for sizing this.
#
# None of these terms depends on `looks`. crossmul's own 8.0 is the REFERENCE
# SLC unpacked to a flat raster, not the interferogram -- so a re-run at
# different looks reuses every byte above and adds only the RIFG itself.
_SCRATCH_BYTES_PER_PX = {
    "rdr2geo": 24.4,
    "geo2rdr": 16.0,
    "coarse_resample": 8.0,
    "dense_offsets": 8.0,
    "rubbersheet": 32.0,
    "fine_resample": 8.0,
    "crossmul": 8.0,
    "baseline": 0.04,
}


# --------------------------------------------------------------------------
# stack helpers
# --------------------------------------------------------------------------
def load_stack(cfg: Config) -> dict:
    """Read stack.json, with an actionable error when ingest has not run."""
    if not cfg.stack_json.exists():
        raise StepFailed(
            f"{cfg.stack_json} not found. Track R reads the granule list, dates and\n"
            f"  radar-grid shapes from it. Run ingest first:\n"
            f"    python run_track_r.py --config {cfg.config_path} --only ingest"
        )
    return read_json(cfg.stack_json)


def pair_list(cfg: Config, stack: dict) -> list[tuple[str, str]]:
    """Explicit `track_r.pairs`, else every consecutive date pair."""
    if cfg.track_r.pairs:
        out = []
        known = set(stack["dates"])
        for p in cfg.track_r.pairs:
            if len(p) != 2:
                raise StepFailed(f"track_r.pairs entry {p} must be [reference, secondary]")
            ref, sec = str(p[0]), str(p[1])
            for d in (ref, sec):
                if d not in known:
                    raise StepFailed(
                        f"track_r.pairs references date {d}, which is not in the stack "
                        f"({sorted(known)}). Re-run ingest, or fix the config."
                    )
            out.append((ref, sec))
        return out
    dates = list(stack["dates"])
    return [(dates[i], dates[i + 1]) for i in range(len(dates) - 1)]


def granule_for(stack: dict, date: str) -> str:
    """Absolute path of the RSLC acquired on `date`."""
    for g in stack["granules"]:
        if g["date"] == date:
            return g["path"]
    raise StepFailed(
        f"no granule in stack.json for date {date}; have "
        f"{[g['date'] for g in stack['granules']]}"
    )


def radar_shape(stack: dict, date: str, freq: str) -> tuple[int, int]:
    """(azimuth_lines, range_samples) of one date's radar grid at `freq`."""
    for g in stack["granules"]:
        if g["date"] == date:
            fr = g["frequencies"].get(freq)
            if fr is None:
                raise StepFailed(
                    f"granule {date} has no frequency {freq}; "
                    f"present: {sorted(g['frequencies'])}"
                )
            az, rg = fr["shape"]
            return int(az), int(rg)
    raise StepFailed(f"no granule in stack.json for date {date}")


def check_pol_available(stack: dict, date: str, freq: str, pol: str) -> None:
    for g in stack["granules"]:
        if g["date"] == date:
            pols = g["frequencies"][freq]["polarizations"]
            if pol not in pols:
                raise StepFailed(
                    f"granule {date} frequency {freq} has polarizations {pols}, "
                    f"not '{pol}'. Fix track_r.polarization."
                )
            return


# --------------------------------------------------------------------------
# sizing
# --------------------------------------------------------------------------
def estimate_scratch(stack: dict, ref: str, sec: str, freq: str,
                     az_looks: int = 0, rg_looks: int = 0) -> dict:
    """
    Scratch bytes one pair commits, itemised by stage.

    Every term is at FULL radar-grid resolution: looks are applied at crossmul,
    after all of this has already been written.
    """
    az_r, rg_r = radar_shape(stack, ref, freq)
    az_s, rg_s = radar_shape(stack, sec, freq)
    px_ref = az_r * rg_r
    px_sec = az_s * rg_s

    # Every term is sized by the REFERENCE grid: the secondary is resampled onto
    # it, so its own dimensions never set the cost.
    per_stage = {
        f"{name}_bytes": int(round(rate * px_ref))
        for name, rate in _SCRATCH_BYTES_PER_PX.items()
    }
    total = sum(_SCRATCH_BYTES_PER_PX.values()) * px_ref

    # RIFG_ifgram_dem -- Float32 DEM on the INTERFEROGRAM grid, so it is the one
    # scratch term that shrinks with looks. Invisible in the freq-B calibration
    # (9x1 -> 40 Mpx -> 0.16 GB) and 11.5 GB at 1x1 on freq A, which is why the
    # flat B/px model above misses it. It also costs a SECOND full-resolution
    # Topo pass at 1x1 -- 13 h, on par with rdr2geo itself -- so its real price
    # is runtime, not bytes.
    if az_looks and rg_looks:
        ifg_px = (az_r // az_looks) * (rg_r // rg_looks)
        per_stage["ifgram_dem_bytes"] = ifg_px * 4
        total += ifg_px * 4

    out = {
        "reference_px": px_ref,
        "secondary_px": px_sec,
        "bytes_per_px": sum(_SCRATCH_BYTES_PER_PX.values()),
        "total_bytes": int(round(total)),
    }
    out.update(per_stage)
    return out


def rifg_shape(stack: dict, ref: str, freq: str, az_looks: int, rg_looks: int) -> tuple[int, int]:
    az, rg = radar_shape(stack, ref, freq)
    return az // az_looks, rg // rg_looks


def rifg_bytes(stack: dict, ref: str, freq: str, az_looks: int, rg_looks: int) -> int:
    """
    Size of the delivered RIFG.

    This is the ONE term that `looks` changes, and leaving it out of the disk
    gate is how a run gets approved and then dies with ENOSPC hours later. At
    1x1 on a full frequency-A frame it is 34.6 GB -- comparable to a whole
    frequency-B pair's scratch, and 16x what 4x4 would cost.

    complex64 wrapped interferogram + float32 coherence = 12 bytes per output
    pixel. HDF5 overhead on top is negligible at this scale.
    """
    az, rg = rifg_shape(stack, ref, freq, az_looks, rg_looks)
    return az * rg * 12


def ground_cell(stack: dict, ref: str, freq: str, az_looks: int, rg_looks: int) -> tuple[float, float]:
    """
    Multilooked ground cell in metres, (azimuth, ground range).

    `sceneCenterGroundRangeSpacing` already carries the 1/sin(incidence)
    projection from slant range, so the ground cell is a plain product.
    """
    for g in stack["granules"]:
        if g["date"] == ref:
            fr = g["frequencies"][freq]
            return (
                az_looks * float(fr["sceneCenterAlongTrackSpacing"]),
                rg_looks * float(fr["sceneCenterGroundRangeSpacing"]),
            )
    raise StepFailed(f"no granule for {ref}")


# --------------------------------------------------------------------------
# paths -- identity includes freq, pol and looks
# --------------------------------------------------------------------------
def pair_tag(cfg: Config, freq: str, pol: str) -> str:
    lk = cfg.track_r.looks_for(freq)
    return f"{freq}_{pol}_{lk.azimuth}x{lk.range}"


def pair_paths(cfg: Config, ref: str, sec: str) -> dict:
    """Every path Track R owns for one pair."""
    tr = cfg.track_r
    freq, pol = tr.frequency, tr.polarization
    tag = pair_tag(cfg, freq, pol)
    lk = tr.looks_for(freq)

    pair_dir = cfg.root / tr.pair_dir_template.format(ref=ref, sec=sec)
    scratch = cfg.scratch_dir / "trackR" / f"{ref}_{sec}_{tag}"

    # The RUNW/GUNW grid is set by phase_unwrap looks, NOT crossmul looks, so
    # its identity must include them. Without this a 9x8 unwrap silently
    # overwrites the 1x1 RUNW -- the same "output identity omits something that
    # changes the output" bug the project has already hit four times.
    # Scratch stays keyed on crossmul looks alone: rdr2geo/geo2rdr/resample are
    # unwrap-independent and SHOULD be shared across unwrap passes.
    out_tag = tag
    if (tr.product_type in ("RUNW", "GUNW", "RIFG_RUNW_GUNW")
            and (tr.phase_unwrap_azimuth_looks != lk.azimuth
                 or tr.phase_unwrap_range_looks != lk.range)):
        out_tag = (f"{tag}_unw{tr.phase_unwrap_azimuth_looks}"
                   f"x{tr.phase_unwrap_range_looks}")
    return {
        "dir": pair_dir,
        "scratch": scratch,
        "product_path": pair_dir,
        "runconfig": cfg.cfg_dir / f"insar_{ref}_{sec}_{tag}.yaml",
        "logfile": cfg.log_dir / f"insar_{ref}_{sec}_{tag}.log",
        "output": pair_dir / f"{tr.product_type}_{ref}_{sec}_{out_tag}.h5",
        "sidecar": pair_dir / f"trackR_{ref}_{sec}_{out_tag}.json",
        "tag": tag,
    }


# --------------------------------------------------------------------------
# block sizing
# --------------------------------------------------------------------------
# Bytes held per (line x range-sample) by each stage's working block.
_BLOCK_BYTES_PER_PX = {
    "rdr2geo": 24,      # x, y, z as Float64
    "geo2rdr": 16,      # range + azimuth offsets as Float64
    "dense_offsets": 16,  # reference + secondary, complex64
    "crossmul": 32,     # reference + secondary, complex64, oversampled x2
}


def lines_for_budget(width: int, stage: str, budget_bytes: int,
                     configured: int, floor: int = 32) -> int:
    """
    Largest block height whose working set fits `budget_bytes`, capped at the
    configured value.

    A FIXED `lines_per_block` is a latent OOM, because the block's memory is
    lines x WIDTH, and width is a property of the frequency, not of the config.
    The stock 1000 lines costs 163 MB of rdr2geo on frequency B and 1302 MB on
    frequency A -- the same number, 8x the memory, because freq A has 54244
    range samples against freq B's 6781. On a 3.9 GB box the second one is an
    OOM kill several hours into a run.

    Deriving from width means one config is correct for both bands.
    """
    per_line = max(1, width * _BLOCK_BYTES_PER_PX[stage])
    return max(floor, min(configured, int(budget_bytes // per_line)))


# --------------------------------------------------------------------------
# runconfig rendering
# --------------------------------------------------------------------------
def render_runconfig(cfg: Config, stack: dict, ref: str, sec: str) -> dict:
    """
    Build one `nisar.workflows.insar` runconfig.

    Only OUR file is yamale-validated (runconfig.py:52-62), never the merged
    result, so every schema-required key must be present here even when the
    installed defaults would supply it.

    Two keys are required by CODE rather than by the schema:
      * `processing.input_subset.list_of_frequencies` -- runconfig.py:110-114
        calls `.keys()` on it unconditionally; omitting it is a KeyError.
      * `logging` as a whole -- insar.py:187 reads cfg['logging']['path'] and
        defaults/insar.yaml has no `logging` key at all.
    """
    tr = cfg.track_r
    freq, pol = tr.frequency, tr.polarization
    lk = tr.looks_for(freq)
    p = pair_paths(cfg, ref, sec)

    # Block heights derived from the ACTUAL range width of this frequency.
    _, width = radar_shape(stack, ref, freq)
    budget = int(tr.block_budget_mb * 1024 ** 2)
    lpb = {
        st: lines_for_budget(width, st, budget, conf)
        for st, conf in (("rdr2geo", tr.rdr2geo_lines_per_block),
                         ("geo2rdr", tr.geo2rdr_lines_per_block),
                         ("dense_offsets", tr.dense_offsets_lines_per_block),
                         ("crossmul", tr.crossmul_lines_per_block))
    }

    # phase_unwrap and ionosphere are emitted ONLY when the product type can
    # actually reach them. Emitting an ionosphere block under product_type RIFG
    # validates fine and then does nothing (insar.py:120-124), which is exactly
    # the kind of silent no-op this wrapper exists to prevent.
    wants_unwrap = tr.product_type in ("RUNW", "GUNW", "RIFG_RUNW_GUNW")
    unwrap_block = {}
    iono_block = {}
    if wants_unwrap:
        unwrap_block = {
            "phase_unwrap": {
                # NOT the same looks as crossmul: these re-multilook from the
                # coregistered SLCs so the RUNW gets its own real coherence,
                # and they are what keeps unwrap.py's whole-array read in RAM.
                "range_looks": int(tr.phase_unwrap_range_looks),
                "azimuth_looks": int(tr.phase_unwrap_azimuth_looks),
                "algorithm": str(tr.unwrap_algorithm),
                **({"crossmul_path": str(tr.unwrap_crossmul_path)}
                   if tr.unwrap_crossmul_path else {}),
                "bridge": {"enabled": bool(tr.unwrap_bridge_enabled)},
                "snaphu": {
                    **({"nlooks": float(tr.unwrap_nlooks)}
                       if tr.unwrap_nlooks is not None else {}),
                    "ntiles": [int(x) for x in tr.unwrap_ntiles],
                    "tile_overlap": [int(x) for x in tr.unwrap_tile_overlap],
                    "nproc": int(tr.unwrap_nproc),
                    # Both default to True upstream and both re-solve or
                    # relabel over the WHOLE grid, which defeats tiling.
                    "single_tile_reoptimize": bool(tr.unwrap_single_tile_reoptimize),
                    "regrow_conncomps": bool(tr.unwrap_regrow_conncomps),
                },
            }
        }
    if tr.ionosphere_enabled and wants_unwrap:
        iono_block = {
            "ionosphere_phase_correction": {
                "enabled": True,
                "spectral_diversity": str(tr.ionosphere_spectral_diversity),
                "lines_per_block": int(tr.ionosphere_lines_per_block),
                # Frequency A ONLY. ionosphere.py builds the frequency B pair
                # itself from decimated frequency A offsets, so listing B here
                # is not how you get the side band -- and a second Track R run
                # on B is not required.
                "list_of_frequencies": {freq: [pol]},
                "dispersive_filter": {
                    "enabled": bool(tr.ionosphere_filter_enabled),
                    "filter_coherence_threshold": float(
                        tr.ionosphere_filter_coherence_threshold),
                    "median_filter_size": int(tr.ionosphere_median_filter_size),
                },
            }
        }

    return {
        "runconfig": {
            "name": f"trackR_{cfg.case_name}_{ref}_{sec}_{p['tag']}",
            "groups": {
                "pge_name_group": {"pge_name": "INSAR_L_PGE"},
                "input_file_group": {
                    "reference_rslc_file": granule_for(stack, ref),
                    "secondary_rslc_file": granule_for(stack, sec),
                },
                "dynamic_ancillary_file_group": {
                    "dem_file": str(cfg.dem_path),
                },
                "product_path_group": {
                    "product_path": str(p["product_path"]),
                    "scratch_path": str(p["scratch"]),
                    "sas_output_file": str(p["output"]),
                },
                "primary_executable": {"product_type": tr.product_type},
                "debug_level_group": {"debug_switch": False},
                "worker": {"gpu_enabled": bool(tr.gpu_enabled)},
                "processing": {
                    # Frequencies present in the defaults but absent here are
                    # DELETED, so this is also how the other band is excluded.
                    "input_subset": {"list_of_frequencies": {freq: [pol]}},
                    "rdr2geo": {
                        "threshold": float(tr.rdr2geo_threshold),
                        "numiter": int(tr.rdr2geo_numiter),
                        "extraiter": int(tr.rdr2geo_extraiter),
                        "lines_per_block": lpb["rdr2geo"],
                    },
                    "geo2rdr": {
                        "threshold": float(tr.geo2rdr_threshold),
                        "maxiter": int(tr.geo2rdr_maxiter),
                        "lines_per_block": lpb["geo2rdr"],
                    },
                    "coarse_resample": {
                        "lines_per_tile": int(tr.coarse_lines_per_tile),
                        "columns_per_tile": int(tr.coarse_columns_per_tile),
                    },
                    "dense_offsets": {
                        "enabled": bool(tr.dense_offsets_enabled),
                        "lines_per_block": lpb["dense_offsets"],
                        "window_range": int(tr.window_range),
                        "window_azimuth": int(tr.window_azimuth),
                        "half_search_range": int(tr.half_search_range),
                        "half_search_azimuth": int(tr.half_search_azimuth),
                        "skip_range": int(tr.skip_range),
                        "skip_azimuth": int(tr.skip_azimuth),
                    },
                    # NEVER enable offsets_product alongside a non-ROFF
                    # product_type: insar.py:65-67 has no `'ROFF' in out_paths`
                    # guard, so it validates cleanly and then raises
                    # KeyError: 'ROFF' at runtime.
                    "offsets_product": {"enabled": False},
                    "fine_resample": {
                        "enabled": bool(tr.dense_offsets_enabled),
                        "lines_per_tile": int(tr.fine_lines_per_tile),
                        "columns_per_tile": int(tr.fine_columns_per_tile),
                    },
                    "crossmul": {
                        # insar_runconfig.py:139 and ionosphere_runconfig.py:348
                        # both inject this, but UnwrapRunConfig does NOT -- so
                        # `python -m nisar.workflows.unwrap` dies with
                        # KeyError: 'flatten_path' at crossmul.py:45 when it
                        # re-multilooks. It is the directory holding
                        # geo2rdr/freq{X}/range.off (crossmul.py:107).
                        # crossmul.run() reads exactly seven keys. Five come
                        # from the installed defaults; these two are injected at
                        # load time by CrossmulRunConfig / InsarRunConfig
                        # (crossmul_runconfig.py:28, insar_runconfig.py:139) and
                        # NOT by UnwrapRunConfig, so the standalone unwrap path
                        # KeyErrors on both without them.
                        "flatten_path": str(p["scratch"]),
                        "coregistered_slc_path": str(p["scratch"]),
                        "range_looks": int(lk.range),
                        "azimuth_looks": int(lk.azimuth),
                        "flatten": bool(tr.crossmul_flatten),
                        "oversample": int(tr.crossmul_oversample),
                        "lines_per_block": lpb["crossmul"],
                        "common_band_range_filter": bool(tr.common_band_range_filter),
                        "common_band_azimuth_filter": bool(tr.common_band_azimuth_filter),
                    },
                    **unwrap_block,
                    **iono_block,
                },
                # 'a', not 'w': runconfig.py:102 applies this BEFORE
                # Persistence reads the log, so 'w' truncates the very file the
                # resume logic depends on. (Persistence is still unusable here
                # for an unrelated reason -- persistence.py:65 resets its
                # success flag per line -- but do not compound it.)
                "logging": {"path": str(p["logfile"]), "write_mode": "a"},
            },
        }
    }


def validate_runconfig(path: Path) -> None:
    """
    yamale-validate our file against the installed insar schema.

    This is the same check `RunConfig` performs, run early so a typo fails in
    the runconfig stage rather than an hour into geocoding.
    """
    import yamale
    from nisar.workflows.helpers import WORKFLOW_SCRIPTS_DIR

    schema = yamale.make_schema(f"{WORKFLOW_SCRIPTS_DIR}/schemas/insar.yaml", parser="ruamel")
    data = yamale.make_data(str(path), parser="ruamel")
    yamale.validate(schema, data)


# --------------------------------------------------------------------------
# stage 1 -- render the runconfigs
# --------------------------------------------------------------------------
def run_runconfig(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    res = Result(stage="R1")
    tr = cfg.track_r
    stack = load_stack(cfg)
    freq, pol = tr.frequency, tr.polarization
    lk = tr.looks_for(freq)

    pairs = pair_list(cfg, stack)
    if not pairs:
        raise StepFailed("no pairs to form; the stack has fewer than two dates")

    az_cell, rg_cell = ground_cell(stack, pairs[0][0], freq, lk.azimuth, lk.range)
    log.info(
        f"Track R: frequency {freq}, polarization {pol}, product {tr.product_type}"
    )
    log.info(
        f"looks {lk.azimuth} az x {lk.range} rg  ->  ground cell "
        f"{az_cell:.1f} m azimuth x {rg_cell:.1f} m ground range"
    )
    log.info(f"{len(pairs)} pair(s): " + ", ".join(f"{r}x{s}" for r, s in pairs))

    # Disk gate. rdr2geo + geo2rdr + two resampled SLCs, all at full radar-grid
    # resolution -- looks do not reduce any of it.
    total = 0
    for ref, sec in pairs:
        check_pol_available(stack, ref, freq, pol)
        check_pol_available(stack, sec, freq, pol)
        est = estimate_scratch(stack, ref, sec, freq, lk.azimuth, lk.range)
        out_bytes = rifg_bytes(stack, ref, freq, lk.azimuth, lk.range)
        total += est["total_bytes"] + out_bytes
        az, rg = rifg_shape(stack, ref, freq, lk.azimuth, lk.range)
        log.info(
            f"  {ref}x{sec}: reference grid {est['reference_px'] / 1e6:.0f} Mpx"
            f"  ->  scratch {human_bytes(est['total_bytes'])}"
            f"  +  RIFG {az} x {rg} ({az * rg / 1e6:.0f} Mpx) "
            f"{human_bytes(out_bytes)}"
            f"  =  {human_bytes(est['total_bytes'] + out_bytes)}"
        )
        log.info(
            f"      rdr2geo {human_bytes(est['rdr2geo_bytes'])}"
            f" + geo2rdr {human_bytes(est['geo2rdr_bytes'])}"
            f" + rubbersheet {human_bytes(est['rubbersheet_bytes'])}"
            f" + dense_offsets {human_bytes(est['dense_offsets_bytes'])}"
            f" + resample {human_bytes(est['coarse_resample_bytes'] + est['fine_resample_bytes'])}"
            f"   ({est['bytes_per_px']:.1f} B/px, measured)"
        )

    # Credit scratch already on disk for this exact pair+tag: a re-run
    # overwrites those rasters IN PLACE, so they are not additional demand.
    # Without this the gate compares total occupancy against free space and
    # refuses runs that actually fit -- e.g. 323.7 GiB "needed" against
    # 316.8 GiB free when 118 GiB of it is already written.
    existing = 0
    for ref, sec in pairs:
        sp = pair_paths(cfg, ref, sec)["scratch"]
        if sp.is_dir():
            existing += sum(f.stat().st_size for f in sp.rglob("*") if f.is_file())
    if existing:
        log.info(
            f"crediting {human_bytes(existing)} of scratch already on disk for "
            f"this pair+tag (overwritten in place, not additional demand)"
        )
        total = max(0, total - existing)

    free = free_disk_bytes(cfg.root)
    log.info(
        f"disk needed {human_bytes(total)} (scratch + delivered product) across "
        f"{len(pairs)} pair(s); free {human_bytes(free)}; "
        f"margin {human_bytes(free - total)} ({100 * (free - total) / max(free, 1):.1f}%)"
    )
    if free < total:
        msg = (
            f"insufficient disk: Track R on frequency {freq} needs "
            f"{human_bytes(total)} of scratch but only {human_bytes(free)} is free.\n"
            f"  Looks do NOT reduce this -- they are applied at crossmul, after every\n"
            f"  coregistration stage has already written at full radar-grid resolution.\n"
            f"  Options: run frequency B instead of A, run one pair at a time, or free disk."
        )
        if tr.enforce_disk_gate:
            raise StepFailed(msg)
        log.warn(msg)
    elif free - total < tr.min_free_gb * 1024**3:
        log.warn(
            f"only {human_bytes(free - total)} would remain after Track R scratch, "
            f"below the configured track_r.min_free_gb of {tr.min_free_gb} GB"
        )

    if dry_run:
        for ref, sec in pairs:
            log.info(f"  would write {pair_paths(cfg, ref, sec)['runconfig']}")
        res.skipped = True
        res.notes = [f"dry run; {len(pairs)} runconfig(s) not written"]
        return res

    written: list[str] = []
    for ref, sec in pairs:
        p = pair_paths(cfg, ref, sec)
        p["dir"].mkdir(parents=True, exist_ok=True)
        p["scratch"].mkdir(parents=True, exist_ok=True)
        cfg.cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        doc = render_runconfig(cfg, stack, ref, sec)
        with open(p["runconfig"], "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)

        validate_runconfig(p["runconfig"])
        log.info(f"wrote + validated {p['runconfig']}")
        written.append(str(p["runconfig"]))

    res.outputs = written
    res.metrics = {
        "pairs": len(pairs),
        "frequency": freq,
        "polarization": pol,
        "looks_azimuth": lk.azimuth,
        "looks_range": lk.range,
        "ground_cell_m": [round(az_cell, 2), round(rg_cell, 2)],
        "scratch_bytes_total": total,
    }
    return res


# --------------------------------------------------------------------------
# stage 2 -- run the InSAR workflow
# --------------------------------------------------------------------------
def run_insar(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    res = Result(stage="R2")
    tr = cfg.track_r
    stack = load_stack(cfg)
    pairs = pair_list(cfg, stack)

    # A dry run reports intent only. In particular it must NOT demand a
    # runconfig that the dry run of the previous step deliberately did not
    # write -- that would make `--dry-run` over the whole pipeline impossible.
    if dry_run:
        for ref, sec in pairs:
            p = pair_paths(cfg, ref, sec)
            log.info(f"  would run: python -m nisar.workflows.insar {p['runconfig']}")
            if not p["runconfig"].exists():
                log.info("     (runconfig not on disk yet; the runconfig step writes it)")
        res.skipped = True
        res.notes = [f"dry run; {len(pairs)} pair(s) not run"]
        return res

    todo: list[tuple[str, str]] = []
    for ref, sec in pairs:
        p = pair_paths(cfg, ref, sec)
        if not p["runconfig"].exists():
            raise StepFailed(
                f"runconfig missing: {p['runconfig']}\n"
                f"  Run the runconfig step first."
            )
        if p["output"].exists() and not force:
            log.info(f"{ref}x{sec}: {p['output'].name} already present -- skipping")
            continue
        todo.append((ref, sec))

    if not todo:
        res.skipped = True
        res.outputs = [str(pair_paths(cfg, r, s)["output"]) for r, s in pairs]
        res.notes = [f"all {len(pairs)} pair(s) already produced"]
        return res

    produced: list[str] = []
    for ref, sec in todo:
        p = pair_paths(cfg, ref, sec)
        log.info(f"--- {ref} x {sec} [{p['tag']}] ---")

        # A crashed run leaves a partial scratch tree that the next attempt
        # would happily reuse. --force means start clean.
        if force and p["scratch"].exists():
            log.info(f"--force: clearing scratch {p['scratch']}")
            shutil.rmtree(p["scratch"], ignore_errors=True)
        p["scratch"].mkdir(parents=True, exist_ok=True)

        run_cmd(
            ["python", "-m", "nisar.workflows.insar", str(p["runconfig"])],
            log,
            tag=f"insar/{ref}x{sec}",
        )

        if not p["output"].exists():
            raise StepFailed(
                f"insar exited 0 but {p['output']} was not written. "
                f"Check {p['logfile']}."
            )
        log.info(
            f"{p['output'].name}: {human_bytes(p['output'].stat().st_size)}"
        )
        produced.append(str(p["output"]))

    res.outputs = produced
    res.metrics = {"pairs_run": len(todo)}
    return res


# --------------------------------------------------------------------------
# stage 3 -- QA, decimated reads only
# --------------------------------------------------------------------------
def _find_dataset(h5, suffix: str) -> str | None:
    """Locate the first dataset whose path ends with `suffix`."""
    hits: list[str] = []

    def visit(name, obj):
        import h5py

        if isinstance(obj, h5py.Dataset) and name.endswith(suffix):
            hits.append(name)

    h5.visititems(visit)
    return hits[0] if hits else None


def run_qa(cfg: Config, log: Logger, force: bool = False, dry_run: bool = False) -> Result:
    """
    Summarise each RIFG: grid shape and a coherence histogram from a decimated
    read. Never loads a full raster.
    """
    import h5py
    import numpy as np

    res = Result(stage="R3")
    tr = cfg.track_r
    stack = load_stack(cfg)
    pairs = pair_list(cfg, stack)
    lk = tr.looks_for(tr.frequency)

    if dry_run:
        res.skipped = True
        res.notes = ["dry run"]
        return res

    report: dict[str, Any] = {}
    for ref, sec in pairs:
        p = pair_paths(cfg, ref, sec)
        if not p["output"].exists():
            log.warn(f"{ref}x{sec}: {p['output'].name} not present -- skipping QA")
            continue

        with h5py.File(p["output"], "r") as h5:
            coh_path = _find_dataset(h5, "coherenceMagnitude")
            ifg_path = _find_dataset(h5, "wrappedInterferogram")
            entry: dict[str, Any] = {"file": str(p["output"])}

            if ifg_path:
                entry["interferogram_dataset"] = ifg_path
                entry["shape"] = list(h5[ifg_path].shape)
            if coh_path:
                d = h5[coh_path]
                entry["coherence_dataset"] = coh_path
                # decimate to <= ~4 Mpx: a stride, not a read-then-subsample
                step = max(1, int(np.sqrt(d.size / 4e6)))
                sub = d[::step, ::step]
                sub = np.asarray(sub, dtype=np.float32)
                good = sub[np.isfinite(sub)]
                if good.size:
                    entry["coherence"] = {
                        "decimation_step": step,
                        "samples": int(good.size),
                        "median": round(float(np.median(good)), 4),
                        "mean": round(float(good.mean()), 4),
                        "frac_above_0.3": round(float((good > 0.3).mean()), 4),
                        "frac_above_0.5": round(float((good > 0.5).mean()), 4),
                    }
                    log.info(
                        f"{ref}x{sec}: shape {entry.get('shape')}  "
                        f"coherence median {entry['coherence']['median']:.3f}  "
                        f">0.3 {entry['coherence']['frac_above_0.3'] * 100:.1f}%  "
                        f">0.5 {entry['coherence']['frac_above_0.5'] * 100:.1f}%"
                    )
                    # A coherence of exactly 1.0 everywhere is the documented
                    # signature of a degenerate single-sample estimator.
                    if float(np.median(good)) > 0.999:
                        log.warn(
                            f"{ref}x{sec}: coherence median is ~1.0, the signature of a "
                            f"1x1-look estimator. Looks are {lk.azimuth}x{lk.range}; "
                            f"if that is 1x1 the value is degenerate, not good coherence."
                        )

        az_cell, rg_cell = ground_cell(stack, ref, tr.frequency, lk.azimuth, lk.range)
        entry["looks"] = {"azimuth": lk.azimuth, "range": lk.range}
        entry["ground_cell_m"] = [round(az_cell, 2), round(rg_cell, 2)]
        report[f"{ref}_{sec}"] = entry

        write_json(p["sidecar"], entry)
        res.outputs.append(str(p["sidecar"]))

    if not report:
        res.skipped = True
        res.notes = ["no RIFG products found to QA"]
    res.metrics = {"pairs_reported": len(report)}
    return res
