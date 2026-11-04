# -*- coding: utf-8 -*-
"""Colour parsing and conversions shared by the image, generator and converter utilities."""
from __future__ import annotations

import colorsys
import re
from typing import Tuple

NAMED = {
    "black": "000000", "white": "ffffff", "red": "ff0000", "green": "008000", "lime": "00ff00", "blue": "0000ff",
    "yellow": "ffff00", "cyan": "00ffff", "aqua": "00ffff", "magenta": "ff00ff", "fuchsia": "ff00ff", "gray": "808080",
    "grey": "808080", "silver": "c0c0c0", "maroon": "800000", "olive": "808000", "navy": "000080", "purple": "800080",
    "teal": "008080", "orange": "ffa500", "pink": "ffc0cb", "brown": "a52a2a", "gold": "ffd700", "indigo": "4b0082",
    "violet": "ee82ee", "coral": "ff7f50", "salmon": "fa8072", "tomato": "ff6347", "crimson": "dc143c", "khaki": "f0e68c",
    "beige": "f5f5dc", "ivory": "fffff0", "lavender": "e6e6fa", "turquoise": "40e0d0", "skyblue": "87ceeb",
    "steelblue": "4682b4", "royalblue": "4169e1", "slategray": "708090", "darkgray": "a9a9a9", "lightgray": "d3d3d3",
    "chocolate": "d2691e", "tan": "d2b48c", "mint": "98ff98", "transparent": "00000000",
    "черный": "000000", "чёрный": "000000", "белый": "ffffff", "красный": "ff0000", "зеленый": "008000",
    "зелёный": "008000", "синий": "0000ff", "желтый": "ffff00", "жёлтый": "ffff00", "голубой": "00bfff",
    "серый": "808080", "оранжевый": "ffa500", "розовый": "ffc0cb", "фиолетовый": "800080", "коричневый": "a52a2a",
    "прозрачный": "00000000",
}

def parse(value: str) -> Tuple[int, ...]:
    """``#rgb``, ``#rrggbb``, ``#rrggbbaa``, ``rgb(…)``, ``rgba(…)``, ``hsl(…)`` or a name → (r, g, b[, a])."""
    text = str(value or "").strip().lower()
    if not text:
        raise ValueError("empty colour")
    if text in NAMED:
        text = "#" + NAMED[text]
    m = re.fullmatch(r"#?([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})", text)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        parts = tuple(int(h[i:i + 2], 16) for i in range(0, len(h), 2))
        return parts
    m = re.fullmatch(r"rgba?\(\s*([\d.]+%?)[\s,]+([\d.]+%?)[\s,]+([\d.]+%?)(?:[\s,/]+([\d.]+%?))?\s*\)", text)
    if m:
        def channel(v: str) -> int:
            return round(float(v[:-1]) * 2.55) if v.endswith("%") else round(float(v))
        rgb = tuple(max(0, min(255, channel(v))) for v in m.groups()[:3])
        if m.group(4):
            a = m.group(4)
            alpha = float(a[:-1]) / 100 if a.endswith("%") else float(a)
            return rgb + (max(0, min(255, round(alpha * 255 if alpha <= 1 else alpha))),)
        return rgb
    m = re.fullmatch(r"hsla?\(\s*([\d.]+)(?:deg)?[\s,]+([\d.]+)%?[\s,]+([\d.]+)%?(?:[\s,/]+([\d.]+%?))?\s*\)", text)
    if m:
        h, s, l = float(m.group(1)) % 360 / 360, float(m.group(2)) / 100, float(m.group(3)) / 100
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return tuple(round(c * 255) for c in (r, g, b))
    m = re.fullmatch(r"(\d{1,3})[\s,;]+(\d{1,3})[\s,;]+(\d{1,3})", text)
    if m:
        return tuple(max(0, min(255, int(v))) for v in m.groups())
    raise ValueError(f"unknown colour {value!r}")

def to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02x}" for c in rgb[:3])

def to_hsl(rgb) -> Tuple[int, int, int]:
    r, g, b = (c / 255 for c in rgb[:3])
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return round(h * 360) % 360, round(s * 100), round(l * 100)

def to_hsv(rgb) -> Tuple[int, int, int]:
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb[:3]))
    return round(h * 360) % 360, round(s * 100), round(v * 100)

def to_cmyk(rgb) -> Tuple[int, int, int, int]:
    r, g, b = (c / 255 for c in rgb[:3])
    k = 1 - max(r, g, b)
    if k >= 1:
        return 0, 0, 0, 100
    return tuple(round((1 - x - k) / (1 - k) * 100) for x in (r, g, b)) + (round(k * 100),)

def from_hsl(h: float, s: float, l: float) -> Tuple[int, int, int]:
    """Hue in degrees, saturation and lightness in percent (as :func:`to_hsl` returns them)."""
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, max(0.0, min(1.0, l / 100)), max(0.0, min(1.0, s / 100)))
    return round(r * 255), round(g * 255), round(b * 255)

def luminance(rgb) -> float:
    def lin(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)

def nearest_name(rgb) -> str:
    best, name = 10 ** 9, ""
    for key, hexv in NAMED.items():
        if len(hexv) != 6 or not key.isascii():
            continue
        c = tuple(int(hexv[i:i + 2], 16) for i in (0, 2, 4))
        d = sum((x - y) ** 2 for x, y in zip(c, rgb[:3]))
        if d < best:
            best, name = d, key
    return name
