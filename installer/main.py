"""Entry point of MemuAI Setup and of the installed uninstaller (the same program).

* Setup exe with a payload, no ``--uninstall``  -> install (window or silent);
* ``--uninstall`` or no payload (the uninstaller copy in the program folder)
  -> uninstall; when started from inside the program folder it first copies
  itself to ``%TEMP%`` and relaunches from there, so it can delete that folder.

The window needs Microsoft Edge WebView2. Without it a native message box
offers to download Microsoft's Evergreen bootstrapper, to install with the
default settings without the interface, or to exit (see README.md here).
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import cli, i18n, products
from .engine import AppsRunning, Engine, Options, SetupError
from .payload import Payload, PayloadError

log = logging.getLogger("memuai.setup")

#: Designed CLIENT size of the window in CSS pixels (DIPs). The page is fluid and
#: fits anything from about 860×540; smaller viewports are scaled to fit (ui/app.js).
WINDOW_SIZE = (960, 620)
#: Gap kept between the window and the edges of the monitor's work area (DIPs).
WINDOW_MARGIN = 16
EXIT_OK, EXIT_ERROR, EXIT_CANCELLED, EXIT_APPS = 0, 1, 2, 5


def fit_window(logical: Tuple[int, int], scale: float, work: Tuple[int, int, int, int],
               nonclient: Tuple[int, int] = (0, 0), margin: int = WINDOW_MARGIN) -> dict:
    """Window rectangle (physical pixels) whose CLIENT area shows ``logical`` DIPs.

    pywebview's WinForms backend sizes the *outer* window and only then removes the
    frame, so on real Windows the client area came out smaller than designed
    (944×581 instead of 960×620). Here the client size is ``logical × scale``
    (125 % → ×1.25), the frame (``nonclient``, zero for a frameless window) is added
    on top, the result is clamped to the work area minus ``margin`` and centred in it.
    """
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = 1.0
    if not (0.5 <= scale <= 6.0):
        scale = 1.0
    left, top, right, bottom = (int(v) for v in work)
    work_w, work_h = max(1, right - left), max(1, bottom - top)
    ncw, nch = (max(0, int(v)) for v in nonclient)
    gap = int(round(margin * scale))
    client_w = int(round(logical[0] * scale))
    client_h = int(round(logical[1] * scale))
    client_w = max(1, min(client_w, work_w - 2 * gap - ncw)) if work_w - 2 * gap - ncw > 0 else min(client_w, work_w)
    client_h = max(1, min(client_h, work_h - 2 * gap - nch)) if work_h - 2 * gap - nch > 0 else min(client_h, work_h)
    width, height = client_w + ncw, client_h + nch
    return {"x": left + (work_w - width) // 2, "y": top + (work_h - height) // 2, "width": width, "height": height,
            "client": (client_w, client_h), "scale": scale,
            "logical": (round(client_w / scale), round(client_h / scale))}


def _hwnd(window) -> int:
    handle = window.native.Handle
    return int(handle.ToInt64() if hasattr(handle, "ToInt64") else handle)


def native_drag(window) -> bool:
    """Run ``winplat.begin_window_drag`` on the GUI thread and wait for it (the bridge call
    comes from a pywebview worker thread; the move loop must run on the form's thread).
    Returns False on any failure so the page falls back to moving the window itself."""
    if os.name != "nt":
        return False
    from .winplat import begin_window_drag
    form = window.native
    if form is None:
        return False
    hwnd = _hwnd(window)
    result = {"ok": False}

    def run() -> None:
        result["ok"] = begin_window_drag(hwnd)

    try:
        from System import Action  # type: ignore  # pythonnet, shipped with pywebview on Windows
        if getattr(form, "InvokeRequired", True):
            form.Invoke(Action(run))
        else:
            run()
    except Exception as exc:  # noqa: BLE001
        log.info("native drag unavailable: %s", exc)
        return False
    return bool(result["ok"])


def resource_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "installer_ui" if base else Path(__file__).resolve().parent / "ui"


def setup_logging(path: Optional[str], kind: str) -> str:
    if path:
        target = Path(path)
    else:
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = Path(tempfile.gettempdir()) / f"MemuAI-{kind}-{stamp}.log"
    handlers: List[logging.Handler] = []
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(target, encoding="utf-8"))
    except OSError:
        target = Path("")
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers or [logging.NullHandler()],
                        format="%(asctime)s %(levelname)s %(message)s", force=True)
    return str(target) if str(target) not in ("", ".") else ""


def make_platform():
    if os.name == "nt":
        from .winplat import WindowsPlatform
        return WindowsPlatform()
    from .engine import PortablePlatform
    return PortablePlatform()


def detect_language(args: cli.Args) -> str:
    if args.language:
        return args.language
    if os.name == "nt":
        from .winplat import ui_language
        return ui_language()
    return i18n.pick_language(os.environ.get("LANG", ""))


def notify(text: str, title: str, flags: int = 0x40, quiet: bool = False) -> int:
    if quiet:
        log.info("%s: %s", title, text)
        return 1
    if os.name == "nt":
        from .winplat import message_box
        return message_box(text, title, flags)
    print(f"{title}: {text}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------- install

def silent_install(engine: Engine, args: cli.Args, language: str, log_path: str) -> int:
    existing = engine.detect()
    defaults = engine.defaults(existing, language)
    components, tasks = cli.resolve_selection(args, engine.product, defaults.components, defaults.tasks)
    directory = args.directory or defaults.install_dir
    checked = engine.check_dir(directory, components)
    if not checked.get("ok"):
        log.error("install folder rejected: %s (%s)", directory, checked.get("error"))
        return EXIT_ERROR
    options = Options(install_dir=str(checked["path"]), components=components, tasks=tasks, language=language)
    try:
        result = engine.install(options, lambda e: None, force_close=True)
    except AppsRunning as exc:
        log.error("programs still running: %s", exc.apps)
        return EXIT_APPS
    except SetupError as exc:
        log.error("install failed (%s): %s", exc.code, exc)
        if not args.very_silent and not args.silent:
            notify(i18n.tr("basic_failed", language, error=str(exc), log=log_path), engine.product.name, 0x10)
        return EXIT_ERROR
    copy_log(log_path, Path(str(result["install_dir"])))
    if args.launch:
        engine.launch(Path(str(result["install_dir"])), components)
    return EXIT_OK


def copy_log(log_path: str, app_dir: Path) -> None:
    if not log_path:
        return
    try:
        for handler in logging.getLogger().handlers:
            handler.flush()
        shutil.copyfile(log_path, app_dir / "install.log")
    except OSError:
        pass


def basic_install(engine: Engine, args: cli.Args, language: str, log_path: str) -> int:
    """Install with defaults and message boxes only (no WebView2 available)."""
    code = silent_install(engine, args, language, log_path)
    name = engine.product.name
    if code == EXIT_OK:
        notify(i18n.tr("basic_done", language, name=name, version=engine.version), name)
    elif code == EXIT_APPS:
        notify(i18n.tr("apps_running", language, apps=name), name, 0x30)
    else:
        notify(i18n.tr("basic_failed", language, error="", log=log_path), name, 0x10)
    return code


def run_window(engine: Engine, mode: str, language: str, log_path: str, app_dir: str = "") -> int:
    import inspect
    import webview  # pywebview (WinForms + Edge WebView2)
    from .bridge import Bridge

    exit_code = {"value": EXIT_OK}
    bridge = Bridge(engine, mode, language, log_path=log_path, app_dir=app_dir,
                    on_exit=lambda code: exit_code.__setitem__("value", code))
    key = "uninstall_title" if mode == "uninstall" else "setup_title"
    kwargs = dict(title=i18n.tr(key, language, name=engine.product.name), url=str(resource_dir() / "index.html"),
                  js_api=bridge, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], resizable=False, frameless=True,
                  easy_drag=False, background_color="#0B0A10", text_select=False, zoomable=False, shadow=True)
    accepted = set(inspect.signature(webview.create_window).parameters)
    window = webview.create_window(**{k: v for k, v in kwargs.items() if k in accepted})
    bridge._window = window
    bridge._begin_drag = lambda: native_drag(window)

    placed: dict = {}

    def on_before_show() -> None:
        # Runs on the GUI thread after the form is built (frame already removed) and
        # before it is shown: size the CLIENT area for the current DPI and centre it
        # on the monitor under the cursor. Any failure keeps pywebview's own placement.
        try:
            from .winplat import place_window
            form = window.native
            try:
                from System.Windows.Forms import FormStartPosition  # type: ignore
                form.StartPosition = FormStartPosition.Manual
            except Exception as exc:  # noqa: BLE001
                log.info("StartPosition unchanged: %s", exc)
            placed.update(place_window(_hwnd(window), WINDOW_SIZE, fit_window))
            log.info("window placed: %s", placed)
        except Exception as exc:  # noqa: BLE001
            log.info("window placement skipped: %s", exc)

    def on_shown() -> None:
        try:
            from .winplat import style_window
            style_window(_hwnd(window))
        except Exception as exc:  # noqa: BLE001
            log.info("native styling unavailable: %s", exc)
        try:
            from .winplat import ensure_client_size, place_window
            hwnd = _hwnd(window)
            if not placed:          # before_show missing (older pywebview) or failed
                placed.update(place_window(hwnd, WINDOW_SIZE, fit_window))
            corrected = ensure_client_size(hwnd, tuple(placed["client"]))
            if corrected:
                log.info("client area corrected to %s (wanted %s)", corrected, placed["client"])
        except Exception as exc:  # noqa: BLE001
            log.info("client size check skipped: %s", exc)

    for name, handler in (("before_show", on_before_show), ("shown", on_shown)):
        try:
            event = getattr(window.events, name)
            event += handler
        except Exception:  # noqa: BLE001
            pass
    start_kwargs = dict(gui="edgechromium", http_server=True, private_mode=True,
                        debug=bool(os.environ.get("MEMUAI_SETUP_DEBUG")),
                        storage_path=str(Path(tempfile.gettempdir()) / "MemuAI-Setup-WebView"))
    accepted = set(inspect.signature(webview.start).parameters)
    webview.start(**{k: v for k, v in start_kwargs.items() if k in accepted})
    if mode == "install":
        state = bridge.poll()
        result = state.get("result")
        if isinstance(result, dict) and result.get("install_dir"):
            copy_log(log_path, Path(str(result["install_dir"])))
    return exit_code["value"]


def webview_available(platform) -> bool:
    try:
        return bool(platform.webview2_version())
    except OSError:
        return False


def interactive_install(engine: Engine, args: cli.Args, language: str, log_path: str) -> int:
    name = engine.product.name
    if os.name == "nt" and not webview_available(engine.os):
        answer = notify(i18n.tr("webview_missing", language, name=name), i18n.tr("setup_title", language, name=name),
                        0x23)  # MB_YESNOCANCEL | MB_ICONQUESTION
        if answer == 2:
            return EXIT_CANCELLED
        if answer == 7:
            return basic_install(engine, args, language, log_path)
        try:
            from .winplat import install_webview2
            install_webview2(Path(tempfile.mkdtemp(prefix="MemuAI-WebView2-")))
        except Exception as exc:  # noqa: BLE001
            log.error("WebView2 install failed: %s", exc)
            if notify(i18n.tr("webview_failed", language, name=name, error=str(exc)), name, 0x24) == 6:
                return basic_install(engine, args, language, log_path)
            return EXIT_CANCELLED
    try:
        return run_window(engine, "install", language, log_path)
    except Exception as exc:  # noqa: BLE001
        log.exception("window failed")
        if notify(i18n.tr("ui_failed", language, name=name, error=str(exc)), name, 0x24) == 6:
            return basic_install(engine, args, language, log_path)
        return EXIT_ERROR


# ---------------------------------------------------------------------- uninstall

def relaunch_from_temp(exe: Path, app_dir: Path, argv: Sequence[str]) -> bool:
    """Copy the uninstaller out of the folder it deletes and start the copy."""
    try:
        temp_root = Path(tempfile.gettempdir())
        for old in temp_root.glob("MemuAI-Uninstall-*"):
            shutil.rmtree(old, ignore_errors=True)   # copies left by earlier runs
        folder = Path(tempfile.mkdtemp(prefix="MemuAI-Uninstall-"))
        copy = folder / exe.name
        shutil.copy2(exe, copy)
        from .winplat import clean_environment
        passthrough = [a for a in argv if a.lower() not in ("--relaunched",) and not a.lower().startswith("--app-dir")]
        subprocess.Popen([str(copy), "--uninstall", "--relaunched", "--app-dir", str(app_dir), *passthrough],
                         cwd=str(folder), env=clean_environment(), close_fds=True)
        return True
    except OSError as exc:
        log.error("relaunch from temp failed: %s", exc)
        return False


def find_product(app_dir: Path, args: cli.Args, exe: Path):
    if args.product:
        return products.get(args.product)
    state = Engine.read_state(app_dir) or {}
    if state.get("product") in products.PRODUCTS:
        return products.get(str(state["product"]))
    for product in products.PRODUCTS.values():
        if exe.name.casefold() == product.uninstaller.casefold():
            return product
    return products.SUITE


def run_uninstall(args: cli.Args, exe: Path, argv: Sequence[str]) -> int:
    app_dir = Path(args.app_dir) if args.app_dir else exe.parent
    log_path = setup_logging(args.log, "Uninstall")
    product = find_product(app_dir, args, exe)
    platform = make_platform()
    engine = Engine(product, platform, None, version=str((Engine.read_state(app_dir) or {}).get("version") or ""))
    state = Engine.read_state(app_dir) or {}
    language = args.language or (state.get("language") if state.get("language") in ("ru", "en") else None) \
        or detect_language(args)
    if getattr(sys, "frozen", False) and not args.relaunched and os.name == "nt":
        try:
            inside = exe.resolve().parent == app_dir.resolve()
        except OSError:
            inside = True
        if inside and relaunch_from_temp(exe, app_dir, [a for a in argv if a.lower() != "--uninstall"]):
            return EXIT_OK
    if not state and not (app_dir / product.uninstaller).exists():
        log.warning("no installation state in %s", app_dir)
    if args.silent:
        try:
            result = engine.uninstall(app_dir, args.remove_data, force_close=True)
        except SetupError as exc:
            log.error("uninstall failed (%s): %s", exc.code, exc)
            return EXIT_ERROR
        return EXIT_OK if result.get("ok") else EXIT_ERROR
    if os.name == "nt" and not webview_available(platform):
        name = product.name
        if notify(i18n.tr("uninstall_ui_failed", language, name=name, error="WebView2"),
                  i18n.tr("uninstall_title", language, name=name), 0x24) != 6:
            return EXIT_CANCELLED
        try:
            engine.uninstall(app_dir, False, force_close=False)
        except AppsRunning as exc:
            notify(i18n.tr("apps_running", language, apps=", ".join(exc.apps)), name, 0x30)
            return EXIT_APPS
        notify(i18n.tr("basic_uninstalled", language, name=name), name)
        return EXIT_OK
    try:
        return run_window(engine, "uninstall", language, log_path, app_dir=str(app_dir))
    except Exception as exc:  # noqa: BLE001
        log.exception("uninstall window failed")
        name = product.name
        if notify(i18n.tr("uninstall_ui_failed", language, name=name, error=str(exc)), name, 0x24) == 6:
            try:
                engine.uninstall(app_dir, False, force_close=True)
                return EXIT_OK
            except SetupError:
                return EXIT_ERROR
        return EXIT_CANCELLED


# ---------------------------------------------------------------------- main

def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = cli.parse(argv)
    frozen = getattr(sys, "frozen", False)
    exe = Path(sys.executable if frozen else os.environ.get("MEMUAI_SETUP_EXE", sys.executable)).resolve()
    payload = None
    payload_error = ""
    if not args.uninstall:
        try:
            payload = Payload.open_optional(exe)
        except PayloadError as exc:
            payload_error = str(exc)
    if args.uninstall or (payload is None and not payload_error and (Path(exe).parent / "install.json").exists()):
        return run_uninstall(args, exe, argv)
    log_path = setup_logging(args.log if args.log != "" else None, "Setup")
    language = detect_language(args)
    if payload is None:
        message = payload_error or "no payload"
        log.error("payload problem: %s", message)
        notify(i18n.tr("no_payload", language, error=message), "MemuAI Setup", 0x10, quiet=args.silent)
        return EXIT_ERROR
    product = products.get(args.product or str(payload.meta.get("product") or "memuai"))
    platform = make_platform()
    engine = Engine(product, platform, payload)
    log.info("MemuAI Setup %s (%s) from %s; args=%s", engine.version, product.id, exe, argv)
    if args.unknown:
        log.warning("ignored arguments: %s", args.unknown)
    mutex = None
    if os.name == "nt":
        from .winplat import single_instance
        mutex = single_instance(f"Local\\MemuAI-Setup-{product.id}")
        if mutex is None:
            payload.close()
            notify(i18n.tr("already_running", language), product.name, 0x30, quiet=args.silent)
            return EXIT_ERROR
    try:
        if args.silent:
            return silent_install(engine, args, language, log_path)
        return interactive_install(engine, args, language, log_path)
    finally:
        payload.close()
        if os.name == "nt":
            from .winplat import release_instance
            release_instance(mutex)
