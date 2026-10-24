"""Screenshots of Settings → «Быстрый доступ» — kept as an entry point for old notes and scripts.

ROUND 23: the page was rebuilt on the settings kit (sections, sub-pages Команды / Голос / Компаньон, own endpoint),
so the Round 22 clicks no longer apply. This script now simply runs tests/quick_ask_settings_r23_shots.py:

    PYTHONUTF8=1 NO_PROXY='*' /workspace/work/venv/bin/python tests/quick_ask_settings_shots.py http://127.0.0.1:PORT OUT_DIR [--quick]

Against YOUR OWN dev server with a copy of the data dir (it changes that copy's preferences).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import quick_ask_settings_r23_shots as r23  # noqa: E402

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raise SystemExit(asyncio.run(r23.main(args[0].rstrip("/"), Path(args[1]), "--quick" in sys.argv)))
