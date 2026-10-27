import shutil
import subprocess
import unittest
from pathlib import Path

from app.music import sources
from app.music.downloader import friendly_error


class MusicSourcesTest(unittest.TestCase):
    def test_direct_sources_and_untrusted_host_names(self):
        samples = {
            "https://x.com/user/status/123": "twitter",
            "https://twitter.com/user/status/123": "twitter",
            "https://instagram.com/reel/abc": "instagram",
            "https://www.threads.net/@user/post/123": "threads",
            "https://fb.watch/example": "facebook",
            "https://reddit.com/r/music/comments/abc": "reddit",
            "https://bsky.app/profile/user/post/abc": "bluesky",
            "https://www.bilibili.com/video/BV123": "bilibili",
            "https://archive.org/details/example": "archive",
            "https://hearthis.at/user/track": "hearthis",
            "https://dropbox.com/s/example/audio.mp3": "dropbox",
            "https://dzen.ru/video/watch/123": "dzen",
        }
        for url, expected in samples.items():
            with self.subTest(url=url):
                info = sources.detect(url)
                self.assertEqual(info["source"], expected)
                self.assertTrue(info["direct"])
                self.assertNotEqual(info["label"], "Web")
        self.assertEqual(sources.detect("https://x.com.attacker.example/item")["source"], "web")
        self.assertFalse(sources.detect("https://open.spotify.com/track/abc")["direct"])

    def test_auth_error_is_explained_without_masking_detail(self):
        result = friendly_error("ERROR: HTTP Error 401: authentication required")
        self.assertIn("cookies", result)
        self.assertIn("401", result)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to verify link detection")
    def test_ui_detects_the_same_hosts(self):
        path = Path(__file__).resolve().parents[1] / "app/module_builtins/music/art.js"
        script = r'''const fs = require('fs'), vm = require('vm');
const code = fs.readFileSync(process.argv[1], 'utf8');
const ctx = {URL, Memu: {lang:'ru'}}; vm.createContext(ctx);
vm.runInContext(code + `\nfor (const [url, source] of [["https://x.com/a/status/1", "twitter"], ["https://instagram.com/reel/abc", "instagram"], ["https://reddit.com/r/a", "reddit"], ["https://x.com.attacker.example/post", "web"], ["https://vk.com/video", "vk"]]) { if (detectLocal(url).source !== source) throw new Error(url); }`, ctx);'''
        subprocess.run([shutil.which("node"), "-e", script, str(path)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
