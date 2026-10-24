---
title: Объект Memu
summary: Полный список методов моста, их аргументы и ответы.
---

# Объект `Memu`

Мост — единственный канал между модулем и MemuAI. Это обычный объект в `window`, доступный сразу, без ожидания.

## Состояние

| Поле | Что это |
| --- | --- |
| `Memu.id` | идентификатор модуля |
| `Memu.version` | версия моста (сейчас `4`: в версии 4 появился `Memu.toolbar`) |
| `Memu.lang` | текущий язык MemuAI: `ru`, `en` |
| `Memu.manifest` | публичное описание модуля из `manifest.json` |
| `Memu.saved` | разрешённые поля с прошлого запуска |
| `Memu.env` | язык, тема, акцент, масштаб шрифта, платформа, имя хоста; `env.toolbar` — `"host"`, если окно MemuAI рисует действия модуля в своей шапке |

Текст для нужного языка удобнее брать из словаря модуля, а не проверять язык вручную:

```javascript
document.querySelector(".greeting").textContent = Memu.t("greeting");
Memu.onLanguage(() => {
  document.querySelector(".greeting").textContent = Memu.t("greeting");
});
```

## Переводы

| Метод | Что делает |
| --- | --- |
| `Memu.t(key, vars)` | текст из `i18n.json` на текущем языке |
| `Memu.onLanguage(handler)` | вызвать `handler`, когда язык MemuAI сменился |
| `Memu.i18n` | словари, формы числа, форматирование чисел и дат |

Подробно — на странице [Переводы модуля](doc:modules-i18n).

## Шапка окна: `Memu.toolbar`

Над модулем одна шапка — строка окна MemuAI: значок и имя модуля, **действия модуля**, затем ⋯ (перезагрузка, расположение окон, настройки модуля), «свернуть», «развернуть» и ×. У развёрнутого листа этой строки нет вовсе — имя, действия и кнопки окна встают прямо в верхнюю панель MemuAI. Поэтому модуль **не рисует свою верхнюю панель**: свои кнопки он отдаёт окну.

| Метод | Что делает |
| --- | --- |
| `Memu.toolbar.supported()` | `true`, если окно рисует действия модуля (`Memu.env.toolbar === "host"`) |
| `Memu.toolbar.set(items)` / `.set({title, status, items})` | заменить все действия; `title` — подзаголовок рядом с именем модуля (например, имя открытого чата), `status` — тихая строка состояния |
| `Memu.toolbar.update(id, patch)` | поменять одно действие: `pressed`, `disabled`, `label`, `dot`… |
| `Memu.toolbar.title(text)` / `.status(text)` / `.clear()` | только подзаголовок / только статус / убрать всё |
| `Memu.toolbar.adopt({buttons, hide, status})` | забрать кнопки готовой шапки модуля, не переписывая её (ниже) |
| `Memu.toolbar.on(id, handler)` | действие нажато (кроме `onClick` у самого действия) |

Действие — обычный объект; функции остаются в модуле, окну уходят только данные:

```javascript
Memu.toolbar.set({
  title: "Алиса — 10 окт.",
  items: [
    { id: "tokens", kind: "meter", value: 0.42, label: "≈3 487 / 8 192", onClick: openInspector },
    { id: "new", icon: "plus", label: Memu.t("new_chat"), onClick: newChat },
    { id: "more", icon: "more", label: Memu.t("more"), pin: true, menu: [
      { id: "export", icon: "download", label: Memu.t("export"), onClick: exportChat },
      { sep: true },
      { id: "clear", icon: "trash", label: Memu.t("clear"), danger: true, onClick: clearChat },
    ] },
    { kind: "separator" },
    { id: "panel", kind: "toggle", icon: "panel", label: Memu.t("panel"), pressed: panelOpen, pin: true, onClick: togglePanel },
  ],
});
```

- `kind`: `button` (по умолчанию), `toggle` (`pressed`), `meter` (полоска `value` 0…1 с подписью, `tone`: `warn`/`danger`), `text`, `separator`.
- `icon` — имя из набора MemuAI (`plus`, `search`, `chat`, `note`, `panel`, `sidebar`, `refresh`, `settings`, `sliders`, `download`, `upload`, `trash`, `more`, `star`, `filter`, `sort`, `grid`, `help`, `history`, `tag`…) или своя SVG-строка (останутся только фигуры и обводка). Без иконки кнопка показывает подпись.
- `label` — всплывающая подсказка и текст для экранного диктора; видна на кнопке только с `showLabel: true` или `primary: true` (одна главная кнопка, акцентная).
- `pin: true` — кнопка не уходит в меню «»» даже в узком окне. Остальные действия, которым не хватило места, складываются в «»» с начала списка, поэтому самое нужное ставьте в конец, ближе к кнопкам окна.
- До 16 действий, до 40 пунктов в меню. Подписи — только текст.

### Готовая шапка: `adopt`

Если у модуля уже есть своя верхняя панель, её не нужно переписывать:

```javascript
if (Memu.toolbar.supported()) Memu.toolbar.adopt({
  hide: [".brand"],                 // логотип и имя — окно их уже показывает
  status: "#status",                // текст статуса уходит в строку окна
  buttons: [{ id: "refresh", el: "#refresh", icon: "refresh", showLabel: true }],
});
```

Перечисленные кнопки исчезают со страницы и появляются в шапке окна; нажатие там нажимает оригинал, а подпись, `disabled` и `aria-pressed` подхватываются на лету. Пустую после этого панель спрячьте стилем `:root[data-host-toolbar="1"] .my-bar { display: none; }` — атрибут `data-host-toolbar` ставится на `<html>` до запуска кода модуля, так что ничего не мигает.

### Без хоста

На стенде `memu-module-test`, в Telegram и в старых версиях MemuAI окна-шапки нет: `supported()` возвращает `false`, `set()` отвечает `{ok: false, supported: false}`, атрибута `data-host-toolbar` нет — и модуль показывает свою шапку как раньше. Поэтому свою панель держите в разметке, а прячьте только под `:root[data-host-toolbar="1"]`.

## Запросы к модели

| Метод | Что делает |
| --- | --- |
| `Memu.request(body)` | один ответ модели целиком |
| `Memu.stream(body, handlers)` | тот же запрос с потоком и статусами |
| `Memu.models()` | список моделей MemuAI с возможностями |
| `Memu.history()` | сообщения чата `module-<id>`, если он есть |

## Медиа

| Метод | Что делает |
| --- | --- |
| `Memu.image({prompt, model, aspect, images})` | генерация изображения |
| `Memu.video({prompt, model, aspect, images})` | генерация видео |
| `Memu.speak({text})` | озвучка текста |
| `Memu.transcribe({audio})` | расшифровка голосового файла |
| `Memu.record.start()` / `.stop()` | запись с микрофона через окно MemuAI |

## Файлы и данные

| Метод | Что делает |
| --- | --- |
| `Memu.extract({name, data})` | текст из PDF, DOCX и обычных файлов |
| `Memu.download({name, text})` | сохранить файл в «Загрузки/MemuAI» |
| `Memu.download({url, name})` | скачать изображение или медиа и сохранить |
| `Memu.handoff({prompt, images, code, files})` | пакет «промпт + картинки + код» для другой ИИ |
| `Memu.copy(text)` | положить текст в буфер обмена |
| `Memu.paste()` | прочитать текст из буфера обмена (право `clipboard`) |
| `Memu.open({url})` | открыть адрес в браузере Windows |
| `Memu.notify({title, text})` | уведомление MemuAI и Windows |

## Поиск

| Метод | Что делает |
| --- | --- |
| `Memu.search({query, limit})` | настроенный веб-поиск MemuAI |
| `Memu.fetch({url, limit})` | прочитать страницу и вернуть текст |

## Служебные

| Метод | Что делает |
| --- | --- |
| `Memu.routes()` | список маршрутов и их доступность |
| `Memu.save(fields)` | запомнить разрешённые поля |
| `Memu.resize({width, height})` | попросить хост изменить размер окна |
| `Memu.on(type, handler)` | подписаться на событие хоста |
| `Memu.onTheme(handler)` | подписка на смену темы |
| `Memu.onLanguage(handler)` | подписка на смену языка |
| `Memu.log(text, level)` | запись в журнал модуля |
| `Memu.close()` | закрыть окно модуля |
| `Memu.call("host", {command: "chat", models: [...]})` | открыть новый чат MemuAI с первой подключённой моделью из списка (до 6); `{ok: false, reason: "not_connected"}`, если ни одной нет |
| `Memu.call("host", {command: "module", id, query, model})` | открыть другой включённый модуль; он получает событие `navigate` с `{query, model, from}` |
| `Memu.ready()` | отметить, что модуль загружен |

## Ошибки

Все методы возвращают промис. Если что-то не так, он отклоняется с понятным текстом на языке интерфейса:

```javascript
try {
  const answer = await Memu.request({ model: "", text: "привет" });
} catch (error) {
  // «Выберите модель» — не пустая строка и не фальшивый успех
  status.textContent = error.message;
}
```

У ошибки есть `error.detail` — исходный ответ сервера, если он был.

> Мост никогда не подменяет ошибку успехом. Если провайдер не вернул изображение, метод не отдаст заглушку: он сообщит текст провайдера.
