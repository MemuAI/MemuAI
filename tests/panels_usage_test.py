"""Round22 «Usage» panel (app/panels_usage.py): local credential discovery for Claude Code / Claude Desktop /
Codex / Antigravity (temp HOME, no real files touched), the usage endpoints with a mocked HTTP layer,
5-hour/weekly windows, plans, rate-limit cooldowns (Retry-After), honest walls, multiple named accounts,
the secrets store (never returned, never logged) and the pure-Python AES-GCM used for Claude Desktop."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import panels, panels_usage as pu, pgdata

NOW = time.time()
KEY = bytes.fromhex("00070e151c232a31383f464d545b626970777e858c939aa1a8afb6bdc4cbd2d9")

CACHE = ("djEwAQIDBAUGBwgJCgsMvIMwk3t0ruwrNW676rrTrwNErjHgM3oyuP64ZHqkrN/msnbAgd6WRThnKFhMkPI3Qpz92bIyZEjLeRteXwzen"
         "yUsJ8lo6Jrsm81DGdN+Y4gIT5mjQVFUjBnlK5v6NBtxbvYFM9uL7194RKslaP0bcPXervV80JRLOkcj6kj3tkCNnYLHAbpdGFbWrMOsJFWSI"
         "JbXskNodSh+y8Foe2fklIw9Zda4Q1udSg==")

def jwt(payload):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc(payload)}.sig"

class FakeHTTP:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __call__(self, method, url, headers, body=None, **kw):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "kw": kw})
        for key, value in self.routes.items():
            if key in url:
                if callable(value):
                    value = value(headers)
                if isinstance(value, Exception):
                    raise value
                status, resp_headers, payload = value
                return status, resp_headers, payload if isinstance(payload, str) else json.dumps(payload)
        return 404, {}, "{}"

CLAUDE_USAGE = {"five_hour": {"utilization": 62.0, "resets_at": "2099-01-01T05:00:00+00:00"},
                "seven_day": {"utilization": 31.5, "resets_at": "2099-01-05T00:00:00Z"},
                "seven_day_opus": {"utilization": None, "resets_at": None}, "seven_day_oauth_apps": None,
                "extra_usage": {"is_enabled": False}}
CODEX_USAGE = {"plan_type": "plus", "rate_limit": {
    "primary_window": {"used_percent": 12, "limit_window_seconds": 18000, "reset_after_seconds": 3600},
    "secondary_window": {"used_percent": 48.5, "limit_window_seconds": 604800, "reset_at": int(NOW + 3 * 86400)}}}
AG_SUMMARY = {"response": {"groups": [
    {"displayName": "Gemini Models", "buckets": [
        {"window": "weekly", "remainingFraction": 0.7, "resetTime": "2099-01-05T00:00:00Z"},
        {"window": "5h", "remainingFraction": 0.92, "resetTime": "2099-01-01T05:00:00Z"}]},
    {"displayName": "Claude and GPT models", "buckets": [
        {"window": "weekly", "resetTime": "2099-01-03T00:00:00Z"}]}]}}
AG_STATUS = {"userStatus": {"userTier": {"name": "Pro"}}}

class UsageBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.env = patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home),
                                           "APPDATA": str(self.home / "AppData" / "Roaming"),
                                           "LOCALAPPDATA": str(self.home / "AppData" / "Local")}, clear=False)
        self.env.start()
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
            os.environ.pop(key, None)
        self.data = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name) / "memu")
        self.data.start()
        self.no_procs = patch.object(pu, "_processes", lambda: [])
        self.no_procs.start()
        pu._AG_LAST.clear()

    def tearDown(self):
        self.no_procs.stop()
        self.data.stop()
        self.env.stop()
        self.tmp.cleanup()

    def write(self, rel, data):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), "utf-8")
        return path

    def run_acc(self, accounts, previous=None, http=None, force=True, now=None):
        with patch.object(pu, "HTTP", http or FakeHTTP({})):
            return asyncio.run(pu.refresh_accounts(accounts, previous or [], lang="ru", force=force, now=now))

class CryptoTest(unittest.TestCase):
    def test_aes_256_known_answer(self):

        rk = pu._expand_key(bytes(range(32)))
        self.assertEqual(pu._aes_encrypt_block(rk, bytes.fromhex("00112233445566778899aabbccddeeff")).hex(),
                         "8ea2b7ca516745bfeafc49904b496089")

    def test_chromium_token_cache_decrypts_and_tamper_is_rejected(self):
        data = pu.decrypt_token_cache(KEY, CACHE)
        entry = data["user:profile user:inference"]
        self.assertEqual(entry["token"], "sk-ant-oat01-TEST")
        raw = bytearray(base64.b64decode(CACHE))
        raw[-1] ^= 1
        with self.assertRaises(ValueError):
            pu.decrypt_token_cache(KEY, base64.b64encode(bytes(raw)).decode())

    def test_scrub_never_leaks_tokens(self):
        text = pu.scrub("Authorization: Bearer abc.def-123 sk-ant-oat01-SECRETSECRET " + jwt({"a": 1}) + " csrf_token=1234-5678-90")
        self.assertNotIn("SECRET", text)
        self.assertNotIn("abc.def-123", text)
        self.assertNotIn("1234-5678", text)

class ClaudeTest(UsageBase):
    def test_claude_code_credentials_and_windows(self):
        self.write(".claude/.credentials.json", {"claudeAiOauth": {"accessToken": "sk-ant-oat01-AAA", "expiresAt": (NOW + 3600) * 1000,
                                                                  "subscriptionType": "max", "rateLimitTier": "default_claude_max_20x",
                                                                  "scopes": ["user:inference", "user:profile"]}})
        http = FakeHTTP({"api.anthropic.com/api/oauth/usage": (200, {}, CLAUDE_USAGE)})
        out = self.run_acc([{"id": "a_000001", "provider": "claude", "name": "Работа", "mode": "auto"}], http=http)
        acc = out["accounts"][0]
        self.assertEqual(acc["state"], "ok")
        self.assertEqual(acc["plan"], "Max 20×")
        self.assertEqual(acc["name"], "Работа")
        self.assertEqual([l["key"] for l in acc["limits"]], ["five_hour", "seven_day", "seven_day_opus"])
        five = acc["limits"][0]
        self.assertEqual(five["used"], 62.0)
        self.assertEqual(five["window"], 5 * 3600)
        self.assertIsNone(five["start"], "a reset further away than the window has no honest start")
        self.assertEqual(out["top_pct"], 62)
        call = http.calls[0]
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-ant-oat01-AAA")
        self.assertEqual(call["headers"]["anthropic-beta"], "oauth-2025-04-20")
        self.assertNotIn("sk-ant", json.dumps(out), "the token never reaches panel data")

    def test_custom_config_dir_is_a_second_account(self):
        self.write(".claude/.credentials.json", {"claudeAiOauth": {"accessToken": "sk-ant-oat01-HOME"}})
        self.write("work-claude/.credentials.json", {"claudeAiOauth": {"accessToken": "sk-ant-oat01-WORK", "subscriptionType": "pro"}})
        http = FakeHTTP({"anthropic.com": (200, {}, CLAUDE_USAGE)})
        out = self.run_acc([{"id": "a_000001", "provider": "claude", "name": "Дом", "mode": "cli"},
                            {"id": "a_000002", "provider": "claude", "name": "Работа", "mode": "dir", "dir": str(self.home / "work-claude")}],
                           http=http)
        self.assertEqual([c["headers"]["Authorization"][-4:] for c in http.calls], ["HOME", "WORK"])
        self.assertEqual(out["accounts"][1]["plan"], "Pro")

    def test_expired_missing_and_rejected(self):
        out = self.run_acc([{"id": "a_000001", "provider": "claude", "mode": "auto"}])
        self.assertEqual(out["accounts"][0]["state"], "wall")
        self.assertEqual(out["accounts"][0]["wall"]["status"], "needs_login:claude")
        self.write(".claude/.credentials.json", {"claudeAiOauth": {"accessToken": "sk-ant-oat01-OLD", "expiresAt": (NOW - 60) * 1000}})
        out = self.run_acc([{"id": "a_000001", "provider": "claude", "mode": "auto"}])
        self.assertIn("истёк", out["accounts"][0]["wall"]["message"])
        self.write(".claude/.credentials.json", {"claudeAiOauth": {"accessToken": "sk-ant-oat01-BAD"}})
        out = self.run_acc([{"id": "a_000001", "provider": "claude", "mode": "auto"}],
                           http=FakeHTTP({"anthropic.com": (401, {}, "{}")}))
        self.assertEqual(out["accounts"][0]["wall"]["status"], "needs_login:claude")

    def test_rate_limit_keeps_last_numbers_and_cools_down(self):
        self.write(".claude/.credentials.json", {"claudeAiOauth": {"accessToken": "sk-ant-oat01-AAA"}})
        ok = self.run_acc([{"id": "a_000001", "provider": "claude", "mode": "auto"}],
                          http=FakeHTTP({"anthropic.com": (200, {}, CLAUDE_USAGE)}))
        http = FakeHTTP({"anthropic.com": (429, {"retry-after": "2708"}, "{}")})
        out = self.run_acc([{"id": "a_000001", "provider": "claude", "mode": "auto"}], previous=ok["accounts"], http=http, now=NOW)
        acc = out["accounts"][0]
        self.assertEqual(acc["state"], "stale")
        self.assertEqual(acc["limits"], ok["accounts"][0]["limits"])
        self.assertAlmostEqual(acc["cooldown_until"], NOW + 2708, delta=2)
        again = self.run_acc([{"id": "a_000001", "provider": "claude", "mode": "auto"}], previous=out["accounts"], http=http, now=NOW + 60)
        self.assertEqual(len(http.calls), 1, "no request during the cooldown, even when forced")
        self.assertEqual(again["accounts"][0]["state"], "stale")

    def test_retry_after_edge_cases(self):
        self.assertEqual(pu.retry_after({"retry-after": "0"}), pu.COOLDOWN_DEFAULT)
        self.assertEqual(pu.retry_after({}), pu.COOLDOWN_DEFAULT)
        self.assertEqual(pu.retry_after({"retry-after": "5"}), pu.COOLDOWN_MIN)
        self.assertEqual(pu.retry_after({"retry-after": "999999"}), pu.COOLDOWN_MAX)

    def test_spacing_without_force(self):
        self.write(".claude/.credentials.json", {"claudeAiOauth": {"accessToken": "sk-ant-oat01-AAA"}})
        http = FakeHTTP({"anthropic.com": (200, {}, CLAUDE_USAGE)})
        first = self.run_acc([{"id": "a_000001", "provider": "claude", "mode": "auto"}], http=http, now=NOW)
        self.run_acc([{"id": "a_000001", "provider": "claude", "mode": "auto"}], previous=first["accounts"], http=http,
                     force=False, now=NOW + 10)
        self.assertEqual(len(http.calls), 1)

    def test_desktop_token_cache_with_injected_key(self):
        desk = self.home / "AppData" / "Roaming" / "Claude"
        desk.mkdir(parents=True)
        (desk / "Local State").write_text(json.dumps({"os_crypt": {"encrypted_key": base64.b64encode(b"DPAPIxx").decode()}}))
        (desk / "config.json").write_text(json.dumps({"oauth:tokenCacheV2": CACHE}))
        tokens = pu.claude_desktop_tokens(key_fn=lambda _ls: KEY, dirs=[desk])
        self.assertEqual(tokens[0]["token"], "sk-ant-oat01-TEST")
        self.assertEqual(pu.claude_plan(tokens[0]["plan"], tokens[0]["tier"]), "Max 20×")

class CodexTest(UsageBase):
    def test_auth_json_usage_and_plan(self):
        token = jwt({"exp": NOW + 3600, "https://api.openai.com/auth": {"chatgpt_plan_type": "plus", "chatgpt_account_id": "acc-1"}})
        self.write(".codex/auth.json", {"OPENAI_API_KEY": None, "tokens": {"access_token": token, "account_id": "acc-1"}})
        http = FakeHTTP({"backend-api/codex/usage": (200, {}, CODEX_USAGE)})
        out = self.run_acc([{"id": "a_000003", "provider": "codex", "name": "Личный", "mode": "auto"}], http=http)
        acc = out["accounts"][0]
        self.assertEqual(acc["state"], "ok")
        self.assertEqual(acc["plan"], "Plus")
        self.assertEqual([l["title"] for l in acc["limits"]], ["5 ч", "Неделя"])
        self.assertEqual(acc["limits"][0]["used"], 12)
        self.assertIsNotNone(acc["limits"][1]["start"])
        self.assertEqual(http.calls[0]["headers"]["chatgpt-account-id"], "acc-1")
        self.assertTrue(http.calls[0]["kw"].get("browser"), "chatgpt.com goes through the browser-like client")

    def test_fallback_endpoint_api_key_only_and_expired(self):
        token = jwt({"exp": NOW + 3600})
        self.write(".codex/auth.json", {"tokens": {"access_token": token}})
        http = FakeHTTP({"codex/usage": (404, {}, "{}"), "wham/usage": (200, {}, CODEX_USAGE)})
        out = self.run_acc([{"id": "a_000003", "provider": "codex", "mode": "auto"}], http=http)
        self.assertEqual(out["accounts"][0]["state"], "ok")
        self.write(".codex/auth.json", {"OPENAI_API_KEY": "sk-proj-x", "tokens": None})
        out = self.run_acc([{"id": "a_000003", "provider": "codex", "mode": "auto"}])
        self.assertIn("API-ключу", out["accounts"][0]["wall"]["message"])
        self.write(".codex/auth.json", {"tokens": {"access_token": jwt({"exp": NOW - 5})}})
        out = self.run_acc([{"id": "a_000003", "provider": "codex", "mode": "auto"}])
        self.assertIn("устарел", out["accounts"][0]["wall"]["message"])

    def test_pasted_token_is_stored_protected_and_never_returned(self):
        token = jwt({"exp": NOW + 3600, "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}})
        with self.assertLogs("memuai.panels.usage", level="INFO") as logs:
            pu.put_secret("a_000009", token)
            logging.getLogger("memuai.panels.usage").info("probe")
            http = FakeHTTP({"codex/usage": (200, {}, CODEX_USAGE)})
            out = self.run_acc([{"id": "a_000009", "provider": "codex", "mode": "token"}], http=http)
        self.assertEqual(out["accounts"][0]["state"], "ok")
        self.assertNotIn(token, " ".join(logs.output))
        raw = pu._secrets_path().read_text("utf-8")
        self.assertNotIn(token, raw, "not stored as plain text")
        self.assertTrue(pu.secret_info("a_000009")["stored"])
        self.assertNotIn("token", pu.secret_info("a_000009"))
        pu.drop_secret("a_000009")
        self.assertFalse(pu.secret_info("a_000009")["stored"])
        with self.assertRaises(ValueError):
            pu.put_secret("a_000009", "short")
        with self.assertRaises(ValueError):
            pu.put_secret("../x", token)

class AntigravityTest(UsageBase):
    def test_daemon_file_and_quota_summary(self):
        self.write(".gemini/antigravity/daemon/ls_1.json", {"httpsPort": 51234, "csrfToken": "abcd-1234-efgh"})
        http = FakeHTTP({"GetUserStatus": (200, {}, AG_STATUS), "RetrieveUserQuotaSummary": (200, {}, AG_SUMMARY)})
        out = self.run_acc([{"id": "a_000004", "provider": "antigravity", "mode": "auto"}], http=http)
        acc = out["accounts"][0]
        self.assertEqual(acc["state"], "ok")
        self.assertEqual(acc["plan"], "Pro")
        self.assertEqual([l["title"] for l in acc["limits"]], ["Gemini · 5 ч", "Gemini · Неделя", "Claude / GPT · Неделя"])
        self.assertEqual(acc["limits"][2]["used"], 100.0, "a missing fraction is an exhausted pool")
        call = http.calls[0]
        self.assertEqual(call["url"], "https://127.0.0.1:51234/exa.language_server_pb.LanguageServerService/GetUserStatus")
        self.assertEqual(call["headers"]["X-Codeium-Csrf-Token"], "abcd-1234-efgh")
        self.assertFalse(call["kw"]["verify"], "self-signed local cert, 127.0.0.1 only")

    def test_not_running_is_an_honest_wall(self):
        out = self.run_acc([{"id": "a_000004", "provider": "antigravity", "mode": "auto"}])
        self.assertEqual(out["accounts"][0]["wall"]["status"], "source_gone:antigravity")
        self.assertIn("не запущен", out["accounts"][0]["wall"]["message"])

    def test_legacy_status_only_server(self):
        self.write(".gemini/x/daemon/ls.json", {"httpsPort": 50000, "csrfToken": "zzzz-zzzz"})
        status = {"userStatus": {"cascadeModelConfigData": {"clientModelConfigs": [
            {"label": "Gemini 3 Pro", "quotaInfo": {"remainingFraction": 0.5, "resetTime": "2099-01-01T00:00:00Z"}},
            {"label": "Claude Opus 4.6", "quotaInfo": {"resetTime": "2099-01-01T00:00:00Z"}}]}}}
        http = FakeHTTP({"GetUserStatus": (200, {}, status), "RetrieveUserQuotaSummary": (404, {}, "{}")})
        out = self.run_acc([{"id": "a_000004", "provider": "antigravity", "mode": "auto"}], http=http)
        self.assertEqual([l["used"] for l in out["accounts"][0]["limits"]], [50.0, 100.0])

class AccountsTest(UsageBase):
    def test_clean_accounts(self):
        rows = pu.clean_accounts([{"provider": "claude", "name": "  Работа   основная ", "mode": "weird"},
                                  {"provider": "nope"}, "x", {"provider": "antigravity", "mode": "token", "id": "a_badid!"}])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Работа основная")
        self.assertEqual(rows[0]["mode"], "auto")
        self.assertEqual(rows[1]["mode"], "auto", "antigravity has no token mode")
        self.assertRegex(rows[1]["id"], r"^a_[a-z0-9]+$")

    def test_detect_reports_presence_without_tokens(self):
        self.write(".claude/.credentials.json", {"claudeAiOauth": {"accessToken": "sk-ant-oat01-DETECT", "subscriptionType": "pro"}})
        found = pu.detect()
        self.assertTrue(found["claude"][0]["found"])
        self.assertEqual(found["claude"][0]["plan"], "Pro")
        self.assertFalse(found["codex"][0]["found"])
        self.assertNotIn("DETECT", json.dumps(found))

class UsagePanelTest(UsageBase):
    def setUp(self):
        super().setUp()
        panels._TASKS.clear()

    def test_panel_lifecycle_and_secret_cleanup(self):
        self.write(".claude/.credentials.json", {"claudeAiOauth": {"accessToken": "sk-ant-oat01-AAA"}})
        empty = panels.create_panel(template="usage", start=False)
        self.assertEqual(empty["status"], "setup")
        self.assertEqual(empty["cadence"], {"kind": "interval", "minutes": 5})
        with patch.object(pu, "HTTP", FakeHTTP({"anthropic.com": (200, {}, CLAUDE_USAGE)})):
            panel = asyncio.run(panels.create_and_fill(template="usage", params={"accounts": [
                {"id": "a_00000a", "provider": "claude", "name": "Работа", "mode": "auto"},
                {"id": "a_00000b", "provider": "codex", "name": "Токен", "mode": "token"}]}, wait=10))
        self.assertEqual(panel["status"], "live")
        self.assertEqual(panel["data"]["accounts"][0]["state"], "ok")
        self.assertEqual(panel["data"]["accounts"][1]["state"], "wall")
        self.assertEqual(panel["data"]["subtitle"], "2 аккаунта")
        self.assertEqual([s["url"] for s in panel["sources"]], ["https://claude.ai/settings/usage", "https://chatgpt.com/codex/settings/usage"])
        pu.put_secret("a_00000b", jwt({"exp": NOW + 99}))
        panels.update_panel(panel["id"], {"params": {"accounts": [{"id": "a_00000a", "provider": "claude", "mode": "auto"}]}})
        self.assertFalse(pu.secret_info("a_00000b")["stored"], "a removed account's token is dropped")
        self.assertEqual(panels.floor_minutes("usage"), 1)

if __name__ == "__main__":
    unittest.main()
