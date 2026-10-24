/* «Нарисовать»: холст для быстрого наброска, который прикрепляется к сообщению как картинка.
 *
 * Намеренно простой: одна строка инструментов, холст и две кнопки. Рисунок хранится списком
 * действий (штрих, фигура, текст, картинка), а не пикселями — поэтому отмена и повтор точные,
 * предметы можно двигать, а при изменении размера окна ничего не размывается.
 * Пока набросок не прикреплён, он не теряется: закройте окно и откройте снова — он на месте.
 */
"use strict";
const MemuSketch = (() => {
  const en = () => PG_LANG === "en";
  const t = (ru, eng) => (en() ? eng : ru);
  const W = 1600, H = 1000;                       // размер рисунка в его собственных точках
  const COLORS = ["#1f2328", "#e5484d", "#f5a524", "#30a46c", "#3b82f6", "#8e4ec6", "#ffffff"];
  const SIZES = [3, 7, 14, 28];
  const MAX_PNG = 4.5 * 1024 * 1024;                // запас до лимита вложения в 5 МБ
  const ICON = {
    pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4z"/>',
    marker: '<path d="m4 20 3-1 11-11-2-2L5 17z"/><path d="M14 6l4 4"/><path d="M3 21h7"/>',
    eraser: '<path d="m7 20-4-4L14 5l6 6-9 9z"/><path d="M11 20h10"/><path d="m9 10 6 6"/>',
    line: '<path d="M5 19 19 5"/>',
    arrow: '<path d="M5 19 19 5"/><path d="M10 5h9v9"/>',
    rect: '<rect x="4" y="6" width="16" height="12" rx="1.5"/>',
    ellipse: '<ellipse cx="12" cy="12" rx="8.5" ry="6"/>',
    text: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 18 5.5-5 4 3.5 2.5-2 4 3.5"/>',
    move: '<path d="M12 3v18M3 12h18M12 3l-3 3m3-3 3 3M12 21l-3-3m3 3 3-3M3 12l3-3m-3 3 3 3M21 12l-3-3m3 3-3 3"/>',
    undo: '<path d="M4 8h9a5 5 0 0 1 0 10H8"/><path d="m8 4-4 4 4 4"/>',
    redo: '<path d="M20 8h-9a5 5 0 0 0 0 10h5"/><path d="m16 4 4 4-4 4"/>',
    clear: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  };
  const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;
  const TOOLS = [
    ["pen", () => t("Карандаш", "Pen"), "P"], ["marker", () => t("Маркер", "Marker"), "M"], ["eraser", () => t("Ластик", "Eraser"), "E"],
    ["line", () => t("Линия", "Line"), "L"], ["arrow", () => t("Стрелка", "Arrow"), "A"], ["rect", () => t("Прямоугольник", "Rectangle"), "R"],
    ["ellipse", () => t("Овал", "Ellipse"), "O"], ["text", () => t("Текст", "Text"), "T"], ["image", () => t("Вставить картинку", "Insert an image"), "I"],
    ["move", () => t("Двигать и менять размер", "Move and resize"), "V"],
  ];

  /* История — список состояний рисунка; каждое состояние — массив действий, которые никогда не
     меняются на месте (сдвиг предмета создаёт его копию). Отмена и повтор — шаг по этому списку,
     поэтому одинаково отменяются и штрих, и перемещение, и удаление, и «Очистить». */
  const state = { history: [[]], at: 0, tool: "pen", color: COLORS[0], size: SIZES[1], draft: null, picked: -1, drag: null,
    root: null, canvas: null, ctx: null,
    get ops() { return this.history[this.at]; } };

  /* ------------------------------------------------------------ рисование */
  function strokePath(ctx, points) {
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    if (points.length === 1) ctx.lineTo(points[0][0] + 0.01, points[0][1]);
    // Сглаживание: кривая идёт через середины отрезков, а сами точки служат направляющими.
    for (let i = 1; i < points.length - 1; i++) {
      const mx = (points[i][0] + points[i + 1][0]) / 2, my = (points[i][1] + points[i + 1][1]) / 2;
      ctx.quadraticCurveTo(points[i][0], points[i][1], mx, my);
    }
    if (points.length > 1) ctx.lineTo(points[points.length - 1][0], points[points.length - 1][1]);
    ctx.stroke();
  }

  function drawOp(ctx, op) {
    ctx.save();
    ctx.lineCap = ctx.lineJoin = "round";
    ctx.strokeStyle = ctx.fillStyle = op.color || "#000";
    ctx.lineWidth = op.size || 4;
    if (op.kind === "stroke") {
      if (op.tool === "eraser") ctx.globalCompositeOperation = "destination-out";
      if (op.tool === "marker") { ctx.globalAlpha = 0.35; ctx.lineWidth = op.size * 2.6; ctx.lineCap = "butt"; }
      strokePath(ctx, op.points);
    } else if (op.kind === "shape") {
      const { x0, y0, x1, y1 } = op;
      ctx.beginPath();
      if (op.shape === "rect") ctx.strokeRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
      else if (op.shape === "ellipse") { ctx.ellipse((x0 + x1) / 2, (y0 + y1) / 2, Math.abs(x1 - x0) / 2, Math.abs(y1 - y0) / 2, 0, 0, Math.PI * 2); ctx.stroke(); }
      else {
        ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
        if (op.shape === "arrow") {
          const angle = Math.atan2(y1 - y0, x1 - x0), head = Math.max(14, op.size * 3.2);
          ctx.beginPath();
          ctx.moveTo(x1, y1);
          ctx.lineTo(x1 - head * Math.cos(angle - 0.42), y1 - head * Math.sin(angle - 0.42));
          ctx.lineTo(x1 - head * Math.cos(angle + 0.42), y1 - head * Math.sin(angle + 0.42));
          ctx.closePath(); ctx.fill();
        }
      }
    } else if (op.kind === "text") {
      ctx.font = `600 ${op.font}px "Segoe UI", system-ui, sans-serif`;
      ctx.textBaseline = "top";
      String(op.text).split("\n").forEach((line, i) => ctx.fillText(line, op.x, op.y + i * op.font * 1.25));
    } else if (op.kind === "image" && op.image) {
      ctx.drawImage(op.image, op.x, op.y, op.w, op.h);
    }
    ctx.restore();
  }

  function bounds(op) {
    if (op.kind === "stroke") {
      const xs = op.points.map((p) => p[0]), ys = op.points.map((p) => p[1]), pad = (op.size || 4) * (op.tool === "marker" ? 1.5 : 0.7) + 6;
      return [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad];
    }
    if (op.kind === "shape") return [Math.min(op.x0, op.x1) - 10, Math.min(op.y0, op.y1) - 10, Math.max(op.x0, op.x1) + 10, Math.max(op.y0, op.y1) + 10];
    if (op.kind === "text") {
      const lines = String(op.text).split("\n");
      const width = Math.max(...lines.map((line) => line.length)) * op.font * 0.6;
      return [op.x - 4, op.y - 4, op.x + width + 4, op.y + lines.length * op.font * 1.25 + 4];
    }
    return [op.x, op.y, op.x + op.w, op.y + op.h];
  }

  function shift(op, dx, dy) {
    if (op.kind === "stroke") op.points = op.points.map(([x, y]) => [x + dx, y + dy]);
    else if (op.kind === "shape") { op.x0 += dx; op.x1 += dx; op.y0 += dy; op.y1 += dy; }
    else { op.x += dx; op.y += dy; }
  }

  function redraw() {
    const { ctx } = state;
    if (!ctx) return;
    ctx.clearRect(0, 0, W, H);
    const moving = state.drag && state.drag.op;
    state.ops.forEach((op, index) => drawOp(ctx, moving && index === state.picked ? moving : op));
    if (state.draft) drawOp(ctx, state.draft);
    const picked = moving || state.ops[state.picked];
    if (state.tool === "move" && picked) {
      const [x0, y0, x1, y1] = bounds(picked);
      ctx.save();
      ctx.setLineDash([8, 6]); ctx.lineWidth = 2; ctx.strokeStyle = "#3b82f6";
      ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
      ctx.setLineDash([]); ctx.fillStyle = "#3b82f6";
      if (picked.kind !== "stroke") ctx.fillRect(x1 - 9, y1 - 9, 18, 18);       // уголок для размера
      ctx.restore();
    }
    syncButtons();
  }

  /* ------------------------------------------------------------ действия */
  function apply(ops) {
    state.history.length = state.at + 1;          // после нового действия «вернуть» уже нечего
    state.history.push(ops);
    if (state.history.length > 200) state.history.shift();
    state.at = state.history.length - 1;
    state.draft = null;
    redraw();
  }
  function commit(op) { apply(state.ops.concat([op])); }
  function undo() { if (state.at > 0) { state.at -= 1; state.picked = -1; redraw(); } }
  function redo() { if (state.at < state.history.length - 1) { state.at += 1; state.picked = -1; redraw(); } }
  function clear() { if (state.ops.length) { state.picked = -1; apply([]); } }
  function reset() { state.history = [[]]; state.at = 0; state.picked = -1; state.draft = state.drag = null; }

  function syncButtons() {
    const root = state.root;
    if (!root) return;
    root.querySelectorAll("[data-sk-tool]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.skTool === state.tool)));
    root.querySelectorAll("[data-sk-color]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.skColor === state.color)));
    root.querySelectorAll("[data-sk-size]").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.skSize === state.size)));
    root.querySelector('[data-sk="undo"]').disabled = state.at === 0;
    root.querySelector('[data-sk="redo"]').disabled = state.at >= state.history.length - 1;
    root.querySelector('[data-sk="clear"]').disabled = !state.ops.length;
    root.querySelector('[data-sk="attach"]').disabled = !state.ops.length;
    state.canvas.style.cursor = state.tool === "move" ? "move" : state.tool === "text" ? "text" : "crosshair";
  }

  function point(event) {
    const box = state.canvas.getBoundingClientRect();
    return [(event.clientX - box.left) * (W / box.width), (event.clientY - box.top) * (H / box.height)];
  }

  function placeImage(image) {
    const scale = Math.min(1, (W * 0.7) / image.naturalWidth, (H * 0.7) / image.naturalHeight);
    const w = image.naturalWidth * scale, h = image.naturalHeight * scale;
    commit({ kind: "image", image, x: (W - w) / 2, y: (H - h) / 2, w, h });
    state.tool = "move";
    state.picked = state.ops.length - 1;
    redraw();
  }

  function loadImage(file) {
    if (!file || !String(file.type).startsWith("image/")) return;
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => { URL.revokeObjectURL(url); placeImage(image); };
    image.onerror = () => { URL.revokeObjectURL(url); toast(t("Не удалось прочитать картинку", "Could not read the image"), "err"); };
    image.src = url;
  }

  function askText(x, y) {
    const box = state.canvas.getBoundingClientRect();
    const scale = box.width / W;
    const font = Math.max(18, state.size * 4);
    const input = document.createElement("textarea");
    input.className = "sketch-text";
    input.rows = 1;
    input.style.left = `${state.canvas.offsetLeft + x * scale}px`;
    input.style.top = `${state.canvas.offsetTop + y * scale}px`;
    input.style.font = `600 ${font * scale}px "Segoe UI", system-ui, sans-serif`;
    input.style.color = state.color;
    input.placeholder = t("Текст…", "Text…");
    state.canvas.parentNode.appendChild(input);
    input.focus();
    let done = false;
    const finish = (keep) => {
      if (done) return;
      done = true;
      const value = input.value.trim();
      input.remove();
      if (keep && value) commit({ kind: "text", text: value, x, y, font, color: state.color });
    };
    input.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); finish(true); }
      else if (event.key === "Escape") { event.preventDefault(); finish(false); }
    });
    input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = `${input.scrollHeight}px`; });
    input.addEventListener("blur", () => finish(true));
  }

  /* Текст ставится по завершённому щелчку, а не по нажатию: иначе браузер, заканчивая
     обработку нажатия, сам забирал бы фокус у только что появившегося поля. */
  function onClick(event) {
    if (state.tool !== "text" || event.button !== 0) return;
    const [x, y] = point(event);
    askText(x, y);
  }

  /* ------------------------------------------------------------ мышь */
  function onDown(event) {
    if (event.button !== 0) return;
    const [x, y] = point(event);
    const tool = state.tool;
    if (tool === "text") return;                 // поле появляется по щелчку: см. onClick
    state.canvas.setPointerCapture(event.pointerId);
    if (tool === "move") {
      const picked = state.ops[state.picked];
      const corner = picked && picked.kind !== "stroke" && (() => { const b = bounds(picked); return Math.abs(x - b[2]) < 16 && Math.abs(y - b[3]) < 16; })();
      if (!corner) {
        state.picked = -1;
        for (let i = state.ops.length - 1; i >= 0; i--) {
          const [x0, y0, x1, y1] = bounds(state.ops[i]);
          if (state.ops[i].tool !== "eraser" && x >= x0 && x <= x1 && y >= y0 && y <= y1) { state.picked = i; break; }
        }
      }
      // Двигается копия: исходный предмет остаётся в прежнем состоянии истории.
      const source = state.ops[state.picked];
      state.drag = source ? { x, y, resize: !!corner, moved: false,
        op: { ...source, points: source.points ? source.points.map((p) => p.slice()) : undefined } } : null;
      redraw();
      return;
    }
    if (tool === "pen" || tool === "marker" || tool === "eraser") {
      state.draft = { kind: "stroke", tool, color: state.color, size: tool === "eraser" ? state.size * 2.2 : state.size, points: [[x, y]] };
    } else {
      state.draft = { kind: "shape", shape: tool, color: state.color, size: state.size, x0: x, y0: y, x1: x, y1: y };
    }
    redraw();
  }

  function onMove(event) {
    const [x, y] = point(event);
    if (state.tool === "move" && state.drag) {
      const op = state.drag.op, dx = x - state.drag.x, dy = y - state.drag.y;
      if (dx || dy) state.drag.moved = true;
      if (state.drag.resize) {
        if (op.kind === "image") { const ratio = op.h / op.w; op.w = Math.max(24, op.w + dx); op.h = op.w * ratio; }
        else if (op.kind === "shape") { op.x1 += dx; op.y1 += dy; }
        else if (op.kind === "text") op.font = Math.max(12, op.font + dy * 0.5);
      } else shift(op, dx, dy);
      state.drag.x = x; state.drag.y = y;
      redraw();
      return;
    }
    if (!state.draft) return;
    if (state.draft.kind === "stroke") {
      const last = state.draft.points[state.draft.points.length - 1];
      if (Math.hypot(x - last[0], y - last[1]) > 1.5) state.draft.points.push([x, y]);
    } else {
      state.draft.x1 = x; state.draft.y1 = y;
      if (event.shiftKey && state.draft.shape !== "line" && state.draft.shape !== "arrow") {
        const side = Math.max(Math.abs(x - state.draft.x0), Math.abs(y - state.draft.y0));   // Shift — квадрат и круг
        state.draft.x1 = state.draft.x0 + Math.sign(x - state.draft.x0 || 1) * side;
        state.draft.y1 = state.draft.y0 + Math.sign(y - state.draft.y0 || 1) * side;
      }
    }
    redraw();
  }

  function onUp() {
    if (state.tool === "move") {
      const drag = state.drag;
      state.drag = null;
      if (drag && drag.moved) apply(state.ops.map((op, index) => (index === state.picked ? drag.op : op)));
      return;
    }
    const draft = state.draft;
    if (!draft) return;
    const tiny = draft.kind === "shape" && Math.hypot(draft.x1 - draft.x0, draft.y1 - draft.y0) < 4;
    if (tiny) { state.draft = null; redraw(); } else commit(draft);
  }

  /* ------------------------------------------------------------ окно */
  function build() {
    const root = document.createElement("div");
    root.className = "sketch";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", t("Нарисовать", "Sketch"));
    root.innerHTML = `<div class="sketch-card">
      <div class="sketch-bar">
        <div class="sketch-group">${TOOLS.map(([id, label, key]) => `<button type="button" class="sketch-btn" data-sk-tool="${id}" aria-pressed="false" title="${esc(label())} (${key})" aria-label="${esc(label())}">${svg(id)}</button>`).join("")}</div>
        <div class="sketch-group">${COLORS.map((color) => `<button type="button" class="sketch-color" data-sk-color="${color}" aria-pressed="false" style="--c:${color}" aria-label="${color}"></button>`).join("")}
          <label class="sketch-color sketch-custom" title="${esc(t("Свой цвет", "Custom colour"))}"><input type="color" value="#ff5a7a" aria-label="${esc(t("Свой цвет", "Custom colour"))}"></label></div>
        <div class="sketch-group">${SIZES.map((size, i) => `<button type="button" class="sketch-size" data-sk-size="${size}" aria-pressed="false" title="${esc(t("Толщина", "Thickness"))} ${i + 1}"><i style="--s:${4 + i * 4}px"></i></button>`).join("")}</div>
        <div class="sketch-group"><button type="button" class="sketch-btn" data-sk="undo" title="${esc(t("Отменить", "Undo"))} (Ctrl+Z)">${svg("undo")}</button>
          <button type="button" class="sketch-btn" data-sk="redo" title="${esc(t("Вернуть", "Redo"))} (Ctrl+Y)">${svg("redo")}</button>
          <button type="button" class="sketch-btn" data-sk="clear" title="${esc(t("Очистить", "Clear"))}">${svg("clear")}</button></div>
        <span class="sketch-grow"></span>
        <button type="button" class="sketch-text-btn" data-sk="close">${esc(t("Отмена", "Cancel"))}</button>
        <button type="button" class="sketch-text-btn primary" data-sk="attach">${esc(t("Прикрепить", "Attach"))}</button>
      </div>
      <div class="sketch-stage"><canvas width="${W}" height="${H}"></canvas></div>
      <input type="file" accept="image/*" hidden>
    </div>`;
    const canvas = root.querySelector("canvas");
    Object.assign(state, { root, canvas, ctx: canvas.getContext("2d") });
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointercancel", onUp);
    canvas.addEventListener("click", onClick);
    const picker = root.querySelector('input[type="file"]');
    picker.addEventListener("change", () => { loadImage(picker.files[0]); picker.value = ""; });
    root.querySelector(".sketch-custom input").addEventListener("input", (event) => { state.color = event.target.value; syncButtons(); });
    root.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) { if (event.target === root) close(); return; }
      if (button.dataset.skTool) {
        if (button.dataset.skTool === "image") { picker.click(); return; }
        state.tool = button.dataset.skTool; state.picked = -1; redraw();
      } else if (button.dataset.skColor) { state.color = button.dataset.skColor; syncButtons(); }
      else if (button.dataset.skSize) { state.size = +button.dataset.skSize; syncButtons(); }
      else if (button.dataset.sk === "undo") undo();
      else if (button.dataset.sk === "redo") redo();
      else if (button.dataset.sk === "clear") clear();
      else if (button.dataset.sk === "close") close();
      else if (button.dataset.sk === "attach") attach();
    });
    // Картинку можно бросить на холст или вставить из буфера.
    root.addEventListener("dragover", (event) => event.preventDefault());
    root.addEventListener("drop", (event) => { event.preventDefault(); event.stopPropagation(); loadImage(event.dataTransfer?.files?.[0]); });
    return root;
  }

  function onKey(event) {
    if (!state.root || event.target.closest?.(".sketch-text")) return;
    const key = event.key.toLowerCase();
    event.stopPropagation();
    if (event.key === "Escape") { event.preventDefault(); close(); return; }
    if (event.ctrlKey || event.metaKey) {
      if (event.code === "KeyZ") { event.preventDefault(); if (event.shiftKey) redo(); else undo(); }
      else if (event.code === "KeyY") { event.preventDefault(); redo(); }
      else if (key === "enter") { event.preventDefault(); attach(); }
      return;
    }
    if ((event.key === "Delete" || event.key === "Backspace") && state.tool === "move" && state.picked >= 0) {
      event.preventDefault();
      const gone = state.picked;
      state.picked = -1;
      apply(state.ops.filter((_, index) => index !== gone));
      return;
    }
    // Буквы-ускорители работают и в русской раскладке: сверяется физическая клавиша.
    const tool = TOOLS.find(([, , letter]) => event.code === `Key${letter}`);
    if (tool) { event.preventDefault(); state.root.querySelector(`[data-sk-tool="${tool[0]}"]`).click(); }
  }

  function onPaste(event) {
    if (!state.root) return;
    const file = [...(event.clipboardData?.files || [])].find((item) => String(item.type).startsWith("image/"));
    if (file) { event.preventDefault(); event.stopPropagation(); loadImage(file); }
  }

  function open() {
    if (state.root) return;
    document.body.appendChild(build());
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("paste", onPaste, true);
    redraw();
    state.root.querySelector('[data-sk-tool="pen"]').focus();
  }

  function close() {
    if (!state.root) return;
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("paste", onPaste, true);
    state.root.remove();
    Object.assign(state, { root: null, canvas: null, ctx: null, draft: null, drag: null, picked: -1 });
    $("#input")?.focus();
  }

  /** PNG рисунка на белом фоне: прозрачный холст в чате и у модели выглядел бы чёрным.
      Набросок со вставленной фотографией в PNG легко превышает лимит вложения (5 МБ) —
      тогда тот же рисунок уходит JPEG'ом. */
  async function toBlob() {
    const out = document.createElement("canvas");
    out.width = W; out.height = H;
    const layer = document.createElement("canvas");
    layer.width = W; layer.height = H;
    const layerCtx = layer.getContext("2d");
    for (const op of state.ops) drawOp(layerCtx, op);
    const ctx = out.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, W, H);
    ctx.drawImage(layer, 0, 0);
    const png = await new Promise((resolve) => out.toBlob(resolve, "image/png"));
    if (!png || png.size <= MAX_PNG) return png;
    return new Promise((resolve) => out.toBlob(resolve, "image/jpeg", 0.9));
  }

  async function attach() {
    if (!state.ops.length) return;
    const blob = await toBlob();
    if (!blob) return toast(t("Не удалось сохранить рисунок", "Could not save the sketch"), "err");
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const ext = blob.type === "image/jpeg" ? "jpg" : "png";
    // Не прикрепилось (уже 10 вложений, общий лимит) — addAttachmentFiles показал причину,
    // а рисунок остаётся в окне: раньше он стирался безвозвратно.
    if (!(await addAttachmentFiles([new File([blob], `sketch-${stamp}.${ext}`, { type: blob.type || "image/png" })]))) return;
    reset();
    close();
  }

  return { open, close, get active() { return !!state.root; }, get ops() { return state.ops; }, toBlob };
})();
