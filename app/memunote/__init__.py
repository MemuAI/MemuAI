"""MemuNote — the light AI notepad of the MemuAI suite (Round 29).

The package is deliberately import-light: ``memunote.py`` starts the window
after importing only :mod:`app.memunote.paths`, :mod:`.server` and
:mod:`.desktop`; document formats (DOCX/ODT/RTF), the AI client and web search
are imported on first use.

Service name used for MemuAI memory tags and Proxy requests: ``"memunote"``.
"""
SERVICE = "memunote"
APP_NAME = "MemuNote"
