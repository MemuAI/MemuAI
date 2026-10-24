---
title: Module translations
summary: Make a module bilingual so it switches language together with MemuAI.
---

# Module translations

A module learns the MemuAI language when it opens and receives an event whenever the language changes. When its texts live in a dictionary rather than in the markup, the whole module is translated at once — no window restart and no switching code of your own.

Every built-in module (Translator, Studio, Design) and every template in the creation wizard works this way.

## What a translation consists of

1. An `i18n.json` file next to `manifest.json` — one dictionary per language.
2. `data-i18n` attributes in `index.html` — which elements to translate.
3. `Memu.t("key")` calls in `main.js` — for strings assembled in code.

```
my-module/
  manifest.json
  index.html
  style.css
  main.js
  i18n.json
```

## The i18n.json file

An object whose top-level keys are language codes, each holding "key → text" pairs:

```json
{
  "ru": {
    "title": "Заметки",
    "add": "Добавить",
    "empty": "Пока пусто",
    "saved": "Сохранено {time}"
  },
  "en": {
    "title": "Notes",
    "add": "Add",
    "empty": "Nothing here yet",
    "saved": "Saved at {time}"
  }
}
```

Keys can be anything; grouping them with dots helps: `form.name`, `form.email`, `error.network`.

> [!TIP]
> The module workbench has a **Translations** tab. "Add missing" finds keys used in the code but absent from the dictionary and adds them to every language — you only fill in the text.

## Markup

`data-i18n` replaces the element's text:

```html
<h1 data-i18n="title"></h1>
<button class="mu-btn primary" id="add" data-i18n="add"></button>
```

Attributes have their own variants:

| Attribute in markup | What it translates |
| --- | --- |
| `data-i18n` | the element's text (`textContent`) |
| `data-i18n-html` | the content as HTML — your own strings only, never user data |
| `data-i18n-placeholder` | an input placeholder |
| `data-i18n-title` | the tooltip |
| `data-i18n-aria-label` | the label read by screen readers |
| `data-i18n-alt` | an image's alternative text |
| `data-i18n-value` | the value of a field or button |
| `data-i18n-label` | the `label` attribute (for example on `<optgroup>`) |

One element can carry several at once:

```html
<input id="search" data-i18n-placeholder="search.hint" data-i18n-aria-label="search.label">
```

The bridge walks the document on start and on every language change. If you insert elements later, call `Memu.i18n.apply(container)` afterwards — or build them with their text through `Memu.t`.

## Code

```javascript
const t = Memu.t;

status.textContent = t("saved", { time: Memu.i18n.date(Date.now(), { timeStyle: "short" }) });
```

Braces in a text are placeholders: `{time}` is replaced by the value from the second argument. A key missing from every dictionary comes back as the key itself, so an untranslated string is visible straight away. A fallback text is possible: `t("hint", { fallback: "Press Enter" })`.

### When the language changes

Texts marked with `data-i18n` update themselves. Anything your code assembled has to be redrawn:

```javascript
function draw() {
  list.innerHTML = "";
  for (const note of notes) {
    const row = document.createElement("div");
    row.textContent = note.text;
    list.append(row);
  }
  counter.textContent = t("count", { count: notes.length });
}

draw();
Memu.onLanguage(draw);
```

The handler receives `{ lang, previous }`. The current language is always available as `Memu.lang`.

## Plurals

For words that change with a number, the key's value is an object of forms. The bridge picks the form by the language's rules (the standard `Intl.PluralRules`); pass the number as `count`:

```json
{
  "ru": { "count": { "one": "{count} заметка", "few": "{count} заметки", "many": "{count} заметок", "other": "{count} заметки" } },
  "en": { "count": { "one": "{count} note", "other": "{count} notes" } }
}
```

Allowed forms: `zero`, `one`, `two`, `few`, `many`, `other`. Always provide `other` — it is used whenever the needed form is missing.

> [!NOTE]
> Russian puts fractions (1.5) into `other`, so fill it in even if the module only counts whole things.

## Locale-aware formatting

| Method | Example result (ru / en) |
| --- | --- |
| `Memu.i18n.number(1234.5)` | `1 234,5` / `1,234.5` |
| `Memu.i18n.number(0.25, { style: "percent" })` | `25 %` / `25%` |
| `Memu.i18n.date(Date.now(), { dateStyle: "long" })` | `23 сентября 2026 г.` / `September 23, 2026` |
| `Memu.i18n.list(["A", "B", "C"])` | `A, B и C` / `A, B, and C` |

The second argument takes the usual options of `Intl.NumberFormat`, `Intl.DateTimeFormat` and `Intl.ListFormat`.

## All methods

| Method | What it does |
| --- | --- |
| `Memu.t(key, vars)` | translation with placeholders and plural forms |
| `Memu.lang` | current language: `ru`, `en` and so on |
| `Memu.onLanguage(handler)` | subscribe to language changes |
| `Memu.i18n.apply(root)` | translate `data-i18n*` elements inside `root` |
| `Memu.i18n.has(key)` | whether the key exists in any applicable dictionary |
| `Memu.i18n.get(key, lang)` | the raw value in one language, no placeholders or fallbacks |
| `Memu.i18n.locales()` | languages that have a dictionary |
| `Memu.i18n.locale()` | the `Intl` code: `ru-RU`, `en-US` |
| `Memu.i18n.add(lang, table)` | add or replace keys of one language at runtime |
| `Memu.i18n.extend(tables)` | the same for several languages at once |
| `Memu.i18n.number / date / list` | locale-aware formatting |

## Which dictionary is used

The bridge looks the key up along a chain and stops at the first value found:

1. the exact MemuAI language (for example `pt-BR`);
2. its base part (`pt`);
3. `default_locale` from `manifest.json`;
4. `en`, then `ru`;
5. any other language in `i18n.json`.

So a module with only an English dictionary still shows English text in a Russian interface rather than raw keys.

```json
{
  "id": "notes",
  "name": "Notes",
  "default_locale": "en"
}
```

## Limits

| What | Limit |
| --- | --- |
| languages per module | 24 |
| keys per language | 3000 |
| length of one text | 20,000 characters |
| language code | `xx`, `xxx` or `xx-YY` |

A JSON mistake never breaks the module: on save the workbench shows the line and column where the file broke, and an installed module with a damaged `i18n.json` opens with empty dictionaries.

## Checking

- Switch the language in the workbench preview: the list comes from `i18n.json`.
- The **Translations** tab shows how many keys each language is missing.
- A key shown in the window as-is (`form.name` instead of text) is missing from every dictionary.

See also: [The Memu object](doc:modules-bridge), [Manifest](doc:modules-manifest), [Workbench](doc:modules-workbench).
