// MemuNote — Round 33: a narrow window never pushes the right-hand toolbar buttons (Run, AI, Export, Split view)
// out of sight. When the toolbar is wider than the window, the least used Markdown formatting buttons move, one by
// one, into a "More" (⋯) menu placed at the end of the formatting group; the menu runs the very same buttons
// (button.click() → the toolbar's own click handler), so nothing is duplicated. Wider window → the buttons return.
//
// Self-installing side-effect module (imported once by app.js): watches #toolbar for re-renders (renderToolbar()
// rewrites its innerHTML) and for size changes.
import { icon, t, showMenu } from "./core.js";

// Dropped first → last. Bold/italic/heading/lists stay visible the longest.
const ORDER = ["md:hr", "md:strike", "md:quote", "md:code", "md:table", "md:ol", "md:task", "md:link", "md:heading", "md:ul", "md:italic", "md:bold"];
const ICONS = { "md:bold": "bold", "md:italic": "italic", "md:strike": "strike", "md:code": "inlineCode", "md:heading": "heading",
  "md:ul": "listUl", "md:ol": "listOl", "md:task": "checklist", "md:quote": "quote", "md:link": "link", "md:table": "table", "md:hr": "hr" };

const overflowing = (bar) => bar.scrollWidth > bar.clientWidth + 1;

/** Split "Bold (Ctrl+B)" into the label and the shortcut. */
function splitTitle(text) {
  const m = /^(.*?)\s*\(([^()]+)\)\s*$/.exec(text || "");
  return m ? [m[1], m[2]] : [text || "", ""];
}

export function fitToolbar(bar) {
  bar.querySelectorAll(".tb-overflow").forEach((el) => el.remove());
  bar.querySelectorAll("[data-fit-hidden]").forEach((el) => { el.style.display = ""; el.removeAttribute("data-fit-hidden"); });
  const ai = bar.querySelector(".ai-btn");   // icon-only below 700 px (r33.css) — keep an accessible name
  if (ai && !ai.getAttribute("aria-label")) ai.setAttribute("aria-label", t("ai_button"));
  const group = bar.querySelector(".md-group");
  if (!group || !overflowing(bar)) return [];
  const more = document.createElement("button");
  more.type = "button";
  more.className = "tb-btn tb-overflow";
  more.title = t("tb_more");
  more.setAttribute("aria-label", t("tb_more"));
  more.setAttribute("aria-haspopup", "menu");
  more.innerHTML = icon("more");
  group.append(more);
  const hidden = [];
  for (const action of ORDER) {
    if (!overflowing(bar)) break;
    const button = group.querySelector(`[data-a="${action}"]`);
    if (!button) continue;
    button.style.display = "none";
    button.setAttribute("data-fit-hidden", "");
    hidden.push(button);
  }
  if (!hidden.length) { more.remove(); return []; }
  more.addEventListener("click", (event) => {
    event.stopPropagation();   // the toolbar's own handler only knows [data-a] buttons
    const items = group.querySelectorAll("[data-fit-hidden]");
    showMenu([...items].map((button) => {
      const [label, shortcut] = splitTitle(button.getAttribute("aria-label") || button.title);
      return { label, kbd: shortcut, icon: ICONS[button.dataset.a] || "", run: () => button.click() };
    }), more);
  });
  return hidden;
}

export function installToolbarFit(bar) {
  if (!bar || bar._fit) return;
  let frame = 0;
  const observer = new MutationObserver(() => schedule());
  const run = () => {
    frame = 0;
    observer.disconnect();
    try { fitToolbar(bar); } finally { observer.takeRecords(); observer.observe(bar, { childList: true }); }
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(run); };
  observer.observe(bar, { childList: true });
  new ResizeObserver(schedule).observe(bar);
  bar._fit = { refit: run };
  schedule();
}

(function start(tries = 0) {
  const bar = typeof document !== "undefined" && document.getElementById("toolbar");
  if (bar) installToolbarFit(bar);
  else if (tries < 600) setTimeout(() => start(tries + 1), 50);
})();
