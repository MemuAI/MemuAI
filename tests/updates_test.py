"""Offline regressions for GitHub release selection and verified updates."""
import asyncio
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import update_release as updates


URL = "https://github.com/MemuAI/MemuAI/releases/download/v3.9/MemuAI-Setup-3.9.exe"
EXE = b"MZ" + b"installer fixture" * 100


def release(tag="v3.9", version="3.9"):
    return {"tag_name": tag, "assets": [{"name": f"MemuAI-Setup-{version}.exe",
        "browser_download_url": URL, "size": len(EXE),
        "digest": "sha256:" + hashlib.sha256(EXE).hexdigest()}]}


class ReleaseMetadataTests(unittest.TestCase):
    def test_382_release_is_offered_to_381(self):
        payload = release("v3.8.2", "3.8.2")
        payload["assets"][0]["browser_download_url"] = URL.replace("3.9", "3.8.2")
        offer = updates.release_offer(payload, "3.8.1")
        self.assertTrue(offer["update_available"])
        self.assertEqual(offer["latest_version"], "3.8.2")
        self.assertIn("MemuAI-Setup-3.8.2.exe", offer["download_url"])

    def test_current_real_world_legacy_tag_uses_installer_version(self):
        result = updates.release_offer(release("MemuAI", "3.8"), "3.7")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "3.8")

    def test_equivalent_or_older_versions_never_trigger_update(self):
        for tag in ("v3.8.0", "3.8.0.0", "v3.7"):
            self.assertFalse(updates.release_offer(release(tag), "3.8")["update_available"])

    def test_proxy_archives_and_unrelated_executables_are_not_offered(self):
        for name in ("MemuAIProxy-Setup-3.9.exe", "MemuAI-Setup-3.9.zip", "MemuAI.exe",
                     "MemuFiles-Setup-3.9.exe", "MemuAI-Setup-3.8.exe"):
            payload = release()
            payload["assets"][0]["name"] = name
            result = updates.release_offer(payload, "3.8")
            self.assertFalse(result["update_available"], name)
            self.assertEqual(result["status"], "missing_installer")

    def test_full_installer_wins_over_proxy_and_sources(self):
        payload = release()
        payload["assets"].insert(0, {"name": "MemuAIProxy-Setup-3.9.exe", "browser_download_url": "https://invalid.example/proxy.exe"})
        self.assertEqual(updates.release_offer(payload, "3.8")["download_url"], URL)

    def test_drafts_and_prereleases_are_excluded(self):
        for flag in ("draft", "prerelease"):
            self.assertFalse(updates.release_offer(dict(release(), **{flag: True}), "3.8")["update_available"])

    def test_custom_manifest_still_supported(self):
        result = updates.release_offer({"version": "3.9", "download_url": URL}, "3.8")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["download_url"], URL)

    def test_invalid_download_metadata_is_rejected(self):
        for fields in ({"digest": "sha256:broken"}, {"size": updates.MAX_INSTALLER_SIZE + 1},
                       {"browser_download_url": "http://insecure.example/a.exe"}):
            payload = release()
            payload["assets"][0].update(fields)
            with self.assertRaises(ValueError):
                updates.release_offer(payload, "3.8")

    def test_installer_size_executable_and_hash_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "setup.exe"
            path.write_bytes(EXE)
            sha = hashlib.sha256(EXE).hexdigest()
            updates.verify_installer(path, len(EXE), sha)
            for content in (b"", b"<html>error</html>", EXE[:-1], EXE[:-1] + b"!"):
                path.write_bytes(content)
                with self.assertRaises(ValueError):
                    updates.verify_installer(path, len(EXE), sha)


class UpdateAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app import pgdata, playground_api
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.payload = release()
        self.body = EXE
        self.http_status = 200
        self.download_gate = None
        self.requests = []

        async def handle(request):
            self.requests.append(str(request.url))
            if request.url.path.endswith("/releases/latest"):
                return httpx.Response(self.http_status, json=self.payload)
            if self.download_gate:
                await self.download_gate.wait()
            return httpx.Response(200, content=self.body)

        real_client = httpx.AsyncClient
        self.upstream = real_client(transport=httpx.MockTransport(handle))
        self.addAsyncCleanup(self.upstream.aclose)
        with patch.object(pgdata, "MEMU_DIR", Path(self.temp.name)), \
             patch.dict(os.environ, {"MEMUAI_PROXY_URL": "http://127.0.0.1:9", "MEMUAI_UPDATE_MANIFEST_URL": ""}), \
             patch.object(playground_api.httpx, "AsyncClient", return_value=self.upstream):
            self.app = playground_api.create_app()
        self.client = real_client(transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)

    async def asyncTearDown(self):
        from app.playground_api import _BACKGROUND_TASKS
        pending = [task for task in _BACKGROUND_TASKS if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        path = self.app.state.update_job.get("path")
        if path:
            Path(path).unlink(missing_ok=True)

    async def download(self):
        info = (await self.client.get("/api/pg/updates/check")).json()
        self.assertTrue(info["update_available"], info)
        result = await self.client.post("/api/pg/updates/download", json={"url": info["download_url"], "version": "untrusted"})
        self.assertEqual(result.status_code, 200, result.text)
        for _ in range(200):
            if self.app.state.update_job["status"] != "downloading":
                return self.app.state.update_job
            await asyncio.sleep(0.01)
        self.fail("Download did not finish")

    async def test_release_download_and_apply_use_verified_server_metadata(self):
        job = await self.download()
        self.assertEqual(job["status"], "ready", job)
        self.assertEqual(job["version"], "3.9")
        self.assertEqual(self.requests[0], updates.LATEST_URL)
        status = (await self.client.get("/api/pg/updates/status")).json()
        self.assertNotIn("path", status)
        from app import playground_api
        with patch.object(playground_api.subprocess, "Popen") as launch:
            result = await self.client.post("/api/pg/updates/apply")
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(launch.call_args.args[0], [job["path"]])

    async def test_failed_check_does_not_leave_previous_download_authorized(self):
        await self.client.get("/api/pg/updates/check")
        self.http_status = 403
        result = (await self.client.get("/api/pg/updates/check")).json()
        self.assertEqual(result["status"], "error")
        denied = await self.client.post("/api/pg/updates/download", json={"url": URL})
        self.assertEqual(denied.status_code, 400)

    async def test_no_published_releases_is_different_from_network_error(self):
        self.http_status = 404
        self.assertEqual((await self.client.get("/api/pg/updates/check")).json()["status"], "no_releases")

    async def test_corrupt_download_is_deleted_and_cannot_be_applied(self):
        self.body = EXE[:-1] + b"!"
        job = await self.download()
        self.assertEqual(job["status"], "error")
        self.assertFalse(Path(job["path"]).exists())
        self.assertEqual((await self.client.post("/api/pg/updates/apply")).status_code, 409)

    async def test_modification_after_download_is_detected_before_launch(self):
        job = await self.download()
        Path(job["path"]).write_bytes(EXE[:-1] + b"!")
        from app import playground_api
        with patch.object(playground_api.subprocess, "Popen") as launch:
            self.assertEqual((await self.client.post("/api/pg/updates/apply")).status_code, 500)
            launch.assert_not_called()

    async def test_installer_cannot_be_started_twice(self):
        await self.download()
        from app import playground_api
        with patch.object(playground_api.subprocess, "Popen") as launch:
            responses = await asyncio.gather(self.client.post("/api/pg/updates/apply"),
                                             self.client.post("/api/pg/updates/apply"))
            self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
            launch.assert_called_once()


class GitHubFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_uses_public_release_page_with_matching_digest(self):
        sha = hashlib.sha256(EXE).hexdigest()
        requests = []

        def handle(request):
            requests.append(str(request.url))
            if request.url.host == "api.github.com":
                return httpx.Response(429)
            if request.url.path.endswith("/releases/latest"):
                return httpx.Response(302, headers={"location": updates.RELEASES_URL + "/tag/MemuAI"})
            if request.url.path.endswith("/tag/MemuAI"):
                return httpx.Response(200, text="release page")
            return httpx.Response(200, text=(
                '<ul><li><a href="/MemuAI/MemuAI/releases/download/MemuAI/MemuAIProxy-Setup-3.9.exe">Proxy</a>'
                '<clipboard-copy value="sha256:' + '0' * 64 + '"></clipboard-copy></li>'
                '<li><a href="/MemuAI/MemuAI/releases/download/MemuAI/MemuAI-Setup-3.9.exe">Setup</a>'
                '<clipboard-copy value="sha256:' + sha + '"></clipboard-copy></li></ul>'))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            payload = await updates.fetch_payload(client, updates.LATEST_URL)
        offer = updates.release_offer(payload, "3.8")
        self.assertTrue(offer["update_available"])
        self.assertEqual(offer["sha256"], sha)
        self.assertEqual(offer["latest_version"], "3.9")
        self.assertIn(updates.RELEASES_URL + "/expanded_assets/MemuAI", requests)

    async def test_custom_manifest_never_falls_back_to_another_source(self):
        calls = []
        def handle(request):
            calls.append(str(request.url))
            return httpx.Response(403)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await updates.fetch_payload(client, "https://updates.example/manifest.json")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
