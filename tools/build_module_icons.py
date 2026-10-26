"""Round 40: ``app/module_builtins/<id>/icon.ico`` — the app icon of every built-in module, drawn from its
``icon.svg`` at EVERY frame size (16, 20, 24, 32, 40, 48, 64, 128, 256 px).

A 256 px picture squeezed to 16/24/32 px by Windows goes soft exactly where an app icon is seen most (taskbar,
title bar, Start, Explorer). Chromium rasterises the vector at each target size instead, then a light unsharp
mask at ≤ 48 px. ``module_apps.write_icon`` ships these frames as they are (module_icons.app_icon_source).

Needs Playwright Chromium (as build_memuai_icon.py). The results are committed; re-run after editing an
``icon.svg``:  ``python tools/build_module_icons.py [--chromium PATH] [id ...]``
"""
from __future__ import annotations

import argparse
import base64
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import module_icons  # noqa: E402

BUILTINS = ROOT / "app" / "module_builtins"

def render(page, svg: bytes, size: int):
    from PIL import Image, ImageFilter
    url = "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")
    page.set_viewport_size({"width": size, "height": size})
    page.set_content(f'<html><body style="margin:0;background:transparent"><img id="i" src="{url}" '
                     f'width="{size}" height="{size}" style="display:block"></body></html>')
    page.wait_for_function("document.getElementById('i').complete")
    shot = page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
    frame = Image.open(io.BytesIO(shot)).convert("RGBA")
    if size <= 48:
        r, g, b, a = frame.split()
        rgb = Image.merge("RGB", (r, g, b)).filter(ImageFilter.UnsharpMask(radius=0.5, percent=55, threshold=1))
        frame = Image.merge("RGBA", (*rgb.split(), a))
    return frame

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--chromium", default="")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    folders = [BUILTINS / i for i in args.ids] if args.ids else sorted(p.parent for p in BUILTINS.glob("*/icon.svg"))
    with sync_playwright() as p:
        options = {"args": ["--no-sandbox", "--force-color-profile=srgb"]}
        if args.chromium:
            options["executable_path"] = args.chromium
        browser = p.chromium.launch(**options)
        page = browser.new_page(device_scale_factor=1)
        for folder in folders:
            svg = (folder / "icon.svg").read_bytes()
            frames = {size: render(page, svg, size) for size in module_icons.ICO_SIZES}
            (folder / "icon.ico").write_bytes(module_icons.ico_bytes(frames))
            print(folder.name, "icon.ico", (folder / "icon.ico").stat().st_size, "bytes")
        browser.close()

if __name__ == "__main__":
    main()
