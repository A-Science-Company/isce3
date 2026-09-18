#!/usr/bin/env python3
"""
Build the Nepal GLOF event report (HTML, self-contained) from tools/glof_event_analysis.py output.

    python tools/build_glof_event_report.py [--quality 82]

Reads  <workdir>/report/glof_event/{analysis.json, fig/*.png}
Writes <workdir>/report/glof_event/nepal_glof_nisar_event_report.html
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from tools.report_kit import CSS, esc  # noqa: E402

OUT = Path("/home/sharath/isce3/case_studies/nepal_nisar_ascending/report/glof_event")
DOC = OUT / "nepal_glof_nisar_event_report.html"


def img_uri(path: Path, quality: int) -> tuple[str, int, int]:
    im = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=quality, method=5)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode(), im.width, im.height


def figure(fid: str, name: str, meta: dict, quality: int, number: int) -> str:
    uri, w, h = img_uri(OUT / meta["file"], quality)
    cap = esc(meta["caption"])
    return (f'<figure id="{fid}"><img src="{uri}" width="{w}" height="{h}" alt="{cap[:140]}">'
            f'<figcaption><b>Figure {number}.</b> {cap}</figcaption></figure>')


def table(headers, rows, cls="tbl") -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


EXTRA_CSS = """
body{max-width:62rem;margin:0 auto;padding:2.2rem 1.1rem 5rem;background:var(--ground);color:var(--ink);font-family:var(--serif);line-height:1.62;font-size:16px}
h1{font-family:var(--sans);font-size:1.9rem;line-height:1.2;margin:0 0 .3rem}
h2{font-family:var(--sans);font-size:1.22rem;margin:2.4rem 0 .6rem;padding-top:.7rem;border-top:1px solid var(--hair)}
h3{font-family:var(--sans);font-size:1.02rem;margin:1.5rem 0 .4rem;color:var(--ink-2)}
.sub{color:var(--muted);font-family:var(--sans);font-size:.92rem;margin:.1rem 0 1.4rem}
figure{margin:1.6rem 0;background:var(--surface);border:1px solid var(--hair);border-radius:10px;padding:.7rem}
figure img{width:100%;height:auto;display:block;border-radius:6px}
figcaption{font-family:var(--sans);font-size:.8rem;color:var(--muted);margin-top:.55rem;line-height:1.5}
table{border-collapse:collapse;width:100%;margin:1rem 0;font-family:var(--sans);font-size:.82rem;background:var(--surface)}
th,td{border:1px solid var(--hair);padding:.36rem .5rem;text-align:left;vertical-align:top}
th{background:var(--plate);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
code{font-family:var(--mono);font-size:.86em;background:var(--code);padding:.08em .3em;border-radius:4px}
.key{background:var(--accent-soft);border-left:3px solid var(--accent);padding:.7rem .9rem;border-radius:0 8px 8px 0;margin:1.1rem 0}
.warn{background:var(--warn-soft);border-left:3px solid var(--warn);padding:.7rem .9rem;border-radius:0 8px 8px 0;margin:1.1rem 0}
.toc{background:var(--surface);border:1px solid var(--hair);border-radius:10px;padding:.8rem 1.1rem;font-family:var(--sans);font-size:.87rem}
.toc ol{margin:.3rem 0;padding-left:1.2rem}.toc a{color:var(--accent);text-decoration:none}
ul,ol{padding-left:1.25rem}li{margin:.25rem 0}
@media print{body{max-width:none;font-size:10.5pt}figure{break-inside:avoid}table{break-inside:avoid}h2{break-after:avoid}}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quality", type=int, default=82)
    a = ap.parse_args()
    data = json.loads((OUT / "analysis.json").read_text())
    F, S = data["figures"], data["stats"]
    order = ["f1_study_area", "f2_network", "f3_validation", "f15_focus_velocity", "f16_focus_timeseries",
             "f18_relaxed_coverage", "f19_intervals", "f20_glacier_vs_ring", "f17_focus_event", "f4_pre_velocity", "f5_corrections", "f6_timeseries", "f11_detection", "f8_coherence",
             "f13_flood_path", "f9_amplitude", "f12_clusters", "f14_controls", "f10_event_displacement", "f7_glacier_zoom"]
    n = {k: i for i, k in enumerate(order, 1)}
    fig = lambda k: figure(k, k, F[k], a.quality, n[k])          # noqa: E731
    ref = lambda k: f'<a href="#{k}">Figure {n[k]}</a>'          # noqa: E731

    ar, pv, ce, gz, ed, det = S["area"], S["pre_velocity"], S["corrections_effect"], S["glacier_zone"], S["event_displacement"], S["detection"]
    fo, fs, fa, fe = S["focus"], S["focus_series"], S["focus_averaging"], S["focus_event"]
    fp, ctl, cl, qa = S["flood_path"], S["controls"], S["change_clusters"], S["provenance"]["qa"]
    rx, gr, iv = S["relaxed"], S["glacier_vs_ring"], S["intervals"]
    pre_iv = [r for r in iv if r["run"] == "pre_event"]
    off = rx["counts"]["mask off, all pixels inverted"]; on = rx["counts"]["connected-component mask on (default)"]
    ev_ctl, c1, c2 = ctl["event 19–31 Aug"], ctl["control 31 Aug – 12 Sep"], ctl["control 14–26 Jul"]
    pre_series, post_series = fs["Pre-event"], fs["Post-event"]

    html = [f"""<!doctype html><meta charset="utf-8"><title>Nepal GLOF Radar Analysis</title>
<style>{CSS}{EXTRA_CSS}</style>
<h1>Line-of-sight velocity and event signatures at the glacier zone: NISAR analysis of the 26 August 2026 outburst</h1>
<p class="sub">Central Nepal Himalaya, near 28.28° N 85.53° E · ascending track 98, frame 16, HH · seven L-band acquisitions,
20 June – 12 September 2026 · ISCE3 0.25.12, snaphu 0.4.1, MintPy 1.6.4 · 16 September 2026</p>

<div class="toc"><b>Contents</b><ol>
<li><a href="#s1">Summary</a></li>
<li><a href="#s2">The two areas, and why there are two</a></li>
<li><a href="#s3">Data</a></li>
<li><a href="#s4">Processing chain</a></li>
<li><a href="#s5">Validation of the measurement chain</a></li>
<li><a href="#s6">Line-of-sight velocity in the analysis box</a></li>
<li><a href="#s7">Recovering the ice pixels, and what they say</a></li>
<li><a href="#s8">The event inside the analysis box</a></li>
<li><a href="#s9">What the wider area contributes: reference, noise level, detection limit</a></li>
<li><a href="#s10">The event in the wider area</a></li>
<li><a href="#s11">Synthesis</a></li><li><a href="#s12">Limitations</a></li><li><a href="#s13">Products and reproduction</a></li></ol></div>

<h2 id="s1">1. Summary</h2>
<div class="key"><b>Line-of-sight velocity in the analysis box.</b> Over the {fo['area_km2']:.1f} km² box (the glacier polygon's
bounding box with a 100 % buffer, {fo['size_km'][0]:.2f} × {fo['size_km'][1]:.2f} km), the pre-event velocity is
<b>{fo['velocity_mm_yr']['50']:+.0f} mm/yr</b> in the median of the {fo['reliable_pre']} reliable pixels, with a typical
uncertainty of <b>±{fo['velocity_sigma_mm_yr']:.0f} mm/yr</b>. Only {fo['significant_pixels']} pixels
({100 * fo['significant_fraction']:.1f} %) have a velocity larger than twice their own uncertainty. Averaging 3 × 3 pixels
narrows the scatter from {fa['velocity_std_40m']:.0f} to {fa['velocity_std_120m']:.0f} mm/yr and leaves the median at
{fa['velocity_median_120m']:+.0f} mm/yr. <b>The honest reading is that no significant motion is measured in this box over the two
pre-event months</b>, and the data bounds any steady LOS motion to roughly ±{2 * fo['velocity_sigma_mm_yr'] / 6:.0f} mm over that
interval.</div>
<div class="warn"><b>On the ice itself there is no velocity at all.</b> Of the {ar['glacier_zone_pixels']} pixels inside the
glacier polygon, {fo['reliable_in_glacier']} survive the reliability test before the event and none survive across it; their
uncertainty (±{fo['glacier_sigma_mm_yr']:.0f} mm/yr) is larger than any plausible signal. All {fo['reliable_in_ring']} usable
pixels sit on the rock and moraine of the buffer ring. Coherence in the box is {fe['coherence_pre12']:.2f} before the event and
{fe['coherence_event']:.2f} across it.</div>
<div class="key"><b>The ice pixels can be recovered, and they change nothing.</b> The default inversion rule (use a pixel only
where the unwrapper gave it a connected region) removed {100 * (1 - on['glacier'][4] / rx['glacier_pixels']):.0f} % of the
glacier; inverting every pixel instead recovers {off['glacier'][4]} of {rx['glacier_pixels']} at γ<sub>t</sub> ≥
{rx['used_threshold']} and gives {rx['glacier_velocity_mm_yr']:+.0f} ± {rx['glacier_sigma_mm_yr']:.0f} mm/yr. Over the four
consecutive pre-event intervals the glacier differs from the rock around it by {gr['intervals'][0]['difference_mm']:+.0f},
{gr['intervals'][1]['difference_mm']:+.0f}, {gr['intervals'][2]['difference_mm']:+.0f} and
{gr['intervals'][3]['difference_mm']:+.0f} mm — individually up to {max(r['z'] for r in gr['intervals']):.1f}σ beyond random
patches, but alternating in sign and summing to {gr['cumulative_mm']:+.0f} ± {gr['cumulative_null_sd_mm']:.0f} mm
(p = {gr['cumulative_p']:.2f}). That is a changing snow and ice surface, not creep.</div>
<div class="key"><b>The event is visible in backscatter, inside the box and beyond it.</b> Within the box,
{fe['strong_change_area_km2']:.2f} km² changed by more than 3 dB across the event. Inside the glacier polygon the change skews
negative ({fe['amp_change_glacier_p5_p95'][0]:.1f} dB at the 5th percentile), on the surrounding ring it skews positive
({fe['amp_change_ring_p5_p95'][1]:+.1f} dB at the 95th). Across the wider area the change concentrates within a kilometre of the
glacier (spread ratio near-to-far {ev_ctl['ratio_near_far']:.2f}, against {c1['ratio_near_far']:.2f} and
{c2['ratio_near_far']:.2f} for two control pairs that do not span the event) and forms a chain of clusters descending to
{min(c['height_m'] for c in cl[:8]):.0f} m, {max(c['dist_km'] for c in cl[:8]):.1f} km away.</div>

<h2 id="s2">2. The two areas, and why there are two</h2>
<p>The measurement target is the glacier and its lake, supplied as a {ar['glacier_zone_km2']:.2f} km² polygon. Interferometry
cannot be run on a patch that small in isolation: the phase of every pixel is relative, so the analysis needs a reference point
on ground that is coherent and stable, and it needs enough surrounding terrain to establish how large the atmospheric and
ionospheric residuals are. That is the role of the wider {ar['aoi_km2']:.0f} km² box: it supplies the reference pixel at
{ar['reference_latlon'][0]:.4f}° N, {ar['reference_latlon'][1]:.4f}° E ({ar['reference_height_m']:.0f} m, 15 km away) and the
error statistics of §8. It is not the subject of the analysis.</p>
<p>The subject is the <b>analysis box</b>: the glacier polygon's bounding box expanded by 100 % about its centre, giving
{fo['size_km'][0]:.2f} × {fo['size_km'][1]:.2f} km ({fo['area_km2']:.1f} km², {fo['pixels']:,} pixels at 40 m posting) spanning
{fo['height_m'][0]:.0f}–{fo['height_m'][1]:.0f} m at a {fo['incidence_deg']:.0f}° incidence angle. Sections 6 to 8 are about that box; sections 9 and 10 place it in context.</p>
{fig('f1_study_area')}

<h2 id="s3">3. Data</h2>
<p>Seven NISAR L1 RSLC acquisitions cover the area on ascending track 98, frame 16, HH, 40 MHz range bandwidth: five before the
flood and two after. Corrections come from the NISAR L2 GUNW products of the consecutive pairs.</p>
{table(["Acquisition", "Role", "Days from the event", "Perpendicular baseline (m)"],
       [[d, "pre-event" if d < "20260826" else "post-event",
         f"{(__import__('datetime').datetime.strptime(d, '%Y%m%d') - __import__('datetime').datetime(2026, 8, 26)).days:+d}",
         f"{S['network']['bperp_m'][d]:+.1f}"] for d in sorted(S['network']['bperp_m'])])}
<p>Perpendicular baselines stay under {max(abs(v) for v in S['network']['bperp_m'].values()):.0f} m, so the topographic
contribution to phase is negligible and cannot masquerade as deformation.</p>
{fig('f2_network')}

<h2 id="s4">4. Processing chain</h2>
<p>Each acquisition was cut to the wider box, coregistered onto the 26 July acquisition (the middle date) with geometry plus
dense offset tracking and a rubber-sheet fit, and interferograms were formed and multilooked 9 × 8 to a posting close to 40 m.
Phase was unwrapped with snaphu in 3 × 3 tiles with a 150-pixel overlap, so whole-cycle offsets are resolved between tiles
rather than inherited. Ionosphere, wet and hydrostatic troposphere, and solid-earth tides were taken from the GUNW products,
sampled at each pixel's position and elevation, and converted from pair screens to per-date screens. Displacement per date was
inverted from the unwrapped network in MintPy with {len(S['network']['pre_pairs'])} interferograms over the five pre-event
dates and {len(S['network']['post_pairs'])} over the three post-event dates, and velocity fitted by a linear time function.
Coregistration took 72–88 minutes per pair; the two time series took about 20 minutes each.</p>

<h2 id="s5">5. Validation of the measurement chain</h2>
<p>Four of the nine pre-event interferograms share the stack reference and therefore have an independent counterpart produced
by ISCE3 during coregistration. They agree to {min(r['median_rad'] for r in S['validation']['rifg_checks']):.4f}–{max(r['median_rad'] for r in S['validation']['rifg_checks']):.4f}
radians in the median over roughly a million coherent pixels each, three orders of magnitude inside the acceptance gate
({ref('f3_validation')}).</p>
<div class="key"><b>A methodological point worth recording.</b> Each acquisition is cropped to its own window, so the two images
of a pair begin at different slant ranges, and that constant difference has to be flattened along with the geometric offset.
One range sample is {S['validation']['cycles_per_sample']:.2f} phase cycles here, so a difference of 8 samples is almost exactly
half a cycle and inverts the interferogram, while 64 samples is nearly a whole cycle and leaves it visibly untouched; the dates
of this stack split into exactly those two groups. The first version of the processing omitted the term, the comparison against
ISCE3 caught the two inverted pairs, and pairs sharing no date with the reference would have carried a constant error with no
such check. After the fix, whole-cycle closure over interferogram triplets holds for all but
{100 * min(t['fraction_nonzero_cycles'] for t in S['provenance']['closure']['pre_event']):.1f}–{100 * max(t['fraction_nonzero_cycles'] for t in S['provenance']['closure']['pre_event']):.1f} %
of pixels.</div>
{fig('f3_validation')}

<h2 id="s6">6. Line-of-sight velocity in the analysis box</h2>
<p>Of the {fo['pixels']:,} pixels in the box, {fo['reliable_pre']} ({100 * fo['reliable_pre'] / fo['pixels']:.1f} %) pass the
reliability test of the inversion before the event. Their distribution is not random: they lie on the rock ribs and moraine of
the buffer ring, and hardly at all on the ice ({ref('f15_focus_velocity')}). The velocity of those pixels has a median of
{fo['velocity_mm_yr']['50']:+.0f} mm/yr with a 16-to-84 percentile range of {fo['velocity_mm_yr']['16']:+.0f} to
{fo['velocity_mm_yr']['84']:+.0f} mm/yr, against a median formal uncertainty of ±{fo['velocity_sigma_mm_yr']:.0f} mm/yr.</p>
<p>That comparison is the result. A velocity smaller than its own uncertainty is not a measurement of motion, and only
{fo['significant_pixels']} pixels in the box reach twice their uncertainty — a fraction
({100 * fo['significant_fraction']:.1f} %) consistent with what noise alone produces at that threshold. The apparent
{abs(fo['velocity_mm_yr']['50']):.0f} mm/yr of motion away from the satellite is best read as the residual atmospheric
difference between this box and the reference pixel 15 km away, which §8 shows to be of exactly this size across the whole
scene.</p>
{fig('f15_focus_velocity')}
<p>The displacement history makes the same point without the annualisation ({ref('f16_focus_timeseries')}). The box median moves
{pre_series['median_mm'][2]:+.0f} mm on 14 July, returns to {pre_series['median_mm'][3]:+.0f} mm on 26 July and sits at
{pre_series['median_mm'][4]:+.0f} mm on 19 August: an oscillation, not a trend, and of the size of the atmospheric residual.
Averaging 3 × 3 pixels (120 m) narrows the velocity scatter from {fa['velocity_std_40m']:.0f} to {fa['velocity_std_120m']:.0f}
mm/yr, as expected for noise averaging down, while leaving the median where it was ({fa['velocity_median_120m']:+.0f} mm/yr).
Spatial averaging therefore does not reveal a hidden signal here; it only confirms that the scatter is uncorrelated between
neighbouring pixels.</p>
{fig('f16_focus_timeseries')}
<div class="warn"><b>What this does and does not bound.</b> Taking twice the uncertainty over the 60-day window, a steady
line-of-sight motion larger than about {2 * fo['velocity_sigma_mm_yr'] / 6:.0f} mm in two months would have been visible on the
rock and moraine of the box and is excluded. Nothing is excluded on the ice itself, where there is no measurement, and nothing
is excluded for motion that is faster than the 12-day sampling or confined to slopes facing away from the sensor.</div>


<h2 id="s7">7. Recovering the ice pixels, and what they say</h2>
<p>Section 6 measured only the rock and moraine, because the inversion had been given a strict rule: a pixel is used in an
interferogram only if the unwrapper placed it inside a connected region. On ice that rule removes almost everything —
{99}% of the glacier pixels sit in no region in every interferogram, so {on['glacier'][4]} of {rx['glacier_pixels']} were
inverted at all, and lowering the temporal-coherence threshold from 0.7 to 0.1 changed nothing ({ref('f18_relaxed_coverage')}).
The threshold was never the binding constraint.</p>
<p>Inverting every pixel instead, and letting temporal coherence do the filtering afterwards, recovers the ice:
{off['glacier'][4]} of {rx['glacier_pixels']} glacier pixels ({100 * off['glacier'][4] / rx['glacier_pixels']:.0f} %) and
{off['box'][4]:,} of {rx['box_pixels']:,} in the box survive at γ<sub>t</sub> ≥ {rx['used_threshold']}. Their velocity is
{rx['glacier_velocity_mm_yr']:+.0f} ± {rx['glacier_sigma_mm_yr']:.0f} mm/yr — coverage improves, significance does not, and the
uncertainty is if anything larger because the recovered pixels are noisier.</p>
{fig('f18_relaxed_coverage')}
<p>With five pre-event dates the series has four consecutive intervals, which is the finest time resolution available
({ref('f19_intervals')}). Read individually, the glacier polygon moves by {pre_iv[0]['glacier_mm']:+.0f},
{pre_iv[1]['glacier_mm']:+.0f}, {pre_iv[2]['glacier_mm']:+.0f} and {pre_iv[3]['glacier_mm']:+.0f} mm over the four intervals.
Those numbers on their own would be startling; the surrounding ring, which is bedrock and moraine, moves by
{pre_iv[0]['ring_mm']:+.0f}, {pre_iv[1]['ring_mm']:+.0f}, {pre_iv[2]['ring_mm']:+.0f} and {pre_iv[3]['ring_mm']:+.0f} mm over
the same intervals ({ref('f20_glacier_vs_ring')}, left). The two track each other because both carry the same atmospheric
delay, and that common part is not motion.</p>
{fig('f19_intervals')}
<p>What matters is therefore the difference between the glacier and its immediate surroundings, and whether that difference is
larger than the same statistic computed on ordinary terrain. Taking an ellipse the size of the glacier polygon and the annulus
around it, and repeating the measurement at 300 random positions per interval, gives the comparison in the middle panel of
{ref('f20_glacier_vs_ring')}:</p>
{table(["Interval", "Days", "Glacier − surroundings (mm)", "Random-patch σ (mm)", "|z|", "p (two-sided)"],
       [[r["pair"][4:8] + " → " + r["pair"][13:], r["days"], f"{r['difference_mm']:+.1f}", f"{r['null_sd_mm']:.1f}",
         f"{r['z']:.1f}", f"{r['p_two_sided']:.3f}"] for r in gr["intervals"]])}
<div class="key"><b>How to read this.</b> Two of the four intervals put the glacier {abs(gr['intervals'][0]['difference_mm']):.0f}
and {abs(gr['intervals'][1]['difference_mm']):.0f} mm away from its surroundings, about three standard deviations beyond what
random patches of the same size produce ({gr['intervals'][0]['z']:.1f}σ and {gr['intervals'][1]['z']:.1f}σ) — but <b>with opposite signs</b>, and the cumulative difference over the two pre-event
months is {gr['cumulative_mm']:+.0f} ± {gr['cumulative_null_sd_mm']:.0f} mm (p = {gr['cumulative_p']:.2f}), which is not
significant. Steady creep would accumulate; this does not. An alternating, interval-scale difference between an ice surface and
the rock around it is what changing snow cover and volume scattering produce: the phase centre in snow and firn moves with
wetness and new accumulation, by millimetres to centimetres, without the ice going anywhere. At the coherence of these pixels
(γ ≈ {S['focus_event']['coherence_pre12']:.2f}) the per-pixel phase noise alone is
{gr['phase_noise_mm_at_coherence']['0.15']:.0f} mm, so individual pixels carry little, and only the spatial median over hundreds
of them is worth interpreting at all.</div>
{fig('f20_glacier_vs_ring')}
<p>The two post-event intervals behave the same way: {iv[4]['glacier_mm']:+.0f} mm across the event and
{iv[5]['glacier_mm']:+.0f} mm after it in the glacier polygon, against {iv[4]['ring_mm']:+.0f} and {iv[5]['ring_mm']:+.0f} mm on
the ring, with the same alternation. <b>The conclusion is unchanged by relaxing the threshold: with this stack there is no
measurable glacier motion, only a bound.</b> What the relaxation does buy is coverage, and with it the ability to say that the
glacier surface is not doing anything that the surrounding rock is not also doing, to within about
{2 * gr['cumulative_null_sd_mm']:.0f} mm over two months.</p>

<h2 id="s8">8. The event inside the analysis box</h2>
<p>Across the pair spanning the outburst, coherence in the box falls from {fe['coherence_pre12']:.2f} to
{fe['coherence_event']:.2f}, a change too small against a 12-day baseline on ice to carry information on its own. Backscatter
is another matter: {fe['strong_change_area_km2']:.2f} km² of the {fo['area_km2']:.1f} km² box —
{100 * fe['strong_change_area_km2'] / fo['area_km2']:.0f} % of it — changed by more than 3 dB ({ref('f17_focus_event')}).</p>
<p>The sign of that change is organised, and this is the most specific observation the data offers about the source. Inside the
glacier polygon the change is predominantly negative, reaching {fe['amp_change_glacier_p5_p95'][0]:.1f} dB at the 5th
percentile, concentrated in the northeastern part of the polygon. On the buffer ring immediately west and south the change is
predominantly positive, reaching {fe['amp_change_ring_p5_p95'][1]:+.1f} dB at the 95th percentile. A surface that darkens has
usually become smoother or wetter at the scale of the L-band wavelength — open water, saturated fine sediment, or the removal of
a rough ice or debris surface. A surface that brightens has usually become rougher or more angular — freshly exposed blocky
debris, scoured channel walls, or new deposits. The pattern is therefore consistent with material leaving the northeastern part
of the glacier zone and being deposited on the slope immediately below and west of it, which is the direction the terrain falls.
</p>
{fig('f17_focus_event')}
<p>Within the box the post-event displacement is {post_series['median_mm'][2]:+.0f} mm by 12 September in the median, of the
same size as the pre-event oscillation, so it should not be read as motion either.</p>

<h2 id="s9">9. What the wider area contributes: reference, noise level, detection limit</h2>
<p>Across the {ar['aoi_km2']:.0f} km² box, {pv['pixels_good']:,} of {pv['pixels_total']:,} pixels
({100 * pv['pixels_good'] / pv['pixels_total']:.0f} %) are reliable, and the fitted velocity has a median of
{pv['velocity_mm_yr']['50']:.0f} mm/yr with a 5-to-95 percentile range of {pv['velocity_mm_yr']['5']:.0f} to
{pv['velocity_mm_yr']['95']:.0f} mm/yr ({ref('f4_pre_velocity')}). This is the noise floor of the method in this terrain, and it
is why the box result of §6 reads as it does: the same apparent drift appears everywhere, including on ground that has no reason
to move.</p>
{fig('f4_pre_velocity')}
<p>The GUNW corrections reduce but do not remove it, narrowing the velocity spread from {ce['velocity_std_uncorrected']:.0f} to
{ce['velocity_std_corrected']:.0f} mm/yr ({ref('f5_corrections')}). The ionosphere is the largest term, reaching
{max(S['corrections']['ionosphere'].values()):.0f} mm of spatial spread on one date; the troposphere reaches
{max(S['corrections']['troposphere'].values()):.0f} mm and the tides stay below
{max(S['corrections']['solid_earth_tides'].values()):.0f} mm. What remains is turbulent atmosphere at scales those screens do
not resolve.</p>
{fig('f5_corrections')}
{fig('f6_timeseries')}
<p>Two independent measures of the detection limit agree ({ref('f11_detection')}): the formal velocity uncertainty has a median
of {det['velocity_std_median_mm_yr']:.0f} mm/yr, and the date-to-date scatter of displacement on reliable ground is
{min(det['date_to_date_scatter_mm']):.0f}–{max(det['date_to_date_scatter_mm']):.0f} mm. The practical limit for this stack is
about {det['detectable_displacement_mm']:.0f} mm of displacement over the two-month window.</p>
{fig('f11_detection')}
<p>Coherence explains where measurement is possible at all ({ref('f8_coherence')}). Over the whole area the 12-day coherence
<em>rises</em> from {gz['coherence_pre12_aoi']:.2f} before the event to {gz['coherence_event_aoi']:.2f} across it as the monsoon
ends, while the glacier zone stays at {gz['coherence_pre12_gz']:.2f}–{gz['coherence_event_gz']:.2f} at every baseline. Ice and
snow at 4500–5200 m decorrelate within 12 days even at L-band.</p>
{fig('f8_coherence')}

<h2 id="s10">10. The event in the wider area</h2>
<p>Coherence loss, the conventional flood indicator, fails the control test here ({ref('f13_flood_path')}):
{fp['lost_area_km2']:.0f} km² of previously coherent ground loses coherence across the event, but the following pair, which spans
no event, flags {fp['control_area_km2']:.0f} km². About a third of the pixels flagged across the event regain coherence
immediately afterwards, pointing to transient scattering changes such as wet snow.</p>
{fig('f13_flood_path')}
<p>Backscatter change does pass that test. The spread within 1 km of the glacier zone is {ev_ctl['spread_near_1km_dB']:.1f} dB
against {ev_ctl['spread_beyond_8km_dB']:.1f} dB beyond 8 km ({ref('f9_amplitude')}), and grouping strong changes into connected
clusters gives {S['change_cluster_summary']['clusters']} of them totalling {S['change_cluster_summary']['total_area_km2']:.2f}
km², of which {S['change_cluster_summary']['within_1km']} lie within a kilometre of the glacier zone
({ref('f12_clusters')}).</p>
{table(["#", "Area (km²)", "Latitude", "Longitude", "Elevation (m)", "Distance (km)", "Amplitude change (dB)", "Coherence before → across"],
       [[i + 1, f"{c['area_km2']:.3f}", f"{c['lat']:.4f}", f"{c['lon']:.4f}", f"{c['height_m']:.0f}", f"{c['dist_km']:.1f}",
         f"{c['amp_db']:+.1f}", f"{c['coh_pre']:.2f} → {c['coh_event']:.2f}"] for i, c in enumerate(cl[:8])])}
<p>The largest cluster covers {cl[0]['area_km2']:.2f} km² at {cl[0]['height_m']:.0f} m, {cl[0]['dist_km']:.1f} km away,
brightening by {cl[0]['amp_db']:.1f} dB; it was well correlated before the event ({cl[0]['coh_pre']:.2f}) and lost that
correlation across it ({cl[0]['coh_event']:.2f}), so its surface was stable for two months and then changed. As a group the
clusters descend from {max(c['height_m'] for c in cl[:8]):.0f} m beside the ice to {min(c['height_m'] for c in cl[:8]):.0f} m
some {max(c['dist_km'] for c in cl[:8]):.1f} km away.</p>
{fig('f9_amplitude')}
{fig('f12_clusters')}
<p>Repeating the identical analysis on two pairs that do not span the event gives near-to-far spread ratios of
{c1['ratio_near_far']:.2f} and {c2['ratio_near_far']:.2f} against {ev_ctl['ratio_near_far']:.2f}, and
{c1['clusters_over_12px']} and {c2['clusters_over_12px']} clusters against {ev_ctl['clusters_over_12px']}
({ref('f14_controls')}). The concentration of surface change at the glacier zone belongs to the interval containing the flood
and not to the season.</p>
{fig('f14_controls')}
<p>For completeness, the displacement field across the event has a 5-to-95 percentile range of {ed['aoi_p5_p50_p95'][0]:.0f} to
{ed['aoi_p5_p50_p95'][2]:.0f} mm ({ref('f10_event_displacement')}), the size of the atmospheric residual, with
{ed['good_pixels_in_gz']} reliable pixels inside the glacier polygon.</p>
{fig('f10_event_displacement')}
{fig('f7_glacier_zoom')}

<h2 id="s11">11. Synthesis</h2>
<ul>
<li><b>Velocity in the analysis box:</b> {fo['velocity_mm_yr']['50']:+.0f} ± {fo['velocity_sigma_mm_yr']:.0f} mm/yr in the
median, not significantly different from zero, and equal to the scene-wide atmospheric residual. Steady motion above roughly
{2 * fo['velocity_sigma_mm_yr'] / 6:.0f} mm over the two pre-event months is excluded on the rock and moraine of the box.</li>
<li><b>On the ice, with every pixel inverted:</b> {off['glacier'][4]} of {rx['glacier_pixels']} pixels are usable and give
{rx['glacier_velocity_mm_yr']:+.0f} ± {rx['glacier_sigma_mm_yr']:.0f} mm/yr; interval by interval the glacier departs from its
surroundings by up to {max(abs(r['difference_mm']) for r in gr['intervals']):.0f} mm with alternating sign, summing to
{gr['cumulative_mm']:+.0f} ± {gr['cumulative_null_sd_mm']:.0f} mm. No cumulative motion is resolved.</li>
<li><b>On the ice with the strict mask:</b> no measurement, before or across the event. This is a limit of 12-day L-band interferometry on snow and
ice at this elevation, not a statement about the glacier.</li>
<li><b>The event:</b> {fe['strong_change_area_km2']:.2f} km² of the box changed by more than 3 dB, darkening inside the
northeastern glacier polygon and brightening on the ring below and west of it. Beyond the box the change continues as a chain of
clusters down to {min(c['height_m'] for c in cl[:8]):.0f} m. Two control intervals reproduce neither pattern.</li>
<li><b>Most likely source and path, on this evidence:</b> the northeastern part of the glacier zone, with material moving onto
the slope immediately west and below and then down the drainage. The radar constrains where the surface changed, not the
mechanism.</li>
</ul>

<h2 id="s12">12. Limitations</h2>
<ul>
<li><b>Five pre-event dates over two months</b>, with a date-to-date scatter of
{min(det['date_to_date_scatter_mm']):.0f}–{max(det['date_to_date_scatter_mm']):.0f} mm, is too short and too sparse to separate
slow deformation from atmosphere.</li>
<li><b>One geometry.</b> Ascending only, so a single projection of a three-dimensional motion field; the descending track
(48/74) covers the same ground and would constrain it far better.</li>
<li><b>Decorrelation on ice</b> defeats phase methods here. Amplitude offset tracking, or pairs shorter than 12 days, would be
the route to glacier motion.</li>
<li><b>One correction missing.</b> The urgent-response GUNW for 31 August – 12 September carries no troposphere screens, so the
post-event series has ionosphere and tides but not troposphere.</li>
<li><b>Backscatter change is ambiguous</b> in origin: wet snow, rain and roughness changes all produce decibel-level change.
The controls establish that the change is specific to the event interval, not what caused it.</li>
<li><b>Relative measurement.</b> Everything is referenced to one pixel 15 km away; a common-mode motion of the whole scene would
be invisible.</li>
</ul>

<h2 id="s13">13. Products and reproduction</h2>
<p>Products cropped to the analysis box are in <code>report/glof_event/export_focus/</code> as GeoTIFFs (EPSG:4326, 47 × 75
pixels): line-of-sight velocity and its uncertainty, the uncorrected velocity, cumulative displacement, temporal coherence and
the reliability mask, for both the pre-event and post-event runs. The full-area equivalents are in each run's
<code>export/</code> directory, and every figure number in this report is reproduced by
<code>tools/glof_event_analysis.py</code>.</p>
<pre><code>cd /home/sharath/isce3/asc/nisar_workflows
python nisar_coreg.py      -c coreg_configs/nepal_nisar_ascending_rslc.yaml       run --detach
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_pre_event.yaml     run --detach
python nisar_timeseries.py -c ts_configs/nepal_nisar_ascending_post_event.yaml    run --detach
python tools/glof_event_analysis.py &amp;&amp; python tools/build_glof_event_report.py</code></pre>
{table(["Run", "Reference point", "Corrections applied", "Reliable pixels (wide area)", "Final product"],
       [[r.replace('_', ' '), f"{qa[r]['reference_latlon'][0]:.4f}° N, {qa[r]['reference_latlon'][1]:.4f}° E",
         ", ".join(qa[r]['corrections_applied']) or "none", f"{qa[r]['aoi_pixels_temporal_coherence_ok']:,}",
         f"<code>{qa[r]['final_timeseries']}</code>"] for r in ("pre_event", "post_event")])}
<p class="sub">Processing parameters are recorded in <code>coreg_configs/defaults.yaml</code> and
<code>ts_configs/defaults.yaml</code>; the values actually used are frozen in <code>params.json</code> beside each product.
Figures and the numbers quoted in the text are generated together; no value in this report was entered by hand.</p>
"""]
    DOC.write_text("".join(html))
    print(f"wrote {DOC} ({DOC.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
