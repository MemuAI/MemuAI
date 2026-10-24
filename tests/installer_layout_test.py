"""Round 21: the installer window must fit ANY client area, not just 960×620.

On real Windows the client area of the frameless window came out 944×581 and the
bottom of the panel (footer note and buttons) was cut off on every screen. The
page is now fluid (``installer/ui/style.css``: a size-container stage with
clamp()ed lengths; ``app.js`` ``fit()`` scales the stage below 860×540 or when a
screen would still overflow). This test opens every screen of the mock interface
in headless Chromium at several viewport sizes, in Russian and English, and
checks geometry:

* the panel lies inside the viewport;
* every visible text element, button, switch and icon of the active screen (and of
  an open modal) lies inside the viewport, inside the panel, and inside every
  ancestor that clips its overflow — i.e. nothing is cut off;
* text is not clipped inside its own box (except where an ellipsis is intended);
* the active screen does not overflow; at 860×540 and larger no transform
  scaling is needed (the CSS alone fits), below that it may scale;
* the hero caption does not run under the panel; the title bar parts do not overlap.

It needs Playwright and a Chromium binary and SKIPS cleanly without them.
Round 23 added the wizard steps (location, options, review, express, What's new).
Run as a script to also save screenshots::

    python tests/installer_layout_test.py --shots /workspace/work/shots/r21-installer
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "installer" / "ui" / "index.html"
CHROME_CANDIDATES = [os.environ.get("MEMUAI_CHROMIUM", ""), "/usr/bin/chromium", "/usr/bin/chromium-browser",
                     "/usr/bin/google-chrome", "/workspace/work/chromium/chrome"]

#: (name, query) — every screen and welcome variant of the mock interface.
CASES = [
    ("welcome", "screen=welcome"),
    ("edition", "screen=edition"),
    ("location", "screen=location"),
    ("location-legacy", "screen=location&legacy=1"),
    ("options", "screen=options"),
    ("review", "screen=review"),
    ("review-express", "screen=express"),
    ("progress", "screen=progress&pct=47"),
    ("done", "screen=done&speed=0.2"),
    ("error", "screen=error"),
    ("apps", "screen=apps"),
    ("welcome-legacy", "screen=welcome&legacy=1"),
    ("update", "kind=upgrade&screen=welcome"),
    ("update-unknown", "kind=upgrade&from=&screen=welcome"),
    ("update-review", "kind=upgrade&screen=review"),
    ("update-options", "kind=upgrade&screen=options"),
    ("update-done", "kind=upgrade&screen=done&speed=0.2"),
    ("repair", "kind=reinstall&screen=welcome"),
    ("repair-review", "kind=reinstall&screen=review"),
    ("downgrade", "kind=downgrade&screen=welcome"),
    ("downgrade-review", "kind=downgrade&screen=review"),
    ("proxy-welcome", "product=proxy&screen=welcome"),
    ("proxy-update", "product=proxy&kind=upgrade&screen=welcome"),
    ("proxy-location", "product=proxy&screen=location"),
    ("proxy-options", "product=proxy&screen=options"),
    ("proxy-review", "product=proxy&screen=review"),
    ("proxy-done", "product=proxy&screen=done&speed=0.2"),
    ("uninstall", "mode=uninstall"),
    ("uninstall-keepoff", "mode=uninstall&screen=keepoff"),
    ("uninstall-progress", "mode=uninstall&screen=progress&pct=40"),
    ("uninstall-done", "mode=uninstall&screen=un-done&speed=0.2"),
]
#: The sizes from the bug report and the design, the fluid minimum, a big window,
#: and one below the minimum (scaled to fit).
SIZES = [(944, 581), (960, 620), (880, 540), (860, 540), (1280, 800), (760, 480)]
FLUID_MIN = (860, 540)
LANGS = ("ru", "en")

CHECK_JS = r"""
() => {
  const W = innerWidth, H = innerHeight, tol = 1.01, problems = [];
  const fit = window.__memuFit ? window.__memuFit() : { scale: 1, scroll: false };
  const panelEl = document.getElementById('panel');
  const panel = panelEl.getBoundingClientRect();
  const inside = (r, b) => r.left >= b.left - tol && r.top >= b.top - tol && r.right <= b.right + tol && r.bottom <= b.bottom + tol;
  const label = el => (el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && el.className.baseVal === undefined ? '.' + String(el.className).trim().split(/\s+/).join('.') : '')) +
    (el.textContent ? ' «' + el.textContent.trim().slice(0, 40) + '»' : '');
  const view = { left: 0, top: 0, right: W, bottom: H };
  if (!inside(panel, view)) problems.push('panel outside viewport ' + JSON.stringify([panel.left, panel.top, panel.right, panel.bottom]));
  const visible = el => {
    if (el.closest('[hidden]')) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0.5 && r.height > 0.5;
  };
  const ownText = el => Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
  const important = el => ownText(el) || el.matches('button, input, .switch, .check, svg, .vt-num, .badge');
  const roots = [];
  const active = document.querySelector('.screen.active');
  if (active) roots.push([active, panel]);
  const modal = document.getElementById('modal');
  if (modal && !modal.hidden) roots.push([modal.querySelector('.modal-card'), view]);
  let checked = 0;
  for (const [root, bounds] of roots) {
    for (const el of root.querySelectorAll('*')) {
      if (!important(el) || !visible(el)) continue;
      if (el.closest('svg') && el.tagName.toLowerCase() !== 'svg') continue;
      checked++;
      const r = el.getBoundingClientRect();
      if (!inside(r, view)) problems.push('outside viewport: ' + label(el));
      else if (!inside(r, bounds)) problems.push('outside panel: ' + label(el));
      for (let a = el.parentElement; a && a !== panelEl && a !== document.body; a = a.parentElement) {
        const cs = getComputedStyle(a);
        if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
          if (!inside(r, a.getBoundingClientRect())) { problems.push('clipped by ' + label(a) + ': ' + label(el)); break; }
        }
      }
      const cs = getComputedStyle(el);
      if (ownText(el) && cs.textOverflow !== 'ellipsis' && (cs.overflowX !== 'visible' || cs.overflowY !== 'visible')) {
        if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 2) problems.push('text clipped inside ' + label(el));
      }
    }
    if (root.classList.contains('screen') && root.scrollHeight > root.clientHeight + 1 && !fit.scroll)
      problems.push('screen overflows by ' + (root.scrollHeight - root.clientHeight) + 'px');
  }
  const cap = document.querySelector('.hero-caption');
  if (cap && !document.body.classList.contains('centered') && getComputedStyle(cap).opacity > 0.05) {
    const c = cap.getBoundingClientRect();
    if (c.right > panel.left + tol) problems.push('hero caption runs under the panel');
    if (!inside(c, view)) problems.push('hero caption outside viewport');
  }
  const brand = document.querySelector('.brand').getBoundingClientRect(), chrome = document.querySelector('.chrome').getBoundingClientRect();
  if (brand.right > chrome.left + tol) problems.push('title overlaps window buttons');
  if (!inside(chrome, view)) problems.push('window buttons outside viewport');
  return { problems, checked, scale: fit.scale, scroll: fit.scroll, screen: document.documentElement.getAttribute('data-screen') };
}
"""


def chromium_path() -> str:
    for candidate in CHROME_CANDIDATES:
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


async def run_matrix(shots: str = "", sizes=SIZES, langs=LANGS, cases=CASES, webgl: bool = False):
    from playwright.async_api import async_playwright
    results = []
    args = ["--no-sandbox", "--hide-scrollbars"]
    if not webgl:
        args.append("--disable-webgl")
    else:
        args += ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=chromium_path(), args=args)
        page = await browser.new_page(viewport={"width": sizes[0][0], "height": sizes[0][1]}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for name, query in cases:
            url = f"{UI.as_uri()}?mock=1&shot=1&lang={langs[0]}&{query}"
            await page.set_viewport_size({"width": sizes[0][0], "height": sizes[0][1]})
            await page.goto(url, wait_until="load")
            await page.wait_for_timeout(1600 if name.endswith("done") else 700)
            for index, lang in enumerate(langs):
                if index:   # switch language in place, as the user does with RU/EN
                    await page.evaluate(f"document.querySelector('.seg button[data-lang=\"{lang}\"]').click()")
                    await page.wait_for_timeout(120)
                for w, h in sizes:
                    await page.set_viewport_size({"width": w, "height": h})
                    await page.wait_for_timeout(180)
                    data = await page.evaluate(CHECK_JS)
                    data.update(case=name, lang=lang, size=f"{w}x{h}", errors=list(errors))
                    errors.clear()
                    results.append(data)
                    if shots:
                        Path(shots).mkdir(parents=True, exist_ok=True)
                        await page.screenshot(path=str(Path(shots) / f"{name}-{lang}-{w}x{h}.png"))
        await browser.close()
    return results


def failures(results):
    bad = []
    for r in results:
        w, h = (int(v) for v in r["size"].split("x"))
        problems = list(r["problems"]) + [f"page error: {e}" for e in r["errors"]]
        if w >= FLUID_MIN[0] and h >= FLUID_MIN[1] and r["scale"] < 0.999:
            problems.append(f"needed transform scaling {r['scale']:.3f} at a supported size")
        if r["scroll"]:
            problems.append("fell back to inner scrolling")
        if problems:
            bad.append(f"{r['case']} [{r['lang']} {r['size']}]: " + "; ".join(problems[:6]))
    return bad


def _playwright_ok() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return bool(chromium_path())


@unittest.skipUnless(_playwright_ok(), "Playwright/Chromium not available")
class InstallerLayoutFitsEveryViewport(unittest.TestCase):
    def test_every_screen_fits_every_size_in_both_languages(self):
        results = asyncio.run(run_matrix())
        self.assertEqual(len(results), len(CASES) * len(LANGS) * len(SIZES))
        self.assertTrue(all(r["checked"] > 3 for r in results), "the checker found nothing to check")
        bad = failures(results)
        self.assertEqual(bad, [], "\n" + "\n".join(bad))


if __name__ == "__main__":
    if "--shots" in sys.argv:
        out = sys.argv[sys.argv.index("--shots") + 1]
        only = sys.argv[sys.argv.index("--sizes") + 1].split(",") if "--sizes" in sys.argv else None
        sizes = [tuple(int(v) for v in s.split("x")) for s in only] if only else SIZES
        picked = sys.argv[sys.argv.index("--cases") + 1].split(",") if "--cases" in sys.argv else None
        cases = [c for c in CASES if not picked or c[0] in picked]
        langs = tuple(sys.argv[sys.argv.index("--langs") + 1].split(",")) if "--langs" in sys.argv else LANGS
        res = asyncio.run(run_matrix(out, sizes=sizes, langs=langs, cases=cases, webgl="--webgl" in sys.argv))
        bad = failures(res)
        print(json.dumps({"checked": len(res), "failures": bad}, ensure_ascii=False, indent=1))
    else:
        unittest.main()
