"""Round 33: Google Docs, Sheets, Tasks and Contacts for the assistant (the same shared Google account).

Each is a separate service in Settings → Integrations → Google (its own tick and scope: ``documents``,
``spreadsheets``, ``tasks``, ``contacts``) and a separate Google Cloud API the user enables in their project
(Docs API, Sheets API, Tasks API, People API) — a disabled API answers with a clear message and a direct link
(``integrations._google_error``).  Tool prefixes are ``gdocs_ / gsheets_ / gtasks_ / gcontacts_`` (``docs_*`` are
MemuAI's own documentation tools).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

DOCS_API = "https://docs.googleapis.com/v1"
SHEETS_API = "https://sheets.googleapis.com/v4"
TASKS_API = "https://tasks.googleapis.com/tasks/v1"
PEOPLE_API = "https://people.googleapis.com/v1"
MAX_TEXT = 100000
PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations,birthdays,addresses,biographies,metadata"

DOCS_TOOLS = [
    "gdocs_read{document_id}",
    "gdocs_create{title,content,folder_id}",
    "gdocs_append{document_id,text}",
    "gdocs_replace{document_id,find,replace,match_case}",
]
SHEETS_TOOLS = [
    "gsheets_read{spreadsheet_id,range}",
    "gsheets_create{title,sheets,values}",
    "gsheets_write{spreadsheet_id,range,values}",
    "gsheets_append{spreadsheet_id,range,values}",
    "gsheets_clear{spreadsheet_id,range}",
    "gsheets_add_sheet{spreadsheet_id,title}",
    "gsheets_delete_sheet{spreadsheet_id,sheet}",
]
TASKS_TOOLS = [
    "gtasks_lists{}",
    "gtasks_list{list_id,show_completed,limit}",
    "gtasks_create{title,notes,due,list_id,parent}",
    "gtasks_update{task_id,list_id,title,notes,due,status}",
    "gtasks_complete{task_id,list_id}",
    "gtasks_delete{task_id,list_id}",
    "gtasks_create_list{title}",
]
CONTACTS_TOOLS = [
    "gcontacts_search{query,limit}",
    "gcontacts_list{limit,page_token}",
    "gcontacts_get{resource_name}",
    "gcontacts_create{name,email,phone,organization,notes}",
    "gcontacts_update{resource_name,name,email,phone,organization,notes}",
    "gcontacts_delete{resource_name}",
]
READ = {"gdocs_read", "gsheets_read", "gtasks_lists", "gtasks_list", "gcontacts_search", "gcontacts_list", "gcontacts_get"}
WRITE = {t.split("{")[0] for t in DOCS_TOOLS + SHEETS_TOOLS + TASKS_TOOLS + CONTACTS_TOOLS} - READ
ALWAYS: set = set()
DANGER = {"gsheets_clear", "gsheets_delete_sheet", "gtasks_delete", "gcontacts_delete", "gdocs_replace"}

DOCS_HINT = ("Google Docs: document_id is the id from the link docs.google.com/document/d/<id> or from drive_list_files "
             "(mime doc). gdocs_create makes a new document from text; gdocs_append adds text at the end.")
SHEETS_HINT = ("Google Sheets: ranges use A1 notation with the sheet name ('Sheet1!A1:D20'); gsheets_read without a range "
               "lists the sheets and returns the first one. values is a 2-D array [[...],[...]]; formulas like =SUM(A1:A9) work.")
TASKS_HINT = "Google Tasks: list_id defaults to the main list; due is a date (2026-10-16)."
CONTACTS_HINT = "Google Contacts: resource_name looks like people/c123…; gcontacts_search finds by name, email or phone."


def _ig():
    from . import integrations
    return integrations


def _arg(text: str):
    return _ig().IntegrationArgumentError(text)


def _call(service: str, method: str, url: str, **kwargs: Any) -> Any:
    return _ig()._google(service, method, url, **kwargs)


def _doc_id(value: Any, what: str) -> str:
    raw = str(value or "").strip()
    found = re.search(r"/d/([A-Za-z0-9_-]{10,})", raw)
    if found:
        raw = found.group(1)
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,}", raw or ""):
        raise _arg("Укажите %s — id из ссылки или из drive_list_files" % what)
    return raw


def _values(value: Any) -> List[List[Any]]:
    if isinstance(value, str):
        text = value.strip()
        try:
            value = json.loads(text)
        except ValueError:
            value = [line.split("\t") if "\t" in line else line.split(",") for line in text.splitlines() if line.strip()]
    if isinstance(value, list) and value and not isinstance(value[0], list):
        value = [value]
    if not isinstance(value, list) or not value:
        raise _arg("values — двумерный массив, например [[\"Имя\", \"Сумма\"], [\"Анна\", 120]]")
    return [[cell if isinstance(cell, (int, float, bool)) or cell is None else str(cell) for cell in row]
            for row in value if isinstance(row, list)][:10000]


# --------------------------------------------------------------------------- Docs
def _doc_text(doc: Dict[str, Any]) -> str:
    lines: List[str] = []

    def walk(content: List[Dict[str, Any]]) -> None:
        for element in content or []:
            if "paragraph" in element:
                para = element["paragraph"]
                text = "".join(str((run.get("textRun") or {}).get("content") or "") for run in para.get("elements") or [])
                style = str((para.get("paragraphStyle") or {}).get("namedStyleType") or "")
                level = {"HEADING_1": "# ", "HEADING_2": "## ", "HEADING_3": "### ", "TITLE": "# "}.get(style, "")
                if "bullet" in para:
                    level = "- "
                lines.append(level + text.rstrip("\n") if text.strip() else "")
            elif "table" in element:
                for row in element["table"].get("tableRows") or []:
                    cells = []
                    for cell in row.get("tableCells") or []:
                        inner: List[str] = []
                        for item in cell.get("content") or []:
                            for run in (item.get("paragraph") or {}).get("elements") or []:
                                inner.append(str((run.get("textRun") or {}).get("content") or "").strip())
                        cells.append(" ".join(x for x in inner if x))
                    lines.append("| " + " | ".join(cells) + " |")
            elif "tableOfContents" in element:
                walk(element["tableOfContents"].get("content") or [])
    walk((doc.get("body") or {}).get("content") or [])
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _docs(tool: str, args: Dict[str, Any]) -> Any:
    S = "google_docs"
    if tool == "gdocs_read":
        doc_id = _doc_id(args.get("document_id") or args.get("id"), "document_id")
        doc = _call(S, "GET", DOCS_API + "/documents/" + doc_id)
        text = _doc_text(doc)
        return {"id": doc_id, "title": doc.get("title"), "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT,
                "link": "https://docs.google.com/document/d/%s/edit" % doc_id}
    if tool == "gdocs_create":
        title = str(args.get("title") or "").strip() or "Новый документ"
        doc = _call(S, "POST", DOCS_API + "/documents", json_body={"title": title})
        doc_id = str(doc.get("documentId") or "")
        content = str(args.get("content") or "")
        if content:
            _call(S, "POST", DOCS_API + "/documents/%s:batchUpdate" % doc_id,
                  json_body={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]})
        out = {"created": True, "id": doc_id, "title": title, "link": "https://docs.google.com/document/d/%s/edit" % doc_id}
        if args.get("folder_id"):
            try:
                ig = _ig()
                folder = str(args["folder_id"]).strip()
                _call("google_drive", "PATCH", ig.DRIVE_API + "/files/" + quote(doc_id, safe=""),
                      params={"addParents": folder, "removeParents": "root", "supportsAllDrives": "true"}, json_body={})
                out["folder_id"] = folder
            except Exception as exc:  # the document exists either way
                out["note"] = "Документ создан в «Мой диск»: переместить в папку не удалось (%s)" % exc
        return out
    if tool == "gdocs_append":
        doc_id = _doc_id(args.get("document_id") or args.get("id"), "document_id")
        text = str(args.get("text") or args.get("content") or "")
        if not text:
            raise _arg("Укажите text")
        _call(S, "POST", DOCS_API + "/documents/%s:batchUpdate" % doc_id,
              json_body={"requests": [{"insertText": {"endOfSegmentLocation": {}, "text": ("\n" + text) if not text.startswith("\n") else text}}]})
        return {"id": doc_id, "appended": len(text)}
    if tool == "gdocs_replace":
        doc_id = _doc_id(args.get("document_id") or args.get("id"), "document_id")
        find = str(args.get("find") or "")
        if not find:
            raise _arg("Укажите find — что заменить")
        row = _call(S, "POST", DOCS_API + "/documents/%s:batchUpdate" % doc_id, json_body={"requests": [{"replaceAllText": {
            "containsText": {"text": find, "matchCase": str(args.get("match_case") or "").lower() in ("1", "true", "yes")},
            "replaceText": str(args.get("replace") or "")}}]})
        changed = ((row.get("replies") or [{}])[0].get("replaceAllText") or {}).get("occurrencesChanged", 0)
        return {"id": doc_id, "replaced": changed}
    raise _arg("Неизвестный инструмент Google Docs: %s" % tool)


# --------------------------------------------------------------------------- Sheets
def _sheets(tool: str, args: Dict[str, Any]) -> Any:
    S = "google_sheets"
    if tool == "gsheets_create":
        title = str(args.get("title") or "").strip() or "Новая таблица"
        names = args.get("sheets") or []
        names = names if isinstance(names, list) else [x.strip() for x in str(names).split(",") if x.strip()]
        body: Dict[str, Any] = {"properties": {"title": title}}
        if names:
            body["sheets"] = [{"properties": {"title": str(n)[:100]}} for n in names[:50]]
        row = _call(S, "POST", SHEETS_API + "/spreadsheets", json_body=body)
        sid = str(row.get("spreadsheetId") or "")
        out = {"created": True, "id": sid, "title": title, "link": row.get("spreadsheetUrl")}
        if args.get("values") not in (None, "", []):
            first = (row.get("sheets") or [{}])[0].get("properties", {}).get("title") or "Sheet1"
            values = _values(args.get("values"))
            _call(S, "PUT", SHEETS_API + "/spreadsheets/%s/values/%s" % (sid, quote("'%s'!A1" % first, safe="")),
                  params={"valueInputOption": "USER_ENTERED"}, json_body={"values": values})
            out["rows"] = len(values)
        return out
    sid = _doc_id(args.get("spreadsheet_id") or args.get("id"), "spreadsheet_id")
    base = SHEETS_API + "/spreadsheets/" + sid
    rng = str(args.get("range") or "").strip()
    if tool == "gsheets_read":
        meta = _call(S, "GET", base, params={"fields": "properties.title,spreadsheetUrl,sheets.properties(sheetId,title,gridProperties)"})
        sheets = [{"title": (s.get("properties") or {}).get("title"), "id": (s.get("properties") or {}).get("sheetId"),
                   "rows": ((s.get("properties") or {}).get("gridProperties") or {}).get("rowCount")} for s in meta.get("sheets") or []]
        target = rng or ("'%s'!A1:Z200" % sheets[0]["title"] if sheets else "A1:Z200")
        data = _call(S, "GET", base + "/values/" + quote(target, safe=""), params={"valueRenderOption": "FORMATTED_VALUE"})
        values = data.get("values") or []
        return {"title": (meta.get("properties") or {}).get("title"), "link": meta.get("spreadsheetUrl"), "sheets": sheets,
                "range": data.get("range"), "values": values[:2000], "truncated": len(values) > 2000}
    if tool in ("gsheets_write", "gsheets_append"):
        if not rng:
            raise _arg("Укажите range, например 'Лист1!A1' (для append — лист или диапазон таблицы)")
        values = _values(args.get("values"))
        if tool == "gsheets_write":
            row = _call(S, "PUT", base + "/values/" + quote(rng, safe=""), params={"valueInputOption": "USER_ENTERED"},
                        json_body={"values": values})
            return {"updated_range": row.get("updatedRange"), "cells": row.get("updatedCells")}
        row = _call(S, "POST", base + "/values/%s:append" % quote(rng, safe=""),
                    params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}, json_body={"values": values})
        return {"appended_range": (row.get("updates") or {}).get("updatedRange"), "rows": (row.get("updates") or {}).get("updatedRows")}
    if tool == "gsheets_clear":
        if not rng:
            raise _arg("Укажите range — что очистить")
        row = _call(S, "POST", base + "/values/%s:clear" % quote(rng, safe=""), json_body={})
        return {"cleared": row.get("clearedRange") or rng}
    if tool == "gsheets_add_sheet":
        title = str(args.get("title") or "").strip()
        if not title:
            raise _arg("Укажите title нового листа")
        row = _call(S, "POST", base + ":batchUpdate", json_body={"requests": [{"addSheet": {"properties": {"title": title}}}]})
        props = (((row.get("replies") or [{}])[0].get("addSheet") or {}).get("properties") or {})
        return {"added": True, "title": props.get("title") or title, "sheet_id": props.get("sheetId")}
    if tool == "gsheets_delete_sheet":
        sheet = str(args.get("sheet") or "").strip()
        if not sheet:
            raise _arg("Укажите sheet — имя или id листа")
        meta = _call(S, "GET", base, params={"fields": "sheets.properties(sheetId,title)"})
        match = next((s.get("properties") or {} for s in meta.get("sheets") or []
                      if str((s.get("properties") or {}).get("sheetId")) == sheet or (s.get("properties") or {}).get("title") == sheet), None)
        if not match:
            raise _arg("Лист не найден: %s" % sheet)
        _call(S, "POST", base + ":batchUpdate", json_body={"requests": [{"deleteSheet": {"sheetId": match.get("sheetId")}}]})
        return {"deleted": True, "sheet": match.get("title")}
    raise _arg("Неизвестный инструмент Google Sheets: %s" % tool)


# --------------------------------------------------------------------------- Tasks
def _list_id(args: Dict[str, Any]) -> str:
    return quote(str(args.get("list_id") or "@default").strip() or "@default", safe="@")


def _due(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    from datetime import date
    try:
        return date.fromisoformat(text[:10]).isoformat() + "T00:00:00.000Z"   # Tasks keeps only the date
    except ValueError as exc:
        raise _arg("due — дата ГГГГ-ММ-ДД") from exc


def _task_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": row.get("id"), "title": row.get("title"), "notes": row.get("notes"), "status": row.get("status"),
            "due": str(row.get("due") or "")[:10] or None, "parent": row.get("parent"), "completed": row.get("completed")}


def _tasks(tool: str, args: Dict[str, Any]) -> Any:
    S = "google_tasks"
    if tool == "gtasks_lists":
        rows = _call(S, "GET", TASKS_API + "/users/@me/lists", params={"maxResults": 100})
        return {"lists": [{"id": r.get("id"), "title": r.get("title")} for r in rows.get("items") or []]}
    if tool == "gtasks_create_list":
        title = str(args.get("title") or "").strip()
        if not title:
            raise _arg("Укажите title списка")
        row = _call(S, "POST", TASKS_API + "/users/@me/lists", json_body={"title": title})
        return {"created": True, "list_id": row.get("id"), "title": row.get("title")}
    base = TASKS_API + "/lists/%s/tasks" % _list_id(args)
    if tool == "gtasks_list":
        show = str(args.get("show_completed") or "").lower() in ("1", "true", "yes")
        rows = _call(S, "GET", base, params={"maxResults": _ig()._int(args.get("limit"), 50, 1, 100),
                                             "showCompleted": "true" if show else "false", "showHidden": "true" if show else "false"})
        return {"tasks": [_task_row(r) for r in rows.get("items") or []]}
    if tool == "gtasks_create":
        title = str(args.get("title") or "").strip()
        if not title:
            raise _arg("Укажите title задачи")
        body: Dict[str, Any] = {"title": title, "notes": str(args.get("notes") or "")}
        if _due(args.get("due")):
            body["due"] = _due(args.get("due"))
        params = {"parent": str(args["parent"])} if args.get("parent") else None
        row = _call(S, "POST", base, json_body=body, params=params)
        return {"created": True, **_task_row(row)}
    task = str(args.get("task_id") or args.get("id") or "").strip()
    if not task:
        raise _arg("Укажите task_id (его даёт gtasks_list)")
    url = base + "/" + quote(task, safe="")
    if tool == "gtasks_update":
        body = {k: str(args[k]) for k in ("title", "notes") if args.get(k) is not None}
        if args.get("due") is not None:
            body["due"] = _due(args.get("due"))
        status = str(args.get("status") or "").lower()
        if status in ("completed", "needsaction", "needs_action", "open", "done"):
            body["status"] = "completed" if status in ("completed", "done") else "needsAction"
            if body["status"] == "needsAction":
                body["completed"] = None
        if not body:
            raise _arg("Нечего менять: title, notes, due или status")
        row = _call(S, "PATCH", url, json_body=body)
        return {"updated": True, **_task_row(row)}
    if tool == "gtasks_complete":
        row = _call(S, "PATCH", url, json_body={"status": "completed"})
        return {"completed": True, **_task_row(row)}
    if tool == "gtasks_delete":
        _call(S, "DELETE", url)
        return {"deleted": True, "task_id": task}
    raise _arg("Неизвестный инструмент Google Tasks: %s" % tool)


# --------------------------------------------------------------------------- Contacts (People API)
def _person(row: Dict[str, Any]) -> Dict[str, Any]:
    def first(key: str, field: str) -> Any:
        items = row.get(key) or []
        return items[0].get(field) if items and isinstance(items[0], dict) else None
    return {"resource_name": row.get("resourceName"), "name": first("names", "displayName"),
            "emails": [e.get("value") for e in row.get("emailAddresses") or [] if isinstance(e, dict)],
            "phones": [p.get("value") for p in row.get("phoneNumbers") or [] if isinstance(p, dict)],
            "organization": first("organizations", "name"), "title": first("organizations", "title"),
            "notes": first("biographies", "value")}


def _person_body(args: Dict[str, Any]) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    name = str(args.get("name") or "").strip()
    if name:
        parts = name.split(None, 1)
        body["names"] = [{"givenName": parts[0], **({"familyName": parts[1]} if len(parts) > 1 else {})}]
    for key, field, inner in (("email", "emailAddresses", "value"), ("phone", "phoneNumbers", "value")):
        value = args.get(key)
        if value not in (None, ""):
            items = value if isinstance(value, list) else [x.strip() for x in str(value).split(",") if x.strip()]
            body[field] = [{inner: str(item)} for item in items]
    if str(args.get("organization") or "").strip():
        body["organizations"] = [{"name": str(args["organization"]).strip()}]
    if args.get("notes") not in (None, ""):
        body["biographies"] = [{"value": str(args["notes"]), "contentType": "TEXT_PLAIN"}]
    return body


def _resource(args: Dict[str, Any]) -> str:
    text = str(args.get("resource_name") or args.get("id") or "").strip()
    if not re.fullmatch(r"people/[A-Za-z0-9_-]+", text):
        raise _arg("Укажите resource_name вида people/c123… (его даёт gcontacts_search)")
    return text


def _contacts(tool: str, args: Dict[str, Any]) -> Any:
    S = "google_contacts"
    ig = _ig()
    if tool == "gcontacts_search":
        query = str(args.get("query") or "").strip()
        if not query:
            raise _arg("Укажите query — имя, почта или телефон")
        params = {"query": query, "readMask": PERSON_FIELDS, "pageSize": ig._int(args.get("limit"), 10, 1, 30)}
        rows = _call(S, "GET", PEOPLE_API + "/people:searchContacts", params=params)
        if not rows.get("results"):
            # People API: the search cache warms up on the first (empty) request — Google's documented workaround.
            _call(S, "GET", PEOPLE_API + "/people:searchContacts", params={"query": "", "readMask": "names"})
            rows = _call(S, "GET", PEOPLE_API + "/people:searchContacts", params=params)
        return {"contacts": [_person(r.get("person") or {}) for r in rows.get("results") or []]}
    if tool == "gcontacts_list":
        params = {"personFields": PERSON_FIELDS, "pageSize": ig._int(args.get("limit"), 50, 1, 200), "sortOrder": "LAST_MODIFIED_DESCENDING"}
        if args.get("page_token"):
            params["pageToken"] = str(args["page_token"])
        rows = _call(S, "GET", PEOPLE_API + "/people/me/connections", params=params)
        return {"contacts": [_person(r) for r in rows.get("connections") or []], "total": rows.get("totalPeople"),
                "next_page_token": rows.get("nextPageToken") or ""}
    if tool == "gcontacts_get":
        return _person(_call(S, "GET", PEOPLE_API + "/" + _resource(args), params={"personFields": PERSON_FIELDS}))
    if tool == "gcontacts_create":
        body = _person_body(args)
        if not body.get("names") and not body.get("emailAddresses"):
            raise _arg("Укажите хотя бы name или email")
        return {"created": True, **_person(_call(S, "POST", PEOPLE_API + "/people:createContact", json_body=body,
                                                 params={"personFields": PERSON_FIELDS}))}
    if tool == "gcontacts_update":
        name = _resource(args)
        body = _person_body(args)
        if not body:
            raise _arg("Нечего менять: name, email, phone, organization или notes")
        current = _call(S, "GET", PEOPLE_API + "/" + name, params={"personFields": "metadata"})
        body["etag"] = current.get("etag")
        row = _call(S, "PATCH", PEOPLE_API + "/%s:updateContact" % name, json_body=body,
                    params={"updatePersonFields": ",".join(k for k in body if k != "etag"), "personFields": PERSON_FIELDS})
        return {"updated": True, **_person(row)}
    if tool == "gcontacts_delete":
        name = _resource(args)
        _call(S, "DELETE", PEOPLE_API + "/%s:deleteContact" % name)
        return {"deleted": True, "resource_name": name}
    raise _arg("Неизвестный инструмент Google Contacts: %s" % tool)


def run(service: str, tool: str, args: Dict[str, Any]) -> Any:
    if service == "google_docs":
        return _docs(tool, args)
    if service == "google_sheets":
        return _sheets(tool, args)
    if service == "google_tasks":
        return _tasks(tool, args)
    return _contacts(tool, args)
