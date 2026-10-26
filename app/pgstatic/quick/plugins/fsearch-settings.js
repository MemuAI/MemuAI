

(function () {
  "use strict";
  if (!window.MemuQuick || typeof MemuQuick.settingsSection !== "function") return;
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4-4M8.5 11h5"/></svg>';

  MemuQuick.settingsSection("fsearch", (body, ctx) => {
    const en = !!ctx.en;
    const tr = (ru, e) => (en ? e : ru);
    const esc = ctx.esc || ((v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
    const K = window.MemuSettingsKit || null;
    const post = (path, payload) => ctx.api(path, { method: "POST", body: { lang: en ? "en" : "ru", ...(payload || {}) } });
    const row = (o) => K ? K.row(o) : `<div class="fss-row"><div><b>${o.label}</b>${o.desc ? `<div class="td">${o.desc}</div>` : ""}</div><div>${o.control || ""}</div></div>`;
    const section = (o) => K ? K.section(o) : `<section><h3>${o.title}</h3>${o.desc ? `<p>${o.desc}</p>` : ""}${o.body}</section>`;
    const toggle = (id, on) => K ? K.toggle(id, on) : `<input type="checkbox" id="${id}" ${on ? "checked" : ""}>`;
    const select = (id, options, value, width = 190) => K ? K.select(id, options, value, { width }) : `<select id="${id}">${options.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    const note = (text, tone) => K ? K.note(text, tone) : `<p>${text}</p>`;
    const state = { st: null, timer: 0 };
    const num = (n) => Number(n || 0).toLocaleString(en ? "en-US" : "ru-RU");
    const mb = (n) => (n / 1048576).toFixed(n > 104857600 ? 0 : 1) + (en ? " MB" : " МБ");
    const ago = (ts) => {
      if (!ts) return "";
      const s = Math.max(0, Date.now() / 1000 - ts);
      if (s < 90) return tr("только что", "just now");
      if (s < 3600) return tr(`${Math.round(s / 60)} мин назад`, `${Math.round(s / 60)} min ago`);
      if (s < 86400) return tr(`${Math.round(s / 3600)} ч назад`, `${Math.round(s / 3600)} h ago`);
      return new Date(ts * 1000).toLocaleString(en ? "en-US" : "ru-RU", { dateStyle: "short", timeStyle: "short" });
    };

    function engineHtml(st) {
      const ev = st.everything || {}, own = st.own || {}, s = st.settings || {};
      const active = st.backend;
      let headline, tone;
      if (!s.enabled) { headline = tr("Поиск файлов выключен.", "File search is turned off."); tone = "warn"; }
      else if (active === "everything") { headline = tr(`Работает через Everything ${esc(ev.version || "")} — поиск мгновенный, MemuAI ничего не индексирует сам.`, `Running on Everything ${esc(ev.version || "")} — instant search, MemuAI indexes nothing itself.`); tone = "ok"; }
      else if (own.state === "building" || own.state === "loading") {
        const p = own.progress || {};
        headline = tr(`Строю собственный индекс… ${num(p.entries)} объектов${p.phase === "mft" ? " (чтение MFT)" : ""}${p.seconds ? ` · ${Math.round(p.seconds)} с` : ""}`,
          `Building the own index… ${num(p.entries)} items${p.phase === "mft" ? " (reading the MFT)" : ""}${p.seconds ? ` · ${Math.round(p.seconds)} s` : ""}`); tone = "info";
      } else if (own.state === "ready") {
        headline = tr(`Собственный индекс MemuAI: ${num(own.entries)} файлов и папок, обновлён ${ago(own.built_at)}.`, `MemuAI's own index: ${num(own.entries)} files and folders, updated ${ago(own.built_at)}.`); tone = "ok";
      } else if (own.state === "error") { headline = tr("Индекс не построился: ", "The index failed: ") + esc(own.error || ""); tone = "danger"; }
      else { headline = tr("Индекс построится при первом поиске (или нажмите «Перестроить»).", "The index is built on the first search (or press “Rebuild”)."); tone = "info"; }
      const live = { usn: tr("журнал USN (NTFS)", "USN journal (NTFS)"), watch: tr("слежение за дисками", "drive watching"), poll: tr(`пересканирование раз в ${s.rescan_minutes || 60} мин`, `rescan every ${s.rescan_minutes || 60} min`), off: tr("выключено", "off") }[own.live] || "—";
      const source = { mft: "NTFS MFT", walk: tr("обход папок", "folder walk"), "mft+walk": "MFT + " + tr("обход папок", "folder walk") }[own.source] || "—";
      let evText;
      if (st.platform !== "win32") evText = tr("только в Windows", "Windows only");
      else if (ev.running) evText = tr(`запущен, версия ${esc(ev.version || "?")}, связь: ${esc(ev.via === "dll" ? "Everything SDK" : "IPC")}${ev.es ? " · es.exe найден" : ""}`, `running, version ${esc(ev.version || "?")}, link: ${esc(ev.via === "dll" ? "Everything SDK" : "IPC")}${ev.es ? " · es.exe found" : ""}`);
      else if (ev.installed) evText = tr("установлен, но не запущен", "installed but not running");
      else evText = tr("не найден", "not found");
      const evButton = st.platform === "win32" && ev.installed && !ev.running ? `<button type="button" class="pbtn ghost sx-btn-sm" id="fss-ev-start">${esc(tr("Запустить Everything", "Start Everything"))}</button>` : "";
      return section({ id: "fss-engine", title: tr("Состояние", "Status"), keywords: "everything index индекс статус status mft usn",
        desc: tr("Ищет по именам на всех дисках за миллисекунды. Индекс хранится только на этом компьютере; модели уходят лишь результаты поиска, который она сама попросила.",
          "Searches names on every drive in milliseconds. The index stays on this computer; a model receives only the results of a search it asked for."),
        body: note(headline, tone)
          + row({ label: "Everything", desc: evText + (ev.installed || st.platform !== "win32" ? "" : tr(" — бесплатная программа voidtools; с ней поиск мгновенный сразу после установки.", " — the free voidtools app; with it search is instant right after install.")), control: evButton })
          + row({ label: tr("Собственный индекс", "Own index"), desc: own.entries ? tr(`${num(own.entries)} объектов · ${num(own.dirs)} папок · источник: ${source} · изменения: ${live}${own.file_bytes ? " · на диске " + mb(own.file_bytes) : ""}${own.build_seconds ? ` · построен за ${own.build_seconds} с` : ""}`,
            `${num(own.entries)} items · ${num(own.dirs)} folders · source: ${source} · changes: ${live}${own.file_bytes ? " · on disk " + mb(own.file_bytes) : ""}${own.build_seconds ? ` · built in ${own.build_seconds} s` : ""}`) : tr("ещё не построен", "not built yet"),
            control: `<button type="button" class="pbtn ghost sx-btn-sm" id="fss-rebuild" ${own.state === "building" ? "disabled" : ""}>${esc(own.state === "building" ? tr("Строится…", "Building…") : tr("Перестроить", "Rebuild"))}</button>` })
          + (st.platform === "win32" && !st.admin ? row({ label: tr("Чтение MFT", "MFT reading"), desc: tr("Самый быстрый способ без Everything — чтение таблицы NTFS — доступен, только когда MemuAI запущен от имени администратора. Без этого индекс строится обходом папок (дольше, но тоже мгновенный поиск потом).", "The fastest way without Everything — reading the NTFS table — works only when MemuAI runs as administrator. Otherwise the index is built by walking folders (slower to build, search is still instant).") }) : "") });
    }
    function optionsHtml(st) {
      const s = st.settings || {};
      const drives = (st.drives || []).filter((d) => d.type === "fixed" || d.type === "removable" || d.type === "home" || d.type === "ramdisk");
      const chosen = (s.roots && s.roots.length ? s.roots : st.default_roots || []).map((r) => String(r).toLowerCase());
      const driveRows = drives.map((d) => {
        const label = `${esc(d.path)}${d.label ? ` <small>${esc(d.label)}</small>` : ""}${d.fs ? ` <small>${esc(d.fs)}</small>` : ""}${d.type === "removable" ? ` <small>${esc(tr("съёмный", "removable"))}</small>` : ""}`;
        return `<label class="fss-drive"><input type="checkbox" data-fss-drive="${esc(d.path)}" ${chosen.includes(String(d.path).toLowerCase()) ? "checked" : ""}><span>${label}</span></label>`;
      }).join("");
      const custom = (s.roots || []).filter((r) => !drives.some((d) => String(d.path).toLowerCase() === String(r).toLowerCase()));
      return section({ id: "fss-options", title: tr("Поиск файлов на ПК", "PC file search"), keywords: "files search поиск файлов everything диски drives исключения exclude",
        body: row({ label: tr("Поиск файлов", "File search"), desc: tr("Alt+F, Ctrl+K → «Поиск файлов на ПК», «?имя» или маска вроде *.txt в поле ввода.", "Alt+F, Ctrl+K → “Search files on this PC”, “?name” or a mask like *.txt in the input."), control: toggle("fss-enabled", s.enabled) })
          + row({ label: tr("ИИ ищет файлы", "AI finds files"), desc: tr("Модель быстрого доступа и основного чата получает инструмент search_files: «где мой отчёт за март?», «последние скачанные pdf».", "The Quick Access and main chat model gets the search_files tool: “where is my March report?”, “latest downloaded PDFs”."), control: toggle("fss-ai", s.ai_tool) })
          + row({ label: tr("«?запрос» в поле ввода", "“?query” in the input"), control: toggle("fss-prefix", s.quick_prefix) })
          + row({ label: tr("Маска *.txt открывает поиск", "A *.txt mask opens the search"), control: toggle("fss-glob", s.quick_glob) })
          + row({ label: tr("Источник", "Engine"), desc: tr("«Авто» — Everything, если он запущен, иначе свой индекс.", "“Auto” — Everything when it runs, otherwise the own index."),
            control: select("fss-backend", [["auto", tr("Авто", "Auto")], ["everything", "Everything"], ["own", tr("Свой индекс MemuAI", "MemuAI's own index")]], s.backend) })
          + row({ label: tr("Диски и папки", "Drives and folders"), desc: tr("Что индексирует свой индекс (Everything настраивается в нём самом).", "What the own index covers (Everything is configured in Everything itself)."), stack: true,
            extra: `<div class="fss-drives">${driveRows}${custom.map((r) => `<label class="fss-drive"><input type="checkbox" data-fss-drive="${esc(r)}" checked><span>${esc(r)}</span></label>`).join("")}</div>
              <div class="fss-add"><input class="pinput sx-input" id="fss-root-add" placeholder="${esc(tr("Добавить папку, например D:\\Проекты", "Add a folder, e.g. D:\\Projects"))}"><button type="button" class="pbtn ghost sx-btn-sm" id="fss-root-add-btn">${esc(tr("Добавить", "Add"))}</button></div>` })
          + row({ label: tr("Исключения", "Exclusions"), stack: true, desc: tr("По одному в строке: имя папки (везде) или полный путь.", "One per line: a folder name (anywhere) or a full path."),
            extra: `<textarea class="ptexta sx-textarea fss-exclude" id="fss-exclude" rows="4" spellcheck="false">${esc((s.exclude || []).join("\n"))}</textarea>` })
          + row({ label: tr("Скрытые и системные файлы", "Hidden and system files"), control: toggle("fss-hidden", s.hidden) })
          + row({ label: tr("Следить за изменениями", "Follow changes"), desc: tr("Новые, удалённые и переименованные файлы попадают в индекс сразу (журнал NTFS или слежение за дисками).", "New, deleted and renamed files reach the index at once (the NTFS journal or drive watching)."), control: toggle("fss-live", s.live) })
          + row({ label: tr("Читать MFT (администратор)", "Read the MFT (administrator)"), control: toggle("fss-mft", s.mft) })
          + row({ label: tr("Выгружать индекс из памяти", "Unload the index from memory"), desc: tr("После простоя без поиска свой индекс освобождает память (≈90 МБ на миллион файлов); следующий поиск загрузит его снова за секунду.", "After a while without a search the own index frees its memory (≈90 MB per million files); the next search loads it again in about a second."),
            control: select("fss-unload", [["5", tr("через 5 мин", "after 5 min")], ["10", tr("через 10 мин", "after 10 min")], ["30", tr("через 30 мин", "after 30 min")], ["60", tr("через час", "after an hour")], ["0", tr("никогда", "never")]], String(s.unload_minutes ?? 10)) }) });
    }
    function draw() {
      const st = state.st;
      const scroller = body.closest(".settings-body");
      const top = scroller ? scroller.scrollTop : 0;
      body.innerHTML = `<div class="fss">${engineHtml(st)}${optionsHtml(st)}</div>`;
      if (scroller) scroller.scrollTop = top;
      bind();
      clearTimeout(state.timer);
      const own = st.own || {};
      if (own.state === "building" || own.state === "loading") state.timer = setTimeout(load, 1500);
    }
    async function load() {
      if (!body.isConnected && state.st) return;
      try { state.st = await ctx.api("/api/fsindex/status"); }
      catch (e) { body.innerHTML = note(esc(tr("Не удалось загрузить состояние поиска: ", "Could not load the search status: ") + e.message), "warn"); return; }
      draw();
    }
    async function save(patch) {
      try { const r = await post("/api/fsindex/settings", { settings: patch }); state.st.settings = r.settings; setTimeout(load, 300); }
      catch (e) { ctx.toast(e.message, "err"); }
    }
    function bind() {
      const $ = (id) => body.querySelector("#" + id);
      const toggles = { "fss-enabled": "enabled", "fss-ai": "ai_tool", "fss-prefix": "quick_prefix", "fss-glob": "quick_glob", "fss-hidden": "hidden", "fss-live": "live", "fss-mft": "mft" };
      for (const [id, key] of Object.entries(toggles)) { const el = $(id); if (el) el.onchange = () => save({ [key]: el.checked }); }
      const backend = $("fss-backend"); if (backend) backend.onchange = () => save({ backend: backend.value });
      const unload = $("fss-unload"); if (unload) unload.onchange = () => save({ unload_minutes: +unload.value || 0 });
      const roots = () => Array.from(body.querySelectorAll("[data-fss-drive]")).filter((x) => x.checked).map((x) => x.dataset.fssDrive);
      body.querySelectorAll("[data-fss-drive]").forEach((el) => { el.onchange = () => {
        const list = roots();
        if (!list.length) { el.checked = true; ctx.toast(tr("Нужен хотя бы один диск или папка", "At least one drive or folder is needed"), "err"); return; }
        save({ roots: list });
      }; });
      const addBtn = $("fss-root-add-btn"), addIn = $("fss-root-add");
      const add = () => { const v = (addIn.value || "").trim(); if (!v) return; save({ roots: [...roots(), v] }); };
      if (addBtn) addBtn.onclick = add;
      if (addIn) addIn.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } };
      const ex = $("fss-exclude"); if (ex) ex.onchange = () => save({ exclude: ex.value.split(/\n+/).map((x) => x.trim()).filter(Boolean) });
      const rb = $("fss-rebuild"); if (rb) rb.onclick = async () => { rb.disabled = true; try { await post("/api/fsindex/rebuild"); ctx.toast(tr("Индекс перестраивается", "Rebuilding the index"), "ok"); } catch (e) { ctx.toast(e.message, "err"); } setTimeout(load, 500); };
      const evs = $("fss-ev-start"); if (evs) evs.onclick = async () => { try { await post("/api/fsindex/everything/start"); ctx.toast(tr("Everything запускается…", "Starting Everything…"), "ok"); } catch (e) { ctx.toast(e.message, "err"); } setTimeout(load, 2500); };
    }
    body.innerHTML = `<div class="fss">${note(esc(tr("Загружаю…", "Loading…")), "info")}</div>`;
    load();
  }, { title: { ru: "Поиск файлов на ПК", en: "PC file search" }, order: 31, icon: ICON,
    desc: { ru: "Мгновенный поиск по всем дискам, Everything, индекс, исключения", en: "Instant search on every drive, Everything, index, exclusions" } });
})();
