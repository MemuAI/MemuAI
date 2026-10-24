import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../build/audit-js/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const start = source.indexOf('function wireOpenCodeFields(');
const end = source.indexOf('\nasync function loadSettings()', start);
assert(start > 0 && end > start);
const code = source.slice(start, end);
for (const prefix of ['af-', 'm-f-']) {
  const dom = new JSDOM(`<div class="field"><select id="${prefix}mode"><option>free</option><option>zen</option><option>go</option><option>gateway</option></select></div>`
    + ['api_key', 'base_url', 'api_protocol'].map(key => `<div class="field"><input id="${prefix}${key}"></div>`).join(''));
  const mounted = [];
  const context = vm.createContext({ document: dom.window.document,
    window: { MemuOpenCodeSetup: { mount: (prefix, options) => mounted.push({prefix, options}) } },
    api: () => {}, LANG: 'ru' });
  vm.runInContext(code, context);
  context.wireOpenCodeFields(prefix, prefix === 'm-f-' ? 'saved-account-id' : '');
  assert.equal(mounted.length, 1);
  assert.equal(mounted[0].options.accountId, prefix === 'm-f-' ? 'saved-account-id' : '');
  const mode = dom.window.document.getElementById(prefix + 'mode');
  const field = key => dom.window.document.getElementById(prefix + key);
  assert.equal(field('api_key').closest('.field').hidden, true);
  assert.equal(field('base_url').closest('.field').hidden, true);
  for (const next of ['go', 'zen', 'gateway', 'free']) {
    mode.value = next;
    mode.dispatchEvent(new dom.window.Event('change'));
    assert.equal(field('api_key').closest('.field').hidden, next === 'free');
    assert.equal(field('api_key').required, ['go', 'zen'].includes(next));
    assert.equal(field('base_url').closest('.field').hidden, next !== 'gateway');
    assert.equal(field('api_protocol').closest('.field').hidden, next !== 'gateway');
  }
}
assert(source.includes('if (a.provider === "opencode") wireOpenCodeFields("m-f-", a.id)'));
assert(source.includes('if (providerId === "opencode") wireOpenCodeFields("af-")'));
assert(source.includes('Числовые лимиты') || source.includes('u.note_en'));
console.log('PASS: OpenCode add/edit forms show exactly the fields required by Free / Zen / Go / gateway, with correct key requirements.');
