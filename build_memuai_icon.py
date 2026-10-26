"""Generate the MemuAI icon (Round 33) and hold the small icon kit shared with ``build_proxy_icon.py``.

Design «Говорящий кристалл» / "Speaking crystal": the MemuAI tile becomes a
speech bubble (a rounded square with a tail at the bottom left — its own
silhouette next to the square MemuNote tile and the round Proxy badge) in the
brand gradient (terracotta → rose → plum, the --accent / --accent-2 pair).
Inside is the MemuAI four-point star cut like a crystal: lit facets white, the
shaded ones pale lilac (the same crystal as the installer's WebGL scene).

Every size is drawn on its own pixel grid instead of being shrunk from 512 px:

* 16 / 20 px — ``tiny``: the bubble fills the whole square, solid white star,
  edges on whole pixels (a 2-px tail that still reads on a 16-px taskbar);
* 24 – 40 px — ``small``: the same proportions on a 32-px grid, solid star;
* 48 px and up — ``full``: the master drawing with facets, soft shadow and light.

Outputs (committed, so the build machine needs nothing extra)::

    memuai-icon.png                 512 px  window/tray icon on non-Windows, /icon.png, docs
    memuai-icon.ico                 16, 20, 24, 32, 40, 48, 64, 128, 256 (MemuAI.exe, MemuCode.exe,
                                    shortcuts, Setup, tray on Windows)
    app/pgstatic/memuai-icon.svg    master drawing (Settings → About, docs)
    app/tgapp/icon.svg              Telegram Mini App favicon (small drawing)
    installer/ui/assets/logo.png    128 px (installer window)

Rendering uses Playwright Chromium (``MEMU_TEST_CHROMIUM`` or the bundled one):
``python build_memuai_icon.py``. Only needed when the design changes; build.py
never re-runs it while the committed files exist.
"""
from __future__ import annotations

import io
import math
import os
from pathlib import Path
from typing import Callable, Dict, Iterable, List

ROOT = Path(__file__).resolve().parent
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

LIGHT = ('<radialGradient id="light" cx="0.22" cy="0.12" r="0.7">'
         '<stop offset="0" stop-color="#fff1e4" stop-opacity=".42"/>'
         '<stop offset="1" stop-color="#fff1e4" stop-opacity="0"/></radialGradient>')

def variant_for(size: int) -> str:
    """Which drawing a pixel size uses."""
    if size <= 20:
        return "tiny"
    if size <= 40:
        return "small"
    return "full"

def f(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")

def star_path(cx: float, cy: float, r: float, inner: float = 0.22) -> str:
    """The MemuAI four-point star with concave sides (quadratic curves)."""
    k = r * inner
    tips = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    ctrl = [(cx + k, cy - k), (cx + k, cy + k), (cx - k, cy + k), (cx - k, cy - k)]
    d = f"M{f(cx)},{f(cy - r)} "
    for i in range(4):
        nx, ny = tips[(i + 1) % 4]
        d += f"Q{f(ctrl[i][0])},{f(ctrl[i][1])} {f(nx)},{f(ny)} "
    return d + "Z"

def crystal_star(cx: float, cy: float, r: float, lit: str, shade: str, inner: float = 0.22,
                 extra: str = "") -> str:
    """The same star split into eight facets: arm halves facing the light are ``lit``.

    Each concave side (tip → control → next tip) is split at its middle with
    de Casteljau, so the facets keep the exact outline of :func:`star_path`.
    """
    k = r * inner
    tips = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    ctrl = [(cx + k, cy - k), (cx + k, cy + k), (cx - k, cy + k), (cx - k, cy - k)]

    lit_halves = {(3, "a"), (3, "b"), (0, "b"), (2, "a")}

    out: List[str] = [f'<path d="{star_path(cx, cy, r, inner)}" fill="{shade}"{extra}/>']
    for i in range(4):
        p0, p1, p2 = tips[i], ctrl[i], tips[(i + 1) % 4]
        mid = ((p0[0] + 2 * p1[0] + p2[0]) / 4, (p0[1] + 2 * p1[1] + p2[1]) / 4)
        c_a = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
        c_b = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        half_a = (f"M{f(cx)},{f(cy)} L{f(p0[0])},{f(p0[1])} Q{f(c_a[0])},{f(c_a[1])} "
                  f"{f(mid[0])},{f(mid[1])} Z")
        half_b = (f"M{f(cx)},{f(cy)} L{f(mid[0])},{f(mid[1])} Q{f(c_b[0])},{f(c_b[1])} "
                  f"{f(p2[0])},{f(p2[1])} Z")
        if (i, "a") in lit_halves:
            out.append(f'<path d="{half_a}" fill="{lit}"{extra}/>')
        if (i, "b") in lit_halves:
            out.append(f'<path d="{half_b}" fill="{lit}"{extra}/>')
    return "".join(out)

def svg_doc(grid: float, defs: str, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {f(grid)} {f(grid)}">'
            f"<defs>{defs}</defs>{body}</svg>")

def render_many(markups: Dict[str, tuple]) -> Dict[str, bytes]:
    """Render ``{key: (svg, size)}`` to transparent PNG bytes in one browser session."""
    from playwright.sync_api import sync_playwright
    executable = os.environ.get("MEMU_TEST_CHROMIUM") or None
    if executable is None and os.path.exists("/usr/bin/chromium"):
        executable = "/usr/bin/chromium"
    out: Dict[str, bytes] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 512, "height": 512}, device_scale_factor=1)
            for key, (markup, size) in markups.items():
                page.set_viewport_size({"width": max(size, 16), "height": max(size, 16)})
                sized = markup.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)
                page.set_content('<html><body style="margin:0;background:transparent">'
                                 f'<div style="width:{size}px;height:{size}px;line-height:0">{sized}</div>'
                                 "</body></html>")
                out[key] = page.screenshot(omit_background=True,
                                           clip={"x": 0, "y": 0, "width": size, "height": size})
        finally:
            browser.close()
    return out

def write_ico(frames: Dict[int, "object"], path: Path) -> None:
    """Write a multi-size .ico: 32-bit BMP entries below 256 px, PNG for 256 px.

    Windows reads both, but BMP entries are what the shell, the icon cache,
    ``LoadImage`` and .NET ``System.Drawing.Icon`` handle everywhere (PNG
    entries at small sizes have shown up blank in some of them). Pillow writes
    every entry as PNG, so the file is assembled here.
    """
    import struct
    entries: List[tuple] = []
    for size in sorted(frames):
        image = frames[size].convert("RGBA")
        if size >= 256:
            buffer = io.BytesIO()
            image.save(buffer, "PNG", optimize=True)
            data = buffer.getvalue()
        else:
            width, height = image.size
            header = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0, 0, 0, 0, 0, 0)
            rows = []
            pixels = image.tobytes("raw", "BGRA")
            stride = width * 4
            for y in range(height - 1, -1, -1):
                rows.append(pixels[y * stride:(y + 1) * stride])
            mask_stride = ((width + 31) // 32) * 4
            alpha = image.getchannel("A").tobytes()
            mask_rows = []
            for y in range(height - 1, -1, -1):
                bits = bytearray(mask_stride)
                for x in range(width):
                    if alpha[y * width + x] == 0:
                        bits[x // 8] |= 0x80 >> (x % 8)
                mask_rows.append(bytes(bits))
            data = header + b"".join(rows) + b"".join(mask_rows)
        entries.append((size, data))
    offset = 6 + 16 * len(entries)
    directory = struct.pack("<HHH", 0, 1, len(entries))
    blobs = []
    for size, data in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs.append(data)
    Path(path).write_bytes(directory + b"".join(blobs))

def build_set(draw: Callable[[str], str], png_out: Iterable[tuple], ico_out: Path,
              extra_png: Iterable[tuple] = ()) -> Dict[int, "object"]:
    """Render every ICO frame from its own drawing and write the PNG/ICO files.

    ``png_out``: ``(path, size)`` rendered from the full drawing;
    ``extra_png``: ``(path, size, variant)`` for other drawings.
    """
    from PIL import Image
    jobs = {f"ico{size}": (draw(variant_for(size)), size) for size in ICO_SIZES}
    for index, (path, size) in enumerate(png_out):
        jobs[f"png{index}"] = (draw("full"), size)
    for index, (path, size, variant) in enumerate(extra_png):
        jobs[f"extra{index}"] = (draw(variant), size)
    rendered = render_many(jobs)
    frames = {size: Image.open(io.BytesIO(rendered[f"ico{size}"])).convert("RGBA") for size in ICO_SIZES}
    for index, (path, size) in enumerate(png_out):
        Image.open(io.BytesIO(rendered[f"png{index}"])).convert("RGBA").save(path, "PNG", optimize=True)
    for index, (path, size, variant) in enumerate(extra_png):
        Image.open(io.BytesIO(rendered[f"extra{index}"])).convert("RGBA").save(path, "PNG", optimize=True)
    write_ico(frames, ico_out)
    return frames

def _brand_defs(grid: float) -> str:
    return ('<linearGradient id="brand" gradientUnits="userSpaceOnUse" '
            f'x1="0" y1="0" x2="{f(grid)}" y2="{f(grid)}">'
            '<stop offset="0" stop-color="#f08c62"/><stop offset=".52" stop-color="#cf6492"/>'
            '<stop offset="1" stop-color="#8752cf"/></linearGradient>' + LIGHT +
            '<filter id="soft" x="-30%" y="-30%" width="160%" height="170%">'
            '<feDropShadow dx="0" dy="9" stdDeviation="12" flood-color="#4a1840" flood-opacity=".28"/></filter>')

def bubble_path(grid: float, left: float, top: float, right: float, body_bottom: float,
                radius: float, tail_tip: tuple, tail_root: tuple, tip_round: float = 0.0) -> str:
    """Rounded body plus a tail at the bottom left, one closed outline.

    ``tail_root`` = (x where the tail leaves the bottom edge, y where it leaves
    the left edge); ``tail_tip`` = the point of the tail, rounded by ``tip_round``.
    """
    r = radius
    root_x, root_y = tail_root
    tip_x, tip_y = tail_tip
    if tip_round > 0:
        def toward(px: float, py: float) -> tuple:
            length = math.hypot(px - tip_x, py - tip_y) or 1.0
            return tip_x + (px - tip_x) * tip_round / length, tip_y + (py - tip_y) * tip_round / length
        (ax, ay), (bx, by) = toward(root_x, body_bottom), toward(left, root_y)
        tip = f"L{f(ax)},{f(ay)} Q{f(tip_x)},{f(tip_y)} {f(bx)},{f(by)} "
    else:
        tip = f"L{f(tip_x)},{f(tip_y)} "
    return (f"M{f(left + r)},{f(top)} H{f(right - r)} "
            f"Q{f(right)},{f(top)} {f(right)},{f(top + r)} V{f(body_bottom - r)} "
            f"Q{f(right)},{f(body_bottom)} {f(right - r)},{f(body_bottom)} H{f(root_x)} "
            f"{tip}L{f(left)},{f(root_y)} V{f(top + r)} "
            f"Q{f(left)},{f(top)} {f(left + r)},{f(top)} Z")

def memuai_svg(variant: str = "full") -> str:
    """MemuAI icon drawing for a variant (``tiny`` / ``small`` / ``full``)."""
    if variant == "tiny":

        grid = 16
        shape = bubble_path(grid, 0, 0, 16, 13, 4, (1.2, 16), (6.5, 9.5))
        body = (f'<path d="{shape}" fill="url(#brand)"/><path d="{shape}" fill="url(#light)"/>'
                f'<path d="{star_path(8, 6.5, 5.1, 0.24)}" fill="#fff"/>')
        return svg_doc(grid, _brand_defs(grid), body)
    if variant == "small":
        grid = 32
        shape = bubble_path(grid, 0, 0, 32, 26, 8, (2.5, 31.5), (12.5, 19), 0.8)
        body = (f'<path d="{shape}" fill="url(#brand)"/><path d="{shape}" fill="url(#light)"/>'
                f'<path d="{star_path(16, 13, 9.8, 0.23)}" fill="#fff"/>')
        return svg_doc(grid, _brand_defs(grid), body)
    grid = 512
    shape = bubble_path(grid, 32, 32, 480, 416, 118, (62, 482), (200, 330), 26)
    body = (f'<path d="{shape}" fill="url(#brand)"/><path d="{shape}" fill="url(#light)"/>'
            f'<g filter="url(#soft)">{crystal_star(256, 224, 150, "#ffffff", "#efd2ff", 0.22)}</g>')
    return svg_doc(grid, _brand_defs(grid), body)

def build() -> None:
    (ROOT / "app" / "pgstatic" / "memuai-icon.svg").write_text(memuai_svg("full") + "\n", encoding="utf-8")
    (ROOT / "app" / "tgapp" / "icon.svg").write_text(memuai_svg("small") + "\n", encoding="utf-8")
    build_set(memuai_svg,
              png_out=[(ROOT / "memuai-icon.png", 512),
                       (ROOT / "installer" / "ui" / "assets" / "logo.png", 128)],
              ico_out=ROOT / "memuai-icon.ico")

if __name__ == "__main__":
    build()
