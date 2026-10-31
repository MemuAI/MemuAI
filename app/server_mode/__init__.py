"""MemuAI server mode: MemuAI connects to its own server.

Files, projects and chats live on the server; the server-side AI reaches
the user's computer only through the local bridge (``app/local_bridge``),
only inside allowed roots, only allowlisted commands, and only with the
user's confirmation.  Everything here is off by default: the local mode
behaves exactly as before.

Server side: :mod:`app.server_mode.router` mounted by ``memuai_server.py``
onto the existing Proxy ``create_app``.  Client side: :mod:`app.server_mode.client`
(sync over a mockable transport) and :mod:`app.server_mode.settings_api`
(the Settings -> Server section).
"""
from .auth import TokenStore, mask_token
from .client import ServerClient
from .router import create_server_router, register_server_mode
from .store import ServerStore
from .sync import apply_resolutions, plan_sync, resolve_conflict

__all__ = [
    "TokenStore", "mask_token", "ServerClient", "ServerStore",
    "create_server_router", "register_server_mode",
    "plan_sync", "resolve_conflict", "apply_resolutions",
]
