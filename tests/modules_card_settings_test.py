# -*- coding: utf-8 -*-
"""Module-card "Settings" button: it must OPEN the module settings, never
toggle them shut, and must not hand focus to the card header button.

Static contracts run everywhere. The browser part runs the REAL
``app/pgstatic/modules.js`` in headless Chromium (no Playwright needed:
``chrome-headless-shell --dump-dom``) against stubbed fetch endpoints and
checks the click sequence on a MemuMeet card:

- "Settings" on a collapsed card opens the inspector;
- "Settings" again keeps it open (before the fix it toggled it shut, so a
  card that was already expanded showed no visible change);
- the header button still toggles (tooltip/inspector behaviour preserved);
- after a "Settings" click focus stays on the Settings button itself, not
  on the header button whose clipped summary feeds the overflow tooltip;
- "Open", "..." and the enable switch keep working.

Skipped without a Chromium binary (MEMU_TEST_CHROMIUM may point at one).
The final live check in the real app belongs to the browser agent.
"""
from __future__ import annotations

import glob
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
PG = ROOT / "app" / "pgstatic"
sys.path.insert(0, str(ROOT))

MODULES_JS = (PG / "modules.js").read_text(encoding="utf-8")
MODULES_CSS = (PG / "modules-settings.css").read_text(encoding="utf-8")


def _chromium() -> str:
    candidates = [
        os.environ.get("MEMU_TEST_CHROMIUM", ""),
        "/usr/bin/chromium",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        "/workspace/work/chromium/chrome",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    # tests/__init__.py isolates HOME on purpose, so "~" does not point at
    # the real profile cache here; resolve the real home from the passwd
    # entry (and JARVIS_HOME) as well.
    homes = {os.path.expanduser("~"), os.environ.get("JARVIS_HOME", "")}
    try:
        import pwd

        homes.add(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        pass
    for home in homes:
        if not home:
            continue
        for candidate in glob.glob(
            os.path.join(home, ".cache", "ms-playwright", "**", "chrome-headless-shell"),
            recursive=True,
        ):
            if os.path.exists(candidate):
                return candidate
    return ""


HARNESS = """<!doctype html><html><head><meta charset="utf-8"><style>__CSS__</style></head><body>
<div id="module-slot-sidebar"></div><div id="module-slot-chats"></div><div id="module-slot-topbar"></div><div id="module-slot-composer"></div>
<div id="pane-modules"></div>
<div class="module-layer" id="module-layer" hidden>
  <div class="module-snap" id="module-snap" hidden></div>
  <section class="module-window" id="module-window" data-win="window" role="dialog" aria-modal="false" aria-labelledby="module-window-title">
    <header class="module-window-bar" id="module-window-bar" data-win="bar"><span class="module-window-icon" data-win="icon"></span><b id="module-window-title" data-win="title"></b><span id="module-window-status" data-win="status" class="module-window-status" hidden></span><div class="module-window-tools"><button type="button" id="module-window-menu" data-win="menu"></button><button type="button" id="module-window-min" data-win="min"></button><button type="button" id="module-window-full" data-win="full"></button><button type="button" id="module-window-close" data-win="close"></button></div></header>
    <div class="module-window-body" id="module-window-body" data-win="body"><iframe id="module-frame" data-win="frame" title="Module" sandbox="allow-scripts"></iframe><div class="module-window-loading" id="module-window-loading" data-win="loading" hidden></div></div>
  </section>
  <nav class="module-taskbar" id="module-taskbar" hidden></nav>
</div>
<pre id="out">PENDING</pre>
<script>
window.PG_LANG='ru';
window.__calls=[];
window.icon=function(n){return '<i>'+n+'</i>';};
window.toast=function(){};
window.showContextMenu=function(items){window.__calls.push({kind:'context-menu',n:items.length});};
window.fetch=async function(url,opts){
  const u=String(url);
  if(u==='/api/pg/modules') return {ok:true,json:async()=>({modules:[
    {id:'meet',title:'MemuMeet',title_en:'MemuMeet',summary:'Запись встреч и звонков: микрофон, системный звук и импорт аудио, транскрипт через настройки Live, конспект, решения и задачи. Очень длинное описание, которое точно обрежется в карточке.',summary_en:'x',version:'1.0',builtin:true,author:'MemuAI',enabled:true,launcher:{slot:'sidebar',hidden:false},permissions:['chat','media','models','storage'],resizable:true},
    {id:'demo',title:'Demo',summary:'Короткое описание',version:'1.0',builtin:false,enabled:true,launcher:{slot:'sidebar',hidden:false},permissions:[],resizable:true}
  ],removed:[]})};
  if(u==='/api/pg/modules/templates') return {ok:true,json:async()=>({permissions:[],slots:[],icons:[]})};
  if(u==='/api/pg/modules/window') return {ok:true,json:async()=>({window:{width:760,height:540,min_width:240,min_height:200,max_width:1600,max_height:1200,center:true,resizable:true,fullscreen:false,size_source:'default'}})};
  if(u==='/api/pg/modules/events?pending=1') return {ok:true,json:async()=>({events:[],cursor:0,acknowledged:0})};
  if(u==='/api/pg/modules/configure') {window.__calls.push({kind:'configure',body:JSON.parse(opts.body)}); return {ok:true,json:async()=>({module:{id:JSON.parse(opts.body).id,enabled:JSON.parse(opts.body).enabled}})};}
  if(u.indexOf('/ui-hints')>=0) return {ok:true,json:async()=>({seen:[]})};
  return {ok:true,json:async()=>({})};
};
</script>
<script>__MODULES_JS__</script>
<script>
(async()=>{
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 const out=[]; const q=s=>document.querySelector(s);
 const copyCalls=()=>JSON.parse(JSON.stringify(window.__calls));
 const snap=(label)=>{const c=q('[data-ms-row="meet"]'); if(!c) return {label,card:false};
   const hdr=c.querySelector('[data-ms-select]'); const set=c.querySelector('[data-ms-toggle]');
   const small=hdr?hdr.querySelector('small'):null;
   return {label, card:true,
     nestedInHeader: set ? !!set.closest('button.ms-module') : null,
     inspector: !!c.querySelector('#ms-inspector'),
     isOpenClass: c.classList.contains('is-open'),
     focusedCls: document.activeElement?document.activeElement.className:null,
     focusedTgl: document.activeElement?document.activeElement.dataset.msToggle||null:null,
     focusedSel: document.activeElement?document.activeElement.dataset.msSelect||null:null,
     summaryClamped: small ? (small.scrollHeight>small.clientHeight+1) : null,
     calls: copyCalls()};};
 try{
  await window.MemuModules.mountSettings('list'); await sleep(50);
  out.push(snap('initial'));
  q('[data-ms-toggle="meet"]').click(); await sleep(30); out.push(snap('after-settings-1'));
  q('[data-ms-toggle="meet"]').click(); await sleep(30); out.push(snap('after-settings-2'));
  q('[data-ms-select="meet"]').click(); await sleep(30); out.push(snap('after-header'));
  q('[data-ms-toggle="meet"]').click(); await sleep(30); out.push(snap('after-header-then-settings'));
  q('[data-module-open="meet"]').click(); await sleep(60); out.push({label:'after-open', openIds:window.MemuModules.openIds, layerHidden:q('#module-layer').hidden, calls: copyCalls()});
  await window.MemuModules.close('meet'); await sleep(30);
  q('[data-module-more="meet"]').click(); await sleep(30); out.push({label:'after-more', calls: copyCalls()});
  const sw=q('[data-module-enabled="meet"]'); sw.click(); await sleep(80);
  out.push({label:'after-switch', checked:sw.checked, calls: copyCalls()});
 }catch(e){ out.push({label:'ERROR', error:String(e&&e.stack||e)}); }
 document.getElementById('out').textContent=JSON.stringify(out,null,1);
})();
</script></body></html>"""


def _run_harness() -> list:
    chromium = _chromium()
    if not chromium:
        raise unittest.SkipTest("no Chromium binary (set MEMU_TEST_CHROMIUM)")
    html = HARNESS.replace("__CSS__", MODULES_CSS).replace(
        "__MODULES_JS__", MODULES_JS.replace("</script>", "<\\/script>")
    )
    with tempfile.TemporaryDirectory(prefix="memu-modules-card-") as tmp:
        page = Path(tmp) / "harness.html"
        page.write_text(html, encoding="utf-8")
        proc = subprocess.run(
            [
                chromium,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--allow-file-access-from-files",
                "--window-size=1200,800",
                "--virtual-time-budget=8000",
                "--dump-dom",
                page.as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
    match = re.search(r'<pre id="out">(.*?)</pre>', proc.stdout, re.S)
    assert match, f"harness produced no output (rc={proc.returncode}, stderr={proc.stderr[:300]})"
    return json.loads(match.group(1))


class StaticContractTest(unittest.TestCase):
    def test_settings_button_uses_open_only_handler(self):
        self.assertIn("const openSelect = (id) => {", MODULES_JS)
        self.assertIn("library.expanded = true;", MODULES_JS)
        self.assertIn(
            "button.onclick = () => openSelect(button.dataset.msToggle)",
            MODULES_JS,
        )
        self.assertIn(
            "button.onclick = () => toggleSelect(button.dataset.msSelect)",
            MODULES_JS,
            "the header button must keep the toggle behaviour",
        )
        self.assertIn("scrollIntoView({ block: 'nearest' })", MODULES_JS)

    def test_open_settings_button_has_active_style(self):
        self.assertIn('.ms-card-settings[aria-expanded="true"]', MODULES_CSS)


class BrowserCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.states = _run_harness()
        cls.by_label = {s["label"]: s for s in cls.states}
        assert "ERROR" not in cls.by_label, cls.by_label.get("ERROR")

    def test_settings_button_is_not_nested_in_header_button(self):
        self.assertFalse(self.by_label["initial"]["nestedInHeader"])

    def test_settings_opens_and_stays_open(self):
        self.assertFalse(self.by_label["initial"]["inspector"])
        self.assertTrue(self.by_label["after-settings-1"]["inspector"])
        self.assertTrue(self.by_label["after-settings-1"]["isOpenClass"])
        self.assertTrue(
            self.by_label["after-settings-2"]["inspector"],
            "a second Settings click must not close the settings",
        )

    def test_settings_focus_stays_on_settings_button(self):
        for label in ("after-settings-1", "after-settings-2", "after-header-then-settings"):
            state = self.by_label[label]
            self.assertIn("ms-card-settings", state["focusedCls"])
            self.assertEqual(state["focusedTgl"], "meet")
            self.assertIsNone(state["focusedSel"])

    def test_header_button_still_toggles(self):
        self.assertFalse(self.by_label["after-header"]["inspector"])
        self.assertEqual(self.by_label["after-header"]["focusedSel"], "meet")
        self.assertTrue(self.by_label["after-header-then-settings"]["inspector"])

    def test_summary_is_clipped_so_tooltip_mechanism_exists(self):
        self.assertTrue(self.by_label["initial"]["summaryClamped"])

    def test_other_card_buttons_keep_working(self):
        self.assertEqual(self.by_label["after-open"]["openIds"], ["meet"])
        self.assertFalse(self.by_label["after-open"]["layerHidden"])
        menus = [c for c in self.by_label["after-more"]["calls"] if c["kind"] == "context-menu"]
        self.assertTrue(menus, "the '...' button must open the actions menu")
        configures = [
            c for c in self.by_label["after-switch"]["calls"] if c["kind"] == "configure"
        ]
        self.assertEqual(len(configures), 1)
        self.assertEqual(configures[0]["body"], {"id": "meet", "enabled": False})
        self.assertFalse(self.by_label["after-switch"]["checked"])


if __name__ == "__main__":
    unittest.main()
