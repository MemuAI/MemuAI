

const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

(async () => {
  const output = path.resolve(process.argv[3]); fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || process.env.PATH.split(path.delimiter).map(p=>path.join(p, process.platform==='win32'?'chromium.exe':'chromium')).find(p=>fs.existsSync(p)),headless:true,args:['--no-sandbox']});
  const context = await browser.newContext({viewport:{width:1440,height:900}, reducedMotion:'reduce'});
  const page = await context.newPage(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route(/^https?:/, route => route.abort());
  const fixture = pathToFileURL(path.resolve(process.argv[2])).href;
  let assertions = 0;
  function check(value, message) { assert.ok(value, message); assertions++; }
  async function boot() {
    await page.goto(fixture);
    await page.waitForFunction(() => document.body.classList.contains('boot-ready'));
    await page.waitForTimeout(350);
  }
  async function snapshot(name) {
    await page.waitForTimeout(100);
    await page.evaluate(() => {
      document.querySelector('.toast-container')?.replaceChildren();
      document.querySelectorAll('input').forEach(el => {
        if (el.checked) el.setAttribute('checked',''); else el.removeAttribute('checked');
        el.setAttribute('value', el.value);
      });
    });
    let html = await page.content();
    html = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
    fs.writeFileSync(path.join(output, name+'.html'), html);
    await page.screenshot({path:path.join(output, name+'.png')});
  }
  async function noViewportOverflow() {
    const result = await page.evaluate(() => {
      const result = [];
      for (const id of ['app','main-content','input','btn-send','panel']) {
        const node = document.getElementById(id), r = node.getBoundingClientRect();
        if (r.width && r.height && (r.left < -1 || r.right > innerWidth+1)) result.push(id);
      }
      return result;
    });
    check(!result.length, 'Viewport overflow: '+result.join(', '));
  }
  await boot();
  check(await page.locator('html').getAttribute('data-accent') === 'terracotta', 'default palette');
  await noViewportOverflow(); await snapshot('dark-home');
  await page.evaluate(() => {currentSettingsTab='appearance'; return showSettings();});
  check(await page.locator('[name="appearance-accent"]').count() === 12, 'twelve accent choices');
  for (const accent of ['blue','violet','teal','rose','graphite','terracotta']) {
    await page.locator(`label:has([name="appearance-accent"][value="${accent}"])`).click();
    check(await page.locator('html').getAttribute('data-accent') === accent, 'instant accent '+accent);
  }
  await page.locator('label:has([name="appearance-accent"][value="violet"])').click();
  await page.waitForTimeout(250);
  await page.locator('[name="appearance-accent"][value="violet"]').focus();
  await page.keyboard.press('ArrowRight');
  check(await page.locator('html').getAttribute('data-accent') === 'teal', 'native radio keyboard navigation');
  await page.locator('label:has([name="appearance-accent"][value="blue"])').click();
  await page.locator('label:has([name="appearance-width"][value="wide"])').click();
  check(await page.locator('html').getAttribute('data-chat-width') === 'wide', 'wide layout applied');
  await page.locator('label:has([name="appearance-theme"][value="light"])').click();
  check(await page.locator('html').getAttribute('data-theme') === 'light', 'light mode applied');
  await page.waitForTimeout(250);
  await boot();
  check(await page.locator('html').getAttribute('data-accent') === 'blue', 'accent survives reload');
  check(await page.locator('html').getAttribute('data-chat-width') === 'wide', 'width survives reload');
  check(await page.locator('html').getAttribute('data-theme') === 'light', 'mode survives reload');
  await page.evaluate(() => {
    S.messages=[{role:'user',content:'Помоги продумать спокойный и удобный интерфейс для рабочего чата.'},
      {role:'assistant',content:'## Начнём с главного\n\nХороший интерфейс оставляет пространство для мысли. Самое важное — **читаемый текст**, понятные действия и предсказуемая навигация.\n\n1. Разделим историю, диалог и инструменты.\n2. Акцентом выделим только ключевые действия.\n3. Сохраним удобство в небольшом окне.\n\n> Настройки должны помогать, а не отвлекать.\n\n```css\n.conversation {\n  max-width: 780px;\n  margin-inline: auto;\n}\n```',model:'demo-claude'}];
    renderMessages();
  });
  check(await page.locator('.code-block-wrap').count() === 1, 'completed fences render as code');
  check(await page.evaluate(() => hidePrivateProtocols('hello\n```memuai-tool\n{}\n```').trim() === 'hello'), 'internal protocol stays hidden');
  check(await page.evaluate(() => hidePrivateProtocols('hello\n```comp').trim() === 'hello'), 'partial opener stays hidden');
  await noViewportOverflow(); await snapshot('light-chat');
  const wide = await page.locator('.messages').boundingBox();
  await page.evaluate(() => {S.preferences.chat_width='comfortable';applyAppearancePrefs();});
  const regular = await page.locator('.messages').boundingBox();
  check(regular.width < wide.width, 'comfortable width actually narrows the conversation');
  await page.evaluate(() => {S.preferences.theme='dark';setTheme('dark',false);S.preferences.accent_color='violet';applyAppearancePrefs();currentSettingsTab='appearance';return showSettings();});
  await noViewportOverflow(); await snapshot('dark-settings');

  await page.keyboard.press('Escape');
  check(!await page.locator('#panel-back').evaluate(el=>el.classList.contains('visible')), 'Escape closes modal');
  await snapshot('dark-chat');
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(() => startDraft(false, false));
  await noViewportOverflow(); await snapshot('narrow-home');
  await page.evaluate(() => {currentSettingsTab='appearance';return showSettings();});
  await noViewportOverflow(); await snapshot('narrow-settings');
  await page.locator('label:has([name="appearance-theme"][value="light"])').click();
  await snapshot('narrow-light-settings');
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:420,height:360});
  await page.evaluate(() => {S.mode='work';S.draftMode='work';S.preferences.work_intro_seen=true;updateWorkUI();renderMessages();renderChips();});
  await noViewportOverflow();
  check(await page.locator('#btn-send').isVisible(), 'composer stays reachable in minimum window');
  await snapshot('minimum-work');
  await page.setViewportSize({width:1280,height:880});
  await page.evaluate(() => {S.mode='chat';S.tempChat=true;updateWorkUI();updateTempUI();renderMessages();renderChips();setTheme('dark',false);});
  await snapshot('temporary-chat');
  await page.evaluate(() => {S.preferences.font_scale=125;applyAppearancePrefs();currentSettingsTab='voice';return showSettings();});
  await noViewportOverflow(); await snapshot('voice-settings-large');
  await page.evaluate(() => {S.preferences.font_scale=100;S.preferences.app_font='serif';applyAppearancePrefs();closePanel();S.tempChat=false;updateTempUI();renderMessages();renderChips();});
  await snapshot('serif-home');
  await page.evaluate(() => {S.preferences.accent_color='bad-value';S.preferences.chat_width='bad-value';applyAppearancePrefs();});
  check(await page.locator('html').getAttribute('data-accent') === 'terracotta', 'invalid accent safely falls back');
  check(await page.locator('html').getAttribute('data-chat-width') === 'comfortable', 'invalid width safely falls back');
  check(errors.length === 0, 'JS runtime errors: '+errors.join('\n'));
  const report = {assertions, errors, states:fs.readdirSync(output).filter(n=>n.endsWith('.html'))};
  fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
  await page.close(); await browser.close();
})().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1);});
