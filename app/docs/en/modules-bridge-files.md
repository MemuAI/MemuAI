---
title: Files, documents, clipboard
summary: PDF and DOCX parsing, saving to Downloads, and the handoff bundle.
---

# Files, documents, clipboard

## Parsing a document

```javascript
const file = document.querySelector("#file").files[0];
const data = await readAsDataUrl(file);
const parsed = await Memu.extract({ name: file.name, data });
console.log(parsed.text);
```

`Memu.extract` understands PDF, DOCX, text and source files — the same parser the MemuAI chat uses. The answer is `{ name, text, chars, truncated }`.

The text can then go to a model:

```javascript
await Memu.request({
  model: Memu.saved.model,
  text: "Translate the document",
  documents: [{ name: parsed.name, text: parsed.text }],
});
```

Documents arrive as `<file name="...">` blocks. The model sees them as content, not as instructions.

## Saving to Downloads

```javascript
// Text: JSON, CSV, HTML, anything
await Memu.download({ name: "notes.json", text: JSON.stringify(state, null, 2) });

// An image or media file by the URL the models returned
await Memu.download({ url: result.urls[0], name: "picture.png" });
```

Files always land in `~/Downloads/MemuAI`, and a duplicate name gets a number. The answer is `{ path, name, bytes }`. The path can be offered to the person; an "Open" button calls `Memu.open`.

> A plain `<a download>` link does not work inside the app window. Always use `Memu.download`.

## The package for another AI

```javascript
const bundle = await Memu.handoff({
  prompt: "Coffee shop website design, light theme, large typography",
  images: designShots,        // data:image/...
  code: currentHtml,
  files: [{ name: "index.html", content: currentHtml },
          { name: "styles.css", content: css }]
});
```

The host saves `index.html`, the other files, `prompt.txt`, `README.md` and a JSON with the images, then returns `paths`. The module must say "package ready" only when a path actually came back:

```javascript
if (!bundle.paths.length) throw new Error("The package was not saved");
status.textContent = "Package ready: " + bundle.paths.join(" · ");
```

## Clipboard

```javascript
await Memu.copy(outputText);
```

WebView sometimes blocks a plain `navigator.clipboard`; the bridge does it reliably. Reading the clipboard back is up to the page and needs no permission beyond `clipboard`.

## Opening in the system

```javascript
await Memu.open({ url: "https://example.com" });
```

The address opens in the external Windows browser. Internal addresses and `file:` are rejected: a module cannot open a local file in the system.
