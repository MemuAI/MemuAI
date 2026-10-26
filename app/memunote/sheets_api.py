"""MemuNote (Round 40, r40-note): save edited cells of an ``.xlsx`` — ``POST /api/sheet_save``.

``{path, mtime?, force?, backup?, changes, target?}`` (``changes`` as in :mod:`formats.xlsx_edit`) → the file is patched in
place (only the touched cells/parts change), written atomically, and read back → ``{ok, mtime, size, sheets}``.
``target`` = «Save as»: the patched copy goes there and ``path`` stays untouched. A file changed on disk since it was opened answers ``{conflict: true}`` like the other save routes. ``.ods`` (and
anything that is not an Excel package) is refused: those stay read-only. Hooked in by :func:`register`.
"""
from __future__ import annotations

import os

def api_sheet_save(self, method: str) -> None:
    from .formats.sheets import read_xlsx
    from .formats.textio import atomic_write
    from .formats.xlsx_edit import SheetEditError, patch_xlsx
    from .server import _clean_path, _stat
    if method != "POST":
        self._error("method not allowed", 405)
        return
    body = self._json_body()
    path = _clean_path(body.get("path", ""))
    if os.path.splitext(path)[1].lower() not in (".xlsx", ".xlsm"):
        self._error("not_editable", 400)
        return
    if not self.app.allowed(path):
        self._error("not opened", 403)
        return
    target = _clean_path(body["target"]) if body.get("target") else path
    if os.path.splitext(target)[1].lower() not in (".xlsx", ".xlsm"):
        self._error("not_editable", 400)
        return
    current = _stat(path)
    expected = int(body.get("mtime") or 0)
    if target == path and not body.get("force") and expected and current["exists"] and abs(current["mtime"] - expected) > 2:
        self._json({"ok": False, "conflict": True, **current})
        return
    with open(path, "rb") as fh:
        data = fh.read()
    changes = body.get("changes") if isinstance(body.get("changes"), dict) else {}
    try:
        out = patch_xlsx(data, changes)
        sheets = read_xlsx(out)["sheets"]
    except (SheetEditError, KeyError, OSError) as exc:
        self._error(f"cannot save the workbook: {exc}", 400)
        return
    atomic_write(target, out, keep_backup=bool(body.get("backup")))
    self.app.allow(target)
    self._json({"ok": True, **_stat(target), "path": target, "sheets": sheets, "new_sheets": [s.get("name") for s in changes.get("new_sheets") or []]})

def register(handler_cls) -> None:
    handler_cls.api_sheet_save = api_sheet_save
