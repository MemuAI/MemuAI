---
title: Примеры кода
summary: Готовые куски: чат, перевод, заметки, медиа, разбор файла.
---

# Примеры кода

## Мини-чат с потоком

```html
<div class="mu-page mu-col">
  <div class="mu-scroll mu-log" id="log"></div>
  <div class="mu-composer">
    <textarea class="mu-textarea" id="text" rows="1"></textarea>
    <button class="mu-btn primary" id="send">Отправить</button>
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
  } catch (error) { last.content = "Ошибка: " + error.message; draw(); }
};
```

## Перевод с глоссарием

```javascript
const glossary = Memu.saved.glossary || {};
const rules = Object.entries(glossary).map(([from, to]) => `${from} → ${to}`).join("\n");

const answer = await Memu.request({
  model: Memu.saved.model,
  instructions: [
    "Переводи текст. Ничего не объясняй, верни только перевод.",
    "Сохраняй имена, числа, форматирование и переносы строк.",
    rules ? "Обязательный глоссарий:\n" + rules : ""
  ].filter(Boolean).join("\n"),
  text: `Переведи на ${targetLanguage}.

${sourceText}`,
});
result.value = answer.text;
```

## Заметки, которые переживают закрытие

```javascript
const field = document.querySelector("#notes");
field.value = Memu.saved.notes || "";
field.addEventListener("input", () => {
  clearTimeout(window._timer);
  window._timer = setTimeout(() => Memu.save({ notes: field.value }), 400);
});
```

## Генерация изображения с прогрессом

```javascript
status.innerHTML = '<span class="mu-spinner"></span> Рисую…';
try {
  const result = await Memu.image({ prompt, model, aspect });
  if (!result.urls.length) throw new Error("Провайдер не вернул изображение");
  stage.innerHTML = result.urls.map((url) => `<img alt="" src="${url}">`).join("");
  await Memu.download({ url: result.urls[0], name: "result.png" });
  status.textContent = "Готово и сохранено";
} catch (error) { status.textContent = error.message; }
```

## Разбор файла и отправка модели

```javascript
document.querySelector("#file").onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  status.textContent = "Читаю файл…";
  const data = await new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.readAsDataURL(file);
  });
  const parsed = await Memu.extract({ name: file.name, data });
  status.textContent = `Прочитано ${parsed.chars} символов`;
  await Memu.request({
    model: Memu.saved.model,
    text: "Разбери этот документ и выдели главное",
    documents: [{ name: parsed.name, text: parsed.text }],
  });
};
```

## Своя тема для модуля

```javascript
Memu.onTheme(({ theme, tokens }) => {
  document.body.style.background = tokens["--bg"] || "";
});
```

## Кнопка «Скопировать результат»

```javascript
document.querySelector("#copy").onclick = async () => {
  await Memu.copy(output.value);
  status.textContent = "Скопировано";
};
```
