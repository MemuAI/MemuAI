"""PyInstaller entry script of MemuAI Setup (and of the installed uninstaller)."""
import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from installer.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
