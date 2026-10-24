"""Round 37 (r37-gemini): Gemini Web — cookie import of every format, frame parser, stream, rotation. No network."""
import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.providers import geminiweb_proto as proto
from app.providers.geminiweb import GeminiWebProvider, _set_cookies
from app.providers.geminiweb_cookies import cookies_from_webview, parse_cookie_import

PSID = "g.a000fixturePSIDvalue-abcdefghijklmnopqrstuvwxyz0123456789"
TS = "sidts-CjEBfixtureTSvalue1234567890"


def codes(result):
    return [e["code"] for e in result.errors], [w["code"] for w in result.warnings]


class CookieImportTest(unittest.TestCase):
    def check_ok(self, raw, fmt):
        result = parse_cookie_import(raw)
        self.assertTrue(result.ok, (result.errors, result.format))
        self.assertEqual(result.format, fmt)
        self.assertEqual(result.cookies["__Secure-1PSID"], PSID)
        self.assertEqual(result.cookies["__Secure-1PSIDTS"], TS)
        return result

    def test_header_and_curl(self):
        header = f"SID=a; __Secure-1PSID={PSID}; __Secure-1PSIDTS={TS}; NID=x"
        self.check_ok(header, "header")
        self.check_ok("Cookie: " + header, "header")
        self.check_ok(f"curl 'https://gemini.google.com/app' -H 'cookie: {header}' --compressed", "header")

    def test_devtools_application_table(self):
        rows = [
            "Name\tValue\tDomain\tPath\tExpires / Max-Age\tSize\tHttpOnly\tSecure",
            f"__Secure-1PSID\t{PSID}\t.google.com\t/\t2027-11-01T10:00:00.000Z\t70\t✓\t✓",
            f"__Secure-1PSIDTS\t{TS}\t.google.com\t/\t2027-11-01T10:00:00.000Z\t40\t✓\t✓",
            "_ga\tGA1.1\t.youtube.com\t/\tSession\t10\t\t",
        ]
        result = self.check_ok("\n".join(rows), "devtools-table")
        self.assertNotIn("_ga", result.cookies)

    def test_cookie_editor_json_and_dict(self):
        data = [{"domain": ".google.com", "name": "__Secure-1PSID", "value": PSID, "expirationDate": time.time() + 9e6},
                {"domain": ".google.com", "name": "__Secure-1PSIDTS", "value": TS, "expirationDate": time.time() + 9e6},
                {"domain": "accounts.google.com", "name": "LSID", "value": "zzz"}]
        result = self.check_ok(json.dumps(data), "json")
        self.assertNotIn("LSID", result.cookies)
        self.check_ok(json.dumps({"cookies": data}), "json")
        self.check_ok(json.dumps({"__Secure-1PSID": PSID, "__Secure-1PSIDTS": TS}), "json")

    def test_netscape(self):
        text = ("# Netscape HTTP Cookie File\n"
                f"#HttpOnly_.google.com\tTRUE\t/\tTRUE\t1893456000\t__Secure-1PSID\t{PSID}\n"
                f".google.com\tTRUE\t/\tTRUE\t1893456000\t__Secure-1PSIDTS\t{TS}\n")
        self.check_ok(text, "netscape")

    def test_two_values(self):
        self.check_ok(f"__Secure-1PSID\t{PSID}\n__Secure-1PSIDTS\t{TS}", "pairs")
        self.check_ok(f"__Secure-1PSID: {PSID}\n__Secure-1PSIDTS = {TS}", "pairs")
        self.check_ok(f"{PSID}\n{TS}\n", "pairs")

    def test_clear_errors(self):
        self.assertEqual(codes(parse_cookie_import(""))[0], ["empty"])
        self.assertIn("no_1psid", codes(parse_cookie_import("SID=a; HSID=b"))[0])
        self.assertIn("no_1psidts", codes(parse_cookie_import(f"__Secure-1PSID={PSID}"))[1])
        wrong = json.dumps([{"domain": "accounts.google.com", "name": "__Secure-1PSID", "value": PSID}])
        result = parse_cookie_import(wrong)
        self.assertEqual(codes(result)[0], ["wrong_domain"])
        self.assertIn(".google.com", result.errors[0]["ru"])
        expired = json.dumps([{"domain": ".google.com", "name": "__Secure-1PSID", "value": PSID, "expirationDate": 1000}])
        self.assertIn("expired_local", codes(parse_cookie_import(expired))[0])
        self.assertIn("truncated", codes(parse_cookie_import("__Secure-1PSID=g.a000abc…"))[0])
        self.assertEqual(codes(parse_cookie_import("просто текст без кук"))[0], ["unknown_format"])

    def test_webview_cookies(self):
        from http.cookies import SimpleCookie
        jar = SimpleCookie()
        jar["__Secure-1PSID"] = PSID
        jar["__Secure-1PSID"]["domain"] = ".google.com"
        other = SimpleCookie()
        other["VISITOR"] = "x"
        other["VISITOR"]["domain"] = ".youtube.com"
        self.assertEqual(cookies_from_webview([jar, other]), {"__Secure-1PSID": PSID})


def wrb(inner):
    return ["wrb.fr", None, json.dumps(inner)]


def candidate(text, thoughts="", done=False, image=""):
    cand = [None] * 38
    cand[0] = "rc_1"
    cand[1] = [text]
    if done:
        cand[8] = [2]
    if thoughts:
        cand[37] = [[thoughts]]
    if image:
        rich = [None] * 8
        rich[7] = [[[[None, None, None, [None, None, None, image]]]]]
        cand[12] = rich
    return [None, ["c_1", "r_1"], None, None, [cand]]


def frames(*parts):
    out = ")]}'\n\n"
    for part in parts:
        body = json.dumps([part])
        out += f"{len(body) + 1}\n{body}\n"
    return out


class FrameParserTest(unittest.TestCase):
    def test_length_prefixed_frames_split_anywhere(self):
        raw = frames(wrb(candidate("Привет")), wrb(candidate("Привет, мир", done=True)), ["di", 120])
        for step in (1, 7, 50, len(raw)):
            parser = proto.FrameParser()
            got = []
            for i in range(0, len(raw), step):
                got += parser.feed(raw[i:i + step])
            got += parser.flush()
            texts = [proto.parse_part(p).text for f in got for p in proto.iter_parts(f)]
            self.assertEqual(texts, ["Привет", "Привет, мир"], step)

    def test_legacy_line_format_and_error_code(self):
        line = json.dumps([wrb(candidate("old"))])
        parser = proto.FrameParser()
        parsed = parser.feed(")]}'\n" + line + "\n")
        self.assertEqual(proto.parse_part(next(proto.iter_parts(parsed[0]))).text, "old")
        err = ["wrb.fr", None, None, None, None, [3, None, [["type.googleapis.com/x", [1037]]]]]
        self.assertEqual(proto.error_code(err), 1037)

    def test_page_tokens(self):
        html = '"SNlM0e":"AT_tok","cfb2h":"boq_gemini-web-uiserver_20261012.15_p0","FdrFJe":"-123","qKIAYe":"feeds/x"'
        info = proto.parse_page(200, "https://gemini.google.com/app", html)
        self.assertTrue(info.signed_in)
        self.assertEqual((info.at, info.bl, info.fsid, info.push_id),
                         ("AT_tok", "boq_gemini-web-uiserver_20261012.15_p0", "-123", "feeds/x"))
        login = proto.parse_page(200, "https://accounts.google.com/ServiceLogin?continue=x", "")
        self.assertTrue(login.login_page)

    def test_freq_body(self):
        inner = proto.build_freq("hi", 3, 0, temporary=True, extended=True, file_refs=[("/contrib/x", "a.png")])
        self.assertEqual((inner[79], inner[80], inner[45], inner[17]), (3, 2, 1, [[0]]))
        self.assertEqual(inner[0][3], [[["/contrib/x"], "a.png"]])
        self.assertIn(b"at=TOK", proto.encode_body(inner, "TOK"))


class FakeResp:
    def __init__(self, status=200, text="", url="https://gemini.google.com/app", headers=None, lines=None):
        self.status_code, self.text, self.url = status, text, url
        self.headers = FakeHeaders(headers or {})
        self._lines = lines or text.split("\n")

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aclose(self):
        pass


class FakeHeaders(dict):
    def get_list(self, key):
        value = self.get(key)
        return value if isinstance(value, list) else ([value] if value else [])


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []
        self.cookies = SimpleNamespace(clear=lambda: None)

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self.routes["GET"](url, kw)

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self.routes["POST"](url, kw)

    async def close(self):
        pass


PAGE = '"SNlM0e":"AT_tok","cfb2h":"boq_gemini-web-uiserver_20261012.15_p0","FdrFJe":"-55","oPEP7c":"me@example.com"'


def account(cookie=f"__Secure-1PSID={PSID}; __Secure-1PSIDTS={TS}"):
    return SimpleNamespace(id="acc1", provider="geminiweb", fields={"cookie": cookie}, extra={}, proxy="", enabled=True)


class ProviderStreamTest(unittest.IsolatedAsyncioTestCase):
    async def run_chat(self, session, acc, model="gemini-3.7-flash"):
        provider = GeminiWebProvider()
        provider._ensure_rotator = lambda: None

        async def fake_session(_acc):
            return session
        provider._session = fake_session
        events = []
        with patch("app.providers.geminiweb.GeminiWebProvider._store_cookie",
                   staticmethod(lambda a, h: a.fields.__setitem__("cookie", h))):
            async for event in provider.chat_stream(acc, model, [{"role": "user", "content": "hi"}], {}):
                events.append(event)
        return events

    async def test_stream_text_thoughts_images_and_request_shape(self):
        body = frames(wrb(candidate("Hel", thoughts="Think")), wrb(candidate("Hello", thoughts="Thinking", done=True,
                                                                          image="https://lh3.googleusercontent.com/gg/img")))
        session = FakeSession({"GET": lambda u, kw: FakeResp(text=PAGE),
                               "POST": lambda u, kw: FakeResp(text=body)})
        events = await self.run_chat(session, account(), "gemini-3.1-pro")
        text = "".join(e["text"] for e in events if e["type"] == "text")
        reasoning = "".join(e["text"] for e in events if e["type"] == "reasoning")
        self.assertTrue(text.startswith("Hello"))
        self.assertIn("![Gemini image 1](https://lh3.googleusercontent.com/gg/img)", text)
        self.assertEqual(reasoning, "Thinking")
        # r41b: после ответа Proxy пробует скачать сгенерированную картинку (GET) — берём сам запрос StreamGenerate
        method, url, kw = next(c for c in reversed(session.calls) if "StreamGenerate" in c[1])
        self.assertIn("bl=boq_gemini-web-uiserver_20261012.15_p0", url)
        self.assertIn("f.sid=-55", url)
        self.assertIn(b"at=AT_tok", kw["data"])
        self.assertEqual(kw["headers"]["X-Same-Domain"], "1")
        # r41: без списка моделей аккаунта заголовок выбора модели не шлётся (выбор — MODE в индексе 79)
        self.assertNotIn(proto.MODEL_HEADER, kw["headers"])
        self.assertIn("%2C3%2C1%5D", kw["data"].decode())
        self.assertIn("__Secure-1PSID=", kw["headers"]["Cookie"])

    async def test_model_header_rejected_retries_without_it(self):
        bad = frames(["wrb.fr", None, None, None, None, [3, None, [["type.googleapis.com/x", [1052]]]]])
        good = frames(wrb(candidate("ok", done=True)))
        answers = [bad, good]
        session = FakeSession({"GET": lambda u, kw: FakeResp(text=PAGE),
                               "POST": lambda u, kw: FakeResp(text=answers.pop(0))})
        acc = account()
        with patch("app.providers.geminiweb.asyncio.sleep", return_value=None):
            events = await self.run_chat(session, acc)
        self.assertEqual("".join(e["text"] for e in events), "ok")
        self.assertNotIn("no_model_header", acc.extra)    # r41: флаг раунда 37 больше не ставится

    async def test_expired_cookie_and_usage_limit(self):
        from app.providers.base import ProviderAuthError, ProviderRateLimitError
        session = FakeSession({"GET": lambda u, kw: FakeResp(url="https://accounts.google.com/ServiceLogin?x=1", text=""),
                               "POST": lambda u, kw: FakeResp()})
        with self.assertRaisesRegex(ProviderAuthError, "страницу входа"):
            await self.run_chat(session, account())
        limit = frames(["wrb.fr", None, None, None, None, [3, None, [["type.googleapis.com/x", [1037]]]]])
        session = FakeSession({"GET": lambda u, kw: FakeResp(text=PAGE), "POST": lambda u, kw: FakeResp(text=limit)})
        with self.assertRaisesRegex(ProviderRateLimitError, "1037"):
            await self.run_chat(session, account())

    async def test_guest_canned_text_then_1096_yields_nothing(self):
        from app.providers.base import ProviderRateLimitError
        body = (frames(wrb(candidate("I'm having a hard time fulfilling your request.")))
                + frames(["wrb.fr", None, None, None, None, [3, None, [["type.googleapis.com/x", [1096]]]]])[6:])
        session = FakeSession({"GET": lambda u, kw: FakeResp(text='"cfb2h":"boq_x_20261012.15_p0"'),
                               "POST": lambda u, kw: FakeResp(text=body)})
        events = []
        with self.assertRaisesRegex(ProviderRateLimitError, "1096"):
            provider = GeminiWebProvider()

            async def fake_session(_acc):
                return session
            provider._session = fake_session
            async for event in provider.chat_stream(account(""), "gemini-3.7-flash", [{"role": "user", "content": "hi"}], {}):
                events.append(event)
        self.assertEqual(events, [])

    async def test_rotation_saves_new_psidts(self):
        new_ts = "sidts-NEWvalue0987654321"
        session = FakeSession({"POST": lambda u, kw: FakeResp(headers={"set-cookie": [
            f"__Secure-1PSIDTS={new_ts}; Domain=.google.com; Path=/; Secure; HttpOnly",
            "OTHER=1; Domain=.youtube.com"]})})
        provider = GeminiWebProvider()

        async def fake_session(_acc):
            return session
        provider._session = fake_session
        acc = account(f"SID=a; __Secure-1PSID={PSID}; __Secure-1PSIDTS={TS}")
        with patch("app.providers.geminiweb.GeminiWebProvider._store_cookie",
                   staticmethod(lambda a, h: a.fields.__setitem__("cookie", h))):
            self.assertEqual(await provider.rotate_cookies(acc, force=True), new_ts)
        method, url, kw = session.calls[0]
        self.assertEqual(url, proto.ROTATE_URL)
        self.assertEqual(kw["headers"]["Cookie"], f"__Secure-1PSID={PSID}; __Secure-1PSIDTS={TS}")
        self.assertIn(f"__Secure-1PSIDTS={new_ts}", acc.fields["cookie"])
        self.assertIn("SID=a", acc.fields["cookie"])

    async def test_diagnosis_steps(self):
        provider = GeminiWebProvider()
        session = FakeSession({"GET": lambda u, kw: FakeResp(text=PAGE),
                               "POST": lambda u, kw: FakeResp(text=frames(wrb(candidate("OK", done=True))))})

        async def fake_session(_acc):
            return session
        provider._session = fake_session
        provider._ensure_rotator = lambda: None
        result = await provider.diagnose(account())
        self.assertTrue(result["ok"], result)
        self.assertEqual([s["id"] for s in result["steps"]], ["cookies", "page", "token", "models", "limits", "request"])
        self.assertEqual(result["email"], "me@example.com")
        bad = await provider.diagnose(account("SID=only"))
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["steps"][0]["status"], "fail")
        self.assertTrue(bad["steps"][0]["fix_ru"])

    def test_set_cookie_filter(self):
        resp = FakeResp(headers={"set-cookie": ["A=1; Domain=.google.com", "B=2; Domain=.youtube.com", "C=; Max-Age=0"]})
        self.assertEqual(_set_cookies(resp), {"A": "1"})


if __name__ == "__main__":
    unittest.main()
