"""Round 34 (r34-quick-agentic): Computer Use, Do For You and their Quick Access cards.

Found in live runs (gpt-6.1-sol / qwen3-vl-plus on real sites in headless Chromium; Computer Use on an emulated
150 % two-monitor desktop) and fixed:

* DFY looped on ``<input type=time|date>`` (typed characters are ignored, the step still said «typed»).
* DFY: an answer is bound to its question (a double click no longer approves the NEXT confirmation).
* DFY: after «Отклонить» on the final step a «done» is not a silent success.
* DFY: the headless browser closes when idle; a browser left over from a crashed MemuAI no longer blocks every task.
* DFY proxy: a long proxy login was cut to 200 characters (407 for a correct login).
* Computer Use: a click into a text field is «normal, now type», not «NO visible change» (repeated clicks).
* Computer Use: a small real change (one digit on a display) is seen (the 32x18 fingerprint missed it → «99»).
* Computer Use: «Разрешить» names its step; «plus» is VK_ADD (VK_OEM_PLUS typed «=»).
* Quick Access cards: one poll chain, «Запустить ещё раз» after reopening, offline notice, DFY «gone» task, step words.

No network. The Chromium part (a real ``<input type=time>``) is skipped without Playwright/Chromium.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r34-quick-agentic-"))
sys.path.insert(0, str(ROOT))

from app import automation_runtime as ar  # noqa: E402
from app import dfy as dfy_mod  # noqa: E402
from app.dfy import DFYManager  # noqa: E402
from app.dfy_browser import end_profile_orphans, picker_value  # noqa: E402

PG = ROOT / "app" / "pgstatic"
CHROMIUM = next((p for p in (os.environ.get("MEMU_TEST_CHROMIUM"), os.environ.get("MEMUAI_BROWSER_EXECUTABLE"),
                             "/workspace/work/chromium/chrome", "/usr/bin/chromium", shutil.which("chromium") or "")
                 if p and os.path.exists(p)), "")


def _shot() -> str:
    return base64.b64encode(b"\xff\xd8\xff\xe0jpeg").decode()


class ScriptBrowser:
    """A fake DFY browser: one page with a button «Submit order» (element 1) and a field (element 2)."""

    def __init__(self):
        self.url = "https://shop.example/checkout"
        self.clicked = []
        self.closed = 0
        self.typed = []
        self.type_result = "typed"

    async def start(self): pass

    async def close(self): self.closed += 1

    async def current_url(self): return self.url

    async def snapshot(self):
        return {"url": self.url, "title": "Checkout", "text": "Order", "screenshot": _shot(),
                "elements": [{"id": 1, "tag": "button", "text": "Submit order"}, {"id": 2, "tag": "input", "type": "time"}]}

    async def element_info(self, element):
        return {"tag": "button", "type": "submit", "text": "Submit order" if element == 1 else "", "form": True, "submit": element == 1}

    async def submit_info(self, element=None): return None

    async def click(self, element):
        self.clicked.append(element)
        return "clicked"

    async def type(self, element, text, *, secret=False, submit=False):
        self.typed.append((element, text))
        return self.type_result


def manager(replies, browser, **settings):
    replies = list(replies)
    prompts = []

    async def completion(model, messages):
        prompts.append(messages)
        return replies.pop(0) if replies else '{"action": "done", "result": "end"}'
    prefs = {"dfy_enabled": True, "dfy_model": "vision-model", "dfy_max_steps": 10, **settings}
    m = DFYManager(completion, lambda: prefs, data_root=Path(tempfile.mkdtemp(prefix="r34-dfy-")),
                   vision_lookup=lambda _m: True, browser_factory=lambda _v: browser)
    m._prompts = prompts
    return m


async def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


# ------------------------------------------------------------------------------------------------ DFY
class DFYPickerInputs(unittest.TestCase):
    def test_picker_value_formats(self):
        cases = [("time", "19:30", "19:30"), ("time", "7:30 pm", "19:30"), ("time", "19.30", "19:30"),
                 ("time", "12:05 am", "00:05"), ("time", "9", "09:00"), ("date", "31.12.2026", "2026-12-31"),
                 ("date", "1/2/2027", "2027-02-01"), ("date", "2026-12-31", "2026-12-31"),
                 ("datetime-local", "31.12.2026 19:30", "2026-12-31T19:30"), ("color", "ff0000", "#ff0000"),
                 ("range", "40", "40")]
        for kind, raw, want in cases:
            self.assertEqual(picker_value(kind, raw), want, (kind, raw))

    def test_type_outcome_reaches_the_model(self):
        """The browser's own outcome (a field that stayed empty) goes into the history, not a blind «typed»."""
        browser = ScriptBrowser()
        browser.type_result = "typed, but the field is still EMPTY — the page did not accept the text; click the field or use another way"
        m = manager(['{"action":"type","element":2,"text":"abc"}', '{"action":"fail","error":"x"}'], browser)

        async def run():
            task = await m.create("fill")
            await asyncio.wait_for(task.job, 5)
            return task
        task = asyncio.run(run())
        self.assertIn("EMPTY", " ".join(task.history))
        self.assertIn("поле осталось пустым", task.steps[0]["detail"])

    @unittest.skipUnless(CHROMIUM, "Chromium is not available")
    def test_real_time_input_gets_its_value(self):
        """A real Chromium page: Input.insertText leaves <input type=time> empty; DFYBrowser.type sets it."""
        from app.dfy_browser import DFYBrowser
        profile = Path(tempfile.mkdtemp(prefix="r34-dfy-prof-"))
        page = "data:text/html," + "<input id=t type=time><input id=x type=text>"

        async def run():
            env = {"MEMUAI_BROWSER_EXECUTABLE": CHROMIUM, "MEMUAI_BROWSER_ARGS": "--no-sandbox"}
            with patch.dict(os.environ, env):
                b = DFYBrowser(profile=profile)
                await b.start()
                try:
                    await b.command("Page.navigate", {"url": page})
                    await asyncio.sleep(0.6)
                    await b.evaluate("document.getElementById('t').focus()")
                    out = await b.type(None, "7:30 pm")
                    value = await b.evaluate("document.getElementById('t').value")
                    await b.evaluate("document.getElementById('x').focus()")
                    plain = await b.type(None, "hello")
                    text = await b.evaluate("document.getElementById('x').value")
                    return out, value, plain, text
                finally:
                    await b.close()
        out, value, plain, text = asyncio.run(run())
        self.assertEqual(value, "19:30")
        self.assertIn("set the time field", out)
        self.assertEqual((plain, text), ("typed", "hello"))
        shutil.rmtree(profile, ignore_errors=True)


class DFYAnswerBinding(unittest.TestCase):
    def test_stale_question_id_is_refused(self):
        browser = ScriptBrowser()
        m = manager(['{"action":"ask_user","question":"Какой размер?","choices":["S","L"]}',
                     '{"action":"ask_user","question":"Какой цвет?"}', '{"action":"done","result":"ok"}'], browser)

        async def run():
            task = await m.create("buy")
            self.assertTrue(await wait_for(lambda: task.status == "waiting_user"))
            first = task.question["id"]
            await m.answer(task.id, {"answer": "L", "question_id": first})
            self.assertTrue(await wait_for(lambda: task.status == "waiting_user" and task.question["id"] != first))
            with self.assertRaises(RuntimeError):     # the double click on the first question's button
                await m.answer(task.id, {"answer": "L", "question_id": first})
            self.assertEqual(task.status, "waiting_user")
            await m.answer(task.id, {"answer": "red", "question_id": task.question["id"]})
            await asyncio.wait_for(task.job, 5)
            return task
        task = asyncio.run(run())
        self.assertEqual(task.status, "done")
        self.assertEqual(len([a for a in task.answers if "red" in str(a) or "L" in str(a)]), 2)

    def test_answer_without_question_id_still_works(self):
        browser = ScriptBrowser()
        m = manager(['{"action":"ask_user","question":"?"}', '{"action":"done","result":"ok"}'], browser)

        async def run():
            task = await m.create("x")
            self.assertTrue(await wait_for(lambda: task.status == "waiting_user"))
            await m.answer(task.id, {"answer": "yes"})
            await asyncio.wait_for(task.job, 5)
            return task.status
        self.assertEqual(asyncio.run(run()), "done")


class DFYDeclinedFinalStep(unittest.TestCase):
    def test_done_after_declined_submit_says_it_was_not_done(self):
        browser = ScriptBrowser()
        m = manager(['{"action":"click","element":1,"reason":"submit the order"}',
                     '{"action":"done","result":"Пицца заказана, отправка отклонена"}'], browser)

        async def run():
            task = await m.create("order a pizza")
            self.assertTrue(await wait_for(lambda: task.status == "waiting_user"))
            self.assertEqual(task.question["kind"], "confirm")
            await m.answer(task.id, {"approve": False, "question_id": task.question["id"]})
            await asyncio.wait_for(task.job, 5)
            return task
        task = asyncio.run(run())
        self.assertEqual(browser.clicked, [])
        self.assertTrue(task.result.startswith("Последний шаг не выполнен: вы отклонили «Submit order»"), task.result)
        self.assertIn("it was NOT done", " ".join(task.history))

    def test_later_approval_clears_the_note(self):
        browser = ScriptBrowser()
        m = manager(['{"action":"click","element":1}', '{"action":"click","element":1}', '{"action":"done","result":"Заказ 42"}'], browser)

        async def run():
            task = await m.create("order")
            for approve in (False, True):
                self.assertTrue(await wait_for(lambda: task.status == "waiting_user"))
                await m.answer(task.id, {"approve": approve})
                await wait_for(lambda: task.status != "waiting_user")
            await asyncio.wait_for(task.job, 5)
            return task
        task = asyncio.run(run())
        self.assertEqual(browser.clicked, [1])
        self.assertEqual(task.result, "Заказ 42")


class DFYEnglishInterface(unittest.TestCase):
    """r34-quick-agentic-2: with the interface in English the confirmation, the step lines and the «declined» note
    are English (they were Russian in an English UI)."""

    def test_question_steps_and_declined_note_follow_the_ui_language(self):
        browser = ScriptBrowser()
        m = manager(['{"action":"click","element":1,"reason":"submit the order"}',
                     '{"action":"done","result":"Order placed"}'], browser, language="en")

        async def run():
            task = await m.create("order a pizza")
            self.assertTrue(await wait_for(lambda: task.status == "waiting_user"))
            prompt = task.question["prompt"]
            await m.answer(task.id, {"approve": False, "question_id": task.question["id"]})
            await asyncio.wait_for(task.job, 5)
            return task, prompt
        task, prompt = asyncio.run(run())
        self.assertTrue(prompt.startswith("DFY is about to press «Submit order»"), prompt)
        self.assertNotRegex(prompt, "[А-Яа-яЁё]")
        self.assertTrue(task.result.startswith("The last step was not done: you declined «Submit order»"), task.result)
        labels = " ".join(str(row.get("detail") or "") for row in task.steps)
        self.assertTrue(labels)
        self.assertNotRegex(labels, "[А-Яа-яЁё]", labels)

    def test_russian_stays_russian(self):
        browser = ScriptBrowser()
        m = manager(['{"action":"click","element":1}'], browser)

        async def run():
            task = await m.create("заказ")
            self.assertTrue(await wait_for(lambda: task.status == "waiting_user"))
            prompt = task.question["prompt"]
            await m.cancel(task.id)
            return prompt
        self.assertTrue(asyncio.run(run()).startswith("DFY собирается нажать «Submit order»"))


    def test_browser_errors_are_english(self):
        from app.dfy_browser import in_language
        self.assertEqual(in_language("Сайт не открылся: net::ERR_NAME_NOT_RESOLVED", "en"),
                         "The site did not open: net::ERR_NAME_NOT_RESOLVED")
        self.assertEqual(in_language("Поле типа «time» не приняло «abc»: нужен формат ЧЧ:ММ (19:30)", "en"),
                         "The «time» field did not accept «abc»: the format is HH:MM (19:30)")
        self.assertEqual(in_language("Сайт не открылся: x", "ru"), "Сайт не открылся: x")

        class Broken(ScriptBrowser):
            async def navigate(self, url):
                raise RuntimeError("Сайт не открылся: net::ERR_CONNECTION_REFUSED")
        m = manager(['{"action":"navigate","url":"https://shop%d.example/"}' % n for n in range(4)], Broken(), language="en")

        async def run():
            task = await m.create("open the shop")
            self.assertTrue(await wait_for(lambda: task.status == "waiting_user"))   # the «same error» pause
            card = task.question["card"]
            await m.cancel(task.id)
            return task, card
        task, card = asyncio.run(run())
        steps = " ".join(str(row.get("detail") or "") for row in task.steps)
        self.assertIn("Error: The site did not open", steps)
        self.assertIn("the site did not open", card["detail_en"].lower())
        self.assertNotRegex(steps + card["detail_en"], "[А-Яа-яЁё]")


class YouTubeBotCheckAndPageReading(unittest.TestCase):
    """r34-quick-agentic-2: a YouTube bot check is not «needs a sign-in / age»; bot-check pages are not «the page»."""

    def _transcript(self, playability):
        import json as _json
        from app import youtube_analysis as yt
        page = "var ytInitialPlayerResponse = " + _json.dumps({"playabilityStatus": playability}) + ";"

        async def fetch(url):
            return page
        with self.assertRaises(yt.TranscriptUnavailable) as ctx:
            asyncio.run(yt.transcript("dQw4w9WgXcQ", "ru", fetch=fetch, use_ytdlp=False))
        return ctx.exception

    def test_bot_check(self):
        exc = self._transcript({"status": "LOGIN_REQUIRED", "reason": "Sign in to confirm you\u2019re not a bot"})
        self.assertEqual(exc.code, "bot_check")
        self.assertIn("бота", str(exc))
        self.assertIn("no sign-in is needed", exc.en)

    def test_age_check_keeps_its_message(self):
        exc = self._transcript({"status": "LOGIN_REQUIRED", "reason": "Sign in to confirm your age"})
        self.assertEqual(exc.code, "login")

    def test_bot_check_pages_are_not_the_page(self):
        from app.quick_ask_browser import page_mismatch
        self.assertEqual(page_mismatch("Ozon", {"title": "Доступ ограничен",
                                                "text": "Подтвердите, что запросы отправляли вы, а не робот. " * 20}), "login_wall")
        self.assertEqual(page_mismatch("Google", {"title": "Google",
                                                  "text": "Our systems have detected unusual traffic from your network. " * 20}), "login_wall")
        self.assertEqual(page_mismatch("Статья", {"title": "Статья", "text": "Lorem ipsum dolor sit amet " * 80}), "")


class DFYApiErrorsInEnglish(unittest.TestCase):
    def test_answer_errors_follow_the_ui_language(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.dfy_api import build_router
        for lang, want in (("en", "The DFY task was not found"), ("ru", "Задача не найдена")):
            m = manager([], ScriptBrowser(), language=lang)
            app = FastAPI()
            app.include_router(build_router(m))
            client = TestClient(app)
            self.assertEqual(client.post("/api/dfy/tasks/nope/answer", json={}).json()["error"], want)


class CUKeyNormalisation(unittest.TestCase):
    """«+», «ctrl++», «ctrl-c», «alt-f4», «page-down» reach Windows as the keys the model meant."""

    def test_keys(self):
        from app.cu_grounding import _normalize_keys
        cases = {"+": "plus", "ctrl++": "ctrl+plus", "shift++": "shift+plus", "ctrl-c": "ctrl+c", "Alt-F4": "alt+f4",
                 "Ctrl+C": "ctrl+c", "ctrl+plus": "ctrl+plus", "ctrl+-": "ctrl+-", "-": "-", "enter": "enter",
                 "page-down": "pagedown", "ctrl+page down": "ctrl+pagedown", "print screen": "printscreen",
                 "ctrl+shift+t": "ctrl+shift+t", "Return": "enter"}
        for raw, want in cases.items():
            self.assertEqual(_normalize_keys(raw), want, raw)
        CA = ar.ComputerAutomation
        for raw in cases:
            self.assertTrue(CA._parse_key_combo(_normalize_keys(raw)), raw)


class DFYBrowserLifetime(unittest.TestCase):
    def test_idle_browser_is_closed(self):
        browser = ScriptBrowser()
        m = manager(['{"action":"done","result":"ok"}'], browser)

        async def run():
            with patch.object(dfy_mod, "BROWSER_IDLE_CLOSE", 0.05):
                m._schedule_idle_close.__func__.__defaults__ = (0.05,)
                try:
                    task = await m.create("x")
                    await asyncio.wait_for(task.job, 5)
                    self.assertIsNotNone(m.browser)
                    self.assertTrue(await wait_for(lambda: m.browser is None, 3))
                finally:
                    m._schedule_idle_close.__func__.__defaults__ = (dfy_mod.BROWSER_IDLE_CLOSE,)
        asyncio.run(run())
        self.assertEqual(browser.closed, 1)

    def test_a_new_task_keeps_the_browser(self):
        browser = ScriptBrowser()
        m = manager(['{"action":"done","result":"1"}', '{"action":"done","result":"2"}'], browser)

        async def run():
            task = await m.create("x")
            await asyncio.wait_for(task.job, 5)
            m._schedule_idle_close(0.2)
            second = await m.create("y")      # cancels the pending idle close
            await asyncio.wait_for(second.job, 5)
            m._idle_close.cancel()
            await asyncio.sleep(0.3)
        asyncio.run(run())
        self.assertEqual(browser.closed, 0)

    def test_orphan_browser_of_a_dead_memuai_is_ended(self):
        try:
            import psutil  # noqa: F401
        except Exception:
            self.skipTest("psutil is missing")
        profile = Path(tempfile.mkdtemp(prefix="r34-orphan-"))
        other = Path(tempfile.mkdtemp(prefix="r34-other-"))
        # a «browser» that is NOT our child: started through a shell that exits at once
        code = "import time; time.sleep(60)"
        # Windows' venv python.exe is a redirector and starts a second process
        # with identical arguments. Use the real interpreter for this fixture
        # so exactly one orphan stands in for each browser profile.
        interpreter = getattr(sys, "_base_executable", None) or sys.executable
        launcher = (f"import subprocess; subprocess.Popen([{interpreter!r},'-c',{code!r},{f'--user-data-dir={profile}'!r}],"
                    f"start_new_session=True); subprocess.Popen([{interpreter!r},'-c',{code!r},{f'--user-data-dir={other}'!r}],"
                    "start_new_session=True)")
        subprocess.run([sys.executable, "-c", launcher], check=True)
        import psutil
        time.sleep(0.4)

        def holders(path):
            return [p for p in psutil.process_iter(["cmdline"]) if f"--user-data-dir={path}" in (p.info.get("cmdline") or [])]
        try:
            self.assertEqual(len(holders(profile)), 1)
            self.assertEqual(end_profile_orphans(profile), 1)
            time.sleep(0.2)
            self.assertEqual(holders(profile), [])
            self.assertEqual(len(holders(other)), 1, "only the processes of THIS profile are ended")
        finally:
            for path in (profile, other):
                for process in holders(path):
                    try:
                        process.kill()
                        process.wait(timeout=5)
                    except psutil.NoSuchProcess:
                        pass

    def test_own_child_is_not_touched(self):
        try:
            import psutil  # noqa: F401
        except Exception:
            self.skipTest("psutil is missing")
        profile = Path(tempfile.mkdtemp(prefix="r34-own-"))
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", f"--user-data-dir={profile}"])
        try:
            time.sleep(0.3)
            self.assertEqual(end_profile_orphans(profile), 0)
            self.assertIsNone(child.poll())
        finally:
            child.kill(); child.wait()

    def test_start_retries_after_ending_an_orphan(self):
        src = (ROOT / "app" / "dfy_browser.py").read_text("utf-8")
        self.assertIn("if not _retry and await asyncio.to_thread(end_profile_orphans, self.profile)", src)


class DFYProxyLogin(unittest.TestCase):
    def test_long_username_is_kept(self):
        from app.dfy_proxy import ProxyStore
        store = ProxyStore(Path(tempfile.mkdtemp(prefix="r34-proxy-")))
        user = "user-zone-resi-session-" + "x" * 700
        store.set("local", {"enabled": True, "host": "proxy.example", "port": 8080, "username": user, "password": "p"})
        self.assertEqual(store.upstream("local").username, user)


# ------------------------------------------------------------------------------------------------ Computer Use
class CUChangeDetection(unittest.TestCase):
    def _screen(self, digit_box=None, glyph=""):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (1400, 788), (243, 243, 243))
        d = ImageDraw.Draw(img)
        d.rectangle([1000, 60, 1300, 120], fill=(240, 240, 240))
        if digit_box:
            d.rectangle(digit_box, fill=(20, 20, 20))
        if glyph == "0":      # a display digit, ~20x34 px on the shown screenshot (36 px font at 150 %, scaled)
            d.ellipse([1268, 72, 1288, 106], outline=(20, 20, 20), width=3)
        elif glyph == "9":
            d.ellipse([1268, 72, 1288, 90], outline=(20, 20, 20), width=3)
            d.line([1286, 82, 1284, 106], fill=(20, 20, 20), width=3)
        return img

    def test_a_digit_on_a_display_is_a_change_for_the_fine_check(self):
        before, after = self._screen(glyph="0"), self._screen(glyph="9")     # «0» → «9» on the calculator display
        coarse = ar._signature_max_change(ar._image_signature(before), ar._image_signature(after))
        self.assertLess(coarse, 5, "the old fingerprint does not see it — that was the bug")
        self.assertGreaterEqual(ar._fine_change(ar._fine_signature(before), ar._fine_signature(after)), ar.FINE_CHANGED_PIXELS)

    def test_a_caret_is_not_a_change(self):
        before, after = self._screen(), self._screen((700, 300, 701, 318))       # 1-2 px caret, 18 px tall
        self.assertLess(ar._fine_change(ar._fine_signature(before), ar._fine_signature(after)), ar.FINE_CHANGED_PIXELS)
        self.assertEqual(ar._fine_change(ar._fine_signature(before), ar._fine_signature(before)), 0)
        self.assertEqual(ar._fine_change(None, b"x"), -1)

    def test_compose_capture_carries_the_fine_signature(self):
        from PIL import Image
        cap = ar.ComputerAutomation._compose_capture([{"left": 0, "top": 0, "right": 2560, "bottom": 1440}],
                                                     Image.new("RGB", (2560, 1440)), "pixels", None)
        fine = cap["screens"][0]["fine"]
        self.assertIsInstance(fine, bytes)
        self.assertEqual(len(fine), 640 * 360)


class CUPlusKeyAndApproval(unittest.TestCase):
    def test_plus_is_vk_add(self):
        CA = ar.ComputerAutomation
        self.assertEqual(CA._parse_key_combo("plus"), [0x6B])
        self.assertEqual(CA._parse_key_combo("ctrl+plus"), [0x11, 0x6B])
        self.assertEqual(CA._parse_key_combo("="), [0xBB])

    def test_approve_for_another_step_is_ignored(self):
        rt = ar.ComputerAutomation()

        async def run():
            waiting = asyncio.create_task(rt.request_approval({"action": "click", "step": 3}))
            await asyncio.sleep(0)
            self.assertFalse(rt.approve(True, step=2), "a click on the card of step 2 must not approve step 3")
            self.assertFalse(waiting.done())
            self.assertTrue(rt.approve(True, step="3"))
            return await waiting
        self.assertTrue(asyncio.run(run()))

    def test_approve_without_step_keeps_working(self):
        rt = ar.ComputerAutomation()

        async def run():
            waiting = asyncio.create_task(rt.request_approval({"action": "click", "step": 1}))
            await asyncio.sleep(0)
            self.assertTrue(rt.approve(False, note="левее"))
            return await waiting
        self.assertFalse(asyncio.run(run()))
        self.assertEqual(rt._reject_note, "левее")
        self.assertFalse(rt.approve(True), "nothing is pending any more")

    def test_route_passes_the_step(self):
        src = (ROOT / "app" / "playground_api.py").read_text("utf-8")
        self.assertIn('computer_runtime.approve(True, all_steps=action == "approve_all", step=body.get("step"))', src)


class CUTextFieldClick(unittest.TestCase):
    """The loop on a minimal fake desktop: clicking a text field changes nothing visible; the model must be told
    to type now (before: «NO visible change» → the same click again until «Агент зациклился»)."""

    def setUp(self):
        from PIL import Image
        CA = ar.ComputerAutomation
        self.typed = []
        image = Image.new("RGB", (2560, 1440), (250, 250, 250))
        field = {"name": "Текстовый редактор", "type": "document", "rect": [700, 300, 1900, 1100], "window": "Блокнот"}

        async def fast_sleep(_s=0, *a, **k):
            await _real_sleep(0)
        patches = [
            patch.object(CA, "_grab_desktop", classmethod(lambda cls: ([{"left": 0, "top": 0, "right": 2560, "bottom": 1440}], image))),
            patch.object(CA, "_monitors", staticmethod(lambda: [{"left": 0, "top": 0, "right": 2560, "bottom": 1440}])),
            patch.object(CA, "_ui_elements", classmethod(lambda cls: [dict(field)])),
            patch.object(CA, "_list_windows", staticmethod(lambda: [{"hwnd": 11, "title": "Блокнот", "rect": [650, 250, 1950, 1150]}])),
            patch.object(CA, "start_overlay", lambda self: None),
            patch.object(CA, "_click", classmethod(lambda cls, x, y, double=False, **kw: None)),
            patch.object(CA, "_type_text", staticmethod(lambda text, should_stop=None: self.typed.append(text))),
            patch.object(CA, "_foreground_window", staticmethod(lambda: 11)),
            patch.object(CA, "_is_own", classmethod(lambda cls, h: False)),
            patch.object(CA, "_own_windows_at", staticmethod(lambda x, y: [])),
            patch.object(ar.asyncio, "sleep", new=fast_sleep),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_the_model_is_told_to_type(self):
        prompts = []
        replies = ['{"action":"click","element":1,"reason":"фокус"}', '{"action":"type","text":"привет"}',
                   '{"action":"done","report":"ok"}']

        async def completion(model, messages):
            prompts.append(messages)
            return replies.pop(0)
        rt = ar.ComputerAutomation()
        asyncio.run(ar.run_computer_agent(rt, completion, model="qwen3-vl-plus", task="напиши привет", ask_each_action=False))
        self.assertEqual(rt.status, "done", rt.error)
        self.assertIn("clicked into the text field 'Текстовый редактор'", rt.steps[0]["outcome"])
        second = prompts[1][1]["content"][0]["text"]
        self.assertIn("normal after focusing a text field: type now", second)
        self.assertNotIn("NO visible change", second)
        self.assertEqual(self.typed, ["привет"])


_real_sleep = asyncio.sleep


# ------------------------------------------------------------------------------------------------ the cards
class QuickCardsStatic(unittest.TestCase):
    def setUp(self):
        self.cu = (PG / "quick" / "quick-computer.js").read_text("utf-8")
        self.dfy = (PG / "quick" / "quick-dfy.js").read_text("utf-8")
        self.client = (PG / "dfy-client.js").read_text("utf-8")

    def test_computer_card_poll_chain_and_answers(self):
        self.assertIn("run.pollGen !== gen", self.cu)
        self.assertIn("run.answered === step", self.cu)
        self.assertIn("ev.repeat", self.cu)
        self.assertIn("_attach: false", self.cu)          # «Запустить ещё раз» after reopening starts a new run
        self.assertIn("Нет связи с MemuAI", self.cu)
        self.assertIn("No connection to MemuAI", self.cu)

    def test_dfy_card(self):
        self.assertIn('task.gone || task.status === "gone"', self.dfy)
        self.assertIn("question_id: asked || undefined", self.dfy)
        self.assertIn("entry.answering === asked", self.dfy)
        self.assertIn('navigate: ["Открыл страницу", "Opened a page"]', self.dfy)
        self.assertIn("qdfy-shot", self.dfy)
        self.assertIn("question_id: (task && task.question && task.question.id)", self.client)

    def test_round34_followups(self):
        """r34-quick-agentic-2: main chat approvals name the step, the cards redraw when the language arrives, the DFY
        head buttons fold into icons in a narrow window, the companion's bubble stays on screen."""
        app_js = (PG / "app.js").read_text("utf-8")
        self.assertIn('body: { action, step: pending.step || undefined }', app_js)
        self.assertIn('Q.on("config", () => { if (D.tasks.size || D.offer || D.notice) render(); });', self.dfy)
        self.assertIn('Q.on("config", () => { if (run) render(); });', self.cu)
        self.assertIn("AbortSignal.timeout(6000)", self.cu)
        self.assertIn("qdfy-hbtn", self.dfy)
        self.assertIn(".qdfy-card .qdfy-hbtn > span { display: none; }", (PG / "quick" / "quick-dfy.css").read_text("utf-8"))
        comp = (PG / "companion" / "companion.js").read_text("utf-8")
        self.assertIn("function fitBubble()", comp)
        stage_css = (PG / "companion" / "stage.css").read_text("utf-8")
        self.assertIn('body[data-surface="desktop"] .stage-tools { flex-direction: column; }', stage_css)
        from app.companion_window import hit_region
        # the tools column (right 40 px) stays clickable next to a narrow model in the middle
        self.assertEqual(hit_region({"x": 120, "y": 60, "w": 40, "h": 300}, 1.0, (300, 440))[2], 300)

    def test_js_syntax(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is missing")
        for path in (PG / "quick" / "quick-computer.js", PG / "quick" / "quick-dfy.js", PG / "dfy-client.js",
                     PG / "companion" / "companion.js", PG / "companion" / "stage.js"):
            subprocess.run([node, "--check", str(path)], check=True)


@unittest.skipUnless(CHROMIUM, "no Chromium")
class QuickComputerCardHeadless(unittest.TestCase):
    """r34-quick-agentic-2: the real quick window with the Computer Use card attached to a running task must poll
    /api/work/computer/state every ~0.7 s (a stalled chain left the card on «Запускается…»), and reopening the window
    several times must not multiply the chains. The /api is answered by an ASYNC route handler: a sync Playwright
    route only runs while Python calls Playwright, so a ``time.sleep`` there holds every request — that, not the card,
    was the «no /state» seen in an earlier headless run."""

    def run_card(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest("no Playwright")
        import functools
        import http.server
        import threading
        from app import themes

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(PG)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        config = {"prefs": {"theme": "dark", "language": "ru", "quick_ask_model": "m1"},
                  "status": {"native": True, "pinned": False, "hotkey": {"ok": True, "hotkey": "Alt+Space", "code": "registered"}},
                  "ui": {"onboarded": True}, "endpoint": {"source": "proxy"}}
        calls = []

        async def route(route_obj, request):
            path = "/" + request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
            if path == "/themes.css":
                return await route_obj.fulfill(status=200, content_type="text/css", body=themes.theme_css())
            if path == "/api/quick/config":
                return await route_obj.fulfill(json=config)
            if path == "/api/pg/state":
                return await route_obj.fulfill(json={"models": [{"id": "m1", "caps": {"vision": True}}], "image_models": []})
            if path == "/api/work/computer/state":
                calls.append(time.monotonic())
                now = time.time()
                return await route_obj.fulfill(json={"task_id": "t1", "status": "running", "task": "Открой Блокнот", "phase": "thinking",
                                                     "phase_started": now - 2, "now": now, "steps": [], "step": 1, "max_steps": 12})
            if path.startswith("/api/"):
                return await route_obj.fulfill(json={"items": [], "events": [], "seq": 0, "ok": True})
            return await route_obj.continue_()

        async def main():
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
                page = await browser.new_page(viewport={"width": 720, "height": 640})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.route("**/*", route)
                await page.goto(base + "/quick/?native=1", wait_until="load")
                await page.wait_for_function("window.QuickAsk && window.QuickComputer && document.documentElement.dataset.design")
                calls.clear()
                await page.evaluate("QuickComputer.start({client:{type:'computer', task:'Открой Блокнот', max_steps:12}, _attach:true, _taskId:'t1'})")
                await page.wait_for_timeout(3000)
                first = len(calls)
                head = await page.evaluate("(document.querySelector('.qcu-now')||{}).textContent || ''")
                for _ in range(5):            # the window reopened 5 times: each «open» polls now, still one chain
                    await page.evaluate("QuickAsk.onShow({fresh:false, maxHeight:640, rounded:true, corner:'dwm', radius:8, pinned:false, source:'hotkey', page:null})")
                calls.clear()
                await page.wait_for_timeout(3000)
                second = len(calls)
                await browser.close()
                return first, head, second, errors

        try:
            return asyncio.run(main())
        finally:
            server.shutdown()
            server.server_close()

    def test_card_keeps_polling_state(self):
        first, head, second, errors = self.run_card()
        self.assertEqual(errors, [])
        self.assertGreaterEqual(first, 3, "the card must poll /state every ~0.7 s")
        self.assertNotIn("Запускается", head)
        self.assertGreaterEqual(second, 3)
        self.assertLessEqual(second, 6, "reopening the window must not add poll chains")


if __name__ == "__main__":
    unittest.main()
