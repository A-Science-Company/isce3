#!/usr/bin/env python3
"""
Overlay upstream fixes onto the installed isce3/nisar packages.

    conda run -n isce3_env python tools/apply_patches.py          # apply
    conda run -n isce3_env python tools/apply_patches.py --check   # report only
    conda run -n isce3_env python tools/apply_patches.py --revert  # undo

Why this exists rather than a source build: some upstream fixes are PURE PYTHON
and depend only on APIs the released conda-forge package already exports. For
those, overlaying one file costs seconds; building isce3 from source costs hours,
a full compiler toolchain, ~250 GB of scratch, and leaves you running an
unreleased 0.26.0-dev you now own.

Every patch here MUST satisfy three conditions, checked at apply time:
  1. it is Python only -- no compiled extension is involved;
  2. every symbol it imports already exists in the installed package;
  3. the file currently installed is byte-identical to the pre-patch upstream
     version, so we know exactly what is being replaced.

Condition 3 is what makes this safe to re-run and safe to revert: the original
is kept alongside as <name>.orig and restored by --revert.

Remove a patch from PATCHES once conda-forge ships a release containing it.
"""

from __future__ import annotations

import argparse
import filecmp
import importlib
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCH_DIR = HERE / "patches"

PATCHES = [
    {
        "name": "resample_slc_v2 optimized HDF5 reader",
        "upstream": "isce-framework/isce3#372 (f42cea75)",
        "src": PATCH_DIR / "resample_slc_v2.py",
        "target": "nisar/workflows/resample_slc_v2.py",
        # Imports the patched file needs. Checked BEFORE overlaying -- an
        # upstream fix written against a newer isce3 would otherwise install
        # cleanly and then fail at import time, mid-run.
        "requires": [
            ("isce3.io", "HDF5OptimizedReader"),
            ("isce3.core.types", "ComplexFloat16Decoder"),
            ("isce3.core.types", "is_complex32"),
        ],
        "why": (
            "resample_secondary_rslc_onto_reference() opened the secondary RSLC via\n"
            "    getSlcDatasetAsNativeComplex(), which uses h5py's DEFAULT chunk cache\n"
            "    rather than one sized to the dataset's own chunking. Resampling reads the\n"
            "    secondary repeatedly, so an undersized cache re-reads the same chunks from\n"
            "    disk. Matters directly for Track R."
        ),
    },
]


def site_packages() -> Path:
    import nisar
    return Path(nisar.__file__).resolve().parent.parent


def check_requires(patch: dict) -> list[str]:
    missing = []
    for mod, sym in patch["requires"]:
        try:
            if not hasattr(importlib.import_module(mod), sym):
                missing.append(f"{mod}.{sym}")
        except Exception as exc:
            missing.append(f"{mod} ({type(exc).__name__})")
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="report status, change nothing")
    g.add_argument("--revert", action="store_true", help="restore the .orig backups")
    args = ap.parse_args()

    sp = site_packages()
    print(f"site-packages: {sp}\n")
    rc = 0

    for p in PATCHES:
        target = sp / p["target"]
        backup = target.with_suffix(target.suffix + ".orig")
        print(f"[{p['name']}]  {p['upstream']}")

        if not p["src"].exists():
            print(f"  ERROR patch source missing: {p['src']}\n")
            rc = 1
            continue
        if not target.exists():
            print(f"  ERROR target missing: {target}\n")
            rc = 1
            continue

        applied = filecmp.cmp(p["src"], target, shallow=False)

        if args.revert:
            if backup.exists():
                shutil.copy2(backup, target)
                backup.unlink()
                print("  REVERTED from .orig\n")
            else:
                print("  nothing to revert (no .orig)\n")
            continue

        if applied:
            print("  already applied\n")
            continue

        missing = check_requires(p)
        if missing:
            print("  REFUSING -- the installed isce3 lacks: " + ", ".join(missing))
            print("  This patch was written against a newer isce3. Overlaying it would\n"
                  "  install cleanly and then fail at import time, mid-run.\n")
            rc = 1
            continue

        if args.check:
            print("  NOT applied (run without --check to apply)")
            print(f"  why: {p['why']}\n")
            continue

        if not backup.exists():
            shutil.copy2(target, backup)
        shutil.copy2(p["src"], target)
        print(f"  APPLIED (original kept at {backup.name})")
        print(f"  why: {p['why']}\n")

    return rc


if __name__ == "__main__":
    sys.exit(main())
