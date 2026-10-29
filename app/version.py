"""The one version of the MemuAI bundle: MemuAI, MemuAI Proxy, MemuCode CLI and the installers.

Everything that shows or embeds a version reads it from here:

* ``build.py`` writes the Windows file-version resources of the three executables
  (``memuai-version.txt``, ``memuai-proxy-version.txt``, ``memucode-version.txt``)
  and hands it to the custom installer (``installer/build_setup.py``), which writes
  it into the Setup payload, the Setup exe version resource and, at install
  time, into "Installed apps" (``DisplayVersion``);
* MemuCode CLI (``app.cli.VERSION``, the banner, ``memucode version``, the MCP
  handshake) reads it from here too — there is no second version to forget;
* MemuAI shows it in Settings → About through ``GET /api/pg/version``;
* MemuAI Proxy reports it in ``/health`` and on the Home tab;
* the runtime discovery files (``memuai.json``, ``proxy.json``) carry it, so
  MemuAI can tell when it talks to a Proxy of another version.

To release: change ``APP_VERSION`` and ``RELEASE_DATE`` here, then run
``python build.py version`` (or a full build). ``tests/round15_test.py`` (OneVersion)
fails when any copy disagrees with this file.
"""
from __future__ import annotations

from typing import Dict, Tuple

APP_VERSION = "4.1"
RELEASE_DATE = "2026-10-28"

def version_tuple(version: str = APP_VERSION) -> Tuple[int, int, int, int]:
    """``"3.0"`` -> ``(3, 0, 0, 0)`` — the Windows four-part form."""
    parts = [int(part) for part in str(version).split(".") if part.strip().isdigit()][:4]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]

def windows_version(version: str = APP_VERSION) -> str:
    return ".".join(str(part) for part in version_tuple(version))

def about() -> Dict[str, str]:
    return {"version": APP_VERSION, "release_date": RELEASE_DATE, "windows_version": windows_version()}
