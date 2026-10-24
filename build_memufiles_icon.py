"""Generate the MemuFiles icon (Round 35): SVG source → memufiles-icon.png / .ico and the static copies.

Design «Folder»: the MemuAI tile (rounded square with soft light in the upper-left
corner, the same proportions as ``build_memunote_icon.py``) in a cool
teal → indigo gradient, a warm folder in front, and the MemuAI four-point star
in the MemuAI brand gradient (terracotta → plum) on the folder. Small sizes
(16–40 px) use a bolder drawing.

Outputs (committed, so the build machine needs nothing extra)::

    memufiles-icon.png                       512 px
    memufiles-icon.ico                       16…256 px (MemuFiles.exe, shortcuts, installer)
    app/memufiles/static/memufiles.svg       in-app logo (full drawing)
    app/memufiles/static/memufiles-small.svg favicon / tab bar logo
    app/memufiles/static/memufiles-icon.png  256 px
    app/pgstatic/memufiles-icon.svg          MemuAI → Settings → «MemuFiles»

Rendering uses Playwright Chromium (``MEMU_TEST_CHROMIUM`` or the bundled browser):
``python build_memufiles_icon.py``. Only needed when the design changes.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

from build_memunote_icon import star

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "app" / "memufiles" / "static"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SMALL_MAX = 40


def svg(small: bool = False) -> str:
    inset, radius = (16, 124) if small else (28, 118)
    side = 512 - 2 * inset
    if small:
        fx, fy, fw, fh = 84, 128, 344, 268
    else:
        fx, fy, fw, fh = 100, 140, 312, 244
    tab_w, tab_h = fw * 0.42, 44 if small else 38
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">',
        '<defs>',
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#3fb5a9"/><stop offset="1" stop-color="#4b55c9"/></linearGradient>',
        '<linearGradient id="brand" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#ec8260"/><stop offset="1" stop-color="#9654c4"/></linearGradient>',
        '<linearGradient id="back" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#f2b14f"/><stop offset="1" stop-color="#e09a35"/></linearGradient>',
        '<linearGradient id="front" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#ffe2a6"/><stop offset="1" stop-color="#ffc861"/></linearGradient>',
        '<radialGradient id="light" cx="0.22" cy="0.12" r="0.62">'
        '<stop offset="0" stop-color="#d9fff8" stop-opacity=".5"/>'
        '<stop offset="1" stop-color="#d9fff8" stop-opacity="0"/></radialGradient>',
        '<filter id="shadow" x="-20%" y="-20%" width="140%" height="160%">'
        '<feDropShadow dx="0" dy="12" stdDeviation="14" flood-color="#1c2266" flood-opacity=".35"/></filter>',
        '</defs>',
        f'<rect x="{inset}" y="{inset}" width="{side}" height="{side}" rx="{radius}" fill="url(#bg)"/>',
        f'<rect x="{inset}" y="{inset}" width="{side}" height="{side}" rx="{radius}" fill="url(#light)"/>',
        '<g filter="url(#shadow)">',
        # back of the folder with its tab
        f'<path d="M{fx + 26} {fy - tab_h} h{tab_w - 30} q14 0 24 12 l20 {tab_h - 12} h{fw - tab_w - 66} '
        f'q26 0 26 26 v{fh - 30} h-{fw} v-{fh + tab_h - 30} q0 -26 26 -26 z" fill="url(#back)"/>',
        # front flap
        f'<rect x="{fx}" y="{fy + 26}" width="{fw}" height="{fh - 26}" rx="30" fill="url(#front)"/>',
        '</g>',
    ]
    r = 64 if small else 54
    parts.append(f'<path d="{star(fx + fw / 2, fy + 26 + (fh - 26) / 2 + 4, r)}" fill="url(#brand)"/>')
    parts.append("</svg>")
    return "".join(parts)


def render(markup: str, size: int = 512) -> bytes:
    from playwright.sync_api import sync_playwright
    executable = os.environ.get("MEMU_TEST_CHROMIUM") or None
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": size, "height": size})
            sized = markup.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)
            page.set_content(f'<html><body style="margin:0;background:transparent">{sized}</body></html>')
            return page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
        finally:
            browser.close()


def build() -> None:
    from PIL import Image
    full_svg, small_svg = svg(False), svg(True)
    (STATIC / "memufiles.svg").write_text(full_svg + "\n", encoding="utf-8")
    (STATIC / "memufiles-small.svg").write_text(small_svg + "\n", encoding="utf-8")
    (ROOT / "app" / "pgstatic" / "memufiles-icon.svg").write_text(full_svg + "\n", encoding="utf-8")
    full = Image.open(io.BytesIO(render(full_svg))).convert("RGBA")
    small = Image.open(io.BytesIO(render(small_svg))).convert("RGBA")
    full.save(ROOT / "memufiles-icon.png", "PNG", optimize=True)
    full.resize((256, 256), Image.LANCZOS).save(STATIC / "memufiles-icon.png", "PNG", optimize=True)
    frames = {size: (small if size <= SMALL_MAX else full).resize((size, size), Image.LANCZOS) for size in ICO_SIZES}
    from build_memuai_icon import write_ico
    write_ico(frames, ROOT / "memufiles-icon.ico")


if __name__ == "__main__":
    build()
