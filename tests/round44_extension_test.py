"""Chrome attachment/chat regression tests with real ASGI and profile storage."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import browser_extension as extension, browser_extension_chat as chat_api
from app import browser_extension_files as files, integrations, pgdata
from app import browser_extension_store

def data_url(raw: bytes, mime="application/octet-stream"):
    return "data:" + mime + ";base64," + base64.b64encode(raw).decode()

def png(color=(44, 102, 200)):
    output = io.BytesIO()
    Image.new("RGB", (5, 4), color).save(output, "PNG")
    return data_url(output.getvalue(), "image/png")

class ExtensionWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memuai-round44-workspace-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for patcher in (patch.object(pgdata, "MEMU_DIR", self.root),
                        patch.object(extension, "_path", return_value=self.root / "extension.json"),
                        patch.object(integrations, "status", return_value={"services": []})):
            patcher.start()
            self.addCleanup(patcher.stop)
        pgdata.save_profiles({"active_id": "default", "items": [
            {"id": "default", "name": "Personal"}, {"id": "work", "name": "Work"}]})
        self.bridge = extension.Bridge()
        patcher = patch.object(extension, "bridge", self.bridge)
        patcher.start()
        self.addCleanup(patcher.stop)
        token = self.bridge.pair(self.bridge.begin_pair()["code"], "a" * 32)["token"]
        self.headers = {"origin": "chrome-extension://" + "a" * 32, "x-memuai-extension": "a" * 32,
                        "authorization": "Bearer " + token}
        self.complete = AsyncMock(return_value="**Answer**")
        self.extract = lambda name, data: {"name": name, "text": base64.b64decode(data.split(",", 1)[1]).decode(),
                                          "truncated": False}
        self.app = FastAPI()
        self.proxy = AsyncMock(return_value=(200, {"models": []}))
        extension.register(self.app, completion=self.complete, proxy_json=self.proxy,
                           extract_file=lambda name, data: self.extract(name, data))
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8600")
        self.addCleanup(self.client.close)

    def post(self, route, body):
        return self.client.post(extension.PREFIX + "/bridge/" + route, headers=self.headers, json=body)

    def get(self, route):
        return self.client.get(extension.PREFIX + "/bridge/" + route, headers=self.headers)

    def events(self, response):
        return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]

    def upload(self, name="notes.txt", data=None, profile="default"):
        result = self.post("attachment", {"name": name, "data": data or data_url(b"document text"),
                                          "profile_id": profile})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_multimodal_image_document_and_page_context_reach_model_and_persist(self):
        image = self.upload("photo.png", png())
        document = self.upload("notes.txt", data_url(b"notes for the model"))
        with patch.object(chat_api, "run_tool", AsyncMock(return_value={"text": "page body", "url": "https://example.com"})):
            response = self.post("chat", {"text": "Explain both", "model": "vision", "profile_id": "default",
                                "attachments": [image["id"], document["id"]], "page_context": True, "tab_id": 7})
        events = self.events(response)
        self.assertEqual(events[-1]["event"], "done", events)
        payload = self.complete.call_args.args[1]
        parts = payload[-1]["content"]
        self.assertIsInstance(parts, list)
        self.assertTrue(any(p.get("type") == "image_url" for p in parts))
        self.assertIn("notes for the model", parts[0]["text"])
        self.assertIn("<untrusted_page>", parts[-1]["text"])
        rows = browser_extension_store.load_chats("default")
        self.assertEqual([m["role"] for m in rows[0]["messages"]], ["user", "assistant"])
        persisted = rows[0]["messages"][0]["attachments"]
        self.assertEqual(len(persisted), 2)
        self.assertTrue(persisted[0]["data"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(persisted[1]["text"], "notes for the model")

    def test_attachment_cannot_be_used_after_switching_profile(self):
        staged = self.upload()
        pgdata.save_profiles({"active_id": "work"})
        response = self.post("chat", {"text": "Read", "model": "fake", "profile_id": "work",
                                      "attachments": [staged["id"]]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(pgdata.load_chats("work"), [])
        self.complete.assert_not_awaited()

    def test_stale_panel_cannot_send_or_read_another_active_profile(self):
        pgdata.save_chats([{"id": "shared-id", "title": "Private A", "messages": []}], "default")
        pgdata.save_chats([{"id": "shared-id", "title": "Private B", "messages": []}], "work")
        pgdata.save_profiles({"active_id": "work"})
        response = self.post("chat", {"text": "from panel A", "model": "fake",
                                      "chat_id": "shared-id", "profile_id": "default"})
        self.assertEqual(response.status_code, 409, response.text)
        detail = self.get("chats?id=shared-id&profile_id=default")
        self.assertEqual(detail.status_code, 409)
        self.assertEqual(pgdata.load_chats("work")[0]["messages"], [])
        self.complete.assert_not_awaited()

    def test_attachment_profile_is_checked_before_and_after_document_extraction(self):
        pgdata.save_profiles({"active_id": "work"})
        response = self.post("attachment", {"name": "a.txt", "data": data_url(b"data"), "profile_id": "default"})
        self.assertEqual(response.status_code, 409)
        pgdata.save_profiles({"active_id": "default"})
        def change_profile(name, data):
            pgdata.save_profiles({"active_id": "work"})
            return {"text": "extracted under A"}
        self.extract = change_profile
        response = self.post("attachment", {"name": "a.txt", "data": data_url(b"data"), "profile_id": "default"})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('"id"', response.text)

    def test_deleted_chat_is_not_recreated_from_stale_panel_id(self):
        response = self.post("chat", {"text": "Continue", "model": "fake",
                                      "chat_id": "deleted", "profile_id": "default"})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(pgdata.load_chats("default"), [])
        self.complete.assert_not_awaited()

    def test_project_chats_are_listed_with_metadata_but_cannot_be_mutated(self):
        rows = [{"id": "normal", "title": "Normal", "mode": "chat", "model": "fake", "updated": 10, "messages": []},
                {"id": "project", "title": "Project", "project_id": "p1", "mode": "work", "updated": 20,
                 "messages": [{"role": "user", "content": "task"}]}]
        pgdata.save_chats(rows, "default")
        response = self.get("chats").json()
        self.assertEqual(response["profile_id"], "default")
        self.assertEqual(response["profile_name"], "Personal")
        self.assertEqual({c["id"] for c in response["chats"]}, {"normal", "project"})
        project = next(c for c in response["chats"] if c["id"] == "project")
        self.assertEqual(project["count"], 1)
        self.assertEqual(project["project_id"], "p1")
        mutation = self.post("chat", {"chat_id": "project", "model": "fake", "text": "Continue"})
        self.assertEqual(mutation.status_code, 400)
        self.complete.assert_not_awaited()

    def test_old_text_and_document_attachments_are_included_when_continuing_chat(self):
        old = {"id": "old", "title": "Old", "mode": "chat", "messages": [
            {"role": "user", "content": "Review", "attachments": [
                {"name": "legacy.txt", "data": data_url(b"old file body")}]},
            {"role": "assistant", "content": "Ready"}]}
        pgdata.save_chats([old])
        response = self.post("chat", {"chat_id": "old", "text": "Continue", "model": "fake"})
        self.assertEqual(self.events(response)[-1]["event"], "done")
        self.assertIn("old file body", json.dumps(self.complete.call_args.args[1]))
        self.assertEqual(len(pgdata.load_chats()[0]["messages"]), 4)

    def test_attachment_count_image_count_and_file_size_limits_are_enforced(self):
        response = self.post("chat", {"text": "Read", "model": "fake", "attachments": ["missing"] * 9})
        self.assertEqual(response.status_code, 400)
        items = [self.upload(str(index) + ".png", png()) for index in range(5)]
        response = self.post("chat", {"text": "Read", "model": "fake", "attachments": [i["id"] for i in items]})
        self.assertEqual(response.status_code, 400)
        too_large = self.post("attachment", {"name": "big.txt", "data": data_url(b"x" * (files.MAX_FILE + 1))})
        self.assertEqual(too_large.status_code, 400)
        self.complete.assert_not_awaited()

    def test_unsupported_and_corrupt_attachments_do_not_enter_chat_history(self):
        for name, data in [("run.exe", data_url(b"binary")), ("notes.txt", "data:text/plain;base64,broken!")]:
            response = self.post("attachment", {"name": name, "data": data})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(pgdata.load_chats(), [])

    def test_multimodal_content_parts_survive_a_history_roundtrip(self):
        image = png()
        pgdata.save_chats([{"id": "multimodal", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Remember this picture"}, {"type": "image_url", "image_url": {"url": image}}]}]}])
        response = self.post("chat", {"chat_id": "multimodal", "text": "Describe that image", "model": "vision"})
        self.assertEqual(self.events(response)[-1]["event"], "done")
        payload = self.complete.call_args.args[1]
        content = payload[1]["content"]
        self.assertIsInstance(content, list)
        self.assertTrue(any(p.get("type") == "image_url" for p in content))

    def test_context_clipping_is_explicit_in_the_model_payload(self):
        item = self.upload("long.txt", data_url(b"x" * 90000))
        response = self.post("chat", {"text": "Summarize this file", "model": "fake", "attachments": [item["id"]]})
        self.assertEqual(self.events(response)[-1]["event"], "done")
        value = self.complete.call_args.args[1][-1]["content"]
        self.assertLess(len(value), 61000)
        self.assertTrue("truncated" in value.lower())

    def test_image_context_is_bounded_across_the_entire_history(self):
        images = [png()] * 4
        pgdata.save_chats([{"id": "many-images", "messages": [
            {"role": "user", "content": "Older pictures", "images": images},
            {"role": "assistant", "content": "Ready"},
            {"role": "user", "content": "Newer pictures", "images": images}]}])
        response = self.post("chat", {"chat_id": "many-images", "text": "Summarize", "model": "vision"})
        self.assertEqual(self.events(response)[-1]["event"], "done")
        contents = [m["content"] for m in self.complete.call_args.args[1] if isinstance(m["content"], list)]
        count = sum(p.get("type") == "image_url" for content in contents for p in content)
        self.assertLessEqual(count, 4)
        self.assertGreater(count, 0)
        self.assertIn("omitted", json.dumps(self.complete.call_args.args[1]).lower())

    def test_cached_desktop_images_are_restored_only_from_their_active_profile(self):
        from app import media_cache
        raw = base64.b64decode(png().split(",", 1)[1])
        cached = media_cache.store_image(raw, "default")
        pgdata.save_chats([{"id": "cached", "messages": [
            {"role": "user", "content": "Remember the generated picture", "images": [cached["url"]]}]}])
        response = self.post("chat", {"chat_id": "cached", "text": "Describe it", "model": "vision"})
        self.assertEqual(self.events(response)[-1]["event"], "done")
        payload = self.complete.call_args.args[1]
        self.assertTrue(any(isinstance(m["content"], list) for m in payload))
        self.assertEqual(files.history_image(cached["url"], "default"), png())
        pgdata.save_profiles({"active_id": "work"})
        self.assertIsNone(files.history_image(cached["url"], "default"))
        self.assertIsNone(files.history_image(cached["url"], "work"))

    def test_large_or_remote_history_images_are_omitted_without_fetching(self):
        from app import media_cache
        raw = base64.b64decode(png().split(",", 1)[1])
        cached = media_cache.store_image(raw, "default")
        target = media_cache.local_path(cached["url"])
        target.write_bytes(b"x" * (files.MAX_FILE + 1))
        with patch.object(media_cache, "local_data_url", side_effect=AssertionError("oversized image must not be read")):
            self.assertIsNone(files.history_image(cached["url"], "default"))
        self.assertIsNone(files.history_image("https://example.com/image.png", "default"))
        self.assertIsNone(files.history_image(data_url(b"x" * (files.MAX_FILE + 1), "image/png"), "default"))
        self.assertIsNone(files.history_image("data:image/png;base64,invalid!", "default"))

    def test_models_route_reuses_catalog_names_aliases_capabilities_and_preferences(self):
        pgdata.save_preferences({"default_model": "chat", "model_aliases": {"chat": "My assistant"},
                                 "hidden_models": ["hidden"], "pinned_models": ["chat"]})
        self.proxy.return_value = (200, {"models": [
            {"id": "chat", "name": "Provider Chat", "caps": {"vision": True}, "provider": "account"},
            {"id": "hidden", "name": "Hidden", "caps": {}},
            {"id": "draw", "name": "Image Generator", "kind": "image", "caps": {}}]})
        result = self.get("models")
        self.assertEqual(result.status_code, 200)
        value = result.json()
        self.assertEqual([m["id"] for m in value["models"]], ["chat"])
        self.assertEqual(value["models"][0]["display"], "My assistant")
        self.assertTrue(value["models"][0]["caps"]["vision"])
        self.assertEqual(value["default_model"], "chat")
        self.proxy.assert_awaited_once_with("GET", "/api/admin/pg/state")

    def test_tool_screenshots_keep_only_four_newest_images_in_every_model_request(self):
        requests = []
        async def complete(_model, messages):
            requests.append(json.loads(json.dumps(messages)))
            if len(requests) < 7:
                return '```memuai-tool\n[{"tool":"chrome_screenshot","args":{"tab_id":7}}]\n```'
            return "Finished looking at the screenshots."
        self.complete.side_effect = complete
        screenshots = [png((20 + index * 25, 80, 150)) for index in range(6)]
        pgdata.save_chats([{"id": "pictures", "messages": [
            {"role": "user", "content": "Earlier pictures", "images": [png()] * 4}]}])
        with patch.object(chat_api, "run_tool", AsyncMock(side_effect=[
                {"data": image.split(",", 1)[1], "media_type": "image/png"} for image in screenshots])):
            response = self.post("chat", {"chat_id": "pictures", "text": "Look at the tab", "model": "vision"})
        self.assertEqual(self.events(response)[-1]["event"], "done", response.text)
        self.assertEqual(len(requests), 7)
        for messages in requests:
            count = sum(part.get("type") == "image_url" for message in messages
                        if isinstance(message["content"], list) for part in message["content"])
            self.assertEqual(count, 4)
        self.assertIn("Earlier image omitted", json.dumps(requests[-1]))
        self.assertEqual(requests[-1][-1]["content"][-1]["type"], "image_url")
        retained = [part["image_url"]["url"] for message in requests[-1] if isinstance(message["content"], list)
                    for part in message["content"] if part.get("type") == "image_url"]
        self.assertEqual(retained, screenshots[-4:])

    def test_invalid_tool_screenshot_data_is_not_sent_as_an_image(self):
        for mime, image in (("image/svg+xml", "PHN2Zy8+"), ("image/png", "broken!"),
                            ("image/png", base64.b64encode(b"x" * (files.MAX_FILE + 1)).decode())):
            with self.subTest(mime=mime, length=len(image)):
                requests = []
                async def complete(_model, messages, requests=requests):
                    requests.append(json.loads(json.dumps(messages)))
                    return ('```memuai-tool\n[{"tool":"chrome_screenshot","args":{"tab_id":7}}]\n```'
                            if len(requests) == 1 else "No image was available.")
                self.complete.side_effect = complete
                with patch.object(chat_api, "run_tool", AsyncMock(return_value={"data": image, "media_type": mime})):
                    response = self.post("chat", {"text": "Look", "model": "vision"})
                self.assertEqual(self.events(response)[-1]["event"], "done", response.text)
                self.assertEqual(len(requests), 2)
                self.assertFalse(any(part.get("type") == "image_url" for message in requests[-1]
                                     if isinstance(message["content"], list) for part in message["content"]))
                self.assertIn("Screenshot omitted", json.dumps(requests[-1]))

    def test_profile_switch_during_tool_execution_stops_the_next_model_round(self):
        async def switch_profile(*_args, **_kwargs):
            pgdata.save_profiles({"active_id": "work"})
            return {"text": "page from the previous profile"}
        self.complete.side_effect = ['```memuai-tool\n[{"tool":"chrome_read","args":{"tab_id":7}}]\n```',
                                    "Must not continue under the new profile"]
        with patch.object(chat_api, "run_tool", AsyncMock(side_effect=switch_profile)):
            response = self.post("chat", {"text": "Read", "model": "fake", "profile_id": "default"})
        events = self.events(response)
        self.assertEqual(events[-1]["event"], "error", events)
        self.assertIn("Профиль", events[-1]["text"])
        self.assertEqual(self.complete.await_count, 1)
        self.assertEqual(pgdata.load_chats("work"), [])
        self.assertEqual([m["role"] for m in browser_extension_store.load_chats("default")[0]["messages"]], ["user"])

if __name__ == "__main__":
    unittest.main()
