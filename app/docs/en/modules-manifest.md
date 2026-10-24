---
title: manifest.json
summary: Every description field, its default and its limit.
---

# `manifest.json`

The file describes the module: how it is named, how it opens, what it remembers and which capabilities it asks for. All fields are optional except `id`.

```json
{
  "id": "my-notes",
  "title": "My notes",
  "title_en": "My notes",
  "summary": "Quick notes that stay saved.",
  "summary_en": "Quick notes that stay saved.",
  "version": "1.0",
  "author": "You",
  "icon": "text",
  "accent": "#d97757",
  "launcher": { "hidden": false, "slot": "sidebar", "order": 0 },
  "window": {
    "default_width": 720,
    "default_height": 520,
    "min_width": 320,
    "min_height": 240,
    "fixed": false,
    "resizable": true,
    "size_mode": "user",
    "center": true
  },
  "persist": ["text", "model"],
  "permissions": ["chat", "storage", "models"],
  "bridge": {
    "model": "",
    "instructions": "You keep short notes.",
    "keep_in_chat": false,
    "thinking": false,
    "web_search": false
  }
}
```

## Top-level fields

| Field | Meaning | Limit |
| --- | --- | --- |
| `id` | Identifier. Required on creation, fixed afterwards | latin, digits, hyphen, 41 |
| `title` / `title_en` | Name in Russian and English | 80 characters |
| `summary` / `summary_en` | Description in settings | 400 characters |
| `version` | Module version shown on the card | 24 characters |
| `author` | Author | 80 characters |
| `icon` | The icon: a glyph name, a package file (`icon.svg`/`.png`/`.webp`/`.ico`) or inline `<svg>`. See [Module icon](#module-icon) | 48 KB inline SVG |
| `icon_bg` | Background behind a glyph: `#rrggbb` or a list of 2–3 colours (a gradient) | 3 colours |
| `accent` | The module's own colour as `#rrggbb` | 3–8 hex digits |
| `default_locale` | Dictionary language used when there is no translation for the MemuAI language. See [Module translations](doc:modules-i18n) | `ru` by default |

### Icons

`text`, `chat`, `sparkle`, `zap`, `image`, `work`, `bot`, `settings`, `palette`, `search`, `globe`, `list`, `clock`, `bell`, `keyboard`, `user`, `users`, `monitor`, `terminal`, `target`, `mic`, `headphones`, `volume`, `download`, `folder`, `info`, `play`, `undo`, `redo`, `scissors`, `clipboard`, `branch`, `pin`, `edit`, `trash`, `copy`, `retry`, `attach`, `send`, `stop`, `temp`, `focus`, `brain`, `export`, `check`, `unread`, `moon`, `sun`, `menu`, `back`, `forward`, `sidebar`, `plus`, `more`, `close`, `bell`.

An unknown name simply draws nothing — it is not an error.


## Module icon

The icon is shown everywhere: the sidebar button, the minimized window pill, the window bar, the card in Settings → Modules,
module cards in chat and Quick access, the module app window, its taskbar button and the Start menu / desktop shortcuts
(MemuAI makes the `.ico` itself).

Three ways to set it in `manifest.json`:

| `icon` value | Looks like |
| --- | --- |
| `"bell"` — a glyph name from the list above | a white glyph on a rounded square; colour from `icon_bg`, else `accent`, else a MemuAI gradient picked by `id` |
| `"icon.svg"` — a package file (`.svg`, `.png`, `.webp`, `.ico`; a folder is fine, `assets/icon.svg`) | your picture as is |
| `"<svg viewBox=\"0 0 512 512\">…</svg>"` or `data:image/png;base64,…` | the same without a separate file (this is what an export writes) |

```json
{ "icon": "icon.svg" }
{ "icon": "list", "icon_bg": ["#5f86f7", "#6142d4"] }
```

**Drawing an icon in the MemuAI style** (every built-in module is drawn this way):

- canvas `viewBox="0 0 512 512"`, a rounded square `<rect x="28" y="28" width="456" height="456" rx="118">` with a diagonal gradient of two related colours;
- a soft top light (a radial white gradient at 35–40% in the top-left corner) and a thin light rim;
- a large light glyph (≈ 260–290 px of 512), filled, with a soft shadow; a small four-point spark is the signature detail;
- check it at 16, 20, 32 and 256 px on light and dark themes: fine details vanish at 16 px.

**Security.** An SVG is cleaned: only drawing elements and attributes stay; `script`, `foreignObject`, `on…` handlers,
`DOCTYPE`/`ENTITY` and any external reference (`http:`, `url(http…)`, `@import`) are removed; links may point only to an `#id` in the file
or to raster `data:image/…`. The picture is shown with `<img>`, so a script in it never runs anyway.
Sizes: a file up to 256 KB, inline SVG up to 48 KB, PNG/WebP/ICO up to 768 KB. «Check» in the Workbench tells you when the icon has a problem.

**The user can replace the icon of any module** — from the module button menu, the card's «⋯» or by clicking the icon on the card:
the MemuAI set, a glyph on a gradient, an emoji on a gradient, their own file (SVG/PNG/ICO/WebP) or «Restore the module's icon».
The replacement lives in MemuAI data (`modules/icons/`), not in the package, and survives module updates.

## `launcher`

- `hidden` — hide the button while keeping the module enabled (useful for helper modules opened from elsewhere).
- `slot` — where the button sits: `sidebar`, `chat-list`, `topbar`, `composer`.
- `order` — position inside the slot, `0`–`9999`. Lower comes first.

Slot and order are changed by the person using MemuAI and stored outside the package: editing `manifest.json` does not overwrite them.

## `persist`

The list of fields the module may remember. A name is up to 40 characters; up to 32 keys. `Memu.save` ignores anything not listed.

> Do not add images or large texts "just in case": every save writes a file. One image as a data URL weighs more than the whole rest of the module.

## `permissions`

What the module asks MemuAI for. When the list is absent the module gets every permission except `net-import` and `music`, which keeps packages made before permissions existed working. Available values: `chat`, `media`, `storage`, `models`, `files`, `download`, `notify`, `external`, `web`, `clipboard`, `background`, `net-import`, `music`. See "Module permissions".

## `net_import`

Only with the `net-import` permission: `{"sources": [...], "hosts": [...]}` — which import sources the module uses (`chub`, `chub-lorebook`, `janitor`, `pygmalion`, `aicc`, `risu`, `perchance`, `generic`; all when absent) and the default hosts for direct PNG/JSON links. The person can replace the hosts in Settings → Modules → ⋯ → Import sources. See `Memu.fetchImport`.

## `bridge`

Defaults for the bridge. Module code may replace any of them per call.

- `model` — a MemuAI model id. An empty string means "the person will choose".
- `instructions` — system text, up to 20 000 characters. It replaces the system prompt rather than adding to it.
- `keep_in_chat` — append the turn to chat `module-<id>` so it shows up in the chat list.
- `thinking` — ask for reasoning when the model supports it.
- `web_search` — ask for web search when the model supports it.
