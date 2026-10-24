---
title: Изображения, видео, звук
summary: Генерация медиа, ошибки провайдера и как их показывать.
---

# Изображения, видео, звук

## Изображения

```javascript
const result = await Memu.image({
  prompt: "Кофейня у моря, тёплый свет, вечер",
  model: "genspark-image/fal-ai/nano-banana",
  aspect: "16:9",
  images: []          // необязательные референсы, data:image/...
});
result.urls.forEach((url) => {
  const img = new Image();
  img.src = url;                      // уже data-URL, доступный в песочнице
  stage.appendChild(img);
});
```

Результат: `{ urls, ms }`. Каждая ссылка приходит уже встроенной как data-URL, поэтому песочница её показывает — обычный внешний адрес тут не сработал бы.

`aspect`: `auto`, `16:9`, `1:1`, `9:16`, `4:3`, `3:2`.

## Видео

```javascript
const result = await Memu.video({
  prompt: "Волна разбивается о камни, замедленная съёмка",
  model: "auto",
  aspect: "16:9",
  images: [reference]
});
```

Работает тем же конвейером Genspark, что и изображения. Учтите, что видео генерируется долго — держите в интерфейсе видимый статус и таймер. Тайм-аут моста для видео — 30 минут.

## Аудио

```javascript
const speech = await Memu.speak({ text: "Доброе утро" });
const audio = new Audio(speech.audio);   // data:audio/...
audio.play();
```

Способ озвучки берётся из настроек MemuAI: локальные голоса Windows, скачанная модель или TTS API. Модулю не нужно об этом знать.

```javascript
const text = await Memu.transcribe({ audio: dataUrl });
// { text, postprocessed, postprocess_error }
```

Голосовой файл передаётся как data-URL. Размер ограничен 35 МБ, а распознавание использует настройки из раздела «Голос и Live».

Запись с микрофона ведёт окно MemuAI: страница модуля работает в песочнице, и браузер не даёт ей микрофон напрямую. Нужно право `media`:

```javascript
await Memu.record.start();               // MemuAI спросит доступ к микрофону
const clip = await Memu.record.stop();   // { audio: "data:audio/webm;...", mime, bytes, ms }
const { text } = await Memu.transcribe({ audio: clip.audio });
// Memu.record.cancel() прерывает запись без результата; закрытие окна — тоже.
```

## Ошибки провайдера

Генерация идёт потоком, и **ошибка приходит отдельным кадром при успешном ответе сервера**. Мост уже разбирает такие кадры и отклоняет промис с настоящим текстом провайдера:

```javascript
try {
  const result = await Memu.image({ prompt: "..." });
  if (!result.urls.length) {
    status.textContent = "Провайдер не вернул изображение";
  }
} catch (error) {
  // «Нет доступного аккаунта Genspark для генерации»
  status.textContent = error.message;
}
```

Если поток оборвался, не дойдя до `status: done`, мост тоже сообщит об этом — пустой список не станет тихим успехом.

> Никогда не подставляйте картинку-заглушку вместо результата. Пользователь должен видеть, что именно ответил провайдер.

## Музыкальный API

Музыкальные API подключаются в аккаунтах **MemuAI Proxy**, вместе с ключами, моделями и схемой запроса. Модуль выбирает модель и передаёт параметры через мост:

```javascript
const { models } = await Memu.music({ action: "models" });
const request_id = crypto.randomUUID();
const result = await Memu.music({
  action: "generate", request_id, model: models[0].id,
  prompt: "Вечернее фортепиано", style: "ambient", duration: 60,
  tempo: 90, lyrics: "", instrumental: true
});
// result.audio — data URL; result.mime, result.model, result.provider
// await Memu.music({ action: "cancel", request_id });
```

Нужно право `media` и включённый модуль. `action: "config"` возвращает тот же список моделей; `configure` направляет к настройкам Proxy. Старое сохранённое подключение доступно только для совместимости старого кода и явного импорта в Proxy; новый модуль должен передавать `model`.

В аккаунте Proxy JSON-шаблон подставляет `{{имя_поля}}`; числа и флаги сохраняют тип, если плейсхолдер занимает всё значение. API может вернуть аудиофайл, JSON с URL/base64/hex либо задание для опроса (до десяти минут); адрес опроса с `{id}` должен принадлежать тому же API. Аудио ограничено 24 МБ; локальные адреса и перенос ключа между серверами запрещены. Пустой путь результата отключает этот вариант. Отмена прекращает ожидание в MemuAI, но не гарантирует отмену уже принятого задания у провайдера.
