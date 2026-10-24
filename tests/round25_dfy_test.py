"""Round 25 — DFY: personal-data vault (phone, e-mail, address, document, company), one-time code
form, safer loop (step limit, stuck pages, Enter/coordinate confirmations), vision source shared
with the settings page, settings/quick UI contracts.

Fake model and fake browser (from round24_dfy_test), no Edge, no Proxy, no network.
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import tests.round24_dfy_test as r24
from app.dfy import DFYManager
from app.dfy_vault import (KIND_FIELDS, REQUESTABLE_KINDS, SecretVault, VaultError, derived_fields,
                           normalize_phone, split_phone)

ROOT = Path(__file__).resolve().parents[1]
PHONE = "+79161234567"


def ref_after(messages, marker):
    text = json.dumps(messages, ensure_ascii=False)
    start = text.rindex(marker) + len(marker)
    return text[start:start + 13]


class FormBrowser(r24.FakeBrowser):
    """FakeBrowser + the Round 25 browser surface: submit_info, point_info, click_xy, focused typing."""

    def __init__(self):
        super().__init__()
        self.submit_label = None
        self.point_label = ""
        self.points = []

    async def submit_info(self, element=None):
        return None if self.submit_label is None else {"text": self.submit_label}

    async def point_info(self, x, y):
        return {"text": self.point_label}

    async def click_xy(self, x, y):
        self.points.append((x, y))
        return "clicked"

    async def scroll(self, dy, element=None):
        return f"scrolled {dy} in {element}"


class Base(r24.DFYBase):
    def setUp(self):
        super().setUp()
        self.browser = FormBrowser()


class VaultPersonalDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = SecretVault(Path(self.tmp.name), use_dpapi=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_phone_normalization_and_split(self):
        self.assertEqual(normalize_phone("8 (916) 123-45-67"), PHONE)
        self.assertEqual(normalize_phone("+370 612 34567"), "+37061234567")
        self.assertEqual(normalize_phone("0049 30 1234567"), "+49301234567")
        self.assertEqual(split_phone("+37061234567"), ("+370", "61234567"))
        self.assertEqual(split_phone(PHONE), ("+7", "9161234567"))
        self.assertEqual(split_phone("+12025550123"), ("+1", "2025550123"))
        for bad in ("9161234567", "+12", "+0123456789", "abc"):
            with self.assertRaises(VaultError, msg=bad):
                normalize_phone(bad)

    def test_new_kinds_masks_never_show_values(self):
        phone = self.vault.add("phone", {"phone": "8 916 123 45 67"})
        email = self.vault.add("email", {"email": "Ivan.Petrov@Example.com"})
        doc = self.vault.add("document", {"doc_type": "Паспорт РФ", "series": "4510", "number": "123456",
                                          "issue_date": "01.02.2015"})
        addr = self.vault.add("address", {"address_type": "shipping", "city": "Вильнюс", "street": "Gedimino",
                                          "house": "9", "postal_code": "01103", "recipient": "Иван Петров"})
        company = self.vault.add("company", {"company_name": "ООО Ромашка", "inn": "7701234567"})
        listing = json.dumps(self.vault.public_for_model(), ensure_ascii=False)
        for value in ("9161234567", "ivan.petrov@", "4510", "123456", "Gedimino", "Иван Петров", "7701234567"):
            self.assertNotIn(value, listing)
        self.assertEqual(phone["mask"], "+7 •••• ••67")
        self.assertTrue(email["mask"].startswith("iv•••@example.com"))
        self.assertIn("••••56", doc["mask"])
        self.assertIn("доставка", addr["mask"])
        self.assertIn("ООО Ромашка", company["mask"])
        rows = {row["id"]: row for row in self.vault.public_for_model()}
        self.assertIn("phone_national", rows[phone["id"]]["derived_fields"])
        self.assertIn("issue_date_dmy", rows[doc["id"]]["derived_fields"])
        with self.assertRaises(VaultError):
            self.vault.add("company", {"inn": "12"})
        with self.assertRaises(VaultError):
            self.vault.add("email", {"email": "nope"})

    def test_derived_fields_resolve(self):
        person = self.vault.add("identity", {"first_name": "Иван", "middle_name": "Сергеевич", "last_name": "Петров",
                                             "birth_date": "20.09.2000", "phone": "+370 612 34567"})
        sid = person["id"]
        text = " | ".join("{{secret:%s.%s}}" % (sid, f) for f in
                          ("phone_national", "phone_country_code", "birth_date_dmy", "birth_year", "fio", "full_name"))
        self.assertEqual(self.vault.resolve(text, "https://shop.example.com/"),
                         "61234567 | +370 | 20.09.2000 | 2000 | Петров Иван Сергеевич | Иван Сергеевич Петров")
        card = self.vault.add("card", {"number": r24.CARD, "exp_month": "9", "exp_year": "2031", "cvc": "123"})
        self.assertEqual(self.vault.resolve("{{secret:%s.exp}}" % card["id"], "https://pay.example.com/"), "09/31")
        self.assertEqual(derived_fields("otp", {"code": "4821"})["code_3"], "2")

    def test_redactor_hides_phone_in_any_format(self):
        self.vault.add("phone", {"phone": PHONE})
        redact = self.vault.redactor()
        out = redact("Мы отправили SMS на +7 (916) 123-45-67 и 8 916 1234567. Отель в Москве")
        self.assertNotIn("123-45-67", out)
        self.assertNotIn("1234567", out)
        self.assertIn("Москве", out)

    def test_city_and_country_are_not_redacted(self):
        self.vault.add("address", {"city": "Москва", "country": "Россия", "street": "Тверская улица"})
        out = self.vault.redactor()("Отели: Москва, Россия. Тверская улица 5")
        self.assertIn("Москва, Россия", out)
        self.assertNotIn("Тверская улица", out)

    def test_update_keeps_id_and_empty_keeps_value(self):
        item = self.vault.add("identity", {"first_name": "Иван", "phone": PHONE})
        updated = self.vault.update(item["id"], {"phone": "", "email": "a@b.co"}, label="Я")
        self.assertEqual(updated["id"], item["id"])
        self.assertEqual(updated["label"], "Я")
        self.assertEqual(self.vault.resolve("{{secret:%s.phone}}" % item["id"], "https://x.example/"), PHONE)
        self.assertIn("email", updated["fields"])
        cleared = self.vault.update(item["id"], {}, clear=["email"])
        self.assertNotIn("email", cleared["fields"])
        with self.assertRaises(VaultError):
            self.vault.update("snope00000000", {"phone": PHONE})
        with self.assertRaises(VaultError):
            self.vault.update(item["id"], {"phone": "123"})

    def test_otp_is_never_written_to_disk(self):
        meta = self.vault.add("otp", {"code": "12 34 56"}, persist=True)
        self.assertFalse(meta["persistent"])
        self.assertFalse((Path(self.tmp.name) / "dfy_vault.json").exists())
        self.assertEqual(self.vault.resolve("{{secret:%s.code}}" % meta["id"], "https://x.example/"), "123456")


class CodeFormTests(Base):
    async def test_request_code_goes_to_vault_and_is_typed(self):
        model = r24.ScriptedModel([
            {"action": "request_code", "label": "Код из SMS", "channel": "sms", "sent_to": "+7 ••• ••67", "length": 6},
            lambda messages: {"action": "type", "element": 1,
                              "text": "{{secret:%s.code}}" % ref_after(messages, "vault entry ")},
            {"action": "done", "result": "Вошли"},
        ])
        mgr = self.manager(model)
        task = await mgr.create("Войди")
        await self.wait_status(mgr, task, ("waiting_user",))
        q = task.question
        self.assertEqual((q["kind"], q["channel"], q["length"]), ("code", "sms", 6))
        self.assertTrue(q["can_resend"])
        await mgr.answer(task.id, {"code": "482 913"})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.typed, [(1, "482913")])
        self.assertNotIn("482913", "".join(model.prompts))
        self.assertNotIn("482913", json.dumps(task.public(), ensure_ascii=False))
        self.assertEqual(self.vault.list(), [], "the one-time code is forgotten after the task")

    async def test_resend_and_plain_string_answer(self):
        model = r24.ScriptedModel([{"action": "request_code", "label": "Код"},
                                   {"action": "request_code", "label": "Код ещё раз"},
                                   {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        await mgr.answer(task.id, {"resend": True})
        for _ in range(200):
            if task.question and task.question.get("prompt") == "Код ещё раз":
                break
            await asyncio.sleep(0.01)
        self.assertIn("send it again", " ".join(task.history))
        await mgr.answer(task.id, {"answer": "7777"})   # Telegram-style plain answer
        await self.wait_status(mgr, task, ("done",))
        self.assertIn("entered the code", " ".join(task.answers))
        self.assertNotIn("7777", "".join(model.prompts))

    async def test_ask_user_for_sms_code_or_phone_becomes_secure_form(self):
        self.vault.add("identity", {"first_name": "Иван", "phone": PHONE})
        model = r24.ScriptedModel([{"action": "ask_user", "question": "Введите код из SMS"},
                                   {"action": "ask_user", "question": "Укажите номер телефона для доставки"},
                                   {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertEqual(task.question["kind"], "code")
        await mgr.answer(task.id, {"approve": False})
        for _ in range(200):
            if task.question and task.question.get("kind") == "secret":
                break
            await asyncio.sleep(0.01)
        self.assertEqual(task.question["secret_kind"], "phone")
        self.assertEqual(task.question["fields"], ["phone"])
        self.assertEqual(len(task.question["existing"]), 1, "the phone from personal data is offered")
        with self.assertRaises(VaultError):   # the API answers 400; the question stays open
            await mgr.answer(task.id, {"secret": {"fields": {"phone": "123"}}})
        self.assertEqual(task.status, "waiting_user")
        await mgr.answer(task.id, {"secret_ref": task.question["existing"][0]["id"]})
        await self.wait_status(mgr, task, ("done",))


class PersonalDataLoopTests(Base):
    async def test_phone_form_normalizes_and_national_format_is_typed(self):
        model = r24.ScriptedModel([
            {"action": "request_secret", "secret_kind": "phone", "label": "Телефон для брони"},
            lambda messages: {"action": "type", "element": 1,
                              "text": "{{secret:%s.phone_national}}" % ref_after(messages, "vault entry ")},
            {"action": "done", "result": "ok"},
        ])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertEqual(task.question["fields"], ["phone"])
        await mgr.answer(task.id, {"secret": {"fields": {"phone": "8 916 123-45-67"}, "save": True}})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.typed, [(1, "9161234567")])
        self.assertNotIn("9161234567", "".join(model.prompts))
        self.assertEqual(self.vault.list()[0]["kind"], "phone")

    async def test_requestable_kinds_and_unknown_fields_dropped(self):
        self.assertNotIn("otp", REQUESTABLE_KINDS)
        model = r24.ScriptedModel([{"action": "request_secret", "secret_kind": "address",
                                    "fields": ["city", "street", "evil_field", "BAD"]}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertEqual(task.question["fields"], ["city", "street"])
        await mgr.cancel(task.id)

    def test_prompt_documents_every_kind_and_action(self):
        from app.dfy import SYSTEM_PROMPT
        for kind in REQUESTABLE_KINDS:
            self.assertIn(kind, SYSTEM_PROMPT)
            for field in KIND_FIELDS[kind]:
                self.assertIn(field, SYSTEM_PROMPT, (kind, field))
        for action in ("request_code", "click_xy", "phone_national", "birth_date_dmy", "code_1"):
            self.assertIn(action, SYSTEM_PROMPT)


class LoopRobustnessTests(Base):
    async def test_questions_do_not_use_the_browser_step_limit(self):
        self.prefs["dfy_max_steps"] = 5
        script = [{"action": "ask_user", "question": f"Вопрос {n}?"} for n in range(6)]
        script.append({"action": "done", "result": "ok"})
        mgr = self.manager(r24.ScriptedModel(script))
        task = await mgr.create("x")
        for _ in range(6):
            await self.wait_status(mgr, task, ("waiting_user",))
            await mgr.answer(task.id, {"answer": "да"})
            for _ in range(100):
                if task.status != "waiting_user":
                    break
                await asyncio.sleep(0.005)
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(task.browser_steps, 0)

    async def test_stuck_on_unchanged_page_is_detected(self):
        model = r24.ScriptedModel([{"action": "click", "element": 4}] * 10)
        mgr = self.manager(model)
        task = await mgr.create("x")
        # Round 29: a stuck task is PAUSED for the user's verdict (app/stuck_detector.py), not failed.
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertEqual(task.question["kind"], "stuck")
        self.assertEqual(len(self.browser.clicked), 3)          # the 4th identical click is never performed
        self.assertIn("REPEATED", "".join(model.prompts))
        await mgr.answer(task.id, {"decision": "stop"})
        await self.wait_status(mgr, task, ("cancelled",))
        self.assertEqual(len(self.browser.clicked), 3)
        # sensitivity "off" keeps the old hard stop
        self.prefs["stuck_sensitivity"] = "off"
        self.browser.clicked.clear()
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("failed",))
        self.assertIn("не меняется", task.error)

    async def test_enter_in_payment_form_needs_confirmation(self):
        self.browser.submit_label = "Оплатить 120 €"
        model = r24.ScriptedModel([{"action": "key", "key": "Enter"}, {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertEqual(task.question["kind"], "confirm")
        self.assertIn("Оплатить", task.question["prompt"])
        await mgr.answer(task.id, {"approve": False})
        await self.wait_status(mgr, task, ("done",))
        self.assertIn("BLOCKED", " ".join(task.history))

    async def test_typed_submit_and_click_xy_on_pay_need_confirmation(self):
        self.browser.submit_label = "Book now"
        self.browser.point_label = "Pay"
        model = r24.ScriptedModel([{"action": "type", "element": 1, "text": "x", "submit": True},
                                   {"action": "click_xy", "x": 100, "y": 200},
                                   {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        await mgr.answer(task.id, {"approve": True})
        for _ in range(200):
            if task.question and "Pay" in task.question.get("prompt", ""):
                break
            await asyncio.sleep(0.01)
        self.assertIn("Pay", task.question["prompt"])
        await mgr.answer(task.id, {"approve": True})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.points, [(100.0, 200.0)])
        self.assertEqual(self.browser.typed, [(1, "x")])

    async def test_every_step_mode_still_confirms_after_an_approved_confirm(self):
        self.prefs["dfy_confirm_mode"] = "every_step"
        model = r24.ScriptedModel([{"action": "confirm", "summary": "Оформить заказ", "element": 3},
                                   {"action": "type", "element": 1, "text": "hello"},
                                   {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        await mgr.answer(task.id, {"approve": True})
        for _ in range(200):
            if task.question and task.question.get("prompt", "").startswith("DFY хочет выполнить: type"):
                break
            await asyncio.sleep(0.01)
        self.assertTrue(task.question["prompt"].startswith("DFY хочет выполнить: type"))
        await mgr.answer(task.id, {"approve": True})
        await self.wait_status(mgr, task, ("done",))

    async def test_focused_typing_and_inner_scroll(self):
        model = r24.ScriptedModel([{"action": "type", "text": "hello"},
                                   {"action": "scroll", "dy": 300, "element": 4},
                                   {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.typed, [(None, "hello")])
        self.assertIn("в активное поле", task.steps[0]["detail"])

    async def test_transient_model_error_is_retried_once(self):
        calls = []

        async def flaky(model, messages):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("502 Bad Gateway")
            return json.dumps({"action": "done", "result": "ok"})

        mgr = self.manager(flaky)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(len(calls), 2)

    async def test_user_answers_stay_in_prompt_after_long_history(self):
        script = [{"action": "ask_user", "question": "Какой отель?"}]
        script += [{"action": "scroll", "dy": 100 + n} for n in range(14)]
        script.append(lambda messages: {"action": "done", "result": json.dumps(messages, ensure_ascii=False)})
        self.prefs["dfy_max_steps"] = 30
        mgr = self.manager(r24.ScriptedModel(script))
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        await mgr.answer(task.id, {"answer": "Hilton Garden"})
        await self.wait_status(mgr, task, ("done",))
        self.assertIn("<user_answers>", task.result)
        self.assertIn("Hilton Garden", task.result.split("<user_answers>")[1].split("</user_answers>")[0])


class VisionSourceTests(unittest.TestCase):
    """The backend reads «has Vision» from /api/pg/state caps.vision — the list Settings shows."""

    def test_install_uses_pg_state_caps(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.dfy_api import install
        prefs = {"dfy_model": "text-only"}
        app = FastAPI()
        state_calls = []

        @app.get("/api/pg/state")
        async def pg_state():
            state_calls.append(1)
            return {"models": [{"id": "text-only", "caps": {"vision": False}},
                               {"id": "img-gen", "caps": {"vision": True}},
                               {"id": "seer", "caps": {"vision": True}}],
                    "image_models": ["img-gen"]}

        async def completion(model, messages):
            return "{}"

        manager = install(app, completion, lambda: dict(prefs))
        manager._real_browser = False
        with TestClient(app) as client:
            st = client.get("/api/dfy/status").json()
            self.assertIs(st["model_has_vision"], False)
            self.assertFalse(st["ready"])
            prefs["dfy_model"] = "seer"
            st = client.get("/api/dfy/status").json()
            self.assertIs(st["model_has_vision"], True)
            self.assertTrue(st["ready"])
            prefs["dfy_model"] = ""
            st = client.get("/api/dfy/status").json()
            self.assertEqual(st["model"], "seer", "the default skips image-generation models")
        self.assertEqual(len(state_calls), 1, "cached between status calls")

    def test_vault_put_contract(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.dfy_api import build_router
        with tempfile.TemporaryDirectory() as tmp:
            mgr = DFYManager(r24.ScriptedModel([]), lambda: {"dfy_model": "m"}, data_root=Path(tmp),
                             vision_lookup=lambda _m: True, browser_factory=lambda _v: r24.FakeBrowser(),
                             vault=SecretVault(Path(tmp), use_dpapi=False))
            app = FastAPI()
            app.include_router(build_router(mgr))
            with TestClient(app) as client:
                item = client.post("/api/dfy/vault", json={"kind": "phone", "fields": {"phone": "+44 20 7946 0958"}}).json()["item"]
                changed = client.put(f"/api/dfy/vault/{item['id']}", json={"fields": {"phone": PHONE}, "label": "Мобильный"})
                self.assertEqual(changed.status_code, 200)
                self.assertEqual(changed.json()["item"]["mask"], "+7 •••• ••67")
                self.assertNotIn("9161234567", client.get("/api/dfy/vault").text)
                self.assertEqual(client.put(f"/api/dfy/vault/{item['id']}", json={"fields": {"phone": "1"}}).status_code, 400)
                self.assertEqual(client.put("/api/dfy/vault/snope00000000", json={"fields": {}}).status_code, 404)


class ReviewRound25Tests(Base):
    """Round 25 loop review: unreadable targets, dialogs, invalid secret input, default model."""

    async def test_click_into_cross_origin_frame_with_pay_reason_needs_confirmation(self):
        self.browser.point_label = ""          # inside a cross-origin payment frame: no readable text
        model = r24.ScriptedModel([{"action": "click_xy", "x": 10, "y": 20, "reason": "focus card number"},
                                   {"action": "click_xy", "x": 30, "y": 40, "reason": "press Pay in the payment frame"},
                                   {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        self.assertEqual(self.browser.points, [(10.0, 20.0)], "an ordinary frame click is not asked about")
        self.assertEqual(task.question["kind"], "confirm")
        await mgr.answer(task.id, {"approve": False})
        await self.wait_status(mgr, task, ("done",))
        self.assertEqual(self.browser.points, [(10.0, 20.0)])

    async def test_invalid_phone_in_answer_is_bad_input_not_a_crash(self):
        model = r24.ScriptedModel([{"action": "request_secret", "secret_kind": "phone", "label": "Телефон"},
                                   {"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("waiting_user",))
        with self.assertRaises(ValueError):
            await mgr.answer(task.id, {"secret": {"fields": {"phone": "916 123"}}})
        self.assertEqual(task.status, "waiting_user", "the form stays open for a corrected value")
        await mgr.answer(task.id, {"secret": {"fields": {"phone": "8 916 123-45-67"}, "save": False}})
        await self.wait_status(mgr, task, ("done",))

    async def test_page_dialogs_reach_the_prompt(self):
        state = await self.browser.snapshot()
        self.browser.snapshot = lambda: asyncio.sleep(0, {**state, "dialogs": [
            {"type": "confirm", "message": "Удалить заказ?", "answered": "dismissed"}]})
        model = r24.ScriptedModel([{"action": "done", "result": "ok"}])
        mgr = self.manager(model)
        task = await mgr.create("x")
        await self.wait_status(mgr, task, ("done",))
        self.assertIn("Удалить заказ?", model.prompts[0])

    def test_default_model_skips_a_configured_model_without_vision(self):
        self.prefs.update(dfy_model="", vision_fallback_model="blind")
        mgr = DFYManager(r24.ScriptedModel([]), lambda: dict(self.prefs), data_root=self.root,
                         vision_lookup=lambda m: m != "blind", browser_factory=lambda _v: self.browser,
                         vault=self.vault, fallback_model=lambda: "seer")
        self.assertEqual(mgr.status()["model"], "seer")
        self.prefs["vision_fallback_model"] = "eyes"
        self.assertEqual(mgr.status()["model"], "eyes")


class BrowserDialogTests(unittest.TestCase):
    """DFYBrowser answers alert/confirm itself (they used to block every CDP script call)."""

    def test_dialogs_are_answered_safely_and_reported(self):
        from app.dfy_browser import DFYBrowser
        browser = DFYBrowser(visible=False)
        sent = []

        async def command(method, params=None, timeout=30):
            sent.append((method, params))
            return {}

        browser.command = command

        async def run():
            browser._on_dialog({"type": "alert", "message": "Привет"})
            browser._on_dialog({"type": "confirm", "message": "Оплатить 100 €?"})
            browser._on_dialog({"type": "beforeunload", "message": ""})
            await asyncio.sleep(0)

        asyncio.run(run())
        self.assertEqual([p["accept"] for _, p in sent], [True, False, True],
                         "confirm() is never accepted on the model's behalf")
        self.assertEqual([d["answered"] for d in browser.dialogs], ["accepted", "dismissed", "accepted"])


class FrontendContractTests(unittest.TestCase):
    def read(self, rel):
        return (ROOT / "app" / "pgstatic" / rel).read_text("utf-8")

    def test_client_forms_cover_every_vault_field(self):
        client = self.read("dfy-client.js")
        for kind in REQUESTABLE_KINDS:
            self.assertRegex(client, rf"\b{kind}: \{{ l:", kind)
            for field in KIND_FIELDS[kind]:
                self.assertRegex(client, rf"\b{field}: \{{ l:", field)
        for needle in ('autocomplete="one-time-code"', 'inputmode="numeric"', "data-dfy=\"resend\"", "{ code: value }",
                       "{ resend: true }", "formatPhone", "forms: {", "update: (id, item)"):
            self.assertIn(needle, client)

    def test_settings_lists_only_vision_models_and_updates_in_place(self):
        src = self.read("dfy-settings.js")
        self.assertNotIn("disabled", src.split("function modelOptions")[1].split("function html")[0])
        self.assertNotIn("ctx.rerender", src, "changing a setting must not re-render the whole page (white flash)")
        self.assertIn("st-dfy-model-reset", src)
        self.assertIn("MemuDFY.vault.update", src)

    def test_quick_card_has_all_fields_code_form_and_amount(self):
        src = self.read("quick/quick-dfy.js")
        self.assertNotIn("q.fields.slice(0, 4)", src)
        for needle in ('data-kind="code"', 'data-qdfy="resend"', "payload = { code }", "qdfy-amount", "F.collect(form, q.secret_kind)"):
            self.assertIn(needle, src)


if __name__ == "__main__":
    unittest.main()
