"""Round 33 (r33-models): the one model list every part of MemuAI shows.

Proxy's ``/api/admin/pg/state`` builds the rows (enabled models only — a model switched off in Proxy is never offered);
:func:`enrich` adds what a picker needs to every row, so the chat picker, Settings, Quick Access, modules, the
Telegram bot and the Mini App all describe a model the same way:

* ``name`` — readable name (:mod:`app.model_names`), ``badges`` (``free``…), ``date`` (snapshot), ``account``;
* ``kind`` — ``chat`` / ``image`` / ``video`` / ``music``;
* ``caps.vision_state`` — ``yes`` / ``no`` / ``unknown`` and ``caps.vision_src`` — why (see :func:`vision_state`).
  ``caps.vision`` keeps its old meaning (may the window attach a picture; unknown → ``true``).

MemuAI's ``GET /api/pg/model-catalog`` (``app/model_catalog_api.py``) serves these rows with the user's names,
pins and hidden models; ``pgstatic/model-picker.js`` (``window.MemuModelPicker``) draws them.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from . import model_names

PROVIDER_LABELS = {"genspark": "Genspark", "kiro": "Kiro", "antigravity": "Antigravity", "geminiweb": "Gemini Web",
                   "grok": "Grok", "local": "Local", "custom": "Custom API"}

VERIFIED_SOURCES = ("probe", "user")

def vision_state(public_id: str, provider: str, entry: Any = None) -> Dict[str, str]:
    """``{"state": yes|no|unknown, "src": probe|user|local|adapter|provider|catalog|family|name|none}``.

    One answer for every window. Order: a picture test the user ran → the user's own setting (Proxy model card) →
    a local model's projector → an adapter that cannot pass pictures → the provider's own model row → the catalogue →
    the model family (name). Nothing known → ``unknown`` (the window offers a one-request test).
    """
    try:
        from . import model_capabilities as mc
        from . import model_info
    except Exception:  # pragma: no cover - the catalogue is optional in tiny builds
        return {"state": "unknown", "src": "none"}
    probe = mc.probe_result(public_id)
    if probe and isinstance(probe.get("vision"), bool):
        return {"state": "yes" if probe["vision"] else "no", "src": "probe"}
    override = mc.get_override(public_id)
    if isinstance(override.get("vision"), bool):
        return {"state": "yes" if override["vision"] else "no", "src": "user"}
    model = str(getattr(entry, "model", "") or public_id)
    if provider == "local":
        local = mc._local_info(model)
        return {"state": "yes" if (local and (local.get("settings") or {}).get("mmproj")) else "no", "src": "local"}
    if provider not in mc.IMAGE_ADAPTERS:
        return {"state": "no", "src": "adapter"}
    try:
        summary = mc.capabilities_for(public_id, entry=entry)
    except Exception:
        summary = {}
    effective = (summary.get("effective") or {}).get("vision")
    if isinstance(effective, bool):
        return {"state": "yes" if effective else "no", "src": "provider"}
    if summary.get("vision") is False and summary.get("vision_confidence") in ("verified", "high"):
        return {"state": "no", "src": str(summary.get("vision_source") or "catalog")}
    hint = model_info.vision_hint(model)
    if hint is True:
        return {"state": "yes", "src": "family"}
    reference = summary.get("vision_ref")
    if reference is True:
        return {"state": "yes", "src": "catalog"}
    if hint is False:
        return {"state": "no", "src": "family"}
    if summary.get("vision") is False and summary.get("vision_confidence") == "medium":
        return {"state": "no", "src": str(summary.get("vision_source") or "catalog")}
    return {"state": "unknown", "src": "none"}

def _kind(row: Dict[str, Any]) -> str:
    from .model_capabilities import kind_for_row
    return kind_for_row(row)

def enrich(payload: Dict[str, Any], *, find_model=None) -> Dict[str, Any]:
    """Add names, kind, account and the vision answer to every ``/pg/state`` row (in place; returns ``payload``)."""
    rows = payload.get("models") or []
    image_ids = set(payload.get("image_models") or [])
    video_ids = set(payload.get("video_models") or [])
    music_ids = set(payload.get("music_models") or [])
    labels: Dict[str, str] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("provider_label") and row.get("id"):
            entry_prefix = str(row["id"]).split("-", 1)[0]
            labels.setdefault(entry_prefix, str(row["provider_label"]))
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        public_id = str(row["id"])
        entry = None
        if find_model is not None:
            try:
                entry = find_model(public_id)
            except Exception:
                entry = None
        prefix = str(getattr(entry, "prefix", "") or "")
        if not prefix and "/" in public_id and public_id.split("/", 1)[0] in ("genspark-image", "genspark-video"):
            prefix = public_id.split("/", 1)[0]
        parsed = model_names.parse(public_id, prefix=prefix)
        if row.get("composite"):

            row["name"] = str(row.get("display_name") or parsed["name"] or public_id)
            row.setdefault("account", str(row.get("provider_label") or "MemuAI Proxy"))
            row["kind"] = "chat"
            continue
        row["name"] = parsed["name"]
        if parsed["badges"]:
            row["badges"] = parsed["badges"]
        if parsed["date"]:
            row["date"] = parsed["date"]
        if prefix:
            row.setdefault("prefix", prefix)
        if entry is not None and getattr(entry, "model", None):
            row.setdefault("raw_model", str(entry.model))
        provider = str(row.get("provider") or "")
        account = row.get("provider_label") or (labels.get(prefix) if prefix else "") or \
            ("Genspark" if public_id.startswith("genspark-") else "") or PROVIDER_LABELS.get(provider) or provider or "—"
        if " · " in str(account) and prefix:
            account = labels.get(prefix) or prefix
        row.setdefault("account", str(account))
        kind = "video" if public_id in video_ids else "image" if public_id in image_ids else \
            "music" if public_id in music_ids and (row.get("caps") or {}).get("text") is not True else _kind(row)
        row["kind"] = kind
        caps = row.setdefault("caps", {})
        if kind != "chat":
            caps["vision_state"], caps["vision_src"] = ("no", "kind")
        else:
            found = vision_state(public_id, provider, entry)
            caps["vision_state"], caps["vision_src"] = found["state"], found["src"]

            if found["state"] == "no" and found["src"] in VERIFIED_SOURCES + ("provider", "local", "adapter"):
                caps["vision"] = False
            elif found["state"] == "yes":
                caps["vision"] = True
        if "free" in parsed["badges"]:
            caps["free"] = True
    return payload

def pick_rows(rows: Iterable[Dict[str, Any]], *, kind: str = "", need: str = "",
              hidden: Optional[Iterable[str]] = None, include_hidden: bool = False) -> List[Dict[str, Any]]:
    """Filter catalogue rows for one place: ``kind`` (chat/image/video/text = not a generator), ``need=vision``."""
    hidden_set = set(hidden or [])
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_kind = row.get("kind") or _kind(row)
        if kind == "text" and row_kind not in ("chat",):
            continue
        if kind and kind != "text" and row_kind != kind:
            continue
        if need == "vision" and (row.get("caps") or {}).get("vision_state") == "no":
            continue
        if not include_hidden and row.get("id") in hidden_set:
            continue
        out.append(row)
    return out

def label(row: Dict[str, Any], *, aliases: Optional[Dict[str, str]] = None, full: bool = True) -> str:
    """Plain-text label (Telegram, the CLI): ``Claude Opus 4.6 · aws9-claude-opus-4.6``."""
    public_id = str(row.get("id") or "")
    alias = (aliases or {}).get(public_id)
    name = alias.strip() if isinstance(alias, str) and alias.strip() else str(row.get("name") or
                                                                                model_names.pretty(public_id))
    return f"{name} · {public_id}" if full and name != public_id else name
