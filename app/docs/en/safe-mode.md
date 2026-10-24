---
title: Safe mode
summary: Start MemuAI without modules, plugins, custom themes, MCP servers and the companion — when an extension broke something. Your data is unchanged.
---

# Safe mode

If MemuAI behaves oddly, doesn't open or crashes after you installed a module, a Quick Ask plugin or a custom theme, start it in **safe mode**. It is an almost «clean» start: everything that extends the program is off for this start only. Chats, settings, modules and themes are not changed or deleted — after a normal restart everything comes back.

## How to turn it on

- **Settings → About → Safe mode → Restart in safe mode**.
- The **MemuAI (safe mode)** shortcut in the Start menu (the installer creates it).
- Hold ++Shift++ while MemuAI starts — it asks whether to turn safe mode on.
- If MemuAI failed to open its window twice in a row, it offers safe mode itself.
- For advanced users: `MemuAI.exe --safe-mode` or the `MEMUAI_SAFE_MODE=1` environment variable.

**Start in safe mode** remembers the choice: **Once** — the next start only, **Always** — until you turn it off.

## What is turned off

- modules (the list is empty, module background jobs in MemuAI Proxy are paused);
- Quick Ask plugins and your own Quick Ask routines;
- MCP servers;
- the desktop companion and the companion next to Live;
- the wake word;
- scheduled panel refreshes;
- custom themes, user CSS and alternative designs — the built-in look is used;
- automatic [Sync](doc:sync) backups (manual ones still work).

Chat, models, Work, MemuCode, settings and the documentation work as usual.

## How to leave it

A **Safe mode** banner is shown at the top of the window. **What is off** shows the list, **Restart normally** restarts MemuAI with all extensions. The same buttons are in **Settings → About**.

## Finding the culprit

Restart MemuAI normally and turn extensions off one at a time: modules in [Modules](doc:modules), plugins in [Quick Ask](doc:quick-ask), themes in [Appearance](doc:appearance). When the problem goes away, the last thing you turned off is the culprit. If data got damaged, bring it back from a [backup](doc:sync#restore).
