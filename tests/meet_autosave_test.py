# -*- coding: utf-8 -*-
"""MemuMeet: settings autosave contract (backend side), the
display-capture policy gate, dual-track meetings and source-labelled
transcripts.

The browser side of autosave (debounce, "Saved" indicator, one write
for a burst of edits) and the getDisplayMedia guard are exercised with
the real ``main.js`` in ``tests/meet_recording_dom_test.cjs``; this
file pins the Python mirror in ``app/module_builtins/meet/backend.py``
and the static shape of the JavaScript that implements the contract:
debounced + chained saves, a flush on the Save button, the
pre-check before any ``getDisplayMedia`` call, and microphone choice
persisted through ``settings.mic_device``.
"""
from __future__ import annotations

import importlib.util
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


class CapturePolicyTest(unittest.TestCase):
    def test_disallowed_policy_reports_unavailable_without_a_guess(self) -> None:
        state = backend.capture_state(platform="win32", media_recorder_available=True,
                                      get_display_media_available=True,
                                      display_capture_allowed=False)
        self.assertEqual(state["system"]["state"], "unavailable")
        self.assertEqual(state["system"]["reason"], "policy_disallowed")

    def test_allowed_policy_keeps_the_honest_unknown_state(self) -> None:
        state = backend.capture_state(platform="win32", media_recorder_available=True,
                                      get_display_media_available=True,
                                      display_capture_allowed=True)
        self.assertEqual(state["system"]["state"], "unknown")
        state = backend.capture_state(platform="win32", media_recorder_available=True,
                                      get_display_media_available=True,
                                      display_capture_allowed=None)
        self.assertEqual(state["system"]["state"], "unknown")

    def test_no_policy_information_keeps_old_defaults(self) -> None:
        # The pre-existing invariants from tests/meet_test.py: win32
        # with getDisplayMedia available is "unknown"; linux without it
        # is "unavailable".
        state = backend.capture_state(platform="win32", media_recorder_available=True,
                                      get_display_media_available=True)
        self.assertEqual(state["system"]["state"], "unknown")
        state = backend.capture_state(platform="linux", media_recorder_available=True,
                                      get_display_media_available=False)
        self.assertEqual(state["system"]["state"], "unavailable")


class SettingsTest(unittest.TestCase):
    def test_clean_settings_keeps_mic_device_and_source(self) -> None:
        clean = backend.clean_settings({"mic_device": "mic-9", "source": "both",
                                        "model": "m1", "unknown_key": 1,
                                        "voice_stt_api_key": "secret"})
        self.assertEqual(clean["mic_device"], "mic-9")
        self.assertEqual(clean["source"], "both")
        self.assertEqual(clean["model"], "m1")
        self.assertNotIn("unknown_key", clean)
        self.assertNotIn("voice_stt_api_key", clean)

    def test_clean_settings_rejects_bad_sources_and_limits_ids(self) -> None:
        clean = backend.clean_settings({"source": "screen", "mic_device": "x" * 500})
        self.assertEqual(clean["source"], "microphone")
        self.assertEqual(len(clean["mic_device"]), 200)

    def test_default_settings_include_the_new_keys(self) -> None:
        defaults = backend.default_settings()
        self.assertEqual(defaults["mic_device"], "")
        self.assertEqual(defaults["source"], "microphone")
        self.assertIn("both", backend.ALLOWED_SOURCES)


class DualTrackTest(unittest.TestCase):
    def test_new_meeting_for_both_has_a_tracks_list(self) -> None:
        meeting = backend.new_meeting("Standup", source="both")
        self.assertEqual(meeting["source"], "both")
        self.assertEqual(meeting["tracks"], [])

    def test_microphone_track_has_its_own_store_key(self) -> None:
        meeting_id = "meet-abcdef0123456789"
        self.assertEqual(backend.audio_mic_store_key(meeting_id),
                         "meetings/meet-abcdef0123456789/audio-mic")
        self.assertNotEqual(backend.audio_store_key(meeting_id),
                            backend.audio_mic_store_key(meeting_id))

    def test_merge_track_segments_sorts_and_tags_sources(self) -> None:
        mic = [{"start": 2.0, "end": 3.0, "text": "yes", "speaker": "speaker-1"},
               {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "speaker-1"}]
        system = [{"start": 0.0, "end": 1.5, "text": "hello", "speaker": "speaker-1"},
                  {"start": 4.0, "end": 5.0, "text": "bye", "speaker": "speaker-1"}]
        merged = backend.merge_track_segments(mic, system)
        self.assertEqual([row["start"] for row in merged], [0.0, 0.0, 2.0, 4.0])
        # Microphone first on the exact tie at 0.0.
        self.assertEqual(merged[0]["source"], "microphone")
        self.assertEqual(merged[1]["source"], "system")
        self.assertEqual(merged[2]["source"], "microphone")
        self.assertEqual(merged[3]["source"], "system")

    def test_single_track_merge_keeps_old_shape(self) -> None:
        rows = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "speaker-1"}]
        merged = backend.merge_track_segments(rows)
        self.assertEqual(merged[0]["source"], "microphone")
        self.assertEqual(merged[0]["speaker"], "speaker-1")

    def test_transcript_text_labels_sources_only_for_both(self) -> None:
        segments = [{"start": 0.0, "text": "hi", "speaker": "speaker-1", "source": "microphone"},
                    {"start": 1.0, "text": "hello", "speaker": "speaker-1", "source": "system"}]
        text = backend.build_transcript_text(segments, lang="ru")
        self.assertIn("Вы:", text)
        self.assertIn("Система:", text)
        single = [{"start": 0.0, "text": "hi", "speaker": "speaker-1", "source": "microphone"}]
        self.assertIn("Говорящий 1:", backend.build_transcript_text(single, lang="ru"))

    def test_export_of_a_both_meeting_names_both_sources(self) -> None:
        meeting = backend.new_meeting("Standup", source="both")
        meeting["segments"] = [{"start": 0.0, "text": "hi", "speaker": "speaker-1", "source": "microphone"},
                               {"start": 1.0, "text": "hello", "speaker": "speaker-1", "source": "system"}]
        export = backend.build_export(meeting, kind="markdown", lang="ru")
        self.assertIn("Микрофон + системный звук", export)
        self.assertIn("Вы:", export)
        self.assertIn("Система:", export)


class AutosaveShapeTest(unittest.TestCase):
    def test_debounce_and_chain_are_present(self) -> None:
        self.assertIn("saveTimer", MAIN_JS)
        self.assertIn("saveChain", MAIN_JS)
        self.assertIsNotNone(
            re.search(r"saveTimer = setTimeout\(function \(\) \{[\s\S]{0,400}?Memu\.save",
                      MAIN_JS),
            "the debounced timer must be what calls Memu.save")
        self.assertIn("350", MAIN_JS)

    def test_save_indicator_states_are_wired(self) -> None:
        self.assertIn("#set-status", MAIN_JS)
        self.assertIn("settings.saving", MAIN_JS)
        self.assertIn("settings.saved", MAIN_JS)
        self.assertIn("settings.save_error", MAIN_JS)

    def test_save_button_flushes_instead_of_debouncing(self) -> None:
        self.assertIn("flushSaveSettings", MAIN_JS)
        self.assertIsNotNone(
            re.search(r"#set-save[\s\S]{0,200}?flushSaveSettings", MAIN_JS),
            "the Save button must flush instead of debouncing")

    def test_every_settings_control_schedules_a_save(self) -> None:
        for control in ("#set-model", "#set-lang", "#set-speakers",
                        "#set-keep-audio", "#set-large", "#set-confirm",
                        "#mic-device"):
            self.assertIn(control, MAIN_JS, control)
        self.assertGreater(MAIN_JS.count("scheduleSaveSettings()"), 5)

    def test_microphone_choice_is_enumerated_and_persisted(self) -> None:
        self.assertIn("enumerateDevices", MAIN_JS)
        self.assertIn("refreshMicDevices", MAIN_JS)
        self.assertIn("mic_device", MAIN_JS)
        self.assertIn("deviceId", MAIN_JS)


class RecordingGuardShapeTest(unittest.TestCase):
    def test_policy_precheck_exists_and_all_calls_are_guarded(self) -> None:
        self.assertIn("displayCaptureAllowed", MAIN_JS)
        self.assertIn('allowsFeature("display-capture")', MAIN_JS)
        # Exactly two real calls (sound check + recording start); the
        # other mentions are comments and the feature-existence check.
        self.assertEqual(MAIN_JS.count("navigator.mediaDevices.getDisplayMedia({"), 2)
        self.assertEqual(MAIN_JS.count("typeof devices.getDisplayMedia"), 1)
        # Each call site is preceded in the same function by the gate.
        for match in re.finditer(r"navigator\.mediaDevices\.getDisplayMedia\(\{", MAIN_JS):
            window = MAIN_JS[max(0, match.start() - 1500):match.start()]
            self.assertIn("displayCaptureAllowed", window)

    def test_no_console_error_on_capture_failure(self) -> None:
        self.assertNotIn("console.error", MAIN_JS)
        self.assertIn(".catch(function (error)", MAIN_JS)

    def test_wizard_steps_and_live_state_are_present(self) -> None:
        for token in ("recStep", "setStep", "#rec-live", "#level-live",
                      "#pending-tracks", "record.recording_both"):
            self.assertIn(token, MAIN_JS, token)

    def test_both_recording_starts_both_tracks_in_one_meeting(self) -> None:
        self.assertIn("startBothRecording", MAIN_JS)
        self.assertIn("micOnlyFallback", MAIN_JS)
        self.assertIn("audioMicKey", MAIN_JS)


if __name__ == "__main__":
    unittest.main()
