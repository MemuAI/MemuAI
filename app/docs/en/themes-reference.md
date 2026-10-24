---
title: Token and selector reference
summary: Every MemuAI styling variable and the stable selectors of the main parts of the window.
---

# Token and selector reference

A token is a CSS variable the interface is painted with. Changing a token in a theme changes the colour everywhere it is used. For targeted changes there are selectors — the ones listed below stay the same between versions.

## Main tokens

Every built-in variant sets these. The theme studio shows them too.

### Surfaces

| Token | Where it is used |
| --- | --- |
| `--bg` | the main window and chat background |
| `--bg-deep` | a deeper background: sidebar, wells |
| `--panel` | panels, cards, popovers, the composer |
| `--panel-2` | a secondary surface: hover, the user bubble |
| `--border` | hairline dividers and frames |
| `--control-border` | input and button borders (stronger than `--border`) |

### Text

| Token | Where it is used |
| --- | --- |
| `--text` | body text |
| `--muted` | secondary text, captions, icons |

### Accent

| Token | Where it is used |
| --- | --- |
| `--accent` | buttons, links, the active element |
| `--on-accent` | text on an accent fill |
| `--accent-soft` | a translucent accent wash |

Normally `--accent` is derived from the chosen accent colour. When a theme sets `--accent` itself, the accent choice in settings no longer affects it.

### States

| Token | Where it is used |
| --- | --- |
| `--danger` | errors and destructive actions |
| `--ok` | success and the connected state |

### Code

| Token | Where it is used |
| --- | --- |
| `--code-bg` | code block background |
| `--code-text` | plain code text |
| `--code-header` | the code block header: language, copy button |

### Effects

| Token | Where it is used |
| --- | --- |
| `--shadow` | shadow of large floating panels |
| `--composer-shadow` | composer shadow |

## Additional tokens

Built-in variants leave these unset: each falls back to a main token. A theme can set any of them to change one part of the interface without touching the rest.

| Token | Default | Where it is used |
| --- | --- | --- |
| `--side-bg` | `var(--bg-deep)` | sidebar background |
| `--topbar-bg` | `var(--bg)` | top bar background |
| `--composer-bg` | `var(--panel)` | composer background |
| `--user-bubble` | `var(--panel-2)` | user message background |
| `--user-bubble-text` | `var(--text)` | user message text |
| `--link` | `var(--accent)` | links in answers |
| `--selection` | accent at 42 % opacity | selected text background |
| `--scroll-thumb` | text at 22 % | scrollbar thumb |
| `--scroll-thumb-hover` | accent mixed with text | thumb under the pointer |
| `--hover` | grey at 16 % | list row under the pointer |
| `--accent-2` | the accent's partner hue | second stop of the brand gradient: the MemuAI mark, the send button, the Live orb, research progress |
| `--brand-gradient` | gradient `--accent` → `--accent-2` | the mark, the send button, the new-chat icon |
| `--chrome-bg` | `var(--bg-deep)` | the window background around the conversation canvas and under the sidebar |
| `--canvas-bg` | `var(--bg)` | the conversation canvas — the raised sheet with the chat |
| `--canvas-border` | `--border`, 80 % | the thin outline of the canvas, menus and popovers |
| `--terminal-bg` | `#0f1115` | MemuCode terminal background |
| `--terminal-text` | `#d7dae0` | MemuCode terminal text |
| `--mc-add` | green | added lines in MemuCode changes |
| `--mc-del` | red | removed lines in MemuCode changes |
| `--tooltip-bg` | panel, slightly lighter | tooltip background |
| `--tooltip-text` | `var(--text)` | tooltip text |

## Shape and fonts

| Token | Default | Meaning |
| --- | --- | --- |
| `--radius` | `12px` | the main corner radius |
| `--radius-sm` / `--radius-md` / `--radius-lg` | `8px` / `12px` / `16px` | small, medium and large corners |
| `--conversation-width` | `780px` (wide — `1080px`) | conversation column width |
| `--sans` | Segoe UI Variable | interface font |
| `--serif` | Tiempos Headline, Charter | heading font |
| `--font` | Charter | answer font when the serif option is chosen |
| `--mono` | Cascadia Code, Consolas | code font |

## Syntax highlighting

Every variant has its own highlighting palette. Override the variables you need — the rest stay from the base:

| Token | What it colours |
| --- | --- |
| `--tok-kw` | keywords: `if`, `return`, `class` |
| `--tok-type` | types and classes |
| `--tok-str` | strings |
| `--tok-com` | comments |
| `--tok-num` | numbers |
| `--tok-fn` | function names |
| `--tok-op` | operators |
| `--tok-lit` | `true`, `false`, `null` |
| `--tok-tag` | HTML and XML tags |
| `--tok-attr` | tag attributes |
| `--tok-prop` | CSS properties and object keys |
| `--tok-var` | variables |
| `--tok-meta` | directives, decorators |
| `--tok-punct` | brackets and punctuation |

```css
:root {
  --tok-kw: #ff79c6;
  --tok-str: #f1fa8c;
  --tok-com: #6272a4;
}
```

## Root element attributes

MemuAI marks its current state with attributes on `<html>`. A theme can use them to behave differently in different situations.

| Attribute | Values |
| --- | --- |
| `data-theme` | `dark`, `light` |
| `data-surface` | the id of the built-in variant or of a custom theme's base |
| `data-custom-theme` | the slug of the active custom theme |
| `data-accent` | `terracotta`, `blue`, `violet`, `teal`, `rose`, `graphite`, `amber`, `forest`, `indigo`, `plum`, `crimson`, `cyan` |
| `data-density` | `compact`, `spacious` (absent — normal) |
| `data-chat-width` | `comfortable`, `wide` |

```css
/* The Forest accent is a little brighter in this theme */
:root[data-accent="forest"] { --accent: #7ee08a; }
```

## Stable selectors

| Selector | What it is |
| --- | --- |
| `aside.side` | the sidebar |
| `.side-btn` | section buttons in the sidebar (Chat, Work, MemuCode…) |
| `.chat-item`, `.chat-item.active` | a chat row in the list, the current chat |
| `header.topbar` | the top bar |
| `#model-dd-btn` | the model picker button |
| `#messages` | the message feed |
| `.msg-user .bubble` | a user message |
| `.msg-assistant .body` | the text of a model answer |
| `.msg-assistant .who` | the line with the model name above an answer |
| `.code-block-wrap` | a code block in an answer |
| `.composer` | the composer with its buttons |
| `#input` | the text field itself |
| `.pbtn`, `.pbtn.ghost`, `.pbtn.danger` | buttons in panels and settings |
| `#panel` | the settings side panel |
| `.memu-tooltip` | a tooltip |
| `#code-dock` | the MemuCode right panel |
| `.module-window` | a module window |

> [!WARNING]
> Selectors missing from this table are internal. They work, but may change in the next MemuAI version. If a token can do the job, use the token.

## Examples

### A coloured sidebar

```css
:root {
  --side-bg: #1b2a22;
}
.side-btn.active-section { background: color-mix(in srgb, var(--accent) 18%, transparent); }
```

### The user bubble in the accent colour

```css
:root {
  --user-bubble: var(--accent);
  --user-bubble-text: var(--on-accent);
}
```

### Square corners everywhere

```css
:root {
  --radius: 0px;
  --radius-sm: 0px;
  --radius-md: 0px;
  --radius-lg: 0px;
}
```

### A pattern behind the feed

```css
#messages {
  background:
    linear-gradient(color-mix(in srgb, var(--bg) 86%, transparent), color-mix(in srgb, var(--bg) 86%, transparent)),
    url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24'><circle cx='2' cy='2' r='1' fill='%23888'/></svg>");
}
```

See also: [Custom themes](doc:themes), [Appearance](doc:appearance).
