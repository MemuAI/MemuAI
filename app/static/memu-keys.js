/* Memu shared keyboard helper (Round 33, item #8): shortcuts work in every keyboard layout.
 *
 * Problem: handlers compared `event.key` with Latin letters ("s" for Ctrl+S). With the Russian layout the same
 * physical key reports "ы", so Ctrl+S, Ctrl+Z, Ctrl+F… silently did nothing until the user switched to English.
 *
 * Fix (the rule CodeMirror and VS Code use): when a shortcut is pressed (Ctrl / Alt / Meta held) and the layout
 * produced a NON-Latin character, the shortcut is read from the PHYSICAL key (`event.code`, US-QWERTY meaning).
 * A Latin layout (QWERTY, Dvorak, AZERTY…) keeps its own `event.key`, so Dvorak users still press "their" S.
 *
 * `install(window)` adds one capture listener on the window that rewrites `event.key` of such events (an own
 * property on the event object; typed text is untouched — it comes from `beforeinput`, not from `key`). Every
 * existing handler in that document — app code, modules, CodeMirror — then sees "s" instead of "ы".
 * AltGr (Ctrl+Alt on Windows producing ą, ś, € …) is left alone, so typing such characters never fires shortcuts.
 *
 * Single-key tool shortcuts (B = brush in the photo editor, B/W in a slide show) have no modifier: they call
 * `MemuKeys.latin(e)` explicitly instead (a global rewrite there would change what type-ahead code sees).
 *
 * The SAME file is shipped to every web root (pgstatic, Proxy static, Mini App, MemuNote); a test keeps the copies
 * identical. Works as a classic <script>, an ES-module side effect and in Node (module.exports) for tests.
 */
(function (root) {
  "use strict";
  // US-QWERTY meaning of physical keys: [unshifted, shifted]
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
    // a single printable ASCII character (any Latin layout) — trusted as is
    return typeof key === "string" && key.length === 1 && key.charCodeAt(0) >= 32 && key.charCodeAt(0) < 127;
  }
  function needsCode(key) {
    if (typeof key !== "string") return true;
    if (key === "Dead" || key === "Unidentified" || key === "Process" || key === "") return true;
    var chars = Array.from(key);
    return chars.length === 1 && !isLatinish(key);
  }
  /** The layout-independent character of a key event ("s" for Ы on the Russian layout). Named keys
   * (Enter, ArrowUp, F5…) are returned unchanged. */
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
  /** Is this event a shortcut whose `key` should be read from the physical key? */
  function shouldRewrite(e) {
    if (!e || !(e.ctrlKey || e.metaKey || e.altKey)) return false;
    if (e.isComposing) return false;
    if (altGraph(e)) return false;                       // AltGr characters are typing, not shortcuts
    if (!needsCode(e.key)) return false;
    return !!fromCode(e.code, !!e.shiftKey);
  }
  /** Rewrite `e.key` in place (own property). Returns true when it changed. */
  function normalize(e) {
    if (!shouldRewrite(e)) return false;
    var mapped = fromCode(e.code, !!e.shiftKey);
    try {
      Object.defineProperty(e, "originalKey", { value: e.key, configurable: true });
      Object.defineProperty(e, "key", { value: mapped, configurable: true });
      return true;
    } catch (_) { return false; }
  }
  /** "Ctrl+Shift+S" for an event (layout-independent); "" for a lone modifier. */
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
  /** Does the event match "s" / "Ctrl+S"-style key regardless of layout (case-insensitive for letters)? */
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
