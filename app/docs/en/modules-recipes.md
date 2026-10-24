---
title: Recipes
summary: Common tasks and how to solve them without extra code.
---

# Recipes

## A module opened only from another module

```json
"launcher": { "hidden": true, "slot": "sidebar" }
```

The module stays enabled but shows no button. The person opens it from settings.

## A window that never drags

```json
"window": { "default_width": 520, "default_height": 620, "fixed": true, "draggable": false }
```

Good for panels with a fixed grid: no time spent adjusting.

## Always open at the author's size

```json
"window": { "size_mode": "authored" }
```

The person can still resize, but the next open returns to the authored size — useful when the layout must be predictable.

## A chat inside a module

Set `"keep_in_chat": true` in the description and pass `keep_in_chat: true` in the request. Message pairs start accumulating in chat `module-<id>`, visible in the ordinary chat list.

## Cancelling a long generation

```javascript
let job = null;
document.querySelector("#go").onclick = () => {
  job = Memu.stream(body, handlers);
};
document.querySelector("#stop").onclick = () => job && job.cancel();
```

For images and video the bridge also handles window close: an unfinished request is stopped.

## Showing an error like a human

```javascript
function showError(error) {
  status.textContent = error.message || "Unknown error";
  status.classList.add("mu-note", "warn");
}
```

The bridge already supplies text in the interface language; nothing more is needed.

## Saving without waste

Store references and small settings, not content. Huge data URLs in `persist` make closing a module slow.

## Checking that the module runs inside MemuAI

```javascript
if (!window.Memu) {
  document.body.textContent = "This screen only works inside MemuAI.";
}
```

That way the package can be opened in a browser to debug layout and shows a clear message instead of a blank page.

## Two languages

```javascript
const t = (ru, en) => (Memu.env.lang === "en" ? en : ru);
document.querySelector("#title").textContent = t("Notes", "Notes");
```
