# -*- coding: utf-8 -*-
"""MemuMusic design auditor (Round 38).

Runs the real module document in Chromium (demo mode, no network — see round37_music_harness.py), switches
themes × designs × light/dark × window sizes, screenshots every screen and looks for design bugs automatically:

* ``offscreen``  — a visible interactive element sticks out of the viewport;
* ``clipped``    — text is cut (scrollWidth > clientWidth) without an ellipsis or a line clamp;
* ``covered``    — an interactive element is covered by another one (hit test at its centre);
* ``contrast``   — text contrast below WCAG AA (4.5, or 3 for large text) on a solid background;
* ``image``      — a picture is bigger than its box, or a cover is huge (the «covers take the whole screen» bug);
* ``hscroll``    — horizontal scrolling of the page or the main column;
* ``shift``      — layout shift (PerformanceObserver «layout-shift») while switching sections;
* ``artifact-*`` — hidden half-transparent artifacts: degenerate scale(0) that still paints, 1–4 % «ghosts», thin rounded bars squashed by scaleX (round 39);
* ``focus-*``    — text fields / lists: no focus ring, or two rings (the field's own rectangle inside its rounded container) (round 39).

Real covers of different proportions (square, 16:9, tall, tiny, broken) are injected as data: URLs.

    python3 tests/round38_music_design_audit.py OUT_DIR [--quick]

Writes OUT_DIR/report.json and screenshots; exit code 1 when there are findings.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from round37_music_harness import frame_of, open_page, wait  # noqa: E402


def cover_images() -> dict:
    """Real raster covers of awkward proportions as data: URLs (the module CSP allows data:)."""
    from PIL import Image, ImageDraw
    out = {}
    specs = {"square": (800, 800), "wide": (1280, 720), "tall": (600, 1200), "tiny": (64, 64)}
    for i, (name, (w, h)) in enumerate(specs.items()):
        img = Image.new("RGB", (w, h), (30 + i * 40, 60, 140 - i * 20))
        d = ImageDraw.Draw(img)
        for k in range(0, max(w, h), max(8, max(w, h) // 12)):
            d.line([(k, 0), (0, k)], fill=(250, 180 - i * 30, 90 + i * 30), width=max(2, w // 60))
        d.rectangle([w * .3, h * .3, w * .7, h * .7], outline=(255, 255, 255), width=max(2, w // 80))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        out[name] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    out["broken"] = "data:image/png;base64,AAAAbroken"
    return out


AUDIT_JS = r"""
(opts) => {
  const out = [];
  const W = innerWidth, H = innerHeight;
  const vis = (el) => { const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return null;
    const cs = getComputedStyle(el); if (cs.visibility === "hidden" || cs.display === "none" || Number(cs.opacity) === 0) return null;
    for (let p = el; p; p = p.parentElement) { const c = getComputedStyle(p); if (Number(c.opacity) < 0.05 || c.visibility === "hidden") return null; if (p.hidden) return null; }
    return r; };
  const name = (el) => (el.id ? "#" + el.id : el.tagName.toLowerCase()) + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "")
    + (el.textContent ? " «" + el.textContent.trim().slice(0, 28) + "»" : "");
  const scrollParentX = (el) => { for (let p = el.parentElement; p; p = p.parentElement) { const c = getComputedStyle(p); if (/(auto|scroll)/.test(c.overflowX) && p.scrollWidth > p.clientWidth + 1) return p; } return null; };
  const inside = (el, sel) => !!el.closest(sel);
  /* centre clipped away by an overflow:hidden ancestor = not visible there (masked lyrics, shelves) */
  const clippedAway = (el, r) => { const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { const c = getComputedStyle(p);
      if (/(hidden|clip|auto|scroll)/.test(c.overflow + c.overflowX + c.overflowY)) { const q = p.getBoundingClientRect(); if (cx < q.left || cx > q.right || cy < q.top || cy > q.bottom) return true; } }
    return false; };
  const layer = opts.layer ? document.querySelector(opts.layer) : document;
  const interactive = [...layer.querySelectorAll("button, a[href], input, select, textarea, [role=button], [role=tab], [role=radio]")].filter((el) => !el.closest("[hidden]"));
  const skipFlow = ".mm-flow, .mm-sprite";
  // 1. offscreen
  for (const el of interactive) {
    const r = vis(el); if (!r || inside(el, skipFlow)) continue;
    if (scrollParentX(el)) continue;
    if (r.right > W + 1 || r.left < -1) out.push({ kind: "offscreen", el: name(el), rect: [r.left, r.right] });
  }
  // 2. clipped text
  for (const el of layer.querySelectorAll("b, span, p, h1, h2, h3, h4, button, em, time, label, div")) {
    if (inside(el, skipFlow) || el.closest("[hidden]")) continue;
    const direct = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!direct) continue;
    const r = vis(el); if (!r) continue;
    const cs = getComputedStyle(el);
    if (cs.textOverflow === "ellipsis" || cs.webkitLineClamp !== "none" && cs.webkitLineClamp) continue;
    if (el.scrollWidth > el.clientWidth + 1 && /(hidden|clip)/.test(cs.overflowX)) out.push({ kind: "clipped", el: name(el), sw: el.scrollWidth, cw: el.clientWidth });
    if (el.scrollHeight > el.clientHeight + 2 && /(hidden|clip)/.test(cs.overflowY) && cs.whiteSpace !== "nowrap") out.push({ kind: "clipped-v", el: name(el), sh: el.scrollHeight, ch: el.clientHeight });
  }
  // 3. covered interactive elements (hit test at the centre)
  for (const el of interactive) {
    const r = vis(el); if (!r || inside(el, skipFlow) || clippedAway(el, r)) continue;
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cx < 0 || cy < 0 || cx > W || cy > H) continue;
    const sp = scrollParentX(el); if (sp) { const pr = sp.getBoundingClientRect(); if (cx < pr.left || cx > pr.right) continue; }
    const ms = el.closest(".mm-main, .mm-drawer-body, .mm-np-queue, .mm-side-list"); if (ms) { const mr = ms.getBoundingClientRect(); if (cy < mr.top + 2 || cy > mr.bottom - 2) continue; }
    const hit = document.elementFromPoint(cx, cy);
    if (!hit || el.contains(hit) || hit.contains(el)) continue;
    if (hit.closest("label") && hit.closest("label").contains(el)) continue;
    if (el.tagName === "INPUT" && (el.type === "checkbox" || el.type === "color" || el.type === "range")) continue;
    if (getComputedStyle(el).pointerEvents === "none") continue;
    out.push({ kind: "covered", el: name(el), by: name(hit) });
  }
  // 4. contrast on solid backgrounds
  const parse = (c) => { const m = /rgba?\(([^)]+)\)/.exec(c || ""); if (!m) return null; const v = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1]; };
  const blend = (top, bot) => { const a = top[3]; return [top[0] * a + bot[0] * (1 - a), top[1] * a + bot[1] * (1 - a), top[2] * a + bot[2] * (1 - a), 1]; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); }; return .2126 * f(c[0]) + .7152 * f(c[1]) + .0722 * f(c[2]); };
  const rootBg = parse(getComputedStyle(document.body).backgroundColor) || [0, 0, 0, 1];
  const bgOf = (el) => { const stack = [];
    for (let p = el; p; p = p.parentElement) { const cs = getComputedStyle(p);
      if (p.classList && (p.classList.contains("mm-np") || p.classList.contains("mm-mix") || p.classList.contains("mm-lyrics") || p.classList.contains("mm-np-tabs"))) return null;   // over canvas / pictures / moving pill
      if (cs.backgroundImage && cs.backgroundImage !== "none") return null;            // gradient / picture: unknown
      if (/blur|drop-shadow/.test(cs.filter || "")) return null;
      const c = parse(cs.backgroundColor); if (c && c[3] > 0) { stack.push(c); if (c[3] >= .99) break; }
    }
    let col = rootBg; for (let i = stack.length - 1; i >= 0; i--) col = blend(stack[i], col); return col; };
  const seen = new Set();
  for (const el of layer.querySelectorAll("b, span, p, h1, h2, h3, h4, button, em, time, label, a, div, small, strong")) {
    if (inside(el, skipFlow + ", .mm-tp, .mm-dp, .mm-npp, .mm-art, .mm-tile-art, svg") || el.closest("[hidden]") || el.closest("[disabled]")) continue;
    const direct = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!direct) continue;
    const r = vis(el); if (!r || r.bottom < 0 || r.top > H) continue;
    const cs = getComputedStyle(el);
    if (cs.webkitTextFillColor && cs.webkitTextFillColor !== cs.color && /rgba?\(0, 0, 0, 0\)/.test(cs.webkitTextFillColor)) continue;   // gradient text
    if (cs.color === "rgba(0, 0, 0, 0)") continue;
    if (/^[\s·•|/—–-]+$/.test(el.textContent) || el.closest("[aria-hidden=true]")) continue;   // decorative separators (WCAG: pure decoration)
    const bg = bgOf(el); if (!bg) continue;
    let op = 1; for (let p = el; p; p = p.parentElement) op *= Number(getComputedStyle(p).opacity);
    const fg0 = parse(cs.color); if (!fg0) continue;
    const fg = blend([fg0[0], fg0[1], fg0[2], fg0[3] * op], bg);
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + .05) / (Math.min(L1, L2) + .05);
    const size = parseFloat(cs.fontSize), bold = Number(cs.fontWeight) >= 600;
    const need = size >= 24 || (size >= 18.6 && bold) ? 3 : 4.5;
    if (ratio < need - .05) { const key = name(el); if (!seen.has(key)) { seen.add(key); out.push({ kind: "contrast", el: key, ratio: Math.round(ratio * 100) / 100, need }); } }
  }
  // 5. images
  for (const img of layer.querySelectorAll("img")) {
    const r = vis(img); if (!r || inside(img, skipFlow)) continue;
    if (img.matches(".mm-coverbg, .mm-np-blur, .mm-np-backdrop, .mm-osheet-bg img")) continue;   // r41b-online: + blurred backdrop of the track sheet
    const p = img.parentElement.getBoundingClientRect();
    const pcs = getComputedStyle(img.parentElement);
    if (!/(hidden|clip)/.test(pcs.overflow) && (r.width > p.width + 1 || r.height > p.height + 1)) out.push({ kind: "image", el: name(img.parentElement), img: [Math.round(r.width), Math.round(r.height)], box: [Math.round(p.width), Math.round(p.height)] });
    const isNp = !!img.closest(".mm-np");
    if (!isNp && (r.width > Math.min(420, W * .45) || r.height > Math.min(420, H * .6))) out.push({ kind: "image-huge", el: name(img.parentElement), img: [Math.round(r.width), Math.round(r.height)] });
    if (Math.abs(r.width - r.height) > 2 && !img.closest(".mm-collage, .mm-orow, .mm-osim") && !isNp) out.push({ kind: "image-ratio", el: name(img.parentElement), img: [Math.round(r.width), Math.round(r.height)] });
  }
  // 6. horizontal scroll
  const se = document.scrollingElement; if (se.scrollWidth > W + 1) out.push({ kind: "hscroll", el: "document", sw: se.scrollWidth, w: W });
  const main = document.querySelector("#main"); if (main && main.scrollWidth > main.clientWidth + 1) out.push({ kind: "hscroll", el: "#main", sw: main.scrollWidth, cw: main.clientWidth });
  // 7. hidden half-transparent artifacts (round 39): things that should be invisible but can still leave pixels —
  //    a degenerate scale(0) with a shadow/background, a «ghost» at 1–4 % opacity, a thin rounded bar squashed by scaleX
  //    (not pixel-snapped: half-transparent rows above/below at 125/150 % and a flattened rounded end).
  const mat = (tf) => { const m = /matrix\(([^)]+)\)/.exec(tf || ""); return m ? m[1].split(",").map(Number) : null; };
  for (const el of layer.querySelectorAll("*")) {
    if (el.closest("[hidden], svg, .mm-flow, .mm-sprite")) continue;
    const cs = getComputedStyle(el); if (cs.display === "none" || cs.visibility === "hidden") continue;
    const r = el.getBoundingClientRect(); if (r.bottom < 0 || r.top > H) continue;
    const paints = (cs.boxShadow !== "none") || ((parse(cs.backgroundColor) || [0, 0, 0, 0])[3] > 0) || cs.backgroundImage !== "none";
    if (!paints) continue;
    const m = mat(cs.transform);
    if (m && (Math.abs(m[0]) < 0.02 || Math.abs(m[3]) < 0.02) && !el.getAnimations().length) out.push({ kind: "artifact-degenerate", el: name(el), transform: cs.transform });
    let op = 1; for (let p = el; p; p = p.parentElement) op *= Number(getComputedStyle(p).opacity);
    if (op > 0.002 && op < 0.04 && r.width > 2 && r.height > 2) out.push({ kind: "artifact-ghost", el: name(el), opacity: Math.round(op * 1000) / 1000 });
    const ownH = parseFloat(cs.height);
    if (m && ownH <= 6 && r.width > 2 && Math.abs(m[0] - 1) > 0.001 && Math.abs(m[3] - 1) < 0.001 && parseFloat(cs.borderTopRightRadius) > 0 && !el.getAnimations().length)
      out.push({ kind: "artifact-scalebar", el: name(el), transform: cs.transform });
  }
  return out;
}
"""

CLS_INSTALL = r"""() => { window.__cls = 0; window.__shifts = []; try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) { window.__cls += e.value;
  window.__shifts.push({ v: e.value, src: (e.sources || []).map((s) => s.node ? (s.node.id || s.node.className || s.node.nodeName) : "?").slice(0, 3) }); } }).observe({ type: "layout-shift", buffered: false }); } catch (e) { window.__cls = -1; } }"""


ALBUMS = {"square": "Northern Lights", "wide": "Live at Moscow Arena (YouTube)", "tall": "Вертикальные дни", "tiny": "Lo-Fi Tapes", "broken": "Без обложки"}


def inject_covers(frame, covers: dict, few: bool = False) -> None:
    """Real covers of awkward proportions; the demo library is reseeded so the sidebar, playlists and stats agree with it."""
    frame.evaluate("""([covers, albums, few]) => {
      const S = MemuMusic.S, names = Object.keys(covers);
      let list = S.tracks.slice();
      if (few) list = list.slice(0, 3);
      list.forEach((x, i) => { if (i < names.length * 3) { const k = names[i % names.length]; x.cover = covers[k]; x.album = albums[k]; x.accent = null; } });
      if (few) { list[0].title = "Не до слез (анимационный клип) — очень длинное название трека для проверки"; list[0].liked = true; }
      MemuMusic.dev.MockMusic.seed(list);
      return MemuMusic.refresh().then(() => MemuMusic.rerender());
    }""", [covers, ALBUMS, few])


def configure(frame, ui: dict) -> None:
    frame.evaluate("(ui) => { const T = MemuMusic.Theme; Object.keys(ui).forEach((k) => { T.ui[k] = ui[k]; }); T.set('mode', T.ui.mode); }", ui)


VIEWS = ["home", "library", "playlists", "download", "stats", "settings"]


def audit_screen(page, frame, out: Path, tag: str, findings: list, layer: str = "") -> None:
    page.wait_for_timeout(700)
    page.screenshot(path=str(out / f"{tag}.png"))
    for item in frame.evaluate(AUDIT_JS, {"layer": layer}):
        item["screen"] = tag
        findings.append(item)


INTEGRATION_STUB = """() => { try { Memu.integration = { list: () => Promise.resolve({ widgets: [
  { id: 'chat-mini', values: { enabled: true, eq: true } }, { id: 'eq-ring', values: { enabled: true, style: 'bars', color: 'cover' } }, { id: 'quick-card', values: { enabled: true } }] }),
  set: () => Promise.resolve({ ok: true }) }; } catch (e) {} }"""


def audit_extra(page, frame, out: Path, tag0: str, findings: list) -> None:
    """Round 38 feature screens: Ctrl+K search with online results, genres/folders, dialogs, selection bar, toast with Undo,
    drag & drop overlay, empty states, the Appearance and Widgets settings tabs."""
    go = lambda view, params="{}": frame.evaluate(f"() => MemuMusic.go('{view}', {params}, {{ instant: true }})")
    frame.evaluate("() => { MemuMusic.go('home', {}, { instant: true }); document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', code: 'KeyK', ctrlKey: true, bubbles: true })); }")
    page.wait_for_timeout(300)
    frame.evaluate("() => { const i = document.querySelector('#view input[type=search], #view .mm-input'); if (i) { i.value = 'glass'; i.dispatchEvent(new Event('input', { bubbles: true })); } }")
    page.wait_for_timeout(900)
    frame.evaluate("() => { const b = [...document.querySelectorAll('#view button')].find((x) => /онлайн|online/i.test(x.textContent)); if (b) b.click(); }")
    audit_screen(page, frame, out, f"{tag0}-search-ctrlk", findings)
    # r41b-online: the «Track info» sheet of an online result (source card, facts grid, formats, similar tracks)
    frame.evaluate("() => { const r = document.querySelectorAll('.mm-orow')[1]; if (r) r.click(); }")
    page.wait_for_timeout(900)
    if frame.evaluate("() => !!document.querySelector('.mm-osheet:not([hidden])')"):
        audit_screen(page, frame, out, f"{tag0}-online-sheet", findings, ".mm-osheet-panel")
        frame.evaluate("() => { const b = document.querySelector('.mm-osheet-body'); b.scrollTop = b.scrollHeight; }")
        audit_screen(page, frame, out, f"{tag0}-online-sheet-end", findings, ".mm-osheet-panel")
        frame.evaluate("() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
    go("search", "{ q: 'zzzzzz-nothing' }")
    audit_screen(page, frame, out, f"{tag0}-empty-search", findings)
    for tab in ("genres", "folders"):
        go("library")
        page.wait_for_timeout(300)
        frame.evaluate(f"() => {{ const b = document.querySelector('.mm-tabs [data-tab={tab}]'); if (b) b.click(); }}")
        audit_screen(page, frame, out, f"{tag0}-lib-{tab}", findings)
    frame.evaluate("() => { const g = document.querySelector('#view [data-genre], #view .mm-tile, #view .mm-folder'); if (g) g.click(); }")
    audit_screen(page, frame, out, f"{tag0}-lib-genre-open", findings)
    # selection bar (library, everything selected)
    go("library")
    page.wait_for_timeout(300)
    frame.evaluate("() => { const b = document.querySelector('.mm-tabs [data-tab=tracks]'); if (b) b.click(); }")
    page.wait_for_timeout(300)
    frame.evaluate("""() => { const rows = document.querySelectorAll('#view .mm-row');
      const hit = (r, mods) => { const b = r.querySelector('.al') || r; const q = b.getBoundingClientRect();
        b.dispatchEvent(new MouseEvent('click', Object.assign({ bubbles: true, clientX: q.left + 4, clientY: q.top + q.height / 2 }, mods))); };
      if (rows.length > 3) { hit(rows[0], { ctrlKey: true }); hit(rows[3], { shiftKey: true }); } }""")
    audit_screen(page, frame, out, f"{tag0}-selbar", findings)
    frame.evaluate("() => MemuMusic.dev.Sel.clear()")
    # dialogs: bulk editor, playlist editor, smart builder, smart manager
    for name, js in (("bulk", "MemuMusic.dev.editTracks(MemuMusic.S.tracks.slice(0, 3))"), ("pl-editor", "MemuMusic.dev.editPlaylist(MemuMusic.S.playlists[0])"),
                     ("smart-builder", "MemuMusic.dev.smartBuilder()"), ("smart-manager", "MemuMusic.dev.smartManager()")):
        frame.evaluate(f"() => {{ document.querySelector('#modal').hidden = true; {js}; }}")
        page.wait_for_timeout(500)
        audit_screen(page, frame, out, f"{tag0}-dlg-{name}", findings, "#modal")
        frame.evaluate("() => { document.querySelector('#modal').hidden = true; }")
    # toast with Undo, drag & drop overlay
    go("library")
    frame.evaluate("() => MemuMusic.dev.toastAction('Удалено: 3 трека', 'Отменить', () => {})")
    audit_screen(page, frame, out, f"{tag0}-toast-undo", findings)
    frame.evaluate("() => { document.querySelector('#toasts').innerHTML = ''; }")
    frame.evaluate("() => { const d = document.querySelector('#drop'); d.hidden = false; d.classList.add('files'); }")
    audit_screen(page, frame, out, f"{tag0}-drop", findings, "#drop")
    frame.evaluate("() => { const d = document.querySelector('#drop'); d.hidden = true; d.classList.remove('files'); }")
    # Appearance + Widgets & integration tabs
    frame.evaluate(INTEGRATION_STUB)
    for tab in ("appearance", "integration"):
        go("settings", f"{{ tab: '{tab}' }}")
        audit_screen(page, frame, out, f"{tag0}-settings-{tab}", findings)


FOCUS_JS = r"""() => {
  const out = [], sel = 'input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=color]):not([type=file]):not([type=hidden]), textarea, select';
  const settle = () => document.getAnimations().forEach((a) => { try { a.finish(); } catch (e) {} });
  const snap = (e) => { const c = getComputedStyle(e); return { o: c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0 ? c.outlineWidth + c.outlineColor : '', bs: c.boxShadow, bc: c.borderTopColor, bw: parseFloat(c.borderTopWidth) }; };
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2 || el.closest('[hidden]') || el.disabled) continue;
    const chain = [el]; for (let p = el.parentElement, k = 0; p && k < 3; p = p.parentElement, k++) chain.push(p);
    if (document.activeElement) document.activeElement.blur(); settle();
    const before = chain.map(snap); el.focus({ preventScroll: true }); settle(); const after = chain.map(snap);
    const own = after[0].o ? 'outline' : after[0].bs !== before[0].bs ? 'shadow' : after[0].bc !== before[0].bc ? 'border' : '';
    let anc = ''; for (let i = 1; i < chain.length; i++) if (after[i].bs !== before[i].bs || after[i].bc !== before[i].bc || after[i].o !== before[i].o) { anc = String(chain[i].className || chain[i].tagName); break; }
    const kind = !own && !anc ? 'focus-none' : own && anc ? 'focus-double' : own === 'outline' && !(before[0].bw > 0) ? 'focus-inner-rect' : '';
    if (kind) out.push({ kind, el: (el.id ? '#' + el.id : el.tagName.toLowerCase()) + '.' + String(el.className).split(' ')[0] + '[' + (el.type || '') + ']', own, anc });
    el.blur(); settle();
  }
  return out; }"""


def audit_focus(page, frame, tag0: str, findings: list) -> None:
    """Round 39: one focus ring per text field / list — on the field or on its rounded container, never both, never none."""
    go = lambda js: (frame.evaluate(js), page.wait_for_timeout(350))
    screens = [("download", "() => MemuMusic.go('download', {}, { instant: true })"), ("search", "() => MemuMusic.go('search', { q: 'a' }, { instant: true })")]
    screens += [(f"settings-{t}", f"() => MemuMusic.go('settings', {{ tab: '{t}' }}, {{ instant: true }})") for t in ("appearance", "playback", "library", "downloads", "keys")]
    screens += [(f"dlg-{n}", f"() => {{ document.querySelector('#modal').hidden = true; {js}; }}") for n, js in (
        ("bulk", "MemuMusic.dev.editTracks(MemuMusic.S.tracks.slice(0, 3))"), ("pl-editor", "MemuMusic.dev.editPlaylist(MemuMusic.S.playlists[0])"), ("smart-builder", "MemuMusic.dev.smartBuilder()"))]
    seen = set()
    for tag, js in screens:
        go(js)
        for item in frame.evaluate(FOCUS_JS):
            key = (item["kind"], item["el"])
            if key in seen:
                continue
            seen.add(key)
            item["screen"] = f"{tag0}-{tag}"
            findings.append(item)
    frame.evaluate("() => { document.querySelector('#modal').hidden = true; }")


NP_CONTRAST_JS = """(hide) => { const np = document.querySelector('#np'); np.classList.toggle('mm-audit-notext', hide);
  let st = document.getElementById('mm-audit-st'); if (!st) { st = document.createElement('style'); st.id = 'mm-audit-st';
  st.textContent = '.mm-audit-notext .mm-np-side, .mm-audit-notext .mm-np-head { visibility: hidden !important; }'; document.head.appendChild(st); }
  const el = np.querySelector('.mm-lyric.on') || np.querySelector('#np-title'); const r = el.getBoundingClientRect();
  const ink = getComputedStyle(np).getPropertyValue('--np-ink').trim();
  return { box: [r.left, r.top, r.right, r.bottom], ink, dpr: devicePixelRatio }; }"""


def np_contrast(page, frame, tag: str, findings: list) -> None:
    """Text over pictures: hide the text, sample the backdrop under the active lyric line (or the title) and check the
    worst 5 % of pixels against the ink colour — WCAG 4.5."""
    from PIL import Image
    info = frame.evaluate(NP_CONTRAST_JS, True)
    page.wait_for_timeout(120)
    img = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    frame.evaluate(NP_CONTRAST_JS, False)
    x0, y0, x1, y1 = [int(v * info["dpr"]) for v in info["box"]]
    crop = img.crop((max(0, x0), max(0, y0), max(x0 + 1, x1), max(y0 + 1, y1)))
    ink = [float(v) for v in info["ink"].replace(",", " ").split()[:3]]
    lum = lambda c: sum(k * ((v / 255) / 12.92 if v / 255 <= .03928 else (((v / 255) + .055) / 1.055) ** 2.4) for k, v in zip((.2126, .7152, .0722), c))
    li = lum(ink)
    small = crop.resize((40, 10))
    ratios = sorted((max(li, lum(px)) + .05) / (min(li, lum(px)) + .05) for px in (small.getpixel((k % 40, k // 40)) for k in range(400)))
    worst = ratios[len(ratios) // 20]
    if worst < 4.5:
        findings.append({"kind": "np-contrast", "screen": tag, "ratio": round(worst, 2), "need": 4.5})


def run(out_dir: str, quick: bool = False, only: list | None = None) -> list:
    from playwright.sync_api import sync_playwright
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    covers = cover_images()
    findings: list = []
    combos = [
        ({"mode": "dark", "theme": "cover", "design": "glass"}, (1360, 860)),
        ({"mode": "light", "theme": "sand", "design": "retro"}, (1360, 860)),
        ({"mode": "dark", "theme": "neon", "design": "neon"}, (1920, 1080)),
        ({"mode": "light", "theme": "midnight", "design": "minimal"}, (1360, 860)),
        ({"mode": "dark", "theme": "graphite", "design": "compact"}, (720, 640)),
        ({"mode": "light", "theme": "aurora", "design": "glass"}, (720, 640)),
        ({"mode": "dark", "theme": "sunset", "design": "minimal"}, (1920, 1080)),
        ({"mode": "light", "theme": "graphite", "design": "neon"}, (1360, 860)),
        ({"mode": "dark", "theme": "vinyl", "design": "retro"}, (1360, 860)),
        ({"mode": "light", "theme": "ocean", "design": "compact"}, (1920, 1080)),
    ]
    if quick:
        combos = combos[:3]
    if only:
        combos = [c for i, c in enumerate(combos) if i in only]
    with sync_playwright() as pw:
        for idx, (ui, size) in enumerate(combos):
            idx = idx if not only else only[idx]
            browser, page, errors = open_page(pw, "ru", "dark", size)
            try:
                frame = frame_of(page)
                wait(page, frame, "() => !!(window.MemuMusic && MemuMusic.S.loaded && MemuMusic.Theme)")
                page.wait_for_timeout(600)
                inject_covers(frame, covers, few=(idx == 0))
                configure(frame, dict(ui, motion="off"))
                tag0 = f"{ui['mode']}-{ui['theme']}-{ui['design']}-{size[0]}"
                for view in VIEWS:
                    frame.evaluate(f"() => MemuMusic.go('{view}', {{}}, {{instant: true}})")
                    audit_screen(page, frame, out, f"{tag0}-{view}", findings)
                # album / artist / playlist pages
                frame.evaluate("() => { const a = MemuMusic.S.tracks[0]; MemuMusic.go('album', { key: a.artist + '|' + (a.album || a.title) }, { instant: true }); }")
                audit_screen(page, frame, out, f"{tag0}-album", findings)
                frame.evaluate("() => MemuMusic.go('artist', { name: MemuMusic.S.tracks[0].artist }, { instant: true })")
                audit_screen(page, frame, out, f"{tag0}-artist", findings)
                # feat screens: search, history, duplicates, settings tabs, track menu, editor dialog
                frame.evaluate("() => MemuMusic.go('search', { q: 'a' }, { instant: true })")
                audit_screen(page, frame, out, f"{tag0}-search", findings)
                for view in ("history", "dupes"):
                    frame.evaluate(f"() => MemuMusic.go('{view}', {{}}, {{instant: true}})")
                    audit_screen(page, frame, out, f"{tag0}-{view}", findings)
                for tab in ("playback", "library", "downloads", "keys"):
                    frame.evaluate(f"() => MemuMusic.go('settings', {{ tab: '{tab}' }}, {{instant: true}})")
                    audit_screen(page, frame, out, f"{tag0}-settings-{tab}", findings)
                frame.evaluate("() => MemuMusic.go('library', {}, { instant: true })")
                page.wait_for_timeout(400)
                frame.evaluate("() => { const r = document.querySelector('.mm-row'); const b = r.getBoundingClientRect(); r.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: b.left + 200, clientY: b.top + 20 })); }")
                audit_screen(page, frame, out, f"{tag0}-menu", findings, "#menu")
                frame.evaluate("() => { const b = [...document.querySelectorAll('#menu button')].find((x) => x.querySelector('use[href=\"#i-edit\"]')); if (b) b.click(); }")
                audit_screen(page, frame, out, f"{tag0}-editor", findings, "#modal")
                # every tab of the track editor
                for k, tab in enumerate(frame.evaluate("() => [...document.querySelectorAll('#modal .mm-ed-tabs [data-tab]')].map((b) => b.dataset.tab)")):
                    frame.evaluate(f"() => document.querySelector('#modal .mm-ed-tabs [data-tab={tab}]').click()")
                    audit_screen(page, frame, out, f"{tag0}-editor-{tab}", findings, "#modal")
                frame.evaluate("() => { document.querySelector('#modal').hidden = true; }")
                audit_extra(page, frame, out, tag0, findings)
                audit_focus(page, frame, tag0, findings)
                # layout shift while switching sections (animations on)
                configure(frame, {"motion": "full"})
                frame.evaluate("() => MemuMusic.go('home', {}, { instant: true })")
                page.wait_for_timeout(500)
                frame.evaluate(CLS_INSTALL)
                for view in ["library", "home", "stats", "playlists", "home", "settings", "download", "home"]:
                    frame.evaluate(f"() => MemuMusic.go('{view}')")
                    page.wait_for_timeout(450)
                cls = frame.evaluate("() => ({ cls: window.__cls, shifts: window.__shifts.slice(0, 6) })")
                if cls["cls"] > 0.01:
                    findings.append({"kind": "shift", "screen": tag0, "cls": round(cls["cls"], 4), "shifts": cls["shifts"]})
                configure(frame, {"motion": "off"})
                # now playing (each style) + queue tab
                frame.evaluate("() => { const S = MemuMusic.S; MemuMusic.Player.playList(S.tracks.slice(0, 20), S.tracks[0].id, 'Демо'); }")
                page.wait_for_timeout(900)
                frame.evaluate("() => MemuMusic.NP().open()")
                for style in (["card", "vinyl", "full", "minimal"] if idx < 2 else ["card", "full"]):
                    configure(frame, {"npStyle": style})
                    page.wait_for_timeout(500)
                    audit_screen(page, frame, out, f"{tag0}-np-{style}", findings, "#np")
                    np_contrast(page, frame, f"{tag0}-np-{style}", findings)
                frame.evaluate("() => document.querySelector('.mm-np-tabs [data-tab=queue]').click()")
                audit_screen(page, frame, out, f"{tag0}-np-queue", findings, "#np")
                frame.evaluate("() => MemuMusic.NP().close()")
                page.wait_for_timeout(400)
                frame.evaluate("() => document.querySelector('#open-queue').click()")
                audit_screen(page, frame, out, f"{tag0}-queue", findings, "#queue-drawer")
                frame.evaluate("() => document.querySelector('#open-queue').click()")
                findings.extend({"kind": "console", "screen": tag0, "el": e[:200]} for e in errors if "favicon" not in e)
            finally:
                browser.close()
    name = "report.json" if not only else "report-" + "-".join(map(str, only)) + ".json"
    (out / name).write_text(json.dumps(findings, ensure_ascii=False, indent=1), encoding="utf-8")
    return findings


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = [int(x) for a in sys.argv if a.startswith("--only=") for x in a[7:].split(",")]
    result = run(args[0] if args else "/tmp/mm-audit", quick="--quick" in sys.argv, only=only or None)
    kinds: dict = {}
    for f in result:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    print("findings:", len(result), kinds)
    sys.exit(1 if result else 0)
