"""Round 33 (r33-proxy): MemuAI Proxy UI — items #12 (UI), #19 (accounts), #24 (provider catalog).

No network by default. The live part (``ProviderCatalogLiveTest``) needs MEMU_LIVE_CATALOG=1 and checks that
every catalog base URL answers ``GET /models`` like an API (401/403/400 for a missing key, or 200), and, with
OPENROUTER_KEY set, that the OpenRouter preset lists models. Keys are read from the environment only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r33-proxy-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "app" / "static"
CYRILLIC = re.compile("[А-Яа-яЁё]")


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


class ProviderCatalogDataTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.providers.catalog import REGIONS, catalog
        self.rows = catalog()
        self.regions = REGIONS

    def test_about_a_hundred_unique_services(self) -> None:
        self.assertGreaterEqual(len(self.rows), 100)
        ids = [r["id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)))
        names = [r["name"] for r in self.rows]
        self.assertEqual(len(names), len(set(names)), "model prefixes must not collide")
        for name in names:
            self.assertRegex(name, r"^[a-z0-9][a-z0-9-]*$")

    def test_regions_cover_the_world(self) -> None:
        seen = {r["region"] for r in self.rows}
        self.assertTrue({"us", "eu", "cn", "ru", "asia", "global", "local"} <= seen)
        self.assertTrue(seen <= set(self.regions))
        self.assertGreaterEqual(sum(r["region"] == "cn" for r in self.rows), 20)
        self.assertGreaterEqual(sum(r["region"] == "eu" for r in self.rows), 8)
        self.assertGreaterEqual(sum(r["region"] == "local" for r in self.rows), 7)

    def test_top_tiles_are_ranked_and_listed_first(self) -> None:
        top = [r for r in self.rows if r["top"]]
        self.assertTrue(12 <= len(top) <= 18)
        self.assertEqual(sorted(r["top"] for r in top), list(range(1, len(top) + 1)))
        self.assertEqual([r["top"] for r in self.rows[:len(top)]], list(range(1, len(top) + 1)))
        rest = [r["title"].lower() for r in self.rows[len(top):]]
        self.assertEqual(rest, sorted(rest), "below the tiles the catalog is alphabetical")
        for must in ("openrouter", "openai", "anthropic", "google-ai-studio", "deepseek"):
            self.assertIn(must, {r["id"] for r in top})

    def test_every_entry_is_complete_and_safe(self) -> None:
        for r in self.rows:
            with self.subTest(r["id"]):
                for field in ("title", "title_en", "desc", "desc_en", "hint", "hint_en", "mono", "base_url"):
                    self.assertTrue(r[field], field)
                for field in ("title_en", "desc_en", "hint_en", "mono"):
                    self.assertIsNone(CYRILLIC.search(r[field]), f"{field} must be English: {r[field]}")
                self.assertRegex(r["color"], r"^#[0-9a-fA-F]{6}$")
                self.assertIn(r["auth"], ("bearer", "header", "none", "optional"))
                if r["auth"] == "header":
                    self.assertTrue(r["auth_header"])
                for field in ("key_url", "docs_url"):
                    if r[field]:
                        self.assertTrue(r[field].startswith("https://"), r[field])
                if r["region"] == "local":
                    self.assertTrue(r["base_url"].startswith("http://127.0.0.1:"))
                    self.assertIn(r["auth"], ("none", "optional"))
                else:
                    self.assertTrue(r["base_url"].startswith("https://"))
                self.assertFalse(r["base_url"].endswith("/"))
                self.assertLessEqual(len(r["mono"]), 3)

    def test_placeholders_have_a_field(self) -> None:
        for r in self.rows:
            with self.subTest(r["id"]):
                text = " ".join([r["base_url"], json.dumps(r["headers"]), " ".join(r["models"])])
                for param in r["params"]:
                    self.assertIn(param["key"], text)
                    self.assertTrue(param.get("label") and param.get("label_en"))
                    self.assertIsNone(CYRILLIC.search(param["label_en"]))
                left = text
                for param in r["params"]:
                    left = left.replace(param["key"], "")
                self.assertIsNone(re.search(r"\b(?:YOUR-[A-Z]+|RESOURCE|PROJECT_ID|ACCOUNT_ID|FOLDER_ID|PRODUCT_ID)\b", left),
                                  "a placeholder without an input field")
                if r["params"]:
                    self.assertTrue(r["edit_url"] or "FOLDER_ID" in text)

    def test_retired_services_are_not_offered(self) -> None:
        ids = {r["id"] for r in self.rows}
        hosts = " ".join(r["base_url"] for r in self.rows)
        for gone in ("github-models", "hyperbolic", "01ai", "ai21", "aleph-alpha", "llama-api"):
            self.assertNotIn(gone, ids)
        for host in ("models.github.ai", "api.hyperbolic.xyz", "lingyiwanwu", "api.llama.com", "api.lambda.ai"):
            self.assertNotIn(host, hosts)

    def test_key_header_presets(self) -> None:
        from app.providers.catalog import catalog, headers_for
        rows = {r["id"]: r for r in catalog()}
        self.assertEqual(headers_for(rows["azure-openai"]), {"api-key": "{api_key}"})
        self.assertEqual(headers_for(rows["anthropic"])["x-api-key"], "{api_key}")
        self.assertEqual(rows["yandex"]["headers"], {"OpenAI-Project": "FOLDER_ID"})


class CustomProviderKeyHeaderTest(unittest.TestCase):
    """A "{api_key}" header value receives the key; the default Bearer header is then dropped."""

    def headers(self, fields):
        from app.providers.custom import CustomProvider
        from app.state import Account
        account = Account(id="t", provider="custom", label="t", fields=dict(fields))
        return CustomProvider()._headers(account)

    def test_plain_key_is_bearer(self) -> None:
        h = self.headers({"name": "x", "base_url": "https://x/v1", "api_key": "sk-1"})
        self.assertEqual(h["Authorization"], "Bearer sk-1")

    def test_azure_style_header(self) -> None:
        h = self.headers({"name": "x", "base_url": "https://x/v1", "api_key": "k-2",
                          "headers": json.dumps({"api-key": "{api_key}"})})
        self.assertEqual(h["api-key"], "k-2")
        self.assertNotIn("Authorization", h)

    def test_both_headers_when_the_preset_asks(self) -> None:
        h = self.headers({"name": "x", "base_url": "https://x/v1", "api_key": "k-3",
                          "headers": json.dumps({"x-api-key": "{api_key}", "Authorization": "Bearer {api_key}"})})
        self.assertEqual((h["x-api-key"], h["Authorization"]), ("k-3", "Bearer k-3"))

    def test_no_key_leaves_no_empty_header(self) -> None:
        h = self.headers({"name": "x", "base_url": "http://127.0.0.1:1/v1",
                          "headers": json.dumps({"api-key": "{api_key}", "Authorization": "Bearer {api_key}"})})
        self.assertNotIn("api-key", h)
        self.assertNotIn("Authorization", h)


class PresetsEndpointTest(unittest.TestCase):
    def test_endpoint_serves_the_catalog(self) -> None:
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app.config import config_store
        from app.main import create_app
        with patch.object(config_store, "_runtime_host", "127.0.0.1"), \
                patch.object(config_store, "_runtime_port", 8317), \
                TestClient(create_app(), base_url="http://127.0.0.1:8317") as client:
            resp = client.get("/api/admin/providers/presets")
        if resp.status_code in (401, 403):
            self.skipTest("the admin API asks for a password in this environment")
        data = resp.json()["presets"]
        self.assertGreaterEqual(len(data), 100)
        row = {p["id"]: p for p in data}
        self.assertEqual(row["azure-openai"]["headers"], {"api-key": "{api_key}"})
        self.assertEqual(row["openrouter"]["icon"], row["openrouter"]["mono"])
        self.assertIn("in_use", row["openai"])


class ProxyUiMarkupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.html = read("index.html")
        self.app = read("app.js")

    def test_model_catalog_is_a_left_rail_page(self) -> None:
        self.assertIn('data-tab="models"', self.html)
        tab = self.html[self.html.index('id="tab-models"'):]
        tab = tab[:tab.index("</section>\n")]
        self.assertIn('id="models-card"', tab)
        self.assertIn('id="models-table"', tab)
        self.assertNotIn('id="sg-sec-models"', self.html)
        self.assertNotIn('data-sg-nav="models"', self.html)
        self.assertIn('btn.dataset.tab === "models") loadModels()', self.app)
        settings = read("settings.js")
        self.assertNotIn('"models", "providers"', settings)
        self.assertIn('goTab("models")', settings)

    def test_duplicate_and_lone_copy_key_buttons_are_gone(self) -> None:
        for gone in ("data-dup=", '["dup",', "data-copykey", "copyAccountKey("):
            self.assertNotIn(gone, self.app)
        self.assertIn("data-copyfield", self.app)          # «копировать» next to the key in the account form

    def test_selection_mode_controls(self) -> None:
        self.assertIn('id="acc-select-cancel"', self.html)
        self.assertIn('<button class="btn" type="button" id="acc-select-mode"', self.html)
        self.assertNotIn('<input type="checkbox" id="acc-select-mode">', self.html)
        js = read("proxy-r33.js")
        for needle in ('"Escape"', "clearSelection", "pointerdown", "edgeSpeed", "acc.sel.count", "reorderAccounts(source"):
            self.assertIn(needle, js)
        self.assertIn('draggable="false" data-reorder=', self.app)
        self.assertNotIn('card.addEventListener("dragstart"', self.app)

    def test_assets_are_wired(self) -> None:
        for name in ("proxy-r33.css", "proxy-r33-i18n.js", "proxy-r33.js", "proxy-catalog-r33.js"):
            self.assertIn(f"/{name}?v=3.5-r33", self.html)
            self.assertTrue((STATIC / name).is_file(), name)
        order = [self.html.index(f"/{n}") for n in ("settings-i18n.js", "proxy-r33-i18n.js", "app.js", "proxy-r33.js")]
        self.assertEqual(order, sorted(order))

    def test_strings_have_both_languages(self) -> None:
        src = read("proxy-r33-i18n.js")
        ru = src[src.index("I18N.ru"):src.index("I18N.en")]
        en = src[src.index("I18N.en"):]
        keys = lambda block: set(re.findall(r'"([a-z][\w.]+)":', block))
        self.assertEqual(keys(ru), keys(en))
        self.assertIsNone(CYRILLIC.search(en), "English strings must not contain Cyrillic")
        catalog_js = read("proxy-catalog-r33.js")
        for key in set(re.findall(r't\("((?:cat|acc\.sel|acc\.drag)[\w.]*)"\)', catalog_js + read("proxy-r33.js"))):
            self.assertIn(key, keys(ru), key)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_js_syntax(self) -> None:
        for name in ("app.js", "settings.js", "proxy-r33.js", "proxy-r33-i18n.js", "proxy-catalog-r33.js"):
            done = subprocess.run([shutil.which("node"), "--check", str(STATIC / name)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, f"{name}: {done.stderr}")


@unittest.skipUnless(os.environ.get("MEMU_LIVE_CATALOG") == "1", "live catalog check: set MEMU_LIVE_CATALOG=1")
class ProviderCatalogLiveTest(unittest.TestCase):
    def test_base_urls_answer_like_an_api(self) -> None:
        import concurrent.futures
        import httpx
        from app.providers.catalog import catalog
        rows = [r for r in catalog() if r["region"] != "local" and not r["params"] and r["id"] != "gigachat"]
        ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131 Safari/537.36",
              "Authorization": "Bearer sk-invalid-test"}

        def probe(row):
            # GET /models first; services without it (Baseten, Perplexity…) are asked for /chat/completions.
            result = None
            for method, path in (("GET", "/models"), ("POST", "/chat/completions")):
                try:
                    with httpx.Client(timeout=20, trust_env=False) as client:
                        result = client.request(method, row["base_url"] + path, headers=ua,
                                                json={"model": "x", "messages": []} if method == "POST" else None).status_code
                except Exception as exc:  # noqa: BLE001 — reported below
                    result = type(exc).__name__
                if result in (200, 400, 401, 403):
                    break
            return row["id"], result
        with concurrent.futures.ThreadPoolExecutor(16) as pool:
            results = dict(pool.map(probe, rows))
        bad = {k: v for k, v in results.items() if v not in (200, 400, 401, 403, 404, 422)}
        self.assertFalse(bad, bad)

    @unittest.skipUnless(os.environ.get("OPENROUTER_KEY"), "OPENROUTER_KEY is not set")
    def test_openrouter_preset_with_a_key(self) -> None:
        import httpx
        from app.providers.catalog import catalog
        row = next(r for r in catalog() if r["id"] == "openrouter")
        with httpx.Client(timeout=30, trust_env=False) as client:
            resp = client.get(row["base_url"] + "/models",
                              headers={"Authorization": f"Bearer {os.environ['OPENROUTER_KEY']}", **row["headers"]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("data"))


if __name__ == "__main__":
    unittest.main()
