"""
HTML building blocks for the four-way comparison report (tools/build_report.py).

Everything numeric in the report is read from comparison.json / figures.json through
`Data.get`, so a number in the page cannot drift from the file it came from; a
missing key raises instead of printing a stale value.
"""

from __future__ import annotations

import base64
import html
import json
import math
from pathlib import Path


class Data:
    def __init__(self, path: Path):
        self.d = json.loads(Path(path).read_text())

    def get(self, dotted: str):
        v = self.d
        for part in dotted.split("|"):
            v = v[part]
        return v


def num(x: float, nd: int = 3, sign: bool = False) -> str:
    """Typographic number: true minus sign, optional explicit plus."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    s = f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"
    return s.replace("-", "−")


def pct(x: float, nd: int = 1) -> str:
    return f"{100 * x:.{nd}f}%"


def esc(s: str) -> str:
    return html.escape(s, quote=True)


CSS = r"""
:root{
  --ground:#F3F5F6; --surface:#FFFFFF; --ink:#14202B; --ink-2:#34414C; --muted:#5A6773;
  --hair:#D6DDE2; --hair-2:#E7ECEF; --accent:#34508F; --accent-soft:#E3E9F5;
  --pass:#2E7555; --pass-soft:#E1F0E8; --warn:#9A6412; --warn-soft:#F6EBD8;
  --fail:#A93A2F; --fail-soft:#F7E2DF; --plate:#E9EDF0; --chart-a:#34508F; --chart-b:#B8541F;
  --chart-c:#2E7555; --chart-d:#7A5AA6; --code:#EEF1F3;
  --serif:"Source Serif 4","Source Serif Pro",Georgia,"Times New Roman",serif;
  --sans:"Archivo","Helvetica Neue",Arial,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --ground:#0E141A; --surface:#141C23; --ink:#E3E8EC; --ink-2:#C3CCD3; --muted:#94A1AB;
    --hair:#2A3540; --hair-2:#1E2831; --accent:#93AEE6; --accent-soft:#1D2A42;
    --pass:#7CC7A0; --pass-soft:#16302A; --warn:#E0B060; --warn-soft:#33291A;
    --fail:#EE8F83; --fail-soft:#3A211F; --plate:#1A232B; --chart-a:#93AEE6; --chart-b:#F0986A;
    --chart-c:#7CC7A0; --chart-d:#C3A6EA; --code:#1A232B;
  }
}
:root[data-theme="dark"]{
  --ground:#0E141A; --surface:#141C23; --ink:#E3E8EC; --ink-2:#C3CCD3; --muted:#94A1AB;
  --hair:#2A3540; --hair-2:#1E2831; --accent:#93AEE6; --accent-soft:#1D2A42;
  --pass:#7CC7A0; --pass-soft:#16302A; --warn:#E0B060; --warn-soft:#33291A;
  --fail:#EE8F83; --fail-soft:#3A211F; --plate:#1A232B; --chart-a:#93AEE6; --chart-b:#F0986A;
  --chart-c:#7CC7A0; --chart-d:#C3A6EA; --code:#1A232B;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{background:var(--ground);color:var(--ink);font:17px/1.62 var(--serif);
  padding-inline:20px;padding-block:0 96px;font-optical-sizing:auto}
.wrap{max-width:1120px;margin:0 auto}
.col{max-width:68ch}
a{color:var(--accent);text-underline-offset:2px;text-decoration-thickness:1px}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}
h1,h2,h3,h4{font-family:var(--sans);color:var(--ink);text-wrap:balance;font-stretch:92%}
h1{font-size:clamp(30px,4.6vw,46px);line-height:1.08;font-weight:650;letter-spacing:-.012em;margin:0}
h2{font-size:26px;line-height:1.2;font-weight:620;margin:0 0 14px;display:flex;gap:14px;align-items:baseline}
h2 .n{font-family:var(--mono);font-size:15px;font-weight:500;color:var(--accent);letter-spacing:.02em;flex:none}
h3{font-size:19px;line-height:1.3;font-weight:620;margin:34px 0 10px;display:flex;gap:10px;align-items:baseline}
h3 .n{font-family:var(--mono);font-size:13.5px;font-weight:500;color:var(--muted);flex:none}
p{margin:0 0 14px}
p,li{hyphens:auto}
section{padding-block:40px 8px;border-top:1px solid var(--hair)}
.eyebrow{font:600 12px/1.3 var(--sans);letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.mono,code{font-family:var(--mono);font-size:.84em}
code{background:var(--code);padding:1px 5px;border-radius:3px;overflow-wrap:anywhere}
sub,sup{line-height:0}
.lead{font-size:19px;line-height:1.55;color:var(--ink-2)}
.small{font-size:14.5px;line-height:1.5;color:var(--muted)}

/* masthead */
header.mast{padding-block:56px 34px;display:grid;gap:22px}
.meta-strip{display:flex;flex-wrap:wrap;gap:8px 26px;font:13px/1.4 var(--mono);color:var(--muted);
  border-top:1px solid var(--hair);border-bottom:1px solid var(--hair);padding-block:12px}
.meta-strip b{color:var(--ink);font-weight:500}
.abstract{background:var(--surface);border:1px solid var(--hair);border-radius:6px;padding:22px 26px;max-width:78ch}
.abstract .eyebrow{margin-bottom:8px}
.abstract p:last-child{margin-bottom:0}

/* layout with contents rail */
.body{display:grid;grid-template-columns:minmax(0,1fr);gap:0 48px}
@media (min-width:1100px){.body{grid-template-columns:200px minmax(0,1fr)}}
nav.toc{display:none}
@media (min-width:1100px){
  nav.toc{display:block;position:sticky;top:24px;align-self:start;font:13.5px/1.45 var(--sans);padding-top:44px}
  nav.toc ol{list-style:none;margin:0;padding:0;display:grid;gap:7px}
  nav.toc a{color:var(--muted);text-decoration:none;display:grid;grid-template-columns:26px 1fr}
  nav.toc a:hover{color:var(--ink)}
  nav.toc .n{font-family:var(--mono);font-size:12px;color:var(--accent)}
}

/* verdict table */
.tbl{overflow-x:auto;margin:18px 0 22px;border:1px solid var(--hair);border-radius:6px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font:14.5px/1.45 var(--sans)}
th,td{padding:9px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--hair-2)}
tr:last-child td{border-bottom:0}
th{font-weight:600;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);background:var(--surface);white-space:nowrap}
td.num,th.num{text-align:right;font-family:var(--mono);font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}
td .sub{display:block;color:var(--muted);font-size:13px}
.chip{display:inline-block;font:600 11.5px/1 var(--sans);letter-spacing:.05em;text-transform:uppercase;padding:5px 8px;border-radius:3px;white-space:nowrap}
.chip.pass{color:var(--pass);background:var(--pass-soft)}
.chip.warn{color:var(--warn);background:var(--warn-soft)}
.chip.fail{color:var(--fail);background:var(--fail-soft)}
.chip.info{color:var(--accent);background:var(--accent-soft)}

/* equations */
.eq{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:16px;margin:16px 0 18px;
  padding:12px 0;overflow-x:auto}
.eq math,math{font-family:"Noto Sans Math","STIX Two Math","Cambria Math",math,serif}
.eq math{font-size:1.08em}
.eq .tag{font:13px var(--mono);color:var(--muted)}

/* figures */
figure{margin:26px 0 30px}
.panels{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(min(100%,var(--pmin,300px)),1fr))}
.panel-h{font:600 13px/1.3 var(--sans);color:var(--ink-2);margin:0 0 6px;display:flex;justify-content:space-between;gap:8px}
.panel-h span:last-child{font:500 12px var(--mono);color:var(--muted)}
.plate{position:relative;background:var(--plate);border-radius:3px;overflow:hidden;max-width:100%}
.plate img{display:block;width:100%;height:100%;object-fit:fill}
.plate svg.ov{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.ov .aoi{fill:none;stroke:var(--ink);stroke-width:1.4;vector-effect:non-scaling-stroke;stroke-dasharray:5 3}
.ov .glacier{fill:none;stroke:#F2F2F2;stroke-width:1.6;vector-effect:non-scaling-stroke}
.ov .v1{fill:none;stroke:#D9822B;stroke-width:1.8;vector-effect:non-scaling-stroke}
.ov .v2{fill:none;stroke:#3FA37A;stroke-width:1.8;vector-effect:non-scaling-stroke}
.scale{position:absolute;left:8px;bottom:8px;font:600 11px/1 var(--sans);color:#fff;text-shadow:0 0 3px #000,0 0 2px #000}
.scale i{display:block;height:4px;background:#fff;box-shadow:0 0 2px #000;margin-bottom:4px}
.legend{display:flex;flex-wrap:wrap;align-items:center;gap:10px 22px;margin:10px 0 0;font:12.5px/1.3 var(--sans);color:var(--muted)}
.cbar{display:grid;grid-template-columns:auto minmax(140px,240px) auto;gap:8px;align-items:center}
.cbar .ramp{height:10px;border-radius:2px;border:1px solid var(--hair)}
.cbar b{font-weight:500;font-family:var(--mono);font-size:12px;color:var(--ink-2);white-space:nowrap}
.key{display:inline-flex;align-items:center;gap:6px}
.key i{display:inline-block;width:18px;height:0;border-top:2px solid}
figcaption{font:14.5px/1.5 var(--serif);color:var(--ink-2);margin-top:10px;max-width:86ch}
figcaption b{font-family:var(--sans);font-weight:650;color:var(--ink)}

/* charts */
.chart{background:var(--surface);border:1px solid var(--hair);border-radius:6px;padding:12px 12px 6px}
.chart svg{display:block;width:100%;height:auto}
.chart text{fill:var(--muted);font:11px var(--sans)}
.chart .axis{stroke:var(--hair);stroke-width:1}
.chart .grid{stroke:var(--hair-2);stroke-width:1}
.chart .ttl{fill:var(--ink-2);font:600 12px var(--sans)}
.s-a{stroke:var(--chart-a)} .s-b{stroke:var(--chart-b)} .s-c{stroke:var(--chart-c)} .s-d{stroke:var(--chart-d)}
.f-a{fill:var(--chart-a)} .f-b{fill:var(--chart-b)} .f-c{fill:var(--chart-c)} .f-d{fill:var(--chart-d)}
.chart path.line{fill:none;stroke-width:1.8;stroke-linejoin:round}
.chart path.area{opacity:.12;stroke:none}
.chart .ref{stroke:var(--muted);stroke-dasharray:3 3;stroke-width:1}

/* callouts */
.note{border-left:3px solid var(--accent);padding:4px 0 4px 16px;margin:18px 0;color:var(--ink-2)}
.note.warn{border-color:var(--warn)}
.note .eyebrow{display:block;margin-bottom:4px}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:1px;background:var(--hair);
  border:1px solid var(--hair);border-radius:6px;overflow:hidden;margin:18px 0}
.kv div{background:var(--surface);padding:12px 14px}
.kv dt{font:600 11.5px/1.3 var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.kv dd{margin:4px 0 0;font:500 20px/1.2 var(--mono);color:var(--ink);font-variant-numeric:tabular-nums}
.kv dd small{font-size:12px;color:var(--muted);font-weight:400}
pre{background:var(--code);padding:14px 16px;border-radius:5px;overflow-x:auto;font:13px/1.55 var(--mono)}
ol.refs{font-size:15px;padding-left:22px}
ul,ol{padding-left:22px}
li{margin-bottom:6px}
footer{border-top:1px solid var(--hair);margin-top:48px;padding-top:18px}
@media (max-width:560px){body{font-size:16px} .abstract{padding:18px} h2{font-size:22px}}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}

/* print / PDF: A4, light palette regardless of viewer theme */
@page{size:A4;margin:16mm 15mm 18mm}
@media print{
  :root,:root[data-theme="dark"],:root:not([data-theme="light"]){
    --ground:#FFFFFF; --surface:#FFFFFF; --ink:#14202B; --ink-2:#34414C; --muted:#5A6773;
    --hair:#D6DDE2; --hair-2:#E7ECEF; --accent:#34508F; --accent-soft:#E3E9F5;
    --pass:#2E7555; --pass-soft:#E1F0E8; --warn:#9A6412; --warn-soft:#F6EBD8;
    --fail:#A93A2F; --fail-soft:#F7E2DF; --plate:#E9EDF0; --chart-a:#34508F; --chart-b:#B8541F;
    --chart-c:#2E7555; --chart-d:#7A5AA6; --code:#EEF1F3;
  }
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  body{font-size:10.5pt;line-height:1.5;padding:0}
  .wrap{max-width:none}
  .col{max-width:none}
  .body{display:block}
  nav.toc{display:none!important}
  header.mast{padding-block:0 14pt;gap:12pt}
  h1{font-size:24pt}
  h2{font-size:16pt;break-after:avoid}
  h3{font-size:12.5pt;margin-top:16pt;break-after:avoid}
  section{padding-block:16pt 2pt}
  section{break-before:auto}
  .kv{grid-template-columns:repeat(4,minmax(0,1fr))}
  .kv dd{font-size:13pt}
  p,li{orphans:3;widows:3}
  .abstract{padding:12pt 14pt;max-width:none}
  .meta-strip{font-size:8pt}
  table{font-size:8.5pt}
  th{font-size:7.5pt}
  td.num,th.num{font-size:8pt}
  th,td{padding:5pt 6pt}
  .tbl{overflow:visible;break-inside:avoid}
  tr{break-inside:avoid}
  figure,.chart,.kv,.eq,.note,pre{break-inside:avoid}
  figure{margin:12pt 0 14pt}
  .panels{gap:8pt}
  .panels.n2,.panels.n4{grid-template-columns:repeat(2,minmax(0,1fr))!important}
  .panels.n3{grid-template-columns:repeat(3,minmax(0,1fr))!important}
  .panel-h{font-size:8pt}
  .panel-h span:last-child{font-size:7.5pt}
  figcaption{font-size:9pt}
  .legend{font-size:8pt}
  .chart{padding:6pt;max-width:15cm;margin-inline:auto}
  pre{white-space:pre-wrap;font-size:8pt}
  code{font-size:.8em}
  a{color:inherit;text-decoration:none}
  footer{margin-top:18pt}
}
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..700'
         '&family=JetBrains+Mono:wght@400;500&family=Noto+Sans+Math&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400'
         '&display=swap">')


class Figs:
    """Embeds report WebP panels with HTML overlays drawn from figures.json."""

    def __init__(self, report_dir: Path):
        self.dir = Path(report_dir)
        self.meta = json.loads((self.dir / "figures.json").read_text())
        self.used: set[str] = set()

    def info(self, name):
        return self.meta["figures"][name]

    def chart(self, name):
        return self.meta["charts"][name]

    def uri(self, name):
        self.used.add(name)
        b = (self.dir / self.meta["figures"][name]["file"]).read_bytes()
        return "data:image/webp;base64," + base64.b64encode(b).decode()

    @staticmethod
    def _poly(rings, cls):
        out = []
        for r in rings or []:
            pts = " ".join(f"{x:.4f},{y:.4f}" for x, y in r)
            out.append(f'<polygon class="{cls}" points="{pts}"/>')
        return "".join(out)

    def panel(self, name, title, right="", alt="", overlays=("aoi",), scale=True):
        m = self.info(name)
        w, h = m["px"]
        ov = "".join(self._poly(m.get(k), {"crop_v1": "v1", "crop_v2": "v2"}.get(k, k)) for k in overlays)
        sc = ""
        ext = m.get("extent_km")
        if scale and ext:
            width_km = ext[0]
            nice = next(v for v in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100) if v >= width_km / 6)
            lbl = f"{nice:g} km" if nice >= 1 else f"{nice * 1000:g} m"
            sc = f'<div class="scale" style="width:{100 * nice / width_km:.2f}%"><i></i>{lbl}</div>'
        svg = f'<svg class="ov" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">{ov}</svg>' if ov else ""
        return (f'<div class="panel"><div class="panel-h"><span>{title}</span><span>{right}</span></div>'
                f'<div class="plate" style="aspect-ratio:{w}/{h}"><img loading="lazy" src="{self.uri(name)}" '
                f'alt="{esc(alt or title)}" width="{w}" height="{h}">{svg}{sc}</div></div>')

    @staticmethod
    def cbar(stops, lo, hi, label, mid=None):
        grad = ", ".join(stops)
        mid_s = f"<b>{mid}</b>" if mid else ""
        return (f'<div class="cbar"><b>{lo}</b><div class="ramp" style="background:linear-gradient(90deg,{grad})"></div>'
                f'<b>{hi}</b></div><span>{label}{(" · centre " + mid) if mid else ""}</span>')

    def figure(self, fid, panels_html, caption, legend_html="", pmin=300):
        n = panels_html.count('<div class="panel">')
        return (f'<figure id="{fid}"><div class="panels n{n}" style="--pmin:{pmin}px">{panels_html}</div>'
                f'{("<div class=legend>" + legend_html + "</div>") if legend_html else ""}'
                f'<figcaption>{caption}</figcaption></figure>')


def svg_lines(series, xlo, xhi, xticks, xlabel, ylabel="density", title="", refs=(), w=640, h=250, ylim=None,
              xfmt=lambda v: num(v, 1), area=True):
    """series: [(label, xs, ys, cls)] drawn as lines on one shared scale."""
    ml, mr, mt, mb = 48, 14, 26 if title else 12, 40
    ymax = ylim or max(max(s[2]) for s in series) * 1.08
    X = lambda v: ml + (v - xlo) / (xhi - xlo) * (w - ml - mr)
    Y = lambda v: mt + (1 - v / ymax) * (h - mt - mb)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title or ylabel)}">']
    if title:
        out.append(f'<text class="ttl" x="{ml}" y="14">{esc(title)}</text>')
    step = _nice_step(ymax / 4)
    yv = 0.0
    while yv <= ymax + 1e-12:
        out.append(f'<line class="grid" x1="{ml}" x2="{w - mr}" y1="{Y(yv):.1f}" y2="{Y(yv):.1f}"/>')
        out.append(f'<text x="{ml - 6}" y="{Y(yv) + 3.5:.1f}" text-anchor="end">{_fmt_tick(yv, step)}</text>')
        yv += step
    out.append(f'<line class="axis" x1="{ml}" x2="{w - mr}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    for t, lbl in xticks:
        out.append(f'<line class="axis" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{Y(0):.1f}" y2="{Y(0) + 4:.1f}"/>')
        out.append(f'<text x="{X(t):.1f}" y="{Y(0) + 16:.1f}" text-anchor="middle">{lbl}</text>')
    out.append(f'<text x="{(ml + w - mr) / 2:.1f}" y="{h - 6}" text-anchor="middle">{esc(xlabel)}</text>')
    for xv, lbl in refs:
        out.append(f'<line class="ref" x1="{X(xv):.1f}" x2="{X(xv):.1f}" y1="{mt}" y2="{Y(0):.1f}"/>')
        out.append(f'<text x="{X(xv) + 4:.1f}" y="{mt + 10}">{esc(lbl)}</text>')
    for label, xs, ys, cls in series:
        pts = [(X(x), Y(min(y, ymax))) for x, y in zip(xs, ys) if xlo <= x <= xhi]
        d = "M" + " L".join(f"{a:.1f},{b:.1f}" for a, b in pts)
        if area:
            out.append(f'<path class="area f-{cls}" d="{d} L{pts[-1][0]:.1f},{Y(0):.1f} L{pts[0][0]:.1f},{Y(0):.1f} Z"/>')
        out.append(f'<path class="line s-{cls}" d="{d}"/>')
    out.append("</svg>")
    keys = "".join(f'<span class="key"><i class="s-{c}" style="border-color:var(--chart-{c})"></i>{lbl}</span>'
                   for lbl, _, _, c in series)
    return f'<div class="chart">{"".join(out)}<div class="legend" style="margin:2px 4px 6px">{keys}</div></div>'


def svg_bars(rows, xlo, xhi, xticks, xlabel, title="", w=640, row_h=26, refs=()):
    """rows: [(label, value, cls)] horizontal bars from xlo."""
    ml, mr, mt, mb = 150, 60, 26 if title else 8, 34
    h = mt + mb + row_h * len(rows)
    X = lambda v: ml + (v - xlo) / (xhi - xlo) * (w - ml - mr)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">']
    if title:
        out.append(f'<text class="ttl" x="8" y="14">{esc(title)}</text>')
    y_axis = mt + row_h * len(rows)
    for t, lbl in xticks:
        out.append(f'<line class="grid" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{mt}" y2="{y_axis}"/>')
        out.append(f'<text x="{X(t):.1f}" y="{y_axis + 15}" text-anchor="middle">{lbl}</text>')
    out.append(f'<text x="{(ml + w - mr) / 2:.1f}" y="{h - 4}" text-anchor="middle">{esc(xlabel)}</text>')
    for i, (label, v, cls) in enumerate(rows):
        y = mt + i * row_h
        out.append(f'<text x="{ml - 8}" y="{y + row_h / 2 + 4:.1f}" text-anchor="end">{esc(label)}</text>')
        x0 = X(min(max(0.0, xlo), xhi))
        out.append(f'<rect class="f-{cls}" x="{min(x0, X(v)):.1f}" y="{y + 5}" width="{abs(X(v) - x0):.1f}" height="{row_h - 10}" rx="2"/>')
        tx, anchor = (X(v) + 6, "start") if v >= max(0.0, xlo) else (X(v) - 6, "end")
        out.append(f'<text x="{tx:.1f}" y="{y + row_h / 2 + 4:.1f}" text-anchor="{anchor}" style="fill:var(--ink-2)">{num(v, 3)}</text>')
    if xlo < 0 < xhi:
        out.append(f'<line class="axis" x1="{X(0):.1f}" x2="{X(0):.1f}" y1="{mt}" y2="{y_axis}"/>')
    for xv, lbl in refs:
        out.append(f'<line class="ref" x1="{X(xv):.1f}" x2="{X(xv):.1f}" y1="{mt - 4}" y2="{y_axis}"/>')
        out.append(f'<text x="{X(xv) + 4:.1f}" y="{mt - 8 if title else mt + 10}">{esc(lbl)}</text>')
    out.append("</svg>")
    return f'<div class="chart">{"".join(out)}</div>'


def _nice_step(x):
    e = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if m * e >= x:
            return m * e
    return 10 * e


def _fmt_tick(v, step):
    nd = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return num(v, nd)


def table(headers, rows, num_cols=()):
    th = "".join(f'<th class="num">{h}</th>' if i in num_cols else f"<th>{h}</th>" for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td class="num">{c}</td>' if i in num_cols else f"<td>{c}</td>"
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'
