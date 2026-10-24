---
title: Appearance
summary: Light and dark modes, theme variants, the accent colour, density, conversation width and fonts.
---

# Appearance

All of MemuAI's styling lives in **Settings → Appearance**. Changes apply instantly, without a restart, and are stored in the profile.

## Mode and theme variant

MemuAI has two modes — **dark** and **light**. Each mode has its own set of variants, and the chosen variant is remembered per mode. If you use "Paper" by day and "Nord" in the evening, the mode button in the top bar brings back exactly those.

### Dark variants

| Variant | Character | Code highlighting |
| --- | --- | --- |
| Graphite | the neutral default dark | Dark+ |
| Dimmed | softer and lighter, less contrast for long sessions | Dark+ |
| Midnight | deep and blue-tinted, like editor night themes | Dark+ |
| Nord | the cool arctic Nord palette | Nord |
| Mocha | pastel in the spirit of Catppuccin Mocha | Mocha |
| Vampire | violet night with vivid syntax colours, like Dracula | Dracula |
| Solarized | classic Solarized Dark | Solarized Dark |
| Warm | coffee browns | Dark+ |
| OLED black | true black, maximum contrast, saves OLED power | Dark+ |

### Light variants

| Variant | Character | Code highlighting |
| --- | --- | --- |
| Light | the warm white default | Light+ |
| Paper | cream paper, easy on the eyes | Light+ |
| Cool | cool blue-grey | Light+ |
| Latte | pastel in the spirit of Catppuccin Latte | Latte |
| Solarized | Solarized Light on sand | Solarized Light |
| High contrast | pure white with black text | Light+ |

A variant changes more than the background: each has its own syntax palette, error and success colours and field borders. Text contrast in every variant is checked automatically and never drops below WCAG AA.

You can build your own theme on top of any of them — see [Custom themes](doc:themes).

## Accent colour

The accent colours buttons, links, the selection and active elements. There are twelve: Terracotta, Ocean, Iris, Jade, Rose, Graphite, Amber, Forest, Indigo, Plum, Crimson and Cyan.

Every accent is tuned separately for light and dark: lighter in dark mode, deeper in light mode. Text on an accent button therefore stays readable with any variant and accent.

## Interface design

The design is the shape of the whole window: typography, corners, outlines, shadows and layout. The colours (theme variant and accent) work with every design. The design also applies to the Quick Ask window.

| Design | Character |
| --- | --- |
| Classic | the original MemuAI design: a conversation canvas in a dark frame |
| Aurora | warm and editorial: soft light, large type, rounded shapes |
| Focus | minimalism: nothing but the conversation |
| Grid | a strict tool: hairlines, square corners, monospace accents |
| Glass | translucent panels over a living gradient, like modern OSes |
| Soft | Material You-style tonal surfaces: big round shapes, no outlines |
| Terminal | a phosphor console: monospace type, a ❯ prompt, scanlines |
| Brutal | bold outlines, hard offset shadows and flat colour blocks |

A design card shortens its description to three lines. Hover the card (or reach it with ++Tab++) and a tooltip shows the name and the full description.

Any text cut off with an ellipsis in the window works the same way — a long chat, model or setting value name: hovering shows a tooltip with the full text.

## Density and width

| Setting | Values | What it changes |
| --- | --- | --- |
| Density | Compact, Normal, Spacious | spacing in lists, panels and messages |
| Conversation width | Comfortable, Wide | the width of the message column |
| Font scale | 85 to 125 % | the size of all interface text |
| Compact messages | on/off | less space between messages |
| Reduce motion | on/off | turns off transitions and fades |

"Comfortable" keeps lines to about 780 pixels, which makes long answers easier to read. "Wide" helps with tables and code.

## Answer font

Model answers can be set in a book serif (the default), a modern sans, a monospace or the Windows system font. Interface, code and heading fonts come from the theme (the `--sans`, `--mono` and `--serif` tokens).

The avatar and colour for you and for the assistant are changed in the [profile](doc:profiles).

## Focus mode

++Ctrl+Shift+F++ hides the sidebar and other chrome, leaving only the conversation and the composer. Press it again to bring everything back.

## The first frame

The theme is applied before the interface loads: MemuAI caches the chosen mode, variant and accent, so there is no white flash on start in dark mode. Custom themes are cached too and appear immediately. The window itself is also created with the background of the chosen variant (OLED, Nord, Sepia or your own theme), so no grey rectangle flashes before the first paint.

Changing a value in the settings (a model, a switch, a list) does not redraw the page: the scroll position, open sections and focus stay put, and the window does not "blink".

See also: [Custom themes](doc:themes), [Token and selector reference](doc:themes-reference).
