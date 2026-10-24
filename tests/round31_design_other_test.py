"""Round 31 (design-other): MemuAI Proxy admin, MemuCode CLI UX, installer — the design pass.

Covers:
* providers/custom.py ``check()`` / ``_probe_key``: a gateway that publishes ``/models`` without auth no longer
  shows «подключено» for a wrong key (probe 401/403 → rejected; 429 / 402 → accepted with a note; 400 → ok);
* chat.py: the English text of «account rejected» has no Cyrillic even when the stored reason is Russian;
* admin ``/admin/api/overview`` returns ``sample_model`` (the «Подключить клиент» sample used a hard-coded id);
* the Proxy panel ships the first-run card, the Accounts empty state, the folded «Дополнительно» block and
  RU/EN strings for them (EN without Cyrillic);
* MemuCode ``short_path`` keeps the end of a long path; headless text mode keeps its CRLF file intact.

    python -m pytest tests/round31_design_other_test.py -q
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r31-design-other-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CYR = re.compile(r"[А-Яа-яЁё]")


class _Resp:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = ""
        self.headers = {}

    def json(self):
        return self._payload


class _Session:
    """Answers GET /models with 200 (public list) and POST /chat/completions with ``probe_status``."""

    def __init__(self, probe_status: int, models_status: int = 200):
        self.probe_status = probe_status
        self.models_status = models_status
        self.posts = []

    async def get(self, url, **kw):
        return _Resp(self.models_status, {"data": [{"id": "m-a"}, {"id": "m-b"}]})

    async def post(self, url, **kw):
        self.posts.append((url, kw.get("json")))
        return _Resp(self.probe_status, {"error": {"message": "x"}})


def _check(probe_status: int, models_status: int = 200, fields=None):
    from app.providers import custom as custom_mod
    from app.state import Account
    account = Account("custom", {"name": "gw", "base_url": "https://gw.test/v1", "api_key": "sk-test",
                                 **(fields or {})})
    session = _Session(probe_status, models_status)

    async def fake_session(*a, **kw):
        return session

    with patch.object(custom_mod.http, "get_session", fake_session):
        result = asyncio.run(custom_mod.CustomProvider().check(account))
    return result, session


class ProbeKey(unittest.TestCase):
    def test_rejected_key_is_not_connected(self):
        (ok, msg), session = _check(401)
        self.assertIs(ok, False)
        self.assertIn("401", msg)
        self.assertEqual(len(session.posts), 1)
        url, body = session.posts[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(body["messages"], [])            # invalid body: no tokens are spent
        self.assertEqual(body["model"], "m-a")             # the first listed model

    def test_forbidden_key(self):
        (ok, _msg), _ = _check(403)
        self.assertIs(ok, False)

    def test_rate_limited_key_is_accepted_with_note(self):
        (ok, msg), _ = _check(429)
        self.assertIs(ok, True)
        self.assertIn("429", msg)

    def test_no_balance_key_is_accepted_with_note(self):
        (ok, msg), _ = _check(402)
        self.assertIs(ok, True)
        self.assertIn("402", msg)

    def test_good_key_shows_model_count(self):
        for status in (200, 400, 422):
            (ok, msg), _ = _check(status)
            self.assertIs(ok, True, status)
            self.assertIn("моделей: 2", msg)

    def test_declared_models_win_for_probe(self):
        _, session = _check(400, fields={"models": "my-model, other"})
        self.assertEqual(session.posts[0][1]["model"], "my-model")

    def test_models_401_short_circuits(self):
        (ok, _msg), session = _check(200, models_status=401)
        self.assertIs(ok, False)
        self.assertEqual(session.posts, [])


class ChatEnglishText(unittest.TestCase):
    """chat.py builds message_en from the stored reason: a Russian reason must not leak into English."""

    def _en(self, why: str) -> str:
        src = (ROOT / "app" / "chat.py").read_text(encoding="utf-8")
        start = src.index("why_en = why if why")
        block = src[start:src.index("raise CompletionError(ru, status=503", start)]
        scope = {"re": re, "why": why, "name": "GW"}
        exec(compile("\n".join(line.strip() for line in _join(block)), "chat_en", "exec"), scope)
        return scope["en"]

    def test_russian_reason_is_translated(self):
        en = self._en("ключ отклонён (HTTP 401)")
        self.assertFalse(CYR.search(en), en)
        self.assertIn("the key was rejected", en)
        self.assertIn("Accounts tab of MemuAI Proxy", en)

    def test_unknown_russian_reason_is_dropped(self):
        en = self._en("неизвестная ошибка")
        self.assertFalse(CYR.search(en), en)

    def test_english_reason_is_kept(self):
        self.assertIn("invalid api key", self._en("invalid api key"))


def _join(block: str):
    """Re-join the multi-line statements of the snippet (they continue inside parentheses)."""
    out, cur, depth = [], "", 0
    for line in block.splitlines():
        cur += " " + line.strip()
        depth += line.count("(") - line.count(")")
        if depth <= 0 and cur.strip():
            out.append(cur.strip())
            cur, depth = "", 0
    return out


class AdminOverviewSample(unittest.TestCase):
    def test_sample_model_in_overview(self):
        src = (ROOT / "app" / "admin_api.py").read_text(encoding="utf-8")
        self.assertIn('"sample_model":', src)
        js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("sample_model", js, "the panel must use overview.sample_model in the client sample")

    def test_sample_model_live(self):
        try:
            from fastapi.testclient import TestClient
            from app.config import config_store
            from app.main import create_app
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"no proxy app: {exc}")
        with patch.object(config_store, "_runtime_host", "127.0.0.1"), \
                patch.object(config_store, "_runtime_port", 8317), \
                TestClient(create_app(), base_url="http://127.0.0.1:8317") as client:
            resp = client.get("/api/admin/overview")
            if resp.status_code in (401, 403):
                self.skipTest("admin API needs a session here")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("sample_model", data)
            self.assertIsInstance(data["sample_model"], str)


class PickerAccountName(unittest.TestCase):
    """The main window's model picker named every own API «custom»: pg_state now carries the account's name."""

    def test_custom_account_label(self):
        from app import admin_api
        from app.state import Account, state
        acc = Account("custom", {"name": "XKiro", "base_url": "https://gw.test/v1", "api_key": "k"}, label="")
        acc2 = Account("custom", {"name": "my or", "base_url": "https://gw2.test/v1"}, label="OpenRouter")
        with patch.dict(state.accounts, {acc.id: acc, acc2.id: acc2}, clear=True):
            self.assertEqual(admin_api._custom_account_label("xkiro"), "XKiro")
            self.assertEqual(admin_api._custom_account_label("my-or"), "OpenRouter")
            self.assertEqual(admin_api._custom_account_label("nope"), "custom")
        src = (ROOT / "app" / "admin_api.py").read_text(encoding="utf-8")
        self.assertIn('row["provider_label"] = _custom_account_label(m.prefix)', src)
        # the helper must not have swallowed the route decorator of pg_state
        self.assertIn('@router.get("/pg/state")\nasync def pg_state', src)
        app_js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn("esc(x.provider_label || x.provider)", app_js)


class ProxyPanelEmptyStates(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        self.css = (ROOT / "app" / "static" / "proxy-r31.css").read_text(encoding="utf-8")
        self.i18n = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")

    def test_markup_and_css_are_hooked(self):
        self.assertIn('href="/proxy-r31.css', self.html)
        self.assertIn('id="start-card"', self.html)
        self.assertIn("acc-empty", self.js)
        self.assertIn('class="acc-more"', self.js)
        for sel in (".start-card", ".acc-empty", "details.acc-more"):
            self.assertIn(sel, self.css)
        self.assertFalse(re.search(r"#[0-9a-fA-F]{6}\b", self.css), "use theme tokens, not fixed colours")

    def test_i18n_keys_have_english_without_cyrillic(self):
        for key in ("home.start.title", "home.start.cta", "acc.empty.cta"):
            found = re.findall(r'["\']' + re.escape(key) + r'["\']\s*:\s*["\']([^"\']*)["\']', self.i18n)
            self.assertTrue(found, f"{key} missing in i18n.js")
            self.assertTrue(any(not CYR.search(v) and v for v in found), f"{key}: no English value")


class MiniAppSmallComputer(unittest.TestCase):
    """At 1024 px the settings row «Тема и цвета · Классическая» was cut on both sides (live check: tg_shots, pc size)."""

    def test_list_column_and_title_rules(self):
        tg = ROOT / "app" / "tgapp"
        layout = (tg / "layout.css").read_text(encoding="utf-8")
        self.assertRegex(layout, r'@media \(max-width: 1180px\) \{ html\[data-device="desktop"\] \{ --lw: clamp\(260px, var\(--list-w\), 37vw\); \} \}')
        css = (tg / "app.css").read_text(encoding="utf-8")
        self.assertIn(".cell-body:has(> .cell-title:only-child):has(+ .cell-value) { flex: 1 0 auto; max-width: calc(100% - 124px); }", css)
        html = (tg / "index.html").read_text(encoding="utf-8")
        # Round 34: later rounds bump the cache versions — only check that both stylesheets are versioned.
        self.assertRegex(html, r'href="app\.css\?v=[\w.-]+"')
        self.assertRegex(html, r'href="layout\.css\?v=[\w.-]+"')


class CliShortPath(unittest.TestCase):
    def test_short_path_keeps_the_end(self):
        from app.cli.tui.banner import short_path
        p = "/workspace/projects/b68b5ef2-9e75-4733-b755-1752fc192a52/work/scratch-design-other/proj"
        out = short_path(p, 40)
        self.assertLessEqual(len(out), 40)
        self.assertTrue(out.startswith("…"))
        self.assertTrue(out.endswith("/scratch-design-other/proj"))
        self.assertEqual(short_path("/a/b", 40), "/a/b")
        win = r"C:\Users\yono\Documents\Projects\very-long-project-name\src"
        self.assertTrue(short_path(win, 30).endswith(r"\src"))
        self.assertLessEqual(len(short_path(win, 30)), 30)

    def test_headless_keeps_crlf(self):
        raw = (ROOT / "app" / "cli" / "headless.py").read_bytes()
        lines = raw.split(b"\n")[:-1]
        self.assertTrue(all(line.endswith(b"\r") for line in lines), "headless.py lost its CRLF line endings")

    def test_headless_single_blank_between_segments(self):
        import io
        from app.cli.headless import HeadlessUI
        ui = HeadlessUI("text")
        ui.out = io.StringIO()

        class A:
            depth = 0
        for seg in ("First.\n\n\n", "\n\nSecond.\n\n"):
            for ch in (seg[:3], seg[3:]):
                ui.stream_text(A, ch)
            ui.stream_finished(A, seg, "", None)
        self.assertEqual(ui.out.getvalue(), "First.\n\nSecond.\n")


if __name__ == "__main__":
    unittest.main()
