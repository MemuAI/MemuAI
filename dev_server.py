"""Dev playground server for module-system testing.

Serves the workspace UI on 127.0.0.1:8601 with an isolated data dir and the
real running Proxy (8318). Never touches the user's %APPDATA%\\MemuAI data.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / ".dev-data"
DATA.mkdir(exist_ok=True)
os.environ["MEMUAI_DATA_DIR"] = str(DATA)
os.environ.setdefault("MEMUAI_PROXY_URL", "http://127.0.0.1:8318")

import uvicorn  # noqa: E402
from app.playground_api import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8601
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
