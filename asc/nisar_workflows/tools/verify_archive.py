#!/usr/bin/env python3
"""Verify the GCS archive against the local tree: per prefix, compare file count and total bytes."""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archive_to_gcs import BUCKET, PLAN, gb, local_size  # noqa: E402


def remote(prefix):
    r = subprocess.run(["gcloud", "storage", "ls", "-l", "-r", f"{BUCKET}/{prefix}"], capture_output=True, text=True)
    n = b = 0
    names = set()
    for line in r.stdout.splitlines():
        p = line.split()
        if len(p) >= 3 and p[0].isdigit() and p[2].startswith("gs://"):
            n += 1
            b += int(p[0])
            names.add(p[2].split(f"/{prefix}/", 1)[-1])
    return b, n, names


def main():
    bad = 0
    print(f"{'prefix':52s} {'local':>12s} {'remote':>12s} {'files l/r':>14s}  status")
    for src, suffix, exclude, _ in PLAN:
        if not src.exists():
            continue
        lb, ln = local_size(src, exclude)
        rb, rn, names = remote(suffix)
        if src.is_dir():
            import re as _re
            rx = _re.compile(exclude) if exclude else None
            lnames = {str(f.relative_to(src)) for f in src.rglob("*")
                      if f.is_file() and not (rx and rx.search("/" + str(f.relative_to(src))))}
            missing, extra = sorted(lnames - names), sorted(names - lnames)
        else:
            missing, extra = ([] if rn == 1 else ["(file)"]), []
        ok = not missing and not extra and abs(rb - lb) < 1024
        bad += 0 if ok else 1
        note = "ok" if ok else f"{len(missing)} missing, {len(extra)} extra"
        print(f"{suffix:52s} {gb(lb):>12s} {gb(rb):>12s} {ln:6d}/{rn:<7d}  {note}")
        for x in (missing[:3] + extra[:3]):
            print(f"      - {x}")
    print("\nall prefixes verified" if not bad else f"\n{bad} prefix(es) do not match")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
