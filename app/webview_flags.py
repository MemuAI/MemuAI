"""Round 36 (perf-core): lighter WebView2 for the MemuAI window and its popups.

pywebview's EdgeChromium backend passes only ``--disable-features=ElasticOverscroll``. WebView2 reads extra browser
arguments from ``WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`` (the variable takes precedence over the creation
properties), so :func:`apply` sets it once, before the first window, with switches that only drop Edge services a
local app does not use — each of them costs memory or a process:

* ``SpareRendererForSitePerProcess`` — Chromium keeps one idle renderer process ready for the next navigation;
  MemuAI never navigates away from its page (~30–60 MB);
* ``BackForwardCache`` — keeps a left page alive in memory; MemuAI is a single page;
* ``msSmartScreenProtection``, ``OptimizationHints``, ``AutofillServerCommunication``, ``Translate``, ``MediaRouter``,
  ``msEdgeSidebarV2``, ``msWebOOUI`` — Edge cloud services / UI the window never shows;
* ``--disable-background-networking`` — no background requests to Microsoft services.

``ElasticOverscroll`` (pywebview's own switch) and a remote-debugging port, when configured, are kept. A variable the
user (or a policy) already set wins: it is not touched. ``MEMUAI_WEBVIEW_LEAN=0`` turns this off.
Not verified on Windows in round 36 (no Windows in the build sandbox) — the switches are documented Chromium/Edge
feature names; an unknown feature name is ignored by Chromium, never an error.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

ENV = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
DISABLED_FEATURES = ("ElasticOverscroll", "SpareRendererForSitePerProcess", "BackForwardCache",
                     "msSmartScreenProtection", "OptimizationHints", "AutofillServerCommunication",
                     "Translate", "MediaRouter", "msEdgeSidebarV2", "msWebOOUI")
SWITCHES = ("--disable-background-networking",)


def arguments(debug_port: Optional[int] = None) -> str:
    args = ["--disable-features=" + ",".join(DISABLED_FEATURES), *SWITCHES]
    if debug_port:
        args.append(f"--remote-debugging-port={int(debug_port)}")
    return " ".join(args)


def apply(platform: str = sys.platform) -> bool:
    """Set the variable for this process (all WebView2 windows of MemuAI share one environment). True if set."""
    if platform != "win32" or os.environ.get(ENV) or os.environ.get("MEMUAI_WEBVIEW_LEAN", "1") == "0":
        return False
    port = None
    try:
        import webview
        port = webview.settings.get("REMOTE_DEBUGGING_PORT")
    except Exception:
        port = None
    os.environ[ENV] = arguments(port)
    return True
