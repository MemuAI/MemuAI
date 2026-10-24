"""Round 33: more of Google Calendar for the assistant — guests, repeats, reminders, Google Meet links, quick add,
accept / decline, free/busy, moving events between calendars and managing calendars themselves.

:mod:`app.integrations` keeps the original five tools and calls :func:`extra_event_fields`, :func:`write_params`,
:func:`event_row` for them; every other ``calendar_*`` name comes to :func:`run`.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List
from urllib.parse import quote

G = "https://www.googleapis.com/auth/"

TOOLS: List[str] = [
    "calendar_get_event{event_id,calendar_id}",
    "calendar_quick_add{text,calendar_id}",
    "calendar_respond{event_id,response,calendar_id}",
    "calendar_move_event{event_id,calendar_id,destination}",
    "calendar_freebusy{start,end,calendars}",
    "calendar_create_calendar{summary,description,timezone}",
    "calendar_delete_calendar{calendar_id}",
]
WRITE = {"calendar_quick_add", "calendar_respond", "calendar_move_event", "calendar_create_calendar", "calendar_delete_calendar"}
#: Deleting a whole calendar deletes every event in it — asked every time.
ALWAYS = {"calendar_delete_calendar"}
DANGER = {"calendar_delete_calendar"}
SCOPES: Dict[str, set] = {
    "calendar_create_calendar": {G + "calendar"},
    "calendar_delete_calendar": {G + "calendar"},
    "calendar_freebusy": {G + "calendar", G + "calendar.readonly", G + "calendar.freebusy", G + "calendar.events.freebusy"},
}

HINT = ("Google Calendar: times are ISO-8601 (2026-10-16T13:00) in the user's local time unless timezone is given; "
        "all-day events use dates (2026-10-16). Guests: attendees=['a@b.c'] and send_updates='all' to email them; "
        "meet=true adds a Google Meet link; recurrence='RRULE:FREQ=WEEKLY;BYDAY=MO'; reminders=[10,60] minutes. "
        "List a past or future range with time_min/time_max. calendar_respond{response:accepted|declined|tentative}.")


def _ig():
    from . import integrations
    return integrations


def _cal(method: str, path: str, **kwargs: Any) -> Any:
    ig = _ig()
    return ig._google("google_calendar", method, ig.CALENDAR_API + path, **kwargs)


def _arg(text: str):
    return _ig().IntegrationArgumentError(text)


def rfc3339(value: str, end: bool = False) -> str:
    """«2026-10-16» / «2026-10-16T13:00» / with an offset → RFC 3339 with an offset (local time if none)."""
    text = str(value or "").strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            moment = datetime.combine(day + timedelta(days=1) if end else day, datetime.min.time())
        else:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _arg("Неверная дата или время: %s (ISO-8601, например 2026-10-16 или 2026-10-16T13:00)" % text) from exc
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.isoformat(timespec="seconds")


def _emails(value: Any) -> List[str]:
    items = value if isinstance(value, (list, tuple)) else re.split(r"[,;\s]+", str(value or ""))
    out = []
    for item in items:
        email = str(item.get("email") if isinstance(item, dict) else item).strip().strip("<>")
        if not email:
            continue
        if not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", email):
            raise _arg("Неверный адрес гостя: %s" % email)
        out.append(email)
    return out


def extra_event_fields(args: Dict[str, Any]) -> Dict[str, Any]:
    """Guests, repeats, reminders, a Meet link, colour, visibility — only what the model passed."""
    body: Dict[str, Any] = {}
    if args.get("attendees") not in (None, ""):
        body["attendees"] = [{"email": email} for email in _emails(args.get("attendees"))]
    recurrence = args.get("recurrence")
    if recurrence not in (None, "", []):
        rules = recurrence if isinstance(recurrence, list) else [recurrence]
        clean = []
        for rule in rules:
            text = str(rule).strip()
            if text and not text.upper().startswith(("RRULE:", "EXRULE:", "RDATE", "EXDATE")):
                text = "RRULE:" + text
            if text:
                clean.append(text)
        body["recurrence"] = clean
    reminders = args.get("reminders")
    if reminders not in (None, ""):
        items = reminders if isinstance(reminders, list) else re.split(r"[,;\s]+", str(reminders))
        minutes = []
        for item in items:
            value = item.get("minutes") if isinstance(item, dict) else item
            try:
                minutes.append(max(0, min(40320, int(float(str(value))))))
            except (TypeError, ValueError):
                continue
        body["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": m} for m in minutes[:5]]} \
            if minutes else {"useDefault": True}
    if str(args.get("meet") or "").lower() in ("1", "true", "yes") or args.get("meet") is True:
        body["conferenceData"] = {"createRequest": {"requestId": uuid.uuid4().hex,
                                                    "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
    if args.get("color_id"):
        body["colorId"] = str(args["color_id"])
    if str(args.get("visibility") or "") in ("default", "public", "private", "confidential"):
        body["visibility"] = str(args["visibility"])
    return body


def write_params(args: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if body.get("conferenceData"):
        params["conferenceDataVersion"] = 1
    updates = str(args.get("send_updates") or "").strip().lower()
    if updates in ("all", "externalonly", "none"):
        params["sendUpdates"] = "externalOnly" if updates == "externalonly" else updates
    elif body.get("attendees"):
        params["sendUpdates"] = "all"   # guests were added on purpose: they should get the invitation
    return params or None  # type: ignore[return-value]


def created_extras(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if row.get("hangoutLink"):
        out["meet"] = row["hangoutLink"]
    if row.get("attendees"):
        out["attendees"] = [a.get("email") for a in row["attendees"] if isinstance(a, dict)]
    return out


def event_row(row: Dict[str, Any]) -> Dict[str, Any]:
    start = row.get("start") or {}
    end = row.get("end") or {}
    out = {"id": row.get("id"), "summary": row.get("summary"), "start": start.get("dateTime") or start.get("date"),
           "end": end.get("dateTime") or end.get("date"), "location": row.get("location")}
    if row.get("attendees"):
        out["attendees"] = [{"email": a.get("email"), "response": a.get("responseStatus"), **({"me": True} if a.get("self") else {})}
                            for a in row["attendees"] if isinstance(a, dict)][:30]
    if row.get("hangoutLink"):
        out["meet"] = row["hangoutLink"]
    if row.get("recurringEventId"):
        out["recurring"] = True
    organizer = row.get("organizer") or {}
    if organizer.get("email") and not organizer.get("self"):
        out["organizer"] = organizer["email"]
    return {**{k: v for k, v in out.items() if v not in (None, "", [])}, "id": row.get("id")}


def _calendar(args: Dict[str, Any], key: str = "calendar_id") -> str:
    if key == "calendar_id":
        return _ig()._calendar_id(args)
    value = str(args.get(key) or "").strip()
    if not value:
        raise _arg("Укажите %s (его даёт calendar_list_calendars)" % key)
    return quote(value, safe="")


def run(tool: str, args: Dict[str, Any]) -> Any:
    if tool == "calendar_get_event":
        event_id = str(args.get("event_id") or "").strip()
        if not event_id:
            raise _arg("Укажите event_id (его даёт calendar_list_events)")
        row = _cal("GET", "/calendars/%s/events/%s" % (_calendar(args), quote(event_id, safe="")))
        return {**event_row(row), "description": row.get("description"), "link": row.get("htmlLink"),
                "recurrence": row.get("recurrence"), "status": row.get("status")}
    if tool == "calendar_quick_add":
        text = str(args.get("text") or "").strip()
        if not text:
            raise _arg("Укажите text, например «Обед с Борисом завтра в 13:00» (Google лучше понимает английский)")
        row = _cal("POST", "/calendars/%s/events/quickAdd" % _calendar(args), params={"text": text})
        return {"created": True, **event_row(row), "link": row.get("htmlLink")}
    if tool == "calendar_respond":
        event_id = str(args.get("event_id") or "").strip()
        response = str(args.get("response") or "").strip().lower()
        response = {"accept": "accepted", "yes": "accepted", "decline": "declined", "no": "declined",
                    "maybe": "tentative"}.get(response, response)
        if not event_id or response not in ("accepted", "declined", "tentative"):
            raise _arg("Укажите event_id и response: accepted, declined или tentative")
        cal = _calendar(args)
        row = _cal("GET", "/calendars/%s/events/%s" % (cal, quote(event_id, safe="")))
        attendees = row.get("attendees") or []
        mine = [a for a in attendees if isinstance(a, dict) and a.get("self")]
        if not mine:
            raise _arg("Вы не приглашены на это событие как гость — отвечать не на что")
        for person in mine:
            person["responseStatus"] = response
        updated = _cal("PATCH", "/calendars/%s/events/%s" % (cal, quote(event_id, safe="")),
                       json_body={"attendees": attendees}, params={"sendUpdates": "all"})
        return {"id": updated.get("id"), "response": response, "summary": updated.get("summary")}
    if tool == "calendar_move_event":
        event_id = str(args.get("event_id") or "").strip()
        if not event_id:
            raise _arg("Укажите event_id")
        destination = str(args.get("destination") or "").strip()
        if not destination:
            raise _arg("Укажите destination — id календаря назначения")
        row = _cal("POST", "/calendars/%s/events/%s/move" % (_calendar(args), quote(event_id, safe="")),
                   params={"destination": destination})
        return {"moved": True, "id": row.get("id"), "to": destination}
    if tool == "calendar_freebusy":
        start, end = str(args.get("start") or "").strip(), str(args.get("end") or "").strip()
        if not start:
            start = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not end:
            end = (datetime.fromisoformat(rfc3339(start)) + timedelta(days=7)).isoformat(timespec="seconds")
        calendars = args.get("calendars") or ["primary"]
        items = calendars if isinstance(calendars, list) else re.split(r"[,\s]+", str(calendars))
        body = {"timeMin": rfc3339(start), "timeMax": rfc3339(end, end=len(end) == 10),
                "items": [{"id": str(c).strip()} for c in items if str(c).strip()][:20]}
        row = _cal("POST", "/freeBusy", json_body=body)
        return {"from": body["timeMin"], "to": body["timeMax"],
                "busy": {cid: (data or {}).get("busy") or [] for cid, data in (row.get("calendars") or {}).items()}}
    if tool == "calendar_create_calendar":
        summary = str(args.get("summary") or "").strip()
        if not summary:
            raise _arg("Укажите summary — название календаря")
        body = {"summary": summary, "description": str(args.get("description") or "")}
        if args.get("timezone"):
            body["timeZone"] = str(args["timezone"])
        row = _cal("POST", "/calendars", json_body=body)
        return {"created": True, "calendar_id": row.get("id"), "summary": row.get("summary")}
    if tool == "calendar_delete_calendar":
        cal = str(args.get("calendar_id") or "").strip()
        if not cal or cal == "primary" or ("@gmail.com" in cal and "group.calendar" not in cal):
            raise _arg("Укажите calendar_id дополнительного календаря — основной календарь удалить нельзя")
        _cal("DELETE", "/calendars/" + quote(cal, safe=""))
        return {"deleted": True, "calendar_id": cal}
    raise _arg("Неизвестный инструмент Google Calendar: %s" % tool)
