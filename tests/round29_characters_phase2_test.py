"""Phase-2 regression tests for r29c-characters (Characters module + import)."""
import unittest
from unittest.mock import patch

from app.playground_api import _ip_is_public, _require_public_response_peer, _safe_media_url

class MediaProxyPublicAddressTest(unittest.TestCase):
    def test_multicast_and_reserved_are_not_public(self) -> None:
        for bad in ("224.0.0.1", "239.255.255.250", "ff02::1", "ff0e::1", "240.0.0.1", "0.0.0.0",
                    "127.0.0.1", "10.0.0.1", "::ffff:127.0.0.1", "::ffff:224.0.0.1", "fe80::1%eth0"):
            self.assertFalse(_ip_is_public(bad), bad)
        for good in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"):
            self.assertTrue(_ip_is_public(good), good)

    def test_media_url_rejects_multicast_dns_answer(self) -> None:
        with patch("app.playground_api.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("239.1.2.3", 443))]):
            with self.assertRaises(ValueError):
                _safe_media_url("https://example.com/a.png")

    def test_connected_peer_multicast_rejected(self) -> None:
        class Stream:
            def get_extra_info(self, name):
                return ("224.0.0.251", 443) if name == "server_addr" else None
        with self.assertRaises(ValueError):
            _require_public_response_peer(type("R", (), {"extensions": {"network_stream": Stream()}})())

if __name__ == "__main__":
    unittest.main()

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

MODULE_JS = r"""
(async () => {
  try {
    await Memu.store.set("chats/a", { n: 1 });
    const back = await Memu.store.get("chats/a");
    const keys = await Memu.store.list("chats/");
    const preset = await Memu.asset("presets/p.json");
    let deltas = "";
    const job = Memu.complete({ model: "mock-chat-fast", messages: [{ role: "system", content: "s" }, { role: "user", content: "hello there" }], params: { thinking: true } },
      { onDelta: (d) => { deltas += d; }, onUsage: () => {} });
    const done = await job;
    const resolved = await Memu.fetchImport("https://chub.ai/characters/someone/some-card", { resolve: true });
    const card = await Memu.fetchImport("someone/some-card", { kind: "character" });
    const json = JSON.parse(atob(card.file.base64));
    let wrong = "";
    try { await Memu.fetchImport("https://chub.ai/lorebooks/someone/book", { kind: "character" }); } catch (e) { wrong = e.code || e.message; }
    let bad = "";
    try { await Memu.fetchImport("http://127.0.0.1/card.png"); } catch (e) { bad = e.code || e.message; }
    Memu.log("RESULT " + JSON.stringify({ back, keys, preset, text: done.text, deltas, reasoning: done.reasoning,
      resolved: resolved.source + ":" + resolved.id, name: json.data.name, type: card.type, wrong, bad }));
  } catch (e) { Memu.log("FAIL " + (e && e.message), "error"); }
})();
"""

@unittest.skipUnless(shutil.which("node"), "node is required")
class ModuleTestStandMocksTest(unittest.TestCase):
    """memu-module-test.js mocks Memu.complete / store / asset / fetchImport (net_import)."""

    def test_new_bridge_actions_are_mocked(self) -> None:
        from app import modules_ai_pack as pack
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = root / "netmod"
            (mod / "assets" / "presets").mkdir(parents=True)
            (mod / "manifest.json").write_text(json.dumps({
                "id": "netmod", "title": "Net", "version": "1.0", "icon": "download",
                "permissions": ["chat", "storage", "net-import"],
                "net_import": {"sources": ["chub", "chub-lorebook"]}}), encoding="utf-8")
            (mod / "index.html").write_text("<div id='x'></div>", encoding="utf-8")
            (mod / "style.css").write_text("#x{color:var(--text)}", encoding="utf-8")
            (mod / "main.js").write_text(MODULE_JS, encoding="utf-8")
            (mod / "assets" / "presets" / "p.json").write_text('{"name":"P"}', encoding="utf-8")
            harness = root / "memu-module-test.js"
            harness.write_text(pack.harness_source(), encoding="utf-8")
            proc = subprocess.run(["node", str(harness), str(mod), "--json"], capture_output=True, text=True, timeout=90)
            report = json.loads(proc.stdout)
            logs = report.get("logs") or []
            line = next((x for x in logs if x.startswith("RESULT ")), None)
            self.assertIsNotNone(line, proc.stdout[-2000:])
            data = json.loads(line[7:])
            self.assertEqual(data["back"], {"n": 1})
            self.assertEqual(data["keys"], ["chats/a"])
            self.assertEqual(data["preset"], {"name": "P"})
            self.assertTrue(data["text"].startswith("mock answer to: hello there"))
            self.assertEqual(data["deltas"], data["text"])
            self.assertTrue(data["reasoning"])
            self.assertEqual(data["resolved"], "chub:someone/some-card")
            self.assertEqual(data["type"], "character")
            self.assertEqual(data["name"], "Mock character")
            self.assertIn("лорбук", data["wrong"])
            self.assertIn("не распознана", data["bad"])

    def test_net_import_needs_permission(self) -> None:
        from app import modules_ai_pack as pack
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = root / "noperm"
            mod.mkdir()
            (mod / "manifest.json").write_text(json.dumps({"id": "noperm", "title": "N", "version": "1.0",
                                                          "permissions": ["storage"]}), encoding="utf-8")
            (mod / "index.html").write_text("<div></div>", encoding="utf-8")
            (mod / "main.js").write_text('Memu.fetchImport("a/b").then(()=>Memu.log("OK")).catch((e)=>Memu.log("DENIED " + e.message));', encoding="utf-8")
            harness = root / "memu-module-test.js"
            harness.write_text(pack.harness_source(), encoding="utf-8")
            proc = subprocess.run(["node", str(harness), str(mod), "--json", "--lang", "en"], capture_output=True, text=True, timeout=90)
            logs = json.loads(proc.stdout).get("logs") or []
            self.assertTrue(any("DENIED" in x and "net-import" in x for x in logs), logs)

ROOT = Path(__file__).resolve().parents[1]

class NetImportPromptLayerTest(unittest.TestCase):
    """The «Download from the internet?» question is asked from a module window, so it must be drawn
    above the module layer (z-index 300); the plain settings panel stays below module windows."""

    def test_prompt_is_above_module_windows(self) -> None:
        import re
        css = (ROOT / "app/pgstatic/modules.css").read_text(encoding="utf-8")
        layer = int(re.search(r"\.module-layer\s*\{[^}]*z-index:\s*(\d+)", css).group(1))
        over = re.search(r"#panel-back\.visible\.over-modules\s*\{\s*z-index:\s*(\d+)", css)
        self.assertIsNotNone(over)
        self.assertGreater(int(over.group(1)), layer)
        self.assertLess(int(over.group(1)), 400)
        js = (ROOT / "app/pgstatic/modules.js").read_text(encoding="utf-8")
        ask = js[js.index("function askNetImport"):js.index("function askNetImport") + 4000]
        self.assertIn('classList.add("over-modules")', ask)
        self.assertIn('classList.remove("over-modules")', ask)
        self.assertIn("shortLabel", ask)

class CharactersDefaultPersonaTest(unittest.TestCase):
    def test_default_persona_is_a_name_not_a_pronoun(self) -> None:
        i18n = json.loads((ROOT / "app/module_builtins/characters/i18n.json").read_text(encoding="utf-8"))
        self.assertEqual(i18n["en"]["default_user_name"], "User")
        self.assertEqual(i18n["ru"]["default_user_name"], "Пользователь")
        js = (ROOT / "app/module_builtins/characters/main.js").read_text(encoding="utf-8")

        self.assertIn('/^(Вы|You|Пользователь|User)$/.test(p.name', js)
