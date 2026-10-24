# -*- coding: utf-8 -*-
"""Round 33 (#11, r33c-models-lists): one way to name and pick a model in every remaining place.

* Telegram bot — readable names on model buttons (``Name · full id`` when it fits), the reasoning levels of the
  current model (app/reasoning_levels.py) instead of a generic list, the chosen level saved into MemuAI's own
  ``model_efforts`` and sent with every answer.
* Mini App — ``chat_models`` rows carry the automatic name, badges, vision tri-state and reasoning levels; the
  composer's level travels chats.send → job.meta → stream params.
* MemuCode CLI — ``/model`` rows: readable name + muted full id (``dlg.Option.detail``), search by the name.
* /api/pg/modules/models — ``account`` and ``vision_state``; Studio / Translator / MemuNote / music rows readable.
* Quick Access and the Proxy model table load the shared naming script (static checks).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r33-lists-")

ROOT = Path(__file__).resolve().parents[1]


def _entry(model_id, provider="custom", model="", prefix=""):
    return SimpleNamespace(id=model_id, provider=provider, model=model or model_id, prefix=prefix, caps={},
                           enabled=True)


QWEN_LEVELS = ["auto", "off", "low", "medium", "high"]


class TelegramModelsTest(unittest.TestCase):
    def test_names_alias_first_then_readable(self):
        from app import telegram_models as tm
        with patch.object(tm, "_entry", return_value=_entry("aws9-claude-opus-4-6-thinking", prefix="aws9")):
            self.assertEqual(tm.display_name("aws9-claude-opus-4-6-thinking", {}), "Claude Opus 4.6 Thinking")
            self.assertEqual(tm.display_name("aws9-claude-opus-4-6-thinking",
                                             {"model_aliases": {"aws9-claude-opus-4-6-thinking": "Мой Опус"}}), "Мой Опус")
            self.assertEqual(tm.label("aws9-claude-opus-4-6-thinking", {}),
                             "Claude Opus 4.6 Thinking · aws9-claude-opus-4-6-thinking")
            self.assertEqual(tm.label("aws9-claude-opus-4-6-thinking", {}, 30), "Claude Opus 4.6 Thinking",
                             "too long for a button: the name alone")
            self.assertTrue(tm.label("aws9-claude-opus-4-6-thinking", {}, 10).endswith("…"))
        self.assertEqual(tm.display_name("", {}), "")

    def test_effort_choice_is_per_model_and_shared_with_memuai(self):
        from app import telegram_models as tm
        prefs = {"model_efforts": {"reform-qwen3.8-flash": "high"}, "default_effort": "low"}
        self.assertEqual(tm.chosen_effort("reform-qwen3.8-flash", prefs), "high")
        self.assertEqual(tm.chosen_effort("other", prefs), "low")
        self.assertEqual(tm.chosen_effort("other", {"default_effort": "weird"}), "auto")
        patch_ = tm.efforts_patch("reform-qwen3.8-flash", "auto", prefs)
        self.assertEqual(patch_, {"model_efforts": {}}, "auto removes the model's own value")
        self.assertEqual(tm.efforts_patch("m", "max", {})["model_efforts"], {"m": "max"})
        self.assertEqual(prefs["model_efforts"], {"reform-qwen3.8-flash": "high"}, "the input is not mutated")
        self.assertEqual(tm.effort_label("xhigh", "en"), "Extra high")
        self.assertEqual(tm.effort_label("off", "ru"), "Выкл")

    def test_effort_param_clamps_to_the_model(self):
        from app import telegram_models as tm
        with patch.object(tm, "effort_options", return_value=QWEN_LEVELS), \
                patch.object(tm, "_entry", return_value=_entry("reform-qwen3.8-flash", model="qwen3.8-flash")):
            self.assertEqual(tm.effort_param("reform-qwen3.8-flash", {"default_effort": "high"}), "high")
            self.assertEqual(tm.effort_param("reform-qwen3.8-flash", {"default_effort": "auto"}), "")
            self.assertIn(tm.effort_param("reform-qwen3.8-flash", {"default_effort": "max"}), ("high",),
                          "a level the model lacks becomes its nearest one")
        with patch.object(tm, "effort_options", return_value=[]):
            self.assertEqual(tm.effort_param("plain", {"default_effort": "high"}), "", "no control → nothing sent")

    def test_effort_options_use_reasoning_levels(self):
        from app import telegram_models as tm
        with patch.object(tm, "_entry", return_value=_entry("reform-qwen3.8-flash", model="qwen3.8-flash")), \
                patch("app.reasoning_levels.reasoning_levels", return_value=["auto", "low", "high"]) as levels:
            self.assertEqual(tm.effort_options("reform-qwen3.8-flash"), ["auto", "low", "high"])
            levels.assert_called_with("reform-qwen3.8-flash", "custom", "qwen3.8-flash")
        self.assertEqual(tm.effort_options(""), [])

    def test_real_reasoning_levels_for_a_known_family(self):
        from app import telegram_models as tm
        with patch.object(tm, "_entry", return_value=None):
            options = tm.effort_options("gpt-5.1")
        self.assertEqual(options[0], "auto")
        self.assertTrue(set(options) <= set(tm.LEVELS))

    def test_catalog_fields(self):
        from app import telegram_models as tm
        entry = _entry("xkiro-qwen/qwen3.8-max:free", prefix="xkiro", model="qwen/qwen3.8-max:free")
        with patch.object(tm, "effort_options", return_value=[]), \
                patch("app.model_catalog.vision_state", return_value={"state": "unknown", "src": "none"}):
            fields = tm.catalog_fields(entry.id, entry, {"default_effort": "high"})
        self.assertEqual(fields["auto_name"], "Qwen3.8 Max")
        self.assertEqual(fields["badges"], ["free"])
        self.assertEqual(fields["vision_state"], "unknown")
        self.assertEqual(fields["reasoning_levels"], [])
        self.assertEqual(fields["effort"], "high")


class _FakeIo:
    @staticmethod
    async def run(function, *args, **kwargs):
        return function(*args, **kwargs)


class TelegramBotModelsTest(unittest.TestCase):
    def setUp(self):
        from app import telegram_bot as tb
        self.tb = tb
        self.screens = []
        self.saved = []
        self.toasts = []
        self.session = {"profile_id": "default", "model_by_profile": {}}
        self.prefs = {"default_model": "aws9-claude-opus-4-6", "pinned_models": [], "telegram_language": "en",
                      "model_efforts": {}}

        async def screen(client, token, chat_id, text, markup, edit_id=0):
            self.screens.append((text, markup))

        async def toast(client, token, callback_id, text, alert=False):
            self.toasts.append(text)

        def save(patch_):
            self.saved.append(patch_)
            self.prefs.update(patch_)
            return dict(self.prefs)

        entries = [("aws9-claude-opus-4-6", "custom"), ("reform-qwen3.8-flash", "custom")]
        self.patches = [
            patch.object(tb, "_io", _FakeIo.run), patch.object(tb, "_screen", screen), patch.object(tb, "_toast", toast),
            patch.object(tb, "_session", lambda chat_id: ({}, self.session)),
            patch.object(tb, "_model_entries", lambda prefs=None: entries),
            patch.object(tb, "_models", lambda prefs=None: [m for m, _ in entries]),
            patch.object(tb, "save_preferences", save),
            patch("app.telegram_models._entry", lambda model_id: _entry(model_id, prefix=model_id.split("-", 1)[0])),
            patch("app.telegram_models.effort_options",
                  lambda model_id: QWEN_LEVELS if "qwen" in model_id else ["auto", "low", "medium", "high", "max"]),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_buttons_and_current_line_use_readable_names(self):
        tb = self.tb
        button = tb._model_button("aws9-claude-opus-4-6", self.prefs, "aws9-claude-opus-4-6")
        self.assertEqual(button["text"], "✓ Claude Opus 4.6 · aws9-claude-opus-4-6")
        self.assertLessEqual(len(button["text"]), 56)
        self.assertEqual(tb._model_name("reform-qwen3.8-flash", self.prefs), "Qwen3.8 Flash")
        self.assertEqual(tb._short_model("reform-qwen3.8-flash", self.prefs, 18), "Qwen3.8 Flash")
        self.assertEqual(tb._short_model("x", {"model_aliases": {"x": "Mine"}}), "Mine")
        long_id = "verylongaccount-claude-opus-4-6-thinking-20260101-preview-extended"
        self.assertLessEqual(len(tb._model_button(long_id, self.prefs, "")["text"]), 56)

    def test_models_screen_offers_the_current_models_levels(self):
        asyncio.run(self.tb._show_models(None, "t", "1", self.prefs))
        text, markup = self.screens[-1]
        self.assertIn("Claude Opus 4.6", text)
        self.assertIn("<code>aws9-claude-opus-4-6</code>", text)
        self.assertIn("Reasoning", text)
        data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        self.assertEqual([d for d in data if d.startswith("effort:")],
                         ["effort:auto", "effort:low", "effort:medium", "effort:high", "effort:max"])
        labels = [b["text"] for row in markup["inline_keyboard"] for b in row if b["callback_data"] == "effort:auto"]
        self.assertTrue(labels[0].startswith("✓"), "auto is ticked by default")

    def test_picking_a_level_saves_model_efforts_and_is_sent(self):
        tb = self.tb
        self._callback("effort:high")
        self.assertEqual(self.saved[-1], {"model_efforts": {"aws9-claude-opus-4-6": "high"}})
        self.assertEqual(tb._effort_param("aws9-claude-opus-4-6", self.prefs), {"effort": "high"})
        self.assertEqual(tb._effort_param("reform-qwen3.8-flash", {}), {})

    def test_a_level_the_model_lacks_is_refused(self):
        self._callback("effort:xhigh")
        self.assertEqual(self.saved, [])

    def _callback(self, data):
        callback = {"id": "cb", "data": data, "from": {"id": 7},
                    "message": {"message_id": 5, "chat": {"id": 1}}}
        with patch.object(self.tb, "_allowed", lambda prefs, chat_id, user_id: True):
            asyncio.run(self.tb._handle_callback(None, "t", callback, self.prefs))
        self.assertTrue(self.toasts, "the callback is answered")


class MiniAppModelsTest(unittest.TestCase):
    def test_chat_models_rows_carry_names_badges_and_levels(self):
        from app import tgapp_chat
        entries = [_entry("aws9-claude-opus-4-6", prefix="aws9"), _entry("xkiro-qwen/qwen3.8-max:free", prefix="xkiro")]
        fake_state = SimpleNamespace(enabled_models=lambda: entries, find_model=lambda i: None)
        with patch("app.state.state", fake_state), \
                patch("app.telegram_models.effort_options", lambda m: ["auto", "low", "high"] if "claude" in m else []), \
                patch("app.model_catalog.vision_state", return_value={"state": "yes", "src": "family"}):
            rows = tgapp_chat.chat_models({"model_aliases": {"aws9-claude-opus-4-6": "Опус"}})
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["aws9-claude-opus-4-6"]["name"], "Опус")
        self.assertEqual(by_id["aws9-claude-opus-4-6"]["auto_name"], "Claude Opus 4.6")
        self.assertEqual(by_id["aws9-claude-opus-4-6"]["reasoning_levels"], ["auto", "low", "high"])
        self.assertEqual(by_id["xkiro-qwen/qwen3.8-max:free"]["name"], "Qwen3.8 Max")
        self.assertEqual(by_id["xkiro-qwen/qwen3.8-max:free"]["badges"], ["free"])
        self.assertEqual(by_id["xkiro-qwen/qwen3.8-max:free"]["vision_state"], "yes")

    def test_effort_params_from_the_composer_or_memuai(self):
        from app import tgapp_chat
        job = SimpleNamespace(meta={"effort": "high"})
        with patch("app.telegram_models.effort_options", return_value=QWEN_LEVELS):
            self.assertEqual(tgapp_chat._effort_params(job, "m"), {"effort": "high"})
            self.assertEqual(tgapp_chat._effort_params(SimpleNamespace(meta={"effort": "max"}), "m"), {},
                             "a level the model lacks is not sent")
            self.assertEqual(tgapp_chat._effort_params(SimpleNamespace(meta={"effort": "auto"}), "m"), {})
        with patch("app.telegram_models.effort_param", return_value="low"), \
                patch("app.pgdata.load_preferences", return_value={}):
            self.assertEqual(tgapp_chat._effort_params(SimpleNamespace(meta={}), "m"), {"effort": "low"})

    def test_frontend_uses_levels_and_names(self):
        js = (ROOT / "app/tgapp/app.js").read_text(encoding="utf-8")
        self.assertIn("function thinkChip(C)", js)
        self.assertIn("effort: C.effort", js)
        self.assertIn('class="mid"', js)
        self.assertIn("modelBadges(m)", js)
        html = (ROOT / "app/tgapp/index.html").read_text(encoding="utf-8")
        self.assertIn('href="models.css', html)
        self.assertTrue((ROOT / "app/tgapp/models.css").is_file())
        api = (ROOT / "app/tgapp_api.py").read_text(encoding="utf-8")
        self.assertIn('effort=str(body.get("effort")', api)
        self.assertIn('"xhigh"', re.search(r'"default_effort": \{[^}]+\}', api).group(0))


class MemuCodeModelListTest(unittest.TestCase):
    def test_option_has_readable_name_and_full_id(self):
        from app.cli.commands import model_cmds
        from app.cli.llm import ModelInfo
        info = ModelInfo(id="aws9-claude-opus-4-6", provider="custom")
        option = model_cmds.model_option(info, "aws9-claude-opus-4-6", True)
        self.assertEqual(option.label, "Claude Opus 4.6")
        self.assertEqual(option.value, "aws9-claude-opus-4-6")
        self.assertEqual(option.detail, "aws9-claude-opus-4-6")
        self.assertEqual(option.hint, "✓")
        aliased = model_cmds.model_option(info, "", None, {"aws9-claude-opus-4-6": "Опус"})
        self.assertEqual(aliased.label, "Опус")
        named = model_cmds.model_option(ModelInfo(id="x-1", name="Catalogue Name"), "")
        self.assertEqual(named.label, "Catalogue Name")
        plain = model_cmds.model_option(ModelInfo(id="Opus"), "")
        self.assertEqual(plain.detail, "", "no duplicate id when the name is the id")
        self.assertEqual(model_cmds.display_name(info, ["not a dict"]), "Claude Opus 4.6")

    def test_search_finds_the_readable_name(self):
        from app.cli.commands import model_cmds
        from app.cli.llm import ModelInfo
        info = ModelInfo(id="aws9-claude-opus-4-6", provider="custom")
        option = model_cmds.model_option(info, "")
        score = model_cmds._model_matcher({info.id: info})
        self.assertIsNotNone(score("opus 4.6", option))
        self.assertIsNotNone(score("claude", option))
        self.assertIsNone(score("gemini", option))

    def test_dialog_draws_the_detail_muted(self):
        from app.cli.tui import dialogs as dlg
        dialog = dlg.Dialog(title="t", options=[dlg.Option("Claude Opus 4.6", "aws9-claude-opus-4-6",
                                                           detail="aws9-claude-opus-4-6")], filterable=True)
        parts = dialog.render_options(100)
        muted = [text for style, text, *_ in parts if style == "class:dialog.option.desc"]
        self.assertTrue(any("aws9-claude-opus-4-6" in text for text in muted), parts)

    def test_proxy_rows_keep_the_catalogue_name(self):
        src = (ROOT / "app/cli/llm.py").read_text(encoding="utf-8")
        self.assertIn('name=str(row.get("name") or "")', src)

    def test_completion_meta_starts_with_the_name(self):
        from app.cli.tui.input import _pretty_name
        from app.cli.llm import ModelInfo
        self.assertEqual(_pretty_name(ModelInfo(id="reform-glm-5.3"), "reform-glm-5.3"), "GLM-5.3")
        self.assertEqual(_pretty_name(ModelInfo(id="a", name="Named"), "a"), "Named")
        self.assertEqual(_pretty_name("reform-qwen3.8-flash", "reform-qwen3.8-flash"), "Qwen3.8 Flash")


ADMIN_MODELS = [
    {"id": "reform-qwen3.8-flash", "provider": "custom", "prefix": "reform", "model": "qwen3.8-flash", "enabled": True,
     "caps": {}},
    {"id": "reform-glm-5.3", "provider": "custom", "prefix": "reform", "model": "glm-5.3", "enabled": True,
     "caps": {"vision": False}},
]
PG_STATE = {
    "default_model": "reform-qwen3.8-flash", "image_models": [],
    "models": [
        {"id": "reform-qwen3.8-flash", "name": "Qwen3.8 Flash", "account": "Reform", "provider_label": "REFORM",
         "caps": {"vision": True, "vision_state": "unknown"}},
        {"id": "reform-glm-5.3", "name": "GLM-5.3", "account": "Reform", "caps": {}},
    ],
}


class _Proxy(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/admin/models"):
            body, code = {"models": ADMIN_MODELS}, 200
        elif self.path.startswith("/api/admin/pg/state"):
            body, code = PG_STATE, 200
        else:
            body, code = {"error": "not found"}, 404
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ModulesModelsFieldsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Proxy)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls._env = os.environ.get("MEMUAI_PROXY_URL")
        os.environ["MEMUAI_PROXY_URL"] = f"http://127.0.0.1:{cls.server.server_address[1]}"
        from app import playground_api
        cls.client = TestClient(playground_api.create_app(), base_url="http://127.0.0.1").__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.server.shutdown()
        cls.server.server_close()
        if cls._env is None:
            os.environ.pop("MEMUAI_PROXY_URL", None)
        else:
            os.environ["MEMUAI_PROXY_URL"] = cls._env

    def test_account_and_vision_state(self):
        response = self.client.post("/api/pg/modules/models", json={})
        self.assertEqual(response.status_code, 200, response.text)
        rows = {row["id"]: row for row in response.json()["models"]}
        self.assertEqual(rows["reform-qwen3.8-flash"]["name"], "Qwen3.8 Flash")
        self.assertEqual(rows["reform-qwen3.8-flash"]["account"], "Reform")
        self.assertEqual(rows["reform-qwen3.8-flash"]["vision_state"], "unknown", "the catalogue's tri-state wins")
        self.assertEqual(rows["reform-glm-5.3"]["vision_state"], "no")


class OtherListsTest(unittest.TestCase):
    def test_memunote_models_are_readable(self):
        from app.memunote import ai
        state = {"models": [{"id": "reform-glm-5.3", "provider": "custom", "caps": {}},
                            {"id": "aws9-gpt-6.1-sol", "name": "GPT-6.1 Sol", "caps": {}}]}
        with patch.object(ai, "request_json", return_value=state):
            rows = {row["id"]: row for row in ai.models("http://x")}
        self.assertEqual(rows["reform-glm-5.3"]["label"], "GLM-5.3")
        self.assertEqual(rows["aws9-gpt-6.1-sol"]["label"], "GPT-6.1 Sol")
        js = (ROOT / "app/memunote/static/js/ai.js").read_text(encoding="utf-8")
        self.assertIn("hint: m.label && m.label !== m.id ? m.id", js)

    def test_music_models_are_readable(self):
        from app import music_runtime
        self.assertEqual(music_runtime._pretty("suno-v4.5"), "Suno V4.5")

    def test_module_pickers_show_name_and_id(self):
        for name in ("studio", "design", "translator"):
            js = (ROOT / "app/module_builtins" / name / "main.js").read_text(encoding="utf-8")
            self.assertIn("`${item.name} · ${item.id}`", js, name)

    def test_quick_access_loads_the_shared_names(self):
        html = (ROOT / "app/pgstatic/quick/index.html").read_text(encoding="utf-8")
        names = html.index("/memu-model-names.js")
        self.assertLess(names, html.index("/quick/quick.js"), "names must load before quick.js")
        self.assertIn("/quick/quick-models.css", html)
        self.assertTrue((ROOT / "app/pgstatic/quick/quick-models.css").is_file())
        js = (ROOT / "app/pgstatic/quick/quick.js").read_text(encoding="utf-8")
        self.assertIn("MemuModelNames.pretty", js)
        self.assertIn('class="mid"', js)
        self.assertIn('vision_state === "unknown"', js)

    def test_proxy_window_loads_the_shared_names(self):
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('src="/model-names.js'), html.index('src="/app.js'))
        self.assertIn("/proxy-model-names.css", html)
        self.assertTrue((ROOT / "app/static/proxy-model-names.css").is_file())
        js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
        self.assertIn("function proxyModelName(m)", js)
        self.assertIn("proxyModelCell(m)", js)

    def test_build_lists(self):
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertEqual(build.count('"app.telegram_models"'), 3)
        for spec in ("MemuAI.spec", "MemuAIProxy.spec"):
            self.assertIn("'app.telegram_models'", (ROOT / spec).read_text(encoding="utf-8"), spec)
        for spec in ("MemuCode.spec", "MemuNote.spec"):
            self.assertIn("'app.model_names'", (ROOT / spec).read_text(encoding="utf-8"), spec)
        self.assertIn("'app.reasoning_levels'", (ROOT / "MemuCode.spec").read_text(encoding="utf-8"))
        import build as build_module
        self.assertIn("app.model_names", build_module.TARGETS["memucode"]["hidden"])
        self.assertIn("app.reasoning_levels", build_module.TARGETS["memucode"]["hidden"])
        self.assertIn("app.model_names", build_module.TARGETS["memunote"]["hidden"])


if __name__ == "__main__":
    unittest.main()
