# -*- coding: utf-8 -*-
"""MemuMeet native system-audio capture: mixer, helper protocol, API routes.

The Windows WASAPI path cannot run in this Linux VM, so the helper is
exercised through its sine backend (virtual devices that play sine
waves): the HTTP protocol, the mixer, the session lifecycle, device
loss and the API routes are all real; only the sounddevice backend
itself is untested live (see fixlog-meet-audio.md).
"""
from __future__ import annotations

import json
import math
import os
import struct
import threading
import time
import unittest
import urllib.error
import urllib.request

os.environ.setdefault("MEMUAI_MEET_NATIVE_BACKEND", "sine")

from app import meet_native_audio as core
from app import meet_native_audio_api as api
from app import meet_native_audio_helper as helper


def _sine_pcm(freq=440.0, rate=48000, seconds=0.1, amplitude=0.5):
    frames = int(rate * seconds)
    out = bytearray()
    for index in range(frames):
        value = int(amplitude * 32767 * math.sin(2.0 * math.pi * freq * index / rate))
        out += struct.pack("<hh", value, value)
    return bytes(out)


class CoreTest(unittest.TestCase):
    def test_pcm_round_trip(self):
        raw = _sine_pcm()
        floats = core.pcm16_to_float(raw)
        self.assertEqual(len(floats), 2 * int(48000 * 0.1))
        self.assertAlmostEqual(max(abs(v) for v in floats), 0.5, places=2)
        back = core.float_to_pcm16(floats)
        self.assertEqual(len(back), len(raw))
        self.assertEqual(list(struct.unpack("<4h", back[:8])),
                         list(struct.unpack("<4h", raw[:8])))

    def test_resampler_44100_to_48000_keeps_frequency(self):
        resampler = core.LinearResampler(44100)
        resampler.push(core.pcm16_to_float(_sine_pcm(rate=44100, seconds=0.2)))
        out = resampler.pull(9600)
        # The last output frame needs one more input sample than the block
        # holds, so the resampler stops one frame short of the full 9600.
        self.assertTrue(9590 <= len(out) // 2 <= 9600, len(out) // 2)
        crossings = sum(1 for i in range(2, len(out), 2) if out[i - 2] < 0 <= out[i])
        self.assertTrue(80 <= crossings <= 96, crossings)  # 440 Hz * 0.2 s = 88

    def test_resampler_passthrough(self):
        resampler = core.LinearResampler(48000)
        resampler.push([0.25, -0.25] * 100)
        self.assertEqual(resampler.pull(100), [0.25, -0.25] * 100)

    def test_jitter_buffer_pads_and_drops(self):
        buffer = core.JitterBuffer()
        buffer.push([0.5] * 960)  # 480 frames of mono-ish data (half a frame)
        got = buffer.pull(960)
        self.assertEqual(len(got), 1920)
        self.assertEqual(got[0], 0.5)
        self.assertEqual(got[-1], 0.0)
        self.assertEqual(buffer.underruns, 480)
        buffer.push([0.5] * (core.SAMPLE_RATE * 2 * 2))  # 2 s: over the max depth
        self.assertGreater(buffer.drops, 0)
        # Overrun drops the oldest data down to the 100 ms target depth.
        self.assertEqual(buffer.depth_frames, 4800)

    def test_mixer_sums_gains_and_limits(self):
        mixer = core.Mixer()
        mixer.add_source("a", 48000, 0.5)
        mixer.add_source("b", 48000, 0.5)
        block = _sine_pcm(amplitude=0.9, seconds=0.02)
        mixer.feed("a", block, 48000)
        mixer.feed("b", block, 48000)
        frame, peak = mixer.mix_frame()
        self.assertEqual(len(frame), core.FRAME_BYTES)
        self.assertLessEqual(peak, 1.0)
        self.assertGreater(peak, 0.5)
        self.assertEqual(mixer.frames_mixed, 1)

    def test_mixer_silence_when_sources_starve(self):
        mixer = core.Mixer()
        mixer.add_source("a", 48000, 1.0)
        frame, peak = mixer.mix_frame()
        self.assertEqual(len(frame), core.FRAME_BYTES)
        self.assertEqual(peak, 0.0)
        self.assertEqual(frame, b"\x00" * core.FRAME_BYTES)

    def test_mixer_different_rates(self):
        mixer = core.Mixer()
        mixer.add_source("a", 44100, 1.0)
        mixer.add_source("b", 48000, 1.0)
        mixer.feed("a", _sine_pcm(rate=44100, seconds=0.05), 44100)
        mixer.feed("b", _sine_pcm(seconds=0.05), 48000)
        frame, peak = mixer.mix_frame()
        self.assertEqual(len(frame), core.FRAME_BYTES)
        self.assertGreater(peak, 0.1)

    def test_upmix_to_stereo(self):
        mono = struct.pack("<4h", 100, -200, 300, -400)
        out = core.upmix_to_stereo(mono, 1)
        self.assertEqual(list(struct.unpack("<8h", out)),
                         [100, 100, -200, -200, 300, 300, -400, -400])
        stereo = struct.pack("<4h", 1, 2, 3, 4)
        self.assertEqual(core.upmix_to_stereo(stereo, 2), stereo)
        quad = struct.pack("<8h", 1, 2, 3, 4, 5, 6, 7, 8)
        self.assertEqual(list(struct.unpack("<4h", core.upmix_to_stereo(quad, 4))),
                         [1, 2, 5, 6])
        self.assertEqual(core.upmix_to_stereo(b"", 1), b"")
        self.assertEqual(core.upmix_to_stereo(b"\x01", 1), b"")  # odd byte dropped

    def test_portaudio_missing_message(self):
        # A missing PortAudio DLL is a different failure from a missing
        # package: the UI text must say so (fixlog-meet-audio2.md, F2).
        err = core.NativeAudioError("portaudio_missing", "PortAudio library not found")
        self.assertEqual(err.code, "portaudio_missing")
        self.assertIn("PortAudio", err.message_ru)
        self.assertIn("PortAudio library not found", err.message_ru)
        self.assertNotEqual(err.message_ru, core.NativeAudioError("no_backend").message_ru)

    def test_clean_selection(self):
        ids, gains = core.clean_selection(["b", "a", "b", "ghost"], {"a": 2.0, "b": 0.25}, ["a", "b"])
        self.assertEqual(ids, ["b", "a"])
        self.assertEqual(gains, {"a": 1.0, "b": 0.25})
        with self.assertRaises(core.NativeAudioError) as caught:
            core.clean_selection(["ghost"], {}, ["a"])
        self.assertEqual(caught.exception.code, "invalid_selection")
        with self.assertRaises(core.NativeAudioError):
            core.clean_device_id("../escape")
        self.assertEqual(core.clean_device_id("sd:3"), "sd:3")


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.server = helper.serve_in_thread(port=0, backend="sine")
        self.base = self.server.base_url
        self.token = self.server.token

    def tearDown(self):
        self.server.stop()

    def _request(self, method, path, body=None, token=True):
        url = self.base + path + ("?token=" + self.token if token else "")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_health_and_devices(self):
        status, body = self._request("GET", "/health", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["ok"], True)
        status, body = self._request("GET", "/devices")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["backend"], "sine")
        self.assertEqual(len(payload["devices"]), 2)
        self.assertTrue(payload["devices"][0]["is_default"])

    def test_token_required(self):
        status, _body = self._request("GET", "/devices", token=False)
        self.assertEqual(status, 401)

    def test_session_stream_and_events(self):
        status, body = self._request("POST", "/sessions", {"devices": ["sine-a", "sine-b"]})
        self.assertEqual(status, 200, body)
        session = json.loads(body)
        self.assertEqual(session["sample_rate"], 48000)
        self.assertEqual(session["channels"], 2)
        session_id = session["session"]

        frames = bytearray()
        events = []

        def read_events():
            request = urllib.request.Request(
                self.base + "/sessions/%s/events?token=%s" % (session_id, self.token))
            with urllib.request.urlopen(request, timeout=10) as response:
                buffer = b""
                while True:
                    chunk = response.read(1024)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n\n" in buffer:
                        raw, buffer = buffer.split(b"\n\n", 1)
                        text = raw.decode("utf-8", "replace")
                        if text.startswith("event: level"):
                            events.append(json.loads(text.split("data: ", 1)[1]))
                        if len(events) >= 2:
                            return

        thread = threading.Thread(target=read_events, daemon=True)
        thread.start()
        request = urllib.request.Request(
            self.base + "/sessions/%s/stream?token=%s" % (session_id, self.token))
        with urllib.request.urlopen(request, timeout=10) as response:
            while len(frames) < core.FRAME_BYTES * 10:
                chunk = response.read(core.FRAME_BYTES)
                if not chunk:
                    break
                frames += chunk
        thread.join(timeout=8)
        self.assertGreaterEqual(len(frames), core.FRAME_BYTES * 10)
        self.assertGreater(max(abs(v) for v in struct.unpack("<%dh" % (len(frames) // 2), bytes(frames))), 1000)
        self.assertGreaterEqual(len(events), 1)
        self.assertIn("sine-a", events[0]["levels"])

        status, _body = self._request("POST", "/sessions/%s/gain" % session_id,
                                      {"device": "sine-a", "gain": 0.0})
        self.assertEqual(status, 200)
        status, _body = self._request("POST", "/sessions/%s/stop" % session_id, {})
        self.assertEqual(status, 200)

    def test_second_session_is_busy_and_bad_device_is_400(self):
        status, body = self._request("POST", "/sessions", {"devices": ["sine-a"]})
        self.assertEqual(status, 200, body)
        session_id = json.loads(body)["session"]
        status, _body = self._request("POST", "/sessions", {"devices": ["sine-b"]})
        self.assertEqual(status, 409)
        status, _body = self._request("POST", "/sessions/%s/stop" % session_id, {})
        self.assertEqual(status, 200)
        status, body = self._request("POST", "/sessions", {"devices": ["ghost"]})
        self.assertEqual(status, 400)
        self.assertIn("invalid_selection", body.decode())

    def test_stream_survives_reader_disconnect(self):
        status, body = self._request("POST", "/sessions", {"devices": ["sine-a"]})
        session_id = json.loads(body)["session"]
        request = urllib.request.Request(
            self.base + "/sessions/%s/stream?token=%s" % (session_id, self.token))
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertTrue(len(response.read(core.FRAME_BYTES)) > 0)
        # The reader went away: the session must still accept a new reader.
        time.sleep(0.2)
        request = urllib.request.Request(
            self.base + "/sessions/%s/stream?token=%s" % (session_id, self.token))
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertTrue(len(response.read(core.FRAME_BYTES)) > 0)
        self._request("POST", "/sessions/%s/stop" % session_id, {})

    def test_quiet_session_survives_silence(self):
        # F1 regression: a WASAPI loopback endpoint delivers NO callbacks
        # while nothing plays. The old code marked such a device lost
        # after 5 s and stopped the whole session; a quiet session must
        # now live on (the mixer pads silence) and keep streaming.
        server = helper.NativeAudioServer(port=0, backend="sine")
        server.backend = helper.SineBackend(
            [("quiet", "Quiet monitor", 440.0, 48000, "output", True)])
        server.start()
        try:
            request = urllib.request.Request(
                server.base_url + "/devices?token=" + server.token)
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read())
            self.assertEqual([d["id"] for d in payload["devices"]], ["quiet"])

            def post(path, payload_body):
                req = urllib.request.Request(
                    server.base_url + path + "?token=" + server.token,
                    data=json.dumps(payload_body).encode(), method="POST")
                req.add_header("Content-Type", "application/json")
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        return resp.status, resp.read()
                except urllib.error.HTTPError as exc:
                    return exc.code, exc.read()

            status, body = post("/sessions", {"devices": ["quiet"]})
            self.assertEqual(status, 200, body)
            session_id = json.loads(body)["session"]

            seen = []

            def collect_events():
                req = urllib.request.Request(
                    server.base_url + "/sessions/%s/events?token=%s" % (session_id, server.token))
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        buffer = b""
                        deadline = time.monotonic() + 7.0
                        while time.monotonic() < deadline:
                            chunk = resp.read(1024)
                            if not chunk:
                                break
                            buffer += chunk
                            while b"\n\n" in buffer:
                                raw, buffer = buffer.split(b"\n\n", 1)
                                for line in raw.decode("utf-8", "replace").split("\n"):
                                    if line.startswith("event:"):
                                        seen.append(line[6:].strip())
                except Exception:
                    pass

            thread = threading.Thread(target=collect_events, daemon=True)
            thread.start()
            req = urllib.request.Request(
                server.base_url + "/sessions/%s/stream?token=%s" % (session_id, server.token))
            with urllib.request.urlopen(req, timeout=10) as resp:
                first = resp.read(core.FRAME_BYTES * 5)
                self.assertEqual(len(first), core.FRAME_BYTES * 5)
                time.sleep(6.5)  # past the old 5 s death sentence
                later = resp.read(core.FRAME_BYTES * 5)
                self.assertEqual(len(later), core.FRAME_BYTES * 5)
                # Silence, not signal — but frames must keep flowing.
                self.assertEqual(set(later), {0})
            thread.join(timeout=9)
            self.assertNotIn("device-lost", seen)
            self.assertNotIn("error", seen)
            # The session is still alive after 6.5 s of pure silence.
            session = server.get_session(session_id)
            self.assertFalse(session.stopped)
            post("/sessions/%s/stop" % session_id, {})
        finally:
            server.stop()

    def test_input_device_in_session(self):
        # Microphones are native devices too (kind "input"): a session
        # with an input device mixes its tone into the same stream.
        server = helper.NativeAudioServer(port=0, backend="sine")
        server.backend = helper.SineBackend([
            ("mic-1", "Virtual microphone", 330.0, 48000, "input", False),
            ("sine-a", "Virtual monitor A", 440.0, 48000, "output", False),
        ])
        server.start()
        try:
            request = urllib.request.Request(
                server.base_url + "/devices?token=" + server.token)
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read())
            kinds = {d["id"]: d["kind"] for d in payload["devices"]}
            self.assertEqual(kinds, {"mic-1": "input", "sine-a": "output"})
            req = urllib.request.Request(
                server.base_url + "/sessions?token=" + server.token,
                data=json.dumps({"devices": ["mic-1", "sine-a"]}).encode(), method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as response:
                body = json.loads(response.read())
            session_id = body["session"]
            req = urllib.request.Request(
                server.base_url + "/sessions/%s/stream?token=%s" % (session_id, server.token))
            with urllib.request.urlopen(req, timeout=10) as response:
                frames = response.read(core.FRAME_BYTES * 10)
            self.assertEqual(len(frames), core.FRAME_BYTES * 10)
            self.assertGreater(max(abs(v) for v in struct.unpack("<%dh" % (len(frames) // 2), frames)), 1000)
            req = urllib.request.Request(
                server.base_url + "/sessions/%s/stop?token=%s" % (session_id, server.token),
                data=b"{}", method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10):
                pass
        finally:
            server.stop()

    def test_partial_open_closes_opened_streams(self):
        # F5: when the second device fails to open, the first device's
        # stream must be closed instead of leaking a live capture.
        class FlakyBackend(helper.SineBackend):
            def __init__(self):
                super().__init__([("ok", "OK device", 440.0, 48000),
                                  ("bad", "Bad device", 660.0, 48000)])
                self.closed = []

            def open(self, device_id, rate, callback):
                if device_id == "bad":
                    raise helper.NativeAudioError("capture_failed", "boom")
                handle = super().open(device_id, rate, callback)
                real_close = handle.close

                def close_and_record(_handle=handle, _close=real_close, _self=self):
                    _self.closed.append(device_id)
                    _close()

                handle.close = close_and_record
                return handle

        server = helper.NativeAudioServer(port=0, backend="sine")
        flaky = FlakyBackend()
        server.backend = flaky
        server.start()
        try:
            with self.assertRaises(helper.NativeAudioError) as caught:
                server.create_session(["ok", "bad"], {})
            self.assertEqual(caught.exception.code, "capture_failed")
            self.assertEqual(flaky.closed, ["ok"])
        finally:
            server.stop()

    def test_describe_backend(self):
        info = helper.describe_backend(
            helper.NullBackend("portaudio_missing", "PortAudio library not found"))
        self.assertEqual(info["name"], "none")
        self.assertEqual(info["reason"], "portaudio_missing")
        self.assertEqual(info["detail"], "PortAudio library not found")
        self.assertIn("installed", info)
        self.assertIn("portaudio", info)
        self.assertIn("portaudio", helper.describe_backend(helper.SineBackend()))

    def test_null_backend_reports_no_devices(self):
        server = helper.serve_in_thread(port=0, backend="none")
        try:
            request = urllib.request.Request(
                server.base_url + "/devices?token=" + server.token)
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read())
            self.assertEqual(payload["backend"], "none")
            self.assertEqual(payload["devices"], [])
        finally:
            server.stop()


class ApiTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self._app = FastAPI()
        api.register(self._app)
        self.client = TestClient(self._app, base_url="http://127.0.0.1")

    def tearDown(self):
        api.MANAGER.stop()

    def test_status_start_stop(self):
        response = self.client.get("/api/pg/meet/native-audio/status")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["available"], payload)
        self.assertEqual(payload["backend"], "sine")
        self.assertEqual(len(payload["devices"]), 2)
        # Diagnostics ride along with the status: sounddevice state for
        # the Settings diagnostics section.
        self.assertIn("sounddevice", payload)
        self.assertIn("installed", payload["sounddevice"])
        self.assertIn("portaudio", payload["sounddevice"])
        response = self.client.post("/api/pg/meet/native-audio/start")
        self.assertEqual(response.status_code, 200, response.text)
        info = response.json()
        self.assertTrue(info["port"] > 0)
        self.assertTrue(info["token"])
        self.assertIn("127.0.0.1", info["base_url"])
        response = self.client.post("/api/pg/meet/native-audio/stop")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
