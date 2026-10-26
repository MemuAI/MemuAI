---
title: What a module is
summary: Your own app inside MemuAI, without plugins or external servers.
---

# What a module is

A **module** is a small application that lives inside the MemuAI window. It is written in HTML, CSS and JavaScript and never opens a separate Windows window. Everything a module needs from MemuAI — models, image generation, speech transcription, file saving — arrives through a single bridge: the `Memu` object.

That leads to the main rule: **a module has no network access**. It cannot load an external library, font or image directly. That is how the sandbox works: even if a foreign link ends up in the code, it will not load. Everything external goes through MemuAI, which decides what is allowed.

## What you can build

**Settings → Modules** shows a searchable, filterable list. Each row lets you enable or open a module. Click its name to expand its shortcut location, visibility, window size and permissions below the row; the row menu has further actions. “Create module” guides you through four steps: template, name and description, window, and permissions. The adjacent catalogue tab lets you inspect and download package source before installing it.

Use **Customize sidebar** to show or hide all module shortcuts or choose individual ones. These controls share the shortcut location and visibility settings with the module settings; they do not enable or disable the module itself.

You can also hide **Customize sidebar** itself. **Settings → Appearance → Sidebar** always provides sidebar customization and a switch to restore the button.

- A calculator, notes, a habit tracker — anything that fits in one window.
- A translator or a conversation with a model, on your own terms and instructions.
- Design preview and refinement, image and video generation, speech and transcription.
- A panel that shows your data and keeps its state between launches.

## How it is put together

A module is a folder with four required files and an optional translation dictionary:

```
my-module/
  manifest.json   description: name, window, permissions, what to remember
  index.html      markup: a fragment, not a full page
  style.css       styles for this screen only
  main.js         behavior: works with the Memu object
  i18n.json       texts in several languages (optional)
```

MemuAI assembles those files into one page and opens it in an isolated window. The module's markup, styles and code are invisible to the rest of MemuAI, and the other way around: only a message channel connects them.

> The built-in modules — Translator, MemuAI Studio and MemuAI Design — work exactly the same way. They are not baked into the chat: they are ordinary packages you can disable, open, edit or reset. Everything a built-in module can do, yours can do too.
