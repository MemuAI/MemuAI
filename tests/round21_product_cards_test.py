"""Round 21: product cards in chat (app/product_cards.py, pgstatic/products.js).

Parser/validator, schema.org extraction from saved store pages, locale store
selection, the lookup tool with mocked search/fetch, the HTTP endpoints, the
prompt wiring and the text fallback. The renderer is smoke-tested in headless
Chromium by round21_product_cards_browser_test.py.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from app import pgdata, product_cards as pc

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
FIX = Path(__file__).resolve().parent / "fixtures" / "product_cards"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def run(coro):
    return asyncio.run(coro)


class NumbersAndText(unittest.TestCase):
    def test_amounts(self):
        cases = {"26 699 ₽": 26699, "$1,299.99": 1299.99, "1.299,00 €": 1299, "12.990": 12990, "от 12 990 руб.": 12990,
                 "26\u00a0699": 26699, "4,8": 4.8, 1999: 1999, "EUR325.59": 325.59, "0": None, "abc": None,
                 True: None, None: None, "-5": 5}
        for raw, expected in cases.items():
            self.assertEqual(pc.parse_amount(raw), expected, raw)

    def test_currencies(self):
        self.assertEqual(pc.currency_of("26 699 ₽"), "RUB")
        self.assertEqual(pc.currency_of("12 990 руб"), "RUB")
        self.assertEqual(pc.currency_of("EUR325.59"), "EUR")
        self.assertEqual(pc.currency_of("$89.99"), "USD")
        self.assertEqual(pc.currency_of("usd"), "USD")
        self.assertEqual(pc.currency_of("10 BYN"), "BYN")
        self.assertEqual(pc.currency_of("12 990", "RUB"), "RUB")
        self.assertEqual(pc.currency_of("RUR"), "RUB")

    def test_snippet_prices_ignore_ratings(self):
        self.assertEqual(pc.snippet_price("Цена: от 10 990 ₽ в наличии"), {"amount": 10990, "currency": "RUB"})
        self.assertEqual(pc.snippet_price("Rated 4.8 out of 5. $89.99 at Amazon"), {"amount": 89.99, "currency": "USD"})
        self.assertEqual(pc.snippet_price("1 TB, скидка 5 р., итог 12 990 ₽")["amount"], 12990)
        self.assertIsNone(pc.snippet_price("Рейтинг 4.9 — 954 оценки"))

    def test_clean_text_strips_markup_and_controls(self):
        self.assertEqual(pc.clean_text("<b>Hi</b>\u202e there &amp; you", 50), "Hi there & you")
        self.assertTrue(pc.clean_text("слово " * 40, 30).endswith("…"))
        self.assertLessEqual(len(pc.clean_text("слово " * 40, 30)), 30)
        self.assertEqual(pc.clean_text({"x": 1}, 10), "")

    def test_ratings_scale(self):
        self.assertEqual(pc._rating("9.2", "10"), 4.6)
        self.assertEqual(pc._rating(92), 4.6)
        self.assertIsNone(pc._rating(0))
        self.assertIsNone(pc._rating("abc"))
        self.assertEqual(pc._rating("4,8"), 4.8)


class Stores(unittest.TestCase):
    def test_store_by_domain_not_by_name(self):
        self.assertEqual(pc.store_for_url("https://www.ozon.ru/product/x-123/"), "ozon")
        self.assertEqual(pc.store_for_url("https://market.yandex.ru/card/x/1"), "yandex_market")
        self.assertEqual(pc.store_for_url("https://www.amazon.co.uk/dp/B000000000"), "amazon")
        self.assertEqual(pc.store_for_url("https://ozon.ru.evil.example/product/1"), "")
        self.assertEqual(pc.store_for_url("https://notozon.ru/product/1"), "")
        self.assertEqual(pc.store_for_url("javascript:alert(1)"), "")

    def test_locale_defaults(self):
        self.assertEqual(pc.LOCALE_STORES["ru"], ("ozon", "wildberries", "dns", "yandex_market", "mvideo", "citilink", "megamarket"))
        self.assertEqual(pc.LOCALE_STORES["en"], ("amazon", "bestbuy", "walmart", "newegg", "bh", "ebay"))
        self.assertEqual(pc.LOCALE_CURRENCY, {"ru": "RUB", "en": "USD"})
        self.assertEqual(pc.store_name("yandex_market", "ru"), "Яндекс Маркет")
        self.assertEqual(pc.store_name("yandex_market", "en"), "Yandex Market")
        self.assertEqual(pc.locale_of("en-US"), "en")
        self.assertEqual(pc.locale_of("de"), "ru")
        for store_id in (*pc.LOCALE_STORES["ru"], *pc.LOCALE_STORES["en"]):
            self.assertIn(store_id, pc.STORES)
            self.assertRegex(pc.STORES[store_id]["color"], r"^#[0-9a-f]{6}$")

    def test_product_pages(self):
        self.assertTrue(pc.is_product_page("https://www.dns-shop.ru/product/6963f3c1d897d9cb/1000-gb/"))
        self.assertFalse(pc.is_product_page("https://www.ozon.ru/category/990-evo-ssd/"))
        self.assertTrue(pc.is_product_page("https://www.wildberries.ru/catalog/1606087471/detail.aspx"))
        self.assertFalse(pc.is_product_page("https://www.wildberries.ru/catalog/elektronika/tags/990"))
        self.assertTrue(pc.is_product_page("https://www.amazon.com/Samsung/dp/B0BHJJ9Y77/ref=sr_1_1"))

    def test_query_lookup_plan_follows_locale(self):
        ru = pc.lookup_queries("WD Black SN770", "ru", pc.LOCALE_STORES["ru"])
        en = pc.lookup_queries("WD Black SN770", "en", pc.LOCALE_STORES["en"])
        self.assertTrue(any("купить" in q for q in ru["shop"]))
        self.assertIn("отзывы", ru["reviews"][0])
        self.assertTrue(any("buy" in q for q in en["shop"]))
        self.assertIn("review", en["reviews"][0])
        # Round 22: every regional store gets its own search attempt where a search address is known.
        self.assertEqual([sid for sid, _ in pc.direct_search_pages("x", "ru", pc.LOCALE_STORES["ru"])],
                         list(pc.LOCALE_STORES["ru"]))
        self.assertIn("https://market.yandex.ru/search?text=x", [u for _, u in pc.direct_search_pages("x", "ru", ["yandex_market"])])
        self.assertTrue(all(sid in pc.LOCALE_STORES["en"] for sid, _ in pc.direct_search_pages("x", "en", pc.LOCALE_STORES["en"])))

    def test_public_url_and_keys(self):
        for bad in ("javascript:alert(1)", "data:image/png;base64,AAAA", "http://127.0.0.1/x", "http://192.168.1.2/a",
                    "http://localhost:8601/api", "https://user:pw@ozon.ru/product/1", "ftp://ozon.ru/x", "https://a.b/\"onerror=",
                    "http://2130706433/"):
            self.assertEqual(pc.public_url(bad), "", bad)
        self.assertEqual(pc.public_url("//ozon.ru/product/1"), "https://ozon.ru/product/1")
        self.assertEqual(pc.url_key("https://www.amazon.com/Some-Name/dp/B0BHJJ9Y77/ref=sr_1_1?th=1"),
                         pc.url_key("https://amazon.com/dp/B0BHJJ9Y77"))
        self.assertEqual(pc.url_key("https://www.ozon.ru/product/x-1/?utm_source=a"), "ozon.ru/product/x-1")

    def test_relevance_requires_model_tokens(self):
        self.assertEqual(pc._relevance("WD Black SN770 1TB", "WD Hard Drive storage WD_BLACK"), 0.0)
        self.assertGreaterEqual(pc._relevance("WD Black SN770 1TB", "WD_BLACK SN770 NVMe 1 ТБ"), 0.75)
        self.assertGreaterEqual(pc._relevance("Samsung 990 EVO Plus 1TB", "Купить 1000 ГБ Samsung 990 EVO Plus"), 0.8)


class Extraction(unittest.TestCase):
    def test_store_product_jsonld(self):
        data = pc.extract_product(fixture("store_product_jsonld.html"), "https://www.dns-shop.ru/product/abc/sn770/")["product"]
        self.assertEqual(data["title"], "1000 ГБ M.2 NVMe накопитель WD Black SN770 [WDS100T3X0E]")
        self.assertEqual(data["brand"], "WD")
        self.assertEqual(data["sku"], "WDS100T3X0E")
        self.assertEqual(data["price"], {"amount": 9499, "currency": "RUB"})   # AggregateOffer → lowPrice
        self.assertEqual(data["availability"], "in_stock")
        self.assertEqual(data["rating"], 4.8)
        self.assertEqual(data["review_count"], 1254)
        self.assertIn("https://www.dns-shop.ru/thumb/sn770-3.jpg", data["images"])   # relative ImageObject
        self.assertEqual(data["images"][0], "https://c.dns-shop.ru/thumb/st4/fit/0/0/sn770-1.jpg")
        self.assertEqual(len(data["reviews"]), 1)                                   # "ok" is too short
        self.assertEqual(data["reviews"][0]["author"], "Алексей")
        self.assertEqual(data["reviews"][0]["rating"], 5)
        self.assertEqual(data["description"], "Накопитель & радиатор не входит")

    def test_market_search_itemlist(self):
        found = pc.extract_product(fixture("market_search.html"), "https://market.yandex.ru/search?text=x")
        self.assertEqual(len(found["list"]), 3)
        first = found["list"][0]
        self.assertTrue(first["url"].startswith("https://market.yandex.ru/card/"))
        self.assertEqual(first["price"]["currency"], "RUB")
        self.assertGreater(first["price"]["amount"], 1000)
        self.assertTrue(first["images"][0].startswith("https://avatars.mds.yandex.net/"))
        self.assertTrue(all("990" in row["title"] for row in found["list"]))

    def test_amazon_page(self):
        data = pc.extract_product(fixture("amazon_dp.html"), "https://www.amazon.com/Samsung/dp/B0BHJJ9Y77/ref=sr_1_1")["product"]
        self.assertTrue(data["title"].startswith("Samsung SSD 990 PRO 2TB"))
        self.assertEqual(data["price"], {"amount": 389.99, "currency": "USD"})
        self.assertEqual(data["rating"], 4.7)
        self.assertEqual(data["review_count"], 13433)
        self.assertEqual(len(data["images"]), 4)
        self.assertTrue(all(img.startswith("https://m.media-amazon.com/") for img in data["images"]))
        self.assertEqual(data["reviews"][0]["author"], "Matthew Hewitt")
        self.assertEqual(data["reviews"][0]["rating"], 5)

    def test_microdata_and_og(self):
        data = pc.extract_product(fixture("microdata_og.html"), "https://shop.example.com/t500")["product"]
        self.assertEqual(data["price"], {"amount": 79.99, "currency": "USD"})        # microdata before og
        self.assertEqual(data["availability"], "out_of_stock")
        self.assertEqual(data["rating"], 4.6)                                         # 9.2 of 10
        self.assertEqual(data["review_count"], 312)
        self.assertIn("https://shop.example.com/media/t500-angle.jpg", data["images"])
        self.assertIn("https://images.example.com/t500-back.jpg", data["images"])
        self.assertNotIn("https://images.example.com/logo.png", data["images"])

    def test_captcha_is_recognized(self):
        self.assertTrue(pc.blocked_page(fixture("captcha.html")))
        self.assertTrue(pc.blocked_page("<html></html>", "https://market.yandex.ru/showcaptcha?cc=1"))
        self.assertFalse(pc.blocked_page(fixture("store_product_jsonld.html")))
        self.assertFalse(pc.blocked_page(fixture("amazon_dp.html")))

    def test_broken_inputs_do_not_raise(self):
        for html in ("", "<script type=application/ld+json>{oops</script>", "<meta property=og:image content=javascript:x>",
                     "<script type=\"application/ld+json\">[1,2,{\"@type\":\"Product\"}]</script>"):
            out = pc.extract_product(html, "https://example.com/p")
            self.assertIsInstance(out["product"], dict)
        self.assertTrue(pc._tiny_image("https://x/samsung_100px.jpg"))
        self.assertFalse(pc._tiny_image("https://m.media-amazon.com/images/I/61n._AC_SL1500_.jpg"))


def _store_with_pages() -> pc.Provenance:
    store = pc.Provenance(None)
    now = int(time.time())
    store.remember("https://market.yandex.ru/card/ssd-a/1", {"title": "SSD A", "price": {"amount": 18130, "currency": "RUB"},
                                                             "availability": "in_stock", "images": ["https://avatars.mds.yandex.net/a.jpg"],
                                                             "rating": 4.9, "review_count": 954, "checked_at": now})
    store.remember("https://www.dns-shop.ru/product/aa/ssd-a/", {"title": "SSD A at DNS",
                                                                 "snippets": ["SSD A — от 17 999 ₽ в DNS"]}, kind="snippet")
    store.remember("https://www.ixbt.com/data/ssd-a-review.html", {"snippets": [
        "Особенно если ограничиваться ходовым терабайтом — в этом случае SN770 укладывался в сотню долларов."]}, kind="snippet")
    return store


BLOCK = {
    "query": "SSD до 20 000 ₽", "locale": "ru", "currency": "RUB",
    "items": [
        {"name": "SSD A 1 ТБ", "brand": "Acme", "best": True, "badge": "Лучший выбор", "price": {"amount": 15000, "currency": "RUB"},
         "rating": 5, "rating_source": "https://www.ixbt.com/data/ssd-a-review.html",
         "images": ["https://img.example.com/a.jpg", "javascript:alert(1)", "http://10.0.0.1/x.png"],
         "offers": [{"store": "Ozon", "url": "https://market.yandex.ru/card/ssd-a/1", "price": 15000},
                    {"url": "https://www.dns-shop.ru/product/aa/ssd-a/", "price": "17 999 ₽"},
                    {"store": "Ozon", "url": "https://ozon-sale.biz/ssd-a", "price": 5000},
                    {"url": "javascript:alert(1)"}],
         "reviews": [{"text": "Особенно если ограничиваться ходовым терабайтом — в этом случае SN770 укладывался в сотню долларов.",
                      "url": "https://www.ixbt.com/data/ssd-a-review.html", "author": "iXBT"},
                     {"text": "Лучший SSD, всем советую", "author": "Иван"},
                     {"text": "Придуманная цитата, которой не было на странице", "url": "https://www.ixbt.com/data/ssd-a-review.html"}],
         "pros": ["Быстрый", "<img src=x onerror=alert(1)>Холодный"], "summary": "Хороший выбор."},
        {"name": "SSD B", "best": True, "price": 21000, "rating": 4.5},
        {"brand": "No name item"},
    ],
}


class Normalize(unittest.TestCase):
    def setUp(self):
        self.store = _store_with_pages()
        self.result = pc.normalize(json.dumps(BLOCK, ensure_ascii=False), locale="ru", store=self.store)
        self.assertTrue(self.result["ok"], self.result)
        self.items = self.result["data"]["items"]

    def test_items_and_best(self):
        self.assertEqual([item["name"] for item in self.items], ["SSD A 1 ТБ", "SSD B"])
        self.assertTrue(self.items[0]["best"])
        self.assertNotIn("best", self.items[1])            # only one best pick
        self.assertEqual(self.result["data"]["currency"], "RUB")

    def test_offers_store_from_domain_and_unknown_removed(self):
        offers = self.items[0]["offers"]
        self.assertEqual([o["store_id"] for o in offers], ["yandex_market", "dns"])
        self.assertEqual(offers[0]["store"], "Яндекс Маркет")                  # not the claimed "Ozon"
        self.assertTrue(any("unknown store" in w for w in self.result["warnings"]))
        self.assertTrue(all(o["url"].startswith("https://") for o in offers))

    def test_prices_verified_or_approx(self):
        market, dns = self.items[0]["offers"]
        self.assertEqual(market["price"], {"amount": 18130, "currency": "RUB", "approx": False})   # page beats the claim
        self.assertTrue(market["verified"])
        self.assertIn("checked_at", market)
        self.assertEqual(dns["price"]["approx"], True)
        self.assertEqual(dns.get("price_source"), "snippet")
        self.assertNotIn("verified", dns)
        price = self.items[0]["price"]
        self.assertEqual((price["amount"], price["approx"], price["store"]), (18130, False, "Яндекс Маркет"))
        self.assertEqual(self.items[1]["price"], {"amount": 21000, "currency": "RUB", "approx": True})
        self.assertTrue(any("replaced with the store page price" in w for w in self.result["warnings"]))

    def test_rating_needs_source(self):
        self.assertEqual(self.items[0]["rating"], 4.9)                 # page rating with more ratings wins
        self.assertEqual(self.items[0]["review_count"], 954)
        self.assertTrue(self.items[0]["rating_verified"])
        self.assertNotIn("rating", self.items[1])                      # no source → dropped
        self.assertTrue(any("rating without a source" in w for w in self.result["warnings"]))

    def test_reviews_need_links_and_are_checked(self):
        reviews = self.items[0]["reviews"]
        self.assertEqual(len(reviews), 2)
        self.assertTrue(reviews[0]["verified"])
        self.assertEqual(reviews[0]["source"], "ixbt.com")
        self.assertNotIn("verified", reviews[1])                       # invented quote is not marked verified
        self.assertTrue(any("review without a link" in w for w in self.result["warnings"]))

    def test_images_and_text_sanitized(self):
        self.assertEqual(self.items[0]["images"], ["https://img.example.com/a.jpg"])
        self.assertEqual(self.items[0]["pros"][1], "Холодный")
        dumped = json.dumps(self.result, ensure_ascii=False)
        self.assertNotIn("javascript:", dumped)
        self.assertNotIn("<img", dumped)
        self.assertNotIn("10.0.0.1", dumped)

    def test_images_fall_back_to_offer_pages(self):
        block = {"items": [{"name": "SSD A", "offers": [{"url": "https://market.yandex.ru/card/ssd-a/1"}]}]}
        item = pc.normalize(block, store=self.store)["data"]["items"][0]
        self.assertEqual(item["images"], ["https://avatars.mds.yandex.net/a.jpg"])

    def test_idempotent(self):
        again = pc.normalize(self.result["data"], locale="ru", store=self.store)
        self.assertEqual(again["data"], self.result["data"])

    def test_fenced_and_bad_input(self):
        fenced = "Вот:\n```memuai-products\n" + json.dumps(BLOCK, ensure_ascii=False) + "\n```\nКонец"
        self.assertTrue(pc.normalize(fenced, store=self.store)["ok"])
        self.assertFalse(pc.normalize("not json", store=self.store)["ok"])
        self.assertFalse(pc.normalize('{"items": []}', store=self.store)["ok"])
        self.assertFalse(pc.normalize("x" * (pc.MAX_RAW_CHARS + 1), store=self.store)["ok"])
        many = {"items": [{"name": f"P{i}"} for i in range(10)]}
        self.assertEqual(len(pc.normalize(many, store=self.store)["data"]["items"]), pc.MAX_ITEMS)
        trailing = '{"items":[{"name":"X",},],}'
        self.assertTrue(pc.normalize(trailing, store=self.store)["ok"])

    def test_brand_official_site_allowed(self):
        block = {"items": [{"name": "Galaxy S25", "brand": "Samsung",
                            "offers": [{"url": "https://www.samsung.com/ru/smartphones/galaxy-s25/buy/"},
                                       {"url": "https://samsung-official.shop.example/s25"}]}]}
        offers = pc.normalize(block, store=self.store)["data"]["items"][0]["offers"]
        self.assertEqual([o["store_id"] for o in offers], ["official"])

    def test_locale_currency_defaults(self):
        en = pc.normalize({"items": [{"name": "X", "price": 99}]}, locale="en", store=self.store)["data"]
        self.assertEqual((en["locale"], en["currency"]), ("en", "USD"))
        self.assertEqual(en["items"][0]["price"]["currency"], "USD")


class ProvenanceStore(unittest.TestCase):
    def test_persist_ttl_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "product_cache.json"
            store = pc.Provenance(path, limit=3)
            for i in range(5):
                store.remember(f"https://ozon.ru/product/p-{i}/", {"price": {"amount": 100 + i, "currency": "RUB"}})
            store.save()
            again = pc.Provenance(path, limit=3)
            self.assertEqual(again.get("https://www.ozon.ru/product/p-4")["price"]["amount"], 104)
            self.assertEqual(again.get("https://ozon.ru/product/p-0/"), {})
            stale = pc.Provenance(path, ttl=0.0)
            self.assertEqual(stale.get("https://ozon.ru/product/p-4/"), {})

    def test_snippet_does_not_overwrite_page(self):
        store = pc.Provenance(None)
        store.remember("https://ozon.ru/product/a/", {"price": {"amount": 1000, "currency": "RUB"}, "title": "Page"})
        store.remember("https://ozon.ru/product/a/", {"title": "Snippet", "snippets": ["x"]}, kind="snippet")
        row = store.get("https://ozon.ru/product/a/")
        self.assertEqual((row["kind"], row["title"], row["snippets"]), ("page", "Page", ["x"]))


def _page_for(url: str) -> str:
    if "market.yandex.ru/search" in url:
        return fixture("market_search.html")
    if "dns-shop.ru/product" in url:
        return fixture("store_product_jsonld.html")
    if "ozon.ru/product" in url:
        return fixture("captcha.html")
    if "ixbt.com" in url:
        return '<html><head><meta property="og:image" content="https://www.ixbt.com/img/sn770-review.jpg"></head></html>'
    raise TimeoutError(url)


class Lookup(unittest.TestCase):
    def setUp(self):
        pc._LOOKUP_CACHE.clear()
        self.searches, self.fetches = [], []

    async def _search(self, query, count):
        self.searches.append(query)
        if "отзывы" in query:
            return [{"title": "Обзор WD Black SN770 1TB", "url": "https://www.ixbt.com/data/wd-black-sn770-1tb-review.html",
                     "snippet": "SN770 — быстрый и холодный накопитель."},
                    {"title": "WD — главная", "url": "https://www.westerndigital.com/", "snippet": "WD_BLACK"}]
        return [{"title": "1000 ГБ M.2 NVMe накопитель WD Black SN770 [WDS100T3X0E]",
                 "url": "https://www.dns-shop.ru/product/68427d56244fed20/1000-gb-m2-nvme-nakopitel-wd-black-sn770/",
                 "snippet": "Выгодные цены на WD Black SN770 в DNS"},
                {"title": "WD Black SN770 1TB — Ozon", "url": "https://www.ozon.ru/product/wd-black-sn770-1tb-774420811/",
                 "snippet": "WD Black SN770 1 ТБ — 9 990 ₽"},
                {"title": "WD Black SN770 1TB", "url": "https://www.citilink.ru/product/wd-black-sn770-1tb-1896886/",
                 "snippet": "SSD WD Black SN770 1TB — от 10 490 ₽"},
                {"title": "Категория SSD", "url": "https://www.ozon.ru/category/ssd/", "snippet": "WD Black SN770"},
                {"title": "local", "url": "http://127.0.0.1/admin", "snippet": "WD Black SN770 1TB"}]

    async def _fetch(self, url):
        self.fetches.append(url)
        return _page_for(url)

    def lookup(self, **kw):
        return run(pc.lookup("WD Black SN770 1TB", locale="ru", search=self._search, fetch=self._fetch,
                             store=pc.Provenance(None), use_cache=kw.pop("use_cache", False), **kw))

    def test_offers_from_pages_snippets_and_blocked(self):
        data = self.lookup()
        by_store = {o["store_id"]: o for o in data["offers"]}
        self.assertEqual(by_store["dns"]["price"], {"amount": 9499, "currency": "RUB"})
        self.assertEqual(by_store["dns"]["price_from"], "page")
        self.assertEqual(by_store["dns"]["rating"], 4.8)
        self.assertEqual(by_store["ozon"]["price_from"], "snippet")              # captcha page → snippet price
        self.assertIn("anti-bot", by_store["ozon"]["page"])
        self.assertEqual(by_store["citilink"]["price"]["amount"], 10490)          # timeout → snippet
        self.assertEqual(data["offers"][0]["price_from"], "page")                 # verified first
        self.assertEqual(data["currency"], "RUB")
        self.assertNotIn("http://127.0.0.1/admin", self.fetches)
        self.assertTrue(all("/category/" not in url for url in self.fetches))
        sources = [r["url"] for r in data["review_sources"]]
        self.assertIn("https://www.ixbt.com/data/wd-black-sn770-1tb-review.html", sources)
        self.assertNotIn("https://www.westerndigital.com/", sources)               # no model token
        self.assertIn("https://www.ixbt.com/img/sn770-review.jpg", [img["url"] for img in data["images"]])

    def test_market_search_page_is_read_directly(self):
        data = self.lookup()
        self.assertTrue(any(u.startswith("https://market.yandex.ru/search?text=") for u in self.fetches))
        # The fixture lists Samsung drives, which do not match this query.
        self.assertFalse(any(o["store_id"] == "yandex_market" for o in data["offers"]))

    def test_provenance_is_filled(self):
        store = pc.Provenance(None)
        run(pc.lookup("WD Black SN770 1TB", locale="ru", search=self._search, fetch=self._fetch, store=store, use_cache=False))
        row = store.get("https://www.dns-shop.ru/product/68427d56244fed20/1000-gb-m2-nvme-nakopitel-wd-black-sn770/")
        self.assertEqual(row["price"]["amount"], 9499)
        self.assertEqual(row["kind"], "page")
        self.assertIn("checked_at", row)
        ozon = store.get("https://www.ozon.ru/product/wd-black-sn770-1tb-774420811/")
        self.assertNotIn("price", ozon)                                            # snippet prices are not page facts
        self.assertEqual(ozon["kind"], "snippet")

    def test_cache_and_budget(self):
        first = self.lookup(use_cache=True)
        count = len(self.searches)
        second = self.lookup(use_cache=True)
        self.assertTrue(second.get("cached"))
        self.assertEqual(len(self.searches), count)
        self.assertEqual(first["offers"], second["offers"])

        async def slow_search(query, n):
            await asyncio.sleep(5)
            return []
        started = time.monotonic()
        data = run(pc.lookup("Some Phone 15", locale="en", search=slow_search, fetch=self._fetch,
                             store=pc.Provenance(None), use_cache=False, budget=1.5))
        self.assertLess(time.monotonic() - started, 4.5)
        self.assertEqual(data["offers"], [])
        self.assertTrue(any("search failed" in note or "No store page" in note for note in data["notes"]))
        self.assertEqual(data["currency"], "USD")

    def test_second_wave_when_no_product_pages(self):
        async def search(query, n):
            self.searches.append(query)
            if query.endswith("site:ozon.ru"):
                return [{"title": "WD Black SN770 1TB", "url": "https://www.ozon.ru/product/wd-black-sn770-1tb-1/",
                         "snippet": "9 990 ₽"}]
            return []
        data = run(pc.lookup("WD Black SN770 1TB", locale="ru", search=search, fetch=self._fetch,
                             store=pc.Provenance(None), use_cache=False))
        self.assertTrue(any(q.endswith(" site:ozon.ru") for q in self.searches))
        # Round 22: a site-specific query for EVERY regional store without a product page.
        for sid in pc.LOCALE_STORES["ru"]:
            self.assertTrue(any(q.endswith("site:" + pc.STORES[sid]["domains"][0]) for q in self.searches), sid)
        self.assertEqual(data["offers"][0]["store_id"], "ozon")

    def test_explicit_urls(self):
        async def none(query, n):
            return []
        data = run(pc.lookup("Anything 1", locale="ru", search=none, fetch=self._fetch, store=pc.Provenance(None),
                             use_cache=False, urls=["https://www.dns-shop.ru/product/x/y/", "file:///etc/passwd"]))
        self.assertEqual(data["offers"][0]["url"], "https://www.dns-shop.ru/product/x/y/")
        self.assertNotIn("file:///etc/passwd", self.fetches)


class TextFallback(unittest.TestCase):
    def test_markdown_fallback(self):
        store = _store_with_pages()
        pc._PROVENANCE = store
        try:
            text = "Вердикт.\n\n```memuai-products\n" + json.dumps(BLOCK, ensure_ascii=False) + "\n```\n\nХвост."
            out = pc.markdown_fallback(text, "ru")
            self.assertIn("**SSD A 1 ТБ** — 18\u00a0130\u00a0₽ · ★ 4,9 (954) — Лучший выбор", out)
            self.assertIn("Яндекс Маркет: 18\u00a0130\u00a0₽ — https://market.yandex.ru/card/ssd-a/1", out)
            self.assertIn("≈\u00a021\u00a0000\u00a0₽", out)
            self.assertNotIn("memuai-products", out)
            self.assertTrue(out.startswith("Вердикт.") and out.endswith("Хвост."))
            self.assertEqual(pc.markdown_fallback("A\n```memuai-products\n{\"items\": [{\"na", "ru"), "A")
            self.assertEqual(pc.markdown_fallback("A\n```memuai-products\nnot json\n```\nB", "ru"), "A\n\nB")
        finally:
            pc._PROVENANCE = None

    def test_format_price(self):
        self.assertEqual(pc.format_price({"amount": 366.5, "currency": "USD"}, "en"), "$366.50")
        self.assertEqual(pc.format_price({"amount": 24802, "currency": "RUB", "approx": True}, "ru"), "≈\u00a024\u00a0802\u00a0₽")


class PromptAndWiring(unittest.TestCase):
    def test_internal_prompt(self):
        row = next(item for item in pgdata.INTERNAL_PROMPTS if item["id"] == "product-cards")
        text = row["text"]
        self.assertIs(text, pgdata.PRODUCT_CARDS_PROMPT)
        self.assertTrue(row["name_ru"] and row["description_ru"])
        for needle in ("memuai-products", "product_lookup", "WHEN TO SHOW CARDS", "Do NOT show cards", "DATA HONESTY",
                       "approx", "No URL → no review", "rating_source", "Ozon, Wildberries, DNS, Яндекс Маркет, М.Видео, Ситилинк, Мегамаркет",
                       "Amazon, Best Buy, Walmart, Newegg, B&H Photo, eBay", "<product_cards_runtime>", "hard ceiling on VERIFIED prices", "RESEARCH FIRST, SILENTLY", "ONLY the memuai-tool block", "max_price",
                       "6-10 candidates", "THIS year", "store_status",
                       "WITHOUT TOOLS", "untrusted"):
            self.assertIn(needle, text)
        example = re.search(r"```memuai-products\n(.*?)\n```", text, re.S).group(1)
        self.assertTrue(pc.normalize(example.replace("…", "x"), store=pc.Provenance(None))["ok"])
        builtin = pgdata.builtin_prompt_set()
        self.assertIn("product-cards", [x["id"] for x in builtin["internal_prompts"]])
        # A user copy of the prompt set keeps the contract and its own edits.
        user = pgdata._normalize_prompt_set({"id": "mine", "internal_prompts": [{"id": "product-cards", "text": "custom"}]})
        self.assertEqual(next(x for x in user["internal_prompts"] if x["id"] == "product-cards")["text"], "custom")

    def test_preference(self):
        self.assertTrue(pgdata.DEFAULT_PREFERENCES["product_cards_enabled"])
        self.assertTrue(pgdata._normalize_preferences({})["product_cards_enabled"])
        self.assertFalse(pgdata._normalize_preferences({"product_cards_enabled": "0"})["product_cards_enabled"])

    def test_frontend_wiring(self):
        html = (PG / "index.html").read_text(encoding="utf-8")
        app = (PG / "app.js").read_text(encoding="utf-8")
        js = (PG / "products.js").read_text(encoding="utf-8")
        css = (PG / "products.css").read_text(encoding="utf-8")
        self.assertLess(html.index("/products.js"), html.index("/app.js"))
        self.assertIn("/products.css", html)
        self.assertIn('MemuProducts.placeholder(cleanCode', app)
        self.assertIn("MemuProducts.maskPending(value)", app)
        self.assertIn("MemuProducts.systemBlock({ lang: PG_LANG, lookup: !!assistant.webAllowed", app)
        self.assertIn('pref("product_cards_enabled", true) !== false', app)
        self.assertIn('"web_fetch", "product_lookup"', app)
        self.assertIn('name === "product_lookup") && assistant.webAllowed === false', app)
        self.assertIn('MemuProducts.stripForText(plain, PG_LANG === "en", "speech")', app)
        self.assertIn('id="st-products"', app)
        # Round 22: research progress instead of flickering drafts between tool rounds.
        self.assertIn("MemuProducts.holdStream(m, visibleAnswer(m.content", app)
        self.assertIn("MemuProducts.holdStream(current, answerText)", app)
        self.assertIn("MemuProducts.progress(cleanCode)", app)
        self.assertIn("assistant.productIntent = MemuProducts.intent(raw)", app)
        self.assertIn("m.streaming && m.progress && !productHold", app)
        self.assertIn('internalPromptText("product-cards")', js)
        self.assertIn("/api/pg/products/normalize", js)
        self.assertIn('target="_blank" rel="noopener noreferrer"', js)
        # Prefix of our own: panels.css (r21-panels) uses .mp-*.
        self.assertNotRegex(css, r"(?<![\w-])\.mp-")
        self.assertNotRegex(js, r'class="mp-')
        for design in ("glass", "grid", "focus"):
            self.assertIn(f':root[data-design="{design}"]', css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("body.reduce-motion", css)

    def test_system_block_shape(self):
        js = (PG / "products.js").read_text(encoding="utf-8")
        self.assertIn('<product_cards_runtime locale="${ru ? "ru" : "en"}" region="${ru ? "Russia" : "USA"}" '
                      'currency="${ru ? "RUB" : "USD"}" product_lookup="${tool}"/>', js)


class Endpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        cls.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def test_normalize_endpoint(self):
        response = self.client.post("/api/pg/products/normalize", json={"raw": json.dumps(BLOCK, ensure_ascii=False), "lang": "ru"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["data"]["items"]), 2)
        self.assertEqual(self.client.post("/api/pg/products/normalize", json={}).status_code, 400)
        self.assertFalse(self.client.post("/api/pg/products/normalize", json={"raw": "{}"}).json()["ok"])

    def test_lookup_tool_respects_web_switch(self):
        off = self.client.post("/api/pg/tools", json={"tool": "product_lookup", "args": {"query": "x y"}, "allow_web": False}).json()
        self.assertIn("error", off)
        short = self.client.post("/api/pg/tools", json={"tool": "product_lookup", "args": {"query": "x"}}).json()
        self.assertIn("error", short)




class TelegramFallback(unittest.TestCase):
    def test_bot_and_miniapp_render_products_as_text(self):
        from app import telegram_bot, tgapp_chat
        text = "Вот:\n```memuai-products\n" + json.dumps(BLOCK, ensure_ascii=False) + "\n```\nИтог."
        for out in (tgapp_chat.clean_answer(text), telegram_bot._clean_internal_protocols(text)):
            self.assertNotIn("memuai-products", out)
            self.assertNotIn('"items"', out)
            self.assertIn("Итог.", out)
            self.assertIn("SSD A", out)
        partial = telegram_bot._clean_internal_protocols("Вот:\n```memuai-products\n{\"items\": [", partial=True)
        self.assertEqual(partial, "Вот:")


# =========================================================================== Round 22

WB_JSON = json.dumps({"data": {"products": [
    {"id": 251234567, "brand": "WD", "name": "SSD Blue SN5000 1 ТБ M.2 NVMe", "salePriceU": 899000,
     "reviewRating": 4.8, "feedbacks": 312, "totalQuantity": 41},
    {"id": 999, "brand": "Noname", "name": "Чехол для SSD", "sizes": [{"price": {"product": 49900}}], "totalQuantity": 0},
]}}, ensure_ascii=False)


def _budget_store() -> pc.Provenance:
    store = pc.Provenance(None)
    now = int(time.time())
    store.remember("https://www.dns-shop.ru/product/a1/wd-blue-sn5000-1tb/",
                   {"title": "WD Blue SN5000 1 ТБ", "price": {"amount": 8999, "currency": "RUB"},
                    "availability": "in_stock", "checked_at": now})
    store.remember("https://www.ozon.ru/product/adata-legend-850-1tb-1/",
                   {"title": "ADATA Legend 850 1TB", "price": {"amount": 16357, "currency": "RUB"},
                    "availability": "in_stock", "checked_at": now})
    store.remember("https://www.citilink.ru/product/old-ssd-1tb-1/",
                   {"title": "Old SSD 1TB", "price": {"amount": 7000, "currency": "RUB"}, "availability": "in_stock",
                    "discontinued": True, "checked_at": now})
    return store


BUDGET_BLOCK = {
    "query": "SSD до 15 000 ₽", "locale": "ru", "currency": "RUB", "budget": {"amount": 15000, "currency": "RUB"},
    "items": [
        {"name": "ADATA Legend 850 1TB", "best": True, "badge": "Лучший выбор", "price": 14500,
         "offers": [{"url": "https://www.ozon.ru/product/adata-legend-850-1tb-1/", "price": 14500}]},
        {"name": "Kingston NV3 1 ТБ", "badge": "Дешевле всего", "price": {"amount": 6500, "approx": True}},
        {"name": "WD Blue SN5000 1 ТБ", "badge": "Для ноутбука",
         "offers": [{"url": "https://www.dns-shop.ru/product/a1/wd-blue-sn5000-1tb/", "price": 8999}]},
        {"name": "Old SSD 1TB", "offers": [{"url": "https://www.citilink.ru/product/old-ssd-1tb-1/", "price": 7000}]},
    ],
}


class Round22Budget(unittest.TestCase):
    """A budget is a hard ceiling on VERIFIED in-stock offer prices."""

    def setUp(self):
        result = pc.normalize(json.dumps(BUDGET_BLOCK, ensure_ascii=False), locale="ru", store=_budget_store())
        self.assertTrue(result["ok"], result)
        self.data = result["data"]
        self.items = {item["name"]: item for item in self.data["items"]}
        self.warnings = result["warnings"]

    def test_budget_kept_on_the_block(self):
        self.assertEqual(self.data["budget"], {"amount": 15000, "currency": "RUB"})

    def test_over_budget_item_is_marked_and_loses_best_pick(self):
        adata = self.items["ADATA Legend 850 1TB"]
        self.assertEqual(adata["budget_status"], "over")              # 16 357 ₽ on the page, not the claimed 14 500
        self.assertEqual(adata["price"]["amount"], 16357)
        self.assertNotIn("best", adata)
        self.assertNotIn("badge", adata)                               # «Лучший выбор» must not stay on it
        self.assertTrue(any("best pick moved" in w for w in self.warnings))

    def test_verified_in_budget_item_becomes_best(self):
        wd = self.items["WD Blue SN5000 1 ТБ"]
        self.assertEqual(wd["budget_status"], "within")
        self.assertTrue(wd["best"])
        self.assertEqual(wd["badge"], "Лучший выбор")                  # promoted: the badge says so
        self.assertEqual(self.data["items"][0]["name"], "WD Blue SN5000 1 ТБ")

    def test_estimate_only_item_is_unverified_not_within(self):
        nv3 = self.items["Kingston NV3 1 ТБ"]
        self.assertEqual(nv3["budget_status"], "unverified")
        self.assertTrue(nv3["price"]["approx"])

    def test_discontinued_item_is_unavailable_and_last(self):
        old = self.items["Old SSD 1TB"]
        self.assertEqual(old["availability"], "unavailable")
        self.assertTrue(old["discontinued"])
        self.assertNotEqual(old.get("budget_status"), "within")
        self.assertEqual(self.data["items"][-1]["name"], "Old SSD 1TB")
        self.assertEqual([item["id"] for item in self.data["items"]], ["p1", "p2", "p3", "p4"])

    def test_order_within_unverified_over_unavailable(self):
        self.assertEqual([item["name"] for item in self.data["items"]],
                         ["WD Blue SN5000 1 ТБ", "Kingston NV3 1 ТБ", "ADATA Legend 850 1TB", "Old SSD 1TB"])

    def test_renormalizing_is_stable(self):
        again = pc.normalize(self.data, locale="ru", store=_budget_store())["data"]
        self.assertEqual([(i["name"], i.get("best"), i.get("budget_status")) for i in again["items"]],
                         [(i["name"], i.get("best"), i.get("budget_status")) for i in self.data["items"]])

    def test_no_fitting_item_means_no_best_pick(self):
        block = {**BUDGET_BLOCK, "items": [BUDGET_BLOCK["items"][0], BUDGET_BLOCK["items"][1]]}
        data = pc.normalize(block, locale="ru", store=_budget_store())["data"]
        self.assertFalse(any(item.get("best") for item in data["items"]))

    def test_markdown_says_over_budget(self):
        text = pc.to_markdown(self.data, "ru")
        self.assertIn("ADATA Legend 850 1TB** — 16\u00a0357\u00a0₽ — выше бюджета", text)
        self.assertIn("Old SSD 1TB", text)
        self.assertIn("нет в продаже", text)
        self.assertIn("цена не подтверждена", text)

    def test_without_budget_nothing_is_flagged(self):
        block = {k: v for k, v in BUDGET_BLOCK.items() if k != "budget"}
        data = pc.normalize(block, locale="ru", store=_budget_store())["data"]
        self.assertFalse(any("budget_status" in item for item in data["items"]))
        self.assertNotIn("budget", data)
        # …but an unavailable item still cannot be the best pick
        self.assertEqual(next(i for i in data["items"] if i.get("best"))["name"], "ADATA Legend 850 1TB")


class Round22Availability(unittest.TestCase):
    def test_discontinued_text(self):
        self.assertTrue(pc.page_discontinued("<html><body><div>Товар снят с производства</div></body></html>"))
        self.assertTrue(pc.page_discontinued("<p>This item is no longer available.</p>"))
        self.assertFalse(pc.page_discontinued("<script>var t='discontinued'</script><p>В наличии</p>"))

    def test_latest_review(self):
        self.assertEqual(pc.latest_review_date([{"date": "2024-03-01"}, {"date": "2025-07-09T10:00"}, {"date": ""}]),
                         "2025-07-09")
        self.assertEqual(pc.latest_review_date([]), "")

    def test_wildberries_api_rows(self):
        rows = pc._wb_products(WB_JSON)
        self.assertEqual(rows[0]["url"], "https://www.wildberries.ru/catalog/251234567/detail.aspx")
        self.assertEqual(rows[0]["price"], {"amount": 8990, "currency": "RUB"})
        self.assertEqual(rows[0]["availability"], "in_stock")
        self.assertEqual((rows[0]["rating"], rows[0]["review_count"]), (4.8, 312))
        self.assertEqual(rows[1]["price"]["amount"], 499)
        self.assertEqual(rows[1]["availability"], "out_of_stock")
        self.assertEqual(pc._wb_products("<html>blocked</html>"), [])


class Round22StoreCoverage(unittest.TestCase):
    """Every regional store is attempted and gets an honest status."""

    def setUp(self):
        pc._LOOKUP_CACHE.clear()
        self.searches, self.fetches = [], []

    async def _search(self, query, count):
        self.searches.append(query)
        if "site:dns-shop.ru" in query or query.endswith("купить"):
            return [{"title": "SSD WD Blue SN5000 1 ТБ [WDS100T4B0E]", "url": "https://www.dns-shop.ru/product/a1/wd-blue-sn5000-1tb/",
                     "snippet": "WD Blue SN5000 1 ТБ — в DNS"}]
        if "site:ozon.ru" in query:
            return [{"title": "WD Blue SN5000 1 ТБ — Ozon", "url": "https://www.ozon.ru/product/wd-blue-sn5000-1tb-55/",
                     "snippet": "WD Blue SN5000 1 ТБ — 9 450 ₽"}]
        return []

    async def _fetch(self, url):
        self.fetches.append(url)
        if url.startswith("https://search.wb.ru/"):
            return WB_JSON
        if "dns-shop.ru/product" in url:
            return ('<html><head><script type="application/ld+json">{"@type":"Product","name":"WD Blue SN5000 1 ТБ",'
                    '"offers":{"@type":"Offer","price":"8999","priceCurrency":"RUB","availability":"https://schema.org/InStock"}}'
                    '</script></head><body>WD Blue SN5000</body></html>')
        if "ozon.ru" in url:
            return fixture("captcha.html")
        raise TimeoutError(url)

    def lookup(self, **kw):
        store = kw.pop("store", None) or pc.Provenance(None)
        return run(pc.lookup("WD Blue SN5000 1TB", locale="ru", search=self._search, fetch=self._fetch, store=store,
                             use_cache=False, **kw)), store

    def test_every_store_is_attempted_and_reported(self):
        data, _ = self.lookup()
        statuses = {row["store_id"]: row["status"] for row in data["store_status"]}
        self.assertEqual(list(statuses), list(pc.LOCALE_STORES["ru"]))
        for sid in pc.LOCALE_STORES["ru"]:
            self.assertTrue(any(u.startswith(pc.STORE_SEARCH[sid].split("{q}")[0]) for u in self.fetches), sid)
        self.assertEqual(statuses["dns"], "found")
        self.assertEqual(statuses["wildberries"], "found")             # public JSON of the store
        self.assertEqual(statuses["ozon"], "approx")                   # captcha page, price only from the snippet
        for sid in ("mvideo", "citilink", "megamarket", "yandex_market"):
            self.assertIn(statuses[sid], ("error", "not_found", "blocked"), sid)
        wb = next(o for o in data["offers"] if o["store_id"] == "wildberries")
        self.assertEqual((wb["price"]["amount"], wb["price_from"]), (8990, "api"))
        self.assertEqual(data["signals"]["verified_offers"], 2)

    def test_budget_flags_on_offers(self):
        data, _ = self.lookup(max_price="9 000 ₽")
        by_store = {o["store_id"]: o for o in data["offers"]}
        self.assertIs(by_store["dns"]["within_budget"], True)
        self.assertIs(by_store["wildberries"]["within_budget"], True)
        self.assertEqual(by_store["ozon"]["within_budget"], "unverified")
        self.assertEqual(data["signals"]["budget"], {"max": 9000, "currency": "RUB", "verified_within": 2})
        tight, _ = self.lookup(max_price=5000)
        self.assertEqual(tight["signals"]["budget"]["verified_within"], 0)
        self.assertTrue(any("must NOT be presented as fitting the budget" in n for n in tight["notes"]))

    def test_cards_show_unchecked_stores(self):
        _, store = self.lookup()
        block = {"locale": "ru", "currency": "RUB", "items": [
            {"name": "WD Blue SN5000 1 ТБ", "model": "WDS100T4B0E", "best": True,
             "offers": [{"url": "https://www.dns-shop.ru/product/a1/wd-blue-sn5000-1tb/", "price": 8999}]}]}
        item = pc.normalize(block, locale="ru", store=store)["data"]["items"][0]
        # Wildberries was found by the lookup with the store's own price: added as a verified offer
        # even though the model did not list it.
        wb = next(o for o in item["offers"] if o["store_id"] == "wildberries")
        self.assertEqual((wb["price"]["amount"], wb["verified"], wb["added"]), (8990, True, "lookup"))
        self.assertEqual(wb["url"], "https://www.wildberries.ru/catalog/251234567/detail.aspx")
        checks = {row["store_id"]: row for row in item["store_checks"]}
        self.assertEqual(set(checks), set(pc.LOCALE_STORES["ru"]) - {"dns", "wildberries"})
        self.assertEqual(checks["ozon"]["status"], "unconfirmed")      # found in search, price not confirmed
        self.assertEqual(checks["ozon"]["url"], "https://www.ozon.ru/product/wd-blue-sn5000-1tb-55/")
        self.assertEqual(checks["ozon"]["color"], "#005bff")
        for sid in ("mvideo", "citilink", "megamarket", "yandex_market"):
            self.assertIn(checks[sid]["status"], ("unchecked", "not_found"), sid)
            self.assertNotIn("url", checks[sid])
        self.assertTrue(checks["citilink"]["search_url"].startswith("https://www.citilink.ru/search/?text=WD+Blue"))
        text = pc.to_markdown({"items": [item]}, "ru")
        self.assertIn("Ozon: найден, цена не подтверждена — https://www.ozon.ru/product/wd-blue-sn5000-1tb-55/", text)
        self.assertRegex(text, r"М\.Видео: (не удалось проверить|не найден)")

    def test_lookup_memory_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            first = pc.Provenance(path)
            first.remember_lookup("WD Blue SN5000 1TB", "ru", [{"store_id": "ozon", "status": "blocked"},
                                                              {"store_id": "bogus", "status": "found"}])
            first.save()
            second = pc.Provenance(path)
            row = second.lookup_for("WD Blue SN5000 1 TB", "ru")
            self.assertEqual(row["stores"], [{"store_id": "ozon", "status": "blocked"}])
            self.assertIsNone(second.lookup_for("Samsung 990 Pro 2TB", "ru"))
            self.assertIsNone(second.lookup_for("WD Blue SN5000 1TB", "en"))


if __name__ == "__main__":
    unittest.main()
