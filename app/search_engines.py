"""Каталог источников веб-поиска — один на Proxy, MemuAI и MemuCode.

Здесь только описание: какие источники есть, бесплатны ли они, нужен ли ключ
или адрес. Сами запросы — в :mod:`app.websearch`. Модуль ничего не
импортирует из приложения, поэтому его безопасно читать из настроек.

``key``: ``""`` — ключ не нужен, ``"required"`` — без ключа источник не
работает, ``"optional"`` — ключ можно указать (свой сервер с защитой).
``url``: ``""`` — адрес не нужен, ``"server"`` — адрес своего сервера,
``"cx"`` — идентификатор поисковой системы Google.
"""
from __future__ import annotations

from typing import Any, Dict, List

ENGINES: Dict[str, Dict[str, Any]] = {
    "auto": {
        "label": "Авто: Kiro → Bing и DuckDuckGo", "label_en": "Auto: Kiro → Bing and DuckDuckGo", "group": "free",
        "note": "Без ключа. Если в Proxy есть аккаунт Kiro, используется его встроенный поиск (как в Kiro IDE); "
                "иначе Bing и DuckDuckGo спрашиваются одновременно — берётся тот, кто ответил первым.",
        "note_en": "No key. With a Kiro account in Proxy its built-in search is used (as in Kiro IDE); "
                   "otherwise Bing and DuckDuckGo are asked at the same time and the first answer wins.",
    },
    "kiro": {
        "label": "Kiro", "label_en": "Kiro", "group": "free",
        "note": "Встроенный веб-поиск Kiro. Нужен аккаунт Kiro в MemuAI Proxy; ключ не нужен.",
        "note_en": "Kiro's built-in web search. Needs a Kiro account in MemuAI Proxy; no key.",
    },
    "bing": {
        "label": "Bing", "label_en": "Bing", "group": "free",
        "note": "Без ключа и без аккаунтов. Быстрый, устойчив к частым запросам, понимает русский.",
        "note_en": "No key and no accounts. Fast, tolerant of frequent queries, understands Russian.",
    },
    "duckduckgo": {
        "label": "DuckDuckGo", "label_en": "DuckDuckGo", "group": "free",
        "note": "Без ключа и без аккаунтов. При частых запросах DuckDuckGo на время просит подтвердить, "
                "что вы человек, — тогда поможет «Авто» или Bing.",
        "note_en": "No key and no accounts. After frequent requests DuckDuckGo asks to confirm you are "
                   "human for a while — «Auto» or Bing help then.",
    },
    "yahoo": {
        "label": "Yahoo", "label_en": "Yahoo", "group": "free",
        "note": "Без ключа. Выдача на индексе Bing; бывает медленным или недоступным в некоторых странах — "
                "в «Авто» это запасной источник.",
        "note_en": "No key. Results from the Bing index; can be slow or unreachable in some countries — "
                   "it is the last resort in «Auto».",
    },
    "wikipedia": {
        "label": "Википедия", "label_en": "Wikipedia", "group": "free",
        "note": "Без ключа. Только энциклопедические статьи: определения, биографии, история. "
                "Язык раздела выбирается по языку запроса.",
        "note_en": "No key. Encyclopedia articles only: definitions, biographies, history. "
                   "The language edition follows the language of the query.",
    },
    "searxng": {
        "label": "SearXNG", "label_en": "SearXNG", "group": "server", "key": "optional", "url": "server",
        "url_hint": "https://search.example.com",
        "note": "Свой или публичный сервер SearXNG (в нём должен быть включён формат JSON). "
                "Укажите корневой адрес или путь /search.",
        "note_en": "Your own or a public SearXNG server (the JSON format must be enabled). "
                   "Enter the root address or the /search path.",
    },
    "tavily": {
        "label": "Tavily", "label_en": "Tavily", "group": "key", "key": "required", "key_hint": "tvly-…",
        "site": "tavily.com",
        "note": "Поиск, сделанный для ИИ: чистые выдержки из страниц. Есть бесплатный месячный лимит.",
        "note_en": "Search built for AI: clean page extracts. Has a free monthly allowance.",
    },
    "brave": {
        "label": "Brave Search API", "label_en": "Brave Search API", "group": "key", "key": "required",
        "key_hint": "BSA…", "site": "brave.com/search/api",
        "note": "Официальный API Brave с собственным индексом. Есть бесплатный тариф.",
        "note_en": "Brave's official API with its own index. Has a free plan.",
    },
    "exa": {
        "label": "Exa", "label_en": "Exa", "group": "key", "key": "required", "site": "exa.ai",
        "note": "Смысловой (нейросетевой) поиск: находит страницы по описанию, а не по словам.",
        "note_en": "Neural search: finds pages by meaning rather than by keywords.",
    },
    "serper": {
        "label": "Google · Serper", "label_en": "Google · Serper", "group": "key", "key": "required",
        "site": "serper.dev",
        "note": "Выдача Google через serper.dev. Быстро и недорого; есть пробные запросы.",
        "note_en": "Google results through serper.dev. Fast and cheap; trial queries included.",
    },
    "serpapi": {
        "label": "Google · SerpApi", "label_en": "Google · SerpApi", "group": "key", "key": "required",
        "site": "serpapi.com",
        "note": "Выдача Google через SerpApi. Есть бесплатный месячный лимит.",
        "note_en": "Google results through SerpApi. Has a free monthly allowance.",
    },
    "google": {
        "label": "Google · Programmable Search", "label_en": "Google · Programmable Search", "group": "key",
        "key": "required", "url": "cx", "key_hint": "AIza…", "url_hint": "0123456789abcdef0",
        "site": "programmablesearchengine.google.com",
        "note": "Официальный Custom Search JSON API: ключ API и идентификатор поисковой системы (cx), 100 запросов в день бесплатно. "
                "С 20.01.2026 Google не принимает новых клиентов и отключает API 1 января 2027 — подходит только тем, у кого он уже был.",
        "note_en": "The official Custom Search JSON API: an API key and a search engine ID (cx), 100 queries a day free. "
                   "Since 20 Jan 2026 Google takes no new customers and shuts the API down on 1 January 2027 — only for existing users.",
    },
    "jina": {
        "label": "Jina Search", "label_en": "Jina Search", "group": "key", "key": "required",
        "key_hint": "jina_…", "site": "jina.ai",
        "note": "Поиск Jina AI (s.jina.ai). Ключ с бесплатным стартовым балансом.",
        "note_en": "Jina AI search (s.jina.ai). The key comes with a free starting balance.",
    },
    "perplexity": {
        "label": "Perplexity Search", "label_en": "Perplexity Search", "group": "key", "key": "required",
        "key_hint": "pplx-…", "site": "perplexity.ai/settings/api",
        "note": "Search API Perplexity: ранжированные результаты с выдержками, без ответа модели.",
        "note_en": "Perplexity's Search API: ranked results with extracts, without a model answer.",
    },
    "linkup": {
        "label": "Linkup", "label_en": "Linkup", "group": "key", "key": "required", "site": "linkup.so",
        "note": "Поиск для ИИ с доступом к части платных изданий. Есть бесплатный лимит.",
        "note_en": "Search for AI with access to some premium publishers. Has a free allowance.",
    },
    "firecrawl": {
        "label": "Firecrawl", "label_en": "Firecrawl", "group": "key", "key": "required",
        "key_hint": "fc-…", "site": "firecrawl.dev",
        "note": "Поиск Firecrawl. Есть бесплатные кредиты.",
        "note_en": "Firecrawl search. Free credits included.",
    },
    "kagi": {
        "label": "Kagi", "label_en": "Kagi", "group": "key", "key": "required", "site": "kagi.com/settings?p=api",
        "note": "Платный поиск без рекламы и SEO-мусора. Нужен доступ к Search API.",
        "note_en": "A paid search without ads and SEO spam. Needs Search API access.",
    },
}

DEFAULT_ENGINE = "auto"

PROXY_ONLY = ("auto", "kiro")

FREE_RACE = ("bing", "duckduckgo")

FREE_CHAIN = FREE_RACE + ("yahoo",)

def engine_ids() -> tuple:
    return tuple(ENGINES)

def needs_key(engine: str) -> bool:
    return ENGINES.get(engine, {}).get("key") == "required"

def needs_url(engine: str) -> bool:
    return bool(ENGINES.get(engine, {}).get("url"))

def catalog() -> List[Dict[str, Any]]:
    """Описание источников для окон настроек (без секретов — их тут и нет)."""
    return [{"id": key, "label": row["label"], "label_en": row["label_en"], "group": row["group"],
             "key": row.get("key", ""), "url": row.get("url", ""),
             "key_hint": row.get("key_hint", ""), "url_hint": row.get("url_hint", ""),
             "site": row.get("site", ""), "note": row["note"], "note_en": row["note_en"]}
            for key, row in ENGINES.items()]

def ready(engine: str, api_key: str = "", url: str = "") -> bool:
    """Хватает ли настроек, чтобы источник мог ответить."""
    if engine not in ENGINES:
        return False
    if needs_key(engine) and not str(api_key or "").strip():
        return False
    if needs_url(engine) and not str(url or "").strip():
        return False
    return True
