#!/usr/bin/env python3
"""
Archive the NISAR workflow work to gs://s1-slc/nisar_workflow/ and write the manifest that describes it.

    python tools/archive_to_gcs.py --dry-run      # what would be uploaded, with sizes
    python tools/archive_to_gcs.py                # upload (needs a valid gcloud login)
    python tools/archive_to_gcs.py --manifest     # regenerate README.md from what is in the bucket, upload nothing else

Layout in the bucket:
    README.md                     this archive's manifest (generated)
    code/nisar_workflows/         drivers, modules, tools, configs, docs
    reports/                      both study reports (PDF + self-contained HTML)
    nepal_glof/                   study 1: four-workflow comparison (RSLC/GSLC, full tile and cropped)
    nepal_nisar_ascending/        study 2: the 26 Aug 2026 outburst event study
Input RSLCs stay in gs://s1-slc/nepal/nisar/ascending/tile_1/SLC/ and are not copied here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ISCE3 = Path("/home/sharath/isce3")
CASES = ISCE3 / "case_studies"
BUCKET = "gs://s1-slc/nisar_workflow"

# (local path, remote suffix, exclude regex or None, description for the manifest)
PLAN = [
    (HERE, "code/nisar_workflows", r"(^|/)(__pycache__|\.pytest_cache)/|\.pyc$", "Drivers, modules, tools, configs and docs"),
    (ISCE3 / "CLAUDE.md", "code/CLAUDE.md", None,
     "Working context for an agent or person picking this up: environments, data locations, conventions, known gotchas"),
    (ISCE3 / "AGENTS.md", "code/AGENTS.md", None, "Pointer to CLAUDE.md for non-Claude agents"),
    (ISCE3 / "asc/env", "code/env", None,
     "Conda environment files, including full package exports of isce3_env (ISCE3 0.25.12) and insar_ts (MintPy, snaphu, dolphin)"),
    (ISCE3 / "nepal_glof_four_workflow_report.pdf", "reports/nepal_glof_four_workflow_report.pdf", None,
     "Study 1 report (PDF, 15 pages)"),
    (ISCE3 / "nepal_glof_nisar_event_report.pdf", "reports/nepal_glof_nisar_event_report.pdf", None,
     "Study 2 report (PDF, 13 pages)"),
    (CASES / "nepal_glof/comparison_v2/report/nepal_glof_four_workflow_report.html",
     "reports/nepal_glof_four_workflow_report.html", None, "Study 1 report (self-contained HTML)"),
    (CASES / "nepal_nisar_ascending/report/glof_event/nepal_glof_nisar_event_report.html",
     "reports/nepal_glof_nisar_event_report.html", None, "Study 2 report (self-contained HTML)"),
    (CASES / "nepal_glof/comparison", "nepal_glof/comparison", None, "Study 1: v1 comparison and adversarial verification"),
    (CASES / "nepal_glof/comparison_v2", "nepal_glof/comparison_v2", None,
     "Study 1: v2 comparison, alignment test, ionosphere transfer, GUNW validation, report figures"),
    (CASES / "nepal_glof/L2_GUNW", "nepal_glof/L2_GUNW", None, "Study 1: NISAR GUNW used to validate the chain"),
    (CASES / "nepal_glof/logs", "nepal_glof/logs", None, "Study 1: run logs (ISCE3 journals with stage timings)"),
    (CASES / "nepal_glof/cfg", "nepal_glof/cfg", None, "Study 1: rendered ISCE3 runconfigs"),
    (CASES / "nepal_glof/provenance", "nepal_glof/provenance", None, "Study 1: provenance records"),
    (CASES / "nepal_glof/stack.json", "nepal_glof/stack.json", None, "Study 1: stack metadata"),
    (CASES / "nepal_glof/ARCHIVE_MANIFEST.md", "nepal_glof/ARCHIVE_MANIFEST.md", None, "Study 1: what was archived and what was deleted locally"),
    (CASES / "nepal_nisar_ascending/crop", "nepal_nisar_ascending/crop", None,
     "Study 2: the seven RSLCs cut to the AOI (direct input to coregistration)"),
    (CASES / "nepal_nisar_ascending/coreg", "nepal_nisar_ascending/coreg", r"isce3/pairs/.*\.h5$",
     "Study 2: coregistered SLC stack, geometry, per-pair RIFG, dense offsets, manifests (the ISCE3 tree's RIFG copies are "
     "hard links to ifg/ and are not duplicated here)"),
    (CASES / "nepal_nisar_ascending/timeseries", "nepal_nisar_ascending/timeseries", None,
     "Study 2: interferograms, unwrapped phase, GUNW corrections, both MintPy inversions, GeoTIFF exports, and the "
     "per-date flattening range offsets"),
    (CASES / "nepal_nisar_ascending/L2_GUNW", "nepal_nisar_ascending/L2_GUNW", None,
     "Study 2: the six NISAR GUNW products supplying the corrections"),
    (CASES / "nepal_nisar_ascending/aux", "nepal_nisar_ascending/aux", None, "Study 2: the NISAR DEM used for processing"),
    (CASES / "nepal_nisar_ascending/report", "nepal_nisar_ascending/report", None,
     "Study 2: analysis.json, the 20 report figures and the focus-area GeoTIFFs"),
    (CASES / "nepal_nisar_ascending/logs", "nepal_nisar_ascending/logs", None, "Study 2: logs of the superseded ad-hoc chain"),
    (CASES / "nepal_nisar_ascending/stack.json", "nepal_nisar_ascending/stack.json", None, "Study 2: stack metadata"),
]


def sh(cmd, check=True):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"failed: {cmd}\n{r.stderr[:2000]}")
    return r


def local_size(path: Path, exclude: str | None) -> tuple[int, int]:
    import re
    if path.is_file():
        return path.stat().st_size, 1
    rx = re.compile(exclude) if exclude else None
    n = b = 0
    for f in path.rglob("*"):
        if f.is_file() and not f.is_symlink():
            rel = str(f.relative_to(path))
            if rx and rx.search("/" + rel):
                continue
            b += f.stat().st_size
            n += 1
    return b, n


def upload(entry, dry: bool):
    src, dstsuffix, exclude, _ = entry
    dst = f"{BUCKET}/{dstsuffix}"
    if src.is_file():
        cmd = ["gcloud", "storage", "cp", str(src), dst]
    else:
        cmd = ["gcloud", "storage", "rsync", "-r", str(src), dst]
        if exclude:
            cmd += ["--exclude", exclude]
    if dry:
        return " ".join(cmd)
    print(f"  {' '.join(cmd)}", flush=True)
    sh(cmd)
    return None


# gcloud storage rsync --exclude did not match these reliably, so they are removed from the bucket after each sync: the
# archive then matches its manifest whatever the client does.
PRUNE = [f"{BUCKET}/code/nisar_workflows/**/__pycache__/**",
         f"{BUCKET}/nepal_nisar_ascending/coreg/**/isce3/pairs/**/*.h5"]


def prune(dry: bool):
    for pat in PRUNE:
        r = sh(["gcloud", "storage", "ls", "-r", pat], check=False)
        objs = [l for l in r.stdout.splitlines() if l.startswith("gs://") and not l.endswith("/")]
        if not objs:
            continue
        print(f"  pruning {len(objs)} object(s) matching {pat.split('nisar_workflow/')[-1]}")
        if not dry:
            proc = subprocess.run(["gcloud", "storage", "rm", "-I"], input="\n".join(objs), text=True, capture_output=True)
            if proc.returncode != 0:
                print(f"    prune failed: {proc.stderr[:300]}")


def remote_inventory() -> dict:
    """Object count and bytes under each top-level prefix, read back from the bucket."""
    out = {}
    r = sh(["gcloud", "storage", "ls", "-l", "-r", f"{BUCKET}/**"], check=False)
    if r.returncode != 0:
        return out
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[2].startswith(BUCKET):
            rel = parts[2][len(BUCKET) + 1:]
            top = rel.split("/")[0] if "/" in rel else "(root)"
            e = out.setdefault(top, {"objects": 0, "bytes": 0})
            e["objects"] += 1
            e["bytes"] += int(parts[0])
    return out


def gb(b):
    return f"{b / 1e9:.2f} GB" if b >= 1e8 else f"{b / 1e6:.1f} MB"


def build_manifest(inv: dict, plan_rows: list) -> str:
    ana = json.loads((CASES / "nepal_nisar_ascending/report/glof_event/analysis.json").read_text())["stats"]
    fo, gr, rx = ana["focus"], ana["glacier_vs_ring"], ana["relaxed"]
    ctl = ana["controls"]["event 19–31 Aug"]
    total_b = sum(v["bytes"] for v in inv.values())
    total_n = sum(v["objects"] for v in inv.values())
    rows = "\\n".join(f"| `{suffix}` | {gb(b)} | {n:,} | {desc} |" for suffix, b, n, desc in plan_rows)
    top = "\\n".join(f"| `{k}/` | {gb(v['bytes'])} | {v['objects']:,} |" for k, v in sorted(inv.items()))
    return f"""# NISAR ISCE3 workflow archive

Everything from the NISAR InSAR work: the four-workflow comparison that established the processing chain, and the Nepal
glacial-lake-outburst event study that applied it. Written {dt.date.today().isoformat()} from the processing VM
(`/home/sharath/isce3`). Total in this prefix: **{gb(total_b)} in {total_n:,} objects**.

Everything here was produced by the code in `code/nisar_workflows/`, and every number in either report is regenerated by the
tools in that tree — no value in the reports was entered by hand. If you are an agent or a person picking this work up, read
`code/CLAUDE.md` first: it explains the environments, the two pipeline modules, where each dataset lives, the conventions this
project follows and the mistakes already paid for. The resume point for the work itself is
`code/nisar_workflows/STATE.md`.

## What is where

| prefix | size | objects |
|---|---|---|
{top}

### Detail

| path | size | files | contents |
|---|---|---|---|
{rows}

**Inputs.** The seven NISAR L1 RSLC granules the studies were processed from (174 GB) are not duplicated here; they live in
`gs://s1-slc/nepal/nisar/ascending/tile_1/SLC/`, all seven in the provisional (PR) tier used by the processing. That prefix
also holds an urgent-response (UR) copy of the 31 August acquisition, which was **not** what the studies used — take the `_PR_`
files. **Also not in this archive:** the ISCE3 scratch of both studies (geometry rasters, resampled SLCs, dense
offsets — about 400 GB) was deleted after verification and is regenerable from the RSLCs and the archived configs. The six
per-pair RIFG products exist once, under `coreg/*/ifg/`; the ISCE3 tree's copies of them were hard links locally and are not
duplicated in the archive.

## Study 1 — four-workflow comparison (`nepal_glof/`)

Pair 20260714 × 20260726, ascending track 98 frame 16, HH. Four workflows compared against the full-tile RSLC benchmark:
RSLC full tile, GSLC full tile, RSLC cropped to an AOI, GSLC cropped. What it settled:

- **Crop-first RSLC is interferometrically equivalent to the full tile** (wrapped phase R 0.994, identical coherence), and
  about six times cheaper. This is why the event study crops first.
- **GSLC dates are registered by geometry only**, which costs about 5 % coherence and leaves a carrier phase term; delivered
  GSLC samples are spectrally white and cannot be realigned afterwards.
- **A crop's ionosphere has the right shape but an unreferenced level**; the full-tile screen sliced onto an AOI reproduces the
  benchmark exactly. Since a full-tile ionosphere costs 7.5 h per pair, the event study takes ionosphere from the GUNW instead.
- Validated against the NISAR GUNW of the same pair: wrapped phase R 0.978 on coherent ground, ionosphere shape 0.0135 TECU.

Report: `reports/nepal_glof_four_workflow_report.pdf` (and `.html`). Evidence: `nepal_glof/comparison_v2/comparison.json`.

## Study 2 — the 26 August 2026 outburst (`nepal_nisar_ascending/`)

Seven acquisitions, 20 June – 12 September 2026, same track. Processing: crop → coregistration onto 20260726 → 9 pre-event and
3 post-event interferograms at 9 × 8 looks (~40 m) → snaphu in 3 × 3 tiles with 150-pixel overlap → GUNW ionosphere, troposphere
and solid-earth tides → MintPy inversion. What it found:

- **No pre-event motion is resolved.** In the glacier bounding box with a 100 % buffer ({fo['area_km2']:.1f} km²) the velocity is
  {fo['velocity_mm_yr']['50']:+.0f} ± {fo['velocity_sigma_mm_yr']:.0f} mm/yr, smaller than its own uncertainty; the detection
  limit is about 40 mm over the two months.
- **The ice needs the connected-component mask turned off.** With MintPy's default rule only 9 of 670 glacier pixels were
  inverted; with every pixel inverted, {rx['glacier_velocity_mm_yr']:+.0f} ± {rx['glacier_sigma_mm_yr']:.0f} mm/yr from 405
  pixels. Over the four consecutive pre-event intervals the glacier differs from the surrounding rock by up to
  {max(abs(r['difference_mm']) for r in gr['intervals']):.0f} mm with alternating sign, summing to
  {gr['cumulative_mm']:+.0f} ± {gr['cumulative_null_sd_mm']:.0f} mm (p = {gr['cumulative_p']:.2f}) — changing snow and ice
  scattering, not creep.
- **The event shows in backscatter, not phase.** Change concentrates within a kilometre of the glacier (near-to-far spread ratio
  {ctl['ratio_near_far']:.2f} against 1.1–1.3 for two control pairs) and forms clusters descending to 2870 m, 7 km away.
- **Coherence loss is not diagnostic here**: the pair that does not span the event loses more coherence than the one that does.

Report: `reports/nepal_glof_nisar_event_report.pdf` (and `.html`). Evidence:
`nepal_nisar_ascending/report/glof_event/analysis.json`.

## Rebuilding the analysis from this archive

```bash
gcloud storage rsync -r {BUCKET}/code/nisar_workflows ./nisar_workflows
gcloud storage rsync -r {BUCKET}/nepal_nisar_ascending/coreg      <workdir>/coreg
gcloud storage rsync -r {BUCKET}/nepal_nisar_ascending/timeseries <workdir>/timeseries
gcloud storage rsync -r {BUCKET}/nepal_nisar_ascending/L2_GUNW    <workdir>/L2_GUNW
gcloud storage rsync -r {BUCKET}/nepal_nisar_ascending/aux        <workdir>/aux
cd nisar_workflows
python tools/glof_event_analysis.py && python tools/build_glof_event_report.py   # figures and report
```

To redo the processing itself, fetch the RSLCs from `gs://s1-slc/nepal/nisar/ascending/tile_1/SLC/` into `<workdir>/L1_RSLC`,
point `coreg_configs/nepal_nisar_ascending_rslc.yaml` at `<workdir>`, and run:

```bash
python nisar_coreg.py      -c coreg_configs/nepal_nisar_ascending_rslc.yaml    run --detach   # ~9 h, 6 pairs
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml  run --detach   # ~20 min
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_post_event.yaml run --detach   # ~15 min
```

Processing parameters live in `coreg_configs/defaults.yaml` and `ts_configs/defaults.yaml`; the values actually used are frozen
in a `params.json` beside every product, so an archived product states its own provenance.

## Software

ISCE3 0.25.12 (conda env `isce3_env`), snaphu-py 0.4.1, MintPy 1.6.4 and dolphin 0.42.5 (conda env `insar_ts`), GDAL 3.12.
The GUNW products were generated by the project with ISCE3 0.25.16. Environment files are in
`code/nisar_workflows/` and `asc/env/`.

## Caveats recorded with the data

- The urgent-response GUNW for 31 Aug – 12 Sep carries no troposphere cubes, so the post-event series has ionosphere and tides
  but no troposphere applied.
- Ionosphere screens are level-unreferenced; only relative deformation is meaningful.
- Everything is referenced to one pixel at 28.3284° N, 85.3784° E; a common-mode motion of the whole scene would be invisible.
- The `timeseries/*/mintpy/` directories are the strict inversion (connected-component mask on) and `mintpy_relaxed/` the
  relaxed one (mask off, temporal coherence ≥ 0.3). The reports use the strict run for wide-area statistics and the relaxed run
  for the glacier zone.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--manifest", action="store_true", help="only regenerate and upload README.md")
    a = ap.parse_args()

    plan_rows, total = [], 0
    for src, suffix, exclude, desc in PLAN:
        if not src.exists():
            print(f"MISSING, skipped: {src}")
            continue
        b, n = local_size(src, exclude)
        plan_rows.append((suffix + ("/" if src.is_dir() else ""), b, n, desc))
        total += b
    print(f"\n{len(plan_rows)} entries, {gb(total)} locally:")
    for suffix, b, n, _ in plan_rows:
        print(f"  {gb(b):>10s}  {n:6,d} files  -> {BUCKET}/{suffix}")

    if a.dry_run:
        print("\ncommands that would run:")
        for e in PLAN:
            if e[0].exists():
                print("  " + upload(e, True))
        return 0

    if not a.manifest:
        print("\nuploading:")
        for e in PLAN:
            if e[0].exists():
                upload(e, False)

    if not a.manifest:
        print("\npruning objects that do not belong in the archive:")
        prune(a.dry_run)
    inv = remote_inventory()
    if not inv:
        raise SystemExit("could not read the bucket back (is the gcloud login valid?)")
    md = build_manifest(inv, plan_rows).replace("\\n", "\n")
    out = Path("/tmp/nisar_workflow_README.md")
    out.write_text(md)
    sh(["gcloud", "storage", "cp", str(out), f"{BUCKET}/README.md"])
    (CASES / "nepal_nisar_ascending/report/ARCHIVE_README.md").write_text(md)
    sh(["gcloud", "storage", "cp", str(out), f"{BUCKET}/nepal_nisar_ascending/report/ARCHIVE_README.md"])
    print(f"\nwrote {BUCKET}/README.md ({len(md)} chars); local copy in case_studies/nepal_nisar_ascending/report/")
    print(f"archive now holds {sum(v['objects'] for v in inv.values()):,} objects, "
          f"{gb(sum(v['bytes'] for v in inv.values()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
