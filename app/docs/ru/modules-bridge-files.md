---
title: Файлы, документы, буфер
summary: Разбор PDF и DOCX, сохранение в загрузки, пакет для другой ИИ.
---

# Файлы, документы, буфер

## Разбор документа

```javascript
const file = document.querySelector("#file").files[0];
const data = await readAsDataUrl(file);
const parsed = await Memu.extract({ name: file.name, data });
console.log(parsed.text);
```

`Memu.extract` понимает PDF, DOCX, текстовые и исходные файлы — тем же разбором, что и обычный чат MemuAI. Ответ: `{ name, text, chars, truncated }`.

Затем текст можно передать модели:

```javascript
await Memu.request({
  model: Memu.saved.model,
  text: "Переведи документ",
  documents: [{ name: parsed.name, text: parsed.text }],
});
```

Документы попадают в запрос как блоки `<file name="...">`. Модель видит их как содержимое, а не как инструкцию.

## Сохранение в загрузки

```javascript
// Текст: JSON, CSV, HTML, что угодно
await Memu.download({ name: "notes.json", text: JSON.stringify(state, null, 2) });

// Картинка или медиа — по ссылке, полученной от моделей
await Memu.download({ url: result.urls[0], name: "picture.png" });
```

Файл всегда попадает в `~/Downloads/MemuAI`, имя при совпадении получает номер. Возвращается `{ path, name, bytes }`. Путь можно показать человеку как ссылку — кнопка «Открыть» вызывает `Memu.open`.

> Обычная ссылка `<a download>` внутри окна приложения не работает. Всегда используйте `Memu.download`.

## Пакет для другой ИИ

```javascript
const bundle = await Memu.handoff({
  prompt: "Дизайн сайта кофейни, светлая тема, крупная типографика",
  images: designShots,        // data:image/...
  code: currentHtml,
  files: [{ name: "index.html", content: currentHtml },
          { name: "styles.css", content: css }]
});
```

Хост сохраняет `index.html`, остальные файлы, `prompt.txt`, `README.md` и JSON с изображениями, а затем возвращает `paths`. Модуль должен сказать «пакет готов» только когда путь реально получен:

```javascript
if (!bundle.paths.length) throw new Error("Пакет не сохранён");
status.textContent = "Пакет готов: " + bundle.paths.join(" · ");
```

## Буфер обмена

```javascript
await Memu.copy(outputText);
```

WebView иногда не даёт обычному `navigator.clipboard` работать; мост делает это надёжно. Обратно из буфера читает сама страница — на это прав, кроме `clipboard`, не нужно.

## Открыть в системе

```javascript
await Memu.open({ url: "https://example.com" });
```

Адрес открывается во внешнем браузере Windows. Внутренние адреса и `file:` отклоняются: модуль не может открыть локальный файл в системе.
