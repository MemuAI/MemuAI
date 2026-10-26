"""Round 24 — Do For You (DFY): agent loop, confirmations, secrets, cancel, SSE, settings.

A fake vision model (scripted JSON actions) and a fake browser are used, so the
test runs without Edge and without MemuAI Proxy.
"""
import asyncio
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from app.dfy import DFYManager, normalize_settings
from app.dfy_vault import SecretVault, VaultError, domain_matches

CARD = "4242424242424242"
PASSWORD = "Tr0ub4dor&3-very-secret"

class FakeBrowser:
    def __init__(self, url="https://shop.example.com/cart"):
        self.url = url
        self.typed = []
        self.clicked = []
        self.closed = False
        self.started = 0
        self.elements = [
            {"id": 1, "tag": "input", "type": "text", "label": "Email"},
            {"id": 2, "tag": "input", "type": "password", "label": "Password"},
            {"id": 3, "tag": "button", "text": "Оплатить 120 €"},
            {"id": 4, "tag": "a", "text": "Каталог"},
        ]

    async def start(self):
        self.started += 1

    async def snapshot(self):

        text = "Корзина. " + " ".join(value for _, value in self.typed)
        return {"url": self.url, "title": "Shop", "text": text, "elements": self.elements,
                "screenshot": "/9j/4AAQSkZJRgABAQ=="}

    async def current_url(self):
        return self.url

    async def element_info(self, element):
        item = next((e for e in self.elements if e["id"] == element), None)
        if not item:
            raise ValueError("missing")
        return {"text": item.get("text") or item.get("label", ""), "tag": item["tag"]}

    async def navigate(self, url):
        self.url = url
        return url

    async def click(self, element):
        self.clicked.append(element)
        return "clicked"

    async def type(self, element, text, *, secret=False, submit=False):
        self.typed.append((element, text))
        return "typed"

    async def select(self, element, value):
        return "selected"

    async def key(self, name):
        return name

    async def scroll(self, dy):
        return "scrolled"

    async def back(self):
        return None

    async def close(self):
        self.closed = True

    async def clear_profile(self):
        self.closed = True

class ScriptedModel:
    """Returns the next scripted action; a callable step may build it from the prompt."""

    def __init__(self, script):
        self.script = list(script)
        self.prompts = []

    async def __call__(self, model, messages):
        self.prompts.append(json.dumps(messages, ensure_ascii=False))
        if not self.script:
            return json.dumps({"action": "done", "result": "end of script"})
        item = self.script.pop(0)
        if callable(item):
            item = item(messages)
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)

def last_secret_ref(messages):
    text = json.dumps(messages, ensure_ascii=False)
    marker = "user provided vault entry "
    start = text.index(marker) + len(marker)
    return text[start:start + 13]

class DFYBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prefs = {"dfy_enabled": True, "dfy_model": "vision/model", "dfy_max_steps": 20}
        self.browser = FakeBrowser()
        self.vault = SecretVault(self.root, use_dpapi=False)

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self, model, vision=True):
        return DFYManager(model, lambda: dict(self.prefs), data_root=self.root,
                          vision_lookup=lambda _m: vision, browser_factory=lambda _visible: self.browser,
                          vault=self.vault)

    async def wait_status(self, mgr, task, statuses, timeout=5.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while task.status not in statuses:
            if asyncio.get_running_loop().time() > deadline:
                self.fail(f"status {task.status!r} never became {statuses}")
            await asyncio.sleep(0.01)
        return task

class LoopTests(DFYBase):
    async def test_basic_loop_steps_and_screenshots(self):
        model = ScriptedModel([
            {"action": "navigate", "url": "https://shop.example.com/", "reason": "open"},
            {"action": "click", "element": 4, "reason": "catalog"},
            {"action": "done", "result": "Каталог открыт"},
        ])
        mgr = self.manager(model)
        task = await mgr.create("Открой каталог", [{"role": "user", "content": "хочу каталог"}], "chat")
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(task.result, "Каталог открыт")
        self.assertEqual([s["action"] for s in task.steps], ["navigate", "click", "done"])
        self.assertEqual(self.browser.clicked, [4])

        self.assertRegex(task.steps[0]["screenshot_url"], r"/screenshots/1(\?v=\d+)?$")
        self.assertIsNotNone(mgr.screenshot_path(task.id, 1))

        self.assertIn("хочу каталог", model.prompts[0])

        self.assertIn("data:image/jpeg;base64,", model.prompts[0])

    async def test_not_ready_without_vision(self):
        mgr = self.manager(ScriptedModel([]), vision=False)
        st = mgr.status()
        self.assertFalse(st["ready"])
        self.assertIn("Vision", st["reason"])
        with self.assertRaises(PermissionError):
            await mgr.create("x")

    async def test_step_limit(self):
        self.prefs["dfy_max_steps"] = 5
        mgr = self.manager(ScriptedModel([{"action": "scroll", "dy": 300}] * 10))
        task = await mgr.create("листай")
        await self.wait_status(mgr, task, ("failed",))
        self.assertIn("лимит", task.error)
        self.assertEqual(len(task.steps), 5)

    async def test_non_json_reply_is_retried(self):
        mgr = self.manager(ScriptedModel(["sure!", {"action": "done", "result": "ok"}]))
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(task.result, "ok")

class ConfirmTests(DFYBase):
    async def test_payment_click_is_held_until_approved(self):
        model = ScriptedModel([{"action": "click", "element": 3, "reason": "pay"},
                               {"action": "done", "result": "Оплачено"}])
        mgr = self.manager(model)
        task = await mgr.create("Купи товар")
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertEqual(task.question["kind"], "confirm")
        self.assertIn("Оплатить", task.question["prompt"])
        self.assertEqual(self.browser.clicked, [], "nothing is clicked before the go-ahead")
        await mgr.answer(task.id, {"approve": True})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.clicked, [3])

    async def test_declined_payment_is_not_clicked(self):
        model = ScriptedModel([{"action": "click", "element": 3},
                               {"action": "done", "result": "Пользователь отказался"}])
        mgr = self.manager(model)
        task = await mgr.create("Купи товар")
        await self.wait_status(mgr, task, ("waiting_user",))
        await mgr.answer(task.id, {"approve": False})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.clicked, [])
        self.assertIn("declined", model.prompts[-1])

    async def test_model_confirm_with_amount_then_click_needs_no_second_prompt(self):
        model = ScriptedModel([{"action": "confirm", "summary": "Оплата заказа", "amount": "120 €", "element": 3},
                               {"action": "click", "element": 3}, {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("Купи")
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertEqual(task.question["amount"], "120 €")
        await mgr.answer(task.id, {"approve": True})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.clicked, [3])

    async def test_every_step_mode_confirms_plain_clicks(self):
        self.prefs["dfy_confirm_mode"] = "every_step"
        mgr = self.manager(ScriptedModel([{"action": "click", "element": 4}, {"action": "done", "result": "ok"}]))
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        await mgr.answer(task.id, {"approve": True})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.clicked, [4])

class SecretTests(DFYBase):
    async def test_secret_never_reaches_model_journal_or_public_state(self):
        model = ScriptedModel([
            {"action": "request_secret", "secret_kind": "login", "label": "Вход в магазин"},
            lambda messages: {"action": "type", "element": 2,
                              "text": "{{secret:%s.password}}" % last_secret_ref(messages)},
            {"action": "done", "result": "Вошёл"},
        ])
        mgr = self.manager(model)
        task = await mgr.create("Войди в магазин")
        await self.wait_status(mgr, task, ("waiting_user",))
        q = task.question
        self.assertEqual(q["kind"], "secret")
        self.assertEqual(q["domain"], "shop.example.com")
        self.assertEqual(q["fields"], ["username", "password"])
        await mgr.answer(task.id, {"secret": {"fields": {"username": "me@example.com", "password": PASSWORD},
                                              "save": True}})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.typed, [(2, PASSWORD)], "the browser gets the real value")
        everything = "\n".join(model.prompts) + json.dumps(task.public(), ensure_ascii=False) + \
            json.dumps(mgr.list(), ensure_ascii=False) + json.dumps(mgr.vault.list(), ensure_ascii=False)
        self.assertNotIn(PASSWORD, everything)
        self.assertNotIn(PASSWORD, (self.root / "dfy_vault.json").read_text("utf-8"))
        self.assertIn("[секрет]", model.prompts[-1], "a value echoed by the page is redacted for the model")

    async def test_login_is_bound_to_its_domain(self):
        meta = self.vault.add("login", {"username": "u", "password": PASSWORD}, domain="other.example.org")
        self.browser.url = "https://evil.example.net/login"
        model = ScriptedModel([{"action": "type", "element": 2, "text": "{{secret:%s.password}}" % meta["id"]},
                               {"action": "fail", "error": "wrong site"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("failed",))
        self.assertEqual(self.browser.typed, [])
        self.assertFalse(task.steps[0]["ok"])
        self.assertIn("other.example.org", task.steps[0]["detail"])

    async def test_card_number_in_plain_answer_is_refused(self):
        mgr = self.manager(ScriptedModel([{"action": "ask_user", "question": "Какой отель?"},
                                          {"action": "done", "result": "ok"}]))
        task = await mgr.create("Забронируй")
        await self.wait_status(mgr, task, ("waiting_user",))
        with self.assertRaises(ValueError):
            await mgr.answer(task.id, {"answer": "моя карта 4242 4242 4242 4242"})
        await mgr.answer(task.id, {"answer": "Hilton"})
        await self.wait_status(mgr, task, ("done",))

    async def test_ask_user_for_password_becomes_secure_form(self):
        mgr = self.manager(ScriptedModel([{"action": "ask_user", "question": "Введите пароль от сайта"}]))
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertEqual(task.question["kind"], "secret")
        await mgr.cancel(task.id)

    async def test_plain_typed_card_number_is_blocked(self):
        mgr = self.manager(ScriptedModel([{"action": "type", "element": 1, "text": CARD},
                                          {"action": "done", "result": "ok"}]))
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.typed, [])
        self.assertNotIn(CARD, json.dumps(task.public()))

    async def test_ephemeral_secret_is_forgotten_after_task(self):
        mgr = self.manager(ScriptedModel([{"action": "request_secret", "secret_kind": "text", "label": "код"},
                                          {"action": "done", "result": "ok"}]))
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        await mgr.answer(task.id, {"secret": {"fields": {"value": "123456"}, "save": False}})
        await self.wait_status(mgr, task, ("done",))
        await asyncio.sleep(0.05)
        self.assertEqual(self.vault.list(), [])

class CancelTests(DFYBase):
    async def test_cancel_while_waiting(self):
        mgr = self.manager(ScriptedModel([{"action": "ask_user", "question": "Даты?"}]))
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        await mgr.cancel(task.id)
        self.assertEqual(task.status, "cancelled")
        self.assertIsNone(task.question)
        with self.assertRaises(RuntimeError):
            await mgr.answer(task.id, {"answer": "завтра"})

    async def test_cancel_during_model_call(self):
        gate = asyncio.Event()

        async def slow(model, messages):
            await gate.wait()
            return json.dumps({"action": "done"})
        mgr = self.manager(slow)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("running",))
        await asyncio.sleep(0.02)
        await mgr.cancel(task.id)
        self.assertEqual(task.status, "cancelled")

class VaultTests(unittest.TestCase):
    def test_fallback_encryption_and_key_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = SecretVault(Path(tmp), use_dpapi=False)
            meta = vault.add("card", {"number": "4242 4242 4242 4242", "exp_month": "12", "exp_year": "2030",
                                      "cvc": "123", "holder": "IVAN"})
            self.assertEqual(meta["mask"], "Visa •••• 4242")
            raw = (Path(tmp) / "dfy_vault.json").read_text("utf-8")
            self.assertNotIn(CARD, raw)
            self.assertNotIn("IVAN", raw)
            if os.name != "nt":
                mode = stat.S_IMODE((Path(tmp) / "dfy_vault.key").stat().st_mode)
                self.assertEqual(mode, 0o600)
            self.assertEqual(vault.resolve("{{secret:%s.number}}" % meta["id"], "https://pay.example.com/"), CARD)
            with self.assertRaises(VaultError):
                vault.resolve("{{secret:%s.number}}" % meta["id"], "http://pay.example.com/")

            data = json.loads(raw)
            blob = data["items"][meta["id"]]["blob"]
            data["items"][meta["id"]]["blob"] = blob[:-6] + ("AAAA" if not blob.endswith("AAAA==") else "BBBB") + "=="
            (Path(tmp) / "dfy_vault.json").write_text(json.dumps(data), "utf-8")
            with self.assertRaises(VaultError):
                SecretVault(Path(tmp), use_dpapi=False).resolve("{{secret:%s.cvc}}" % meta["id"], "https://a.com")
            self.assertTrue(vault.delete(meta["id"]))

    def test_validation_and_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = SecretVault(Path(tmp), use_dpapi=False)
            with self.assertRaises(VaultError):
                vault.add("card", {"number": "1234 5678 9012 3456"})
            with self.assertRaises(VaultError):
                vault.add("login", {"username": "u", "password": "p"})
        self.assertTrue(domain_matches("booking.com", "https://secure.booking.com/x"))
        self.assertFalse(domain_matches("booking.com", "https://booking.com.evil.io/"))
        self.assertFalse(domain_matches("booking.com", "http://booking.com/"))

class SettingsTests(unittest.TestCase):
    def test_normalize(self):
        out = normalize_settings({"dfy_enabled": "false", "dfy_max_steps": "1000", "dfy_confirm_mode": "never",
                                  "dfy_browser_visible": 1, "dfy_model": " m "})
        self.assertEqual(out, {"dfy_enabled": False, "dfy_model": "m", "dfy_max_steps": 80,
                               "dfy_confirm_mode": "irreversible", "dfy_browser_visible": True,
                           "dfy_local_system": False, "dfy_schedule_browser": "isolated"})
        self.assertTrue(normalize_settings({"dfy_local_system": "on"})["dfy_local_system"])
        self.assertEqual(normalize_settings(None)["dfy_max_steps"], 30)
        self.assertEqual(normalize_settings({"dfy_max_steps": "x"})["dfy_max_steps"], 30)

    def test_preferences_roundtrip(self):
        from app import pgdata
        prefs = pgdata._normalize_preferences({"dfy_max_steps": 2, "dfy_confirm_mode": "every_step"})
        self.assertEqual(prefs["dfy_max_steps"], 5)
        self.assertEqual(prefs["dfy_confirm_mode"], "every_step")
        self.assertTrue(prefs["dfy_enabled"])
        self.assertIn("dfy_model", pgdata.DEFAULT_PREFERENCES)

class HttpTests(unittest.TestCase):
    def test_contract_and_sse(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.dfy_api import build_router
        with tempfile.TemporaryDirectory() as tmp:
            browser = FakeBrowser()
            model = ScriptedModel([{"action": "click", "element": 3},
                                   {"action": "done", "result": "Оплачено"}])
            mgr = DFYManager(model, lambda: {"dfy_model": "vision/model"}, data_root=Path(tmp),
                             vision_lookup=lambda _m: True, browser_factory=lambda _v: browser,
                             vault=SecretVault(Path(tmp), use_dpapi=False))
            app = FastAPI()
            app.include_router(build_router(mgr))
            with TestClient(app) as client:
                st = client.get("/api/dfy/status").json()
                self.assertTrue(st["ready"])
                self.assertEqual(set(["enabled", "model", "model_has_vision", "ready", "reason"]) - set(st), set())
                self.assertEqual(client.post("/api/dfy/tasks", json={"goal": ""}).status_code, 400)
                created = client.post("/api/dfy/tasks", json={"goal": "Купи", "context": [], "source": "quick"}).json()
                task_id = created["id"]

                for _ in range(200):
                    item = client.get(f"/api/dfy/tasks/{task_id}").json()
                    if item["status"] == "waiting_user":
                        break
                    time.sleep(0.02)
                self.assertEqual(item["question"]["kind"], "confirm")
                self.assertEqual(client.post(f"/api/dfy/tasks/{task_id}/answer", json={"approve": True}).status_code, 200)
                for _ in range(200):
                    if client.get(f"/api/dfy/tasks/{task_id}").json()["status"] == "done":
                        break
                    time.sleep(0.02)
                seen = []
                with client.stream("GET", f"/api/dfy/tasks/{task_id}/events") as stream:
                    self.assertEqual(stream.headers["content-type"].split(";")[0], "text/event-stream")
                    for line in stream.iter_lines():
                        if line.startswith("data: "):
                            seen.append(json.loads(line[6:]))
                self.assertEqual(seen[-1]["status"], "done")
                self.assertTrue(any(step["action"] == "confirm" for step in seen[-1]["steps"]))
                full = client.get(f"/api/dfy/tasks/{task_id}").json()
                self.assertEqual(full["result"], "Оплачено")
                self.assertEqual(full["source"], "quick")
                shot = client.get(full["steps"][0]["screenshot_url"])
                self.assertEqual(shot.headers["content-type"], "image/jpeg")
                self.assertEqual(client.get("/api/dfy/tasks").json()["tasks"][0]["id"], task_id)
                self.assertEqual(client.post(f"/api/dfy/tasks/{task_id}/answer", json={"approve": True}).status_code, 409)
                self.assertEqual(client.get("/api/dfy/tasks/000000000000").status_code, 404)
                added = client.post("/api/dfy/vault", json={"kind": "login", "domain": "a.com",
                                                              "fields": {"username": "u", "password": PASSWORD}})
                self.assertEqual(added.status_code, 200)
                listing = client.get("/api/dfy/vault").text
                self.assertNotIn(PASSWORD, listing)
                self.assertEqual(client.delete(f"/api/dfy/vault/{added.json()['item']['id']}").status_code, 200)
                self.assertEqual(client.post("/api/dfy/browser/clear").status_code, 200)

class StaticTests(unittest.TestCase):
    def test_client_is_served_and_wired(self):
        root = Path(__file__).resolve().parents[1] / "app" / "pgstatic"
        client = (root / "dfy-client.js").read_text("utf-8")
        for name in ("status", "start", "watch", "answer", "cancel", "renderCard"):
            self.assertIn(name, client)

        self.assertIn('password: { l: ["Пароль", "Password"], type: "password"', client)
        index = (root / "index.html").read_text("utf-8")
        self.assertIn('src="/dfy-client.js', index)
        app_js = (root / "app.js").read_text("utf-8")
        self.assertIn("memuai-dfy", app_js)
        self.assertIn('"/dfy"', app_js)

if __name__ == "__main__":
    unittest.main()

class ReviewFixTests(DFYBase):
    """Round 24 review: Quick Ask string answers for secrets, localhost/file: blocking."""

    async def test_secret_answer_as_json_string_goes_to_vault(self):
        model = ScriptedModel([
            {"action": "request_secret", "secret_kind": "login", "label": "Вход"},
            lambda messages: {"action": "type", "element": 2,
                              "text": "{{secret:%s.password}}" % last_secret_ref(messages)},
            {"action": "done", "result": "ok"},
        ])
        mgr = self.manager(model)
        task = await mgr.create("Войди")
        await self.wait_status(mgr, task, ("waiting_user",))
        await mgr.answer(task.id, {"answer": json.dumps({"username": "me@example.com", "password": PASSWORD})})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.typed, [(2, PASSWORD)])
        everything = "\n".join(model.prompts) + json.dumps(task.public(), ensure_ascii=False) + \
            "\n".join(task.history) + json.dumps(mgr.vault.list(), ensure_ascii=False)
        self.assertNotIn(PASSWORD, everything)

    async def test_secret_answer_single_field_string_and_ambiguous_refused(self):
        mgr = self.manager(ScriptedModel([{"action": "request_secret", "secret_kind": "text", "label": "код"},
                                          {"action": "done", "result": "ok"}]))
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        await mgr.answer(task.id, {"answer": "654321"})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(len(self.vault.list()), 1)
        self.assertNotIn("654321", json.dumps(task.public(), ensure_ascii=False))

        mgr2 = self.manager(ScriptedModel([{"action": "request_secret", "secret_kind": "login", "label": "вход"}]))
        task2 = await mgr2.create("y")
        await self.wait_status(mgr2, task2, ("waiting_user",))
        with self.assertRaises(ValueError):
            await mgr2.answer(task2.id, {"answer": PASSWORD})
        await mgr2.cancel(task2.id)

    async def test_click_landing_on_local_address_is_reverted(self):
        browser = self.browser
        backs = []

        async def click(element):
            browser.url = "http://127.0.0.1:8765/api/preferences"
            return "clicked"

        async def back():
            backs.append(1)
            browser.url = "https://shop.example.com/cart"

        browser.click = click
        browser.back = back
        model = ScriptedModel([{"action": "click", "element": 4}, {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(backs, [1])
        self.assertTrue(any(s["ok"] is False for s in task.steps))

    def test_url_blocked_reason(self):
        from app.dfy_browser import url_blocked_reason
        for bad in ("file:///C:/Windows/win.ini", "http://localhost:8000/", "http://127.0.0.1/",
                    "http://[::1]/", "http://192.168.1.1/", "chrome://settings", "javascript:alert(1)",
                    "http://169.254.169.254/latest", "http://app.localhost/"):
            self.assertTrue(url_blocked_reason(bad), bad)
        for good in ("https://example.com/", "about:blank", "", "http://8.8.8.8/"):
            self.assertEqual(url_blocked_reason(good), "", good)

    def test_quick_card_sends_contract_secret_shape(self):
        src = (Path(__file__).resolve().parent.parent / "app/pgstatic/quick/quick-dfy.js").read_text("utf-8")
        self.assertIn("{ secret: { kind: q.secret_kind", src)
        self.assertIn('data-qdfy="decline-secret"', src)
        self.assertIn("secret_ref: btn.dataset.value", src)
