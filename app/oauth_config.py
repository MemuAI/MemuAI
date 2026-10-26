"""Private OAuth clients for Google, Notion and Antigravity.

Google and Notion only let an app sign a person in through a registered OAuth
client.  MemuAI cannot ship a shared one in the source tree, so there are three
places a client can come from, in this order:

1. **The user's own client** — entered in Settings → Integrations (the wizard
   links straight to Google Cloud Console / Notion).  Not handled here: it lives
   with the account (``gdrive_sync`` settings for Google, ``integrations.json``
   for Notion) and always wins.
2. **Environment at run time** — ``MEMUAI_GOOGLE_CLIENT_ID`` /
   ``MEMUAI_GOOGLE_CLIENT_SECRET``, ``MEMUAI_NOTION_CLIENT_ID`` /
   ``MEMUAI_NOTION_CLIENT_SECRET`` (developers, portable setups).
3. **The build** — ``build.py`` writes ``app/oauth_build.py`` from the same
   variables (or from ``oauth_clients.local.json`` next to ``build.py``) before
   PyInstaller runs, so the owner of a build can bake their client in and every
   user gets the one-click sign-in.

Antigravity uses its own MEMUAI_ANTIGRAVITY_CLIENT_ID and
MEMUAI_ANTIGRAVITY_CLIENT_SECRET (or ANTIGRAVITY_* build values).
It never implicitly reuses the Google Drive / Calendar client.

For a Google OAuth client of type «Desktop app» the secret is not confidential
by Google's own design (it ships inside every installed app); the loopback
redirect + PKCE protect the flow.  Notion's public-integration secret is
confidential in theory — a build owner who bakes it in accepts that a desktop
app cannot hide it.
"""
from __future__ import annotations

import os
from typing import Tuple

ENV = {
    "google": ("MEMUAI_GOOGLE_CLIENT_ID", "MEMUAI_GOOGLE_CLIENT_SECRET"),
    "notion": ("MEMUAI_NOTION_CLIENT_ID", "MEMUAI_NOTION_CLIENT_SECRET"),
    "antigravity": ("MEMUAI_ANTIGRAVITY_CLIENT_ID", "MEMUAI_ANTIGRAVITY_CLIENT_SECRET"),
}

def _built(provider: str) -> Tuple[str, str]:
    try:
        from . import oauth_build  # type: ignore[attr-defined]  # written by build.py, absent in a source checkout
    except ImportError:
        return "", ""
    prefix = provider.upper()
    return (str(getattr(oauth_build, prefix + "_CLIENT_ID", "") or "").strip(),
            str(getattr(oauth_build, prefix + "_CLIENT_SECRET", "") or "").strip())

def client(provider: str) -> Tuple[str, str, str]:
    """``(client_id, client_secret, source)`` with source ``env`` | ``build`` | ``""`` (none configured)."""
    id_var, secret_var = ENV[provider]
    cid = os.environ.get(id_var, "").strip()
    if cid:
        return cid, os.environ.get(secret_var, "").strip(), "env"
    cid, secret = _built(provider)
    if cid:
        return cid, secret, "build"
    return "", "", ""
