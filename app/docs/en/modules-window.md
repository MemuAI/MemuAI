---
title: The module window
summary: Size, dragging, fullscreen, and what the author decides versus the person.
---

# The module window

Normally the window lives inside MemuAI, so you can keep working in MemuAI next to an open module. But any module can also open **in a separate Windows window** or be **installed as a separate app** with its own shortcut — see [below](#a-separate-window-and-a-module-as-an-app).

## The author sets the bounds

```json
"window": {
  "default_width": 880,
  "default_height": 560,
  "min_width": 320,
  "min_height": 240,
  "max_width": 1600,
  "max_height": 1200,
  "fixed": false,
  "resizable": true,
  "size_mode": "user",
  "center": true
}
```

- `default_width` / `default_height` — the size the author had in mind. From 240×200 to 2400×1600.
- `min_*` / `max_*` — the bounds the person cannot drag past.
- `fixed: true` — the authored size always wins and the edge cannot be dragged. Studio works that way on purpose.
- `resizable` — whether the size can change when the window is not fixed (yes by default).
- `size_mode` — `user` prefers the size the person chose last time; `authored` always reopens the authored size.
- `center: true` — the window opens centred instead of where it was left.

## One header

The window has a single title row shared by MemuAI and the module: the module icon and name, the module's subtitle (say, the open chat), the module's actions, then ⋯, “— Minimize”, “expand” and “× Close”. The ⋯ menu holds "Reload the module", "Window layout…" and the module settings. An expanded sheet has no row of its own: all of this moves into the MemuAI top bar, so there is still one header above the module, not three rows of buttons.

**Do not paint your own header.** The window already shows the module logo and name; hand the module's buttons to [`Memu.toolbar`](doc:modules-bridge) — `set([...])` for a new module or `adopt({...})` for an existing bar. Keep only what belongs to the content inside the page: tabs, search, filters. Keep your own bar for the bench and Telegram and hide it under `:root[data-host-toolbar="1"]`. The workbench warns when `index.html` has a `<header>` and the code never touches `Memu.toolbar`.

Scrollbars belong only to what really scrolls: `overflow: auto` on a list or the content area, not on a strip of tabs or tags. Give horizontal ribbons `class="mu-hscroll"` (or `mu-tabs`): the bar is hidden and the mouse wheel scrolls the ribbon sideways — the bridge does that.

## Minimize and close

The module's buttons sit together in one capsule in its header, always in the same order — like any window: **⋯**, **— Minimize**, **expand**, **× Close**. When the header has room, the words “Minimize” and “Close” are written next to the icons; a narrow window keeps the icons, and the tooltip still says what each button does.

- **— Minimize** (or Esc) — the module goes to the module bar at the bottom of MemuAI and **keeps running**: an answer keeps streaming, a recording keeps going. Click it on the bar to bring it back.
- **× Close** (or Ctrl+W) — the module window closes, its saved data stays. If the module is still working or has unsaved changes, MemuAI asks first.
- The × in the very corner of the window closes **MemuAI**, not the module (what happens to the modules then — see [below](#when-you-close-memuai)).

“Minimize” used to be an arrow into a line and looked exactly like the “Download” button of many modules — it is now the bar every window uses for minimizing.

## A separate window and a module as an app

The ⋯ menu of a module window, the right-click menu of a module button and the module settings (⋯ on a card) have two items:

- **Open in a separate window** — the module moves into its own Windows window: with its own button and icon on the taskbar; put it on another monitor, minimize and maximize it with the Windows buttons. Models, data and settings are the same as in MemuAI. To move it back — ⋯ → “Move back into MemuAI”; to open MemuAI itself — the ✦ button in the module window header.
- **Install as an app…** — the module becomes a separate program: a “Name (MemuAI)” shortcut with the module's icon appears in the Start menu (and on the desktop if you like). It runs `MemuAI.exe --module <id>` — just the module window, without the main MemuAI window. If MemuAI is already running, the window opens in it — there is no second copy of the program. If MemuAI was not running, it starts quietly (the tray icon stays, Quick Access works), and when you close the last module window without ever opening MemuAI itself, the program exits completely. The module can be removed from the MemuAI module list — then it lives only as an app. The list of installed apps, their shortcuts and removal — **Settings → Modules → “Modules as apps”**.

A pinned taskbar button of a module window starts that very module, too. The shortcuts repair themselves when MemuAI starts if the program was moved or the module was deleted.

> Shortcuts and a taskbar button of its own exist on Windows only. In a browser (development mode) the “separate window” is a browser popup.

For a module author nothing changes in a separate window: it is the same bridge, the same header (`Memu.toolbar`), the same storage. `Memu.close()` closes the window, and “Minimize” minimizes it to the Windows taskbar.

## When you close MemuAI

While at least one module is open, the MemuAI × (and Alt+F4, and “Close window” on the taskbar) **no longer silently takes the modules away**. MemuAI asks:

- **Keep the modules open** — each module moves to its own window and MemuAI goes to the tray as usual;
- **Close the modules and MemuAI** — as before: the modules close, their data is kept;
- **Cancel** — close nothing.

The “Remember my choice and don't ask again” box saves the answer; change it in **Settings → Modules → “When you close MemuAI while modules are open”** (Ask me / Keep the modules open / Close the modules with MemuAI). A module that is generating right now or has unsaved changes always asks — moving to its own window reopens it, and an unfinished answer would be lost. “Выключить” (Shut down) in the tray icon menu exits MemuAI together with every module window without a question.

## Dragging

The window can be dragged by its top bar when at least one is true:

- it is resizable;
- the author set `"draggable": true`;
- the window is small — neither side above 620×460.

A small or resizable window floats above the chat, so MemuAI stays usable next to it. A large fixed window (Studio) does not drag: its size is already the author's choice.

An explicit `"draggable": false` disables dragging for good, even for a resizable window.

## Fullscreen

Any window expands from the top-right button and comes back the same way. The expanded window is a sheet inside the MemuAI content area next to the sidebar: a small margin, rounded corners and a shadow, always below the top bar and the Windows caption buttons. Size and position are remembered separately: after collapsing you get exactly what you had.

**Esc collapses the expanded window** — both on MemuAI itself and inside the module. If your module uses Esc itself (closes its own dialog, clears a search), call `event.preventDefault()` in its `keydown` handler: such a press is not forwarded to the host and the window stays expanded. Presses in input fields and while a `<dialog>` is open are not forwarded either.

On every window state change the module receives the `window` event:

```javascript
Memu.on("window", ({ state }) => {
  // "floating" — normal window, "snapped" — docked to an edge, "expanded" — expanded
  document.body.classList.toggle("wide", state === "expanded");
});
```

## What the host remembers

- size, position and fullscreen state, per module;
- whether the module is open right now, so it can be restored on the next launch;
- the size of a module's separate window and the installed apps — in `modules/apps.json`.

Geometry is written to `state.json` and never into the package. Editing code or resetting a module does not reset its window.

## Working with the window from code

```javascript
// How much room the window has right now
const width = window.innerWidth;

// Ask the host for a different size (for example when a panel expands)
await Memu.resize({ width: 900, height: 640 });
```

Layout must survive resizing: use `flex`/`grid` and `min-height: 0` instead of fixed heights. The shared `mu-*` styles already do that.

## Full screen — focus mode

While a module fills the MemuAI area, the interface behind it is "paused": the content it covers can't be reached with the keyboard and the sidebar is dimmed. If you reach for MemuAI anyway — click the sidebar, open settings, search (Ctrl+K) or another window — the module smoothly **steps aside** into its pill on the module bar (marked ⤢). When the window it stepped aside for (settings, search) is closed — Esc, × or the same shortcut — the module **comes back** full screen by itself. If you clicked away to another section, the module waits in its pill: one click brings it back full screen.

Main-window shortcuts work while the focus is inside the module too: if the module doesn't handle Ctrl+K, MemuAI opens search and the module steps aside. Notifications, module questions and confirmations always show above the module and never minimize it.

## A module app's own title bar

A module opened in its own window or installed as an app gets its own title bar in its colours instead of the Windows frame: the name, the module's actions, Minimize / Maximize / Close. Drag the empty part of the bar to move the window, double-click to maximize, right-click or Alt+Space for the system menu; hovering Maximize on Windows 11 shows Snap Layouts; edges and corners resize as usual.

To turn it off: Settings → Modules → "Modules as apps" → "Own window title bar" (all apps) or the "Own title bar" box of one app; the same is in the module window's ⋯ menu. The change applies the next time the window opens.

For module authors — in the manifest:

```json
"window": { "appbar": { "style": "transparent", "height": 36, "text": "#ffffff" } }
```

- `style`: `auto` (a bar in the theme colours, default), `transparent` (the bar floats over the module — its background runs to the very top), `custom` (your `color` and `text`), `none` (no bar, only the window buttons).
- `height` 28–64, `color`/`text` — `#rrggbb`, `overlay: true` — the bar floats over the content.
- At run time: `Memu.appbar.set({title, actions, color, text, style, caption})`, `Memu.appbar.state()` → `{active, height, overlay, style}`, `Memu.appbar.on(fn)`.
- `caption: false` removes the window icon and name from the bar when the module draws its own name (for example, a logo in its sidebar); the window buttons and dragging stay. Every `set` call defines the colours, style and `caption` as a whole: a field you leave out goes back to its default. Call `set` only on a real change (theme, light/dark screen), not on every frame.
- Any element with `data-memu-drag` moves the window (double-click maximizes); buttons and fields inside it keep working, `data-memu-drag="false"` opts out.
- When the bar floats over the content, the module document gets `--memu-appbar-h` (its height) and `html[data-memu-appbar="overlay"]` — move your top buttons down: `padding-top: var(--memu-appbar-h, 0)`.
