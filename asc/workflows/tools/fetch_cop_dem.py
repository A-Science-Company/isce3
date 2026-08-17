#!/usr/bin/env python3
"""
Fetch a Copernicus GLO-30 DEM as a WGS84-ELLIPSOIDAL float32 GeoTIFF.

Runs under **isce2_env**, which is where planetary-computer / pystac-client live.
The output is consumed by Track G under isce3_env.

    conda run -n isce2_env python fetch_cop_dem.py \
        --bbox "9.31 12.42 -69.81 -66.62" \
        --out /path/to/dem_cop.tif

Approach and the vertical-datum handling are taken from the existing ISCE2
implementation (isce2 @ sharath_dolphin:gdal_copernicus_dem.py), which sources
tiles from the Microsoft Planetary Computer STAC catalog rather than from the
Copernicus portal directly -- open collections are signed anonymously, so no
credentials are needed.

THE DATUM POINT, restated because it is the whole reason this file is careful:
Copernicus GLO-30 ships EGM2008 *geoid* heights. ISCE3 geometry (geo2rdr,
geocode_slc) needs heights above the WGS84 *ellipsoid*. Over this AOI the
undulation is roughly -9 m (SW) to -38 m (NE), so skipping the conversion puts
a tens-of-metres bias straight into the range computation. We therefore warp
EPSG:4326+3855 -> EPSG:4979, exactly as the ISCE2 script does.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "cop-dem-glo-30"


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str]) -> None:
    log("  $ " + " ".join(str(c) for c in cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"FAILED ({res.returncode}): {res.stderr.strip()[:2000]}")


def check_egm2008_available() -> None:
    """Fail early and loudly if PROJ cannot do the vertical transform.

    Without the EGM2008 grid, gdalwarp silently performs a horizontal-only
    transform and returns geoid heights labelled as ellipsoidal -- a wrong
    answer that looks exactly like a right one.
    """
    from pyproj import Transformer

    t = Transformer.from_crs("EPSG:4326+3855", "EPSG:4979", always_xy=True)
    lon, lat = -68.2, 10.9  # AOI centroid
    _, _, h = t.transform(lon, lat, 0.0)
    if abs(h) < 1.0:
        sys.exit(
            "PROJ returned a ~0 m geoid separation at the AOI centroid, which means\n"
            "the EGM2008 grid is NOT installed. The vertical transform would be a\n"
            "silent no-op. Install it with:  conda install -c conda-forge proj-data\n"
            "(or set PROJ_NETWORK=ON to fetch grids on demand)."
        )
    log(f"  PROJ EGM2008 check OK: undulation at AOI centroid = {h:+.2f} m")


def search_tiles(south: float, north: float, west: float, east: float) -> list[str]:
    import planetary_computer
    from pystac_client import Client

    log(f"searching {COLLECTION} over bbox W{west} S{south} E{east} N{north}")
    catalog = Client.open(STAC_URL)
    search = catalog.search(collections=[COLLECTION], bbox=[west, south, east, north])
    items = list(search.item_collection())
    if not items:
        sys.exit("no Copernicus DEM tiles returned for that bbox")
    log(f"  {len(items)} tile(s)")

    urls = []
    for item in items:
        signed = planetary_computer.sign(item.assets["data"].href)
        urls.append(signed)
    return urls


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", required=True,
                    help="'S N W E' (space separated), matching the ISCE2 script's convention")
    ap.add_argument("--out", required=True, help="output GeoTIFF path")
    ap.add_argument("--keep-geoid", action="store_true",
                    help="ALSO write a *_egm2008.tif with the untouched geoid heights "
                         "(useful for deriving a water mask via h<=0)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    south, north, west, east = (float(v) for v in args.bbox.split())
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    if out.exists() and not args.force:
        log(f"{out} exists, SKIP (use --force to redo)")
        return

    check_egm2008_available()
    urls = search_tiles(south, north, west, east)

    tmp = Path(tempfile.mkdtemp(prefix="copdem_"))
    try:
        # /vsicurl/ streams the signed COGs -- no need to download whole tiles
        vsi = [u.replace("https://", "/vsicurl/https://") for u in urls]
        listfile = tmp / "tiles.txt"
        listfile.write_text("\n".join(vsi) + "\n")

        vrt = tmp / "mosaic.vrt"
        log("building mosaic VRT")
        run(["gdalbuildvrt", "-input_file_list", str(listfile), str(vrt)])

        te = ["-te", str(west), str(south), str(east), str(north)]

        if args.keep_geoid:
            geoid_out = out.with_name(out.stem + "_egm2008.tif")
            log(f"writing untouched EGM2008 geoid heights -> {geoid_out.name}")
            run(["gdalwarp", "-t_srs", "EPSG:4326", *te,
                 "-r", "bilinear", "-ot", "Float32",
                 "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES",
                 "-overwrite", str(vrt), str(geoid_out)])

        log("warping EGM2008 geoid -> WGS84 ellipsoid (EPSG:4326+3855 -> EPSG:4979)")
        run(["gdalwarp",
             "-s_srs", "EPSG:4326+3855",
             "-t_srs", "EPSG:4979",
             *te,
             "-r", "bilinear", "-ot", "Float32",
             "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES",
             "-overwrite", str(vrt), str(out)])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    log(f"\nwrote {out}  ({out.stat().st_size / 2**20:.1f} MiB)")
    log("verify the datum:  ocean should read NEGATIVE (the geoid undulation),")
    log("not ~0 -- if it reads ~0 the vertical transform did not apply.")


if __name__ == "__main__":
    main()
