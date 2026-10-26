# -*- coding: utf-8 -*-
"""Конвейер ИИ-подборки для панелей (Раунд 22): новости и любые «ленты» по теме.

Отзыв пользователя: панель «новости ИИ каждое утро» брала три русскоязычных сайта, показывала
мусор и только OpenAI, а важный релиз дня пропустила. Поэтому лента собирается так:

1. **План источников** (:func:`plan_for`) — по теме и языку источников, а не по языку интерфейса.
   Для известных тем (ИИ, технологии, наука, крипто, безопасность, космос, игры) — проверенный
   каталог первоисточников: официальные блоги (RSS, где он есть, иначе ссылки со страницы новостей),
   Hacker News (Algolia API), Reddit (``.rss``), профильные СМИ, плюс несколько узких поисковых
   запросов на английском. Для своей темы план предлагает модель, адреса лент проверяются чтением.
   X/Twitter не читается: надёжного открытого доступа нет — честно пропускаем.
2. **Сбор кандидатов** (:func:`collect`) — все источники параллельно; упавший источник — не стена,
   а строчка в ``errors``. Стена — только если не ответил ни один.
3. **Склейка** — канонизация адресов (utm, www, amp, …), кластеризация одной истории из разных
   источников (общий адрес, похожесть заголовков, общие «имена с версиями» вроде «Haiku 5.5»).
4. **Оценка** — авторитет источника, подтверждение несколькими площадками, отклик (очки HN, место
   в топе Reddit), свежесть, сигнал темы; штраф «корпоративной воды» (кейсы клиентов, вебинары,
   партнёрства) и пометка слухов. Если доступна модель — рубрика значимости/достоверности поверх
   эвристики, она же переводит заголовок и пишет одну фразу пересказа на языке пользователя.
5. **Разнообразие** — не больше ~40 % ленты об одной компании и с одного сайта.
6. **Уже показанное** не повторяется: хранилище ``seen`` в записи панели (адреса и нормализованные
   заголовки); история возвращается только при крупном обновлении (новые источники, рост оценки).
7. У каждой записи хранится «почему здесь» — для прозрачности в подробном списке.

Сеть, модель и поиск передаются функциями (``fetch_text``, ``search``, ``ask_model``), поэтому
весь конвейер проверяется на фикстурах без сети.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import math
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse

FetchText = Callable[[str], Awaitable[str]]
SearchFn = Callable[[str, int], Awaitable[Dict[str, Any]]]
AskModel = Callable[[str, str], Awaitable[Dict[str, Any]]]
Progress = Callable[[str, str], None]

MAX_SEEN = 400
SEEN_DAYS = 21
MAX_ITEMS = 10

AUTHORITY = {"official": 1.0, "primary": 0.9, "media": 0.8, "aggregator": 0.65, "community": 0.6, "search": 0.42}

HOSTS: Dict[str, Tuple[str, float]] = {

    "openai.com": ("official", 1.0), "anthropic.com": ("official", 1.0), "deepmind.google": ("official", 1.0),
    "blog.google": ("official", 0.95), "ai.google.dev": ("official", 0.95), "ai.meta.com": ("official", 1.0),
    "about.fb.com": ("official", 0.9), "mistral.ai": ("official", 1.0), "x.ai": ("official", 1.0),
    "deepseek.com": ("official", 1.0), "api-docs.deepseek.com": ("official", 1.0), "qwenlm.github.io": ("official", 1.0),
    "qwen.ai": ("official", 1.0), "huggingface.co": ("primary", 0.9), "github.com": ("primary", 0.8),
    "arxiv.org": ("primary", 0.8), "developers.openai.com": ("official", 1.0), "docs.anthropic.com": ("official", 1.0),
    "nvidia.com": ("official", 0.9), "blogs.nvidia.com": ("official", 0.9), "microsoft.com": ("official", 0.9),
    "blogs.microsoft.com": ("official", 0.9), "apple.com": ("official", 0.9), "aws.amazon.com": ("official", 0.85),
    "simonwillison.net": ("primary", 0.85), "labs.google": ("official", 0.9),

    "theverge.com": ("media", 0.82), "techcrunch.com": ("media", 0.8), "arstechnica.com": ("media", 0.85),
    "reuters.com": ("media", 0.92), "bloomberg.com": ("media", 0.9), "nytimes.com": ("media", 0.88),
    "wsj.com": ("media", 0.88), "ft.com": ("media", 0.88), "cnbc.com": ("media", 0.8), "wired.com": ("media", 0.8),
    "technologyreview.com": ("media", 0.85), "venturebeat.com": ("media", 0.72), "the-decoder.com": ("media", 0.72),
    "theinformation.com": ("media", 0.85), "axios.com": ("media", 0.8), "engadget.com": ("media", 0.75),
    "404media.co": ("media", 0.78), "bbc.com": ("media", 0.88), "bbc.co.uk": ("media", 0.88), "apnews.com": ("media", 0.9),
    "theguardian.com": ("media", 0.84), "news.mit.edu": ("primary", 0.85), "nature.com": ("primary", 0.92),
    "science.org": ("primary", 0.92), "sciencedaily.com": ("media", 0.72), "quantamagazine.org": ("media", 0.86),
    "phys.org": ("media", 0.7), "coindesk.com": ("media", 0.8), "cointelegraph.com": ("media", 0.66),
    "decrypt.co": ("media", 0.7), "theblock.co": ("media", 0.8), "krebsonsecurity.com": ("primary", 0.9),
    "bleepingcomputer.com": ("media", 0.84), "thehackernews.com": ("media", 0.72), "nasa.gov": ("official", 1.0),
    "spacenews.com": ("media", 0.84), "space.com": ("media", 0.7), "esa.int": ("official", 1.0),
    "polygon.com": ("media", 0.78), "eurogamer.net": ("media", 0.78), "ign.com": ("media", 0.72),
    "marktechpost.com": ("aggregator", 0.55),

    "news.ycombinator.com": ("community", 0.6), "reddit.com": ("community", 0.55), "x.com": ("community", 0.5),
    "twitter.com": ("community", 0.5),

    "habr.com": ("media", 0.66), "rbc.ru": ("media", 0.78), "kommersant.ru": ("media", 0.8), "interfax.ru": ("media", 0.82),
    "tass.ru": ("media", 0.8), "ria.ru": ("media", 0.74), "vc.ru": ("aggregator", 0.5), "3dnews.ru": ("media", 0.6),
    "ixbt.com": ("media", 0.58), "cnews.ru": ("media", 0.6), "securitylab.ru": ("media", 0.58),
}
HOST_NAMES = {
    "openai.com": "OpenAI", "anthropic.com": "Anthropic", "deepmind.google": "Google DeepMind", "blog.google": "Google",
    "ai.meta.com": "Meta AI", "mistral.ai": "Mistral AI", "x.ai": "xAI", "deepseek.com": "DeepSeek", "qwenlm.github.io": "Qwen",
    "huggingface.co": "Hugging Face", "github.com": "GitHub", "arxiv.org": "arXiv", "theverge.com": "The Verge",
    "techcrunch.com": "TechCrunch", "arstechnica.com": "Ars Technica", "reuters.com": "Reuters", "bloomberg.com": "Bloomberg",
    "nytimes.com": "The New York Times", "wsj.com": "WSJ", "ft.com": "Financial Times", "cnbc.com": "CNBC", "wired.com": "Wired",
    "technologyreview.com": "MIT Technology Review", "venturebeat.com": "VentureBeat", "the-decoder.com": "The Decoder",
    "theinformation.com": "The Information", "axios.com": "Axios", "engadget.com": "Engadget", "404media.co": "404 Media",
    "bbc.com": "BBC", "apnews.com": "AP", "theguardian.com": "The Guardian", "nature.com": "Nature",
    "news.ycombinator.com": "Hacker News", "reddit.com": "Reddit", "simonwillison.net": "Simon Willison",
    "developers.openai.com": "OpenAI", "labs.google": "Google Labs", "nasa.gov": "NASA", "habr.com": "Хабр",
}

def source_name(url: str) -> str:
    host = host_of(url)
    parts = host.split(".")
    for i in range(len(parts) - 1):
        key = ".".join(parts[i:])
        if key in HOST_NAMES:
            return HOST_NAMES[key]
    return host

RU_HOSTS = ("habr.com", "rbc.ru", "kommersant.ru", "interfax.ru", "tass.ru", "ria.ru", "vc.ru", "3dnews.ru",
            "ixbt.com", "cnews.ru", "securitylab.ru")

def host_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    for prefix in ("www.", "m.", "amp.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host

def host_authority(url: str) -> Tuple[str, float]:
    host = host_of(url)
    parts = host.split(".")
    for i in range(len(parts) - 1):
        key = ".".join(parts[i:])
        if key in HOSTS:
            return HOSTS[key]
    if host.endswith(".gov") or host.endswith(".edu"):
        return ("primary", 0.8)
    return ("search", 0.4)

def _feed(url, name, kind, scoped=True, **extra):
    return {"url": url, "name": name, "kind": kind, "scoped": scoped, **extra}

TOPICS: Dict[str, Dict[str, Any]] = {
    "ai": {
        "label_ru": "ИИ", "label_en": "AI",
        "match": r"\b(ai|ии|llm|llms|нейросет\w*|нейронк\w*|искусственн\w* интеллект\w*|artificial intelligence|gpt|chatgpt|"
                 r"claude|gemini|машинн\w* обучени\w*|machine learning|openai|anthropic|deepmind|генеративн\w*|"
                 r"языков\w* модел\w*|language models?)\b",
        "keywords": ["ai", "a.i.", "llm", "llms", "gpt", "chatgpt", "openai", "anthropic", "claude", "gemini", "deepmind",
                     "mistral", "llama", "grok", "xai", "deepseek", "qwen", "copilot", "model", "models", "agent", "agents",
                     "agentic", "neural", "machine learning", "transformer", "diffusion", "inference", "reasoning",
                     "chatbot", "hugging face", "open-weights", "weights", "fine-tuning", "benchmark", "sora", "veo",
                     "midjourney", "nvidia", "gpu", "synthid", "codex", "artificial intelligence", "ии", "нейросет",
                     "модель", "искусственн"],
        "signal": r"\b(launch(?:es|ed)?|releas(?:e|es|ed)|introduc(?:e|es|ing)|announc(?:e|es|ed|ing)|unveil\w*|"
                  r"open[- ]?(?:source|weights?)|available|preview|beta|benchmark\w*|model|api|update|version|"
                  r"\d+(?:\.\d+)+|gpt-?\d|raises?|acquir\w*|lawsuit|ban\w*|regulat\w*)\b",
        "entities": {
            "OpenAI": ["openai", "chatgpt", "gpt-", "gpt ", "sora", "codex", "sam altman"],
            "Anthropic": ["anthropic", "claude"],
            "Google": ["google", "deepmind", "gemini", "veo", "gemma", "synthid"],
            "Meta": ["meta ai", "meta's", "meta ", "llama"],
            "Mistral": ["mistral"], "xAI": ["xai", "x.ai", "grok"], "DeepSeek": ["deepseek"],
            "Alibaba": ["qwen", "alibaba"], "Microsoft": ["microsoft", "copilot"], "Nvidia": ["nvidia"],
            "Apple": ["apple"], "Amazon": ["amazon", "aws", "bedrock"], "Hugging Face": ["hugging face", "huggingface"],
        },
        "feeds": [
            _feed("https://openai.com/news/rss.xml", "OpenAI", "official"),
            _feed("https://deepmind.google/blog/rss.xml", "Google DeepMind", "official"),
            _feed("https://blog.google/technology/ai/rss/", "Google AI", "official"),
            _feed("https://huggingface.co/blog/feed.xml", "Hugging Face", "primary"),
            _feed("https://qwenlm.github.io/blog/index.xml", "Qwen", "official"),
            _feed("https://simonwillison.net/atom/everything/", "Simon Willison", "primary", scoped=False),
            _feed("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "The Verge", "media"),
            _feed("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch", "media"),
            _feed("https://arstechnica.com/ai/feed/", "Ars Technica", "media"),
            _feed("https://www.technologyreview.com/topic/artificial-intelligence/feed", "MIT Technology Review", "media"),
            _feed("https://the-decoder.com/feed/", "The Decoder", "media"),
        ],
        "pages": [
            {"url": "https://www.anthropic.com/news", "name": "Anthropic", "kind": "official", "pattern": r"^/news/[a-z0-9-]{4,}$"},
            {"url": "https://mistral.ai/news", "name": "Mistral AI", "kind": "official", "pattern": r"^/news/[a-z0-9-]{4,}/?$"},
            {"url": "https://x.ai/news", "name": "xAI", "kind": "official", "pattern": r"^/news/[a-z0-9-]{3,}$"},
            {"url": "https://ai.meta.com/blog/", "name": "Meta AI", "kind": "official", "pattern": r"^/blog/[a-z0-9-]{4,}/?$"},
            {"url": "https://api-docs.deepseek.com/news/news", "name": "DeepSeek", "kind": "official", "pattern": r"^/news/news\d{4,}$"},
        ],
        "reddit": ["LocalLLaMA", "MachineLearning", "singularity", "OpenAI", "ClaudeAI"],
        "hn": {"min_points": 60},
        "queries": ["new AI model release", "OpenAI announces", "Anthropic Claude announcement",
                    "Google Gemini release", "open-weights LLM release"],
    },
    "tech": {
        "label_ru": "Технологии", "label_en": "Tech",
        "match": r"\b(технолог\w*|tech|гаджет\w*|gadgets?|смартфон\w*|smartphones?|it[- ]новост\w*|софт\w*|software)\b",
        "keywords": [], "signal": r"\b(launch\w*|releas\w*|announc\w*|unveil\w*|update|acquir\w*|lawsuit|outage|breach)\b",
        "entities": {"Apple": ["apple", "iphone", "mac"], "Google": ["google", "android", "pixel"], "Microsoft": ["microsoft", "windows"],
                     "Meta": ["meta", "facebook", "instagram"], "Amazon": ["amazon"], "Samsung": ["samsung"]},
        "feeds": [_feed("https://www.theverge.com/rss/index.xml", "The Verge", "media"),
                  _feed("https://feeds.arstechnica.com/arstechnica/index", "Ars Technica", "media"),
                  _feed("https://techcrunch.com/feed/", "TechCrunch", "media"),
                  _feed("https://www.engadget.com/rss.xml", "Engadget", "media")],
        "pages": [], "reddit": ["technology"], "hn": {"min_points": 120}, "queries": ["tech news today"],
    },
    "science": {
        "label_ru": "Наука", "label_en": "Science",
        "match": r"\b(наук\w*|научн\w*|science|scientific|исследовани\w*|research|физик\w*|биолог\w*|physics|biology)\b",
        "keywords": [], "signal": r"\b(discover\w*|study|finds?|first|breakthrough|nobel|paper)\b", "entities": {},
        "feeds": [_feed("https://www.nature.com/nature.rss", "Nature", "primary"),
                  _feed("https://www.quantamagazine.org/feed/", "Quanta", "media"),
                  _feed("https://www.sciencedaily.com/rss/all.xml", "ScienceDaily", "media"),
                  _feed("https://phys.org/rss-feed/", "Phys.org", "media")],
        "pages": [], "reddit": ["science"], "hn": {"min_points": 150}, "queries": ["science breakthrough this week"],
    },
    "crypto": {
        "label_ru": "Крипто", "label_en": "Crypto",
        "match": r"\b(крипт\w*|crypto\w*|bitcoin|биткоин\w*|ethereum|блокчейн\w*|blockchain|defi|web3)\b",
        "keywords": ["bitcoin", "btc", "ethereum", "eth", "crypto", "stablecoin", "defi", "sec", "etf", "solana", "token", "blockchain"],
        "signal": r"\b(etf|sec|hack\w*|exploit|approv\w*|launch\w*|record|ban\w*|regulat\w*)\b",
        "entities": {"Bitcoin": ["bitcoin", "btc"], "Ethereum": ["ethereum", "eth"], "Solana": ["solana"], "Binance": ["binance"],
                     "Coinbase": ["coinbase"], "Tether": ["tether", "usdt"]},
        "feeds": [_feed("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk", "media"),
                  _feed("https://decrypt.co/feed", "Decrypt", "media"),
                  _feed("https://cointelegraph.com/rss", "Cointelegraph", "media")],
        "pages": [], "reddit": ["CryptoCurrency"], "hn": {"min_points": 100}, "queries": ["crypto market news today"],
    },
    "security": {
        "label_ru": "Кибербезопасность", "label_en": "Security",
        "match": r"\b(кибербез\w*|безопасност\w*|security|cyber\w*|уязвимост\w*|vulnerabilit\w*|хакер\w*|hackers?|ransomware|утечк\w*)\b",
        "keywords": [], "signal": r"\b(cve-\d+|zero-?day|breach|ransomware|exploit\w*|patch\w*|arrest\w*)\b", "entities": {},
        "feeds": [_feed("https://krebsonsecurity.com/feed/", "Krebs on Security", "primary"),
                  _feed("https://www.bleepingcomputer.com/feed/", "BleepingComputer", "media"),
                  _feed("https://feeds.feedburner.com/TheHackersNews", "The Hacker News", "media")],
        "pages": [], "reddit": ["netsec"], "hn": {"min_points": 100}, "queries": ["security vulnerability news"],
    },
    "space": {
        "label_ru": "Космос", "label_en": "Space",
        "match": r"\b(космос\w*|космич\w*|space|nasa|spacex|роскосмос\w*|ракет\w*|rocket\w*|astronom\w*|астроном\w*)\b",
        "keywords": [], "signal": r"\b(launch\w*|landing|mission|orbit|starship|artemis)\b", "entities": {},
        "feeds": [_feed("https://www.nasa.gov/news-release/feed/", "NASA", "official"),
                  _feed("https://spacenews.com/feed/", "SpaceNews", "media")],
        "pages": [], "reddit": ["space"], "hn": {"min_points": 120}, "queries": ["space launch news"],
    },
    "gaming": {
        "label_ru": "Игры", "label_en": "Gaming",
        "match": r"\b(игр\w*|gaming|games?|видеоигр\w*|playstation|xbox|nintendo|steam)\b",
        "keywords": [], "signal": r"\b(announc\w*|releas\w*|trailer|launch\w*|delay\w*|review)\b", "entities": {},
        "feeds": [_feed("https://www.polygon.com/rss/index.xml", "Polygon", "media"),
                  _feed("https://www.eurogamer.net/feed", "Eurogamer", "media")],
        "pages": [], "reddit": ["Games"], "hn": {"min_points": 150}, "queries": ["video game news today"],
    },
}

FEED_RE = re.compile(r"(?i)\b(новост\w*|news|дайджест\w*|digest|headlines?|заголовк\w*|что нового|what'?s new|"
                     r"анонс\w*|релиз\w*|releases?|обновлени\w*|updates?|лента|feed|события|events?)\b")

FLUFF_RE = re.compile(
    r"(?i)\b(customer stor\w*|case stud\w*|how [\w .'-]{1,40} uses?|scales? claude|scales? (?:ai|gpt)|"
    r"partner(?:s|ing|ship)? with|in partnership|webinar|join us|register now|event recap|recap|summit|"
    r"conference|hackathon|careers?|we'?re hiring|hiring|academy|program(?:me)?\b|certification|"
    r"fellowship|grant(?:s)? program|award|sponsor\w*|newsletter|podcast|livestream|ama\b|"
    r"embedded evaluation|frontier academy|verification program|office in|opens? (?:an? )?office|"
    r"expands? (?:to|into)|kicks? off|celebrat\w*|spotlight|meet the|behind the scenes|"
    r"кейс\w*|вебинар\w*|партн[её]рств\w*|мероприят\w*|конференци\w*|ваканси\w*|"

    r"best [\w '-]{2,40} (?:of|for|in) 20\d\d|deals?\b|\d+% off|discount\w*|buying guide|gift guide|"
    r"лучши[ехй] [\w -]{2,40} 20\d\d|скидк\w*|распродаж\w*|подборк\w*)")
RUMOR_RE = re.compile(r"(?i)\b(rumou?r\w*|reportedly|leak(?:s|ed)?|allegedly|could launch|might|speculat\w*|"
                      r"insiders?|sources say|слух\w*|утечк\w*|якобы|по данным источников|инсайд\w*)\b")

STOP = set("""a an the of to in on for and or with by at from as is are was were be been it its this that these those
into over under about after before new how why what when who will can could would should says said vs via just now
more most than then they their them our your you we i he she his her not no yes up out off all any one two
и в во на по с со к ко от до из за для о об а но или что как это эта этот эти же ли не ни бы был была были будет
уже ещё еще так там тут его её их ее при про под над без""".split())

HN_RE = re.compile(r"(?i)\bhacker\s?news\b|\bycombinator\b|news\.ycombinator|\bHN\b|хакер\s?ньюс")

def wants_hn(text: str) -> bool:
    """The brief names Hacker News itself (Round 31): «Top 5 Hacker News stories» is HN's front page, not the
    security topic («hacker» matched it and the panel showed The Hacker News/Krebs instead)."""
    return bool(HN_RE.search(str(text or "")))

def detect_topic(text: str) -> str:
    """Тема по описанию панели: ключ каталога или ``""`` (своя тема)."""
    low = HN_RE.sub(" ", str(text or "")).lower()
    best, hits = "", 0
    for key, topic in TOPICS.items():
        found = len(re.findall(topic["match"], low))
        if found > hits:
            best, hits = key, found
    return best

def is_feed_brief(text: str) -> bool:
    """Описание просит ленту (новости, релизы, дайджест), а не одно значение."""
    return bool(FEED_RE.search(str(text or "")))

def wanted_count(text: str, default: int = 5) -> int:
    match = re.search(r"(?i)(?:топ|top)[- ]?(\d{1,2})|(\d{1,2})\s*(?:главн\w*|последн\w*|свеж\w*)?\s*(?:новост\w*|news|items?|заголовк\w*|headlines?)",
                      str(text or ""))
    if match:
        value = int(match.group(1) or match.group(2))
        if 1 <= value <= MAX_ITEMS:
            return value
    return default

def plan_for(subject: str, *, topic: str = "", count: int = 0, hours: int = 0, out_lang: str = "ru",
             extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """План источников. ``extra`` — то, что предложил планировщик-модель для своей темы."""
    key = topic if topic in TOPICS else detect_topic(subject)
    count = max(1, min(MAX_ITEMS, int(count or wanted_count(subject))))
    plan: Dict[str, Any] = {"version": 2, "topic": key, "subject": str(subject or "")[:300], "count": count,
                            "hours": max(6, min(24 * 7, int(hours or 36))), "out_lang": "en" if out_lang == "en" else "ru",
                            "feeds": [], "pages": [], "reddit": [], "hn": None, "queries": [], "keywords": [],
                            "entities": {}, "source_lang": "en", "global": True}
    if key:
        pack = TOPICS[key]
        plan.update({"feeds": [dict(f) for f in pack["feeds"]], "pages": [dict(p) for p in pack["pages"]],
                     "reddit": list(pack["reddit"]), "hn": dict(pack["hn"]) if pack.get("hn") else None,
                     "queries": list(pack["queries"]), "keywords": list(pack["keywords"]),
                     "entities": pack["entities"], "label": pack["label_" + plan["out_lang"]]})
    extra = extra if isinstance(extra, dict) else {}
    if extra:
        lang = str(extra.get("source_lang") or "en")[:5].lower()
        plan["source_lang"] = lang if re.fullmatch(r"[a-z]{2}", lang) else "en"
        plan["global"] = extra.get("global") is not False
        for url in (extra.get("feeds") or [])[:6] if isinstance(extra.get("feeds"), list) else []:
            if isinstance(url, str) and re.match(r"^https?://", url):
                plan["feeds"].append(_feed(url[:400], host_of(url), host_authority(url)[0], scoped=True, proposed=True))
        for sub in (extra.get("subreddits") or [])[:4] if isinstance(extra.get("subreddits"), list) else []:
            name = re.sub(r"^/?r/", "", str(sub or ""))
            if re.fullmatch(r"[A-Za-z0-9_]{2,40}", name) and name not in plan["reddit"]:
                plan["reddit"].append(name)
        queries = [str(q).strip()[:160] for q in (extra.get("queries") or []) if str(q).strip()] \
            if isinstance(extra.get("queries"), list) else []
        plan["queries"] = list(dict.fromkeys(queries + plan["queries"]))[:6]
        words = [str(w).strip().lower()[:40] for w in (extra.get("keywords") or []) if str(w).strip()] \
            if isinstance(extra.get("keywords"), list) else []
        plan["keywords"] = list(dict.fromkeys(plan["keywords"] + words))[:60]
        if not plan.get("hn") and extra.get("hn") is not False and plan["global"]:
            plan["hn"] = {"min_points": 80}
        plan["label"] = str(extra.get("label") or plan.get("label") or "")[:40]
    if wants_hn(subject) and not key:

        plan.update({"hn": {"min_points": 40}, "hours": min(plan["hours"], 24), "queries": [], "keywords": [],
                     "reddit": [], "label": "Hacker News", "source_lang": "en", "global": True, "named": "hn"})
        return plan
    if not plan["queries"]:
        plan["queries"] = [plan["subject"][:160]] if plan["subject"] else []
    if not plan["keywords"]:
        plan["keywords"] = [t for t in tokens(plan["subject"]) if len(t) > 3][:12]
    return plan

PLANNER_PROMPT = (
    "You plan the SOURCES for a small news panel on a user's own topic. Think about where the best, most "
    "trusted, PRIMARY coverage of this topic lives — independent of the user's interface language: a global "
    "topic is covered best in English; a local topic (a city, a country's politics, a local team) in that "
    "place's language. Return JSON only: {\"label\":\"2-3 word topic label in the user's language\","
    "\"global\":true|false,\"source_lang\":\"en|ru|de|…\",\"queries\":[\"3-5 SPECIFIC search queries in "
    "source_lang, each about a different angle, no generic 'news' alone\"],\"feeds\":[\"RSS/Atom URLs of "
    "official blogs or reputable outlets you are confident exist\"],\"subreddits\":[\"names\"],"
    "\"keywords\":[\"10-20 words that an on-topic headline contains, in source_lang AND English\"],"
    "\"hn\":true|false}. Never invent feed URLs you are not sure about — fewer is better.")

def _strip_xml(text: str) -> str:
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", str(text or "").strip()[:2_000_000])
    text = re.sub(r"<!DOCTYPE[^\[>]*\[[\s\S]*?\]\s*>", "", text, flags=re.I)
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=re.I)
    if "<!ENTITY" in text.upper():
        raise ValueError("feed declares XML entities")
    return text

def _plain(value: str, limit: int = 400) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    value = re.sub(r"\s+", " ", value)

    value = re.sub(r"\s+([,;:!?…)\]»]|\.(?=\s|$))", r"\1", value)
    value = re.sub(r"([(\[«])\s+", r"\1", value)
    return value.strip()[:limit]

def parse_when(value: str) -> Optional[float]:
    value = str(value or "").strip()
    if not value:
        return None
    moment = None
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
            if match:
                try:
                    moment = datetime.fromisoformat(match.group(1))
                except ValueError:
                    moment = None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        return moment.timestamp()
    except (OverflowError, OSError, ValueError):
        return None

def parse_feed_items(text: str, limit: int = 40) -> List[Dict[str, Any]]:
    """RSS 2.0 / Atom / RDF → [{title, url, published, summary, links}] (безопасно: без сущностей)."""
    try:
        root = ET.fromstring(_strip_xml(text))
    except ET.ParseError as exc:
        raise ValueError(f"not an RSS/Atom feed ({exc})") from exc

    def local(tag: str) -> str:
        return str(tag).rsplit("}", 1)[-1].lower()

    def child(node, name):
        for item in list(node):
            if local(item.tag) == name:
                return item
        return None

    def text_of(node) -> str:
        if node is None:
            return ""
        return "".join(node.itertext())

    out: List[Dict[str, Any]] = []
    for node in [n for n in root.iter() if local(n.tag) in ("item", "entry")][:limit]:
        title = _plain(text_of(child(node, "title")), 300)
        link = ""
        for item in list(node):
            if local(item.tag) == "link":
                rel = item.get("rel") or "alternate"
                href = item.get("href") or text_of(item).strip()
                if href and rel == "alternate":
                    link = href.strip()
                    break
                if href and not link:
                    link = href.strip()
        if not link:
            guid = child(node, "guid")
            if guid is not None and re.match(r"^https?://", text_of(guid).strip()):
                link = text_of(guid).strip()
        raw_summary = ""
        for name in ("description", "summary", "content", "encoded"):
            node_s = child(node, name)
            if node_s is not None and text_of(node_s).strip():
                raw_summary = text_of(node_s)
                break
        links = re.findall(r'href="(https?://[^"]+)"', html.unescape(raw_summary))
        when = parse_when(text_of(child(node, "pubdate")) or text_of(child(node, "published"))
                          or text_of(child(node, "updated")) or text_of(child(node, "date")))
        if title and re.match(r"^https?://", link):
            out.append({"title": title, "url": link, "published": when, "summary": _plain(raw_summary, 500),
                        "links": links[:6]})
    return out

def parse_page_links(page: str, base_url: str, pattern: str, limit: int = 14) -> List[Dict[str, Any]]:
    """Ссылки на записи со страницы «Новости» (у многих компаний нет RSS) в порядке страницы."""
    rx = re.compile(pattern)
    seen, out = set(), []
    for match in re.finditer(r'<a\b[^>]*?href="([^"#]+)"[^>]*>([\s\S]{0,1500}?)</a>', str(page or ""), re.I):
        href = html.unescape(match.group(1)).strip()
        full = urljoin(base_url, href)
        path = urlparse(full).path
        if not rx.search(path) or host_of(full) != host_of(base_url):
            continue
        key = path.rstrip("/")
        if key in seen:
            continue
        inner = match.group(2)
        heading = re.search(r"<h[1-6][^>]*>([\s\S]*?)</h[1-6]>", inner, re.I)
        title = _plain(heading.group(1) if heading else inner, 200)
        if len(title) < 6:
            slug = key.rsplit("/", 1)[-1]
            title = slug.replace("-", " ").strip().capitalize()
        title = re.sub(r"^(?:(?:announcements?|product|news|policy|research|company|blog)\s+)+", "", title, flags=re.I)
        title = re.sub(r"\s+(?:[A-Z][a-z]{2} \d{1,2}, \d{4}|\d{1,2} [A-Z][a-z]{2} \d{4})\s*$", "", title).strip()
        seen.add(key)
        out.append({"title": title[:200], "url": full.split("?")[0], "published": None, "summary": "", "slug": key})
        if len(out) >= limit:
            break
    return out

_TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|yclid|mc_cid|mc_eid|ref|ref_src|ref_url|source|src|cmpid|ncid|ocid|"
                       r"sr_share|smid|taid|ito|guccounter|guce_\w+|_ga|igshid|si|feature|via|share)$", re.I)

def canonical_url(url: str) -> str:
    """Один адрес — одна история: без трекинга, www, amp, якоря и хвостового слэша."""
    text = str(url or "").strip()
    try:
        parsed = urlparse(text)
    except ValueError:
        return text
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return text
    host = host_of(text)
    path = re.sub(r"/(amp|amp\.html)$", "", parsed.path or "/")
    path = re.sub(r"/index\.html?$", "/", path)
    path = path.rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False) if not _TRACKING.match(k)]
    if host == "news.ycombinator.com":
        query = [(k, v) for k, v in query if k == "id"]
    if host in ("youtube.com", "youtu.be"):
        query = [(k, v) for k, v in query if k == "v"]
    tail = ("?" + urlencode(sorted(query))) if query else ""
    return f"https://{host}{path}{tail}".lower() if host != "youtube.com" else f"https://{host}{path}{tail}"

def tokens(title: str) -> List[str]:
    text = unicodedata.normalize("NFKC", str(title or "")).lower()
    text = text.replace("’", "'").replace("‘", "'").replace("‑", "-").replace("–", "-").replace("—", "-")
    words = re.findall(r"[a-zа-яё0-9]+(?:[.\-][a-zа-яё0-9]+)*", text)
    out = []
    for word in words:
        word = word.strip(".-")
        if not word or word in STOP:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss") and not re.search(r"\d", word):
            word = word[:-1]
        out.append(word)
    return out

def key_terms(title: str) -> set:
    """«Имена с версиями» и собственные имена: Claude Haiku 5.5 → {haiku 5.5, claude, haiku, 5.5}."""
    raw = unicodedata.normalize("NFKC", str(title or "")).replace("‑", "-")
    terms = set()
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+(?:[.\-][A-Za-zА-Яа-яЁё0-9]+)*", raw)
    for i, word in enumerate(words):
        low = word.lower()
        if re.search(r"\d", word) and not re.fullmatch(r"(19|20)\d\d", word) and len(word) <= 12:
            terms.add(low)
            if i > 0 and words[i - 1][:1].isupper():
                terms.add(words[i - 1].lower() + " " + low)
        elif word[:1].isupper() and i > 0 and low not in STOP and len(word) > 2:
            terms.add(low)
    return terms

def similarity(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def same_story(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    if a["canon"] == b["canon"] or (a.get("target") and a.get("target") == b.get("target")) \
            or (a.get("target") and a["target"] == b["canon"]) or (b.get("target") and b["target"] == a["canon"]):
        return True
    jac = similarity(a["title"], b["title"])
    if jac >= 0.55:
        return True
    shared = a["terms"] & b["terms"]

    versioned = {t for t in shared if re.search(r"\d", t) and re.search(r"[a-zа-яё]", t)}
    if versioned and jac >= 0.15:
        return True
    return len(shared) >= 2 and jac >= 0.3

def cluster(candidates: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    parent = list(range(len(candidates)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            if find(i) != find(j) and same_story(candidates[i], candidates[j]):
                parent[find(j)] = find(i)
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for i, item in enumerate(candidates):
        groups.setdefault(find(i), []).append(item)
    return list(groups.values())

def make_candidate(title: str, url: str, *, source: str, kind: str, published: Optional[float] = None,
                   summary: str = "", points: Optional[int] = None, comments: Optional[int] = None,
                   rank: Optional[int] = None, scoped: bool = False, target: str = "", discussion: str = "",
                   origin: str = "") -> Dict[str, Any]:
    host_kind, host_auth = host_authority(target or url)
    auth = AUTHORITY.get(kind, 0.4)
    if kind in ("community", "search"):

        auth = max(auth, host_auth if target or kind == "search" else auth)
    elif host_kind != "search":
        auth = max(auth, host_auth)
    eff = kind
    if kind in ("community", "search") and host_kind != "search" and (target or kind == "search"):
        eff = host_kind
    return {"title": _plain(title, 300), "url": url, "canon": canonical_url(url), "target": canonical_url(target) if target else "",
            "eff_kind": eff,
            "target_url": target, "source": source, "kind": kind, "authority": round(min(1.0, auth), 3),
            "published": published, "summary": _plain(summary, 500), "points": points, "comments": comments,
            "rank": rank, "scoped": scoped, "discussion": discussion, "origin": origin or source,
            "host": host_of(target or url), "terms": key_terms(title)}

async def _collect_feed(feed: Dict[str, Any], fetch_text: FetchText) -> List[Dict[str, Any]]:
    text = await fetch_text(feed["url"])
    out = []
    for index, row in enumerate(parse_feed_items(text, 40)):
        out.append(make_candidate(row["title"], row["url"], source=feed["name"], kind=feed["kind"],
                                  published=row["published"], summary=row["summary"], rank=index,
                                  scoped=bool(feed.get("scoped", True)), origin=feed["url"]))
    return out

async def _collect_page(page: Dict[str, Any], fetch_text: FetchText) -> List[Dict[str, Any]]:
    text = await fetch_text(page["url"])
    out = []
    for index, row in enumerate(parse_page_links(text, page["url"], page["pattern"])):
        cand = make_candidate(row["title"], row["url"], source=page["name"], kind=page["kind"], rank=index,
                              scoped=True, origin=page["url"])
        cand["slug"] = row["slug"]
        cand["page_source"] = page["url"]
        out.append(cand)
    return out

async def _collect_reddit(sub: str, fetch_text: FetchText) -> List[Dict[str, Any]]:
    text = await fetch_text(f"https://www.reddit.com/r/{quote(sub)}/top/.rss?t=day&limit=25")
    out = []
    for index, row in enumerate(parse_feed_items(text, 25)):
        external = next((u for u in row.get("links") or [] if host_of(u) not in ("reddit.com", "redd.it", "i.redd.it",
                                                                                     "v.redd.it", "preview.redd.it")), "")
        out.append(make_candidate(row["title"], row["url"], source=f"r/{sub}", kind="community",
                                  published=row["published"], summary=row["summary"], rank=index, target=external,
                                  discussion=row["url"], origin=f"reddit:{sub}"))
    return out

async def _collect_reddits(subs: List[str], fetch_text: FetchText) -> List[Dict[str, Any]]:
    """Reddit ограничивает анонимные запросы по IP: читаем сабреддиты по очереди и останавливаемся на 429."""
    out: List[Dict[str, Any]] = []
    errors = []
    for index, sub in enumerate(subs):
        if index:
            await asyncio.sleep(0.6)
        try:
            out.extend(await _collect_reddit(sub, fetch_text))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
            if "429" in str(exc):
                break
    if not out and errors:
        raise errors[0]
    return out

async def _collect_hn(plan: Dict[str, Any], fetch_text: FetchText, now: float) -> List[Dict[str, Any]]:
    since = int(now - plan["hours"] * 3600)
    points = int((plan.get("hn") or {}).get("min_points") or 80)
    url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=100&numericFilters="
           + quote(f"points>{points},created_at_i>{since}"))
    data = json.loads(await fetch_text(url))
    out = []
    for hit in data.get("hits") or []:
        title = str(hit.get("title") or "")
        if not title:
            continue
        item = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        target = str(hit.get("url") or "")
        out.append(make_candidate(title, target or item, source="Hacker News", kind="community",
                                  published=float(hit.get("created_at_i") or 0) or None, points=int(hit.get("points") or 0),
                                  comments=int(hit.get("num_comments") or 0), target=target, discussion=item,
                                  origin="hn"))
    return out

async def _collect_search(query: str, search: SearchFn) -> List[Dict[str, Any]]:
    found = await search(query, 8)
    out = []
    for index, row in enumerate((found or {}).get("results") or []):
        url = str(row.get("url") or "")
        if not re.match(r"^https?://", url):
            continue
        kind, _auth = host_authority(url)
        out.append(make_candidate(str(row.get("title") or ""), url, source=source_name(url), kind="search",
                                  summary=str(row.get("snippet") or ""), rank=index, origin=f"search:{query}",
                                  published=parse_when(str(row.get("date") or row.get("published") or ""))))
    return out

async def collect(plan: Dict[str, Any], *, fetch_text: FetchText, search: Optional[SearchFn] = None,
                  now: Optional[float] = None, progress: Optional[Progress] = None) -> Tuple[List[Dict[str, Any]], List[str], int]:
    """Все источники параллельно. Возвращает (кандидаты, ошибки, сколько источников ответило)."""
    now = time.time() if now is None else now
    jobs: List[Tuple[str, Awaitable[List[Dict[str, Any]]]]] = []
    for feed in plan.get("feeds") or []:
        jobs.append((feed["url"], _collect_feed(feed, fetch_text)))
    for page in plan.get("pages") or []:
        jobs.append((page["url"], _collect_page(page, fetch_text)))
    if plan.get("reddit"):
        jobs.append(("reddit", _collect_reddits(plan["reddit"][:5], fetch_text)))
    if plan.get("hn"):
        jobs.append(("hn", _collect_hn(plan, fetch_text, now)))
    if search is not None:
        for query in (plan.get("queries") or [])[:5]:
            jobs.append((f"search:{query}", _collect_search(query, search)))
    if progress:
        progress("collect", str(len(jobs)))

    async def guarded(name, job):
        try:
            return name, await asyncio.wait_for(job, 30)
        except Exception as exc:  # noqa: BLE001 — один источник не роняет ленту
            return name, exc

    results = await asyncio.gather(*(guarded(name, job) for name, job in jobs))
    candidates: List[Dict[str, Any]] = []
    errors: List[str] = []
    ok = 0
    for name, result in results:
        if isinstance(result, BaseException):
            errors.append(f"{name}: {_safe_error(result)}")
            continue
        ok += 1
        candidates.extend(result)
    return candidates, errors, ok

def _safe_error(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    text = re.sub(r"(?i)(bearer|token|key)[=: ]+\S+", r"\1 ***", text)
    return text[:160]

def relevance(item: Dict[str, Any], plan: Dict[str, Any]) -> float:
    if item.get("scoped") and (item.get("eff_kind") or item["kind"]) == "official":
        return 1.0
    words = [w for w in plan.get("keywords") or [] if w]
    if not words:
        return 1.0 if item.get("scoped") else 0.7
    scoped_floor = 0.5 if item.get("scoped") else 0.0
    title = " " + " ".join(tokens(item["title"])) + " "
    raw_title = item["title"].lower()
    summary = item.get("summary", "").lower()

    def hit(text: str, padded: str) -> bool:
        for word in words:
            if " " in word or "." in word or not re.fullmatch(r"[a-z0-9-]+", word):
                if word in text:
                    return True
            elif f" {word} " in padded or re.search(rf"\b{re.escape(word)}\b", text):
                return True
        return False

    if hit(raw_title, title):
        return 1.0
    if hit(summary, " " + " ".join(tokens(summary)) + " "):
        return max(0.55, scoped_floor)
    return scoped_floor

def company_of(text: str, plan: Dict[str, Any]) -> str:
    low = " " + str(text or "").lower() + " "
    for name, aliases in (plan.get("entities") or {}).items():
        if any(alias in low for alias in aliases):
            return name
    return ""

def score_cluster(members: List[Dict[str, Any]], plan: Dict[str, Any], now: float) -> Dict[str, Any]:
    """Эвристическая оценка истории (0…1) и признаки для «почему здесь»."""
    hours = float(plan.get("hours") or 36)
    best = max(members, key=lambda m: (m["authority"], -(m.get("rank") or 0)))
    hosts = {m["host"] for m in members if m["host"]}
    non_search_hosts = {m["host"] for m in members if m["kind"] != "search" and m["host"]}
    origins = {m["origin"] for m in members}
    authority = max(m["authority"] for m in members)
    corroboration = min(1.0, max(0, len(hosts | {m["origin"] for m in members if m["kind"] == "community"}) - 1) / 3)
    points = max([m.get("points") or 0 for m in members] + [0])
    engagement = min(1.0, math.log10(points) / 3) if points > 1 else 0.0
    for m in members:
        if m["kind"] == "community" and m["origin"].startswith("reddit") and m.get("rank") is not None:
            engagement = max(engagement, 0.75 - 0.025 * m["rank"])
    dates = [m["published"] for m in members if m.get("published")]
    first = min(dates) if dates else None
    if first:
        age_h = max(0.0, (now - first) / 3600)
        recency = math.exp(-age_h / max(6.0, hours / 1.5))
    else:
        age_h = None
        recency = 0.55 if any(m.get("fresh") for m in members) else 0.35
    rel = max(relevance(m, plan) for m in members)
    text = " ".join(m["title"] for m in members)
    signal = 1.0 if plan.get("topic") and re.search(TOPICS[plan["topic"]]["signal"], text, re.I) else 0.4
    base = 0.30 * authority + 0.20 * corroboration + 0.20 * engagement + 0.20 * recency + 0.10 * signal
    fluff = bool(FLUFF_RE.search(best["title"])) and len(non_search_hosts) < 3
    rumor = bool(RUMOR_RE.search(text)) and not any((m.get("eff_kind") or m["kind"]) == "official" for m in members)
    if fluff:
        base *= 0.45
    if rumor:
        base *= 0.8
    if plan.get("global", True) and plan.get("out_lang") == "ru" and all(m["host"] in RU_HOSTS for m in members):
        base *= 0.8
    only_search = all(m["kind"] == "search" for m in members)
    return {"score": round(base * (0.35 + 0.65 * rel), 4), "relevance": rel, "authority": authority,
            "hosts": len(hosts), "sources": len({m["source"] for m in members}), "origins": len(origins), "points": points, "age_h": age_h, "fluff": fluff,
            "rumor": rumor, "official": any((m.get("eff_kind") or m["kind"]) == "official" for m in members), "only_search": only_search,
            "first": first}

def pick_representative(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Адрес и заголовок истории: первоисточник → СМИ → то, на что ссылалось сообщество."""
    def rank(m):
        order = {"official": 0, "primary": 1, "media": 2, "aggregator": 3, "search": 4, "community": 5}
        return (order.get(m.get("eff_kind") or m["kind"], 6), 0 if m["kind"] != "community" else 1, -m["authority"])

    best = sorted(members, key=rank)[0]
    url = best["url"]
    if best["kind"] in ("community", "search") and best.get("target_url"):
        url = best["target_url"]
    title = best["title"]
    if best["kind"] == "community":
        better = [m for m in sorted(members, key=rank) if m["kind"] in ("official", "primary", "media")]
        if better:
            title = better[0]["title"]
    source = best["source"]
    if best["kind"] in ("community", "search") and (best.get("target_url") or best["kind"] == "search"):
        source = source_name(url)
    return {"title": title, "url": url, "source": source, "summary": next((m["summary"] for m in sorted(members, key=rank) if m.get("summary")), "")}

def fingerprint(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    urls = sorted({m["canon"] for m in members} | {m["target"] for m in members if m.get("target")})
    rep = pick_representative(members)
    return {"fp": hashlib.sha1((urls[0] if urls else rep["title"]).encode("utf-8")).hexdigest()[:16],
            "urls": urls[:8], "title": " ".join(tokens(rep["title"]))[:200]}

def clean_state(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    seen = []
    for row in raw.get("seen") or [] if isinstance(raw.get("seen"), list) else []:
        if not isinstance(row, dict) or not row.get("fp"):
            continue
        try:
            seen.append({"fp": str(row["fp"])[:24], "urls": [str(u)[:400] for u in (row.get("urls") or [])[:8]],
                         "title": str(row.get("title") or "")[:200], "first": float(row.get("first") or 0),
                         "last": float(row.get("last") or 0), "hosts": int(row.get("hosts") or 1),
                         "sig": float(row.get("sig") or 0), "url": str(row.get("url") or "")[:600]})
        except (TypeError, ValueError):
            continue
    baseline = {}
    if isinstance(raw.get("baseline"), dict):
        for key, slugs in list(raw["baseline"].items())[:20]:
            if isinstance(slugs, list):
                baseline[str(key)[:400]] = [str(s)[:200] for s in slugs[:60]]
    plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else None
    try:
        plan = json.loads(json.dumps(plan)) if plan else None
    except (TypeError, ValueError):
        plan = None
    return {"seen": seen[-MAX_SEEN:], "baseline": baseline, "plan": plan,
            "planned_for": str(raw.get("planned_for") or "")[:300], "runs": int(raw.get("runs") or 0)
            if str(raw.get("runs") or "0").isdigit() else 0}

def match_seen(fp: Dict[str, Any], state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    urls = set(fp["urls"])
    words = set(fp["title"].split())
    for row in state["seen"]:
        if row["fp"] == fp["fp"] or urls & set(row["urls"]):
            return row
        other = set(row["title"].split())
        if words and other and len(words & other) / len(words | other) >= 0.6:
            return row
    return None

def stay_seconds(cadence: Optional[Dict[str, Any]]) -> float:
    """Сколько показанная история остаётся «текущей», прежде чем считаться старой."""
    cadence = cadence or {}
    if cadence.get("kind") == "interval":
        return max(3 * 3600.0, min(22 * 3600.0, float(cadence.get("minutes") or 60) * 60 * 0.95))
    return 22 * 3600.0

def apply_baseline(candidates: List[Dict[str, Any]], state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ссылки со страниц без дат: при первом чтении страницы новыми считаются только верхние 3,
    остальное — «уже было» (иначе первая лента состояла бы из старых записей)."""
    by_page: Dict[str, List[Dict[str, Any]]] = {}
    for cand in candidates:
        if cand.get("page_source"):
            by_page.setdefault(cand["page_source"], []).append(cand)
    for page, rows in by_page.items():
        known = state["baseline"].get(page)
        if known is None:
            fresh = rows[:3]
        else:
            fresh = [row for row in rows if row["slug"] not in known]
        fresh_ids = {id(row) for row in fresh}
        for row in rows:
            if id(row) in fresh_ids:
                row["fresh"] = True
            else:
                row["stale_page"] = True
        state["baseline"][page] = list(dict.fromkeys([r["slug"] for r in rows] + (known or [])))[:60]
    return candidates

RUBRIC_PROMPT = (
    "You are the editor of a tiny glanceable news panel. For each candidate story decide how much it deserves "
    "one of the few slots. significance 0-10: 9-10 a major release/event everyone in the field talks about "
    "(new flagship model, big acquisition, major outage/law); 6-8 notable and concrete; 3-5 minor; 0-2 fluff "
    "(customer stories, partnerships, webinars, programs, opinion pieces, listicles, SEO pages, evergreen "
    "pages). credible: false for unsourced rumors, satire, obvious SEO/clickbait or claims contradicted by "
    "primary sources. on_topic: false if it is not about the panel topic. Then write in {lang}: \"title\" — a "
    "faithful headline (translate if needed, keep names/versions exact, ≤ 90 chars), \"summary\" — ONE "
    "factual sentence from the given text only (≤ 170 chars, no hype, no invented facts), \"why\" — why it "
    "earns a slot (≤ 80 chars), \"company\" — the main organisation or \"\". Return JSON only: "
    "{{\"items\":[{{\"id\":\"…\",\"significance\":N,\"credible\":true,\"on_topic\":true,\"title\":\"…\","
    "\"summary\":\"…\",\"why\":\"…\",\"company\":\"…\"}}]}}")

async def rubric(clusters: List[Dict[str, Any]], plan: Dict[str, Any], ask_model: AskModel, now: float) -> Dict[str, Dict[str, Any]]:
    lang = "English" if plan.get("out_lang") == "en" else "Russian"
    lines = []
    for row in clusters:
        sources = ", ".join(sorted({m["source"] for m in row["members"]}))[:160]
        age = f"{row['score']['age_h']:.0f}h ago" if row["score"].get("age_h") is not None else "date unknown"
        snippet = row["rep"]["summary"][:280]
        lines.append(json.dumps({"id": row["id"], "title": row["rep"]["title"], "sources": sources, "age": age,
                                 "points": row["score"]["points"] or None, "text": snippet}, ensure_ascii=False))
    user = (f"Now: {datetime.fromtimestamp(now).astimezone().strftime('%Y-%m-%d %H:%M %Z')}\n"
            f"Panel topic: {plan.get('label') or plan.get('topic') or ''} — {plan.get('subject', '')}\n"
            "Candidates (one JSON per line):\n" + "\n".join(lines))
    answer = await ask_model(RUBRIC_PROMPT.format(lang=lang), user)
    out: Dict[str, Dict[str, Any]] = {}
    for item in answer.get("items") or [] if isinstance(answer.get("items"), list) else []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = item
    return out

def _why_text(score: Dict[str, Any], lang: str) -> str:
    ru = lang != "en"
    parts = []
    if score["official"]:
        parts.append("первоисточник" if ru else "primary source")
    n = score.get("sources") or score["hosts"]
    if n >= 2:
        parts.append((f"{n} источника" if n < 5 else f"{n} источников") if ru else f"{n} sources")
    if score["points"]:
        parts.append(f"{score['points']} очков на HN" if ru else f"{score['points']} points on HN")
    if score.get("age_h") is not None and score["age_h"] < 12:
        parts.append("свежее" if ru else "fresh")
    if not parts:
        parts.append(("авторитетный источник" if ru else "reputable source") if score["authority"] >= 0.8 else
                     ("по теме панели" if ru else "on topic"))
    return " · ".join(parts)[:90]

def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",.;: ") + "…"

def select_diverse(rows: List[Dict[str, Any]], count: int) -> List[Dict[str, Any]]:
    """Жадный выбор с ограничением «одна компания / один сайт» (~40 % ленты), потом добор."""
    cap = max(1, math.ceil(count * 0.4))
    picked: List[Dict[str, Any]] = []
    per_company: Dict[str, int] = {}
    per_host: Dict[str, int] = {}
    for row in rows:
        company = row.get("company") or ""
        host = host_of(row["rep"]["url"])
        if company and per_company.get(company, 0) >= cap:
            continue
        if per_host.get(host, 0) >= cap:
            continue
        picked.append(row)
        if company:
            per_company[company] = per_company.get(company, 0) + 1
        per_host[host] = per_host.get(host, 0) + 1
        if len(picked) >= count:
            return picked
    for row in rows:
        if row not in picked:
            picked.append(row)
            if len(picked) >= count:
                break
    return sorted(picked, key=lambda r: -r["final"])

async def build_feed(plan: Dict[str, Any], state: Dict[str, Any], *, fetch_text: FetchText,
                     search: Optional[SearchFn] = None, ask_model: Optional[AskModel] = None,
                     cadence: Optional[Dict[str, Any]] = None, now: Optional[float] = None,
                     progress: Optional[Progress] = None) -> Dict[str, Any]:
    """Весь конвейер: сбор → склейка → оценка → рубрика → разнообразие → «уже показанное».

    Возвращает {"items", "sources", "note", "stats", "errors", "state"}; ``state`` нужно сохранить.
    Поднимает ``RuntimeError("no-sources")``, если не ответил ни один источник.
    """
    now = time.time() if now is None else now
    lang = plan.get("out_lang") or "ru"
    state = clean_state(state)
    candidates, errors, ok = await collect(plan, fetch_text=fetch_text, search=search, now=now, progress=progress)
    if not ok:
        raise RuntimeError("no-sources: " + "; ".join(errors[:4]))
    raw_count = len(candidates)
    candidates = apply_baseline(candidates, state)
    hours = float(plan.get("hours") or 36)
    fresh: List[Dict[str, Any]] = []
    for cand in candidates:
        if cand.get("published") and now - cand["published"] > hours * 3600 * 1.6:
            continue
        if cand.get("published") and cand["published"] - now > 6 * 3600:
            cand["published"] = None
        if relevance(cand, plan) <= 0:
            continue
        fresh.append(cand)
    if progress:
        progress("dedupe", str(len(fresh)))
    groups = cluster(fresh)
    rows: List[Dict[str, Any]] = []
    for index, members in enumerate(groups):
        if all(m.get("stale_page") for m in members):
            continue
        score = score_cluster(members, plan, now)
        if score["only_search"] and score["hosts"] < 2 and not (score["first"] and now - score["first"] < hours * 3600):
            continue
        rep = pick_representative(members)
        rows.append({"id": f"c{index}", "members": members, "score": score, "rep": rep,
                     "company": company_of(" ".join(m["title"] for m in members), plan),
                     "fp": fingerprint(members), "final": score["score"]})
    rows.sort(key=lambda r: -r["final"])
    rows = rows[:28]
    if progress:
        progress("score", str(len(rows)))
    verdicts: Dict[str, Dict[str, Any]] = {}
    model_used = False
    if ask_model is not None and rows:
        try:
            verdicts = await rubric(rows, plan, ask_model, now)
            model_used = bool(verdicts)
        except Exception as exc:  # noqa: BLE001 — без модели остаётся эвристика
            errors.append(f"model: {_safe_error(exc)}")
    kept: List[Dict[str, Any]] = []
    for row in rows:
        verdict = verdicts.get(row["id"]) if model_used else None
        if verdict is not None:
            try:
                sig = max(0.0, min(10.0, float(verdict.get("significance") or 0)))
            except (TypeError, ValueError):
                sig = 0.0
            if verdict.get("on_topic") is False or sig < 4:
                continue
            if verdict.get("credible") is False and not row["score"]["official"]:
                continue
            row["final"] = round(0.5 * row["score"]["score"] + 0.5 * sig / 10, 4)
            row["sig"] = sig
            row["title"] = _clip(verdict.get("title") or row["rep"]["title"], 120)
            row["summary"] = _clip(verdict.get("summary") or "", 200)
            row["why"] = _clip(verdict.get("why") or _why_text(row["score"], lang), 100)
            if verdict.get("company"):
                row["company"] = _clip(str(verdict["company"]), 30)
        else:
            if model_used:
                continue
            if row["score"]["fluff"] or row["score"]["score"] < 0.22:
                continue
            row["sig"] = round(row["score"]["score"] * 10, 1)
            row["title"] = _clip(row["rep"]["title"], 120)
            row["summary"] = _clip(row["rep"]["summary"], 200)
            row["why"] = _why_text(row["score"], lang)
        kept.append(row)
    kept.sort(key=lambda r: -r["final"])

    stay = stay_seconds(cadence)
    eligible: List[Dict[str, Any]] = []
    for row in kept:
        seen = match_seen(row["fp"], state)
        row["seen"] = seen
        if seen and now - seen["first"] > stay:
            grew = row["score"]["sources"] >= seen["hosts"] + 2 or row["sig"] >= seen["sig"] + 3
            if not grew:
                continue
            row["update"] = True
        eligible.append(row)
    picked = select_diverse(eligible, int(plan.get("count") or 5))
    if progress:
        progress("summarize", str(len(picked)))
    items: List[Dict[str, Any]] = []
    sources: List[Dict[str, str]] = []
    for row in picked:
        members = row["members"]
        links = []
        for m in sorted(members, key=lambda m: -m["authority"]):
            link = m["target_url"] if m["kind"] == "community" and m.get("target_url") and m["host"] != host_of(row["rep"]["url"]) else m["url"]
            name = source_name(link) if link == m.get("target_url") else m["source"]
            if link and not any(l["url"] == link for l in links) and canonical_url(link) != canonical_url(row["rep"]["url"]):
                links.append({"name": name, "url": link})
            if m.get("discussion") and not any(l["url"] == m["discussion"] for l in links):
                links.append({"name": m["source"], "url": m["discussion"]})
        badge, tone = "", ""
        if row.get("update"):
            badge, tone = ("обновление" if lang != "en" else "update"), "info"
        elif row["score"]["rumor"]:
            badge, tone = ("не подтверждено" if lang != "en" else "unconfirmed"), "warn"
        elif row["score"]["official"]:
            badge, tone = ("официально" if lang != "en" else "official"), "ok"
        first = row["score"]["first"]
        url = (row.get("seen") or {}).get("url") or row["rep"]["url"]
        items.append({"title": row["title"], "url": url, "source": row["rep"]["source"],
                      "ts": first, "summary": row["summary"], "why": row["why"], "badge": badge, "tone": tone,
                      "company": row.get("company") or "", "score": round(row["final"] * 100),
                      "links": [l for l in links if canonical_url(l["url"]) != canonical_url(url)][:4],
                      "n": row["score"]["sources"]})
        name = row["rep"]["source"]
        if not any(s["url"] == row["rep"]["url"] for s in sources):
            sources.append({"name": name[:60], "url": row["rep"]["url"]})

    for row in picked:
        seen = row.get("seen")
        if seen:
            seen["last"] = now
            seen["hosts"] = max(seen["hosts"], row["score"]["sources"])
            seen["sig"] = max(seen["sig"], row["sig"])
            seen["urls"] = list(dict.fromkeys(seen["urls"] + row["fp"]["urls"]))[:8]
            if row.get("update"):
                seen["first"] = now
        else:
            state["seen"].append({"fp": row["fp"]["fp"], "urls": row["fp"]["urls"], "title": row["fp"]["title"],
                                  "first": now, "last": now, "hosts": row["score"]["sources"], "sig": row["sig"],
                                  "url": row["rep"]["url"]})
    horizon = now - SEEN_DAYS * 86400
    state["seen"] = [row for row in state["seen"] if row["last"] >= horizon][-MAX_SEEN:]
    state["runs"] = state.get("runs", 0) + 1
    note = ""
    if not items:
        note = ("Нового с прошлого обновления нет" if lang != "en" else "Nothing new since the last refresh")
    elif ask_model is not None and rows and not model_used:

        note = ("Модель не ответила — список без её отбора и перевода, повторю при следующем обновлении"
                if lang != "en" else "The model did not answer — the list is not curated or translated; will retry next refresh")
    return {"items": items, "sources": sources[:6], "note": note, "errors": errors, "state": state,
            "stats": {"candidates": raw_count, "relevant": len(fresh), "stories": len(groups), "ranked": len(rows),
                      "kept": len(kept), "shown": len(items), "sources_ok": ok, "sources_failed": len(errors),
                      "model": model_used}}
