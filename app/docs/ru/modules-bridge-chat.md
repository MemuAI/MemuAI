---
title: Запросы к модели
summary: request, stream, история, права и полная замена инструкций.
---

# Запросы к модели

## Один ответ целиком

```javascript
const answer = await Memu.request({
  model: "genspark-claude-opus-5",
  instructions: "Переводи на английский. Верни только перевод.",
  text: "Привет, мир",
  images: ["data:image/png;base64,..."],
  documents: [{ name: "brief.txt", text: "..." }],
  keep_in_chat: false,
  thinking: true,
  web_search: false,
  temperature: 0.3
});
console.log(answer.text);
```

Ответ: `{ text, model, images, documents, chat, keep_in_chat }`. Поле `chat` появляется, только если `keep_in_chat` истинно — это чат `module-<id>`, который сразу виден в списке чатов MemuAI.

## Поток

```javascript
await Memu.stream({
  model: Memu.saved.model,
  history: messages,        // [{role: "user"|"assistant", content: "..."}]
  text: "Расскажи про луну"
}, {
  onDelta(chunk) { output.textContent += chunk; },
  onStatus(status) { status.textContent = status; },
  onDone(result) { status.textContent = "Готово"; },
  onError(message) { status.textContent = message; }
});
```

`onStatus` приходит со значениями `queued`, `working`, `streaming`, `done`. Пока модель думает, хост присылает `tick`, чтобы интерфейс мог показать, что работа идёт.

`Memu.stream` возвращает промис с методом `cancel()`:

```javascript
const job = Memu.stream(body, handlers);
document.querySelector("#stop").onclick = () => job.cancel();
```

Отмена прекращает запрос у провайдера, а не только скрывает индикатор.

## История

`history` — это предыдущие ходы разговора. Мост передаёт до 40 последних записей, обрезая каждую до 200 000 символов, и всегда добавляет текущий `text` последним сообщением пользователя.

```javascript
const conversation = [
  { role: "user", content: "Сколько будет 2+2?" },
  { role: "assistant", content: "4" },
];
await Memu.stream({ history: conversation, text: "А умножить на три?" }, handlers);
```

Чтобы прочитать, что уже накопилось в чате модуля:

```javascript
const chat = await Memu.history();
console.log(chat.messages);
```

## Полная замена инструкций

`instructions` не дополняют, а заменяют системный текст целиком. Это позволяет сделать из модуля переводчика, редактора, учителя — что угодно:

```javascript
instructions: [
  "Ты редактор. Ничего не объясняешь.",
  "Возвращаешь только исправленный текст.",
  "Сохраняешь авторский стиль, имена и числа."
].join(" ")
```

Инструкции собираются из кода модуля, поэтому их можно менять по ходу работы: добавить строку про язык, про формат ответа, про тон.

## Модели

```javascript
const models = await Memu.models();
// [{ id, name, provider, image: true, vision: true, thinking: true, web_search: true }]
```

Список берётся из MemuAI и не требует ключей. Для выбора модели по умолчанию берите `Memu.manifest.bridge.model`, а если он пустой — первую модель из списка без `image: true`. Запрос совсем без модели (`model: ""`) MemuAI выполнит основной моделью чата. Выбор человека сохраняйте в `persist` (например, поле `model`).

## Чат модуля

`keep_in_chat: true` дописывает пару «запрос — ответ» в чат `module-<id>`. Такой чат:

- виден в списке чатов MemuAI;
- называется так же, как модуль;
- пополняется каждой парой сообщений;
- не смешивается с обычными разговорами.

Это удобно, когда модуль делает что-то серьёзное и результат хочется потом перечитать или продолжить в обычном чате.


## Полный массив сообщений

`Memu.complete` (Round 29) отправляет модели готовый массив сообщений с ролями `system`, `user`, `assistant` в любом порядке — как Chat Completion API — и параметры сэмплера. Нужно право `chat`. Ответ приходит потоком, как у `Memu.stream`, плюс размышления и настоящий расход токенов.

```js
const job = Memu.complete({
  model: "провайдер/модель",
  messages: [
    { role: "system", content: "Ты — рассказчик." },
    { role: "user", content: "Начни историю." },
    { role: "assistant", content: "Однажды" }          // префилл: модель продолжит
  ],
  params: { temperature: 0.9, top_p: 0.95, max_tokens: 800, presence_penalty: 0.3,
            top_k: 40, min_p: 0.05, seed: 7, stop: ["\nUser:"], effort: "high", thinking: true,
            prompt_caching: true }
}, {
  onDelta(text) { /* кусок ответа */ },
  onReasoning(text) { /* кусок размышлений */ },
  onUsage(usage) { /* prompt_tokens, completion_tokens */ }
});
const result = await job;   // { text, reasoning, usage, finish_reason, model, ms }
job.cancel();               // остановить
```

- `content` — строка или массив частей `text` / `image_url` (только `data:image/...`).
- Нейтральные значения (`top_k: 0`, штрафы `0`, `repetition_penalty: 1`, `seed: -1`) не отправляются: многие API не принимают незнакомые поля.
- Кэширование промпта включено по умолчанию там, где провайдер его поддерживает.
- Используется встроенным модулем [Персонажи](doc:characters).
