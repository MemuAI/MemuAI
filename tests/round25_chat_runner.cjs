
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const root = process.argv[2] || process.cwd();
const js = fs.readFileSync(path.join(root, 'app/pgstatic/app.js'), 'utf8');
function section(from, to) {
  const start = js.indexOf(from), end = js.indexOf(to, start + from.length);
  assert(start >= 0 && end > start, from);
  return js.slice(start, end);
}
const ctx = { window: {}, S: { workspaces: {} }, PG_LANG: 'ru', icon: () => '', isProjectMode: () => false,
  memeText: (ru) => ru, console };
vm.createContext(ctx);
vm.runInContext(section('const esc = ', 'const ICONS = {'), ctx);
vm.runInContext(section('const CODE_LANGUAGE_NAMES = {', 'function setTheme('), ctx);
vm.runInContext(section('function cappedChats(', 'let chatsSaveSoonTimer'), ctx);
const md = (text) => vm.runInContext('mdRender', ctx)(text);

// A fence opened or closed mid-line used to leave the «@@BLOCK0@@» marker in the paragraph.
let html = md('Код: ```js\nfoo()\n``` и всё');
assert(!html.includes('@@BLOCK'), html);
assert(html.includes('code-block-wrap') && html.includes('и всё'), html);

html = md('Вот:\n```python\nprint(1)');
assert(!html.includes('```'), html);
assert(html.includes('data-lang="python"'), html);

assert(md('**жирный *курсив* текст**').includes('<strong>жирный <em>курсив</em> текст</strong>'));
assert(md('строка\n*курсив*').includes('<br><em>курсив</em>'));

html = md('- [ ] задача\n- [x] готово');
assert(html.includes('class="md-task"') && html.includes('class="md-task done"') && !html.includes('[ ]'), html);

html = md('ссылка <https://example.com>');
assert(html.includes('<a href="https://example.com"') && !html.includes('&lt;') && !html.includes('&gt;'), html);

assert(md('[Foo](https://en.wikipedia.org/wiki/Foo_(bar)) x').includes('href="https://en.wikipedia.org/wiki/Foo_(bar)"'));
assert(!md('![x](https://evil.example/a.png)').includes('<img'));
assert(!md('snake__case__name и module/__init__.py').includes('<strong>'));
assert(md('\\(a+b\\)').includes('<code>a+b</code>'));

html = md('<img src=x onerror=alert(1)> [c](javascript:alert(2))\n```html\n<script>alert(3)</script>\n```');
assert(!/<img|<script|href="javascript/i.test(html), html);

const chats = Array.from({ length: 205 }, (_, i) => ({ id: 'c' + i, pinned: i === 204 }));
const kept = ctx.cappedChats(chats);
assert.strictEqual(kept.length, 200);
assert(kept.some((chat) => chat.id === 'c204'));
assert(!kept.some((chat) => chat.id === 'c199'));
console.log('PASS: markdown fences/emphasis/tasks/autolinks/XSS and pinned-aware chat cap.');
