#!/usr/bin/env python3
"""
Search ASF for NISAR products over an AOI and download them to a local directory.

    python tools/nisar_fetch.py --kml AOI.kml --level GUNW --start 2026-07-14 --end 2026-07-26 --search-only
    python tools/nisar_fetch.py --kml AOI.kml --level GUNW --start 2026-07-14 --end 2026-07-26 \
        --ref-date 20260714 --sec-date 20260726 --track 98 --frame 16 --out <dir>

Local counterpart of /home/sharath/nisar_downloader/nisar_downloader.py (which stages to GCS): same ASF
param endpoint, the same Earthdata session that keeps the Authorization header only on hops involving
urs.earthdata.nasa.gov, and HTTP Range resume from a .part file. Refuses to overwrite a complete file.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import requests.utils

ASF_URL = "https://api.daac.asf.alaska.edu/services/search/param"
EDL_HOST = "urs.earthdata.nasa.gov"
CHUNK = 8 * 1024 * 1024


class EarthdataSession(requests.Session):
    def rebuild_auth(self, prepared_request, response):
        headers = prepared_request.headers
        if "Authorization" not in headers:
            return
        a = requests.utils.urlparse(response.request.url).hostname
        b = requests.utils.urlparse(prepared_request.url).hostname
        if a == b or EDL_HOST in (a, b):
            return
        del headers["Authorization"]


def session():
    s = EarthdataSession()
    auth = requests.utils.get_netrc_auth(f"https://{EDL_HOST}")
    if not auth:
        raise SystemExit(f"no credentials for {EDL_HOST} in ~/.netrc")
    s.auth = auth
    return s


def kml_wkt(path):
    txt = Path(path).read_text()
    m = re.search(r"<coordinates>(.*?)</coordinates>", txt, re.S)
    pts = [tuple(map(float, p.split(",")[:2])) for p in m.group(1).split()]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return "POLYGON((" + ",".join(f"{x} {y}" for x, y in pts) + "))"


def search(s, level, start, end, wkt, retries=4):
    end_x = (datetime.datetime.strptime(end, "%Y-%m-%d") + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    params = {"platform": "NISAR", "processingLevel": level, "start": start, "end": end_x,
              "intersectsWith": wkt, "output": "json", "maxResults": 500}
    for k in range(retries):
        try:
            r = s.get(ASF_URL, params=params, timeout=120)
            r.raise_for_status()
            js = r.json()
            if isinstance(js, list):
                return js[0] if js and js[0] else []
        except Exception as e:                       # noqa: BLE001
            print(f"search attempt {k + 1} failed: {e}", flush=True)
        time.sleep(2 ** (k + 1))
    raise SystemExit("ASF search failed")


def remote_size(s, url):
    try:
        r = s.head(url, allow_redirects=True, timeout=120)
        return int(r.headers.get("content-length", 0)) or None
    except Exception:                                # noqa: BLE001
        return None


def download(s, url, out: Path):
    size = remote_size(s, url)
    if out.exists():
        if size is None or out.stat().st_size == size:
            print(f"exists, complete: {out}", flush=True)
            return True
        raise SystemExit(f"{out} exists with a different size ({out.stat().st_size} vs {size}); not overwriting")
    part = out.with_suffix(out.suffix + ".part")
    for attempt in range(6):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with s.get(url, stream=True, timeout=300, allow_redirects=True, headers=headers) as r:
                r.raise_for_status()
                mode = "ab" if have and r.status_code == 206 else "wb"
                got = have if mode == "ab" else 0
                last = time.time()
                with open(part, mode) as f:
                    for chunk in r.iter_content(CHUNK):
                        f.write(chunk)
                        got += len(chunk)
                        if time.time() - last > 30:
                            print(f"  {got / 1e9:.2f} / {(size or 0) / 1e9:.2f} GB", flush=True)
                            last = time.time()
            if size and part.stat().st_size != size:
                raise IOError(f"incomplete: {part.stat().st_size} of {size}")
            part.rename(out)
            print(f"downloaded {out} ({out.stat().st_size / 1e9:.2f} GB)", flush=True)
            return True
        except Exception as e:                       # noqa: BLE001
            print(f"download attempt {attempt + 1} failed: {e}; partial kept", flush=True)
            time.sleep(2 ** (attempt + 1))
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kml", required=True)
    ap.add_argument("--level", default="GUNW")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--ref-date"); ap.add_argument("--sec-date")
    ap.add_argument("--track", type=int); ap.add_argument("--frame", type=int)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--tier", choices=["PR", "UR"], default=None, help="keep only this product tier (provisional or urgent response)")
    ap.add_argument("--search-only", action="store_true")
    a = ap.parse_args()
    s = session()
    scenes = search(s, a.level, a.start, a.end, kml_wkt(a.kml))
    rows = []
    for sc in scenes:
        name = sc.get("fileName") or sc.get("granuleName") or ""
        dates = re.findall(r"(\d{8})T\d{6}", name)
        rows.append({"name": name, "url": sc.get("downloadUrl"), "track": sc.get("track") or sc.get("relativeOrbit"),
                     "frame": sc.get("frameNumber"), "dates": dates, "start": sc.get("startTime"), "stop": sc.get("stopTime"),
                     "direction": sc.get("flightDirection")})
    print(json.dumps(rows, indent=1), flush=True)
    if a.search_only:
        return 0
    pick = [r for r in rows if a.ref_date in r["name"] and a.sec_date in r["name"]
            and (a.track is None or f"_{a.track:03d}_" in r["name"]) and (a.frame is None or f"_{a.frame:03d}_" in r["name"])
            and (a.tier is None or f"_{a.tier}_" in r["name"])]
    if not pick:
        raise SystemExit("no granule matches the requested dates/track/frame")
    a.out.mkdir(parents=True, exist_ok=True)
    ok = True
    for r in pick:
        ok &= download(s, r["url"], a.out / Path(r["url"]).name)
    (a.out / "fetch_manifest.json").write_text(json.dumps({"query": vars(a) | {"out": str(a.out)}, "downloaded": pick}, indent=1, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
