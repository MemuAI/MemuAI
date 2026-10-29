const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function check(filename) {
  const source = fs.readFileSync(filename, 'utf8');
  const start = source.indexOf('let changeTimer = 0;');
  const end = source.indexOf('function toastAction(', start);
  assert(start >= 0 && end > start, filename);
  let full = 0, playlists = 0, renders = 0, concurrent = 0, peak = 0, fail = false;
  const refresh = async () => {
    peak = Math.max(peak, ++concurrent);
    await new Promise(resolve => setTimeout(resolve, 20));
    concurrent--;
    if (fail) throw new Error('refresh failed');
  };
  const context = vm.createContext({ setTimeout, clearTimeout, Promise, S: { view: 'library' },
    refreshAll: async () => { full++; await refresh(); },
    loadPlaylists: async () => { playlists++; await refresh(); },
    rerender: () => renders++ });
  vm.runInContext(source.slice(start, end), context);
  const changed = context.libraryChanged;
  const timeout = promise => Promise.race([promise, new Promise((_, reject) => {
    const timer = setTimeout(() => reject(new Error('refresh caller stranded')), 2000);
    promise.finally(() => clearTimeout(timer)).catch(() => {});
  })]);
  await timeout(Promise.all([changed({ quiet: true }), changed({ playlistsOnly: true, now: true })]));
  assert.equal(full, 1, 'a full refresh must survive a later playlist-only request');
  assert.equal(playlists, 0);
  assert.equal(renders, 1, 'a visible refresh must survive a quiet request');
  const first = changed({ now: true, quiet: true });
  await new Promise(resolve => setTimeout(resolve, 5));
  const second = changed({ now: true, playlistsOnly: true, quiet: true });
  await timeout(Promise.all([first, second]));
  assert.equal(peak, 1, 'refresh batches must not overwrite each other out of order');
  fail = true;
  const failures = await timeout(Promise.allSettled([changed({ now: true }), changed({ now: true })]));
  assert(failures.every(result => result.status === 'rejected'), 'all callers must receive a refresh error');
  fail = false;
  await timeout(changed({ now: true, playlistsOnly: true }));
  console.log('PASS music refresh: coalesced callers, full/visible intent, serialized batches, failure recovery:', filename);
}

(async () => {
  const files = process.argv.slice(2);
  for (const filename of files.length ? files : [path.join(__dirname, '../app/module_builtins/music/feat-core.js')]) await check(filename);
})().catch(error => { console.error(error); process.exitCode = 1; });
