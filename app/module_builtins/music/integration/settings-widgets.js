/* MemuMusic · integration/settings-widgets.js — вкладка Настроек «Виджеты и интеграция» (раунд 38, r38-mod-integ).
 * Что MemuMusic показывает за пределами своего окна: мини-плеер над полем ввода чата, кольцо-эквалайзер вокруг
 * Быстрого доступа (стиль, цвет из обложки) и карточку «Сейчас играет» там же. Значения хранит MemuAI
 * (contributes.widgets → /api/pg/modint/settings), модуль читает и пишет их через Memu.integration.list/set.
 * Подключается одной строкой-маркером в main.js; оформление — токены темы MemuMusic (--mm-*, --tint). */
(function () {
  if (typeof SETTINGS_TABS === "undefined") return;
  const L = (ru, en) => (String(Memu.lang || "ru").indexOf("en") === 0 ? en : ru);
  const STYLE = `
.mm-int-preview { display: flex; gap: 12px; flex-wrap: wrap; }
.mm-int-style { position: relative; width: 132px; padding: 10px; border-radius: calc(14px * var(--mm-rk, 1)); border: 1px solid var(--mm-line);
  background: var(--mm-panel); color: var(--mm-muted); cursor: pointer; display: flex; flex-direction: column; gap: 8px; align-items: stretch;
  font: inherit; font-size: 12.5px; font-weight: 600; text-align: center; transition: border-color .2s, color .2s; }
.mm-int-style:hover { color: var(--mm-text); }
.mm-int-style[aria-checked="true"] { color: var(--mm-text); border-color: var(--tint); box-shadow: 0 0 0 1px var(--tint); }
.mm-int-style:disabled { opacity: .45; cursor: default; }
.mm-int-mock { position: relative; height: 58px; border-radius: 10px; background: var(--mm-panel-2); overflow: hidden; }
.mm-int-mock::before { content: ""; position: absolute; inset: 9px; border-radius: 7px; background: var(--mm-surface); box-shadow: 0 0 0 1px var(--mm-line); }
.mm-int-mock[data-s="bars"]::after { content: ""; position: absolute; inset: 3px; border-radius: 9px;
  background: repeating-linear-gradient(90deg, var(--int-c) 0 2px, transparent 2px 5px); mask: linear-gradient(#000 0 0) top/100% 4px no-repeat, linear-gradient(#000 0 0) bottom/100% 4px no-repeat;
  -webkit-mask: linear-gradient(#000 0 0) top/100% 4px no-repeat, linear-gradient(#000 0 0) bottom/100% 4px no-repeat; opacity: .9; }
.mm-int-mock[data-s="glow"]::after { content: ""; position: absolute; inset: 6px; border-radius: 9px; box-shadow: 0 0 0 2px var(--int-c), 0 0 16px 2px var(--int-c); opacity: .75; }
.mm-int-mock[data-s="wave"]::after { content: ""; position: absolute; left: 4px; right: 4px; top: 2px; height: 7px;
  background: radial-gradient(circle at 50% 100%, transparent 3px, var(--int-c) 3.5px 4.5px, transparent 5px) 0 0/10px 7px repeat-x; opacity: .9; }
.mm-int-swatch { display: inline-block; width: 12px; height: 12px; border-radius: 50%; vertical-align: -1px; margin-right: 6px; background: var(--int-c); }
.mm-int-note { margin: 0; color: var(--mm-muted); font-size: 12.5px; }
.mm-int-off { opacity: .5; pointer-events: none; }`;

  const ICON = { widgets: "sparkle" };
  let state = { widgets: [] };
  const w = (id) => state.widgets.find((x) => x.id === id) || { values: {} };
  const val = (id, key, dflt) => { const v = (w(id).values || {})[key]; return v === undefined ? dflt : v; };

  function swatch() {
    const color = val("eq-ring", "color", "cover");
    const track = typeof Player !== "undefined" && Player.current ? Player.current() : null;
    const cur = (track && track.accent) || "";
    return color === "cover" && /^#[0-9a-f]{6}$/i.test(cur) ? cur : "var(--tint)";
  }
  function sw(id, key) {
    return `<label class="mm-switch"><input type="checkbox" data-int="${id}:${key}"${val(id, key, id !== "chat-mini" || key !== "enabled") ? " checked" : ""} aria-label="${esc(key)}"><i></i></label>`;
  }
  function seg(id, key, values) {
    const cur = String(val(id, key, values[0][0]));
    return `<div class="mm-seg2" role="radiogroup" data-int-seg="${id}:${key}">${values.map(([v, label]) => `<button type="button" role="radio" data-v="${v}" aria-checked="${cur === v}">${esc(label)}</button>`).join("")}</div>`;
  }
  function render(host) {
    if (!document.getElementById("mm-int-style")) {
      const style = document.createElement("style"); style.id = "mm-int-style"; style.textContent = STYLE; document.head.appendChild(style);
    }
    if (!Memu.integration) { host.innerHTML = `<p class="mm-int-note">${esc(L("Обновите MemuAI, чтобы настроить виджеты.", "Update MemuAI to set up the widgets."))}</p>`; return; }
    host.innerHTML = `<div class="mm-ap"><p class="mm-int-note">${esc(L("Загружаю…", "Loading…"))}</p></div>`;
    Memu.integration.list().then((data) => { state = data || { widgets: [] }; draw(host); },
      (e) => { host.innerHTML = `<p class="mm-int-note">${esc(String(e && e.message || e))}</p>`; });
  }
  function draw(host) {
    if (!state.widgets.length) { host.innerHTML = `<p class="mm-int-note">${esc(L("Включите MemuMusic в «Настройки → Модули», чтобы он появился в MemuAI.", "Turn MemuMusic on in Settings → Modules so it shows up in MemuAI."))}</p>`; return; }
    const ringOn = !!val("eq-ring", "enabled", true);
    const styles = [["bars", L("Столбики", "Bars")], ["glow", L("Свечение", "Glow")], ["wave", L("Волна", "Wave")]];
    const cur = String(val("eq-ring", "style", "bars"));
    host.innerHTML = `<div class="mm-ap" style="--int-c:${swatch()}">
      ${setGroup(L("Главное окно MemuAI", "MemuAI main window"), L("Хватит одного плеера: по умолчанию — док в боковой панели. Изменения применяются сразу.", "One player is enough: the dock in the sidebar by default. Changes apply at once."), [
        // r39-integ: the dock (music-dock.js) and the chat mini player are both switchable now
        setLine(L("Док-плеер", "Dock player"), L("Внизу боковой панели: обложка, пауза, перемотка", "At the bottom of the sidebar: cover, pause, seek"),
          seg("dock", "mode", [["always", L("Всегда", "Always")], ["playing", L("Когда играет", "While playing")], ["off", L("Выкл.", "Off")]])),
        setLine(L("Мини-плеер над полем чата", "Mini player above the chat box"), L("Обложка, название и кнопки: назад, пауза, дальше", "Cover, title and buttons: previous, pause, next"), sw("chat-mini", "enabled")),
        `<div class="${val("chat-mini", "enabled", false) ? "" : "mm-int-off"}">${setLine(L("Живой эквалайзер", "Live equalizer"), L("Маленькие столбики рядом с названием", "Small bars next to the title"), sw("chat-mini", "eq"))}</div>`,
        setLine(L("Карточки команд в чате", "Command cards in the chat"), L("«Включи трек …» в чате — карточка с обложкой под ответом", "“Play the track …” in the chat — a card with the cover under the answer"), sw("chat-cards", "enabled")),
      ].join(""))}
      ${setGroup(L("Быстрый доступ", "Quick Access"), L("Окно Быстрого доступа (Alt+Space) знает, что играет.", "The Quick Access window (Alt+Space) knows what is playing."), [
        setLine(L("Кольцо-эквалайзер", "Equalizer ring"), L("Мягко пульсирует по краю окна, пока играет музыка", "Pulses softly along the window edge while music plays"), sw("eq-ring", "enabled")),
        `<div class="mm-ap-line${ringOn ? "" : " mm-int-off"}"><div><b>${esc(L("Стиль кольца", "Ring style"))}</b></div>
          <div class="mm-int-preview" role="radiogroup" data-int-seg="eq-ring:style">${styles.map(([v, label]) => `<button type="button" role="radio" class="mm-int-style" data-v="${v}" aria-checked="${cur === v}"><span class="mm-int-mock" data-s="${v}" aria-hidden="true"></span>${esc(label)}</button>`).join("")}</div></div>`,
        `<div class="${ringOn ? "" : "mm-int-off"}">${setLine(L("Цвет", "Colour"), L("Из обложки — у каждого трека свой оттенок", "From the cover — every track has its own tint"),
          `<span><i class="mm-int-swatch" aria-hidden="true"></i></span>` + seg("eq-ring", "color", [["cover", L("Из обложки", "From the cover")], ["accent", L("Акцент MemuAI", "MemuAI accent")]]))}</div>`,
        setLine(L("Карточка «Сейчас играет»", "“Now playing” card"), L("Обложка, прогресс, громкость, «Далее» и кнопки", "Cover, progress, volume, “Up next” and buttons"), sw("quick-card", "enabled")),
      ].join(""))}
      ${setGroup(L("Команды и панели", "Commands and panels"), "", `<p class="mm-int-note">${esc(L("Скажите в чате или Быстром доступе «Включи трек …», «Следующий трек», «Включи плейлист …» — MemuAI сделает это через MemuMusic. Панели «Сейчас играет», «Плейлист» и «Статистика» — в «Новая панель → Панели модулей».", "Say “Play the track …”, “Next track”, “Play the playlist …” in the chat or Quick Access — MemuAI does it through MemuMusic. The “Now playing”, “Playlist” and “Stats” panels are in New panel → Module panels."))}</p>`)}
    </div>`;
  }
  async function save(host, id, key, value) {
    const item = w(id);
    item.values = { ...(item.values || {}), [key]: value };
    try {
      const out = await Memu.integration.set("widget", id, { [key]: value });
      item.values = (out && out.values) || item.values;
    } catch (e) { if (typeof toast === "function") toast(String(e && e.message || e)); }
    draw(host);
  }
  function bind(host) {
    host.addEventListener("change", (e) => {
      const el = e.target.closest("[data-int]"); if (!el) return;
      const [id, key] = el.dataset.int.split(":");
      save(host, id, key, el.checked);
    });
    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-int-seg] [data-v]"); if (!b) return;
      const [id, key] = b.closest("[data-int-seg]").dataset.intSeg.split(":");
      save(host, id, key, b.dataset.v);
    });
  }
  SETTINGS_TABS.push({ id: "integration", order: 60, icon: ICON.widgets, title: () => L("Виджеты и интеграция", "Widgets & integration"),
    render: (host) => { bind(host); render(host); } });
})();
