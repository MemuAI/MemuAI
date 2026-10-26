"""Generate the MemuNote icon (Round 30): SVG source → memunote-icon.png / .ico and the static copies.

Design «Notepad»: the MemuAI tile (rounded square, soft light in the upper-left
corner, same proportions as ``build_memuai_icon.py``) in a warmer amber → rose
gradient, a white notepad page with an amber header band, text lines and the
MemuAI four-point star in the MemuAI brand gradient (terracotta → plum).

Small sizes (16–40 px) use their own drawing: a bigger page, one bold line and a
bigger star — the full drawing turns to mush below 48 px.

Outputs (committed, so the build machine needs nothing extra)::

    memunote-icon.png                      512 px  (window icon on GTK/Qt, docs)
    memunote-icon.ico                      16…256 px (MemuNote.exe, shortcuts, installer)
    app/memunote/static/memunote.svg       favicon / in-app logo (full drawing)
    app/memunote/static/memunote-small.svg favicon for 16–32 px tabs
    app/memunote/static/memunote-icon.png  256 px
    app/pgstatic/memunote-icon.svg         MemuAI → Settings → «MemuNote»

Rendering uses Playwright Chromium (``MEMU_TEST_CHROMIUM`` or the bundled
browser): ``python build_memunote_icon.py``. Only needed when the design changes.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "app" / "memunote" / "static"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SMALL_MAX = 40

def star(cx: float, cy: float, r: float, inner: float = 0.22) -> str:
    """The MemuAI four-point star with concave sides (quadratic curves)."""
    k = r * inner
    points = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    controls = [(cx + k, cy - k), (cx + k, cy + k), (cx - k, cy + k), (cx - k, cy - k)]
    path = f"M{cx:g},{cy - r:g} "
    for i in range(4):
        nx, ny = points[(i + 1) % 4]
        path += f"Q{controls[i][0]:.1f},{controls[i][1]:.1f} {nx:g},{ny:g} "
    return path + "Z"

def svg(small: bool = False) -> str:
    inset, radius = (16, 124) if small else (28, 118)
    px, py, pw, ph, pr = (104, 92, 304, 332, 52) if small else (120, 100, 272, 316, 44)
    band = 92 if small else 70
    side = 512 - 2 * inset
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">',
        '<defs>',
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#f4a65f"/><stop offset="1" stop-color="#c9556f"/></linearGradient>',
        '<linearGradient id="brand" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#ec8260"/><stop offset="1" stop-color="#9654c4"/></linearGradient>',
        '<linearGradient id="band" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#ffd28f"/><stop offset="1" stop-color="#ffbf73"/></linearGradient>',
        '<radialGradient id="light" cx="0.22" cy="0.12" r="0.62">'
        '<stop offset="0" stop-color="#ffe2c4" stop-opacity=".55"/>'
        '<stop offset="1" stop-color="#ffe2c4" stop-opacity="0"/></radialGradient>',
        '<filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">'
        '<feDropShadow dx="0" dy="10" stdDeviation="14" flood-color="#5a1f2a" flood-opacity=".3"/></filter>',
        f'<clipPath id="page"><rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="{pr}"/></clipPath>',
        '</defs>',
        f'<rect x="{inset}" y="{inset}" width="{side}" height="{side}" rx="{radius}" fill="url(#bg)"/>',
        f'<rect x="{inset}" y="{inset}" width="{side}" height="{side}" rx="{radius}" fill="url(#light)"/>',
        f'<g filter="url(#shadow)"><rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="{pr}" fill="#fff"/></g>',
        f'<rect x="{px}" y="{py}" width="{pw}" height="{band}" fill="url(#band)" clip-path="url(#page)"/>',
    ]
    if small:
        parts += [f'<rect x="{px + 46}" y="{py + band + 52}" width="{pw - 92}" height="40" rx="20" fill="#e49a8a"/>',
                  f'<rect x="{px + 46}" y="{py + band + 122}" width="{pw - 210}" height="40" rx="20" fill="#e49a8a"/>',
                  f'<path d="{star(px + pw - 92, py + ph - 82, 62)}" fill="url(#brand)"/>']
    else:
        parts += [f'<rect x="{px + 40}" y="{py + band + 46}" width="{pw - 80}" height="22" rx="11" fill="#eab0a2"/>',
                  f'<rect x="{px + 40}" y="{py + band + 96}" width="{pw - 140}" height="22" rx="11" fill="#eab0a2"/>',
                  f'<path d="{star(px + pw - 78, py + ph - 74, 50)}" fill="url(#brand)"/>']
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
    (STATIC / "memunote.svg").write_text(full_svg + "\n", encoding="utf-8")
    (STATIC / "memunote-small.svg").write_text(small_svg + "\n", encoding="utf-8")
    (ROOT / "app" / "pgstatic" / "memunote-icon.svg").write_text(full_svg + "\n", encoding="utf-8")
    full = Image.open(io.BytesIO(render(full_svg))).convert("RGBA")
    small = Image.open(io.BytesIO(render(small_svg))).convert("RGBA")
    full.save(ROOT / "memunote-icon.png", "PNG", optimize=True)
    full.resize((256, 256), Image.LANCZOS).save(STATIC / "memunote-icon.png", "PNG", optimize=True)
    frames = {size: (small if size <= SMALL_MAX else full).resize((size, size), Image.LANCZOS)
              for size in ICO_SIZES}

    from build_memuai_icon import write_ico
    write_ico(frames, ROOT / "memunote-icon.ico")

if __name__ == "__main__":
    build()
