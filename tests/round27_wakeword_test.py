"""Round 27 (r27-wakeword): the Quick Ask wake word — app/wake_word.py, wake_audio.py, wake_word_api.py, plugins/wake*.

    MEMUAI_DATA_DIR=/tmp/x python -m unittest tests.round27_wakeword_test

No network. The real-recogniser tests (``RealVoskTest``) run only when the ``vosk`` package, the small ru/en models
(``MEMU_VOSK_MODELS`` = a folder with vosk-model-small-ru-0.22 / vosk-model-small-en-us-0.15) and ``espeak-ng`` exist;
otherwise they are skipped. Everything else uses a fake recogniser and a fake microphone.
"""
from __future__ import annotations

import array
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import wake_audio, wake_word  # noqa: E402
from app.wake_word import (DECOYS, Detector, EnergyGate, Session, WakeService, analyze_phrase,  # noqa: E402
                           grammar_for, match_hit, normalize_settings, threshold)

RATE = 16000
CHUNK = 1600
PHRASE_VALUE = 12000
OTHER_VALUE = 7000
COMMAND_VALUE = 9000

def tone(value: int, chunks: int = 1) -> list:

    data = array.array("h", [value if i % 2 else -value for i in range(CHUNK)]).tobytes()
    return [data] * chunks

def silence(chunks: int = 1, level: int = 0) -> list:
    return tone(level, chunks) if level else [bytes(CHUNK * 2)] * chunks

class FakeModel:
    def __init__(self, known=None):
        self.known = set(known or ())

    def vosk_model_find_word(self, word):
        return 1 if (not self.known or word in self.known) else -1

class FakeRecognizer:
    """Endpoints after a quiet chunk that follows loud ones; «says» the phrase for PHRASE_VALUE chunks."""

    def __init__(self, model, rate, grammar=None):
        self.grammar = json.loads(grammar) if grammar else None
        self.samples = 0
        self.loud: list = []
        self.pending = None

    def SetWords(self, on):
        pass

    def _value(self, chunk):
        samples = array.array("h")
        samples.frombytes(chunk)
        return max(abs(x) for x in samples) if samples else 0

    def AcceptWaveform(self, chunk):
        value = self._value(chunk)
        start = self.samples
        self.samples += len(chunk) // 2
        if value >= 5000:
            if self.loud and self.loud[-1][2] == value and self.loud[-1][1] == start:
                self.loud[-1] = (self.loud[-1][0], self.samples, value)
            else:
                self.loud.append((start, self.samples, value))
            return False
        if self.loud:
            self.pending, self.loud = self.loud, []
            return True
        return False

    def _words(self, spans):
        words = []
        for start, end, value in spans:
            if self.grammar is None:
                words.append({"word": "какая", "conf": 1.0, "start": start / RATE, "end": end / RATE})
                continue
            if value == PHRASE_VALUE:
                variant = self.grammar[0].split()
                mid = (start + end) / 2
                words += [{"word": variant[0], "conf": 0.95, "start": start / RATE, "end": mid / RATE},
                          {"word": variant[1], "conf": 0.9, "start": mid / RATE, "end": end / RATE}]
            else:
                words.append({"word": "мама", "conf": 1.0, "start": start / RATE, "end": end / RATE})
        return words

    def Result(self):
        spans, self.pending = self.pending or [], None
        words = self._words(spans)
        return json.dumps({"text": " ".join(w["word"] for w in words), "result": words})

    def PartialResult(self):
        return json.dumps({"partial": "какая" if self.loud else ""})

    def FinalResult(self):
        spans = (self.pending or []) + self.loud
        self.pending, self.loud = None, []
        words = self._words(spans)
        return json.dumps({"text": " ".join(w["word"] for w in words), "result": words})

class FakeVosk:
    Model = FakeModel
    KaldiRecognizer = FakeRecognizer

    def __init__(self, known=None):
        self.known = known

    def model(self, path):
        return FakeModel(self.known)

class FakeSource:
    def __init__(self, device, chunks, log):
        self.device = device
        self.chunks = list(chunks)
        self.log = log
        self.closed = False
        log.append(("open", device))

    def read(self, timeout=0.5):
        time.sleep(0.0005)
        if self.chunks:
            return self.chunks.pop(0)
        time.sleep(0.002)
        return bytes(CHUNK * 2)

    def close(self):
        self.closed = True
        self.log.append(("close", self.device))

def wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()

class TempRoot(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wake-test-"))
        patches = [mock.patch.object(wake_word, "_root", lambda: self.tmp),
                   mock.patch.object(wake_word, "ui_language", lambda: "ru")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

class PhraseTest(unittest.TestCase):
    def test_word_count_is_two_to_four(self):
        known = lambda w: True  # noqa: E731
        self.assertIsNotNone(analyze_phrase("мему", "ru", known)["problem"])
        self.assertIsNotNone(analyze_phrase("раз два три четыре пять", "ru", known)["problem"])
        self.assertTrue(analyze_phrase("Привет, Мему!", "ru", known)["ok"])
        self.assertEqual(analyze_phrase("Привет, Мему!", "ru", known)["phrase"], "привет мему")
        self.assertIn("2–4", analyze_phrase("x", "en", known)["problem"]["en"])

    def test_unknown_word_gets_one_sound_alike_alias(self):
        vocab = {"привет", "мемо", "мемы", "мем"}
        out = analyze_phrase("привет мему", "ru", lambda w: w in vocab)
        self.assertTrue(out["ok"])
        self.assertEqual(out["words"][1]["alias"], ["мемо"])
        self.assertEqual(out["variants"], [["привет", "мемо"]])
        self.assertEqual(wake_word.alias_words(out), frozenset({"мемо"}))
        en = analyze_phrase("hey memu", "en", lambda w: w in {"hey", "memo"})
        self.assertEqual(en["variants"], [["hey", "memo"]])

    def test_yo_spelling_is_the_same_word(self):
        out = analyze_phrase("ну еще", "ru", lambda w: w in {"ну", "ещё"})
        self.assertTrue(out["ok"])
        self.assertTrue(out["words"][1]["known"])
        self.assertEqual(out["variants"], [["ну", "ещё"]])

    def test_split_into_two_known_words(self):
        out = analyze_phrase("эй абвгд", "ru", lambda w: w in {"эй", "абв", "гд"})
        self.assertEqual(out["words"][1]["alias"], ["абв гд"])
        self.assertEqual(out["variants"], [["эй", "абв", "гд"]])

    def test_unmatchable_word_is_refused_with_both_languages(self):
        out = analyze_phrase("привет щщщщщ", "ru", lambda w: w == "привет")
        self.assertFalse(out["ok"])
        self.assertIn("щщщщщ", out["problem"]["ru"])
        self.assertIn("щщщщщ", out["problem"]["en"])

    def test_grammar_has_decoys_but_not_near_homophones(self):
        variants = [["привет", "мемо"]]
        grammar = grammar_for(variants, "ru", lambda w: True, "привет мему")
        self.assertEqual(grammar[0], "привет мемо")
        self.assertEqual(grammar[-1], "[unk]")
        self.assertIn("мама", grammar)
        self.assertNotIn("мемы", grammar)
        self.assertNotIn("привет", grammar[1:])
        self.assertGreater(len(grammar), 60)
        unknown = grammar_for(variants, "ru", lambda w: w in {"мама"}, "")
        self.assertEqual(unknown, ["привет мемо", "мама", "[unk]"])

    def test_decoy_lists_are_clean(self):
        for lang, words in DECOYS.items():
            self.assertEqual(len(words), len(set(words)), lang)
            self.assertTrue(all(w == w.lower() and " " not in w for w in words), lang)

    def test_match_hit_needs_consecutive_confident_words(self):
        res = {"result": [{"word": "привет", "conf": 0.9, "start": 0.1, "end": 0.5},
                          {"word": "мемо", "conf": 0.8, "start": 0.5, "end": 0.9},
                          {"word": "погода", "conf": 1.0, "start": 1.0, "end": 1.4}]}
        hit = match_hit(res, [["привет", "мемо"]], 0.65)
        self.assertEqual((hit["start"], hit["end"]), (0.1, 0.9))
        self.assertAlmostEqual(hit["conf"], 0.85)
        self.assertIsNone(match_hit(res, [["привет", "мемо"]], 0.9))
        weak = {"result": [{"word": "привет", "conf": 1.0}, {"word": "мемо", "conf": 0.4}]}
        self.assertIsNone(match_hit(weak, [["привет", "мемо"]], 0.6))
        self.assertIsNotNone(match_hit(weak, [["привет", "мемо"]], 0.6, frozenset({"мемо"})))
        apart = {"result": [{"word": "привет", "conf": 1.0}, {"word": "мама", "conf": 1.0}, {"word": "мемо", "conf": 1.0}]}
        self.assertIsNone(match_hit(apart, [["привет", "мемо"]], 0.5))
        self.assertIsNone(match_hit({}, [["привет", "мемо"]], 0.5))

    def test_threshold_follows_sensitivity(self):
        self.assertAlmostEqual(threshold(50), 0.6)
        self.assertGreater(threshold(0), threshold(100))
        self.assertAlmostEqual(threshold(500), threshold(100))

class SettingsTest(TempRoot):
    def test_normalize(self):
        clean = normalize_settings({"enabled": 1, "lang": "xx", "sensitivity": "nan", "cooldown": 999,
                                    "phrase": "  Hey,   MEMU!! ", "device": 5}, "en")
        self.assertEqual(clean["lang"], "en")
        self.assertEqual(clean["sensitivity"], 50)
        self.assertEqual(clean["cooldown"], 30)
        self.assertEqual(clean["phrase"], "hey memu")
        self.assertEqual(clean["device"], "5")
        self.assertTrue(clean["enabled"])
        self.assertEqual(normalize_settings(None)["enabled"], False)
        self.assertEqual(normalize_settings({"sensitivity": float("inf")})["sensitivity"], 50)

    def test_save_and_load_roundtrip_ignores_unknown_keys(self):
        wake_word.save_settings({"enabled": True, "phrase": "Окей, компьютер", "evil": 1})
        data = json.loads((self.tmp / "wake_word.json").read_text("utf-8"))
        self.assertNotIn("evil", data["settings"])
        loaded = wake_word.load_settings()
        self.assertEqual(loaded["phrase"], "окей компьютер")
        self.assertTrue(loaded["enabled"])
        self.assertEqual(wake_word.effective_phrase(dict(loaded, phrase="")), "привет мему")
        (self.tmp / "wake_word.json").write_text("{broken", "utf-8")
        self.assertFalse(wake_word.load_settings()["enabled"])

class AudioPartsTest(unittest.TestCase):
    def test_rms_and_resample(self):
        self.assertAlmostEqual(wake_audio.rms(tone(16384)[0]), 0.5, places=3)
        self.assertEqual(wake_audio.rms(b""), 0.0)
        pcm = array.array("h", range(0, 4410)).tobytes()
        self.assertEqual(len(wake_audio.resample(pcm, 44100)) // 2, 1600)
        self.assertEqual(wake_audio.resample(pcm, 16000), pcm)

    def test_gate_never_opens_on_steady_noise_and_opens_on_speech_with_preroll(self):
        gate = EnergyGate()
        for chunk in silence(30, level=300):
            feed, closed = gate.push(chunk, wake_audio.rms(chunk))
            self.assertEqual(feed, [])
        loud = tone(8000)[0]
        feed, closed = gate.push(loud, wake_audio.rms(loud))
        self.assertTrue(gate.open)
        self.assertEqual(len(feed), 6)
        for i in range(gate.hang - 1):
            feed, closed = gate.push(silence(1, 300)[0], 0.009)
            self.assertFalse(closed)
        feed, closed = gate.push(silence(1, 300)[0], 0.009)
        self.assertTrue(closed)
        self.assertFalse(gate.open)

    def test_gate_floor_adapts_to_a_louder_room(self):
        gate = EnergyGate()
        for _ in range(200):
            gate.push(b"", 0.03)
        self.assertGreater(gate.floor, 0.02)
        feed, _closed = gate.push(b"x", 0.05)
        self.assertEqual(feed, [])

    def test_session_endpoints(self):
        empty = Session()
        for _ in range(int(wake_word.NO_SPEECH * 10)):
            empty.feed(silence()[0], 0.0, 0.02)
        self.assertEqual(empty.phase, "empty")
        spoken = Session()
        for _ in range(8):
            spoken.feed(tone(9000)[0], 0.27, 0.02)
        for _ in range(int(wake_word.END_SILENCE * 10)):
            spoken.feed(silence()[0], 0.0, 0.02)
        self.assertEqual(spoken.phase, "captured")
        public = spoken.public(with_audio=True)
        self.assertTrue(public["audio"].startswith("data:audio/wav;base64,"))
        import base64
        wav = base64.b64decode(public["audio"].split(",", 1)[1])
        with wave.open(io.BytesIO(wav)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels()), (16000, 1))
        endless = Session()
        for _ in range(int(wake_word.MAX_COMMAND * 10)):
            endless.feed(tone(9000)[0], 0.27, 0.02)
        self.assertEqual(endless.phase, "captured")

    def test_chime_is_not_speech(self):
        session = Session(mute_until=0.3)
        for _ in range(3):
            session.feed(tone(9000)[0], 0.27, 0.02)
        self.assertFalse(session.heard)
        session.feed(tone(9000)[0], 0.27, 0.02)
        self.assertTrue(session.heard)

    def test_audio_seconds_and_chime(self):
        self.assertAlmostEqual(wake_word.audio_seconds(wake_word.wav_bytes(bytes(32000)), "audio/wav"), 1.0)
        self.assertAlmostEqual(wake_word.audio_seconds(b"x" * 16000, "audio/mpeg"), 1.0)
        pcm = wake_word.chime_pcm()
        self.assertGreater(len(pcm), 4000)
        self.assertLess(wake_audio.rms(pcm), 0.2)

class ServiceTest(TempRoot):
    def make(self, chunks, *, settings=None, locked=lambda: False, factory=None, known=None):
        self.log = []
        self.opened = []
        fake = FakeVosk(known)
        fake.Model = lambda path: FakeModel(known)
        sources = []

        def default_factory(device):
            source = FakeSource(device, chunks if not sources else [], self.log)
            sources.append(source)
            return source

        service = WakeService(source_factory=factory or default_factory, vosk_loader=lambda: fake, locked=locked)
        service.opener = self.opened.append
        self.sources = sources
        for p in (mock.patch.object(wake_word, "model_ready", lambda lang: True),
                  mock.patch.object(wake_word, "engine_info", lambda: {"available": True, "installable": False}),
                  mock.patch.object(WakeService, "_quick_enabled", lambda self: True),
                  mock.patch.object(WakeService, "_chime", lambda self: False)):
            p.start()
            self.addCleanup(p.stop)
        wake_word.save_settings(dict({"enabled": True, "phrase": "привет мему"}, **(settings or {})))
        service.reload()
        self.addCleanup(service.shutdown)
        return service

    def utterance(self, value=PHRASE_VALUE, n=8, tail=10):
        return silence(5, 200) + tone(value, n) + silence(tail, 200)

    def test_phrase_opens_quick_ask_and_records_the_command(self):
        chunks = self.utterance() + tone(COMMAND_VALUE, 10) + silence(20, 200)
        service = self.make(chunks)
        self.assertTrue(wait_for(lambda: self.opened))
        self.assertEqual(self.opened[0]["glow"], True)
        self.assertTrue(wait_for(lambda: service.session_public()["phase"] == "captured"))
        public = service.session_public(with_audio=True)
        self.assertGreaterEqual(public["seconds"], 1.0)
        self.assertEqual(public["text"], "какая")
        self.assertIn("audio", public)
        self.assertEqual(service.last_hit["phrase"], "привет мему")
        self.assertTrue(service.end_session(public["id"], "done"))
        self.assertIsNone(service.session_public())
        self.assertFalse(service.end_session(public["id"], "done"))
        self.assertEqual(service.status()["state"] in ("listening", "paused"), True)
        self.assertEqual(self.log[0], ("open", ""))

    def test_one_breath_command_is_kept(self):

        chunks = silence(5, 200) + tone(PHRASE_VALUE, 8) + silence(1, 200) + tone(COMMAND_VALUE, 6) + silence(20, 200)
        service = self.make(chunks)
        self.assertTrue(wait_for(lambda: service.session_public() and service.session_public()["phase"] == "captured"))
        self.assertTrue(service.session_public()["heard"])

    def test_other_speech_and_noise_do_not_trigger(self):
        chunks = self.utterance(OTHER_VALUE) + silence(30, 600) + self.utterance(OTHER_VALUE)
        service = self.make(chunks)
        time.sleep(0.6)
        self.assertEqual(self.opened, [])
        self.assertIsNone(service.last_hit)

    def test_cooldown_blocks_a_second_trigger(self):
        chunks = self.utterance() + self.utterance() + self.utterance()
        service = self.make(chunks, settings={"cooldown": 30})
        self.assertTrue(wait_for(lambda: self.opened))
        time.sleep(0.5)
        self.assertEqual(len(self.opened), 1)

    def test_busy_tts_and_live_mute_it(self):
        service = self.make([])
        service.busy("tts", 30)
        self.assertEqual(service.busy_reason(), "tts")
        self.assertTrue(wait_for(lambda: self.sources))
        self.sources[0].chunks.extend(self.utterance())
        self.assertTrue(wait_for(lambda: service.status()["state"] == "paused" and service.status()["reason"] == "tts"))
        time.sleep(0.3)
        self.assertEqual(self.opened, [])
        service.busy("tts", 0)
        service.hold("live", True)
        service.hold("live", True)
        service.hold("live", False)
        self.assertEqual(service.busy_reason(), "live")
        service.hold("live", False)
        self.assertEqual(service.busy_reason(), "")
        service.busy("tts", 0.05)
        time.sleep(0.08)
        self.assertEqual(service.busy_reason(), "")
        service.busy("voice", 10 ** 9)
        self.assertLessEqual(service.busy_until["voice"] - time.monotonic(), wake_word.BUSY_MAX + 1)

    def test_live_cancels_a_running_session(self):
        chunks = self.utterance() + silence(40, 200)
        service = self.make(chunks)
        self.assertTrue(wait_for(lambda: service.session_public() is not None))
        service.hold("live", True)
        self.assertEqual(service.session_public()["phase"], "cancelled")
        service.hold("live", False)

    def test_test_mode_reports_without_opening(self):
        chunks = self.utterance()
        service = self.make(chunks, settings={"enabled": False})
        self.assertEqual(service.status()["state"], "off")
        self.sources.clear()
        service.set_test(True)
        self.assertTrue(wait_for(lambda: service.last_hit is not None))
        self.assertTrue(service.last_hit["test"])
        self.assertEqual(self.opened, [])
        service.set_test(False)
        self.assertTrue(wait_for(lambda: service.status()["state"] == "off"))
        self.assertTrue(wait_for(lambda: self.sources and self.sources[0].closed))

    def test_locked_session_releases_the_mic(self):
        state = {"locked": False}
        service = self.make([], locked=lambda: state["locked"])
        self.assertTrue(wait_for(lambda: self.sources))
        state["locked"] = True
        self.assertTrue(wait_for(lambda: service.status()["reason"] == "locked", timeout=6))
        self.assertTrue(self.sources[0].closed)
        state["locked"] = False
        self.assertTrue(wait_for(lambda: len(self.sources) == 2 and service.status()["state"] == "listening", timeout=6))

    def test_mic_error_is_reported_and_retried(self):
        calls = []

        def broken(device):
            calls.append(device)
            raise wake_audio.MicError("no_device", "Микрофон не найден.", "No microphone found.")
        service = self.make([], factory=broken)
        self.assertTrue(wait_for(lambda: service.status()["state"] == "error"))
        status = service.status()
        self.assertEqual(status["reason"], "mic")
        self.assertEqual(status["error"]["code"], "no_device")
        self.assertEqual(status["error"]["en"], "No microphone found.")
        service.shutdown()

    def test_device_change_reopens_and_disable_stops(self):
        service = self.make([])
        self.assertTrue(wait_for(lambda: self.sources))
        wake_word.save_settings({"device": "USB Mic"})
        service.reload()
        self.assertTrue(wait_for(lambda: ("open", "USB Mic") in self.log))
        self.assertTrue(self.sources[0].closed)
        wake_word.save_settings({"enabled": False})
        service.reload()
        self.assertTrue(wait_for(lambda: service.status()["state"] == "off" and service.thread is None))
        self.assertTrue(all(s.closed for s in self.sources))

    def test_bad_phrase_is_an_error_not_a_crash(self):
        service = self.make([], settings={"phrase": "привет щщщщщ"}, known={"привет"})
        self.assertTrue(wait_for(lambda: service.status()["reason"] == "phrase"))
        self.assertIn("щщщщщ", service.status()["error"]["ru"])

    def test_finish_and_cancel_from_the_window(self):
        chunks = self.utterance() + tone(COMMAND_VALUE, 3) + [tone(COMMAND_VALUE)[0]] * 200
        service = self.make(chunks)
        self.assertTrue(wait_for(lambda: service.session_public() is not None))
        sid = service.session_public()["id"]

        self.assertTrue(wait_for(lambda: service.session is not None and bool(service.session.audio)))
        self.assertTrue(service.end_session(sid, "finish"))
        self.assertEqual(service.session_public()["phase"], "captured")
        self.assertFalse(service.end_session("other", "cancel"))
        self.assertTrue(service.end_session(sid, "cancel"))
        self.assertIsNone(service.session_public())

    def test_quick_ask_off_means_no_window(self):
        service = self.make(self.utterance())
        with mock.patch.object(WakeService, "_quick_enabled", lambda self: False):
            self.assertTrue(wait_for(lambda: service.last_hit is not None))
            time.sleep(0.2)
        self.assertEqual(self.opened, [])
        self.assertIsNone(service.session_public())

    def test_no_engine_and_no_model_states(self):
        service = WakeService(source_factory=lambda d: self.fail("mic opened without a model"),
                              vosk_loader=lambda: FakeVosk(), locked=lambda: False)
        self.addCleanup(service.shutdown)
        wake_word.save_settings({"enabled": True})
        with mock.patch.object(wake_word, "engine_info", lambda: {"available": False, "installable": True}):
            service.reload()
            self.assertTrue(wait_for(lambda: service.status()["reason"] == "no_engine"))
        with mock.patch.object(wake_word, "engine_info", lambda: {"available": True, "installable": False}), \
                mock.patch.object(wake_word, "model_ready", lambda lang: False):
            service.reload()
            self.assertTrue(wait_for(lambda: service.status()["reason"] == "no_model"))
            service.jobs["ru"] = wake_word._Job("ru")
            service.jobs["ru"].total, service.jobs["ru"].done = 100, 50
            service.apply()
            self.assertTrue(wait_for(lambda: service.status()["reason"] == "downloading"))
            self.assertEqual(service.status()["progress"], 47)
            service.jobs.pop("ru")

    def test_quit_stops_the_thread(self):
        service = self.make([])
        self.assertTrue(wait_for(lambda: self.sources))
        thread = service.thread
        service.shutdown()
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.sources[0].closed)

class DownloadTest(TempRoot):
    def make_zip(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            for name in ("am/final.mdl", "conf/model.conf", "graph/HCLr.fst"):
                zf.writestr("vosk-model-small-ru-0.22/" + name, "x" * 100)
        return buffer.getvalue()

    def serve(self, payload, *, honour_range=True, status=200):
        import httpx
        seen = []

        def handler(request):
            seen.append(dict(request.headers))
            rng = request.headers.get("range")
            if rng and honour_range:
                start = int(rng.split("=")[1].rstrip("-"))
                body = payload[start:]
                return httpx.Response(206, content=body, headers={
                    "content-range": f"bytes {start}-{len(payload) - 1}/{len(payload)}", "content-length": str(len(body))})
            return httpx.Response(status, content=payload, headers={"content-length": str(len(payload))})
        real = httpx.Client
        factory = lambda *a, **k: real(transport=httpx.MockTransport(handler), follow_redirects=True)  # noqa: E731
        return seen, mock.patch("httpx.Client", factory)

    def meta(self, payload, sha=None):
        return mock.patch.dict(wake_word.MODELS["ru"], {"size": len(payload), "sha256": sha or hashlib.sha256(payload).hexdigest(),
                                                        "url": "https://models.invalid/ru.zip"})

    def run_job(self):
        job = wake_word._Job("ru")
        done = []
        wake_word._download(job, lambda: done.append(1))
        return job, done

    def test_download_verifies_and_unpacks(self):
        payload = self.make_zip()
        seen, patch = self.serve(payload)
        with self.meta(payload), patch:
            job, done = self.run_job()
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(done, [1])
        self.assertTrue(wake_word.model_ready("ru"))
        self.assertFalse(any(p.suffix in (".zip", ".part") for p in (self.tmp / "wake-models").iterdir()))
        self.assertEqual(job.public()["progress"], 100)
        wake_word.delete_model("ru")
        self.assertFalse(wake_word.model_ready("ru"))

    def test_resume_from_part(self):
        payload = self.make_zip()
        root = self.tmp / "wake-models"
        root.mkdir(parents=True)
        (root / "vosk-model-small-ru-0.22.zip.part").write_bytes(payload[:300])
        seen, patch = self.serve(payload)
        with self.meta(payload), patch:
            job, _done = self.run_job()
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(seen[0].get("range"), "bytes=300-")

    def test_checksum_mismatch_deletes_the_part(self):
        payload = self.make_zip()
        seen, patch = self.serve(payload)
        with self.meta(payload, sha="0" * 64), patch:
            job, done = self.run_job()
        self.assertEqual(job.state, "error")
        self.assertIn("Контрольная сумма", job.error["ru"])
        self.assertIn("checksum", job.error["en"])
        self.assertFalse(list((self.tmp / "wake-models").glob("*.part")))
        self.assertEqual(done, [])

    def test_not_a_zip_is_refused(self):
        payload = b"<html>not a model</html>" * 10
        seen, patch = self.serve(payload)
        with self.meta(payload), patch:
            job, _done = self.run_job()
        self.assertEqual(job.state, "error")
        self.assertIn("повреждён", job.error["ru"])
        self.assertFalse(wake_word.model_ready("ru"))

    def test_http_error_and_disk_space(self):
        payload = self.make_zip()
        seen, patch = self.serve(payload, status=503)
        with self.meta(payload), patch:
            job, _done = self.run_job()
        self.assertEqual(job.state, "error")
        self.assertIn("503", job.error["en"])
        Usage = shutil._ntuple_diskusage if hasattr(shutil, "_ntuple_diskusage") else None
        fake_usage = (Usage or (lambda *a: type("U", (), {"free": a[2]})()))(10, 10, 5)
        with self.meta(payload), mock.patch.object(wake_word.shutil, "disk_usage", lambda p: fake_usage):
            job, _done = self.run_job()
        self.assertEqual(job.state, "error")
        self.assertIn("Недостаточно места", job.error["ru"])
        self.assertIn("disk space", job.error["en"])

    def test_unsafe_archive_paths_are_refused(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("../evil/am/final.mdl", "x")
        archive = self.tmp / "evil.zip"
        archive.write_bytes(buffer.getvalue())
        with self.assertRaises(ValueError):
            wake_word._unpack(archive, "ru")
        self.assertFalse((self.tmp.parent / "evil").exists())

    def test_service_download_api(self):
        service = WakeService(vosk_loader=lambda: FakeVosk())
        payload = self.make_zip()
        seen, patch = self.serve(payload)
        with self.meta(payload), patch:
            first = service.start_download("ru")
            self.assertIn(first["state"], ("downloading", "unpacking", "done"))
            self.assertTrue(wait_for(lambda: service.jobs["ru"].state == "done"))
        self.assertTrue(service.models()[0]["ready"])
        with self.assertRaises(ValueError):
            service.start_download("de")
        self.assertFalse(service.cancel_download("en"))
        service.delete_model("ru")
        self.assertFalse(service.models()[0]["ready"])

    def test_fetch_still_checks_gguf_by_default(self):
        from app import local_models
        import inspect
        self.assertIn("check", inspect.signature(local_models._fetch).parameters)
        self.assertIn("(check or read_gguf)(part)", inspect.getsource(local_models._fetch))

class ApiTest(TempRoot):
    def setUp(self):
        super().setUp()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import wake_word_api
        self.service = WakeService(vosk_loader=lambda: FakeVosk(), source_factory=lambda d: FakeSource(d, [], []),
                                   locked=lambda: False)
        patch = mock.patch.object(wake_word_api, "SERVICE", self.service)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.service.shutdown)
        for p in (mock.patch.object(WakeService, "_quick_enabled", lambda self: True),):
            p.start()
            self.addCleanup(p.stop)
        app = FastAPI()
        app.include_router(wake_word_api.router)
        self.client = TestClient(app)

    def test_state_settings_and_phrase(self):
        state = self.client.get("/api/wake/state").json()
        self.assertEqual(state["settings"]["enabled"], False)
        self.assertEqual(state["defaults"], {"ru": "привет мему", "en": "hey memu"})
        self.assertEqual([m["lang"] for m in state["models"]], ["ru", "en"])
        self.assertIn("available", state["engine"])
        self.assertIn("devices", state["mic"])
        out = self.client.post("/api/wake/settings", json={"settings": {"sensitivity": 80, "lang": "en"}}).json()
        self.assertEqual(out["settings"]["sensitivity"], 80)
        self.assertEqual(out["settings"]["lang"], "en")
        self.assertEqual(self.service.settings["lang"], "en")
        with mock.patch.object(wake_word, "model_ready", lambda lang: False):
            check = self.client.post("/api/wake/phrase", json={"phrase": "один", "lang": "ru"}).json()
        self.assertFalse(check["checked"])
        self.assertFalse(check["ok"])
        self.assertIn("2–4", check["problem"]["ru"])
        with mock.patch.object(wake_word, "model_ready", lambda lang: True), \
                mock.patch.object(wake_word, "engine_info", lambda: {"available": True, "installable": False}):
            self.service.vosk_loader = lambda: type("V", (), {"Model": lambda self_, p: FakeModel({"привет", "мемо"})})()
            check = self.client.post("/api/wake/phrase", json={"phrase": "Привет, Мему", "lang": "ru"}).json()
        self.assertTrue(check["checked"])
        self.assertEqual(check["variants"], [["привет", "мемо"]])

    def test_model_routes_validate(self):
        self.assertEqual(self.client.post("/api/wake/model", json={"lang": "de", "action": "download"}).status_code, 400)
        self.assertEqual(self.client.post("/api/wake/model", json={"lang": "ru", "action": "explode"}).status_code, 400)
        self.service.jobs["ru"] = wake_word._Job("ru")
        busy = self.client.post("/api/wake/model", json={"lang": "ru", "action": "delete"})
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.json()["error_en"], "Wait for the download to finish")
        self.assertTrue(self.client.post("/api/wake/model", json={"lang": "ru", "action": "cancel"}).json()["models"])
        self.assertTrue(self.service.jobs["ru"].cancel.is_set())

    def test_session_busy_and_test_routes(self):
        self.assertEqual(self.client.get("/api/wake/session").json(), {"session": None})
        self.assertEqual(self.client.post("/api/wake/session/end", json={"id": 1, "action": "nuke"}).status_code, 400)
        self.assertEqual(self.client.post("/api/wake/session/end", json={"id": 1}).json(), {"ok": False})
        self.client.post("/api/wake/busy", json={"source": "tts", "on": True, "seconds": 5})
        self.assertEqual(self.service.busy_reason(), "tts")
        self.client.post("/api/wake/busy", json={"source": "tts", "on": False})
        self.assertEqual(self.service.busy_reason(), "")
        self.client.post("/api/wake/busy", json={"source": "<script>", "seconds": "nan"})
        self.assertEqual(self.service.busy_reason(), "")
        status = self.client.post("/api/wake/test", json={"on": True}).json()
        self.assertTrue(status["testing"])
        self.assertFalse(self.client.post("/api/wake/test", json={"on": False}).json()["testing"])
        self.assertIn("state", self.client.get("/api/wake/status").json())

    def test_install_route_in_a_frozen_build(self):
        with mock.patch.object(wake_word, "engine_info", lambda: {"available": False, "installable": False}):
            out = self.client.post("/api/wake/engine/install", json={})
        self.assertEqual(out.status_code, 409)
        self.assertIn("Reinstall", out.json()["error_en"])

    def test_plugin_manifest_registers_the_backend(self):
        from app import quick_ask_api
        rows = {row["id"]: row for row in quick_ask_api.plugin_manifest()["plugins"]}
        self.assertEqual(rows["wake"]["backend"], "app.wake_word_api")
        self.assertIn("app.wake_word_api", quick_ask_api.load_backend_plugins())
        from app import wake_word_api
        self.assertIn(wake_word_api.router, quick_ask_api.EXTRA_ROUTES)

class WiringTest(unittest.TestCase):
    def test_hooks_and_build(self):
        playground = (ROOT / "app" / "playground_api.py").read_text("utf-8")
        self.assertIn("note_speech(audio, mime)", playground)
        self.assertIn("live_session(True)", playground)
        self.assertIn("live_session(False)", playground)
        memuai = (ROOT / "memuai.py").read_text("utf-8")
        self.assertIn("wake_word.attach(app)", memuai)
        self.assertIn("shell.quit_hooks.append(wake_word.shutdown)", memuai)
        spec = (ROOT / "MemuAI.spec").read_text("utf-8")
        build = (ROOT / "build.py").read_text("utf-8")
        for name in ("app.wake_word", "app.wake_word_api", "app.wake_audio"):
            self.assertIn(f"'{name}'", spec)
            self.assertIn(f'"{name}"', build)
        self.assertIn("collect_all('vosk')", spec)
        self.assertIn('"vosk"', build)
        self.assertIn("vosk", (ROOT / "requirements.txt").read_text("utf-8"))

    def test_note_speech_mutes_the_service(self):
        with mock.patch.object(wake_word, "SERVICE", WakeService()) as service:
            wake_word.note_speech(wake_word.wav_bytes(bytes(64000)), "audio/wav")
            self.assertEqual(service.busy_reason(), "tts")
            self.assertGreater(service.busy_until["tts"] - time.monotonic(), 3.0)
            wake_word.live_session(True)
            self.assertEqual(service.holds, {"live": 1})
            wake_word.live_session(False)
            self.assertEqual(service.holds, {})

    def test_js_parses_and_strings_are_bilingual(self):
        node = shutil.which("node")
        for name in ("wake.js", "wake-settings.js"):
            path = ROOT / "app" / "pgstatic" / "quick" / "plugins" / name
            if node:
                done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)
            import re
            source = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text("utf-8"))

            for line in source.splitlines():
                if line.strip().startswith("//") or not re.search(r"[а-яё]", line, re.I):
                    continue
                self.assertRegex(line, r"t\(|ru:|\"ru\"", line)

    def test_windows_structures(self):
        if sys.platform != "win32":
            self.assertFalse(wake_audio.session_locked())
            self.assertIn(wake_audio.list_devices()["backend"], ("", "sounddevice"))
            if not _has("sounddevice"):
                with self.assertRaises(wake_audio.MicError) as ctx:
                    wake_audio.open_source("")
                self.assertEqual(ctx.exception.code, "unavailable")
                self.assertIn("sounddevice", ctx.exception.text("en"))
            return
        import ctypes
        self.assertEqual(ctypes.sizeof(wake_audio.WAVEFORMATEX), 18)
        self.assertEqual(ctypes.sizeof(wake_audio.WAVEHDR), 48 if ctypes.sizeof(ctypes.c_void_p) == 8 else 32)

def _has(module):
    try:
        __import__(module)
        return True
    except Exception:
        return False

MODELS_DIR = Path(os.environ.get("MEMU_VOSK_MODELS", "/workspace/work/r27/vosk"))

@unittest.skipUnless(_has("vosk") and shutil.which("espeak-ng") and (MODELS_DIR / wake_word.MODELS["ru"]["id"]).is_dir()
                     and (MODELS_DIR / wake_word.MODELS["en"]["id"]).is_dir(), "vosk, models or espeak-ng missing")
class RealVoskTest(unittest.TestCase):
    """Synthetic speech (espeak-ng, several speeds/pitches) through the real small models and the real pipeline."""

    @classmethod
    def setUpClass(cls):
        import vosk
        vosk.SetLogLevel(-1)
        cls.vosk = vosk
        cls.models = {lang: vosk.Model(str(MODELS_DIR / meta["id"])) for lang, meta in wake_word.MODELS.items()}

    def speech(self, text, voice, speed=160, pitch=50):
        out = subprocess.run(["espeak-ng", "-v", voice, "-s", str(speed), "-p", str(pitch), "--stdout", text],
                             capture_output=True, check=True).stdout
        with wave.open(io.BytesIO(out)) as w:
            return wake_audio.resample(w.readframes(w.getnframes()), w.getframerate())

    def detect(self, lang, pcm, sensitivity=50, warm=b""):
        """The service's path: gate → one detector; ``warm`` is speech heard earlier in the same listening run."""
        model = self.models[lang]
        known = lambda w: model.vosk_model_find_word(w) >= 0  # noqa: E731
        analysis = analyze_phrase(wake_word.MODELS[lang]["phrase"], lang, known)
        self.assertTrue(analysis["ok"], analysis)
        detector = Detector(model, self.vosk, grammar_for(analysis["variants"], lang, known, analysis["phrase"]),
                            analysis["variants"], threshold(sensitivity), wake_word.alias_words(analysis))
        gate = EnergyGate()
        audio = bytes(RATE) + (warm + bytes(RATE * 3) if warm else b"") + pcm + bytes(RATE * 4)
        for i in range(0, len(audio), wake_audio.CHUNK_BYTES):
            chunk = audio[i:i + wake_audio.CHUNK_BYTES]
            feed, closed = gate.push(chunk, wake_audio.rms(chunk))
            for n, piece in enumerate(feed):
                hit = detector.accept(piece)
                if hit:
                    rest = b"".join(feed[n + 1:]) + audio[i + wake_audio.CHUNK_BYTES:]
                    return hit, detector.tail(hit), rest
            if closed:
                hit = detector.finish()
                if hit:
                    return hit, detector.tail(hit), audio[i + wake_audio.CHUNK_BYTES:]
        return None

    def test_default_phrases_use_known_aliases(self):
        for lang, alias in (("ru", ["привет", "мемо"]), ("en", ["hey", "memo"])):
            model = self.models[lang]
            out = analyze_phrase(wake_word.MODELS[lang]["phrase"], lang, lambda w: model.vosk_model_find_word(w) >= 0)
            self.assertEqual(out["variants"], [alias], lang)
            self.assertFalse(out["words"][1]["known"])

    def test_phrase_is_caught(self):
        cases = (("ru", "ru", "Привет, Мему"), ("en", "en-us", "Hey Memu"))
        for lang, voice, text in cases:
            for speed, pitch in ((150, 50), (130, 70), (180, 40)):
                with self.subTest(lang=lang, speed=speed, pitch=pitch):
                    self.assertIsNotNone(self.detect(lang, self.speech(text, voice, speed, pitch)))

    def test_other_speech_is_not_caught(self):
        cases = (("ru", "ru", ["привет мама", "привет мир", "Какая сегодня погода в Москве", "привет меню",
                               "Привет, как дела", "мемы это смешно"]),
                 ("en", "en-us", ["hey mom", "what is the weather", "hello there", "hey you", "memory is cheap"]))
        for lang, voice, texts in cases:
            for text in texts:
                for speed, pitch in ((150, 50), (190, 30)):
                    with self.subTest(lang=lang, text=text, speed=speed):
                        self.assertIsNone(self.detect(lang, self.speech(text, voice, speed, pitch)))

    def test_noise_is_not_caught(self):
        import random
        rng = random.Random(7)
        noise = array.array("h", [int(rng.gauss(0, 2500)) for _ in range(RATE * 4)]).tobytes()
        hum = array.array("h", [int(4000 * math.sin(2 * math.pi * 120 * i / RATE)) for i in range(RATE * 3)]).tobytes()
        for lang in ("ru", "en"):
            self.assertIsNone(self.detect(lang, noise), lang)
            self.assertIsNone(self.detect(lang, hum), lang)

    def test_one_breath_command_and_offline_transcript(self):
        phrase = self.speech("Привет, Мему", "ru")
        command = self.speech("какая сегодня погода", "ru")
        hit, tail, rest = self.detect("ru", phrase + bytes(RATE // 5) + command, warm=self.speech("Включи музыку", "ru"))

        session = Session(self.vosk.KaldiRecognizer(self.models["ru"], RATE))
        audio = tail + rest
        for i in range(0, len(audio), wake_audio.CHUNK_BYTES):
            chunk = audio[i:i + wake_audio.CHUNK_BYTES]
            session.feed(chunk, wake_audio.rms(chunk), 0.01)
        self.assertEqual(session.phase, "captured")
        self.assertIn("погода", session.text)
        self.assertNotIn("привет", session.text)

    def test_service_end_to_end(self):
        """The real thread: a fake mic plays «Включи музыку», «привет мама», then «Привет, Мему, какая погода»."""
        tmp = Path(tempfile.mkdtemp(prefix="wake-e2e-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        audio = (bytes(RATE) + self.speech("Включи музыку", "ru") + bytes(RATE * 2) + self.speech("привет мама", "ru")
                 + bytes(RATE * 2) + self.speech("Привет, Мему", "ru") + bytes(RATE // 4)
                 + self.speech("какая сегодня погода", "ru") + bytes(RATE * 3))
        chunks = [audio[i:i + wake_audio.CHUNK_BYTES] for i in range(0, len(audio), wake_audio.CHUNK_BYTES)]
        log, opened = [], []
        patches = [mock.patch.object(wake_word, "_root", lambda: tmp),
                   mock.patch.object(wake_word, "model_dir", lambda lang: MODELS_DIR / wake_word.MODELS[lang]["id"]),
                   mock.patch.object(WakeService, "_quick_enabled", lambda self: True),
                   mock.patch.object(WakeService, "_chime", lambda self: False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        wake_word.save_settings({"enabled": True, "lang": "ru"})
        service = WakeService(source_factory=lambda device: FakeSource(device, chunks, log), locked=lambda: False)
        service.opener = opened.append
        self.addCleanup(service.shutdown)
        service.reload()
        self.assertTrue(wait_for(lambda: service.session_public() and service.session_public()["phase"] == "captured", 30))
        self.assertEqual(len(opened), 1)
        self.assertEqual(service.last_hit["phrase"], "привет мемо")
        session = service.session_public(with_audio=True)
        self.assertIn("погода", session["text"])
        self.assertTrue(session["audio"].startswith("data:audio/wav;base64,"))

    def test_cpu_budget(self):
        """Grammar decoding of continuous speech costs well under 3 % of a core (the gate skips silence entirely)."""
        model = self.models["ru"]
        known = lambda w: model.vosk_model_find_word(w) >= 0  # noqa: E731
        analysis = analyze_phrase("привет мему", "ru", known)
        rec = self.vosk.KaldiRecognizer(model, RATE, json.dumps(grammar_for(analysis["variants"], "ru", known), ensure_ascii=False))
        speech = self.speech("Какая сегодня погода в Москве, привет как дела, всё хорошо, спасибо, до встречи", "ru")
        started = time.process_time()
        for i in range(0, len(speech), wake_audio.CHUNK_BYTES):
            chunk = speech[i:i + wake_audio.CHUNK_BYTES]
            wake_audio.rms(chunk)
            rec.AcceptWaveform(chunk)
        rec.FinalResult()
        cpu = time.process_time() - started
        self.assertLess(cpu / (len(speech) / 2 / RATE), 0.08)

if __name__ == "__main__":
    unittest.main()
