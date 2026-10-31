"""Version 4.5: comparison, upgrade detection and the release tag.

``4.2 < 4.5``, ``4.5 == 4.5`` and ``4.5.0 == 4.5`` must all hold, so an upgrade
from 4.2 is offered and a 4.5.0 release is not offered again to 4.5. The
release date in About resolves from the ``v4.5`` GitHub release tag.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from app import release_date, update_release, version

ROOT = Path(__file__).resolve().parents[1]


class Version45Test(unittest.TestCase):
    def test_app_version_is_4_5(self):
        self.assertEqual(version.APP_VERSION, "4.5")
        self.assertEqual(version.windows_version(), "4.5.0.0")
        self.assertEqual(version.about()["version"], "4.5")

    def test_version_comparison(self):
        self.assertLess(version.version_tuple("4.2"), version.version_tuple("4.5"))
        self.assertEqual(version.version_tuple("4.5"), version.version_tuple("4.5"))
        self.assertEqual(version.version_tuple("4.5.0"), version.version_tuple("4.5"))
        self.assertLess(update_release.version_number("4.2"), update_release.version_number("4.5"))
        self.assertEqual(update_release.version_number("4.5"), update_release.version_number("4.5.0"))

    def test_release_date_uses_v45_tag(self):
        self.assertEqual(release_date.release_url(), release_date.release_url("4.5"))
        self.assertTrue(release_date.release_url().endswith("/releases/tags/v4.5"))
        self.assertEqual(release_date.REPOSITORY, update_release.REPOSITORY)

    def test_upgrade_from_42_is_offered_and_45_is_current(self):
        def payload(tag):
            return {"tag_name": tag, "assets": [{
                "name": f"MemuAI-Setup-{tag.lstrip('v')}.exe",
                "browser_download_url": f"https://github.com/MemuAI/MemuAI/releases/download/{tag}/MemuAI-Setup-{tag.lstrip('v')}.exe",
                "digest": "sha256:" + "0" * 64, "size": 100, "state": "uploaded"}]}

        offered = update_release.release_offer(payload("v4.5"), "4.2")
        self.assertTrue(offered["update_available"])
        self.assertEqual(offered["latest_version"], "4.5")
        self.assertEqual(offered["status"], "available")

        current = update_release.release_offer(payload("v4.5"), "4.5")
        self.assertFalse(current["update_available"])
        self.assertEqual(current["status"], "current")

        same = update_release.release_offer(payload("v4.5.0"), "4.5")
        self.assertFalse(same["update_available"])
        self.assertEqual(same["status"], "current")

    def test_installer_brand_and_version_resources_agree(self):
        html = (ROOT / "installer" / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn(f'id="brand-ver">{version.APP_VERSION}<', html)
        text = (ROOT / "memuai-version.txt").read_text(encoding="utf-8")
        self.assertIn(f"filevers={version.version_tuple()}", text)
        self.assertIn(f"'FileVersion', '{version.windows_version()}'", text)


if __name__ == "__main__":
    unittest.main()
