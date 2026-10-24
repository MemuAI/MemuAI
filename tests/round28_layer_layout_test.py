"""Round 28: the screen layer of Quick Ask — layout maths and monitor handling (pure, no Windows needed).

app/quick_layer_core.py (positions independent of resolution/DPI, per-monitor layouts with the latest arrangement as
the default, clamping, never overlapping, never under the Quick Ask window, hidden when nothing fits),
app/quick_layer.region_plan (the click-through window shape in physical pixels) and app/quick_ask_core.choose_monitor
/ place(target=…) / place_saved(target=…) (Quick Ask opens on the monitor the user is on — before Round 28 a
remembered spot pinned it to one monitor forever).
"""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import quick_ask_core as qc  # noqa: E402
from app import quick_layer_core as lc  # noqa: E402
from app.quick_layer import region_plan  # noqa: E402

# A laptop (1920×1200 @150 %, taskbar at the bottom) as primary, a 2560×1440 @100 % monitor to its LEFT and a
# 1366×768 @100 % one ABOVE it (negative coordinates), taskbar of the left monitor on its left edge.
MONITORS = [
    {"rect": (0, 0, 1920, 1200), "work": (0, 0, 1920, 1152), "dpi": 144, "primary": True},
    {"rect": (-2560, 0, 0, 1440), "work": (-2512, 0, 0, 1440), "dpi": 96, "primary": False},
    {"rect": (0, -768, 1366, 0), "work": (0, -768, 1366, -40), "dpi": 96, "primary": False},
]


def items_of(n_panels=4, n_buttons=8, seed=0):
    rnd = random.Random(seed)
    out = []
    for i in range(n_panels):
        w, h = rnd.choice([(2, 2), (4, 2), (4, 4), (8, 2)])
        out.append({"id": f"i_p{i:04d}", "type": "panel", "ref": f"p_{i}", "x": 0, "y": i * 2, "w": w, "h": h})
    for i in range(n_buttons):
        u = rnd.choice([1, 1, 1, 2])
        out.append({"id": f"i_b{i:04d}", "type": "action", "ref": f"a_{i:06d}", "x": i % 8, "y": 9, "w": u, "h": u})
    return out


def rect(row):
    return row["x"], row["y"], row["w"], row["h"]


def overlap(a, b, pad=0.0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw + pad - 0.01 and bx < ax + aw + pad - 0.01 and ay < by + bh + pad - 0.01 and by < ay + ah + pad - 0.01


class Monitors(unittest.TestCase):
    def test_keys_and_areas_follow_dpi_taskbar_and_negative_origins(self):
        laptop = lc.area_of(MONITORS[0])
        self.assertEqual(laptop["key"], "1920x1200@144")
        self.assertEqual((laptop["w"], laptop["h"], laptop["scale"]), (1280, 768, 1.5))
        left = lc.area_of(MONITORS[1])
        self.assertEqual((left["w"], left["h"], left["origin"]), (2512, 1440, [-2512, 0]))
        top = lc.area_of(MONITORS[2])
        self.assertEqual((top["w"], top["h"], top["origin"]), (1366, 728, [0, -768]))

    def test_css_physical_round_trip(self):
        for monitor in MONITORS:
            area = lc.area_of(monitor)
            for css in ((0, 0, 100, 50), (37.5, 120.25, 312, 152), (area["w"] - 72, area["h"] - 72, 72, 72)):
                phys = lc.to_physical_rect(css, area)
                back = lc.to_css_rect(phys, area)
                for a, b in zip(css, back):
                    self.assertAlmostEqual(a, b, delta=1.0 / area["scale"] + 1e-6)
            # the work-area corner is (0, 0) in the layer even for a monitor left of / above the primary one
            self.assertEqual(lc.to_css_rect(area["physical"], area)[:2], (0.0, 0.0))

    def test_keepout_is_the_window_and_its_growth(self):
        area = lc.area_of(MONITORS[0])
        geometry = qc.place([MONITORS[0]], (500, 500), mode="center")
        keep = lc.keepout_for(geometry, area)
        hx, hy, hw, hh = keep["hard"]
        sx, sy, sw, sh = keep["soft"]
        self.assertAlmostEqual(hw, geometry["w"] / 1.5, delta=1)
        self.assertGreaterEqual(sh, hh)
        self.assertEqual((hx, hy), (sx, sy))
        # anchored at the bottom: the answer grows UP, so the soft zone is above the window
        bottom = qc.place([MONITORS[0]], (500, 500), mode="bottom")
        keep_b = lc.keepout_for(bottom, area)
        self.assertLess(keep_b["soft"][1], keep_b["hard"][1] + 0.01)
        self.assertAlmostEqual(keep_b["soft"][1] + keep_b["soft"][3], keep_b["hard"][1] + keep_b["hard"][3], delta=1)


class Positions(unittest.TestCase):
    def test_round_trip_on_the_same_area(self):
        rnd = random.Random(7)
        for _ in range(300):
            aw, ah = rnd.randint(900, 3000), rnd.randint(600, 1600)
            w, h = rnd.choice([(72, 72), (152, 152), (312, 152), (632, 152)])
            x, y = rnd.uniform(0, aw - w), rnd.uniform(0, ah - h)
            pos = lc.rect_to_pos((x, y, w, h), aw, ah)
            bx, by = lc.pos_to_xy(pos, w, h, aw, ah, [aw, ah])
            self.assertAlmostEqual(bx, x, delta=0.02)
            self.assertAlmostEqual(by, y, delta=0.02)

    def test_edges_stay_edges_on_another_monitor(self):
        # a button 24 px from the right and top edges of a big screen
        pos = lc.rect_to_pos((2560 - 24 - 72, 24, 72, 72), 2560, 1400)
        self.assertEqual((pos["ax"], pos["ay"]), ("r", "t"))
        x, y = lc.pos_to_xy(pos, 72, 72, 1366, 728, [2560, 1400])
        self.assertLessEqual(1366 - (x + 72), 24)       # still glued to the right edge (offset shrinks, never grows)
        self.assertLessEqual(y, 24)
        x2, _ = lc.pos_to_xy(pos, 72, 72, 3840, 2100, [2560, 1400])
        self.assertAlmostEqual(3840 - (x2 + 72), 24)    # a bigger screen keeps the same distance
        # a centred panel stays centred
        mid = lc.rect_to_pos((1280 - 156, 600, 312, 152), 2560, 1400)
        cx, _ = lc.pos_to_xy(mid, 312, 152, 1366, 728, [2560, 1400])
        self.assertAlmostEqual(cx + 156, 683, delta=1)

    def test_clamp_keeps_everything_inside(self):
        for aw, ah, w, h, x, y in ((800, 600, 312, 312, -500, 4000), (300, 200, 632, 152, 50, 50), (1366, 728, 72, 72, 1360, 720)):
            cx, cy = lc.clamp_xy(x, y, w, h, aw, ah)
            if w <= aw:
                self.assertGreaterEqual(cx, 0)
                self.assertLessEqual(cx + w, aw + 1e-6)
            if h <= ah:
                self.assertGreaterEqual(cy, 0)
                self.assertLessEqual(cy + h, ah + 1e-6)


class Resolve(unittest.TestCase):
    def check(self, placed, area_w, area_h, keepout):
        shown = [p for p in placed if not p["hidden"]]
        hard = keepout.get("hard")
        for i, a in enumerate(shown):
            self.assertGreaterEqual(a["x"], -1e-6, a)
            self.assertGreaterEqual(a["y"], -1e-6, a)
            self.assertLessEqual(a["x"] + a["w"], area_w + 1e-6, a)
            self.assertLessEqual(a["y"] + a["h"], area_h + 1e-6, a)
            if hard:
                self.assertFalse(overlap(rect(a), tuple(hard)), f"{a['id']} under the Quick Ask window")
            for b in shown[i + 1:]:
                self.assertFalse(overlap(rect(a), rect(b)), f"{a['id']} overlaps {b['id']}")
        return shown

    def test_fuzz_never_overlaps_never_off_screen_never_under_the_window(self):
        rnd = random.Random(28)
        for case in range(120):
            items = items_of(rnd.randint(0, 6), rnd.randint(0, 14), seed=case)
            layer = lc.normalize_layer(None, items)                     # migrated from a Round 27 grid
            for monitor in MONITORS:
                area = lc.area_of(monitor)
                geometry = qc.place([monitor], ((monitor["rect"][0] + monitor["rect"][2]) // 2, (monitor["rect"][1] + monitor["rect"][3]) // 2),
                                    mode=rnd.choice(["center", "top", "bottom-right", "left"]))
                keepout = lc.keepout_for(geometry, area)
                placed = lc.resolve(items, layer, area["key"], area["w"], area["h"], keepout=keepout,
                                    density=rnd.choice(list(lc.DENSITY)))
                self.assertEqual(len(placed), len(items))
                self.check(placed, area["w"], area["h"], keepout)

    def test_items_without_room_are_hidden_not_off_screen(self):
        items = items_of(6, 30, seed=3)
        placed = lc.resolve(items, lc.normalize_layer(None, items), "x", 420, 300, keepout={"hard": [60, 60, 300, 120]})
        shown = self.check(placed, 420, 300, {"hard": [60, 60, 300, 120]})
        self.assertTrue(any(p["hidden"] for p in placed))
        self.assertTrue(shown)

    def test_soft_zone_is_given_up_only_when_needed(self):
        items = items_of(0, 3)
        layer = {"monitors": {}, "default": {"area": [1280, 768], "items": {}}}
        # plenty of room: nothing in the room the answer grows into
        keep = {"hard": [280, 170, 480, 90], "soft": [280, 170, 480, 560]}
        placed = lc.resolve(items, layer, "k", 1280, 768, keepout=keep)
        for p in placed:
            self.assertFalse(overlap(rect(p), tuple(keep["soft"])))
        # a tiny area: the soft zone is used, the hard one never
        keep2 = {"hard": [12, 12, 200, 60], "soft": [12, 12, 200, 250]}
        placed2 = lc.resolve(items_of(0, 4), layer, "k", 300, 300, keepout=keep2)
        self.check(placed2, 300, 300, keep2)

    def test_big_panels_shrink_on_a_small_monitor(self):
        items = [{"id": "i_wide000", "type": "panel", "ref": "p_w", "x": 0, "y": 0, "w": 8, "h": 4}]
        placed = lc.resolve(items, lc.normalize_layer(None, items), "small", 500, 400)
        self.assertLessEqual(placed[0]["w"], 500 - 2 * lc.MARGIN)
        self.assertGreaterEqual(placed[0]["units"][0], 2)
        self.assertFalse(placed[0]["hidden"])

    def test_per_monitor_layout_wins_and_default_adapts(self):
        items = items_of(2, 3, seed=5)
        layer = lc.normalize_layer({}, items)
        big = lc.area_of(MONITORS[1])
        # the user arranges on the big monitor
        rects = [{"id": it["id"], "x": 40 + 400 * i, "y": 60, "w": lc.item_px(it)[0], "h": lc.item_px(it)[1]} for i, it in enumerate(items)]
        lc.remember(layer, big["key"], big["w"], big["h"], rects)
        placed = {p["id"]: p for p in lc.resolve(items, layer, big["key"], big["w"], big["h"])}
        for r in rects:
            self.assertEqual((placed[r["id"]]["x"], placed[r["id"]]["y"]), (r["x"], r["y"]))
            self.assertEqual(placed[r["id"]]["source"], "exact")
        # another, smaller monitor never arranged: adapted from the default, inside, relative order kept
        small = lc.area_of(MONITORS[2])
        adapted = lc.resolve(items, layer, small["key"], small["w"], small["h"])
        self.check(adapted, small["w"], small["h"], {})
        self.assertTrue(all(p["source"] == "adapted" for p in adapted))
        xs = [p["x"] for p in sorted(adapted, key=lambda p: [r["id"] for r in rects].index(p["id"]))]
        self.assertEqual(xs, sorted(xs), "left-to-right order survives the adaptation")
        # then arranged on the small one too: it gets its own layout, the big one keeps its own
        lc.remember(layer, small["key"], small["w"], small["h"], [{**r, "x": 20, "y": 20 + 160 * i} for i, r in enumerate(rects)])
        again = {p["id"]: p for p in lc.resolve(items, layer, big["key"], big["w"], big["h"])}
        self.assertEqual(again[rects[0]["id"]]["x"], rects[0]["x"])
        self.assertEqual(len(layer["monitors"]), 2)

    def test_new_items_go_to_the_top_right_and_not_on_others(self):
        items = items_of(1, 1)
        layer = lc.normalize_layer({}, items)
        placed = lc.resolve(items, layer, "k", 1280, 768)
        self.check(placed, 1280, 768, {})
        self.assertTrue(all(p["source"] == "auto" for p in placed))
        self.assertTrue(all(p["x"] + p["w"] / 2 > 1280 / 2 for p in placed))
        self.assertTrue(any(abs(p["x"] + p["w"] - (1280 - lc.MARGIN)) < 0.01 and abs(p["y"] - lc.MARGIN) < 0.01 for p in placed))

    def test_remember_keeps_at_most_twelve_monitors(self):
        items = items_of(0, 1)
        layer = lc.normalize_layer({}, items)
        for i in range(20):
            lc.remember(layer, f"{1000 + i}x700@96", 1000 + i, 700, [{"id": items[0]["id"], "x": 10, "y": 10, "w": 72, "h": 72}], now=i)
        self.assertEqual(len(layer["monitors"]), lc.MAX_MONITORS)
        self.assertIn("1019x700@96", layer["monitors"])
        self.assertNotIn("1000x700@96", layer["monitors"])


class Migration(unittest.TestCase):
    def test_round27_grid_keeps_its_shape(self):
        grid = [{"id": "i_aaaa0001", "type": "panel", "ref": "p_a", "x": 0, "y": 0, "w": 4, "h": 2},
                {"id": "i_aaaa0002", "type": "action", "ref": "a_000001", "x": 4, "y": 0, "w": 1, "h": 1},
                {"id": "i_aaaa0003", "type": "action", "ref": "a_000002", "x": 5, "y": 0, "w": 1, "h": 1},
                {"id": "i_aaaa0004", "type": "panel", "ref": "p_b", "x": 0, "y": 2, "w": 8, "h": 2}]
        layer = lc.normalize_layer(None, grid)
        self.assertEqual(layer["migrated"], "r27-grid")
        self.assertEqual(set(layer["default"]["items"]), {g["id"] for g in grid})
        placed = {p["id"]: p for p in lc.resolve(grid, layer, "1920x1080@96", 1920, 1032)}
        a, b, c, d = (placed[g["id"]] for g in grid)
        self.assertLess(a["x"], b["x"])
        self.assertLess(b["x"], c["x"])
        self.assertAlmostEqual(a["y"], b["y"])
        self.assertGreater(d["y"], a["y"] + a["h"] - 1)
        self.assertGreater(a["x"], 1920 / 2, "the block sits on the right, away from the centred window")

    def test_garbage_is_cleaned(self):
        items = items_of(1, 1)
        raw = {"monitors": {"bad key that is far too long to be a monitor id at all": {}, "1920x1080@96": {"area": ["x"], "items": {
            items[0]["id"]: {"ax": "q", "dx": "nan", "dy": 3}, items[1]["id"]: {"ax": "l", "ay": "t", "dx": 5, "dy": 6}, "i_gone": {"ax": "l"}}}},
               "default": "nope"}
        layer = lc.normalize_layer(raw, items)
        self.assertEqual(list(layer["monitors"]), ["1920x1080@96"])
        mon = layer["monitors"]["1920x1080@96"]
        self.assertEqual(list(mon["items"]), [items[1]["id"]])
        self.assertEqual(mon["area"], list(lc.MIGRATION_AREA))
        self.assertEqual(layer["default"]["items"], {})


class RegionPlan(unittest.TestCase):
    def test_items_are_scaled_clipped_and_rounded(self):
        plan = region_plan([[10, 10, 72, 72, 18], [-50, 0, 100, 40, 0], [0, 0, 0, 10, 4], "bad"], 1.5, (1920, 1152))
        self.assertFalse(plan["full"])
        self.assertEqual(plan["rects"][0], [15, 15, 123, 123, 54])
        self.assertEqual(plan["rects"][1][:4], [0, 0, 75, 60])
        self.assertEqual(len(plan["rects"]), 2)

    def test_full_cuts_out_the_quick_window(self):
        plan = region_plan([], 1.25, (1600, 900), full=True, keepout=[100, 100, 400, 120])
        self.assertTrue(plan["full"])
        self.assertEqual(plan["rects"], [[0, 0, 1600, 900, 0]])
        self.assertEqual(plan["minus"], [125, 125, 625, 275])


class QuickMonitor(unittest.TestCase):
    def test_choose_monitor(self):
        self.assertEqual(qc.choose_monitor(MONITORS, "cursor", (-100, 50)), 1)
        self.assertEqual(qc.choose_monitor(MONITORS, "cursor", (100, -100)), 2)
        self.assertEqual(qc.choose_monitor(MONITORS, "primary", (-100, 50)), 0)
        # the monitor that holds most of the active window
        self.assertEqual(qc.choose_monitor(MONITORS, "window", (500, 500), (-900, 100, 300, 800)), 1)
        # the desktop window spans every screen → the cursor decides
        self.assertEqual(qc.choose_monitor(MONITORS, "window", (100, -100), (-2560, -768, 1920, 1440)), 2)
        self.assertEqual(qc.choose_monitor(MONITORS, "window", (100, 100), None), 0)

    def test_place_on_the_target_monitor_with_its_dpi(self):
        g = qc.place(MONITORS, (100, 100), mode="center", target=1)
        self.assertEqual(g["monitor"], 1)
        self.assertEqual(g["scale"], 1.0)
        self.assertTrue(-2512 <= g["x"] and g["x"] + g["w"] <= 0)
        g2 = qc.place(MONITORS, (-500, 100), mode="cursor", target=0)    # cursor elsewhere: classic spot of the target
        self.assertEqual(g2["monitor"], 0)
        self.assertEqual(g2["scale"], 1.5)
        self.assertTrue(0 <= g2["x"] and g2["x"] + g2["w"] <= 1920)

    def test_remembered_spot_opens_on_the_monitor_the_user_is_on(self):
        # the window was dragged to the top-right corner of the laptop …
        g = qc.place(MONITORS, (100, 100), mode="top-right", target=0)
        record = qc.snap(MONITORS, (g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"]))["record"]
        # … before Round 28 it reopened there even with the cursor on the left monitor
        legacy = qc.place_saved(MONITORS, record, (-1000, 500))
        self.assertEqual(legacy["monitor"], 0)
        # now the spot is applied on the target monitor: the same corner of THAT monitor, its DPI, its work area
        moved = qc.place_saved(MONITORS, record, (-1000, 500), target=1)
        self.assertEqual(moved["monitor"], 1)
        self.assertEqual(moved["scale"], 1.0)
        self.assertEqual(moved["x"] + moved["w"], 0 - qc.SCREEN_MARGIN)          # right edge of the left monitor
        self.assertEqual(moved["y"], 0 + qc.SCREEN_MARGIN)
        above = qc.place_saved(MONITORS, record, (100, -100), target=2)
        self.assertEqual(above["monitor"], 2)
        self.assertTrue(-768 <= above["y"] < -40)

    def test_prefs_normalize_the_monitor_choice(self):
        out = qc.normalize_prefs({"quick_ask_monitor": "foreground"})
        self.assertEqual(out["quick_ask_monitor"], "window")
        self.assertEqual(qc.normalize_prefs({})["quick_ask_monitor"], "cursor")
        self.assertEqual(qc.normalize_prefs({"quick_ask_monitor": "bogus"})["quick_ask_monitor"], "cursor")
        self.assertEqual(qc.PREF_DEFAULTS["quick_ask_monitor"], "cursor")


if __name__ == "__main__":
    unittest.main()
