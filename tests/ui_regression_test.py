from __future__ import annotations

import hmac
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app import pgdata
from app.playground_api import _parse_duckduckgo_results, _render_notification_wav

ROOT = Path(__file__).resolve().parents[1]

class DesktopLayoutContractTest(unittest.TestCase):
    def test_native_frame_keeps_resize_with_styled_internal_appbar(self) -> None:
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "app" / "pgstatic" / "style.css").read_text(encoding="utf-8")
        window = (ROOT / "app" / "desktop.py").read_text(encoding="utf-8")
        self.assertIn('class="topbar appbar"', html)
        self.assertIn('class="compact-toolbar"', html)
        self.assertIn(".appbar {", css)
        self.assertIn("#app { display: flex; height: 100vh; }", css)
        self.assertIn("frameless=False", window)
        self.assertIn("easy_drag=False", window)
        self.assertIn("resizable=True", window)
        self.assertNotIn("frameless=True", window)

    def test_low_window_navigation_scrolls_above_profile(self) -> None:
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        scroll_start = html.index('<div class="side-scroll">')
        scroll_end = html.index('</div>\n    </div>\n    <div class="side-bottom">', scroll_start)
        nav = html.index('<div class="side-nav">')
        self.assertLess(scroll_start, nav)
        self.assertLess(nav, scroll_end)

    def test_startup_cover_waits_for_hydrated_state(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        boot = js[js.index("async function bootApp()") : js.index("/* ==================== MEMUAI V3.0", js.index("async function bootApp()"))]
        self.assertLess(boot.index("await refreshState()"), boot.index("finishBootScreen()"))
        self.assertNotIn("notify_badge", js)

    def test_modal_and_preview_security_contracts(self) -> None:
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "app" / "pgstatic" / "style.css").read_text(encoding="utf-8")
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn("position: fixed; inset: 0; z-index: 200", css)
        self.assertIn('role="dialog" aria-modal="true"', html)
        self.assertNotIn("allow-same-origin", html)
        self.assertIn('sandbox="allow-scripts allow-modals"', html)
        self.assertNotIn('onclick="openLivePreview(', js)
        self.assertNotIn('onclick="executeWorkCommand(', js)
        self.assertNotIn("window.open(url", js)
        self.assertIn('const modalOpen = $("#panel-back")?.classList.contains("visible")', js)
        self.assertIn("if (modalOpen) {", js)

    def test_focus_mode_and_accessible_preview_resizer_are_wired(self) -> None:
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "app" / "pgstatic" / "style.css").read_text(encoding="utf-8")
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="btn-focus"', html)
        self.assertIn('role="separator" tabindex="0"', html)
        self.assertIn("function initLivePreviewResizer()", js)
        self.assertIn('id:"focus", value:"Ctrl+Shift+F"', js)
        self.assertIn(".focus-mode .side", css)
        self.assertNotIn(".switch input { display: none; }", css)

    def test_dynamic_skill_ids_never_enter_inline_javascript(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("onclick=\"editSkill('${", js)
        self.assertNotIn("onclick=\"deleteCustomSkill('${", js)
        self.assertIn('data-skill-edit="${esc(s.id)}"', js)

    def test_malformed_unicode_cannot_break_message_rendering(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const safeEncodeURIComponent =", js)
        self.assertIn("safeEncodeURIComponent(cleanCode)", js)

    def test_generated_schedules_require_explicit_user_application(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        extraction = js[js.index("async function extractSchedules") : js.index("function scheduleTiming")]
        self.assertIn('assistant.scheduleActions = { status: "pending"', extraction)
        self.assertNotIn('await api("/api/pg/schedules"', extraction)
        self.assertIn("async function applyScheduleActions", js)
        self.assertIn("data-schedule-apply", js)

    def test_all_streams_share_crlf_and_eof_safe_sse_parser(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn("async function* readSSE(body", js)

        self.assertIn("SSE_STALL_MS", js)
        self.assertIn("buffer.split(/\\r?\\n\\r?\\n/)", js)
        self.assertIn("buffer += decoder.decode();", js)
        self.assertEqual(js.count("body.getReader()"), 1)
        self.assertEqual(js.count("for await (const data of readSSE(resp.body))"), 3)
        self.assertNotIn("scrollTop = 1e9", js)

    def test_assistant_activity_shares_one_trail_component(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "app" / "pgstatic" / "style.css").read_text(encoding="utf-8")

        self.assertIn("function assistantTrail(", js)
        self.assertIn('return `<div class="trail">', js)
        for hook in ('data-work-preview', 'data-work-apply', 'data-work-delete-apply',
                     'data-command-run', 'data-schedule-preview', 'data-schedule-apply',
                     'data-browser-show', 'data-computer-show', 'data-computer-start',
                     'data-thought-elapsed'):
            self.assertIn(hook, js, hook)
        self.assertNotIn('class="work-change ', js)
        self.assertNotIn('class="agent-run"', js)
        self.assertIn('<div class="answer">', js)
        self.assertIn(".trail::before", css)
        self.assertIn(".step-dot", css)

        self.assertIn('root?.querySelector(".body")', js)

    def test_work_reads_files_through_tools_not_through_the_prompt(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")

        self.assertIn("<project_structure", js)
        self.assertNotIn("<relevant_project_files>", js)
        self.assertIn("WORK_TOOLS_PROMPT", js)
        for tool in ("work_tree", "work_read", "work_search", "work_outline", "work_patch"):
            self.assertIn(tool, js, tool)
        self.assertIn('tool.startswith("work_")', api)
        self.assertIn("work_tools.WRITE_TOOLS", api)

        self.assertIn("TOOL_ROUND_LIMIT", js)
        self.assertNotIn("_toolsResumed", js)

    def test_web_search_is_a_tool_the_model_decides_to_call(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        chat = (ROOT / "app" / "chat.py").read_text(encoding="utf-8")

        self.assertNotIn("найди в сети", js)
        self.assertIn("<web_tools>", js)
        self.assertIn("web_search {query, queries?, limit?, read?}", js)
        self.assertIn("web_fetch {url, limit?}", js)
        self.assertIn('tool in ("web_search", "web_fetch")', api)
        self.assertIn('params.get("tools_available")', chat)
        self.assertIn('chunk.object === "memuai.event"', js)

    def test_memucode_is_a_separate_project_mode(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "app" / "pgstatic" / "style.css").read_text(encoding="utf-8")
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn('id="nav-code"', html)
        self.assertIn("CODE_MODE_PROMPT", js)
        self.assertIn("function isProjectMode(", js)

        self.assertIn("body.code-mode", css)
        self.assertIn("grid-column: 1 / -1", css)
        self.assertIn('id="codebar"', html)
        self.assertIn('id="code-explorer"', html)
        self.assertIn('id="code-dock"', html)
        self.assertIn("memucode.js", html)
        self.assertIn("function codeSkillsPrompt", (ROOT / "app" / "pgstatic" / "memucode.js").read_text(encoding="utf-8"))
        self.assertIn("sound-settings-divider", (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8"))
        self.assertIn('"code_intro_seen": False', (ROOT / "app" / "pgdata.py").read_text(encoding="utf-8"))
        self.assertIn('tool.startswith("code_")', api)
        self.assertIn("/api/work/auto-project", api)
        self.assertIn("ensureAutoProject", js)

        self.assertIn("(c.project_id || \"\") === activeProject", js)

    def test_memucode_edits_are_reversible_and_tools_are_extensible(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")

        self.assertIn("session: codeSessionId()", js)
        self.assertIn("code_checkpoints.require_snapshot", api)
        self.assertIn("/api/code/rollback", api)
        self.assertIn("function showCodeChanges(", js)

        self.assertIn('tool.startswith("mcp_")', api)
        self.assertIn("/api/code/mcp", api)
        self.assertIn("function mcpPromptBlock(", js)
        self.assertIn("function showMcpPanel(", js)

        self.assertIn("CODE_SLASH_COMMANDS", js)
        for command in ("/review", "/test", "/fix", "/explain", "/refactor",
                        "/commit", "/diff", "/undo", "/mcp"):
            self.assertIn(f'"{command}"', js, command)

    def test_a_cut_off_answer_can_be_continued(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function continueAnswer(", js)
        self.assertIn("<answer_so_far>", js)
        self.assertIn("<previous_reasoning_tail>", js)
        self.assertIn("data-continue", js)

    def test_profile_pin_is_verified_server_side(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("/verify-pin`,", js)
        self.assertIn('profiles/{profile_id}/verify-pin', api)
        self.assertNotIn("lockInput.value === profile.pin", js)
        self.assertNotIn('value="${esc(activeProfile.pin', js)

    def test_file_and_agent_context_stays_out_of_the_system_prompt(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const untrustedContext = []", js)
        self.assertIn("<context_safety>", js)
        self.assertIn("function profileReferenceContext(", js)
        self.assertIn("untrusted personalization data", js)
        self.assertIn('role: "user",\n        content: "<supplemental_context>', js)
        self.assertNotIn('base += "\\n\\n<saved_memory>', js)
        self.assertNotIn('base += "\\n\\n<user_profile>', js)
        self.assertNotIn('sys += "\\n\\n<relevant_project_files>', js)
        self.assertNotIn('sys += "\\n\\n<attached_files>', js)
        self.assertNotIn('sys += "\\n\\n<subagent_notes>', js)

    def test_agent_orchestration_search_and_toasts_keep_the_new_contracts(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        genspark = (ROOT / "app" / "providers" / "genspark.py").read_text(encoding="utf-8")
        proxy_ui = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        proxy_css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn('id="st-agent-model"', js)
        self.assertNotIn('id="st-agents-mode"', js)
        self.assertIn('P("search"', js)
        self.assertIn('data-set-tab="${p.id}"', js)
        self.assertIn('"/api/pg/search/run"', api)
        self.assertIn("web_search {query, queries?, limit?, read?}", js)
        self.assertIn('id="st-search-provider"', js)
        self.assertIn("openSearchModeMenu", js)
        render_caps = js[js.index("function renderCaps()") : js.index("/* ------------------------------ чаты")]
        self.assertIn("ts.oncontextmenu = openSearchModeMenu", render_caps)
        self.assertIn('button.closest(".settings-body, .panel")', js)

        self.assertIn("websearch.run_engine(provider", api)
        self.assertNotIn("api.tavily.com", api)

        self.assertNotIn("/api/pg/agents/run", js)
        self.assertNotIn("/api/pg/agents/run", api)
        self.assertNotIn("agent_synthesis", js)
        self.assertNotIn("agent_synthesis", genspark)
        self.assertNotIn("<original_request>", api)
        self.assertIn("toastContainer.children.length >= 3", js)
        self.assertIn("dataset.toastKey", js)
        self.assertIn("logsExpanded = new Set", proxy_ui)
        self.assertIn("data-request-id", proxy_ui)
        self.assertIn('input[type="url"]', proxy_css)

    def test_schedule_delivery_and_prompt_cache_controls_are_visible(self) -> None:
        js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="st-prompt-caching"', js)
        self.assertIn('id="st-email-enabled"', js)
        self.assertIn('id="se-notify-telegram"', js)
        self.assertIn('id="se-notify-email"', js)

class NotificationSoundContractTest(unittest.TestCase):
    def test_pcm_sound_has_valid_header_and_faded_silence(self) -> None:
        payload = _render_notification_wav("gentle", 55)
        with wave.open(io.BytesIO(payload), "rb") as sound:
            self.assertEqual(sound.getnchannels(), 1)
            self.assertEqual(sound.getsampwidth(), 2)
            self.assertEqual(sound.getframerate(), 44100)
            frames = sound.readframes(sound.getnframes())
        self.assertEqual(frames[:2], b"\0\0")
        self.assertEqual(frames[-2:], b"\0\0")
        self.assertNotEqual(set(frames), {0})

    def test_zero_volume_is_silent(self) -> None:
        payload = _render_notification_wav("warm", 0)
        with wave.open(io.BytesIO(payload), "rb") as sound:
            self.assertEqual(set(sound.readframes(sound.getnframes())), {0})

class ProfileLifecycleContractTest(unittest.TestCase):
    def test_profile_pin_is_protected_at_rest_and_never_returned_to_the_ui(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(pgdata, "MEMU_DIR", root):
                saved = pgdata.save_profiles({
                    "active_id": "locked",
                    "items": [{"id": "locked", "name": "Locked", "pin": "2468"}],
                })
                raw = json.loads((root / "profiles.json").read_text(encoding="utf-8"))
                stored_pin = raw["items"][0]["pin"]

                self.assertNotIn("pin", saved["items"][0])
                self.assertTrue(saved["items"][0]["has_pin"])
                self.assertNotIn("pin", pgdata.load_profiles()["items"][0])
                self.assertTrue(pgdata.load_profiles()["items"][0]["has_pin"])
                self.assertNotEqual(stored_pin, "2468")
                self.assertTrue(stored_pin.startswith(pgdata._PIN_HASH_PREFIX))
                self.assertNotIn("_pin_cipher", raw["items"][0])
                self.assertTrue(pgdata.verify_profile_pin("locked", "2468"))

                pgdata.save_profiles({"active_id": "locked", "items": [
                    {"id": "locked", "name": "Renamed", "has_pin": True}
                ]})
                self.assertTrue(pgdata.verify_profile_pin("locked", "2468"))
                cleared = pgdata.save_profiles({"active_id": "locked", "items": [
                    {"id": "locked", "name": "Renamed", "clear_pin": True}
                ]})
                self.assertFalse(cleared["items"][0]["has_pin"])

    def test_legacy_plaintext_pin_migrates_on_timing_safe_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "profiles.json").write_text(json.dumps({
                "active_id": "locked",
                "items": [{"id": "locked", "name": "Locked", "pin": "2468"}],
            }), encoding="utf-8")
            with patch.object(pgdata, "MEMU_DIR", root), \
                 patch("app.pgdata.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
                public = pgdata.load_profiles()["items"][0]
                self.assertNotIn("pin", public)
                self.assertTrue(public["has_pin"])
                still_legacy = json.loads((root / "profiles.json").read_text(encoding="utf-8"))
                self.assertEqual(still_legacy["items"][0]["pin"], "2468")
                self.assertFalse(pgdata.verify_profile_pin("locked", "0000"))
                self.assertIsInstance(compare.call_args.args[0], bytes)
                self.assertIsInstance(compare.call_args.args[1], bytes)
                self.assertEqual(len(compare.call_args.args[0]), 32)
                self.assertEqual(len(compare.call_args.args[1]), 32)
                migrated = json.loads((root / "profiles.json").read_text(encoding="utf-8"))
                self.assertTrue(migrated["items"][0]["pin"].startswith(pgdata._PIN_HASH_PREFIX))
                self.assertNotIn("_pin_cipher", migrated["items"][0])
                self.assertTrue(pgdata.verify_profile_pin("locked", "2468"))

    def test_unsafe_and_colliding_profile_ids_are_migrated_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "profiles" / "foo"
            legacy.mkdir(parents=True)
            (legacy / "chats.json").write_text(json.dumps([
                {"id": "legacy-chat", "messages": [{"role": "user", "content": "saved"}]}
            ]), encoding="utf-8")
            (root / "profiles.json").write_text(json.dumps({
                "active_id": "foo?",
                "items": [
                    {"id": "foo!", "name": "First"},
                    {"id": "foo?", "name": "Second"},
                    {"id": "Profile", "name": "Third"},
                    {"id": "profile", "name": "Fourth"},
                    {"name": "Missing"},
                ],
            }), encoding="utf-8")
            with patch.object(pgdata, "MEMU_DIR", root):
                state = pgdata.load_profiles()
                ids = [item["id"] for item in state["items"]]
                by_name = {item["name"]: item["id"] for item in state["items"]}

                self.assertEqual(len(ids), len({item.casefold() for item in ids}))
                self.assertTrue(all(pgdata._valid_profile_id(item) for item in ids))
                self.assertEqual(state["active_id"], by_name["Second"])
                self.assertEqual(pgdata.load_chats(by_name["First"])[0]["id"], "legacy-chat")
                self.assertEqual(pgdata.load_chats(by_name["Second"])[0]["id"], "legacy-chat")
                self.assertTrue(legacy.exists(), "legacy data remains as a rollback copy")

    def test_delete_profile_removes_only_its_scoped_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(pgdata, "MEMU_DIR", root):
                pgdata.save_profiles({
                    "active_id": "second",
                    "items": [
                        {"id": "default", "name": "Main"},
                        {"id": "second", "name": "Second", "pin": "1234"},
                    ],
                })
                pgdata.save_chats([{"id": "main-chat", "messages": [{"role": "user", "content": "main"}]}], "default")
                pgdata.save_chats([{"id": "second-chat", "messages": [{"role": "user", "content": "second"}]}], "second")

                state = pgdata.delete_profile("second")

                self.assertEqual(state["active_id"], "default")
                self.assertEqual([item["id"] for item in state["items"]], ["default"])
                self.assertEqual(pgdata.load_chats("default")[0]["id"], "main-chat")
                self.assertFalse((root / "profiles" / "second").exists())

    def test_cannot_delete_only_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(pgdata, "MEMU_DIR", Path(temp)):
                pgdata.save_profiles({"active_id": "default", "items": [{"id": "default", "name": "Main"}]})
                with self.assertRaisesRegex(ValueError, "единственный"):
                    pgdata.delete_profile("default")

class PreferenceNormalizationContractTest(unittest.TestCase):
    def test_corrupted_preferences_are_safely_normalized_on_load_and_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.mkdir(parents=True, exist_ok=True)
            (root / "preferences.json").write_text(json.dumps({
                "font_scale": "huge",
                "agent_count": "many",
                "notify_sound_volume": "loud",
                "hidden_models": 17,
                "chat_folders": "not-a-list",
                "model_aliases": ["not-a-map"],
                "sidebar_visible": "false",
                "web_search_mode": "broken",
                "custom_search_provider": "broken",
                "custom_search_max_results": 999,
                "smtp_port": "bad",
                "smtp_security": "obsolete",
            }), encoding="utf-8")
            with patch.object(pgdata, "MEMU_DIR", root):
                preferences = pgdata.load_preferences()
                self.assertEqual(preferences["font_scale"], 100)
                self.assertEqual(preferences["agent_count"], 2)
                self.assertEqual(preferences["notify_sound_volume"], 55)
                self.assertEqual(preferences["hidden_models"], [])
                self.assertEqual(preferences["chat_folders"], [])
                self.assertEqual(preferences["model_aliases"], {})
                self.assertFalse(preferences["sidebar_visible"])
                self.assertEqual(preferences["web_search_mode"], "auto")
                self.assertEqual(preferences["custom_search_provider"], "auto")
                self.assertEqual(preferences["custom_search_max_results"], 10)
                self.assertEqual(preferences["smtp_port"], 587)
                self.assertEqual(preferences["smtp_security"], "starttls")
                saved = pgdata.save_preferences({"font_scale": 999, "focus_mode": "true"})
                self.assertEqual(saved["font_scale"], 125)
                self.assertTrue(saved["focus_mode"])
                self.assertFalse(saved["work_auto_apply"])

    def test_duckduckgo_html_results_are_safely_normalized(self) -> None:
        page = """<div class="result">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fguide"><b>Useful</b> guide</a>
          <a class="result__snippet">A <b>short</b> &amp; useful snippet.</a>
        </div>"""
        self.assertEqual(_parse_duckduckgo_results(page, 3), [{
            "title": "Useful guide",
            "url": "https://example.com/guide",
            "snippet": "A short & useful snippet.",
        }])

    def test_corrupted_auxiliary_data_cannot_break_desktop_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "memory.json").write_text(
                json.dumps({"enabled": "false", "facts": {"bad": "shape"}}), encoding="utf-8"
            )
            (root / "telegram_state.json").write_text(json.dumps({
                "sessions": {"42": {
                    "profile_id": "default", "chat_by_profile": ["bad"],
                    "model_by_profile": 17,
                }}
            }), encoding="utf-8")
            (root / "workspaces.json").write_text(json.dumps({
                "active_id": "work", "items": [{
                    "id": "work", "path": 123, "added": "not-a-number",
                }]
            }), encoding="utf-8")
            (root / "prompts.json").write_text(
                json.dumps({"schema_version": "broken", "prompts": 17}), encoding="utf-8"
            )
            (root / "profiles.json").write_text(json.dumps({
                "active_id": "default", "items": [{
                    "id": "default", "name": "Main",
                    "memory": {"enabled": True, "facts": 17}, "schedules": "bad",
                }]
            }), encoding="utf-8")
            with patch.object(pgdata, "MEMU_DIR", root):
                self.assertEqual(pgdata.load_memory(), {"enabled": False, "facts": []})
                telegram = pgdata.load_telegram_state()["sessions"]["42"]
                self.assertEqual(telegram["chat_by_profile"], {})
                self.assertEqual(telegram["model_by_profile"], {})
                workspace = pgdata.load_workspaces()
                self.assertEqual(workspace["items"][0]["path"], "123")
                self.assertEqual(workspace["items"][0]["added"], 0.0)
                self.assertEqual(pgdata.load_prompts()["schema_version"], pgdata.PROMPT_SCHEMA_VERSION)
                self.assertEqual(pgdata.load_profiles()["items"][0]["memory"]["facts"], [])
                pgdata.save_chats(17)  # type: ignore[arg-type]
                self.assertEqual(pgdata.load_chats(), [])

if __name__ == "__main__":
    unittest.main()
