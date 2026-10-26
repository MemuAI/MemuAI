

(function () {
  "use strict";
  if (window.MemuDFY) return;

  const en = () => (window.PG_LANG || document.documentElement.lang || "ru").toLowerCase().startsWith("en");
  const T = (ru, e) => (en() ? e : ru);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const FINAL = ["done", "failed", "cancelled"];

  async function call(path, opts = {}) {
    const resp = await fetch(path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json" },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const error = new Error(data.error || `HTTP ${resp.status}`);
      error.status = resp.status; error.payload = data;
      throw error;
    }
    return data;
  }

  const status = () => call("/api/dfy/status");
  const get = (id) => call(`/api/dfy/tasks/${encodeURIComponent(id)}`);
  const list = () => call("/api/dfy/tasks");
  function chooseBrowser(choices) {
    return new Promise((resolve) => {
      const previous = document.activeElement, dialog = document.createElement("dialog");
      dialog.className = "dfy-browser-choice";
      dialog.style.cssText = "font:inherit;color:var(--text,#e8e8ef);background:var(--panel,#22222b);border:1px solid var(--line,#555);border-radius:18px;padding:24px;max-width:440px;max-height:calc(100dvh - 24px);overflow:auto;width:calc(100vw - 40px);box-sizing:border-box;box-shadow:0 20px 80px #0006";
      dialog.innerHTML = `<style>@media(max-height:420px){.dfy-browser-choice{padding:16px!important}.dfy-browser-choice>p{display:none}.dfy-browser-choice [data-browser]{padding:10px!important}.dfy-browser-choice [data-cancel]{margin-top:8px!important}}</style><h2 style="margin:0 0 8px;font-size:20px">${T("Где выполнить задачу?", "Where should DFY run?")}</h2><p style="margin:0 0 18px;line-height:1.5;opacity:.75">${T("Выбор действует только для этой задачи. В Chrome доступны ваши входы на сайты; отдельный браузер использует профиль DFY.", "This choice applies to this task only. Chrome uses your site sessions; the separate browser uses the DFY profile.")}</p><div style="display:grid;gap:10px">${choices.map(row => `<button type="button" data-browser="${esc(row.id)}" ${row.available ? "" : "disabled"} style="font:inherit;text-align:left;padding:14px;border-radius:12px;border:1px solid var(--line,#666);background:transparent;color:inherit;cursor:pointer;opacity:${row.available ? 1 : .5}"><b>${esc(row.name)}</b>${row.reason ? `<span style="display:block;font-size:12px;margin-top:5px">${esc(row.reason)}</span>` : ""}</button>`).join("")}</div><button type="button" data-cancel style="font:inherit;padding:9px 14px;margin-top:16px;border:0;border-radius:9px;background:transparent;color:inherit;cursor:pointer">${T("Отмена", "Cancel")}</button>`;
      const finish = (value) => { dialog.close?.(); dialog.remove(); previous?.focus?.(); resolve(value); };
      dialog.addEventListener("click", (event) => { const button = event.target.closest("button"); if (button?.hasAttribute("data-cancel")) finish(""); else if (button?.dataset.browser && !button.disabled) finish(button.dataset.browser); });
      dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(""); });
      document.body.append(dialog);
      if (typeof dialog.showModal === "function") dialog.showModal();
      else { dialog.setAttribute("open", ""); dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true"); }
      dialog.querySelector("button:not([disabled])")?.focus();
    });
  }
  async function start(goal, context = [], source = "chat", extra = {}) {
    const rows = (Array.isArray(context) ? context : []).map((m) => ({
      role: m && m.role === "assistant" ? "assistant" : "user",
      content: typeof m?.content === "string" ? m.content
        : Array.isArray(m?.content) ? m.content.filter((p) => p && p.type === "text").map((p) => p.text).join(" ") : "",
    })).filter((m) => m.content).slice(-24);
    const options = { ...(extra || {}) };
    if ((!options.target || options.target === "browser") && source !== "schedule") {
      const state = await status();
      if (state.ready === false) throw new Error(state.reason || T("DFY не готов", "DFY is not ready"));
      if (state.browser_choice_required) {

        options.browser = await chooseBrowser(state.browser_choices || []);
        if (!options.browser) throw Object.assign(new Error(T("Задача отменена", "Task cancelled")), { code: "dfy_cancelled" });
      }
    }
    return call("/api/dfy/tasks", { method: "POST", body: { goal, context: rows, source, ...options } });
  }
  const answer = (id, payload) => call(`/api/dfy/tasks/${encodeURIComponent(id)}/answer`, { method: "POST", body: payload || {} });
  const cancel = (id) => call(`/api/dfy/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST", body: {} });

  function watch(id, onUpdate) {
    let stopped = false, source = null, timer = null, lastVersion = -1, failures = 0;
    const emit = (task) => {
      if (stopped || !task) return;
      if (task.version === lastVersion) return;
      lastVersion = task.version;
      try { onUpdate(task); } catch (e) { console.error("MemuDFY onUpdate", e); }
      if (FINAL.includes(task.status)) stop();
    };
    const poll = async () => {
      if (stopped) return;
      try { emit(await get(id)); failures = 0; } catch (e) { failures++; if (e.status === 404) { gone(); return; } }
      if (!stopped) timer = setTimeout(poll, Math.min(8000, 1200 + failures * 800));
    };
    const open = () => {
      if (stopped) return;
      if (typeof EventSource !== "function") { poll(); return; }
      source = new EventSource(`/api/dfy/tasks/${encodeURIComponent(id)}/events`);
      source.onmessage = (ev) => { try { emit(JSON.parse(ev.data)); failures = 0; } catch (e) {} };
      source.addEventListener("gone", () => gone());
      source.onerror = () => {
        source && source.close(); source = null;
        if (stopped) return;
        failures++;

        timer = setTimeout(() => get(id).then((t) => { emit(t); if (!FINAL.includes(t.status)) (failures > 3 ? poll() : open()); })
          .catch((e) => { if (e.status === 404) gone(); else poll(); }), Math.min(5000, 500 * failures));
      };
    };
    function stop() { stopped = true; clearTimeout(timer); if (source) { source.close(); source = null; } }
    function gone() { if (stopped) return; stop(); try { onUpdate({ id, gone: true, status: "gone", steps: [] }); } catch (e) {} }
    open();
    return stop;
  }

  function injectStyle() {
    if (document.getElementById("memu-dfy-style")) return;
    const s = document.createElement("style");
    s.id = "memu-dfy-style";
    s.textContent = `
.dfy-card{--dfy-acc:var(--accent,#7c6cf0);border:1px solid var(--line,rgba(127,127,127,.28));border-radius:14px;padding:12px 14px;margin:8px 0;background:var(--panel,rgba(127,127,127,.06));font-size:13.5px;line-height:1.45;max-width:640px;color:inherit}
.dfy-head{display:flex;align-items:center;gap:8px}.dfy-badge{font-weight:700;font-size:11px;letter-spacing:.06em;padding:2px 7px;border-radius:999px;background:var(--dfy-acc);color:#fff}
.dfy-goal{flex:1;font-weight:600;overflow-wrap:anywhere}.dfy-chip{font-size:11.5px;padding:2px 8px;border-radius:999px;background:rgba(127,127,127,.16);white-space:nowrap}
.dfy-chip.running{background:rgba(80,140,255,.18)}.dfy-chip.waiting_user{background:rgba(255,170,40,.22)}.dfy-chip.done{background:rgba(60,190,110,.22)}.dfy-chip.failed{background:rgba(240,80,80,.22)}
.dfy-steps{list-style:none;margin:8px 0 0;padding:0;max-height:220px;overflow:auto}.dfy-steps li{display:flex;gap:6px;padding:2px 0;opacity:.88}.dfy-steps li.bad{color:#e66}
.dfy-steps b{min-width:22px;text-align:right;opacity:.6;font-weight:500}.dfy-steps .act{opacity:.7;min-width:62px}
.dfy-shot{margin-top:8px}.dfy-shot img{max-width:100%;max-height:180px;border-radius:8px;border:1px solid rgba(127,127,127,.25);cursor:zoom-in;display:block}.dfy-shot img.big{max-height:none;cursor:zoom-out}
.dfy-q{margin-top:10px;padding:10px;border-radius:10px;background:rgba(255,170,40,.10);border:1px solid rgba(255,170,40,.35)}
.dfy-q p{margin:0 0 8px;white-space:pre-wrap;overflow-wrap:anywhere}.dfy-amount{font-size:16px;font-weight:700;margin:0 0 8px}
.dfy-q input,.dfy-q textarea{width:100%;box-sizing:border-box;margin:3px 0 6px;padding:7px 9px;border-radius:8px;border:1px solid rgba(127,127,127,.35);background:rgba(127,127,127,.08);color:inherit;font:inherit}
.dfy-q label{font-size:12px;opacity:.8}.dfy-q .dfy-save{display:flex;gap:6px;align-items:center;font-size:12.5px;opacity:.9;margin:2px 0 8px}.dfy-q .dfy-save input{width:auto;margin:0}
.dfy-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}.dfy-btn{border:1px solid rgba(127,127,127,.35);background:transparent;color:inherit;border-radius:9px;padding:6px 12px;cursor:pointer;font:inherit}
.dfy-btn.primary{background:var(--dfy-acc);border-color:var(--dfy-acc);color:#fff}.dfy-btn.danger{border-color:rgba(240,80,80,.6);color:#e66}.dfy-btn:disabled{opacity:.5;cursor:default}
.dfy-result{margin-top:10px;white-space:pre-wrap;overflow-wrap:anywhere}.dfy-error{margin-top:10px;color:#e66;white-space:pre-wrap}
.dfy-note{font-size:11.5px;opacity:.7;margin-top:4px}
.dfy-fields{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:2px 10px}.dfy-field{display:flex;flex-direction:column;font-size:12px}.dfy-field>span{opacity:.8}
.dfy-q select{width:100%;box-sizing:border-box;margin:3px 0 0;padding:7px 9px;border-radius:8px;border:1px solid rgba(127,127,127,.35);background:rgba(127,127,127,.08);color:inherit;font:inherit}
.dfy-q .dfy-field input{margin-bottom:0}.dfy-err{color:#e66;font-size:11.5px;min-height:14px}.dfy-field [aria-invalid],.dfy-q [aria-invalid]{border-color:rgba(240,80,80,.8)!important}
.dfy-chip.user{background:var(--accent-soft,rgba(124,108,240,.18));color:inherit}
.dfy-takeover{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:10px;padding:9px 10px 9px 12px;border-radius:10px;background:var(--accent-soft,rgba(124,108,240,.12))}
.dfy-takeover>span{flex:1 1 220px;min-width:0}.dfy-takeover b{font-weight:600}
.dfy-journal{margin:4px 0 2px 28px;padding:0 0 0 14px;font-size:12.5px;opacity:.8}.dfy-journal li{padding:1px 0;overflow-wrap:anywhere}
.dfy-q input.dfy-code{font-size:22px;letter-spacing:.35em;text-align:center;font-variant-numeric:tabular-nums;max-width:260px;display:block}.dfy-more{font-size:12px;opacity:.75;background:none;border:0;color:inherit;cursor:pointer;padding:2px 0;text-decoration:underline}`;
    document.head.appendChild(s);
  }

  const STATUS_LABEL = {
    queued: ["В очереди", "Queued"], running: ["Выполняется", "Running"], waiting_user: ["Ждёт вас", "Needs you"], user: ["Управляете вы", "You're in control"],
    done: ["Готово", "Done"], failed: ["Ошибка", "Failed"], cancelled: ["Отменено", "Cancelled"],
  };

  const FIELDS = {
    username: { l: ["Логин / e-mail", "Username / email"], ac: "username" },
    password: { l: ["Пароль", "Password"], type: "password", ac: "new-password" },
    number: { l: ["Номер", "Number"] },
    exp_month: { l: ["Месяц (ММ)", "Month (MM)"], mode: "numeric", max: 2, ph: "MM", ac: "cc-exp-month" },
    exp_year: { l: ["Год (ГГГГ)", "Year (YYYY)"], mode: "numeric", max: 4, ph: "YYYY", ac: "cc-exp-year" },
    cvc: { l: ["CVC/CVV", "CVC/CVV"], type: "password", mode: "numeric", max: 4, ac: "cc-csc" },
    holder: { l: ["Имя на карте", "Name on card"], ac: "cc-name" },
    first_name: { l: ["Имя", "First name"], ac: "given-name" },
    last_name: { l: ["Фамилия", "Last name"], ac: "family-name" },
    middle_name: { l: ["Отчество", "Middle name"], ac: "additional-name" },
    email: { l: ["E-mail", "Email"], type: "email", ac: "email", ph: "name@example.com" },
    phone: { l: ["Телефон", "Phone"], type: "tel", ac: "tel", ph: "+7 916 123 45 67" },
    birth_date: { l: ["Дата рождения", "Date of birth"], type: "date", ac: "bday" },
    address: { l: ["Адрес", "Address"], ac: "street-address" },
    city: { l: ["Город", "City"], ac: "address-level2" },
    region: { l: ["Регион / область", "Region / state"], ac: "address-level1" },
    postal_code: { l: ["Индекс", "Postal code"], ac: "postal-code" },
    country: { l: ["Страна", "Country"], ac: "country-name" },
    recipient: { l: ["Получатель", "Recipient"], ac: "name" },
    street: { l: ["Улица", "Street"], ac: "address-line1" },
    house: { l: ["Дом, корпус", "House / building"], ac: "address-line2" },
    apartment: { l: ["Квартира / офис", "Apartment / suite"], ac: "address-line3" },
    address_type: { l: ["Назначение", "Use for"], options: [["both", ["Доставка и оплата", "Shipping and billing"]], ["shipping", ["Доставка", "Shipping"]], ["billing", ["Плательщик (billing)", "Billing"]]] },
    doc_type: { l: ["Документ", "Document"], ph: ["Паспорт РФ", "Passport"] },
    series: { l: ["Серия", "Series"], type: "password" },
    issued_by: { l: ["Кем выдан", "Issued by"] },
    issue_date: { l: ["Дата выдачи", "Issue date"], type: "date" },
    issuer_code: { l: ["Код подразделения", "Issuer code"] },
    expiry_date: { l: ["Действует до", "Valid until"], type: "date" },
    birth_place: { l: ["Место рождения", "Place of birth"] },
    company_name: { l: ["Название организации", "Company name"], ac: "organization" },
    inn: { l: ["ИНН", "Tax ID (INN)"], mode: "numeric", max: 12 },
    kpp: { l: ["КПП", "KPP"], mode: "numeric", max: 9 },
    ogrn: { l: ["ОГРН", "OGRN"], mode: "numeric", max: 15 },
    legal_address: { l: ["Юридический адрес", "Legal address"] },
    code: { l: ["Код", "Code"], mode: "numeric", ac: "one-time-code" },
    value: { l: ["Значение", "Value"], type: "password" },
  };

  const KIND_FIELD = {
    card: { number: { l: ["Номер карты", "Card number"], type: "password", mode: "numeric", ac: "cc-number", max: 23 } },
    document: { number: { l: ["Номер документа", "Document number"], type: "password" } },
  };
  const KINDS = {
    phone: { l: ["Телефон", "Phone"], group: "personal", fields: ["phone"] },
    email: { l: ["E-mail", "Email"], group: "personal", fields: ["email"] },
    identity: { l: ["Личные данные (ФИО, дата рождения…)", "Personal details (name, birth date…)"], group: "personal",
      fields: ["last_name", "first_name", "middle_name", "birth_date", "phone", "email", "country", "city", "address", "postal_code"] },
    address: { l: ["Адрес доставки / плательщика", "Shipping / billing address"], group: "personal",
      fields: ["address_type", "recipient", "country", "region", "city", "street", "house", "apartment", "postal_code"] },
    document: { l: ["Паспорт / документ", "Passport / ID"], group: "personal",
      fields: ["doc_type", "series", "number", "issue_date", "issued_by", "issuer_code", "expiry_date", "birth_place"] },
    company: { l: ["Организация (ИНН, КПП…)", "Company (tax IDs)"], group: "personal", fields: ["company_name", "inn", "kpp", "ogrn", "legal_address"] },
    login: { l: ["Логин сайта", "Site login"], group: "secret", fields: ["username", "password"], domain: true },
    card: { l: ["Банковская карта", "Payment card"], group: "secret", fields: ["number", "exp_month", "exp_year", "cvc", "holder"] },
    text: { l: ["Другой секрет", "Other secret"], group: "secret", fields: ["value"] },
  };

  const CC = new Set(("1 7 20 27 30 31 32 33 34 36 39 40 41 43 44 45 46 47 48 49 51 52 53 54 55 56 57 58 60 61 62 63 64 65 66 81 82 84 86 90 91 92 93 94 95 98 " +
    "211 212 213 216 218 220 221 222 223 224 225 226 227 228 229 230 231 232 233 234 235 236 237 238 239 240 241 242 243 244 245 246 248 249 250 251 252 253 254 255 256 257 258 260 261 262 263 264 265 266 267 268 269 290 291 297 298 299 " +
    "350 351 352 353 354 355 356 357 358 359 370 371 372 373 374 375 376 377 378 380 381 382 383 385 386 387 389 420 421 423 500 501 502 503 504 505 506 507 508 509 590 591 592 593 594 595 596 597 598 599 " +
    "670 672 673 674 675 676 677 678 679 680 681 682 683 685 686 687 688 689 690 691 692 850 852 853 855 856 880 886 960 961 962 963 964 965 966 967 968 970 971 972 973 974 975 976 977 992 993 994 995 996 998").split(" "));
  function phoneParts(raw) {
    const text = String(raw || "").trim();
    let digits = text.replace(/\D/g, "");
    if (text.startsWith("00")) digits = digits.slice(2);
    else if (!text.startsWith("+")) {
      if (digits.length === 11 && digits[0] === "8") digits = "7" + digits.slice(1);
      else if (!(digits.length === 11 && digits[0] === "7")) return { digits, plus: false };
    }
    const size = [3, 2, 1].find((n) => CC.has(digits.slice(0, n)) && digits.length > n) || 1;
    return { digits, plus: true, cc: digits.slice(0, size), national: digits.slice(size) };
  }

  function formatPhone(raw) {
    const p = phoneParts(raw);
    if (!p.plus) return String(raw || "");

    const n = p.national, groups = [n.slice(0, 3), n.slice(3, 6)].filter(Boolean);
    const rest = n.slice(6);
    if (rest.length > 3) { const head = rest.length % 2 ? 3 : 0; if (head) groups.push(rest.slice(0, head)); groups.push(...rest.slice(head).match(/\d{2}/g)); }
    else if (rest) groups.push(rest);
    return ("+" + p.cc + " " + groups.join(" ")).trim();
  }
  function luhn(number) {
    const d = String(number).replace(/\D/g, "");
    if (d.length < 12 || d.length > 19) return false;
    let sum = 0;
    for (let i = 0; i < d.length; i++) { let v = +d[d.length - 1 - i]; if (i % 2) { v *= 2; if (v > 9) v -= 9; } sum += v; }
    return sum % 10 === 0;
  }
  const fieldMeta = (name, kind) => ({ ...(FIELDS[name] || { l: [name, name] }), ...((KIND_FIELD[kind] || {})[name] || {}) });
  const fieldLabel = (name, kind) => { const m = fieldMeta(name, kind); return en() ? m.l[1] : m.l[0]; };

  function validate(name, value, kind) {
    const v = String(value || "").trim();
    if (!v) return "";
    if (name === "phone") {
      const p = phoneParts(v);
      if (!p.plus) return T("Укажите номер с кодом страны: +7…, +370…, +49…", "Include the country code: +1…, +44…, +49…");
      if (p.digits.length < 8 || p.digits.length > 15) return T("Номер: код страны и 8–15 цифр", "A number has a country code and 8–15 digits");
    }
    if (name === "email" && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(v)) return T("Некорректный e-mail", "Invalid email");
    if (name === "number" && kind === "card" && !luhn(v)) return T("Номер карты с ошибкой", "The card number is not valid");
    if (name === "cvc" && !/^\d{3,4}$/.test(v)) return T("CVC — 3 или 4 цифры", "CVC is 3 or 4 digits");
    if (name === "exp_month" && !(+v >= 1 && +v <= 12)) return T("Месяц от 01 до 12", "Month 01–12");
    if (name === "exp_year" && !/^(\d{2}|\d{4})$/.test(v)) return T("Год: ГГ или ГГГГ", "Year: YY or YYYY");
    if (name === "inn" && !/^(\d{10}|\d{12})$/.test(v)) return T("ИНН — 10 или 12 цифр", "INN is 10 or 12 digits");
    return "";
  }

  function inputHtml(name, opts = {}) {
    const m = fieldMeta(name, opts.kind);
    const label = en() ? m.l[1] : m.l[0];
    const cls = opts.cls ? ` class="${esc(opts.cls)}"` : "";
    const ph = opts.placeholder != null ? opts.placeholder : (Array.isArray(m.ph) ? (en() ? m.ph[1] : m.ph[0]) : (m.ph || ""));
    let control;
    if (m.options) {
      control = `<select${cls} data-field="${esc(name)}">${m.options.map(([v, l]) => `<option value="${esc(v)}">${esc(en() ? l[1] : l[0])}</option>`).join("")}</select>`;
    } else {
      control = `<input${cls} type="${m.type || "text"}" data-field="${esc(name)}" autocomplete="${esc(m.ac || "off")}" spellcheck="false"` +
        `${m.mode ? ` inputmode="${m.mode}"` : ""}${m.max ? ` maxlength="${m.max}"` : ""}${ph ? ` placeholder="${esc(ph)}"` : ""}` +
        `${opts.required ? " required" : ""} aria-label="${esc(label)}">`;
    }
    return `<label class="dfy-field" data-for="${esc(name)}"><span>${esc(label)}</span>${control}<small class="dfy-err" aria-live="polite"></small></label>`;
  }

  function collect(form, kind) {
    const fields = {};
    let ok = true;
    form.querySelectorAll("[data-field]").forEach((input) => {
      const name = input.dataset.field, value = input.value;
      const error = validate(name, value, kind);
      const slot = input.closest(".dfy-field")?.querySelector(".dfy-err");
      if (slot) slot.textContent = error;
      input.toggleAttribute("aria-invalid", !!error);
      if (error) { ok = false; return; }
      if (String(value).trim() || (name === "password" && value)) fields[name] = name === "phone" ? "+" + phoneParts(value).digits : value;
    });
    return ok ? fields : null;
  }

  function onInput(ev) {
    const input = ev.target;
    const name = input && input.dataset && input.dataset.field;
    if (!name) return;
    const slot = input.closest(".dfy-field")?.querySelector(".dfy-err");
    if (slot && slot.textContent) { slot.textContent = ""; input.removeAttribute("aria-invalid"); }
    if (name === "phone" && ev.inputType !== "deleteContentBackward" && /^\+\d/.test(input.value)) {
      const end = input.selectionEnd === input.value.length;
      input.value = formatPhone(input.value);
      if (end) input.setSelectionRange(input.value.length, input.value.length);
    } else if (input.inputMode === "numeric" && input.type !== "password" && name !== "code") {
      const digits = input.value.replace(/\D/g, "");
      if (digits !== input.value) input.value = digits;
    }
  }
  const CHANNEL = { sms: ["SMS", "SMS"], email: ["письмо", "email"], app: ["приложение", "app"], call: ["звонок", "call"] };

  function codeHtml(q, cls = "") {
    const len = q.length || 0;
    const via = CHANNEL[q.channel] ? (en() ? CHANNEL[q.channel][1] : CHANNEL[q.channel][0]) : "";
    const where = [via, q.sent_to].filter(Boolean).join(" → ");
    return `<form data-dfy-code data-len="${len}" data-numeric="${q.numeric === false ? "0" : "1"}" autocomplete="off">
      ${where ? `<div class="dfy-note">${esc(T("Код отправлен: ", "Code sent: "))}${esc(where)}</div>` : ""}
      <input class="dfy-code ${esc(cls)}" data-code type="text" ${q.numeric === false ? 'autocapitalize="characters"' : 'inputmode="numeric" pattern="[0-9]*"'} autocomplete="one-time-code"
        maxlength="${len ? len + 4 : 16}" placeholder="${len ? "•".repeat(len) : esc(T("Код", "Code"))}" aria-label="${esc(T("Код подтверждения", "Verification code"))}" spellcheck="false">
      <small class="dfy-err" aria-live="polite"></small>
      <div class="dfy-row"><button type="submit" class="dfy-btn primary ${esc(cls)}" data-dfy-code-send>${T("Отправить код", "Send code")}</button>
      ${q.can_resend !== false ? `<button type="button" class="dfy-btn ${esc(cls)}" data-dfy="resend">${T("Код не пришёл — отправить снова", "No code — send again")}</button>` : ""}
      <button type="button" class="dfy-btn ${esc(cls)}" data-dfy="decline-code">${T("Отмена", "Cancel")}</button></div>
      <div class="dfy-note" data-dfy-timer data-deadline="${Date.now() + (q.expires_in || 600) * 1000}"></div></form>`;
  }
  function codeValue(form) {
    const input = form.querySelector("[data-code]");
    const raw = String(input?.value || "").replace(/[\s-]/g, "");
    const numeric = form.dataset.numeric !== "0";
    const value = numeric ? raw.replace(/\D/g, "") : raw.replace(/[^A-Za-z0-9]/g, "");
    const len = +form.dataset.len || 0;
    const slot = form.querySelector(".dfy-err");
    const bad = !value || (len && value.length !== len) || value.length < 3;
    if (slot) slot.textContent = bad ? (len ? T(`Нужно ${len} символов`, `${len} characters needed`) : T("Введите код", "Enter the code")) : "";
    return bad ? "" : value;
  }

  function tickTimers(root) {
    let any = false;
    root.querySelectorAll("[data-dfy-timer]").forEach((el) => {
      const left = Math.max(0, Math.round((+el.dataset.deadline - Date.now()) / 1000));
      el.textContent = left ? T(`Ожидаю код ещё ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`, `Waiting for the code ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`)
        : T("Время вышло — нажмите «отправить снова»", "Time is up — press “send again”");
      if (left) any = true;
    });
    return any;
  }

  function onCodeInput(ev, submit) {
    const input = ev.target;
    if (!input || !input.matches || !input.matches("[data-code]")) return;
    const form = input.closest("[data-dfy-code]");
    if (form.dataset.numeric !== "0") { const d = input.value.replace(/\D/g, ""); if (d !== input.value) input.value = d; }
    const len = +form.dataset.len || 0;
    if (len && input.value.replace(/[\s-]/g, "").length === len) submit(form);
  }

  function autofocus(box) {
    const active = document.activeElement;
    const busy = active && active !== document.body && !box.contains(active) && active.matches && active.matches("input,textarea,select,[contenteditable=true]");
    if (busy) return;
    const first = box.querySelector("[data-code],[data-field],[data-dfy-text]");
    if (first) try { first.focus({ preventScroll: true }); } catch (e) {}
  }

  const TAKEOVER_WHY = {
    captcha: ["Сайт просит доказать, что вы не робот. Перехватите управление, пройдите проверку и верните управление ИИ.", "The site wants proof you are not a robot. Take control, pass the check and hand control back."],
    login: ["Нужно войти на сайт — это лучше сделать вам. Перехватите управление, войдите и верните управление ИИ.", "Signing in is best done by you. Take control, sign in and hand control back."],
    "2fa": ["Нужно подтвердить вход (код или приложение). Перехватите управление и верните его ИИ после подтверждения.", "The sign-in needs a confirmation (code or app). Take control and hand it back once confirmed."],
    verification: ["Сайт просит пройти проверку. Перехватите управление и верните его ИИ после проверки.", "The site asks for a check. Take control and hand it back afterwards."],
    other: ["ИИ просит вас сделать шаг в браузере. Перехватите управление и верните его, когда закончите.", "The AI asks you to do a step in the browser. Take control and hand it back when done."],
  };

  let takeoverLoad = null;
  function loadTakeover() {
    if (window.MemuDFYTakeover) return Promise.resolve(window.MemuDFYTakeover);
    if (!takeoverLoad) takeoverLoad = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/dfy-takeover.js?v=3.5-r33";
      s.onload = () => resolve(window.MemuDFYTakeover);
      s.onerror = () => { takeoverLoad = null; reject(new Error(T("Не удалось загрузить окно перехвата", "Could not load the takeover window"))); };
      document.head.appendChild(s);
    });
    return takeoverLoad;
  }

  const syncTakeover = (task) => { try { if (window.MemuDFYTakeover && window.MemuDFYTakeover.sync) window.MemuDFYTakeover.sync(task); } catch (e) {} };
  const takeover = {
    open: (id, opts) => loadTakeover().then((m) => m.open(id, opts)),
    giveBack: (id) => loadTakeover().then((m) => m.giveBack(id)),
  };

  const onMachine = (task) => !!(task.target && !task.target.browser);
  function takeoverHtml(task) {
    if (FINAL.includes(task.status) || onMachine(task) || (lastStatus && lastStatus.takeover === false)) return "";
    if (task.control === "user") {
      return `<div class="dfy-takeover"><span><b>${T("Управляете вы", "You're in control")}</b> — ${T("ИИ на паузе и ждёт, пока вы вернёте управление.", "the AI is paused until you hand control back.")}</span>
        <button class="dfy-btn" data-dfy="takeover">${T("Открыть окно", "Open window")}</button>
        <button class="dfy-btn primary" data-dfy="giveback">${T("Вернуть управление ИИ", "Return control to AI")}</button></div>`;
    }
    return "";
  }

  function questionHtml(q, task) {
    if (!q) return "";

    if (q.kind === "stuck" && window.MemuStuck) return window.MemuStuck.dfyQuestionHtml(task || { id: "" }, q, { compact: true });
    if (q.kind === "takeover") {
      const why = TAKEOVER_WHY[q.why] || TAKEOVER_WHY.other;
      return `<div class="dfy-q" data-kind="takeover"><p>${esc(q.prompt || (en() ? why[1] : why[0]))}</p>
        <div class="dfy-note" style="margin:-4px 0 8px">${T("Окно браузера откроется здесь. Когда закончите — «Вернуть управление ИИ»: он получит описание ваших действий (пароли скрыты) и продолжит.", "The browser opens right here. When you are done, press “Return control to AI”: it gets a description of what you did (passwords hidden) and continues.")}</div>
        <div class="dfy-row"><button class="dfy-btn primary" data-dfy="takeover">${T("Перехватить управление", "Take control")}</button>
        <button class="dfy-btn" data-dfy="skip">${T("Пропустить", "Skip")}</button></div></div>`;
    }
    if (q.kind === "confirm" && q.machine && window.MemuDFYRemote) return window.MemuDFYRemote.confirmHtml(q);
    if (q.kind === "confirm") {
      return `<div class="dfy-q" data-kind="confirm"><p>${esc(q.prompt)}</p>
        ${q.amount ? `<div class="dfy-amount">${esc(T("Сумма: ", "Total: "))}${esc(q.amount)}</div>` : ""}
        <div class="dfy-row"><button class="dfy-btn primary" data-dfy="approve">${T("Подтвердить", "Approve")}</button>
        <button class="dfy-btn" data-dfy="reject">${T("Отклонить", "Decline")}</button></div></div>`;
    }
    if (q.kind === "code") {
      return `<div class="dfy-q" data-kind="code"><p>🔑 ${esc(q.prompt || T("Введите код подтверждения", "Enter the verification code"))}${q.domain ? ` <span class="dfy-note">(${esc(q.domain)})</span>` : ""}</p>
        ${codeHtml(q)}<div class="dfy-note">${T("Код не попадёт в чат и в промпт модели: MemuAI сам введёт его на сайте и забудет после задачи.", "The code never reaches the chat or the model: MemuAI types it on the site and forgets it after the task.")}</div></div>`;
    }
    if (q.kind === "secret") {
      const fields = (q.fields || []).map((f) => inputHtml(f, { kind: q.secret_kind })).join("");
      const existing = (q.existing || []).map((r) => `<button type="button" class="dfy-btn" data-dfy-use="${esc(r.id)}">${T("Использовать", "Use")} ${esc(r.mask)}</button>`).join("");
      const personal = (KINDS[q.secret_kind] || {}).group === "personal";
      return `<div class="dfy-q" data-kind="secret"><p>${personal ? "🪪" : "🔒"} ${esc(q.prompt || T("Нужны данные", "Data needed"))}${q.domain ? ` <span class="dfy-note">(${esc(q.domain)})</span>` : ""}</p>
        ${existing ? `<div class="dfy-row" style="margin:0 0 8px">${existing}</div>` : ""}
        <form data-dfy-secret autocomplete="off" novalidate><div class="dfy-fields">${fields}</div>
        <label class="dfy-save"><input type="checkbox" data-dfy-save checked> ${personal ? T("Запомнить для следующих задач", "Remember for future tasks") : T("Сохранить в защищённом хранилище", "Save to the secure vault")}</label>
        <div class="dfy-row"><button type="submit" class="dfy-btn primary">${T("Передать безопасно", "Send securely")}</button>
        <button type="button" class="dfy-btn" data-dfy="decline-secret">${T("Не давать", "Don't provide")}</button></div></form>
        <div class="dfy-note">${T("Значения шифруются и не попадают в чат, в промпт модели и в журнал. ИИ видит только маску.", "Values are encrypted and never reach the chat, the model prompt or the log. The AI sees only a mask.")}${q.secret_kind === "login" && q.domain ? " " + esc(T("Логин будет вводиться только на ", "The login is entered only on ")) + esc(q.domain) + "." : ""}</div></div>`;
    }
    if (q.kind === "form" && q.form && window.MemuAskForm && task && task.id) {

      const rec = { id: `dfy-${task.id}-${q.id || "q"}`, form: q.form, status: "pending" };
      window.MemuAskForm.mount(rec, { compact: true, onSubmit: async (answers, skipped) => {
        await answer(task.id, Object.assign({ question_id: q.id || undefined }, skipped ? { skip: true } : { answers }));
        return {};
      } });
      return `<div class="dfy-q" data-kind="form">${window.MemuAskForm.html(rec, { compact: true })}</div>`;
    }
    const choices = (q.choices || []).map((c) => `<button class="dfy-btn" data-dfy-choice="${esc(c)}">${esc(c)}</button>`).join("");
    return `<div class="dfy-q" data-kind="${esc(q.kind)}"><p>${esc(q.prompt)}</p>
      ${choices ? `<div class="dfy-row" style="margin:0 0 8px">${choices}</div>` : ""}
      <textarea rows="2" data-dfy-text placeholder="${choices ? T("Или свой вариант…", "Or your own answer…") : T("Ваш ответ…", "Your answer…")}"></textarea>
      <div class="dfy-row"><button class="dfy-btn primary" data-dfy="send">${T("Ответить", "Reply")}</button>
      <button class="dfy-btn" data-dfy="skip">${T("Не отвечать", "Skip")}</button></div></div>`;
  }

  function renderCard(container, id, opts = {}) {
    injectStyle();
    if (!container) return { stop() {}, el: null };
    if (container.__dfyStop) container.__dfyStop();
    const el = document.createElement("div");
    el.className = "dfy-card";
    el.dataset.dfyCard = id;
    container.innerHTML = "";
    container.appendChild(el);
    let task = null, showAll = false, bigShot = false, busy = false, questionId = "", timer = 0;

    const paint = () => {
      if (!task) { el.innerHTML = `<div class="dfy-head"><span class="dfy-badge">DFY</span><span class="dfy-goal">${T("Загрузка…", "Loading…")}</span></div>`; return; }
      const steps = task.steps || [];
      const shown = showAll ? steps : steps.slice(-6);
      const lastShot = [...steps].reverse().find((s) => s.screenshot_url);
      const label = (task.control === "user" ? STATUS_LABEL.user : STATUS_LABEL[task.status]) || [task.status, task.status];

      const sameQuestion = task.question && task.question.id === questionId;
      const keep = sameQuestion ? el.querySelector(".dfy-q") : null;
      const oldShot = el.querySelector(".dfy-shot img");
      const remote = task.target && window.MemuDFYRemote;
      el.classList.toggle("dfy-card--machine", onMachine(task));
      el.innerHTML = `<div class="dfy-head"><span class="dfy-badge">DFY</span><span class="dfy-goal">${esc(task.goal)}</span>
          ${remote ? remote.targetChip(task.target) : ""}<span class="dfy-chip ${esc(task.control === "user" ? "user" : task.status)}">${esc(en() ? label[1] : label[0])}</span></div>
        ${steps.length > 6 ? `<button class="dfy-more" data-dfy="toggle-steps">${showAll ? T("Свернуть шаги", "Collapse steps") : T(`Все шаги (${steps.length})`, `All steps (${steps.length})`)}</button>` : ""}
        ${shown.length ? `<ol class="dfy-steps">${shown.map((s) => `<li class="${s.ok === false ? "bad" : ""}"><b>${s.n}</b><span class="act">${esc(s.action)}</span><span>${esc(s.detail)}</span></li>${remote ? remote.stepExtra(s, { task: task.id, live: s === steps[steps.length - 1] && !FINAL.includes(task.status) }) : ""}${Array.isArray(s.journal) && s.journal.length ? `<ol class="dfy-journal">${s.journal.slice(-12).map((l) => `<li>${esc(l)}</li>`).join("")}</ol>` : ""}`).join("")}</ol>` : ""}
        ${lastShot && !FINAL.includes(task.status) || (lastShot && opts.showFinalShot) ? `<div class="dfy-shot"><img alt="" src="${esc(lastShot.screenshot_url)}" class="${bigShot ? "big" : ""}" data-dfy="shot"></div>` : ""}
        ${takeoverHtml(task)}
        <div class="dfy-q-slot"></div>
        ${task.result ? `<div class="dfy-result">${esc(task.result)}</div>` : ""}
        ${task.error ? `<div class="dfy-error">${esc(task.error)}</div>` : ""}
        ${FINAL.includes(task.status) ? "" : `<div class="dfy-row">${task.control !== "user" && task.status !== "queued" && !onMachine(task) && !(task.question && task.question.kind === "takeover") && !(lastStatus && lastStatus.takeover === false)
          ? `<button class="dfy-btn" data-dfy="takeover" title="${esc(T("ИИ встанет на паузу, а вы сможете сами кликать и печатать в его браузере", "The AI pauses and you click and type in its browser yourself"))}">${T("Перехватить управление", "Take control")}</button>` : ""}<button class="dfy-btn danger" data-dfy="cancel">${T("Остановить", "Stop")}</button></div>`}`;
      keepShot(oldShot);
      const slot = el.querySelector(".dfy-q-slot");
      if (keep && task.control !== "user") slot.replaceWith(keep);
      else if (task.question && task.control !== "user") {
        slot.outerHTML = questionHtml(task.question, task); questionId = task.question.id;
        const box = el.querySelector(".dfy-q");
        if (box && task.question.kind !== "confirm") autofocus(box);
      } else questionId = "";
      if (busy) el.querySelectorAll(".dfy-q button").forEach((b) => (b.disabled = true));
      clearInterval(timer);
      if (tickTimers(el)) timer = setInterval(() => { if (!el.isConnected || !tickTimers(el)) clearInterval(timer); }, 1000);
    };

    const keepShot = (old) => {
      const fresh = el.querySelector(".dfy-shot img");
      if (!old || !fresh) return;
      const next = fresh.getAttribute("src") || "";
      old.className = fresh.className;
      fresh.replaceWith(old);
      if (old.getAttribute("src") === next || old.dataset.loading === next) return;
      old.dataset.loading = next;
      const pre = new Image();
      pre.onload = () => { if (old.dataset.loading === next) { old.src = next; delete old.dataset.loading; } };
      pre.onerror = () => { if (old.dataset.loading === next) delete old.dataset.loading; };
      pre.src = next;
    };
    const sendCode = (form) => {
      const value = codeValue(form);
      if (!value) return;
      const input = form.querySelector("[data-code]");
      if (input) input.value = "";
      send({ code: value });
    };

    const send = async (payload) => {
      if (busy) return;
      busy = true; paint();

      try { await answer(id, Object.assign({ question_id: (task && task.question && task.question.id) || undefined }, payload)); }
      catch (e) { (window.toast || alert)(e.message, "err"); }
      finally { busy = false; }
      try { task = await get(id); } catch (e) {}
      paint();
    };

    el.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-dfy],[data-dfy-choice],[data-dfy-use]");
      if (!b || b.disabled) return;
      ev.preventDefault();
      const what = b.dataset.dfy;
      if (b.dataset.dfyChoice != null) return send({ answer: b.dataset.dfyChoice });
      if (b.dataset.dfyUse) return send({ secret_ref: b.dataset.dfyUse });
      if (what === "takeover") { takeover.open(id, { question: task && task.question }); return; }
      if (what === "giveback") { b.disabled = true; takeover.giveBack(id).finally(() => { b.disabled = false; }); return; }
      if (what === "approve") return send({ approve: true });
      if (what === "reject") return send({ approve: false });
      if (what === "decline-secret" || what === "decline-code" || what === "skip") return send({ approve: false });
      if (what === "resend") return send({ resend: true });
      if (what === "send") {
        const v = el.querySelector("[data-dfy-text]")?.value.trim();
        if (v) send({ answer: v });
        return;
      }
      if (what === "cancel") { b.disabled = true; cancel(id).then((t) => { task = { ...task, status: t.status, control: t.control || "ai", question: null }; paint(); syncTakeover(task); }).catch((e) => (window.toast || alert)(e.message, "err")); return; }
      if (what === "toggle-steps") { showAll = !showAll; paint(); return; }
      if (what === "shot") { bigShot = !bigShot; b.classList.toggle("big", bigShot); }
    });
    el.addEventListener("input", (ev) => { onInput(ev); onCodeInput(ev, sendCode); });
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey && ev.target.matches("[data-dfy-text]")) { ev.preventDefault(); el.querySelector('[data-dfy="send"]')?.click(); }
    });
    el.addEventListener("submit", (ev) => {
      const code = ev.target.closest("[data-dfy-code]");
      if (code) { ev.preventDefault(); sendCode(code); return; }
      const form = ev.target.closest("[data-dfy-secret]");
      if (!form) return;
      ev.preventDefault();
      const q = task?.question || {};
      const fields = collect(form, q.secret_kind);
      if (!fields) { form.querySelector("[aria-invalid]")?.focus(); return; }
      if (!Object.keys(fields).length) { form.querySelector("[data-field]")?.focus(); return; }
      const save = !!form.querySelector("[data-dfy-save]")?.checked;

      form.querySelectorAll("input[data-field]").forEach((i) => { i.value = ""; });
      send({ secret: { kind: q.secret_kind, label: q.label, domain: q.domain, fields, save } });
    });

    paint();
    const stopWatch = watch(id, (t) => {
      if (t.gone) {

        const f = opts.fallback || {};
        task = { id, goal: f.goal || "", status: FINAL.includes(f.status) ? f.status : "failed", steps: [],
          result: f.result || "", error: FINAL.includes(f.status) ? (f.error || "") : T("Задача прервана: MemuAI был перезапущен", "The task was interrupted: MemuAI restarted") };
        paint(); return;
      }
      task = t; paint(); syncTakeover(t);
      if (typeof opts.onUpdate === "function") try { opts.onUpdate(t); } catch (e) {}
      if (FINAL.includes(t.status) && typeof opts.onFinish === "function") try { opts.onFinish(t); } catch (e) {}
    });
    function stop() { stopWatch(); clearInterval(timer); container.__dfyStop = null; }
    container.__dfyStop = stop;
    return { stop, el };
  }

  const cards = new Map();
  function hydrate(root = document) {
    (root.querySelectorAll ? root : document).querySelectorAll("[data-dfy-task]:not([data-dfy-bound])").forEach((host) => {
      host.setAttribute("data-dfy-bound", "1");
      const id = host.dataset.dfyTask;
      const known = cards.get(id);
      if (known && known.el) { host.innerHTML = ""; host.appendChild(known.el); return; }
      const card = renderCard(host, id, {
        fallback: { goal: host.dataset.dfyGoal, status: host.dataset.dfyStatus, result: host.dataset.dfyResult, error: host.dataset.dfyError },
        onFinish: (t) => document.dispatchEvent(new CustomEvent("dfy-finish", { detail: t })),
      });
      host.__dfyStop = null;
      cards.set(id, card);
      if (cards.size > 40) { const [first] = cards.keys(); const old = cards.get(first); cards.delete(first); if (old && !old.el.isConnected) old.stop(); }
    });
  }

  let lastStatus = null;
  async function refreshStatus() { try { lastStatus = await status(); } catch (e) {} return lastStatus; }
  function promptBlock(lang) {
    const machines = lastStatus && lastStatus.enabled && lastStatus.model && ((lastStatus.machines || []).length || lastStatus.local_system);
    if (!lastStatus || !(lastStatus.ready || machines)) return "";
    return "\n\n<dfy_tool>Do For You (DFY) is available: a separate agent with its own persistent browser (saved logins and cookies) " +
      "that performs real web tasks for the user — booking, ordering, registering, filling forms, buying, cancelling. It shows its steps, " +
      "asks the user for missing choices, logins, payment cards, phone, address, passport data and SMS codes through secure forms (values never reach you), and always asks the user's " +
      "go-ahead with the total before paying, booking or sending. When the user wants you to DO such a task on a website (not only find information) " +
      "and the essential choices are known (what, which site or business, dates/times, people, price limit), append exactly one hidden block:\n" +
      "```memuai-dfy\n{\"goal\":\"a complete, self-contained instruction with every detail and constraint the user gave\"}\n```\n" +
      "and tell the user in one short sentence that DFY is starting and will ask them before anything irreversible. If an essential choice is missing, " +
      "ask the user first instead of emitting the block. Never put passwords, card numbers, codes or passport data into the goal (DFY asks for them itself). Do not add choices the user did not make." +
      machinesBlock() + "</dfy_tool>";
  }

  function machinesBlock() {
    const browsers = (lastStatus.machines || []).filter((m) => m.kind === "http").map((m) => `"${m.id}/browser"`);
    const rows = (lastStatus.machines || []).map((m) => `"${m.id}" = ${m.name}${m.os_label ? " (" + m.os_label + ")" : ""}`);
    if (lastStatus.local_system) rows.unshift(`"local" = this computer (the user's own PC)`);
    if (!rows.length) return "";
    const picked = window.MemuDFYRemote ? window.MemuDFYRemote.selected() : "";
    return "\nDFY can also work on a whole machine instead of the browser: shell commands, files, downloads, packages, processes, services. " +
      "Machines: " + rows.join("; ") + ". When the user wants something done on one of them (a server, \"on my PC\", by its name), add \"target\":\"<id>\" to the block; " +
      "without \"target\" DFY uses its browser." + (browsers.length ? ` To use a website FROM one of these machines (its IP, its network, its logged-in browser), use "target":"<id>/browser" (${browsers.join(", ")}).` : "") + " DFY itself asks the user before anything destructive and never touches MemuAI or its own agent." +
      (picked ? ` The user picked "${picked}" as the DFY target in this window: use it unless they say otherwise.` : "");
  }
  function chatHtml(action) {
    if (!action) return "";
    if (action.id) {
      return `<div class="dfy-host" data-dfy-task="${esc(action.id)}" data-dfy-goal="${esc(action.goal || "")}" data-dfy-status="${esc(action.status || "")}" data-dfy-result="${esc(action.result || "")}" data-dfy-error="${esc(action.error || "")}"></div>`;
    }
    injectStyle();
    const failed = action.status === "failed";
    return `<div class="dfy-card"><div class="dfy-head"><span class="dfy-badge">DFY</span><span class="dfy-goal">${esc(action.goal || "")}</span>
      <span class="dfy-chip ${failed ? "failed" : "queued"}">${failed ? T("Ошибка", "Failed") : T("Запуск…", "Starting…")}</span></div>
      ${action.error ? `<div class="dfy-error">${esc(action.error)}</div>` : ""}</div>`;
  }
  const observer = new MutationObserver((records) => {
    for (const r of records) for (const n of r.addedNodes) {
      if (n.nodeType === 1 && (n.matches?.("[data-dfy-task]") || n.querySelector?.("[data-dfy-task]"))) { hydrate(document); return; }
    }
  });
  const boot = () => {
    hydrate(document); observer.observe(document.body, { childList: true, subtree: true });
    refreshStatus(); setInterval(refreshStatus, 60000);
  };
  if (document.body) boot(); else document.addEventListener("DOMContentLoaded", boot, { once: true });

  window.MemuDFY = {
    status: () => refreshStatus().then((st) => st || status()), start, get, list, watch, answer, cancel, renderCard, hydrate,
    promptBlock, chatHtml, refreshStatus, takeover, get lastStatus() { return lastStatus; },
    vault: {
      list: () => call("/api/dfy/vault"),
      add: (item) => call("/api/dfy/vault", { method: "POST", body: item || {} }),
      update: (id, item) => call(`/api/dfy/vault/${encodeURIComponent(id)}`, { method: "PUT", body: item || {} }),
      remove: (id) => call(`/api/dfy/vault/${encodeURIComponent(id)}`, { method: "DELETE" }),
    },
    clearSessions: () => call("/api/dfy/browser/clear", { method: "POST", body: {} }),
    forms: { FIELDS, KINDS, fieldLabel, inputHtml, validate, collect, onInput, formatPhone, phoneParts,
      codeHtml, codeValue, onCodeInput, tickTimers, autofocus, injectStyle },
  };
})();
