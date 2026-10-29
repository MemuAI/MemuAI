"""Round 27, фаза 2 (r27-hunt-chat): основной чат MemuAI и панели — регрессионные тесты без сети.

Запуск: NO_PROXY=127.0.0.1,localhost MEMUAI_DATA_DIR=/tmp/x python -m unittest tests.round27_hunt_chat_test
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r27hc-test-"))

APP_JS = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

def _run_node(script: str) -> dict:
    """Выполняет кусок app.js в node: функции берутся из настоящего файла, а не из копии."""
    out = subprocess.run([NODE, "-e", script], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise AssertionError(out.stderr[-2000:])
    return json.loads(out.stdout.strip().splitlines()[-1])

def _slice_js(start: str, end: str) -> str:
    return (f"const src = require('fs').readFileSync('app/pgstatic/app.js', 'utf8');\n"
            f"const a = src.indexOf({json.dumps(start)}), b = src.indexOf({json.dumps(end)}, a);\n"
            f"if (a < 0 || b < 0) throw new Error('slice not found');\n"
            f"const code = src.slice(a, b);\n")

def _pdf(pages):
    """Минимальный PDF с текстом Helvetica — pypdf извлекает его без шрифтовых таблиц."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream")
        content = len(objects)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R "
                       f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        data = body if isinstance(body, bytes) else body.encode("latin-1")
        out += f"{number} 0 obj\n".encode() + data + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)

@unittest.skipUnless(NODE, "node is required")
class ImportedChats(unittest.TestCase):
    def _parse(self, data):
        script = _slice_js("function importStamp(", "function importChatsFromFile(") + (
            "const api = new Function('crypto', 'function trText(ru, en) { return ru; }\\n' + code + "
            "'\\nreturn { parseImportedChats };')({ randomUUID: () => 'x'.repeat(36) });\n"
            f"console.log(JSON.stringify(api.parseImportedChats({json.dumps(data)})));")
        return _run_node(script)

    def test_chatgpt_keeps_only_the_shown_branch(self):

        mapping = {
            "root": {"message": None, "parent": None},
            "n1": {"message": {"author": {"role": "user"}, "content": {"parts": ["Plan Rome"]}, "create_time": 1}, "parent": "root"},
            "n2": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["OLD"]}, "create_time": 2}, "parent": "n1"},
            "n3": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["NEW"]}, "create_time": 3}, "parent": "n1"},
            "n4": {"message": {"author": {"role": "user"}, "content": {"parts": ["Florence?"]}, "create_time": 4}, "parent": "n3"},
            "n5": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["Yes"]}, "create_time": 5}, "parent": "n4"},
            "h": {"message": {"author": {"role": "user"}, "content": {"parts": ["hidden ctx"]}, "create_time": 0,
                              "metadata": {"is_visually_hidden_from_conversation": True}}, "parent": None},
        }
        mapping["n1"]["parent"] = "h"
        chats = self._parse([{"title": "Trip", "current_node": "n5", "mapping": mapping}])
        self.assertEqual([m["content"] for m in chats[0]["messages"]], ["Plan Rome", "NEW", "Florence?", "Yes"])

        del mapping["h"]
        mapping["n1"]["parent"] = "root"
        chats = self._parse([{"title": "Trip", "mapping": mapping}])
        self.assertEqual(len(chats[0]["messages"]), 5)

    def test_imported_title_survives_the_next_message(self):
        chats = self._parse({"chats": [
            {"title": "Мой заголовок", "titleManual": True, "messages": [{"role": "user", "content": "a"}]},
            {"title": "Из ChatGPT", "messages": [{"role": "user", "content": "b"}]},
            {"messages": [{"role": "user", "content": "без названия"}]},
        ]})
        self.assertTrue(chats[0].get("titleManual"))
        self.assertTrue(chats[1].get("titleAuto"))
        self.assertFalse(chats[2].get("titleAuto") or chats[2].get("titleManual"),
                         "a title made from the first message stays a fallback")

    def test_export_keeps_manual_titles_and_import_drops_the_oldest(self):
        self.assertIn("titleManual: !!chat.titleManual", APP_JS)
        panel = APP_JS[APP_JS.index("function openChatImportPanel("):APP_JS.index("function aboutFactsFallback(")]
        self.assertIn(".sort((a, b) => (+b.updated || 0) - (+a.updated || 0))", panel)
        self.assertNotIn("Math.min(rows.length, 200)", panel)

@unittest.skipUnless(NODE, "node is required")
class SystemPromptWithoutPrompt(unittest.TestCase):
    """«Без системного промпта»: обычный чат — без системного сообщения, Work/MemuCode — только среда агента."""

    def _build(self, mode, default_id, access="ask"):
        stubs = (
            "const S = { prompts: { prompts: [{ id: 'empty', name: 'No', text: '' }, { id: 'd', text: 'BASE' }], "
            f"default_id: {json.dumps(default_id)}, disabled_for: [] }}, models: [], imageModels: [], tempChat: false, "
            "workspaces: { items: [{ id: 'p1', name: 'Proj' }], active_id: 'p1' } };\n"
            "const pref = (k, d) => d, internalPromptText = () => '', MEM_PROMPT = 'MEM', getSkills = () => [];\n"
            "const isProjectMode = (m) => m === 'work' || m === 'code', promptData = (v) => String(v);\n"
            f"const workAccessMode = () => {json.dumps(access)}, WORK_TOOLS_PROMPT = 'WORK_TOOLS', EDIT_TOOLS_PROMPT = 'EDIT_TOOLS';\n"
            "const CODE_MODE_PROMPT = 'CODE_MODE', codeSkillsPrompt = () => '', mcpPromptBlock = () => '';\n"
            "const WORK_EXECUTION_FALLBACK = 'WORK_EXECUTION';\n"
            "const browserSkillModel = () => false, computerUseSkillModel = () => false, currentModel = () => 'm';\n"
            "const profileReferenceContext = () => '', runtimeContextBlock = () => 'RUNTIME', PG_LANG = 'ru';\n"
            "const buildLivePersonalityInstructions = () => '';\n")
        script = _slice_js("function buildSystemPrompt(", "function runtimeContextBlock(") + (
            "const run = new Function(" + json.dumps(stubs) + " + code + "
            f"'\\nreturn buildSystemPrompt({{ mode: {json.dumps(mode)}, model: \"m\", "
            "workspaces: { items: [{ id: \"p1\", name: \"Proj\" }], active_id: \"p1\" } });');\n"
            "console.log(JSON.stringify({ text: run() }));")
        return _run_node(script)["text"]

    def test_chat_without_prompt_has_no_system_message(self):
        self.assertEqual(self._build("chat", "empty"), "")

    def test_work_and_code_keep_their_runtime(self):
        for mode in ("work", "code"):
            text = self._build(mode, "empty")
            self.assertTrue(text.startswith("<work_mode>"), text[:80])
            self.assertIn("WORK_TOOLS", text)
            self.assertNotIn("MEM", text.replace("memuai", ""))
            self.assertNotIn("<personality>", text)
        self.assertIn("CODE_MODE", self._build("code", "empty"))
        full = self._build("code", "d")
        self.assertIn("BASE", full)
        self.assertIn("<work_mode>", full)

    def test_ask_mode_does_not_steer_edits_to_refused_tools(self):
        """r27-hunt-chat-2 (вживую, Work «Без промпта», Ask): модель сначала звала work_patch, получала 403
        «требует полного доступа» («Инструменты: ошибка») и лишь потом слала memuai-work."""
        ask = self._build("work", "empty", "ask")
        self.assertNotIn("Existing files are edited with work_patch", ask)
        self.assertIn("write tools (work_patch", ask)
        self.assertIn("memuai-work block with its complete updated content", ask)
        full = self._build("work", "empty", "full")
        self.assertIn("Existing files are edited with work_patch", full)

class ChatClientFixes(unittest.TestCase):
    def test_user_stop_is_marked(self):
        self.assertIn('assistant.stopped = e.name === "AbortError" && localController.signal.aborted', APP_JS)
        self.assertIn('stopped: ["остановлено", "stopped"]', APP_JS)
        self.assertIn('m.stopped && !m.streaming ? "stopped" : "done"', APP_JS)
        self.assertIn('"Stopped." : "Ответ остановлен."', APP_JS)
        cont = APP_JS[APP_JS.index("async function continueAnswer("):APP_JS.index("async function retryAnswerAt(")]
        self.assertIn("assistant.stopped = false", cont)
        self.assertNotIn("transport failure", cont)

    def test_empty_answers_are_not_replayed(self):
        body = APP_JS[APP_JS.index("async function generate("):]
        self.assertIn('if (!String(content).trim() && !imgList.length) continue;', body)

    def test_branch_and_edit_keep_titles_right(self):
        branch = APP_JS[APP_JS.index("function branchAt("):APP_JS.index("function removeMessage(")]
        self.assertIn("{ titleManual: true } : { titleAuto: true }", branch)
        self.assertIn("S.contextSummary = branched.contextSummary", branch)
        edit = APP_JS[APP_JS.index("function editMessage("):APP_JS.index("async function continueAnswer(")]
        self.assertIn("owner.titleAuto = false", edit)
        self.assertIn('"The message is empty"', edit)

    def test_integrations_list_is_retried_and_fallback_titles_are_bilingual(self):
        self.assertRegex(APP_JS, r"if \(sys && \(!integrationsCache\.loaded \|\|[^\n]+\)\) await refreshIntegrations\(\);")
        self.assertIn('"Temporary chat" : "Временный чат"', APP_JS)
        self.assertNotIn('|| "Чат";', APP_JS)

class AttachmentExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        cls.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def _extract(self, raw: bytes, name="doc.pdf"):
        data = "data:application/pdf;base64," + base64.b64encode(raw).decode()
        r = self.client.post("/api/pg/attachments/extract", json={"name": name, "data": data})
        self.assertEqual(r.status_code, 200, r.text[:300])
        return r.json()

    def test_long_pdf_is_not_silently_cut_at_80_pages(self):
        out = self._extract(_pdf([f"Page {i} marker" for i in range(1, 121)]))
        self.assertIn("Page 120 marker", out["text"])
        self.assertFalse(out["truncated"])

    def test_cut_pdf_is_reported_as_truncated(self):
        line = "x" * 120
        out = self._extract(_pdf([line] * 1800))
        self.assertTrue(out["truncated"])
        self.assertLessEqual(len(out["text"]), 200000)

class PanelsTransientRetry(unittest.TestCase):
    def setUp(self):
        from app import panels
        self.panels = panels
        for p in list(panels.load()["panels"]):
            panels.dismiss(p["id"], purge=True)

    def test_network_failure_retries_in_minutes_not_a_day(self):
        p = self.panels.create_panel(template="news", params={"topic": "ai"},
                                     cadence={"kind": "daily", "at": "08:00"}, start=False)
        now = 1_800_000_000.0
        out = self.panels._finish(p["id"], wall=self.panels.Wall("blocked:source-down", "offline"), now=now)
        self.assertLessEqual(out["next_refresh"] - now, 300)
        out = self.panels._finish(p["id"], wall=self.panels.Wall("blocked:proxy-offline", "proxy"), now=now)
        self.assertLessEqual(out["next_refresh"] - now, 600)
        self.assertGreater(out["next_refresh"] - now, 300, "repeated failures still back off")

    def test_short_cadence_and_rate_limit_are_unchanged(self):
        p = self.panels.create_panel(template="crypto", params={"coin": "bitcoin"},
                                     cadence={"kind": "interval", "minutes": 1}, start=False)
        now = 1_800_000_000.0
        out = self.panels._finish(p["id"], wall=self.panels.Wall("blocked:source-down", "x"), now=now)
        self.assertLessEqual(out["next_refresh"] - now, 61)
        out = self.panels._finish(p["id"], wall=self.panels.Wall("blocked:rate-limit", "429", retry_after=900), now=now)
        self.assertGreaterEqual(out["next_refresh"] - now, 900)

class PanelsHumanWalls(unittest.TestCase):
    """r27-hunt-chat-2: погода на HTTP 429 писала голое «HTTP 429» (Telegram-бот, Mini App, подсказки),
    а чип верхней панели упавшей сборки вечно показывал «…»."""

    def setUp(self):
        from app import panels
        self.panels = panels
        for p in list(panels.load()["panels"]):
            panels.dismiss(p["id"], purge=True)

    def _refresh_weather(self, lang, error):
        import asyncio
        from unittest import mock
        self.panels.update_settings({"lang": lang, "location": {"mode": "manual", "name": "Kazan", "lat": 55.79, "lon": 49.12}})
        p = self.panels.create_panel(template="weather", params={}, cadence={"kind": "interval", "minutes": 60}, start=False)

        async def boom(url, **_kw):
            raise ValueError(error)
        with mock.patch.object(self.panels, "_http_text", boom):
            return asyncio.run(self.panels.refresh_panel(p["id"], force=True, now=1_800_000_000.0))

    def test_rate_limit_message_is_human_in_both_languages(self):
        out = self._refresh_weather("ru", "HTTP 429")
        self.assertEqual(out["wall"]["status"], "blocked:rate-limit")
        self.assertIn("ограничил запросы", out["wall"]["message"])
        self.assertIn("повторю позже", out["wall"]["message"])
        self.assertNotEqual(out["error"].strip(), "HTTP 429")
        self.assertGreaterEqual(out["next_refresh"] - 1_800_000_000.0, 300)
        out = self._refresh_weather("en", "HTTP 429")
        self.assertIn("rate-limiting", out["wall"]["message"])
        self.assertIn("will retry later", out["wall"]["message"])

    def test_other_http_failures_are_classified_and_explained(self):
        settings = {"lang": "en"}
        for code, status, words in ((403, "needs_login:source", "sign-in"), (404, "source_gone:source", "not found"),
                                    (503, "blocked:source-down", "temporarily down"),
                                    (418, "blocked:source-down", "error (HTTP 418)")):
            wall = self.panels.failure_wall(f"HTTP {code}", settings)
            self.assertEqual(wall.status, status)
            self.assertIn(words, wall.message)
        wall = self.panels.failure_wall("Connection refused", {"lang": "ru"})
        self.assertEqual(wall.status, "blocked:source-down")
        self.assertIn("Источник не ответил", wall.message)
        self.assertIn("Connection refused", wall.message)

    def test_open_meteo_limit_body_is_localized(self):
        import asyncio
        from unittest import mock
        self.panels.update_settings({"lang": "en", "location": {"mode": "manual", "name": "Kazan", "lat": 55.79, "lon": 49.12}})
        p = self.panels.create_panel(template="weather", params={}, cadence={"kind": "interval", "minutes": 60}, start=False)

        async def limited(url, **_kw):
            return json.dumps({"error": True, "reason": "Daily API request limit exceeded."})
        with mock.patch.object(self.panels, "_http_text", limited):
            out = asyncio.run(self.panels.refresh_panel(p["id"], force=True, now=1_800_000_000.0))
        self.assertEqual(out["wall"]["status"], "blocked:rate-limit")
        self.assertTrue(out["wall"]["message"].startswith("Open-Meteo is rate-limiting"))

    def test_failed_chip_is_not_an_endless_spinner(self):
        js = (ROOT / "app" / "pgstatic" / "panels.js").read_text(encoding="utf-8")
        body = js[js.index("function chipHtml"):js.index("function ensureMounts(")]
        self.assertIn('panel.status === "failed" && !!panel.wall', body)
        self.assertIn("wallText(panel.wall, panel)", body, "the tooltip explains the wall")

@unittest.skipUnless(NODE, "node is required")
class DeepResearchInterrupted(unittest.TestCase):
    """r27-hunt-chat-2 (вживую): перезагрузка окна посреди исследования давала «Глубокое исследование
    не удалось: network error», а исследование без задачи (MemuAI убили) оставалось «выполняется»
    с вечным счётчиком времени."""

    def test_settle_stops_an_orphaned_run_at_its_last_event(self):
        script = ("globalThis.PG_LANG='ru';const vm=require('vm');"
                  "const src=require('fs').readFileSync('app/pgstatic/research.js','utf8');"
                  "vm.runInThisContext(src+';globalThis.R=MemuResearch;');"
                  "const r={status:'running',phase:'searching',queries:[],sources:[],startedAt:1000};"
                  "R.apply(r,{type:'phase',phase:'reading',round:1});const last=r.lastEventAt;"
                  "const changed=R.settle(r);const again=R.settle(r);"
                  "console.log(JSON.stringify({changed,again,status:r.status,fin:r.finishedAt===last,"
                  "done:R.settle({status:'done'})}));")
        out = _run_node(script)
        self.assertEqual(out, {"changed": True, "again": False, "status": "cancelled", "fin": True, "done": False})

    def test_restore_and_stop_settle_research_and_reload_is_explained(self):
        restore = APP_JS[APP_JS.index("async function openChat("):APP_JS.index("if (stale) renderMessages();")]
        self.assertIn("MemuResearch.settle(m.research)", restore)
        stop = APP_JS[APP_JS.index("function setSendBusy("):APP_JS.index("async function useVisionFallback")]
        self.assertIn("MemuResearch.settle(m.research)", stop)
        catch = APP_JS[APP_JS.index("const result = await MemuResearch.run("):APP_JS.index("if (usedResearch) {")]
        self.assertIn("connectionLost", catch)
        self.assertIn("связь с MemuAI прервалась", catch)
        self.assertIn("the connection to MemuAI was interrupted", catch)

@unittest.skipUnless(NODE, "node is required")
class PrivateAttachmentPrep(unittest.TestCase):
    """r27-hunt-chat-3 (вживую): окно «Подготавливаю изображения» / «Модель смотрит видео» закрывалось «×»,
    но запрос шёл дальше — сообщение уходило само (или дважды после повторной отправки), а closePanel()
    закрывал уже другое окно (например, Настройки)."""

    def _run(self, mode):
        script = _slice_js("async function privateAttachmentPrep", "function chooseVideoDelivery(") + (
            "let onClose=null, closes=[], seen=null;"
            "globalThis.panel=(h,k,o)=>{onClose=o.onClose;};"
            "globalThis.closePanel=(r)=>{closes.push(r);};"
            "eval(code);"
            "const req=(signal)=>new Promise((res,rej)=>{seen=signal;"
            f"  if('{mode}'==='ok') setTimeout(()=>res({{text:'x'}}),5);"
            f"  else if('{mode}'==='fail') setTimeout(()=>rej(new Error('boom')),5);"
            "  else signal.addEventListener('abort',()=>rej(new Error('AbortError')));});"
            "const p=privateAttachmentPrep('<h2>x</h2>',req);"
            f"if('{mode}'==='cancel') setTimeout(()=>{{closes.push('user');onClose('dismiss');}},5);"
            "p.then((v)=>console.log(JSON.stringify({v,closes,aborted:seen.aborted})),"
            "(e)=>console.log(JSON.stringify({err:e.message,closes,aborted:seen.aborted})));")
        return _run_node(script)

    def test_closing_the_progress_cancels_and_sends_nothing(self):
        self.assertEqual(self._run("cancel"), {"v": None, "closes": ["user"], "aborted": True})

    def test_success_closes_its_own_panel_once(self):
        self.assertEqual(self._run("ok"), {"v": {"text": "x"}, "closes": ["done"], "aborted": False})

    def test_failure_is_reported(self):
        self.assertEqual(self._run("fail"), {"err": "boom", "closes": ["done"], "aborted": False})

    def test_send_uses_it_for_images_and_video(self):
        body = APP_JS[APP_JS.index("async function send()"):APP_JS.index("async function privateAttachmentPrep")]
        self.assertEqual(body.count("await privateAttachmentPrep("), 2)
        self.assertIn("if (!visionResult) return;", body)
        self.assertIn("if (!reports) return;", body)
        self.assertNotIn('panel(`<div class="vision-routing-progress">', body)

class MediaBridgeErrors(unittest.TestCase):
    """r27-hunt-chat-3: ошибки /api/pg/vision/describe и /api/pg/video/analyze (тосты окна) были только
    по-русски в английском интерфейсе; недоступный Proxy показывался голым текстом httpx."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        cls.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def test_validation_errors_follow_the_ui_language(self):
        r = self.client.post("/api/pg/vision/describe", json={"model": "m", "images": [], "lang": "en"})
        self.assertEqual((r.status_code, r.json()["error"]), (400, "Choose a vision model and attach an image"))
        r = self.client.post("/api/pg/vision/describe", json={"model": "m", "images": [], "lang": "ru"})
        self.assertIn("Выберите vision-модель", r.json()["error"])
        r = self.client.post("/api/pg/video/analyze", json={"frames": [], "lang": "en"})
        self.assertEqual(r.json()["error"], "Choose a video recognition model")
        r = self.client.post("/api/pg/video/analyze", json={"model": "m", "frames": [], "lang": "en"})
        self.assertEqual(r.json()["error"], "The video has no frames to analyse")
        r = self.client.post("/api/pg/video/analyze", content=b"{", headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 400)

    def test_unreachable_proxy_is_explained(self):
        from unittest import mock
        import httpx
        frame = "data:image/jpeg;base64,AAAA"
        with mock.patch("httpx.AsyncClient.stream", side_effect=httpx.ConnectError("All connection attempts failed")), \
                mock.patch("app.playground_api._runtime_proxy_url", return_value=""):
            r = self.client.post("/api/pg/video/analyze", json={"model": "m", "frames": [{"t": 1, "data": frame}], "lang": "en"})
            self.assertEqual((r.status_code, r.json()["error"]), (502, "MemuAI Proxy is unreachable — check that it is running"))
            r = self.client.post("/api/pg/vision/describe", json={"model": "m", "images": [frame]})
            self.assertIn("MemuAI Proxy", r.json()["error"])

@unittest.skipUnless(NODE, "node is required")
class SketchIsNotLost(unittest.TestCase):
    """r27-hunt-chat-3: «Прикрепить» при 10 вложениях (или PNG со вставленной фотографией > 5 МБ)
    показывал предупреждение и тут же стирал рисунок без возможности вернуть."""

    def test_add_attachment_files_reports_how_many_were_attached(self):
        script = _slice_js("async function addAttachmentFiles", "async function addVideoAttachments(") + (
            "globalThis.window=globalThis;"
            "globalThis.PG_LANG='en';globalThis.MemuVideo=undefined;const toasts=[];"
            "globalThis.toast=(m)=>toasts.push(m);globalThis.renderAttachments=()=>{};"
            "globalThis.FileReader=class{readAsDataURL(f){this.result='data:image/png;base64,AA';setTimeout(()=>this.onload(),1);}};"
            "eval(code);globalThis.attachments=[];"
            "const f={name:'s.png',type:'image/png',size:10};"
            "(async()=>{const one=await addAttachmentFiles([f]);"
            "for(let i=0;i<9;i++)attachments.push({data:''});"
            "const none=await addAttachmentFiles([f]);"
            "console.log(JSON.stringify({one,none,toasts}));})();")
        out = _run_node(script)
        self.assertEqual((out["one"], out["none"]), (1, 0))
        self.assertIn("Up to 10 attachments per message", out["toasts"])

    def test_attach_keeps_the_drawing_when_nothing_was_attached(self):
        js = (ROOT / "app" / "pgstatic" / "sketch.js").read_text(encoding="utf-8")
        attach = js[js.index("async function attach()"):js.index("return { open, close")]
        self.assertLess(attach.index("if (!(await addAttachmentFiles("), attach.index("reset();"))
        to_blob = js[js.index("async function toBlob()"):js.index("async function attach()")]
        self.assertIn("png.size <= MAX_PNG", to_blob)
        self.assertIn('"image/jpeg", 0.9', to_blob)

class MessageModelName(unittest.TestCase):
    """r27-hunt-chat-3 (вживую, Yandex): шапка ответа показывала сырой id «ya-gpt://b1g…/deepseek…/latest»
    и игнорировала своё название модели, а выбор модели вверху — красивое имя."""

    def test_answer_header_uses_the_display_name(self):
        self.assertIn('title="${esc(m.model)}">${esc(modelDisplayName(m.model))}</span>', APP_JS)

if __name__ == "__main__":
    unittest.main()
