# -*- coding: utf-8 -*-
"""MemuMeet: manifest, storage layout, pause segmentation, export,
settings and capture states.

The backend helpers live in ``app/module_builtins/meet/backend.py`` and
are loaded from the file directly: the module package has no ``__init__``
and importing the whole application is not needed to check its rules.
The module host is exercised through the real ``app.modules_host`` to
prove the package is discovered, loads and validates as a built-in.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
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

def _has_fastapi() -> bool:
    try:
        import fastapi  # noqa: F401
    except ImportError:
        return False
    return True



def energies(speech, silence, frame=0.1):
    """Synthetic RMS track: ``speech`` seconds loud, ``silence`` quiet, ..."""
    out = []
    for _ in range(int(round(speech / frame))):
        out.append(0.4)
    for _ in range(int(round(silence / frame))):
        out.append(0.001)
    return out


class ManifestTest(unittest.TestCase):
    def test_manifest_is_valid_and_declares_no_server_handler(self) -> None:
        manifest = json.loads((MEET_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "meet")
        self.assertEqual(manifest["title"], "MemuMeet")
        self.assertFalse(manifest["enabled"])
        self.assertIn("chat", manifest["permissions"])
        self.assertIn("media", manifest["permissions"])
        self.assertIn("storage", manifest["permissions"])
        self.assertIn("models", manifest["permissions"])
        self.assertNotIn("web", manifest["permissions"])
        self.assertEqual(manifest["persist"], ["settings"])
        self.assertEqual(manifest["bridge"]["model"], "")
        contributes = manifest["contributes"]
        self.assertEqual(contributes["skill"], "integration/skill.md")
        self.assertTrue((MEET_DIR / "integration" / "skill.md").is_file())
        # Every command opens the module; none names a Python handler,
        # so no change to module_integration_handlers.py is needed.
        for command in contributes["commands"]:
            self.assertEqual(command["run"], {"open": True})
        # Planning actions were added by the planning round: two default
        # actions, both disabled until the user enables them.
        self.assertIn("planning", contributes)
        self.assertEqual(len(contributes["planning"]), 2)
        self.assertTrue(all(not a.get("enabled", False) for a in contributes["planning"]))
        self.assertNotIn("widgets", contributes)
        for panel in contributes["panels"]:
            self.assertTrue((MEET_DIR / panel["entry"]).is_file())
            for name in panel["scripts"] + panel["styles"]:
                self.assertTrue((MEET_DIR / name).is_file(), name)

    def test_package_files_exist_and_i18n_covers_the_code(self) -> None:
        for name in ("index.html", "style.css", "main.js", "i18n.json", "backend.py", "icon.svg"):
            self.assertTrue((MEET_DIR / name).is_file(), name)
        i18n = json.loads((MEET_DIR / "i18n.json").read_text(encoding="utf-8"))
        self.assertIn("ru", i18n)
        self.assertIn("en", i18n)
        self.assertEqual(set(i18n["ru"]), set(i18n["en"]))
        code = (MEET_DIR / "main.js").read_text(encoding="utf-8")
        html = (MEET_DIR / "index.html").read_text(encoding="utf-8")
        import re
        # Keys always contain a dot; the pattern must not pick up words
        # from string literals that merely follow a "t(" elsewhere.
        used = set(re.findall(r'Memu\.t\("([a-z][a-z0-9_]*\.[a-z0-9_.]+)"', code))
        used |= set(re.findall(r'(?<![\w.])t\("([a-z][a-z0-9_]*\.[a-z0-9_.]+)"', code))
        used |= set(re.findall(r'data-i18n(?:-placeholder|-aria-label|-title)?="([^"]+)"', html))
        used.discard("meetings.source.")
        for key in sorted(used):
            self.assertIn(key, i18n["ru"], key)
            self.assertIn(key, i18n["en"], key)
        # The code must not hardcode a model list or invent STT keys.
        self.assertIn("Memu.models()", code)
        self.assertIn("Memu.transcribe", code)
        self.assertIn("Memu.record", code)
        self.assertIn("Memu.store", code)
        self.assertNotIn("voice_stt_api_key", code)
        self.assertNotIn("gpt-4o", code)
        # The sandbox blocks native dialogs: confirmations are in-page,
        # copying goes through the bridge, and an unfinished recording is
        # stopped when the page goes away (the host test requires this).
        self.assertIn("pagehide", code)
        self.assertIn("Memu.copy", code)
        self.assertNotIn("window.confirm(", code)
        self.assertNotIn("navigator.clipboard", code)

    @unittest.skipUnless(_has_fastapi(), "fastapi is not installed in this VM")
    def test_host_discovers_and_loads_meet(self) -> None:
        from app import modules_host as host
        self.assertIn("meet", host.BUILTIN_ORDER)
        with tempfile.TemporaryDirectory() as temp:
            rows = host.list_modules(Path(temp))
            self.assertEqual([row["id"] for row in rows], list(host.BUILTIN_ORDER))
            package = host.load_package(Path(temp), "meet")
            self.assertFalse(package["missing"])
            self.assertGreater(len(package["html"]), 400)
            self.assertGreater(len(package["js"]), 1200)
            self.assertIn("Memu.", package["js"])
        from app.module_integration import normalize_contributes, validate_contributes
        manifest = json.loads((MEET_DIR / "manifest.json").read_text(encoding="utf-8"))
        clean = normalize_contributes(manifest["contributes"], builtin=True)
        self.assertEqual(len(clean["commands"]), 4)
        errors = validate_contributes(manifest["contributes"], builtin=True, folder=MEET_DIR)
        self.assertEqual(errors, [])


class StorageTest(unittest.TestCase):
    def test_store_layout_is_under_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = backend.store_root(temp)
            self.assertEqual(root, Path(temp) / "memuai" / "modules" / "store" / "meet")
            self.assertEqual(backend.meeting_store_key("meet-abcdef0123456789"), "meetings/meet-abcdef0123456789")
            self.assertEqual(backend.audio_store_key("meet-abcdef0123456789"),
                             "meetings/meet-abcdef0123456789/audio")

    def test_meeting_id_validation_rejects_paths(self) -> None:
        for bad in ("", "../config", "meetings/x", "meet-1", "a b", ".", ".."):
            with self.assertRaises(ValueError, msg=bad):
                backend.validate_meeting_id(bad)
        self.assertEqual(backend.validate_meeting_id("meet-abcdef0123456789"), "meet-abcdef0123456789")

    def test_new_meeting_defaults(self) -> None:
        meeting = backend.new_meeting("Standup", source="microphone", duration_ms=61000, mime="audio/webm")
        self.assertTrue(meeting["id"].startswith("meet-"))
        self.assertEqual(meeting["title"], "Standup")
        self.assertFalse(meeting["has_audio"])
        self.assertEqual(meeting["transcript"], "")
        self.assertEqual(meeting["segments"], [])
        self.assertTrue(meeting["approximate_segments"])
        with self.assertRaises(ValueError):
            backend.new_meeting(source="screen")

    def test_audio_limits_mirror_the_voice_runtime(self) -> None:
        self.assertEqual(backend.MAX_AUDIO_BYTES, 25 * 1024 * 1024)
        payload = b"\x00\x01" * 100
        data_url = "data:audio/webm;base64," + base64.b64encode(payload).decode("ascii")
        raw, mime = backend.parse_data_url(data_url)
        self.assertEqual(raw, payload)
        self.assertEqual(mime, "audio/webm")
        with self.assertRaises(ValueError):
            backend.parse_data_url("data:text/plain;base64," + base64.b64encode(b"x").decode("ascii"))
        with self.assertRaises(ValueError):
            backend.validate_audio_bytes(b"x", "audio/unknown")
        with self.assertRaises(ValueError):
            backend.validate_audio_bytes(b"x" * (backend.MAX_AUDIO_BYTES + 1), "audio/wav")
        self.assertEqual(backend.audio_suffix("audio/ogg; codecs=opus"), ".ogg")
        self.assertEqual(backend.audio_suffix("audio/mpeg"), ".mp3")


class SegmentationTest(unittest.TestCase):
    def test_silence_splits_segments(self) -> None:
        track = energies(2.0, 1.0) + energies(3.0, 0.0)
        segments = backend.segment_energy(track)
        self.assertEqual(len(segments), 2)
        self.assertAlmostEqual(segments[0]["start"], 0.0)
        self.assertAlmostEqual(segments[0]["end"], 2.0, places=1)
        self.assertAlmostEqual(segments[1]["start"], 3.0, places=1)
        self.assertAlmostEqual(segments[1]["end"], 6.0, places=1)

    def test_short_pause_does_not_split(self) -> None:
        track = energies(2.0, 0.2) + energies(2.0, 0.0)
        self.assertEqual(len(backend.segment_energy(track)), 1)

    def test_empty_and_silent_tracks(self) -> None:
        self.assertEqual(backend.segment_energy([]), [])
        self.assertEqual(backend.segment_energy([0.001] * 50), [])
        self.assertEqual(backend.segment_energy([0.4] * 10), [{"start": 0.0, "end": 1.0}])

    def test_speakers_alternate_only_on_long_pauses(self) -> None:
        segments = [{"start": 0.0, "end": 2.0}, {"start": 2.4, "end": 4.0},
                    {"start": 6.5, "end": 8.0}]
        labelled = backend.assign_speakers(segments)
        self.assertEqual([row["speaker"] for row in labelled],
                         ["speaker-1", "speaker-1", "speaker-2"])
        off = backend.assign_speakers(segments, enabled=False)
        self.assertTrue(all(row["speaker"] == "speaker-1" for row in off))

    def test_text_is_spread_proportionally_and_marked_approximate(self) -> None:
        segments = backend.build_segments(
            energies(2.0, 1.0) + energies(2.0, 0.0),
            "one two three four five six seven eight")
        self.assertEqual(len(segments), 2)
        self.assertTrue(all(row["approximate"] for row in segments))
        self.assertEqual(" ".join(row["text"] for row in segments),
                         "one two three four five six seven eight")
        self.assertEqual(len(segments[0]["text"].split()), 4)
        # No timings at all: the whole text stays one block.
        whole = backend.distribute_transcript([], "hello world")
        self.assertEqual(len(whole), 1)
        self.assertTrue(whole[0]["approximate"])


class ExportAndSettingsTest(unittest.TestCase):
    def _meeting(self):
        return {
            "id": "meet-abcdef0123456789", "title": "Standup",
            "source": "microphone", "created_at": 1790000000000, "duration_ms": 125000,
            "transcript": "Hello everyone",
            "segments": [{"start": 0.0, "end": 5.0, "speaker": "speaker-1",
                          "text": "Hello everyone", "approximate": True}],
            "summary": "## Конспект\n\n- Договорились о релизе.",
            "speaker_names": {},
        }

    def test_markdown_export_contains_summary_and_transcript(self) -> None:
        text = backend.build_export(self._meeting(), kind="markdown", lang="ru")
        self.assertIn("# Standup", text)
        self.assertIn("## Конспект", text)
        self.assertIn("[00:00] Говорящий 1: Hello everyone", text)
        self.assertIn("не точная диаризация", text)
        self.assertIn("Микрофон", text)

    def test_text_export_and_filename(self) -> None:
        text = backend.build_export(self._meeting(), kind="txt", lang="en")
        self.assertIn("TRANSCRIPT", text)
        self.assertIn("Speaker 1: Hello everyone", text)
        name = backend.export_filename(self._meeting(), "markdown")
        self.assertTrue(name.endswith(".md"))
        self.assertIn("standup", name)

    def test_summary_prompt_stays_inside_the_transcript(self) -> None:
        prompt = backend.summary_prompt("We agreed to ship on Friday.", lang="en", title="Standup")
        self.assertIn("ONLY from the transcript", prompt["system"])
        self.assertIn("We agreed to ship on Friday.", prompt["user"])
        self.assertNotIn("http", prompt["system"] + prompt["user"])

    def test_default_settings_and_cleaning(self) -> None:
        settings = backend.default_settings()
        self.assertEqual(settings["model"], "")
        self.assertTrue(settings["speaker_heuristic"])
        self.assertTrue(settings["keep_audio"])
        cleaned = backend.clean_settings({"model": "some-model", "speaker_heuristic": False,
                                          "voice_stt_api_key": "must-not-stick", "unknown": 1})
        self.assertEqual(cleaned["model"], "some-model")
        self.assertFalse(cleaned["speaker_heuristic"])
        self.assertNotIn("voice_stt_api_key", cleaned)
        self.assertNotIn("unknown", cleaned)
        self.assertEqual(backend.clean_settings(None), backend.default_settings())

    def test_stt_description_never_carries_secrets(self) -> None:
        info = backend.describe_stt({"voice_stt_provider": "api", "voice_stt_api_model": "gpt-transcribe",
                                     "voice_stt_api_url": "https://api.openai.com/v1",
                                     "voice_stt_api_key": "sk-secret", "voice_language": "ru"})
        self.assertEqual(info["provider"], "api")
        self.assertEqual(info["model"], "gpt-transcribe")
        self.assertNotIn("sk-secret", json.dumps(info))
        self.assertNotIn("openai.com", json.dumps(info))
        local = backend.describe_stt({})
        self.assertEqual(local, backend.describe_stt({"voice_stt_provider": "bogus"}))
        self.assertEqual(local["provider"], "local")
        self.assertEqual(local["model"], "small")
        self.assertEqual(local["language"], "auto")


class CaptureStateTest(unittest.TestCase):
    def test_microphone_needs_the_host_bridge(self) -> None:
        states = backend.capture_state(platform="win32", media_recorder_available=True,
                                       get_display_media_available=False, host_record_available=True)
        self.assertEqual(states["microphone"]["state"], "available")
        self.assertEqual(states["import"]["state"], "available")
        states = backend.capture_state(platform="win32", media_recorder_available=True,
                                       get_display_media_available=False, host_record_available=False)
        self.assertEqual(states["microphone"]["state"], "unavailable")

    def test_system_audio_is_unknown_until_tried(self) -> None:
        states = backend.capture_state(platform="win32", media_recorder_available=True,
                                       get_display_media_available=True, host_record_available=True)
        self.assertEqual(states["system"]["state"], "unknown")
        states = backend.capture_state(platform="linux", media_recorder_available=True,
                                       get_display_media_available=False, host_record_available=True)
        self.assertEqual(states["system"]["state"], "unavailable")

    def test_no_media_recorder_means_no_capture(self) -> None:
        states = backend.capture_state(platform="win32", media_recorder_available=False,
                                       get_display_media_available=False, host_record_available=True)
        self.assertEqual(states["microphone"]["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
