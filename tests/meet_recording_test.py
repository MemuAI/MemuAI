# -*- coding: utf-8 -*-
"""MemuMeet: microphone list honesty, split sound checks and the
opaque-origin display-capture gate.

The browser behaviour itself (a sandboxed iframe with an opaque
origin, fake devices, a real getDisplayMedia call) is exercised with
Playwright in the verification harness; this file pins the Python
mirror in ``app/module_builtins/meet/backend.py`` and the static
shape of the JavaScript that implements the contract, so a regression
cannot silently bring back numbered microphone names, a
getDisplayMedia call from the microphone check, or a policy gate
that only works when the Permissions-Policy API happens to exist.
"""
from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEET_DIR = ROOT / "app" / "module_builtins" / "meet"


def load_backend():
    spec = importlib.util.spec_from_file_location("memu_meet_backend", MEET_DIR / "backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backend = load_backend()
MAIN_JS = (MEET_DIR / "main.js").read_text(encoding="utf-8")
I18N = json.loads((MEET_DIR / "i18n.json").read_text(encoding="utf-8"))
HTML = (MEET_DIR / "index.html").read_text(encoding="utf-8")


class MicLabelTest(unittest.TestCase):
    def test_real_label_is_used_as_is(self) -> None:
        self.assertEqual(backend.format_mic_label("USB Microphone", "mic-2"), "USB Microphone")
        self.assertEqual(backend.format_mic_label("  Rode NT-USB  ", "x"), "Rode NT-USB")

    def test_unlabelled_device_is_honest_with_a_short_id(self) -> None:
        self.assertEqual(backend.format_mic_label("", "mic-3"), "Устройство без подписи · mic-3")
        self.assertEqual(
            backend.format_mic_label(None, "52654e5b9ba15cb37cb8ceeb74f56aae25e55c66cadbb7f0b3523a52543de510"),
            "Устройство без подписи · 5265…e510")
        self.assertEqual(backend.format_mic_label("", "", lang="en"), "Unlabelled device")

    def test_no_numbered_microphone_names_anywhere(self) -> None:
        for label in (backend.format_mic_label("", "a"), backend.format_mic_label("", "b" * 40)):
            self.assertNotIn("Микрофон 1", label)
            self.assertNotRegex(label, r"Microphone \d")
        self.assertNotIn('t("record.mic_device") + " "', MAIN_JS)
        self.assertNotIn("Микрофон 1", MAIN_JS)

    def test_short_device_id(self) -> None:
        self.assertEqual(backend.short_device_id("default"), "default")
        self.assertEqual(backend.short_device_id(""), "")
        self.assertEqual(backend.short_device_id("abcdefghijk"), "abcd…hijk")


class CaptureGateTest(unittest.TestCase):
    def test_opaque_origin_blocks_system_capture(self) -> None:
        state = backend.capture_state(platform="win32", media_recorder_available=True,
                                      get_display_media_available=True,
                                      display_capture_allowed=True, origin_opaque=True)
        self.assertEqual(state["system"]["state"], "unavailable")
        self.assertEqual(state["system"]["reason"], "policy_disallowed")

    def test_opaque_origin_does_not_change_the_microphone_state(self) -> None:
        state = backend.capture_state(platform="win32", media_recorder_available=True,
                                      get_display_media_available=True,
                                      display_capture_allowed=True, origin_opaque=True)
        self.assertEqual(state["microphone"]["state"], "available")

    def test_js_gate_checks_more_than_the_policy_api(self) -> None:
        self.assertIn('window.origin === "null"', MAIN_JS)
        self.assertIn('document.featurePolicy', MAIN_JS)
        self.assertIn('allowsFeature("display-capture")', MAIN_JS)


class RecordingShapeTest(unittest.TestCase):
    def test_permission_is_requested_before_enumeration(self) -> None:
        start = MAIN_JS.index("function refreshMicDevices")
        end = MAIN_JS.index('$("#mic-device").addEventListener', start)
        window = MAIN_JS[start:end]
        self.assertIn("requestMicPermission", window)
        branch = window[window.index("if (requestPermission)"):]
        self.assertLess(branch.index("requestMicPermission"), branch.index("enumerate()"))
        self.assertIn("return enumerate();", window)
        self.assertIn("getUserMedia({ audio: true })", MAIN_JS)

    def test_mic_check_uses_a_level_meter_and_never_gets_display_media(self) -> None:
        match = re.search(r"function startMicLevelCheck\(\)[\s\S]*?^}", MAIN_JS, re.M)
        self.assertIsNotNone(match)
        body = match.group(0)
        self.assertIn("getUserMedia", body)
        self.assertIn("startLevelMeter", body)
        self.assertNotIn(".getDisplayMedia(", body)
        self.assertIn("micCheckViaBridge", body)

    def test_system_check_is_the_only_other_get_display_media_caller(self) -> None:
        # Exactly two real calls, both guarded (sound check + recording).
        self.assertEqual(MAIN_JS.count("navigator.mediaDevices.getDisplayMedia({"), 2)
        for match in re.finditer(r"navigator\.mediaDevices\.getDisplayMedia\(\{", MAIN_JS):
            window = MAIN_JS[max(0, match.start() - 1500):match.start()]
            self.assertIn("displayCaptureAllowed", window)

    def test_check_status_is_written_exactly_once(self) -> None:
        # setCheckStatus targets one block only: the shared footer
        # element that duplicated every check status is gone, so the
        # fallback paragraph cannot appear twice on step 2.
        match = re.search(r"function setCheckStatus\(text, micOnly\) \{[\s\S]*?\n\}", MAIN_JS)
        self.assertIsNotNone(match)
        body = match.group(0)
        self.assertIn("#mic-check-status", body)
        self.assertIn("#system-check-status", body)
        stripped = body.replace("#mic-check-status", "").replace("#system-check-status", "")
        self.assertNotIn("#check-status", stripped)
        self.assertNotIn('id="check-status"', HTML)
        self.assertIn('id="mic-check-status"', HTML)
        self.assertIn('id="system-check-status"', HTML)

    def test_system_check_button_is_disabled_when_capture_is_blocked(self) -> None:
        self.assertIn("refreshSystemCheckUI", MAIN_JS)
        self.assertIn("btn.disabled = true", MAIN_JS)
        self.assertIn("#check-system-hint", MAIN_JS)
        self.assertIn("record.checking_picker", MAIN_JS)

    def test_recording_tracks_and_mic_only_fallback_are_shown(self) -> None:
        for token in ("updateRecordingTracks", "#rec-track-mic-state", "#rec-track-system-state",
                      "recSystemReason", "recording_mic_only", "record.pending_mic_only",
                      "#pending-tracks-note", "rec_mic_only"):
            self.assertIn(token, MAIN_JS, token)
        for element in ("mic-permission", "level-mic", "check-mic-stop", "mic-check-status",
                        "system-check-status", "check-system-hint", "rec-tracks",
                        "level-live-wrap", "level-live-label", "pending-tracks-note"):
            self.assertIn('id="%s"' % element, HTML, element)

    def test_new_i18n_keys_exist_in_both_languages(self) -> None:
        for key in ("record.mic_unlabeled", "record.mic_loading",
                    "record.mic_permission_granted", "record.mic_permission_denied",
                    "record.mic_permission_sandbox", "record.mic_permission_unavailable",
                    "record.mic_permission_unknown", "record.mic_level_requesting",
                    "record.mic_level_listening", "record.mic_level_done",
                    "record.mic_level_silent", "record.mic_level_unavailable",
                    "record.checking_picker", "record.system_no_audio",
                    "record.system_unavailable_context", "record.recording_mic_only",
                    "record.rec_mic_only", "record.rec_track_mic_active",
                    "record.rec_track_mic_planned", "record.rec_track_mic_off",
                    "record.rec_track_system_active", "record.rec_track_system_planned",
                    "record.rec_track_system_off", "record.rec_track_system_unavailable",
                    "record.pending_mic_only", "record.level_system", "record.level_mic"):
            self.assertIn(key, I18N["ru"], key)
            self.assertIn(key, I18N["en"], key)
        self.assertEqual(set(I18N["ru"]), set(I18N["en"]))

    def test_picker_text_only_appears_on_the_calling_path(self) -> None:
        # "Окно выбора" must not appear in the gate/blocked texts.
        self.assertNotIn("окно выбора", I18N["ru"]["record.system_blocked"].lower())
        self.assertNotIn("окно выбора", I18N["ru"]["record.system_unavailable_context"].lower())
        self.assertIn("окно выбора", I18N["ru"]["record.system_denied"].lower())
        self.assertIn("окне выбора", I18N["ru"]["record.system_no_audio"].lower())


if __name__ == "__main__":
    unittest.main()
