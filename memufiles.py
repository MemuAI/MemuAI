"""MemuFiles — the fast file manager of MemuAI (Round 35). Entry point of MemuFiles.exe.

Usage: ``MemuFiles.exe [folders/files…] [--new-instance] [--shutdown] [--debug]
[--no-window --print-url] [--port=N] [--shell-open <folder>] [--restore-explorer]``
(the last two: «MemuFiles по умолчанию», :mod:`app.memufiles.default_shell`). A second start hands its folders to the
running window (new tabs). Unhandled errors go to
``%LOCALAPPDATA%\\MemuAI\\logs\\memufiles-crash.log`` (:mod:`app.memufiles.crash`).
"""
import sys


def _main() -> int:
    from app.memufiles import crash
    crash.install()
    try:
        from app.memufiles.desktop import main
        return main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:  # noqa: BLE001 - last resort: tell the user why, then exit non-zero
        crash.report(*sys.exc_info(), where="start-up")
        return 1


if __name__ == "__main__":
    sys.exit(_main())
