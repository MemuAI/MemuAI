---
title: The Memu object
summary: Every bridge method, its arguments and its answer.
---

# The `Memu` object

The bridge is the only channel between a module and MemuAI. It is an ordinary object on `window`, available immediately, with no waiting.

## State

| Field | Meaning |
| --- | --- |
| `Memu.id` | the module id |
| `Memu.version` | bridge version (currently `4`: version 4 added `Memu.toolbar`) |
| `Memu.lang` | current MemuAI language: `ru`, `en` |
| `Memu.manifest` | the public description from `manifest.json` |
| `Memu.saved` | allowed fields from the last run |
| `Memu.env` | language, theme, accent, font scale, platform, host name; `env.toolbar` is `"host"` when the MemuAI window draws the module's actions in its own header |

Take language-specific text from the module's dictionary instead of checking the language by hand:

```javascript
document.querySelector(".greeting").textContent = Memu.t("greeting");
Memu.onLanguage(() => {
  document.querySelector(".greeting").textContent = Memu.t("greeting");
});
```

## Translations

| Method | What it does |
| --- | --- |
| `Memu.t(key, vars)` | a text from `i18n.json` in the current language |
| `Memu.onLanguage(handler)` | call `handler` when the MemuAI language changes |
| `Memu.i18n` | dictionaries, plural forms, number and date formatting |

Details: [Module translations](doc:modules-i18n).

## Window header: `Memu.toolbar`

A module has one header — the MemuAI window's row: the module icon and name, **the module's actions**, then ⋯ (reload, window layout, module settings), minimize, expand and ×. An expanded sheet has no such row at all — the name, actions and window buttons move into the MemuAI top bar itself. So a module **does not paint its own top bar**: it hands its buttons to the window.

| Method | What it does |
| --- | --- |
| `Memu.toolbar.supported()` | `true` when the window draws the module's actions (`Memu.env.toolbar === "host"`) |
| `Memu.toolbar.set(items)` / `.set({title, status, items})` | replace all actions; `title` is a subtitle next to the module name (say, the open chat), `status` a quiet status line |
| `Memu.toolbar.update(id, patch)` | change one action: `pressed`, `disabled`, `label`, `dot`… |
| `Memu.toolbar.title(text)` / `.status(text)` / `.clear()` | just the subtitle / just the status / remove everything |
| `Memu.toolbar.adopt({buttons, hide, status})` | take over the buttons of an existing in-page header without rewriting it (below) |
| `Memu.toolbar.on(id, handler)` | an action was clicked (besides the action's own `onClick`) |

An action is a plain object; functions stay in the module, only data goes to the window:

```javascript
Memu.toolbar.set({
  title: "Alice — Oct 10",
  items: [
    { id: "tokens", kind: "meter", value: 0.42, label: "≈3,487 / 8,192", onClick: openInspector },
    { id: "new", icon: "plus", label: Memu.t("new_chat"), onClick: newChat },
    { id: "more", icon: "more", label: Memu.t("more"), pin: true, menu: [
      { id: "export", icon: "download", label: Memu.t("export"), onClick: exportChat },
      { sep: true },
      { id: "clear", icon: "trash", label: Memu.t("clear"), danger: true, onClick: clearChat },
    ] },
    { kind: "separator" },
    { id: "panel", kind: "toggle", icon: "panel", label: Memu.t("panel"), pressed: panelOpen, pin: true, onClick: togglePanel },
  ],
});
```

- `kind`: `button` (default), `toggle` (`pressed`), `meter` (a `value` 0…1 bar with a caption, `tone`: `warn`/`danger`), `text`, `separator`.
- `icon` — a name from the MemuAI set (`plus`, `search`, `chat`, `note`, `panel`, `sidebar`, `refresh`, `settings`, `sliders`, `download`, `upload`, `trash`, `more`, `star`, `filter`, `sort`, `grid`, `help`, `history`, `tag`…) or your own SVG string (only shapes and strokes survive). Without an icon the button shows its label.
- `label` — the tooltip and the screen-reader text; it is shown on the button only with `showLabel: true` or `primary: true` (one main, accented button).
- `pin: true` — the button never folds into the «»» menu, even in a narrow window. Other actions that do not fit fold into «»» from the start of the list, so put what matters most at the end, next to the window buttons.
- Up to 16 actions and 40 menu entries. Labels are plain text.

### An existing header: `adopt`

If the module already has its own top bar, there is no need to rewrite it:

```javascript
if (Memu.toolbar.supported()) Memu.toolbar.adopt({
  hide: [".brand"],                 // logo and name — the window already shows them
  status: "#status",                // the status text moves into the window row
  buttons: [{ id: "refresh", el: "#refresh", icon: "refresh", showLabel: true }],
});
```

The listed buttons disappear from the page and show up in the window header; a click there clicks the original, and the label, `disabled` and `aria-pressed` follow it live. Hide the then-empty bar with `:root[data-host-toolbar="1"] .my-bar { display: none; }` — the `data-host-toolbar` attribute is set on `<html>` before the module code runs, so nothing flashes.

### Without a host

In the `memu-module-test` bench, in Telegram and in older MemuAI versions there is no window header: `supported()` returns `false`, `set()` answers `{ok: false, supported: false}`, there is no `data-host-toolbar` attribute — and the module shows its own header as before. So keep your bar in the markup and hide it only under `:root[data-host-toolbar="1"]`.

## Model requests

| Method | What it does |
| --- | --- |
| `Memu.request(body)` | one complete model answer |
| `Memu.stream(body, handlers)` | the same request with a stream and statuses |
| `Memu.models()` | the MemuAI model list with capabilities |
| `Memu.history()` | messages of chat `module-<id>`, when it exists |

## Media

| Method | What it does |
| --- | --- |
| `Memu.image({prompt, model, aspect, images})` | image generation |
| `Memu.video({prompt, model, aspect, images})` | video generation |
| `Memu.speak({text})` | speech synthesis |
| `Memu.transcribe({audio})` | transcribe an audio file |
| `Memu.record.start()` / `.stop()` | record the microphone through the MemuAI window |

## Files and data

| Method | What it does |
| --- | --- |
| `Memu.extract({name, data})` | text from PDF, DOCX and plain files |
| `Memu.download({name, text})` | save a file into Downloads/MemuAI |
| `Memu.download({url, name})` | fetch and save an image or media file |
| `Memu.handoff({prompt, images, code, files})` | a "prompt + images + code" package for another AI |
| `Memu.copy(text)` | put text on the clipboard |
| `Memu.paste()` | read text from the clipboard (permission `clipboard`) |
| `Memu.open({url})` | open a URL in the Windows browser |
| `Memu.notify({title, text})` | a MemuAI and Windows notification |

## Search

| Method | What it does |
| --- | --- |
| `Memu.search({query, limit})` | the configured MemuAI web search |
| `Memu.fetch({url, limit})` | read a page and return its text |

## Environment

| Method | What it does |
| --- | --- |
| `Memu.routes()` | route list and availability |
| `Memu.save(fields)` | remember allowed fields |
| `Memu.resize({width, height})` | ask the host to resize the window |
| `Memu.on(type, handler)` | subscribe to a host event |
| `Memu.onTheme(handler)` | subscribe to theme changes |
| `Memu.onLanguage(handler)` | subscribe to language changes |
| `Memu.log(text, level)` | write to the module log |
| `Memu.close()` | close the module window |
| `Memu.call("host", {command: "chat", models: [...]})` | open a new MemuAI chat with the first connected model from the list (up to 6); `{ok: false, reason: "not_connected"}` when none is |
| `Memu.call("host", {command: "module", id, query, model})` | open another enabled module; it receives the `navigate` event with `{query, model, from}` |
| `Memu.ready()` | mark the module as loaded |

## Errors

Every method returns a promise. When something is wrong it rejects with a clear message in the interface language:

```javascript
try {
  const answer = await Memu.request({ model: "", text: "hello" });
} catch (error) {
  // "Choose a model" — not an empty string and not a fake success
  status.textContent = error.message;
}
```

The error carries `error.detail` — the original server response, when there was one.

> The bridge never turns an error into a success. If the provider returned no image, the method does not invent a placeholder: it reports the provider's own text.
