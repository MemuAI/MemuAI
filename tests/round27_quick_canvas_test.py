"""Round 27 (r27-quick-canvas): panels and «Быстрые действия» in Quick Ask — app/quick_canvas.py, app/quick_actions.py.

Layout without overlaps (fuzzed), the separate Quick Ask layout (MemuAI placements untouched), button/step/hotkey/icon
validation, risks and the approval signature, the macro runner with mocked executors ({prev}, waits, cancel, continue
on error, engine confirmations, step confirmations, page steps, paste, Do not disturb fallback), the HTTP routes
(approval before the first run, delete/undo, duplicate, import/export) and the wiring (manifest, build, panels.js embed).
The headless window / settings runs are tests/round27_quick_canvas_shots.py and round27_quick_canvas_settings_shots.py.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="r27qcv-test-")

from app import quick_actions as qa  # noqa: E402
from app import quick_canvas as qc  # noqa: E402

def overlapping(items):
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]:
                return (a, b)
    return None

class Layout(unittest.TestCase):
    def test_resolve_never_overlaps_and_stays_in_the_grid(self):
        rng = random.Random(27)
        for _ in range(300):
            items = []
            for n in range(rng.randint(0, 30)):
                kind = rng.choice(["panel", "action"])
                items.append({"id": f"i_{n:06d}", "type": kind, "ref": f"p_{n}" if kind == "panel" else f"a_{n:06d}",
                              "x": rng.randint(-3, 10), "y": rng.randint(-2, 12), "w": rng.randint(0, 12), "h": rng.randint(0, 9)})
            out = qc.resolve_layout(items)
            self.assertIsNone(overlapping(out))
            for it in out:
                self.assertGreaterEqual(it["x"], 0)
                self.assertLessEqual(it["x"] + it["w"], qc.COLS)
                if it["type"] == "panel":
                    self.assertGreaterEqual(min(it["w"], it["h"]), 2, "a panel is at least 2×2")
                else:
                    self.assertIn((it["w"], it["h"]), ((1, 1), (2, 2)), "a button is 1×1 or 2×2")

    def test_bad_rows_and_duplicates_are_dropped(self):
        out = qc.resolve_layout([
            {"id": "i_aaaaaa", "type": "panel", "ref": "p_x", "x": 0, "y": 0, "w": 2, "h": 2},
            {"id": "i_aaaaaa", "type": "panel", "ref": "p_y", "x": 2, "y": 0, "w": 2, "h": 2},
            {"id": "i_bbbbbb", "type": "panel", "ref": "p_x", "x": 4, "y": 0, "w": 2, "h": 2},
            {"id": "i_cccccc", "type": "widget", "ref": "p_z", "x": 0, "y": 3},
            {"id": "i_dddddd", "type": "panel", "ref": "../../etc", "x": 0, "y": 3},
            {"id": "x", "type": "action", "ref": "a_abcdef", "x": 0, "y": 0, "w": 1, "h": 1},
            "junk",
        ])
        self.assertEqual([i["ref"] for i in out], ["p_x", "a_abcdef"])
        self.assertTrue(out[1]["id"].startswith("i_") and out[1]["y"] >= 2)

    def test_add_item_takes_the_first_free_place(self):
        items = []
        a = qc.add_item(items, "panel", "p_one", "m")
        b = qc.add_item(items, "panel", "p_two", "m")
        c = qc.add_item(items, "action", "a_abcdef")
        self.assertEqual((a["x"], a["y"], a["w"], a["h"]), (0, 0, 4, 2))
        self.assertEqual((b["x"], b["y"]), (4, 0))
        self.assertEqual((c["x"], c["y"], c["w"]), (0, 2, 1))
        self.assertIsNone(overlapping(items))

    def test_quick_layout_never_touches_memuai_placements(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import panels
        pid = panels.create_panel(template="clock", start=False)["id"]
        before = next(p for p in panels.load()["panels"] if p["id"] == pid)
        app = FastAPI()
        app.include_router(qc.router)
        client = TestClient(app)
        out = client.post("/api/quick/canvas/panels", json={"panel": pid, "size": "w"}).json()
        self.assertTrue(out["ok"])
        self.assertEqual((out["item"]["w"], out["item"]["h"]), (8, 2))
        after = next(p for p in panels.load()["panels"] if p["id"] == pid)
        self.assertEqual((before["placements"], before["order"], before["size"]), (after["placements"], after["order"], after["size"]))

        again = client.post("/api/quick/canvas/panels", json={"panel": pid}).json()
        self.assertEqual(again["item"]["id"], out["item"]["id"])

class Validation(unittest.TestCase):
    def test_hotkeys(self):
        self.assertEqual(qa.normalize_hotkey("shift+ctrl+1"), "Ctrl+Shift+1")
        self.assertEqual(qa.normalize_hotkey("F7"), "F7")
        self.assertEqual(qa.normalize_hotkey(""), "")
        for bad in ("Q", "Ctrl+K", "Alt+M", "Alt+3", "Ctrl+Alt", "Ctrl+Shift+Enter", "Ctrl+A+B", "Ctrl+Ж"):
            with self.assertRaises(qa.ActionInvalid, msg=bad):
                qa.normalize_hotkey(bad)

    def test_icons(self):
        self.assertEqual(qa.validate_icon({"kind": "emoji", "value": "🚀"})["value"], "🚀")
        self.assertEqual(qa.validate_icon(None), {"kind": "builtin", "value": "bolt"})
        png = "data:image/png;base64," + "A" * 200
        self.assertEqual(qa.validate_icon({"kind": "image", "value": png})["kind"], "image")
        for bad in ({"kind": "builtin", "value": "../x"}, {"kind": "emoji", "value": "<b>"}, {"kind": "image", "value": "javascript:alert(1)"},
                    {"kind": "image", "value": "data:image/svg+xml;base64,AAAA"}, {"kind": "image", "value": "data:image/png;base64," + "A" * 200000},
                    {"kind": "font"}):
            with self.assertRaises(qa.ActionInvalid, msg=str(bad)[:60]):
                qa.validate_icon(bad)

    def test_steps(self):
        ok = qa.validate_action({"label": "Громкость", "steps": [{"type": "command", "action": "volume.set", "params": {"level": "30", "junk": 1}}]})
        self.assertEqual(ok["steps"][0]["params"], {"level": "30"})
        self.assertTrue(ok["id"].startswith("a_"))

        tpl = qa.validate_step({"type": "command", "action": "volume.set", "params": {"level": "{clipboard}"}})
        self.assertEqual(tpl["params"]["level"], "{clipboard}")
        bad = [
            {"type": "command", "action": "volume.set", "params": {"level": "громко"}},
            {"type": "command", "action": "_timer.drop"},
            {"type": "command", "action": "nope.nope"},
            {"type": "command", "action": "app.open", "params": {}},
            {"type": "open", "target": "folder", "value": "C:\\Windows"},
            {"type": "open", "target": "settings", "value": "regedit"},
            {"type": "open", "target": "app", "value": ""},
            {"type": "ask", "prompt": "  "},
            {"type": "shell", "command": ""},
            {"type": "memuai", "target": "hack"},
            {"type": "eval", "code": "1"},
        ]
        for row in bad:
            with self.assertRaises(qa.ActionInvalid, msg=json.dumps(row, ensure_ascii=False)):
                qa.validate_step(row)
        self.assertEqual(qa.validate_step({"type": "wait", "ms": 10 ** 9})["ms"], 600000)
        self.assertEqual(qa.validate_step({"type": "system", "op": "dnd", "value": "maybe"})["value"], "toggle")
        with self.assertRaises(qa.ActionInvalid):
            qa.validate_action({"label": "x", "steps": []})
        with self.assertRaises(qa.ActionInvalid):
            qa.validate_action({"label": "x", "steps": [{"type": "wait"}] * (qa.MAX_STEPS + 1)})

    def test_every_template_is_valid(self):
        for lang in ("ru", "en"):
            rows = qa.templates(lang)
            self.assertGreaterEqual(len(rows), 10)
            keys = {r["key"] for r in rows}
            self.assertTrue({"morning", "call", "translate"} <= keys, keys)
            for row in rows:
                qa.validate_action({k: v for k, v in row.items() if k != "key"})
                self.assertTrue(row["label"])

    def test_risks_and_signature(self):
        safe = qa.validate_action({"label": "a", "steps": [{"type": "command", "action": "volume.set", "params": {"level": 30}}]})
        self.assertEqual(qa.risks(safe), [])
        risky = qa.validate_action({"label": "b", "steps": [{"type": "open", "target": "url", "value": "https://x.org"},
                                                             {"type": "command", "action": "app.close", "params": {"name": "notepad"}},
                                                             {"type": "shell", "command": "dir"},
                                                             {"type": "open", "target": "path", "value": "{clipboard}"}]})
        tiers = [(r["index"], r["tier"]) for r in qa.risks(risky)]
        self.assertEqual(tiers, [(1, "confirm"), (2, "dangerous"), (3, "confirm")])
        sig = qa.signature(risky)
        relabel = dict(risky, label="other", color="#ff0000")
        self.assertEqual(qa.signature(relabel), sig, "looks do not change what was approved")
        changed = json.loads(json.dumps(risky))
        changed["steps"][1]["params"]["name"] = "explorer"
        self.assertNotEqual(qa.signature(changed), sig, "a changed step needs a new approval")
        self.assertEqual(qa.step_tier({"type": "command", "action": "volume.set", "params": {}}, {"always_confirm": ["volume.set"]}), "confirm")

    def test_render(self):
        from datetime import datetime
        ctx = {"now": datetime(2026, 10, 9, 14, 5), "clipboard": lambda: "БУФЕР", "selection": "", "prev": "P", "input": "I"}
        self.assertEqual(qa.render("{date} {time} {weekday} {prev} {input} {clipboard} {json}", ctx, "ru"),
                         "09.10.2026 14:05 пятница P I БУФЕР {json}")
        self.assertEqual(qa.render("{text}", {"selection": "SEL", "clipboard": "C"}, "ru"), "SEL")
        self.assertEqual(qa.render("{text}", {"selection": "", "clipboard": "C"}, "ru"), "C")

class FakeEngine:
    def __init__(self, tiers=None, fail=()):
        self.calls, self.confirms = [], []
        self.tiers = tiers or {}
        self.fail = set(fail)

    async def run(self, action, params, lang="ru", confirmed=False):
        self.calls.append((action, dict(params), confirmed))
        from app import assistant_catalog as catalog
        tier = self.tiers.get(action, (catalog.get(action) or {}).get("tier", "safe"))
        if action in self.fail:
            return {"status": "error", "message": "boom " + action}
        if tier != "safe" and not confirmed:
            return {"status": "confirm", "message": "confirm " + action,
                    "confirm": {"token": "tok-" + action, "tier": tier, "countdown": 5 if tier == "dangerous" else 0, "expires": time.time() + 60}}
        if action == "dfy.start":
            return {"status": "running", "message": "handing over", "client": {"type": "dfy", "goal": params.get("goal")}}
        return {"status": "done", "message": "ok " + action, "copy": params.get("text", "")}

    async def confirm(self, token, accept, lang="ru"):
        self.confirms.append((token, accept))
        if not accept:
            return {"status": "cancelled", "message": "cancelled"}
        action = token[4:]
        return await self.run(action, {}, lang, True)

def make_runner(engine, **kw):
    return qa.Runner(run_action=engine.run, confirm_token=engine.confirm, ask_ai=kw.pop("ask_ai", None) or (lambda p, l: asyncio.sleep(0, "AI(" + p + ")")),
                     insert_text=kw.pop("insert_text", None) or (lambda text: asyncio.sleep(0, True)), clipboard_get=lambda: "CLIP",
                     dnd=kw.pop("dnd", lambda mode: True), engine_settings=lambda: {}, **kw)

def action_of(steps, approved=False):
    a = qa.validate_action({"label": "test", "steps": steps})
    if approved:
        a["approved"] = qa.signature(a)
    return a

async def wait_status(run, statuses=("done", "error", "cancelled"), timeout=5.0):
    end = time.monotonic() + timeout
    while run.status not in statuses:
        if time.monotonic() > end:
            raise AssertionError(f"run stuck in {run.status}: {run.public()}")
        await asyncio.sleep(0.01)
    return run

class Runner(unittest.IsolatedAsyncioTestCase):
    async def test_macro_passes_prev_and_runs_in_order(self):
        eng = FakeEngine()
        runner = make_runner(eng)
        run = runner.start(action_of([{"type": "ask", "mode": "silent", "prompt": "fix {selection}"},
                                      {"type": "text", "mode": "copy", "text": "<{prev}>"},
                                      {"type": "command", "action": "volume.set", "params": {"level": 30}}]),
                           context={"selection": "teh text"})
        await wait_status(run)
        self.assertEqual(run.status, "done", run.public())
        self.assertEqual(eng.calls[0], ("clipboard.set", {"text": "<AI(fix teh text)>"}, False))
        self.assertEqual(eng.calls[1][0], "volume.set")
        self.assertEqual([s["status"] for s in run.public()["steps"]], ["done", "done", "done"])

    async def test_error_stops_unless_continue_on_error(self):
        eng = FakeEngine(fail={"volume.mute"})
        runner = make_runner(eng)
        run = runner.start(action_of([{"type": "system", "op": "mute"}, {"type": "command", "action": "volume.set", "params": {"level": 5}}]))
        await wait_status(run)
        self.assertEqual(run.status, "error")
        self.assertEqual([s["status"] for s in run.steps], ["error", "skipped"])
        self.assertEqual(len(eng.calls), 1)
        eng2 = FakeEngine(fail={"volume.mute"})
        run2 = make_runner(eng2).start(action_of([{"type": "system", "op": "mute", "continue_on_error": True},
                                                  {"type": "command", "action": "volume.set", "params": {"level": 5}}]))
        await wait_status(run2)
        self.assertEqual(run2.status, "done")
        self.assertEqual([s["status"] for s in run2.steps], ["error", "done"])

    async def test_cancel_interrupts_a_wait(self):
        eng = FakeEngine()
        runner = make_runner(eng)
        run = runner.start(action_of([{"type": "wait", "ms": 60000}, {"type": "command", "action": "volume.set", "params": {"level": 5}}]))
        await asyncio.sleep(0.05)
        started = time.monotonic()
        self.assertTrue(runner.cancel(run.id))
        await wait_status(run)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(eng.calls, [], "nothing after the cancel ran")
        self.assertFalse(runner.cancel(run.id), "a finished run can not be cancelled again")

    async def test_unapproved_confirm_step_waits_for_the_user(self):
        eng = FakeEngine()
        runner = make_runner(eng)
        run = runner.start(action_of([{"type": "command", "action": "app.close", "params": {"name": "notepad"}}]))
        await wait_status(run, ("confirm",))
        pub = run.public()
        self.assertEqual(pub["confirm"]["kind"], "engine")
        self.assertNotIn("token", pub["confirm"], "the engine token never leaves the server")
        self.assertEqual(eng.calls, [("app.close", {"name": "notepad"}, False)])
        self.assertTrue(runner.answer_confirm(run.id, True))
        await wait_status(run)
        self.assertEqual(run.status, "done")
        self.assertEqual(eng.confirms, [("tok-app.close", True)])

    async def test_rejected_confirmation_cancels(self):
        eng = FakeEngine()
        runner = make_runner(eng)
        run = runner.start(action_of([{"type": "command", "action": "app.close", "params": {"name": "x"}},
                                      {"type": "command", "action": "volume.set", "params": {"level": 1}}]))
        await wait_status(run, ("confirm",))
        runner.answer_confirm(run.id, False)
        await wait_status(run)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(eng.confirms, [("tok-app.close", False)])
        self.assertFalse(any(c[0] == "volume.set" for c in eng.calls))

    async def test_approved_confirm_runs_confirmed_but_dangerous_still_asks(self):
        eng = FakeEngine()
        runner = make_runner(eng)
        run = runner.start(action_of([{"type": "command", "action": "app.close", "params": {"name": "x"}},
                                      {"type": "shell", "command": "dir"}], approved=True))
        await wait_status(run, ("confirm", "done", "error"))
        self.assertEqual(eng.calls[0], ("app.close", {"name": "x"}, True), "the approval covers a confirm-tier step")
        self.assertEqual(run.status, "confirm", "a dangerous step asks on every run")
        self.assertEqual(run.public()["confirm"]["tier"], "dangerous")
        self.assertEqual(run.public()["confirm"]["countdown"], 5)
        self.assertEqual(eng.calls[1], ("shell.run", {"command": "dir"}, False))
        runner.answer_confirm(run.id, True)
        await wait_status(run)
        self.assertEqual(run.status, "done")

    async def test_template_parameters_are_never_pre_approved(self):
        eng = FakeEngine()
        runner = make_runner(eng)
        run = runner.start(action_of([{"type": "open", "target": "path", "value": "{clipboard}"}], approved=True))
        await wait_status(run, ("confirm", "done"))
        self.assertEqual(eng.calls[0], ("open.path", {"path": "CLIP"}, False), "a path from the clipboard is confirmed by the engine")

    async def test_step_confirmation(self):
        eng = FakeEngine()
        runner = make_runner(eng)
        run = runner.start(action_of([{"type": "command", "action": "volume.set", "params": {"level": 9}, "confirm": True}]))
        await wait_status(run, ("confirm",))
        self.assertEqual(run.public()["confirm"]["kind"], "step")
        self.assertEqual(eng.calls, [], "nothing runs before the answer")
        runner.answer_confirm(run.id, False)
        await wait_status(run)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(eng.calls, [])

    async def test_page_steps(self):
        eng = FakeEngine()
        shown = []
        runner = make_runner(eng, show_window=lambda: asyncio.sleep(0, shown.append(1)))
        run = runner.start(action_of([{"type": "ask", "mode": "window", "prompt": "hi {input}"},
                                      {"type": "text", "mode": "copy", "text": "{prev}!"}]), context={"input": "there"})
        await asyncio.sleep(0.05)
        pub = run.public()
        self.assertEqual(pub["client"], [{"seq": 1, "type": "ask", "text": "hi there"}])
        self.assertTrue(shown, "the window is shown for an answer in the window")
        self.assertFalse(runner.client_done(run.id, 99, True, "x"), "unknown sequence numbers are ignored")
        self.assertTrue(runner.client_done(run.id, 1, True, "ANSWER"))
        await wait_status(run)
        self.assertEqual(eng.calls[-1], ("clipboard.set", {"text": "ANSWER!"}, False))
        self.assertEqual(run.public()["client"], [])

    async def test_page_step_timeout_is_an_error(self):
        runner = make_runner(FakeEngine(), client_timeout=0.1)
        run = runner.start(action_of([{"type": "text", "mode": "input", "text": "x"}]))
        await wait_status(run)
        self.assertEqual(run.status, "error")
        self.assertIn("не ответило", run.error)

    async def test_dfy_card_is_handed_to_the_page(self):
        eng = FakeEngine()
        runner = make_runner(eng)
        run = runner.start(action_of([{"type": "dfy", "goal": "book a table"}], approved=True))
        await asyncio.sleep(0.05)
        rows = run.public()["client"]
        self.assertEqual(rows[0]["type"], "card")
        self.assertEqual(rows[0]["card"]["client"], {"type": "dfy", "goal": "book a table"})
        runner.client_done(run.id, rows[0]["seq"], True)
        await wait_status(run)
        self.assertEqual(run.status, "done")

    async def test_paste_without_the_windows_app_is_a_clear_error(self):
        runner = make_runner(FakeEngine(), insert_text=lambda text: asyncio.sleep(0, False))
        run = runner.start(action_of([{"type": "text", "mode": "paste", "text": "hello"}]))
        await wait_status(run)
        self.assertEqual(run.status, "error")
        self.assertIn("Windows", run.error)

    async def test_dnd_falls_back_to_settings(self):
        eng = FakeEngine()
        run = make_runner(eng, dnd=lambda mode: None).start(action_of([{"type": "system", "op": "dnd", "value": "on"}]))
        await wait_status(run)
        self.assertEqual(eng.calls, [("open.settings", {"page": "focus"}, False)])
        self.assertEqual(run.status, "done")
        self.assertIn("настройки", run.steps[0]["message"])
        eng2 = FakeEngine()
        run2 = make_runner(eng2, dnd=lambda mode: True).start(action_of([{"type": "system", "op": "dnd", "value": "on"}]))
        await wait_status(run2)
        self.assertEqual(eng2.calls, [])
        self.assertIn("включено", run2.steps[0]["message"])

class Routes(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.tmp = tempfile.mkdtemp(prefix="r27qcv-routes-")
        self.old_path = qc._path
        qc._path = lambda: Path(self.tmp) / "quick_canvas.json"
        self.engine = FakeEngine()
        self.old_runner = qc.RUNNER
        qc.RUNNER = make_runner(self.engine)
        app = FastAPI()
        app.include_router(qc.router)

        self.client = TestClient(app).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        qc._path = self.old_path
        qc.RUNNER = self.old_runner
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, action, **extra):
        out = self.client.post("/api/quick/canvas/actions", json={"action": action, **extra}).json()
        self.assertTrue(out.get("ok"), out)
        return out["action"]

    def test_defaults_do_not_get_in_the_way(self):
        st = self.client.get("/api/quick/canvas").json()
        self.assertEqual(st["settings"]["open_mode"], "input", "the window opens in the usual input mode")
        self.assertEqual(st["actions"], [])
        self.assertEqual(st["layout"]["items"], [])

    def test_first_run_of_a_risky_button_needs_approval(self):
        a = self.save({"label": "close", "steps": [{"type": "command", "action": "app.close", "params": {"name": "x"}}]})
        self.assertFalse(a["approved"])
        out = self.client.post("/api/quick/canvas/run", json={"id": a["id"]}).json()
        self.assertIn("approval", out)
        self.assertEqual(self.engine.calls, [], "nothing ran without the approval")
        out = self.client.post("/api/quick/canvas/run", json={"id": a["id"], "approve": True}).json()
        self.assertTrue(out["ok"])
        st = self.client.get("/api/quick/canvas").json()
        self.assertTrue(st["actions"][0]["approved"])

        a2 = self.save({**{k: v for k, v in st["actions"][0].items() if k not in ("approved", "risks")}, "label": "renamed"})
        self.assertTrue(a2["approved"])
        changed = {**{k: v for k, v in a2.items() if k not in ("approved", "risks")}}
        changed["steps"] = [{"type": "command", "action": "app.close", "params": {"name": "y"}}]
        self.assertFalse(self.save(changed)["approved"])
        forgot = self.save({**{k: v for k, v in a2.items() if k not in ("approved", "risks")}}, forget_approval=True)
        self.assertFalse(forgot["approved"])

    def test_safe_button_runs_and_reports_progress(self):
        a = self.save({"label": "vol", "steps": [{"type": "command", "action": "volume.set", "params": {"level": 3}}]})
        run = self.client.post("/api/quick/canvas/run", json={"id": a["id"], "context": {"selection": "s" * 100000}}).json()["run"]
        for _ in range(100):
            run = self.client.get(f"/api/quick/canvas/runs/{run['id']}").json()["run"]
            if run["status"] == "done":
                break
            time.sleep(0.02)
        self.assertEqual(run["status"], "done")
        self.assertEqual(self.client.get("/api/quick/canvas/runs/r_nope").status_code, 404)
        self.assertEqual(self.client.post("/api/quick/canvas/run", json={"id": "a_missing"}).status_code, 404)

    def test_page_step_over_http(self):
        a = self.save({"label": "ask", "steps": [{"type": "ask", "mode": "window", "prompt": "Переведи: {selection}"}]})
        run = self.client.post("/api/quick/canvas/run", json={"id": a["id"], "context": {"selection": "hello"}}).json()["run"]
        for _ in range(100):
            run = self.client.get(f"/api/quick/canvas/runs/{run['id']}").json()["run"]
            if run["client"]:
                break
            time.sleep(0.02)
        self.assertEqual(run["client"][0]["text"], "Переведи: hello")
        out = self.client.post(f"/api/quick/canvas/runs/{run['id']}/client", json={"seq": run["client"][0]["seq"], "ok": True, "result": "Translate: hello"}).json()
        self.assertTrue(out["ok"])
        for _ in range(100):
            run = self.client.get(f"/api/quick/canvas/runs/{run['id']}").json()["run"]
            if run["status"] == "done":
                break
            time.sleep(0.02)
        self.assertEqual((run["status"], run["output"]), ("done", "Translate: hello"))

    def test_switched_off_buttons_do_not_run(self):
        a = self.save({"label": "vol", "steps": [{"type": "command", "action": "volume.set", "params": {"level": 3}}]})
        self.client.post("/api/quick/canvas/settings", json={"settings": {"actions": False, "open_mode": "bogus", "density": "large"}})
        st = self.client.get("/api/quick/canvas").json()["settings"]
        self.assertEqual((st["actions"], st["open_mode"], st["density"]), (False, "input", "large"))
        self.assertEqual(self.client.post("/api/quick/canvas/run", json={"id": a["id"]}).status_code, 403)

    def test_delete_undo_duplicate(self):
        a = self.save({"label": "x", "hotkey": "Ctrl+Alt+1", "steps": [{"type": "wait", "ms": 100}]}, size="l")
        st = self.client.get("/api/quick/canvas").json()
        self.assertEqual(st["layout"]["items"][0]["w"], 2, "a large button is 2×2")
        dup = self.client.post(f"/api/quick/canvas/actions/{a['id']}/duplicate", json={"lang": "ru"}).json()["action"]
        self.assertNotEqual(dup["id"], a["id"])
        self.assertEqual(dup["hotkey"], "", "a copy does not steal the hotkey")
        self.assertIn("копия", dup["label"])
        removed = self.client.delete(f"/api/quick/canvas/actions/{a['id']}").json()["removed"]
        self.assertEqual(len(self.client.get("/api/quick/canvas").json()["actions"]), 1)
        self.client.post("/api/quick/canvas/actions/restore", json={"removed": removed})
        st = self.client.get("/api/quick/canvas").json()
        self.assertEqual({x["id"] for x in st["actions"]}, {a["id"], dup["id"]})
        self.assertIsNone(overlapping(st["layout"]["items"]))

    def test_import_export(self):
        a = self.save({"label": "close", "steps": [{"type": "command", "action": "app.close", "params": {"name": "x"}}]})
        self.client.post("/api/quick/canvas/run", json={"id": a["id"], "approve": True})
        data = self.client.get("/api/quick/canvas/export").json()
        self.assertEqual(data["format"], qc.EXPORT_FORMAT)
        self.assertNotIn("approved", data["actions"][0])
        data["actions"][0]["approved"] = qa.signature(data["actions"][0])
        data["actions"].append({"label": "bad", "steps": [{"type": "eval"}]})
        out = self.client.post("/api/quick/canvas/import", json={"data": data, "mode": "merge"}).json()
        self.assertEqual((out["added"], out["skipped"]), (1, 1))
        ids = [x["id"] for x in out["actions"]]
        self.assertEqual(len(set(ids)), 2, "an existing id gets a new one")
        imported = [x for x in out["actions"] if x["id"] != a["id"]][0]
        self.assertFalse(imported["approved"], "imported buttons are never pre-approved")
        bad = self.client.post("/api/quick/canvas/import", json={"data": {"format": "x"}}).json()
        self.assertIn("error", bad)
        out = self.client.post("/api/quick/canvas/import", json={"data": data, "mode": "replace"}).json()
        self.assertEqual(len(out["actions"]), 1)

    def test_layout_route_cleans_and_de_overlaps(self):
        a = self.save({"label": "x", "steps": [{"type": "wait", "ms": 100}]})
        items = [{"id": "i_000001", "type": "panel", "ref": "p_aaa", "x": 0, "y": 0, "w": 4, "h": 4},
                 {"id": "i_000002", "type": "action", "ref": a["id"], "x": 1, "y": 1, "w": 1, "h": 1, "pinned": True},
                 {"id": "i_000003", "type": "action", "ref": "a_deleted00", "x": 6, "y": 0, "w": 1, "h": 1}]
        out = self.client.post("/api/quick/canvas/layout", json={"items": items}).json()["layout"]["items"]
        self.assertEqual([i["ref"] for i in out], ["p_aaa", a["id"]], "a deleted button's item is dropped")
        self.assertIsNone(overlapping(out))
        self.assertTrue(out[1]["pinned"])

    def test_validate_route(self):
        out = self.client.post("/api/quick/canvas/actions/validate", json={"action": {"label": "x", "steps": [{"type": "keys", "keys": "Win+L"}]}, "lang": "en"}).json()
        self.assertTrue(out["ok"])
        self.assertEqual(out["risks"][0]["tier"], "confirm")
        out = self.client.post("/api/quick/canvas/actions/validate", json={"action": {"label": "x", "steps": [{"type": "ask"}]}, "lang": "en"}).json()
        self.assertEqual(out["error"], "Step 1: Write the request for the AI")

class Wiring(unittest.TestCase):
    def test_manifest_build_and_embed(self):
        manifest = json.loads((ROOT / "app" / "pgstatic" / "quick" / "plugins" / "index.json").read_text("utf-8"))
        row = next(p for p in manifest["plugins"] if p.get("id") == "canvas")
        self.assertEqual(row["backend"], "app.quick_canvas")
        for key in ("src", "css", "settings", "settings_css"):
            self.assertTrue((ROOT / "app" / "pgstatic" / "quick" / "plugins" / row[key]).exists(), row[key])
        for name in ("build.py", "MemuAI.spec"):
            text = (ROOT / name).read_text("utf-8")
            self.assertIn("app.quick_canvas", text)
            self.assertIn("app.quick_actions", text)
        panels_js = (ROOT / "app" / "pgstatic" / "panels.js").read_text("utf-8")
        self.assertIn("window.MEMU_PANELS_EMBED", panels_js)
        self.assertIn("subscribe:", panels_js)
        self.assertIn("after: afterCard", (ROOT / "app" / "pgstatic" / "quick" / "plugins" / "commands.js").read_text("utf-8"))
        node = shutil.which("node")
        if node:
            for name in ("quick/plugins/canvas.js", "quick/plugins/canvas-settings.js", "quick/plugins/canvas-icons.js", "panels.js"):
                result = subprocess.run([node, "--check", str(ROOT / "app" / "pgstatic" / name)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, name + result.stderr)

    def test_every_ui_string_has_both_languages(self):
        text = (ROOT / "app" / "pgstatic" / "quick" / "plugins" / "canvas.js").read_text("utf-8")
        import re
        for match in re.finditer(r"\bt\(\"([^\"]*)\", \"([^\"]*)\"\)", text):
            ru, en = match.groups()
            self.assertTrue(ru and en, match.group(0))
            self.assertFalse(re.search(r"[А-Яа-яЁё]", en), "Cyrillic in an English string: " + en)

class WindowShots(unittest.TestCase):
    """tests/round27_quick_canvas_shots.py (the window) and round27_quick_canvas_settings_shots.py (Settings) in headless
    Chromium (skipped without playwright/Chromium)."""

    def run_script(self, name):
        out = Path(tempfile.mkdtemp(prefix="r27qcv-shots-"))
        try:
            env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="*")
            env.pop("MEMUAI_DATA_DIR", None)
            result = subprocess.run([sys.executable, str(ROOT / "tests" / name), str(out)], capture_output=True, text=True, timeout=300, env=env)
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def setUp(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright is not installed")
        chrome = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
        if not os.path.exists(chrome) and not os.path.exists("/usr/bin/chromium"):
            self.skipTest("no Chromium")

    def test_headless_window(self):
        self.run_script("round27_quick_canvas_shots.py")

    def test_headless_settings(self):
        self.run_script("round27_quick_canvas_settings_shots.py")

if __name__ == "__main__":
    unittest.main()
