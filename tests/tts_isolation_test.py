"""Offline regression tests. Uses real child processes; no Piper/model download.
python -m unittest discover -s tests -p 'tts_isolation_test.py' -v
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import tts_process, tts_worker


class TTSIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / 'voice.onnx'; self.model.write_bytes(b'fixture')
        self.config = self.root / 'voice.onnx.json'; self.config.write_text('{}')
        self.jobs = []

    def tearDown(self):
        self.temp.cleanup()

    def command(self, code):
        def build(job):
            self.jobs.append(job)
            return [sys.executable, '-c', code, str(job)]
        return build

    def speak(self, **kwargs):
        return tts_process.synthesize_isolated('Привет. Hello.', self.model, self.config, **kwargs)

    def test_native_process_exit_does_not_kill_parent_and_next_call_recovers(self):
        with patch.object(tts_process, 'worker_command', self.command('import os; os._exit(23)')):
            with self.assertRaises(tts_process.LocalTTSError) as caught:
                self.speak()
        self.assertEqual(caught.exception.code, 'tts_worker_crashed')
        self.assertTrue(all(not job.exists() for job in self.jobs))
        self.test_success_returns_valid_wave_and_cleans_job()

    def test_timeout_kills_worker_and_cleans_private_files(self):
        with patch.object(tts_process, 'worker_command', self.command('import time; time.sleep(20)')):
            with self.assertRaises(tts_process.LocalTTSError) as caught:
                self.speak(timeout=.1)
        self.assertEqual(caught.exception.code, 'tts_timeout')
        self.assertTrue(all(not job.exists() for job in self.jobs))
        self.assertTrue(tts_process._SYNTHESIS_SLOT.acquire(False))
        tts_process._SYNTHESIS_SLOT.release()

    def test_success_returns_valid_wave_and_cleans_job(self):
        code = """import pathlib,sys,wave,json
p=pathlib.Path(sys.argv[1])
with wave.open(str(p/'speech.wav'),'wb') as w:
 w.setnchannels(1);w.setsampwidth(2);w.setframerate(22050);w.writeframes(b'\\x00\\x00'*100)
(p/'result.json').write_text(json.dumps({'ok':True}))
"""
        with patch.object(tts_process, 'worker_command', self.command(code)):
            audio, mime = self.speak()
        self.assertEqual(audio[:4], b'RIFF'); self.assertEqual(mime, 'audio/wav')
        self.assertTrue(all(not job.exists() for job in self.jobs))

    def test_broken_wave_is_not_returned_as_success(self):
        code = "import pathlib,sys;p=pathlib.Path(sys.argv[1]);(p/'speech.wav').write_bytes(b'bad'*30);(p/'result.json').write_text('{\"ok\":true}')"
        with patch.object(tts_process, 'worker_command', self.command(code)):
            with self.assertRaises(tts_process.LocalTTSError) as caught: self.speak()
        self.assertEqual(caught.exception.code, 'tts_invalid_audio')

    def test_error_is_allowlisted_not_raw_worker_content(self):
        code = "import pathlib,sys;p=pathlib.Path(sys.argv[1]);(p/'result.json').write_text('{\"ok\":false,\"code\":\"PRIVATE TEXT\"}')"
        with patch.object(tts_process, 'worker_command', self.command(code)):
            with self.assertRaises(tts_process.LocalTTSError) as caught: self.speak()
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(caught.exception.code, 'tts_worker_failed')

    def test_parallel_request_is_rejected_without_starting_native_runtime(self):
        tts_process._SYNTHESIS_SLOT.acquire()
        try:
            with patch.object(tts_process, 'worker_command') as command:
                with self.assertRaises(tts_process.LocalTTSError) as caught: self.speak()
                command.assert_not_called()
            self.assertEqual(caught.exception.code, 'tts_busy')
        finally: tts_process._SYNTHESIS_SLOT.release()

    def test_launch_failure_releases_slot_and_cleans_job(self):
        with patch.object(tts_process.subprocess, 'Popen', side_effect=OSError('launch failed')):
            with self.assertRaises(OSError): self.speak()
        self.assertTrue(tts_process._SYNTHESIS_SLOT.acquire(False))
        tts_process._SYNTHESIS_SLOT.release()

    def test_frozen_command_reenters_same_executable_without_text_argument(self):
        with patch.object(sys, 'frozen', True, create=True):
            command = tts_process.worker_command(self.root)
        self.assertEqual(command, [sys.executable, '--memuai-tts-worker', str(self.root)])
        launcher = (Path(__file__).resolve().parents[1] / 'memuai.py').read_text(encoding='utf-8')
        tail = launcher[launcher.index('if __name__ == "__main__":'):]
        self.assertLess(tail.index('tts_worker_main'), tail.rindex('main()'))

    def test_model_pair_and_text_are_checked_before_spawn(self):
        with patch.object(tts_process.subprocess, 'Popen') as spawn:
            for text in ['', 'x' * 20001]:
                with self.assertRaises(ValueError): tts_process.synthesize_isolated(text, self.model, self.config)
            self.config.unlink()
            with self.assertRaises(FileNotFoundError): self.speak()
            spawn.assert_not_called()

    def test_worker_handles_missing_dependency_without_crashing_parent(self):
        job = self.root / 'job'; job.mkdir()
        (job/'request.json').write_text(json.dumps({'text':'Hello','model':str(self.model),'config':str(self.config)}))
        with patch.dict(sys.modules, {'piper':None}): tts_worker.run_job(job)
        self.assertEqual(json.loads((job/'result.json').read_text())['code'], 'tts_missing_runtime')

    def test_chunks_are_bounded_and_unicode_is_preserved(self):
        text = 'Привет ' * 300 + '\n' + '界' * 1300
        chunks = list(tts_worker.split_text(text))
        self.assertTrue(all(0 < len(p) <= 600 for p in chunks))
        self.assertEqual(''.join(''.join(chunks).split()), ''.join(text.split()))

    def test_worker_joins_real_pcm_chunks_and_rejects_format_changes(self):
        chunk = SimpleNamespace(sample_rate=22050, sample_width=2, sample_channels=1, audio_int16_bytes=b'\x00\x00'*50)
        voice = SimpleNamespace(synthesize=lambda text: iter([chunk, chunk]))
        path = self.root/'test.wav'
        tts_worker.synthesize_to_wave(voice, 'Hello. Привет.', path)
        tts_process.validate_wave(path)
        with wave.open(str(path)) as wav: self.assertEqual(wav.getnframes(), 200)
        bad = SimpleNamespace(sample_rate=44100, sample_width=2, sample_channels=1, audio_int16_bytes=b'\x00\x00')
        voice.synthesize = lambda text: iter([chunk, bad])
        with self.assertRaises(ValueError): tts_worker.synthesize_to_wave(voice, 'Hello', path)

    def test_worker_bounds_output_size(self):
        chunk = SimpleNamespace(sample_rate=22050, sample_width=2, sample_channels=1, audio_int16_bytes=b'\x00\x00'*50)
        voice = SimpleNamespace(synthesize=lambda text: iter([chunk]*3))
        with patch.object(tts_worker, 'MAX_WAV_BYTES', 150):
            with self.assertRaises(tts_worker.AudioLimitError): tts_worker.synthesize_to_wave(voice, 'Hello', self.root/'large.wav')


if __name__ == '__main__': unittest.main()
