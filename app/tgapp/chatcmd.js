

(function () {
  "use strict";
  (window.MemuTgPlugins = window.MemuTgPlugins || []).push(function (kit) {
    const { $, $$, esc, tr, icon, haptic } = kit;
    const COMMANDS = [
      { name: "image", aliases: ["img"], icon: "image", ru: "Нарисовать картинку по описанию", en: "Draw an image from a description" },
      { name: "video", aliases: [], icon: "play", ru: "Сгенерировать видео по описанию", en: "Generate a video from a description" },
    ];

    kit.hooks.composerExtras.push((C, view, entry, options = {}) => {
      if (options.code) return;
      const input = $("#msg-input", view), inner = $(".composer-inner", view);
      if (!input || !inner) return;
      const menu = document.createElement("div");
      menu.className = "slash-menu chat-cmd-menu"; menu.hidden = true; menu.setAttribute("role", "listbox");
      inner.classList.add("has-cmd-menu");
      inner.insertBefore(menu, inner.firstChild);
      if (window.MemuCmdHighlight) window.MemuCmdHighlight.attach(input, { noteAfter: $(".composer-tools", inner) });
      const M = { open: false, rows: [], index: 0 };

      function draw() {
        menu.hidden = !M.open;
        if (!M.open) { menu.innerHTML = ""; return; }
        menu.innerHTML = M.rows.map((row, i) => `<button class="slash-item arg ${i === M.index ? "active" : ""}" data-i="${i}" role="option" aria-selected="${i === M.index}">
          <span class="slash-ico">${icon(row.icon)}</span><code>/${esc(row.name)}</code><span class="slash-hint">${esc(tr("описание", "description"))}</span><span class="slash-desc">${esc(tr(row.ru, row.en))}</span></button>`).join("");
        $$("[data-i]", menu).forEach((b) => { b.onmousedown = (event) => event.preventDefault(); b.onclick = () => pick(+b.dataset.i); });
      }
      function update() {
        const value = input.value;
        const typed = /^\/(\w*)$/.exec(value);
        M.rows = typed ? COMMANDS.filter((row) => [row.name, ...row.aliases].some((name) => name.startsWith(typed[1].toLowerCase()))) : [];
        M.open = M.rows.length > 0; M.index = 0;
        draw();
      }
      function pick(index) {
        const row = M.rows[index]; if (!row) return;
        haptic("select");
        input.value = "/" + row.name + " ";
        input.focus(); input.setSelectionRange(input.value.length, input.value.length);
        input.dispatchEvent(new Event("input"));
      }
      input.addEventListener("input", update);
      input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input && M.open) { M.open = false; draw(); } }, 160));

      input.addEventListener("keydown", (event) => {
        if (!M.open) return;
        if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); M.index = (M.index + (event.key === "ArrowDown" ? 1 : -1) + M.rows.length) % M.rows.length; draw(); }
        else if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey && !event.isComposing)) { event.preventDefault(); event.stopImmediatePropagation(); pick(M.index); }
        else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); M.open = false; draw(); }
      }, true);
      update();
    });
  });
})();
