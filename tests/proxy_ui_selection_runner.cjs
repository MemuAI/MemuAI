

const fs = require("fs");
const vm = require("vm");
const path = require("path");

const source = fs.readFileSync(path.join(__dirname, "..", "app", "static", "app.js"), "utf8");
const from = source.indexOf("function syncModelsSelection()");
const pick = source.indexOf("function pickModel(modelId, event)");
const marqueeStart = source.indexOf("const marquee = {");
const marqueeEnd = source.indexOf("(function bindModelsSelection()");
if ([from, pick, marqueeStart, marqueeEnd].some((i) => i < 0)) {
  process.stdout.write(JSON.stringify({ error: "selection code not found in app.js" }) + "\n");
  process.exit(0);
}
const code = source.slice(from, marqueeEnd);

const harness = `
  let modelsSelected = new Set();
  let lastPickedModel = "";
  const ids = ["r0", "r1", "r2", "r3", "r4", "r5"];
  const checked = {};
  const rows = ids.map((id, i) => ({
    dataset: { mid: id },
    classList: { toggle(name, on) { checked[id] = on; } },
    querySelector() { return { set checked(v) { checked[id + ":box"] = v; } }; },
    getBoundingClientRect() { return { left: 0, right: 400, top: 100 + 30 * i - list.scrollTop, bottom: 130 + 30 * i - list.scrollTop }; },
  }));
  // The scrolling list: rows move up on the screen as it scrolls.
  const list = { textContent: "", scrollTop: 0, getBoundingClientRect() { return { top: 100, bottom: 400 }; } };
  function $$(selector) { return selector === "#models-table tr[data-mid]" ? rows : []; }
  function $() { return list; }
  function syncModelsBulkBar() {}
  const document = { createElement() { return { style: {}, remove() {} }; }, body: { appendChild() {}, classList: { add() {}, remove() {} } } };
  const window = { scrollY: 0, innerHeight: 800, addEventListener() {}, removeEventListener() {}, getSelection() { return { removeAllRanges() {} }; } };
  const setTimeout = () => 0, setInterval = () => 0, clearInterval = () => {};
  ${code}
  const out = {};
  const sel = () => ids.filter((id) => modelsSelected.has(id));
  const drag = (x0, y0, x1, y1, additive, base) => {
    modelsSelected = new Set(base || []);
    Object.assign(marquee, { x0, y0, lastX: x1, lastY: y1, additive: !!additive, base: new Set(modelsSelected),
      scroll0: list.scrollTop, page0: window.scrollY });
    marqueeApply();
    return sel();
  };
  // A rectangle over the middle of three rows selects exactly the rows it touches.
  out.dragThree = drag(10, 115, 200, 175);
  out.dragUpwards = drag(200, 175, 10, 115);                 // the same rectangle drawn backwards
  out.dragOne = drag(10, 135, 50, 140);
  out.dragNothing = drag(10, 10, 50, 60);                    // above the list
  out.dragAll = drag(0, 0, 500, 500);
  // Ctrl adds to the existing selection and toggles rows already selected.
  out.ctrlAdd = drag(10, 135, 50, 140, true, ["r4"]);
  out.ctrlToggleOff = drag(10, 135, 50, 140, true, ["r1", "r4"]);
  // Clicks: plain, Shift range (replaces), Ctrl toggle, Ctrl+Shift range (adds).
  modelsSelected = new Set(); lastPickedModel = "";
  pickModel("r2", {});                      out.click = sel();
  pickModel("r4", { shiftKey: true });      out.shiftRange = sel();
  pickModel("r3", { ctrlKey: true });       out.ctrlOff = sel();
  pickModel("r3", { ctrlKey: true });       out.ctrlOn = sel();
  pickModel("r0", { shiftKey: true, ctrlKey: true }); out.ctrlShift = sel();
  pickModel("r5", {});                      out.plainAgain = sel();
  pickModel("r1", { shiftKey: true });      out.shiftBackwards = sel();
  // A plain click on the only selected row clears it; on one of several it keeps just that row.
  modelsSelected = new Set(); lastPickedModel = "";
  pickModel("r2", {}); pickModel("r2", {}); out.clickTwice = sel();
  pickModel("r2", {}); pickModel("r3", { ctrlKey: true }); pickModel("r2", {}); out.clickOneOfTwo = sel();
  pickModel("r4", null);                    out.keyboardKeeps = sel();   // arrows never toggle a row off
  pickModel("r4", null);                    out.keyboardKeepsAgain = sel();
  // Dragging while the list scrolls: the start stays glued to row 0, so rows that left the
  // screen stay inside the rectangle (they used to drop out of the selection).
  modelsSelected = new Set();
  Object.assign(marquee, { x0: 10, y0: 115, lastX: 200, lastY: 175, additive: false, base: new Set(), scroll0: 0, page0: 0 });
  list.scrollTop = 90;
  marqueeApply();
  out.dragWhileScrolling = sel();
  out.startFollowsTheList = marqueeStartY();
  // …and scrolling back up shrinks it again.
  list.scrollTop = 0; marquee.lastY = 140;
  marqueeApply();
  out.dragScrolledBack = sel();
  __done(out);
`;
const ctx = { __done: (value) => process.stdout.write(JSON.stringify(value) + "\n") };
vm.createContext(ctx);
try {
  vm.runInContext(harness, ctx);
} catch (error) {
  process.stdout.write(JSON.stringify({ error: String((error && error.stack) || error) }) + "\n");
}
