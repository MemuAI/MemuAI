"""Readable browser actions and one confirmation for a set of tab closures."""
from __future__ import annotations

def tab_ids(values):
    if not isinstance(values, list) or not values or len(values) > 250:
        raise ValueError("Укажите от 1 до 250 вкладок")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
        raise ValueError("Нужны корректные идентификаторы вкладок")
    return list(dict.fromkeys(values))

def group_closures(calls):
    result, pending = [], []
    def flush():
        if pending:
            result.append({"tool": "chrome_close_tabs", "args": {"tab_ids": list(dict.fromkeys(pending))}})
            pending.clear()
    for call in calls:
        name, args = call.get("tool"), call.get("args") or {}
        try:
            if name == "chrome_close":
                pending.extend(tab_ids([args.get("tab_id")]))
            elif name == "chrome_close_tabs":
                pending.extend(tab_ids(args.get("tab_ids")))
            else:
                flush()
                result.append(call)
        except ValueError:
            flush()
            result.append(call)
    flush()
    return result

async def prepare_close(args):
    from .browser_extension import bridge
    ids = tab_ids(args.get("tab_ids") if "tab_ids" in args else [args.get("tab_id")])
    value = await bridge.rpc("tabs", {})
    rows = {row.get("tab_id"): row for row in value.get("tabs", []) if isinstance(row, dict)}
    if any(value not in rows for value in ids):
        raise ValueError("Одна из вкладок уже закрыта. Получите новый список вкладок")
    tabs = [{"tab_id": value, "title": str(rows[value].get("title") or "Без названия")[:1000],
             "url": str(rows[value].get("url") or "")[:16000]} for value in ids]
    expected = args.get("_expected_tabs")
    if expected is not None:
        if not isinstance(expected, list) or len(expected) != len(tabs):
            raise ValueError("Список вкладок изменился. Повторите действие")
        old = {row.get("tab_id"): row.get("url") for row in expected if isinstance(row, dict)}
        if any(old.get(row["tab_id"]) != row["url"] for row in tabs):
            raise ValueError("Вкладка перешла на другую страницу. Требуется новое подтверждение")
    return {**args, "tab_ids": ids, "_expected_tabs": tabs}, tabs

def presentation(name, args, tabs=None):
    titles = {"chrome_tabs": "Список вкладок", "chrome_read": "Читаю страницу", "chrome_screenshot": "Снимок страницы",
              "chrome_open": "Открыть вкладку", "chrome_navigate": "Перейти на страницу", "chrome_action": "Действие на странице",
              "web_search": "Поиск в интернете", "web_fetch": "Читаю веб-страницу", "current_time": "Текущее время"}
    if name in ("chrome_close", "chrome_close_tabs"):
        rows = tabs or []
        return {"title": "Закрыть вкладку" if len(rows) == 1 else f"Закрыть вкладки ({len(rows)})",
                "description": "\n".join(row["title"] + " — " + row["url"] for row in rows), "tabs": rows}
    description = str(args.get("query") or args.get("url") or "")[:1000]
    return {"title": titles.get(name, name.replace("_", " ")), "description": description}
