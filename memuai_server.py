"""MemuAI Server: run MemuAI's Proxy app as your own server.

Usage (from the MemuAI checkout)::

    python memuai_server.py [--host 127.0.0.1] [--port 8327]
                            [--data-dir <server data dir>]

The server reuses the existing MemuAI Proxy FastAPI app
(:func:`app.main.create_app`) and mounts the server-mode API
(``/api/server/*``) on top of it.  On first start a bearer token is
generated and stored ONLY on the server, in ``<data-dir>/server_token``
(mode 0600).  The token itself is never printed: this process prints the
token file's location and a mask.  Copy the token to the client once,
into Settings -> Server; the client stores it in protected storage.

Do NOT expose this process directly to the internet.  Put it behind a
reverse proxy with HTTPS (see ``docs/SERVER.md`` and
``packaging/server/nginx.conf.example``).  Binding a non-loopback host
prints a warning and requires ``--allow-remote`` as an explicit opt-in.

Environment overrides: ``MEMUAI_SERVER_HOST``, ``MEMUAI_SERVER_PORT``,
``MEMUAI_SERVER_DATA_DIR``.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8327


def default_data_dir() -> Path:
    env = os.environ.get("MEMUAI_SERVER_DATA_DIR")
    if env:
        return Path(env)
    try:
        from app.config import default_data_dir as _default_data_dir
        base = Path(_default_data_dir("MemuAI"))
    except Exception:
        base = Path.home() / ".local" / "share" / "MemuAI"
    return base / "server"


def build_server_app(data_dir=None, *, base_app_factory=None):
    """Create the Proxy app and mount the server-mode router on it.

    ``base_app_factory`` exists for tests / verification environments
    without the Proxy's heavy provider dependencies; in production it is
    always ``app.main.create_app`` (imported lazily, only when needed).
    """
    if base_app_factory is None:
        from app.main import create_app
        base_app_factory = create_app
    from app.server_mode import register_server_mode

    app = base_app_factory()
    register_server_mode(app, data_dir or default_data_dir())
    return app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MemuAI Server (server mode)")
    parser.add_argument("--host", default=os.environ.get("MEMUAI_SERVER_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("MEMUAI_SERVER_PORT", DEFAULT_PORT)))
    parser.add_argument("--data-dir", default=None,
                        help="server data dir (default: MEMUAI_SERVER_DATA_DIR or the MemuAI data dir + /server)")
    parser.add_argument("--allow-remote", action="store_true",
                        help="explicitly allow binding a non-loopback address (use only behind a reverse proxy with HTTPS)")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.allow_remote:
        print("ERROR: refusing to bind a non-loopback address without --allow-remote.",
              file=sys.stderr)
        print("Expose the server only behind a reverse proxy with HTTPS; see docs/SERVER.md.",
              file=sys.stderr)
        return 2
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: bound to a non-loopback address. Serve only behind a "
              "reverse proxy with HTTPS (see docs/SERVER.md).", file=sys.stderr)

    from app.server_mode.auth import TokenStore, mask_token

    tokens = TokenStore(data_dir)
    token = tokens.get_or_create()
    app = build_server_app(data_dir)

    print(f"MemuAI Server listening on http://{args.host}:{args.port}")
    print(f"Server data dir: {data_dir}")
    print(f"Server token file (0600, server only): {tokens.path}")
    print(f"Server token mask: {mask_token(token)}")
    print("Copy the token from the token file into MemuAI Settings -> Server on the client.")

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
