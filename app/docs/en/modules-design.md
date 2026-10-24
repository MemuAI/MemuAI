---
title: Styling and the shared layer
summary: MemuAI tokens, mu-* classes, dark and light themes, accessibility.
---

# Styling and the shared layer

A module receives every MemuAI design token. Use them and the module automatically matches the theme, accent and density of the interface.

## Tokens

| Token | Meaning |
| --- | --- |
| `--bg`, `--bg-deep` | window background and nested areas |
| `--panel`, `--panel-2` | card and field surfaces |
| `--border`, `--control-border` | borders |
| `--text`, `--muted` | primary and secondary text |
| `--accent`, `--accent-soft`, `--on-accent` | accent, its soft fill and text on it |
| `--danger` | errors and destructive actions |
| `--radius`, `--radius-sm`, `--radius-md`, `--radius-lg` | corners |
| `--shadow` | window and menu shadow |
| `--sans`, `--serif`, `--mono` | fonts |
| `--font-scale` | the text scale the person chose (85–125) |

```css
.my-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
}
.my-card .hint { color: var(--muted); }
.my-card button { background: var(--accent); color: var(--on-accent); }
```

Never hardcode a background and text colour: in the opposite theme such a module becomes unreadable.

## Ready-made `mu-*` classes

The shared layer already brings components, so you do not have to rewrite them:

| Class | What it gives |
| --- | --- |
| `mu-page` | padded scrolling page |
| `mu-col` | full-height vertical column |
| `mu-toolbar` | a top tool strip |
| `mu-card` | a card surface |
| `mu-title`, `mu-sub`, `mu-muted` | heading and captions |
| `mu-field` | label plus field |
| `mu-input`, `mu-select`, `mu-textarea` | MemuAI fields |
| `mu-btn` + `.primary` / `.ghost` / `.danger` / `.small` | buttons |
| `mu-tabs` + `mu-tab` | section switcher |
| `mu-badge`, `mu-note`, `mu-empty` | badge, hint, empty state |
| `mu-row`, `mu-grid`, `mu-grow` | layout |
| `mu-stage`, `mu-thumb` | media stage |
| `mu-log`, `mu-msg` | conversation |
| `mu-composer` | input with a button |
| `mu-spinner`, `mu-progress` | waiting and progress |

A whole form with no CSS at all:

```html
<div class="mu-page">
  <div class="mu-title">Notes</div>
  <label class="mu-field">Text<textarea class="mu-textarea" id="text"></textarea></label>
  <div class="mu-row">
    <button class="mu-btn primary" id="save">Save</button>
    <span class="mu-grow"></span>
    <span class="mu-muted" id="status"></span>
  </div>
</div>
```

## Theme and language

```javascript
document.documentElement.dataset.theme;      // "dark" | "light"
document.documentElement.dataset.accent;     // "terracotta", "blue", ...
document.documentElement.dataset.density;    // "normal" | "compact" | "spacious"
Memu.env.lang;                               // "ru" | "en"
```

Subscribe to theme changes when you draw on canvas or set colours in code:

```javascript
Memu.onTheme((theme) => drawChart(theme.tokens["--text"]));
```

## Accessibility

- Every control should be a real `button`, `input` or `select` so it works with the keyboard and with a screen reader.
- Give icon-only buttons an `aria-label`.
- Never rely on colour alone: back an error up with text.
- The shared layer already honours `prefers-reduced-motion` and draws a visible focus ring.

## Your own accent

If a module needs its own colour, declare it in the description:

```json
"accent": "#4f8ad8"
```

The host applies it to the module page's `--accent` and `--accent-soft`. The rest of MemuAI is unaffected.
