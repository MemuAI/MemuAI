"""Round 13: many web-search sources, shared by the Proxy, MemuAI and MemuCode.

Nothing here touches the network: every source answers from a canned response
in the shape its real API returns.
"""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import asyncio  # noqa: E402
import json  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import config, pgdata, search_engines, websearch  # noqa: E402
from app.providers import http  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

class Reply:
    def __init__(self, body, status: int = 200) -> None:
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)

class Web:
    """Answers by URL prefix and remembers every request."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.calls: list = []

    async def _call(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        for prefix, reply in self.routes.items():
            if url.startswith(prefix):
                return reply(kwargs) if callable(reply) else reply
        return Reply("", 404)

    async def get(self, url, **kwargs):
        return await self._call("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self._call("POST", url, **kwargs)

def run(engine: str, web: Web, **kwargs):
    async def session(_proxy=""):
        return web

    async def go():
        with patch.object(http, "get_session", session):
            return await websearch.run_engine(engine, "курс доллара", 3, **kwargs)
    return asyncio.run(go())

HIT = {"title": "Курс доллара", "url": "https://cbr.example/usd", "snippet": "90 рублей"}

DDG_HTML = ('<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg='
            'https%3A%2F%2Fcbr.example%2Fusd&amp;rut=1">Курс <b>доллара</b></a>'
            '<a class="result__snippet" href="#">90 <b>рублей</b></a></div>')
DDG_LITE = ("<tr><td><a rel=\"nofollow\" href=\"https://cbr.example/usd\" class='result-link'>Курс доллара</a></td></tr>"
            "<tr><td class='result-snippet'>90 <b>рублей</b></td></tr>")
DDG_CHALLENGE = '<div class="anomaly-modal">Unfortunately, bots use DuckDuckGo too.</div>'
YAHOO = ('<div class="dd algo algo-sr relsrch Sr"><div class="compTitle"><a class="d-ib" target="_blank" '
         'href="https://r.search.yahoo.com/_ylt=A;_ylu=B/RV=2/RE=1/RO=10/RU=https%3a%2f%2fcbr.example%2fusd/RK=2/RS=x">'
         '<div><span class="d-ib"><span>cbr.example</span>https://cbr.example</span></div>'
         '<h3 class="title"><span class="d-b">Курс доллара</span></h3></a></div>'
         '<div class="compText aAbs"><p><span>Сегодня · </span>90 <b>рублей</b></p></div></div>')

class EnginesTest(unittest.TestCase):
    def test_the_catalogue_separates_kiro_from_duckduckgo_and_names_what_each_source_needs(self) -> None:
        ids = search_engines.engine_ids()
        for name in ("auto", "kiro", "bing", "duckduckgo", "yahoo", "wikipedia", "searxng", "tavily", "brave", "exa",
                     "serper", "serpapi", "google", "jina", "perplexity", "linkup", "firecrawl", "kagi"):
            self.assertIn(name, ids)
        self.assertEqual(ids[0], "auto")
        self.assertEqual(config.SEARCH_ENGINES, ids)
        rows = {row["id"]: row for row in search_engines.catalog()}
        self.assertEqual([row["id"] for row in rows.values() if row["group"] == "free"],
                         ["auto", "kiro", "bing", "duckduckgo", "yahoo", "wikipedia"])
        for row in rows.values():
            self.assertTrue(row["note"] and row["note_en"] and row["label"] and row["label_en"], row["id"])
            self.assertEqual(row["key"] == "required", row["group"] == "key", row["id"])
        self.assertEqual((rows["searxng"]["key"], rows["searxng"]["url"]), ("optional", "server"))
        self.assertEqual(rows["google"]["url"], "cx")
        self.assertTrue(search_engines.ready("duckduckgo"))
        self.assertFalse(search_engines.ready("tavily"))
        self.assertFalse(search_engines.ready("google", "key"))
        self.assertTrue(search_engines.ready("google", "key", "cx"))
        self.assertFalse(search_engines.ready("made-up"))

    def test_free_sources_parse_real_page_shapes(self) -> None:
        ddg = run("duckduckgo", Web({"https://html.duckduckgo.com": Reply(DDG_HTML)}))
        self.assertEqual(ddg, [HIT])
        lite = run("duckduckgo", Web({"https://html.duckduckgo.com": Reply("<html></html>"),
                                      "https://lite.duckduckgo.com": Reply(DDG_LITE)}))
        self.assertEqual(lite, [HIT], "the lite page is the fallback when the main one is empty")
        self.assertEqual(run("yahoo", Web({"https://search.yahoo.com": Reply(YAHOO)})),
                         [{"title": "Курс доллара", "url": "https://cbr.example/usd", "snippet": "Сегодня · 90 рублей"}])
        web = Web({"https://ru.wikipedia.org": Reply({"query": {"search": [
            {"title": "Доллар США", "snippet": "<span>Денежная</span> единица"}]}})})
        self.assertEqual(run("wikipedia", web), [{"title": "Доллар США", "snippet": "Денежная единица",
                                                  "url": "https://ru.wikipedia.org/wiki/%D0%94%D0%BE%D0%BB%D0%BB%D0%B0%D1%80_%D0%A1%D0%A8%D0%90"}])
        self.assertTrue(web.calls[0]["url"].startswith("https://ru."), "Cyrillic query → the Russian edition")

    def test_duckduckgo_challenge_is_explained_and_auto_moves_on_to_yahoo(self) -> None:
        blocked = {"https://html.duckduckgo.com": Reply(DDG_CHALLENGE, 202),
                   "https://lite.duckduckgo.com": Reply(DDG_CHALLENGE, 202)}
        with self.assertRaisesRegex(RuntimeError, "подтвердить, что запрос от человека"):
            run("duckduckgo", Web(blocked))
        self.assertEqual(run("auto", Web({**blocked, "https://search.yahoo.com": Reply(YAHOO)}))[0]["url"],
                         "https://cbr.example/usd")
        with self.assertRaisesRegex(RuntimeError, "Kiro"):
            run("kiro", Web({}))

    def test_keyed_sources_send_the_key_where_each_api_expects_it(self) -> None:
        item = {"title": "Курс доллара", "url": "https://cbr.example/usd"}
        cases = {
            "tavily": ("https://api.tavily.com/search", {"results": [{**item, "content": "90 рублей"}]},
                       lambda c: c["headers"]["Authorization"] == "Bearer KEY" and c["json"]["query"] == "курс доллара"),
            "brave": ("https://api.search.brave.com/res/v1/web/search", {"web": {"results": [{**item, "description": "90 рублей"}]}},
                      lambda c: c["headers"]["X-Subscription-Token"] == "KEY" and c["params"]["q"] == "курс доллара"),
            "exa": ("https://api.exa.ai/search", {"results": [{**item, "text": "90 рублей"}]},
                    lambda c: c["headers"]["x-api-key"] == "KEY" and c["json"]["numResults"] == 3),
            "serper": ("https://google.serper.dev/search", {"organic": [{"title": item["title"], "link": item["url"], "snippet": "90 рублей"}]},
                       lambda c: c["headers"]["X-API-KEY"] == "KEY" and c["json"]["q"] == "курс доллара"),
            "serpapi": ("https://serpapi.com/search.json", {"organic_results": [{"title": item["title"], "link": item["url"], "snippet": "90 рублей"}]},
                        lambda c: c["params"]["api_key"] == "KEY" and c["params"]["engine"] == "google"),
            "jina": ("https://s.jina.ai/", {"data": [{**item, "description": "90 рублей"}]},
                     lambda c: c["headers"]["Authorization"] == "Bearer KEY"),
            "perplexity": ("https://api.perplexity.ai/search", {"results": [{**item, "snippet": "90 рублей"}]},
                           lambda c: c["headers"]["Authorization"] == "Bearer KEY" and c["json"]["max_results"] == 3),
            "linkup": ("https://api.linkup.so/v1/search", {"results": [{"name": item["title"], "url": item["url"], "content": "90 рублей"}]},
                       lambda c: c["json"]["outputType"] == "searchResults"),
            "firecrawl": ("https://api.firecrawl.dev/v2/search", {"data": {"web": [{**item, "description": "90 рублей"}]}},
                          lambda c: c["headers"]["Authorization"] == "Bearer KEY" and c["json"]["limit"] == 3),
            "kagi": ("https://kagi.com/api/v0/search", {"data": [{"t": 1, "list": ["x"]}, {"t": 0, **item, "snippet": "90 рублей"}]},
                     lambda c: c["headers"]["Authorization"] == "Bot KEY"),
        }
        self.assertEqual(set(cases), set(websearch._KEYED), "every keyed source is covered")
        for engine, (url, body, check) in cases.items():
            with self.subTest(engine=engine):
                web = Web({url: Reply(body)})
                self.assertEqual(run(engine, web, api_key="KEY"), [HIT])
                self.assertTrue(check(web.calls[0]), web.calls[0])
                with self.assertRaisesRegex(RuntimeError, "API-ключ"):
                    run(engine, Web({}))
                with self.assertRaisesRegex(RuntimeError, "ключ отклонён"):
                    run(engine, Web({url: Reply("{}", 401)}), api_key="KEY")
                with self.assertRaisesRegex(RuntimeError, "429"):
                    run(engine, Web({url: Reply("{}", 429)}), api_key="KEY")

    def test_google_and_searxng_need_their_second_field(self) -> None:
        web = Web({"https://www.googleapis.com/customsearch/v1": Reply({"items": [
            {"title": HIT["title"], "link": HIT["url"], "snippet": HIT["snippet"]}]})})
        self.assertEqual(run("google", web, api_key="KEY", base_url="CX"), [HIT])
        self.assertEqual((web.calls[0]["params"]["key"], web.calls[0]["params"]["cx"]), ("KEY", "CX"))
        with self.assertRaisesRegex(RuntimeError, "cx"):
            run("google", Web({}), api_key="KEY")
        web = Web({"https://searx.example/search": Reply({"results": [
            {**HIT, "content": HIT["snippet"]}, {"title": "local", "url": "http://127.0.0.1/admin", "content": "x"}]})})
        self.assertEqual(run("searxng", web, base_url="https://searx.example/"), [HIT],
                         "links into the local network are dropped")
        self.assertNotIn("Authorization", web.calls[0]["headers"])
        run("searxng", web, base_url="https://searx.example/search", api_key="KEY")
        self.assertEqual(web.calls[1]["headers"]["X-API-Key"], "KEY")
        with self.assertRaisesRegex(RuntimeError, "адрес сервера"):
            run("searxng", Web({}))

class SettingsTest(unittest.TestCase):
    def test_proxy_settings_keep_a_key_per_source_and_migrate_the_old_single_fields(self) -> None:
        old = config._normalized_settings({"search_engine": "duckduckgo", "search_api_key": " tvly-1 ",
                                           "search_base_url": "https://searx.example"})
        self.assertEqual(old["search_engine"], "auto", "the old «Kiro / DuckDuckGo» is the «auto» source now")
        self.assertEqual(old["search_keys"], {"tavily": "tvly-1"})
        self.assertEqual(old["search_urls"], {"searxng": "https://searx.example"})
        again = config._normalized_settings({**old, "search_engine": "duckduckgo"})
        self.assertEqual(again["search_engine"], "duckduckgo", "chosen after the migration, it stays DuckDuckGo")
        self.assertEqual(config._normalized_settings({"search_engine": "nope"})["search_engine"], "auto")
        self.assertEqual(config._normalized_settings({"search_keys": {"exa": "k", "made-up": "x", "brave": ""}})["search_keys"],
                         {"exa": "k"})

        store = config.ConfigStore.__new__(config.ConfigStore)
        import threading
        store._lock = threading.RLock()
        store.settings = config.Settings(**config._normalized_settings({}))
        store.save = lambda: None
        self.assertEqual(store.apply_updates({"search_engine": "exa", "search_keys": {"exa": " key-1 "}}), [])
        self.assertEqual(store.apply_updates({"search_keys": {"brave": "key-2"}}), [])
        self.assertEqual(store.settings.search_keys, {"exa": "key-1", "brave": "key-2"}, "one source does not erase another")
        self.assertEqual(store.apply_updates({"search_keys": {"exa": ""}}), [])
        self.assertEqual(store.settings.search_keys, {"brave": "key-2"})
        self.assertEqual(store.apply_updates({"search_keys": {"made-up": "x"}}), ["search_keys"])
        self.assertEqual(store.apply_updates({"search_engine": "altavista"}), ["search_engine"])

    def test_memuai_preferences_migrate_the_same_way(self) -> None:
        old = pgdata._normalize_preferences({"custom_search_provider": "duckduckgo", "tavily_api_key": "tvly-1",
                                             "custom_search_url": "https://searx.example", "custom_search_api_key": "sx"})
        self.assertEqual(old["custom_search_provider"], "auto")
        self.assertEqual(old["search_api_keys"], {"tavily": "tvly-1", "searxng": "sx"})
        self.assertEqual(old["search_urls"], {"searxng": "https://searx.example"})
        self.assertEqual(pgdata._normalize_preferences({**old, "custom_search_provider": "duckduckgo"})["custom_search_provider"],
                         "duckduckgo")
        self.assertEqual(pgdata._normalize_preferences({"custom_search_provider": "broken"})["custom_search_provider"], "auto")

    def test_an_unconfigured_source_is_reported_to_tools_and_falls_back_for_plain_chat(self) -> None:
        settings = config.Settings(**config._normalized_settings({"search_engine": "tavily"}))
        web = Web({"https://html.duckduckgo.com": Reply(DDG_HTML)})

        async def session(_proxy=""):
            return web

        async def go(**kwargs):
            websearch._CACHE.clear()
            with patch.object(http, "get_session", session), \
                    patch.object(config.config_store, "settings", settings):
                return await websearch.search("курс доллара", limit=3, **kwargs)

        self.assertEqual(asyncio.run(go()), [HIT], "no key yet: the request still gets free results")
        with self.assertRaisesRegex(RuntimeError, "не настроен"):
            asyncio.run(go(raise_errors=True))
        settings.search_keys["tavily"] = "KEY"
        web.routes["https://api.tavily.com/search"] = Reply({"results": [{**HIT, "content": "90 рублей"}]})
        asyncio.run(go())
        self.assertEqual(web.calls[-1]["url"], "https://api.tavily.com/search")
        asyncio.run(go(engine="auto"))
        self.assertTrue(web.calls[-1]["url"].startswith("https://html.duckduckgo.com"),
                        "MemuAI may ask the Proxy for the free mode explicitly")

class MemuAISearchRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patches = [patch.object(pgdata, "MEMU_DIR", Path(self._temp.name)),
                         patch.dict(os.environ, {"MEMUAI_PROXY_URL": "http://127.0.0.1:9"})]
        for item in self._patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        self._temp.cleanup()

    def test_the_window_gets_the_catalogue_and_can_test_the_chosen_source(self) -> None:
        from app.playground_api import create_app
        web = Web({"https://api.exa.ai/search": Reply({"results": [{**HIT, "text": "90 рублей"}]}),
                   "https://html.duckduckgo.com": Reply(DDG_HTML)})

        async def session(_proxy=""):
            return web

        with TestClient(create_app(), base_url="http://127.0.0.1") as client, patch.object(http, "get_session", session):
            catalog = client.get("/api/pg/search/engines").json()
            self.assertEqual(catalog["default"], "auto")
            self.assertGreaterEqual(len(catalog["engines"]), 17)
            pgdata.save_preferences({"custom_search_provider": "exa"})
            missing = client.post("/api/pg/search/test")
            self.assertEqual(missing.status_code, 400)
            self.assertIn("API-ключ Exa", missing.json()["error"])
            pgdata.save_preferences({"search_api_keys": {"exa": "KEY"}})
            ok = client.post("/api/pg/search/test").json()
            self.assertEqual((ok["ok"], ok["engine"], ok["count"], ok["first"]["url"]), (True, "exa", 1, HIT["url"]))
            self.assertEqual(web.calls[-1]["headers"]["x-api-key"], "KEY")

            pgdata.save_preferences({"custom_search_provider": "auto"})
            self.assertEqual(client.post("/api/pg/search/test").json()["first"]["url"], HIT["url"])
            pgdata.save_preferences({"custom_search_provider": "kiro"})
            self.assertIn("Proxy не запущен", client.post("/api/pg/search/test").json()["error"])

    def test_both_windows_build_the_source_list_from_the_catalogue(self) -> None:
        window = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        for needle in ('"/api/pg/search/engines"', '"/api/pg/search/test"', "search_api_keys", "search_urls",
                       'id="st-search-test"'):
            self.assertIn(needle, window)
        self.assertNotIn("st-search-tavily-key", window, "one key field serves every source")
        panel = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        for needle in ('"/api/admin/search/engines"', '"/api/admin/search/test"', "search_keys", "search_urls"):
            self.assertIn(needle, panel)
        html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="search-engine"', html)
        self.assertNotIn("Kiro / DuckDuckGo", html)

if __name__ == "__main__":
    unittest.main()
