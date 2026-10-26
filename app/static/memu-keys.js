

(function (root) {
  "use strict";

  var CODE = {
    Minus: ["-", "_"], Equal: ["=", "+"], BracketLeft: ["[", "{"], BracketRight: ["]", "}"], Backslash: ["\\", "|"],
    Semicolon: [";", ":"], Quote: ["'", "\""], Comma: [",", "<"], Period: [".", ">"], Slash: ["/", "?"],
    Backquote: ["`", "~"], IntlBackslash: ["\\", "|"],
    Digit1: ["1", "!"], Digit2: ["2", "@"], Digit3: ["3", "#"], Digit4: ["4", "$"], Digit5: ["5", "%"],
    Digit6: ["6", "^"], Digit7: ["7", "&"], Digit8: ["8", "*"], Digit9: ["9", "("], Digit0: ["0", ")"],
  };
  function fromCode(code, shift) {
    if (!code) return "";
    var m = /^Key([A-Z])$/.exec(code);
    if (m) return shift ? m[1] : m[1].toLowerCase();
    var c = CODE[code];
    return c ? c[shift ? 1 : 0] : "";
  }
  function isLatinish(key) {

    return typeof key === "string" && key.length === 1 && key.charCodeAt(0) >= 32 && key.charCodeAt(0) < 127;
  }
  function needsCode(key) {
    if (typeof key !== "string") return true;
    if (key === "Dead" || key === "Unidentified" || key === "Process" || key === "") return true;
    var chars = Array.from(key);
    return chars.length === 1 && !isLatinish(key);
  }

  function latin(e) {
    if (!e) return "";
    var key = e.key;
    if (!needsCode(key)) return key;
    var mapped = fromCode(e.code, !!e.shiftKey);
    return mapped || key || "";
  }
  function altGraph(e) {
    try { return !!(e.getModifierState && e.getModifierState("AltGraph")); } catch (_) { return false; }
  }

  function shouldRewrite(e) {
    if (!e || !(e.ctrlKey || e.metaKey || e.altKey)) return false;
    if (e.isComposing) return false;
    if (altGraph(e)) return false;
    if (!needsCode(e.key)) return false;
    return !!fromCode(e.code, !!e.shiftKey);
  }

  function normalize(e) {
    if (!shouldRewrite(e)) return false;
    var mapped = fromCode(e.code, !!e.shiftKey);
    try {
      Object.defineProperty(e, "originalKey", { value: e.key, configurable: true });
      Object.defineProperty(e, "key", { value: mapped, configurable: true });
      return true;
    } catch (_) { return false; }
  }

  function combo(e) {
    var k = latin(e);
    if (!k || /^(Control|Shift|Alt|Meta|AltGraph|OS)$/.test(k)) return "";
    var parts = [];
    if (e.ctrlKey) parts.push("Ctrl");
    if (e.metaKey) parts.push("Meta");
    if (e.altKey) parts.push("Alt");
    if (e.shiftKey) parts.push("Shift");
    if (k === " ") k = "Space";
    else if (k.length === 1) k = k.toUpperCase();
    parts.push(k);
    return parts.join("+");
  }

  function is(e, key) {
    var k = latin(e);
    return typeof key === "string" && (k === key || (k.length === 1 && key.length === 1 && k.toLowerCase() === key.toLowerCase()));
  }
  function install(win) {
    win = win || (typeof window !== "undefined" ? window : null);
    if (!win || !win.addEventListener || win.__memuKeysInstalled) return false;
    win.__memuKeysInstalled = true;
    var h = function (e) { normalize(e); };
    win.addEventListener("keydown", h, true);
    win.addEventListener("keyup", h, true);
    return true;
  }
  var api = { latin: latin, normalize: normalize, shouldRewrite: shouldRewrite, combo: combo, is: is, fromCode: fromCode, install: install, version: 1 };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) {
    root.MemuKeys = root.MemuKeys || api;
    if (root.window === root && root.addEventListener) install(root);
  }
})(typeof globalThis !== "undefined" ? globalThis : (typeof window !== "undefined" ? window : this));
