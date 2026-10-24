---
title: When something does not work
summary: Common symptoms, causes and what to do.
---

# When something does not work

## The module window is empty

**Cause.** The package has no `index.html` and `main.js`, or the markup is empty.

**What to do.** Open "Code" and check the markup and script are there. If it is a built-in module you have not edited, press "Reset to shipped": the original package is restored.

## Styles are not applied

**Cause.** The selector does not match the markup, or a colour is hardcoded and invisible in the current theme.

**What to do.** Use the `--bg`, `--panel`, `--text`, `--muted`, `--accent` tokens instead of literal colours. Check both modes, light and dark.

## No button appeared

- The module is disabled — turn the switch on.
- "Hide button" is ticked — clear it.
- The module is pinned to a different slot — check the top bar and the composer area.

## "The module is not allowed…"

The module lacks a permission. Open "Code", find the permissions list and add what is needed: `chat` for model requests, `download` for saving files, and so on.

## The model does not answer

- No model is selected: the bridge returns "Choose a model".
- No account is connected in MemuAI Proxy — check the Proxy panel.
- The model cannot do what you ask (image generation, say). Pick one from `Memu.models()` with the capability you need.

## Saving does not work

Check the key is listed in the module's `persist` array. The workbench warns about this before you save the code.

## An external link or library does not load

That is intended: a module has no network access. Use `Memu.fetch`, `Memu.search` or `Memu.request` to get data.

## "Package ready" never appears

The host saves the files and returns `paths`. Check that you show the result only when `paths` is non-empty, and that the module has the `download` permission.

## An error in the module code

Check the log: `Memu.log(text, "error")` and uncaught errors arrive there automatically. Developer tools are not available in the app window, so the log is the main way to see a problem.

## The window opens somewhere else

If the description has `"center": true`, the window always opens centred. Remove that field to remember the position.

## The module broke after a MemuAI update

An update does not touch your modules or your edits to built-ins: they live in profile data. If the shared style layer changed, check whether you override its `mu-*` classes — those belong to the host.
