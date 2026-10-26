"""Round 33: the rest of Notion for the assistant — database schema, simple property values, updating, archiving
(deleting to the trash) and restoring pages, replacing a page's content, deleting a block, comments.

Audit of Round 30: ``notion_create_page{properties}`` expected raw Notion JSON (``{"Status": {"select": {"name":
"Done"}}}``) — models usually wrote ``{"Status": "Done"}`` and Notion answered 400.  :func:`simple_properties` now
turns plain values into the right shape using the database schema (title, rich_text, number, select, multi_select,
status, date, checkbox, url, email, phone_number, people by id, relation by id); raw Notion JSON still passes through.

HTTP and errors: :func:`app.integrations_notion._request`.  :func:`app.integrations_notion.run` hands every unknown
``notion_*`` name to :func:`run`.
"""
from __future__ import annotations

from typing import Any, Dict, List

TOOLS: List[str] = [
    "notion_get_database{database_id}",
    "notion_update_page{page_id,title,properties,icon}",
    "notion_delete_page{page_id}",
    "notion_restore_page{page_id}",
    "notion_replace_content{page_id,content}",
    "notion_delete_block{block_id}",
    "notion_comment{page_id,text}",
]
WRITE = {"notion_update_page", "notion_delete_page", "notion_restore_page", "notion_replace_content",
         "notion_delete_block", "notion_comment"}
DANGER = {"notion_delete_page", "notion_replace_content", "notion_delete_block"}

HINT = ("Notion: ids come from notion_search (a page link works too). Before adding rows to a database call "
        "notion_get_database to learn its property names and options; properties take plain values "
        "({\"Status\": \"Done\", \"Due\": \"2026-10-16\", \"Tags\": [\"a\"], \"Done\": true}). notion_delete_page moves "
        "a page to Notion's trash (notion_restore_page brings it back).")

def _n():
    from . import integrations_notion
    return integrations_notion

def _rich(text: str) -> List[Dict[str, Any]]:
    return _n()._rich(str(text))

def simple_properties(schema: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    """Plain values → Notion property objects by the database schema; values already in Notion's shape pass as is."""
    props = schema.get("properties") or {}
    out: Dict[str, Any] = {}
    lower = {str(name).lower(): name for name in props}
    for key, value in (values or {}).items():
        name = key if key in props else lower.get(str(key).lower(), key)
        kind = (props.get(name) or {}).get("type")
        if isinstance(value, dict) and (not kind or kind in value or len(value) == 1 and next(iter(value)) in (
                "title", "rich_text", "number", "select", "multi_select", "status", "date", "checkbox", "url",
                "email", "phone_number", "people", "relation", "files")):
            out[name] = value
            continue
        if kind is None:
            out[name] = {"rich_text": _rich("" if value is None else str(value))}
        elif kind == "title":
            out[name] = {"title": _rich(str(value or ""))}
        elif kind == "rich_text":
            out[name] = {"rich_text": _rich("" if value is None else str(value))}
        elif kind == "number":
            try:
                out[name] = {"number": None if value in (None, "") else float(str(value).replace(",", "."))}
            except ValueError:
                out[name] = {"number": None}
        elif kind in ("select", "status"):
            out[name] = {kind: None if value in (None, "") else {"name": str(value)}}
        elif kind == "multi_select":
            items = value if isinstance(value, list) else [x.strip() for x in str(value or "").split(",") if x.strip()]
            out[name] = {"multi_select": [{"name": str(item)} for item in items]}
        elif kind == "date":
            if value in (None, ""):
                out[name] = {"date": None}
            elif isinstance(value, (list, tuple)) and value:
                out[name] = {"date": {"start": str(value[0]), **({"end": str(value[1])} if len(value) > 1 else {})}}
            else:
                text = str(value)
                start, _sep, end = text.partition("/") if "/" in text else text.partition(" → ")
                out[name] = {"date": {"start": start.strip(), **({"end": end.strip()} if end.strip() else {})}}
        elif kind == "checkbox":
            out[name] = {"checkbox": value is True or str(value).strip().lower() in ("1", "true", "yes", "да", "x")}
        elif kind in ("url", "email", "phone_number"):
            out[name] = {kind: None if value in (None, "") else str(value)}
        elif kind in ("people", "relation"):
            items = value if isinstance(value, list) else [x.strip() for x in str(value or "").split(",") if x.strip()]
            out[name] = {kind: [{"id": _n()._id(item, "id") if kind == "relation" else str(item)} for item in items]}
        else:

            continue
    return out

def _schema_row(schema: Dict[str, Any]) -> Dict[str, Any]:
    props = {}
    for name, prop in (schema.get("properties") or {}).items():
        if not isinstance(prop, dict):
            continue
        kind = prop.get("type")
        row: Dict[str, Any] = {"type": kind}
        options = (prop.get(kind) or {}).get("options") if kind in ("select", "multi_select", "status") else None
        if options:
            row["options"] = [o.get("name") for o in options if isinstance(o, dict)]
        props[name] = row
    return props

def run(tool: str, args: Dict[str, Any]) -> Any:
    n = _n()
    if tool == "notion_get_database":
        database_id = n._id(args.get("database_id") or args.get("id"), "database_id")
        schema = n._request("GET", "/databases/" + database_id)
        return {"id": schema.get("id"), "title": n._title(schema), "url": schema.get("url"), "properties": _schema_row(schema)}
    if tool == "notion_update_page":
        page_id = n._id(args.get("page_id") or args.get("id"))
        body: Dict[str, Any] = {}
        props: Dict[str, Any] = {}
        page = n._request("GET", "/pages/" + page_id)
        if isinstance(args.get("properties"), dict) and args["properties"]:
            parent = page.get("parent") or {}
            if parent.get("type") == "database_id":
                schema = n._request("GET", "/databases/" + str(parent.get("database_id")))
            else:
                schema = {"properties": page.get("properties") or {}}
            props.update(simple_properties(schema, args["properties"]))
        if str(args.get("title") or "").strip():
            title_prop = next((name for name, prop in (page.get("properties") or {}).items()
                               if isinstance(prop, dict) and prop.get("type") == "title"), "title")
            props[title_prop] = {"title": _rich(str(args["title"]).strip())}
        if props:
            body["properties"] = props
        icon = str(args.get("icon") or "").strip()
        if icon:
            body["icon"] = {"type": "external", "external": {"url": icon}} if icon.startswith("http") else {"type": "emoji", "emoji": icon}
        if not body:
            raise n._err("Нечего менять: title, properties или icon", argument=True)
        row = n._request("PATCH", "/pages/" + page_id, json_body=body)
        return {"updated": True, "id": row.get("id"), "title": n._title(row), "url": row.get("url")}
    if tool in ("notion_delete_page", "notion_restore_page"):
        page_id = n._id(args.get("page_id") or args.get("id"))
        row = n._request("PATCH", "/pages/" + page_id, json_body={"archived": tool == "notion_delete_page"})
        return {"id": row.get("id"), "title": n._title(row), "in_trash": bool(row.get("archived") or row.get("in_trash")),
                "note": "Страница в корзине Notion — её можно вернуть notion_restore_page или из Trash." if tool == "notion_delete_page" else ""}
    if tool == "notion_replace_content":
        page_id = n._id(args.get("page_id") or args.get("id"))
        blocks = n.markdown_blocks(str(args.get("content") or ""))
        removed = 0
        cursor = None
        ids: List[str] = []
        while True:
            params: Dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = n._request("GET", "/blocks/%s/children" % page_id, params=params)
            ids += [str(b.get("id")) for b in data.get("results") or [] if isinstance(b, dict)
                    and b.get("type") not in ("child_page", "child_database")]
            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor or len(ids) >= n.MAX_BLOCKS:
                break
        for block_id in ids:
            n._request("DELETE", "/blocks/" + block_id)
            removed += 1
        added = n._append(page_id, blocks) if blocks else 0
        return {"id": page_id, "removed_blocks": removed, "added_blocks": added,
                "note": "Вложенные страницы и базы сохранены." if removed else ""}
    if tool == "notion_delete_block":
        block_id = n._id(args.get("block_id") or args.get("id"), "block_id")
        n._request("DELETE", "/blocks/" + block_id)
        return {"deleted": True, "block_id": block_id}
    if tool == "notion_comment":
        page_id = n._id(args.get("page_id") or args.get("id"))
        text = str(args.get("text") or args.get("body") or "").strip()
        if not text:
            raise n._err("Укажите text комментария", argument=True)
        row = n._request("POST", "/comments", json_body={"parent": {"page_id": page_id}, "rich_text": _rich(text)})
        return {"comment_id": row.get("id"), "page_id": page_id}
    raise n._err("Неизвестный инструмент Notion: %s" % tool, argument=True)
