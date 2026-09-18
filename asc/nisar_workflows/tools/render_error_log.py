#!/usr/bin/env python3
"""
Render a workflow document's problems-and-errors log from the evidence files.

    python tools/render_error_log.py --doc WF1 > /tmp/wf1_log.md
    python tools/render_error_log.py --doc WF1 --into docs/WF1_RSLC_FULL_TILE.md

Sources (case_studies/nepal_glof/comparison/verification/):
  errors_W1_rslc_full.json ... errors_W5_ops_and_comparison.json
      evidence-backed logs from the 2026-09-14 transcript sweep
  errors_supplement.json
      the critic's missing problems plus issues found after the sweep
  critic.json
      contradictions and the correct HOME of duplicated/misattributed entries

The critic's de-duplication is applied through MOVES and DROPS below, keyed on a
substring of the entry title, so every entry appears in exactly one document and
the others carry a one-line cross-reference. With --into, the rendered text
replaces everything between the markers
    <!-- ERROR-LOG:BEGIN -->  and  <!-- ERROR-LOG:END -->
in the target document.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

V = Path("/home/sharath/isce3/case_studies/nepal_glof/comparison/verification")
SOURCES = {"W1": "errors_W1_rslc_full.json", "W2": "errors_W2_gslc_full.json",
           "W3": "errors_W3_rslc_crop.json", "W4": "errors_W4_gslc_crop.json",
           "W5": "errors_W5_ops_and_comparison.json"}
DOC_OF = {"WF1": "W1", "WF2": "W2", "WF3": "W3", "WF4": "W4", "COMPARISON": "W5", "OPS": "OPS"}
NAMES = {"W1": "WF1 (RSLC full tile)", "W2": "WF2 (GSLC full tile)", "W3": "WF3 (RSLC cropped)",
         "W4": "WF4 (GSLC cropped)", "W5": "COMPARISON (tooling)", "OPS": "OPERATIONS"}

# (source log, title substring) -> new home. From critic.json misattributed_or_duplicate.
MOVES = [
    ("W1", "pkill -f self-match", "OPS"),
    ("W1", "Monitoring and log-capture hygiene", "OPS"),
    ("W1", "GCS destination bucket", "OPS"),
    ("W1", "GDAL geolocation-array warp silently clipped", "OPS"),
    ("W1", "Sinc resampling ringing", "OPS"),
    ("W1", "Overlay layer switcher", "OPS"),
    ("W1", "Overlay request for VV", "OPS"),
    ("W1", "nohup under Linger=no", "OPS"),
    ("W1", "Session-bound watchers died", "OPS"),
    ("W1", "`cmd | tee log", "OPS"),
    ("W1", "Disk capacity added repeatedly", "OPS"),
    ("W2", "Harness interruptions", "OPS"),
    ("W2", "Block-buffered Python stdout", "OPS"),
    ("W3", "Workflow 3 launch scripts and the geocoded RIFG lived in /tmp", "OPS"),
    ("W3", "Geocoding the R_crop RIFG with GDAL geolocation arrays", "W5"),
    ("W3", "Wrong prediction that the cropped dense-offset field", "W5"),
    ("W3", "stack.json was regenerated while the R_crop chain was running", "W4"),
    ("W4", "W4 orchestration scripts lived in /tmp", "OPS"),
    ("W4", "No completion watcher and downstream stages not chained", "OPS"),
    ("W5", "pkill -f / pgrep -f patterns", "OPS"),
    ("W5", "Monitor filter matched routine log lines", "OPS"),
    ("W5", "Publishing bucket has Public Access Prevention", "OPS"),
    ("W5", "GDAL geolocation-array warp with automatic output bounds", "OPS"),
    ("W5", "GDAL-Python dangling dataset", "OPS"),
    ("W5", "Overlay dB stretch came out at -134 dB", "OPS"),
    ("W5", "USER CAUGHT: overlay showed one layer", "OPS"),
    ("W5", "~24 h run first launched with nohup", "OPS"),
    ("W5", "Session-bound watchers died with the Claude client", "OPS"),
    ("W5", "VM too small for freq A", "OPS"),
    ("W5", "Disk increases (500->600->700->1000 GB)", "OPS"),
    ("W5", "No obvious progress log for the user", "OPS"),
    ("W5", "Redirected python stdout was block-buffered", "OPS"),
    ("W5", "VM reboot during the 3-day pause", "OPS"),
    ("W5", "USER CAUGHT: gdalwarp still running", "OPS"),
    ("W5", "VSCode/Claude client crashes", "OPS"),
    ("W5", "Agent-harness friction during the comparison", "OPS"),
    ("W5", "Crop origin not a multiple of the looks", "W3"),
    ("W5", "Cropped granules do not cover the NW", "W3"),
    ("W5", "Assistant irreversibly deleted 46 GB", "W1"),
]
# Duplicates whose content is carried by another entry that stays.
DROPS = [
    ("W5", "USER ASKED TO BE TOLD WHEN THE RUN STARTED"),     # = W2 pgrep waiter deadlock
    ("W5", "Claimed the full-tile 1x1 RUNW carries an ionosphere screen"),  # = W1 entry
    ("W1", "The benchmark ionosphere's absolute level is not pinned"),     # -> supplement W1 + W3 V1 entry
    ("W2", "freqAB GSLC name resolver fixed only in igram.py"),           # home W4; W2 keeps pointer
    ("W2", "ISCE3 ionosphere filter defaults are pixel counts"),          # design note in WF2 section 6
    ("W3", "OPEN: the native ISCE3 R_crop ionosphere is offset"),          # superseded by V1 decomposition + v2
]
# Status changes after the 2026-09-14 sweep. The evidence files stay as recorded;
# the rendered document shows the current status and says what changed.
STATUS_UPDATES = [
    ("W3", "Pixel buffer applied per band breaks the shared A/B starting range", "fixed",
     "v2 (2026-09-14): one range window on freq A, freq-B origin = freq-A origin / 8 on every date; verified B0*8 - A0 = 0 for both dates in comparison_v2 crop_geometry."),
    ("W3", "Each date cropped to its own window, and the crop origin is not written into the product", "fixed",
     "v2 writes subset_azimuth_origin, subset_range_origin_frequency{A,B}, subset_geometry_doppler, subset_band_aligned and subset_source_granule as attributes on /science/LSAR/RSLC/swaths. Each date still gets its own window (required: the dates' geometries differ)."),
    ("W3", "Cropped granules were patched in place by ad-hoc scripts; the current tool has never run end-to-end", "fixed",
     "v2 granules were produced by tools/rslc_subset.py end to end (logs/aoi_v2.log) with no manual patching."),
    ("W3", "Latent: the subsetter's rewrite of zeroDopplerStart/EndTime truncates", "fixed",
     "Now formatted with strftime('%Y-%m-%dT%H:%M:%S.%f') + '000' (always 9 fractional digits); v2 granules read e.g. 2026-07-14T23:39:24.772368000."),
    ("W3", "The AOI config still defaults to frequency B and 9x1 looks", "fixed",
     "configs/nepal_glof_aoi_v2.yaml sets frequencies [A, B] and track_r.frequency A with a fresh header; the v1 config is unchanged as a record."),
    ("W3", "Snaphu tiling changed from the benchmark", "fixed",
     "v2 config uses [4,4] / nproc 8 / overlap 256, identical to the benchmark 9x8 unwrap."),
    ("W3", "Crop origin not snapped to multiples of looks or dense-offset skip", "worked around",
     "v2 origins are multiples of 9 (azimuth) and 8 (range), so the 9x8 grids align exactly; the dense-offset grid (start 52, step 32) and the freq-B side band (needs 64) still do not align for v2 -- the comparison interpolates both; future crops snap to 64."),
    ("W5", "Crop origin not a multiple of the looks, so the 9x8 RSLC products are sub-look misaligned", "fixed",
     "v2 origins: reference (7254, 6792) = (806x9, 849x8); secondary (8001, 6784)."),
    ("W5", "Cropped granules do not cover the NW ~18-20%", "fixed",
     "Root cause was the native-Doppler window (see the zero-Doppler entry); v2 coverage per quadrant is reported in comparison_v2 lookup_verification."),
    ("W3", "Red flag missed: the R_crop ionosphere read +32.6 rad", "worked around",
     "Caught by adversarial verification (verdicts.json V1), which decomposed the offset; v2 re-measures it in COMPARISON."),
    ("W5", "R-vs-G phase difference labelled a 'planar ramp'", "fixed",
     "comparison_v2 I1b (2026-09-15): the ML plane lifts 40 m agreement only 0.892 -> 0.916, while -k*daz with k = 2*pi*fdc/1520 Hz (median 3.975 rad/line) lifts it to 0.939 and removes offset and trend; a free fit peaks at -4.00. Which chain carries daz stays open (COMPARISON.md)."),
    ("W5", "screen_agreement argument order is the reverse", "fixed",
     "v2 screen_agreement(p, q) returns offset_P_minus_Q_* keys and comparison.json carries a conventions block (P__vs__Q = P minus Q)."),
    ("W5", "nearest_cycle_combo attribution is meaningless", "fixed",
     "Removed in v2; cycle_difference() decomposes unwrapped A and B separately into modal whole cycles plus a circular remainder (v2: A 0 cycles on 99.2%, B +1 cycle on 99.2%, remainders < 0.001 cycles)."),
    ("W5", "G_crop products use NaN nodata", "fixed",
     "v2 reads every raster through c64()/f32() (nan_to_num) and phase_agreement additionally requires isfinite; report_figures v2 uses the same readers."),
    ("W5", "compare_four_way.py caches ignore their inputs", "fixed",
     "v2 keys every cached layer and the lookup by manifest_of(input sizes + mtimes, lookup key, radar window, LAYER_VERSION); outputs go only under comparison_v2/."),
    ("W5", "STATE.md, the resume document, still asserts", "fixed",
     "STATE.md rewritten 2026-09-14 with a 'Withdrawn or refuted claims' list, and updated 2026-09-15 after comparison v2."),
    ("W4", "Resolver cleanup left latent bugs", "open",
     "gridgate now resolves per (date, frequency); qa.py still resolves one band per date."),
]
DROPS += [
    ("W4", "dispersive_sigma_filtered.tif is 100% +inf (RuntimeWarning"),   # superseded by supplement entry (fixed)
    ("W5", "Ionosphere compared as a whole-scene median against an AOI median"),  # home W4 has its own entry
]

POINTERS = {
    "W2": ["freqAB GSLC name resolver (fixed first in igram.py, deferred in gridgate.py) -- see WF4",
           "ISCE3 ionosphere Gaussian defined in pixels -- see WF1; WF2 section 6 explains sigma in km"],
    "W3": ["+3.59 TECU R_full vs R_crop ionosphere offset -- decomposed in this log (A/B origin bug + whole cycles) and re-measured in COMPARISON"],
}
CATEGORY_HINTS = [("assistant_error", "judgement"), ("OOM", "resources"), ("memory", "resources"), ("disk", "resources"),
                  ("KeyError", "software"), ("ValueError", "software"), ("bug", "software"), ("GDAL", "tooling"),
                  ("tmux", "operations"), ("reboot", "operations"), ("coherence", "science"), ("ionosphere", "science"),
                  ("Doppler", "geometry"), ("offset", "geometry"), ("crop", "geometry"), ("identity", "data management"),
                  ("name", "data management")]


def category(p: dict) -> str:
    text = p["title"] + " " + p["root_cause"]
    for key, cat in CATEGORY_HINTS[1:]:
        if key.lower() in text.lower():
            return cat
    return "judgement" if p.get("assistant_error") else "other"


def collect() -> dict[str, list[dict]]:
    homes: dict[str, list[dict]] = {k: [] for k in NAMES}
    for src, fname in SOURCES.items():
        for p in json.loads((V / fname).read_text())["problems"]:
            t = p["title"]
            if any(src == s and sub in t for s, sub in DROPS):
                continue
            home = next((h for s, sub, h in MOVES if s == src and sub in t), src)
            upd = next(((st, note) for s, sub, st, note in STATUS_UPDATES if s == src and sub in t), None)
            if upd:
                p = {**p, "status": upd[0], "fix": p["fix"] + " **Update:** " + upd[1]}
            homes[home].append({**p, "_from": src})
    for p in json.loads((V / "errors_supplement.json").read_text())["problems"]:
        homes[p["home"]].append({**p, "_from": "supplement"})
    return homes


def esc(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render(doc: str) -> str:
    key = DOC_OF[doc]
    homes = collect()
    items = homes[key]
    prefix = {"W1": "WF1", "W2": "WF2", "W3": "WF3", "W4": "WF4", "W5": "CMP", "OPS": "OPS"}[key]
    out = []
    n_user = sum(p["caught_by"] == "user" for p in items)
    n_ai = sum(bool(p["assistant_error"]) for p in items)
    n_open = sum(p["status"] == "open" for p in items)
    out.append(f"{len(items)} entries: {n_user} caught by the user, {n_ai} were the assistant's own mistakes "
               f"of judgement, {n_open} still open. Every entry cites its evidence; the full records are in "
               f"`case_studies/nepal_glof/comparison/verification/`.\n")
    out.append("| id | problem | category | caught by | assistant error | status | cost |")
    out.append("|---|---|---|---|---|---|---|")
    for i, p in enumerate(items, 1):
        out.append(f"| {prefix}-{i:02d} | {esc(p['title'])} | {category(p)} | {p['caught_by']} | "
                   f"{'yes' if p['assistant_error'] else 'no'} | {p['status']} | {esc(p['cost'])[:160]} |")
    ptrs = POINTERS.get(key, [])
    others = [(h, len(v)) for h, v in homes.items() if h != key]
    out.append("")
    if ptrs:
        out.append("**Recorded elsewhere, relevant here:** " + "; ".join(ptrs) + ".\n")
    out.append("Cross-cutting operational problems (process supervision, reboots, logs, disk, agent "
               "harness) are in OPERATIONS_AND_LESSONS.md.\n")
    for i, p in enumerate(items, 1):
        out.append(f"#### {prefix}-{i:02d} — {p['title']}\n")
        out.append(f"- **Symptom:** {p['symptom']}")
        out.append(f"- **Root cause:** {p['root_cause']}")
        out.append(f"- **Fix:** {p['fix']}")
        out.append(f"- **Cost:** {p['cost']}")
        out.append(f"- **Caught by:** {p['caught_by']}{' · assistant error' if p['assistant_error'] else ''} · **Status:** {p['status']}")
        out.append(f"- **Evidence:** {p['evidence']}")
        out.append(f"- **Automation lesson:** {p['automation_lesson']}\n")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc", required=True, choices=sorted(DOC_OF))
    ap.add_argument("--into", type=Path, default=None)
    ap.add_argument("--counts", action="store_true")
    a = ap.parse_args()
    if a.counts:
        for h, v in collect().items():
            print(f"{h:4s} {len(v):3d}")
        return 0
    text = render(a.doc)
    if a.into:
        s = a.into.read_text()
        pat = re.compile(r"(<!-- ERROR-LOG:BEGIN -->)(.*?)(<!-- ERROR-LOG:END -->)", re.S)
        if not pat.search(s):
            raise SystemExit(f"{a.into}: markers not found")
        a.into.write_text(pat.sub(lambda m: m.group(1) + "\n" + text + "\n" + m.group(3), s))
        print(f"rendered {a.doc} log into {a.into}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
