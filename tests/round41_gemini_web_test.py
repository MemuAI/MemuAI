"""Round 41 (r41-gemini): Gemini Web — models of the account (GetUserStatus), model-selection headers, Thinking level,
service-stub replies treated as errors, chat clean-up. No network: responses are recorded in the real wire format
(batchexecute / StreamGenerate length-prefixed frames, field positions as in the public Gemini web client)."""
import asyncio
import json
import unittest
import urllib.parse
from types import SimpleNamespace
from unittest.mock import patch

from app.providers import geminiweb_proto as proto
from app.providers.base import ProviderModelUnavailableError
from app.providers.geminiweb import GeminiWebProvider, LEGACY, MODELS

PSID = "g.a000fixturePSIDvalue-abcdefghijklmnopqrstuvwxyz0123456789"
TS = "sidts-CjEBfixtureTSvalue1234567890"
PAGE = '"SNlM0e":"AT_tok","cfb2h":"boq_gemini-web-uiserver_20261012.15_p0","FdrFJe":"-55","oPEP7c":"me@example.com"'

PICKER = [("a1b2c3d4e5f60001", "3.5 Flash-Lite", "Fastest answers", 6),
          ("a1b2c3d4e5f60002", "3.8 Flash", "Everyday help", 1),
          ("a1b2c3d4e5f60003", "3.1 Pro", "Advanced math and code", 3),
          ("a1b2c3d4e5f60004", "4 Argon", "Our most capable model", 7)]

def model_entry(model_id, display, description, number, *, sparse=False):
    entry = [None] * 18
    entry[0] = model_id
    entry[11] = display
    entry[12] = description
    if sparse:
        entry = entry[:13] + [{"18": number}]
    else:
        entry[17] = number
    return entry

def status_body(models, *, status=1000, tiers=(), caps=()):
    body = [None] * 18
    body[14] = status
    body[15] = models
    body[16] = list(tiers)
    body[17] = list(caps)
    return body

def batch_response(rpcid, body):
    part = json.dumps([["wrb.fr", rpcid, json.dumps(body), None, None, None, "generic"], ["di", 59],
                       ["af.httprm", 58, "-123456789", 4]])
    return f")]}}'\n\n{len(part)}\n{part}\n"

def paid_status(models=None):
    return batch_response(proto.RPC_USER_STATUS, status_body(
        models if models is not None else [model_entry(*m) for m in PICKER], tiers=[8], caps=[115, 19]))

def answer_frames(*texts, cid="c_fixture1", done_last=True):
    out = ")]}'\n\n"
    for i, text in enumerate(texts):
        cand = [None] * 38
        cand[0] = "rc_1"
        cand[1] = [text]
        if done_last and i == len(texts) - 1:
            cand[8] = [2]
        inner = [None, [cid, "r_1"], None, None, [cand]]
        body = json.dumps([["wrb.fr", None, json.dumps(inner)]])
        out += f"{len(body) + 1}\n{body}\n"
    return out

class FakeHeaders(dict):
    def get_list(self, key):
        value = self.get(key)
        return value if isinstance(value, list) else ([value] if value else [])

class FakeResp:
    def __init__(self, status=200, text="", url="https://gemini.google.com/app"):
        self.status_code, self.text, self.url = status, text, url
        self.headers = FakeHeaders()
        self._lines = text.split("\n")

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aclose(self):
        pass

class FakeSession:
    """Routes by URL: the /app page, batchexecute per rpcid, StreamGenerate answers in order."""

    def __init__(self, status_text, answers):
        self.status_text, self.answers, self.calls = status_text, list(answers), []
        self.cookies = SimpleNamespace(clear=lambda: None)

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return FakeResp(text=PAGE)

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        if "batchexecute" in url:
            if f"rpcids={proto.RPC_USER_STATUS}" in url:
                return FakeResp(text=self.status_text) if self.status_text is not None else FakeResp(status=500)
            return FakeResp(text=batch_response(proto.RPC_DELETE_CHAT, []))
        return FakeResp(text=self.answers.pop(0))

    async def close(self):
        pass

    def generate_calls(self):
        return [c for c in self.calls if c[0] == "POST" and "StreamGenerate" in c[1]]

    def batch_calls(self, rpcid):
        return [c for c in self.calls if c[0] == "POST" and f"rpcids={rpcid}" in c[1]]

def inner_of(call):
    form = urllib.parse.parse_qs(call[2]["data"].decode())
    return json.loads(json.loads(form["f.req"][0])[1])

def account(cookie=f"__Secure-1PSID={PSID}; __Secure-1PSIDTS={TS}", **fields):
    return SimpleNamespace(id="acc41", provider="geminiweb", fields={"cookie": cookie, **fields}, extra={},
                           proxy="", enabled=True)

def make_provider(session):
    provider = GeminiWebProvider()
    provider._ensure_rotator = lambda: None

    async def fake_session(_acc):
        return session
    provider._session = fake_session
    return provider

async def run_chat(provider, acc, model, params=None, prompt="Привет"):
    events = []
    with patch("app.providers.geminiweb.GeminiWebProvider._seed_catalog", staticmethod(lambda status: None)), \
            patch("app.providers.geminiweb.asyncio.sleep", return_value=None):
        async for event in provider.chat_stream(acc, model, [{"role": "user", "content": prompt}], params or {}):
            events.append(event)
    return events

def text_of(events):
    return "".join(e["text"] for e in events if e["type"] == "text")

class StaticListTest(unittest.TestCase):
    def test_static_list_is_the_app_picker(self):
        self.assertEqual(sorted(MODELS), ["gemini-3.1-pro", "gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-4-argon"])
        self.assertEqual({c["display"] for c in MODELS.values()}, {"3.5 Flash-Lite", "3.8 Flash", "3.1 Pro", "4 Argon"})
        for retired in ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-auto", "gemini-3.5-flash-thinking"):
            self.assertNotIn(retired, MODELS)
            self.assertIn(retired, LEGACY)
        self.assertNotIn("MODEL_IDS", dir(proto))

    def test_seed_and_offline_dynamic_models(self):
        provider = GeminiWebProvider()
        self.assertEqual([n for n, _ in provider.seed_models()], list(MODELS))

class UserStatusParseTest(unittest.TestCase):
    def test_paid_account_models_and_capacity(self):
        status = proto.parse_user_status(paid_status())
        self.assertTrue(status.found)
        self.assertEqual(status.status, 1000)
        self.assertTrue(status.paid)
        self.assertEqual([m.name for m in status.models],
                         ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.1-pro", "gemini-4-argon"])
        self.assertEqual([m.display for m in status.models], ["3.5 Flash-Lite", "3.8 Flash", "3.1 Pro", "4 Argon"])
        argon = status.models[3]
        self.assertEqual((argon.number, argon.capacity, argon.capacity_field), (7, 4, 12))
        self.assertTrue(all(m.available for m in status.models))

    def test_free_account_has_no_argon_and_capacity_one(self):
        text = batch_response(proto.RPC_USER_STATUS, status_body([model_entry(*m) for m in PICKER[:3]]))
        status = proto.parse_user_status(text)
        self.assertFalse(status.paid)
        self.assertEqual(status.models[1].capacity, 1)
        self.assertNotIn("gemini-4-argon", [m.name for m in status.models])

    def test_guest_only_default_model_available(self):
        text = batch_response(proto.RPC_USER_STATUS, status_body([model_entry(*m) for m in PICKER[1:3]], status=1016))
        status = proto.parse_user_status(text)
        self.assertEqual([m.available for m in status.models], [True, False])

    def test_sparse_fields_category_fallback_and_field13(self):
        entries = [model_entry("ffff000011112222", "3.8 Flash", "x", 1, sparse=True),
                   ["eeee000011112222", "Pro", "Old style description", None, None, None, None, None, None, 3]]
        status = proto.parse_user_status(batch_response(proto.RPC_USER_STATUS, status_body(entries, tiers=[22])))
        self.assertEqual(status.models[0].number, 1)
        self.assertEqual((status.models[1].name, status.models[1].number, status.models[1].description),
                         ("gemini-pro", 3, "Old style description"))
        header = json.loads(status.models[1].header("SID-1"))
        self.assertEqual(header[11:13], [None, 2])

    def test_other_rpc_and_garbage_are_ignored(self):
        self.assertFalse(proto.parse_user_status(batch_response("MaZiqc", status_body([]))).found)
        self.assertFalse(proto.parse_user_status(")]}'\n\nnot json").found)
        self.assertFalse(proto.parse_user_status("").found)

    def test_model_header_format(self):
        model = proto.parse_user_status(paid_status()).models[2]
        header = json.loads(model.header("CLIENT-UUID", extended=True))
        self.assertEqual(header[0], 1)
        self.assertEqual(header[4], "a1b2c3d4e5f60003")
        self.assertEqual(header[8], [4, 5, 6, 8])
        self.assertEqual(header[11], 4)
        self.assertEqual(header[14], 3)
        self.assertEqual(header[15:], [2, "CLIENT-UUID"])
        batch = json.loads(proto.batch_model_header("CLIENT-UUID"))
        self.assertIsNone(batch[4])
        self.assertEqual(batch[-1], "CLIENT-UUID")

    def test_batch_body(self):
        form = urllib.parse.parse_qs(proto.encode_batch(proto.RPC_DELETE_CHAT, json.dumps(["c_1"]), "AT").decode())
        self.assertEqual(json.loads(form["f.req"][0]), [[["GzXR5e", '["c_1"]', None, "generic"]]])
        self.assertEqual(form["at"], ["AT"])

class ResponseParseTest(unittest.TestCase):
    def test_candidate_without_rcid_is_skipped_and_card_content_unwrapped(self):
        stub = [None, ["Такие вопросы и задачи не для меня."]]
        real = [None] * 23
        real[0] = "rc_9"
        real[1] = ["http://googleusercontent.com/card_content/1"]
        real[22] = ["Привет! Чем могу помочь?"]
        part = ["wrb.fr", None, json.dumps([None, ["c_9", "r_9"], None, None, [stub, real]])]
        snap = proto.parse_part(part)
        self.assertEqual((snap.text, snap.cid), ("Привет! Чем могу помочь?", "c_9"))

    def test_canned_detection(self):
        for text in ("Такие вопросы и задачи не для меня. Я всего лишь языковая модель.",
                     "В моей программе нет таких возможностей. Я просто генерирую текст.",
                     "I'm a language model and don't have the capacity to help with that.",
                     "I’m just a language model, so I can’t help you with that."):
            self.assertTrue(proto.is_canned_reply(text), text)
        self.assertFalse(proto.is_canned_reply("Привет! Чем могу помочь?"))
        long = "Языковая модель — это… " + "x" * 500 + " Я всего лишь языковая модель."
        self.assertFalse(proto.is_canned_reply(long))

    def test_freq_compact_and_fields(self):
        inner = proto.build_freq("Привет", 3, 0, temporary=True, extended=False, request_uuid="U")
        self.assertEqual((inner[0][0], inner[1], inner[6], inner[17], inner[41], inner[45], inner[79], inner[80]),
                         ("Привет", ["en"], [1], [[0]], [1], 1, 3, 1))
        body = proto.encode_body(inner, "AT").decode()
        self.assertNotIn("%2C+", body)

class ProviderFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_header_for_each_model_from_account_list(self):
        for name, (model_id, display, _d, number) in zip(
                ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.1-pro", "gemini-4-argon"], PICKER):
            session = FakeSession(paid_status(), [answer_frames("Привет! Чем могу помочь?")])
            provider = make_provider(session)
            events = await run_chat(provider, account(delete_chats="false"), name)
            self.assertEqual(text_of(events), "Привет! Чем могу помочь?", name)
            call = session.generate_calls()[0]
            header = json.loads(call[2]["headers"][proto.MODEL_HEADER])
            self.assertEqual((header[4], header[11], header[14], header[15]), (model_id, 4, number, 1), display)
            self.assertEqual(header[16], provider._client_id(account()))
            self.assertEqual(call[2]["headers"]["x-goog-ext-73010990-jspb"], "[0,0,0]")
            self.assertEqual(inner_of(call)[79], number)
            status_call = session.batch_calls(proto.RPC_USER_STATUS)[0]
            self.assertIn("source-path=%2Fapp", status_call[1])
            self.assertIn(b"at=AT_tok", status_call[2]["data"])

    async def test_legacy_names_route_to_current_models(self):
        session = FakeSession(paid_status(), [answer_frames("ok")])
        await run_chat(make_provider(session), account(delete_chats="false"), "gemini-3.7-flash")
        self.assertIn("a1b2c3d4e5f60002", session.generate_calls()[0][2]["headers"][proto.MODEL_HEADER])

    async def test_unavailable_model_is_a_clear_error(self):
        free = batch_response(proto.RPC_USER_STATUS, status_body([model_entry(*m) for m in PICKER[:3]]))
        session = FakeSession(free, [])
        with self.assertRaises(ProviderModelUnavailableError) as ctx:
            await run_chat(make_provider(session), account(), "gemini-4-argon")
        self.assertIn("AI Pro/Ultra", ctx.exception.message_ru)
        self.assertIn("3.8 Flash", ctx.exception.message_ru)
        self.assertEqual(session.generate_calls(), [])

    async def test_static_fallback_without_model_list(self):
        session = FakeSession(None, [answer_frames("ok")])
        await run_chat(make_provider(session), account(delete_chats="false"), "gemini-3.1-pro")
        call = session.generate_calls()[0]
        self.assertNotIn(proto.MODEL_HEADER, call[2]["headers"])
        self.assertEqual(inner_of(call)[79], 3)
        with self.assertRaises(ProviderModelUnavailableError):
            await run_chat(make_provider(FakeSession(None, [])), account(), "gemini-4-argon")

    async def test_thinking_level_mapping(self):
        cases = [({"_reasoning_level": "low"}, "gemini-3.1-pro", [[4]], 1),
                 ({"effort": "medium"}, "gemini-3.1-pro", [[0]], 1),
                 ({"_reasoning_level": "high"}, "gemini-3.1-pro", [[0]], 2),
                 ({"effort": "xhigh"}, "gemini-3.1-pro", [[0]], 2),
                 ({}, "gemini-3.1-pro@effort=low", [[4]], 1),
                 ({"_reasoning_level": "low"}, "gemini-3.1-pro@effort=high", [[0]], 2),
                 ({}, "gemini-3.1-pro-thinking", [[0]], 2)]
        for params, model, think, ext in cases:
            session = FakeSession(paid_status(), [answer_frames("ok")])
            await run_chat(make_provider(session), account(delete_chats="false"), model, params)
            call = session.generate_calls()[0]
            inner = inner_of(call)
            self.assertEqual((inner[17], inner[80]), (think, ext), (params, model))
            self.assertEqual(json.loads(call[2]["headers"][proto.MODEL_HEADER])[15], ext)

    async def test_default_level_setting(self):
        settings = SimpleNamespace(provider_settings={"geminiweb": {"default_effort": "high"}},
                                   request_timeout=60, stream_idle_timeout=60)
        session = FakeSession(paid_status(), [answer_frames("ok")])
        with patch("app.providers.geminiweb.config_store", SimpleNamespace(settings=settings)):
            await run_chat(make_provider(session), account(delete_chats="false"), "gemini-3.8-flash")
        self.assertEqual(inner_of(session.generate_calls()[0])[80], 2)

    async def test_canned_stub_is_an_error_not_an_answer(self):
        stub = "Такие вопросы и задачи не для меня. Я всего лишь языковая модель."
        session = FakeSession(paid_status(), [answer_frames(stub), answer_frames(stub), answer_frames(stub)])
        with self.assertRaises(ProviderModelUnavailableError) as ctx:
            await run_chat(make_provider(session), account(delete_chats="false"), "gemini-3.1-pro")
        self.assertIn("заглушку", ctx.exception.message_ru)
        self.assertIn("Проверить подключение", ctx.exception.message_ru)
        self.assertEqual(len(session.batch_calls(proto.RPC_USER_STATUS)), 2)

    async def test_canned_stub_then_real_answer(self):
        session = FakeSession(paid_status(), [answer_frames("В моей программе нет таких возможностей. Я просто генерирую текст."),
                                              answer_frames("Привет! Чем могу помочь?")])
        events = await run_chat(make_provider(session), account(delete_chats="false"), "gemini-3.1-pro")
        self.assertEqual(text_of(events), "Привет! Чем могу помочь?")

    async def test_long_answer_streams_progressively(self):
        parts = ["А" * 100, "А" * 400, "А" * 900]
        session = FakeSession(paid_status(), [answer_frames(*parts)])
        events = await run_chat(make_provider(session), account(delete_chats="false"), "gemini-3.8-flash")
        self.assertEqual(text_of(events), "А" * 900)
        self.assertGreaterEqual(len([e for e in events if e["type"] == "text"]), 2)

    async def test_model_header_rejected_rereads_list(self):
        bad = ")]}'\n\n" + (lambda b: f"{len(b)}\n{b}\n")(json.dumps(
            [["wrb.fr", None, None, None, None, [3, None, [["type.googleapis.com/x", [1052]]]]]]))
        session = FakeSession(paid_status(), [bad, answer_frames("ok")])
        events = await run_chat(make_provider(session), account(delete_chats="false"), "gemini-3.1-pro")
        self.assertEqual(text_of(events), "ok")
        self.assertEqual(len(session.batch_calls(proto.RPC_USER_STATUS)), 2)

    async def test_proxy_chat_is_deleted_from_history(self):
        session = FakeSession(paid_status(), [answer_frames("ok", cid="c_proxychat")])
        provider = make_provider(session)
        await run_chat(provider, account(), "gemini-3.8-flash")
        for _ in range(5):
            await asyncio.sleep(0)
        deletes = session.batch_calls(proto.RPC_DELETE_CHAT)
        self.assertEqual(len(deletes), 1)
        self.assertIn("c_proxychat", urllib.parse.unquote_plus(deletes[0][2]["data"].decode()))
        session2 = FakeSession(paid_status(), [answer_frames("ok", cid="c_keep")])
        await run_chat(make_provider(session2), account(delete_chats="false"), "gemini-3.8-flash")
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(session2.batch_calls(proto.RPC_DELETE_CHAT), [])

    async def test_account_status_blocks(self):
        from app.providers.base import ProviderAuthError, ProviderServerError
        expired = batch_response(proto.RPC_USER_STATUS, status_body([model_entry(*PICKER[1])], status=1016))
        with self.assertRaises(ProviderAuthError):
            await run_chat(make_provider(FakeSession(expired, [])), account(), "gemini-3.8-flash")
        region = batch_response(proto.RPC_USER_STATUS, status_body([], status=1060))
        with self.assertRaisesRegex(ProviderServerError, "1060"):
            await run_chat(make_provider(FakeSession(region, [])), account(), "gemini-3.8-flash")

    async def test_dynamic_models_catalog_and_diagnosis(self):
        free = batch_response(proto.RPC_USER_STATUS, status_body([model_entry(*m) for m in PICKER[:3]]))
        session = FakeSession(free, [answer_frames("Привет! Чем могу помочь?")])
        provider = make_provider(session)
        acc = account(delete_chats="false")
        with patch("app.providers.geminiweb.GeminiWebProvider._seed_catalog", staticmethod(lambda status: None)):
            names = await provider.dynamic_models(acc)
            self.assertEqual(names, ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.1-pro"])
            self.assertEqual(acc.extra["gw_models"]["models"][1]["display"], "3.8 Flash")
            with patch.object(GeminiWebProvider, "_accounts", staticmethod(lambda: [acc])):
                self.assertTrue(provider.catalog_hidden("gemini-4-argon"))
                self.assertTrue(provider.catalog_hidden("gemini-3.7-flash"))
                self.assertFalse(provider.catalog_hidden("gemini-3.1-pro"))
                self.assertEqual(provider.model_note("gemini-3.1-pro"), "3.1 Pro · Advanced math and code")
            result = await provider.diagnose(acc)
        self.assertTrue(result["ok"], result)
        self.assertEqual([s["id"] for s in result["steps"]], ["cookies", "page", "token", "models", "limits", "request"])
        self.assertEqual([m["display"] for m in result["models"]], ["3.5 Flash-Lite", "3.8 Flash", "3.1 Pro"])
        self.assertIn("3.1 Pro", result["steps"][3]["detail_ru"])

    async def test_persisted_list_survives_restart(self):
        acc = account()
        acc.extra["gw_models"] = {"ts": 0, "status": 1000, "tier_flags": [8], "capability_flags": [115],
                                  "models": [m.to_dict() for m in proto.parse_user_status(paid_status()).models]}
        provider = GeminiWebProvider()
        self.assertEqual([m.display for m in provider._known_status(acc).models][-1], "4 Argon")

class ReasoningProfileTest(unittest.TestCase):
    def test_gemini_web_levels(self):
        from app import reasoning_levels
        prof = reasoning_levels.profile("geminiweb/gemini-3.1-pro", "geminiweb", "gemini-3.1-pro")
        self.assertEqual((prof["kind"], prof["levels"], prof["default"]), ("levels", ["low", "medium", "high"], "medium"))
        self.assertEqual(reasoning_levels.clamp("max", prof), "high")

if __name__ == "__main__":
    unittest.main()
