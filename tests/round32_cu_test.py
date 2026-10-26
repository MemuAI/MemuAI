"""Round 32 (cu): Computer Use — grounding, coordinate mapping, the loop on a fake desktop, focus/own windows, UI.

No real clicks: the OS layer is replaced by a small fake desktop (physical pixels, scaled monitors, a negative-origin
second monitor, a topmost "Quick Access" window that is excluded from screenshots but swallows clicks).
Run: python -m pytest -q tests/round32_cu_test.py
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r32cu-"))

from PIL import Image, ImageDraw

from app import automation_runtime as ar
from app import cu_grounding as g

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"

_real_sleep = asyncio.sleep

async def _short_sleep(delay=0, *a, **kw):
    await _real_sleep(min(float(delay or 0), 0.005))

class Conventions(unittest.TestCase):
    def test_model_families(self):
        self.assertEqual(g.model_convention("xkiro-qwen/qwen3.8-max:free"), g.NORM1000)
        self.assertEqual(g.model_convention("qwen/qwen3-vl-plus:free"), g.NORM1000)
        self.assertEqual(g.model_convention("google/gemini-3-pro"), g.NORM1000)
        self.assertEqual(g.model_convention("Qwen/Qwen2.5-VL-72B-Instruct"), g.QWEN25)
        self.assertEqual(g.model_convention("claude-sonnet-5"), g.PIXELS)
        self.assertEqual(g.model_convention("sol-gpt-6.1-sol"), g.PIXELS)
        self.assertEqual(g.model_convention("some-unknown-vision"), g.PIXELS)
        self.assertEqual(g.model_convention("xkiro-qwen/qwen3.8-max:free", "pixels"), g.PIXELS, "a preference wins")
        self.assertEqual(g.model_convention("x", "bogus"), g.PIXELS)

    def test_model_to_shown(self):
        self.assertEqual(g.model_to_shown(500, 500, g.NORM1000, 1400, 788), (700.0, 394.0))
        self.assertEqual(g.model_to_shown(700, 394, g.PIXELS, 1400, 788), (700.0, 394.0))
        self.assertEqual(g.model_to_shown(.5, .25, g.NORM1, 1000, 800), (500.0, 200.0))

        self.assertEqual(g.model_to_shown(1050, 260, g.NORM1000, 1400, 788), (1050.0, 260.0))

        rh, rw = g.smart_resize(788, 1400)
        self.assertEqual((rh % 28, rw % 28), (0, 0))
        x, y = g.model_to_shown(rw / 2, rh / 2, g.QWEN25, 1400, 788)
        self.assertAlmostEqual(x, 700, delta=1); self.assertAlmostEqual(y, 394, delta=1)

    def test_round_trip_shown_model(self):
        for conv in (g.PIXELS, g.NORM1000, g.QWEN25):
            mx, my = g.shown_to_model(333, 222, conv, 1400, 788)
            sx, sy = g.model_to_shown(mx, my, conv, 1400, 788)
            self.assertAlmostEqual(sx, 333, delta=2, msg=conv); self.assertAlmostEqual(sy, 222, delta=2, msg=conv)

    def test_prompt_declares_the_units(self):
        norm = ar.computer_system_prompt("norm1000", [{"monitor": 1, "width": 1400, "height": 788}])
        self.assertIn("0..1000", norm); self.assertIn("Never answer in pixels", norm)
        px = ar.computer_system_prompt("pixels", [{"monitor": 1, "width": 1400, "height": 788}])
        self.assertIn("1400x788", px); self.assertIn("Never answer in 0..1000", px)
        self.assertIn('"action":"open_app"', px); self.assertIn('"element":12', px)

    def test_ruler_in_norm_units(self):
        labels = g.ruler_labels(g.NORM1000, 1400)
        self.assertEqual(labels[1], (140, "100")); self.assertEqual(len(labels), 10)
        image = ar.ComputerAutomation._draw_ruler(Image.new("RGB", (1400, 788)), convention="norm1000")
        self.assertNotEqual(image.getpixel((140, 300)), image.getpixel((160, 300)), "a line at 100/1000")

def _desktop(monitors, targets):
    """A virtual-desktop image with small bright squares at the given physical global points."""
    vx = min(m["left"] for m in monitors); vy = min(m["top"] for m in monitors)
    vw = max(m["right"] for m in monitors) - vx; vh = max(m["bottom"] for m in monitors) - vy
    image = Image.new("RGB", (vw, vh), (30, 30, 30))
    draw = ImageDraw.Draw(image)
    for (x, y) in targets:
        draw.rectangle([x - vx - 6, y - vy - 6, x - vx + 6, y - vy + 6], fill=(255, 255, 255))
    return image

class MappingAcrossScaling(unittest.TestCase):
    """A model answer at the target in the shown screenshot must land on the target's PHYSICAL pixel for every
    Windows scaling (the physical resolution grows with the scale), every convention, and a negative-origin monitor."""

    CASES = {"100%": (1920, 1080), "125%": (2400, 1350), "150%": (2560, 1440), "175%": (3360, 1890), "200%": (3840, 2160)}

    def test_every_scale_and_convention(self):
        for label, (w, h) in self.CASES.items():
            monitors = [{"left": 0, "top": 0, "right": w, "bottom": h}, {"left": -1920, "top": 180, "right": 0, "bottom": 1260}]
            target = (int(w * .73), int(h * .41))
            second = (-1920 + 1500, 180 + 300)
            for conv in (g.PIXELS, g.NORM1000, g.QWEN25):
                cap = ar.ComputerAutomation._compose_capture(monitors, _desktop(monitors, [target, second]), conv)
                s1, s2 = cap["screens"]
                self.assertLessEqual(max(s1["width"], s1["height"]), ar.MAX_SCREENSHOT_DIM)
                for screen, point, mon in ((s1, target, 1), (s2, second, 2)):
                    shown = ((point[0] - screen["left"]) * screen["scale_x"], (point[1] - screen["top"]) * screen["scale_y"])
                    mx, my = g.shown_to_model(shown[0], shown[1], conv, screen["width"], screen["height"])
                    gx, gy = ar.resolve_click_point({"monitor": mon, "x": mx, "y": my}, cap["screens"], conv)

                    tolerance = 2.5 / screen["scale_x"] + (screen["real_width"] / 1000 if conv == g.NORM1000 else 0)
                    self.assertLessEqual(abs(gx - point[0]), tolerance, (label, conv, mon, gx, point))
                    self.assertLessEqual(abs(gy - point[1]), tolerance, (label, conv, mon, gy, point))

    def test_the_old_bug_qwen_norm_answer_read_as_pixels(self):
        """Proof of the reported «it clicked off»: a 0..1000 answer read as pixels lands 30 % left/up."""
        screens = [{"monitor": 1, "left": 0, "top": 0, "width": 1400, "height": 788, "real_width": 2560, "real_height": 1440,
                    "scale_x": 1400 / 2560, "scale_y": 788 / 1440}]
        right = ar.resolve_click_point({"x": 750, "y": 330}, screens, g.NORM1000)
        wrong = ar.resolve_click_point({"x": 750, "y": 330}, screens, g.PIXELS)
        self.assertAlmostEqual(right[0], 1920, delta=3)
        self.assertGreater(right[0] - wrong[0], 500)

    def test_box_2d_and_point_2d_carry_their_convention(self):
        screens = [{"monitor": 1, "left": 0, "top": 0, "width": 1000, "height": 500, "real_width": 2000, "real_height": 1000,
                    "scale_x": .5, "scale_y": .5}]
        gem = g.normalize_action({"action": "click", "box_2d": [400, 200, 600, 400]})
        self.assertEqual(ar.resolve_click_point(gem, screens, g.PIXELS), (600, 500))
        qwen = g.normalize_action({"action": "click", "point_2d": [250, 500]})
        self.assertEqual(ar.resolve_click_point(qwen, screens, g.PIXELS), (500, 500))

class AbsoluteInput(unittest.TestCase):
    def test_round_trip_with_negative_origin(self):
        v = (-1920, -200, 1920 + 3840, 2160 + 200)
        for x, y in ((-1920, -200), (0, 0), (2559, 1439), (3839, 2159), (-1, 5), (1234, 987)):
            dx, dy = g.absolute_input_coords(x, y, *v)
            self.assertTrue(0 <= dx <= 65535 and 0 <= dy <= 65535)
            self.assertEqual(g.absolute_to_pixel(dx, dy, *v), (x, y))

    def test_clamps_outside(self):
        self.assertEqual(g.absolute_input_coords(-99999, 99999, 0, 0, 1920, 1080), (17, 65505))

class ParserRobustness(unittest.TestCase):
    def test_shapes(self):
        n = g.normalize_action
        self.assertEqual(n({"action": "left_click", "coordinate": [10, 20]})["action"], "click")
        self.assertEqual((n({"action": "left_click", "coordinate": [10, 20]})["x"]), 10)
        self.assertEqual(n({"name": "click", "arguments": '{"x": 5, "y": 6}'})["y"], 6)
        self.assertEqual(n({"function": {"name": "double_click", "arguments": {"element": "7"}}})["element"], 7)
        self.assertEqual(n({"action": "click", "x": "512, 300"})["y"], 300)
        self.assertEqual(n({"action": "click", "x": "512", "y": "300.5"})["y"], 300.5)
        self.assertEqual(n({"action": "click", "bbox": [0, 0, 100, 50]})["x"], 50)
        self.assertEqual(n({"action": "click", "position": "(12, 34)"})["x"], 12)
        self.assertEqual(n({"action": "click", "element_id": "[12]"})["element"], 12)
        self.assertEqual(n({"action": "hotkey", "keys": ["Control", "S"]})["keys"], "ctrl+s")
        self.assertEqual(n({"action": "key", "text": "Return"})["keys"], "enter")
        self.assertEqual(n({"action": "press", "key": "ctrl + shift + esc"})["keys"], "ctrl+shift+esc")
        self.assertEqual(n({"action": "type_text", "content": "привет"})["text"], "привет")
        self.assertEqual(n({"action": "launch", "app": "Блокнот"})["app"], "Блокнот")
        self.assertEqual(n({"action": "open_app", "name": "Calculator"})["app"], "Calculator")
        self.assertEqual(n({"action": "finish", "answer": "84"})["report"], "84")
        self.assertEqual(n({"action": "scroll_down", "amount": 3})["dy"], 360)
        self.assertEqual(n({"action": "scroll", "direction": "up"})["dy"], -600)
        self.assertEqual(n({"action": "left_click_drag", "start_coordinate": [1, 2], "coordinate": [3, 4]})["x2"], 3)
        self.assertEqual(n({"action": {"action": "click", "x": 1, "y": 2}})["x"], 1)
        self.assertEqual(n([{"action": "wait"}])["action"], "wait")
        self.assertEqual(n("garbage")["action"], "")
        self.assertEqual(n({"action": "computer.click", "x": 1, "y": 1})["action"], "click")
        self.assertEqual(n({"action": "focus", "window": "Блокнот"})["title"], "Блокнот")

        for empty in (0, -1, None, "none", ""):
            self.assertNotIn("element", n({"action": "type", "text": "x", "element": empty}), empty)

    def test_json_from_chatty_reply(self):
        raw = 'Думаю, надо открыть.\n```json\n{"action":"open_app","app":"Калькулятор","reason":"x"}\n```'
        self.assertEqual(g.normalize_action(ar._json_object(raw))["app"], "Калькулятор")

    def test_error_codes(self):
        self.assertEqual(g.classify_error("HTTP 429 Too Many Requests"), "rate_limit")
        self.assertEqual(g.classify_error("Windows не принял ввод (окно, вероятно, запущено от имени администратора"), "input_blocked")
        self.assertEqual(g.classify_error("Модель управления не вернула корректное JSON-действие"), "json")
        self.assertEqual(g.classify_error("Агент зациклился: одно и то же"), "stuck")
        self.assertEqual(g.classify_error("401 Unauthorized"), "auth")
        self.assertEqual(g.classify_error("Управление компьютером доступно только в Windows"), "not_windows")
        self.assertEqual(g.classify_error("что-то"), "other")
        self.assertEqual(g.classify_error(""), "")

class Marks(unittest.TestCase):
    def test_pick_and_draw(self):
        screens = [{"monitor": 1, "left": 0, "top": 0, "width": 1400, "height": 788, "real_width": 2560, "real_height": 1440,
                    "scale_x": 1400 / 2560, "scale_y": 788 / 1440},
                   {"monitor": 2, "left": -1920, "top": 180, "width": 1400, "height": 788, "real_width": 1920, "real_height": 1080,
                    "scale_x": 1400 / 1920, "scale_y": 788 / 1080}]
        elements = [{"name": "OK", "type": "button", "rect": [100, 100, 200, 140]},
                    {"name": "OK", "type": "button", "rect": [101, 101, 201, 141]},
                    {"name": "", "type": "button", "rect": [0, 0, 50, 50]},
                    {"name": "", "type": "edit", "rect": [300, 300, 900, 340]},
                    {"name": "tiny", "type": "button", "rect": [5, 5, 7, 7]},
                    {"name": "pane", "type": "pane", "rect": [0, 0, 2560, 1440]},
                    {"name": "Left", "type": "listitem", "rect": [-1800, 400, -1700, 440]},
                    {"name": "Nowhere", "type": "button", "rect": [9000, 9000, 9100, 9100]}]
        marks = g.pick_marks(elements, screens)
        self.assertEqual([m["name"] for m in marks], ["OK", "", "Left"])
        self.assertEqual([m["id"] for m in marks], [1, 2, 3])
        self.assertEqual(marks[2]["monitor"], 2)
        rows = g.marks_prompt(marks, screens, g.NORM1000)
        self.assertEqual(rows[0]["at"], [59, 83])
        image = g.draw_marks(Image.new("RGB", (1400, 788)), marks, screens[0])
        self.assertEqual(image.getpixel((int(100 * 1400 / 2560), int(110 * 788 / 1440))), (255, 0, 170))

class FakeDesktop:
    """Physical pixels; monitor 1 2560x1440 (150 %), monitor 2 at (-1920, 180). Apps: an icon «Блокнот» on the
    desktop, a Notepad window when opened. A topmost Quick Access window (excluded from the screenshot) swallows clicks
    in its rectangle unless hidden, and takes the focus whenever the user approves a step."""
    MONITORS = [{"left": 0, "top": 0, "right": 2560, "bottom": 1440}, {"left": -1920, "top": 180, "right": 0, "bottom": 1260}]
    ICON = (60, 60, 180, 210)
    EDITOR = (700, 300, 1900, 1100)
    QUICK = (840, 450, 1725, 1140)

    def __init__(self):
        self.notepad = False
        self.focused = "desktop"
        self.text = ""
        self.quick_text = ""
        self.quick_visible = True
        self.clicks = []
        self.swallowed = 0
        self.launched = []

    def image(self):
        img = Image.new("RGB", (2560 + 1920, 1440 + 180), (20, 40, 80))
        d = ImageDraw.Draw(img)
        d.rectangle([1920 + self.ICON[0], self.ICON[1], 1920 + self.ICON[2], self.ICON[3]], fill=(240, 240, 240))
        if self.notepad:
            d.rectangle([1920 + 650, 250, 1920 + 1950, 1150], fill=(250, 250, 250))
            if self.text:
                d.rectangle([1920 + 720, 320, 1920 + 720 + 30 * len(self.text), 360], fill=(0, 0, 0))
        return img

    @staticmethod
    def inside(box, x, y):
        return box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def click(self, x, y, clicks):
        self.clicks.append((x, y, clicks))
        if self.quick_visible and self.inside(self.QUICK, x, y):
            self.focused = "quick"; self.swallowed += 1; return
        if self.notepad and self.inside(self.EDITOR, x, y):
            self.focused = "notepad"; return
        if self.inside(self.ICON, x, y) and clicks >= 2:
            self.notepad = True; self.focused = "notepad"; return
        self.focused = "desktop"

    def install(self, test):
        CA = ar.ComputerAutomation
        desk = self
        hw = {"notepad": 11, "quick": 99}
        original_approve = CA.approve

        def approve(rt, allowed, **kw):
            if rt._approval and not rt._approval.done():
                desk.focused = "quick"
            return original_approve(rt, allowed, **kw)

        def type_text(text, should_stop=None):
            if desk.focused == "notepad":
                desk.text += text
            elif desk.focused == "quick":
                desk.quick_text += text

        def launch(cls, name):
            desk.launched.append(name)
            if "блок" not in name.lower() and "notepad" not in name.lower():
                raise ValueError(f"Приложение «{name}» не найдено")
            desk.notepad = True; desk.focused = "notepad"
            return {"name": "Блокнот", "target": "notepad.exe"}

        def windows():
            return [{"hwnd": 11, "title": "Безымянный — Блокнот", "rect": [650, 250, 1950, 1150]}] if desk.notepad else []

        def set_visible(hwnds, visible):
            if 99 in hwnds:
                desk.quick_visible = bool(visible)

        def focus(hwnd):
            if hwnd == 11:
                desk.focused = "notepad"

        def elements(cls):
            rows = [{"name": "Блокнот", "type": "listitem", "rect": list(desk.ICON), "window": "Рабочий стол"}]
            if desk.notepad:
                rows.append({"name": "Текстовый редактор", "type": "document", "rect": list(desk.EDITOR), "window": "Блокнот"})
            return rows

        patches = [
            patch.object(CA, "_grab_desktop", classmethod(lambda cls: ([dict(m) for m in desk.MONITORS], desk.image()))),
            patch.object(CA, "_monitors", staticmethod(lambda: [dict(m) for m in desk.MONITORS])),
            patch.object(CA, "_ui_elements", classmethod(elements)),
            patch.object(CA, "_list_windows", staticmethod(windows)),
            patch.object(CA, "start_overlay", lambda self: None),
            patch.object(CA, "_click", classmethod(lambda cls, x, y, double=False, **kw: desk.click(x, y, kw.get("clicks", 2 if double else 1)))),
            patch.object(CA, "_type_text", staticmethod(type_text)),
            patch.object(CA, "_send_key_combo", classmethod(lambda cls, combo: cls._parse_key_combo(combo))),
            patch.object(CA, "_launch_app", classmethod(launch)),
            patch.object(CA, "_focus_window", staticmethod(focus)),
            patch.object(CA, "_find_window_by_title", classmethod(lambda cls, t: 11 if desk.notepad else 0)),
            patch.object(CA, "_foreground_window", staticmethod(lambda: hw.get(desk.focused, 0))),
            patch.object(CA, "_is_own", classmethod(lambda cls, h: h == 99)),
            patch.object(CA, "_window_alive", staticmethod(lambda h: h == 11 and desk.notepad)),
            patch.object(CA, "_own_windows_at", staticmethod(lambda x, y: [99] if desk.quick_visible and desk.inside(desk.QUICK, x, y) else [])),
            patch.object(CA, "_set_windows_visible", staticmethod(set_visible)),
            patch.object(CA, "approve", approve),
            patch.object(ar.asyncio, "sleep", new=_short_sleep),
        ]
        for item in patches:
            item.start()
            test.addCleanup(item.stop)

class Script:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def __call__(self, model, messages):
        self.prompts.append(messages)
        reply = self.replies.pop(0) if self.replies else '{"action": "done", "report": "end"}'
        return reply(self) if callable(reply) else reply

def run_agent(script, *, ask=True, decide=None, **kw):
    runtime = ar.ComputerAutomation()
    seen = []

    async def scenario():
        runtime.job = asyncio.create_task(ar.run_computer_agent(runtime, script, task="Открой Блокнот и напиши привет",
                                                                ask_each_action=ask, **kw))
        while not runtime.job.done():
            await _real_sleep(0.005)
            if runtime.pending_action:
                seen.append(dict(runtime.pending_action))
                (decide or (lambda rt, n: rt.approve(True)))(runtime, len(seen))
    asyncio.run(scenario())
    return runtime, seen

class LoopOnFakeDesktop(unittest.TestCase):
    def setUp(self):
        self.desk = FakeDesktop()
        self.desk.install(self)

    def test_qwen_norm_double_click_on_icon_hits_it(self):

        model = Script(['{"action":"double_click","monitor":1,"x":47,"y":94,"reason":"Открыть Блокнот"}',
                        '{"action":"done","report":"ok"}'])
        runtime, _ = run_agent(model, model="xkiro-qwen/qwen3.8-max:free", ask=False)
        self.assertEqual(runtime.convention, "norm1000")
        self.assertTrue(self.desk.notepad, self.desk.clicks)
        sys_prompt = model.prompts[0][0]["content"]
        self.assertIn("0..1000", sys_prompt)
        self.assertEqual(runtime.steps[0]["point"], [round(47 / 1000, 4), round(94 / 1000, 4)])

    def test_element_click_and_quick_window_steps_aside_and_focus_returns(self):
        model = Script(['{"action":"open_app","app":"Блокнот"}',
                        lambda s: '{"action":"click","element":%d}' % next(
                            int(m.group(1)) for m in [re.search(r'"id": (\d+), "name": "Текстовый редактор"', s.prompts[-1][1]["content"][0]["text"])]),
                        '{"action":"type","text":"привет"}',
                        '{"action":"done","report":"Готово"}'])
        runtime, seen = run_agent(model, model="qwen3.8-max")
        self.assertEqual(runtime.status, "done", runtime.error)
        self.assertEqual(self.desk.launched, ["Блокнот"])
        self.assertEqual(self.desk.swallowed, 0, "the click under the Quick Access window reached Notepad")
        self.assertTrue(self.desk.quick_visible, "the window is shown again after the click")
        self.assertEqual(self.desk.text, "привет", "after «Разрешить» the focus went back to Notepad")
        self.assertEqual(self.desk.quick_text, "")
        self.assertEqual(len(seen), 3)
        self.assertIn("«Текстовый редактор»", seen[1]["description"])
        self.assertEqual(runtime.steps[1]["target"], "Текстовый редактор")
        prompt = model.prompts[0][1]["content"][0]["text"]
        self.assertIn("<elements", prompt)
        self.assertIn('"name": "Блокнот"', prompt)

    def test_element_by_name(self):
        model = Script(['{"action":"open_app","app":"Блокнот"}', '{"action":"click","element":"Текстовый редактор"}',
                        '{"action":"type","text":"ok"}', '{"action":"done"}'])
        runtime, _ = run_agent(model, model="qwen3.8-max", ask=False)
        self.assertEqual((runtime.status, self.desk.text, runtime.steps[1]["target"]), ("done", "ok", "Текстовый редактор"))

    def test_type_with_element_clicks_it_first(self):
        model = Script(['{"action":"open_app","app":"notepad"}', '{"action":"type","text":"hi","element":2}', '{"action":"done"}'])
        runtime, _ = run_agent(model, model="qwen3.8-max", ask=False)
        self.assertEqual((runtime.status, self.desk.text), ("done", "hi"))

    def test_without_the_fix_the_old_bug_reproduces(self):
        """Proof: if MemuAI's window did not step aside, the click lands on it and typing goes into it."""
        with patch.object(ar.ComputerAutomation, "_own_windows_at", staticmethod(lambda x, y: [])), \
             patch.object(ar.ComputerAutomation, "_ensure_focus", new=lambda self: _real_sleep(0)):
            model = Script(['{"action":"open_app","app":"Блокнот"}', '{"action":"click","element":2}',
                            '{"action":"type","text":"привет"}', '{"action":"done"}'])
            run_agent(model, model="qwen3.8-max")
        self.assertEqual(self.desk.swallowed, 1)
        self.assertEqual(self.desk.text, "")
        self.assertEqual(self.desk.quick_text, "привет")

    def test_unknown_app_is_a_recoverable_failure(self):
        model = Script(['{"action":"open_app","app":"Фотошоп"}', '{"action":"done","report":"нет"}'])
        runtime, _ = run_agent(model, model="qwen3.8-max", ask=False)
        self.assertEqual(runtime.status, "done")
        self.assertEqual(runtime.steps[0]["status"], "failed")
        self.assertIn("не найдено", model.prompts[1][1]["content"][0]["text"])

    def test_typed_text_is_seen_as_a_change(self):
        model = Script(['{"action":"open_app","app":"Блокнот"}', '{"action":"type","text":"ab"}', '{"action":"done"}'])
        runtime, _ = run_agent(model, model="qwen3.8-max", ask=False)
        self.assertTrue(runtime.steps[1]["changed"], "a short word is a visible change")
        self.assertIn("[screen changed]", model.prompts[2][1]["content"][0]["text"])

    def test_pause_and_resume(self):
        runtime = ar.ComputerAutomation()
        model = Script(['{"action":"wait","seconds":1}', '{"action":"done","report":"ok"}'])
        phases = []

        async def scenario():
            runtime.job = asyncio.create_task(ar.run_computer_agent(runtime, model, model="m", task="x", ask_each_action=False))
            await _real_sleep(0)
            runtime.pause()
            for _ in range(800):
                await _real_sleep(0.005)
                phases.append(runtime.phase)
                if phases.count("paused_user") >= 20:
                    break
            self.assertFalse(runtime.job.done())
            self.assertTrue(runtime.user_paused)
            runtime.resume()
            await runtime.job
        asyncio.run(scenario())
        self.assertIn("paused_user", phases)
        self.assertEqual(runtime.status, "done")
        self.assertFalse(runtime.user_paused)

    def test_stop_while_paused(self):
        runtime = ar.ComputerAutomation()
        model = Script(['{"action":"wait","seconds":1}'] * 3)

        async def scenario():
            runtime.job = asyncio.create_task(ar.run_computer_agent(runtime, model, model="m", task="x", ask_each_action=False))
            await _real_sleep(0)
            runtime.pause()
            await _real_sleep(0.05)
            await runtime.stop()
            with contextlib.suppress(asyncio.CancelledError):
                await runtime.job
        asyncio.run(scenario())
        self.assertEqual((runtime.status, runtime.error_code), ("cancelled", "stopped"))

    def test_error_code_and_steps_limit(self):
        def broken(script):
            raise RuntimeError("HTTP 429: rate limit")
        runtime, _ = run_agent(Script([broken]), model="m", ask=False)
        self.assertEqual((runtime.status, runtime.error_code), ("error", "rate_limit"))
        runtime, _ = run_agent(Script(['{"action":"wait","seconds":1}'] * 5), model="m", ask=False, max_steps=2)
        self.assertEqual((runtime.status, runtime.error_code), ("done", "steps"))
        state = asyncio.run(runtime.public_state())
        for key in ("user_paused", "error_code", "convention", "started_at", "finished_at"):
            self.assertIn(key, state)

    def test_big_shot_kept_for_the_magnifier(self):
        model = Script(['{"action":"click","monitor":1,"x":10,"y":10}', '{"action":"done"}'])
        runtime, _ = run_agent(model, model="m", ask=False)
        big, small = runtime.shot(1, 1, big=True), runtime.shot(1, 1)
        self.assertTrue(big and small and big[:2] == b"\xff\xd8")
        self.assertGreater(Image.open(__import__("io").BytesIO(big)).width, Image.open(__import__("io").BytesIO(small)).width)

class Win32Details(unittest.TestCase):
    def test_overlay_styles_go_on_the_toplevel(self):
        src = (ROOT / "app" / "automation_runtime.py").read_text(encoding="utf-8")
        self.assertIn("hwnd = _tk_toplevel(bar)", src)
        self.assertIn("chwnd = _tk_toplevel(cursor_win)", src)
        self.assertNotIn("hwnd = bar.winfo_id(); style", src)

        class W:
            def winfo_id(self):
                return 5
        fake = type("U", (), {"GetAncestor": staticmethod(lambda h, f: 77 if (h, f) == (5, 2) else 0),
                              "GetParent": staticmethod(lambda h: 0)})()
        with patch.object(ctypes, "windll", type("D", (), {"user32": fake})(), create=True):
            self.assertEqual(ar._tk_toplevel(W()), 77)

    def test_cursor_overlay_hides_during_input(self):
        src = (ROOT / "app" / "automation_runtime.py").read_text(encoding="utf-8")
        self.assertIn("if target is None or self._input_busy:", src)

    def test_set_cursor_pos_is_verified_with_absolute_fallback(self):
        calls = []

        class U:
            def GetCursorPos(self, p):
                p._obj.x, p._obj.y = (100, 100); return 1

            def SetCursorPos(self, x, y):
                calls.append(("set", x, y)); return 1

            def GetSystemMetrics(self, i):
                return {76: -1920, 77: 0, 78: 1920 + 2560, 79: 1440}[i]

            def SendInput(self, n, arr, size):
                calls.append(("input", arr[0].mi.dwFlags, arr[0].mi.dx, arr[0].mi.dy)); return n
        with patch.object(ctypes, "windll", type("D", (), {"user32": U()})(), create=True), \
             patch.object(ar.time, "sleep", lambda *_: None):
            ar.ComputerAutomation._move_cursor(500, 300, glide=False)
        flags = ar.MOUSEEVENTF_MOVE | ar.MOUSEEVENTF_ABSOLUTE | ar.MOUSEEVENTF_VIRTUALDESK
        sent = [c for c in calls if c[0] == "input"]
        self.assertEqual(sent[0][1], flags)
        self.assertEqual(g.absolute_to_pixel(sent[0][2], sent[0][3], -1920, 0, 4480, 1440), (500, 300))

    def test_new_key_tokens(self):
        self.assertEqual(ar.ComputerAutomation._parse_key_combo("shift+="), [0x10, 0xBB])
        self.assertEqual(ar.ComputerAutomation._parse_key_combo("*"), [0x6A])

    def test_blocked_system_tools(self):
        with patch("app.assistant_apps.index", lambda wait=True: [{"name": "Редактор реестра", "aliases": ["regedit"], "target": "regedit.exe", "exe": "regedit.exe", "kind": "builtin"}]):
            with self.assertRaises(PermissionError):
                ar.ComputerAutomation._launch_app("regedit")

class ServerWiring(unittest.TestCase):
    def test_endpoint_completion_is_passed_to_the_run(self):
        src = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        block = src[src.index('@app.post("/api/work/computer/run")'):src.index('@app.get("/api/work/computer/state")')]
        self.assertIn("computer_runtime, completion, model=model", block)
        self.assertNotIn("computer_runtime, proxy_completion, model=model", block)

    def test_pause_resume_ask_each_routes(self):
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        app = create_app()
        runtime = next(r for r in (getattr(route.endpoint, "__closure__", None) or [] for route in app.routes
                                    if getattr(route, "path", "") == "/api/work/computer/state")
                       for r in [c.cell_contents for c in r] if isinstance(r, ar.ComputerAutomation))
        client = TestClient(app, base_url="http://127.0.0.1")
        runtime.status = "running"
        runtime._resume = asyncio.Event(); runtime._resume.set()
        self.assertEqual(client.post("/api/work/computer/action", json={"action": "pause"}).json(), {"ok": True})
        self.assertTrue(runtime.user_paused); self.assertFalse(runtime._resume.is_set())
        client.post("/api/work/computer/action", json={"action": "resume"})
        self.assertFalse(runtime.user_paused); self.assertTrue(runtime._resume.is_set())
        client.post("/api/work/computer/action", json={"action": "ask_each", "value": False})
        self.assertFalse(runtime.ask_each)
        runtime._big[2] = {1: b"\xff\xd8big"}; runtime._shots[2] = {1: b"\xff\xd8small"}
        self.assertEqual(client.get("/api/work/computer/shot/2?size=big").content, b"\xff\xd8big")
        self.assertEqual(client.get("/api/work/computer/shot/2").content, b"\xff\xd8small")
        runtime.status = "idle"

    def test_new_module_is_packaged(self):
        self.assertIn('"app.cu_grounding"', (ROOT / "build.py").read_text(encoding="utf-8"))
        self.assertIn("'app.cu_grounding'", (ROOT / "MemuAI.spec").read_text(encoding="utf-8"))

class QuickCardUI(unittest.TestCase):
    def setUp(self):
        self.js = (QUICK / "quick-computer.js").read_text(encoding="utf-8")
        self.css = (QUICK / "quick-computer.css").read_text(encoding="utf-8")

    def test_bilingual_strings(self):
        pairs = re.findall(r't\(\s*[`"]([^`"]*[а-яё][^`"]*)[`"],\s*[`"]([^`"]*)[`"]\)', self.js, flags=re.I)
        self.assertGreater(len(pairs), 60)
        for ru, en in pairs:
            self.assertTrue(en and not re.search("[а-яё]", en, re.I), (ru, en))

    def test_features(self):
        for needle in ('data-cu="${userPaused ? "resume" : "pause"}"', "data-cu-ask", "function explain(st)", "function lens(row",
                       "qcu-progress", "qcu-live", 'data-cu="approve_all"', "size=big", "rate_limit", "input_blocked"):
            self.assertIn(needle, self.js, needle)
        for needle in (".qcu-lens", ".qcu-switch", "@media (max-width: 480px)", ".qcu-explain", "prefers-reduced-motion"):
            self.assertIn(needle, self.css, needle)
        self.assertNotRegex(self.css, r":is\([^)]*::after", "a pseudo-element inside :is() drops the whole rule")

    def test_cache_bust(self):
        html = (QUICK / "index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r"/quick/quick-computer\.js\?v=2\.[0-9]+-r3[2-9]")
        self.assertIn("/quick/quick-computer.css?v=2.0-r32", html)

if __name__ == "__main__":
    unittest.main()
