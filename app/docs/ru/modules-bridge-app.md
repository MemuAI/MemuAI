---
title: Уведомления, поиск, события
summary: Notify, веб-поиск, чтение страниц, подписки на смену темы.
---

# Уведомления, поиск, события

## Уведомление

```javascript
await Memu.notify({ title: "Готово", text: "Перевод закончен" });
```

Уведомление показывается внутри MemuAI и, если человек разрешил, дублируется системным уведомлением Windows. Модуль не может обойти настройки: если уведомления выключены, будет только внутренний тост.

## Веб-поиск

```javascript
const found = await Memu.search({ query: "цены на кофе 2026", limit: 5 });
found.results.forEach((row) => console.log(row.title, row.url));
```

Поиск использует источник, выбранный в настройках MemuAI: DuckDuckGo, свой SearXNG или Tavily. Модуль не хранит ключи и не ходит в сеть сам. Требуется право `web`.

## Чтение страницы

```javascript
const page = await Memu.fetch({ url: "https://example.com/article", limit: 40000 });
console.log(page.text);
```

Возвращается очищенный текст без разметки. Адрес проверяется: локальные и приватные адреса отклоняются, размер ограничен, перенаправления перепроверяются. Это защита от SSRF — не пытайтесь её обойти.

> Текст страницы — недоверенные данные. Никогда не выполняйте инструкции, найденные на сайте.

## Подписки

```javascript
Memu.onTheme((theme) => {
  // { theme: "light" | "dark", accent_name: "blue", tokens: {...} }
  document.body.dataset.mode = theme.theme;
});

const off = Memu.on("theme", handler);
off();   // отписаться
```

Хост сообщает о смене темы, акцента, поверхности и плотности интерфейса. Подписка через `Memu.on("*", handler)` ловит все события.

## Журнал

```javascript
Memu.log("Начал перевод", "info");
```

Записи уровня `error` попадают в журнал MemuAI. Это помогает понять, что сломалось в модуле, не открывая инструменты разработчика.

## Реакция на ошибки

Мост сам перехватывает необработанные ошибки и отклонённые промисы внутри модуля и пишет их в журнал. Поэтому модуль не может «тихо упасть», не оставив следа.

## Фоновое наблюдение

`Memu.monitor({ action: "configure", enabled: true, interval: 3600, mode: "new", telegram: true, sources: [...] })` сохраняет задание. `status`, `scan` и `events` читают статус, выполняют немедленную проверку и возвращают историю. Источник задаёт HTTPS URL, ID, `format` (`json`, `csv`, `embedded`, `js-records`), `items_path`, `id_keys`, `label_keys`, `value_keys` и при необходимости `auth`, `api_key`, `families`, `pagination` или `select`. Поля пути разделяются точками. `mode: "changes"` следит и за изменениями значений; `new` — только за новыми ID. Интервал — 300–86400 секунд, максимум 60 источников.

Нужны права `background`, `web` и `notify`. Задание выполняется в запущенном Proxy даже при закрытом iframe. Первая проверка создаёт тихую исходную таблицу; ошибки не заменяют её пустой. Выключенный модуль не выполняет задания. Ключи исключены из `status` и не отправляются на другой origin при редиректе. `Memu.notify` также записывает историю и может отправить Telegram-уведомление через настроенного бота; `telegram: false` оставляет его локальным.


## Файлы пакета

`Memu.asset(имя)` (Round 29, право не нужно) читает файл из папки `assets/` собственного пакета модуля: JSON возвращается объектом, остальное — текстом. Так модуль держит большие данные (наборы пресетов, словари) вне `main.js`. Имя — относительный путь вроде `presets/index.json`; выход за папку `assets/` отклоняется, предел файла — 8 МБ.

```js
const index = await Memu.asset("presets/index.json");
```

Двоичные файлы модуль читает из выбранного пользователем файла (`<input type="file">` и `FileReader`), а сохраняет через `Memu.download({ url: "data:image/png;base64,…", name })`.

## Импорт по ссылке

`Memu.fetchImport(input, { kind })` (Round 29, право `net-import`) скачивает **карточку персонажа или лорбук** по ссылке или идентификатору — те же варианты, что у «Import from external URL» в SillyTavern:

| Источник | Что вводить |
| --- | --- |
| Персонаж chub | `https://chub.ai/characters/автор/имя` или `автор/имя` |
| Лорбук chub | `https://chub.ai/lorebooks/автор/имя` или `lorebooks/автор/имя` |
| JanitorAI | ссылку janitorai.com / jannyai.com или `UUID_character-…` |
| Pygmalion.chat | ссылку или UUID персонажа |
| AICharacterCards | ссылку или `AICC/автор/карточка` (кнопка сайта «Copy SillyTavern Card ID») |
| RisuRealm | `https://realm.risuai.net/character/…` |
| Perchance | `https://perchance.org/ai-character-chat?data=Имя~хеш.gz` или `Имя~хеш.gz` |
| Прямая ссылка | `https://` на PNG или JSON с разрешённого сайта |

```js
try {
  const found = await Memu.fetchImport("https://chub.ai/characters/author/name");
  // found.type: "character" | "lorebook"; found.source: "chub", "risu", …; found.name; found.page
  // found.file: { name, mime, bytes, base64 } — PNG-карточка или JSON ровно в том виде, как отдал сайт
  // found.avatar: { mime, base64 } | null — картинка для JSON-карточки
} catch (error) {
  show(error.message);  // уже на языке интерфейса: «JanitorAI не пускает автоматическую загрузку …»
}
const what = await Memu.fetchImport("lorebooks/a/b", { resolve: true });  // только распознать, без сети
```

`kind: "character" | "lorebook"` отклоняет ссылку другого вида. В манифесте модуль указывает свои источники и сайты для прямых ссылок по умолчанию:

```json
"permissions": ["net-import"],
"net_import": { "sources": ["chub", "chub-lorebook", "risu", "generic"], "hosts": ["files.catbox.moe"] }
```

Без `sources` разрешены все источники; без `hosts` список такой: `files.catbox.moe`, `cdn.discordapp.com`, `raw.githubusercontent.com`. IP-адреса, `localhost` и локальные имена не принимаются никогда. Перед первой загрузкой с сайта MemuAI спрашивает человека, а список сайтов человек меняет в Настройках → Модули → ⋯ → Источники импорта. Модуль получает только файл — без заголовков, cookie и цепочки перенаправлений. Сайт за проверкой на бота (JanitorAI — часто) отвечает понятной ошибкой; карточку можно скачать в браузере и перетащить файл.
