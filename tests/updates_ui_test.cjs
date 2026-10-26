
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = readFileSync('app/pgstatic/app.js', 'utf8');
const controller = source.slice(source.indexOf('let updateInfo = null;'), source.indexOf('// Round 36 (perf): the status is polled'));

function fixture(enabled = true) {
  const calls = [], messages = [], timers = [];
  const button = {hidden: true, addEventListener() {}};
  const hub = {textContent: 'Connected'};
  const f = {
    calls, messages, button, timers,
    info: {status: 'current', update_available: false}, status: {status: 'idle'},
  };
  const context = vm.createContext({
    PG_LANG: 'en', document: {hidden: false},
    pref: () => enabled,
    $: id => id === '#btn-update-apply' ? button : id === '#hub-status-text' ? hub : null,
    toast: (text, kind) => messages.push({text, kind}),
    pt: () => 'Connected', setTimeout: (...args) => timers.push(args), pollUpdateStatus() {},
    api: async (path, options) => {
      calls.push({path, options});
      if (path.endsWith('/check')) return f.info;
      if (path.endsWith('/status')) return f.status;
      return {ok: true};
    },
  });
  vm.runInContext(controller, context);
  f.run = code => vm.runInContext(code, context);
  return f;
}

(async () => {
  const disabled = fixture(false);
  await disabled.run('checkForUpdates()');
  assert.equal(disabled.calls.length, 0, 'disabled automatic checks stay offline');
  await disabled.run('checkForUpdates(true)');
  assert.equal(disabled.calls.length, 1, 'manual check works when automatic checks are disabled');
  assert.match(disabled.messages[0].text, /latest version/);

  for (const [status, text] of [['no_releases', /No releases/], ['missing_installer', /no MemuAI installer/], ['error', /offline/]]) {
    const f = fixture();
    f.info = {status, update_available: false, latest_version: '3.9', error: 'offline'};
    await f.run('checkForUpdates(true)');
    assert.match(f.messages[0].text, text);
    assert.equal(f.calls.length, 1, 'failed/unavailable check never downloads an installer');
  }

  const available = fixture();
  available.info = {status: 'available', update_available: true, download_url: 'https://example.com/setup.exe', latest_version: '3.9'};
  await available.run('checkForUpdates()');
  assert.equal(available.calls.at(-1).path, '/api/pg/updates/download');
  assert.equal(available.run('updatePollingFast()'), true);
  available.status = {status: 'ready', version: '3.9'};
  await available.run('refreshUpdateStatus()');
  assert.equal(available.button.hidden, false, 'verified update exposes the install button');
  assert.equal(available.run('updatePollingFast()'), false, 'ready updates stop fast polling');
  available.calls.length = 0;
  await available.run('checkForUpdates(true)');
  assert.equal(available.calls.some(c => c.path.endsWith('/download')), false, 'ready current offer is not downloaded twice');

  const failed = fixture();
  failed.status = {status: 'error', error: 'checksum mismatch'};
  await failed.run('refreshUpdateStatus()');
  await failed.run('updateStatusChecked = 0; refreshUpdateStatus()');
  assert.equal(failed.messages.length, 1, 'a failed download is reported once');
  assert.equal(failed.button.hidden, true);
  assert.equal(failed.run('updatePollingFast()'), false);

  const boot = source.slice(source.indexOf('async function bootApp()'), source.indexOf('/* ==================== MEMUAI V3.0 SYSTEM MODULES'));
  assert(boot.indexOf('S.preferences = await api') < boot.indexOf('checkForUpdates()'), 'saved preferences load before the startup check');
  console.log('Update UI: automatic/manual checks, errors, download, install button, polling, startup preferences passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
