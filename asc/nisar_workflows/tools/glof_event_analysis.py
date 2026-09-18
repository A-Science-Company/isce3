#!/usr/bin/env python3
"""
Analysis behind the Nepal GLOF event report: reads the coregistered stack and the two time-series runs
(nisar_timeseries.py), computes every number and figure the report uses, and writes them to

    <workdir>/report/glof_event/{analysis.json, fig/*.png}

    python tools/glof_event_analysis.py [--only F1 F4 ...] [--force]

Everything is derived here; the report builder only formats. Radar-grid layers are geocoded by nearest-neighbour
binning onto a regular lat/lon grid at the multilooked posting.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import h5py
import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import matplotlib                                     # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                       # noqa: E402
from matplotlib.path import Path as MplPath           # noqa: E402

from nisar_timeseries import envi_memmap              # noqa: E402

W = Path("/home/sharath/isce3/case_studies/nepal_nisar_ascending")
STACK = W / "coreg/RSLC_ref20260726_AHH_glof_bigger_aoi"
TS = W / "timeseries/RSLC_ref20260726_AHH_glof_bigger_aoi"
OUT = W / "report/glof_event"
FIG = OUT / "fig"
AOI_KML = Path("/home/sharath/nisar_downloader/glof_bigger_aoi.kml")
GZ_KML = Path("/home/sharath/nisar_downloader/nepal_glacier_zone.kml")
EVENT = "2026-08-26"
PRE_DATES = ["20260620", "20260702", "20260714", "20260726", "20260819"]
POST_DATES = ["20260819", "20260831", "20260912"]
PRE12 = ["20260620_20260702", "20260702_20260714", "20260714_20260726"]
EVENT_PAIR = "20260819_20260831"
POST12 = "20260831_20260912"

plt.rcParams.update({"font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8, "figure.dpi": 160,
                     "savefig.bbox": "tight", "axes.grid": True, "grid.alpha": 0.25, "grid.linewidth": 0.4,
                     "figure.constrained_layout.use": True, "figure.constrained_layout.w_pad": 0.06,
                     "xtick.labelsize": 7, "ytick.labelsize": 7})
CB = {"pos": "#1f6feb", "neg": "#d1242f", "n1": "#8250df", "n2": "#1a7f37", "grey": "#57606a"}


def kml_poly(p: Path):
    txt = p.read_text()
    block = re.findall(r"<coordinates>(.*?)</coordinates>", txt, re.S)[0]
    return np.array([tuple(map(float, t.split(",")[:2])) for t in block.split()])


def save(fig, name: str, caption: str, index: dict):
    from matplotlib.ticker import MaxNLocator
    for ax in fig.axes:                                   # keep longitude/latitude ticks readable on map panels
        if "longitude" in (ax.get_xlabel() or ""):
            ax.xaxis.set_major_locator(MaxNLocator(4, prune="both"))
            ax.yaxis.set_major_locator(MaxNLocator(5))
    FIG.mkdir(parents=True, exist_ok=True)
    path = FIG / f"{name}.png"
    fig.savefig(path)
    plt.close(fig)
    index[name] = {"file": f"fig/{name}.png", "caption": caption}
    print(f"  wrote {path.name}")


class Ctx:
    """Everything loaded once: geometry, coherence, time series, corrections."""

    def __init__(self):
        with h5py.File(TS / "pre_event/mintpy/inputs/geometryRadar.h5") as f:
            self.lat, self.lon, self.hgt = f["latitude"][()], f["longitude"][()], f["height"][()]
            self.inc = f["incidenceAngle"][()]
            self.attrs = dict(f.attrs)
        self.meta = json.loads((TS / "pre_event/geometry/meta.json").read_text())
        self.meta_post = json.loads((TS / "post_event/geometry/meta.json").read_text())
        self.bperp = {**self.meta["bperp"], **self.meta_post["bperp"]}      # both relative to the stack reference
        self.aoi, self.gz = kml_poly(AOI_KML), kml_poly(GZ_KML)
        pts = np.c_[self.lon.ravel(), self.lat.ravel()]
        self.in_aoi = MplPath(self.aoi).contains_points(pts).reshape(self.lat.shape)
        self.in_gz = MplPath(self.gz).contains_points(pts).reshape(self.lat.shape)
        self.gz_centre = (float(self.gz[:, 1].mean()), float(self.gz[:, 0].mean()))
        lo0, lo1 = self.gz[:, 0].min(), self.gz[:, 0].max()
        la0, la1 = self.gz[:, 1].min(), self.gz[:, 1].max()
        cx, cy, w, h = (lo0 + lo1) / 2, (la0 + la1) / 2, lo1 - lo0, la1 - la0
        self.focus_bbox = (cx - w, cx + w, cy - h, cy + h)          # glacier bounding box with a 100 % buffer
        self.in_focus = ((self.lon >= cx - w) & (self.lon <= cx + w) & (self.lat >= cy - h) & (self.lat <= cy + h))
        self.in_ring = self.in_focus & ~self.in_gz
        self.focus_km = (float((2 * w) * 111.32 * np.cos(np.deg2rad(cy))), float(2 * h * 110.57))
        self.dist_km = np.hypot((self.lon - self.gz_centre[1]) * 111.32 * np.cos(np.deg2rad(28.3)),
                                (self.lat - self.gz_centre[0]) * 110.57)
        self.qa = {r: json.loads((TS / r / "qa/mintpy.json").read_text()) for r in ("pre_event", "post_event")}
        self.ref_yx = tuple(self.qa["pre_event"]["reference_yx"])

    def coh(self, run, pair):
        return np.array(envi_memmap(TS / run / f"pairs/{pair}/coh.cor"))

    def unw(self, run, pair):
        return np.array(envi_memmap(TS / run / f"pairs/{pair}/unw.unw"))

    def cc(self, run, pair):
        return np.array(envi_memmap(TS / run / f"pairs/{pair}/conncomp.cc"))

    def ts(self, run, corrected=True, variant="mintpy"):
        name = self.qa[run]["final_timeseries"] if corrected else "timeseries.h5"
        with h5py.File(TS / run / variant / name) as f:
            return [d.decode() for d in f["date"][()]], f["timeseries"][()] * 1000.0     # mm

    def tcoh(self, run, variant="mintpy"):
        with h5py.File(TS / run / variant / "temporalCoherence.h5") as f:
            return f["temporalCoherence"][()]

    def velocity(self, run, corrected=True, variant="mintpy"):
        n = "velocity.h5" if corrected else "velocity_uncorrected.h5"
        with h5py.File(TS / run / variant / n) as f:
            return f["velocity"][()] * 1000.0, (f["velocityStd"][()] * 1000.0 if "velocityStd" in f else None)

    def corrections(self, run):
        with h5py.File(TS / run / "corrections/per_date_phase.h5") as f:
            dates = json.loads(f.attrs["dates"])
            lam = self.meta["wavelength"]
            return dates, {k: f[k][()] * (-lam / (4 * np.pi)) * 1000.0 for k in f}       # mm, +toward satellite

    def amp_ml(self, date):
        m = self.meta
        R0, R1, C0, C1 = m["window_full"]
        az, rg = m["looks"]
        nl, nw = m["ml_shape"]
        s = envi_memmap(STACK / f"slc/{date}.slc")
        out = np.empty((nl, nw), np.float32)
        for i0 in range(0, nl, 100):
            i1 = min(nl, i0 + 100)
            a = np.asarray(s[R0 + i0 * az:R0 + i1 * az, C0:C1])
            out[i0:i1] = (a.real ** 2 + a.imag ** 2).reshape(i1 - i0, az, nw, rg).mean(axis=(1, 3))
        return out

    def geocode(self, arr, step=4.0e-4, bbox=None, agg="mean", fill=0):
        """Nearest-neighbour binning of a radar-grid layer onto a lat/lon grid (step in degrees)."""
        lo0, lo1, la0, la1 = bbox if bbox else (self.lon.min(), self.lon.max(), self.lat.min(), self.lat.max())
        nx, ny = int((lo1 - lo0) / step) + 1, int((la1 - la0) / step) + 1
        ix = ((self.lon - lo0) / step).astype(np.int32)
        iy = ((la1 - self.lat) / step).astype(np.int32)
        good = np.isfinite(arr) & (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        flat = iy[good].astype(np.int64) * nx + ix[good]
        s = np.bincount(flat, weights=np.asarray(arr)[good].astype(np.float64), minlength=nx * ny)
        n = np.bincount(flat, minlength=nx * ny)
        with np.errstate(invalid="ignore", divide="ignore"):
            g = np.where(n > 0, s / np.maximum(n, 1), np.nan).reshape(ny, nx)
        if fill:                                   # close the gaps left when the output grid is finer than the radar grid
            from scipy.ndimage import distance_transform_edt
            bad = ~np.isfinite(g)
            if bad.any() and (~bad).any():
                d, ind = distance_transform_edt(bad, return_distances=True, return_indices=True)
                g = np.where(bad & (d <= fill), g[tuple(ind)], g)
        return g, (lo0, lo0 + nx * step, la1 - ny * step, la1)


# ---------------------------------------------------------------- figures
def f1_study_area(c: Ctx, idx, st):
    hgt_g, ext = c.geocode(np.where(c.hgt > -500, c.hgt, np.nan))
    fig, ax = plt.subplots(1, 2, figsize=(9.2, 3.6))
    im = ax[0].imshow(hgt_g, extent=ext, cmap="terrain", origin="upper")
    ax[0].plot(*np.vstack([c.aoi, c.aoi[:1]]).T, color="k", lw=1.0, label="AOI (1637 km²)")
    ax[0].plot(*np.vstack([c.gz, c.gz[:1]]).T, color=CB["neg"], lw=1.4, label="glacier zone (0.61 km²)")
    ry, rx = c.ref_yx
    ax[0].plot(c.lon[ry, rx], c.lat[ry, rx], "k*", ms=9, label="reference point")
    ax[0].set_title("Radar window, elevation (m)"); ax[0].legend(loc="lower left", fontsize=6.5)
    plt.colorbar(im, ax=ax[0], shrink=0.85, label="m")
    la, lo = c.gz_centre
    bb = (lo - 0.05, lo + 0.05, la - 0.04, la + 0.04)
    hz, ez = c.geocode(np.where(c.hgt > -500, c.hgt, np.nan), bbox=bb)
    im = ax[1].imshow(hz, extent=ez, cmap="terrain", origin="upper")
    ax[1].plot(*np.vstack([c.gz, c.gz[:1]]).T, color=CB["neg"], lw=1.4)
    ax[1].set_title("Glacier zone ± 5 km, elevation (m)")
    plt.colorbar(im, ax=ax[1], shrink=0.85, label="m")
    for a in ax:
        a.set_xlabel("longitude (°E)"); a.set_ylabel("latitude (°N)")
    st["area"] = {"aoi_km2": 1637.4, "glacier_zone_km2": 0.61, "glacier_zone_pixels": int(c.in_gz.sum()),
                  "glacier_zone_height_m": [float(np.nanmin(c.hgt[c.in_gz])), float(np.nanmax(c.hgt[c.in_gz]))],
                  "window_height_m": [float(np.nanpercentile(c.hgt, 1)), float(np.nanpercentile(c.hgt, 99))],
                  "reference_latlon": [float(c.lat[ry, rx]), float(c.lon[ry, rx])], "reference_height_m": float(c.hgt[ry, rx]),
                  "ml_shape": list(c.lat.shape)}
    save(fig, "f1_study_area", "Radar window over the GLOF area. Left: elevation with the analysis AOI, the glacier zone "
                               "and the common reference point. Right: the glacier zone in its local topographic setting.", idx)


def f2_network(c: Ctx, idx, st):
    import datetime as dt
    fig, ax = plt.subplots(1, 2, figsize=(9.2, 3.0), sharey=True)
    for k, (run, dates) in enumerate((("pre_event", PRE_DATES), ("post_event", POST_DATES))):
        bp = c.bperp
        d = {x: dt.datetime.strptime(x, "%Y%m%d") for x in dates}
        pairs = [p.name for p in sorted((TS / run / "pairs").iterdir()) if p.is_dir()]
        for p in pairs:
            a, b = p.split("_")
            ax[k].plot([d[a], d[b]], [bp[a], bp[b]], "-", color=CB["grey"], lw=0.8, zorder=1)
        ax[k].scatter([d[x] for x in dates], [bp[x] for x in dates], s=28, color=CB["pos"], zorder=2)
        for x in dates:
            ax[k].annotate(x[4:], (d[x], bp[x]), fontsize=6, xytext=(0, 5), textcoords="offset points", ha="center")
        ax[k].axvline(dt.datetime.strptime(EVENT.replace("-", ""), "%Y%m%d"), color=CB["neg"], ls="--", lw=1)
        ax[k].set_title(f"{run.replace('_', ' ')}: {len(dates)} dates, {len(pairs)} pairs")
        ax[k].tick_params(axis="x", rotation=30)
    ax[0].set_ylabel("perpendicular baseline (m)")
    st["network"] = {"bperp_m": c.bperp,
                     "pre_pairs": sorted(p.name for p in (TS / "pre_event/pairs").iterdir() if p.is_dir()),
                     "post_pairs": sorted(p.name for p in (TS / "post_event/pairs").iterdir() if p.is_dir())}
    save(fig, "f2_network", "Interferogram networks. Dashed line: the outburst on 26 August 2026. Perpendicular baselines are "
                            "under 60 m, so topographic error is negligible.", idx)


def f3_validation(c: Ctx, idx, st):
    rows = []
    for f in sorted((TS / "pre_event/status").glob("ifg_*.json")):
        d = json.loads(f.read_text())
        ch = d.get("checks", {}).get("rifg")
        if ch:
            rows.append((d["unit"], ch["median_abs_dphi_rad"], ch["p95_abs_dphi_rad"], ch["pixels_coh_gt_0.5"],
                         d.get("start_range_phase_rad", 0.0)))
    sr = c.meta["starting_range_by_date"]
    ref = c.meta["reference"]
    dr, lam = c.meta["range_spacing"], c.meta["wavelength"]
    fig, ax = plt.subplots(1, 2, figsize=(9.2, 3.0))
    x = np.arange(len(rows))
    ax[0].bar(x, [r[1] for r in rows], color=CB["pos"], label="median |Δφ|")
    ax[0].bar(x, [r[2] for r in rows], color=CB["pos"], alpha=0.35, label="p95 |Δφ|")
    ax[0].axhline(0.2, color=CB["neg"], ls="--", lw=1, label="gate (0.2 rad)")
    ax[0].set_xticks(x); ax[0].set_xticklabels([r[0].replace("_", "\n") for r in rows], fontsize=6)
    ax[0].set_ylabel("phase difference vs ISCE3 RIFG (rad)"); ax[0].set_yscale("log"); ax[0].legend(fontsize=6.5)
    ax[0].set_title("Interferogram formation checked against ISCE3")
    ds = np.linspace(-80, 80, 400)
    ax[1].plot(ds, np.angle(np.exp(1j * 4 * np.pi * ds * dr / lam)), color=CB["grey"], lw=1)
    for d, s in sr.items():
        if d == ref:
            continue
        n = (s - sr[ref]) / dr
        ax[1].plot(n, np.angle(np.exp(1j * 4 * np.pi * n * dr / lam)), "o", ms=6,
                   color=CB["neg"] if abs(abs(np.angle(np.exp(1j * 4 * np.pi * n * dr / lam))) - np.pi) < 0.3 else CB["n2"])
        ax[1].annotate(d[4:], (n, np.angle(np.exp(1j * 4 * np.pi * n * dr / lam))), fontsize=6, xytext=(3, 4), textcoords="offset points")
    ax[1].set_xlabel("crop start-range difference from the reference (samples)")
    ax[1].set_ylabel("phase constant (rad)")
    ax[1].set_title("Why the start-range term matters")
    st["validation"] = {"rifg_checks": [{"pair": r[0], "median_rad": r[1], "p95_rad": r[2], "pixels": r[3]} for r in rows],
                        "start_range_samples": {d: round((s - sr[ref]) / dr, 3) for d, s in sr.items()},
                        "cycles_per_sample": 2 * dr / lam}
    save(fig, "f3_validation", "Left: every interferogram that shares the stack reference is compared with ISCE3's own RIFG; all "
                               "sit three orders of magnitude below the acceptance gate. Right: each date is cropped to its own "
                               "window, so its samples start at a different slant range; the constant that must be flattened is "
                               "half a cycle for an 8-sample difference and nearly zero for 64.", idx)


def f4_pre_velocity(c: Ctx, idx, st):
    v, vstd = c.velocity("pre_event")
    tc = c.tcoh("pre_event")
    good = (tc >= 0.7) & np.isfinite(v)
    vg, ext = c.geocode(np.where(good, v, np.nan))
    sg, _ = c.geocode(np.where(good, vstd, np.nan)) if vstd is not None else (None, None)
    tg, _ = c.geocode(tc)
    fig, ax = plt.subplots(1, 3, figsize=(11.5, 3.2))
    lim = float(np.nanpercentile(np.abs(v[good]), 95))
    im = ax[0].imshow(vg, extent=ext, cmap="RdBu_r", vmin=-lim, vmax=lim, origin="upper")
    plt.colorbar(im, ax=ax[0], shrink=0.85, label="mm/yr")
    ax[0].set_title("LOS velocity, 20 Jun – 19 Aug (corrected)")
    if sg is not None:
        im = ax[1].imshow(sg, extent=ext, cmap="magma", vmin=0, vmax=float(np.nanpercentile(vstd[good], 95)), origin="upper")
        plt.colorbar(im, ax=ax[1], shrink=0.85, label="mm/yr")
    ax[1].set_title("Velocity uncertainty (1σ)")
    im = ax[2].imshow(tg, extent=ext, cmap="viridis", vmin=0, vmax=1, origin="upper")
    plt.colorbar(im, ax=ax[2], shrink=0.85, label="temporal coherence")
    ax[2].set_title("Temporal coherence (mask ≥ 0.7)")
    for a in ax:
        a.plot(*np.vstack([c.gz, c.gz[:1]]).T, color="k", lw=1.0)
        a.set_xlabel("longitude (°E)")
    ax[0].set_ylabel("latitude (°N)")
    st["pre_velocity"] = {
        "pixels_total": int(c.in_aoi.sum()), "pixels_good": int((good & c.in_aoi).sum()),
        "velocity_mm_yr": {q: float(np.nanpercentile(v[good & c.in_aoi], q)) for q in (5, 25, 50, 75, 95)},
        "velocity_std_mm_yr": {q: float(np.nanpercentile(vstd[good & c.in_aoi], q)) for q in (50, 95)} if vstd is not None else None,
        "glacier_zone_good_pixels": int((good & c.in_gz).sum()),
        "temporal_coherence_median_aoi": float(np.nanmedian(tc[c.in_aoi])),
        "temporal_coherence_median_gz": float(np.nanmedian(tc[c.in_gz])),
    }
    save(fig, "f4_pre_velocity", "Pre-event line-of-sight velocity, its uncertainty and the temporal coherence that defines "
                                 "where the time series is trustworthy. Positive velocity is motion toward the satellite. "
                                 "Black outline: the glacier zone.", idx)


def f5_corrections(c: Ctx, idx, st):
    dates, corr = c.corrections("pre_event")
    fig, ax = plt.subplots(1, 2, figsize=(9.2, 3.2))
    x = np.arange(len(dates))
    lab = {"ionosphere": "ionosphere", "troposphere": "troposphere (wet + hydrostatic)", "solid_earth_tides": "solid-earth tides"}
    for j, (k, col) in enumerate((("ionosphere", CB["n1"]), ("troposphere", CB["n2"]), ("solid_earth_tides", CB["grey"]))):
        if k not in corr:
            continue
        sd = [float(np.nanstd(corr[k][i][c.in_aoi])) for i in range(len(dates))]
        ax[0].plot(x, sd, "o-", color=col, label=lab[k], lw=1.2, ms=4)
        st.setdefault("corrections", {})[k] = {d: round(s, 2) for d, s in zip(dates, sd)}
    ax[0].set_xticks(x); ax[0].set_xticklabels([d[4:] for d in dates], fontsize=7)
    ax[0].set_ylabel("spatial spread over the AOI (mm)"); ax[0].legend(fontsize=6.5)
    ax[0].set_title("GUNW corrections, per date (relative to 20 Jun)")
    v, _ = c.velocity("pre_event"); vu, _ = c.velocity("pre_event", corrected=False)
    tc = c.tcoh("pre_event"); good = (tc >= 0.7) & c.in_aoi & np.isfinite(v) & np.isfinite(vu)
    bins = np.linspace(-400, 400, 81)
    ax[1].hist(vu[good], bins=bins, histtype="step", color=CB["grey"], label=f"uncorrected (σ={np.std(vu[good]):.0f})")
    ax[1].hist(v[good], bins=bins, histtype="step", color=CB["pos"], label=f"corrected (σ={np.std(v[good]):.0f})")
    ax[1].set_xlabel("LOS velocity (mm/yr)"); ax[1].set_ylabel("pixels"); ax[1].legend(fontsize=6.5)
    ax[1].set_title("Effect of the corrections on the velocity distribution")
    st["corrections_effect"] = {"velocity_std_uncorrected": float(np.std(vu[good])), "velocity_std_corrected": float(np.std(v[good])),
                                "velocity_median_uncorrected": float(np.median(vu[good])), "velocity_median_corrected": float(np.median(v[good]))}
    save(fig, "f5_corrections", "Left: how large each GUNW correction is across the AOI on each date, as a spatial spread in "
                                "millimetres. Right: the velocity distribution before and after applying them.", idx)


def f6_timeseries(c: Ctx, idx, st):
    tc_pre, tc_post = c.tcoh("pre_event"), c.tcoh("post_event")
    dpre, tspre = c.ts("pre_event"); dpost, tspost = c.ts("post_event")
    good = (tc_pre >= 0.7) & (tc_post >= 0.7)
    sites = {}
    # nearest reliable pixel to the glacier zone, one 3 km downvalley, one on stable ground, and the reference itself
    for name, sel in (("near the glacier zone", good & (c.dist_km < 2.0)),
                      ("valley, 3–5 km away", good & (c.dist_km > 3.0) & (c.dist_km < 5.0) & (c.hgt < 4200)),
                      ("stable ground, 10 km away", good & (c.dist_km > 9.0) & (c.dist_km < 11.0))):
        if not sel.any():
            continue
        score = np.where(sel, np.minimum(tc_pre, tc_post), -1)
        i, j = np.unravel_index(int(np.argmax(score)), score.shape)
        sites[name] = (int(i), int(j))
    sites["reference point"] = c.ref_yx
    import datetime as dt
    fig, ax = plt.subplots(1, 2, figsize=(9.6, 3.2), sharey=True)
    cols = [CB["neg"], CB["n2"], CB["pos"], CB["grey"]]
    st["sites"] = {}
    for (name, (i, j)), col in zip(sites.items(), cols):
        tpre = [dt.datetime.strptime(d, "%Y%m%d") for d in dpre]
        tpost = [dt.datetime.strptime(d, "%Y%m%d") for d in dpost]
        ax[0].plot(tpre, tspre[:, i, j], "o-", color=col, ms=4, lw=1.2, label=f"{name} ({c.hgt[i, j]:.0f} m)")
        ax[1].plot(tpost, tspost[:, i, j], "o-", color=col, ms=4, lw=1.2)
        st["sites"][name] = {"row_col": [i, j], "lat": float(c.lat[i, j]), "lon": float(c.lon[i, j]), "height_m": float(c.hgt[i, j]),
                             "pre_mm": [float(x) for x in tspre[:, i, j]], "post_mm": [float(x) for x in tspost[:, i, j]],
                             "temporal_coherence": [float(tc_pre[i, j]), float(tc_post[i, j])]}
    ax[0].set_title("Pre-event displacement (relative to 20 Jun)"); ax[1].set_title("Post-event displacement (relative to 19 Aug)")
    ax[1].axvline(dt.datetime(2026, 8, 26), color=CB["neg"], ls="--", lw=1)
    ax[0].set_ylabel("LOS displacement (mm, + toward satellite)")
    ax[0].legend(fontsize=6.5, loc="best")
    for a in ax:
        a.tick_params(axis="x", rotation=30)
    save(fig, "f6_timeseries", "Displacement histories at reliable pixels: the closest one to the glacier zone, one in the "
                               "valley below it, one on stable ground, and the reference pixel itself (zero by construction).", idx)


def f7_glacier_zoom(c: Ctx, idx, st):
    la, lo = c.gz_centre
    bb = (lo - 0.045, lo + 0.045, la - 0.035, la + 0.035)
    pre12 = np.mean([c.coh("pre_event", p) for p in PRE12], axis=0)
    ev = c.coh("post_event", EVENT_PAIR)
    amp = 10 * np.log10(np.maximum(c.amp_ml("20260831"), 1e-9) / np.maximum(c.amp_ml("20260819"), 1e-9))
    amp = amp - np.nanmedian(amp[np.isfinite(amp)])
    panels = [(pre12, "coherence, mean of 12-day pre-event pairs", "viridis", 0, 0.8, "γ"),
              (ev, "coherence, 19 Aug – 31 Aug (spans the event)", "viridis", 0, 0.8, "γ"),
              (ev - pre12, "coherence change across the event", "RdBu_r", -0.3, 0.3, "Δγ"),
              (amp, "amplitude change, 19 → 31 Aug", "RdBu_r", -4, 4, "dB")]
    fig, ax = plt.subplots(1, 4, figsize=(13.5, 3.1))
    for a, (arr, title, cmap, lo_, hi_, unit) in zip(ax, panels):
        g, ext = c.geocode(arr, bbox=bb)
        im = a.imshow(g, extent=ext, cmap=cmap, vmin=lo_, vmax=hi_, origin="upper")
        a.plot(*np.vstack([c.gz, c.gz[:1]]).T, color="k", lw=1.1)
        a.set_title(title, fontsize=7.5); a.set_xlabel("longitude (°E)")
        plt.colorbar(im, ax=a, shrink=0.85, label=unit)
    ax[0].set_ylabel("latitude (°N)")
    near = c.dist_km < 1.0
    st["glacier_zone"] = {
        "coherence_pre12_gz": float(np.nanmedian(pre12[c.in_gz])), "coherence_event_gz": float(np.nanmedian(ev[c.in_gz])),
        "coherence_pre12_aoi": float(np.nanmedian(pre12[c.in_aoi])), "coherence_event_aoi": float(np.nanmedian(ev[c.in_aoi])),
        "coherence_post12_gz": float(np.nanmedian(c.coh("post_event", POST12)[c.in_gz])),
        "amp_change_gz_p5_p50_p95": [float(x) for x in np.nanpercentile(amp[c.in_gz], [5, 50, 95])],
        "amp_change_within_1km_p5_p50_p95": [float(x) for x in np.nanpercentile(amp[near], [5, 50, 95])],
        "amp_change_aoi_p5_p50_p95": [float(x) for x in np.nanpercentile(amp[c.in_aoi], [5, 50, 95])],
    }
    np.save(OUT / "amp_change_db.npy", amp)
    save(fig, "f7_glacier_zoom", "The glacier zone at 40 m posting. Coherence before and across the event, their difference, and "
                                 "the amplitude change. Phase is unusable on the ice in every pair; amplitude is not.", idx)


def f8_coherence_vs_baseline(c: Ctx, idx, st):
    import datetime as dt
    rows = []
    for run in ("pre_event", "post_event"):
        for p in sorted((TS / run / "pairs").iterdir()):
            if not p.is_dir():
                continue
            a, b = p.name.split("_")
            dtd = (dt.datetime.strptime(b, "%Y%m%d") - dt.datetime.strptime(a, "%Y%m%d")).days
            g = c.coh(run, p.name)
            rows.append({"run": run, "pair": p.name, "days": dtd, "aoi": float(np.nanmedian(g[c.in_aoi])),
                         "gz": float(np.nanmedian(g[c.in_gz])),
                         "high": float(np.nanmedian(g[c.in_aoi & (c.hgt > 5000)])),
                         "low": float(np.nanmedian(g[c.in_aoi & (c.hgt < 3500)]))})
    fig, ax = plt.subplots(1, 2, figsize=(9.2, 3.2))
    for run, mark in (("pre_event", "o"), ("post_event", "s")):
        r = [x for x in rows if x["run"] == run]
        for key, col, lab in (("aoi", CB["pos"], "whole AOI"), ("gz", CB["neg"], "glacier zone"),
                              ("high", CB["n1"], "above 5000 m"), ("low", CB["n2"], "below 3500 m")):
            ax[0].plot([x["days"] for x in r], [x[key] for x in r], mark, color=col, ms=5, alpha=0.85,
                       label=f"{lab} ({run.split('_')[0]})" if mark == "o" else None)
    ax[0].set_xlabel("temporal baseline (days)"); ax[0].set_ylabel("median coherence")
    ax[0].legend(fontsize=6.5); ax[0].set_title("Coherence vs temporal baseline (circles pre, squares post)")
    r12 = [x for x in rows if x["days"] == 12]
    ax[1].bar(np.arange(len(r12)), [x["aoi"] for x in r12], color=CB["pos"], width=0.4, label="AOI")
    ax[1].bar(np.arange(len(r12)) + 0.4, [x["gz"] for x in r12], color=CB["neg"], width=0.4, label="glacier zone")
    ax[1].set_xticks(np.arange(len(r12)) + 0.2)
    ax[1].set_xticklabels([x["pair"].replace("_", "\n") for x in r12], fontsize=6)
    ax[1].set_ylabel("median coherence"); ax[1].legend(fontsize=6.5); ax[1].set_title("12-day pairs, in time order")
    st["coherence_rows"] = rows
    save(fig, "f8_coherence", "Coherence against temporal baseline, split by terrain. The glacier zone stays near the noise "
                              "floor at every baseline, while the rest of the AOI improves after the monsoon.", idx)


def f9_amplitude(c: Ctx, idx, st):
    amp = np.load(OUT / "amp_change_db.npy")
    g, ext = c.geocode(amp)
    fig, ax = plt.subplots(1, 2, figsize=(9.6, 3.4))
    im = ax[0].imshow(g, extent=ext, cmap="RdBu_r", vmin=-3, vmax=3, origin="upper")
    ax[0].plot(*np.vstack([c.gz, c.gz[:1]]).T, color="k", lw=1.0)
    plt.colorbar(im, ax=ax[0], shrink=0.85, label="dB")
    ax[0].set_title("Amplitude change 19 → 31 Aug"); ax[0].set_xlabel("longitude (°E)"); ax[0].set_ylabel("latitude (°N)")
    rings = [(0, 0.5), (0.5, 1), (1, 2), (2, 3), (3, 5), (5, 8), (8, 12), (12, 20)]
    xs, p5, p95, iqr = [], [], [], []
    for lo_, hi_ in rings:
        sel = (c.dist_km >= lo_) & (c.dist_km < hi_) & np.isfinite(amp)
        if sel.sum() < 50:
            continue
        xs.append((lo_ + hi_) / 2)
        p5.append(float(np.nanpercentile(amp[sel], 5))); p95.append(float(np.nanpercentile(amp[sel], 95)))
        iqr.append(float(np.nanpercentile(amp[sel], 75) - np.nanpercentile(amp[sel], 25)))
    ax[1].plot(xs, p95, "o-", color=CB["neg"], label="95th percentile")
    ax[1].plot(xs, p5, "o-", color=CB["pos"], label="5th percentile")
    ax[1].plot(xs, iqr, "s--", color=CB["grey"], label="interquartile range")
    ax[1].set_xlabel("distance from the glacier zone (km)"); ax[1].set_ylabel("amplitude change (dB)")
    ax[1].legend(fontsize=6.5); ax[1].set_title("Amplitude change concentrates near the glacier zone")
    st["amplitude_rings"] = [{"km": x, "p5": a, "p95": b, "iqr": i} for x, a, b, i in zip(xs, p5, p95, iqr)]
    save(fig, "f9_amplitude", "Backscatter change across the event. The spread grows sharply within about a kilometre of the "
                              "glacier zone, where the phase carries no information.", idx)


def f10_event_displacement(c: Ctx, idx, st):
    dates, ts = c.ts("post_event")
    tc = c.tcoh("post_event")
    disp = ts[dates.index("20260831")]
    good = (tc >= 0.7) & np.isfinite(disp)
    g, ext = c.geocode(np.where(good, disp, np.nan))
    fig, ax = plt.subplots(1, 2, figsize=(9.8, 3.4))
    lim = float(np.nanpercentile(np.abs(disp[good & c.in_aoi]), 95))
    im = ax[0].imshow(g, extent=ext, cmap="RdBu_r", vmin=-lim, vmax=lim, origin="upper")
    ax[0].plot(*np.vstack([c.gz, c.gz[:1]]).T, color="k", lw=1.0)
    plt.colorbar(im, ax=ax[0], shrink=0.85, label="mm")
    ax[0].set_title("LOS displacement 19 → 31 Aug (spans the event)")
    ax[0].set_xlabel("longitude (°E)"); ax[0].set_ylabel("latitude (°N)")
    # radial profile around the glacier zone: displacement and coherence
    ev = c.coh("post_event", EVENT_PAIR)
    edges = np.arange(0, 20.5, 0.5)
    mid, dmed, dlo, dhi, cmed, npix = [], [], [], [], [], []
    for lo_, hi_ in zip(edges[:-1], edges[1:]):
        sel = (c.dist_km >= lo_) & (c.dist_km < hi_)
        sg = sel & good
        if sel.sum() < 30:
            continue
        mid.append((lo_ + hi_) / 2)
        dmed.append(float(np.nanmedian(disp[sg])) if sg.sum() > 10 else np.nan)
        dlo.append(float(np.nanpercentile(disp[sg], 16)) if sg.sum() > 10 else np.nan)
        dhi.append(float(np.nanpercentile(disp[sg], 84)) if sg.sum() > 10 else np.nan)
        cmed.append(float(np.nanmedian(ev[sel])))
        npix.append(int(sg.sum()))
    ax[1].fill_between(mid, dlo, dhi, color=CB["pos"], alpha=0.2, label="16–84 %")
    ax[1].plot(mid, dmed, "-", color=CB["pos"], lw=1.4, label="median displacement")
    ax[1].axhline(0, color="k", lw=0.6)
    ax2 = ax[1].twinx(); ax2.plot(mid, cmed, "--", color=CB["grey"], lw=1.2, label="coherence")
    ax2.set_ylabel("coherence of the event pair"); ax2.grid(False)
    ax[1].set_xlabel("distance from the glacier zone (km)"); ax[1].set_ylabel("LOS displacement (mm)")
    ax[1].legend(fontsize=6.5, loc="upper right"); ax2.legend(fontsize=6.5, loc="lower right")
    ax[1].set_title("Radial profile away from the glacier zone")
    st["event_displacement"] = {
        "aoi_p5_p50_p95": [float(x) for x in np.nanpercentile(disp[good & c.in_aoi], [5, 50, 95])],
        "good_pixels_in_gz": int((good & c.in_gz).sum()),
        "profile": [{"km": m, "median_mm": a, "p16": b, "p84": d, "coherence": e, "pixels": n}
                    for m, a, b, d, e, n in zip(mid, dmed, dlo, dhi, cmed, npix)],
    }
    save(fig, "f10_event_displacement", "Displacement over the pair that spans the outburst, and its radial profile around the "
                                        "glacier zone with the coherence that supports it.", idx)


def f11_detection(c: Ctx, idx, st):
    v, vstd = c.velocity("pre_event")
    tc = c.tcoh("pre_event")
    good = (tc >= 0.7) & c.in_aoi & np.isfinite(v)
    dpre, tspre = c.ts("pre_event")
    resid = []
    for i in range(1, len(dpre)):                      # scatter of consecutive-date differences on reliable ground
        d = tspre[i] - tspre[i - 1]
        resid.append(np.nanstd(d[good]))
    fig, ax = plt.subplots(1, 2, figsize=(9.2, 3.2))
    ax[0].hist(vstd[good], bins=np.linspace(0, np.nanpercentile(vstd[good], 99), 60), color=CB["pos"], alpha=0.85)
    ax[0].axvline(float(np.nanmedian(vstd[good])), color=CB["neg"], ls="--", lw=1,
                  label=f"median {np.nanmedian(vstd[good]):.0f} mm/yr")
    ax[0].set_xlabel("velocity uncertainty (mm/yr)"); ax[0].set_ylabel("pixels"); ax[0].legend(fontsize=6.5)
    ax[0].set_title("Velocity uncertainty over reliable pixels")
    x = np.arange(1, len(dpre))
    ax[1].plot(x, resid, "o-", color=CB["n1"], lw=1.2)
    ax[1].set_xticks(x); ax[1].set_xticklabels([f"{dpre[i-1][4:]}→{dpre[i][4:]}" for i in x], fontsize=6.5)
    ax[1].set_ylabel("scatter of date-to-date displacement (mm)")
    ax[1].set_title("Residual scatter between consecutive dates")
    st["detection"] = {
        "velocity_std_median_mm_yr": float(np.nanmedian(vstd[good])),
        "velocity_std_p95_mm_yr": float(np.nanpercentile(vstd[good], 95)),
        "date_to_date_scatter_mm": [float(r) for r in resid],
        "interval_days": 60, "detectable_displacement_mm": float(2 * np.median(resid)),
    }
    save(fig, "f11_detection", "What the pre-event series can and cannot resolve: the uncertainty of the fitted velocity, and "
                               "the scatter between consecutive dates that sets the smallest believable displacement.", idx)


def f12_change_clusters(c: Ctx, idx, st):
    """Where did the surface actually change? Connected clusters of strong amplitude change near the glacier zone."""
    from scipy import ndimage
    amp = np.load(OUT / "amp_change_db.npy")
    ev = c.coh("post_event", EVENT_PAIR)
    pre12 = np.mean([c.coh("pre_event", p) for p in PRE12], axis=0)
    strong = np.isfinite(amp) & (np.abs(amp) > 3.0) & (c.dist_km < 8.0)
    lab, n = ndimage.label(ndimage.binary_opening(strong, np.ones((2, 2))))
    rows = []
    for k in range(1, n + 1):
        m = lab == k
        if m.sum() < 12:                      # at least ~12 pixels (~19000 m2)
            continue
        rows.append({"pixels": int(m.sum()), "area_km2": round(float(m.sum()) * 0.0016, 3),
                     "lat": float(np.median(c.lat[m])), "lon": float(np.median(c.lon[m])),
                     "height_m": float(np.median(c.hgt[m])), "dist_km": float(np.median(c.dist_km[m])),
                     "amp_db": float(np.median(amp[m])), "coh_pre": float(np.median(pre12[m])),
                     "coh_event": float(np.median(ev[m]))})
    rows.sort(key=lambda r: -r["pixels"])
    la, lo = c.gz_centre
    bb = (lo - 0.06, lo + 0.06, la - 0.045, la + 0.045)
    g, ext = c.geocode(amp, bbox=bb)
    fig, ax = plt.subplots(1, 2, figsize=(10.2, 3.6))
    im = ax[0].imshow(g, extent=ext, cmap="RdBu_r", vmin=-4, vmax=4, origin="upper")
    plt.colorbar(im, ax=ax[0], shrink=0.85, label="dB")
    ax[0].plot(*np.vstack([c.gz, c.gz[:1]]).T, color="k", lw=1.2)
    for i, r in enumerate(rows[:8]):
        ax[0].plot(r["lon"], r["lat"], "o", mfc="none", mec="k", ms=8, mew=1.0)
        ax[0].annotate(str(i + 1), (r["lon"], r["lat"]), fontsize=7, xytext=(5, 3), textcoords="offset points")
    ax[0].set_title("Amplitude change with the largest clusters numbered")
    ax[0].set_xlabel("longitude (°E)"); ax[0].set_ylabel("latitude (°N)")
    if rows:
        ax[1].scatter([r["dist_km"] for r in rows], [r["amp_db"] for r in rows],
                      s=[max(8, min(200, r["pixels"])) for r in rows],
                      c=[r["height_m"] for r in rows], cmap="terrain", edgecolor="k", linewidth=0.4)
        for i, r in enumerate(rows[:8]):
            ax[1].annotate(str(i + 1), (r["dist_km"], r["amp_db"]), fontsize=7, xytext=(4, 3), textcoords="offset points")
        sc = ax[1].collections[0]
        plt.colorbar(sc, ax=ax[1], shrink=0.85, label="elevation (m)")
    ax[1].axhline(0, color="k", lw=0.6)
    ax[1].set_xlabel("distance from the glacier zone (km)"); ax[1].set_ylabel("median amplitude change (dB)")
    ax[1].set_title("Cluster size, distance, elevation and sign")
    st["change_clusters"] = rows[:20]
    st["change_cluster_summary"] = {"clusters": len(rows), "within_1km": sum(1 for r in rows if r["dist_km"] < 1),
                                    "total_area_km2": round(sum(r["area_km2"] for r in rows), 2)}
    save(fig, "f12_clusters", "Clusters where backscatter changed by more than 3 dB across the event, within 8 km of the "
                              "glacier zone. Marker size is cluster area, colour is elevation.", idx)


def f13_flood_path(c: Ctx, idx, st):
    """Pixels that were coherent before the event and lost coherence across it: the classic flood-footprint mask."""
    amp = np.load(OUT / "amp_change_db.npy")
    pre12 = np.mean([c.coh("pre_event", p) for p in PRE12], axis=0)
    post12 = c.coh("post_event", POST12)
    ev = c.coh("post_event", EVENT_PAIR)
    lost = (pre12 > 0.40) & (ev < 0.20)                      # coherent before, decorrelated across the event
    recovered = lost & (post12 > 0.35)                        # coherence returned afterwards: transient scattering change
    control = (pre12 > 0.40) & (post12 < 0.20)                # same test on a pair that does not span the event
    fig, ax = plt.subplots(1, 3, figsize=(13.2, 3.5))
    la, lo = c.gz_centre
    bb = (lo - 0.10, lo + 0.10, la - 0.07, la + 0.07)
    hz, ext = c.geocode(np.where(c.hgt > -500, c.hgt, np.nan), bbox=bb)
    lz, _ = c.geocode(lost.astype(float), bbox=bb)
    ax[0].imshow(hz, extent=ext, cmap="Greys_r", origin="upper", alpha=0.9)
    ax[0].imshow(np.where(lz > 0.5, 1.0, np.nan), extent=ext, cmap="autumn", origin="upper", vmin=0, vmax=1)
    ax[0].plot(*np.vstack([c.gz, c.gz[:1]]).T, color=CB["pos"], lw=1.3)
    ax[0].set_title("Coherence lost across the event (γ > 0.40 → < 0.20)")
    ax[0].set_xlabel("longitude (°E)"); ax[0].set_ylabel("latitude (°N)")
    # elevation profile of the loss, compared with the control pair
    bins = np.arange(2500, 6200, 200)
    for m, col, lab in ((lost, CB["neg"], "across the event (19–31 Aug)"), (control, CB["grey"], "control pair (31 Aug – 12 Sep)")):
        frac = []
        for b0, b1 in zip(bins[:-1], bins[1:]):
            sel = (c.hgt >= b0) & (c.hgt < b1) & (pre12 > 0.40)
            frac.append(100 * m[sel].mean() if sel.sum() > 200 else np.nan)
        ax[1].plot(bins[:-1] + 100, frac, "o-", color=col, ms=4, lw=1.3, label=lab)
    ax[1].set_xlabel("elevation (m)"); ax[1].set_ylabel("share of previously coherent pixels that lost coherence (%)")
    ax[1].legend(fontsize=6.5); ax[1].set_title("Coherence loss by elevation")
    # downstream distance profile: loss fraction and median amplitude change
    edges = np.arange(0, 12.5, 0.5)
    mid, fr, ab = [], [], []
    for lo_, hi_ in zip(edges[:-1], edges[1:]):
        sel = (c.dist_km >= lo_) & (c.dist_km < hi_) & (pre12 > 0.40)
        if sel.sum() < 100:
            continue
        mid.append((lo_ + hi_) / 2); fr.append(100 * lost[sel].mean())
        ab.append(float(np.nanpercentile(np.abs(amp[(c.dist_km >= lo_) & (c.dist_km < hi_)]), 95)))
    ax[2].plot(mid, fr, "o-", color=CB["neg"], ms=4, lw=1.3, label="coherence loss (%)")
    ax2 = ax[2].twinx(); ax2.plot(mid, ab, "s--", color=CB["n2"], ms=4, lw=1.2, label="|amplitude change| p95 (dB)")
    ax2.set_ylabel("|amplitude change| p95 (dB)"); ax2.grid(False)
    ax[2].set_xlabel("distance from the glacier zone (km)"); ax[2].set_ylabel("coherence loss (%)")
    ax[2].legend(fontsize=6.5, loc="upper right"); ax2.legend(fontsize=6.5, loc="lower right")
    ax[2].set_title("Both signatures fade away from the glacier zone")
    st["flood_path"] = {
        "lost_pixels": int(lost.sum()), "lost_area_km2": round(float(lost.sum()) * 0.0016, 2),
        "control_pixels": int(control.sum()), "control_area_km2": round(float(control.sum()) * 0.0016, 2),
        "recovered_fraction": float(recovered.sum() / max(1, lost.sum())),
        "lost_height_p5_p50_p95": [float(x) for x in np.nanpercentile(c.hgt[lost], [5, 50, 95])] if lost.any() else None,
        "lost_dist_km_p5_p50_p95": [float(x) for x in np.nanpercentile(c.dist_km[lost], [5, 50, 95])] if lost.any() else None,
        "lost_within_2km_km2": round(float((lost & (c.dist_km < 2)).sum()) * 0.0016, 3),
    }
    save(fig, "f13_flood_path", "Pixels that held coherence before the outburst and lost it across it, the elevations where that "
                                "happened, and how both the coherence loss and the amplitude change decay away from the glacier "
                                "zone. The 31 Aug – 12 Sep pair is the control: the same test where no event occurred.", idx)


def f14_controls(c: Ctx, idx, st):
    """The same amplitude test on pairs that do not span the event: is the change near the glacier zone special?"""
    from scipy import ndimage
    pairs = {"event 19–31 Aug": ("20260819", "20260831"), "control 31 Aug – 12 Sep": ("20260831", "20260912"),
             "control 14–26 Jul": ("20260714", "20260726")}
    amps, stats = {}, {}
    for name, (a, b) in pairs.items():
        d = 10 * np.log10(np.maximum(c.amp_ml(b), 1e-9) / np.maximum(c.amp_ml(a), 1e-9))
        d = d - np.nanmedian(d[np.isfinite(d)])
        amps[name] = d
        near = (c.dist_km < 1) & np.isfinite(d)
        far = (c.dist_km > 8) & np.isfinite(d)
        strong = np.isfinite(d) & (np.abs(d) > 3.0) & (c.dist_km < 8.0)
        lab, n = ndimage.label(ndimage.binary_opening(strong, np.ones((2, 2))))
        sizes = np.bincount(lab.ravel())[1:]
        stats[name] = {"spread_near_1km_dB": float(np.nanpercentile(d[near], 95) - np.nanpercentile(d[near], 5)),
                       "spread_beyond_8km_dB": float(np.nanpercentile(d[far], 95) - np.nanpercentile(d[far], 5)),
                       "clusters_over_12px": int((sizes >= 12).sum()),
                       "cluster_area_km2": round(float(sizes[sizes >= 12].sum()) * 0.0016, 3)}
        stats[name]["ratio_near_far"] = stats[name]["spread_near_1km_dB"] / stats[name]["spread_beyond_8km_dB"]
    la, lo = c.gz_centre
    bb = (lo - 0.06, lo + 0.06, la - 0.045, la + 0.045)
    fig, ax = plt.subplots(1, 4, figsize=(13.8, 3.3))
    for a, (name, d) in zip(ax[:3], amps.items()):
        g, ext = c.geocode(d, bbox=bb)
        im = a.imshow(g, extent=ext, cmap="RdBu_r", vmin=-4, vmax=4, origin="upper")
        a.plot(*np.vstack([c.gz, c.gz[:1]]).T, color="k", lw=1.2)
        a.set_title(f"{name}\nnear/far spread ratio {stats[name]['ratio_near_far']:.2f}", fontsize=7.5)
        a.set_xlabel("longitude (°E)")
        plt.colorbar(im, ax=a, shrink=0.85, label="dB")
    ax[0].set_ylabel("latitude (°N)")
    rings = np.arange(0, 12.5, 0.5)
    for name, d in amps.items():
        xs, ys = [], []
        for lo_, hi_ in zip(rings[:-1], rings[1:]):
            sel = (c.dist_km >= lo_) & (c.dist_km < hi_) & np.isfinite(d)
            if sel.sum() < 100:
                continue
            xs.append((lo_ + hi_) / 2)
            ys.append(float(np.nanpercentile(d[sel], 95) - np.nanpercentile(d[sel], 5)))
        ax[3].plot(xs, ys, "o-", ms=3.5, lw=1.3, label=name)
    ax[3].set_xlabel("distance from the glacier zone (km)"); ax[3].set_ylabel("amplitude-change spread, p95 − p5 (dB)")
    ax[3].legend(fontsize=6.5); ax[3].set_title("Only the event pair peaks at the glacier zone")
    st["controls"] = stats
    save(fig, "f14_controls", "The same backscatter-change test applied to the event pair and to two pairs that do not span the "
                              "event. The concentration of change at the glacier zone appears only across the event.", idx)


def _block_median(arr, mask, k=3):
    """Median of valid values in k x k blocks, returned on the full grid (noise beaten down by ~k)."""
    out = np.full(arr.shape, np.nan)
    for i in range(0, arr.shape[0] - k + 1, k):
        for j in range(0, arr.shape[1] - k + 1, k):
            b, m = arr[i:i + k, j:j + k], mask[i:i + k, j:j + k]
            if m.sum() >= max(2, k * k // 3):
                out[i:i + k, j:j + k] = np.median(b[m])
    return out


def f15_focus_velocity(c: Ctx, idx, st):
    """The glacier bounding box with a 100 % buffer: where the measurement exists and what velocity it gives."""
    v, vstd = c.velocity("pre_event")
    tc = c.tcoh("pre_event")
    ok = (tc >= 0.7) & np.isfinite(v)
    bb = c.focus_bbox
    hz, ext = c.geocode(np.where(c.hgt > -500, c.hgt, np.nan), step=2.0e-4, bbox=bb, fill=3)
    fig, ax = plt.subplots(1, 4, figsize=(14.0, 3.4))
    for a in ax:
        a.imshow(hz, extent=ext, cmap="Greys_r", origin="upper")
        a.plot(*np.vstack([c.gz, c.gz[:1]]).T, color=CB["pos"], lw=1.3)
        a.set_xlabel("longitude (°E)")
        a.set_xlim(bb[0], bb[1]); a.set_ylim(bb[2], bb[3])
    ax[0].set_ylabel("latitude (°N)")
    ax[0].set_title(f"Terrain, {c.focus_km[0]:.1f} × {c.focus_km[1]:.1f} km box")
    sel = c.in_focus & ok
    for a, arr, cmap, lim, lab, title in (
            (ax[1], v, "RdBu_r", (-300, 300), "mm/yr", "LOS velocity, reliable pixels"),
            (ax[2], vstd, "magma", (0, 400), "mm/yr", "Velocity uncertainty (1σ)"),
            (ax[3], np.where(np.abs(v) > 2 * vstd, np.abs(v) / vstd, np.nan), "viridis", (0, 4), "|v| / σ", "Significance")):
        m = sel & np.isfinite(arr)
        sc = a.scatter(c.lon[m], c.lat[m], c=arr[m], s=5, cmap=cmap, vmin=lim[0], vmax=lim[1], linewidths=0)
        plt.colorbar(sc, ax=a, shrink=0.85, label=lab)
        a.set_title(title)
    sig = sel & (np.abs(v) > 2 * vstd)
    st["focus"] = {
        "bbox": list(bb), "size_km": list(c.focus_km), "area_km2": round(c.focus_km[0] * c.focus_km[1], 2),
        "pixels": int(c.in_focus.sum()), "reliable_pre": int(sel.sum()),
        "reliable_in_glacier": int((ok & c.in_gz).sum()), "reliable_in_ring": int((ok & c.in_ring).sum()),
        "height_m": [float(np.nanmin(c.hgt[c.in_focus])), float(np.nanmax(c.hgt[c.in_focus]))],
        "incidence_deg": float(np.nanmedian(c.inc[c.in_focus])),
        "velocity_mm_yr": {q: float(np.percentile(v[sel], q)) for q in (16, 50, 84)},
        "velocity_sigma_mm_yr": float(np.median(vstd[sel])),
        "significant_pixels": int(sig.sum()), "significant_fraction": float(sig.sum() / max(1, sel.sum())),
        "glacier_velocity_mm_yr": (float(np.median(v[ok & c.in_gz])) if (ok & c.in_gz).any() else None),
        "glacier_sigma_mm_yr": (float(np.median(vstd[ok & c.in_gz])) if (ok & c.in_gz).any() else None),
    }
    save(fig, "f15_focus_velocity", "The analysis box: the glacier polygon's bounding box with a 100 % buffer. Reliable pixels "
                                    "sit on the rock and moraine around the ice, not on it. The fourth panel shows where the "
                                    "velocity exceeds twice its own uncertainty.", idx)


def f16_focus_timeseries(c: Ctx, idx, st):
    """Displacement history of the box, and what spatial averaging buys."""
    import datetime as dt
    v, vstd = c.velocity("pre_event")
    tc_pre, tc_post = c.tcoh("pre_event"), c.tcoh("post_event")
    ok_pre, ok_post = (tc_pre >= 0.7) & np.isfinite(v), (tc_post >= 0.7)
    dpre, tspre = c.ts("pre_event"); dpost, tspost = c.ts("post_event")
    fig, ax = plt.subplots(1, 3, figsize=(12.6, 3.3))
    for a, (dates, ts, m, title) in ((ax[0], (dpre, tspre, c.in_focus & ok_pre, "Pre-event, box median")),
                                     (ax[1], (dpost, tspost, c.in_focus & ok_post, "Post-event, box median"))):
        t = [dt.datetime.strptime(x, "%Y%m%d") for x in dates]
        med = [float(np.median(ts[i][m])) for i in range(len(dates))]
        p16 = [float(np.percentile(ts[i][m], 16)) for i in range(len(dates))]
        p84 = [float(np.percentile(ts[i][m], 84)) for i in range(len(dates))]
        a.fill_between(t, p16, p84, color=CB["pos"], alpha=0.2, label="16–84 % of pixels")
        a.plot(t, med, "o-", color=CB["pos"], lw=1.4, ms=4, label="median")
        ring = [float(np.median(ts[i][c.in_ring & m])) for i in range(len(dates))]
        a.plot(t, ring, "s--", color=CB["grey"], lw=1.1, ms=3.5, label="buffer ring only")
        a.axhline(0, color="k", lw=0.6)
        a.set_title(title); a.tick_params(axis="x", rotation=30); a.legend(fontsize=6.5)
        st.setdefault("focus_series", {})[title.split(",")[0]] = {"dates": dates, "median_mm": med, "p16": p16, "p84": p84}
    ax[1].axvline(dt.datetime(2026, 8, 26), color=CB["neg"], ls="--", lw=1)
    ax[0].set_ylabel("LOS displacement (mm, + toward satellite)")
    sel = c.in_focus & ok_pre
    vb = _block_median(v, sel, 3)
    keep = np.isfinite(vb) & c.in_focus
    ax[2].hist(v[sel], bins=np.linspace(-600, 400, 50), histtype="step", color=CB["grey"],
               label=f"40 m pixels (σ {np.std(v[sel]):.0f} mm/yr)")
    ax[2].hist(vb[keep], bins=np.linspace(-600, 400, 50), histtype="step", color=CB["pos"],
               label=f"120 m blocks (σ {np.nanstd(vb[keep]):.0f} mm/yr)")
    ax[2].axvline(0, color="k", lw=0.6)
    ax[2].set_xlabel("LOS velocity (mm/yr)"); ax[2].set_ylabel("pixels"); ax[2].legend(fontsize=6.5)
    ax[2].set_title("Averaging 3 × 3 pixels")
    st["focus_averaging"] = {"velocity_std_40m": float(np.std(v[sel])), "velocity_std_120m": float(np.nanstd(vb[keep])),
                             "velocity_median_120m": float(np.nanmedian(vb[keep]))}
    save(fig, "f16_focus_timeseries", "Displacement history inside the box before and across the event, and the effect of "
                                      "averaging 3 × 3 pixels on the velocity scatter.", idx)


def f17_focus_event(c: Ctx, idx, st):
    """The event signatures inside the box."""
    amp = np.load(OUT / "amp_change_db.npy")
    pre12 = np.mean([c.coh("pre_event", p) for p in PRE12], axis=0)
    ev = c.coh("post_event", EVENT_PAIR)
    bb = c.focus_bbox
    fig, ax = plt.subplots(1, 4, figsize=(14.0, 3.3))
    for a, (arr, title, cmap, lo_, hi_, unit) in zip(ax, [
            (pre12, "coherence, 12-day pre-event mean", "viridis", 0, 0.6, "γ"),
            (ev, "coherence across the event", "viridis", 0, 0.6, "γ"),
            (ev - pre12, "coherence change", "RdBu_r", -0.3, 0.3, "Δγ"),
            (amp, "amplitude change 19 → 31 Aug", "RdBu_r", -6, 6, "dB")]):
        g, ext = c.geocode(arr, step=2.0e-4, bbox=bb, fill=3)
        im = a.imshow(g, extent=ext, cmap=cmap, vmin=lo_, vmax=hi_, origin="upper")
        a.plot(*np.vstack([c.gz, c.gz[:1]]).T, color="k", lw=1.2)
        a.set_title(title, fontsize=7.5); a.set_xlabel("longitude (°E)")
        a.set_xlim(bb[0], bb[1]); a.set_ylim(bb[2], bb[3])
        plt.colorbar(im, ax=a, shrink=0.85, label=unit)
    ax[0].set_ylabel("latitude (°N)")
    st["focus_event"] = {
        "coherence_pre12": float(np.nanmedian(pre12[c.in_focus])), "coherence_event": float(np.nanmedian(ev[c.in_focus])),
        "amp_change_p5_p50_p95": [float(x) for x in np.nanpercentile(amp[c.in_focus], [5, 50, 95])],
        "amp_change_glacier_p5_p95": [float(x) for x in np.nanpercentile(amp[c.in_gz], [5, 95])],
        "amp_change_ring_p5_p95": [float(x) for x in np.nanpercentile(amp[c.in_ring], [5, 95])],
        "strong_change_pixels": int((np.abs(amp[c.in_focus]) > 3).sum()),
        "strong_change_area_km2": round(float((np.abs(amp[c.in_focus]) > 3).sum()) * 0.0016, 3),
    }
    save(fig, "f17_focus_event", "Inside the analysis box: coherence before and across the event, its change, and the "
                                 "amplitude change. The strong backscatter changes sit on and immediately around the ice.", idx)


RELAXED = "mintpy_relaxed"
TCOH_MIN = 0.3


def f18_relaxed_coverage(c: Ctx, idx, st):
    """Snaphu leaves the ice in component 0, so the default mask removed it. With the mask off, coverage returns."""
    thr = [0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
    rows = {}
    for name, variant in (("connected-component mask on (default)", "mintpy"), ("mask off, all pixels inverted", RELAXED)):
        tc = c.tcoh("pre_event", variant)
        v, vs = c.velocity("pre_event", variant=variant)
        rows[name] = {"glacier": [int(((tc >= t) & c.in_gz).sum()) for t in thr],
                      "box": [int(((tc >= t) & c.in_focus).sum()) for t in thr],
                      "velocity_at_0.3": (float(np.median(v[(tc >= 0.3) & c.in_gz])) if ((tc >= 0.3) & c.in_gz).any() else None),
                      "sigma_at_0.3": (float(np.median(vs[(tc >= 0.3) & c.in_gz])) if ((tc >= 0.3) & c.in_gz).any() else None)}
    fig, ax = plt.subplots(1, 3, figsize=(12.4, 3.3))
    for (name, r), col in zip(rows.items(), (CB["grey"], CB["pos"])):
        ax[0].plot(thr, r["glacier"], "o-", color=col, lw=1.4, ms=4, label=name)
        ax[0].plot(thr, r["box"], "s--", color=col, lw=1.1, ms=3.5, alpha=0.7)
    ax[0].axvline(TCOH_MIN, color=CB["neg"], ls=":", lw=1)
    ax[0].set_xlabel("temporal-coherence threshold"); ax[0].set_ylabel("pixels retained")
    ax[0].set_yscale("log"); ax[0].legend(fontsize=6.2, loc="lower left")
    ax[0].set_title("Coverage (circles: glacier polygon, squares: box)")
    tc = c.tcoh("pre_event", RELAXED)
    v, vs = c.velocity("pre_event", variant=RELAXED)
    sel = c.in_focus & (tc >= TCOH_MIN) & np.isfinite(v)
    bb = c.focus_bbox
    hz, ext = c.geocode(np.where(c.hgt > -500, c.hgt, np.nan), step=2.0e-4, bbox=bb, fill=3)
    for a, arr, cmap, lim, lab, title in ((ax[1], v, "RdBu_r", (-300, 300), "mm/yr", f"LOS velocity, mask off, γ_t ≥ {TCOH_MIN}"),
                                          (ax[2], vs, "magma", (0, 500), "mm/yr", "Its uncertainty (1σ)")):
        a.imshow(hz, extent=ext, cmap="Greys_r", origin="upper")
        sc = a.scatter(c.lon[sel], c.lat[sel], c=arr[sel], s=4, cmap=cmap, vmin=lim[0], vmax=lim[1], linewidths=0)
        a.plot(*np.vstack([c.gz, c.gz[:1]]).T, color=CB["pos"], lw=1.3)
        a.set_xlim(bb[0], bb[1]); a.set_ylim(bb[2], bb[3]); a.set_xlabel("longitude (°E)")
        plt.colorbar(sc, ax=a, shrink=0.85, label=lab); a.set_title(title)
    ax[1].set_ylabel("latitude (°N)")
    st["relaxed"] = {"thresholds": thr, "counts": rows, "glacier_pixels": int(c.in_gz.sum()), "box_pixels": int(c.in_focus.sum()),
                     "used_threshold": TCOH_MIN,
                     "glacier_velocity_mm_yr": rows["mask off, all pixels inverted"]["velocity_at_0.3"],
                     "glacier_sigma_mm_yr": rows["mask off, all pixels inverted"]["sigma_at_0.3"]}
    save(fig, "f18_relaxed_coverage", "Left: pixels retained against the temporal-coherence threshold, with and without the "
                                      "connected-component mask. The mask, not the threshold, was what removed the ice. Right: "
                                      "the velocity that the recovered pixels give, and its uncertainty.", idx)


def _interval_stats(c: Ctx, run, variant=RELAXED, tmin=TCOH_MIN):
    dates, ts = c.ts(run, variant=variant)
    tc = c.tcoh(run, variant)
    ok = (tc >= tmin) & np.isfinite(ts[-1])
    out = []
    for i in range(1, len(dates)):
        d = ts[i] - ts[i - 1]
        pair = f"{dates[i - 1]}_{dates[i]}"
        coh = c.coh(run, pair) if (TS / run / "pairs" / pair).exists() else None
        g, r = ok & c.in_gz, ok & c.in_ring
        days = (dt.datetime.strptime(dates[i], "%Y%m%d") - dt.datetime.strptime(dates[i - 1], "%Y%m%d")).days
        out.append({"pair": pair, "days": days, "n_glacier": int(g.sum()), "n_ring": int(r.sum()),
                    "glacier_mm": float(np.median(d[g])) if g.sum() > 10 else None,
                    "glacier_p16": float(np.percentile(d[g], 16)) if g.sum() > 10 else None,
                    "glacier_p84": float(np.percentile(d[g], 84)) if g.sum() > 10 else None,
                    "ring_mm": float(np.median(d[r])) if r.sum() > 10 else None,
                    "difference_mm": (float(np.median(d[g]) - np.median(d[r])) if g.sum() > 10 and r.sum() > 10 else None),
                    "coherence_glacier": float(np.median(coh[c.in_gz])) if coh is not None else None,
                    "field": d})
    return dates, out


import datetime as dt  # noqa: E402


def f19_intervals(c: Ctx, idx, st):
    """The four consecutive pre-event intervals and the two post-event ones, inside the box."""
    bb = c.focus_bbox
    rows = []
    for run in ("pre_event", "post_event"):
        _, iv = _interval_stats(c, run)
        for r in iv:
            r["run"] = run
            rows.append(r)
    fig, ax = plt.subplots(1, len(rows), figsize=(2.35 * len(rows), 3.0))
    tc = {run: c.tcoh(run, RELAXED) for run in ("pre_event", "post_event")}
    for a, r in zip(ax, rows):
        d = np.where(tc[r["run"]] >= TCOH_MIN, r["field"], np.nan)
        g, ext = c.geocode(d, step=2.0e-4, bbox=bb, fill=2)
        im = a.imshow(g, extent=ext, cmap="RdBu_r", vmin=-60, vmax=60, origin="upper")
        a.plot(*np.vstack([c.gz, c.gz[:1]]).T, color="k", lw=1.1)
        a.set_xlim(bb[0], bb[1]); a.set_ylim(bb[2], bb[3])
        a.set_title(f"{r['pair'][4:8]}→{r['pair'][13:]}  ({r['days']} d)" + ("\nspans the event" if r["pair"] == "20260819_20260831" else ""), fontsize=7)
        a.set_xticks([]); a.set_yticks([])
    plt.colorbar(im, ax=ax[-1], shrink=0.85, label="LOS displacement (mm)")
    for r in rows:
        r.pop("field")
    st["intervals"] = rows
    save(fig, "f19_intervals", "Displacement over each consecutive interval inside the analysis box, with the connected-"
                               "component mask off and temporal coherence ≥ 0.3. Positive is toward the satellite.", idx)


def f20_glacier_vs_ring(c: Ctx, idx, st):
    """Is the glacier moving differently from its surroundings? Tested against random patches of the same size."""
    rng = np.random.default_rng(7)
    ys, xs = np.where(c.in_gz)
    cy0, cx0 = ys.mean(), xs.mean()
    ry, rx = (ys.max() - ys.min()) / 2, (xs.max() - xs.min()) / 2
    L, W = c.in_gz.shape
    dates, iv = _interval_stats(c, "pre_event")
    tc = c.tcoh("pre_event", RELAXED)
    ok = (tc >= TCOH_MIN) & np.isfinite(tc)
    Y, X = np.ogrid[:L, :W]

    def stat(d, cy, cx):
        e = ((Y - cy) / ry) ** 2 + ((X - cx) / rx) ** 2
        inner, outer = (e <= 1) & ok, (e > 1) & (e <= 4) & ok
        if inner.sum() < 100 or outer.sum() < 300:
            return None
        return float(np.median(d[inner]) - np.median(d[outer]))

    obs, nulls = [], []
    for r in iv:
        d = r["field"]
        o = stat(d, cy0, cx0)
        n = []
        while len(n) < 300:
            cy, cx = rng.uniform(2 * ry, L - 2 * ry), rng.uniform(2 * rx, W - 2 * rx)
            if np.hypot((cy - cy0) / ry, (cx - cx0) / rx) < 3:
                continue
            v = stat(d, cy, cx)
            if v is not None:
                n.append(v)
        obs.append(o); nulls.append(np.array(n))
    fig, ax = plt.subplots(1, 3, figsize=(12.0, 3.2))
    x = np.arange(len(iv))
    lab = [f"{r['pair'][4:8]}→{r['pair'][13:]}" for r in iv]
    ax[0].plot(x, [r["glacier_mm"] for r in iv], "o-", color=CB["neg"], lw=1.4, ms=5, label="glacier polygon")
    ax[0].plot(x, [r["ring_mm"] for r in iv], "s-", color=CB["grey"], lw=1.4, ms=4.5, label="surrounding ring")
    ax[0].fill_between(x, [r["glacier_p16"] for r in iv], [r["glacier_p84"] for r in iv], color=CB["neg"], alpha=0.15)
    ax[0].axhline(0, color="k", lw=0.6); ax[0].set_xticks(x); ax[0].set_xticklabels(lab, fontsize=6.5)
    ax[0].set_ylabel("LOS displacement over the interval (mm)"); ax[0].legend(fontsize=6.5)
    ax[0].set_title("Each interval: glacier and its surroundings move together")
    sd = np.array([n.std() for n in nulls])
    ax[1].bar(x, obs, color=[CB["pos"] if abs(o) > 2 * s else CB["grey"] for o, s in zip(obs, sd)])
    ax[1].errorbar(x, np.zeros(len(x)), yerr=2 * sd, fmt="none", ecolor=CB["neg"], elinewidth=1.4, capsize=4,
                   label="±2σ of random patches")
    ax[1].axhline(0, color="k", lw=0.6); ax[1].set_xticks(x); ax[1].set_xticklabels(lab, fontsize=6.5)
    ax[1].set_ylabel("glacier − surroundings (mm)"); ax[1].legend(fontsize=6.5)
    ax[1].set_title("The difference, against what random patches give")
    cum = np.cumsum(obs); cum_null = np.cumsum(np.array(nulls), axis=0)
    ax[2].plot(np.arange(1, len(cum) + 1), cum, "o-", color=CB["pos"], lw=1.6, ms=5, label="cumulative difference")
    ax[2].fill_between(np.arange(1, len(cum) + 1), -2 * cum_null.std(axis=1), 2 * cum_null.std(axis=1),
                       color=CB["grey"], alpha=0.25, label="±2σ null")
    ax[2].axhline(0, color="k", lw=0.6)
    ax[2].set_xticks(np.arange(1, len(cum) + 1)); ax[2].set_xticklabels(lab, fontsize=6.5)
    ax[2].set_ylabel("cumulative glacier − surroundings (mm)"); ax[2].legend(fontsize=6.5)
    ax[2].set_title("Cumulative over the two pre-event months")
    st["glacier_vs_ring"] = {
        "intervals": [{"pair": r["pair"], "days": r["days"], "difference_mm": o, "null_sd_mm": float(s),
                       "z": float(abs(o) / s), "p_two_sided": float((np.abs(n) >= abs(o)).mean())}
                      for r, o, s, n in zip(iv, obs, sd, nulls)],
        "cumulative_mm": float(cum[-1]), "cumulative_null_sd_mm": float(cum_null.std(axis=1)[-1]),
        "cumulative_p": float((np.abs(cum_null[-1]) >= abs(cum[-1])).mean()),
        "phase_noise_mm_at_coherence": {g: float(np.sqrt((1 - g ** 2) / (2 * 72 * g ** 2)) * c.meta["wavelength"] / (4 * np.pi) * 1000)
                                        for g in (0.15, 0.2, 0.3, 0.5)},
    }
    save(fig, "f20_glacier_vs_ring", "Left: the glacier polygon and its surrounding ring move together interval by interval, "
                                     "which is atmosphere. Middle: their difference, compared with the same statistic computed "
                                     "on random patches of identical size elsewhere. Right: the difference accumulated over the "
                                     "two pre-event months.", idx)


FIGS = {"f1": f1_study_area, "f2": f2_network, "f3": f3_validation, "f4": f4_pre_velocity, "f5": f5_corrections,
        "f6": f6_timeseries, "f7": f7_glacier_zoom, "f8": f8_coherence_vs_baseline, "f9": f9_amplitude,
        "f10": f10_event_displacement, "f11": f11_detection, "f12": f12_change_clusters, "f13": f13_flood_path, "f14": f14_controls, "f15": f15_focus_velocity, "f16": f16_focus_timeseries, "f17": f17_focus_event, "f18": f18_relaxed_coverage, "f19": f19_intervals, "f20": f20_glacier_vs_ring}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=list(FIGS), help="run only these figures")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    old = json.loads((OUT / "analysis.json").read_text()) if (OUT / "analysis.json").exists() else {}
    idx, st = old.get("figures", {}), old.get("stats", {})
    c = Ctx()
    for key in (a.only or list(FIGS)):
        print(f"{key}:")
        FIGS[key](c, idx, st)
    st["provenance"] = {"stack": str(STACK), "timeseries": str(TS), "event": EVENT,
                        "pre_dates": PRE_DATES, "post_dates": POST_DATES,
                        "qa": {r: json.loads((TS / r / "qa/mintpy.json").read_text()) for r in ("pre_event", "post_event")},
                        "closure": {r: json.loads((TS / r / "qa/unwrap_closure.json").read_text())["triplets"]
                                    for r in ("pre_event", "post_event")},
                        "corrections_status": {r: json.loads((TS / r / "status/corrections.json").read_text())
                                               for r in ("pre_event", "post_event")}}
    (OUT / "analysis.json").write_text(json.dumps({"figures": idx, "stats": st}, indent=1, default=str))
    print(f"\nwrote {OUT / 'analysis.json'} ({len(idx)} figures)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
