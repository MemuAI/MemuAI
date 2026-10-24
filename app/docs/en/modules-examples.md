---
title: Code examples
summary: Working pieces: chat, translation, notes, media, file parsing.
---

# Code examples

## Mini chat with streaming

```html
<div class="mu-page mu-col">
  <div class="mu-scroll mu-log" id="log"></div>
  <div class="mu-composer">
    <textarea class="mu-textarea" id="text" rows="1"></textarea>
    <button class="mu-btn primary" id="send">Send</button>
  </div>
</div>
```

```javascript
let history = Array.isArray(Memu.saved.history) ? Memu.saved.history : [];
const log = document.querySelector("#log"), text = document.querySelector("#text");

function draw() {
  log.innerHTML = history
    .map((m) => `<div class="mu-msg ${m.role === "user" ? "me" : ""}">${escapeHtml(m.content)}</div>`)
    .join("");
  log.scrollTop = log.scrollHeight;
}
const escapeHtml = (v) => String(v).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
draw();

document.querySelector("#send").onclick = async () => {
  const value = text.value.trim();
  if (!value) return;
  text.value = "";
  history.push({ role: "user", content: value });
  const last = { role: "assistant", content: "" };
  history.push(last); draw();
  try {
    await Memu.stream({ model: Memu.saved.model, history, text: value }, {
      onDelta(chunk) { last.content += chunk; draw(); },
    });
    await Memu.save({ history: history.slice(-40) });
  } catch (error) { last.content = "Error: " + error.message; draw(); }
};
```

## Translation with a glossary

```javascript
const glossary = Memu.saved.glossary || {};
const rules = Object.entries(glossary).map(([from, to]) => `${from} → ${to}`).join("\n");

const answer = await Memu.request({
  model: Memu.saved.model,
  instructions: [
    "Translate the text. Explain nothing, return only the translation.",
    "Keep names, numbers, formatting and line breaks.",
    rules ? "Required glossary:\n" + rules : ""
  ].filter(Boolean).join("\n"),
  text: `Translate into ${targetLanguage}.

${sourceText}`,
});
result.value = answer.text;
```

## Notes that survive closing

```javascript
const field = document.querySelector("#notes");
field.value = Memu.saved.notes || "";
field.addEventListener("input", () => {
  clearTimeout(window._timer);
  window._timer = setTimeout(() => Memu.save({ notes: field.value }), 400);
});
```

## Image generation with progress

```javascript
status.innerHTML = '<span class="mu-spinner"></span> Drawing…';
try {
  const result = await Memu.image({ prompt, model, aspect });
  if (!result.urls.length) throw new Error("The provider returned no image");
  stage.innerHTML = result.urls.map((url) => `<img alt="" src="${url}">`).join("");
  await Memu.download({ url: result.urls[0], name: "result.png" });
  status.textContent = "Done and saved";
} catch (error) { status.textContent = error.message; }
```

## Parse a file and send it to a model

```javascript
document.querySelector("#file").onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  status.textContent = "Reading the file…";
  const data = await new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.readAsDataURL(file);
  });
  const parsed = await Memu.extract({ name: file.name, data });
  status.textContent = `Read ${parsed.chars} characters`;
  await Memu.request({
    model: Memu.saved.model,
    text: "Analyse this document and highlight the essentials",
    documents: [{ name: parsed.name, text: parsed.text }],
  });
};
```

## A custom theme reaction

```javascript
Memu.onTheme(({ theme, tokens }) => {
  document.body.style.background = tokens["--bg"] || "";
});
```

## A copy-result button

```javascript
document.querySelector("#copy").onclick = async () => {
  await Memu.copy(output.value);
  status.textContent = "Copied";
};
```
