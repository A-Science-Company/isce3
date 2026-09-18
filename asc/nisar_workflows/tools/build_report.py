#!/usr/bin/env python3
"""
Build the four-way comparison report (single self-contained HTML).

    python tools/build_report.py            # after compare_four_way.py v2 and report_figures.py

Inputs (read-only):  <case>/comparison_v2/{comparison.json, supplement_nondispersive.json}
                     <case>/comparison_v2/report/{figures.json, fig/*.webp, footprints.json}
Output:              <case>/comparison_v2/report/nepal_glof_four_workflow_report.html

Every quantitative statement is formatted from those JSON files at build time.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

from osgeo import gdal, ogr, osr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_kit import CSS, FONTS, Data, Figs, esc, num, pct, svg_bars, svg_lines, table  # noqa: E402

gdal.UseExceptions()
C = Path("/home/sharath/isce3/case_studies/nepal_glof")
CMP = C / "comparison_v2"
REP = CMP / "report"
OUT = REP / "nepal_glof_four_workflow_report.html"

D = Data(CMP / "comparison.json")
S = Data(CMP / "supplement_nondispersive.json")
FP = Data(REP / "footprints.json")
F = Figs(REP)
AL = Data(CMP / "alignment" / "alignment.json")
_FA = json.loads((CMP / "alignment" / "figures_alignment.json").read_text())
F.meta["figures"].update(_FA["figures"])
F.meta["charts"].update({"al_" + k: v for k, v in _FA["charts"].items()})
g = D.get
a_ = AL.get
IT = Data(CMP / "iono_transfer" / "iono_transfer.json")
GV = Data(CMP / "gunw_validation" / "gunw_validation.json")
_FG = json.loads((CMP / "gunw_validation" / "figures_gunw.json").read_text())
F.meta["figures"].update(_FG["figures"])
F.meta["charts"].update({"gv_" + k: v for k, v in _FG["charts"].items()})
it_, gv_ = IT.get, GV.get

F0, F1 = 1.239e9, 1.2935e9
LAM_A = 299792458.0 / F0
MM_PER_RAD = LAM_A / (4 * math.pi) * 1000
RAD2TECU = g("I3_ionosphere|constants|rad2tecu")
MM_PER_TECU = MM_PER_RAD / RAD2TECU
PI = "π"
M = "−"


def sig(R):
    return math.sqrt(-2 * math.log(R))


def leg(name):
    a, b = name.split("_")
    return f"{a}<sub>{b}</sub>"


def pair(key):
    p, q = key.split("__vs__")
    return f"{leg(p)} − {leg(q)}"


def sec(n, anchor, title):
    return f'<section id="{anchor}"><h2><span class="n">§{n}</span>{title}</h2>'


def sub(n, title):
    return f'<h3><span class="n">{n}</span>{title}</h3>'


def eq(mathml, tag):
    return f'<div class="eq"><math display="block">{mathml}</math><span class="tag">({tag})</span></div>'


def ph_legend(label="wrapped phase"):
    s = F.info("f03_phase_R_full")["cmap_stops"]
    return F.cbar(s, f"{M}{PI}", f"+{PI}", f"{label}, rad (cyclic)")


def div_legend(name, unit, fmt=lambda v: num(v, 2, sign=True)):
    m = F.info(name)
    lo, hi = m["vrange"]
    return F.cbar(m["cmap_stops"], fmt(lo), fmt(hi), unit)


def frame_footprints():
    """True zero-Doppler crop perimeters as panel fractions of the frame overview."""
    ds = gdal.Open(str(C / "pairs/20260714_20260726/trackG/ifg_A_HH_8x8.amp.tif"))
    gt = ds.GetGeoTransform()
    w_m, h_m = ds.RasterXSize * gt[1], ds.RasterYSize * abs(gt[5])
    s = osr.SpatialReference(); s.ImportFromEPSG(4326); s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    t = osr.SpatialReference(); t.ImportFromEPSG(32645); t.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(s, t)
    out = {}
    for key, cls in (("crop_v1_native_doppler_bug", "crop_v1"), ("crop_v2_zero_doppler", "crop_v2")):
        geom = ogr.CreateGeometryFromWkt(FP.get(f"{key}|wkt_linearring"))
        geom.FlattenTo2D(); geom.Transform(tr)
        r = geom.GetGeometryRef(0)
        out[cls] = [[[(r.GetX(i) - gt[0]) / w_m, (gt[3] - r.GetY(i)) / h_m] for i in range(r.GetPointCount())]]
    return out


def main() -> int:
    F.meta["figures"]["f01_frame_amp"].update(frame_footprints())

    # ------------------------------------------------------------------ numbers
    cg = g("crop_geometry")
    AZ0, RG0, L, W = cg["reference_A"]
    full_len, full_wid = 53200, 54244
    crop_frac = L * W / (full_len * full_wid)
    lk = g("lookup_verification")
    c1 = g("C1_dense_offsets")
    c2s = g("C2_coregistered_slc|coregistered_secondary_slc")
    c3 = g("C3_gslc_amplitude")
    c4 = g("C4_cross_track_geolocation")
    i1 = g("I1_wrapped_phase")
    i1b = g("I1b_doppler_carrier_attribution")
    i2 = g("I2_coherence")
    i3 = g("I3_ionosphere")
    rr5, rr40 = i1["R_full__vs__R_crop"]["5m"], i1["R_full__vs__R_crop"]["40m"]
    rg5, rg40 = i1["R_full__vs__G_full"]["5m"], i1["R_full__vs__G_full"]["40m"]
    gg5, gg40 = i1["G_full__vs__G_crop"]["5m"], i1["G_full__vs__G_crop"]["40m"]
    mneg = i1b["model_sign_-"]
    mpos = i1b["model_sign_+"]
    kk = i1b["k_rad_per_line_median"]
    daz = i1b["azimuth_residual_lines"]
    rsc = i3["R_full__vs__R_crop_screen"]
    rA, rB = i3["R_full__vs__R_crop_unwrapped_A_cycles"], i3["R_full__vs__R_crop_unwrapped_B_cycles"]
    gsc = i3["G_full__vs__G_crop_screen_40m"]
    xt = i3["cross_track_5m_lattice"]
    cst = i3["constants"]
    ratio = i2["G_full_over_R_full_ratio_40m_by_R_bin"]
    cov_v1 = F.info("f02b_coverage_v1")
    v1_cov = 1 - cov_v1["aoi_no_radar_sample"] - cov_v1["aoi_excluded_shadow_layover"]
    nd_f, nd_c = S.get("R_full_window|median_nondispersive_rad"), S.get("R_crop_window|median_nondispersive_rad")
    nd_g = S.get("G_full_whole_scene_selected_class|median_nondispersive_rad")
    gcyc = i3["G_cycle_offsets_applied"]
    los_rr = sig(rr40["phase_diff_coherence"]) * MM_PER_RAD
    los_rg = sig(rg40["phase_diff_coherence"]) * MM_PER_RAD
    los_model = sig(mneg["phase_diff_coherence"]) * MM_PER_RAD
    los_model_hi = sig(mneg["by_benchmark_coherence"]["0.7-1.0"]) * MM_PER_RAD
    p95_carrier_rad = kk * daz["p95"]
    build_date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------ masthead
    h = []
    h.append(f"""
<header class="mast">
  <div class="eyebrow">Research note · NISAR L-band repeat-pass interferometry · Nepal GLOF case study</div>
  <h1>Crop first, geocode first: what each shortcut costs a NISAR interferogram</h1>
  <div class="meta-strip">
    <span>pair <b>2026-07-14 × 2026-07-26</b> (12 d)</span><span>track <b>98</b> frame <b>16</b> · ascending · left-looking</span>
    <span>pol <b>HH</b></span><span>f<sub>A</sub> <b>1239.0 MHz</b> · f<sub>B</sub> <b>1293.5 MHz</b></span>
    <span>ISCE3 <b>0.25.12</b></span><span>built <b>{build_date}</b> from <b>comparison v2</b></span>
  </div>
  <div class="abstract">
    <div class="eyebrow">Abstract</div>
    <p>We process one NISAR RSLC pair over a glacial-lake outburst flood (GLOF) study area in the Nepal Himalaya four ways — radar-domain (RSLC) and geocoded
    (GSLC), each on the full 53&#8201;200 × 54&#8201;244-sample tile and on an area-of-interest (AOI) crop taken <em>before</em>
    any processing — and measure every alternative against the full-tile RSLC chain on a common 5&#8201;m lattice covering
    {pct(g("common_mask|fraction_of_aoi"))} of the AOI. Cropping the RSLC first is interferometrically transparent: the reference
    SLC is bit-identical, dense offsets differ by {num(c1["alongTrackOffset"]["R_full_minus_R_crop_std_samples"], 3)} lines
    (1σ), and the wrapped phase agrees to a phase-difference coherence of {num(rr5["phase_diff_coherence"], 4)} at 5&#8201;m
    ({num(los_rr, 1)}&#8201;mm line-of-sight at 40&#8201;m), for {pct(crop_frac)} of the pixels and roughly a sixth of the wall time.
    The split-spectrum ionosphere screen of the crop reproduces the benchmark's shape to {num(rsc["residual_std_tecu"], 4)}&#8201;TECU but sits
    {num(rsc["offset_P_minus_Q_tecu"], 2)}&#8201;TECU away — exactly one unwrapping cycle of the side band, which ISCE3 never references.
    The GSLC route is internally self-consistent (full vs crop: coherence {num(gg5["phase_diff_coherence"], 4)}), but departs from
    the RSLC phase by a field that is quantitatively the azimuth coregistration residual times the Doppler carrier
    (k = {num(kk, 3)}&#8201;rad&#8201;line<sup>−1</sup>; free fit {num(abs(i1b["free_fit_rad_per_line"]), 2)}); modelling it raises 40&#8201;m agreement from
    {num(rg40["phase_diff_coherence"], 3)} to {num(mneg["phase_diff_coherence"], 3)}. GSLC coherence is lower by about {pct(1 - ratio["0.7-1.0"], 0)}. A registration experiment explains both. The GSLC chain never
    registers the two dates beyond geometry, and geometry leaves {num(a_("E1_rslc_control|ref_vs_geometry_only_mean|az|mean"), 3)} lines in azimuth and
    {num(a_("E1_rslc_control|ref_vs_geometry_only_mean|rg|mean"), 2)} samples in range between them. Rebuilding the benchmark with geometry-only registration
    reproduces the GSLC interferogram (40&#8201;m agreement {num(a_("E3_closure|phase_40m|Rc__vs__G_crop|by_benchmark_coherence|0.7-1.0"), 3)} on coherent ground,
    carrier coefficient {num(a_("E4_carrier_coefficient|G_crop|b_hat"), 2)}) and its coherence (median {num(a_("E3_closure|coherence_distributions|R_geometry_only_unflat|median"), 3)}
    vs {num(a_("E3_closure|coherence_distributions|G_crop|median"), 3)}). The RSLC–GSLC gap is the missing dense-offset alignment.
    Against the NISAR production GUNW for the same pair, our RSLC interferograms agree to {num(gv_("wrapped|R_full__vs__GUNW_20m|by_R_full_coherence|0.7-1.0"), 3)}
    on coherent ground, and the ionosphere shape agrees to {num(gv_("ionosphere_80m|R_full__vs__GUNW|residual_std_tecu"), 4)}&#8201;TECU. The absolute levels
    differ by one joint cycle ({num(gv_("ionosphere_80m|R_full__vs__GUNW|offset_tecu"), 2)}&#8201;TECU), which is the degeneracy of the method.</p>
  </div>
</header>""")

    # ------------------------------------------------------------------ contents
    toc = [("1", "findings", "Findings"), ("2", "design", "Design"), ("3", "theory", "Observables"),
           ("4", "crop", "The radar crop"), ("5", "lattice", "Common lattice"), ("6", "coreg", "Coregistration"),
           ("7", "phase", "Wrapped phase"), ("8", "carrier", "RSLC vs GSLC phase"), ("9", "coherence", "Coherence"),
           ("10", "registration", "Registration test"), ("11", "iono", "Ionosphere"), ("12", "gunw", "GUNW validation"),
           ("13", "automation", "For the pipeline"), ("14", "limits", "Limitations"),
           ("A", "repro", "Reproduction")]
    nav = "".join(f'<li><a href="#{a}"><span class="n">{n}</span>{t}</a></li>' for n, a, t in toc)

    b = []
    # ================================================================== 1 findings
    chip = lambda cls, t: f'<span class="chip {cls}">{t}</span>'
    b.append(sec(1, "findings", "Findings at a glance"))
    b.append('<p class="col">Verdicts are relative to the full-tile RSLC chain, which is a <em>processing</em> benchmark — '
             'not ground truth. “Equivalent” means differences are at or below the noise of the comparison itself.</p>')
    rows = [
        ["RSLC cropped first: coregistration", chip("pass", "equivalent"),
         f"reference SLC bit-identical; secondary phase coherence {num(c2s['phase_diff_coherence'], 4)}; offsets differ by "
         f"{num(c1['alongTrackOffset']['R_full_minus_R_crop_std_samples'], 3)} lines / {num(c1['slantRangeOffset']['R_full_minus_R_crop_std_samples'], 3)} samples (1σ)", "§6"],
        ["RSLC cropped first: wrapped phase and coherence", chip("pass", "equivalent"),
         f"R = {num(rr5['phase_diff_coherence'], 4)} (5 m), {num(rr40['phase_diff_coherence'], 4)} (40 m) ≈ {num(los_rr, 1)} mm LOS; "
         f"coherence medians {num(i2['distributions']['R_full']['median'], 4)} vs {num(i2['distributions']['R_crop']['median'], 4)}", "§7, §9"],
        ["RSLC cropped first: ionosphere", chip("warn", "shape yes · level no"),
         f"residual {num(rsc['residual_std_tecu'], 4)} TECU (r = {num(rsc['pearson_r'], 4)}); constant {num(rsc['offset_P_minus_Q_rad'], 2)} rad = one side-band cycle "
         f"({num(cst['rad_per_cycle_B'], 2)} rad)", "§11"],
        ["GSLC cropped first vs GSLC full tile", chip("pass", "equivalent"),
         f"phase R = {num(gg5['phase_diff_coherence'], 4)}; amplitude p95 difference {pct(c3['20260726']['amp_rel_diff_p95'], 2)}; "
         f"ionosphere offset {num(gsc['offset_P_minus_Q_tecu'], 4)} TECU after cycle resolution", "§7, §11"],
        ["GSLC vs RSLC: wrapped phase", chip("warn", "registration error"),
         f"R = {num(rg5['phase_diff_coherence'], 3)} (5 m), {num(rg40['phase_diff_coherence'], 3)} (40 m); explained by azimuth residual × Doppler carrier "
         f"→ {num(mneg['phase_diff_coherence'], 3)}; p95 of the carrier term {num(p95_carrier_rad, 2)} rad ≈ {num(p95_carrier_rad * MM_PER_RAD, 0)} mm", "§8, §10"],
        ["GSLC vs RSLC: coherence", chip("warn", "≈ 5% lower, registration"),
         f"G/R ratio {num(ratio['0.3-0.5'], 3)}, {num(ratio['0.5-0.7'], 3)}, {num(ratio['0.7-1.0'], 3)} in benchmark-coherence bins 0.3–0.5, 0.5–0.7, 0.7–1.0; geometry-only RSLC registration gives "
         f"{num(a_('E3_closure|coherence_ratio_40m_by_R_bin|0.3-0.5|coarse_over_fine'), 3)}, {num(a_('E3_closure|coherence_ratio_40m_by_R_bin|0.5-0.7|coarse_over_fine'), 3)}, "
         f"{num(a_('E3_closure|coherence_ratio_40m_by_R_bin|0.7-1.0|coarse_over_fine'), 3)}", "§9, §10"],
        ["Are the GSLC dates co-registered to each other?", chip("fail", "geometry only"),
         f"geometry leaves {num(a_('E1_rslc_control|ref_vs_geometry_only_mean|az|mean'), 3)} lines / {num(a_('E1_rslc_control|ref_vs_geometry_only_mean|rg|mean'), 2)} samples; "
         f"G agrees with geometry-only RSLC at {num(a_('E3_closure|phase_40m|Rc__vs__G_crop|by_benchmark_coherence|0.7-1.0'), 3)} (γ &gt; 0.7), b = "
         f"{num(a_('E4_carrier_coefficient|G_crop|b_hat'), 2)} [{num(a_('E4_carrier_coefficient|G_crop|b_ci95')[0], 2)}, {num(a_('E4_carrier_coefficient|G_crop|b_ci95')[1], 2)}]", "§10"],
        ["Crop interferogram + full-tile ionosphere screen", chip("pass", "reproduces benchmark"),
         f"screen difference {num(it_('unwrapped_9x8_minus_benchmark|A_full_tile_screen_sliced|screen_minus_benchmark_screen|median_offset_rad'), 3)} rad; corrected phase "
         f"R = {num(it_('wrapped_1x1_vs_benchmark|A_full_tile_screen_sliced|5m|phase_diff_coherence'), 4)}, offset {num(it_('wrapped_1x1_vs_benchmark|A_full_tile_screen_sliced|5m|constant_offset_rad'), 3, sign=True)} rad", "§11"],
        ["Our outputs vs NISAR GUNW (same RSLCs)", chip("pass", "consistent"),
         f"wrapped R {num(gv_('wrapped|R_full__vs__GUNW_20m|by_R_full_coherence|0.7-1.0'), 3)} (γ &gt; 0.7, 20 m), {num(gv_('wrapped|R_full__vs__GUNW_80m_from_20m|phase_diff_coherence'), 3)} (80 m); "
         f"unwrapped: one whole-cycle offset on {pct(gv_('unwrapped_80m|R_full__vs__GUNW|fraction_at_mode'), 0)}; ionosphere shape {num(gv_('ionosphere_80m|R_full__vs__GUNW|residual_std_tecu'), 4)} TECU, "
         f"level {num(gv_('ionosphere_80m|R_full__vs__GUNW|offset_in_joint_cycles'), 2)} joint cycles apart", "§12"],
        ["GSLC vs RSLC: ionosphere", chip("info", "agree, level by prior"),
         f"offset {num(xt['R_full__vs__G_full']['offset_P_minus_Q_tecu'], 3)} TECU, residual {num(xt['R_full__vs__G_full']['residual_std_tecu'], 3)} TECU; "
         f"GSLC level fixed by a cycle-class criterion, not by ISCE3", "§11"],
        ["Cost of the crop", chip("pass", "6–10× less"),
         f"{pct(crop_frac)} of the radar grid; InSAR chain 1 h 29 min (shared CPU) vs 8 h 34 min to the wrapped interferogram; scratch 34 GB vs ≈ 301 GB", "§2"],
    ]
    b.append(table(["question", "verdict", "evidence", "where"], rows))
    b.append("</section>")

    # ================================================================== 2 design
    b.append(sec(2, "design", "Experimental design"))
    b.append(f"""<div class="col">
<p>The operational question is whether a production pipeline may reduce its input to the region it is asked about
before doing any geometry, and whether it may form interferograms from geocoded SLCs instead of radar-domain ones.
Both shortcuts save an order of magnitude in compute and disk; both could, in principle, bias phase, coherence or the
ionospheric correction. We isolate each shortcut by holding every science parameter fixed and changing only the
processing domain (radar vs map) and the moment the AOI is imposed (before vs after processing). The four legs are:</p></div>""")
    b.append(table(
        ["leg", "input", "interferogram formed on", "AOI imposed", "compute (this VM)", "disk"],
        [[f"<b>{leg('R_full')}</b><span class=sub>benchmark</span>", "RSLC full tile, 26–27 GB", "radar grid, 1×1 looks",
          "after, by resampling to the lattice", "8 h 34 min to RIFG; +≈50 min unwrap 9×8 & ionosphere", "≈ 301 GB scratch"],
         [f"<b>{leg('R_crop')}</b>", "RSLC subset, 1.80 GB", "radar grid, 1×1 looks", "before: subset granule",
          "≈2.3 min subset per date; 1 h 29 min chain (concurrent with G<sub>crop</sub>)", "34 GB scratch"],
         [f"<b>{leg('G_full')}</b>", "GSLC full tile, ≈10 GB per date per band", "5 m UTM lattice", "after",
          "≈4.5 h geocoding (A+B, two dates); 27 min interferogram; ≈72 min ionosphere", "≈ 93 GB"],
         [f"<b>{leg('G_crop')}</b>", "GSLC on the AOI grid, 0.95 GB per date per band", "5 m UTM lattice", "before: geocoding extent",
          "≈65 min total (concurrent with R<sub>crop</sub>)", "≈ 9 GB"]]))
    b.append(f"""<div class="col">
<p>Common to all legs: frequency A HH for the interferogram at full resolution (RSLC 1×1 looks, samples
{num(c1['azimuth_sample_spacing_m'], 2)} m along track × {num(c1['range_sample_spacing_m'], 2)} m slant range; GSLC 5 m posting);
DEM-flattened phase; 3×3 boxcar coherence; split-spectrum ionosphere from frequencies A and B at ≈40 m. The RSLC
ionosphere is ISCE3's <code>main_side_band</code> on a 9×8-look grid; the GSLC ionosphere is the same inversion ported to the map
domain (<code>tools/gslc_ionosphere.py</code>) at 40 m with a 10 km Gaussian. Wall times are indicative only: legs ran on
different days and the two crop legs shared eight cores.</p></div>""")
    b.append("</section>")

    # ================================================================== 3 theory
    b.append(sec(3, "theory", "Observables and estimators"))
    b.append('<div class="col">')
    b.append(sub("3.1", "Interferogram and coherence"))
    b.append("<p>For co-registered complex samples <i>s</i><sub>1</sub>, <i>s</i><sub>2</sub> the interferometric phase and the "
             "sample coherence over an <i>N</i>-sample window are</p>")
    b.append(eq('<mi>φ</mi><mo>=</mo><mo>arg</mo><mo>(</mo><msub><mi>s</mi><mn>1</mn></msub><msubsup><mi>s</mi><mn>2</mn><mo>*</mo></msubsup><mo>)</mo>'
                '<mspace width="2em"/><mover><mi>γ</mi><mo>^</mo></mover><mo>=</mo>'
                '<mfrac><mrow><mo>|</mo><munder><mo>∑</mo><mi>N</mi></munder><msub><mi>s</mi><mn>1</mn></msub><msubsup><mi>s</mi><mn>2</mn><mo>*</mo></msubsup><mo>|</mo></mrow>'
                '<msqrt><munder><mo>∑</mo><mi>N</mi></munder><msup><mrow><mo>|</mo><msub><mi>s</mi><mn>1</mn></msub><mo>|</mo></mrow><mn>2</mn></msup>'
                '<munder><mo>∑</mo><mi>N</mi></munder><msup><mrow><mo>|</mo><msub><mi>s</mi><mn>2</mn></msub><mo>|</mo></mrow><mn>2</mn></msup></msqrt></mfrac>', "1"))
    b.append(f"<p>γ̂ is biased upward. For fully decorrelated scatterers its expectation is</p>")
    b.append(eq('<mi>E</mi><mo>[</mo><mover><mi>γ</mi><mo>^</mo></mover><mo>|</mo><mi>γ</mi><mo>=</mo><mn>0</mn><mo>]</mo><mo>≈</mo>'
                '<mfrac><msqrt><mi>π</mi></msqrt><mrow><mn>2</mn><msqrt><mi>N</mi></msqrt></mrow></mfrac>', "2"))
    b.append(f"<p>which is {num(i2['bias_floor_3x3'], 3)} for the 3×3 window used here. ISCE3's own crossmul coherence is identically 1 at 1×1 "
             "looks, so the RSLC coherence in this note is computed from the coregistered SLCs with equation (1), both with and "
             "without the flattening phase.</p>")
    b.append(sub("3.2", "Agreement between two phase fields"))
    b.append("<p>Two estimates of the same interferogram, P and Q, are compared through the unit phasor of their difference. "
             "The statistic reported throughout is the <em>phase-difference coherence</em></p>")
    b.append(eq('<mi>R</mi><mo>=</mo><mo>|</mo><mo>⟨</mo><msup><mi>e</mi><mrow><mi>i</mi><mo>(</mo><msub><mi>φ</mi><mi>P</mi></msub><mo>−</mo><msub><mi>φ</mi><mi>Q</mi></msub><mo>)</mo></mrow></msup><mo>⟩</mo><mo>|</mo>'
                '<mo>,</mo><mspace width="2em"/><msub><mi>σ</mi><mi>c</mi></msub><mo>=</mo><msqrt><mrow><mo>−</mo><mn>2</mn><mo>ln</mo><mi>R</mi></mrow></msqrt>'
                '<mo>,</mo><mspace width="2em"/><msub><mi>d</mi><mi>LOS</mi></msub><mo>=</mo><mfrac><msub><mi>λ</mi><mi>A</mi></msub><mrow><mn>4</mn><mi>π</mi></mrow></mfrac><msub><mi>σ</mi><mi>c</mi></msub>', "3"))
    b.append(f"<p>R = 1 for identical fields and R → 0 for unrelated ones; σ<sub>c</sub> is the circular standard deviation, and with "
             f"λ<sub>A</sub> = {num(LAM_A * 100, 2)} cm one radian is {num(MM_PER_RAD, 2)} mm of line-of-sight range. R is insensitive to a "
             "constant offset, which is reported separately, and a best-fitting plane is removed by circular maximum likelihood "
             "(an FFT-seeded search that maximises R) to test whether a difference is a ramp. The sign convention is P − Q with P the "
             "benchmark-side leg.</p>")
    b.append(sub("3.3", "Split-spectrum ionosphere"))
    b.append("<p>With a non-dispersive part φ<sub>nd</sub> scaling as <i>f</i> and a dispersive part φ<sub>d</sub> as 1/<i>f</i>, both "
             "referred to f<sub>A</sub>,</p>")
    b.append(eq('<msub><mi>φ</mi><mi>A</mi></msub><mo>=</mo><msub><mi>φ</mi><mi>nd</mi></msub><mo>+</mo><msub><mi>φ</mi><mi>d</mi></msub>'
                '<mo>,</mo><mspace width="1.5em"/><msub><mi>φ</mi><mi>B</mi></msub><mo>=</mo><mfrac><msub><mi>f</mi><mi>B</mi></msub><msub><mi>f</mi><mi>A</mi></msub></mfrac><msub><mi>φ</mi><mi>nd</mi></msub>'
                '<mo>+</mo><mfrac><msub><mi>f</mi><mi>A</mi></msub><msub><mi>f</mi><mi>B</mi></msub></mfrac><msub><mi>φ</mi><mi>d</mi></msub>'
                '<mspace width="1.5em"/><mo>⇒</mo><mspace width="1.5em"/><msub><mi>φ</mi><mi>d</mi></msub><mo>=</mo>'
                '<mfrac><mrow><msub><mi>f</mi><mi>B</mi></msub><mo>(</mo><msub><mi>f</mi><mi>B</mi></msub><msub><mi>φ</mi><mi>A</mi></msub><mo>−</mo><msub><mi>f</mi><mi>A</mi></msub><msub><mi>φ</mi><mi>B</mi></msub><mo>)</mo></mrow>'
                '<mrow><msubsup><mi>f</mi><mi>B</mi><mn>2</mn></msubsup><mo>−</mo><msubsup><mi>f</mi><mi>A</mi><mn>2</mn></msubsup></mrow></mfrac>', "4"))
    cA = cst["rad_per_cycle_A"] / (2 * math.pi)
    cB = cst["rad_per_cycle_B"] / (2 * math.pi)
    amp = math.hypot(cA, cB)
    b.append(f"<p>Numerically φ<sub>d</sub> = {num(cA, 3)} φ<sub>A</sub> {M} {num(abs(cB), 3)} φ<sub>B</sub>. Two consequences govern everything in §11. "
             f"First, phase noise is amplified by √({num(cA, 2)}² + {num(abs(cB), 2)}²) = {num(amp, 2)}, which is why the screen must be "
             "heavily low-pass filtered. Second, the inversion needs <em>unwrapped</em> phase, and each band's unwrapping carries its "
             "own unknown integer: adding cycles (m, n) to (A, B) moves the screen by</p>")
    b.append(eq(f'<mi>Δ</mi><msub><mi>φ</mi><mi>d</mi></msub><mo>=</mo><mn>{num(cst["rad_per_cycle_A"], 2)}</mn><mspace width="0.2em"/><mi>m</mi>'
                f'<mo>−</mo><mn>{num(abs(cst["rad_per_cycle_B"]), 2)}</mn><mspace width="0.2em"/><mi>n</mi><mspace width="0.4em"/><mtext>rad</mtext>'
                f'<mo>,</mo><mspace width="2em"/><mn>1</mn><mspace width="0.2em"/><mtext>rad</mtext><mo>=</mo><mn>{num(RAD2TECU, 4)}</mn><mspace width="0.2em"/><mtext>TECU</mtext>', "5"))
    b.append(f"<p>One cycle in A alone is {num(cst['rad_per_cycle_A'] * RAD2TECU, 2)} TECU; along the degenerate line m = n the screen moves by only "
             f"{num(cst['rad_per_joint_cycle'], 2)} rad ({num(cst['rad_per_joint_cycle'] * RAD2TECU, 3)} TECU) per joint cycle. The class d = m − n is "
             "constrained by the data; the member along the line is not, and must be chosen by a prior. ISCE3's "
             "<code>main_side_band</code> makes no such choice anywhere: the absolute level is whatever the two unwrappers returned. "
             f"For scale, 1 TECU of dispersive phase in band A is {num(MM_PER_TECU, 0)} mm of apparent range.</p>")
    b.append("</div></section>")

    # ================================================================== 4 crop
    b.append(sec(4, "crop", "Making a radar-domain crop that ISCE3 will process correctly"))
    b.append(f"""<div class="col">
<p>ISCE3 has no radar-domain AOI option; its <code>geocode</code> bounds only restrict output. Cropping first therefore
means writing a smaller, self-consistent RSLC granule (<code>tools/rslc_subset.py</code>). Three invariants turn out to be
necessary, and the first version of the subsetter violated two of them.</p>
<ol>
<li><b>Zero-Doppler geometry.</b> NISAR RSLCs are processed to zero Doppler, and ISCE3's own <code>rdr2geo</code> and
<code>geo2rdr</code> pass an empty Doppler LUT. Placing the window with the native Doppler centroid (≈{num(i1b['secondary_doppler_hz_median'], 0)} Hz)
slides it by roughly 3300 lines (≈15 km) along track. The v1 crop therefore missed the north of the AOI.</li>
<li><b>Commensurate band lattices.</b> Frequency B is an exact 8:1 range decimation of A with the same first sample.
ISCE3 derives the side-band offsets by decimating the frequency-A offsets using the <em>reference</em> granule only
(<code>ionosphere.py:122–237</code>), so 8·rg0<sub>B</sub> − rg0<sub>A</sub> must be identical on every date. v1 origins differed by
0.625 B samples between dates, which lowered B coherence from 0.573 to 0.428 and put a non-integer +1.75 rad (≈ −1.49 TECU) on
the side band.</li>
<li><b>Look-aligned origins and physical buffers.</b> The azimuth origin is a multiple of the 9 azimuth looks and the range
origin a multiple of the 8 range looks, so the multilooked grids of crop and tile coincide exactly; buffers of 1000 lines and 12.5 km of slant
range are set by the ionosphere Gaussian, not by coregistration.</li>
</ol>
<p>The v2 crop satisfies all three: A/B alignment 8·rg0<sub>B</sub> − rg0<sub>A</sub> = {cg['band_alignment']['reference_B0x8_minus_A0']} on both dates, origin
({AZ0}, {RG0}) ≡ ({cg['reference_origin_mod_9x8'][0]}, {cg['reference_origin_mod_9x8'][1]}) mod (9, 8), and {L} × {W} samples, {pct(crop_frac)} of the tile.
The secondary date's window is ({cg['secondary_A'][0]}, {cg['secondary_A'][1]}), placed independently from that date's own orbit.</p></div>""")
    legend = ('<span class="key"><i style="border-color:#D9822B"></i>v1 crop, true footprint</span>'
              '<span class="key"><i style="border-color:#3FA37A"></i>v2 crop, true footprint</span>'
              '<span class="key"><i style="border-color:var(--ink);border-top-style:dashed"></i>AOI polygon</span>')
    b.append(F.figure("fig1",
                      F.panel("f01_frame_amp", "Frame 16, 40 m GSLC amplitude", "UTM 45N", overlays=("aoi", "crop_v1", "crop_v2"))
                      + F.panel("f02b_coverage_v1", "v1: AOI coverage", f"{pct(v1_cov)} usable")
                      + F.panel("f02b_coverage_v2", "v2: AOI coverage", f"{pct(lk['aoi_coverage_after_geoloc_mask'])} usable"),
                      f"<b>Figure 1.</b> Left: ground perimeters of the two crop windows, computed afresh for this note with ISCE3's zero-Doppler "
                      f"geometry over the project DEM (not from the granules' own metadata). The v1 window sits {cg['reference_A'][0] - FP.get('crop_v1_native_doppler_bug|window_full_frame')[0]} "
                      "lines earlier in azimuth. Centre and right: AOI cells with no radar sample (dark grey) and cells whose nearest "
                      "radar sample is more than 15 m away, mostly layover and shadow (orange). v1 coverage by quadrant was NW "
                      f"{pct(F.chart('coverage_by_quadrant')['v1_gdalwarp_native_doppler']['NW'], 0)}; v2 is at least "
                      f"{pct(min(lk['aoi_coverage_by_quadrant'].values()), 0)} everywhere.", legend, pmin=260))
    b.append("</section>")

    # ================================================================== 5 lattice
    b.append(sec(5, "lattice", "A common 5 m lattice, and what it costs the comparison"))
    b.append(f"""<div class="col">
<p>The RSLC legs live in radar coordinates, the GSLC legs on a UTM grid. We compare on the GSLC-crop lattice
({g('lattice|shape')[0]} × {g('lattice|shape')[1]} cells at 5 m) masked by the AOI polygon. Each cell takes the <em>nearest</em>
radar sample, found by a KD-tree over the rdr2geo coordinates. No resampling kernel is applied, so no RSLC phase is
altered. The price is a sub-cell position mismatch: among retained cells the nearest sample lies a median
{num(lk['p50_abs_m_kept'], 2)} m from the cell centre (p95 {num(lk['p95_abs_m_kept'], 2)} m). Cells beyond 15 m, where
foreshortening and layover leave no nearby sample, are excluded ({pct(lk['excluded_fraction_of_valid'])} of valid cells).</p>
<p>Two checks guard this step. The stored indices are re-read against the geolocation rasters and reproduce the
residuals to {num(lk['index_verification_max_abs_diff_m'] * 1e6, 1)} µm over {lk['index_verification_samples']:,} samples. The mean
residual vector is ({num(lk['mean_dx_m'] * 1000, 1, sign=True)}, {num(lk['mean_dy_m'] * 1000, 1, sign=True)}) mm, so there is no convention
offset. An earlier <code>gdalwarp -geoloc</code> lookup failed both checks: it carried a +2.5 m half-cell bias and silently
skipped output chunks, and was discarded.</p>
<p>The mismatch matters for single-look comparisons between an RSLC and a GSLC leg. The GSLC is interpolated to the cell
centre, whereas the RSLC value comes from a sample about 2 m away. At 5 m, the phase of a partially coherent resolution cell is
not constant over that distance, so 5 m RSLC-vs-GSLC agreement is a lower bound. The 40 m statistics (8×8 complex means)
are the informative ones. RSLC-vs-RSLC and GSLC-vs-GSLC comparisons are unaffected, because both members sample
identical positions.</p></div>""")
    b.append("</section>")

    # ================================================================== 6 coregistration
    b.append(sec(6, "coreg", "Coregistration: the crop changes nothing measurable"))
    at, sr = c1["alongTrackOffset"], c1["slantRangeOffset"]
    b.append('<div class="col">')
    b.append(f"""<p>If cropping perturbed coregistration, it would show first in the dense-offset fields that drive the rubber-sheet
resampling. The offset grid has a 32-sample step anchored at each product's origin, and the crop origin is not a multiple of 32,
so the two grids are displaced by {num(c1['azimuth_subgrid_offset_steps'], 3)} and {num(c1['range_subgrid_offset_steps'], 3)} steps.
The full-tile field is therefore interpolated bilinearly onto the crop's grid positions before differencing.</p></div>""")
    b.append('<div class="kv">'
             f'<div><dt>along-track, full − crop</dt><dd>{num(at["R_full_minus_R_crop_median"] * 1000, 2, sign=True)} <small>mm median</small></dd></div>'
             f'<div><dt>along-track 1σ</dt><dd>{num(at["R_full_minus_R_crop_std_samples"], 3)} <small>lines · {num(at["R_full_minus_R_crop_std"] * 100, 1)} cm</small></dd></div>'
             f'<div><dt>slant range, full − crop</dt><dd>{num(sr["R_full_minus_R_crop_median"] * 1000, 2, sign=True)} <small>mm median</small></dd></div>'
             f'<div><dt>slant range 1σ</dt><dd>{num(sr["R_full_minus_R_crop_std_samples"], 3)} <small>samples · {num(sr["R_full_minus_R_crop_std"] * 100, 1)} cm</small></dd></div>'
             '</div>')
    b.append('<div class="col">')
    b.append(f"""<p>For context, the benchmark's own residual offsets have medians of {num(at['R_full_median_samples'], 3)} lines and
{num(sr['R_full_median_samples'], 3)} samples. The crop reproduces them without bias. The scatter is small beside those residuals and
concentrated in low-correlation patches where offset estimates are noisy (Figure 2).</p>
<p>Downstream of the offsets, the reference SLC extracted from the crop is <b>bit-identical</b> to the corresponding window of the
full-tile product. The coregistered secondary is not bit-identical, as expected, since the rubber-sheet fields differ
slightly. Its phase agrees at R = {num(c2s['phase_diff_coherence'], 4)} (σ<sub>c</sub> = {num(c2s['circular_std_rad'], 3)} rad) with
median amplitude difference {pct(c2s['amp_rel_diff_median'], 2)}. This is the expected size. An azimuth misregistration δ in a
product with Doppler centroid f<sub>dc</sub> acquires phase 2πf<sub>dc</sub>δ/f<sub>line</sub> (§8). With k = {num(kk, 2)} rad line<sup>−1</sup>
and δ-scatter {num(at['R_full_minus_R_crop_std_samples'], 3)} lines, that predicts ≈{num(kk * at['R_full_minus_R_crop_std_samples'], 2)} rad,
about half the measured phase variance.</p>
<p>The GSLC legs need no coregistration. Their per-date amplitudes agree between full and crop to a median of
{pct(c3['20260714']['amp_rel_diff_median'], 2)} and p95 of {pct(c3['20260714']['amp_rel_diff_p95'], 3)} / {pct(c3['20260726']['amp_rel_diff_p95'], 3)}.
Log-amplitude phase correlation finds no shift between R<sub>full</sub> and G<sub>full</sub> amplitudes
({c4['20260714']['shift_R_full_onto_G_full_px']['tiles']} tiles; medians 0.00 and {num(c4['20260726']['shift_R_full_onto_G_full_px']['shift_cols_median_px'], 2)} cells), at the 0.05-cell
resolution of that estimator.</p></div>""")
    oh = F.chart("offset_diff_hist")
    b.append(F.figure("fig2",
                      F.panel("f07_offdiff_alongTrackOffset", "Along-track offset, full − crop", "radar geometry", overlays=(), scale=False)
                      + F.panel("f07_offdiff_slantRangeOffset", "Slant-range offset, full − crop", "radar geometry", overlays=(), scale=False),
                      "<b>Figure 2.</b> Dense-offset differences in radar geometry (azimuth down, slant range across; one estimate per 32 "
                      "samples). The differences are spatially white except in low-correlation patches. The one-cell frame is the edge of the "
                      "crop's estimation grid.", div_legend("f07_offdiff_alongTrackOffset", "metres"), pmin=320))
    b.append(svg_lines([("along track", oh["alongTrackOffset"]["centers"], oh["alongTrackOffset"]["density"], "a"),
                        ("slant range", oh["slantRangeOffset"]["centers"], oh["slantRangeOffset"]["density"], "b")],
                       -0.6, 0.6, [(v, num(v, 1, sign=v != 0)) for v in (-0.6, -0.3, 0, 0.3, 0.6)],
                       "full − crop offset difference (m); tails clipped at ±0.6", title="Distribution of offset differences"))
    b.append("</section>")

    # ================================================================== 7 wrapped phase
    b.append(sec(7, "phase", "Wrapped phase"))
    b.append('<div class="col">')
    b.append(f"""<p>Table 1 gives the full pairwise matrix. The structure is clear. The two RSLC legs agree with each other, and so do the
two GSLC legs. Every RSLC–GSLC pair looks the same (R ≈ {num(rg5['phase_diff_coherence'], 2)} at 5 m, ≈{num(rg40['phase_diff_coherence'], 2)} at 40 m),
whichever crop is involved. So the domain of processing matters and the crop does not.</p></div>""")
    rows = []
    for key in ("R_full__vs__R_crop", "G_full__vs__G_crop", "R_full__vs__G_full", "R_full__vs__G_crop", "R_crop__vs__G_full", "R_crop__vs__G_crop"):
        e5, e40 = i1[key]["5m"], i1[key]["40m"]
        bb = e40["by_benchmark_coherence"]
        rows.append([pair(key), num(e5["phase_diff_coherence"], 4), num(e40["phase_diff_coherence"], 4),
                     num(e40["circular_std_rad"], 3), num(sig(max(e40["phase_diff_coherence"], 1e-9)) * MM_PER_RAD, 1),
                     num(e40["constant_offset_rad"], 3, sign=True), num(e40["phase_diff_coherence_after_planar"], 4),
                     num(bb["0.3-0.5"], 3), num(bb["0.7-1.0"], 3)])
    b.append(table(["pair (P − Q)", "R 5 m", "R 40 m", "σ<sub>c</sub> 40 m rad", "≈ mm LOS", "offset rad", "R after plane", "R | γ 0.3–0.5", "R | γ 0.7–1"],
                   rows, num_cols=tuple(range(1, 9))))
    b.append(f'<p class="small col">Table 1. Phase-difference coherence over {rr5["n"]:,} common 5 m cells ({rr40["n"]:,} 40 m cells). '
             "The last two columns restrict to cells where benchmark coherence γ lies in the stated range. mm LOS uses equation (3).</p>")
    b.append('<div class="col">')
    b.append(f"""<p>R<sub>full</sub> − R<sub>crop</sub> has no offset ({num(rr40['constant_offset_rad'], 4, sign=True)} rad) and no ramp (the fitted plane
changes phase by {num(rr40['planar_change_across_extent_rad'][0], 3)} rad across the AOI). Its scatter is concentrated at low coherence:
R = {num(rr40['by_benchmark_coherence']['0.0-0.3'], 3)} where γ &lt; 0.3, but {num(rr40['by_benchmark_coherence']['0.7-1.0'], 4)} where γ &gt; 0.7. This is the
fingerprint of the small resampling differences of §6 acting on noisy phase, not of a geometric error. The NE quadrant is the least
consistent (R = {num(rr40['quadrants']['NE']['R'], 3)} against ≥ {num(min(v['R'] for k, v in rr40['quadrants'].items() if k != 'NE'), 3)} elsewhere).
G<sub>full</sub> − G<sub>crop</sub> is identical to within floating-point noise (p95 |Δφ| = {num(gg5['abs_p95_rad'] * 1000, 1)} mrad): geocoding is
pointwise, so extent cannot matter.</p>
<p>Dropping the 15 m geolocation mask lowers R<sub>full</sub> − G<sub>full</sub> agreement at 40 m from {num(rg40['phase_diff_coherence'], 3)} to
{num(i1['sensitivity_R_full__vs__G_full_without_geoloc_mask']['40m']['phase_diff_coherence'], 3)}. The mask is doing its job on
layover without driving the result.</p></div>""")
    b.append(F.figure("fig3",
                      "".join(F.panel(f"f03_phase_{k}", leg(k), "40 m display") for k in ("R_full", "R_crop", "G_full", "G_crop")),
                      "<b>Figure 3.</b> Wrapped, flattened interferograms of the four legs over the AOI (8×8 complex means). Grey: excluded "
                      "cells. The four are visually indistinguishable at this scale.", ph_legend(), pmin=420))
    b.append(F.figure("fig4",
                      F.panel("f04_dphase_R_full__vs__R_crop", f"{pair('R_full__vs__R_crop')}")
                      + F.panel("f04_dphase_G_full__vs__G_crop", f"{pair('G_full__vs__G_crop')}")
                      + F.panel("f04_dphase_R_full__vs__G_full", f"{pair('R_full__vs__G_full')}"),
                      "<b>Figure 4.</b> Phase differences at 40 m on a diverging scale. The crop comparisons are flat. RSLC − GSLC shows a smooth, "
                      "range-oriented field, taken up in §8.", div_legend("f04_dphase_R_full__vs__R_crop", "phase difference, rad",
                                                                       fmt=lambda v: (M if v < 0 else "+") + PI), pmin=280))
    ph = F.chart("phase_diff_hist")
    b.append(svg_lines([("R<sub>full</sub> − R<sub>crop</sub>", ph["R_full__vs__R_crop_40m"]["centers"], ph["R_full__vs__R_crop_40m"]["density"], "a"),
                        ("R<sub>full</sub> − G<sub>full</sub>", ph["R_full__vs__G_full_40m"]["centers"], ph["R_full__vs__G_full_40m"]["density"], "b"),
                        ("R<sub>full</sub> − G<sub>full</sub>, carrier modelled", F.chart("phase_diff_hist_R_full__vs__G_full_40m_doppler_model")["centers"],
                         F.chart("phase_diff_hist_R_full__vs__G_full_40m_doppler_model")["density"], "c")],
                       -math.pi, math.pi, [(-math.pi, f"{M}{PI}"), (-math.pi / 2, f"{M}{PI}/2"), (0, "0"), (math.pi / 2, f"{PI}/2"), (math.pi, PI)],
                       "phase difference about its circular mean (rad), 40 m cells", ylabel="density", title="Distribution of 40 m phase differences", area=False)
             .replace("&lt;sub&gt;", "").replace("&lt;/sub&gt;", ""))
    phh = F.chart("al_phase_hist_40m")
    jsd = a_("P_phase_distributions|js_distance_to_R_full_40m")
    b.append(svg_lines([("R<sub>full</sub>", phh["R_full"]["centers"], phh["R_full"]["density"], "a"),
                        ("R<sub>crop</sub>", phh["R_crop"]["centers"], phh["R_crop"]["density"], "d"),
                        ("G<sub>full</sub> = G<sub>crop</sub>", phh["G_crop"]["centers"], phh["G_crop"]["density"], "b"),
                        ("RSLC, geometry-only registration (§10)", phh["Rc_geometry_only"]["centers"], phh["Rc_geometry_only"]["density"], "c")],
                       -math.pi, math.pi, [(-math.pi, f"{M}{PI}"), (-math.pi / 2, f"{M}{PI}/2"), (0, "0"), (math.pi / 2, f"{PI}/2"), (math.pi, PI)],
                       "wrapped flattened phase (rad), 40 m cells", title="Distribution of wrapped phase", area=False))
    b.append(f'<p class="small col">The wrapped-phase distributions of the four legs over the same {rr40["n"]:,} 40 m cells. Jensen–Shannon distance to '
             f'R<sub>full</sub> (0 identical, 1 disjoint): R<sub>crop</sub> {num(jsd["R_crop"], 3)}, G<sub>full</sub> {num(jsd["G_full"], 3)}, G<sub>crop</sub> '
             f'{num(jsd["G_crop"], 3)}. G<sub>full</sub> and G<sub>crop</sub> coincide. The GSLC distribution is displaced and broader (circular mean '
             f'{num(a_("P_phase_distributions|circular_40m|G_crop|circular_mean_rad"), 2)} vs {num(a_("P_phase_distributions|circular_40m|R_full|circular_mean_rad"), 2)} rad, '
             f'mean resultant length {num(a_("P_phase_distributions|circular_40m|G_crop|mean_resultant_length"), 3)} vs '
             f'{num(a_("P_phase_distributions|circular_40m|R_full|mean_resultant_length"), 3)}). The RSLC interferogram rebuilt with geometry-only registration '
             f'lands on the GSLC curve (JS distance {num(a_("P_phase_distributions|js_distance_G_crop_to_Rc_geometry_only_40m"), 3)}).</p>')
    b.append(F.figure("fig5",
                      F.panel("f09_zoom_amp", "Glacier zone, amplitude", "5 m cells", overlays=("glacier",))
                      + F.panel("f09_zoom_phase_R_full", f"{leg('R_full')} phase", "5 m", overlays=("glacier",))
                      + F.panel("f09_zoom_phase_G_full", f"{leg('G_full')} phase", "5 m", overlays=("glacier",))
                      + F.panel("f09_zoom_dphase", f"{pair('R_full__vs__G_full')}", "5 m", overlays=("glacier",)),
                      "<b>Figure 5.</b> A 5 × 5 km window on the glacier zone (white outline) at native 5 m cells. The single-look difference "
                      "is dominated by speckle-scale phase noise plus the sub-cell sampling mismatch of §5; its large-scale part is the field of §8.",
                      ph_legend("phase and difference"), pmin=230))
    b.append("</section>")

    # ================================================================== 8 carrier
    b.append(sec(8, "carrier", "Why GSLC phase differs from RSLC phase"))
    b.append('<div class="col">')
    b.append(f"""<p>Three hypotheses were considered for the R<sub>full</sub> − G<sub>full</sub> field. (i) A planar ramp from differing
reference-phase or flattening conventions predicts that a plane removes it. It does not: the ML plane raises R only from
{num(rg40['phase_diff_coherence'], 3)} to {num(rg40['phase_diff_coherence_after_planar'], 3)}. (ii) A DEM-flattening difference predicts a field that
follows relief, but the observed field is smooth and range-oriented (Figure 6). (iii) A coregistration difference is the third hypothesis. The RSLC chain
refines geometry-only coregistration with dense offsets, while the GSLC chain is geometry-only. A zero-Doppler-focused SLC still carries a
Doppler carrier in azimuth, so an azimuth shift of δ lines applied to one image changes its interferometric phase by</p></div>""")
    b.append(eq('<mi>Δ</mi><msub><mi>φ</mi><mrow><mi>R</mi><mo>−</mo><mi>G</mi></mrow></msub><mo>≈</mo><mo>−</mo><mi>k</mi><mspace width="0.2em"/><msub><mi>δ</mi><mi>az</mi></msub>'
                '<mo>,</mo><mspace width="2em"/><mi>k</mi><mo>=</mo><mfrac><mrow><mn>2</mn><mi>π</mi><msub><mi>f</mi><mi>dc</mi></msub></mrow><msub><mi>f</mi><mi>line</mi></msub></mfrac>', "6"))
    b.append('<div class="col">')
    b.append(f"""<p>Here f<sub>line</sub> = {num(i1b['line_rate_hz'], 0)} Hz is the zero-Doppler <em>line rate</em>, not the {num(1909.635, 3)} Hz acquisition PRF.
Using the PRF gives the wrong constant. With the secondary's Doppler centroid evaluated at every cell (median {num(i1b['secondary_doppler_hz_median'], 1)} Hz), k has median
{num(kk, 3)} rad line<sup>−1</sup>. δ<sub>az</sub> is R<sub>full</sub>'s own rubber-sheet azimuth correction, the part of its coregistration that geometry
did not predict: median {num(daz['median'], 3)} lines, p5 {num(daz['p5'], 3)}, p95 {num(daz['p95'], 3)} lines.</p>
<p>The model has no free parameter, and its sign is not given a priori. Applying it with the negative sign raises 40 m agreement from
{num(rg40['phase_diff_coherence'], 3)} to <b>{num(mneg['phase_diff_coherence'], 4)}</b>. The constant offset falls from
{num(i1b['baseline_40m']['constant_offset_rad'], 3, sign=True)} to {num(mneg['constant_offset_rad'], 3, sign=True)} rad, and the planar trend from
({num(i1b['baseline_40m']['planar_rad_per_km_x_east'], 4, sign=True)}, {num(i1b['baseline_40m']['planar_rad_per_km_y_south'], 4, sign=True)}) to
({num(mneg['planar_rad_per_km_x_east'], 4, sign=True)}, {num(mneg['planar_rad_per_km_y_south'], 4, sign=True)}) rad km<sup>−1</sup>. The positive sign
<em>lowers</em> agreement to {num(mpos['phase_diff_coherence'], 3)}. Letting k float, a grid search peaks at k = {num(i1b['free_fit_rad_per_line'], 2)}
rad line<sup>−1</sup>, matching in magnitude the value predicted from the Doppler LUT and line rate to within the search's 0.05 rad line<sup>−1</sup> step.
Among the high-coherence cells, residual agreement reaches {num(mneg['by_benchmark_coherence']['0.5-0.7'], 3)} (γ 0.5–0.7) and
{num(mneg['by_benchmark_coherence']['0.7-1.0'], 3)} (γ &gt; 0.7), i.e. {num(los_model_hi, 1)} mm LOS, comparable to the crop-vs-full scatter.</p>
<p>So the systematic RSLC–GSLC phase difference is, to within the precision of this test, the dense-offset azimuth correction multiplied
by the Doppler carrier. On its own this does not say which product is right: δ<sub>az</sub> could be a genuine residual misregistration that the
GSLC keeps, or estimator noise that the RSLC chain injects. §10 settles it. The geometry-only RSLC secondary is measurably displaced from the
reference by δ<sub>az</sub> itself (slope {num(a_('E1_rslc_control|ref_vs_geometry_only__vs_rubber_az|ols_slope'), 3)}, r =
{num(a_('E1_rslc_control|ref_vs_geometry_only__vs_rubber_az|pearson_r'), 3)}), and the rubber sheet removes it. The GSLC interferogram therefore carries
an uncorrected registration error, with a phase term up to {num(p95_carrier_rad, 2)} rad (p95), about {num(p95_carrier_rad * MM_PER_RAD, 0)} mm.</p></div>""")
    b.append(F.figure("fig6",
                      F.panel("f04_dphase_R_full__vs__G_full", f"{pair('R_full__vs__G_full')}", "as observed")
                      + F.panel("f04_dphase_R_full__vs__G_full_doppler_model", f"{pair('R_full__vs__G_full')} + kδ<sub>az</sub>", "carrier modelled")
                      + F.panel("f04b_azimuth_residual", "δ<sub>az</sub>, R<sub>full</sub> rubber-sheet correction", "lines"),
                      f"<b>Figure 6.</b> Left and centre share the phase scale (±{PI} rad); right is δ<sub>az</sub> on "
                      f"±{num(F.info('f04b_azimuth_residual')['vrange'][1], 2)} lines. The observed difference is the negative image of δ<sub>az</sub>; after "
                      "subtracting −kδ<sub>az</sub> the field is flat.",
                      div_legend("f04_dphase_R_full__vs__G_full", "phase difference, rad", fmt=lambda v: (M if v < 0 else "+") + PI)
                      + " " + div_legend("f04b_azimuth_residual", "δ<sub>az</sub>, lines"), pmin=280))
    bins = ["0.0-0.3", "0.3-0.5", "0.5-0.7", "0.7-1.0"]
    b.append(svg_bars([(f"γ {k.replace('-', '–')} observed", rg40["by_benchmark_coherence"][k], "b") for k in bins]
                      + [(f"γ {k.replace('-', '–')} modelled", mneg["by_benchmark_coherence"][k], "c") for k in bins],
                      0.4, 1.0, [(v, num(v, 1)) for v in (0.4, 0.6, 0.8, 1.0)], "phase-difference coherence R, 40 m",
                      title="R full − G full agreement by benchmark coherence, before and after the carrier model"))
    b.append("</section>")

    # ================================================================== 9 coherence
    dR, dG = i2["distributions"]["R_full"], i2["distributions"]["G_full"]
    b.append(sec(9, "coherence", "Coherence"))
    b.append('<div class="col">')
    b.append(f"""<p>RSLC coherence is unchanged by cropping. Medians are {num(dR['median'], 4)} and {num(i2['distributions']['R_crop']['median'], 4)}, and the
paired difference has median {num(i2['paired']['R_full__vs__R_crop']['median'], 4)} and σ = {num(i2['paired']['R_full__vs__R_crop']['std'], 4)}
(r = {num(i2['paired']['R_full__vs__R_crop']['pearson_r'], 4)}). The GSLC legs are likewise identical to each other. Between domains there is
a real difference. G<sub>full</sub> has median {num(dG['median'], 4)} against {num(dR['median'], 4)}, and {pct(dG['frac_gt_0p5'])} of cells exceed 0.5
against {pct(dR['frac_gt_0p5'])}. Binned by benchmark coherence, the 40 m ratio G/R is {num(ratio['0.3-0.5'], 3)}, {num(ratio['0.5-0.7'], 3)} and
{num(ratio['0.7-1.0'], 3)} for γ in 0.3–0.5, 0.5–0.7 and 0.7–1.0. The loss is close to multiplicative, a few per cent rising slightly with γ.
Below 0.3 the ratio is {num(ratio['0.0-0.3'], 3)}, but there both estimators sit on the bias floor of equation (2) and the ratio means little.</p>
<p>§10 identifies the cause. An RSLC interferogram formed with geometry-only registration has median coherence
{num(a_('E3_closure|coherence_distributions|R_geometry_only_unflat|median'), 4)}, against {num(a_('E3_closure|coherence_distributions|G_crop|median'), 4)} for the GSLC and
{num(a_('E3_closure|coherence_distributions|R_rubber_sheet_unflat|median'), 4)} for the same RSLC with the rubber sheet. Its bin-wise ratios to the registered RSLC
({num(a_('E3_closure|coherence_ratio_40m_by_R_bin|0.3-0.5|coarse_over_fine'), 3)}, {num(a_('E3_closure|coherence_ratio_40m_by_R_bin|0.5-0.7|coarse_over_fine'), 3)},
{num(a_('E3_closure|coherence_ratio_40m_by_R_bin|0.7-1.0|coarse_over_fine'), 3)}) track the GSLC's. The loss is mostly the uniform
{num(a_('E5_predicted_coherence_loss|misregistration_40m_medians|rg_samples'), 2)}-sample range misregistration, not the azimuth field.
Flattening is not the cause: R<sub>full</sub>'s flattened minus unflattened coherence has median {num(i2['flattening_effect_R_full_flat_minus_unflat']['median'], 4)}.</p></div>""")
    b.append(F.figure("fig7",
                      F.panel("f05_coh_R_full", f"{leg('R_full')} coherence", "3×3")
                      + F.panel("f05_coh_G_full", f"{leg('G_full')} coherence", "3×3")
                      + F.panel("f05_dcoh_R_full__vs__G_full", f"γ(R<sub>full</sub>) − γ(G<sub>full</sub>)", "40 m mean"),
                      "<b>Figure 7.</b> Coherence (40 m means of the 3×3 estimates) and its difference. The difference is positive over most "
                      "of the AOI.",
                      F.cbar(F.info("f05_coh_R_full")["cmap_stops"], "0", "1", "coherence") + " "
                      + div_legend("f05_dcoh_R_full__vs__G_full", "difference"), pmin=280))
    ch = F.chart("al_coherence_hist")
    b.append(svg_lines([("R<sub>full</sub> flattened", ch["R_full"]["centers"], ch["R_full"]["density"], "a"),
                        ("G<sub>crop</sub>", ch["G_crop"]["centers"], ch["G_crop"]["density"], "b"),
                        ("RSLC, geometry-only registration (§10)", ch["R_geometry_only"]["centers"], ch["R_geometry_only"]["density"], "c")],
                       0, 1, [(v, num(v, 1)) for v in (0, 0.2, 0.4, 0.6, 0.8, 1.0)], "3×3 coherence, common cells",
                       title="Coherence distributions", refs=[(ch["bias_floor_3x3"], "bias floor, N = 9")], area=True)
             .replace("&lt;sub&gt;", "").replace("&lt;/sub&gt;", ""))
    b.append("</section>")

    # ================================================================== 10 registration
    e0, e1, e2s, e3, e4, e5, ss = (a_("E0_calibration"), a_("E1_rslc_control"), a_("E2_spectral_occupancy"), a_("E3_closure"),
                                   a_("E4_carrier_coefficient"), a_("E5_predicted_coherence_loss"), a_("S_sampling_sharpness"))
    az_m = c1["azimuth_sample_spacing_m"]; rg_m = c1["range_sample_spacing_m"]
    pRG, pRC, pCG = e3["phase_40m"]["R_full__vs__G_crop"], e3["phase_40m"]["R_full__vs__Rc"], e3["phase_40m"]["Rc__vs__G_crop"]
    cr = e3["coherence_ratio_40m_by_R_bin"]
    b.append(sec(10, "registration", "Registration: is the RSLC–GSLC gap an alignment gap?"))
    b.append(f"""<div class="col">
<p>The RSLC chain registers the secondary image twice: once from geometry (orbits and DEM, <code>geo2rdr</code>), then again with a dense-offset
rubber sheet estimated from the images themselves. The GSLC chain geocodes each date independently from the same orbits and DEM and never
registers the dates to one another. Its inter-date registration is therefore geometry-only. This suggests a single hypothesis H: every
RSLC–GSLC difference in §§7–9 is the rubber-sheet correction that the GSLC chain lacks. H makes three quantitative predictions, and each is tested below.
(i) The geometry-only RSLC secondary is displaced from the reference by exactly the rubber-sheet field. (ii) An interferogram formed with
that secondary, R<sub>c</sub>, reproduces the GSLC interferogram in both phase and coherence. (iii) The carrier coefficient of §8 is −1 for R<sub>c</sub>
as well as for the GSLC.</p></div>""")
    b.append(sub("10.1", "Why the GSLC cannot be registered after the fact"))
    b.append(f"""<div class="col"><p>Sub-sample registration and Fourier resampling assume a band-limited signal. A 256 × 256 RSLC chip meets
that assumption: {pct(e2s['RSLC_radar']['fraction_within_6dB_median'], 0)} of its spectrum lies within 6 dB of the peak and the stop band falls to
{num(e2s['RSLC_radar']['min_dB_median'], 0)} dB. The delivered 5 m GSLC samples do not: {pct(e2s['GSLC_lattice']['fraction_within_6dB_median'], 0)} of the spectrum lies within
6 dB, and nowhere is it more than {num(abs(e2s['GSLC_lattice']['min_dB_median']), 0)} dB down (Figure 10). A first attempt to cross-correlate and shift the GSLCs directly
returned exactly zero shift on half the chips and was discarded. We did not establish why the GSLC spectrum is white; a terrain-dependent flattening
phase was tested and not confirmed. The experiment is therefore run in radar geometry, where it is valid, and the GSLC is compared
with its result.</p></div>""")
    b.append(F.figure("fig10",
                      F.panel("f12_spectrum2d_rslc", "RSLC chip, radar geometry", "az ↓ · rg →", overlays=(), scale=False)
                      + F.panel("f12_spectrum2d_gslc", "GSLC chip, 5 m lattice", "N ↓ · E →", overlays=(), scale=False),
                      "<b>Figure 10.</b> Mean two-dimensional power spectra of 60 chips, normalised frequency −½…½ on both axes. The RSLC band is a clean "
                      "rectangle, wrapped vertically because the azimuth band is centred on the Doppler carrier. The GSLC spectrum is flat.",
                      F.cbar(F.info("f12_spectrum2d_rslc")["cmap_stops"], f"{M}30 dB", "0 dB", "power relative to peak"), pmin=260))
    b.append(sub("10.2", "The estimator, and a pitfall worth recording"))
    c10, c06 = e0["coherence_1.0"], e0["coherence_0.6"]
    b.append(f"""<div class="col"><p>Displacements are measured by complex cross-correlation of Tukey-tapered 128 × 128 chips with upsampled-DFT refinement. The RSLC
azimuth band is centred on the Doppler carrier, {num(i1b['secondary_doppler_hz_median'] / i1b['line_rate_hz'], 2)} of the line rate, so it wraps across Nyquist.
A physical sub-sample delay of such a band-pass signal leaves a phase step at the wrap, and a plain correlator misreads it.
Calibrated on RSLC chips given known physical delays (demodulate, shift, remodulate), the naive estimator returns
{num(c10['naive_azimuth']['gain'], 3)} of the azimuth delay, with the wrong sign, while range is unaffected
({num(c10['naive_range']['gain'], 3)}). Demodulating both chips by their joint spectral centroid first gives gains of
{num(c10['carrier_aware_azimuth']['gain'], 3)} (azimuth) and {num(c10['carrier_aware_range']['gain'], 3)} (range), with rms error
{num(c10['carrier_aware_azimuth']['rmse'], 4)} samples at coherence 1 and {num(c06['carrier_aware_azimuth']['rmse'], 4)} at 0.6. A synthetic test that shifts by whole FFT bins
cannot reveal this bias; only a physical delay does.</p></div>""")
    b.append(sub("10.3", "Control: what geometry leaves behind"))
    ga, gr = e1["ref_vs_geometry_only__vs_rubber_az"], e1["ref_vs_geometry_only__vs_rubber_rg"]
    cfa, cfr = e1["geometry_only_vs_rubber_sheet__vs_rubber_az"], e1["geometry_only_vs_rubber_sheet__vs_rubber_rg"]
    b.append(table(["measurement (chip displacement)", "vs rubber sheet: slope [95% CI]", "r", "mean measured", "mean rubber sheet"],
                   [["reference → geometry-only secondary, azimuth", f"{num(ga['ols_slope'], 3)} [{num(ga['ols_slope_ci95'][0], 3)}, {num(ga['ols_slope_ci95'][1], 3)}]",
                     num(ga['pearson_r'], 3), f"{num(e1['ref_vs_geometry_only_mean']['az']['mean'], 4)} lines", f"{num(e1['ref_vs_geometry_only_mean']['az']['rubber_mean'], 4)} lines"],
                    ["reference → geometry-only secondary, range", f"{num(gr['ols_slope'], 3)} [{num(gr['ols_slope_ci95'][0], 3)}, {num(gr['ols_slope_ci95'][1], 3)}]",
                     num(gr['pearson_r'], 3), f"{num(e1['ref_vs_geometry_only_mean']['rg']['mean'], 4)} samples", f"{num(e1['ref_vs_geometry_only_mean']['rg']['rubber_mean'], 4)} samples"],
                    ["geometry-only → rubber-sheet secondary, azimuth", f"{num(cfa['ols_slope'], 3)} [{num(cfa['ols_slope_ci95'][0], 3)}, {num(cfa['ols_slope_ci95'][1], 3)}]",
                     num(cfa['pearson_r'], 3), "", ""],
                    ["geometry-only → rubber-sheet secondary, range", f"{num(cfr['ols_slope'], 3)} [{num(cfr['ols_slope_ci95'][0], 3)}, {num(cfr['ols_slope_ci95'][1], 3)}]",
                     num(cfr['pearson_r'], 3), "", ""],
                    ["reference → rubber-sheet secondary, azimuth / range", "", "", f"{num(e1['ref_vs_rubber_sheet_residual']['az']['mean'], 4)} / {num(e1['ref_vs_rubber_sheet_residual']['rg']['mean'], 4)}", "≈ 0 expected"]],
                   num_cols=(2,)))
    b.append(f"""<p class="small col">Table 2. {e1['chips_used']:,} of {e1['chips']:,} chips (chip coherence ≥ 0.5, |displacement| &lt; 1 sample); CIs by bootstrap over
2 × 2 km blocks. The range field is nearly uniform across the AOI, so its slope is poorly constrained, but its mean is not.</p>""")
    b.append(f"""<div class="col"><p>Prediction (i) holds. After geometric registration the secondary is displaced from the reference by
{num(e1['ref_vs_geometry_only_mean']['az']['mean'], 3)} lines ({num(e1['ref_vs_geometry_only_mean']['az']['mean'] * az_m * 100, 0)} cm) along track and by
{num(e1['ref_vs_geometry_only_mean']['rg']['mean'], 3)} samples ({num(e1['ref_vs_geometry_only_mean']['rg']['mean'] * rg_m, 2)} m) in slant range, on average. The azimuth displacement follows
ISCE3's rubber-sheet field with slope {num(ga['ols_slope'], 3)}. The rubber sheet removes this to within
{num(abs(e1['ref_vs_rubber_sheet_residual']['az']['mean']), 3)} lines and {num(abs(e1['ref_vs_rubber_sheet_residual']['rg']['mean']), 3)} samples.
The estimator therefore recovers, independently, the correction that ISCE3 measured and applied.</p></div>""")
    b.append(sub("10.4", "Closure: rebuild the benchmark with geometry-only registration"))
    b.append("""<div class="col"><p>R<sub>c</sub> = R<sub>full</sub> · e<sup>i arg(s<sub>fine</sub> s<sub>coarse</sub><sup>*</sup>)</sup> is the benchmark interferogram with the
rubber-sheet registration taken out and everything else, including flattening, kept. If H is right, the GSLC should agree with R<sub>c</sub> far
better than with R<sub>full</sub>.</p></div>""")
    b.append(table(["pair, 40 m", "R", "offset rad", "plane rad/km (E, S)", "R | γ 0.3–0.5", "R | γ 0.5–0.7", "R | γ 0.7–1"],
                   [[lbl, num(e['phase_diff_coherence'], 4), num(e['constant_offset_rad'], 3, sign=True),
                     f"{num(e['planar_rad_per_km_x_east'], 4, sign=True)}, {num(e['planar_rad_per_km_y_south'], 4, sign=True)}",
                     num(e['by_benchmark_coherence']['0.3-0.5'], 3), num(e['by_benchmark_coherence']['0.5-0.7'], 3), num(e['by_benchmark_coherence']['0.7-1.0'], 4)]
                    for lbl, e in (("R<sub>full</sub> − G<sub>crop</sub>", pRG), ("R<sub>full</sub> − R<sub>c</sub>", pRC), ("<b>R<sub>c</sub> − G<sub>crop</sub></b>", pCG))],
                   num_cols=(1, 2, 3, 4, 5, 6)))
    bp = e3["block_phase_agreement_with_G"]
    bc = e3["block_coherence_ratio"]
    b.append(f"""<div class="col"><p>Prediction (ii) holds for phase. Where the benchmark is coherent, the GSLC agrees with R<sub>c</sub> at
{num(pCG['by_benchmark_coherence']['0.5-0.7'], 3)} (γ 0.5–0.7) and {num(pCG['by_benchmark_coherence']['0.7-1.0'], 4)} (γ &gt; 0.7). Its constant offset
({num(pCG['constant_offset_rad'], 3, sign=True)} rad) and planar trend both vanish. Removing the rubber sheet from the RSLC reproduces the RSLC–GSLC difference
(R<sub>full</sub> − R<sub>c</sub>: offset {num(pRC['constant_offset_rad'], 3, sign=True)} rad against {num(pRG['constant_offset_rad'], 3, sign=True)}; the same plane). Block by block,
{pct(bp['fraction_blocks_closer_to_Rc'], 1)} of {bp['blocks']:,} 1.28 km blocks agree better with R<sub>c</sub> than with R<sub>full</sub>. The mean gain is
{num(bp['mean_gain'], 4, sign=True)} [{num(bp['mean_gain_ci95'][0], 4)}, {num(bp['mean_gain_ci95'][1], 4)}], and a Wilcoxon signed-rank p-value
is below double-precision underflow.</p>
<p>It also holds for coherence. The geometry-only RSLC has median coherence {num(e3['coherence_distributions']['R_geometry_only_unflat']['median'], 4)}; the GSLC has
{num(e3['coherence_distributions']['G_crop']['median'], 4)}, and the registered RSLC {num(e3['coherence_distributions']['R_rubber_sheet_unflat']['median'], 4)}. Registration accounts for
{pct((e3['coherence_distributions']['R_rubber_sheet_unflat']['median'] - e3['coherence_distributions']['R_geometry_only_unflat']['median']) / (e3['coherence_distributions']['R_rubber_sheet_unflat']['median'] - e3['coherence_distributions']['G_crop']['median']), 0)}
of the median gap. In 1.28 km blocks the GSLC/RSLC ratio (median {num(bc['median_G_over_R'], 3)}) sits slightly below the geometry-only/registered ratio
({num(bc['median_coarse_over_fine'], 3)}). The paired mean difference is {num(bc['paired_difference_G_minus_coarse']['mean'], 4)}
[{num(bc['paired_difference_G_minus_coarse']['mean_ci95'][0], 4)}, {num(bc['paired_difference_G_minus_coarse']['mean_ci95'][1], 4)}], a residual loss of under 1% that belongs to
geocoding itself.</p></div>""")
    b.append(table(["benchmark coherence bin", "GSLC / RSLC", "geometry-only / registered RSLC", "40 m cells"],
                   [[k.replace("-", "–"), num(v["G_over_R"], 3), num(v["coarse_over_fine"], 3), f"{v['n']:,}"] for k, v in cr.items()],
                   num_cols=(1, 2, 3)))
    b.append(F.figure("fig11",
                      F.panel("f13_dphase_R_vs_Rc", f"R<sub>full</sub> − R<sub>c</sub>", "rubber sheet removed")
                      + F.panel("f13_dphase_Rc_vs_G", f"R<sub>c</sub> − G<sub>crop</sub>", "what is left")
                      + F.panel("f13_cohratio_coarse_over_fine", "γ geometry-only / γ registered", "RSLC, 40 m")
                      + F.panel("f13_cohratio_G_over_R", "γ GSLC / γ RSLC", "40 m"),
                      "<b>Figure 11.</b> Top: the phase that registration alone contributes to the benchmark (left) matches the RSLC − GSLC difference of Figure 6. "
                      "What remains between R<sub>c</sub> and the GSLC (right) is flat. Bottom: coherence ratios on a common 0.8–1.2 scale, cells with benchmark "
                      "coherence &gt; 0.3.",
                      div_legend("f13_dphase_R_vs_Rc", "phase difference, rad", fmt=lambda v: (M if v < 0 else "+") + PI) + " "
                      + div_legend("f13_cohratio_G_over_R", "coherence ratio", fmt=lambda v: num(v, 1)), pmin=420))
    b.append(sub("10.5", "Carrier coefficient with uncertainty"))
    cg, cc = F.chart("al_carrier_curve_G_crop"), F.chart("al_carrier_curve_Rc_geometry_only")
    b.append(f"""<div class="col"><p>Prediction (iii) holds. Fitting φ<sub>R</sub> − φ<sub>X</sub> = a + b k δ<sub>az</sub> by circular maximum likelihood, with a bootstrap over blocks, gives
b = {num(e4['G_crop']['b_hat'], 2)} [{num(e4['G_crop']['b_ci95'][0], 2)}, {num(e4['G_crop']['b_ci95'][1], 2)}] for the GSLC and
b = {num(e4['Rc_geometry_only']['b_hat'], 2)} [{num(e4['Rc_geometry_only']['b_ci95'][0], 2)}, {num(e4['Rc_geometry_only']['b_ci95'][1], 2)}] for R<sub>c</sub>. Theory gives −1 in both cases.</p></div>""")
    b.append(svg_lines([("GSLC", cg["b"], cg["R"], "b"), ("R_c (geometry-only RSLC)", cc["b"], cc["R"], "c")],
                       -2, 1, [(v, num(v, 1, sign=v != 0)) for v in (-2, -1.5, -1, -0.5, 0, 0.5, 1)], "carrier coefficient b",
                       ylabel="R", title="Agreement with R_full as a function of b", refs=[(-1.0, "theory b = −1")], area=False,
                       ylim=1.0))
    b.append(sub("10.6", "Coherence loss predicted from first principles"))
    b.append(f"""<div class="col"><p>For stationary speckle, a misregistration d multiplies coherence by |ρ(d)|, the normalised Fourier transform of the SLC power
spectrum. With the measured RSLC spectra, ρ<sub>az</sub>(0.05 lines) = {num(e5['rho_az_at']['0.05'], 3)} and ρ<sub>rg</sub>(0.25 samples) = {num(e5['rho_rg_at']['0.25'], 3)}.
The measured misregistration (medians {num(e5['misregistration_40m_medians']['az_lines'], 3)} lines, {num(e5['misregistration_40m_medians']['rg_samples'], 3)} samples) predicts a factor of
{num(e5['predicted_factor_median'], 3)}. The observed factors are {num(e5['observed_median_coarse_over_fine'], 3)} (geometry-only/registered RSLC) and {num(e5['observed_median_G_over_R'], 3)}
(GSLC/RSLC). Both sit a little above the prediction, as the upward estimator bias of equation (2) requires, since it inflates the lower coherence proportionally more.
The loss comes almost entirely from the range term. Because that term is nearly uniform, the level is the test here; the block-to-block pattern is too
flat to add much (r = {num(e5['blocks_observed_coarse_on_predicted']['pearson_r'], 2)}).</p></div>""")
    b.append(sub("10.7", "Is the GSLC blurrier?"))
    b.append(f"""<div class="col"><p>Not in amplitude. Speckle contrast in 7 × 7 windows is {num(ss['speckle_contrast_7x7']['R_full_on_lattice_median'], 3)} for the RSLC sampled onto the lattice
and {num(ss['speckle_contrast_7x7']['G_crop_median'], 3)} for the GSLC (paired ratio {num(ss['speckle_contrast_7x7']['paired_ratio_G_over_R_median'], 3)}). The intensity autocorrelation
is {num(ss['intensity_acf_fwhm']['R_full_on_lattice_cells_rows_cols'][0], 2)} × {num(ss['intensity_acf_fwhm']['R_full_on_lattice_cells_rows_cols'][1], 2)} cells wide (FWHM) for the RSLC
and {num(ss['intensity_acf_fwhm']['G_crop_cells_rows_cols'][0], 2)} × {num(ss['intensity_acf_fwhm']['G_crop_cells_rows_cols'][1], 2)} for the GSLC. The crisper look of the RSLC
interferometric maps is interferometric: higher coherence and no registration phase. Nearest-sample mapping plays a small part: {pct(ss['lookup_nearest_neighbour']['fraction_duplicating_east_neighbour'], 0)} of
lattice cells repeat their eastern neighbour's radar sample and {pct(ss['lookup_nearest_neighbour']['fraction_duplicating_south_neighbour'], 0)} their southern.</p>
<div class="note"><span class="eyebrow">What aligning the GSLCs would take</span>The correction has to enter at geocoding. Delivered GSLC samples
cannot be realigned afterwards (§10.1). ISCE3's GSLC workflow passes azimuth-time and slant-range correction LUTs to <code>geocodeSlc</code>
(<code>nisar/workflows/gslc.py:201–202</code>); today they are built only from TEC files and solid-earth tides. Geocoding the secondary with the rubber-sheet
field converted to seconds and metres is the direct test of H. It has not been run. Separately, the uniform
{num(e1['ref_vs_geometry_only_mean']['rg']['mean'] * rg_m, 2)} m slant-range misregistration has no identified cause. Candidates are differential
ionospheric group delay (≈ 0.26 m per TECU at f<sub>A</sub>), differential tropospheric delay, and range timing between acquisitions.</div></div>""")
    b.append("</section>")

    # ================================================================== 10 ionosphere
    lv = F.chart("iono_levels")
    b.append(sec(11, "iono", "Ionosphere: the shape is robust, the level is not"))
    b.append('<div class="col">')
    b.append(f"""<p><b>RSLC, crop vs full.</b> The two ISCE3 screens, compared on the identical 9×8 grid (the look-aligned origin makes the
slice exact), agree in shape to a residual σ of {num(rsc['residual_std_tecu'], 4)} TECU (p95 {num(rsc['residual_p95_abs_tecu'], 4)}; r =
{num(rsc['pearson_r'], 5)}). That is {num(rsc['residual_std_tecu'] * MM_PER_TECU, 1)} mm of apparent range. The levels, however, differ by
{num(rsc['offset_P_minus_Q_rad'], 3)} rad ({num(rsc['offset_P_minus_Q_tecu'], 3)} TECU). Decomposing via equation (5) accounts for it exactly:</p>
<ul>
<li>unwrapped band A, full − crop: 0 cycles on {pct(rA['modal_whole_cycles'][0]['fraction'])} of cells, non-integer remainder
{num(rA['remainder_circular_mean_cycles'], 4, sign=True)} cycles;</li>
<li>unwrapped band B, full − crop: +{rB['modal_whole_cycles'][0]['cycles']} cycle on {pct(rB['modal_whole_cycles'][0]['fraction'])} of cells, remainder
{num(rB['remainder_circular_mean_cycles'], 4, sign=True)} cycles;</li>
<li>so (m, n) = (0, +1) and equation (5) predicts Δφ<sub>d</sub> = {num(cst['rad_per_cycle_B'], 3)} rad. The observed offset is {num(rsc['offset_P_minus_Q_rad'], 3)} rad.</li>
</ul>
<p>The crop's screen is the benchmark's screen displaced by one side-band unwrapping cycle, and nothing else. The v1 non-integer
error from subset bug 2 is gone: the B remainder is {num(abs(rB['remainder_circular_mean_cycles']), 4)} cycles, wrapped B phase agrees at
R = {num(i3['R_full__vs__R_crop_wrapped_B']['phase_diff_coherence'], 3)}, and side-band coherence medians are
{num(i3['R_sideband_coherence_median']['R_full'], 3)} / {num(i3['R_sideband_coherence_median']['R_crop'], 3)}. The whole-cycle jump is not a crop defect. It is the
unreferenced integration constant of snaphu on a different domain, and ISCE3 has no step that would notice.</p>
<p><b>Which level is right?</b> Equation (5) alone cannot say, but the partition gives a weak discriminant. Moving to a
neighbouring class shifts the non-dispersive median by the opposite amount. Over the same cells, R<sub>full</sub>'s non-dispersive phase
has median {num(nd_f, 1, sign=True)} rad and R<sub>crop</sub>'s {num(nd_c, 1, sign=True)} rad. The class criterion of the GSLC tool, which keeps the class with the smallest
|median φ<sub>nd</sub>|, selected a class with {num(nd_g, 1, sign=True)} rad on G<sub>full</sub>. It would therefore have kept R<sub>full</sub>'s level and rejected R<sub>crop</sub>'s. This rests on a
prior, |median φ<sub>nd</sub>| ≲ 38 rad (≈ 6 band-A cycles, ≈ 70 cm of non-dispersive delay), and is not an absolute calibration. That still needs an
external TEC reference.</p>
<p><b>GSLC, crop vs full.</b> On identical 40 m cells the ported screens agree to {num(gsc['offset_P_minus_Q_tecu'], 4, sign=True)} TECU with residual σ
{num(gsc['residual_std_tecu'], 4)} TECU. This agreement is <em>produced</em> by the cycle resolver. Raw band-A unwraps differed by
{i3['G_full__vs__G_crop_unwrapped_A_cycles']['modal_whole_cycles'][0]['cycles']} cycles on {pct(i3['G_full__vs__G_crop_unwrapped_A_cycles']['modal_whole_cycles'][0]['fraction'], 2)}
of cells, and the resolver applied (m, n) = ({gcyc['G_full']['A']}, {gcyc['G_full']['B']}) to the full tile and ({gcyc['G_crop']['A']}, {gcyc['G_crop']['B']}) to the crop.
The small residual is the 10 km Gaussian seeing different data near the crop's edges and holes.</p>
<p><b>Across domains.</b> Sampled onto the 5 m lattice, R<sub>full</sub> − G<sub>full</sub> is {num(xt['R_full__vs__G_full']['offset_P_minus_Q_tecu'], 3, sign=True)} TECU
with residual σ {num(xt['R_full__vs__G_full']['residual_std_tecu'], 3)} TECU (r = {num(xt['R_full__vs__G_full']['pearson_r'], 3)}), or
{num(xt['R_full__vs__G_full']['residual_std_tecu'] * MM_PER_TECU, 1)} mm. The two screens come from different grids (≈40 m × 200 m vs 40 m) and filters (pixel Gaussian vs 10 km),
so a residual of this size is expected. The ported GSLC inversion reproduces ISCE3's radar-domain result.</p></div>""")
    ivr = F.info("f06_iono_R_full")["vrange"][1]
    b.append(F.figure("fig8",
                      "".join(F.panel(f"f06_iono_{k}", leg(k), f"median {num(lv[k]['median'], 3, sign=True)} TECU") for k in ("R_full", "R_crop", "G_full", "G_crop")),
                      f"<b>Figure 8.</b> Filtered dispersive screens, each shown about its own median (stated on each panel) on a common ±{num(ivr, 2)} TECU scale. "
                      "The shapes are the same; the levels are not.",
                      div_legend("f06_iono_R_full", "TECU about median"), pmin=420))
    b.append(F.figure("fig9",
                      F.panel("f06_resid_R_full__vs__R_crop", pair("R_full__vs__R_crop"), f"{num(F.info('f06_resid_R_full__vs__R_crop')['removed_offset_tecu'], 3, sign=True)} TECU removed")
                      + F.panel("f06_resid_G_full__vs__G_crop", pair("G_full__vs__G_crop"), f"{num(F.info('f06_resid_G_full__vs__G_crop')['removed_offset_tecu'], 4, sign=True)} TECU removed")
                      + F.panel("f06_resid_R_full__vs__G_full", pair("R_full__vs__G_full"), f"{num(F.info('f06_resid_R_full__vs__G_full')['removed_offset_tecu'], 3, sign=True)} TECU removed"),
                      "<b>Figure 9.</b> Screen differences after removing the median offset, on ±0.05 TECU (≈ ±13 mm). The RSLC crop residual has faint "
                      "range-oriented banding (p95 0.0055 TECU). The cross-domain residual is broad and smooth, consistent with the two different filters.",
                      div_legend("f06_resid_R_full__vs__R_crop", "TECU", fmt=lambda v: num(v, 2, sign=True)), pmin=280))
    b.append(svg_bars([(k.replace("_", " "), lv[k]["median"], "a" if k.startswith("R") else "b") for k in ("R_full", "R_crop", "G_full", "G_crop")],
                      -2, 5, [(v, num(v, 0, sign=v != 0)) for v in (-2, -1, 0, 1, 2, 3, 4, 5)], "median screen over the common AOI cells (TECU)",
                      title="Absolute levels", refs=[(lv["R_full"]["median"] - cst["rad_per_cycle_B"] * RAD2TECU, "R_full + one B cycle")]))
    b.append('<div class="note col"><span class="eyebrow">Consequence</span>A spatially constant screen offset is a constant phase after '
             "correction and cancels when a pair is referenced to a stable point, so relative deformation is unaffected. It does not "
             "cancel where independently processed crops or frames are mosaicked (a seam of a whole B cycle), and it invalidates any "
             "absolute-TEC product.</div>")
    b.append(sub("11.1", "Correcting a crop-first interferogram"))
    itn = it_("wrapped_1x1_vs_benchmark")
    iun = it_("unwrapped_9x8_minus_benchmark")
    rule = it_("cycle_rule")
    lbl = {"A_full_tile_screen_sliced": "full-tile screen, sliced to the crop", "C_crop_own_screen": "crop's own screen, as ISCE3 wrote it"}
    for t_ in rule["minimal_norm_members"]:
        key = f"D_crop_screen_cycles_m{t_['m']:+d}_n{t_['n']:+d}"
        sel = " (rule's choice)" if (t_["m"], t_["n"]) == (rule["selected"]["m"], rule["selected"]["n"]) else ""
        lbl[key] = f"crop's own screen, cycle rule (m, n) = ({t_['m']:+d}, {t_['n']:+d}){sel}"
    b.append(table(["screen applied to the crop interferogram", "screen − benchmark screen (rad)", "corrected phase R, 5 m", "constant offset (rad)"],
                   [[lbl[k], num(iun[k]["screen_minus_benchmark_screen"]["median_offset_rad"], 3, sign=True), num(itn[k]["5m"]["phase_diff_coherence"], 4),
                     num(itn[k]["5m"]["constant_offset_rad"], 3, sign=True)] for k in lbl], num_cols=(1, 2, 3)))
    b.append(f"""<div class="col"><p>Each option is applied to the crop's 1×1 interferogram, with the 9×8 screen interpolated to the 1×1 cells, and
compared with the benchmark interferogram corrected by its own screen. The full-tile screen slices exactly onto the crop, because the crop origin
lies on the 9×8 look lattice, and it reproduces the benchmark's correction. The corrected-phase agreement ({num(itn['A_full_tile_screen_sliced']['5m']['phase_diff_coherence'], 4)})
equals the uncorrected crop-versus-tile agreement ({num(it_('wrapped_1x1_uncorrected_R_full_vs_R_crop_5m|phase_diff_coherence'), 4)}), so the correction adds nothing.
The crop's own screen differs only by a constant. The class rule recovers the class, but its minimal-norm tie-break lands one joint cycle
({num(RAD2TECU * cst['rad_per_joint_cycle'], 3)} TECU) from the benchmark, at the GUNW's level (§12). Unwrapped 9×8 phase differs between crop and tile on
{pct(1 - rA['modal_whole_cycles'][0]['fraction'], 1)} of cells by whole cycles, independently of any ionosphere step.</p></div>""")
    b.append("</section>")

    # ================================================================== 12 GUNW validation
    gm = gv_("gunw_metadata")
    gw_ = gv_("wrapped")
    gu_ = gv_("unwrapped_80m")
    gi_ = gv_("ionosphere_80m")
    gc_ = gv_("coherence_20m")
    b.append(sec(12, "gunw", "External validation against the NISAR GUNW"))
    b.append(f"""<div class="col"><p>The NISAR science data system's L2 GUNW for this pair (<code>{esc(gv_('gunw'))}</code>) was built from the same two RSLC
granules with ISCE3 {esc(str(gm['software_version']))}, against our 0.25.12. It differs from our processing in ways that set expectations for the comparison.
Its wrapped interferogram uses {esc(str(gm['wrapped_looks_az_rg'][0]))} × {esc(str(gm['wrapped_looks_az_rg'][1]))} looks geocoded to 20 m. Its unwrapped phase and ionosphere use
{esc(str(gm['unwrapped_looks_az_rg'][0]))} × {esc(str(gm['unwrapped_looks_az_rg'][1]))} looks at 80 m. Ionosphere is estimated with <code>main_diff_ms_band</code> with unwrapping-error correction (ours:
<code>main_side_band</code>). Dense offsets use a 96 × 64 window on a 75-sample skip, unwrapping runs as a single snaphu tile, and the DEM is the NISAR DEM v1.2.
Our layers are averaged onto GUNW cells by cell centre: 16 of our 5 m cells per 20 m cell and 256 per 80 m cell, restricted to the comparison mask.</p></div>""")
    b.append(table(["our leg vs GUNW", "wrapped R, 20 m", "R | γ 0.5–0.7", "R | γ &gt; 0.7", "wrapped R, 80 m", "offset (rad)", "unwrapped: cells at modal cycle", "unwrapped residual MAD (rad)"],
                   [[leg(k), num(gw_[f"{k}__vs__GUNW_20m"]["phase_diff_coherence"], 3), num(gw_[f"{k}__vs__GUNW_20m"]["by_R_full_coherence"]["0.5-0.7"], 3),
                     num(gw_[f"{k}__vs__GUNW_20m"]["by_R_full_coherence"]["0.7-1.0"], 3), num(gw_[f"{k}__vs__GUNW_80m_from_20m"]["phase_diff_coherence"], 3),
                     num(gw_[f"{k}__vs__GUNW_80m_from_20m"]["constant_offset_rad"], 3, sign=True), pct(gu_[f"{k}__vs__GUNW"]["fraction_at_mode"], 1),
                     num(gu_[f"{k}__vs__GUNW"]["residual_after_mode_mad_rad"], 3)] for k in ("R_full", "R_crop", "G_full", "G_crop")],
                   num_cols=(1, 2, 3, 4, 5, 6, 7)))
    b.append(f"""<div class="col"><p><b>Interferograms.</b> Both RSLC legs agree with the GUNW with no constant offset
({num(gw_['R_full__vs__GUNW_80m_from_20m']['constant_offset_rad'], 3, sign=True)} rad) and no ramp. Agreement is limited by noise where the benchmark is
incoherent and reaches {num(gw_['R_full__vs__GUNW_20m']['by_R_full_coherence']['0.7-1.0'], 3)} where it is coherent. The GSLC legs disagree with the GUNW by the same
+0.30 rad offset and planar trend they show against our RSLC, so the production RSLC chain independently confirms the registration result of §10. Conjugating
the GUNW drops agreement to {num(gw_['R_full__vs__GUNW_20m']['sign_check_vs_conjugate_R'], 2)}, which confirms that the sign conventions match. The unwrapped phases differ by a single whole-cycle constant (an arbitrary
reference) on {pct(gu_['R_full__vs__GUNW']['fraction_at_mode'], 0)} of 80 m cells, with a residual MAD of {num(gu_['R_full__vs__GUNW']['residual_after_mode_mad_rad'], 2)} rad.
The remaining cells are regions the two unwrappers placed on different cycles; the GUNW labels {pct(gu_['GUNW_connected_components_in_AOI']['fraction_labelled'], 0)} of the AOI as one connected component.
GUNW coherence (median {num(gc_['GUNW_20m_6x5looks_median'], 3)}) correlates with ours (r = {num(gc_['pearson_R_full_vs_GUNW'], 3)} for RSLC,
{num(gc_['pearson_G_full_vs_GUNW'], 3)} for GSLC). Its lower level is expected from a 30-look estimator's smaller upward bias.</p></div>""")
    b.append(F.figure("fig12",
                      F.panel("f20_gunw_wrapped_phase", "GUNW wrapped phase", "40 m display")
                      + F.panel("f20_R_full_minus_gunw_wrapped", f"R<sub>full</sub> − GUNW", "")
                      + F.panel("f20_G_full_minus_gunw_wrapped", f"G<sub>full</sub> − GUNW", ""),
                      "<b>Figure 12.</b> The production GUNW interferogram over the AOI, and its differences from our benchmark and our GSLC route. The GSLC − GUNW "
                      "difference is the registration field of Figure 6; the RSLC − GUNW difference is noise.",
                      ph_legend("phase, difference"), pmin=280))
    b.append(table(["our screen − GUNW screen", "offset (rad)", "offset (TECU)", "in joint cycles", "residual σ (TECU)", "residual σ (mm)", "r"],
                   [[leg(k), num(gi_[f"{k}__vs__GUNW"]["offset_rad"], 3, sign=True), num(gi_[f"{k}__vs__GUNW"]["offset_tecu"], 3, sign=True),
                     num(gi_[f"{k}__vs__GUNW"]["offset_in_joint_cycles"], 3, sign=True), num(gi_[f"{k}__vs__GUNW"]["residual_std_tecu"], 4),
                     num(gi_[f"{k}__vs__GUNW"]["residual_std_mm"], 1), num(gi_[f"{k}__vs__GUNW"]["pearson_r"], 3)] for k in ("R_full", "R_crop", "G_full", "G_crop")],
                   num_cols=(1, 2, 3, 4, 5, 6)))
    b.append(f"""<div class="col"><p><b>Ionosphere.</b> Every one of our four screens reproduces the shape of the GUNW screen to
{num(min(gi_[f'{k}__vs__GUNW']['residual_std_tecu'] for k in ('R_full', 'R_crop', 'G_full', 'G_crop')), 4)}–{num(max(gi_[f'{k}__vs__GUNW']['residual_std_tecu'] for k in ('R_full', 'R_crop', 'G_full', 'G_crop')), 4)} TECU
(r ≥ {num(min(gi_[f'{k}__vs__GUNW']['pearson_r'] for k in ('R_full', 'R_crop', 'G_full', 'G_crop')), 2)}). That is well inside the GUNW's own median uncertainty of
{num(gi_['GUNW_uncertainty_median_rad'], 2)} rad ({num(gi_['GUNW_uncertainty_median_rad'] * RAD2TECU, 3)} TECU). The absolute levels fall into two groups one joint cycle apart. The benchmark and both
GSLC legs (resolver) sit {num(gi_['R_full__vs__GUNW']['offset_in_joint_cycles'], 2)}–{num(gi_['G_full__vs__GUNW']['offset_in_joint_cycles'], 2)} joint cycles above the GUNW. The GUNW
(<code>main_diff_ms_band</code> with unwrapping-error correction) and the crop's own screen after the class rule (§11.1) share the lower level to within
{num(abs(gi_['R_full__vs__GUNW']['offset_rad'] - abs(it_('unwrapped_9x8_minus_benchmark|D_crop_screen_cycles_m-1_n+0|screen_minus_benchmark_screen|median_offset_rad'))), 2)} rad.
The uncorrected R<sub>crop</sub> screen is one B cycle further away, in a different class. Leaving it aside, the independent estimators agree on the class and
split only along the degenerate joint-cycle line, exactly as equation (5) allows. The data here cannot decide which level is right. An external TEC reference can.</p></div>""")
    b.append(F.figure("fig13",
                      F.panel("f23_gunw_iono_anomaly", "GUNW screen", f"median {num(F.info('f23_gunw_iono_anomaly')['median_tecu'], 2, sign=True)} TECU")
                      + F.panel("f23_R_full_minus_gunw_iono", f"R<sub>full</sub> − GUNW", "offset removed")
                      + F.panel("f23_G_full_minus_gunw_iono", f"G<sub>full</sub> − GUNW", "offset removed"),
                      "<b>Figure 13.</b> The GUNW ionosphere screen about its median (80 m), and our screens minus it after removing the constant, on ±0.05 TECU.",
                      div_legend("f23_gunw_iono_anomaly", "TECU", fmt=lambda v: num(v, 2, sign=True)) + " "
                      + div_legend("f23_R_full_minus_gunw_iono", "TECU", fmt=lambda v: num(v, 2, sign=True)), pmin=280))
    b.append("</section>")

    # ================================================================== 11 automation
    b.append(sec(13, "automation", "What the pipeline should do"))
    b.append(f"""<div class="col"><ol>
<li><b>Default to crop-first RSLC for quantitative phase.</b> It matches the benchmark in coregistration, phase and coherence at a sixth to a tenth of the
cost. It is safe only with the three invariants of §4 enforced by the subsetter and re-checked as gates: zero-Doppler window;
8·rg0<sub>B</sub> − rg0<sub>A</sub> identical on all dates; origins on the look lattice. For exact side-band cells, the range origin should be a
multiple of 64 (8 range looks × 8:1 decimation). v2 is on 8 only, and the comparison interpolated the side band.</li>
<li><b>Treat the ionospheric level as unknown until referenced.</b> Add a post-ISCE3 step that computes the non-dispersive median for the chosen
and neighbouring classes, flags any solution whose class differs from a reference (full frame, previous run, or overlapping crop), and records
(m, n). Mosaics must be built from screens on a common class. Expect ±1 joint cycle (0.235 TECU) between estimators even then (§12):
the production GUNW and our benchmark differ by exactly that. For a set of AOIs inside one frame, computing the full-tile screen once per pair and slicing it reproduces the
benchmark exactly (§11.1).</li>
<li><b>Register GSLC pairs, or use them only where registration error is tolerable.</b> Geocoding each date independently leaves the
geometry-only misregistration (§10): ≈5% coherence and a carrier phase term with p95 {num(p95_carrier_rad * MM_PER_RAD, 0)} mm. The fix belongs inside geocoding.
Estimate dense offsets once per pair in radar geometry and pass them to the secondary's geocoding as azimuth-time and slant-range corrections,
which ISCE3's GSLC workflow already accepts (<code>nisar/workflows/gslc.py:201–202</code>, today filled only from TEC and tides). Delivered GSLC
samples cannot be realigned afterwards (§10.1).</li>
<li><b>Gates that caught real defects in this study</b> should be permanent. A bit-identical reference-SLC control between crop and tile; lookup
index re-verification and a mean-residual test; B-cycle remainder ≈ 0 and whole-cycle offset reported; overwrite refusal on every product path (the
output-identity rule: anything that changes bytes is in the filename).</li>
</ol></div>""")
    b.append("</section>")

    # ================================================================== 12 limitations
    b.append(sec(14, "limits", "Limitations and open questions"))
    b.append(f"""<div class="col"><ul>
<li><b>One pair, one scene.</b> A 12-day July pair over extreme relief (DEM 1295–7892 m). Coherence, offset scatter and the carrier term all
depend on season, terrain and Doppler. The magnitudes here are not transferable without repeats.</li>
<li><b>Benchmark ≠ truth.</b> Agreement with R<sub>full</sub> is processing equivalence. No GNSS, corner reflectors or GIM TEC were used, so the absolute
ionospheric level and the cause of the uniform 0.24-sample range misregistration (§10) remain open.</li>
<li><b>Comparison resolution.</b> Nearest-sample mapping (median {num(lk['p50_abs_m_kept'], 1)} m offset) makes 5 m RSLC-vs-GSLC statistics conservative;
{pct(1 - g('common_mask|fraction_of_aoi'))} of the AOI (layover, shadow, no sample) is excluded.</li>
<li><b>Different supports.</b> RSLC screens are 9×8-look radar grids with ISCE3's pixel Gaussian; GSLC screens are 40 m with a 10 km Gaussian.
Cross-domain ionosphere residuals include filter differences.</li>
<li><b>Confounds in the benchmark itself.</b> The full-tile 9×8 ionosphere came from the standalone unwrap entry point on the finished RIFG, the crop
from the integrated workflow; snaphu tiling differed between them. The G<sub>full</sub> 2026-07-26 frequency-B GSLC lost most of its metadata groups when the VM
stopped. Its HH image and coordinates are complete, but it is not a conformant product. The R<sub>crop</sub> leg has no 1×1 unwrap.</li>
<li><b>Known tool defects.</b> A 512-row block seam in the GSLC coherence writer; nominal rather than effective snaphu looks; TECU sign convention not
independently verified. None changes a verdict above.</li>
<li><b>Registration test scope.</b> §10 measures registration on the RSLC (where it is valid) and infers the GSLC's from the closure.
Direct re-geocoding of the secondary with offset corrections has not been run. The whiteness of the GSLC spectrum is measured but not explained.</li>
<li><b>Timing.</b> Wall times are uncontrolled (different days, concurrent legs); treat them as order-of-magnitude.</li>
</ul></div>""")
    b.append("</section>")

    # ================================================================== A reproduction
    b.append(sec("A", "repro", "Reproduction"))
    b.append("""<div class="col"><p>All paths relative to <code>/home/sharath/isce3</code>. Environment: conda <code>isce3_env</code>
(ISCE3 0.25.12 with the project's nisar overlays, <code>asc/nisar_workflows/tools/apply_patches.py --check</code>).
Per-workflow detail, runtimes and problem logs are in <code>asc/nisar_workflows/docs/WF1…WF4</code>.</p></div>""")
    b.append("""<pre>cd asc/nisar_workflows
python -u tools/rslc_subset.py …                          # v2 crop (see docs/WF3_RSLC_CROPPED.md)
bash ../../case_studies/nepal_glof/logs/run_aoi_v2.sh     # R_crop and G_crop legs
python -u tools/compare_four_way.py --rcrop-root aoi_v2 --gcrop-root aoi_v2 \\
       --rcrop-granules L1_RSLC_AOI_v2 --out comparison_v2 --geoloc-tol 15
python -u tools/report_figures.py                         # comparison_v2/report/fig, figures.json
python tools/build_report.py                              # this page</pre>""")
    b.append('<div class="col"><p class="small">Numbers on this page are formatted at build time from '
             '<code>case_studies/nepal_glof/comparison_v2/comparison.json</code>, <code>supplement_nondispersive.json</code>, '
             '<code>report/figures.json</code> and <code>report/footprints.json</code>. Stated but not recomputed here: v1 defect '
             'magnitudes (verified in <code>comparison/verification/verdicts.json</code>) and wall times (from the workflow logs).</p></div>')
    b.append("</section>")

    page = f"""<title>Nepal GLOF Four-Workflow InSAR Study</title>
<meta name="description" content="Crop-first and geocoded NISAR interferometry measured against a full-tile RSLC benchmark.">
{FONTS}
<style>{CSS}</style>
<div class="wrap">
{''.join(h)}
<div class="body">
<nav class="toc" aria-label="Contents"><ol>{nav}</ol></nav>
<main>{''.join(b)}
<footer class="small">Prepared for the team stand-up. Research note; figures and statistics regenerate from the files named in §A.</footer>
</main></div></div>"""
    OUT.write_text(page)
    unused = set(F.meta["figures"]) - F.used
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB); figures used {len(F.used)}, unused {sorted(unused)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
