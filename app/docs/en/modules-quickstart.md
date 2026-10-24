---
title: Your first module in five minutes
summary: Create, open, edit, disable — the whole loop.
---

# Your first module in five minutes

## 1. Create it

Open **Settings → Modules** and press "Create module". The wizard asks for an id, a title and a template. Pick "Blank" — it already has a field, a button and a model call.

The id is the internal name: lowercase latin letters, digits and hyphens, up to 41 characters, for example `my-notes`. It never changes and appears in the module's page address.

## 2. Enable and open it

Every module has a switch. While it is off, the module takes no space and cannot be opened. Turn it on and a button with the module name appears in the sidebar. The button can be hidden or moved somewhere else.

## 3. Edit the code

Press "Code" on the module card to open the workbench: HTML, CSS and JS side by side with a live preview on the right. Saving reloads the open module window, so the result is visible at once.

## 4. Check the model bridge

`main.js` in the template already has:

```javascript
const answer = await Memu.request({
  model: Memu.manifest.bridge.model,
  instructions: Memu.manifest.bridge.instructions,
  text: document.querySelector("#input").value,
});
document.querySelector("#out").textContent = answer.text;
```

`Memu.request` returns `{ text, model, chat }` — what the model answered. If no model is selected, the bridge returns a clear error instead of empty text.

## 5. Remember state

```javascript
await Memu.save({ input: input.value, draft: true });
```

Saving works only for keys listed in the module's `persist` array. Everything else is dropped silently, which keeps accidental huge writes out. The same data is in `Memu.saved` the next time the module opens.

## 6. Disable or delete

A disabled module does nothing and holds no live reference. Your own module can be deleted completely (along with its saved fields); a built-in one can only be turned off, and an edited copy of it can be reset to the shipped package.
