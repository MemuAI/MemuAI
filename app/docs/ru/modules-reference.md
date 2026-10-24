---
title: Справочник по мосту
summary: Все методы, аргументы и ответы одной таблицей.
---

# Справочник по мосту

## Запросы

| Метод | Аргументы | Ответ |
| --- | --- | --- |
| `Memu.request` | `{model, instructions, text, images, documents, history, keep_in_chat, thinking, web_search, temperature}` | `{text, model, images, documents, keep_in_chat, chat}` |
| `Memu.stream` | то же + `{onDelta, onStatus, onDone, onError}` | промис с `cancel()` |
| `Memu.models` | — | `[{id, name, provider, image, vision, thinking, web_search}]` |
| `Memu.history` | — | `{id, title, messages}` |

## Медиа

| Метод | Аргументы | Ответ |
| --- | --- | --- |
| `Memu.image` | `{prompt, model, aspect, images}` | `{urls, ms}` |
| `Memu.video` | `{prompt, model, aspect, images}` | `{urls, ms}` |
| `Memu.speak` | `{text}` | `{audio, mime, spoken_text}` |
| `Memu.music` | `{action: "models"}` либо `{action: "generate", request_id, model, prompt, style, lyrics, duration, tempo, instrumental}`; отмена `{action: "cancel", request_id}` | `{models}` либо `{audio, mime, model, provider}` |
| `Memu.transcribe` | `{audio}` | `{text, postprocessed, postprocess_error}` |
| `Memu.record.start` / `stop` / `cancel` | — | `start`: `{recording, started}`; `stop`: `{audio, mime, bytes, ms}` |

## Данные

| Метод | Аргументы | Ответ |
| --- | --- | --- |
| `Memu.extract` | `{name, data}` | `{name, text, chars, truncated}` |
| `Memu.download` | `{name, text}` или `{url, name}` | `{path, name, bytes}` |
| `Memu.handoff` | `{prompt, images, code, files}` | `{prompt, images, files, readme, saved, paths}` |
| `Memu.copy` | `text` | `{ok}` |
| `Memu.open` | `{url}` | `{ok}` |
| `Memu.notify` | `{title, text}` | `{ok}` |
| `Memu.search` | `{query, limit}` | `{results, count}` |
| `Memu.fetch` | `{url, limit}` | `{text, title, url}` |

## Среда

| Метод | Аргументы | Ответ |
| --- | --- | --- |
| `Memu.save` | `{ключ: значение}` | полное сохранённое состояние |
| `Memu.routes` | — | `{routes}` |
| `Memu.env()` | — | среда: язык, тема, акцент, плотность, масштаб, платформа |
| `Memu.resize` | `{width, height}` | новая политика окна |
| `Memu.log` | `text, level` | — |
| `Memu.close` | — | — |

## События

| Событие | Данные |
| --- | --- |
| `theme` | `{theme, accent_name, tokens, surface, density}` |
| `host-event` | произвольное событие хоста |
| `reload` | `{reason}` |

## Значения статусов потока

`queued` — запрос принят; `working` — провайдер обрабатывает; `streaming` — идёт текст; `done` — завершено.

## Уровни журнала

`info`, `warn`, `error`. Уровень `error` попадает в журнал MemuAI.

## Тайм-ауты (миллисекунды)

| Действие | Тайм-аут |
| --- | --- |
| `request` | 300 000 |
| `stream` | без ограничения |
| `image` | 900 000 |
| `video` | 1 800 000 |
| `speak` | 180 000 |
| `music` | 700 000 |
| `monitor` | 180 000 |
| `transcribe` | 600 000 |
| `extract` | 120 000 |
| `download` | 180 000 |
| `search`, `fetch` | 60 000 |
| `save`, `models` | 20 000 |
| остальные | 5 000–30 000 |
