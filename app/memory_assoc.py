# -*- coding: utf-8 -*-
"""Associative relevance for memory v2 (Round 40, r40-memory).

Before Round 40 a fact reached the prompt only when the request shared a word (a 6-letter prefix) with it:
«Не переносит лактозу» never met «Какой кофе заказать в кофейне». Here every fact gets *keys* of four kinds,
kept in one inverted index:

* ``word``    — light RU/EN stems of the fact text and its tags (suffix stripping, no dependencies);
* ``concept`` — associations the model wrote with the fact (``"concepts"`` in the ```memuai-memory block, or
  filled in later in the background by :mod:`app.memory_worker` for older facts), stemmed the same way;
* ``seed``    — a small built-in association table (:data:`SEED_CLUSTERS`): works offline and before the model
  ever filled concepts (lactose → milk, coffee, cappuccino, cheese …);
* fuzzy       — character trigrams of the stems catch typos and other word forms («молоко» ≈ «молочный»).

:func:`score` returns a relevance per fact id; :mod:`app.memory_v2` adds its priors (pinned, scope, recency…)
and the «core» facts (allergies, diet, name, language) that are always included within a small token share.
The index is cached per document revision, so a request costs one dictionary walk over the query stems.
Standard library only: imported by the MemuAI window, the Proxy core, the Telegram bot, MemuCode and MemuNote.
"""
from __future__ import annotations

import math
import re
import threading
from typing import Any, Dict, Iterable, List, Set, Tuple

# ------------------------------------------------------------------ stemming

_RU_ENDINGS = tuple(sorted("""
ившись ывшись ующая ующие ующий ующих ующим ующую ующее ость ости остью остей остям остями остях ение ения ению ением
ении ений ениям ениями ениях ание ания анию анием ании аний аниям аниями аниях ировать ировал ировала ируют ирует
ивший ывший иться ыться ешься ишься ется ится утся ятся ялся ался ился ылся лась лись лось ными ыми ими иями ями ами
ией иям иях ого его ому ему ыми ими ой ей ий ый ая яя ое ее ую юю ых их ам ям ах ях ом ем ов ев ия ие ии ию ья ье ьи
ью ть ти ла ло ли ет ют ут ит ат ят ешь ишь ете ите ал ял ил ыл ся сь а я о е ы и у ю ь й
""".split(), key=len, reverse=True))
_EN_ENDINGS = ("ational", "ization", "fulness", "iveness", "ations", "ation", "ingly", "ments", "ness", "ment",
               "ings", "less", "ful", "ing", "ies", "ied", "ers", "est", "ed", "es", "ly", "er", "s")
_WORD = re.compile(r"[^\W\d_]+|\d{3,}", re.U)
_STOP = set("""
a an the and or of to in on at for with by from is are was were be been being it its this that these those as i me my we
our you your he she they them his her their user users do does did not no but if so than then what which who how when
where can could would should will just very also about into over only some any all most more
и в во на с со к ко по о об от до за из у же ли не нет ни а но да что как это этот эта эти тот та те для при про он она они
его ее их мне меня мой моя мои ты тебя твой вы вас ваш пользователь пользователя пользователю был была были быть есть
будет уже еще очень так там тут где когда чтобы или какой какая какое какие мой можно нужно надо мне себя сам сама
""".split())
# Negations / change markers are not content: «не любит кофе» and «любит кофе» share the key «коф».
_NEGATION = re.compile(r"(?:^|[^\w])(?:не|нет|ни|никогда|больше не|без|бросил\w*|перестал\w*|отказал\w*|"
                       r"not|no|never|don'?t|doesn'?t|isn'?t|no longer|stopped|quit|gave up|without)(?=[^\w]|$)", re.I | re.U)
_CHANGE = re.compile(r"(?:^|[^\w])(?:теперь|уже не|больше не|переехал\w*|переезд\w*|сменил\w*|поменял\w*|"
                     r"перешл\w*|перешел|перешёл|бросил\w*|перестал\w*|now|no longer|moved|relocated|"
                     r"switched|changed|quit|stopped|became|anymore)(?=[^\w]|$)", re.I | re.U)


def stem(word: str) -> str:
    """A light RU/EN stem: the longest known ending is cut, at least 3 letters stay (4 for English)."""
    low = str(word or "").lower().replace("ё", "е")
    if len(low) <= 3 or low.isdigit():
        return low
    if re.fullmatch(r"[а-я]+", low):
        for end in _RU_ENDINGS:
            if low.endswith(end) and len(low) - len(end) >= 3:
                low = low[: len(low) - len(end)]
                break
        # «кофейня» → «кофейн», «молочный» → «молочн»: a soft sign / й at the end adds nothing.
        return low.rstrip("ьй") if len(low) > 3 else low
    if re.fullmatch(r"[a-z]+", low):
        for end in _EN_ENDINGS:
            if low.endswith(end) and len(low) - len(end) >= 4:
                low = low[: len(low) - len(end)]
                break
        if len(low) >= 4 and low[-1] == low[-2] and low[-1] not in "aeiouls":   # «stopp» → «stop»
            low = low[:-1]
        return low.rstrip("e") if len(low) > 4 else low
    return low


def stems(text: str) -> List[str]:
    out: List[str] = []
    for raw in _WORD.findall(str(text or "")):
        low = raw.lower().replace("ё", "е")
        if len(low) < 2 or low in _STOP:
            continue
        value = stem(low)
        if len(value) >= 2 and value not in out:
            out.append(value)
    return out


def trigrams(word: str) -> Set[str]:
    padded = f" {word} "
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


def trigram_sim(a: str, b: str) -> float:
    """Dice over character trigrams of two stems (1.0 = the same stem)."""
    if a == b:
        return 1.0
    left, right = trigrams(a), trigrams(b)
    if not left or not right:
        return 0.0
    return 2.0 * len(left & right) / (len(left) + len(right))


def negated(text: str) -> bool:
    return bool(_NEGATION.search(str(text or "").lower().replace("ё", "е")))


def changes(text: str) -> bool:
    """«теперь», «переехала», «no longer» …: the fact says something replaced an older state."""
    return bool(_CHANGE.search(str(text or "").lower().replace("ё", "е")))


# ------------------------------------------------------------------ built-in associations

# Each cluster is one association field; a fact that hits a cluster word gets the whole cluster as weak keys.
# Words are written naturally; they are stemmed once at import.
SEED_CLUSTERS: Tuple[str, ...] = (
    # food & diet
    "лактоза лактозы непереносимость молоко молочное молочка кефир йогурт сливки сыр творог мороженое кофе капучино "
    "латте раф флэт какао кофейня кафе десерт lactose dairy milk cheese cream yogurt coffee cappuccino latte cafe",
    "глютен пшеница хлеб макароны паста пицца выпечка пиво мука целиакия gluten wheat bread pasta pizza bakery celiac",
    "вегетарианец вегетарианка веган веганство мясо курица говядина свинина рыба бургер шашлык ресторан рецепт "
    "vegetarian vegan meat chicken beef pork fish burger steak restaurant recipe",
    "аллергия орехи арахис миндаль фундук кешью десерт шоколад торт выпечка allergy nuts peanut almond hazelnut cashew "
    "chocolate cake dessert",
    "морепродукты креветки краб мидии суши роллы рыба seafood shrimp crab sushi rolls fish",
    "диета калории похудение кето сахар диабет сладкое еда питание рецепт ужин обед завтрак продукты "
    "diet calories keto sugar diabetes food meal dinner lunch breakfast groceries",
    "алкоголь вино пиво коктейль бар трезвость alcohol wine beer cocktail bar sober",
    "кофеин кофе чай энергетик сон бессонница caffeine coffee tea energy sleep insomnia",
    # health & body
    "аллергия пыльца кошки шерсть пыль антигистаминные allergy pollen cat dander dust antihistamine",
    "спина поясница осанка кресло стол тренировка спорт back posture chair desk workout",
    "зрение очки линзы экран монитор шрифт яркость eyesight glasses lenses screen monitor font",
    "беременность беременна ребенок малыш pregnancy pregnant baby",
    # places & travel
    "город живет переехала переехал жилье квартира погода район город city lives moved apartment weather "
    "neighbourhood",
    "путешествие поездка отпуск перелет самолет отель виза билеты travel trip vacation flight hotel visa tickets",
    "аэрофобия самолет перелет поезд fear flying plane flight train",
    "морская болезнь укачивает корабль паром автобус seasick ferry boat bus",
    # pets & people
    "кошка кот котенок корм ветеринар лоток cat kitten catfood vet litter",
    "собака пес щенок прогулка корм ветеринар dog puppy walk dogfood vet",
    "ребенок дети сын дочь школа садик игрушки подарок child children kids son daughter school toys gift",
    "жена муж партнер девушка парень годовщина подарок свидание wife husband partner girlfriend boyfriend "
    "anniversary gift date",
    "день рождения праздник подарок торт birthday party gift cake",
    # work, study, tech
    "python питон pip django flask fastapi pandas скрипт код программирование script code programming",
    "javascript typescript node react vue frontend фронтенд верстка веб сайт web website",
    "windows linux macos ноутбук компьютер пк ос laptop computer pc os",
    "игры геймер steam игра консоль playstation xbox nintendo games gamer console",
    "студент университет учеба экзамен сессия курсовая диплом student university study exam thesis",
    "работа должность офис удаленка зарплата начальник коллеги job office remote salary boss colleagues",
    "английский язык перевод изучение english language translation learning",
    "бюджет экономия деньги дорого дешево цена скидка budget savings money expensive cheap price discount",
    # style & preferences
    "краткость кратко коротко длинные ответы подробно short brief concise detailed answers",
    "темная тема светлая тема оформление дизайн цвет dark theme light theme design colour",
    "музыка песни плейлист жанр рок джаз рэп music songs playlist genre rock jazz rap",
    "фильмы сериалы кино аниме movies series cinema anime",
    "книги чтение роман фантастика books reading novel fiction",
    "спорт бег зал фитнес йога велосипед sport running gym fitness yoga bike",
)


def _build_seed() -> Dict[str, Set[int]]:
    out: Dict[str, Set[int]] = {}
    for index, cluster in enumerate(SEED_CLUSTERS):
        for value in stems(cluster):
            out.setdefault(value, set()).add(index)
    return out


_SEED = _build_seed()
_SEED_STEMS: List[Set[str]] = [set(stems(cluster)) for cluster in SEED_CLUSTERS]


def seed_keys(words: Iterable[str]) -> Set[str]:
    """Stems of every built-in cluster the given stems belong to (minus the stems themselves)."""
    have = set(words)
    out: Set[str] = set()
    for word in have:
        for index in _SEED.get(word, ()):
            out |= _SEED_STEMS[index]
    return out - have


# ------------------------------------------------------------------ concepts

MAX_CONCEPTS = 12


def clean_concepts(value: Any) -> List[str]:
    """Model-written associations → at most 12 short distinct strings."""
    if isinstance(value, str):
        value = re.split(r"[,;|\n]+", value)
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for raw in value:
        text = re.sub(r"\s+", " ", str(raw or "")).strip(" .,#\"'«»")[:32]
        key = text.lower().replace("ё", "е")
        if len(re.findall(r"[^\W\d_]", text)) < 2 or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= MAX_CONCEPTS:
            break
    return out


# ------------------------------------------------------------------ index

W_WORD, W_CONCEPT, W_SEED = 1.0, 0.75, 0.45
FUZZY_MIN = 0.62           # trigram Dice for a fuzzy hit
FUZZY_FACTOR = 0.7         # a fuzzy hit counts this much of an exact one


class Index:
    """Inverted index over the active items of one document: key → {item id: weight}."""

    def __init__(self, items: List[Dict[str, Any]]) -> None:
        self.keys: Dict[str, Dict[str, float]] = {}
        self.size: Dict[str, int] = {}
        self.tri: Dict[str, Set[str]] = {}
        for item in items:
            self.add(item["id"], item.get("text", ""), item.get("tags") or [], item.get("concepts") or [])
        self.total = max(1, len(self.size))

    def _put(self, key: str, item_id: str, weight: float) -> None:
        row = self.keys.setdefault(key, {})
        if row.get(item_id, 0.0) < weight:
            row[item_id] = weight
        for gram in trigrams(key):
            self.tri.setdefault(gram, set()).add(key)

    def add(self, item_id: str, text: str, tags: List[str], concepts: List[str]) -> None:
        words = set(stems(text + " " + " ".join(tags)))
        concept_words = set(stems(" ".join(concepts))) - words
        for word in words:
            self._put(word, item_id, W_WORD)
        for word in concept_words:
            self._put(word, item_id, W_CONCEPT)
        for word in seed_keys(words | concept_words) - words - concept_words:
            self._put(word, item_id, W_SEED)
        self.size[item_id] = len(words)

    def _fuzzy(self, word: str) -> List[Tuple[str, float]]:
        if len(word) < 4:
            return []
        counts: Dict[str, int] = {}
        for gram in trigrams(word):
            for key in self.tri.get(gram, ()):
                counts[key] = counts.get(key, 0) + 1
        out = []
        for key, shared in counts.items():
            if key == word or shared < 2:
                continue
            sim = trigram_sim(word, key)
            if sim >= FUZZY_MIN:
                out.append((key, sim))
        out.sort(key=lambda pair: pair[1], reverse=True)
        return out[:4]

    def score(self, query: str) -> Dict[str, float]:
        """item id → relevance (idf-weighted; exact stems > model concepts > built-in associations > fuzzy)."""
        out: Dict[str, float] = {}
        for word in stems(query)[:80]:
            matches: List[Tuple[str, float]] = [(word, 1.0)] if word in self.keys else []
            matches += [(key, sim * FUZZY_FACTOR) for key, sim in self._fuzzy(word)]
            best: Dict[str, float] = {}
            for key, factor in matches:
                row = self.keys[key]
                idf = math.log(1 + self.total / max(1, len(row)))
                for item_id, weight in row.items():
                    value = weight * factor * idf
                    if value > best.get(item_id, 0.0):
                        best[item_id] = value
            for item_id, value in best.items():
                out[item_id] = out.get(item_id, 0.0) + value
        for item_id in list(out):
            out[item_id] = out[item_id] / (1.0 + 0.08 * self.size.get(item_id, 0))
        return out


_CACHE: Dict[str, Tuple[str, Index]] = {}
_CACHE_LOCK = threading.Lock()


def _signature(items: List[Dict[str, Any]]) -> str:
    return "|".join(f"{i['id']}:{i.get('updated_at', 0)}:{len(i.get('concepts') or [])}" for i in items)


def index_for(key: str, items: List[Dict[str, Any]]) -> Index:
    """The cached index of these items (rebuilt only when a fact, its text or its concepts changed)."""
    sig = _signature(items)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == sig:
            return hit[1]
    idx = Index(items)
    with _CACHE_LOCK:
        if len(_CACHE) > 16:
            _CACHE.clear()
        _CACHE[key] = (sig, idx)
    return idx


def overlap(a: str, b: str, concepts_a: Iterable[str] = (), concepts_b: Iterable[str] = ()) -> float:
    """How much two facts talk about the same thing (0…1): shared stems, then shared concepts/associations."""
    left, right = set(stems(a)), set(stems(b))
    if not left or not right:
        return 0.0
    direct = len(left & right) / min(len(left), len(right))
    wide_l = left | set(stems(" ".join(concepts_a)))
    wide_r = right | set(stems(" ".join(concepts_b)))
    wide = len(wide_l & wide_r) / max(1, min(len(wide_l), len(wide_r)))
    return max(direct, 0.6 * wide)
