# -*- coding: utf-8 -*-
"""Карточки товаров в чате MemuAI (Раунд 21, по мотивам ChatGPT Shopping).

Три части, без зависимостей от HTTP-слоя приложения (их подставляет
``playground_api``):

* **Магазины** — ``STORES`` и ``LOCALE_STORES``: какие магазины показываются
  для русского и английского интерфейса. Магазин оффера всегда определяется по
  домену ссылки, а не по тому, как его назвала модель: ссылку на чужой сайт
  нельзя подписать «Ozon».
* **Сбор данных** — ``lookup()``: инструмент модели ``product_lookup``. Ищет
  товар в магазинах выбранного региона, параллельно открывает страницы и
  достаёт из них цену, картинки, рейтинг и отзывы (JSON-LD schema.org
  Product/Offer/AggregateRating/Review, микроразметка, og:-теги, отдельный
  разбор Amazon). Всё увиденное записывается в ``Provenance`` — журнал
  «что мы действительно видели на какой странице».
* **Проверка** — ``normalize()``: блок ```memuai-products, который модель
  пишет в ответе, перед показом проходит через сервер. Ссылки — только
  публичные http(s), магазины — только из списка, отзыв без ссылки
  выбрасывается, цена без подтверждения помечается «≈», а подтверждённая
  берётся со страницы магазина, а не из текста модели.

Системный промпт фичи живёт в ``pgdata.INTERNAL_PROMPTS`` под id
``product-cards`` (Настройки → Системные промпты), интерфейс — ``pgstatic/products.js``.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin, urlparse

log = logging.getLogger("memuai.products")

SCHEMA_VERSION = 1
MAX_ITEMS = 6
MAX_OFFERS = 8
MAX_IMAGES = 8
MAX_REVIEWS = 6
MAX_RAW_CHARS = 200_000

# --------------------------------------------------------------------------- stores

# product — признак страницы товара в адресе (не каталога и не поиска).
STORES: Dict[str, Dict[str, Any]] = {
    # Россия
    "ozon": {"name": "Ozon", "domains": ("ozon.ru", "ozon.by", "ozon.kz", "ozon.com"),
             "product": r"/product/", "color": "#005bff"},
    "wildberries": {"name": "Wildberries", "domains": ("wildberries.ru", "wildberries.by", "wildberries.kz", "wb.ru"),
                    "product": r"/catalog/\d+/detail", "color": "#a73afd"},
    "dns": {"name": "DNS", "domains": ("dns-shop.ru",), "product": r"/product/", "color": "#fc8507"},
    "yandex_market": {"name": "Яндекс Маркет", "name_en": "Yandex Market", "domains": ("market.yandex.ru",),
                      "product": r"/(?:card|product--|product)/", "color": "#ffcc00"},
    "mvideo": {"name": "М.Видео", "name_en": "M.Video", "domains": ("mvideo.ru",), "product": r"/products/",
               "color": "#e30613"},
    "citilink": {"name": "Ситилинк", "name_en": "Citilink", "domains": ("citilink.ru",), "product": r"/product/",
                 "color": "#ff6600"},
    "megamarket": {"name": "Мегамаркет", "name_en": "Megamarket", "domains": ("megamarket.ru", "sbermegamarket.ru"),
                   "product": r"/catalog/details/", "color": "#8654cc"},
    "eldorado": {"name": "Эльдорадо", "name_en": "Eldorado", "domains": ("eldorado.ru",), "product": r"/cat/detail/",
                 "color": "#c8102e"},
    "aliexpress": {"name": "AliExpress", "domains": ("aliexpress.ru", "aliexpress.com", "aliexpress.us"),
                   "product": r"/item/", "color": "#e43225"},
    "lamoda": {"name": "Lamoda", "domains": ("lamoda.ru",), "product": r"/p/", "color": "#000000"},
    "onlinetrade": {"name": "ОНЛАЙН ТРЕЙД.РУ", "name_en": "Onlinetrade", "domains": ("onlinetrade.ru",),
                    "product": r"/catalogue/.+\.html", "color": "#f05a28"},
    "regard": {"name": "Регард", "name_en": "Regard", "domains": ("regard.ru",), "product": r"/product/",
               "color": "#d71920"},
    "restore": {"name": "re:Store", "domains": ("re-store.ru",), "product": r"/catalog/", "color": "#111111"},
    # США и англоязычные
    "amazon": {"name": "Amazon", "domains": ("amazon.com", "amazon.co.uk", "amazon.ca", "amazon.de", "amazon.com.au",
                                            "amazon.in", "amazon.fr", "amazon.it", "amazon.es", "amzn.to"),
               "product": r"/(?:dp|gp/product|gp/aw/d)/", "color": "#ff9900"},
    "bestbuy": {"name": "Best Buy", "domains": ("bestbuy.com", "bestbuy.ca"), "product": r"/site/|/product/",
                "color": "#0046be"},
    "walmart": {"name": "Walmart", "domains": ("walmart.com", "walmart.ca"), "product": r"/ip/", "color": "#0071dc"},
    "newegg": {"name": "Newegg", "domains": ("newegg.com", "newegg.ca"), "product": r"/p/|/Product/",
               "color": "#f7a21b"},
    "bh": {"name": "B&H Photo", "domains": ("bhphotovideo.com",), "product": r"/c/product/", "color": "#cc0000"},
    "ebay": {"name": "eBay", "domains": ("ebay.com", "ebay.co.uk", "ebay.de", "ebay.ca"), "product": r"/itm/",
             "color": "#e53238"},
    "target": {"name": "Target", "domains": ("target.com",), "product": r"/p/", "color": "#cc0000"},
    "costco": {"name": "Costco", "domains": ("costco.com",), "product": r"\.product\.", "color": "#005daa"},
    "adorama": {"name": "Adorama", "domains": ("adorama.com",), "product": r"/[a-z0-9]+\.html", "color": "#000000"},
    "microcenter": {"name": "Micro Center", "domains": ("microcenter.com",), "product": r"/product/",
                    "color": "#c8102e"},
}

# Главные магазины региона (их ищем); остальные из STORES тоже разрешены в
# карточках, если модель нашла товар там.
LOCALE_STORES: Dict[str, Tuple[str, ...]] = {
    "ru": ("ozon", "wildberries", "dns", "yandex_market", "mvideo", "citilink", "megamarket"),
    "en": ("amazon", "bestbuy", "walmart", "newegg", "bh", "ebay"),
}
LOCALE_CURRENCY = {"ru": "RUB", "en": "USD"}

# Сайты производителей: оффер с официального сайта бренда тоже допустим.
# Сравнение — по метке домена (samsung.com, store.samsung.com, apple.com/ru).
_BRAND_SLUG_RE = re.compile(r"[^a-z0-9]+")

CURRENCIES = {"RUB", "USD", "EUR", "GBP", "KZT", "BYN", "UAH", "CAD", "AUD", "CNY", "JPY", "INR", "TRY", "AMD",
              "UZS", "GEL", "PLN", "CHF"}
_CURRENCY_SIGNS = (
    (re.compile(r"\bBYN(?![A-Za-z])|бел\.?\s*руб", re.I), "BYN"),
    (re.compile(r"₽|руб|(?<![A-Za-zА-Яа-яЁё])р\.|(?<![A-Za-z])RU[BR](?![A-Za-z])", re.I), "RUB"),
    (re.compile(r"US\$|\$|(?<![A-Za-z])USD(?![A-Za-z])", re.I), "USD"),
    (re.compile(r"€|(?<![A-Za-z])EUR(?![A-Za-z])", re.I), "EUR"),
    (re.compile(r"£|(?<![A-Za-z])GBP(?![A-Za-z])", re.I), "GBP"),
    (re.compile(r"₸|(?<![A-Za-z])KZT(?![A-Za-z])|тенге", re.I), "KZT"),
    (re.compile(r"₴|(?<![A-Za-z])UAH(?![A-Za-z])|грн", re.I), "UAH"),
)


def locale_of(value: Any) -> str:
    return "en" if str(value or "").lower().startswith("en") else "ru"


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def store_for_url(url: str) -> str:
    """Id магазина по домену ссылки ('' — не магазин из списка)."""
    host = _host(url)
    if not host:
        return ""
    for store_id, spec in STORES.items():
        for domain in spec["domains"]:
            if host == domain or host.endswith("." + domain):
                return store_id
    return ""


def store_name(store_id: str, locale: str = "ru") -> str:
    spec = STORES.get(store_id) or {}
    if locale == "en" and spec.get("name_en"):
        return spec["name_en"]
    return spec.get("name") or store_id


def is_product_page(url: str) -> bool:
    store_id = store_for_url(url)
    if not store_id:
        return False
    try:
        path = urlparse(url).path or "/"
    except ValueError:
        return False
    return bool(re.search(STORES[store_id]["product"], path, re.I))


def _brand_official(url: str, brand: str) -> bool:
    slug = _BRAND_SLUG_RE.sub("", str(brand or "").lower())
    if len(slug) < 3:
        return False
    host = _host(url)
    labels = host.split(".")
    return any(_BRAND_SLUG_RE.sub("", label) == slug for label in labels[:-1])


def public_url(url: Any) -> str:
    """Публичный http(s)-адрес или ''. Без localhost, частных сетей и учётных данных."""
    value = str(url or "").strip()
    if not value or len(value) > 2000 or any(ch in value for ch in "\r\n\t \"'<>`"):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return ""
    try:
        from .websearch import _public_url
        if not _public_url(value):
            return ""
    except Exception:  # pragma: no cover - модуль поиска недоступен
        host = parsed.hostname.lower()
        if host in ("localhost",) or host.endswith((".local", ".localhost", ".internal")) or re.fullmatch(r"[\d.]+", host):
            return ""
    return value


def url_key(url: str) -> str:
    """Ключ страницы для журнала: хост без www + путь без хвостового «/».

    Параметры отбрасываются (метки рекламы, сессии), кроме тех, без которых
    адрес не указывает на товар (eBay ?item=, Walmart ?selected= и т. п. —
    им хватает пути).
    """
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    if store_for_url(url) == "amazon":
        # /Some-Name/dp/B0ABC/ref=... и /dp/B0ABC — одна страница.
        match = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", parsed.path or "", re.I)
        if match:
            return f"{host}/dp/{match.group(1).upper()}"
    return host + path


# --------------------------------------------------------------------------- text and numbers

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def clean_text(value: Any, limit: int) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    text = html_lib.unescape(str(value))
    text = _TAG_RE.sub(" ", text)
    text = _CTRL_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text).strip()
    if len(text) > limit:
        cut = text[: limit - 1]
        space = cut.rfind(" ")
        text = (cut[:space] if space > limit * 0.6 else cut).rstrip(" ,.;:-") + "…"
    return text


def currency_of(text: Any, default: str = "") -> str:
    value = str(text or "").strip()
    if value.upper() in CURRENCIES:
        return value.upper()
    if value.upper() == "RUR":
        return "RUB"
    for pattern, code in _CURRENCY_SIGNS:
        if pattern.search(value):
            return code
    return default


def parse_amount(value: Any) -> Optional[float]:
    """«26 699 ₽», «$1,299.99», «1.299,00 €», 26699 → число или None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).replace("\u00a0", " ").replace("\u202f", " ").replace("\u2009", " ")
        match = re.search(r"\d[\d\s.,']*", text)
        if not match:
            return None
        raw = re.sub(r"[\s']", "", match.group(0)).rstrip(".,")
        if "," in raw and "." in raw:
            if raw.rfind(",") > raw.rfind("."):
                raw = raw.replace(".", "").replace(",", ".")
            else:
                raw = raw.replace(",", "")
        elif "," in raw:
            head, _, tail = raw.rpartition(",")
            raw = (head.replace(",", "") + "." + tail) if len(tail) in (1, 2) and raw.count(",") == 1 \
                else raw.replace(",", "")
        elif raw.count(".") > 1 or (raw.count(".") == 1 and len(raw.rpartition(".")[2]) == 3
                                     and len(raw.partition(".")[0]) <= 3 and not raw.startswith("0")):
            # 1.299 / 12.990.000 — разделители тысяч (европейская запись).
            raw = raw.replace(".", "")
        try:
            number = float(raw)
        except ValueError:
            return None
    if not (number > 0) or number > 100_000_000 or number != number:
        return None
    return round(number, 2)


_SNIPPET_PRICE_RE = re.compile(
    r"(?:(?:US\$|\$|€|£)\s?\d(?:[\d,.]*\d)?|\d(?:[\d\s\u00a0\u202f.,]*\d)?\s?(?:₽|руб\.?|р\.|€|₸|USD|EUR|RUB)"
    r"(?![A-Za-zА-Яа-я]))",
    re.I)


def snippet_price(text: str) -> Optional[Dict[str, Any]]:
    """Первая правдоподобная цена в тексте выдачи («от 12 990 ₽», «$89.99»)."""
    for match in _SNIPPET_PRICE_RE.finditer(str(text or "")):
        chunk = match.group(0)
        amount = parse_amount(chunk)
        currency = currency_of(chunk)
        if amount and currency and amount >= 1:
            # «4.8 $» и «1 р.» не цены; рубли меньше 50 — почти всегда мусор.
            if currency == "RUB" and amount < 50:
                continue
            return {"amount": amount, "currency": currency}
    return None


def _rating(value: Any, best: Any = None) -> Optional[float]:
    number = parse_amount(value) if not isinstance(value, (int, float)) else float(value)
    if number is None or isinstance(value, bool):
        return None
    scale = parse_amount(best) if best not in (None, "") else None
    if scale and scale > 0 and scale != 5:
        number = number * 5.0 / scale
    elif number > 5 and number <= 10:
        number = number / 2
    elif number > 10 and number <= 100:
        number = number / 20
    if not (0 < number <= 5):
        return None
    return round(number, 2)


def _count(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
    else:
        digits = re.sub(r"[^\d]", "", str(value or ""))
        if not digits or len(digits) > 9:
            return None
        number = int(digits)
    return number if 0 <= number < 1_000_000_000 else None


_AVAILABILITY = (
    ("out_of_stock", re.compile(r"OutOfStock|SoldOut|Discontinued|нет в наличии|распродан|out of stock|unavailable|"
                                r"currently unavailable|нет на складе", re.I)),
    ("preorder", re.compile(r"PreOrder|PreSale|BackOrder|предзаказ|pre-?order|под заказ", re.I)),
    ("in_stock", re.compile(r"InStock|InStoreOnly|OnlineOnly|LimitedAvailability|в наличии|in stock|есть в наличии|"
                            r"available", re.I)),
)


def availability_of(value: Any) -> str:
    text = str(value or "")
    if text in ("in_stock", "out_of_stock", "preorder", "unknown"):
        return text
    for code, pattern in _AVAILABILITY:
        if pattern.search(text):
            return code
    return "unknown"


# --------------------------------------------------------------------------- page extraction

_LD_RE = re.compile(r"<script\b[^>]*type\s*=\s*['\"]?application/ld\+json['\"]?[^>]*>(.*?)</script>", re.I | re.S)
_META_RE = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR_RE = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
_ITEMPROP_RE = re.compile(r"<[a-z][a-z0-9]*\b[^>]*\bitemprop\s*=\s*['\"]?([a-zA-Z]+)['\"]?[^>]*>", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _attrs(tag: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, value in _ATTR_RE.findall(tag):
        if value[:1] in "\"'":
            value = value[1:-1]
        out[name.lower()] = html_lib.unescape(value)
    return out


def _loads_lenient(text: str) -> Any:
    body = text.strip()
    body = re.sub(r"^\s*<!--|-->\s*$", "", body).strip()
    body = re.sub(r"^\s*//\s*<!\[CDATA\[|//\s*\]\]>\s*$", "", body).strip()
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        pass
    # Хвостовые запятые и управляющие символы внутри строк — частые ошибки CMS.
    fixed = re.sub(r",\s*([}\]])", r"\1", body)
    fixed = re.sub(r"[\x00-\x1f]", " ", fixed)
    try:
        return json.loads(fixed)
    except ValueError:
        pass
    # Несколько объектов подряд в одном <script>.
    decoder, out, index = json.JSONDecoder(), [], 0
    while index < len(fixed):
        while index < len(fixed) and fixed[index] in " \n\r\t,;":
            index += 1
        if index >= len(fixed):
            break
        try:
            value, index = decoder.raw_decode(fixed, index)
        except ValueError:
            break
        out.append(value)
    return out or None


def _types(node: Dict[str, Any]) -> List[str]:
    value = node.get("@type")
    values = value if isinstance(value, list) else [value]
    return [str(item).rsplit("/", 1)[-1] for item in values if item]


def _walk(value: Any, depth: int = 0) -> Iterable[Dict[str, Any]]:
    if depth > 12:
        return
    if isinstance(value, dict):
        yield value
        for key, item in value.items():
            if key in ("@context",):
                continue
            yield from _walk(item, depth + 1)
    elif isinstance(value, list):
        for item in value[:400]:
            yield from _walk(item, depth + 1)


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _text_of(value: Any) -> str:
    value = _first(value)
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value") or value.get("text") or ""
    return str(value or "")


def _images_of(value: Any, base: str) -> List[str]:
    out: List[str] = []
    items = value if isinstance(value, list) else [value]
    for item in items[:20]:
        if isinstance(item, dict):
            item = item.get("contentUrl") or item.get("url") or item.get("@id") or ""
        if not isinstance(item, str):
            continue
        url = public_url(urljoin(base, item.strip())) if item.strip() else ""
        if url and url not in out:
            out.append(url)
    return out


PRODUCT_TYPES = {"Product", "ProductGroup", "IndividualProduct", "ProductModel", "SomeProducts", "Vehicle",
                 "Book", "SoftwareApplication", "VideoGame"}


def _offer_fields(offers: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    candidates = offers if isinstance(offers, list) else [offers]
    prices: List[Tuple[float, str, str, str]] = []
    for offer in candidates[:50]:
        if not isinstance(offer, dict):
            continue
        nested = offer.get("offers")
        if nested and nested is not offer:
            sub = _offer_fields(nested)
            if sub.get("price"):
                prices.append((sub["price"], sub.get("currency", ""), sub.get("availability", "unknown"),
                               sub.get("url", "")))
        spec = _first(offer.get("priceSpecification"))
        amount = parse_amount(offer.get("price"))
        if amount is None:
            amount = parse_amount(offer.get("lowPrice"))
        if amount is None and isinstance(spec, dict):
            amount = parse_amount(spec.get("price"))
        spec_currency = spec.get("priceCurrency") if isinstance(spec, dict) else ""
        currency = currency_of(offer.get("priceCurrency") or spec_currency or "")
        if amount is not None:
            prices.append((amount, currency, availability_of(offer.get("availability")),
                           str(offer.get("url") or "")))
        elif offer.get("availability") and "availability" not in out:
            out["availability"] = availability_of(offer.get("availability"))
    if prices:
        in_stock = [row for row in prices if row[2] != "out_of_stock"] or prices
        best = min(in_stock, key=lambda row: row[0])
        out.update({"price": best[0], "currency": best[1], "availability": best[2]})
        if best[3]:
            out["url"] = best[3]
        if len(prices) > 1:
            out["offer_count"] = len(prices)
    return out


def _reviews_of(value: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    items = value if isinstance(value, list) else [value]
    for item in items[:30]:
        if not isinstance(item, dict):
            continue
        text = clean_text(item.get("reviewBody") or item.get("description") or item.get("text"), 400)
        if len(text) < 8:
            continue
        rating_node = item.get("reviewRating") if isinstance(item.get("reviewRating"), dict) else {}
        out.append({
            "author": clean_text(_text_of(item.get("author")), 60),
            "rating": _rating(rating_node.get("ratingValue"), rating_node.get("bestRating")),
            "text": text,
            "date": clean_text(item.get("datePublished"), 20),
        })
    return out


def _product_from_ld(node: Dict[str, Any], base: str) -> Dict[str, Any]:
    brand = _text_of(node.get("brand") or node.get("manufacturer"))
    out: Dict[str, Any] = {
        "title": clean_text(_text_of(node.get("name")), 240),
        "brand": clean_text(brand, 60),
        "sku": clean_text(_first(node.get("mpn") or node.get("sku")
                                 or (node.get("model") if isinstance(node.get("model"), str) else "")), 60),
        "description": clean_text(node.get("description"), 600),
        "images": _images_of(node.get("image"), base),
    }
    offers = _offer_fields(node.get("offers")) if node.get("offers") else {}
    if not offers and isinstance(node.get("hasVariant"), list):
        offers = _offer_fields([{"offers": v.get("offers")} for v in node["hasVariant"][:30]
                                if isinstance(v, dict) and v.get("offers")])
    if offers.get("price"):
        out["price"] = {"amount": offers["price"], "currency": offers.get("currency", "")}
    if offers.get("availability"):
        out["availability"] = offers["availability"]
    if offers.get("url"):
        url = public_url(urljoin(base, offers["url"]))
        if url:
            out["offer_url"] = url
    rating = node.get("aggregateRating") if isinstance(node.get("aggregateRating"), dict) else {}
    if rating:
        out["rating"] = _rating(rating.get("ratingValue"), rating.get("bestRating"))
        out["review_count"] = _count(rating.get("reviewCount") or rating.get("ratingCount"))
    reviews = _reviews_of(node.get("review") or node.get("reviews"))
    if reviews:
        out["reviews"] = reviews
    url = node.get("url") or node.get("@id")
    if isinstance(url, str) and url.startswith(("http://", "https://", "/")):
        resolved = public_url(urljoin(base, url))
        if resolved:
            out["url"] = resolved
    return {key: value for key, value in out.items() if value not in (None, "", [], {})}


def _metas(html: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for tag in _META_RE.findall(html[:600_000]):
        attrs = _attrs(tag)
        key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        if key and "content" in attrs:
            out.setdefault(key, []).append(attrs["content"])
    return out


def _microdata(html: str, base: str) -> Dict[str, Any]:
    values: Dict[str, List[str]] = {}
    for match in _ITEMPROP_RE.finditer(html[:1_500_000]):
        attrs = _attrs(match.group(0))
        prop = match.group(1)
        value = attrs.get("content") or attrs.get("src") or attrs.get("href") or attrs.get("data-src") or ""
        if not value:
            # <span itemprop="price">12 990</span>
            tail = html[match.end(): match.end() + 200]
            value = clean_text(tail.split("<", 1)[0], 120)
        if value:
            values.setdefault(prop, []).append(value)
    out: Dict[str, Any] = {}
    amount = next((parse_amount(v) for v in values.get("price", []) if parse_amount(v)), None)
    if amount:
        out["price"] = {"amount": amount, "currency": currency_of(next(iter(values.get("priceCurrency", [])), ""))}
    if values.get("ratingValue"):
        out["rating"] = _rating(values["ratingValue"][0], next(iter(values.get("bestRating", [])), None))
    count = values.get("reviewCount") or values.get("ratingCount")
    if count:
        out["review_count"] = _count(count[0])
    if values.get("availability"):
        out["availability"] = availability_of(values["availability"][0])
    images = _images_of([v for v in values.get("image", []) if not v.startswith("data:")], base)
    if images:
        out["images"] = images
    return {key: value for key, value in out.items() if value not in (None, "", [], {})}


def _amazon(html: str, base: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    title = re.search(r'id="productTitle"[^>]*>(.*?)</span>', html, re.S)
    if title:
        out["title"] = clean_text(title.group(1), 240)
    images: List[str] = []
    for pattern in (r'"hiRes":"(https://[^"]+)"', r'"large":"(https://[^"]+)"', r'data-old-hires="(https://[^"]+)"'):
        for url in re.findall(pattern, html):
            url = public_url(url)
            if url and url not in images:
                images.append(url)
        if len(images) >= 6:
            break
    if images:
        out["images"] = images[:MAX_IMAGES]
    core = re.search(r'id="(?:corePrice_feature_div|corePriceDisplay_desktop_feature_div|apex_desktop)"(.{0,6000})',
                     html, re.S)
    price_area = core.group(1) if core else html
    price = re.search(r'class="a-offscreen">\s*([^<]{1,40})<', price_area)
    if price:
        amount = parse_amount(price.group(1))
        if amount:
            out["price"] = {"amount": amount, "currency": currency_of(price.group(1), "USD")}
    rating = re.search(r'id="acrPopover"[^>]*title="([\d.,]+) out of 5', html) or \
        re.search(r'([\d.,]+) out of 5 stars', html)
    if rating:
        out["rating"] = _rating(rating.group(1))
    count = re.search(r'id="acrCustomerReviewText"[^>]*>\s*\(?([\d,. ]+)', html)
    if count:
        out["review_count"] = _count(count.group(1))
    availability = re.search(r'primary-availability-message[^>]*>\s*([^<]{2,120})<', html) or \
        re.search(r'id="availability"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{2,120})<', html)
    if availability:
        out["availability"] = availability_of(clean_text(availability.group(1), 200))
    reviews = []
    # Отзывы на странице товара: блоки data-hook="review" (имя, звёзды, текст).
    for block in html.split('data-hook="review" ')[1:7]:
        block = block[:14000]
        body = re.search(r'data-hook="(?:reviewRichContentContainer|review-body)"[^>]*>(.*?)</div>', block, re.S)
        text = clean_text(body.group(1), 400) if body else ""
        if len(text) < 8:
            continue
        author = re.search(r'class="a-profile-name">([^<]{1,60})<', block)
        stars = re.search(r'data-hook="review-star-rating".{0,200}?([\d.,]+) out of 5', block, re.S)
        reviews.append({"author": clean_text(author.group(1), 60) if author else "",
                        "rating": _rating(stars.group(1)) if stars else None, "text": text})
    if not reviews:
        for body in re.findall(r'data-hook="review-body"[^>]*>(.*?)</span>\s*</div>', html, re.S)[:5]:
            text = clean_text(body, 400)
            if len(text) >= 8:
                reviews.append({"author": "", "rating": None, "text": text})
    if reviews:
        out["reviews"] = reviews
    return out


def _tiny_image(url: str) -> bool:
    """Миниатюры (…_100px.jpg, /w:80/, ?width=64) в карточке выглядят мылом."""
    for match in re.finditer(r"(?:[_\-/](\d{2,4})px\b|[/_,]w[:_=]?(\d{2,4})\b|[?&](?:w|width)=(\d{2,4})\b)", url, re.I):
        size = int(next(group for group in match.groups() if group))
        if size < 240:
            return True
    return False


def extract_product(html: str, url: str) -> Dict[str, Any]:
    """Данные товара со страницы магазина или обзора.

    Возвращает ``{"product": {...}, "list": [{...}, ...]}``: страница товара даёт
    product, страница поиска/категории со списком schema.org ItemList — list.
    Пустые поля опускаются; ничего не найдено — пустой product.
    """
    text = str(html or "")[:3_000_000]
    base = url
    products: List[Dict[str, Any]] = []
    listed: List[Dict[str, Any]] = []
    for raw in _LD_RE.findall(text)[:40]:
        data = _loads_lenient(html_lib.unescape(raw) if "&quot;" in raw[:200] else raw)
        if data is None:
            continue
        for node in _walk(data):
            types = set(_types(node))
            if "ItemList" in types:
                for element in (node.get("itemListElement") or [])[:40] if isinstance(
                        node.get("itemListElement"), list) else []:
                    item = element.get("item") if isinstance(element, dict) and isinstance(
                        element.get("item"), dict) else element
                    if isinstance(item, dict) and set(_types(item)) & PRODUCT_TYPES:
                        row = _product_from_ld(item, base)
                        if row.get("title"):
                            listed.append(row)
            elif types & PRODUCT_TYPES:
                row = _product_from_ld(node, base)
                if row.get("title") or row.get("price"):
                    products.append(row)
    # Список товаров дублирует их же как Product: на странице товара это одна
    # запись, на странице поиска — список.
    listed_titles = {row.get("title") for row in listed}
    products = [row for row in products if row.get("title") not in listed_titles] or ([] if listed else products)
    product: Dict[str, Any] = {}
    if products:
        # Самая полная запись (с ценой и картинками) — главная.
        products.sort(key=lambda row: (bool(row.get("price")), len(row.get("images") or []), len(row)), reverse=True)
        product = dict(products[0])
        for extra in products[1:3]:
            for key, value in extra.items():
                product.setdefault(key, value)
    metas = _metas(text)
    micro = _microdata(text, base) if not product.get("price") or not product.get("rating") else {}
    special = _amazon(text, base) if store_for_url(url) == "amazon" else {}
    for source in (special, micro):
        for key, value in source.items():
            if key == "images":
                merged = list(product.get("images") or [])
                merged += [img for img in value if img not in merged]
                product["images"] = merged
            else:
                product.setdefault(key, value)
    og_images = _images_of((metas.get("og:image:secure_url") or []) + (metas.get("og:image") or [])
                           + (metas.get("twitter:image") or []), base)
    if og_images:
        merged = list(product.get("images") or [])
        merged += [img for img in og_images if img not in merged]
        product["images"] = merged
    if not product.get("price"):
        amount = parse_amount(next(iter(metas.get("product:price:amount") or metas.get("og:price:amount") or []), None))
        if amount:
            product["price"] = {"amount": amount, "currency": currency_of(next(iter(
                metas.get("product:price:currency") or metas.get("og:price:currency") or []), ""))}
    if not product.get("availability") and metas.get("product:availability"):
        product["availability"] = availability_of(metas["product:availability"][0])
    if not product.get("title"):
        og_title = next(iter(metas.get("og:title") or []), "")
        match = _TITLE_RE.search(text)
        product["title"] = clean_text(og_title or (match.group(1) if match else ""), 240)
    if product.get("images"):
        product["images"] = [img for img in product["images"] if not re.search(
            r"logo|sprite|placeholder|favicon|blank\.|pixel|spacer|1x1", img, re.I) and not _tiny_image(img)][:MAX_IMAGES]
    price = product.get("price")
    if price and not price.get("currency"):
        price["currency"] = {"ru": "RUB"}.get("ru" if _host(url).endswith(".ru") else "", "")
        if not price["currency"] and store_for_url(url) in LOCALE_STORES["en"]:
            price["currency"] = "USD"
    return {"product": {k: v for k, v in product.items() if v not in (None, "", [], {})}, "list": listed[:30]}


# --------------------------------------------------------------------------- provenance

class Provenance:
    """Журнал увиденного: что именно было на странице (или в выдаче) и когда.

    Нормализация доверяет цене, рейтингу и картинкам только отсюда. Журнал
    хранится на диске (данные публичные, без пользовательского текста), чтобы
    отметка «цена со страницы магазина, проверено …» переживала перезапуск;
    запись старше ``ttl`` забывается — цены устаревают.
    """

    def __init__(self, path: Optional[Path] = None, *, limit: int = 1500, ttl: float = 30 * 86400) -> None:
        self._path = path
        self._limit = limit
        self._ttl = ttl
        self._lock = threading.Lock()
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._lookups: Dict[str, Dict[str, Any]] = {}
        self._loaded = path is None
        self._dirty = False

    def _ensure(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(Path(self._path).read_text(encoding="utf-8"))
            rows = data.get("pages") if isinstance(data, dict) else None
            if isinstance(rows, dict):
                now = time.time()
                self._rows = {str(k): v for k, v in rows.items()
                              if isinstance(v, dict) and now - float(v.get("seen") or 0) < self._ttl}
            lookups = data.get("lookups") if isinstance(data, dict) else None
            if isinstance(lookups, dict):
                self._lookups = {str(k): v for k, v in lookups.items() if isinstance(v, dict)}
        except (OSError, ValueError, TypeError):
            self._rows = {}

    def remember(self, url: str, facts: Dict[str, Any], *, kind: str = "page") -> None:
        key = url_key(url)
        if not key:
            return
        with self._lock:
            self._ensure()
            row = dict(self._rows.get(key) or {})
            if kind == "page" or row.get("kind") != "page":
                row["kind"] = kind
            for name, value in facts.items():
                if value in (None, "", [], {}):
                    continue
                if name in ("images", "reviews", "snippets"):
                    merged = list(row.get(name) or [])
                    merged += [item for item in value if item not in merged]
                    row[name] = merged[:30 if name == "snippets" else 12]
                elif kind == "page" or name not in row:
                    row[name] = value
            row["url"] = row.get("url") or url
            row["seen"] = time.time()
            self._rows[key] = row
            if len(self._rows) > self._limit:
                for old in sorted(self._rows, key=lambda k: self._rows[k].get("seen", 0))[: len(self._rows) - self._limit]:
                    self._rows.pop(old, None)
            self._dirty = True

    def get(self, url: str) -> Dict[str, Any]:
        key = url_key(url)
        with self._lock:
            self._ensure()
            row = self._rows.get(key)
            if row and time.time() - float(row.get("seen") or 0) >= self._ttl:
                self._rows.pop(key, None)
                return {}
            return dict(row) if row else {}

    def image_seen(self, image: str) -> bool:
        with self._lock:
            self._ensure()
            return any(image in (row.get("images") or []) for row in self._rows.values())

    def text_seen(self, url: str, text: str) -> bool:
        """Цитата отзыва действительно встречалась на этой странице/в выдаче."""
        row = self.get(url)
        if not row:
            return False
        needle = _norm_words(text)
        if len(needle) < 12:
            return False
        haystacks = [_norm_words(item.get("text") if isinstance(item, dict) else item)
                     for item in (row.get("reviews") or []) + (row.get("snippets") or [])]
        probe = needle[:60]
        return any(probe in hay or (len(hay) >= 30 and hay[:60] in needle) for hay in haystacks if hay)

    # Итоги product_lookup по магазинам: карточка товара показывает «не удалось
    # проверить» для магазина региона без оффера, даже если модель о нём промолчала.
    def remember_lookup(self, query: str, locale: str, stores: List[Dict[str, Any]]) -> None:
        tokens = " ".join(sorted(set(_tokens(query))))
        if not tokens or not stores:
            return
        key = f"{locale_of(locale)}:{tokens}"
        rows = [{k: v for k, v in row.items() if k in ("store_id", "status", "url")} for row in stores
                if isinstance(row, dict) and row.get("store_id") in STORES and row.get("status") in STORE_STATUS]
        with self._lock:
            self._ensure()
            self._lookups[key] = {"query": clean_text(query, 160), "stores": rows, "seen": time.time()}
            if len(self._lookups) > 400:
                for old in sorted(self._lookups, key=lambda k: self._lookups[k].get("seen", 0))[: len(self._lookups) - 400]:
                    self._lookups.pop(old, None)
            self._dirty = True

    def lookup_for(self, name: str, locale: str, *extra: str) -> Optional[Dict[str, Any]]:
        """Самая подходящая свежая проверка магазинов для товара ``name`` (модель/артикул в ``extra``)."""
        prefix = locale_of(locale) + ":"
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0
        with self._lock:
            self._ensure()
            rows = [(k, dict(v)) for k, v in self._lookups.items() if k.startswith(prefix)]
        now = time.time()
        for _key, row in rows:
            if now - float(row.get("seen") or 0) >= 2 * 86400:      # статусы магазинов быстро устаревают
                continue
            score = _relevance(row.get("query", ""), name, *extra)
            if score >= 0.75 and (score > best_score or (score == best_score and best and
                                                         row.get("seen", 0) > best.get("seen", 0))):
                best, best_score = row, score
        return best

    def save(self) -> None:
        if not self._path:
            return
        with self._lock:
            if not self._dirty:
                return
            payload = {"version": 1, "pages": dict(self._rows), "lookups": dict(self._lookups)}
            self._dirty = False
        try:
            from .storage import save_json
            save_json(str(self._path), payload, backup=False)
        except Exception as exc:  # pragma: no cover - диск недоступен
            log.debug("product cache not saved: %s", exc)


def _norm_words(text: Any) -> str:
    return re.sub(r"[^\w]+", " ", str(text or "").lower()).strip()


_PROVENANCE: Optional[Provenance] = None
_PROVENANCE_LOCK = threading.Lock()


def provenance() -> Provenance:
    global _PROVENANCE
    with _PROVENANCE_LOCK:
        if _PROVENANCE is None:
            try:
                from .pgdata import MEMU_DIR
                path: Optional[Path] = Path(MEMU_DIR) / "product_cache.json"
            except Exception:  # pragma: no cover
                path = None
            _PROVENANCE = Provenance(path)
        return _PROVENANCE


# --------------------------------------------------------------------------- lookup

SearchFn = Callable[[str, int], Awaitable[List[Dict[str, Any]]]]
FetchFn = Callable[[str], Awaitable[str]]

_LOOKUP_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_LOOKUP_TTL = 15 * 60
_LOOKUP_MAX = 120
_STOP = {"купить", "цена", "price", "buy", "the", "and", "для", "with", "new", "отзывы", "review", "reviews"}


def _tokens(text: str) -> List[str]:
    return [tok for tok in re.findall(r"[a-zа-яё0-9]+", str(text or "").lower()) if len(tok) > 1 and tok not in _STOP]


def _relevance(query: str, *texts: str) -> float:
    wanted = _tokens(query)
    if not wanted:
        return 0.0
    hay = " ".join(_tokens(" ".join(texts)))
    # «1tb» и «1 тб»: цифры запроса должны встретиться хотя бы как часть слова.
    hits = sum(1 for tok in wanted if tok in hay)
    # Модель товара — это слова с цифрами (sn770, 990, 15): без них страница
    # о бренде вообще («WD Black» на главной сайта) не про этот товар.
    model = [tok for tok in wanted if re.search(r"\d", tok) and not re.fullmatch(r"\d+(?:tb|gb|тб|гб|mb)?", tok)] \
        or [tok for tok in wanted if re.search(r"\d", tok)]
    if model and not any(tok in hay for tok in model):
        return 0.0
    return hits / len(wanted)


_BLOCKED_RE = re.compile(r"showcaptcha|/captcha|Вы не робот|Robot Check|Are you a human|Access Denied|"
                         r"Attention Required|challenge-platform|Pardon Our Interruption|Доступ ограничен", re.I)


def blocked_page(html: str, url: str = "") -> bool:
    """Страница-заглушка антибота: из неё нельзя брать ни картинку, ни цену."""
    head = str(html or "")[:20000]
    title = _TITLE_RE.search(head)
    return bool(_BLOCKED_RE.search(url or "") or (title and _BLOCKED_RE.search(title.group(1)))
                or (len(head) < 30000 and "captcha" in head.lower() and "product" not in head.lower()))


def lookup_queries(query: str, locale: str, stores: Iterable[str]) -> Dict[str, List[str]]:
    names = [store_name(s, "ru" if locale == "ru" else "en") for s in stores][:3]
    if locale == "ru":
        return {"shop": [f"{query} купить", f"{query} цена {' '.join(names)}"], "reviews": [f"{query} отзывы"]}
    return {"shop": [f"{query} buy", f"{query} price {' '.join(names)}"], "reviews": [f"{query} review"]}


# Собственный поиск каждого магазина: страница поиска (schema.org ItemList/Product,
# если магазин отдаёт его без JavaScript) или публичный JSON. Многие магазины
# закрывают такие адреса антиботом — тогда магазин получает честный статус
# «не удалось проверить», а не молча пропадает из «Где купить».
STORE_SEARCH: Dict[str, str] = {
    "ozon": "https://www.ozon.ru/search/?text={q}&from_global=true",
    "wildberries": "https://search.wb.ru/exactmatch/ru/common/v4/search?appType=1&curr=rub&dest=-1257786"
                   "&query={q}&resultset=catalog&sort=popular&spp=30",
    "dns": "https://www.dns-shop.ru/search/?q={q}",
    "yandex_market": "https://market.yandex.ru/search?text={q}",
    "mvideo": "https://www.mvideo.ru/product-list-page?q={q}",
    "citilink": "https://www.citilink.ru/search/?text={q}",
    "megamarket": "https://megamarket.ru/catalog/?q={q}",
    "bestbuy": "https://www.bestbuy.com/site/searchpage.jsp?st={q}",
    "walmart": "https://www.walmart.com/search?q={q}",
    "newegg": "https://www.newegg.com/p/pl?d={q}",
}


# Страница поиска для человека, где адрес STORE_SEARCH — служебный JSON.
STORE_USER_SEARCH: Dict[str, str] = {
    "wildberries": "https://www.wildberries.ru/catalog/0/search.aspx?search={q}",
    "amazon": "https://www.amazon.com/s?k={q}",
    "bh": "https://www.bhphotovideo.com/c/search?q={q}",
    "ebay": "https://www.ebay.com/sch/i.html?_nkw={q}",
}


def direct_search_pages(query: str, locale: str, stores: Iterable[str]) -> List[Tuple[str, str]]:
    """Адреса собственного поиска магазинов из ``stores`` (порядок сохраняется)."""
    return [(store_id, STORE_SEARCH[store_id].format(q=quote_plus(query)))
            for store_id in stores if store_id in STORE_SEARCH]


def site_queries(query: str, stores: Iterable[str]) -> List[Tuple[str, str]]:
    """Поиск товара на сайте конкретного магазина через веб-поиск (``site:домен``)."""
    return [(store_id, f"{query} site:{STORES[store_id]['domains'][0]}") for store_id in stores if store_id in STORES]


def _wb_products(text: str) -> List[Dict[str, Any]]:
    """Публичный JSON поиска Wildberries → товары с ценой, рейтингом и наличием (цена в копейках)."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    rows = ((data.get("data") or {}).get("products") if isinstance(data, dict) else None) or []
    out = []
    for row in rows[:30] if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        try:
            product_id = int(row["id"])
        except (TypeError, ValueError):
            continue
        cents = row.get("salePriceU")
        if not cents:
            for size in row.get("sizes") or []:
                price = (size or {}).get("price") or {}
                cents = price.get("product") or price.get("total")
                if cents:
                    break
        amount = parse_amount(cents) / 100 if parse_amount(cents) else None
        title = clean_text(" ".join(x for x in (str(row.get("brand") or ""), str(row.get("name") or "")) if x), 240)
        quantity = row.get("totalQuantity")
        item: Dict[str, Any] = {
            "title": title, "brand": clean_text(row.get("brand"), 60),
            "url": f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx",
            "availability": "unknown" if quantity is None else ("in_stock" if parse_amount(quantity) else "out_of_stock"),
        }
        if amount:
            item["price"] = {"amount": round(amount, 2), "currency": "RUB"}
        rating = _rating(row.get("reviewRating") or row.get("rating"))
        if rating:
            item["rating"] = rating
            count = _count(row.get("feedbacks"))
            if count is not None:
                item["review_count"] = count
        out.append({k: v for k, v in item.items() if v not in (None, "", [], {})})
    return out


# Признаки того, что товар сняли с продажи (страница есть, купить нельзя).
_DISCONTINUED_RE = re.compile(
    r"сня(?:т|та|ты) с (?:производства|продажи)|больше не (?:производится|продаётся|продается)|"
    r"товар (?:закончился|больше не продаётся|больше не продается|недоступен для заказа)|нет в продаже|"
    r"discontinued|no longer (?:available|sold|produced)|this item is no longer|end of life", re.I)


def page_discontinued(html: str) -> bool:
    """Видимый текст страницы товара говорит, что товар сняли с продажи."""
    text = _TAG_RE.sub(" ", re.sub(r"<(script|style)\b[\s\S]*?</\1>", " ", str(html or "")[:800_000], flags=re.I))
    return bool(_DISCONTINUED_RE.search(text))


def latest_review_date(reviews: Iterable[Dict[str, Any]]) -> str:
    """Самая свежая дата отзыва (YYYY-MM-DD) или ''."""
    best = ""
    for review in reviews or []:
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", str((review or {}).get("date") or ""))
        if match and match.group(0) > best:
            best = match.group(0)
    return best


# Статусы проверки магазина: что именно удалось узнать о товаре в нём.
STORE_STATUS = ("found", "approx", "not_found", "blocked", "error")
_STATUS_RANK = {"found": 0, "approx": 1, "blocked": 2, "error": 3, "not_found": 4}


def _better_status(old: str, new: str) -> str:
    if not old:
        return new
    return new if _STATUS_RANK.get(new, 9) < _STATUS_RANK.get(old, 9) else old


async def lookup(query: str, *, locale: str = "ru", search: SearchFn, fetch: FetchFn,
                 stores: Optional[Iterable[str]] = None, urls: Optional[Iterable[str]] = None,
                 limit: int = 6, budget: float = 18.0, store: Optional[Provenance] = None,
                 use_cache: bool = True, max_price: Any = None) -> Dict[str, Any]:
    """Инструмент ``product_lookup``: реальные цены, картинки, рейтинги и отзывы.

    ``search(query, n)`` — веб-поиск приложения, ``fetch(url)`` — сырое HTML
    страницы (с защитой от SSRF). Всё идёт параллельно и укладывается в
    ``budget`` секунд; что не успело — просто не попадает в ответ, частичные
    данные лучше пустых.

    Каждый магазин региона проверяется своими способами: общий поиск, собственный
    поиск магазина (страница или публичный JSON), затем ``запрос site:домен``
    для тех, где страницы товара ещё нет. Итог по каждому магазину —
    ``store_status`` (found / approx / blocked / error / not_found), чтобы модель
    и карточка честно говорили «не удалось проверить», а не молча теряли магазин.
    ``max_price`` — бюджет: офферы получают ``within_budget``.
    """
    started = time.monotonic()
    locale = locale_of(locale)
    query = clean_text(query, 160)
    store = store or provenance()
    chosen = [s for s in (stores or []) if s in STORES] or list(LOCALE_STORES[locale])
    explicit = [u for u in (public_url(u) for u in (urls or [])) if u][:4]
    ceiling = budget_in(max_price, LOCALE_CURRENCY[locale]) if max_price not in (None, "", 0) else None
    cache_key = json.dumps([query.lower(), locale, sorted(chosen), explicit, limit,
                            ceiling and ceiling["amount"]], ensure_ascii=False)
    if use_cache:
        cached = _LOOKUP_CACHE.get(cache_key)
        if cached and time.time() - cached[0] < _LOOKUP_TTL:
            return {**cached[1], "cached": True}
    notes: List[str] = []
    deadline = started + budget
    status: Dict[str, Dict[str, Any]] = {sid: {"status": "", "how": []} for sid in chosen}

    def mark(sid: str, value: str, how: str = "") -> None:
        if sid not in status:
            return
        status[sid]["status"] = _better_status(status[sid]["status"], value)
        if how and how not in status[sid]["how"]:
            status[sid]["how"].append(how)

    def left(cap: float) -> float:
        return max(0.5, min(cap, deadline - time.monotonic()))

    queries = lookup_queries(query, locale, chosen)

    async def run_search(text: str, kind: str) -> List[Dict[str, Any]]:
        try:
            rows = await asyncio.wait_for(search(text, 8), left(7.0))
        except Exception as exc:
            notes.append(f"search failed: {text} ({type(exc).__name__})")
            return []
        out = []
        for row in rows or []:
            url = public_url(row.get("url"))
            if url:
                out.append({"title": clean_text(row.get("title"), 200), "url": url,
                            "snippet": clean_text(row.get("snippet"), 400), "kind": kind})
        return out

    async def run_direct(store_id: str, url: str) -> List[Dict[str, Any]]:
        label = store_name(store_id, "en")
        try:
            page = await asyncio.wait_for(fetch(url), left(7.0))
        except Exception as exc:
            mark(store_id, "error", "store search")
            notes.append(f"{label} search page unavailable ({type(exc).__name__})")
            return []
        if store_id == "wildberries" and page.lstrip().startswith("{"):
            found_rows = _wb_products(page)
            if not found_rows:
                mark(store_id, "not_found", "store search")
            return [{**row, "via": "api"} for row in found_rows]
        if blocked_page(page, url):
            mark(store_id, "blocked", "store search")
            notes.append(f"{label} search page is behind an anti-bot check")
            return []
        found = extract_product(page, url)
        rows = []
        for item in found["list"]:
            item_url = public_url(item.get("url") or item.get("offer_url"))
            if item_url and store_for_url(item_url):
                rows.append({**item, "url": item_url, "via": "search page"})
        if not rows:
            # Страница открылась, но товаров в разметке нет (каталог рисует JavaScript):
            # это «не удалось проверить», а не «товара нет».
            mark(store_id, "blocked" if len(page) < 4000 else "error", "store search")
        return rows

    tasks = [run_search(q, "shop") for q in queries["shop"]] + [run_search(q, "reviews") for q in queries["reviews"]]
    direct = direct_search_pages(query, locale, chosen)
    results = await asyncio.gather(*tasks, *(run_direct(s, u) for s, u in direct))
    search_rows: List[Dict[str, Any]] = [row for chunk in results[: len(tasks)] for row in chunk]
    direct_rows: List[Dict[str, Any]] = [row for chunk in results[len(tasks):] for row in chunk]

    for row in search_rows:
        store.remember(row["url"], {"title": row["title"], "snippets": [row["snippet"]] if row["snippet"] else []},
                       kind="snippet")

    def relevant_page(row: Dict[str, Any]) -> bool:
        return is_product_page(row["url"]) and _relevance(query, row["title"], row["url"], row.get("snippet", "")) >= 0.5

    # Вторая волна: «запрос site:домен» для каждого магазина региона, где страницы
    # товара ещё нет (ни в выдаче, ни в собственном поиске магазина).
    covered = {store_for_url(row["url"]) for row in search_rows if relevant_page(row)}
    covered |= {store_for_url(row["url"]) for row in direct_rows if _relevance(query, row.get("title", "")) >= 0.6}
    missing = [sid for sid in chosen if sid not in covered]
    if not explicit and missing and deadline - time.monotonic() > 4:
        wave = site_queries(query, missing[:7])
        more = await asyncio.gather(*(run_search(text, "shop") for _sid, text in wave))
        for (sid, _text), chunk in zip(wave, more):
            hit = False
            for row in chunk:
                store.remember(row["url"], {"title": row["title"], "snippets": [row["snippet"]] if row["snippet"] else []},
                               kind="snippet")
                search_rows.append(row)
                hit = hit or (store_for_url(row["url"]) == sid and relevant_page(row))
            if not hit:
                mark(sid, "not_found", "site search")

    # Кандидаты: страницы товаров известных магазинов, релевантные запросу.
    candidates: List[Dict[str, Any]] = []
    seen_keys = set()
    for url in explicit:
        key = url_key(url)
        if key not in seen_keys:
            seen_keys.add(key)
            candidates.append({"url": url, "title": "", "snippet": "", "score": 2.0})
    for row in search_rows:
        if not is_product_page(row["url"]):
            continue
        key = url_key(row["url"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        score = _relevance(query, row["title"], row["url"], row["snippet"])
        if score < 0.5:
            continue
        sid = store_for_url(row["url"])
        score += 0.3 if sid in chosen else 0.0
        candidates.append({**row, "score": score})
    candidates.sort(key=lambda row: row["score"], reverse=True)
    # Разные магазины важнее нескольких страниц одного: сначала по одной странице
    # каждого магазина, потом вторые страницы.
    picked: List[Dict[str, Any]] = []
    used_stores: Dict[str, int] = {}
    cap = max(1, min(10, limit + 2))
    for row in candidates:
        sid = store_for_url(row["url"]) or row["url"]
        if used_stores.get(sid, 0) >= (2 if row["score"] >= 2 else 1):
            continue
        used_stores[sid] = used_stores.get(sid, 0) + 1
        picked.append(row)
        if len(picked) >= cap:
            break

    semaphore = asyncio.Semaphore(5)

    async def read(row: Dict[str, Any]) -> Dict[str, Any]:
        url = row["url"]
        async with semaphore:
            try:
                page = await asyncio.wait_for(fetch(url), left(7.0))
            except Exception as exc:
                return {"row": row, "error": type(exc).__name__}
        if blocked_page(page, url):
            return {"row": row, "error": "anti-bot check", "blocked": True}
        try:
            data = extract_product(page, url)["product"]
            if store_for_url(url) and page_discontinued(page):
                data["discontinued"] = True
            return {"row": row, "data": data}
        except Exception as exc:  # pragma: no cover - разбор не должен ронять инструмент
            return {"row": row, "error": type(exc).__name__}

    # Обзорные страницы (не магазины): картинка и, бывает, рейтинг/отзывы в JSON-LD.
    review_rows = [row for row in search_rows if row["kind"] == "reviews"]
    seen_review = set()
    review_sources = []
    for row in review_rows:
        key = url_key(row["url"])
        if key in seen_review or _relevance(query, row["title"], row["snippet"]) < 0.5:
            continue
        seen_review.add(key)
        review_sources.append(row)
    review_reads = [row for row in review_sources if not store_for_url(row["url"])][:2]

    reads = await asyncio.gather(*(read(row) for row in picked + review_reads)) if left(99) > 0.6 else []

    offers: List[Dict[str, Any]] = []
    aggregate_images: List[Dict[str, str]] = []
    for item in direct_rows:
        if _relevance(query, item.get("title", "")) < 0.6:
            continue
        sid = store_for_url(item["url"])
        offer = {"store": store_name(sid, locale), "store_id": sid, "url": item["url"],
                 "title": item.get("title", ""), "price": item.get("price"),
                 "price_from": ("api" if item.get("via") == "api" else "page") if item.get("price") else "none",
                 "availability": item.get("availability", "unknown"),
                 "images": (item.get("images") or [])[:4], "rating": item.get("rating"),
                 "review_count": item.get("review_count")}
        store.remember(item["url"], {k: offer[k] for k in ("title", "price", "availability", "images", "rating",
                                                           "review_count") if offer.get(k)}
                       | {"store_id": sid, "checked_at": int(time.time())})
        mark(sid, "found" if item.get("price") else "approx", item.get("via") or "store search")
        offers.append({k: v for k, v in offer.items() if v not in (None, "", [], {})})
        if len([o for o in offers if o["store_id"] == sid]) >= 3:
            break
    for result in reads:
        row = result["row"]
        sid = store_for_url(row["url"])
        data = result.get("data") or {}
        if not sid:
            # Обзор: только картинки и отзывы как источник.
            if data.get("images"):
                aggregate_images += [{"url": img, "from": row["url"]} for img in data["images"][:2]]
            facts = {k: data.get(k) for k in ("title", "images", "rating", "review_count", "reviews")}
            store.remember(row["url"], {**facts, "checked_at": int(time.time())})
            for source in review_sources:
                if source["url"] == row["url"]:
                    if data.get("rating"):
                        source["rating"] = data["rating"]
                    if data.get("reviews"):
                        source["reviews"] = data["reviews"][:3]
            continue
        price = data.get("price")
        price_from = "page" if price else "none"
        if not price:
            guess = snippet_price(row.get("snippet", "") + " " + row.get("title", ""))
            if guess:
                price, price_from = guess, "snippet"
        availability = "out_of_stock" if data.get("discontinued") else data.get("availability", "unknown")
        offer = {"store": store_name(sid, locale), "store_id": sid, "url": data.get("url") if data.get("url") and
                 store_for_url(data.get("url")) == sid else row["url"],
                 "title": data.get("title") or row.get("title", ""), "price": price, "price_from": price_from,
                 "availability": availability, "images": (data.get("images") or [])[:4],
                 "rating": data.get("rating"), "review_count": data.get("review_count"),
                 "reviews": [{k: v for k, v in r.items() if v} for r in (data.get("reviews") or [])[:3]],
                 "latest_review": latest_review_date(data.get("reviews") or []),
                 "discontinued": bool(data.get("discontinued")),
                 "brand": data.get("brand", ""), "sku": data.get("sku", "")}
        if result.get("error"):
            offer["page"] = f"not readable ({result['error']}); data from search snippet"
        mark(sid, "found" if price_from == "page" else "approx" if price or not result.get("blocked") else "blocked",
             "product page")
        if result.get("blocked") and price_from != "page":
            mark(sid, "blocked")
        if data:
            facts = {k: offer.get(k) for k in ("title", "availability", "images", "rating", "review_count",
                                               "reviews", "brand", "sku", "discontinued", "latest_review")}
            if price_from == "page":
                facts["price"] = price
            store.remember(row["url"], {**facts, "store_id": sid, "checked_at": int(time.time())})
            if offer["url"] != row["url"]:
                store.remember(offer["url"], {**facts, "store_id": sid, "checked_at": int(time.time())})
        offers.append({k: v for k, v in offer.items() if v not in (None, "", [], {}, False)})
    # Бюджет: только цена со страницы магазина (или его API) доказывает «в бюджете».
    for offer in offers:
        price = offer.get("price") or {}
        if ceiling and price.get("amount"):
            # A price without a currency proves nothing about a budget (Round 23: it used to count as «same»).
            same = price.get("currency") == ceiling["currency"]
            if offer.get("price_from") in ("page", "api") and same:
                offer["within_budget"] = price["amount"] <= ceiling["amount"] * 1.0001
            else:
                offer["within_budget"] = "unverified"
    # Порядок: подтверждённая цена, в наличии, магазин региона, дешевле.
    offers.sort(key=lambda o: (o.get("price_from") not in ("page", "api"), o.get("availability") == "out_of_stock",
                               o.get("store_id") not in chosen, (o.get("price") or {}).get("amount") or 10 ** 12))
    for offer in offers:
        for img in offer.get("images") or []:
            if not any(entry["url"] == img for entry in aggregate_images):
                aggregate_images.append({"url": img, "from": offer["url"]})
    # Итог по магазинам: каждый магазин региона назван, даже если проверить не удалось.
    store_status = []
    for sid in chosen:
        value = status[sid]["status"] or "not_found"
        entry: Dict[str, Any] = {"store_id": sid, "store": store_name(sid, locale), "status": value}
        best = next((o for o in offers if o.get("store_id") == sid), None)
        if best:
            entry["url"] = best["url"]
            if best.get("price"):
                entry["price"] = best["price"]
        if status[sid]["how"]:
            entry["checked_via"] = status[sid]["how"]
        store_status.append(entry)
    signals: Dict[str, Any] = {}
    verified = [o for o in offers if o.get("price_from") in ("page", "api")]
    in_stock = [o for o in verified if o.get("availability") == "in_stock"]
    signals["verified_offers"] = len(verified)
    signals["in_stock_offers"] = len(in_stock)
    if any(o.get("discontinued") for o in offers):
        signals["discontinued"] = True
        notes.append("A store page says this product is discontinued / no longer sold. Prefer a current model.")
    latest = max((o.get("latest_review") or "" for o in offers), default="")
    if latest:
        signals["latest_review"] = latest
        if latest < time.strftime("%Y-%m-%d", time.gmtime(time.time() - 365 * 86400)):
            notes.append(f"The newest review found is from {latest} (over a year old): the product may be outdated "
                         "or hard to buy. Prefer a current model unless the user asked for this one.")
    if verified and not in_stock and all(o.get("availability") == "out_of_stock" for o in verified):
        notes.append("Every verified offer is out of stock. Do not recommend this product as available.")
    if ceiling:
        fits = [o for o in offers if o.get("within_budget") is True and o.get("availability") != "out_of_stock"]
        signals["budget"] = {"max": ceiling["amount"], "currency": ceiling["currency"],
                             "verified_within": len(fits)}
        if not fits:
            notes.append("No verified in-stock offer at or below the budget: this product must NOT be presented as "
                         "fitting the budget (mark it as over budget or drop it).")
    try:
        store.remember_lookup(query, locale, store_status)
    except Exception:  # pragma: no cover
        pass
    try:
        await asyncio.to_thread(store.save)
    except Exception:  # pragma: no cover
        pass
    if not offers:
        notes.append("No store page with this product was found or readable. Do not invent offers; show the item "
                     "without offers or with an approximate price, or try a more exact model name.")
    payload = {
        "query": query, "locale": locale, "currency": LOCALE_CURRENCY[locale],
        "stores_searched": [store_name(s, locale) for s in chosen],
        "store_status": store_status,
        "offers": offers[:MAX_OFFERS],
        "signals": signals,
        "images": aggregate_images[:MAX_IMAGES],
        "review_sources": [{k: v for k, v in {
            "site": store_name(store_for_url(r["url"]), locale) if store_for_url(r["url"]) else _host(r["url"]),
            "title": r["title"], "url": r["url"], "snippet": r["snippet"], "rating": r.get("rating"),
            "reviews": r.get("reviews")}.items() if v} for r in review_sources[:6]],
        "took_ms": int((time.monotonic() - started) * 1000),
        "note": ("Untrusted web data. price_from=page/api is the store's own price (exact), snippet = search snippet "
                 "(mark approx), none = no price. store_status tells, per store, whether the product was found "
                 "(found/approx), not found, or could not be checked (blocked/error) — never claim a store price "
                 "that is not in offers. Copy URLs, image URLs and quotes exactly into memuai-products."),
    }
    if notes:
        payload["notes"] = notes[:8]
    if offers or review_sources:
        _LOOKUP_CACHE[cache_key] = (time.time(), payload)
        if len(_LOOKUP_CACHE) > _LOOKUP_MAX:
            for old in sorted(_LOOKUP_CACHE, key=lambda k: _LOOKUP_CACHE[k][0])[: len(_LOOKUP_CACHE) - _LOOKUP_MAX]:
                _LOOKUP_CACHE.pop(old, None)
    return payload


# --------------------------------------------------------------------------- normalize

BLOCK_RE = re.compile(r"```[ \t]*memuai[-_ ]?products[ \t]*\r?\n([\s\S]*?)(?:\r?\n[ \t]*```[ \t]*(?=\r?\n|$)|```\s*$)",
                      re.I)


def parse_block(raw: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    if isinstance(raw, dict):
        return raw, ""
    text = str(raw or "").strip()
    if len(text) > MAX_RAW_CHARS:
        return None, "block too large"
    match = BLOCK_RE.search(text)
    if match:
        text = match.group(1).strip()
    data = _loads_lenient(text)
    if isinstance(data, list):
        data = {"items": data} if data and all(isinstance(x, dict) for x in data) and not any(
            "items" in x for x in data) else (data[0] if data and isinstance(data[0], dict) else None)
    if not isinstance(data, dict):
        return None, "not a JSON object"
    return data, ""


def _clean_list(values: Any, count: int, limit: int) -> List[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    out = []
    for value in values:
        text = clean_text(value, limit)
        if text and text not in out:
            out.append(text)
        if len(out) >= count:
            break
    return out


def _price_in(value: Any, currency_hint: str) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        amount = parse_amount(value.get("amount") if value.get("amount") is not None else value.get("value"))
        currency = currency_of(value.get("currency"), "") or currency_of(str(value.get("amount") or ""), currency_hint)
        approx = bool(value.get("approx"))
    else:
        amount = parse_amount(value)
        currency = currency_of(str(value or ""), currency_hint)
        approx = False
    if amount is None:
        return None
    return {"amount": amount, "currency": currency or currency_hint, "approx": approx}


_BUDGET_SCALE = re.compile(
    r"(?<![A-Za-zА-Яа-я])(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>k|к|тыс(?:\.|яч\w*)?|thousand|m|млн|mln|million)(?![A-Za-zА-Яа-я])",
    re.I)


def budget_in(value: Any, currency_hint: str) -> Optional[Dict[str, Any]]:
    """A budget («15 000 ₽», "$1.5k", «15к», «20 тыс.», {"amount": 15000}) → price dict.

    Round 23: «15к» / "$1.5k" parsed as 15 / 1.5 — every product was then «over budget»."""
    text = value.get("amount") if isinstance(value, dict) else value
    if isinstance(text, str):
        found = _BUDGET_SCALE.search(text.replace("\u00a0", " "))
        if found:
            number = float(found.group("num").replace(",", "."))
            scale = 1_000_000 if found.group("unit").lower() in ("m", "млн", "mln", "million") else 1000
            currency = currency_of(value.get("currency"), "") if isinstance(value, dict) else ""
            currency = currency or currency_of(text, currency_hint) or currency_hint
            amount = round(number * scale, 2)
            return {"amount": amount, "currency": currency, "approx": False} if 0 < amount <= 100_000_000 else None
    return _price_in(value, currency_hint)


def _close(a: float, b: float, tolerance: float = 0.02) -> bool:
    return abs(a - b) <= max(1.0, tolerance * max(a, b))


def normalize(raw: Any, *, locale: str = "ru", store: Optional[Provenance] = None) -> Dict[str, Any]:
    """Проверенная версия блока ```memuai-products для показа.

    Возвращает ``{"ok": True, "data": {...}, "warnings": [...]}`` или
    ``{"ok": False, "error": "..."}``. Функция чистая относительно входа:
    повторная нормализация своего же результата его не меняет.
    """
    data, error = parse_block(raw)
    if data is None:
        return {"ok": False, "error": error}
    store = store or provenance()
    warnings: List[str] = []
    loc = locale_of(data.get("locale") or locale) if data.get("locale") in ("ru", "en") else locale_of(locale)
    currency = currency_of(data.get("currency"), "") or LOCALE_CURRENCY[loc]
    rows = data.get("items") if isinstance(data.get("items"), list) else data.get("products")
    if not isinstance(rows, list):
        return {"ok": False, "error": "no items"}
    items: List[Dict[str, Any]] = []
    best_taken = False
    # Бюджет пользователя (модель переносит его в блок): потолок по ПРОВЕРЕННОЙ цене.
    raw_budget = data.get("budget") if data.get("budget") not in (None, "", 0) else data.get("max_price")
    budget = budget_in(raw_budget, currency) if raw_budget not in (None, "", 0) else None
    if budget and budget["currency"] != currency:
        warnings.append("budget currency differs from the block currency; budget ignored")
        budget = None
    for index, row in enumerate(rows):
        if len(items) >= MAX_ITEMS:
            warnings.append(f"only the first {MAX_ITEMS} items are shown")
            break
        if not isinstance(row, dict):
            continue
        name = clean_text(row.get("name") or row.get("title"), 140)
        if not name:
            warnings.append(f"item {index + 1}: no name")
            continue
        brand = clean_text(row.get("brand"), 60)
        item: Dict[str, Any] = {"id": f"p{len(items) + 1}", "name": name}
        if brand:
            item["brand"] = brand
        model = clean_text(row.get("model"), 80)
        if model:
            item["model"] = model
        badge = clean_text(row.get("badge") or row.get("tag") or row.get("label"), 28)
        best = bool(row.get("best") or row.get("best_pick") or row.get("is_best")) and not best_taken
        if best:
            best_taken = True
            item["best"] = True
        if badge:
            item["badge"] = badge
        specs = _clean_list(row.get("key_specs") or row.get("specs"), 6, 80)
        if specs:
            item["key_specs"] = specs
        summary = clean_text(row.get("summary") or row.get("description"), 700)
        if summary:
            item["summary"] = summary
        for field in ("pros", "cons"):
            values = _clean_list(row.get(field), 5, 140)
            if values:
                item[field] = values

        # Офферы: магазин по домену, цена — со страницы, если мы её видели.
        offers: List[Dict[str, Any]] = []
        offer_keys = set()
        raw_offers = row.get("offers") if isinstance(row.get("offers"), list) else []
        for offer in raw_offers[:20]:
            if not isinstance(offer, dict):
                continue
            url = public_url(offer.get("url") or offer.get("link"))
            if not url:
                warnings.append(f"{name}: offer without a valid link removed")
                continue
            sid = store_for_url(url)
            official = not sid and _brand_official(url, brand or name.split(" ")[0])
            if not sid and not official:
                warnings.append(f"{name}: offer from an unknown store removed ({_host(url)})")
                continue
            key = url_key(url)
            if key in offer_keys:
                continue
            offer_keys.add(key)
            seen = store.get(url)
            claimed = _price_in(offer.get("price"), currency)
            out: Dict[str, Any] = {
                "store_id": sid or "official",
                "store": store_name(sid, loc) if sid else (brand or _host(url)),
                "url": url,
                "availability": availability_of(offer.get("availability")),
            }
            if sid and STORES[sid].get("color"):
                out["color"] = STORES[sid]["color"]
            seen_price = seen.get("price") if seen.get("kind") == "page" or seen.get("checked_at") else None
            if isinstance(seen_price, dict) and parse_amount(seen_price.get("amount")):
                out["price"] = {"amount": parse_amount(seen_price["amount"]),
                                "currency": currency_of(seen_price.get("currency"), currency), "approx": False}
                out["verified"] = True
                if seen.get("checked_at"):
                    out["checked_at"] = int(seen["checked_at"])
                if claimed and not _close(claimed["amount"], out["price"]["amount"], 0.05):
                    warnings.append(f"{name}: price at {out['store']} replaced with the store page price")
            elif claimed:
                snippet_hit = any((snippet_price(s) or {}).get("amount") and _close(
                    snippet_price(s)["amount"], claimed["amount"]) for s in (seen.get("snippets") or []))
                out["price"] = {**claimed, "approx": True}
                if snippet_hit:
                    out["price_source"] = "snippet"
            if seen.get("availability") and seen.get("kind") == "page":
                out["availability"] = availability_of(seen["availability"])
            if seen.get("discontinued") and seen.get("kind") == "page":
                out["availability"] = "out_of_stock"
                out["discontinued"] = True
            if seen.get("title"):
                out["title"] = clean_text(seen["title"], 160)
            offers.append(out)
            if len(offers) >= MAX_OFFERS:
                break
        # Магазины, где product_lookup нашёл этот товар с ценой со страницы, но модель
        # их не перечислила: оффер добавляется из журнала (ссылка и цена — увиденные).
        checks = store.lookup_for(name, loc, model, brand)
        have = {o.get("store_id") for o in offers}
        for row in (checks or {}).get("stores") or []:
            sid, url = row.get("store_id"), public_url(row.get("url"))
            if row.get("status") != "found" or not url or sid in have or store_for_url(url) != sid \
                    or len(offers) >= MAX_OFFERS:
                continue
            seen = store.get(url)
            seen_price = seen.get("price") if isinstance(seen.get("price"), dict) else None
            if not seen_price or not parse_amount(seen_price.get("amount")) or \
                    currency_of(seen_price.get("currency"), currency) != currency:
                continue
            extra = {"store_id": sid, "store": store_name(sid, loc), "url": url, "verified": True, "added": "lookup",
                     "availability": availability_of(seen.get("availability")),
                     "price": {"amount": parse_amount(seen_price["amount"]), "currency": currency, "approx": False}}
            if STORES[sid].get("color"):
                extra["color"] = STORES[sid]["color"]
            if seen.get("checked_at"):
                extra["checked_at"] = int(seen["checked_at"])
            if seen.get("discontinued"):
                extra["availability"], extra["discontinued"] = "out_of_stock", True
            if seen.get("title"):
                extra["title"] = clean_text(seen["title"], 160)
            offers.append(extra)
            have.add(sid)
        offers.sort(key=lambda o: (not o.get("verified"), o.get("availability") == "out_of_stock",
                                   o.get("store_id") not in LOCALE_STORES[loc],
                                   (o.get("price") or {}).get("amount") or 10 ** 12))
        if offers:
            item["offers"] = offers

        # Цена карточки: лучшая подтверждённая цена в наличии, иначе оценка модели «≈».
        verified = [o for o in offers if o.get("verified") and o.get("availability") != "out_of_stock"
                    and o["price"]["currency"] == currency] or \
                   [o for o in offers if o.get("verified") and o["price"]["currency"] == currency]
        claimed_price = _price_in(row.get("price"), currency)
        if verified:
            cheapest = min(verified, key=lambda o: o["price"]["amount"])
            item["price"] = {"amount": cheapest["price"]["amount"], "currency": cheapest["price"]["currency"],
                             "approx": False, "source": cheapest["url"], "store": cheapest["store"]}
            if cheapest.get("checked_at"):
                item["price"]["checked_at"] = cheapest["checked_at"]
            if len({round(o["price"]["amount"]) for o in verified}) > 1:
                item["price"]["from"] = True
        elif claimed_price:
            item["price"] = {**claimed_price, "approx": True}
            priced = [o for o in offers if o.get("price")]
            if priced:
                item["price"]["source"] = min(priced, key=lambda o: o["price"]["amount"])["url"]
        elif offers and any(o.get("price") for o in offers):
            cheapest = min((o for o in offers if o.get("price")), key=lambda o: o["price"]["amount"])
            item["price"] = {**cheapest["price"], "approx": True, "source": cheapest["url"]}

        # Наличие: «в продаже», если хоть один проверенный оффер в наличии; «нет в
        # продаже», если все проверенные офферы закончились или товар сняли с продажи.
        checked = [o for o in offers if o.get("verified")]
        if any(o.get("availability") == "in_stock" for o in checked):
            item["availability"] = "in_stock"
        elif checked and all(o.get("availability") == "out_of_stock" for o in checked):
            item["availability"] = "unavailable"
            if any(o.get("discontinued") for o in checked):
                item["discontinued"] = True
        # Бюджет: «в бюджете» только при проверенной цене в наличии не выше потолка.
        if budget:
            fits = [o for o in checked if o.get("availability") != "out_of_stock"
                    and o["price"]["currency"] == budget["currency"]
                    and o["price"]["amount"] <= budget["amount"] * 1.0001]
            if fits:
                item["budget_status"] = "within"
                cheapest_fit = min(fits, key=lambda o: o["price"]["amount"])
                if not item.get("price") or item["price"].get("approx") or \
                        item["price"]["amount"] > cheapest_fit["price"]["amount"]:
                    item["price"] = {"amount": cheapest_fit["price"]["amount"],
                                     "currency": cheapest_fit["price"]["currency"], "approx": False,
                                     "source": cheapest_fit["url"], "store": cheapest_fit["store"]}
            elif checked and any(o.get("availability") != "out_of_stock" for o in checked):
                item["budget_status"] = "over"
            elif item.get("price") and not item["price"].get("approx") and item["price"]["amount"] > budget["amount"]:
                item["budget_status"] = "over"
            else:
                item["budget_status"] = "unverified"

        # Рейтинг: со страницы магазина/обзора или с указанным источником. Из
        # нескольких источников берётся тот, где больше оценок: «5,0 (7)» с
        # одной витрины хуже «4,9 (954)» со страницы отзывов.
        candidates: List[Dict[str, Any]] = []
        for offer in offers:
            seen = store.get(offer["url"])
            if seen.get("rating") and _rating(seen["rating"]):
                candidates.append({"value": _rating(seen["rating"]), "count": _count(seen.get("review_count")),
                                   "source": offer["url"], "verified": True})
        if row.get("rating") is not None:
            source = public_url(row.get("rating_source") or row.get("rating_url"))
            value = _rating(row.get("rating"))
            if value and source:
                seen = store.get(source)
                if seen.get("rating"):
                    candidates.append({"value": _rating(seen["rating"]), "count": _count(seen.get("review_count")),
                                       "source": source, "verified": True})
                else:
                    shown = f"{value:.1f}"
                    in_snippet = any(shown in str(text) or shown.replace(".", ",") in str(text)
                                     for text in (seen.get("snippets") or []))
                    candidates.append({"value": value, "count": _count(row.get("review_count")),
                                       "source": source, "verified": in_snippet})
            elif value:
                warnings.append(f"{name}: rating without a source removed")
        rating = max(candidates, key=lambda c: (c["verified"], c.get("count") or 0)) if candidates else None
        if rating and rating.get("value"):
            item["rating"] = rating["value"]
            if rating.get("count") is not None:
                item["review_count"] = rating["count"]
            item["rating_source"] = rating["source"]
            item["rating_store"] = store_name(store_for_url(rating["source"]), loc) if store_for_url(
                rating["source"]) else _host(rating["source"])
            if rating.get("verified"):
                item["rating_verified"] = True

        # Картинки: только http(s); если модель не дала — со страниц офферов.
        images: List[str] = []
        for image in (row.get("images") if isinstance(row.get("images"), list) else
                      [row.get("image")] if row.get("image") else []):
            url = public_url(image.get("url") if isinstance(image, dict) else image)
            if url and url not in images and not re.search(r"\.(?:svg|ico)(?:$|\?)", url, re.I):
                images.append(url)
        if not images:
            for offer in offers:
                images += [img for img in (store.get(offer["url"]).get("images") or []) if img not in images]
        if images:
            item["images"] = images[:MAX_IMAGES]

        # Отзывы: без ссылки отзыва нет; проверенный — если цитата встречалась на странице.
        reviews: List[Dict[str, Any]] = []
        raw_reviews = row.get("reviews") if isinstance(row.get("reviews"), list) else []
        for review in raw_reviews[:20]:
            if not isinstance(review, dict):
                continue
            url = public_url(review.get("url") or review.get("source_url") or review.get("link"))
            text = clean_text(review.get("text") or review.get("quote"), 320)
            if not url:
                warnings.append(f"{name}: review without a link removed")
                continue
            if len(text) < 8:
                continue
            sid = store_for_url(url)
            out = {"source": store_name(sid, loc) if sid else _host(url), "url": url, "text": text}
            author = clean_text(review.get("author"), 60)
            if author:
                out["author"] = author
            value = _rating(review.get("rating")) if review.get("rating") is not None else None
            if value:
                out["rating"] = value
            if store.text_seen(url, text):
                out["verified"] = True
            reviews.append(out)
            if len(reviews) >= MAX_REVIEWS:
                break
        reviews.sort(key=lambda r: not r.get("verified"))
        if reviews:
            item["reviews"] = reviews

        # Магазины региона без оффера: что о них известно из product_lookup.
        have = {o.get("store_id") for o in offers}
        if checks:
            status_of = {row.get("store_id"): row.get("status") for row in checks.get("stores") or []}
            missing = []
            for sid in LOCALE_STORES[loc]:
                if sid in have or sid not in status_of:
                    continue
                check = {"store_id": sid, "store": store_name(sid, loc),
                         "status": {"not_found": "not_found", "approx": "unconfirmed"}.get(status_of[sid], "unchecked")}
                found_url = public_url(next((r.get("url") for r in checks.get("stores") or []
                                             if r.get("store_id") == sid), ""))
                if check["status"] == "unconfirmed" and found_url and store_for_url(found_url) == sid:
                    check["url"] = found_url
                if STORES[sid].get("color"):
                    check["color"] = STORES[sid]["color"]
                template = STORE_USER_SEARCH.get(sid) or STORE_SEARCH.get(sid)
                if template:
                    # Поиск по самому магазину — чтобы проверить вручную (ссылка строится
                    # из названия товара, а не берётся у модели).
                    check["search_url"] = template.format(q=quote_plus(name))
                missing.append(check)
            if missing:
                item["store_checks"] = missing
        items.append(item)
    if not items:
        return {"ok": False, "error": "no valid items", "warnings": warnings[:20]}

    # «Лучший выбор» не может быть вне бюджета или не в продаже, если есть подходящий товар.
    def fitting(entry: Dict[str, Any]) -> bool:
        return entry.get("availability") != "unavailable" and (not budget or entry.get("budget_status") == "within")

    best_item = next((entry for entry in items if entry.get("best")), None)
    if best_item and not fitting(best_item):
        replacement = next((entry for entry in items if entry is not best_item and fitting(entry)), None)
        if replacement:
            best_item.pop("best", None)
            if re.search(r"лучш|best|топ|top|выбор", best_item.get("badge", ""), re.I):
                best_item.pop("badge", None)
            replacement["best"] = True
            replacement["badge"] = "Лучший выбор" if loc == "ru" else "Best pick"
            warnings.append(f"best pick moved from {best_item['name']} to {replacement['name']} (budget/availability)")
        elif budget and best_item.get("budget_status") != "within":
            best_item.pop("best", None)
            warnings.append(f"{best_item['name']}: not a best pick — no verified offer within the budget")
    for entry in items:
        if entry.get("best"):
            continue
        if entry.get("budget_status") == "over" or entry.get("availability") == "unavailable":
            if re.search(r"лучш|best|бюджет|budget|дешев|cheap|value", entry.get("badge", ""), re.I):
                entry.pop("badge", None)
    rank = {"within": 0, None: 1, "unverified": 2, "over": 3}
    items.sort(key=lambda entry: (not entry.get("best"), entry.get("availability") == "unavailable",
                                  rank.get(entry.get("budget_status"), 1)))
    for position, entry in enumerate(items):
        entry["id"] = f"p{position + 1}"
    result = {"v": SCHEMA_VERSION, "locale": loc, "currency": currency, "items": items}
    if budget:
        result["budget"] = {"amount": budget["amount"], "currency": budget["currency"]}
    for field, limit in (("query", 200), ("context", 300), ("title", 120)):
        value = clean_text(data.get(field), limit)
        if value:
            result[field] = value
    return {"ok": True, "data": result, "warnings": warnings[:20]}


# --------------------------------------------------------------------------- text fallback

def format_price(price: Optional[Dict[str, Any]], locale: str = "ru") -> str:
    if not price or not price.get("amount"):
        return ""
    amount = float(price["amount"])
    whole = abs(amount - round(amount)) < 0.005
    number = f"{amount:,.0f}" if whole else f"{amount:,.2f}"
    if locale == "ru":
        number = number.replace(",", "\u00a0").replace(".", ",")
    signs = {"RUB": "₽", "USD": "$", "EUR": "€", "GBP": "£", "KZT": "₸", "UAH": "₴"}
    code = str(price.get("currency") or "")
    sign = signs.get(code, code)
    text = f"{sign}{number}" if code in ("USD", "GBP", "EUR") and locale == "en" else f"{number}\u00a0{sign}".strip()
    return ("≈\u00a0" if price.get("approx") else "") + text


def to_markdown(data: Dict[str, Any], locale: str = "ru") -> str:
    """Карточки текстом: для экспорта, копирования и чужих поверхностей (Telegram)."""
    en = locale == "en"
    lines: List[str] = []
    for item in data.get("items") or []:
        head = f"**{item['name']}**"
        price = format_price(item.get("price"), locale)
        if price:
            head += f" — {price}"
        if item.get("rating"):
            head += f" · ★ {item['rating']:.1f}".replace(".", "," if not en else ".")
            if item.get("review_count"):
                head += f" ({item['review_count']})"
        if item.get("best"):
            head += " — " + (item.get("badge") or ("best pick" if en else "лучший выбор"))
        if item.get("availability") == "unavailable":
            head += " — " + ("not available" if en else "нет в продаже")
        elif item.get("budget_status") == "over":
            head += " — " + ("over budget" if en else "выше бюджета")
        elif item.get("budget_status") == "unverified":
            head += " — " + ("price not confirmed" if en else "цена не подтверждена")
        lines.append("- " + head)
        if item.get("summary"):
            lines.append(f"  {item['summary']}")
        for offer in (item.get("offers") or [])[:4]:
            lines.append(f"  - {offer['store']}: {format_price(offer.get('price'), locale) or '—'} — {offer['url']}")
        for check in item.get("store_checks") or []:
            state = ("not found" if en else "не найден") if check.get("status") == "not_found" \
                else ("found, price not confirmed" if en else "найден, цена не подтверждена") \
                if check.get("status") == "unconfirmed" else ("could not check" if en else "не удалось проверить")
            lines.append(f"  - {check['store']}: {state}" + (f" — {check['url']}" if check.get("url") else ""))
    return "\n".join(lines)


def markdown_fallback(text: str, locale: str = "ru") -> str:
    """Заменяет каждый блок ```memuai-products в ответе его текстовой версией.

    Для поверхностей без карточек (Telegram-бот, Mini App, экспорт). Блок без
    проверки (битый JSON) убирается целиком, а не показывается кодом.
    """
    def replace(match: "re.Match[str]") -> str:
        result = normalize(match.group(0), locale=locale)
        return ("\n" + to_markdown(result["data"], locale) + "\n") if result.get("ok") else ""

    value = BLOCK_RE.sub(replace, str(text or ""))
    # Незаконченный блок в конце (ответ оборвался) тоже не показывается.
    value = re.sub(r"```[ \t]*memuai[-_ ]?products\b[\s\S]*$", "", value, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", value).strip()
