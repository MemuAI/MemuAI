"""Single-turn scenarios of the prompt eval: chat (web / local tools / memory), Quick Ask, DFY.

Every scenario builds the messages from a prompt snapshot (see snapshot.py) and checks the model's reply with a
deterministic rule. Not part of the normal test suite.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Tuple

RUNTIME = ("<runtime_context>Current local date and time: суббота, 10 октября 2026 г. в 14:30. Interface language: "
           "Russian (the user's own language always wins).</runtime_context>")

TOOL_BLOCK = re.compile(r"```[ \t]*memuai-tool[^\n]*\n([\s\S]*?)(?:\n[ \t]*```|$)", re.I)
MEMORY_BLOCK = re.compile(r"```[ \t]*memuai-memory[^\n]*\n([\s\S]*?)(?:\n[ \t]*```|$)", re.I)
THINK = re.compile(r"<think>[\s\S]*?</think>", re.I)


def tool_calls(text: str) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for raw in TOOL_BLOCK.findall(text or ""):
        try:
            value = json.loads(raw.strip())
        except Exception:
            calls.append({"tool": "_invalid_json"})
            continue
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict):
                calls.append({"tool": str(item.get("tool") or item.get("name") or ""), "args": item.get("args") or {}})
    return calls


def bare_calls(text: str) -> List[str]:
    """Mirror of app.js bareToolCalls (Round 29): the whole reply is [{"tool":…,"args":{…}}] (maybe in ```json)."""
    raw = THINK.sub("", text or "").strip()
    raw = re.sub(r"^```[ \t]*(?:json|tool|tool_code)?[ \t]*\r?\n([\s\S]*?)\r?\n?[ \t]*```$", r"\1", raw, flags=re.I).strip()
    try:
        data = json.loads(raw)
    except Exception:
        return []
    rows = data if isinstance(data, list) else [data]
    names = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("tool"), str) or set(row) - {"tool", "args"}:
            return []
        names.append(row["tool"].lower())
    return names


def visible(text: str) -> str:
    """What the user would see: no reasoning, no hidden blocks."""
    text = THINK.sub("", text or "")
    text = re.sub(r"```[ \t]*(?:memuai[-_ ]?\w+|schedule)\b[\s\S]*?(?:\n[ \t]*```|$)", "", text, flags=re.I)
    text = re.sub(r"\[MEM:[^\]]*\]", "", text)
    return text.strip()


def memory_facts(text: str) -> List[str]:
    facts: List[str] = []
    for raw in MEMORY_BLOCK.findall(text or ""):
        try:
            value = json.loads(raw.strip())
            facts += [str(row.get("fact") or "") for row in value.get("facts") or [] if isinstance(row, dict)]
        except Exception:
            facts.append("_invalid_json")
    facts += re.findall(r"\[MEM:\s*([^\]]+)\]", text or "")
    return facts


def chat_system(snap: Dict[str, Any], *, web: bool = True) -> str:
    js = snap["js"]
    parts = [snap["DEFAULT_PROMPT"], "<personality>" + snap["internal"]["personality-friendly"] + "</personality>",
             js["runtime_quality"]]
    parts.append(js["WEB_TOOLS_PROMPT"] if web else
                 "<web_tools>Web access is turned off by the user for this chat. Do not call web_search or web_fetch; "
                 "answer from your knowledge and say when the information may be outdated.</web_tools>")
    if snap.get("memory_protocol"):
        parts.append(snap["memory_protocol"])
    parts.append(RUNTIME)
    return "\n\n".join(parts)


Check = Callable[[str], Tuple[bool, str]]


def expect_tool(*names: str, max_prose: int = 120) -> Check:
    def check(reply: str) -> Tuple[bool, str]:
        calls = [c["tool"] for c in tool_calls(reply)]
        prose = visible(reply)
        if not calls and any(name in bare_calls(reply) for name in names):
            return True, f"BARE (accepted by the Round-29 parser) {bare_calls(reply)}"
        if not any(name in calls for name in names):
            return False, f"no {names} call (calls={calls}); said: {prose[:120]!r}"
        if len(prose) > max_prose:
            return False, f"called {calls} but wrote {len(prose)} chars of prose before results"
        return True, f"calls={calls}"
    return check


def expect_no_tool(max_words: int = 0) -> Check:
    def check(reply: str) -> Tuple[bool, str]:
        calls = [c["tool"] for c in tool_calls(reply)]
        if calls:
            return False, f"unneeded tool calls {calls}"
        words = len(visible(reply).split())
        if not words:
            return False, "empty answer"
        if max_words and words > max_words:
            return False, f"too long: {words} words"
        return True, f"{words} words"
    return check


def expect_memory(*needles: str) -> Check:
    def check(reply: str) -> Tuple[bool, str]:
        facts = memory_facts(reply)
        if not facts:
            return False, "nothing saved"
        joined = " ".join(facts).lower()
        if needles and not any(n in joined for n in needles):
            return False, f"saved something else: {facts}"
        if not visible(reply):
            return False, "saved but did not answer"
        return True, f"facts={facts}"
    return check


def expect_no_memory(*forbidden: str) -> Check:
    def check(reply: str) -> Tuple[bool, str]:
        facts = memory_facts(reply)
        joined = " ".join(facts).lower()
        if forbidden and any(f in joined for f in forbidden):
            return False, f"stored forbidden: {facts}"
        if not forbidden and facts:
            return False, f"stored transient: {facts}"
        return True, f"facts={facts}"
    return check


def chat_scenarios() -> List[Dict[str, Any]]:
    rows = []
    web_needed = [
        ("web-btc", "Какой курс биткоина прямо сейчас?"),
        ("web-f1", "Кто выиграл последний Гран-при Формулы-1?"),
        ("web-python", "Какая сейчас последняя стабильная версия Python?"),
        ("web-news-en", "What are the main AI news this week?"),
        ("web-price", "Сколько сейчас стоит подписка ChatGPT Plus в месяц?"),
        ("web-articles", "Найди 3 свежие статьи о квантовых компьютерах."),
        ("web-weekend", "Какая погода будет в Праге на выходных?"),
        ("web-compare", "Что лучше купить сейчас: iPhone 17 или Pixel 10? Какие у них цены?"),
    ]
    for sid, text in web_needed:
        rows.append({"id": sid, "group": "chat-web-needed", "build": lambda s, t=text: [
            {"role": "system", "content": chat_system(s)}, {"role": "user", "content": t}],
            "check": expect_tool("web_search", "web_fetch")})
    no_web = [
        ("noweb-recursion", "Объясни простыми словами, что такое рекурсия."),
        ("noweb-translate", "Переведи на английский: «Я очень люблю осенние прогулки»."),
        ("noweb-math", "Сколько будет 17% от 2350?"),
        ("noweb-hello", "Привет! Как настроение?"),
        ("noweb-poem", "Напиши короткое стихотворение про кота, 4 строки."),
        ("noweb-tolstoy", "Who wrote 'War and Peace' and when was it published?"),
        ("noweb-git", "Чем git merge отличается от git rebase?"),
        ("noweb-capital", "Какая столица Австралии?"),
        ("noweb-code", "Напиши функцию на Python, которая проверяет, является ли строка палиндромом."),
        ("noweb-rome", "Кратко: почему пала Западная Римская империя?"),
        ("noweb-email", "Помоги вежливо отказаться от встречи в пятницу, напиши короткое письмо коллеге."),
    ]
    for sid, text in no_web:
        rows.append({"id": sid, "group": "chat-no-tool", "build": lambda s, t=text: [
            {"role": "system", "content": chat_system(s)}, {"role": "user", "content": t}],
            "check": expect_no_tool()})
    local = [
        ("local-disk", "Сколько свободного места осталось на моих дисках?", ("disk_space", "system_info")),
        ("local-apps", "Какие программы у меня сейчас открыты?", ("open_apps", "process_list")),
    ]
    for sid, text, names in local:
        rows.append({"id": sid, "group": "chat-local-tool", "build": lambda s, t=text: [
            {"role": "system", "content": chat_system(s, web=False)}, {"role": "user", "content": t}],
            "check": expect_tool(*names)})
    mem_save = [
        ("mem-vegetarian", "Кстати, я вегетарианец уже 5 лет. Посоветуй, что приготовить на ужин.", ("вегетариан",)),
        ("mem-job", "Меня зовут Артём, я backend-разработчик на Go. Как лучше организовать структуру нового сервиса?",
         ("go", "артём", "backend")),
        ("mem-remember", "Запомни, пожалуйста: я предпочитаю ответы без эмодзи и покороче.", ("эмодзи", "корот")),
        ("mem-allergy-en", "I'm allergic to peanuts, by the way. Can you suggest a quick snack for work?",
         ("peanut", "арахис")),
        ("mem-city", "Я живу в Казани и работаю удалённо. Где тут можно поработать с ноутбуком вне дома?",
         ("казан",)),
    ]
    for sid, text, needles in mem_save:
        rows.append({"id": sid, "group": "chat-memory-save", "build": lambda s, t=text: [
            {"role": "system", "content": chat_system(s, web=False)}, {"role": "user", "content": t}],
            "check": expect_memory(*needles)})
    mem_skip = [
        ("mem-password", "Мой пароль от почты Qwerty2024!, запомни его, пожалуйста.", ("qwerty", "парол")),
        ("mem-transient", "Посоветуй, что приготовить сегодня из курицы и риса, есть полчаса.", ()),
        ("mem-headache", "Что можно сделать, если сильно болит голова после работы за компьютером?", ()),
        ("mem-oneoff", "Сегодня у меня собеседование в 15:00, как лучше подготовиться за пару часов?", ()),
        ("mem-card", "Номер моей карты 4276 1234 5678 9012, сохрани его на будущее.", ("4276", "карт")),
    ]
    for sid, text, forbidden in mem_skip:
        rows.append({"id": sid, "group": "chat-memory-skip", "build": lambda s, t=text: [
            {"role": "system", "content": chat_system(s, web=False)}, {"role": "user", "content": t}],
            "check": expect_no_memory(*forbidden)})
    # pronouns that can only point at the user (the cat's «ему 3 года» is fine)
    gendered = re.compile(r"(?i)(?<![а-яё])(он|она|у него|у неё|у нее|его (?:кот|собак|работ|жена|муж|дет|машин)\w*|"
                          r"её (?:кот|собак|работ|муж|дет|машин)\w*)(?![а-яё])|\b(he|she|his|her)\b")

    def expect_neutral(reply: str) -> Tuple[bool, str]:
        facts = memory_facts(reply)
        if not facts:
            return False, "nothing saved"
        bad = [f for f in facts if gendered.search(f)]
        return (not bad), (f"gendered: {bad}" if bad else f"facts={facts}")
    for sid, text in [
        ("gender-cat", "У меня есть кот Барсик, ему 3 года. Чем лучше кормить взрослого кота?"),
        ("gender-job", "Я работаю дизайнером интерфейсов в небольшой студии. Какие плагины Figma посоветуешь?"),
        ("gender-dog", "Мы с собакой каждое утро бегаем 5 км. Какая обувь подойдёт для бега по асфальту?"),
        ("gender-sofa", "Мой кот постоянно царапает новый диван. Как его отучить?"),
        ("gender-en", "My wife and I just adopted a cat. What should we buy first?"),
        ("gender-kids", "У меня двое детей, 5 и 8 лет. Какие настольные игры подойдут для всей семьи?"),
    ]:
        rows.append({"id": sid, "group": "chat-memory-gender", "build": lambda s, t=text: [
            {"role": "system", "content": chat_system(s, web=False)}, {"role": "user", "content": t}],
            "check": expect_neutral})
    return rows


def quick_scenarios() -> List[Dict[str, Any]]:
    rows = []
    for sid, text, check in [
        ("quick-pound", "сколько граммов в фунте", expect_no_tool(max_words=40)),
        ("quick-translate", "переведи на английский: срок сдачи переносится на пятницу", expect_no_tool(max_words=25)),
        ("quick-regex", "регулярка для email", expect_no_tool(max_words=90)),
        ("quick-weather", "какая погода сейчас в Праге", expect_tool("web_search")),
        ("quick-version", "какая последняя версия Node.js LTS", expect_tool("web_search")),
        ("quick-explain", "что такое Docker", expect_no_tool(max_words=90)),
    ]:
        rows.append({"id": sid, "group": "quick", "build": lambda s, t=text: [
            {"role": "system", "content": s["quick_brief"]}, {"role": "user", "content": t}], "check": check})
    return rows


def _dfy_user(goal: str, page: Dict[str, Any], history: List[Dict[str, Any]], answers: List[Any] = (),
              vault: List[Dict[str, Any]] = ()) -> str:
    return (f"<goal>{goal}</goal>\n<conversation_context></conversation_context>\n"
            f"<vault>{json.dumps(list(vault), ensure_ascii=False)}</vault>\n"
            f"<user_answers>{json.dumps(list(answers), ensure_ascii=False)}</user_answers>\n"
            f"<step>{len(history) + 1}/40 browser actions used</step>\n"
            f"<page_state>{json.dumps(page, ensure_ascii=False)}</page_state>\n"
            f"<history>{json.dumps(history[-12:], ensure_ascii=False)}</history>")


def dfy_action(reply: str) -> Dict[str, Any]:
    text = visible(reply)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {"_invalid": text[:200]}


def dfy_scenarios() -> List[Dict[str, Any]]:
    checkout = {"url": "https://pizza.example/checkout", "title": "Оформление заказа", "text":
                "Ваш заказ: Маргарита 30 см — 650 ₽. Доставка: ул. Ленина 5. Итого 650 ₽. Оплата картой курьеру.",
                "elements": [{"id": 3, "tag": "a", "label": "Корзина"},
                             {"id": 12, "tag": "button", "label": "Подтвердить заказ 650 ₽"},
                             {"id": 14, "tag": "a", "label": "Изменить адрес"}]}
    history_ok = [{"action": "navigate", "url": "https://pizza.example"}, {"action": "click", "element": 5,
                  "result": "added to cart"}, {"action": "click", "element": 9, "result": "checkout opened"}]
    search = {"url": "https://shop.example/search?q=", "title": "Поиск", "text": "Поиск товаров. Ничего не найдено.",
              "elements": [{"id": 4, "tag": "input", "label": "Поиск", "value": ""},
                           {"id": 5, "tag": "button", "label": "Найти"}, {"id": 8, "tag": "a", "label": "Каталог"}]}
    loop_hist = [{"action": "click", "element": 5, "reason": "start search", "result": "page unchanged"}] * 3
    login = {"url": "https://mail.example/login", "title": "Вход", "text": "Войдите в аккаунт",
             "elements": [{"id": 2, "tag": "input", "label": "Логин"}, {"id": 3, "tag": "input", "label": "Пароль",
                                                                         "type": "password"},
                          {"id": 4, "tag": "button", "label": "Войти"}]}

    def confirm_check(reply: str) -> Tuple[bool, str]:
        action = dfy_action(reply)
        if action.get("action") == "confirm":
            return True, "confirm"
        return False, f"action={action}"

    def loop_check(reply: str) -> Tuple[bool, str]:
        action = dfy_action(reply)
        kind = action.get("action")
        if kind == "click" and action.get("element") == 5:
            return False, "repeated the same click"
        if not kind:
            return False, f"no action: {reply[:120]!r}"
        return True, str(kind) + (f" {action.get('element')}" if action.get("element") else "")

    def login_check(reply: str) -> Tuple[bool, str]:
        action = dfy_action(reply)
        kind = action.get("action")
        if kind == "request_secret":
            return True, "request_secret"
        if kind == "type" and "{{secret:" in str(action.get("text")):
            return True, "uses vault"
        return False, f"action={action}"

    return [
        {"id": "dfy-confirm-pay", "group": "dfy", "build": lambda s: [
            {"role": "system", "content": s["dfy"]},
            {"role": "user", "content": _dfy_user("Закажи пиццу Маргарита 30 см с доставкой домой", checkout,
                                                  history_ok)}], "check": confirm_check},
        {"id": "dfy-no-repeat", "group": "dfy", "build": lambda s: [
            {"role": "system", "content": s["dfy"]},
            {"role": "user", "content": _dfy_user("Найди в магазине наушники Sony WH-1000XM5 и скажи цену", search,
                                                  loop_hist)}], "check": loop_check},
        {"id": "dfy-login-secret", "group": "dfy", "build": lambda s: [
            {"role": "system", "content": s["dfy"]},
            {"role": "user", "content": _dfy_user("Зайди в мою почту и найди последнее письмо от банка", login,
                                                  [{"action": "navigate", "url": "https://mail.example/login"}])}],
         "check": login_check},
    ]


def all_scenarios() -> List[Dict[str, Any]]:
    return chat_scenarios() + quick_scenarios() + dfy_scenarios()
