"""Round 32 (cu): grounding for Computer Use — how a vision model's answer becomes a real screen point.

Everything here is pure (no ctypes, no screen) and covered by ``tests/round32_cu_test.py``:

* **coordinate conventions** — models do not agree on what ``x, y`` means. Measured live (round 32): qwen3.8-max asked
  "where is the icon" without units answers in 0..1000 *relative* units (the Qwen3-VL/Gemini convention); told
  "pixels of this image" it answers pixels. The old prompt said "ruler pixels" but did not pin it down, so a Qwen
  answer of x=751 on a 1400-px screenshot clicked 30 % to the left of the target. Now every run declares one
  convention in the prompt (:func:`model_convention`), draws the ruler in those units and maps the answer back with
  :func:`model_to_shown` (with a sanity fallback: a "0..1000" value above 1000 can only be pixels).
* **action normalisation** — :func:`normalize_action` accepts the shapes real models produce (``left_click``,
  Anthropic ``coordinate``, Qwen ``point_2d``, Gemini ``box_2d`` [ymin, xmin, ymax, xmax], ``bbox``, ``"x": "512"``,
  ``{"name": "click", "arguments": {...}}``, ``press``/``hotkey``, ``launch``…) and returns one canonical dict.
* **marks** — UI Automation elements (or the mock's) are numbered and drawn on the screenshot (set-of-marks), so the
  model can answer ``{"action": "click", "element": 7}`` instead of guessing pixels.
* **SendInput absolute coordinates** — :func:`absolute_input_coords` (MOUSEEVENTF_ABSOLUTE | VIRTUALDESK, 0..65535
  over the whole virtual desktop, negative origins included).
* **error codes** — :func:`classify_error` turns a run's exception text into a code the UI explains in RU/EN.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable

PIXELS, NORM1000, NORM1, QWEN25 = "pixels", "norm1000", "norm1", "qwen25"
CONVENTIONS = (PIXELS, NORM1000, NORM1, QWEN25)

_NORM_FAMILIES = re.compile(r"(?i)(qwen|qwq|qvq|gemini|gemma|glm|cogagent|kimi|internvl|minicpm|seed|doubao|ui-tars|"
                            r"step-?\d|hunyuan|ernie|yi-vl|llava|pixtral|molmo)")

_QWEN25 = re.compile(r"(?i)qwen2(\.5)?-?vl")

_PIXEL_FAMILIES = re.compile(r"(?i)(claude|anthropic|gpt|openai|\bo[134]\b|o[134]-|chatgpt|computer-use|operator)")

def model_convention(model_id: str, override: str | None = None) -> str:
    """The coordinate convention we ask ``model_id`` to use. ``override`` (a preference) wins when valid."""
    if override and str(override).lower() in CONVENTIONS:
        return str(override).lower()
    name = str(model_id or "")
    if _QWEN25.search(name):
        return QWEN25
    if _PIXEL_FAMILIES.search(name) and not _NORM_FAMILIES.search(name):
        return PIXELS
    if _NORM_FAMILIES.search(name):
        return NORM1000
    return PIXELS

def smart_resize(height: int, width: int, factor: int = 28, min_pixels: int = 56 * 56,
                 max_pixels: int = 28 * 28 * 1280) -> tuple[int, int]:
    """Qwen2-VL / Qwen2.5-VL image resize (``qwen_vl_utils.smart_resize``): the size the model really saw."""
    height, width = max(1, int(height)), max(1, int(width))
    h_bar = max(factor, round(height / factor) * factor)
    w_bar = max(factor, round(width / factor) * factor)
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = max(factor, math.floor(height / beta / factor) * factor)
        w_bar = max(factor, math.floor(width / beta / factor) * factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = math.ceil(height * beta / factor) * factor
        w_bar = math.ceil(width * beta / factor) * factor
    return int(h_bar), int(w_bar)

def model_to_shown(x: float, y: float, convention: str, shown_w: int, shown_h: int) -> tuple[float, float]:
    """A model point in ``convention`` → pixels of the screenshot we showed (``shown_w`` × ``shown_h``).

    Sanity: in a relative convention a value that cannot be relative (> 1000, or > 1 for ``norm1``) means the model
    answered in pixels after all — taken as pixels of the shown image instead of being clamped to the far edge."""
    x, y = float(x), float(y)
    shown_w, shown_h = max(1, int(shown_w)), max(1, int(shown_h))
    if convention == NORM1000:
        if x > 1000.5 or y > 1000.5:
            return x, y
        return x / 1000.0 * shown_w, y / 1000.0 * shown_h
    if convention == NORM1:
        if x > 1.0001 or y > 1.0001:
            return model_to_shown(x, y, NORM1000, shown_w, shown_h)
        return x * shown_w, y * shown_h
    if convention == QWEN25:
        rh, rw = smart_resize(shown_h, shown_w)
        return x * shown_w / rw, y * shown_h / rh
    return x, y

def shown_to_model(x: float, y: float, convention: str, shown_w: int, shown_h: int) -> tuple[int, int]:
    """Inverse of :func:`model_to_shown` (element centres in the prompt are given in the model's own units)."""
    shown_w, shown_h = max(1, int(shown_w)), max(1, int(shown_h))
    if convention == NORM1000:
        return round(x / shown_w * 1000), round(y / shown_h * 1000)
    if convention == NORM1:
        return round(x / shown_w * 1000), round(y / shown_h * 1000)
    if convention == QWEN25:
        rh, rw = smart_resize(shown_h, shown_w)
        return round(x * rw / shown_w), round(y * rh / shown_h)
    return round(x), round(y)

def ruler_labels(convention: str, length: int) -> list[tuple[int, str]]:
    """Positions (shown pixels) and labels of the ruler lines along an axis of ``length`` shown pixels."""
    if convention in (NORM1000, NORM1):
        return [(round(v / 1000 * length), str(v)) for v in range(0, 1001, 100) if round(v / 1000 * length) < length]
    step = 200 if length > 600 else 100
    return [(v, str(v)) for v in range(0, length, step)]

def convention_text(convention: str, screens: list[dict[str, Any]]) -> str:
    """The sentence of the prompt that pins the units down (with the real numbers of this run)."""
    sizes = ", ".join(f"monitor {s.get('monitor')}: {s.get('width')}x{s.get('height')} px" for s in screens)
    if convention == NORM1000:
        return ("COORDINATES: x and y are RELATIVE, 0..1000 on both axes of the monitor screenshot: (0,0) is its top-left "
                "corner, (1000,1000) its bottom-right, (500,500) its centre — independent of the image size. The yellow "
                "ruler lines are labelled in these same units (every 100). Never answer in pixels.")
    if convention == QWEN25:
        return (f"COORDINATES: x and y are absolute pixels of the screenshot as you receive it ({sizes}); (0,0) is the "
                f"top-left corner.")
    return (f"COORDINATES: x and y are PIXELS of the monitor screenshot exactly as shown ({sizes}); (0,0) is its top-left "
            f"corner. The yellow ruler lines are labelled in these pixels. Never answer in 0..1000 units.")

_ACTION_ALIASES = {
    "left_click": "click", "leftclick": "click", "tap": "click", "press_button": "click", "click_element": "click",
    "mouse_click": "click", "single_click": "click", "click_xy": "click",
    "doubleclick": "double_click", "double-click": "double_click", "dblclick": "double_click", "left_double_click": "double_click",
    "rightclick": "right_click", "right-click": "right_click", "context_click": "right_click", "context_menu": "right_click",
    "tripleclick": "triple_click", "triple-click": "triple_click", "middleclick": "middle_click",
    "mouse_move": "move", "hover": "move", "move_mouse": "move", "moveto": "move", "move_to": "move",
    "left_click_drag": "drag", "drag_and_drop": "drag", "dragdrop": "drag",
    "type_text": "type", "input": "type", "write": "type", "input_text": "type", "typing": "type", "enter_text": "type",
    "fill": "type", "text": "type",
    "press": "key", "hotkey": "key", "keypress": "key", "key_press": "key", "press_key": "key", "keys": "key",
    "keyboard": "key", "shortcut": "key", "key_combo": "key",
    "launch": "open_app", "open": "open_app", "open_application": "open_app", "start_app": "open_app",
    "launch_app": "open_app", "run_app": "open_app", "open_program": "open_app", "start": "open_app", "run": "open_app",
    "launch_application": "open_app", "openapp": "open_app",
    "finish": "done", "complete": "done", "completed": "done", "answer": "done", "terminate": "done", "finished": "done",
    "task_complete": "done", "stop": "done", "success": "done",
    "fail": "stuck", "failed": "stuck", "give_up": "stuck", "call_user": "stuck",
    "sleep": "wait", "pause": "wait", "delay": "wait",
    "scroll_down": "scroll", "scroll_up": "scroll", "wheel": "scroll",
    "focus": "focus_window", "activate_window": "focus_window", "switch_window": "focus_window",
    "close": "close_window", "minimize": "minimize_window", "maximize": "maximize_window",
}
_NUM = r"-?\d+(?:\.\d+)?"

def _num(value: float) -> float | int:
    return int(value) if float(value).is_integer() else float(value)

def _numbers(value: Any) -> list[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [_num(value)]
    if isinstance(value, dict):
        out = []
        for key in ("x", "y", "x1", "y1", "x2", "y2"):
            if value.get(key) is not None:
                out += _numbers(value[key])
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out += _numbers(item)
        return out
    if isinstance(value, str):
        return [_num(float(v)) for v in re.findall(_NUM, value)]
    return []

def _int_like(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        match = re.fullmatch(r"\s*\[?#?\s*(\d{1,4})\s*\]?\s*", value)
        if match:
            return int(match.group(1))
    return None

def normalize_action(raw: Any) -> dict[str, Any]:
    """One canonical action dict from whatever shape the model used. Never raises; unknown shapes pass through."""
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        raw = raw[0]
    if not isinstance(raw, dict):
        return {"action": ""}
    action = dict(raw)

    for wrapper in ("function", "tool_call", "call"):
        if isinstance(action.get(wrapper), dict):
            action = {**action, **action.pop(wrapper)}
    if isinstance(action.get("action"), dict):
        inner = action.pop("action")
        action = {**action, **inner}
    for key in ("arguments", "args", "parameters", "params", "input"):
        value = action.get(key)
        if isinstance(value, str) and value.strip().startswith("{"):
            try:
                value = json.loads(value)
            except ValueError:
                value = None
        if isinstance(value, dict):
            action.pop(key, None)
            action = {**value, **{k: v for k, v in action.items() if k not in value}}
    kind = action.get("action")
    if not isinstance(kind, str) or not kind.strip():
        for key in ("name", "tool", "command", "op", "type", "action_type"):
            value = action.get(key)
            if isinstance(value, str) and value.strip() and (key != "type" or value.lower() in _ACTION_ALIASES
                                                              or value.lower() in _CANONICAL):
                kind = value
                break
    kind = re.sub(r"[\s]+", "_", str(kind or "").strip().lower())
    if kind.startswith("computer.") or kind.startswith("computer_"):
        kind = kind.split(".", 1)[-1].replace("computer_", "", 1)
    original = kind
    kind = _ACTION_ALIASES.get(kind, kind)
    action["action"] = kind

    if kind == "scroll":
        if action.get("dy") is None:
            direction = str(action.get("direction") or action.get("scroll_direction") or
                            ("down" if original == "scroll_down" else "up" if original == "scroll_up" else "")).lower()
            amount = _numbers(action.get("amount") if action.get("amount") is not None else action.get("scroll_amount"))
            pixels = (amount[0] * 120 if amount and amount[0] < 40 else amount[0]) if amount else 600
            if direction in ("down", "up"):
                action["dy"] = abs(pixels) if direction == "down" else -abs(pixels)

    if kind == "key":
        keys = action.get("keys") or action.get("key") or action.get("combo") or action.get("text") or action.get("hotkey")
        if isinstance(keys, (list, tuple)):
            keys = "+".join(str(k) for k in keys)
        if keys is not None:
            action["keys"] = _normalize_keys(str(keys))
    if kind == "type":
        text = action.get("text")
        if text is None:
            for key in ("content", "value", "string", "input_text", "input", "keys"):
                if isinstance(action.get(key), str):
                    text = action[key]
                    break
        if text is not None:
            action["text"] = str(text)
    if kind == "open_app":
        name = action.get("name") if kind != original or action.get("app") is None else None
        for key in ("app", "application", "app_name", "program", "target", "name", "text", "query"):
            value = action.get(key)
            if isinstance(value, str) and value.strip() and value.strip().lower() not in _ACTION_ALIASES \
                    and value.strip().lower() != "open_app":
                name = value
                break
        if name:
            action["app"] = str(name).strip()[:120]
    if kind == "done":
        if not action.get("report"):
            for key in ("text", "answer", "result", "message", "summary", "content"):
                if isinstance(action.get(key), str) and action[key].strip():
                    action["report"] = action[key]
                    break
    if kind in ("close_window", "focus_window", "minimize_window", "maximize_window") and not action.get("title"):
        for key in ("window", "name", "app", "target"):
            if isinstance(action.get(key), str) and action[key].strip():
                action["title"] = action[key]
                break

    if action.get("element") is None:
        for key in ("element_id", "elementId", "mark", "mark_id", "id", "index", "target_id", "som", "label_id"):
            value = _int_like(action.get(key))
            if value is not None:
                action["element"] = value
                break
    else:
        value = _int_like(action.get("element"))
        if value is not None:
            action["element"] = value
        elif isinstance(action.get("element"), str) and kind in _POINTER and action.get("x") is None:
            action["element_name"] = action.pop("element")

    if "element" in action and (not isinstance(action.get("element"), int) or isinstance(action["element"], bool)
                                or action["element"] <= 0):
        action.pop("element", None)

    _normalize_point(action)
    return action

_CANONICAL = {"click", "double_click", "right_click", "triple_click", "middle_click", "move", "drag", "type", "key",
              "scroll", "wait", "done", "stuck", "open_app", "close_window", "focus_window", "minimize_window",
              "maximize_window"}
_POINTER = {"click", "double_click", "right_click", "triple_click", "middle_click", "move", "scroll", "type"}
_KEY_ALIASES = {"control": "ctrl", "ctl": "ctrl", "return": "enter", "windows": "win", "super": "win", "meta": "win",
                "cmd": "win", "command": "win", "lwin": "win", "del": "delete", "pgup": "pageup", "pgdn": "pagedown",
                "page_up": "pageup", "page_down": "pagedown", "arrowup": "up", "arrowdown": "down", "arrowleft": "left",
                "arrowright": "right", "escape": "esc", "bksp": "backspace", "back": "backspace", "spacebar": "space",
                "option": "alt", "plus": "plus"}

def _normalize_keys(keys: str) -> str:
    text = str(keys).strip()
    if not text:
        return ""

    text = re.sub(r"(^|\+)\s*\+\s*(?=$|\+)", r"\1plus", text)

    text = re.sub(r"(?i)\b(page|print|caps)[-_ ]?(up|down|screen|lock)\b", lambda m: (m.group(1) + m.group(2)).lower(), text)

    combo = "+" in text or " " in text or re.search(r"(?i)^(ctrl|control|alt|shift|win|cmd|super|meta)-[a-z0-9]", text)
    parts = re.split(r"\s*(?:\+|-(?=[a-z0-9])|\s)\s*", text.lower()) if combo else [text.lower()]
    return "+".join(_KEY_ALIASES.get(part.strip(), part.strip()) for part in parts if part.strip())

def _normalize_point(action: dict[str, Any]) -> None:
    """Fill ``x``/``y`` (and ``x1..y2`` for drag) from any coordinate spelling; marks Gemini boxes as 0..1000."""
    kind = action.get("action")
    if kind == "drag":
        start = action.get("start_coordinate") or action.get("start") or action.get("from")
        end = action.get("end_coordinate") or action.get("end") or action.get("to") or action.get("coordinate")
        if action.get("x1") is None and start is not None and end is not None:
            a, b = _numbers(start), _numbers(end)
            if len(a) >= 2 and len(b) >= 2:
                action.update(x1=a[0], y1=a[1], x2=b[0], y2=b[1])
        for key in ("x1", "y1", "x2", "y2"):
            if isinstance(action.get(key), str):
                values = _numbers(action[key])
                action[key] = values[0] if values else action[key]
        return
    if action.get("x") is not None and action.get("y") is not None:
        x, y = _numbers(action["x"]), _numbers(action["y"])
        if x and y:
            action["x"], action["y"] = x[0], y[0]
        return
    if action.get("x") is not None and action.get("y") is None:
        values = _numbers(action["x"])
        if len(values) >= 2:
            action["x"], action["y"] = values[0], values[1]
            return
    box = action.get("box_2d")
    if box is not None:
        values = _numbers(box)
        if len(values) >= 4:
            action["x"], action["y"] = (values[1] + values[3]) / 2, (values[0] + values[2]) / 2
            action["_convention"] = NORM1000
            return
    for key in ("bbox", "box", "bounding_box", "rect"):
        values = _numbers(action.get(key)) if action.get(key) is not None else []
        if len(values) >= 4:
            action["x"], action["y"] = (values[0] + values[2]) / 2, (values[1] + values[3]) / 2
            return
    for key in ("coordinate", "coordinates", "point", "position", "point_2d", "pos", "location", "xy", "center",
                "click_point", "target_point"):
        values = _numbers(action.get(key)) if action.get(key) is not None else []
        if len(values) >= 2:
            action["x"], action["y"] = values[0], values[1]
            if key == "point_2d":
                action["_convention"] = NORM1000
            return
        if len(values) >= 4:
            action["x"], action["y"] = (values[0] + values[2]) / 2, (values[1] + values[3]) / 2
            return

INTERACTIVE_TYPES = {"button", "edit", "combobox", "checkbox", "radiobutton", "hyperlink", "listitem", "menuitem",
                     "tabitem", "treeitem", "document", "splitbutton", "dataitem", "slider", "spinner", "menubaritem",
                     "appbarbutton", "toggle", "togglebutton", "text_input", "searchbox", "icon"}
MAX_MARKS = 90

def pick_marks(elements: Iterable[dict[str, Any]], monitors: list[dict[str, Any]], limit: int = MAX_MARKS) -> list[dict[str, Any]]:
    """Number the elements worth clicking. ``elements``: ``{"name", "type", "rect": [l, t, r, b] (physical, global),
    "window"?}``; ``monitors``: the capture's screens (``left/top/real_width/real_height``). Drops invisible, tiny,
    huge (a whole window pane) and unnamed non-edit elements, de-duplicates and keeps foreground ones first."""
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for element in elements or []:
        try:
            left, top, right, bottom = (float(v) for v in element.get("rect") or ())
        except (TypeError, ValueError):
            continue
        width, height = right - left, bottom - top
        kind = str(element.get("type") or "").lower().replace("controltype.", "")
        name = re.sub(r"\s+", " ", str(element.get("name") or "")).strip()
        if width < 4 or height < 4:
            continue
        if not name and kind not in ("edit", "document", "combobox", "searchbox"):
            continue
        cx, cy = left + width / 2, top + height / 2
        monitor = None
        for screen in monitors:
            sl, st = float(screen.get("left") or 0), float(screen.get("top") or 0)
            sw = float(screen.get("real_width") or screen.get("width") or 0)
            sh = float(screen.get("real_height") or screen.get("height") or 0)
            if sl <= cx < sl + sw and st <= cy < st + sh:
                monitor = screen
                break
        if monitor is None:
            continue
        mw = float(monitor.get("real_width") or monitor.get("width") or 1)
        mh = float(monitor.get("real_height") or monitor.get("height") or 1)
        if width * height > 0.6 * mw * mh and kind not in ("document", "edit"):
            continue
        key = (round(cx / 6), round(cy / 6), name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name[:60], "type": kind or "element", "rect": [round(left), round(top), round(right), round(bottom)],
                    "center": [round(cx), round(cy)], "monitor": int(monitor.get("monitor") or 1),
                    "window": str(element.get("window") or "")[:60]})
        if len(out) >= limit:
            break
    for index, mark in enumerate(out, 1):
        mark["id"] = index
    return out

def mark_to_shown(mark: dict[str, Any], screen: dict[str, Any]) -> list[float]:
    """The mark's rectangle in the shown screenshot's pixels."""
    sx = float(screen.get("scale_x") or screen.get("scale") or 1.0) or 1.0
    sy = float(screen.get("scale_y") or screen.get("scale") or 1.0) or 1.0
    left, top = float(screen.get("left") or 0), float(screen.get("top") or 0)
    l, t, r, b = mark["rect"]
    return [(l - left) * sx, (t - top) * sy, (r - left) * sx, (b - top) * sy]

def draw_marks(image: Any, marks: list[dict[str, Any]], screen: dict[str, Any]) -> Any:
    """Thin outlines and small numbered tags (set-of-marks) on the shown screenshot of ``screen``."""
    if not marks:
        return image
    from PIL import ImageDraw
    canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    width, height = canvas.size
    for mark in marks:
        if int(mark.get("monitor") or 1) != int(screen.get("monitor") or 1):
            continue
        l, t, r, b = mark_to_shown(mark, screen)
        draw.rectangle([l, t, r, b], outline=(255, 0, 170), width=1)
        label = str(mark["id"])
        tw = 7 * len(label) + 5
        tx = max(0, min(width - tw, l))
        ty = t - 13 if t >= 13 else min(height - 13, b)
        draw.rectangle([tx, ty, tx + tw, ty + 12], fill=(255, 0, 170))
        draw.text((tx + 3, ty), label, fill=(255, 255, 255))
    return canvas

def marks_prompt(marks: list[dict[str, Any]], screens: list[dict[str, Any]], convention: str) -> list[dict[str, Any]]:
    """The <elements> list for the prompt: id, name, type, monitor and the centre in the model's units."""
    rows = []
    by_monitor = {int(s.get("monitor") or 1): s for s in screens}
    for mark in marks:
        screen = by_monitor.get(int(mark.get("monitor") or 1))
        if not screen:
            continue
        l, t, r, b = mark_to_shown(mark, screen)
        cx, cy = shown_to_model((l + r) / 2, (t + b) / 2, convention, screen.get("width") or 1, screen.get("height") or 1)
        row = {"id": mark["id"], "name": mark["name"], "type": mark["type"], "monitor": mark["monitor"], "at": [cx, cy]}
        if mark.get("window"):
            row["window"] = mark["window"]
        rows.append(row)
    return rows

def absolute_input_coords(x: int, y: int, v_left: int, v_top: int, v_width: int, v_height: int) -> tuple[int, int]:
    """MOUSEINPUT dx/dy for MOUSEEVENTF_MOVE|ABSOLUTE|VIRTUALDESK: 0..65535 across the virtual desktop
    (``SM_XVIRTUALSCREEN``… in physical pixels). Exact inverse of Windows' ``dx * width / 65536`` rounding."""
    v_width, v_height = max(1, int(v_width)), max(1, int(v_height))
    rel_x = min(max(int(x) - int(v_left), 0), v_width - 1)
    rel_y = min(max(int(y) - int(v_top), 0), v_height - 1)

    return (min(65535, int((rel_x + 0.5) * 65536 / v_width)), min(65535, int((rel_y + 0.5) * 65536 / v_height)))

def absolute_to_pixel(dx: int, dy: int, v_left: int, v_top: int, v_width: int, v_height: int) -> tuple[int, int]:
    """What Windows does with absolute dx/dy (used by the tests to prove the round trip)."""
    return int(v_left + dx * v_width // 65536), int(v_top + dy * v_height // 65536)

_ERROR_PATTERNS = (
    ("not_windows", r"только в Windows|only on Windows"),
    ("rate_limit", r"\b429\b|rate.?limit|too many requests|quota|лимит запросов"),
    ("auth", r"\b401\b|\b403\b|unauthori[sz]ed|forbidden|invalid api key|permissiondenied"),
    ("vision", r"image|vision|изображени|картинк|не видит|multimodal"),
    ("input_blocked", r"не принял ввод|did not accept the input|SendInput|заблокировал перемещение"),
    ("json", r"JSON"),
    ("stuck", r"зациклился|is stuck|stuck"),
    ("timeout", r"timeout|timed out|тайм-?аут|превышено время"),
    ("network", r"connect|network|соединени|unreachable|ECONN|getaddrinfo"),
    ("empty", r"пуст|empty"),
)

def classify_error(text: str) -> str:
    value = str(text or "")
    for code, pattern in _ERROR_PATTERNS:
        if re.search(pattern, value, re.I):
            return code
    return "other" if value else ""
