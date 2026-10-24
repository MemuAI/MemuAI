"""memu — the MemuAI command line (Round 35). Entry point of memu.exe; the code is app/memu_cli.py.

``memucode`` is the separate coding agent; ``memu`` talks to the running MemuAI / MemuAI Proxy.
"""
import sys

if __name__ == "__main__":
    from app.memu_cli import main
    sys.exit(main())
