"""Generate the MemuAI Proxy icon (Round 33) with the kit of ``build_memuai_icon.py``.

Design «Шлюз» / "Gateway": the Proxy keeps its round violet badge (users know
the purple circle), now in a violet → indigo gradient with the family's warm
light, and the plain ring is replaced by API brackets ‹ › with the MemuAI star
between them — requests pass through the gateway to the models. The star is
warm (peach → coral) and cut like the MemuAI crystal on large sizes, so the
three icons read as one family: MemuAI = speech bubble, MemuNote = square
tile, Proxy = circle.

Each size has its own pixel grid (see ``variant_for``): 16/20 px — 2-px
brackets and a bigger star; 24–40 px — 32-px grid; 48 px+ — master drawing.

Outputs (committed)::

    icon.png                       512 px  Proxy tray/window icon, MemuAI fallback
    icon.ico                       16…256 px (MemuAIProxy.exe, shortcuts, Proxy Setup)
    app/static/icon.png            256 px  favicon of the Proxy panel
    app/static/proxy-icon.svg      master drawing (docs)
    installer/ui/assets/logo-proxy.png  128 px (Proxy installer window)

``python build_proxy_icon.py`` (Playwright Chromium, only when the design changes).
"""
from __future__ import annotations

from pathlib import Path

from build_memuai_icon import LIGHT, build_set, crystal_star, f, star_path, svg_doc

ROOT = Path(__file__).resolve().parent

#: The brackets' white and the warm star — also used by the Proxy panel's logo mark.
STAR_WARM = ("#ffd3a6", "#ff8a6e")


def _defs(grid: float) -> str:
    return ('<linearGradient id="gate" gradientUnits="userSpaceOnUse" '
            f'x1="0" y1="0" x2="{f(grid)}" y2="{f(grid)}">'
            '<stop offset="0" stop-color="#9a72ff"/><stop offset="1" stop-color="#4632c8"/></linearGradient>'
            '<linearGradient id="warm" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{STAR_WARM[0]}"/><stop offset="1" stop-color="{STAR_WARM[1]}"/>'
            '</linearGradient>' + LIGHT +
            '<filter id="soft" x="-40%" y="-40%" width="180%" height="190%">'
            '<feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#1c0f5a" flood-opacity=".32"/></filter>')


def _brackets(cx: float, cy: float, half_h: float, inner_x: float, outer_x: float, width: float,
              extra: str = "") -> str:
    """‹ › as two round-capped polylines (inner_x: distance of the arms' ends from the centre)."""
    style = (f'fill="none" stroke="#fff" stroke-width="{f(width)}" stroke-linecap="round" '
             f'stroke-linejoin="round"{extra}')
    left = f"M{f(cx - inner_x)},{f(cy - half_h)} L{f(cx - outer_x)},{f(cy)} L{f(cx - inner_x)},{f(cy + half_h)}"
    right = f"M{f(cx + inner_x)},{f(cy - half_h)} L{f(cx + outer_x)},{f(cy)} L{f(cx + inner_x)},{f(cy + half_h)}"
    return f'<path d="{left}" {style}/><path d="{right}" {style}/>'


def proxy_svg(variant: str = "full") -> str:
    """MemuAI Proxy icon drawing for a variant (``tiny`` / ``small`` / ``full``)."""
    if variant == "tiny":
        grid = 16
        body = ('<circle cx="8" cy="8" r="8" fill="url(#gate)"/><circle cx="8" cy="8" r="8" fill="url(#light)"/>'
                + _brackets(8, 8, 3.6, 3.1, 5.6, 1.9)
                + f'<path d="{star_path(8, 8, 2.9, 0.26)}" fill="url(#warm)"/>')
        return svg_doc(grid, _defs(grid), body)
    if variant == "small":
        grid = 32
        body = ('<circle cx="16" cy="16" r="16" fill="url(#gate)"/>'
                '<circle cx="16" cy="16" r="16" fill="url(#light)"/>'
                + _brackets(16, 16, 7, 6, 11.2, 3.4)
                + f'<path d="{star_path(16, 16, 5.6, 0.24)}" fill="url(#warm)"/>')
        return svg_doc(grid, _defs(grid), body)
    grid = 512
    body = ('<circle cx="256" cy="256" r="232" fill="url(#gate)"/>'
            '<circle cx="256" cy="256" r="232" fill="url(#light)"/>'
            f'<g filter="url(#soft)">{_brackets(256, 256, 96, 82, 166, 44)}'
            f'{crystal_star(256, 256, 84, STAR_WARM[0], STAR_WARM[1], 0.22)}</g>')
    return svg_doc(grid, _defs(grid), body)


def build() -> None:
    (ROOT / "app" / "static" / "proxy-icon.svg").write_text(proxy_svg("full") + "\n", encoding="utf-8")
    build_set(proxy_svg,
              png_out=[(ROOT / "icon.png", 512), (ROOT / "app" / "static" / "icon.png", 256),
                       (ROOT / "installer" / "ui" / "assets" / "logo-proxy.png", 128)],
              ico_out=ROOT / "icon.ico")


if __name__ == "__main__":
    build()
