---
title: Оформление и общие стили
summary: Токены MemuAI, классы mu-*, тёмная и светлая тема, доступность.
---

# Оформление и общие стили

Модуль получает все токены оформления MemuAI. Если пользоваться ими, модуль автоматически совпадает с темой, акцентом и плотностью интерфейса.

## Токены

| Токен | Что это |
| --- | --- |
| `--bg`, `--bg-deep` | фон окна и вложенных областей |
| `--panel`, `--panel-2` | поверхности карточек и полей |
| `--border`, `--control-border` | границы |
| `--text`, `--muted` | основной и вторичный текст |
| `--accent`, `--accent-soft`, `--on-accent` | акцент, его мягкая заливка и текст на нём |
| `--danger` | ошибки и разрушительные действия |
| `--radius`, `--radius-sm`, `--radius-md`, `--radius-lg` | скругления |
| `--shadow` | тень окна и меню |
| `--sans`, `--serif`, `--mono` | шрифты |
| `--font-scale` | масштаб текста, выбранный человеком (85–125) |

```css
.my-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
}
.my-card .hint { color: var(--muted); }
.my-card button { background: var(--accent); color: var(--on-accent); }
```

Не задавайте свой фон и цвет текста жёстко: в светлой теме такой модуль станет нечитаемым.

## Готовые классы `mu-*`

Общий слой уже приносит готовые элементы — их не нужно описывать заново:

| Класс | Что даёт |
| --- | --- |
| `mu-page` | страница с отступами и прокруткой |
| `mu-col` | вертикальная колонка на всю высоту |
| `mu-toolbar` | полоса инструментов сверху |
| `mu-card` | карточка-поверхность |
| `mu-title`, `mu-sub`, `mu-muted` | заголовок и подписи |
| `mu-field` | подпись и поле |
| `mu-input`, `mu-select`, `mu-textarea` | поля MemuAI |
| `mu-btn` + `.primary` / `.ghost` / `.danger` / `.small` | кнопки |
| `mu-tabs` + `mu-tab` | переключатель разделов |
| `mu-badge`, `mu-note`, `mu-empty` | значок, подсказка, пустое состояние |
| `mu-row`, `mu-grid`, `mu-grow` | раскладка |
| `mu-stage`, `mu-thumb` | сцена для медиа |
| `mu-log`, `mu-msg` | переписка |
| `mu-composer` | поле ввода с кнопкой |
| `mu-spinner`, `mu-progress` | ожидание и прогресс |

Например, целая форма без единой строки CSS:

```html
<div class="mu-page">
  <div class="mu-title">Заметки</div>
  <label class="mu-field">Текст<textarea class="mu-textarea" id="text"></textarea></label>
  <div class="mu-row">
    <button class="mu-btn primary" id="save">Сохранить</button>
    <span class="mu-grow"></span>
    <span class="mu-muted" id="status"></span>
  </div>
</div>
```

## Тема и язык

```javascript
document.documentElement.dataset.theme;      // "dark" | "light"
document.documentElement.dataset.accent;     // "terracotta", "blue", ...
document.documentElement.dataset.density;    // "normal" | "compact" | "spacious"
Memu.env.lang;                               // "ru" | "en"
```

Подпишитесь на смену темы, если рисуете что-то на canvas или задаёте цвета в коде:

```javascript
Memu.onTheme((theme) => drawChart(theme.tokens["--text"]));
```

## Доступность

- Все элементы управления должны быть настоящими `button`, `input`, `select` — тогда они работают с клавиатуры и с экранным диктором.
- У иконок-кнопок ставьте `aria-label`.
- Не полагайтесь только на цвет: ошибку подкрепляйте текстом.
- Общий слой уже уважает `prefers-reduced-motion` и рисует видимую рамку фокуса.

## Свой акцент

Если модулю нужен собственный цвет, укажите его в описании:

```json
"accent": "#4f8ad8"
```

Хост подставит его в `--accent` и `--accent-soft` страницы модуля. Остальной MemuAI это не затронет.
