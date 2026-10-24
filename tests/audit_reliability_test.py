"""Offline regression checks for the reliability audit; no desktop interaction."""
from __future__ import annotations

import codecs
import io
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import tempfile
import threading
import time
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
import types
import sys
import subprocess

import tests  # bind a temporary application-data directory before importing app

from app.memufiles import assistant, fsops, textedit
from app.memufiles.opqueue import OpQueue


def wait(queue, op_id):
    op = queue.get(op_id)
    deadline = time.monotonic() + 5
    while op.status not in ("done", "error", "cancelled") and time.monotonic() < deadline:
        time.sleep(0.01)
    if op.status not in ("done", "error", "cancelled"):
        raise AssertionError("operation did not finish")
    return op


class FilesAssistantTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / "scope"
        self.scope.mkdir()
        patch = mock.patch.dict(os.environ, {"MEMUFILES_TRASH_DIR": str(self.root / "trash")})
        patch.start()
        self.addCleanup(patch.stop)

    def plan(self, *actions):
        return assistant.build_plan(str(self.scope), {"actions": list(actions)})

    def test_edit_preserves_encoding_line_endings_and_undo_redo(self):
        for encoding, bom in (("utf-8", b""), ("cp1251", b""), ("utf-16-le", codecs.BOM_UTF16_LE)):
            with self.subTest(encoding=encoding):
                path = self.scope / (encoding + ".txt")
                raw = bom + "Привет\r\nмир\r\n".encode(encoding)
                path.write_bytes(raw)
                plan = self.plan({"op": "edit", "path": path.name, "old_text": "Привет\nмир", "new_text": "Привет\nдруг"})
                self.assertEqual(plan["rejected"], [])
                self.assertIn("-мир", plan["items"][0]["diff"])
                public = assistant.plan_for_page(plan)
                self.assertNotIn("content", public["items"][0])
                queue = OpQueue()
                result = assistant.execute_plan(plan, [0], queue)
                op = wait(queue, result["ops"][0])
                self.assertEqual(op.status, "done", op.errors)
                expected = bom + "Привет\r\nдруг\r\n".encode(encoding)
                self.assertEqual(path.read_bytes(), expected)
                undo = queue.undo(op.id)
                self.assertEqual(wait(queue, undo.id).status, "done")
                self.assertEqual(path.read_bytes(), raw)
                redo = queue.redo()
                self.assertEqual(wait(queue, redo.id).status, "done")
                self.assertEqual(path.read_bytes(), expected)

    def test_create_and_undo_without_touching_existing_files(self):
        plan = self.plan({"op": "write", "path": "new.txt", "content": "hello\n"})
        queue = OpQueue()
        op = wait(queue, assistant.execute_plan(plan, [0], queue)["ops"][0])
        self.assertEqual((self.scope / "new.txt").read_bytes(), b"hello\n")
        self.assertEqual(wait(queue, queue.undo(op.id).id).status, "done")
        self.assertFalse((self.scope / "new.txt").exists())

    def test_stale_file_and_stale_undo_do_not_overwrite_user_changes(self):
        path = self.scope / "note.txt"
        path.write_bytes(b"original")
        plan = self.plan({"op": "write", "path": path.name, "content": "AI"})
        path.write_bytes(b"user change")
        queue = OpQueue()
        op = wait(queue, assistant.execute_plan(plan, [0], queue)["ops"][0])
        self.assertEqual(op.errors[0]["error"], "file_changed")
        self.assertEqual(path.read_bytes(), b"user change")
        plan = self.plan({"op": "write", "path": path.name, "content": "AI"})
        op = wait(queue, assistant.execute_plan(plan, [0], queue)["ops"][0])
        path.write_bytes(b"another user change")
        self.assertEqual(wait(queue, queue.undo(op.id).id).status, "error")
        self.assertEqual(path.read_bytes(), b"another user change")

    def test_new_file_collision_is_not_overwritten(self):
        plan = self.plan({"op": "write", "path": "new.txt", "content": "AI"})
        (self.scope / "new.txt").write_bytes(b"user")
        queue = OpQueue()
        op = wait(queue, assistant.execute_plan(plan, [0], queue)["ops"][0])
        self.assertEqual(op.status, "error")
        self.assertEqual((self.scope / "new.txt").read_bytes(), b"user")

    def test_ambiguous_edits_require_explicit_all(self):
        path = self.scope / "note.txt"
        path.write_bytes(b"hello hello")
        action = {"op": "edit", "path": path.name, "old_text": "hello", "new_text": "hi"}
        self.assertEqual(self.plan(action)["rejected"][0]["why"], "ambiguous_edit")
        action["all"] = True
        self.assertEqual(self.plan(action)["items"][0]["content"], "hi hi")

    def test_edit_keeps_unrelated_mixed_line_endings_byte_identical(self):
        path = self.scope / "mixed.txt"
        path.write_bytes(b"one\r\ntwo\nthree\rfour\r\n")
        plan = self.plan({"op": "edit", "path": path.name, "find": "two", "replace": "TWO"})
        queue = OpQueue()
        self.assertEqual(wait(queue, assistant.execute_plan(plan, [0], queue)["ops"][0]).status, "done")
        self.assertEqual(path.read_bytes(), b"one\r\nTWO\nthree\rfour\r\n")

    def test_scope_cannot_be_overridden_by_model(self):
        path = self.scope / "note.txt"
        path.write_bytes(b"hello")
        plan = self.plan({"op": "copy", "paths": [path.name], "dest": "..", "dest_outside_ok": True})
        self.assertFalse(plan["items"])
        self.assertEqual(plan["rejected"][0]["why"], "dest_outside_scope")
        outside = self.root / "private.txt"
        outside.write_bytes(b"private")
        for tool in ("read_file", "file_info", "list_folder", "folder_stats", "find_files"):
            with self.subTest(tool=tool), self.assertRaises(fsops.FsError):
                assistant.run_tool(str(self.scope), tool, {"path": str(outside), "root": str(self.root)})

    def test_symlinks_cannot_escape_or_loop(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"private")
        try:
            (self.scope / "link").symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("directory symlinks unavailable")
            # Junctions need no developer-mode symlink privilege on Windows.
            result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(self.scope / "link"), str(outside)],
                                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                self.skipTest("directory links unavailable")
        self.assertFalse(assistant.find_files(str(self.scope), {})["items"])
        self.assertEqual(self.plan({"op": "write", "path": "link/new.txt", "content": "x"})["rejected"][0]["why"], "outside_scope")

    def test_concurrent_confirmation_submits_once(self):
        plan = self.plan({"op": "mkdir", "path": "new"})
        queue = OpQueue()
        def execute():
            try:
                return assistant.execute_plan(plan, [0], queue)
            except fsops.FsError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: execute(), range(2)))
        self.assertIn("plan_executed", results)
        self.assertEqual(len(queue.ops), 1)

    def test_single_quoted_tool_call_is_parsed_without_eval(self):
        call = assistant.parse_tool("<tool>{'name': 'read_file', 'args': {'path': 'x.txt'}}</tool>")
        self.assertEqual(call["name"], "read_file")
        self.assertEqual(call["args"]["path"], "x.txt")

    def test_transport_advertises_tools_and_files_service(self):
        from app.memunote import ai
        sent = []
        def stream(base, payload, cancel, on_conn):
            sent.append(payload)
            yield {"t": "delta", "text": "ok"}
        with mock.patch.object(ai, "stream_chat", stream), mock.patch.object(ai, "discover", return_value="http://localhost:8317"):
            self.assertEqual(list(assistant.proxy_llm("test", {}, threading.Event(), {})([])), ["ok"])
        self.assertTrue(sent[0]["tools_available"])
        self.assertEqual(sent[0]["service"], "memufiles")

    def test_empty_and_unfinished_tool_responses_are_actionable(self):
        events = []
        calls = []
        def broken(messages):
            calls.append([dict(m) for m in messages])
            yield '<tool>{broken' if len(calls) == 1 else "Fixed"
        assistant.run_chat(str(self.scope), [{"role": "user", "content": "help"}], "en", broken,
                           lambda e: events.append(e) or True, assistant.PlanStore(), threading.Event(), "untrusted selection")
        self.assertEqual(calls[0][1]["role"], "user")
        self.assertIn("bad_json", calls[1][-1]["content"])
        self.assertEqual(events[-1]["text"], "Fixed")
        events.clear()
        assistant.run_chat(str(self.scope), [], "en", lambda m: iter(()), lambda e: events.append(e) or True,
                           assistant.PlanStore(), threading.Event())
        self.assertEqual(events[-1]["kind"], "empty_answer")

    def test_document_read_extracts_text_with_bounded_archive_reads(self):
        path = self.scope / "document.docx"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", '<w:document><w:p><w:t>Договор поставки</w:t></w:p></w:document>')
        with mock.patch.object(zipfile.ZipFile, "read", side_effect=AssertionError("unbounded archive read")):
            result = assistant.run_tool(str(self.scope), "read_file", {"path": path.name})
        self.assertIn("Договор поставки", result["text"])
        self.assertTrue(result["extracted"])


class FilesTransportTest(unittest.TestCase):
    def test_http_chat_read_edit_review_execute_and_undo(self):
        from app.memufiles import server, settings
        from app.memunote import ai

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
                "MEMUAI_DATA_DIR": str(Path(tmp) / "data"), "MEMUFILES_TRASH_DIR": str(Path(tmp) / "trash")}):
            scope = Path(tmp) / "scope"
            scope.mkdir()
            path = scope / "note.txt"
            path.write_bytes(b"old text\r\n")
            requests = []

            class Proxy(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_GET(self):
                    value = {"service": "memuai-proxy"} if self.path == "/health" else {
                        "models": [{"id": "test-chat", "name": "Test Chat", "provider": "custom", "caps": {"chat": True}}]}
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(value).encode())

                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    requests.append((dict(self.headers), body))
                    if len(requests) == 1:
                        text = '<tool>{"name":"read_file","args":{"path":"note.txt"}}</tool>'
                    elif len(requests) == 2:
                        text = '<tool>{"name":"propose_plan","args":{"actions":[{"op":"edit","path":"note.txt","old_text":"old text","new_text":"new text"}]}}</tool>'
                    else:
                        text = "Review the text changes."
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for start in range(0, len(text), 7):
                        item = {"choices": [{"delta": {"content": text[start:start + 7]}}]}
                        self.wfile.write(("data: " + json.dumps(item) + "\n\n").encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")

            proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            app = server.FilesApp()
            httpd, _thread = server.start(app)
            try:
                settings.save({"proxy_url": f"http://127.0.0.1:{proxy.server_port}", "language": "en"})
                with mock.patch.dict(ai._STATUS_CACHE, {"at": 0.0, "value": None}):
                    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
                    conn.request("GET", "/api/ai/status", headers={"X-MF-Token": app.token})
                    status = json.loads(conn.getresponse().read())
                    conn.close()
                    self.assertTrue(status["online"])
                    self.assertEqual(status["default_model"], "test-chat")
                conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
                conn.request("POST", "/api/ai/chat", json.dumps({"scope": str(scope), "model": "test-chat",
                             "messages": [{"role": "user", "content": "change old to new"}]}),
                             headers={"X-MF-Token": app.token, "Content-Type": "application/json"})
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                events = [json.loads(line[6:]) for line in response.read().decode().splitlines() if line.startswith("data: ")]
                conn.close()
                self.assertEqual(events[-1]["t"], "done")
                self.assertNotIn("error", [e["t"] for e in events])
                plan_event = next(e for e in events if e["t"] == "plan")
                self.assertIn("-old text", plan_event["plan"]["items"][0]["diff"])
                self.assertEqual(path.read_bytes(), b"old text\r\n")  # review never writes
                plan = app.plans.get(plan_event["plan"]["id"])
                op = wait(app.queue, assistant.execute_plan(plan, [0], app.queue)["ops"][0])
                self.assertEqual(op.status, "done", op.errors)
                self.assertEqual(path.read_bytes(), b"new text\r\n")
                self.assertEqual(wait(app.queue, app.queue.undo(op.id).id).status, "done")
                self.assertEqual(path.read_bytes(), b"old text\r\n")
                self.assertTrue(all(body["tools_available"] for _, body in requests))
                self.assertTrue(all(headers["X-MemuAI-Service"] == "memufiles" for headers, _ in requests))
            finally:
                httpd.shutdown()
                httpd.server_close()
                proxy.shutdown()
                proxy.server_close()


class NativeDragTest(unittest.TestCase):
    def test_clipboard_has_an_owner_and_reports_native_failure(self):
        import ctypes
        from app.memufiles import clip
        user = mock.Mock()
        user.GetForegroundWindow.return_value = 1234
        user.OpenClipboard.return_value = 1
        user.EmptyClipboard.return_value = 1
        user.SetClipboardData.return_value = 100
        user.RegisterClipboardFormatW.return_value = 200
        kernel = mock.Mock()
        memory = ctypes.create_string_buffer(4096)
        kernel.GlobalAlloc.return_value = 100
        kernel.GlobalLock.return_value = ctypes.addressof(memory)
        with mock.patch.object(clip, "IS_WIN", True), mock.patch.object(clip, "_user32", return_value=(ctypes, user, kernel)):
            self.assertTrue(clip.write_files([r"C:\work\file.txt"]))
            user.OpenClipboard.assert_called_with(1234)
            user.SetClipboardData.return_value = 0
            self.assertFalse(clip.write_files([r"C:\work\file.txt"], owner=4321))
            user.OpenClipboard.assert_called_with(4321)
            kernel.GlobalFree.assert_called_with(100)
            kernel.GlobalAlloc.return_value = 0
            self.assertFalse(clip.write_files([r"C:\work\file.txt"]))

    def test_native_export_uses_file_drop_and_copy_without_text_or_clipboard(self):
        from app.memufiles.desktop import Desktop
        from app import win_titlebar
        class Collection(list):
            def Add(self, value):
                self.append(value)
        class Data:
            def __init__(self):
                self.formats = {}
            def SetFileDropList(self, files):
                self.formats["FileDrop"] = list(files)
            def SetData(self, key, value):
                self.formats[key] = value
        modules = {"System.Collections.Specialized": types.SimpleNamespace(StringCollection=Collection),
                   "System.Windows.Forms": types.SimpleNamespace(DataObject=Data, DragDropEffects=types.SimpleNamespace(Copy=1))}
        native = mock.Mock()
        native.DoDragDrop.return_value = 1
        desktop = Desktop(None, None, "dark")
        with tempfile.TemporaryDirectory() as tmp:
            paths = [str(Path(tmp) / "a b.txt"), str(Path(tmp) / "файл.txt")]
            for path in paths:
                Path(path).write_bytes(b"test")
            with mock.patch.dict(sys.modules, modules), mock.patch.object(desktop, "_native", return_value=native), \
                    mock.patch.object(win_titlebar, "winforms_ui_invoker", return_value=lambda fn: fn()), \
                    mock.patch("app.memufiles.desktop.os.name", "nt"):
                result = desktop.file_drag(paths)
            self.assertTrue(result["ok"])
            data, effect = native.DoDragDrop.call_args.args
            self.assertEqual(data.formats["FileDrop"], paths)
            self.assertEqual(effect, 1)
            self.assertNotIn("text/plain", data.formats)


class NoteReliabilityTest(unittest.TestCase):
    def test_assistant_catalog_never_falls_back_to_non_chat_models(self):
        from app.memunote import ai
        data = {"models": [{"id": "song", "caps": {"music": True, "text": False}},
                           {"id": "image", "caps": {"image": True}},
                           {"id": "video", "caps": {"video": True}}]}
        with mock.patch.object(ai, "request_json", return_value=data) as request:
            self.assertEqual(ai.models("http://localhost:8317"), [])
            self.assertEqual(request.call_count, 1)
        data["models"] += [{"id": "chat", "caps": {"text": True}, "context": {"context": "unknown"}},
                           {"id": "chat", "caps": {"text": True}}]
        with mock.patch.object(ai, "request_json", return_value=data):
            rows = ai.models("http://localhost:8317")
        self.assertEqual([row["id"] for row in rows], ["chat"])
        self.assertEqual(rows[0]["context"], 0)

    def test_cyrillic_encoding_detection_counts_uppercase_letters(self):
        from app.memunote.formats import textio
        for text in ("Привет\r\nмир\r\n", "ДОГОВОР ПОСТАВКИ ТОВАРОВ И УСЛУГ"):
            data = text.encode("cp1251")
            info = textio.detect_bytes(data)
            self.assertEqual(info.encoding, "cp1251")
            self.assertEqual(textio.decode(data, info.encoding), text)

    def test_ipv6_proxy_discovery_produces_a_valid_url(self):
        from app.memunote.ai import _valid_local, _conn
        url = _valid_local("http://[::1]:8317/")
        self.assertEqual(url, "http://[::1]:8317")
        conn = _conn(url, 1)
        self.assertEqual(conn.host, "::1")
        conn.close()

    def test_fast_utf8_save_preserves_intentional_leading_feff(self):
        from app.memunote.formats import textio
        text = "\ufeffhello\nworld"
        for bom in (False, True):
            self.assertEqual(textio.encode_utf8_bytes(text.encode(), "utf-8", "crlf", bom),
                             textio.encode(text, "utf-8", "crlf", bom)[0])

    def test_preview_reads_utf32_and_truncated_utf16(self):
        from app.memufiles.preview import read_text
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_bytes("Привет, мир!".encode("utf-32"))
            self.assertEqual(read_text(str(path))["text"], "Привет, мир!")
            path.write_bytes("hello world".encode("utf-16"))
            self.assertNotIn("\ufffd", read_text(str(path), 9)["text"])

    def test_trailing_think_tag_prefix_is_not_lost(self):
        from app.memunote import ai
        data = {"choices": [{"delta": {"content": "x <thi"}}]}
        response = io.BytesIO(("data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n").encode())
        response.status = 200
        conn = mock.Mock()
        conn.getresponse.return_value = response
        with mock.patch.object(ai, "_conn", return_value=conn):
            events = list(ai.stream_chat("http://localhost:1", {"model": "test", "messages": []}, threading.Event()))
        self.assertEqual("".join(e["text"] for e in events if e["t"] == "delta"), "x <thi")


class InstallerReliabilityTest(unittest.TestCase):
    def test_payload_closes_its_file_when_index_parsing_fails(self):
        from installer import payload
        stream = io.BytesIO(b"x" * 100)
        found = types.SimpleNamespace(trailer_pos=50, index_len=10)
        with mock.patch.object(Path, "open", return_value=stream), mock.patch.object(payload, "locate", return_value=found), \
                mock.patch.object(payload.Index, "from_bytes", side_effect=payload.PayloadError("bad index")):
            with self.assertRaises(payload.PayloadError):
                payload.Payload(Path("test.exe"))
        self.assertTrue(stream.closed)


class CodeReliabilityTest(unittest.TestCase):
    def test_renderer_respects_disabled_color_and_width_in_dumb_terminals(self):
        from app.cli.tui.render import Renderer
        from app.cli.tui.theme import palette_for
        with mock.patch.dict(os.environ, {"TERM": "dumb"}):
            renderer = Renderer(palette_for("dark", None), None)
            self.assertIsNone(renderer.color_system)
            output = renderer.render("word " * 50, width=40)
            self.assertNotIn("\x1b[", output)
            self.assertTrue(all(len(line.rstrip()) <= 40 for line in output.splitlines()))

    def test_catalog_removal_supports_cross_volume_skills_and_unique_trash_names(self):
        import errno
        from app import skills_catalog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            skill = skills / "sample"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("sample")
            (skill / ".memuai-source.json").write_text("{}")
            with mock.patch.object(skills_catalog, "code_skills_root", return_value=skills), \
                    mock.patch.object(skills_catalog, "_data_dir", return_value=root / "other-drive"), \
                    mock.patch("os.rename", side_effect=OSError(errno.EXDEV, "cross-device link")):
                result = skills_catalog.uninstall_code("sample")
            self.assertFalse(skill.exists())
            self.assertEqual((Path(result["trash"]) / "SKILL.md").read_text(), "sample")


class MusicReliabilityTest(unittest.TestCase):
    def setUp(self):
        from app.music.library import Library
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lib = Library(Path(tmp.name))
        self.addCleanup(self.lib.close)

    def test_deleting_one_duplicate_keeps_shared_audio(self):
        path = self.lib.tracks_dir / "song.mp3"
        path.write_bytes(b"audio")
        a = self.lib.add({"file": str(path), "title": "A"})
        b = self.lib.add({"file": str(path), "title": "B"})
        self.assertFalse(self.lib.delete(a["id"])["file_removed"])
        self.assertTrue(path.exists())
        self.assertTrue(self.lib.delete(b["id"])["file_removed"])
        self.assertFalse(path.exists())

    def test_settings_are_independent_and_recover_wrong_json_type(self):
        from app.music.library import DEFAULT_SETTINGS
        self.lib.settings()["lyrics_sources"].clear()
        self.assertTrue(DEFAULT_SETTINGS["lyrics_sources"])
        (self.lib.root / "settings.json").write_text("[]")
        self.assertEqual(self.lib.settings()["volume"], 1)

    def test_concurrent_settings_updates_are_not_lost(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(self.lib.set_settings, [{"volume": 0.3}, {"crossfade": 4}, {"seek_step": 8}, {"gapless": False}]))
        value = self.lib.settings()
        self.assertEqual([value[k] for k in ("volume", "crossfade", "seek_step", "gapless")], [0.3, 4, 8, False])

    def test_russian_titles_and_artists_are_searchable_in_any_case(self):
        path = self.lib.tracks_dir / "song.mp3"
        path.write_bytes(b"audio")
        track = self.lib.add({"file": str(path), "title": "Привет, Мир!", "artist": "Группа"})
        for query in ("привет", "ПРИВЕТ", "группа", "ГРУППА", "мир"):
            self.assertEqual(self.lib.list(q=query)["tracks"][0]["id"], track["id"])

    def test_dedup_ignores_official_audio_title_suffix(self):
        path = self.lib.tracks_dir / "song.mp3"
        path.write_bytes(b"audio")
        track = self.lib.add({"file": str(path), "title": "Песня", "artist": "Группа", "duration": 100})
        found = self.lib.find_similar("ПЕСНЯ (Official Audio)", "ГРУППА", 101)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], track["id"])

    def test_concurrent_playlist_additions_are_not_lost(self):
        ids = []
        for i in range(8):
            path = self.lib.tracks_dir / f"{i}.mp3"
            path.write_bytes(b"audio")
            ids.append(self.lib.add({"file": str(path)})["id"])
        playlist = self.lib.create_playlist("Test")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda tid: self.lib.update_playlist(playlist["id"], {"add": tid}), ids))
        self.assertEqual({t["id"] for t in self.lib.playlist(playlist["id"])["tracks"]}, set(ids))


if __name__ == "__main__":
    unittest.main()
