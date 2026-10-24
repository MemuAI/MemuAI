/* MemuMusic · fx.js — живой фон из цветов обложки, визуализатор, 3D-наклон
 * обложки и 3D-карусель. Один общий rAF-цикл: работает, только пока есть
 * чему двигаться; стоп при document.hidden, «облегчённых эффектах» и reduced-motion. */
const Fx = (() => {
  const reduce = window.matchMedia ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: false, addEventListener() {} };
  const tasks = new Set();
  let raf = 0, idleUntil = 0;
  const calm = () => document.documentElement.hasAttribute("data-calm");
  function loop(ts) {
    raf = 0;
    if (document.hidden) return;
    tasks.forEach((task) => { try { task(ts); } catch (e) { console.error(e); tasks.delete(task); } });
    if (tasks.size) raf = requestAnimationFrame(loop);
  }
  function add(task) { tasks.add(task); if (!raf && !document.hidden) raf = requestAnimationFrame(loop); }
  function remove(task) { tasks.delete(task); }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && tasks.size && !raf) raf = requestAnimationFrame(loop); });
  /* Неактивность: через 2 минуты без ввода и без музыки живой фон замирает. */
  ["pointermove", "keydown", "wheel", "pointerdown"].forEach((type) => addEventListener(type, () => { idleUntil = performance.now() + 120000; }, { passive: true }));
  idleUntil = performance.now() + 120000;
  const idle = () => performance.now() > idleUntil && !Player.st.playing;

  /* ------------------------------ живой фон (mesh/flow) ------------------------ */
  function mix(a, b, k) { return a.map((v, i) => v + (b[i] - v) * k); }
  function Ambient(canvas, { w = 64, h = 40, strength = 1 } = {}) {
    const g = canvas.getContext("2d", { alpha: false });
    canvas.width = w; canvas.height = h;
    let colors = [[124, 92, 255], [255, 92, 138], [32, 199, 181]], target = colors, base = [8, 8, 11];
    let t0 = performance.now(), last = 0, running = false;
    function draw(ts) {
      if (ts - last < 40) return;   // ~25 к/с: фон плавный и почти бесплатный
      last = ts;
      colors = colors.map((c, i) => mix(c, target[i], .06));
      const energy = strength * (Player.st.playing ? 1 : .35) * (1 + Fx.bass * .8);
      const t = (ts - t0) / 1000 * (Player.st.playing ? .22 : .06);
      g.globalCompositeOperation = "source-over";
      g.fillStyle = `rgb(${base.join(",")})`; g.fillRect(0, 0, w, h);
      g.globalCompositeOperation = document.documentElement.dataset.mmScheme === "light" ? "source-over" : "lighter";
      const blobs = [[.25 + .18 * Math.sin(t), .3 + .2 * Math.cos(t * .8), .75, 0], [.78 + .15 * Math.cos(t * .9), .25 + .2 * Math.sin(t * 1.1), .7, 1],
        [.55 + .25 * Math.sin(t * .6 + 2), .85 + .1 * Math.cos(t * .7), .8, 2], [.1 + .1 * Math.cos(t * 1.3), .9 + .08 * Math.sin(t), .5, 0]];
      for (const [x, y, r, ci] of blobs) {
        const c = colors[ci].map(Math.round), R = r * w;
        const grad = g.createRadialGradient(x * w, y * h, 0, x * w, y * h, R);
        grad.addColorStop(0, `rgba(${c.join(",")},${(.55 * energy).toFixed(3)})`); grad.addColorStop(1, `rgba(${c.join(",")},0)`);
        g.fillStyle = grad; g.fillRect(0, 0, w, h);
      }
      if (!running || calm() || reduce.matches || idle()) remove(draw);
    }
    return {
      setColors(hexes, bg) {
        target = hexes.map((x) => hexToRgb(x) || [128, 128, 128]);
        if (bg) base = hexToRgb(bg) || base;
        if (calm() || reduce.matches) colors = target;
        this.kick();
      },
      setBase(hex) { base = hexToRgb(hex) || base; this.kick(); },
      start() { running = true; this.kick(); },
      stop() { running = false; },
      kick() { last = 0; add(draw); },
    };
  }

  /* ---------------------------------- визуализатор -----------------------------
   * Светящееся «живое кольцо» вокруг обложки: три слоя-контура, радиус каждого колышется по своей
   * полосе спектра (зеркально, чтобы фигура была симметричной), плюс мягкий ореол от баса.
   * Без звука (пауза, нет WebAudio) кольцо медленно «дышит». Холст — только область сцены, ≤1.5× DPR,
   * ~60 к/с под музыку и ~20 к/с в «дыхании»; стоп — когда окно скрыто, «облегчённые эффекты» или простой. */
  function Visualizer(canvas) {
    const g = canvas.getContext("2d");
    const POINTS = 96;
    let data = null, on = false, colors = ["#fff", "#fff"], peak = 0, last = 0, cx = 0, cy = 0, R = 100, dpr = 1, mode = "ring";
    const smooth = [new Float32Array(POINTS), new Float32Array(POINTS), new Float32Array(POINTS)];
    function size() {
      const r = canvas.getBoundingClientRect();
      dpr = Math.min(1.5, devicePixelRatio || 1);
      canvas.width = Math.max(1, Math.round(r.width * dpr)); canvas.height = Math.max(1, Math.round(r.height * dpr));
      const art = document.getElementById("np-art"), a = art && art.getBoundingClientRect();
      if (a && a.width) {
        cx = (a.left + a.width / 2 - r.left) * dpr; cy = (a.top + a.height / 2 - r.top) * dpr;
        R = a.width / 2 * dpr;
      } else { cx = canvas.width / 2; cy = canvas.height / 2; R = Math.min(canvas.width, canvas.height) * .3; }
    }
    function rgba(hex, a) { const c = hexToRgb(hex) || [255, 255, 255]; return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }
    function ring(layer, radius, amp, color, width, alpha, ts, spin) {
      const pts = smooth[layer], step = Math.PI * 2 / POINTS;
      g.beginPath();
      const xy = (i) => {
        const k = pts[(i + POINTS) % POINTS];
        const wob = Math.sin(i * step * 3 + ts * .0007 * spin + layer) * .006 + Math.sin(i * step * 5 - ts * .0005) * .004;
        // суперэллипс — контур повторяет скруглённый квадрат обложки и «дышит» наружу
        const ang = i * step, c = Math.cos(ang), sn = Math.sin(ang);
        const se = Math.pow(Math.pow(Math.abs(c), 5) + Math.pow(Math.abs(sn), 5), -1 / 5);
        const rr = radius * se * (1 + wob) + k * amp;
        return [cx + c * rr, cy + sn * rr];
      };
      // сглаженный замкнутый контур: квадратичные кривые через середины отрезков
      let [px, py] = xy(0), [nx, ny] = xy(1);
      g.moveTo((px + nx) / 2, (py + ny) / 2);
      for (let i = 1; i <= POINTS; i++) {
        [px, py] = xy(i); [nx, ny] = xy(i + 1);
        g.quadraticCurveTo(px, py, (px + nx) / 2, (py + ny) / 2);
      }
      g.closePath();
      g.strokeStyle = rgba(color, alpha); g.lineWidth = width * dpr;
      g.shadowColor = rgba(color, Math.min(1, alpha + .2)); g.shadowBlur = 22 * dpr;
      g.stroke();
    }
    function draw(ts) {
      const an = Player.analyser(), live = !!(an && Player.st.playing);
      if (!live && ts - last < 50) return;   // «дыхание» — 20 к/с хватает
      last = ts;
      const W = canvas.width, H = canvas.height;
      g.clearRect(0, 0, W, H);
      if (!on) return;
      let bass = 0;
      if (live) {
        if (!data || data.length !== an.frequencyBinCount) data = new Uint8Array(an.frequencyBinCount);
        an.getByteFrequencyData(data);
        for (let i = 2; i < 12; i++) bass += data[i];
        bass /= 10 * 255;
        const half = POINTS / 2, bands = [[1, .12], [.08, .35], [.25, .7]];
        bands.forEach(([from, to], layer) => {
          const lo = Math.max(1, Math.floor(from * data.length * .5)), hi = Math.max(lo + 4, Math.floor(to * data.length * .5));
          const pts = smooth[layer];
          for (let i = 0; i < POINTS; i++) {
            const m = i < half ? i : POINTS - 1 - i;               // зеркально
            const idx = lo + Math.floor(Math.pow(m / half, 1.3) * (hi - lo));
            const v = (data[idx] || 0) / 255;
            pts[i] += (v * v - pts[i]) * (v > pts[i] ? .5 : .12);  // быстрый подъём, мягкий спад
          }
          for (let pass = 0; pass < 2; pass++) {                    // сглаживание по соседям — плавная волна вместо «иголок»
            let prev = pts[POINTS - 1];
            for (let i = 0; i < POINTS; i++) { const cur = pts[i]; pts[i] = (prev + cur * 2 + pts[(i + 1) % POINTS]) / 4; prev = cur; }
          }
        });
      } else {
        const breath = (Math.sin(ts / 1400) + 1) / 2;
        smooth.forEach((pts, layer) => { for (let i = 0; i < POINTS; i++) pts[i] += ((.05 + breath * .06) * (1 - layer * .25) - pts[i]) * .08; });
      }
      peak = Math.max(bass, peak * .93); Fx.bass = bass;
      if (mode === "wave" || mode === "bars") { (mode === "wave" ? wave : bars)(ts, live); if (!live && idle()) remove(draw); return; }
      // ореол за обложкой
      const edge = Math.min(cx, cy, W - cx, H - cy);                // ореол гаснет до края холста — без «прямоугольника»
      const glowR = Math.max(R * 1.05, Math.min(R * (1.6 + peak * .5), edge));
      const grad = g.createRadialGradient(cx, cy, R * .7, cx, cy, glowR);
      grad.addColorStop(0, rgba(colors[0], .28 + peak * .35)); grad.addColorStop(.55, rgba(colors[1], .12 + peak * .12)); grad.addColorStop(1, rgba(colors[1], 0));
      g.shadowBlur = 0; g.fillStyle = grad; g.fillRect(0, 0, W, H);
      g.globalCompositeOperation = "lighter";
      const amp = R * (live ? .3 : .16), gap = 14 * dpr;
      ring(1, R + gap * 1.4, amp * .95, colors[0], 18, .18 + peak * .3, ts, 1);   // широкая мягкая аура
      ring(2, R + gap * 2.2, amp * .8, colors[1], 1.2, .34, ts, -1);
      ring(1, R + gap * 1.5, amp * .95, colors[0], 1.6, .5, ts, 1);
      ring(0, R + gap, amp * 1.1, "#ffffff", 2.2, .85, ts, .6);
      g.globalCompositeOperation = "source-over";
      const art = document.getElementById("np-art");
      if (art && !calm()) art.style.scale = String(1 + peak * .03);
      if (!live && idle()) { remove(draw); }
    }
    /* «Волна»: три светящиеся линии под обложкой (спектр зеркально), «Столбцы»: скруглённые столбики под обложкой. */
    function wave(ts, live) {
      const W = canvas.width, base = Math.min(canvas.height - 24 * dpr, cy + R + 34 * dpr), width = Math.min(W * .9, R * 2.6), x0 = cx - width / 2;
      g.globalCompositeOperation = "lighter";
      [[0, colors[0], 2.4, .9, 1], [1, colors[1], 1.6, .6, -1], [2, "#ffffff", 1.2, .5, .6]].forEach(([layer, color, lw, alpha, dir]) => {
        const pts = smooth[layer];
        g.beginPath();
        for (let i = 0; i <= 64; i++) {
          const k = i / 64, idx = Math.floor(k * (POINTS / 2 - 1));
          const env = Math.sin(Math.PI * k);
          const y = base - (pts[idx] * R * (live ? .55 : .9) + Math.sin(k * 9 + ts * .0016 * dir + layer) * (live ? 3 : 7) * dpr) * env * dir;
          i ? g.lineTo(x0 + k * width, y) : g.moveTo(x0, y);
        }
        g.strokeStyle = rgba(color, alpha); g.lineWidth = lw * dpr; g.shadowColor = rgba(color, .9); g.shadowBlur = 16 * dpr; g.stroke();
      });
      g.globalCompositeOperation = "source-over"; g.shadowBlur = 0;
    }
    function bars(ts, live) {
      const count = 40, width = Math.min(canvas.width * .9, R * 2.2), gap = width / count, bw = Math.max(2 * dpr, gap * .56);
      const base = Math.min(canvas.height - 12 * dpr, cy + R + 22 * dpr + R * .42), maxH = R * .42;
      const grad = g.createLinearGradient(0, base - maxH, 0, base);
      grad.addColorStop(0, rgba(colors[1], .95)); grad.addColorStop(1, rgba(colors[0], .55));
      g.fillStyle = grad; g.shadowColor = rgba(colors[0], .6); g.shadowBlur = 10 * dpr;
      const pts = smooth[0], pts2 = smooth[1];
      for (let i = 0; i < count; i++) {
        const k = i / (count - 1), m = Math.abs(k - .5) * 2, idx = Math.floor(m * (POINTS / 2 - 1));
        const v = Math.max(.04, Math.min(1, (pts[idx] * .6 + pts2[idx] * .8) * (live ? 1.6 : 1) + (live ? 0 : (Math.sin(ts / 900 + i * .45) + 1) * .1 + .06)));
        const h = Math.max(bw, v * maxH), x = cx - width / 2 + i * gap + (gap - bw) / 2;
        g.beginPath(); if (g.roundRect) g.roundRect(x, base - h, bw, h, bw / 2); else g.rect(x, base - h, bw, h); g.fill();
      }
      g.shadowBlur = 0;
    }
    return {
      setColors(c) { colors = c; },
      setMode(m) { mode = m === "wave" || m === "bars" ? m : "ring"; },
      start() { if (calm() || reduce.matches) return this.stop(); on = true; size(); add(draw); },
      stop() { on = false; remove(draw); g.clearRect(0, 0, canvas.width, canvas.height); Fx.bass = 0; const art = document.getElementById("np-art"); if (art) art.style.scale = ""; },
      resize: size,
    };
  }

  /* ---------------------------------- 3D-наклон -------------------------------- */
  function tilt(stage, target) {
    let px = 0, py = 0, pending = false;
    stage.addEventListener("pointermove", (e) => {
      if (calm() || reduce.matches) return;
      const r = stage.getBoundingClientRect();
      px = (e.clientX - r.left) / r.width - .5; py = (e.clientY - r.top) / r.height - .5;
      if (!pending) { pending = true; requestAnimationFrame(apply); }
    }, { passive: true });
    stage.addEventListener("pointerleave", () => { px = 0; py = 0; requestAnimationFrame(apply); }, { passive: true });
    function apply() {
      pending = false;
      target.style.setProperty("--ry", (px * 18).toFixed(2) + "deg");
      target.style.setProperty("--rx", (-py * 14).toFixed(2) + "deg");
      target.style.setProperty("--gx", (50 + px * 90).toFixed(1) + "%");
      target.style.setProperty("--gy", (40 + py * 90).toFixed(1) + "%");
    }
  }

  /* ---------------------------------- 3D-карусель ------------------------------ */
  function Carousel(root, items, { onPlay, onFocus } = {}) {
    const stage = root.querySelector(".mm-flow-stage");
    const nodes = [...stage.children];
    let pos = 0, target = 0, vel = 0, dragX = null, dragPos = 0, moved = 0;
    const n = nodes.length;
    function layout() {
      const spacing = Math.min(170, root.clientWidth / 6);
      nodes.forEach((node, i) => {
        const d = i - pos, a = Math.abs(d);
        if (a > 5.5) { node.style.visibility = "hidden"; return; }
        node.style.visibility = "";
        const rot = Math.max(-1, Math.min(1, d)) * -52;
        const x = d * spacing * (a < 1 ? 1.15 : .72 + .43 / a);
        node.style.transform = `translateX(${x.toFixed(1)}px) translateZ(${(-a * 140).toFixed(1)}px) rotateY(${rot.toFixed(1)}deg)`;
        node.style.zIndex = String(100 - Math.round(a * 10));
        node.style.opacity = String(Math.max(0, 1 - Math.max(0, a - 3.5) / 2));
        node.style.setProperty("--glare", String(Math.max(0, 1 - a) * .9));
      });
    }
    let autoTimer = 0;
    function scheduleAuto() {
      clearTimeout(autoTimer);
      if (calm() || reduce.matches) return;
      autoTimer = setTimeout(() => {
        if (!root.isConnected) return;
        if (document.hidden || dragX !== null || root.matches(":hover") || idle()) { scheduleAuto(); return; }
        target = (Math.round(target) + 1) % n; add(step);
      }, 4800);
    }
    function step() {
      if (!root.isConnected) { remove(step); clearTimeout(autoTimer); return; }
      if (dragX === null) {
        const f = (target - pos) * .14; vel = vel * .55 + f; pos += vel;
        if (Math.abs(target - pos) < .002 && Math.abs(vel) < .002) { pos = target; vel = 0; layout(); caption(); remove(step); scheduleAuto(); return; }
      }
      layout(); caption();
    }
    let shown = -1;
    function caption() {
      const i = ((Math.round(pos) % n) + n) % n;
      if (i === shown) return; shown = i;
      if (onFocus) onFocus(items[i], i);
    }
    function go(i) { target = Math.max(0, Math.min(n - 1, i)); add(step); }
    root.addEventListener("pointerdown", (e) => { dragX = e.clientX; dragPos = pos; moved = 0; root.setPointerCapture(e.pointerId); add(step); });
    root.addEventListener("pointermove", (e) => {
      if (dragX === null) return;
      moved = Math.max(moved, Math.abs(e.clientX - dragX));
      pos = Math.max(-.4, Math.min(n - .6, dragPos - (e.clientX - dragX) / 160));
    });
    const end = (e) => {
      if (dragX === null) return;
      dragX = null; target = Math.max(0, Math.min(n - 1, Math.round(pos))); add(step);
      if (moved < 6) {
        const hit = e.target.closest && e.target.closest(".mm-flow-item");
        if (hit) { const i = nodes.indexOf(hit); if (i === Math.round(pos) && onPlay) onPlay(items[i], i); else go(i); }
      }
    };
    root.addEventListener("pointerup", end); root.addEventListener("pointercancel", end);
    root.addEventListener("wheel", (e) => {
      if (Math.abs(e.deltaX) < Math.abs(e.deltaY) * 1.2 && !e.shiftKey) return;
      e.preventDefault(); go(Math.round(target) + Math.sign(e.deltaX || e.deltaY));
    }, { passive: false });
    root.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); go(Math.round(target) - 1); }
      if (e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); go(Math.round(target) + 1); }
      if (e.key === "Enter" && onPlay) onPlay(items[Math.round(target)], Math.round(target));
    });
    target = pos = Math.min(2, Math.floor((n - 1) / 2));   // мало альбомов — по центру, а не у края
    layout(); caption(); scheduleAuto();
    return { go, prev: () => go(Math.round(target) - 1), next: () => go(Math.round(target) + 1), relayout: layout };
  }

  return { add, remove, Ambient, Visualizer, tilt, Carousel, bass: 0, reduce };
})();
