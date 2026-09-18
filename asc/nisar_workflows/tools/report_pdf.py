#!/usr/bin/env python3
"""
Print the four-way comparison report (HTML) to PDF with headless Chromium.

    /home/sharath/.venvs/report-pdf/bin/python tools/report_pdf.py [report.html] [out.pdf]

Uses the page's own @media print stylesheet (A4, light palette). Needs network for the
Google Fonts; falls back to the CSS fallback stacks if they do not load. Refuses to
overwrite an existing PDF unless --force is given.
"""

from __future__ import annotations

import sys
from pathlib import Path

import base64
import io
import re
import tempfile

from PIL import Image
from playwright.sync_api import sync_playwright

REP = Path("/home/sharath/isce3/case_studies/nepal_glof/comparison_v2/report")
PLATE = (0xE9, 0xED, 0xF0)      # --plate in print: transparent (masked) cells land on this


def jpeg_images(html: str, max_px: int = 1100, quality: int = 84) -> str:
    """Chromium stores WebP losslessly in PDFs (~45 MB here). Flatten each panel onto the
    plate colour and re-encode as JPEG, which Chromium embeds as-is (DCT)."""
    def sub(m):
        im = Image.open(io.BytesIO(base64.b64decode(m.group(1)))).convert("RGBA")
        bg = Image.new("RGB", im.size, PLATE)
        bg.paste(im, mask=im.split()[3])
        if max(bg.size) > max_px:
            bg.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        bg.save(buf, "JPEG", quality=quality, optimize=True, progressive=False)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return re.sub(r"data:image/webp;base64,([A-Za-z0-9+/=]+)", sub, html)


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    src = Path(args[0]) if args else REP / "nepal_glof_four_workflow_report.html"
    out = Path(args[1]) if len(args) > 1 else src.with_suffix(".pdf")
    if out.exists() and not force:
        raise SystemExit(f"{out} exists; pass --force to replace it")
    footer = ('<div style="width:100%;font:7.5pt Arial,sans-serif;color:#5A6773;padding:0 15mm;'
              'display:flex;justify-content:space-between">'
              '<span>Nepal GLOF four-workflow InSAR study · comparison v2</span>'
              '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(color_scheme="light", viewport={"width": 1200, "height": 1600})
        tmp = Path(tempfile.mkdtemp()) / "report_print.html"
        tmp.write_text(jpeg_images(src.read_text()))
        page.goto(tmp.as_uri(), wait_until="load", timeout=180_000)
        page.evaluate("""async () => {
            const imgs = [...document.images];
            imgs.forEach(i => { i.loading = 'eager'; });
            await Promise.all(imgs.map(i => i.decode().catch(() => null)));
            await document.fonts.ready;
        }""")
        fonts = page.evaluate("[...document.fonts].filter(f => f.status === 'loaded').map(f => f.family)")
        broken = page.evaluate("[...document.images].filter(i => !i.complete || i.naturalWidth === 0).length")
        page.emulate_media(media="print")
        page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True,
                 display_header_footer=True, header_template="<span></span>", footer_template=footer,
                 margin={"top": "16mm", "bottom": "18mm", "left": "15mm", "right": "15mm"})
        browser.close()
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB); fonts loaded: {sorted(set(fonts))}; broken images: {broken}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
