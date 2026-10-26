"""Round 36 (perf-core-2): how long the hidden Quick Access window keeps its WebView2 renderer.

Every pywebview window is a separate WebView2 renderer process (tens of MB).
Before round 36 the Quick Access popup and its screen layer were created
hidden at MemuAI start and kept for the whole session, even on days when
Alt+Space was never pressed.

``quick_ask_warm`` (Settings → Quick Access):

* ``always`` — as before: ready at start, never released (instant Alt+Space).
* ``auto``   — default: ready at start (instant), released after
  ``AUTO_RELEASE_MIN`` minutes hidden; the next Alt+Space creates it again
  (about a second once) and it stays warm again.
* ``never``  — created on the first Alt+Space, released ``SAVE_RELEASE_MIN``
  minutes after it was hidden (least memory, the first opening is slower).

Pinned or visible windows are never released.  NOT verified on Windows.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

CHOICES = ("always", "auto", "never")
DEFAULT = "auto"
AUTO_RELEASE_MIN = 30
SAVE_RELEASE_MIN = 2

CHECK_EVERY = 60.0

def policy(prefs: Optional[Dict[str, Any]]) -> str:
    value = str((prefs or {}).get("quick_ask_warm") or DEFAULT)
    return value if value in CHOICES else DEFAULT

def warm_at_start(prefs: Optional[Dict[str, Any]]) -> bool:
    return policy(prefs) != "never"

def release_after(prefs: Optional[Dict[str, Any]]) -> Optional[float]:
    """Seconds a hidden window may stay before its renderer is released; ``None`` = keep."""
    mode = policy(prefs)
    if mode == "always":
        return None
    return float((AUTO_RELEASE_MIN if mode == "auto" else SAVE_RELEASE_MIN) * 60)

def should_release(prefs: Optional[Dict[str, Any]], *, has_window: bool, visible: bool, pinned: bool,
                   hidden_for: float, busy: bool = False) -> bool:
    limit = release_after(prefs)
    if limit is None or not has_window or visible or pinned or busy:
        return False
    return hidden_for >= limit
