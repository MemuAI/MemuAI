"""The MemuCode tool set."""
from __future__ import annotations

from .base import (KINDS, Prepared, Tool, ToolCall, ToolContext, ToolError, ToolRegistry, ToolResult, arg,
                   display_path, resolve_path, workspace_of)

def build_registry() -> ToolRegistry:
    from . import automation, connected, files, meta, shell, skills, web
    registry = ToolRegistry()
    files.register(registry)
    shell.register(registry)
    web.register(registry)
    meta.register(registry)
    skills.register(registry)
    automation.register(registry)
    connected.register(registry)
    return registry

__all__ = ["KINDS", "Prepared", "Tool", "ToolCall", "ToolContext", "ToolError", "ToolRegistry", "ToolResult",
           "arg", "build_registry", "display_path", "resolve_path", "workspace_of"]
