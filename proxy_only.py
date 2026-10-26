"""Standalone MemuAI Proxy entry point with its own local data directory."""
from __future__ import annotations

import os
import sys
from pathlib import Path

def configure_edition() -> None:
    os.environ["MEMUAI_PROXY_ONLY"] = "1"
    if not os.environ.get("MEMUAI_DATA_DIR") and not os.environ.get("OMNIHUB_DATA_DIR"):
        appdata = Path(os.environ.get("APPDATA") or Path.home())
        os.environ["MEMUAI_DATA_DIR"] = str(appdata / "MemuAIProxy")

if __name__ == "__main__":
    configure_edition()
    from main import main, _report_startup_crash

    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        pass
    except BaseException as exc:
        _report_startup_crash(exc)
        sys.exit(1)
