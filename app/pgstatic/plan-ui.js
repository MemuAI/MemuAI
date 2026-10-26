

(function () {
  "use strict";
  if (window.MemuPlanUI) return;
  const isEn = (lang) => (lang ? lang === "en" : (typeof PG_LANG !== "undefined" && PG_LANG === "en"));
  const H = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad = (n) => String(n).padStart(2, "0");
  let openMenu = null;

  function place(pop, anchor, { gap = 6, minHeight = 160, align = "start" } = {}) {
    const r = anchor.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight, edge = 8;
    const below = vh - r.bottom - gap - edge, above = r.top - gap - edge;
    const up = below < Math.min(minHeight, pop.scrollHeight) && above > below;
    const room = Math.max(120, up ? above : below);
    pop.style.maxHeight = `${Math.floor(room)}px`;
    const w = pop.offsetWidth;
    let left = align === "end" ? r.right - w : r.left;
    left = Math.max(edge, Math.min(left, vw - w - edge));
    pop.style.left = `${Math.round(left)}px`;
    const h = Math.min(pop.scrollHeight, room);
    pop.style.top = `${Math.round(up ? r.top - gap - h : r.bottom + gap)}px`;
    pop.classList.toggle("up", up);
  }
  function closeOnOutside(pop, anchor, close) {
    const down = (ev) => { if (!pop.contains(ev.target) && !anchor.contains(ev.target)) close(); };

    const scroll = (ev) => {
      if (pop.contains(ev.target)) return;
      const r = anchor.getBoundingClientRect();
      if (!anchor.isConnected || r.bottom < 0 || r.top > window.innerHeight) { close(); return; }
      place(pop, anchor, { minHeight: 160, align: pop.dataset.align || "start" });
    };
    const resize = () => close();
    setTimeout(() => {
      document.addEventListener("mousedown", down, true);
      document.addEventListener("scroll", scroll, true);
      window.addEventListener("resize", resize);
      window.addEventListener("blur", resize);
    }, 0);
    return () => {
      document.removeEventListener("mousedown", down, true);
      document.removeEventListener("scroll", scroll, true);
      window.removeEventListener("resize", resize);
      window.removeEventListener("blur", resize);
    };
  }

  function menu(anchor, items, opts = {}) {
    if (openMenu) openMenu.close(false);
    const en = isEn(opts.lang);
    const count = items.filter((it) => !it.sep).length;
    const withFilter = opts.filter ?? count > 9;
    const pop = document.createElement("div");
    pop.className = `pln-pop${opts.cls ? " " + opts.cls : ""}`;
    pop.setAttribute("role", "menu");
    pop.innerHTML = (withFilter ? `<div class="pln-pop-filter"><input type="text" autocomplete="off" spellcheck="false" placeholder="${en ? "Filter…" : "Найти…"}" aria-label="${en ? "Filter the list" : "Фильтр списка"}"></div>` : "")
      + `<div class="pln-pop-list">${items.map((it, i) => it.sep ? `<div class="pln-pop-sep" role="presentation">${H(it.sep)}</div>`
        : `<button type="button" role="menuitem" data-i="${i}" class="${it.danger ? "danger" : ""}" ${it.disabled ? "disabled" : ""}>
            <span class="pln-pop-ico">${it.icon || ""}</span><span class="pln-pop-text"><b>${H(it.title)}</b>${it.desc ? `<small>${H(it.desc)}</small>` : ""}</span>${it.badge ? `<em>${H(it.badge)}</em>` : ""}</button>`).join("")}
        <div class="pln-pop-none" hidden>${en ? "Nothing found" : "Ничего не найдено"}</div></div>`;
    document.body.appendChild(pop);
    anchor.setAttribute("aria-expanded", "true");
    let detach = () => {};
    const close = (refocus = true) => {
      if (!pop.isConnected) return;
      pop.remove(); detach(); anchor.setAttribute("aria-expanded", "false");
      if (openMenu && openMenu.pop === pop) openMenu = null;
      if (refocus) { try { anchor.focus({ preventScroll: true }); } catch (e) {  } }
      opts.onClose?.();
    };
    detach = closeOnOutside(pop, anchor, () => close(false));
    const buttons = () => [...pop.querySelectorAll(".pln-pop-list button:not([hidden]):not([disabled])")];
    const focusAt = (k) => { const b = buttons(); if (b.length) b[(k + b.length) % b.length].focus(); };
    const filter = pop.querySelector(".pln-pop-filter input");
    if (filter) {
      filter.addEventListener("input", () => {
        const q = filter.value.trim().toLowerCase();
        let shown = 0;
        pop.querySelectorAll(".pln-pop-list button").forEach((b) => { const hit = !q || b.textContent.toLowerCase().includes(q); b.hidden = !hit; if (hit) shown++; });

        pop.querySelectorAll(".pln-pop-sep").forEach((sep) => {
          let n = sep.nextElementSibling, any = false;
          while (n && !n.classList.contains("pln-pop-sep")) { if (n.tagName === "BUTTON" && !n.hidden) { any = true; break; } n = n.nextElementSibling; }
          sep.hidden = !any;
        });
        pop.querySelector(".pln-pop-none").hidden = shown > 0;
      });
    }
    pop.addEventListener("keydown", (ev) => {
      const b = buttons(), at = b.indexOf(document.activeElement);
      if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); close(); return; }
      if (ev.key === "Tab") { close(false); return; }
      if (ev.key === "ArrowDown") { ev.preventDefault(); focusAt(at < 0 ? 0 : at + 1); return; }
      if (ev.key === "ArrowUp") { ev.preventDefault(); if (at <= 0 && filter) filter.focus(); else focusAt(at < 0 ? b.length - 1 : at - 1); return; }
      if (ev.key === "Home" && at >= 0) { ev.preventDefault(); focusAt(0); return; }
      if (ev.key === "End" && at >= 0) { ev.preventDefault(); focusAt(b.length - 1); return; }
      if (ev.key === "Enter" && document.activeElement === filter) { ev.preventDefault(); b[0]?.click(); return; }
      if (filter && at >= 0 && ev.key.length === 1 && !ev.ctrlKey && !ev.metaKey && !ev.altKey) { filter.focus(); }
    });
    pop.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-i]");
      if (!btn || btn.disabled) return;
      close(false);
      items[+btn.dataset.i].run?.();
    });
    pop.dataset.align = opts.align || "start";
    place(pop, anchor, { align: pop.dataset.align, minHeight: 320 });
    (filter || buttons()[0])?.focus({ preventScroll: true });
    openMenu = { pop, close };
    return pop;
  }

  function parseTime(text) {
    const v = String(text || "").trim().replace(/[.,\s-]/g, ":");
    let h, m;
    let mm = /^(\d{1,2}):(\d{1,2})$/.exec(v);
    if (mm) { h = +mm[1]; m = mm[2].length === 1 ? +mm[2] * 10 : +mm[2]; }
    else if ((mm = /^(\d{1,4})$/.exec(v))) {
      const d = mm[1];
      if (d.length <= 2) { h = +d; m = 0; } else { h = +d.slice(0, d.length - 2); m = +d.slice(-2); }
    } else return "";
    return h < 24 && m < 60 ? `${pad(h)}:${pad(m)}` : "";
  }
  function timeList(step = 30) {
    const out = [];
    for (let t = 0; t < 1440; t += step) out.push(`${pad(Math.floor(t / 60))}:${pad(t % 60)}`);
    return out;
  }
  function enhanceTimes(root) {
    (root || document).querySelectorAll("input[data-time]:not([data-time-ready])").forEach((input) => {
      input.dataset.timeReady = "1";
      input.setAttribute("inputmode", "numeric");
      input.setAttribute("autocomplete", "off");
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-expanded", "false");
      input.maxLength = 5;
      if (!input.placeholder) input.placeholder = "09:00";
      let good = parseTime(input.value) || input.value;
      input.dataset.lastCommitted = good;
      let list = null, detach = () => {};
      const shut = () => { if (!list) return; list.remove(); list = null; detach(); input.setAttribute("aria-expanded", "false"); };
      const commit = () => {
        const v = parseTime(input.value);
        if (!v && input.value.trim() === "" && input.dataset.optional === "1") { good = ""; }
        else if (!v) { input.value = good; input.classList.remove("bad"); void input.offsetWidth; input.classList.add("bad"); return; }
        else good = v;
        input.value = good;
        if (input.dataset.lastCommitted !== good) {
          input.dataset.lastCommitted = good;
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      };
      const mark = () => {
        if (!list) return;
        const v = parseTime(input.value) || good;
        const opts = [...list.querySelectorAll("button")];
        let best = opts[0], diff = 1e9;
        const mins = (s) => +s.slice(0, 2) * 60 + +s.slice(3);
        if (v) opts.forEach((b) => { const d = Math.abs(mins(b.dataset.t) - mins(v)); if (d < diff) { diff = d; best = b; } });
        opts.forEach((b) => b.classList.toggle("on", b === best));
        best?.scrollIntoView({ block: "center" });
      };
      const openList = () => {
        if (list) return;
        list = document.createElement("div");
        list.className = "pln-pop pln-tp-list";
        list.setAttribute("role", "listbox");
        const times = timeList(30);
        if (good && !times.includes(good)) { times.push(good); times.sort(); }
        list.innerHTML = times.map((t) => `<button type="button" role="option" tabindex="-1" data-t="${t}">${t}</button>`).join("");
        document.body.appendChild(list);
        list.addEventListener("mousedown", (ev) => ev.preventDefault());
        list.addEventListener("click", (ev) => {
          const b = ev.target.closest("button[data-t]");
          if (!b) return;
          input.value = b.dataset.t; commit(); shut();
        });
        place(list, input, { minHeight: 200 });
        detach = closeOnOutside(list, input, shut);
        input.setAttribute("aria-expanded", "true");
        mark();
      };
      const step = (k) => {
        if (!list) { openList(); return; }
        const opts = [...list.querySelectorAll("button")];
        const at = Math.max(0, opts.findIndex((b) => b.classList.contains("on")));
        const next = opts[Math.max(0, Math.min(opts.length - 1, at + k))];
        opts.forEach((b) => b.classList.toggle("on", b === next));
        next.scrollIntoView({ block: "nearest" });
        input.value = next.dataset.t;
      };
      input.addEventListener("focus", () => { input.select(); openList(); });
      input.addEventListener("click", openList);
      input.addEventListener("input", () => { input.classList.remove("bad"); openList(); mark(); });
      input.addEventListener("blur", () => { commit(); shut(); });
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "ArrowDown") { ev.preventDefault(); step(1); }
        else if (ev.key === "ArrowUp") { ev.preventDefault(); step(-1); }
        else if (ev.key === "Enter") { ev.preventDefault(); commit(); shut(); }
        else if (ev.key === "Escape" && list) { ev.preventDefault(); ev.stopPropagation(); input.value = good; shut(); }
      });
    });
  }

  const MONTHS = {
    ru: ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"],
    en: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
  };

  const MONTH_STEMS = [["янв", "jan"], ["фев", "feb"], ["мар", "mar"], ["апр", "apr"], ["ма", "may"], ["июн", "jun"],
    ["июл", "jul"], ["авг", "aug"], ["сен", "sep"], ["окт", "oct"], ["ноя", "nov"], ["дек", "dec"]];
  const WEEK = { ru: ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"], en: ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"] };
  const isoOf = (y, m, d) => `${y}-${pad(m)}-${pad(d)}`;
  const validIso = (iso) => {
    const mm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
    if (!mm) return "";
    const dt = new Date(+mm[1], +mm[2] - 1, +mm[3]);
    return dt.getFullYear() === +mm[1] && dt.getMonth() === +mm[2] - 1 && dt.getDate() === +mm[3] ? mm[0] : "";
  };
  const todayIso = () => { const t = new Date(); return isoOf(t.getFullYear(), t.getMonth() + 1, t.getDate()); };

  function formatDate(iso, lang) {
    const v = validIso(iso);
    if (!v) return "";
    const [y, m, d] = v.split("-").map(Number);
    return isEn(lang) ? `${MONTHS.en[m - 1].slice(0, 3)} ${d}, ${y}` : `${pad(d)}.${pad(m)}.${y}`;
  }

  function parseDate(text, lang) {
    const v = String(text || "").trim().toLowerCase().replace(/,/g, " ").replace(/\s+/g, " ");
    if (!v) return "";
    let mm = /^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$/.exec(v);
    if (mm) return validIso(isoOf(+mm[1], +mm[2], +mm[3]));
    let d, m, y;
    const monthOf = (word) => { const i = MONTH_STEMS.findIndex(([ru, en]) => word.startsWith(ru) || word.startsWith(en)); return i < 0 ? 0 : i + 1; };
    if ((mm = /^(\d{1,2})\s*([a-zа-яё]+)\.?(?:\s+(\d{2,4}))?$/.exec(v))) { d = +mm[1]; m = monthOf(mm[2]); y = mm[3]; }
    else if ((mm = /^([a-zа-яё]+)\.?\s*(\d{1,2})(?:\s+(\d{2,4}))?$/.exec(v))) { m = monthOf(mm[1]); d = +mm[2]; y = mm[3]; }
    else if ((mm = /^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?$/.exec(v))) {

      if (isEn(lang) && v.includes("/")) { m = +mm[1]; d = +mm[2]; } else { d = +mm[1]; m = +mm[2]; }
      y = mm[3];
    } else return "";
    if (!m || !d) return "";
    if (y === undefined) {
      const today = todayIso();
      let year = new Date().getFullYear();
      if (validIso(isoOf(year, m, d)) && isoOf(year, m, d) < today) year += 1;
      return validIso(isoOf(year, m, d));
    }
    y = +y; if (y < 100) y += 2000;
    return validIso(isoOf(y, m, d));
  }
  function shiftIso(iso, days, months = 0) {
    const [y, m, d] = (validIso(iso) || todayIso()).split("-").map(Number);
    const dt = months ? new Date(y, m - 1 + months, 1) : new Date(y, m - 1, d + days);
    if (months) dt.setDate(Math.min(d, new Date(dt.getFullYear(), dt.getMonth() + 1, 0).getDate()));
    return isoOf(dt.getFullYear(), dt.getMonth() + 1, dt.getDate());
  }
  function enhanceDates(root) {
    (root || document).querySelectorAll("input[data-date]:not([data-date-ready])").forEach((input) => {
      input.dataset.dateReady = "1";
      input.type = "text";
      input.setAttribute("autocomplete", "off");
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-haspopup", "dialog");
      input.setAttribute("aria-expanded", "false");
      const en = isEn(), lang = en ? "en" : "ru";
      if (!input.placeholder) input.placeholder = en ? "MMM D, YYYY" : "дд.мм.гггг";
      let good = validIso(input.value);
      input.dataset.iso = good;
      input.value = formatDate(good, lang);
      let pop = null, view = good || todayIso(), pick = good, detach = () => {};
      const shut = () => { if (!pop) return; pop.remove(); pop = null; detach(); input.setAttribute("aria-expanded", "false"); };
      const commit = () => {
        const typed = input.value.trim();
        const v = typed === formatDate(good, lang) ? good : parseDate(typed, lang);
        if (!v && typed === "" && input.dataset.optional === "1") good = "";
        else if (!v) { input.value = formatDate(good, lang); input.classList.remove("bad"); void input.offsetWidth; input.classList.add("bad"); return; }
        else good = v;
        input.value = formatDate(good, lang);
        if (input.dataset.iso !== good) {
          input.dataset.iso = good;
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      };
      const draw = () => {
        if (!pop) return;
        const [y, m] = view.split("-").map(Number);
        const first = new Date(y, m - 1, 1).getDay();
        const lead = en ? first : (first + 6) % 7;
        const days = new Date(y, m, 0).getDate(), today = todayIso();
        let cells = "";
        for (let i = 0; i < lead; i += 1) cells += "<span></span>";
        for (let d = 1; d <= days; d += 1) {
          const iso = isoOf(y, m, d);
          cells += `<button type="button" tabindex="-1" data-d="${iso}" class="${iso === pick ? "on" : ""}${iso === today ? " today" : ""}" aria-pressed="${iso === pick}" aria-label="${H(formatDate(iso, lang))}">${d}</button>`;
        }
        pop.innerHTML = `<div class="pln-cal-head"><button type="button" tabindex="-1" data-m="-1" aria-label="${en ? "Previous month" : "Предыдущий месяц"}">‹</button>`
          + `<b>${H(MONTHS[lang][m - 1])} ${y}</b><button type="button" tabindex="-1" data-m="1" aria-label="${en ? "Next month" : "Следующий месяц"}">›</button></div>`
          + `<div class="pln-cal-week">${WEEK[lang].map((w) => `<span>${w}</span>`).join("")}</div><div class="pln-cal-grid">${cells}</div>`
          + `<div class="pln-cal-foot"><button type="button" tabindex="-1" data-today="1">${en ? "Today" : "Сегодня"}</button>`
          + (input.dataset.optional === "1" ? `<button type="button" tabindex="-1" data-clear="1">${en ? "Clear" : "Очистить"}</button>` : "") + "</div>";
      };
      const open = () => {
        if (pop) return;
        pick = good; view = good || todayIso();
        pop = document.createElement("div");
        pop.className = "pln-pop pln-cal";
        pop.setAttribute("role", "dialog");
        pop.setAttribute("aria-label", en ? "Pick a date" : "Выбор даты");
        document.body.appendChild(pop);
        draw();
        pop.addEventListener("mousedown", (ev) => ev.preventDefault());
        pop.addEventListener("click", (ev) => {
          const b = ev.target.closest("button");
          if (!b) return;
          if (b.dataset.m) { view = shiftIso(view, 0, +b.dataset.m); draw(); return; }
          if (b.dataset.clear) { input.value = ""; commit(); shut(); return; }
          input.value = formatDate(b.dataset.today ? todayIso() : b.dataset.d, lang);
          commit(); shut();
        });
        place(pop, input, { minHeight: 300 });
        detach = closeOnOutside(pop, input, shut);
        input.setAttribute("aria-expanded", "true");
      };
      const move = (days, months = 0) => {
        if (!pop) { open(); return; }
        pick = shiftIso(pick || view, days, months); view = pick;
        input.value = formatDate(pick, lang);
        draw();
      };
      input.addEventListener("focus", () => { input.select(); open(); });
      input.addEventListener("click", open);
      input.addEventListener("input", () => {
        input.classList.remove("bad");
        const v = parseDate(input.value, lang);
        if (v && pop) { pick = v; view = v; draw(); }
      });
      input.addEventListener("blur", () => { commit(); shut(); });
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "ArrowDown") { ev.preventDefault(); move(1); }
        else if (ev.key === "ArrowUp") { ev.preventDefault(); move(-1); }
        else if (ev.key === "PageDown") { ev.preventDefault(); move(0, 1); }
        else if (ev.key === "PageUp") { ev.preventDefault(); move(0, -1); }
        else if (ev.key === "Enter") { ev.preventDefault(); commit(); shut(); }
        else if (ev.key === "Escape" && pop) { ev.preventDefault(); ev.stopPropagation(); input.value = formatDate(good, lang); shut(); }
      });
    });
  }

  window.MemuPlanUI = Object.freeze({ menu, enhanceTimes, enhanceDates, parseTime, parseDate, formatDate, place });
})();
