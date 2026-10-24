/* MemuAI 3.1 · Round 30 · /image and /video in the message box: a coloured chip with an icon, a hint for the argument.
 *
 * Same commands and words as the Mini App (tgapp/chatcmd.js): a message that starts with /image (/img) or /video is
 * not a question to the chat model — a picture or a video is generated from the description. The box says so:
 *   • the command becomes a chip (icon + accent pill) — drawn BEHIND the transparent textarea, so typing, the caret,
 *     selection, IME, paste, undo and autosize stay the textarea's own; the only change to the field is a
 *     `text-indent` that makes room for the icon (set in the capture phase of `input`, before the page's autosize);
 *   • after a bare command a ghost text says what to type («описание картинки…»);
 *   • a line under the box: «Картинка — нарисуется по описанию, текстового ответа не будет».
 * Programmatic `field.value = …` (send clears the box without an input event) is caught by the field's own value
 * setter, so the chip never outlives its text.
 *
 * Main window: `#input` (app.js sends /image and /video itself — media-routing.js).
 * Quick Access: `#qa-input`; it has no generator of its own, so such a message opens a new MemuAI chat with the
 * command and sends it there (the quick «handoff»; app.js `MemuQuickHandoff` → `send: true`).
 */
(function () {
  "use strict";
  const MEDIA_RE = /^\/(image|img|video)(?=\s|$)/i;
  const BARE_RE = /^\/(image|img|video)\s*$/i;
  const INDENT = 22;                 // px of the first line given to the icon
  const lang = () => {
    try { if (typeof PG_LANG !== "undefined") return PG_LANG; } catch (e) { /* not the main window */ }
    try { if (window.MemuQuick) return window.MemuQuick.lang(); } catch (e) { /* not ready */ }
    return document.documentElement.lang === "en" ? "en" : "ru";
  };
  const t = (ru, en) => (lang() === "en" ? en : ru);
  const ICONS = {
    image: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-9 9"/></svg>',
    video: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="13" height="14" rx="3"/><path d="m16 10 5-3v10l-5-3"/></svg>',
  };

  /** "image" | "video" | "" for a message text. */
  function kindOf(text) {
    const match = MEDIA_RE.exec(String(text || ""));
    return match ? (match[1].toLowerCase() === "video" ? "video" : "image") : "";
  }
  const word = (kind) => (kind === "video" ? t("Видео", "Video") : t("Картинка", "Image"));
  function ghost(kind) {
    return kind === "video"
      ? t("описание сцены — например: волны на закате, медленный пролёт камеры", "describe the scene — e.g. waves at sunset, a slow camera fly-over")
      : t("описание картинки — например: кот на луне, акварель", "describe the picture — e.g. a cat on the moon, watercolor");
  }

  const canvas = document.createElement("canvas");
  function textWidth(text, style) {
    const ctx = canvas.getContext("2d");
    ctx.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    const spacing = parseFloat(style.letterSpacing) || 0;
    return ctx.measureText(text).width + spacing * text.length;
  }

  /**
   * Mark media commands in `field`. opts: { note(kind) → text of the line under the box, noteAfter: element the
   * line is inserted after, onChange(kind) }. Returns { update, kind }.
   */
  function attach(field, opts = {}) {
    if (!field || field.__memuCmd) return field && field.__memuCmd;
    const parent = field.parentElement;
    if (getComputedStyle(parent).position === "static") parent.classList.add("cmdhl-host");
    const chip = document.createElement("div");
    chip.className = "cmdhl-chip";
    chip.hidden = true;
    chip.setAttribute("aria-hidden", "true");
    const hint = document.createElement("div");
    hint.className = "cmdhl-ghost";
    hint.hidden = true;
    hint.setAttribute("aria-hidden", "true");
    parent.insertBefore(chip, field);
    parent.insertBefore(hint, field.nextSibling);
    field.classList.add("cmdhl-field");
    let note = null;
    if (opts.noteAfter) {
      note = document.createElement("div");
      note.className = "cmdhl-note";
      note.hidden = true;
      note.setAttribute("role", "status");
      opts.noteAfter.insertAdjacentElement("afterend", note);
    }
    let kind = "", frame = 0;

    function place() {
      frame = 0;
      if (!kind) return;
      const style = getComputedStyle(field);
      const value = field.value;
      const command = MEDIA_RE.exec(value)[0];
      const padLeft = parseFloat(style.paddingLeft) || 0, padTop = parseFloat(style.paddingTop) || 0;
      const borderLeft = parseFloat(style.borderLeftWidth) || 0, borderTop = parseFloat(style.borderTopWidth) || 0;
      const fontSize = parseFloat(style.fontSize) || 14;
      const line = parseFloat(style.lineHeight) || fontSize * 1.4;
      const left = field.offsetLeft + borderLeft + padLeft;
      const top = field.offsetTop + borderTop + padTop - field.scrollTop;
      const height = Math.round(Math.min(line, fontSize * 1.55));
      const width = INDENT + textWidth(command, style);
      chip.style.left = Math.round(left - 4) + "px";
      chip.style.top = Math.round(top + (line - height) / 2) + "px";
      chip.style.width = Math.round(width + 6) + "px";     // 4 px before the icon, 2 px after the word
      chip.style.height = height + "px";
      chip.style.setProperty("--cmdhl-icon", Math.round(fontSize * 0.95) + "px");
      // The first line scrolled out of the box: no chip floating over the second one.
      chip.style.visibility = field.scrollTop > line / 2 ? "hidden" : "";
      const bare = BARE_RE.test(value) && !value.includes("\n");
      hint.hidden = !bare;
      if (bare) {
        const typed = INDENT + textWidth(value, style) + (/\s$/.test(value) ? 0 : textWidth(" ", style));
        const room = field.clientWidth - padLeft - (parseFloat(style.paddingRight) || 0) - typed;
        hint.textContent = ghost(kind);
        hint.style.left = Math.round(left + typed) + "px";
        hint.style.top = Math.round(top) + "px";
        hint.style.lineHeight = line + "px";
        hint.style.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        hint.style.maxWidth = Math.max(0, Math.floor(room)) + "px";
        hint.hidden = room < 40;
      }
    }
    function schedule() { if (!frame) frame = requestAnimationFrame(place); }

    function update() {
      const next = kindOf(field.value);
      if (next !== kind) {
        kind = next;
        // Before the page's autosize: the indent changes how the first line wraps.
        field.style.textIndent = kind ? INDENT + "px" : "";
        field.classList.toggle("cmdhl-on", !!kind);
        chip.hidden = !kind;
        if (kind) {
          chip.dataset.kind = kind;
          chip.innerHTML = `<span class="cmdhl-ic">${ICONS[kind]}</span>`;
          field.setAttribute("aria-description", word(kind) + ": " + (kind === "video" ? t("видео сгенерируется по описанию", "a video is generated from the description")
            : t("картинка нарисуется по описанию", "a picture is drawn from the description")));
        } else {
          hint.hidden = true;
          field.removeAttribute("aria-description");
        }
        if (note) {
          note.hidden = !kind;
          if (kind) {
            note.dataset.kind = kind;
            note.innerHTML = `<span class="cmdhl-ic">${ICONS[kind]}</span><b>${word(kind)}</b><span>${(opts.note || defaultNote)(kind)}</span>`;
          }
        }
        if (typeof opts.onChange === "function") { try { opts.onChange(kind); } catch (e) { /* host */ } }
      }
      if (kind) place(); else hint.hidden = true;
    }
    function defaultNote(k) {
      return k === "video" ? t("сгенерируется по описанию — текстового ответа не будет", "will be generated from the description — no text answer")
        : t("нарисуется по описанию — текстового ответа не будет", "will be drawn from the description — no text answer");
    }

    // Capture: runs before the page's own input handler (autosize, menus).
    field.addEventListener("input", update, true);
    field.addEventListener("scroll", schedule, { passive: true });
    // `field.value = …` from code (send, history, drafts) bypasses input events.
    // Round 33 (#21): chain to a setter another script already put on this field (app.js spell check clears its
    // underline there) — taking the prototype's setter here silently dropped it, and the red underline of a sent or
    // cleared message stayed on screen («phantom spell check»).
    const own = Object.getOwnPropertyDescriptor(field, "value") || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
    Object.defineProperty(field, "value", {
      configurable: true,
      get() { return own.get.call(this); },
      set(next) { own.set.call(this, next); update(); },
    });
    if (typeof ResizeObserver === "function") new ResizeObserver(schedule).observe(field);
    // A design or theme switch changes the font: measure again.
    new MutationObserver(schedule).observe(document.documentElement, { attributes: true, attributeFilter: ["data-design", "data-theme", "style", "class"] });
    window.addEventListener("resize", schedule);
    update();
    const api = { update, get kind() { return kind; } };
    field.__memuCmd = api;
    return api;
  }

  /* ---------------------------------------------------------------- the two message boxes */
  function initMain() {
    const field = document.getElementById("input");
    if (!field) return;
    attach(field, { noteAfter: document.querySelector(".composer .attach-row") });
  }
  function initQuick() {
    const field = document.getElementById("qa-input");
    const quick = window.MemuQuick;
    if (!field || !quick) return;
    const resize = () => { try { quick.resize(); } catch (e) { /* not ready */ } };
    attach(field, {
      noteAfter: document.getElementById("qa-form"),
      note: (kind) => kind === "video"
        ? t("откроется в MemuAI и сгенерируется там по описанию", "opens in MemuAI and is generated there from the description")
        : t("откроется в MemuAI и нарисуется там по описанию", "opens in MemuAI and is drawn there from the description"),
      onChange: resize,
    });
    quick.on("submit", (event) => {
      if (!kindOf(event.text) || !/^\/\S+\s+\S/.test(event.text)) return;
      event.preventDefault();
      quick.api("/api/quick/window", { method: "POST", body: { action: "handoff", handoff: { new_chat: true, draft: event.text, send: true } } })
        .then((r) => { if (!r || !r.ok) quick.toast(t("Картинки и видео создаются в окне MemuAI — откройте его", "Images and videos are made in the MemuAI window — open it"), "warn", 3200); })
        .catch(() => quick.toast(t("Не удалось открыть MemuAI", "Could not open MemuAI"), "warn"));
    });
  }
  function init() { initMain(); initQuick(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();

  window.MemuCmdHighlight = Object.freeze({ attach, kindOf });
})();
