"""MemuCode CLI — the coding agent of MemuAI in a terminal.

The package reuses the MemuCode backend of the desktop app (sandboxed
commands, cached reads, checkpoints, skills, MCP, browser and computer use)
and talks to models through MemuAI Proxy or a directly configured API.
Entry points: ``memucode.py`` (source) and ``MemuCode.exe`` (frozen build).
"""
from __future__ import annotations

from ..version import APP_VERSION as VERSION      # one version for the whole bundle (app/version.py)

NAME = "MemuCode"
