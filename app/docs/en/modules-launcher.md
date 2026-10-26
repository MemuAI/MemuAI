---
title: Button and place in the interface
summary: Four slots, hiding, order, and how the person changes them.
---

# Button and place in the interface

An enabled module gets a button. Where it sits is the author's `launcher.slot`, and the person can move it at any time.

| Slot | Where | Best for |
| --- | --- | --- |
| `sidebar` | side panel, under Planning and Skills | apps you come back to |
| `chat-list` | above the chat list | things related to conversations |
| `topbar` | top bar, near the model picker | quick tools |
| `composer` | under the message field | things that help you write |

## Hiding

`launcher.hidden` removes the button without disabling the module. Use it when a module is opened from another module or by a shortcut and the button would only get in the way.

## Order

`launcher.order` sets the position inside a slot: lower comes first. Authors set this value in their package’s `manifest.json`.

## What the person sees

Select a module in the library on the left. The right side has an enable switch, shortcut visibility, a slot picker and “Open module”. All of it lives in `state.json` and survives restarts and MemuAI updates. “Customize sidebar” controls individual module shortcuts using the same settings.

> A module button is not a menu entry and not a separate window. It is how you open the module window inside MemuAI. If the button is hidden, the window is still reachable from the settings section.
