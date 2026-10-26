"""Human-friendly times: "5 мин назад" / "5m ago"."""
from __future__ import annotations

import datetime as _dt
import time

from .i18n import tr

def ago(stamp: float) -> str:
    seconds = max(0.0, time.time() - float(stamp or 0))
    if seconds < 90:
        return tr("только что", "just now")
    if seconds < 3600:
        return tr(f"{int(seconds // 60)} мин назад", f"{int(seconds // 60)}m ago")
    if seconds < 86400:
        return tr(f"{int(seconds // 3600)} ч назад", f"{int(seconds // 3600)}h ago")
    if seconds < 86400 * 7:
        return tr(f"{int(seconds // 86400)} дн назад", f"{int(seconds // 86400)}d ago")
    return _dt.datetime.fromtimestamp(float(stamp)).strftime("%d.%m.%Y")

def duration(seconds: float) -> str:
    if 0 < seconds < 9.95:
        return f"{seconds:.1f}s"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
