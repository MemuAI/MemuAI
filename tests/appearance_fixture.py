"""Build a local fixture of the REAL UI with mocked API responses, not a mock UI.
Run from the repository root: python tests/appearance_fixture.py /tmp/memuai-ui
No account data is read. The generated files are for offline tests only.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build_fixture(output: Path) -> Path:
    os.environ.setdefault("MEMUAI_DATA_DIR", str(output / "unused-data"))
    from app.pgdata import DEFAULT_PREFERENCES
    from app import themes
    output.mkdir(parents=True, exist_ok=True)
    (output / "themes.css").write_text(themes.theme_css(), encoding="utf-8")
    defaults = dict(DEFAULT_PREFERENCES)
    defaults.update(profile_name="Alex", profile_onboarded=True, updates_enabled=False,
                    app_font="sans", voice_mic_visible=True, voice_live_visible=True,
                    auto_pin_primary_models=False)
    bootstrap = '''<script>
const QA_DEFAULTS = __PREFS__;
const QA_THEMES = __THEMES__;
let qaPrefs = {...QA_DEFAULTS, ...JSON.parse(localStorage.getItem('qa_prefs') || '{}')};
window.__qa_requests = [];
window.fetch = async (url, options = {}) => {
  const path = String(url), post = (options.method || 'GET') === 'POST';
  const body = options.body ? JSON.parse(options.body) : {};
  window.__qa_requests.push({path, post});
  let value = {};
  if (path === '/api/pg/preferences') {
    if (post) { qaPrefs = {...qaPrefs, ...body}; localStorage.setItem('qa_prefs', JSON.stringify(qaPrefs)); }
    value = qaPrefs;
  } else if (path === '/api/pg/profiles') value = {active_id:'default', items:[{id:'default',name:'Alex',has_pin:false,bio:''}]};
  else if (path === '/api/pg/chats') value = {chats:[]};
  else if (path === '/api/work/projects') value = {active_id:'demo',root:'/demo',items:[{id:'demo',name:'MemuAI',path:'/demo'}]};
  else if (path === '/api/pg/hub') value = {online:true,enabled:true};
  else if (path === '/api/pg/state') value = {models:[{id:'demo-claude',name:'Claude Sonnet',provider:'Demo',caps:{vision:true,web_search:true,thinking:true}},{id:'demo-gemini',name:'Gemini',provider:'Demo',caps:{vision:true}}],image_models:[],memory:{enabled:true,facts:[]}};
  else if (path === '/api/pg/prompts') value = {prompts:[],default_id:'',disabled_for:[]};
  else if (path === '/api/voice/catalog') value = {stt_models:[],tts_models:[],windows_voices:[],models_dir:'/demo/models'};
  else if (path === '/api/pg/updates/status') value = {status:'idle'};
  else if (path === '/api/pg/updates/check') value = {update_available:false};
  else if (path === '/api/pg/schedules') value = {tasks:[]};
  else if (path === '/api/pg/skills') value = {skills:[]};
  else if (path === '/api/pg/themes') value = QA_THEMES;
  return new Response(JSON.stringify(value), {status:200,headers:{'Content-Type':'application/json'}});
};
</script>'''.replace('__PREFS__', json.dumps(defaults, ensure_ascii=False)).replace('__THEMES__', json.dumps(themes.catalog(), ensure_ascii=False))
    html = (ROOT / 'app/pgstatic/index.html').read_text(encoding='utf-8')
    html = html.replace('<head>', '<head>' + bootstrap)
    html = html.replace('href="/themes.css"', f'href="{(output / "themes.css").resolve().as_uri()}"')
    html = html.replace('href="/icon.png"', f'href="{(ROOT / "memuai-icon.png").as_uri()}"')
    for attribute in ('src', 'href'):
        html = html.replace(f'{attribute}="/', f'{attribute}="{(ROOT / "app/pgstatic").as_uri()}/')
    target = output / 'fixture.html'
    target.write_text(html, encoding='utf-8')
    return target


if __name__ == '__main__':
    print(build_fixture(Path(sys.argv[1]).resolve()))
