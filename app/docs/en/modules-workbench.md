---
title: The workbench: create and edit
summary: Wizard, templates, live preview, code checks, import and export.
---

# The workbench: create and edit

**Settings → Modules** is not a list of checkboxes; it is a workspace.

## Creating

"Create module" opens a wizard:

1. **Id and title.** The id is validated right away: a taken or invalid one is refused.
2. **Template.** Blank, Mini chat, Tool or Media. Templates give working code, not a stub.
3. **Window.** Size, bounds, fixed or resizable, whose size wins.
4. **Permissions and persistence.** Tick what the module may do and which fields it remembers.

After creation the wizard opens the new module's code so you can continue straight away.

## Editing code

The "Code" button on a card opens a workbench with three panes:

- **HTML**, **CSS**, **JS** with syntax checking;
- **Translations** — the `i18n.json` dictionaries per language, an "Add missing" button and a count of missing keys;
- **Preview** — the live module page, refreshed on save; the preview language is chosen separately so each translation can be checked;
- **Description, window, permissions, saved keys** next to the code, not in a separate dialog;
- **Package import and export** as a `.memu-module.json` file.

The workbench lists problems before you save: forgotten `persist` keys, dead external links, closing `</body>` tags in the markup.

## Built-in modules

A built-in module's code edits like any other: changes go to `overrides/<id>` and never touch the installed files. A MemuAI update therefore cannot lose your version, and "Reset to shipped" brings the original back.

## Code checks

The workbench catches the mistakes that would otherwise only appear at runtime:

| Check | What it reports |
| --- | --- |
| `</body>` and `</html>` in the markup | the markup is inserted into the page; closing tags are not needed |
| `script` and `link` with an external address | these tags will not load; a module has no network |
| `http://` and `https://` in code | use `Memu.fetch`, `Memu.download` or `Memu.open` |
| a `Memu.save` key missing from `persist` | the field will not persist |
| an empty `main.js` | the window shows only markup |
| a module that never calls a model | the Memu bridge is available if it is needed |

## Import and export

A module package is one JSON file:

```
my-notes.memu-module.json
```

Export keeps the description and all the code. Import can:

- create a module from a package;
- give it a new name when the id is already taken;
- take a package someone else built.

This works between computers and between MemuAI profiles.

## Useful actions on the card

- **Enable** — the module gets a button and can be opened.
- **Hide button** — enabled but out of the way.
- **Launcher slot** — move it between the four places.
- **Open** — the module window right from settings.
- **Code** — the workbench.
- **Duplicate** — your own copy to experiment with.
- **Clear saved fields** — reset the module's memory.
- **Reset to shipped** — restore a built-in module's original code.
- **Delete** — for your own module; a built-in one only turns off.

## Highlighting and preview size

The editor highlights HTML, CSS, JavaScript and translation JSON while preserving selection, scrolling and undo. Tab indents, Shift+Tab unindents. Open the preview separately when needed; narrow screens stack the code and preview instead of squeezing the module into a thin strip.
