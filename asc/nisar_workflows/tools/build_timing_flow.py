#!/usr/bin/env python3
"""
Flow chart + timeline of the full-tile RSLC 1x1 benchmark (WF1), with the measured duration of every stage.

    python tools/build_timing_flow.py

Durations are the "successfully ran <stage> in N seconds" lines of the ISCE3 journal
case_studies/nepal_glof/logs/insar_20260714_20260726_A_HH_1x1.log (8-core / 31 GB VM). Failed attempts on the earlier
2-core VM and the out-of-memory restarts are excluded. Writes
case_studies/nepal_glof/comparison_v2/report/rslc_1x1_stage_timings.html.
"""

from __future__ import annotations

import html
from pathlib import Path

OUT = Path("/home/sharath/isce3/case_studies/nepal_glof/comparison_v2/report/rslc_1x1_stage_timings.html")

# (key, label, seconds, phase, run, note, journal line)
S = {
    "rdr2geo": ("rdr2geo", 5952.640, "geom", "int", "lon/lat/height for every reference sample", 1),
    "geo2rdr": ("geo2rdr", 398.282, "geom", "int", "secondary range/azimuth offsets from orbit + DEM", 1),
    "prep": ("product prep", 7481.556, "prep", "int", "DEM layers; includes the 1×1 interferogram-grid DEM pass (≈1 h 37 m)", 1),
    "coarse": ("coarse resample", 3225.730, "coreg", "int", "secondary resampled with geometry offsets", 1),
    "dense": ("dense offsets", 8666.956, "coreg", "int", "cross-correlation every 32 samples", 1),
    "rubber": ("rubber sheet", 1929.453, "coreg", "int", "cull, fill, filter; offsets on the full grid", 1),
    "fine": ("fine resample", 693.142, "coreg", "int", "secondary resampled with refined offsets", 1),
    "xmul1": ("crossmul 1×1 (pass 1)", 1579.475, "ifg", "int", "ref × conj(sec), flattened", 1),
    "xmul2": ("crossmul 1×1 (pass 2)", 933.903, "ifg", "int", "second crossmul pass in the same run", 1),
    "uprep": ("product prep 1×1", 6982.676, "unw", "unw1", "RUNW 1×1 preparation", 1),
    "snaphu": ("snaphu unwrap 1×1", 46233.266, "unw", "unw1", "32 × 32 tiles; 1×1 unwrapped phase", 1),
    "aprep": ("product prep 9×8", 223.120, "iono", "iono", "interferogram grid at 9×8", 1),
    "axmul": ("crossmul A 9×8", 1053.326, "iono", "iono", "freq-A interferogram formed directly at 9×8", 1),
    "aunw": ("unwrap A 9×8", 1389.928, "iono", "iono", "snaphu, 4 × 4 tiles", 1),
    "brdr": ("rdr2geo B", 780.524, "iono", "iono", "freq-B geometry (8× fewer range samples)", 1),
    "bg2r": ("geo2rdr B", 41.440, "iono", "iono", "", 1),
    "bprep": ("product prep B", 879.302, "iono", "iono", "", 1),
    "bres": ("resample B", 432.282, "iono", "iono", "uses freq-A offsets decimated 8× in range", 1),
    "bxmul": ("crossmul B 9×8", 126.195, "iono", "iono", "", 1),
    "bunw": ("unwrap B", 49.692, "iono", "iono", "", 1),
    "solve": ("split-spectrum solve, filter, write", 1626.813, "iono", "iono", "journal “Ionosphere”: solve ≈13 m, Gaussian ≈4 m, unwrap-error correction ≈4 m, write ≈4 m (scratch timestamps)", 1),
}
RUNS = {"int": "Integrated run → RIFG 1×1", "unw1": "Unwrap 1×1 → RUNW 1×1", "iono": "9×8 unwrap + iono → RUNW 9×8"}
ORDER = {"int": ["rdr2geo", "geo2rdr", "prep", "coarse", "dense", "rubber", "fine", "xmul1", "xmul2"],
         "unw1": ["uprep", "snaphu"],
         "iono": ["aprep", "axmul", "aunw", "brdr", "bg2r", "bprep", "bres", "bxmul", "bunw", "solve"]}
PHASES = {"geom": "Geometry", "prep": "Product prep", "coreg": "Coregistration", "ifg": "Interferogram 1×1", "unw": "Unwrap 1×1", "iono": "Ionosphere path (9×8)"}
LOOKS = {"rdr2geo": "no", "geo2rdr": "no", "prep": "yes (DEM pass)", "coarse": "no", "dense": "no", "rubber": "no", "fine": "no",
         "xmul1": "partly", "xmul2": "partly", "uprep": "yes", "snaphu": "yes", "aprep": "yes", "axmul": "partly", "aunw": "yes",
         "brdr": "no (B grid)", "bg2r": "no (B grid)", "bprep": "yes", "bres": "no (B grid)", "bxmul": "partly", "bunw": "yes", "solve": "yes"}


def hms(s):
    s = int(round(s))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    if h:
        return f"{h} h {m:02d} m"
    if m:
        return f"{m} m {sec:02d} s"
    return f"{sec} s"


def hms_full(s):
    s = int(round(s))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}"


def esc(t):
    return html.escape(t, quote=True)


run_total = {r: sum(S[k][1] for k in ks) for r, ks in ORDER.items()}
grand = sum(run_total.values())
coreg_only = sum(S[k][1] for k in ("rdr2geo", "geo2rdr", "coarse", "dense", "rubber", "fine"))
iono_only_low = coreg_only + run_total["iono"]
iono_only_high = iono_only_low + (S["prep"][1] - 5820)      # the 1×1 run's non-DEM prep work, if it recurs

# ------------------------------------------------------------------ flow chart SVG
W, NW, NH, PITCH = 1090, 290, 54, 78
LX = 30                      # left column
AX, BX = 450, 790            # ionosphere columns
CW = 270


def node(x, y, key, w=NW, h=NH, out=False):
    if out:
        label, sub = key
        return (f'<g class="out"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h / 2}"/>'
                f'<text class="nl" x="{x + w / 2}" y="{y + 23}" text-anchor="middle">{esc(label)}</text>'
                f'<text class="ns" x="{x + w / 2}" y="{y + 40}" text-anchor="middle">{esc(sub)}</text></g>')
    label, sec, ph, _, _, _ = S[key]
    return (f'<g class="node ph-{ph}"><rect class="box" x="{x}" y="{y}" width="{w}" height="{h}" rx="6"/>'
            f'<rect class="stripe" x="{x}" y="{y}" width="6" height="{h}" rx="3"/>'
            f'<text class="nl" x="{x + 16}" y="{y + 22}">{esc(label)}</text>'
            f'<text class="nd" x="{x + 16}" y="{y + 41}">{esc(hms(sec))}</text>'
            f'<text class="nsec" x="{x + w - 10}" y="{y + 41}" text-anchor="end">{int(round(sec)):,} s</text></g>')


def arrow(x1, y1, x2, y2, label="", lx=None, ly=None, anchor="start", cls="ar"):
    pts = f"{x1},{y1} {x2},{y2}"
    lab = ""
    if label:
        lx = (x1 + x2) / 2 + 8 if lx is None else lx
        ly = (y1 + y2) / 2 + 4 if ly is None else ly
        lab = f'<text class="al" x="{lx}" y="{ly}" text-anchor="{anchor}">{esc(label)}</text>'
    return f'<polyline class="{cls}" points="{pts}" marker-end="url(#ah)"/>{lab}'


def elbow(pts, label="", lx=0, ly=0, anchor="start", cls="ar"):
    p = " ".join(f"{x},{y}" for x, y in pts)
    lab = f'<text class="al" x="{lx}" y="{ly}" text-anchor="{anchor}">{esc(label)}</text>' if label else ""
    return f'<polyline class="{cls}" points="{p}" marker-end="url(#ah)"/>{lab}'


g = []
cx = LX + NW / 2
ys = {}
y = 20
g.append(node(LX, y, ("Reference + secondary RSLC", "freq A HH · 53 200 × 54 244 samples"), out=True))
ys["in"] = y
seq = ["rdr2geo", "geo2rdr", "prep", "coarse", "dense", "rubber", "fine", "xmul1", "xmul2"]
labels_between = {"rdr2geo": "reference geometry", "geo2rdr": "lon / lat / height", "prep": "run order", "coarse": "geometry offsets",
                  "dense": "coarsely aligned secondary", "rubber": "offsets every 32 samples", "fine": "full-grid offsets", "xmul1": "coregistered SLCs",
                  "xmul2": ""}
prev_bottom = y + NH
for k in seq:
    y += PITCH
    g.append(arrow(cx, prev_bottom, cx, y - 2, labels_between[k]))
    g.append(node(LX, y, k))
    ys[k] = y
    prev_bottom = y + NH
y += PITCH
g.append(arrow(cx, prev_bottom, cx, y - 2, "wrapped φ, 1×1"))
g.append(node(LX, y, ("RIFG 1×1", "28.6 GB wrapped interferogram"), out=True))
ys["rifg"] = y
prev_bottom = y + NH
for k in ("uprep", "snaphu"):
    y += PITCH
    g.append(arrow(cx, prev_bottom, cx, y - 2, "RIFG" if k == "uprep" else ""))
    g.append(node(LX, y, k))
    ys[k] = y
    prev_bottom = y + NH
y += PITCH
g.append(arrow(cx, prev_bottom, cx, y - 2, "unwrapped φ, 1×1"))
g.append(node(LX, y, ("RUNW 1×1", "17.4 GB · ionosphere disabled"), out=True))
H_left = y + NH

# ionosphere columns
ay0 = ys["fine"]
acx, bcx = AX + CW / 2, BX + CW / 2
# freq-B column starts beside dense offsets
by = ys["coarse"]
g.append(f'<text class="colh" x="{BX}" y="{by - 14}">freq-B side band</text>')
b_seq = ["brdr", "bg2r", "bprep", "bres", "bxmul", "bunw"]
prevb = None
for i, k in enumerate(b_seq):
    yy = by + i * PITCH
    if prevb is not None:
        g.append(arrow(bcx, prevb, bcx, yy - 2, "" if k != "bxmul" else "resampled B SLCs"))
    g.append(node(BX, yy, k, w=CW))
    ys[k] = yy
    prevb = yy + NH
# rubber sheet offsets -> resample B
yr = ys["rubber"] + NH / 2
g.append(elbow([(LX + NW, yr), (BX - 40, yr), (BX - 40, ys["bres"] + NH / 2), (BX - 2, ys["bres"] + NH / 2)],
               "freq-A offsets ÷ 8 in range", LX + NW + 10, yr - 7))
# freq-A 9x8 column starts beside fine resample
ay = ys["fine"]
g.append(f'<text class="colh" x="{AX}" y="{ay - 14}">freq A at 9×8</text>')
a_seq = ["aprep", "axmul", "aunw"]
preva = None
for i, k in enumerate(a_seq):
    yy = ay + i * PITCH
    if preva is not None:
        g.append(arrow(acx, preva, acx, yy - 2, "wrapped φ_A, 9×8" if k == "aunw" else ""))
    g.append(node(AX, yy, k, w=CW))
    ys[k] = yy
    preva = yy + NH
yf = ys["fine"] + NH / 2
g.append(arrow(LX + NW, yf, AX - 2, yf, "coregistered SLCs", LX + NW + 10, yf - 7))
# merge into solve
sy = max(ys["aunw"], ys["bunw"]) + PITCH + 10
sx, sw = AX + 60, (BX + CW) - (AX + 60)
g.append(elbow([(acx, ys["aunw"] + NH), (acx, sy - 2)], "unwrapped φ_A", acx + 8, ys["aunw"] + NH + 20))
g.append(elbow([(bcx, ys["bunw"] + NH), (bcx, sy - 2)], "unwrapped φ_B", bcx + 8, ys["bunw"] + NH + 20))
g.append(node(sx, sy, "solve", w=sw))
g.append(arrow(sx + sw / 2, sy + NH, sx + sw / 2, sy + NH + PITCH - NH - 2, "dispersive screen"))
g.append(node(sx + sw / 2 - 150, sy + PITCH, ("RUNW 9×8 + ionosphere screen", "0.61 GB · screen ≈ 3 km × 25 km effective"), w=300, out=True))
H = max(H_left, sy + PITCH + NH) + 20

# phase bracket labels on the far left? (use colour stripes + legend instead)
flow_svg = (f'<svg class="flow" viewBox="0 0 {W} {H}" role="img" aria-label="Flow of the full-tile RSLC 1x1 benchmark: geometry, product preparation, '
            f'coregistration and 1x1 interferogram in one run, then a 1x1 unwrap branch and a 9x8 ionosphere branch, with measured durations">'
            '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>' + "".join(g) + "</svg>")

# ------------------------------------------------------------------ run lanes (Gantt)
GW, GL, GR = 1040, 270, 30
scale_h = 15.0
px = (GW - GL - GR) / scale_h
lanes = []
lane_y = 40
for r in ("int", "unw1", "iono"):
    t = 0.0
    lanes.append(f'<text class="ll" x="{GL - 12}" y="{lane_y + 17}" text-anchor="end">{esc(RUNS[r])}</text>')
    lanes.append(f'<text class="lt" x="{GL - 12}" y="{lane_y + 33}" text-anchor="end">{esc(hms(run_total[r]))}</text>')
    for k in ORDER[r]:
        label, sec, ph, _, _, _ = S[k]
        w_ = sec / 3600 * px
        x_ = GL + t / 3600 * px
        lanes.append(f'<rect class="seg ph-{ph}" x="{x_:.1f}" y="{lane_y}" width="{max(w_, 0.8):.1f}" height="38"><title>{esc(label)}: {esc(hms(sec))}</title></rect>')
        if w_ > 64:
            lanes.append(f'<text class="sl" x="{x_ + 6:.1f}" y="{lane_y + 16}">{esc(label)}</text>'
                         f'<text class="sd" x="{x_ + 6:.1f}" y="{lane_y + 31}">{esc(hms(sec))}</text>')
        t += sec
    lane_y += 64
axis_y = lane_y + 4
ticks = "".join(f'<line class="tk" x1="{GL + h_ * px:.1f}" x2="{GL + h_ * px:.1f}" y1="30" y2="{axis_y}"/>'
                f'<text class="tl" x="{GL + h_ * px:.1f}" y="{axis_y + 16}" text-anchor="middle">{h_} h</text>' for h_ in range(0, 16, 1))
gantt_svg = (f'<svg class="gantt" viewBox="0 0 {GW} {axis_y + 30}" role="img" aria-label="The three runs on a common hour scale: the integrated run takes 8 h 34 m, '
             f'the 1x1 unwrap 14 h 47 m, the 9x8 unwrap and ionosphere 1 h 50 m">{ticks}{"".join(lanes)}</svg>')

# zoom on the ionosphere lane
ZW, ZL, ZR = 1040, 250, 30
zmax = 120.0
zpx = (ZW - ZL - ZR) / zmax
z = []
t = 0.0
zy = 30
for i, k in enumerate(ORDER["iono"]):
    label, sec, ph, _, _, _ = S[k]
    x_ = ZL + t / 60 * zpx
    w_ = sec / 60 * zpx
    row = zy + i * 26
    z.append(f'<text class="ll" x="{ZL - 12}" y="{row + 14}" text-anchor="end">{esc(label)}</text>')
    z.append(f'<rect class="seg ph-{"iono" if not k.startswith("b") else "ionob"}" x="{x_:.1f}" y="{row + 2}" width="{max(w_, 1.5):.1f}" height="17"/>')
    z.append(f'<text class="sd2" x="{x_ + w_ + 6:.1f}" y="{row + 15}">{esc(hms(sec))}</text>')
    t += sec
zaxis = zy + len(ORDER["iono"]) * 26 + 4
zt = "".join(f'<line class="tk" x1="{ZL + m * zpx:.1f}" x2="{ZL + m * zpx:.1f}" y1="{zy - 6}" y2="{zaxis}"/>'
             f'<text class="tl" x="{ZL + m * zpx:.1f}" y="{zaxis + 16}" text-anchor="middle">{m} m</text>' for m in range(0, 121, 10))
zoom_svg = (f'<svg class="gantt" viewBox="0 0 {ZW} {zaxis + 30}" role="img" aria-label="The 9x8 ionosphere run stage by stage over 110 minutes">{zt}{"".join(z)}</svg>')

# ------------------------------------------------------------------ table
rows = []
for r in ("int", "unw1", "iono"):
    rows.append(f'<tr class="grp"><td colspan="6">{esc(RUNS[r])} <span>{esc(hms(run_total[r]))}</span></td></tr>')
    for k in ORDER[r]:
        label, sec, ph, _, note, _ = S[k]
        rows.append(f'<tr><td><span class="dot ph-{ph}"></span>{esc(label)}<span class="sub">{esc(note)}</span></td>'
                    f'<td class="num">{sec:,.0f}</td><td class="num">{hms_full(sec)}</td><td class="num">{100 * sec / run_total[r]:.1f}%</td>'
                    f'<td>{esc(LOOKS[k])}</td><td>{esc(PHASES[ph])}</td></tr>')
table = ('<div class="tbl"><table><thead><tr><th>stage</th><th class="num">seconds</th><th class="num">h:mm:ss</th><th class="num">share of run</th>'
         '<th>cost depends on looks?</th><th>phase</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")

legend = "".join(f'<span class="key"><i class="dot ph-{p}"></i>{esc(n)}</span>' for p, n in PHASES.items())

page = f"""<title>RSLC Full-Tile Stage Clock</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
:root{{--ground:#F3F5F6;--surface:#FFFFFF;--ink:#14202B;--ink2:#34414C;--muted:#5A6773;--hair:#D6DDE2;--hair2:#E7ECEF;
 --geom:#3E7CB1;--prep:#8A6FB0;--coreg:#2F8A6B;--ifg:#C07A2C;--unw:#7A828C;--iono:#B3475E;--ionob:#D98A9A;--outfill:#E3E9F5;--outline:#34508F;
 --sans:"Archivo","Helvetica Neue",Arial,sans-serif;--mono:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--ground:#0E141A;--surface:#141C23;--ink:#E3E8EC;--ink2:#C3CCD3;--muted:#94A1AB;--hair:#2A3540;--hair2:#1E2831;
 --geom:#7FB0DB;--prep:#B9A3DA;--coreg:#6CC4A3;--ifg:#E6A864;--unw:#A9B1BA;--iono:#E08397;--ionob:#9C5566;--outfill:#1D2A42;--outline:#93AEE6}}}}
:root[data-theme="dark"]{{--ground:#0E141A;--surface:#141C23;--ink:#E3E8EC;--ink2:#C3CCD3;--muted:#94A1AB;--hair:#2A3540;--hair2:#1E2831;
 --geom:#7FB0DB;--prep:#B9A3DA;--coreg:#6CC4A3;--ifg:#E6A864;--unw:#A9B1BA;--iono:#E08397;--ionob:#9C5566;--outfill:#1D2A42;--outline:#93AEE6}}
*{{box-sizing:border-box}}
body{{background:var(--ground);color:var(--ink);font:15px/1.55 var(--sans);padding-inline:20px;padding-block:0 72px}}
.wrap{{max-width:1100px;margin:0 auto}}
header{{padding-block:44px 18px;display:grid;gap:12px}}
h1{{font-size:clamp(26px,3.6vw,38px);line-height:1.1;margin:0;font-weight:650;font-stretch:92%;text-wrap:balance}}
h2{{font-size:19px;margin:36px 0 8px;font-weight:620;font-stretch:92%}}
p{{margin:0 0 10px;max-width:78ch;color:var(--ink2)}}
.meta{{font:12.5px var(--mono);color:var(--muted);display:flex;flex-wrap:wrap;gap:6px 22px;border-top:1px solid var(--hair);border-bottom:1px solid var(--hair);padding-block:10px}}
.meta b{{color:var(--ink);font-weight:500}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:1px;background:var(--hair);border:1px solid var(--hair);border-radius:6px;overflow:hidden;margin:18px 0 6px}}
.tiles div{{background:var(--surface);padding:12px 14px}}
.tiles dt{{font:600 11.5px/1.3 var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}}
.tiles dd{{margin:4px 0 0;font:500 22px/1.2 var(--mono);font-variant-numeric:tabular-nums}}
.tiles dd small{{display:block;font:12.5px var(--sans);color:var(--muted);margin-top:2px}}
figure{{margin:14px 0 26px}}
.scroll{{overflow-x:auto;background:var(--surface);border:1px solid var(--hair);border-radius:6px;padding:10px}}
svg.flow{{display:block;width:100%;min-width:820px;height:auto;color:var(--ink)}}
svg.gantt{{display:block;width:100%;min-width:760px;height:auto;color:var(--ink)}}
figcaption{{font-size:13.5px;color:var(--muted);margin-top:8px;max-width:90ch}}
.node .box{{fill:var(--surface);stroke:var(--hair);stroke-width:1.2}}
.node .stripe{{stroke:none}}
.ph-geom .stripe,.seg.ph-geom,.dot.ph-geom{{fill:var(--geom);background:var(--geom)}}
.ph-prep .stripe,.seg.ph-prep,.dot.ph-prep{{fill:var(--prep);background:var(--prep)}}
.ph-coreg .stripe,.seg.ph-coreg,.dot.ph-coreg{{fill:var(--coreg);background:var(--coreg)}}
.ph-ifg .stripe,.seg.ph-ifg,.dot.ph-ifg{{fill:var(--ifg);background:var(--ifg)}}
.ph-unw .stripe,.seg.ph-unw,.dot.ph-unw{{fill:var(--unw);background:var(--unw)}}
.ph-iono .stripe,.seg.ph-iono,.dot.ph-iono{{fill:var(--iono);background:var(--iono)}}
.seg.ph-ionob{{fill:var(--ionob)}}
.node.ph-iono .box{{stroke:var(--iono);stroke-opacity:.55}}
.out rect{{fill:var(--outfill);stroke:var(--outline);stroke-width:1.2}}
.nl{{font:600 13px var(--sans);fill:var(--ink)}}
.nd{{font:500 12.5px var(--mono);fill:var(--ink)}}
.nsec,.ns{{font:11px var(--mono);fill:var(--muted)}}
.ar{{fill:none;stroke:currentColor;stroke-width:1.3;opacity:.7}}
.al{{font:italic 11px var(--sans);fill:var(--muted)}}
.colh{{font:600 11.5px var(--sans);letter-spacing:.08em;text-transform:uppercase;fill:var(--iono)}}
.seg{{stroke:var(--surface);stroke-width:1}}
.sl{{font:600 11px var(--sans);fill:#fff}}
.sd{{font:11px var(--mono);fill:#fff}}
.sd2{{font:11px var(--mono);fill:var(--ink2)}}
.ll{{font:600 12px var(--sans);fill:var(--ink)}}
.lt{{font:12px var(--mono);fill:var(--muted)}}
.tk{{stroke:var(--hair2);stroke-width:1}}
.tl{{font:11px var(--mono);fill:var(--muted)}}
.legend{{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:12.5px;color:var(--muted);margin:8px 2px 0}}
.key{{display:inline-flex;align-items:center;gap:6px}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:8px;vertical-align:-1px}}
.key .dot{{margin-right:0}}
.tbl{{overflow-x:auto;background:var(--surface);border:1px solid var(--hair);border-radius:6px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px}}
th,td{{padding:7px 10px;text-align:left;border-bottom:1px solid var(--hair2);vertical-align:top}}
th{{font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;white-space:nowrap}}
td.num,th.num{{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}}
td .sub{{display:block;color:var(--muted);font-size:12px;margin-left:18px}}
tr.grp td{{background:var(--hair2);font-weight:650}}
tr.grp span{{font:500 12.5px var(--mono);color:var(--muted);margin-left:10px}}
.note{{border-left:3px solid var(--iono);padding:6px 0 6px 14px;margin:14px 0;max-width:86ch}}
.note p{{margin:0 0 6px}}
@media print{{body{{padding:0}} .scroll{{border:0;padding:0}} svg.flow,svg.gantt{{min-width:0}}}}
</style>
<div class="wrap">
<header>
<h1>How long each stage of the full-tile RSLC 1×1 workflow takes</h1>
<div class="meta"><span>pair <b>20260714 × 20260726</b></span><span>freq A HH <b>53 200 × 54 244</b> samples</span><span>ISCE3 <b>0.25.12</b></span>
<span>VM <b>8 cores · 31 GB</b></span><span>source: ISCE3 journal <b>logs/insar_20260714_20260726_A_HH_1x1.log</b></span></div>
<p>These are the measured durations of the successful runs that produced the benchmark. The benchmark was executed as three runs.
The integrated run went up to the wrapped 1×1 interferogram; then came a standalone 1×1 unwrap and a standalone 9×8 unwrap with ionosphere. Failed attempts are
excluded: runs on the earlier 2-core VM and out-of-memory restarts, roughly 32 h.</p>
<dl class="tiles">
<div><dt>to wrapped 1×1 interferogram</dt><dd>{hms(run_total['int'])}<small>geometry, prep, coregistration, crossmul</small></dd></div>
<div><dt>1×1 unwrap</dt><dd>{hms(run_total['unw1'])}<small>only needed for 1×1 unwrapped phase</small></dd></div>
<div><dt>9×8 unwrap + ionosphere</dt><dd>{hms(run_total['iono'])}<small>given coregistered SLCs</small></dd></div>
<div><dt>ionosphere layer from scratch</dt><dd>≈ {hms(iono_only_low)}–{hms(iono_only_high)}<small>estimate: coregistration + the 9×8 path, no 1×1 products</small></dd></div>
</dl>
</header>

<h2>The flow, with measured time per stage</h2>
<figure><div class="scroll">{flow_svg}</div>
<div class="legend">{legend}<span class="key"><i class="dot" style="background:var(--outfill);border:1px solid var(--outline)"></i>product written</span></div>
<figcaption>Coregistration runs once, on full-resolution SLCs, and feeds both branches. The 1×1 unwrap reads the RIFG. The ionosphere path reuses the
coregistered SLCs to form freq A directly at 9×8. It builds its own freq-B side band, resampled with freq A's offsets decimated 8× in range, and solves
the split spectrum from the two unwrapped phases.</figcaption></figure>

<h2>Where the hours go</h2>
<figure><div class="scroll">{gantt_svg}</div>
<figcaption>The three runs on one hour scale, each segment a stage in execution order (hover a segment for its name). Inside the integrated run, dense offsets
({hms(S['dense'][1])}), product prep ({hms(S['prep'][1])}) and rdr2geo ({hms(S['rdr2geo'][1])}) take {100 * (S['dense'][1] + S['prep'][1] + S['rdr2geo'][1]) / run_total['int']:.0f}% of the time.
The 1×1 unwrap alone is longer than everything else combined.</figcaption></figure>
<figure><div class="scroll">{zoom_svg}</div>
<figcaption>The 9×8 ionosphere run, stage by stage (dark: freq A and the solve; light: freq-B side band). The journal's “Ionosphere {S['solve'][1]:.0f} s” covers only the solve,
filter and write, from 06:28 to 06:55 by the scratch-raster timestamps. The freq-B stages before it are timed separately.</figcaption></figure>

<div class="note"><p><b>Ionosphere layer only, from scratch.</b> The layer still needs freq-A coregistration
({hms(coreg_only)}: rdr2geo, geo2rdr, both resamples, dense offsets, rubber sheet) plus the 9×8 path ({hms(run_total['iono'])}), which gives ≈ {hms(iono_only_low)}.
If the ≈{hms(S['prep'][1] - 5820)} of non-DEM product prep in the integrated run recurs, it is ≈ {hms(iono_only_high)}. The 1×1 DEM pass and 1×1 crossmul are dropped.
This is an estimate assembled from measured stages; it has not been run as one job.</p></div>

<h2>Every stage</h2>
{table}
<p style="margin-top:14px;font-size:13px;color:var(--muted)">“Cost depends on looks” says whether choosing coarser interferogram looks would shorten the stage. Rows marked “no” run on every
full-resolution sample whatever the looks.</p>
</div>"""
OUT.write_text(page)
print(f"wrote {OUT} ({OUT.stat().st_size / 1e3:.0f} KB); integrated {hms(run_total['int'])}, unwrap1x1 {hms(run_total['unw1'])}, iono {hms(run_total['iono'])}, "
      f"grand {hms(grand)}, iono-only {hms(iono_only_low)}-{hms(iono_only_high)}; flow H {H}")
