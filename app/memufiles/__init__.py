"""MemuFiles — the fast file manager of the MemuAI suite (Round 35).

Built like MemuNote (``app/memunote``): an import-light stdlib HTTP backend
(:mod:`.server`) and a static frontend (``static/``) in a pywebview window
(:mod:`.desktop`). Heavy things (Pillow thumbnails, the AI client, the PC-wide
file index ``app.fsindex``) are imported on first use.

Service name used for Proxy requests: ``"memufiles"``.
"""
SERVICE = "memufiles"
APP_NAME = "MemuFiles"
