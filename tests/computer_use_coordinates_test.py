"""Pure-Python coordinate math for Computer Use — no real clicks, no ctypes/PIL.

Screenshots shown to the vision model are downscaled to a known factor and
placed on the virtual desktop, which can have monitors with negative origins
(anything left of or above the primary display). A model-reported (monitor,
x, y) must round-trip back to the correct real, global desktop pixel. These
tests pin that math down without touching the real screen or cursor.
"""
from __future__ import annotations

import unittest

from app.automation_runtime import (
    MAX_SCREENSHOT_DIM,
    ComputerAutomation,
    _fit_scale,
    _scale_point_up,
    resolve_click_point,
)


class FitScaleContracts(unittest.TestCase):
    def test_image_within_bounds_is_not_scaled(self) -> None:
        self.assertEqual(_fit_scale(1280, 720, MAX_SCREENSHOT_DIM), 1.0)
        self.assertEqual(_fit_scale(MAX_SCREENSHOT_DIM, MAX_SCREENSHOT_DIM, MAX_SCREENSHOT_DIM), 1.0)

    def test_oversized_image_scales_down_by_longest_edge(self) -> None:
        scale = _fit_scale(3840, 2160, MAX_SCREENSHOT_DIM)
        self.assertAlmostEqual(scale, MAX_SCREENSHOT_DIM / 3840)
        self.assertLess(round(3840 * scale), 3840)
        self.assertEqual(round(3840 * scale), MAX_SCREENSHOT_DIM)

    def test_portrait_image_scales_by_its_own_longest_edge(self) -> None:
        scale = _fit_scale(1200, 4000, MAX_SCREENSHOT_DIM)
        self.assertEqual(round(4000 * scale), MAX_SCREENSHOT_DIM)

    def test_degenerate_dimensions_never_divide_by_zero(self) -> None:
        self.assertGreater(_fit_scale(0, 0, MAX_SCREENSHOT_DIM), 0)


class ScalePointRoundTripContracts(unittest.TestCase):
    def test_unscaled_point_is_unchanged(self) -> None:
        self.assertEqual(_scale_point_up(500, 300, 1.0), (500, 300))

    def test_scaled_point_maps_back_to_native_pixels(self) -> None:
        # A 3840-wide monitor downscaled to 1400 for the model: the model
        # reports a coordinate in that smaller image, and it must scale back
        # up to (roughly) where that pixel really is on the native screen.
        scale = _fit_scale(3840, 2160, MAX_SCREENSHOT_DIM)
        x, y = _scale_point_up(700, 394, scale)  # dead-center of the shown image
        self.assertAlmostEqual(x, 1920, delta=2)
        self.assertAlmostEqual(y, 1080, delta=2)

    def test_zero_or_invalid_scale_falls_back_to_identity(self) -> None:
        self.assertEqual(_scale_point_up(10, 20, 0), (10, 20))
        self.assertEqual(_scale_point_up(10, 20, -1), (10, 20))


class ResolveClickPointContracts(unittest.TestCase):
    def _monitor(self, **overrides) -> dict:
        base = {"left": 0, "top": 0, "width": 1920, "height": 1080,
                "real_width": 1920, "real_height": 1080, "scale": 1.0}
        base.update(overrides)
        return base

    def test_no_scaling_single_monitor_at_origin(self) -> None:
        screens = [self._monitor()]
        self.assertEqual(resolve_click_point({"monitor": 1, "x": 500, "y": 300}, screens), (500, 300))

    def test_downscaled_monitor_scales_click_back_up(self) -> None:
        scale = _fit_scale(3840, 2160, MAX_SCREENSHOT_DIM)
        shown_w, shown_h = round(3840 * scale), round(2160 * scale)
        screens = [self._monitor(width=shown_w, height=shown_h,
                                  real_width=3840, real_height=2160, scale=scale)]
        # Click near the middle of the *shown* (downscaled) image.
        gx, gy = resolve_click_point({"monitor": 1, "x": shown_w // 2, "y": shown_h // 2}, screens)
        self.assertAlmostEqual(gx, 1920, delta=3)
        self.assertAlmostEqual(gy, 1080, delta=3)

    def test_secondary_monitor_with_negative_origin_offsets_correctly(self) -> None:
        # A monitor placed to the left of the primary display has a negative
        # virtual-desktop origin; forgetting to add it is a classic
        # multi-monitor coordinate bug.
        screens = [
            self._monitor(),  # primary at (0, 0)
            self._monitor(left=-1920, top=-40, width=1920, height=1080),  # secondary, to the left and up
        ]
        gx, gy = resolve_click_point({"monitor": 2, "x": 10, "y": 10}, screens)
        self.assertEqual((gx, gy), (-1920 + 10, -40 + 10))

    def test_out_of_range_shown_coordinates_are_clamped_not_wrapped(self) -> None:
        screens = [self._monitor(width=800, height=600)]
        gx, gy = resolve_click_point({"monitor": 1, "x": 99999, "y": -500}, screens)
        self.assertEqual((gx, gy), (799, 0))

    def test_out_of_range_monitor_index_clamps_to_last_screen(self) -> None:
        screens = [self._monitor(), self._monitor(left=1920, top=0)]
        gx, gy = resolve_click_point({"monitor": 99, "x": 5, "y": 5}, screens)
        self.assertEqual((gx, gy), (1925, 5))

    def test_empty_screen_list_raises_instead_of_crashing(self) -> None:
        with self.assertRaises(ValueError):
            resolve_click_point({"monitor": 1, "x": 0, "y": 0}, [])


class KeyComboGuardContracts(unittest.TestCase):
    """These only exercise the validation that runs before any real input is
    sent (SendInput import happens after all checks pass), so they cannot
    press a real key even on a live Windows session."""

    def test_dangerous_combo_is_blocked(self) -> None:
        with self.assertRaises(PermissionError):
            ComputerAutomation._send_key_combo("win+r")

    def test_unknown_key_token_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ComputerAutomation._send_key_combo("ctrl+doesnotexist")

    def test_overlong_combo_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ComputerAutomation._send_key_combo("ctrl+alt+shift+win+a")

    def test_empty_combo_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ComputerAutomation._send_key_combo("")


if __name__ == "__main__":
    unittest.main()
