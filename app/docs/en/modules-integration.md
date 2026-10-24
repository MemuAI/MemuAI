---
title: Integration with MemuAI: panels, commands, widgets
summary: How a module adds its panels to the home screen, commands for the AI in chat and Quick Access, and widgets.
---

# Integration with MemuAI: panels, commands, widgets

A module can reach beyond its window. The `contributes` block of `manifest.json` adds:

- **panels** — module cards in the “New panel” list (the “Module panels” section);
- **commands** — actions the AI runs in chat and in Quick Access (“Play Believer”);
- **a skill** — text for the AI: what the module can do and when to call its commands;
- **widgets** — a mini player above the message box, a card and an equalizer ring in Quick Access.

All of it works only for **enabled** modules and only with the module's own permissions. In safe mode modules add nothing.

## Example

```json
"contributes": {
  "panels": [{
    "id": "now-playing", "title": "Сейчас играет", "title_en": "Now playing",
    "summary": "Обложка и управление", "icon": "music", "size": "m", "height": 200,
    "entry": "integration/panel.html", "scripts": ["integration/panels.js"], "styles": ["integration/panels.css"],
    "settings": [{"key": "compact", "type": "bool", "label": "Компактно", "label_en": "Compact", "default": false}]
  }],
  "commands": [{
    "id": "open-note", "title": "Открыть заметку", "title_en": "Open a note",
    "description": "Открыть заметку по названию", "description_en": "Open a note by its title",
    "args": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
    "phrases": ["открой заметку {title}"], "phrases_en": ["open the note {title}"],
    "danger": false, "run": {"open": true}
  }],
  "skill": "integration/skill.md",
  "widgets": [{
    "id": "eq-ring", "slot": "quick.ring", "kind": "equalizer", "source": "music.player",
    "title": "Equalizer", "settings": [{"key": "enabled", "type": "bool", "label": "Show", "default": true}]
  }]
}
```

All files live in the module package, usually in the `integration/` folder.

## Panels

A panel is a separate page of the module in the same sandbox as its window: no network, the `Memu.*` bridge and the module's permissions.

- `entry` — the panel's HTML fragment; `scripts` and `styles` — package files (up to 6 and 4).
- `size` — the default size: `s`, `m`, `l` or `w`; `height` — the card height, 96–640 px.
- `settings` — panel settings (`bool`, `choice`, `text`, `color`); their values are in `Memu.panel.settings`. People change them in the panel settings on the home screen.
- `data` — the name of a server data source. Built-in modules only (Characters, API models, Benchmarks); a third-party module gets a validation error and the field is dropped.

A panel is created lazily: the page loads only when the card appears on screen and lives as long as the panel stays.

### The Memu.panel object

| Field | What it is |
| --- | --- |
| `id` | the panel id from the manifest — one script can draw several panels |
| `settings` | the panel's setting values |
| `params` | the card's size and place, e.g. `{"size": "m"}` |
| `visible` | whether the card is on screen now |
| `open(detail)` | open the module window; `detail` arrives in the `navigate` event |
| `data(args)` | a `Promise` with the panel's server data (only with `data` in the manifest) |
| `height(px)` | report the height the card needs |

While the card is off screen or the MemuAI window is minimized, the `memu-panel-visible` event arrives with `visible: false` — stop polling and animations. The `html[data-size]` attribute mirrors the card size, handy for styles.

```js
const P = Memu.panel;
document.addEventListener("memu-panel-visible", (e) => { if (e.detail.visible) refresh(); });
async function refresh() {
  const data = P.data ? await P.data({ limit: 5 }) : null;   // a built-in module with "data"
  render(P.id, data);
}
```

### What a panel can use

`Memu.music.call` (the `music` permission), `Memu.store` (`get`, `get_many`, `set`, `list`, the `storage` permission), `Memu.monitor` (only `status` and `events`, the `background` permission), `Memu.env`, `Memu.log`, `Memu.panel.open`. Everything else is in the module window: the panel gets an “open the module” error.

## The common look of panels

Every panel gets the MemuAI style kit **before** its own styles — so panels of different modules look equally calm and follow the light and dark theme on their own. The accent colour is the `--c` variable (`--accent` by default).

| Class | What it is |
| --- | --- |
| `.mk` | the panel root: padding, column, `--c` |
| `.mk-glow` | a soft accent glow in the corner |
| `.mk-head`, `.mk-ico`, `.mk-link` | the header, its icon and a link on the right (“All…”) |
| `.mk-list`, `.mk-row`, `.mk-main`, `.mk-side` | a list of rows: a row button, its text and a value on the right |
| `.mk-mono` (`.round`) | a 32 px monogram avatar or picture; the hue is `--h` |
| `.mk-pill` (`.dim`) | a pill label |
| `.mk-chips`, `.mk-chip` | scrollable filters; the selected one has `aria-pressed="true"` |
| `.mk-cta` | the main button |
| `.mk-empty` | the empty state |
| `.mk-skel` | the loading skeleton |

```html
<main class="mk" id="mk" aria-live="polite">
  <div class="mk-skel" aria-hidden="true"><i></i><b></b><b></b></div>
</main>
```

Write the panel's own styles on top, with the theme tokens (see [design](doc:modules-design)).

## Commands

A command is an action the AI can call with the `module_command {module, command, args}` tool.

- `args` — a JSON schema of an object: `string`, `number`, `integer`, `boolean`, `enum`, `required` (up to 8 arguments).
- `phrases` / `phrases_en` — exact phrases with `{name}` placeholders.
- `danger: true` — MemuAI asks the user before running the command.
- `run.open: true` — MemuAI opens the module window and passes the command with the `navigate` event: `Memu.on("navigate", (d) => d.command, d.args)`.
- `run.handler` — a handler on the MemuAI server. Built-in modules only.

```js
Memu.on("navigate", (d) => {
  if (d.command === "open-note") openNote(d.args.title);
});
```

### Phrases in Quick Access

A request that **exactly** matches a phrase runs in Quick Access at once, without a model: “play the track Believer” for the phrase `play the track {query}`. Case and extra spaces do not matter, a trailing `.`, `!` or `?` is allowed, quotes around the argument are removed. Everything else — “could you put on something by Queen” — goes to the AI, which picks the command itself. Commands with `danger: true` never run from a phrase: the AI always handles them, with confirmation.

### The result card

After a command the person sees a card: in chat — under the answer (it is saved with the conversation), in Quick Access — at the bottom of the window. It has a cover, a title and buttons. The model reads only the text of the result and answers in one sentence. A card with a cover and buttons is prepared by a built-in module's handler; a `run.open` command has none — Quick Access shows a short “Opening…”.

## Skill

`skill` — a path to a `.md` file of the package or `{"text": "…", "text_en": "…"}`, up to 6000 characters. The text goes into the system prompt of chat and Quick Access next to the command list while the module is enabled. Keep it short: when to call which command and what the module cannot do.

```markdown
Notes — the user's local notes.
- “open the note X” → notes.open-note {title}
- If there is no such note, offer to create it — there is no command for that, open the module.
```

## Widgets

MemuAI draws a widget itself; the module chooses its kind, place and settings. No module code runs in the chat window.

| `slot` | Where | `kind` |
|---|---|---|
| `chat.composer` | above the chat message box | `miniplayer` |
| `quick.card` | a card in Quick Access | `nowplaying` |
| `quick.ring` | a ring along the edge of Quick Access | `equalizer` |

There is one data source for now — `music.player` (the MemuMusic player). Widgets live only while music plays and the window is visible; without sound in the window the ring animates from the track data.

## Settings in the module itself

A module shows and changes the settings of **its own** widgets and panels with the `Memu.integration` bridge — so they can sit on one settings tab of the module (in MemuMusic — “Widgets & integration”).

```js
const { widgets, panels } = await Memu.integration.list();
// widgets: [{id, slot, kind, title, settings, values}], panels: [{id, title, settings}]
await Memu.integration.set("widget", "eq-ring", { style: "glow" });
```

Settings of other modules are not available. MemuAI stores the values; outside the module they are available through `GET` and `POST /api/pg/modint/settings` with `{module, kind: "widget"|"panel", id, values}`.

## Export and import

An exported module file carries every file named in `contributes`: panel pages, scripts and styles and the skill file. On import MemuAI writes only those files — text, no `..` in the path, up to 400 KB each and 2 MB in total. A file not named in `contributes` is skipped on import.

## Validation

The module workbench and `modules_validate` check `contributes`: ids, panel and skill files, the argument schema, widget places, `data` and `run.handler`. A broken item is dropped, the rest works.

Limits: up to 8 panels, 24 commands, 6 widgets, 12 settings per item and 8 phrases per command.
