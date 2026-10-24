"""Round 35 (branches): chat branches and answer versions in the ordinary chat.

* the tree model — Python (app/chat_tree.py) and JS (pgstatic/branches.js core, in Node) behave the same:
  regenerate = new version, edit = new branch, switch, reveal a message deep in an inactive branch, normalize
  (orphaned forks, emptied versions), old linear chats stay untouched;
* Telegram: ↻ Retry / an edited prompt keep the old answer as a version, «◀ 2/3 ▶» row, switching;
* Mini App: regenerate keeps a version, «chats.get» marks messages with versions, «chats.version» switches;
* Ctrl+K search indexes other versions labelled «branch: other» (+ preview of that version);
* static wiring: script order, hooks in app.js / media-routing.js / export.js / search.js, EN without Cyrillic;
* end-to-end in headless Chromium with an offline model mock: regenerate keeps the old answer («‹ 2/2 ›»),
  switching back and forth, edit-and-resend makes a branch, the model request carries the ACTIVE branch only,
  «Ответвить чат отсюда», «Карта веток», Undo of a deleted version, a 400-message chat stays fast.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r35-branches-"))
for _key in ("NO_PROXY", "no_proxy"):
    os.environ[_key] = "127.0.0.1,localhost"

from app import chat_tree as T  # noqa: E402

PG = ROOT / "app" / "pgstatic"
BRANCHES_JS = PG / "branches.js"
NODE = shutil.which("node")


def M(role, content, at=0, **extra):
    row = {"role": role, "content": content, "createdAt": 1_700_000_000_000 + at}
    row.update(extra)
    return row


def contents(chat):
    return [m["content"] for m in chat["messages"]]


def sample():
    chat = {"id": "c1", "messages": [M("user", "q1", 1), M("assistant", "a1", 2), M("user", "q2", 3), M("assistant", "a2", 4)]}
    T.begin_version(chat, 3)
    chat["messages"].append(M("assistant", "a2b", 5))           # regenerate a2
    T.begin_version(chat, 0)
    chat["messages"] += [M("user", "q1x", 6), M("assistant", "a1x", 7)]   # edit q1 → a new branch
    return chat


class PythonTreeTest(unittest.TestCase):
    def test_old_chat_untouched(self):
        chat = {"id": "x", "messages": [M("user", "hi"), M("assistant", "hello")]}
        before = copy.deepcopy(chat)
        self.assertFalse(T.normalize(chat))
        self.assertEqual(T.info(chat), {})
        self.assertEqual(T.stats(chat), {"forks": 0, "versions": 0, "hidden": 0})
        self.assertEqual(chat, before)                       # no ids, no branches field — lazy migration
        self.assertIs(T.active_path(chat), chat["messages"])

    def test_regenerate_edit_switch_reveal(self):
        chat = sample()
        self.assertEqual(contents(chat), ["q1x", "a1x"])
        self.assertEqual(T.info(chat), {0: {"key": T.ROOT, "n": 2, "cur": 1}})
        self.assertTrue(T.switch_to(chat, T.ROOT, 0))
        self.assertEqual(contents(chat), ["q1", "a1", "q2", "a2b"])
        info = T.info(chat)
        self.assertEqual(info[3]["n"], 2)
        self.assertEqual(info[3]["cur"], 1)
        self.assertTrue(T.switch_to(chat, info[3]["key"], 0))
        self.assertEqual(contents(chat), ["q1", "a1", "q2", "a2"])
        self.assertFalse(T.switch_to(chat, info[3]["key"], 0))  # already there
        self.assertFalse(T.switch_to(chat, info[3]["key"], 7))  # out of range
        # switching the outer fork away and back keeps the inner choice
        T.switch_to(chat, T.ROOT, 1)
        T.switch_to(chat, T.ROOT, 0)
        self.assertEqual(contents(chat), ["q1", "a1", "q2", "a2"])
        # reveal a message that sits inside an inactive version of an inactive branch
        T.switch_to(chat, T.ROOT, 1)
        a2b = next(r["m"] for r in T.inactive_messages(chat) if r["m"]["content"] == "a2b")
        self.assertTrue(T.reveal(chat, a2b["id"]))
        self.assertEqual(contents(chat), ["q1", "a1", "q2", "a2b"])
        self.assertEqual(T.stats(chat), {"forks": 2, "versions": 2, "hidden": 3})
        self.assertEqual(T.version_note(chat, 3), "Версия 2/2")
        self.assertEqual(T.version_note(chat, 3, "en"), "Version 2/2")
        self.assertFalse(T.reveal(chat, "nope"))

    def test_normalize_emptied_and_orphaned(self):
        chat = sample()
        # deleting the only message of the active version shows the neighbouring one
        chat["messages"].pop()
        chat["messages"].pop()
        self.assertTrue(T.normalize(chat))
        self.assertEqual(contents(chat), ["q1", "a1", "q2", "a2b"])
        # deleting a fork key orphans its versions → dropped, the chat becomes linear again
        del chat["messages"][2]
        self.assertTrue(T.normalize(chat))
        self.assertNotIn("branches", chat)
        self.assertEqual(contents(chat), ["q1", "a1", "a2b"])

    def test_begin_version_nothing_to_keep(self):
        chat = {"messages": [M("user", "q")]}
        self.assertEqual(T.begin_version(chat, 1), "")
        self.assertNotIn("branches", chat)
        chat = {"messages": [M("user", "q"), M("user", "continue", internal=True)]}
        self.assertEqual(T.begin_version(chat, 1), "")      # internal-only suffix is not a version

    def test_fork_off(self):
        chat = sample()
        chat["contextSummary"], chat["contextSummaryThrough"] = "s", 1
        out = T.fork_off(chat, 0, "c2", "↳ x")
        self.assertEqual([m["content"] for m in out["messages"]], ["q1x"])
        self.assertNotIn("branches", out)
        self.assertEqual(out["parent_id"], "c1")
        self.assertEqual(out["contextSummaryThrough"], 1)

    def test_max_alts_capped(self):
        chat = {"messages": [M("user", "q"), M("assistant", "a0")]}
        for i in range(T.MAX_ALTS + 5):
            T.begin_version(chat, 1)
            chat["messages"].append(M("assistant", f"a{i + 1}"))
        fork = chat["branches"]["forks"][chat["messages"][0]["id"]]
        self.assertEqual(len(fork["alts"]), T.MAX_ALTS)
        self.assertEqual(fork["sel"], T.MAX_ALTS - 1)
        self.assertIsNone(fork["alts"][fork["sel"]]["m"])


def run_node(script: str):
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class JsCoreTest(unittest.TestCase):
    def test_js_core_matches_python(self):
        got = run_node(f"""
const B = require({json.dumps(str(BRANCHES_JS))});
const M = (r, c) => ({{ role: r, content: c }});
const C = (ch) => ch.messages.map((m) => m.content).join(",");
const chat = {{ messages: [M("user","q1"), M("assistant","a1"), M("user","q2"), M("assistant","a2")] }};
const out = {{}};
B.beginVersion(chat, 3); chat.messages.push(M("assistant","a2b"));
B.beginVersion(chat, 0); chat.messages.push(M("user","q1x"), M("assistant","a1x"));
out.after = C(chat); out.info = [...B.info(chat)].map(([i, v]) => [i, v.n, v.cur]);
B.switchTo(chat, "^", 0); out.back = C(chat);
const k = chat.messages[2].id; B.switchTo(chat, k, 0); out.inner = C(chat);
B.switchTo(chat, "^", 1);
const a2b = B.allMessages(chat).find((x) => x.m.content === "a2b").m;
out.reveal = B.reveal(chat, a2b.id); out.revealed = C(chat);
out.stats = B.stats(chat);
out.tree = B.tree(chat).map((n) => n.type);
out.forkAlts = B.tree(chat)[0].alts.map((a) => [a.active, a.count]);
chat.messages.pop(); out.norm = B.normalize(chat); out.normed = C(chat);
const plain = {{ messages: [M("user","x")] }}; out.plainNorm = B.normalize(plain); out.plainKeys = Object.keys(plain);
out.nothing = B.beginVersion({{ messages: [M("user","q")] }}, 1);
console.log(JSON.stringify(out));""")
        self.assertEqual(got["after"], "q1x,a1x")
        self.assertEqual(got["info"], [[0, 2, 1]])
        self.assertEqual(got["back"], "q1,a1,q2,a2b")
        self.assertEqual(got["inner"], "q1,a1,q2,a2")
        self.assertTrue(got["reveal"])
        self.assertEqual(got["revealed"], "q1,a1,q2,a2b")
        self.assertEqual(got["stats"], {"forks": 2, "versions": 2, "hidden": 3})
        self.assertEqual(got["tree"], ["fork"])
        self.assertEqual(got["forkAlts"], [[True, 4], [False, 2]])
        self.assertTrue(got["norm"])
        self.assertEqual(got["normed"], "q1,a1,q2,a2")         # emptied version → the neighbouring one
        self.assertFalse(got["plainNorm"])
        self.assertEqual(got["plainKeys"], ["messages"])
        self.assertEqual(got["nothing"], "")

    def test_js_reads_python_tree(self):
        """A chat changed in Telegram (Python) is understood by the window (JS) and vice versa."""
        chat = sample()
        got = run_node(f"""
const B = require({json.dumps(str(BRANCHES_JS))});
const chat = {json.dumps(chat)};
const info = [...B.info(chat)];
B.switchTo(chat, "^", 0);
console.log(JSON.stringify({{ info: info.map(([i, v]) => [i, v.n, v.cur]), chat }}));""")
        self.assertEqual(got["info"], [[0, 2, 1]])
        back = got["chat"]
        self.assertEqual(contents(back), ["q1", "a1", "q2", "a2b"])
        self.assertEqual(T.info(back)[3]["n"], 2)
        self.assertFalse(T.normalize(back))                 # JS left a consistent tree


class TelegramTest(unittest.TestCase):
    def setUp(self):
        from app import pgdata
        self.pgdata = pgdata
        pgdata.save_chats([])

    def test_retry_and_edit_keep_versions(self):
        from app import telegram_bot as tb
        chat = {"id": "tg-1", "title": "t", "messages": [M("user", "q", 1), M("assistant", "old", 2)], "updated": 1}
        self.pgdata.upsert_chat(chat)
        self.assertEqual(tb._predicted_versions(chat, True, False), (1, 2))
        self.assertIsNone(tb._predicted_versions(chat, False, False))
        self.assertTrue(tb._persist_turn("default", chat, {"role": "user", "content": "q", "createdAt": 3},
                                         {"role": "assistant", "content": "new", "createdAt": 3}, True, False))
        saved = next(c for c in self.pgdata.load_chats() if c["id"] == "tg-1")
        self.assertEqual(contents(saved), ["q", "new"])
        self.assertEqual(tb._last_turn_versions(saved)[1:], (1, 2))
        self.assertEqual(tb._predicted_versions(saved, True, False), (2, 3))
        # edited prompt → a branch at the question
        self.assertTrue(tb._persist_turn("default", saved, {"role": "user", "content": "q2", "createdAt": 4},
                                         {"role": "assistant", "content": "ans2", "createdAt": 4}, False, True))
        saved = next(c for c in self.pgdata.load_chats() if c["id"] == "tg-1")
        self.assertEqual(contents(saved), ["q2", "ans2"])
        self.assertEqual(T.info(saved)[0]["n"], 2)
        # ◀ switches the last turn's fork; the chat in MemuAI follows
        back = tb._switch_version("default", "tg-1", -1)
        self.assertEqual(contents(back), ["q", "new"])
        # now the last turn's own fork is the answer's (old / new): ◀ goes to the old answer, then nothing further
        self.assertEqual(contents(tb._switch_version("default", "tg-1", -1)), ["q", "old"])
        self.assertIsNone(tb._switch_version("default", "tg-1", -1))
        kb = tb._answer_keyboard({}, "tg-1", "m", None, temp=False, tts=False, versions=(1, 3))
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("версия 2/3", texts)
        datas = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("ver:tg-1:-1", datas)
        kb = tb._answer_keyboard({}, "tg-1", "m", None, temp=True, tts=False, versions=(1, 3))
        self.assertNotIn("версия 2/3", [b["text"] for row in kb["inline_keyboard"] for b in row])


class MiniAppTest(unittest.TestCase):
    def test_regenerate_keeps_version(self):
        from app import pgdata, tgapp_chat
        pgdata.save_chats([])
        chat = {"id": "tga-1", "title": "t", "messages": [M("user", "q", 1), M("assistant", "old", 2)], "updated": 1}
        pgdata.upsert_chat(chat)
        user_row = dict(chat["messages"][0])
        tgapp_chat._persist(chat, user_row, {"role": "assistant", "content": "new", "createdAt": 9}, "", "m", "default", "")
        saved = next(c for c in pgdata.load_chats() if c["id"] == "tga-1")
        self.assertEqual(contents(saved), ["q", "new"])
        self.assertEqual(T.info(saved)[1]["n"], 2)

    def test_api_marks_versions_and_switches(self):
        src = (ROOT / "app" / "tgapp_api.py").read_text(encoding="utf-8")
        self.assertIn('item["versions"]', src)
        extras = (ROOT / "app" / "tgapp_extras.py").read_text(encoding="utf-8")
        self.assertIn('@op("chats.version")', extras)
        self.assertIn("chat_tree.begin_version(row, index)", extras)
        js = (ROOT / "app" / "tgapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn('api("chats.version"', js)
        self.assertIn("versionsHtml(C, m)", js)


class SearchTest(unittest.TestCase):
    def test_other_versions_indexed(self):
        from app import search_index as si
        chat = sample()
        chat["title"] = "Кофейня"
        docs = si.ChatsProvider._docs(chat, "", "", {})
        other = [d for d in docs if d.key.startswith("msgv:")]
        texts = {d.text for d in other}
        self.assertTrue({"q1", "a1", "q2", "a2b"} <= texts)
        self.assertNotIn("q1x", texts)                       # the active one is a normal hit
        hit = next(d for d in other if d.text == "a2b")
        self.assertEqual(hit.meta["branch"], "other")
        self.assertEqual(hit.open["branch"], "other")
        self.assertTrue(hit.open["message_id"])
        self.assertIn("branch", si._PUBLIC_META)
        self.assertTrue(si._branch_sig(chat))
        self.assertEqual(si._branch_sig({"messages": []}), "")


class StaticWiringTest(unittest.TestCase):
    def test_wiring(self):
        html = (PG / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index("/branches.js"), html.index("/app.js"))
        self.assertIn("/branches.css", html)
        app = (PG / "app.js").read_text(encoding="utf-8")
        self.assertIn("MemuBranches.keepVersion(userIndex + 1)", app)       # regenerate = new version
        self.assertIn("MemuBranches.keepVersion(index)", app)               # edit = new branch
        self.assertIn("MemuBranches.switchHtml(brInfo, i)", app)
        self.assertIn("MemuBranches.normalizeChat(c)", app)
        self.assertIn("_branchSig", app)                                    # no stringify of big branches
        self.assertIn("MemuBranches.keepVersion(userIndex + 1)", (PG / "media-routing.js").read_text(encoding="utf-8"))
        self.assertIn("otherVersions()", (PG / "export.js").read_text(encoding="utf-8"))
        self.assertIn("MemuBranches.revealMessage(o.message_id", (PG / "search.js").read_text(encoding="utf-8"))
        self.assertIn("branch_map", (PG / "hotkeys.js").read_text(encoding="utf-8"))

    def test_english_has_no_cyrillic(self):
        src = BRANCHES_JS.read_text(encoding="utf-8")
        pairs = re.findall(r'tr\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"', src)
        self.assertGreater(len(pairs), 15)
        for ru, en in pairs:
            self.assertFalse(re.search("[А-Яа-яЁё]", en), en)
        # tr() with a computed string: the EN branch of the few template ones
        for en in re.findall(r'"(Forks: [^"]*)"', src):
            self.assertFalse(re.search("[А-Яа-яЁё]", en))

    def test_build_lists_module(self):
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn("chat_tree", build)
        for spec in ("MemuAI.spec", "MemuAIProxy.spec"):
            self.assertIn("app.chat_tree", (ROOT / spec).read_text(encoding="utf-8"), spec)


# --------------------------------------------------------------------------- end to end (headless Chromium)

def _chromium():
    for path in ("/usr/bin/chromium", "/workspace/work/chromium/chrome"):
        if os.path.exists(path):
            return path
    return None


@unittest.skipUnless(_chromium(), "chromium is not available")
class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed")
        from tests.ui_lang_scan import Servers
        from app import pgdata
        cls.servers = Servers().start()
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test"})
        big = {"id": "c-big", "title": "big", "updated": 1, "mode": "chat", "messages": []}
        for i in range(200):
            big["messages"] += [M("user", f"вопрос {i}", i * 10), M("assistant", f"ответ **{i}**\n\n- a\n- b", i * 10 + 5)]
        T.begin_version(big, 101)
        big["messages"] += [M("assistant", f"ветка {k}", 9000 + k) for k in range(299)]
        pgdata.save_chats([big])
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 800})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(cls.servers.pg + "/", wait_until="load", timeout=90000)   # the Inbox SSE stream never idles
        for _ in range(300):
            if cls.page.evaluate("typeof S !== 'undefined' && !!S.models && S.models.length > 0"):
                break
            cls.page.wait_for_timeout(200)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.servers.stop()

    def js(self, code):
        return self.page.evaluate(code)

    def wait_idle(self, timeout=60.0):
        import time
        end = time.time() + timeout
        self.page.wait_for_timeout(150)
        while time.time() < end:
            if self.js("!generationJobs.has(S.currentId) && !S.messages.some((m) => m.streaming)"):
                return
            self.page.wait_for_timeout(150)
        raise AssertionError("the answer did not finish")

    def test_flow(self):
        from tests import mock_custom
        page = self.page
        model = self.js("S.models.find((m) => /gpt-test/.test(m.id)).id")
        self.js(f"setCurrentModel({json.dumps(model)}); newChat();")
        page.fill("#input", "Первый вопрос")
        self.js("send()")
        self.wait_idle()
        self.assertEqual(self.js("S.messages.length"), 2)
        self.js("S.messages[1].content = 'СТАРЫЙ-ОТВЕТ'; persistChat(); renderMessages();")
        # regenerate → a new version, the old one stays
        self.js("retryAnswerAt(1)")
        self.wait_idle()
        self.assertEqual(self.js("document.querySelector('.br-switch .br-count').textContent"), "2/2")
        self.assertNotEqual(self.js("S.messages[1].content"), "СТАРЫЙ-ОТВЕТ")
        self.js("document.querySelector('.br-switch [data-br-step=\"-1\"]').click()")
        self.assertEqual(self.js("S.messages[1].content"), "СТАРЫЙ-ОТВЕТ")
        self.assertEqual(self.js("document.querySelector('.br-switch .br-count').textContent"), "1/2")
        # the next question goes to the model with the ACTIVE branch only
        page.fill("#input", "Второй вопрос")
        self.js("send()")
        self.wait_idle()
        sent = json.dumps(mock_custom.state["last_body"]["messages"], ensure_ascii=False)
        self.assertIn("СТАРЫЙ-ОТВЕТ", sent)
        self.assertNotIn("ответ мока", sent)                 # the regenerated (inactive) answer is not sent
        self.assertEqual(self.js("S.messages.length"), 4)
        # switching the first answer's version hides the second question (it lives in version 1)
        self.js("document.querySelector('.br-switch [data-br-step=\"1\"]').click()")
        self.assertEqual(self.js("S.messages.length"), 2)
        self.js("document.querySelector('.br-switch [data-br-step=\"-1\"]').click()")
        self.assertEqual(self.js("S.messages.length"), 4)
        # edit-and-resend the second question → a branch at it
        self.js("editMessage(2)")
        page.fill("#edit-message-text", "Второй вопрос, исправленный")
        page.click("#edit-resend")
        self.wait_idle()
        self.assertEqual(self.js("S.messages[2].content"), "Второй вопрос, исправленный")
        self.assertEqual(self.js("MemuBranches.currentInfo().get(2).n"), 2)
        sent = json.dumps(mock_custom.state["last_body"]["messages"], ensure_ascii=False)
        self.assertIn("исправленный", sent)
        self.assertEqual(sent.count("Второй вопрос"), 1)      # the old question (other branch) is not sent
        # stored on the server with the tree
        self.js("saveLocalChats()")
        page.wait_for_timeout(1200)
        from app import pgdata
        stored = next(c for c in pgdata.load_chats() if c["id"] == self.js("S.currentId"))
        self.assertEqual(T.stats(stored)["forks"], 2)
        # the branch map: click an inactive message → its branch opens
        self.js("MemuBranches.openMap()")
        self.assertGreaterEqual(self.js("document.querySelectorAll('#panel .br-fork').length"), 2)
        page.click("#panel .br-node:not(.br-active) >> text=Второй вопрос")
        page.wait_for_timeout(300)
        self.assertEqual(self.js("S.messages[2].content"), "Второй вопрос")
        # deleting a version's message shows the neighbour; Undo brings the exact tree back
        n_before = self.js("S.messages.length")
        self.js("removeMessage(S.messages.length - 1); removeMessage(S.messages.length - 1);")
        self.assertEqual(self.js("S.messages[2].content"), "Второй вопрос, исправленный")
        # fork the chat from here → a new linear chat
        self.js("branchAt(1)")
        self.assertEqual(self.js("S.messages.length"), 2)
        self.assertFalse(self.js("!!currentChat().branches"))
        self.assertEqual(self.errors, [])
        self.assertGreater(n_before, 0)

    def test_failed_answer_is_not_a_version(self):
        model = self.js("S.models.find((m) => /gpt-test/.test(m.id)).id")
        self.js(f"setCurrentModel({json.dumps(model)}); newChat();")
        self.page.fill("#input", "Вопрос с ошибкой")
        self.js("send()")
        self.wait_idle()
        self.js("Object.assign(S.messages[1], { content: '', error: 'HTTP 503' }); persistChat(); renderMessages();")
        self.js("retryAnswerAt(1)")
        self.wait_idle()
        self.assertFalse(self.js("!!currentChat().branches"))          # «Ответа нет» → «Повторить» replaces it
        self.assertEqual(self.js("document.querySelectorAll('.br-switch').length"), 0)

    def test_miniapp_versions(self):
        import time
        import urllib.parse
        from app import pgdata, tg_webapp
        chat = {"id": "c-tg", "title": "Мини-чат", "updated": int(time.time() * 1000), "mode": "chat",
                "messages": [M("user", "вопрос", 1), M("assistant", "ответ-1", 2)]}
        T.begin_version(chat, 1)
        chat["messages"].append(M("assistant", "ответ-2", 3))
        pgdata.upsert_chat(chat)
        srv = self.servers
        pgdata.save_preferences({"telegram_bot_token": srv.TG_TOKEN, "telegram_chat_id": str(srv.TG_USER), "language": "ru"})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
        init = tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH-t",
                                         "user": {"id": srv.TG_USER, "first_name": "T", "language_code": "ru"}}, srv.TG_TOKEN)
        hp = urllib.parse.urlencode({"tgWebAppData": init, "tgWebAppVersion": "7.10", "tgWebAppPlatform": "android"})
        ctx = self.browser.new_context(viewport={"width": 390, "height": 760}, bypass_csp=True)
        try:
            page = ctx.new_page()
            page.goto(f"{srv.tg}/tgapp/#{hp}")
            page.wait_for_selector("#app:not([hidden]) .tab", timeout=20000)
            page.evaluate("document.querySelector('.tab[data-tab=\"chats\"]')?.click()")
            page.wait_for_timeout(600)
            page.click("text=Мини-чат")
            page.wait_for_selector(".ver-switch", timeout=10000)
            self.assertEqual(page.evaluate("document.querySelector('.ver-switch b').textContent"), "2/2")
            page.click(".ver-switch button >> nth=0")
            page.wait_for_timeout(1200)
            self.assertEqual(page.evaluate("document.querySelector('.ver-switch b').textContent"), "1/2")
            stored = next(c for c in pgdata.load_chats() if c["id"] == "c-tg")
            self.assertEqual(contents(stored), ["вопрос", "ответ-1"])
        finally:
            ctx.close()

    def test_big_chat_fast(self):
        self.js("openChat('c-big')")
        self.page.wait_for_timeout(500)
        t = self.js("(() => { const t = performance.now(); for (let i = 0; i < 10; i++) renderMessages(); return (performance.now() - t) / 10; })()")
        self.assertLess(t, 120, f"renderMessages on 400 messages took {t:.1f} ms")
        self.assertEqual(self.js("S.messages.length"), 400)
        self.assertEqual(self.js("[...MemuBranches.currentInfo().keys()]"), [101])
        t = self.js("(() => { const t = performance.now(); for (let i = 0; i < 1000; i++) MemuBranches.currentInfo(); return performance.now() - t; })()")
        self.assertLess(t, 50)
        self.js("MemuBranches.step(MemuBranches.currentInfo().get(101).key, 101, -1)")
        self.assertEqual(self.js("S.messages.length"), 400)
        self.assertEqual(self.js("S.messages[101].content"), "ответ **50**\n\n- a\n- b")


if __name__ == "__main__":
    unittest.main()
