"""Round 30 (r30-design): extra «Внешний вид» preferences — corners, transparency, window material, motion
speed and character, how new messages appear, message text size and a higher-contrast mode.

The page side is ``pgstatic/appearance-plus.js`` (+ ``appearance-plus.css``): each value becomes a
``data-*`` attribute on <html>. ``pgdata`` merges ``DEFAULTS`` into its preference defaults and calls
``normalize`` so an unknown value never reaches the page.
"""
from __future__ import annotations

from typing import Any, Dict

CHOICES: Dict[str, tuple] = {
    "ui_radius": ("", "sharp", "round"),
    "ui_glass": ("", "light", "strong"),
    "ui_backdrop": ("none", "mica", "acrylic", "tabbed"),
    "ui_motion_speed": ("", "slow", "fast"),
    "ui_motion_feel": ("", "smooth", "springy"),
    "ui_msg_anim": ("", "fade", "pop", "slide", "none"),
    "ui_msg_size": ("", "sm", "lg", "xl"),
    "ui_contrast": ("", "more"),
}
DEFAULTS: Dict[str, Any] = {key: values[0] for key, values in CHOICES.items()}

from . import theme_editor as _theme_editor  # noqa: E402
DEFAULTS.update(_theme_editor.DEFAULTS)

def normalize(prefs: Dict[str, Any]) -> Dict[str, Any]:
    """In place: every extra key holds one of its allowed values."""
    for key, values in CHOICES.items():
        value = prefs.get(key, values[0])
        prefs[key] = value if isinstance(value, str) and value in values else values[0]
    _theme_editor.normalize_prefs(prefs)
    return prefs
