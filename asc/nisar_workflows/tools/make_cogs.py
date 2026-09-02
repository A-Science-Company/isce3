#!/usr/bin/env python3
"""
Build Cloud Optimized GeoTIFFs for the browser viewer.

    conda run -n isce3_env python tools/make_cogs.py \
        --pair-dir <case>/pairs/20260613_20260625/trackG \
        --freq A --pol HH

Why COGs rather than the PNGs the overlay stage bakes: the PNG route rendered
781 MiB for six layers at a FIXED resolution and a FIXED colour stretch. A COG
carries the real values plus internal overviews, so the browser range-requests
only the tiles and zoom level it is showing, colour mapping happens client-side,
and you can read actual numbers off a pixel instead of a colour.

Everything is warped to EPSG:3857 because that is what Leaflet renders in;
doing it once here beats reprojecting per tile in the browser.

Complex rasters cannot be consumed by the JS readers, so the interferogram is
split into a real `phase` band here.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from osgeo import gdal

gdal.UseExceptions()

WEBM = "EPSG:3857"

#: (suffix, output stem, how to turn the source into a real float32 band)
LAYERS = [
    ("amp_{f}_{p}_{d1}.tif", "amp_{d1}", "db"),
    ("amp_{f}_{p}_{d2}.tif", "amp_{d2}", "db"),
    ("ifg_{f}_{p}.filt.int.tif", "wrapped_phase", "phase"),
    ("ifg_{f}_{p}.coh.tif", "coherence", "identity"),
    ("ifg_{f}_{p}.filt.phsig.coh.tif", "phsig", "identity"),
    ("ifg_{f}_{p}.filt.unw.tif", "unwrapped_phase", "identity"),
    ("ifg_{f}_{p}.filt.unw.conncomp.tif", "conncomp", "identity"),
]


def log(m: str) -> None:
    print(m, flush=True)


def _derive(src: Path, how: str, dst: Path) -> None:
    """Write a real-valued float32 GeoTIFF derived from `src`, block by block."""
    ds = gdal.Open(str(src))
    W, H = ds.RasterXSize, ds.RasterYSize
    drv = gdal.GetDriverByName("GTiff")
    out = drv.Create(str(dst), W, H, 1, gdal.GDT_Float32,
                     options=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"])
    out.SetGeoTransform(ds.GetGeoTransform())
    out.SetProjection(ds.GetProjection())
    band_in, band_out = ds.GetRasterBand(1), out.GetRasterBand(1)
    band_out.SetNoDataValue(float("nan"))

    step = max(1, min(2048, H))
    for r0 in range(0, H, step):
        n = min(step, H - r0)
        a = band_in.ReadAsArray(0, r0, W, n)
        if how == "phase":
            v = np.angle(a).astype(np.float32)
            v[~np.isfinite(a) | (a == 0)] = np.nan
        elif how == "db":
            with np.errstate(divide="ignore", invalid="ignore"):
                v = (10.0 * np.log10(np.where(np.real(a) > 0, np.real(a), np.nan))
                     ).astype(np.float32)
        else:
            v = np.asarray(a, dtype=np.float32)
            if np.isrealobj(a):
                v = np.where(v == 0, np.nan, v) if "conncomp" not in dst.name else v
        band_out.WriteArray(v, 0, r0)
    band_out.FlushCache()
    out = ds = None


def _to_cog(src: Path, dst: Path, resample: str) -> None:
    gdal.Warp(str(dst), str(src), format="COG", dstSRS=WEBM,
              resampleAlg=resample, dstNodata=float("nan"),
              creationOptions=["COMPRESS=DEFLATE", "PREDICTOR=3",
                               "OVERVIEW_RESAMPLING=" + resample.upper(),
                               "BIGTIFF=IF_SAFER", "NUM_THREADS=ALL_CPUS"])


def stats(path: Path) -> dict:
    """Robust display range from the overview, so we never read the full grid."""
    ds = gdal.Open(str(path))
    b = ds.GetRasterBand(1)
    ov = b.GetOverview(b.GetOverviewCount() - 1) if b.GetOverviewCount() else b
    a = ov.ReadAsArray().astype("float64")
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"min": 0.0, "max": 1.0}
    lo, hi = np.percentile(a, [2.0, 98.0])
    return {"min": round(float(lo), 4), "max": round(float(hi), 4),
            "p50": round(float(np.median(a)), 4)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pair-dir", required=True)
    ap.add_argument("--freq", default="A")
    ap.add_argument("--pol", default="HH")
    ap.add_argument("--out", default=None, help="default: <pair-dir>/cog")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    pair = Path(a.pair_dir).resolve()
    out = Path(a.out).resolve() if a.out else pair / "cog"
    out.mkdir(parents=True, exist_ok=True)
    # --pair-dir points at .../pairs/<ref>_<sec>/trackG, so the dates live on the
    # parent. Accept either level.
    stem = pair.name if "_" in pair.name else pair.parent.name
    parts = stem.split("_")
    if len(parts) < 2:
        sys.exit(f"cannot parse <ref>_<sec> dates from {pair} (looked at {stem!r})")
    d1, d2 = parts[0], parts[1]

    manifest = {"pair": pair.name, "freq": a.freq, "pol": a.pol,
                "ref_date": d1, "sec_date": d2, "layers": []}
    tmp = out / "_tmp"
    tmp.mkdir(exist_ok=True)
    try:
        for pat, stem, how in LAYERS:
            src = pair / pat.format(f=a.freq, p=a.pol, d1=d1, d2=d2)
            name = stem.format(d1=d1, d2=d2)
            dst = out / f"{name}.tif"
            if not src.exists():
                log(f"  SKIP {name}: {src.name} not found")
                continue
            if dst.exists() and not a.force:
                log(f"  skip {name} (exists)")
            else:
                log(f"  {name:18s} <- {src.name}")
                mid = tmp / f"{name}_real.tif"
                _derive(src, how, mid)
                _to_cog(mid, dst, "nearest" if "conncomp" in name else "average")
                mid.unlink(missing_ok=True)
            manifest["layers"].append(
                {"name": name, "file": dst.name,
                 "mb": round(dst.stat().st_size / 2**20, 1), **stats(dst)})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    total = sum(l["mb"] for l in manifest["layers"])
    log(f"\n{len(manifest['layers'])} layer(s), {total:.1f} MiB total -> {out}")
    log(f"manifest -> {out / 'manifest.json'}")


if __name__ == "__main__":
    main()
