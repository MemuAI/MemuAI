"""MemuUtils bridge API (app/utils_kit/api.py): operation shapes, the limits for third-party modules (no system /
network / folder / dangerous tools, no settings or jobs, only their own results, saving only into picked folders)
and the ``/api/pg/utils/call`` endpoint permission check (``utils`` permission, built-in = trusted)."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-utils-api-"))

from app.utils_kit import api  # noqa: E402
from app.utils_kit.core import REGISTRY, UtilError  # noqa: E402


def call(op, args=None, **kw):
    return asyncio.run(api.call_async(op, args or {}, **kw))


def test_ops_shapes():
    cat = call("catalog")
    assert len(cat["utils"]) >= 150 and cat["skill"]["tools"] == 0 and len(cat["recommended"]) > 50
    res = call("run", {"util": "text.case", "text": "hello world", "opts": {"mode": "upper"}})
    assert res["ok"] and res["run"], res
    info = call("run.get", {"run": res["run"]})
    assert info["util"] == "text.case" and info["run"] == res["run"]
    assert "HELLO WORLD" in str(info["result"])
    live = call("run", {"util": "text.case", "text": "abc", "live": True})
    assert live["ok"]
    saved = call("settings.set", {"ai_tools": ["text.case", "gen.uuid"]})
    assert set(saved) == {"settings", "skill"} and saved["skill"]["tools"] == 2, saved
    assert call("settings.get")["ai_tools"] == ["text.case", "gen.uuid"]
    preview = call("skill.preview")
    assert "text.case" in preview["text"] and preview["tools"] == 2
    starter = call("settings.set", {"ai_tools": cat["recommended"]})
    assert starter["skill"]["tools"] == len(cat["recommended"])
    back = call("settings.set", {"ai_tools": None})
    assert back["skill"]["tools"] == 0
    stopped = call("jobs.stop", {"all": True})
    assert set(stopped) == {"stopped", "jobs"}
    assert call("jobs.stop", {})["stopped"] == 0
    assert "text.case" in call("help", {"util": "text.case"})["text"]
    with pytest.raises(UtilError):
        call("nope")
    with pytest.raises(UtilError):
        call("pick.folder")  # no MemuAI window in a test


def test_third_party_limits(tmp_path):
    kw = {"trusted": False, "owner": "third-party"}
    with pytest.raises(UtilError):
        call("settings.set", {"ai_tools": []}, **kw)
    with pytest.raises(UtilError):
        call("jobs.stop", {"all": True}, **kw)
    for util_id in ("sys.info", "net.dns", "sys.foldersize"):
        assert REGISTRY[util_id].cat in api.UNTRUSTED_CATS or REGISTRY[util_id].pick
        with pytest.raises(UtilError):
            call("run", {"util": util_id, "text": "example.com"}, **kw)
    mine = call("run", {"util": "gen.uuid", "opts": {"count": 2}}, **kw)
    assert mine["ok"]
    assert call("run.get", {"run": mine["run"]}, **kw)["util"] == "gen.uuid"
    window = call("run", {"util": "text.case", "text": "secret text"})
    with pytest.raises(UtilError):
        call("run.get", {"run": window["run"]}, **kw)
    with pytest.raises(UtilError):
        call("run.get", {"run": mine["run"]}, trusted=False, owner="another-module")
    qr = call("run", {"util": "gen.qr", "text": "https://example.com"})
    out = next(o for o in qr["outputs"] if o.get("ref"))
    with pytest.raises(UtilError):
        call("run", {"util": "image.info", "files": [{"ref": out["ref"]}]}, **kw)
    with pytest.raises(UtilError):
        call("out.get", {"run": qr["run"], "name": out["name"]}, **kw)
    own_qr = call("run", {"util": "gen.qr", "text": "https://example.org"}, **kw)
    own_out = next(o for o in own_qr["outputs"] if o.get("ref"))
    assert call("out.get", {"run": own_qr["run"], "name": own_out["name"]}, **kw)["data"].startswith("data:image/")
    again = call("run", {"util": "image.info", "files": [{"ref": own_out["ref"]}]}, **kw)
    assert again["ok"]
    with pytest.raises(UtilError):
        call("out.save", {"run": own_qr["run"], "name": own_out["name"], "folder": str(tmp_path)}, **kw)
    api._PICKED.add(str(tmp_path.resolve()))
    try:
        saved = call("out.save", {"run": own_qr["run"], "name": own_out["name"], "folder": str(tmp_path)}, **kw)
        assert Path(saved["path"]).parent == tmp_path.resolve() or Path(saved["path"]).is_file()
    finally:
        api._PICKED.discard(str(tmp_path.resolve()))
    with pytest.raises(UtilError):
        call("out.reveal", {"path": "/etc/passwd"}, **kw)


def test_endpoint_permissions(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import modules_host as host
    api._ALLOWED.clear()
    with patch("app.modules_host.default_root", return_value=tmp_path):
        host.create_module(tmp_path, {"id": "no-utils", "title": "N", "js": "void 0", "persist": [], "permissions": []})
        host.create_module(tmp_path, {"id": "with-utils", "title": "W", "js": "void 0", "persist": [], "permissions": ["utils"]})
        app = FastAPI()
        api.register(app)
        client = TestClient(app, base_url="http://127.0.0.1")
        body = {"op": "run", "args": {"util": "text.case", "text": "ab"}}
        assert client.post("/api/pg/utils/call", json={**body, "module_id": "no-utils"}).status_code == 403
        assert client.post("/api/pg/utils/call", json={**body, "module_id": "never-existed"}).status_code == 403
        ok = client.post("/api/pg/utils/call", json={**body, "module_id": "with-utils"})
        assert ok.status_code == 200 and ok.json()["result"]["ok"], ok.text
        limited = client.post("/api/pg/utils/call", json={"op": "settings.set", "args": {"ai_tools": []}, "module_id": "with-utils"})
        assert limited.status_code == 400 and "MemuUtils" in limited.json()["error"]
        system = client.post("/api/pg/utils/call", json={"op": "run", "args": {"util": "sys.info"}, "module_id": "with-utils"})
        assert system.status_code == 400
        builtin = client.post("/api/pg/utils/call", json={"op": "run", "args": {"util": "sys.info"}, "module_id": "utils", "lang": "en"})
        assert builtin.status_code == 200, builtin.text
        assert client.post("/api/pg/utils/call", json={"op": "rm -rf", "module_id": "utils"}).status_code == 400
        assert client.post("/api/pg/utils/call", content=b"[1]", headers={"content-type": "application/json"}).status_code == 400
    api._ALLOWED.clear()


def test_playground_registers_the_route():
    source = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
    assert "from .utils_kit import api as utils_api" in source
    bridge = (ROOT / "app" / "modules_host.py").read_text(encoding="utf-8")
    assert "utils_call" in bridge
    modules_js = (ROOT / "app" / "pgstatic" / "modules.js").read_text(encoding="utf-8")
    assert 'case "utils_call"' in modules_js and "/api/pg/utils/call" in modules_js
    assert 'utils_call: "utils"' in modules_js
