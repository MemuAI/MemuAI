"""«MemuFiles по умолчанию» (Round 38): open folders and Win+E in MemuFiles — opt-in, fully reversible.

Windows has no API for "the default file manager", so this repeats the approach the Files app ships
(files-community ``SetFilesAsDefault.reg`` / ``UnsetFilesAsDefault.reg``), per user, without admin rights:

* **folders** — ``HKCU\\Software\\Classes\\Folder\\shell\\open\\command`` (default = our command, empty
  ``DelegateExecute`` so the shell runs the command instead of Explorer's COM verb) plus a ``MemuFiles`` verb that is
  the default verb of ``Directory`` and ``Drive`` (double click, Enter, ``ShellExecute(folder)`` from other programs);
* **win_e** — ``HKCU\\Software\\Classes\\CLSID\\{52205fd8-5dfb-447d-801a-d0b52f2e83e1}\\shell\\opennewwindow\\command``
  (the "new Explorer window" object behind Win+E and the taskbar's Explorer button), the same key Files writes;
  :mod:`.winkey` is an optional low-level-hook fallback while MemuFiles runs.

The ``explore`` verb is never touched: it is the always-working way back to the real Explorer
(``ShellExecute(path, "explore")`` / ``explorer.exe /e,``) — no recursion when MemuFiles hands a path it cannot
show (``::{CLSID}``, ``shell:``, MTP devices, a missing folder) back to Explorer.

**Rollback is the main feature.** Before the first write every value we touch is backed up exactly (type + data, or
"absent", plus which keys did not exist) into ``<data>\\memufiles\\shell\\state.json``; :func:`restore_all` puts every
value back, deletes only what we created and leaves keys other programs added meanwhile alone. Also:
``shell\\restore-explorer.reg`` (an emergency .reg generated from the same backup), ``MemuFiles.exe
--restore-explorer``, the MemuAI uninstaller (``installer/products.py`` ``pre_uninstall``), removing MemuFiles from
MemuAI (``app/memufiles_manager.uninstall``). Without a backup (data folder wiped) restore removes whatever points to
MemuFiles — in HKCU those keys do not exist on a clean Windows, so that *is* the original state.

The registry command is a tiny JScript launcher (``shell\\memufiles-open.js`` run by ``wscript.exe``) that survives
MemuFiles being moved or deleted: if ``MemuFiles.exe`` is gone or fails, the folder opens in Explorer. When Windows
Script Host is disabled the command calls ``MemuFiles.exe --shell-open`` directly (MemuFiles start-up re-points it
when the exe moved — :func:`heal`). Everything is a no-op returning ``available: False`` off Windows.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .paths import files_dir, read_json, write_json

log = logging.getLogger("memufiles.default_shell")

REG_SZ, REG_EXPAND_SZ, REG_DWORD = 1, 2, 4
CLASSES = r"Software\Classes"
VERB = "MemuFiles"
MARKER = "memufiles"
WIN_E_CLSID = "{52205fd8-5dfb-447d-801a-d0b52f2e83e1}"
THIS_PC = "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
FEATURES = ("folders", "win_e")
WSH_KEY = r"Software\Microsoft\Windows Script Host\Settings"
_LOCK = threading.RLock()

class ShellError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code

class WinReg:
    """Thin ``winreg`` wrapper over HKEY_CURRENT_USER; tests replace it with a fake of the same shape."""

    def __init__(self) -> None:
        import winreg  # type: ignore
        self.w = winreg

    def get(self, path: str, name: str) -> Optional[Tuple[int, Any]]:
        try:
            with self.w.OpenKey(self.w.HKEY_CURRENT_USER, path, 0, self.w.KEY_READ) as key:
                data, kind = self.w.QueryValueEx(key, name)
                return int(kind), data
        except OSError:
            return None

    def key_exists(self, path: str) -> bool:
        try:
            with self.w.OpenKey(self.w.HKEY_CURRENT_USER, path, 0, self.w.KEY_READ):
                return True
        except OSError:
            return False

    def set(self, path: str, name: str, kind: int, data: Any) -> None:
        with self.w.CreateKeyEx(self.w.HKEY_CURRENT_USER, path, 0, self.w.KEY_WRITE) as key:
            self.w.SetValueEx(key, name, 0, kind, data)

    def delete_value(self, path: str, name: str) -> None:
        try:
            with self.w.OpenKey(self.w.HKEY_CURRENT_USER, path, 0, self.w.KEY_SET_VALUE) as key:
                self.w.DeleteValue(key, name)
        except FileNotFoundError:
            pass

    def is_empty(self, path: str) -> bool:
        try:
            with self.w.OpenKey(self.w.HKEY_CURRENT_USER, path, 0, self.w.KEY_READ) as key:
                subkeys, values, _ = self.w.QueryInfoKey(key)
                return subkeys == 0 and values == 0
        except OSError:
            return False

    def delete_tree(self, path: str) -> None:
        try:
            with self.w.OpenKey(self.w.HKEY_CURRENT_USER, path, 0, self.w.KEY_ALL_ACCESS) as key:
                while True:
                    try:
                        child = self.w.EnumKey(key, 0)
                    except OSError:
                        break
                    self.delete_tree(path + "\\" + child)
            self.w.DeleteKey(self.w.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass

    def notify(self) -> None:
        """SHChangeNotify(SHCNE_ASSOCCHANGED) — Explorer drops its cached verbs."""
        try:
            import ctypes
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
        except Exception:  # noqa: BLE001
            pass

_REG: Any = None

def _reg() -> Any:
    global _REG
    if _REG is None:
        _REG = WinReg()
    return _REG

def set_registry(reg: Any) -> None:
    """Tests: plug a fake registry (``None`` restores the real one lazily)."""
    global _REG
    _REG = reg

def available() -> bool:
    return os.name == "nt" or _REG is not None and not isinstance(_REG, WinReg)

def shell_dir() -> Path:
    path = files_dir() / "shell"
    path.mkdir(parents=True, exist_ok=True)
    return path

def _state_path() -> Path:
    return shell_dir() / "state.json"

def load_state() -> Dict[str, Any]:
    state = read_json(_state_path(), {})
    state.setdefault("features", {})
    return state

def _save_state(state: Dict[str, Any]) -> None:
    write_json(_state_path(), state)

def launcher_path() -> Path:
    return shell_dir() / "memufiles-open.js"

def emergency_reg_path() -> Path:
    return shell_dir() / "restore-explorer.reg"

def current_exe() -> Optional[str]:
    """MemuFiles.exe of this install (frozen), else the installed one MemuAI knows about."""
    if getattr(sys, "frozen", False) and Path(sys.executable).name.lower() == "memufiles.exe":
        return str(Path(sys.executable).resolve())
    try:
        from .. import memufiles_manager
        exe = memufiles_manager.paths().get("exe")
        if exe is not None and exe.is_file():
            return str(exe)
    except Exception:  # noqa: BLE001
        pass
    return None

def _wsh_enabled(reg: Any) -> bool:
    got = reg.get(WSH_KEY, "Enabled")
    if got is None:
        return True
    try:
        return int(str(got[1]).strip() or "1") != 0
    except ValueError:
        return True

def _command(exe: str, mode: str, with_arg: bool) -> str:
    arg = ' "%1"' if with_arg else ""
    if mode == "launcher":
        return f'"%SystemRoot%\\System32\\wscript.exe" //B //Nologo "{launcher_path()}"{arg}'
    return f'"{exe}" --shell-open{arg}'

def _plan(feature: str, exe: str, mode: str, lang: str) -> List[Tuple[str, str, int, Any]]:
    """``(key, value name, type, data)`` for one feature; "" is the key's default value."""
    title = "Открыть в MemuFiles" if lang == "ru" else "Open in MemuFiles"
    if feature == "folders":
        rows: List[Tuple[str, str, int, Any]] = [
            (CLASSES + r"\Folder\shell\open\command", "", REG_EXPAND_SZ, _command(exe, mode, True)),
            (CLASSES + r"\Folder\shell\open\command", "DelegateExecute", REG_SZ, ""),
        ]
        for cls in ("Directory", "Drive"):
            base = CLASSES + "\\" + cls + r"\shell"
            rows += [(base, "", REG_SZ, VERB),
                     (base + "\\" + VERB, "", REG_SZ, title),
                     (base + "\\" + VERB, "Icon", REG_SZ, f'"{exe}",0'),
                     (base + "\\" + VERB + r"\command", "", REG_EXPAND_SZ, _command(exe, mode, True))]
        return rows
    if feature == "win_e":
        key = CLASSES + r"\CLSID" + "\\" + WIN_E_CLSID + r"\shell\opennewwindow\command"
        return [(key, "", REG_EXPAND_SZ, _command(exe, mode, False)), (key, "DelegateExecute", REG_SZ, "")]
    raise ShellError("bad_feature", feature)

def _parents(key: str) -> List[str]:
    """``A\\B\\C`` → [``A\\B\\C``, ``A\\B``, ``A``] (only below ``Software\\Classes``)."""
    out, parts = [], key.split("\\")
    for n in range(len(parts), 2, -1):
        out.append("\\".join(parts[:n]))
    return out

def _js(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)

def launcher_source(exe: str) -> str:
    """JScript for wscript.exe: folder → MemuFiles; anything MemuFiles cannot show, a missing/broken MemuFiles →
    the shell's own ``explore`` verb (real Explorer, never our ``open`` → no loop). No arg = Win+E."""
    return f"""// MemuFiles folder launcher (MemuAI Round 38). Written by MemuFiles; safe to delete after
// "Restore Windows Explorer" (MemuFiles.exe --restore-explorer or shell\\restore-explorer.reg).
var EXE = {_js(exe)};
var THIS_PC = {_js(THIS_PC)};
var INSTANCE = {_js(str(files_dir() / "instance.json"))};
var args = WScript.Arguments;
var target = args.length > 0 ? String(args(0)) : "";
if (target.charAt(target.length - 1) == '"') target = target.slice(0, -1) + "\\\\";
if (/^[A-Za-z]:$/.test(target)) target += "\\\\";
var fso = new ActiveXObject("Scripting.FileSystemObject");
var shellApp = new ActiveXObject("Shell.Application");
function explore(path) {{
  try {{ shellApp.ShellExecute(path || THIS_PC, "", "", "explore", 1); }}
  catch (e) {{ new ActiveXObject("WScript.Shell").Run('explorer.exe /e,"' + (path || THIS_PC) + '"', 1, false); }}
}}
function quote(s) {{ return '"' + (s.charAt(s.length - 1) == "\\\\" ? s + "\\\\" : s) + '"'; }}
// Round 39: a running MemuFiles takes the folder straight from here (one loopback request, no new process) —
// Win+E / a folder double-click opens a tab in well under 300 ms. Any failure falls back to starting the exe.
// Round 41: "shell":true — a folder from the shell («Show in folder» in a browser): MemuFiles selects the fresh file.
function handOff(path) {{
  try {{
    if (!fso.FileExists(INSTANCE)) return false;
    var f = fso.OpenTextFile(INSTANCE, 1, false, 0), text = f.ReadAll(); f.Close();
    var port = /"port":\\s*(\\d+)/.exec(text), token = /"token":\\s*"([^"]+)"/.exec(text);
    if (!port || !token) return false;
    var shell = path ? ',"shell":true' : "";            // Win+E (no folder) is not a «Show in folder»
    if (!path) path = new ActiveXObject("WScript.Shell").ExpandEnvironmentStrings("%USERPROFILE%");
    var body = '{{"action":"open","require_ui":true' + shell + ',"paths":["' + path.replace(/\\\\/g, "\\\\\\\\").replace(/"/g, '\\\\"') + '"]}}';
    var http = new ActiveXObject("WinHttp.WinHttpRequest.5.1");
    http.SetProxy(1);                                   // direct: never through a configured WinHTTP proxy
    http.SetTimeouts(500, 500, 1500, 3000);
    http.Open("POST", "http://127.0.0.1:" + port[1] + "/api/remote", false);
    http.SetRequestHeader("Content-Type", "application/json");
    http.SetRequestHeader("X-MF-Token", token[1]);
    http.Send(body);
    return http.Status == 200 && !/"taken":\\s*false/.test(http.ResponseText);
  }} catch (e) {{ return false; }}
}}
var special = target.indexOf("::") >= 0 || target.toLowerCase().indexOf("shell:") == 0;
if (special || (target && !fso.FolderExists(target)) || !fso.FileExists(EXE)) {{
  explore(target);
}} else if (!handOff(target)) {{
  try {{
    new ActiveXObject("WScript.Shell").Run(quote(EXE) + " --shell-open" + (target ? " " + quote(target) : ""), 1, false);
  }} catch (e) {{ explore(target); }}
}}
"""

def _launcher_outdated(exe: str) -> bool:
    try:
        current = launcher_path().read_bytes()
    except OSError:
        return True
    return current != b"\xff\xfe" + launcher_source(exe).replace("\n", "\r\n").encode("utf-16-le")

def _write_launcher(exe: str) -> None:
    path = launcher_path()
    data = launcher_source(exe).replace("\n", "\r\n").encode("utf-16-le")
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(b"\xff\xfe" + data)
    os.replace(tmp, path)

def _backup(reg: Any, rows: List[Tuple[str, str, int, Any]]) -> Dict[str, Any]:
    values, keys = [], []
    seen_keys = set()
    for key, name, _kind, _data in rows:
        got = reg.get(key, name)
        if got is not None and name == "" and MARKER in str(got[1]).lower():
            got = None
        values.append({"key": key, "name": name, "existed": got is not None,
                       "type": got[0] if got else None, "data": _jsonable(got[1]) if got else None})
        for parent in _parents(key):
            if parent.lower() in seen_keys:
                continue
            seen_keys.add(parent.lower())
            keys.append({"key": parent, "existed": reg.key_exists(parent)})
    return {"values": values, "keys": keys, "saved_at": int(time.time())}

def _jsonable(data: Any) -> Any:
    if isinstance(data, bytes):
        return {"hex": data.hex()}
    return data

def _from_json(data: Any) -> Any:
    if isinstance(data, dict) and "hex" in data:
        return bytes.fromhex(data["hex"])
    return data

def _reg_value(kind: Optional[int], data: Any) -> str:
    if kind == REG_SZ:
        return '"' + str(data).replace("\\", "\\\\").replace('"', '\\"') + '"'
    if kind == REG_DWORD:
        return "dword:%08x" % (int(data) & 0xFFFFFFFF)
    if kind == REG_EXPAND_SZ or isinstance(data, str):
        raw = (str(data) + "\0").encode("utf-16-le")
        return "hex(2):" + ",".join("%02x" % b for b in raw)
    if isinstance(data, (bytes, bytearray)):
        return "hex(%x):" % int(kind or 3) + ",".join("%02x" % b for b in data)
    if isinstance(data, list):
        raw = ("\0".join(str(x) for x in data) + "\0\0").encode("utf-16-le")
        return "hex(7):" + ",".join("%02x" % b for b in raw)
    return "hex(%x):" % int(kind or 3)

def emergency_reg_text(state: Optional[Dict[str, Any]] = None) -> str:
    """A .reg that undoes everything (from the backup; without one — removes what points to MemuFiles)."""
    lines = ["Windows Registry Editor Version 5.00", "",
             "; MemuFiles: restore Windows Explorer as the folder handler (Win+E, double click on folders).",
             "; MemuFiles: вернуть Проводник Windows (Win+E, двойной щелчок по папкам).", ""]
    hkcu = "HKEY_CURRENT_USER\\"
    features = (state or {}).get("features") or {}
    backups = [features[f]["backup"] for f in FEATURES if isinstance(features.get(f), dict) and features[f].get("backup")]
    if not backups:
        backups = [_fallback_backup(None)]
    deleted: List[str] = []
    for backup in backups:
        created = [k["key"] for k in backup.get("keys", []) if not k.get("existed")]
        for key in sorted(created, key=lambda k: k.count("\\")):
            if not any(key.lower().startswith(d.lower() + "\\") or key.lower() == d.lower() for d in deleted):
                if _ours_only(key):
                    deleted.append(key)
                    lines += [f"[-{hkcu}{key}]", ""]
        for row in backup.get("values", []):
            if any(row["key"].lower().startswith(d.lower() + "\\") or row["key"].lower() == d.lower() for d in deleted):
                continue
            name = "@" if row["name"] == "" else '"' + row["name"] + '"'
            value = _reg_value(row.get("type"), _from_json(row.get("data"))) if row.get("existed") else "-"
            lines += [f"[{hkcu}{row['key']}]", f"{name}={value}", ""]
    return "\r\n".join(lines) + "\r\n"

def _ours_only(key: str) -> bool:
    """Keys that only MemuFiles ever creates — safe to delete whole in the emergency file."""
    low = key.lower()
    return low.endswith("\\shell\\" + VERB.lower()) or low.endswith("\\" + WIN_E_CLSID.lower())

def _write_emergency(state: Dict[str, Any]) -> None:
    path = emergency_reg_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(b"\xff\xfe" + emergency_reg_text(state).encode("utf-16-le"))
    os.replace(tmp, path)

def _fallback_backup(_reg_unused: Any) -> Dict[str, Any]:
    """No backup on disk: on a clean Windows none of these HKCU values exist — the original state is "absent"."""
    rows = _plan("folders", "x", "direct", "en") + _plan("win_e", "x", "direct", "en")
    keys, seen = [], set()
    for key, _n, _k, _d in rows:
        for parent in _parents(key):
            if parent.lower() not in seen:
                seen.add(parent.lower())
                keys.append({"key": parent, "existed": False})
    return {"values": [{"key": k, "name": n, "existed": False} for k, n, _t, _d in rows], "keys": keys}

def _is_ours(got: Optional[Tuple[int, Any]], expected: Any) -> bool:
    if got is None:
        return False
    if isinstance(expected, str) and MARKER in expected.lower():
        return MARKER in str(got[1]).lower()
    return got[1] == expected

def _feature_state(reg: Any, feature: str, exe: str, mode: str) -> str:
    rows = _plan(feature, exe, mode, "en")
    hits = 0
    for key, name, _kind, data in rows:
        got = reg.get(key, name)
        if name == "" and key.endswith("\\" + VERB):
            hits += got is not None
        elif _is_ours(got, data):
            hits += 1
    if hits == len(rows):
        return "on"
    return "off" if hits == 0 else "partial"

def status() -> Dict[str, Any]:
    if not available():
        return {"ok": True, "available": False, "state": "unavailable", "features": {},
                "reason": "windows_only"}
    with _LOCK:
        reg = _reg()
        state = load_state()
        exe = current_exe() or state.get("exe") or ""
        mode = state.get("mode") or "launcher"
        features = {}
        for feature in FEATURES:
            saved = state["features"].get(feature) or {}
            features[feature] = {"state": _feature_state(reg, feature, exe or "MemuFiles.exe", mode),
                                 "enabled_by_us": bool(saved.get("enabled")),
                                 "enabled_at": saved.get("enabled_at"),
                                 "has_backup": bool(saved.get("backup"))}
        states = {f["state"] for f in features.values()}
        overall = "on" if states == {"on"} else "off" if states == {"off"} else "partial"
        return {"ok": True, "available": True, "state": overall, "features": features, "mode": mode,
                "exe": exe, "exe_exists": bool(exe) and os.path.isfile(exe), "hook": bool(state.get("hook")),
                "wsh": _wsh_enabled(reg), "emergency_reg": str(emergency_reg_path()),
                "can_restore": any(f["state"] != "off" or f["has_backup"] for f in features.values())}

def _lang() -> str:
    try:
        from . import settings
        value = settings.load().get("language", "auto")
        if value in ("ru", "en"):
            return value
    except Exception:  # noqa: BLE001
        pass
    return "ru"

def enable(feature: str, exe: Optional[str] = None) -> Dict[str, Any]:
    """Turn one feature on (backing the original values up first — never twice: a second enable keeps the
    first backup, otherwise a re-enable would "back up" our own values and the rollback would lose Explorer)."""
    if feature not in FEATURES:
        raise ShellError("bad_feature", feature)
    if not available():
        raise ShellError("unavailable", "Windows only")
    with _LOCK:
        reg = _reg()
        exe = exe or current_exe()
        if not exe or not os.path.isfile(exe):
            raise ShellError("no_exe", "MemuFiles.exe was not found — install MemuFiles first")
        state = load_state()
        mode = "launcher" if _wsh_enabled(reg) else "direct"
        rows = _plan(feature, exe, mode, _lang())
        saved = state["features"].get(feature) or {}
        if not saved.get("backup"):
            saved["backup"] = _backup(reg, rows)
        saved.update({"enabled": True, "enabled_at": int(time.time())})
        state["features"][feature] = saved
        state.update({"exe": exe, "mode": mode, "version": 1})
        _save_state(state)
        _write_emergency(state)
        if mode == "launcher":
            _write_launcher(exe)
        try:
            for key, name, kind, data in rows:
                reg.set(key, name, kind, data)
        except OSError as exc:
            _restore_feature(reg, state, feature)
            _save_state(state)
            raise ShellError("registry", f"registry write failed: {exc}") from exc
        reg.notify()
        log.info("default shell: %s enabled (%s)", feature, mode)
        return status()

def _restore_feature(reg: Any, state: Dict[str, Any], feature: str) -> List[str]:
    saved = state["features"].get(feature) or {}
    backup = saved.get("backup") or _fallback_feature_backup(feature)
    problems: List[str] = []
    exact = bool(saved.get("backup"))

    ours = {row["key"].lower() for row in backup.get("values", []) if row["name"] == ""
            and MARKER in str((reg.get(row["key"], "") or (0, ""))[1]).lower()}
    for row in backup.get("values", []):
        try:
            if row.get("existed"):
                reg.set(row["key"], row["name"], int(row.get("type") or REG_SZ), _from_json(row.get("data")))
            elif exact or row["key"].lower() in ours:
                reg.delete_value(row["key"], row["name"])
        except OSError as exc:
            problems.append(f"{row['key']}\\{row['name'] or '@'}: {exc}")
    created = [k["key"] for k in backup.get("keys", []) if not k.get("existed")]
    for key in sorted(created, key=lambda k: -k.count("\\")):
        try:
            if _ours_only(key) and reg.key_exists(key):
                _delete_ours(reg, key)
            elif reg.key_exists(key) and reg.is_empty(key):
                reg.delete_tree(key)
        except OSError as exc:
            problems.append(f"{key}: {exc}")
    state["features"].pop(feature, None)
    return problems

def _delete_ours(reg: Any, key: str) -> None:
    """Our verb / our Win+E CLSID key: delete it unless something foreign was added inside since."""
    if key.lower().endswith("\\" + WIN_E_CLSID.lower()):
        command = key + r"\shell\opennewwindow\command"
        for sub in (command, key + r"\shell\opennewwindow", key + r"\shell", key):
            if reg.key_exists(sub) and reg.is_empty(sub):
                reg.delete_tree(sub)
        return
    reg.delete_tree(key)

def _fallback_feature_backup(feature: str) -> Dict[str, Any]:
    full = _fallback_backup(None)
    mine = {(k, n) for k, n, _t, _d in _plan(feature, "x", "direct", "en")}
    keys = {p.lower() for k, _n in mine for p in _parents(k)}
    return {"values": [v for v in full["values"] if (v["key"], v["name"]) in mine],
            "keys": [k for k in full["keys"] if k["key"].lower() in keys]}

def disable(feature: str) -> Dict[str, Any]:
    """Turn one feature off: its values go back to exactly what they were before :func:`enable`."""
    if feature not in FEATURES:
        raise ShellError("bad_feature", feature)
    if not available():
        raise ShellError("unavailable", "Windows only")
    with _LOCK:
        reg = _reg()
        state = load_state()
        problems = _restore_feature(reg, state, feature)
        _finish(state)
        reg.notify()
        result = status()
        result["problems"] = problems
        return result

def restore_all() -> Dict[str, Any]:
    """«Вернуть Проводник Windows»: every feature back to the backup (or removed when there is none)."""
    if not available():
        return {"ok": True, "available": False, "state": "unavailable", "problems": []}
    with _LOCK:
        reg = _reg()
        state = load_state()
        problems: List[str] = []
        for feature in FEATURES:
            if state["features"].get(feature) or _feature_state(reg, feature, "MemuFiles.exe", "launcher") != "off":
                problems += _restore_feature(reg, state, feature)
        state["hook"] = False
        _finish(state)
        reg.notify()
        result = status()
        result["problems"] = problems
        log.info("default shell: Explorer restored (%d problems)", len(problems))
        return result

def _finish(state: Dict[str, Any]) -> None:
    if state["features"]:
        _save_state(state)
        _write_emergency(state)
        return
    hook = bool(state.get("hook"))
    for path in (launcher_path(), emergency_reg_path()):
        try:
            path.unlink()
        except OSError:
            pass
    if hook:
        _save_state({"features": {}, "hook": True})
    else:
        try:
            _state_path().unlink()
        except OSError:
            pass

def set_hook(on: bool) -> Dict[str, Any]:
    with _LOCK:
        state = load_state()
        state["hook"] = bool(on)
        _save_state(state)
        return status()

def heal(exe: Optional[str] = None) -> bool:
    """MemuFiles start-up: re-point the launcher / commands when MemuFiles.exe moved. True when anything changed."""
    if not available():
        return False
    with _LOCK:
        state = load_state()
        enabled = [f for f in FEATURES if (state["features"].get(f) or {}).get("enabled")]
        exe = exe or current_exe()
        if not enabled or not exe:
            return False
        if os.path.normcase(exe) == os.path.normcase(state.get("exe") or ""):

            if (state.get("mode") or "launcher") == "launcher" and _launcher_outdated(exe):
                _write_launcher(exe)
                return True
            return False
        reg = _reg()
        mode = state.get("mode") or "launcher"
        state["exe"] = exe
        _save_state(state)
        if mode == "launcher":
            _write_launcher(exe)
        for feature in enabled:
            for key, name, kind, data in _plan(feature, exe, mode, _lang()):
                if name != "" or not key.endswith("\\" + VERB):
                    reg.set(key, name, kind, data)
        reg.notify()
        log.info("default shell: re-pointed to %s", exe)
        return True

def route(raw: str) -> Tuple[str, str]:
    """``("explorer", target)`` for what MemuFiles cannot show, else ``("open", folder)``.

    Repairs ``"C:\\"`` quoting (``C:"`` after CommandLineToArgvW) and keeps UNC / long / Cyrillic paths as is."""
    target = (raw or "").strip()
    if target.endswith('"') and not target.startswith('"'):
        target = target[:-1] + "\\"
    target = target.strip('"')
    if not target:
        return "open", ""
    low = target.lower()
    if "::" in target or low.startswith("shell:") or "://" in target:
        return "explorer", target
    if len(target) == 2 and target[1] == ":":
        target += "\\"
    if not os.path.isdir(target):
        return "explorer", target
    return "open", target

def explore(target: str) -> None:
    """Hand a path to the real Explorer through the untouched ``explore`` verb (no recursion into MemuFiles)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        code = ctypes.windll.shell32.ShellExecuteW(None, "explore", target or THIS_PC, None, None, 1)
        if code > 32:
            return
    except Exception:  # noqa: BLE001
        pass
    import subprocess
    subprocess.Popen(["explorer.exe", "/e," + (target or THIS_PC)])   # noqa: S603,S607

def cli_restore() -> int:
    """``MemuFiles.exe --restore-explorer`` (the uninstaller runs it too)."""
    result = restore_all()
    try:
        from . import archive_assoc
        archive_assoc.sync(False)
    except Exception:  # noqa: BLE001
        pass
    text = ("Проводник Windows восстановлен. / Windows Explorer restored." if not result.get("problems")
            else "Восстановлено с ошибками / restored with errors: " + "; ".join(result["problems"]))
    if result.get("available") is False:
        text = "Not Windows: nothing to restore."
    try:
        if sys.stdout is not None:
            sys.stdout.write(text + "\n")
    except Exception:  # noqa: BLE001
        pass
    return 0 if not result.get("problems") else 1

def handle(body: Dict[str, Any]) -> Dict[str, Any]:
    """One entry point for both HTTP APIs (MemuFiles ``/api/default_shell``, MemuAI ``/api/pg/memufiles/default-shell``).
    Errors come back as ``{"ok": False, "error": code}`` — never an exception (Linux: ``unavailable``)."""
    body = body if isinstance(body, dict) else {}
    action = str(body.get("action") or "")
    feature = str(body.get("feature") or "")
    try:
        if action == "restore":
            return restore_all()
        if not available():
            return {"ok": False, "available": False, "error": "unavailable", "state": "unavailable"}
        if action == "enable":
            if body.get("confirmed") is not True:
                return {"ok": False, "error": "not_confirmed"}
            return enable(feature)
        if action == "disable":
            return disable(feature)
        if action == "hook":
            return set_hook(bool(body.get("on")))
        return {"ok": False, "error": "bad_action"}
    except ShellError as exc:
        return {"ok": False, "error": exc.code, "message": str(exc)[:300]}
    except OSError as exc:
        log.exception("default shell %s failed", action)
        return {"ok": False, "error": "registry", "message": str(exc)[:300]}
