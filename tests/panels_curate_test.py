"""Round22 AI-curated feeds (app/panels_curate.py): source planning by topic and language, safe feed and
page parsing, URL canonicalisation, same-story clustering across sources, significance scoring (authority,
corroboration, engagement, recency, fluff/rumour), the model rubric, diversity, the seen-store that stops
repeats across refreshes, and the panels.py integration — all on fixtures, no network."""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import panels, panels_curate as pc, pgdata

NOW = 1_791_400_000.0

def rss(items, title="Feed"):
    rows = "".join(f"<item><title>{t}</title><link>{u}</link><pubDate>{d}</pubDate><description>{s}</description></item>"
                   for t, u, d, s in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>{title}</title>{rows}</channel></rss>'

def when(hours_ago):
    from email.utils import formatdate
    return formatdate(NOW - hours_ago * 3600)

OPENAI = rss([("GPT-6 and Intelligent UI for everyone", "https://openai.com/index/gpt-6-for-everyone/?utm_source=rss", when(5), "We are launching GPT-6."),
              ("How Acme uses ChatGPT Enterprise", "https://openai.com/index/acme-customer-story/", when(3), "Customer story."),
              ("Introducing the OpenAI Academy webinar series", "https://openai.com/index/academy-webinars/", when(2), "Join us."),
              ("Old news", "https://openai.com/index/old/", when(24 * 9), "Ancient.")])
VERGE = rss([("Anthropic releases Claude Haiku 5.5, its fastest model yet", "https://www.theverge.com/ai/123/claude-haiku-5-5", when(9), "Anthropic launched Claude Haiku 5.5."),
             ("OpenAI launches GPT-6 for everyone", "https://www.theverge.com/ai/124/gpt-6", when(4), "GPT-6 is here."),
             ("Best robot vacuums of 2026", "https://www.theverge.com/23/best-robot-vacuum", when(6), "Our picks.")])
HN = {"hits": [
    {"title": "Claude Haiku 5.5", "url": "https://www.anthropic.com/claude-haiku-5-5", "points": 784, "num_comments": 400,
     "created_at_i": int(NOW - 11 * 3600), "objectID": "1"},
    {"title": "Margaret Hamilton has died", "url": "https://news.mit.edu/2026/hamilton", "points": 1122, "num_comments": 90,
     "created_at_i": int(NOW - 20 * 3600), "objectID": "2"},
    {"title": "Show HN: an open-weights 2B decision model", "url": "https://strands.example/decider", "points": 275,
     "num_comments": 50, "created_at_i": int(NOW - 30 * 3600), "objectID": "3"},
]}
REDDIT = """<feed xmlns="http://www.w3.org/2005/Atom"><title>top</title>
<entry><title>Claude Haiku 5.5 is out — benchmarks inside</title><link href="https://www.reddit.com/r/LocalLLaMA/comments/x1/"/>
<updated>2026-10-07T01:00:00+00:00</updated><content type="html">&lt;a href="https://www.anthropic.com/claude-haiku-5-5"&gt;[link]&lt;/a&gt;</content></entry>
<entry><title>Rumor: DeepSeek V5 leaked weights could launch next week</title><link href="https://www.reddit.com/r/LocalLLaMA/comments/x2/"/>
<updated>2026-10-07T02:00:00+00:00</updated><content type="html">text post</content></entry></feed>"""
ANTHROPIC_PAGE = """<html><body>
<a href="/news/claude-haiku-5-5"><h3>Introducing Claude Haiku 5.5</h3></a>
<a href="/news/barclays-scales-claude"><h3>Barclays scales Claude across the bank</h3></a>
<a href="/news/older-1">Older one</a><a href="/news/older-2">Older two</a><a href="/careers">Careers</a>
<a href="https://evil.example/news/x">x</a></body></html>"""

class FakeNet:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __call__(self, url):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value if isinstance(value, str) else json.dumps(value)
        raise ValueError(f"HTTP 404 {url}")

def ai_plan(**kw):
    plan = pc.plan_for("Новости ИИ каждое утро", out_lang="ru", **kw)
    plan["feeds"] = [f for f in plan["feeds"] if f["name"] in ("OpenAI", "The Verge")]
    plan["pages"] = [p for p in plan["pages"] if p["name"] == "Anthropic"]
    plan["reddit"] = ["LocalLLaMA"]
    return plan

NET = {"openai.com/news/rss.xml": OPENAI, "theverge.com/rss": VERGE, "hn.algolia.com": HN,
       "reddit.com/r/LocalLLaMA": REDDIT, "anthropic.com/news": ANTHROPIC_PAGE}

class PlanTest(unittest.TestCase):
    def test_topic_and_language_are_independent_of_the_ui(self):
        plan = pc.plan_for("Новости ИИ каждое утро, топ-5", out_lang="ru")
        self.assertEqual(plan["topic"], "ai")
        self.assertEqual(plan["count"], 5)
        self.assertEqual(plan["out_lang"], "ru")
        hosts = {pc.host_of(f["url"]) for f in plan["feeds"]}
        self.assertTrue({"openai.com", "deepmind.google", "techcrunch.com", "theverge.com"} <= hosts)
        self.assertFalse(any(h.endswith(".ru") for h in hosts), "global AI news is English-first")
        self.assertTrue(plan["hn"] and plan["reddit"])
        self.assertTrue(all(q.isascii() for q in plan["queries"]), "specialised English queries")
        self.assertTrue(any(p["name"] == "Anthropic" for p in plan["pages"]), "official blog without RSS is read too")

    def test_detection_and_counts(self):
        self.assertEqual(pc.detect_topic("Что нового в нейросетях и LLM"), "ai")
        self.assertEqual(pc.detect_topic("крипто новости про биткоин"), "crypto")
        self.assertEqual(pc.detect_topic("цены на гречку в Твери"), "")
        self.assertTrue(pc.is_feed_brief("AI news every morning"))
        self.assertTrue(pc.is_feed_brief("Свежие релизы моделей"))
        self.assertFalse(pc.is_feed_brief("Курс золота"))
        self.assertEqual(pc.wanted_count("топ-3 новости"), 3)
        self.assertEqual(pc.wanted_count("7 главных новостей"), 7)
        self.assertEqual(pc.wanted_count("новости"), 5)

    def test_planner_extra_for_a_custom_topic_is_validated(self):
        plan = pc.plan_for("новости Твери", out_lang="ru", extra={
            "label": "Тверь", "global": False, "source_lang": "ru", "queries": ["Тверь новости сегодня", ""],
            "feeds": ["https://tver.example/rss", "javascript:alert(1)"], "subreddits": ["r/tver", "bad name!"],
            "keywords": ["тверь", "тверской"]})
        self.assertEqual(plan["topic"], "")
        self.assertEqual(plan["source_lang"], "ru")
        self.assertFalse(plan["global"])
        self.assertEqual([f["url"] for f in plan["feeds"]], ["https://tver.example/rss"])
        self.assertEqual(plan["reddit"], ["tver"])
        self.assertIn("тверь", plan["keywords"])
        self.assertIsNone(plan["hn"])

class ParseTest(unittest.TestCase):
    def test_feed_parsing_is_safe_and_complete(self):
        items = pc.parse_feed_items(OPENAI)
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0]["title"], "GPT-6 and Intelligent UI for everyone")
        self.assertAlmostEqual(items[0]["published"], NOW - 5 * 3600, delta=2)
        with self.assertRaises(ValueError):
            pc.parse_feed_items('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&e;</title></item></channel></rss>')
        reddit = pc.parse_feed_items(REDDIT)
        self.assertEqual(reddit[0]["links"], ["https://www.anthropic.com/claude-haiku-5-5"])

    def test_page_links(self):
        rows = pc.parse_page_links(ANTHROPIC_PAGE, "https://www.anthropic.com/news", r"^/news/[a-z0-9-]{4,}$")
        self.assertEqual([r["url"] for r in rows][:2], ["https://www.anthropic.com/news/claude-haiku-5-5",
                                                       "https://www.anthropic.com/news/barclays-scales-claude"])
        self.assertEqual(rows[0]["title"], "Introducing Claude Haiku 5.5")
        self.assertFalse(any("evil" in r["url"] or "careers" in r["url"] for r in rows))

    def test_canonical_url(self):
        self.assertEqual(pc.canonical_url("https://www.OpenAI.com/index/gpt-6/?utm_source=rss&utm_medium=x#top"),
                         "https://openai.com/index/gpt-6")
        self.assertEqual(pc.canonical_url("https://m.example.com/a/amp?id=3&fbclid=zz"), "https://example.com/a?id=3")
        self.assertEqual(pc.canonical_url("https://news.ycombinator.com/item?id=5&utm=1"), "https://news.ycombinator.com/item?id=5")

class ClusterTest(unittest.TestCase):
    def cand(self, title, url, kind="media", **kw):
        return pc.make_candidate(title, url, source=pc.host_of(url), kind=kind, **kw)

    def test_same_story_across_sources(self):
        a = self.cand("Claude Haiku 5.5", "https://news.ycombinator.com/item?id=1", kind="community",
                      target="https://www.anthropic.com/claude-haiku-5-5")
        b = self.cand("Anthropic releases Claude Haiku 5.5, its fastest model yet", "https://www.theverge.com/ai/123")
        c = self.cand("Introducing Claude Haiku 5.5", "https://www.anthropic.com/claude-haiku-5-5/", kind="official")
        d = self.cand("Best robot vacuums of 2026", "https://www.theverge.com/best-vacuum")
        e = self.cand("OpenAI launches GPT-6 for everyone", "https://www.theverge.com/ai/124")
        f = self.cand("GPT-6 and Intelligent UI for everyone", "https://openai.com/index/gpt-6", kind="official")
        groups = pc.cluster([a, b, c, d, e, f])
        as_titles = sorted(sorted(m["title"] for m in g) for g in groups)
        self.assertIn(sorted([a["title"], b["title"], c["title"]]), as_titles)
        self.assertIn(sorted([e["title"], f["title"]]), as_titles)
        self.assertIn([d["title"]], as_titles)

    def test_representative_prefers_primary_source(self):
        hn = self.cand("Claude Haiku 5.5", "https://news.ycombinator.com/item?id=1", kind="community",
                       target="https://www.anthropic.com/claude-haiku-5-5")
        verge = self.cand("Anthropic releases Claude Haiku 5.5", "https://www.theverge.com/ai/123")
        rep = pc.pick_representative([verge, hn])
        self.assertEqual(rep["url"], "https://www.anthropic.com/claude-haiku-5-5")
        self.assertEqual(rep["source"], "Anthropic")

class ScoreTest(unittest.TestCase):
    def test_fluff_rumour_and_corroboration(self):
        plan = ai_plan()
        big = [pc.make_candidate("GPT-6 and Intelligent UI for everyone", "https://openai.com/index/gpt-6", source="OpenAI",
                                 kind="official", published=NOW - 5 * 3600, scoped=True),
               pc.make_candidate("OpenAI launches GPT-6", "https://www.theverge.com/ai/124", source="The Verge", kind="media",
                                 published=NOW - 4 * 3600, scoped=True)]
        fluff = [pc.make_candidate("How Acme uses ChatGPT Enterprise", "https://openai.com/index/acme", source="OpenAI",
                                   kind="official", published=NOW - 3 * 3600, scoped=True)]
        rumor = [pc.make_candidate("Rumor: DeepSeek V5 leaked weights could launch", "https://www.reddit.com/r/x/1",
                                   source="r/LocalLLaMA", kind="community", published=NOW - 3 * 3600, rank=1)]
        s_big, s_fluff, s_rumor = (pc.score_cluster(g, plan, NOW) for g in (big, fluff, rumor))
        self.assertTrue(s_fluff["fluff"])
        self.assertTrue(s_rumor["rumor"])
        self.assertGreater(s_big["score"], s_fluff["score"] * 1.8)
        self.assertGreater(s_big["score"], s_rumor["score"])
        self.assertEqual(s_big["hosts"], 2)
        self.assertTrue(s_big["official"])

    def test_relevance_gate(self):
        plan = ai_plan()
        off = pc.make_candidate("Best robot vacuums of 2026", "https://example.com/vac", source="x", kind="media")
        on = pc.make_candidate("A new open-weights LLM tops the benchmark", "https://example.com/llm", source="x", kind="media")
        self.assertEqual(pc.relevance(off, plan), 0.0)
        self.assertEqual(pc.relevance(on, plan), 1.0)

    def test_diversity_caps_one_company(self):
        rows = [{"rep": {"url": f"https://openai.com/{i}"}, "company": "OpenAI", "final": 1 - i / 10} for i in range(4)]
        rows.append({"rep": {"url": "https://anthropic.com/x"}, "company": "Anthropic", "final": 0.5})
        rows.append({"rep": {"url": "https://theverge.com/y"}, "company": "Google", "final": 0.4})
        picked = pc.select_diverse(rows, 4)
        companies = [r["company"] for r in picked]
        self.assertLessEqual(companies.count("OpenAI"), 2)
        self.assertIn("Anthropic", companies)
        self.assertEqual(len(picked), 4)

class BuildFeedTest(unittest.TestCase):
    def build(self, state=None, now=NOW, ask=None, net=None, cadence=None):
        return asyncio.run(pc.build_feed(ai_plan(), state or {}, fetch_text=FakeNet(net or NET), ask_model=ask,
                                         cadence=cadence or {"kind": "daily", "at": "08:00"}, now=now))

    def test_pipeline_ranks_the_release_of_the_day_first_and_drops_junk(self):
        out = self.build()
        titles = [i["title"] for i in out["items"]]
        self.assertIn("Haiku 5.5", titles[0])
        haiku = out["items"][0]
        self.assertEqual(pc.host_of(haiku["url"]), "anthropic.com")
        self.assertEqual(haiku["badge"], "официально")
        self.assertGreaterEqual(haiku["n"], 3)
        self.assertTrue(any("GPT-6" in t for t in titles))
        joined = " ".join(titles)
        for junk in ("robot vacuum", "Acme uses", "Academy webinar", "Old news", "Margaret Hamilton", "Barclays"):
            self.assertNotIn(junk, joined)
        self.assertEqual(len([t for t in titles if "GPT-6" in t]), 1, "one story, not one per source")
        self.assertTrue(all(i["why"] for i in out["items"]), "every item says why it is here")
        self.assertTrue(all(s["url"].startswith("https://") for s in out["sources"]))
        self.assertEqual(out["stats"]["sources_failed"], 0)
        self.assertFalse(out["stats"]["model"])

    def test_seen_store_prevents_repeats_but_keeps_the_current_edition(self):
        first = self.build()
        state = first["state"]
        self.assertTrue(state["seen"])
        same_day = self.build(state=json.loads(json.dumps(state)), now=NOW + 3 * 3600)
        self.assertEqual([i["url"] for i in same_day["items"]][:2], [i["url"] for i in first["items"]][:2])
        next_day = self.build(state=json.loads(json.dumps(same_day["state"])), now=NOW + 24 * 3600)
        shown = {i["url"] for i in first["items"]}
        self.assertFalse(shown & {i["url"] for i in next_day["items"]}, "yesterday's stories do not come back")
        self.assertEqual(next_day["items"], [])
        self.assertTrue(next_day["note"])

    def test_major_update_returns_with_a_badge(self):
        first = self.build()
        state = json.loads(json.dumps(first["state"]))
        more = dict(NET)
        more["techcrunch.com"] = rss([("OpenAI GPT-6: everything you need to know", "https://techcrunch.com/gpt-6", when(1), "x")])
        more["arstechnica.com"] = rss([("GPT-6 is here and it is everywhere", "https://arstechnica.com/gpt-6", when(1), "y")])
        more["wired.com"] = rss([("Inside GPT-6", "https://www.wired.com/gpt-6", when(1), "z")])
        plan = ai_plan()
        plan["feeds"] += [pc._feed("https://techcrunch.com/feed", "TechCrunch", "media"),
                          pc._feed("https://arstechnica.com/feed", "Ars", "media"),
                          pc._feed("https://www.wired.com/feed", "Wired", "media")]
        out = asyncio.run(pc.build_feed(plan, state, fetch_text=FakeNet(more), now=NOW + 24 * 3600,
                                        cadence={"kind": "daily", "at": "08:00"}))
        gpt = [i for i in out["items"] if "GPT-6" in i["title"]]
        self.assertTrue(gpt and gpt[0]["badge"] == "обновление")

    def test_first_read_of_a_page_without_dates_counts_only_its_top(self):
        out = self.build()
        state = out["state"]
        self.assertIn("https://www.anthropic.com/news", state["baseline"])
        page = ANTHROPIC_PAGE.replace('<a href="/news/claude-haiku-5-5">',
                                      '<a href="/news/brand-new-model"><h3>Claude Opus 6 is here</h3></a><a href="/news/claude-haiku-5-5">')
        net = dict(NET)
        net["anthropic.com/news"] = page
        again = self.build(state=json.loads(json.dumps(state)), net=net, now=NOW + 3600)
        self.assertTrue(any("Opus 6" in i["title"] for i in again["items"]))

    def test_one_failing_source_is_not_a_wall_but_all_failing_is(self):
        net = dict(NET)
        net["openai.com/news/rss.xml"] = ValueError("HTTP 503")
        out = self.build(net=net)
        self.assertTrue(out["items"])
        self.assertEqual(out["stats"]["sources_failed"], 1)
        with self.assertRaises(RuntimeError):
            self.build(net={"nothing": ""})

    def test_model_rubric_translates_and_vetoes(self):
        prompts = []

        async def ask(system, user):
            prompts.append((system, user))
            lines = [json.loads(line) for line in user.split("\n") if line.startswith("{")]
            items = []
            for row in lines:
                if "Haiku" in row["title"]:
                    items.append({"id": row["id"], "significance": 9, "credible": True, "on_topic": True,
                                  "title": "Anthropic выпустила Claude Haiku 5.5", "summary": "Самая быстрая модель семейства.",
                                  "why": "крупный релиз", "company": "Anthropic"})
                elif "GPT-6" in row["title"]:
                    items.append({"id": row["id"], "significance": 9, "credible": True, "on_topic": True,
                                  "title": "OpenAI выпустила GPT-6", "summary": "Новая флагманская модель.", "why": "флагман",
                                  "company": "OpenAI"})
                else:
                    items.append({"id": row["id"], "significance": 2, "credible": True, "on_topic": True, "title": row["title"]})
            return {"items": items}

        out = self.build(ask=ask)
        self.assertTrue(out["stats"]["model"])
        self.assertEqual([i["title"] for i in out["items"]], ["Anthropic выпустила Claude Haiku 5.5", "OpenAI выпустила GPT-6"])
        self.assertIn("Russian", prompts[0][0])
        self.assertIn("fluff", prompts[0][0])

    def test_broken_model_falls_back_to_heuristics(self):
        async def ask(system, user):
            raise panels.Wall("blocked:model-error", "boom")

        out = self.build(ask=ask)
        self.assertTrue(out["items"])
        self.assertTrue(any(e.startswith("model:") for e in out["errors"]))

class PanelFeedIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pgdata, "MEMU_DIR", Path(self.tmp.name))
        self.patch.start()
        panels._TASKS.clear()
        self.saved = panels.context()
        net = FakeNet(NET)

        async def http_text(url, limit=0):
            return await net(url)

        self.net = net
        self.http = patch.object(panels, "_http_text", http_text)
        self.http.start()
        panels.set_context(panels.PanelContext())

    def tearDown(self):
        self.http.stop()
        self.patch.stop()
        panels.set_context(self.saved)
        panels._TASKS.clear()
        self.tmp.cleanup()

    def test_custom_news_brief_becomes_a_curated_feed_and_fills_at_creation(self):
        async def scenario():
            created = await panels.create_and_fill(template="custom", subject="Новости ИИ каждое утро", wait=10)
            return created

        panel = asyncio.run(scenario())
        self.assertEqual(panel["kind"], "news")
        self.assertEqual(panel["source"]["type"], "feed")
        self.assertEqual(panel["status"], "live", panel.get("wall"))
        self.assertIn("Haiku 5.5", panel["data"]["items"][0]["title"])
        self.assertTrue(panel["feed"]["seen"])
        public = panels.public_panel(panel)
        self.assertNotIn("feed", public, "the seen-store stays on the backend")
        self.assertTrue(any("hn.algolia.com" in url for url in self.net.calls))

    def test_model_list_spec_for_news_is_routed_to_the_pipeline(self):
        panel = panels.create_panel(subject="AI news headlines", start=False, spec={"blocks": [
            {"type": "title", "text": "AI"}, {"type": "list", "items": "{{headlines}}"}]},
            values={"headlines": [{"title": "invented"}]})
        self.assertEqual(panel["source"]["type"], "feed")
        self.assertEqual(panel["data"], {})

    def test_nothing_new_keeps_the_previous_items(self):
        panel = asyncio.run(panels.create_and_fill(template="news", params={"topic": "ai", "count": 10}, wait=10))
        first = panel["data"]["items"]
        self.assertTrue(first)

        self.net.routes = {"openai.com/news/rss.xml": OPENAI}

        def age(d):
            for p in d["panels"]:
                if p["id"] == panel["id"]:
                    for s in p["feed"]["seen"]:
                        s["first"] -= 2 * 86400
        panels.mutate(age)
        out = asyncio.run(panels.refresh_panel(panel["id"], force=True))
        self.assertEqual(out["data"]["items"], first)
        self.assertTrue(out["data"]["note"])

class PlainTextTest(unittest.TestCase):
    def test_no_space_before_punctuation_after_stripped_links(self):
        text = pc._plain('As promised <a href="x">here</a> , it is <b>Haiku 5.5</b> . The ( new ) model on .NET 8 .')
        self.assertEqual(text, "As promised here, it is Haiku 5.5. The (new) model on .NET 8.")

if __name__ == "__main__":
    unittest.main()
