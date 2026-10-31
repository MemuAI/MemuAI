"""Local bridge: the server-side AI reaches the user's computer only here.

See :mod:`app.local_bridge.policy` (what is allowed) and
:mod:`app.local_bridge.agent` (how it runs, with confirmation and a
journal).  The bridge is off by default and revocable in one click.
"""
from .agent import BridgeRequest, BridgeResult, BridgeService
from .policy import ACTION_CLASSES, ALLOWED_COMMANDS, BridgePolicy

__all__ = ["BridgeRequest", "BridgeResult", "BridgeService",
           "BridgePolicy", "ACTION_CLASSES", "ALLOWED_COMMANDS"]
