"""Round 36 (skills): fast GitHub catalog (parallel, pooled, Range heads, stale-while-revalidate, long-poll progress,
separate search limit) and a virtual, recycled catalog grid with tags that never get cut.

    python -m pytest tests/round36_skills_test.py -q
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r36-skills-")
for _key in ("NO_PROXY", "no_proxy"):
    os.environ[_key] = "127.0.0.1,localhost"

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app import skills_catalog as sc  # noqa: E402

REPOS = ["acme/one", "acme/two", "acme/three"]


def skill_md(r: int, i: int) -> str:
    lic = ["MIT", "Apache-2.0", "CC-BY-NC-SA-4.0"][i % 3]
    return f"---\nname: skill-{r}-{i}\ndescription: Skill {i} of repo {r}.\nlicense: {lic}\n---\n# Skill\n\nBody {i}.\n"


class BigGitHub:
    """Three repositories × N skills; records calls and the headers they carried."""

    def __init__(self, per_repo: int = 30, delay: float = 0.0):
        self.per_repo, self.delay = per_repo, delay
        self.calls: list = []
        self.lock = threading.Lock()
        self.active = self.peak = 0

    def __call__(self, url, headers):
        with self.lock:
            self.calls.append((url, dict(headers)))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            for ri, repo in enumerate(REPOS):
                if url == f"{sc.API}/repos/{repo}":
                    return 200, {"etag": '"m"'}, json.dumps({"full_name": repo, "stargazers_count": 10 * (ri + 1),
                                                              "default_branch": "main", "owner": {"login": "acme"}}).encode()
                if url.startswith(f"{sc.API}/repos/{repo}/git/trees/"):
                    tree = [{"path": f"skills/s{i}/SKILL.md", "type": "blob", "sha": f"{ri}-{i}", "size": 200}
                            for i in range(self.per_repo)]
                    return 200, {"etag": '"t"'}, json.dumps({"tree": tree}).encode()
                prefix = f"{sc.RAW}/{repo}/main/skills/s"
                if url.startswith(prefix):
                    text = skill_md(ri, int(url[len(prefix):].split("/")[0])).encode()
                    if headers.get("Range"):
                        return 206, {}, text[:sc.HEAD_BYTES]
                    return 200, {}, text
            if url.startswith(f"{sc.API}/search/"):
                return 200, {}, json.dumps({"items": []}).encode()
            return 404, {}, b"{}"
        finally:
            with self.lock:
                self.active -= 1


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc36-")
        self.fake = BigGitHub(delay=0.01)
        sources = [{"repo": r, "ru": r, "en": r, "official": i == 0} for i, r in enumerate(REPOS)]
        patches = [mock.patch.object(sc, "_http_get", self.fake), mock.patch.object(sc, "cache_dir", lambda: Path(self.tmp)),
                   mock.patch.object(sc, "SOURCES", sources),
                   mock.patch.dict(sc._rate, {"remaining": None, "reset": 0, "limited_until": 0, "search_until": 0}),
                   mock.patch.dict(sc._snap, {"data": None, "partial": None, "version": 0, "running": False, "loaded": False})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)


class CatalogSpeed(Base):
    def test_parallel_bounded_and_progressive(self):
        seen = []
        result = sc.catalog(on_progress=lambda part: seen.append(len(part["sources"])))
        self.assertEqual(len(result["skills"]), 90)
        self.assertTrue(result["complete"])
        self.assertEqual([s["repo"] for s in result["sources"]], REPOS, "sources keep the curated order")
        self.assertEqual(seen, [1, 2], "each finished source is reported before the end")
        self.assertGreater(self.fake.peak, 3, "repositories are fetched in parallel")
        self.assertLessEqual(self.fake.peak, sc.NET_SLOTS, "the pool is bounded")

    def test_list_reads_only_the_front_matter_and_blobs_are_cached_by_sha(self):
        sc.catalog()
        raw = [h for u, h in self.fake.calls if u.startswith(sc.RAW)]
        self.assertTrue(raw and all(h.get("Range") == f"bytes=0-{sc.HEAD_BYTES - 1}" for h in raw))
        before = len(self.fake.calls)
        sc.catalog(refresh=True)
        again = [u for u, _ in self.fake.calls[before:] if u.startswith(sc.RAW)]
        self.assertEqual(again, [], "an unchanged sha never downloads the SKILL.md again, even on refresh")

    def test_detail_still_reads_the_whole_file(self):
        detail = sc.skill_detail("acme/one", "skills/s3")
        self.assertIn("Body 3.", detail["body"])

    def test_search_limit_does_not_block_the_core_api(self):
        sc._rate["search_until"] = time.time() + 600
        result = sc.catalog()
        self.assertEqual(len(result["skills"]), 90)
        self.assertEqual(sc._rate["limited_until"], 0)

    def test_stale_while_revalidate_and_long_poll(self):
        first = sc.catalog_swr()
        self.assertTrue(first["refreshing"])
        view = first
        for _ in range(100):
            view = sc.catalog_swr(since=view["version"], wait=5)
            if not view["refreshing"]:
                break
        self.assertFalse(view["refreshing"])
        self.assertEqual(len(view["skills"]), 90)
        self.assertTrue((Path(self.tmp) / "catalog_snapshot.json").exists())
        # A new window process answers at once from the snapshot on disk, without any network call.
        sc._snap.update(data=None, loaded=False)
        before = len(self.fake.calls)
        started = time.time()
        warm = sc.catalog_swr()
        self.assertLess(time.time() - started, 0.5)
        self.assertEqual(len(warm["skills"]), 90)
        self.assertFalse(warm["refreshing"], "a fresh snapshot is not refetched")
        self.assertEqual(len(self.fake.calls), before)

    def test_stale_snapshot_is_shown_while_refreshing(self):
        sc.catalog_swr()
        while sc.catalog_swr(since=sc._snap["version"], wait=5)["refreshing"]:
            pass
        sc._snap["data"]["fetched_at"] = time.time() - sc.TTL_LIST - 10
        view = sc.catalog_swr()
        self.assertTrue(view["refreshing"])
        self.assertTrue(view["stale"])
        self.assertEqual(len(view["skills"]), 90, "the old catalog is shown, never an empty list")
        while sc.catalog_swr(since=sc._snap["version"], wait=5)["refreshing"]:
            pass


class FrontendStatic(unittest.TestCase):
    def test_virtual_grid_and_render_memo(self):
        js = (STATIC / "skills-ui.js").read_text(encoding="utf-8")
        self.assertNotIn("rows.slice(0, 400)", js, "the catalog is not cut at 400")
        self.assertIn("function withSkills", js)
        self.assertIn("{ passive: true }", js)
        self.assertIn("swr=1", js)
        self.assertNotRegex(js, r"setInterval\(")
        css = (STATIC / "skills-ui.css").read_text(encoding="utf-8")
        self.assertNotIn(".sk-card:has(", css)
        self.assertIn(".sk-vrow [hidden]", css)
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn("/skills-ui.js?v=1.0-r36", html)
        self.assertIn("/skills-ui.css?v=1.0-r36", html)


try:
    from round27_ui_lang_test import _chromium, _has_playwright  # noqa: E402
except Exception:  # pragma: no cover
    _chromium = lambda: ""  # noqa: E731
    _has_playwright = lambda: False  # noqa: E731

INJECT = """(n) => { const st = MemuSkillsUI.state; const base = st.catalog.skills; const out = [];
  for (let i = 0; i < n; i++) { const b = base[i % base.length];
    out.push({...b, id: b.id + '#' + i, name: b.name + '-' + i, has_scripts: i % 2 === 0, scripts: ['run.py'],
              license: i % 3 ? 'CC-BY-NC-SA-4.0 with a very long custom licence name' : 'MIT'}); }
  st.extra['big/repo'] = {source: {repo: 'big/repo'}, skills: out}; }"""


@unittest.skipUnless(_has_playwright() and _chromium(), "Playwright / Chromium not installed")
class LiveGrid(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import httpx
        from playwright.sync_api import sync_playwright
        from ui_lang_scan import Servers
        cls.cache = tempfile.mkdtemp(prefix="sc36-live-")
        sources = [{"repo": r, "ru": r, "en": r, "official": i == 0} for i, r in enumerate(REPOS)]
        cls._patches = [mock.patch.object(sc, "_http_get", BigGitHub()), mock.patch.object(sc, "cache_dir", lambda: Path(cls.cache)),
                        mock.patch.object(sc, "SOURCES", sources),
                        mock.patch.dict(sc._snap, {"data": None, "partial": None, "version": 0, "running": False, "loaded": False})]
        for p in cls._patches:
            p.start()
        cls.servers = Servers().start()
        cls.http = httpx.Client(timeout=30, trust_env=False)
        cls.http.post(cls.servers.pg + "/api/pg/preferences", json={
            "profile_onboarded": True, "profile_name": "Alex", "language": "ru", "updates_enabled": False, "ui_motion": "off"})
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.http.close()
        cls.servers.stop()
        for p in cls._patches:
            p.stop()
        shutil.rmtree(cls.cache, True)

    def test_thousands_of_skills_stay_light_and_tags_are_whole(self):
        page = self.browser.new_page(viewport={"width": 1100, "height": 760})
        self.addCleanup(page.close)
        errors: list = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(self.servers.pg + "/")
        page.wait_for_selector("body.boot-ready", timeout=90000)
        page.evaluate("showSkillsModal({tab: 'catalog'})")
        for _ in range(100):   # progressive load: wait until every source is in
            if page.evaluate("!!(MemuSkillsUI.state.catalog && MemuSkillsUI.state.catalog.complete !== false && !MemuSkillsUI.state.refreshing)"):
                break
            page.wait_for_timeout(100)
        page.evaluate(INJECT, 3000)
        page.click('[data-csource="all"]')
        page.wait_for_selector(".sk-vrow .sk-card")
        cards = page.locator(".sk-vrow .sk-card").count()
        self.assertLess(cards, 80, "only the rows in view are in the DOM")
        # Scroll to the very end: the last skill is reachable (no 400 cap) and the DOM did not grow.
        page.evaluate("(() => { const s = document.querySelector('#panel .sk-scroll'); s.scrollTop = s.scrollHeight; })()")
        page.wait_for_timeout(200)
        self.assertLess(page.locator(".sk-vrow .sk-card").count(), 80, "the DOM does not grow while scrolling")
        last = page.evaluate("Math.max(...[...document.querySelectorAll('.sk-vrow .sk-card:not([hidden]) [data-i]')].map(e => +e.dataset.i))")
        self.assertEqual(last, 3090 - 1)
        cut = page.evaluate("""[...document.querySelectorAll('.sk-vrow .sk-card:not([hidden]) .sk-badge:not([hidden])')]
          .filter(b => { const r = b.getBoundingClientRect(), c = b.closest('.sk-badges').getBoundingClientRect();
                         return b.scrollWidth > b.clientWidth + 1 || r.right > c.right + 0.5; }).length""")
        self.assertEqual(cut, 0, "no tag is cut")
        # Narrow cards fold tags that do not fit into «+N» with a hint.
        page.set_viewport_size({"width": 420, "height": 760})
        page.wait_for_timeout(300)
        more = page.locator(".sk-vrow .sk-more:not([hidden])")
        if more.count():
            self.assertRegex(more.first.inner_text(), r"^\+\d$")
            self.assertTrue(more.first.get_attribute("title"))
        cut = page.evaluate("""[...document.querySelectorAll('.sk-vrow .sk-card:not([hidden]) .sk-badge:not([hidden])')]
          .filter(b => { const r = b.getBoundingClientRect(), c = b.closest('.sk-badges').getBoundingClientRect();
                         return r.right > c.right + 0.5; }).length""")
        self.assertEqual(cut, 0)
        # Search filters the virtual list; the add button works through delegation.
        page.fill("#sk-q", "skill-1-7")
        page.wait_for_selector(".sk-vrow .sk-card:not([hidden])")
        names = page.eval_on_selector_all(".sk-vrow .sk-card:not([hidden]) .sk-card-name", "els => els.map(e => e.textContent)")
        self.assertTrue(names and all(re.search(r"skill 1 7", n, re.I) for n in names), names[:5])
        page.fill("#sk-q", "")
        page.locator('.sk-vrow [data-cadd="gh:acme/one/skills/s0"]').first.click()
        for _ in range(50):
            if page.evaluate("getSkills().some(s => s.source && s.source.repo === 'acme/one' && s.source.path === 'skills/s0')"):
                break
            page.wait_for_timeout(100)
        self.assertTrue(page.evaluate("getSkills().some(s => s.source && s.source.path === 'skills/s0')"))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
