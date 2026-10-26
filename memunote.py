"""MemuNote — the light AI notepad of MemuAI (Round 29). Entry point of MemuNote.exe.

Usage: ``MemuNote.exe [files…] [--new-instance] [--shutdown] [--debug]
[--register-open-with | --unregister-open-with] [--no-window --print-url]``.
A second start hands its files to the running window (one instance, tabs).

Round 30: the exe is windowed, so an unhandled error is written to
``%LOCALAPPDATA%\\MemuAI\\logs\\memunote-crash.log`` and shown in a message box
(:mod:`app.memunote.crash`) instead of the process vanishing silently.
"""
import sys

def _main() -> int:
    from app.memunote import crash
    crash.install()
    try:
        from app.memunote.desktop import main
        return main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:  # noqa: BLE001 - last resort: tell the user why, then exit non-zero
        crash.report(*sys.exc_info(), where="start-up")
        return 1

if __name__ == "__main__":
    sys.exit(_main())
