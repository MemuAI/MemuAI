---
title: Custom themes
summary: The theme editor on the live window, the theme studio and CSS files: colours, fonts, layout and individual parts of the interface.
---

# Custom themes

A custom theme is an ordinary `.css` file. It joins the same list as the built-in variants and belongs to one of the modes — light or dark. Inside a theme you can change anything, from the background colour to the look of a single button.

There are two ways to make one:

- in the **theme studio** — visually, with a live preview and a readability check;
- **as a file** — in any text editor, when you want full control.

Both end in the same place: a file in the themes folder.

## The theme studio

**Settings → Appearance → Custom themes → Create theme.**

The studio shows the theme's fields on the left and a miniature MemuAI window on the right that follows every change. The whole application around it is repainted too while the studio is open, so you see the theme in the real interface.

| Field | What it sets |
| --- | --- |
| Name | the theme's name in the list |
| Mode | dark or light — which mode the theme belongs to |
| Based on | the built-in variant the theme starts from |
| Author, description | captions on the theme card |
| Colours | all main tokens, grouped by purpose |
| Custom CSS | any additional rules |

Every colour has a picker and a text field. The field takes any CSS value: `#1e1e2e`, `rgb(30 30 46)`, `color-mix(in srgb, var(--accent) 20%, transparent)`. "Back to the base value" undoes the change to one colour.

Below the colours the studio shows a **readability (WCAG)** table: contrast of body and secondary text on the background and on panels, of the accent on the background, and of text on an accent button. "Weak" means the text is hard to read, "poor" means it is barely visible.

++Ctrl+S++ saves the theme and applies it. "Back" closes the studio without saving and restores the previous look.

> [!TIP]
> Start from the base closest to what you want. A theme stores only what you changed and takes the rest from its base, so it stays short and survives MemuAI updates better.

## The theme editor

The theme editor is a mode of the main window in which you change the look right on the live interface — no trips to Settings and no guessing what a checkbox will actually change. The MemuAI window stays visible and an inspector opens beside it.

**Open it:** ++Ctrl+Shift+U++; the **⋯ → View → Theme editor** menu; the command palette ++Ctrl+K++ → “theme editor”; the “Theme editor” card in **Settings → Appearance**.

**Leave it:** ++Esc++ discards everything unsaved and restores the previous look. With many changes the editor asks first — a second ++Esc++ confirms. **Save** (or ++Ctrl+S++) keeps the changes.

The inspector has four tabs.

| Tab | What is there |
| --- | --- |
| Element | hover any part of the window — it is outlined and labelled; a click selects it. Then: fill and its opacity, text and border colour, border width, corners, shadow, background blur, font, text size and weight, padding. Colours are kept separately for the light and the dark theme, shapes are shared |
| Look | light or dark theme and its variant, accent colour, interface design (Glass included), window background — a gradient, a colour or your own picture (with dim, blur and canvas transparency), font and scale, corners, density, frosted glass, contrast, motion |
| Layout | sidebar on the left or right, full or compact, its width; the top bar style; the message box style, width and position; the conversation column width; message bubbles and the side of your own messages; panel places; top bar buttons — order and visibility |
| Themes | ready-made presets, your themes, “Save as theme”, import and export |

Handy tricks:

- **Undo / redo** — ++Ctrl+Z++ and ++Ctrl+Shift+Z++ (or ++Ctrl+Y++), or the buttons in the inspector header.
- **Before** — hold the ++\++ key or the compare button: while it is held the window shows the look from before your edits.
- **Click normally** — hold ++Alt++: clicks then go to the app itself (to open another chat, say) instead of selecting elements. The arrow button in the header turns mouse picking off entirely.
- **Reset** — changed settings and the top bar button list have a “Reset”; the edits of the selected element are cleared by its reset button.
- **Top bar buttons** are dragged by their ⋮⋮ grip; in a long list the inspector scrolls by itself when you bring the row to its edge. From the keyboard: focus a row and press ++Alt+↑++ / ++Alt+↓++. The eye on the right hides a button.
- **Where the inspector sits** — right, left or floating: use the header button or simply drag the header. A floating inspector always fits inside the MemuAI window: dragged low it gets shorter instead of going past the edge.

### Your own themes from the editor

“Save as theme” writes the current colours and element edits into an ordinary theme file (`.css`) — it works without the editor and can be edited by hand. At its end the file carries a service comment `@memu-editor`, which lets you reopen the theme in the editor and keep working on it. Older themes (made in the studio or by hand) open in the editor too: their colours are read from the `:root` block.

The **⋯** menu of a theme: rename, duplicate, export to a file, copy the code, delete.

- A **file** (`.memutheme.json`) carries the whole theme, background picture included.
- A **code** is a `MEMU-THEME-1.…` line, handy to send in a message. The background picture does not fit into a code (the editor tells you).

Import accepts a code, a `.json` file and an ordinary `.css` theme.

> [!NOTE]
> The editor accepts only safe CSS values: colours, `var()`, `color-mix()`, gradients and numbers. Values with `url(`, `;`, braces or comments are dropped — a theme from someone else's code cannot break the window.

## A theme as a file

Any `.css` file in the themes folder becomes a theme. **Themes folder** opens it; usually it is

```
%APPDATA%\MemuAI\memuai\themes
```

The file starts with a comment header and continues with ordinary CSS:

```css
/*
 * @name        Northern night
 * @base        dark
 * @extends     nord
 * @author      Anna
 * @description Nord with a warm accent and larger code
 */

:root {
  --bg: #2b303b;
  --panel: #323846;
  --accent: #ebcb8b;
  --on-accent: #2b303b;
  --code-bg: #242933;
}

.msg-assistant .body pre code {
  font-size: 14px;
}
```

| Header field | Required | Meaning |
| --- | --- | --- |
| `@name` | no, but recommended | the name in the list; the file name otherwise |
| `@base` | yes | `dark` or `light` — the theme's mode |
| `@extends` | no | id of the built-in base variant: `nord`, `mocha`, `paper` and so on |
| `@author` | no | the author |
| `@version` | no | a version |
| `@description` | no | a one-line description |

Variant ids for `@extends`: dark — `dim`, `midnight`, `nord`, `mocha`, `dusk`, `solar`, `warm`, `contrast`; light — `paper`, `cool`, `latte`, `solar`, `contrast`. Without `@extends` the default variant (Graphite or Light) is the base.

Save the file and switch back to MemuAI: the theme is re-read when the window regains focus, so you can edit in an outside editor and flip between the two.

## Why a theme rule always wins

MemuAI's built-in tokens are declared with zero specificity (through `:where()`). A plain `:root { --bg: … }` in a theme therefore overrides any built-in variant without `!important`. Theme styles load after the application's, so a rule for a specific element with equal specificity wins as well.

`!important` is needed in two cases only:

- syntax highlighting: code token colours are set as `var(--tok-*) !important` so nothing overrides them by accident — change the `--tok-*` variables themselves;
- properties the application sets in an element's `style` attribute.

## Applying, copying and moving themes

A theme card in the list offers:

- **Use** — make the theme its mode's variant;
- **Edit** — open it in the studio;
- **⋯** — duplicate, export to a file, copy the CSS, delete.

Export produces the same `.css` that sits in the folder. Import takes `.css` and `.json` (an object with `name`, `base`, `extends`, `author`, `description`, `css`). An imported theme is applied immediately.

## Limits

| What | Limit |
| --- | --- |
| size of one file | 256 KB |
| themes in the folder | 100 |
| file name | Latin letters, digits and hyphens; Cyrillic names are transliterated |

A theme is CSS only. The window's security policy blocks outside stylesheets (`@import` from another site) and web fonts. Use fonts installed in Windows or embed one as a `data:` URL. Images over `https://` do load but disappear offline — embed backgrounds as `data:` too.

## Modules and the theme

Open modules receive the current theme's colours automatically, built-in or custom. `--bg`, `--panel`, `--text`, `--accent` and the rest are available in module CSS under the same names. More: [Module design](doc:modules-design).

## When something goes wrong

- **The theme is not in the list.** Check `@base`: a theme appears only in its own mode. The file must end in `.css`.
- **A colour does not change.** Token names start with two hyphens: `--bg`, not `-bg`. The full list is in the [reference](doc:themes-reference).
- **The interface became unreadable.** Pick a built-in variant in **Settings → Appearance**, or delete the theme file from the folder — MemuAI falls back to the default variant.

See also: [Token and selector reference](doc:themes-reference), [Appearance](doc:appearance).
