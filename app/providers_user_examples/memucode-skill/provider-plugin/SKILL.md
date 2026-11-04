---
name: provider-plugin
description: Добавить провайдер (плагин) в MemuAI Proxy — кодовый или декларативный; проверить check() и пробным чатом до установки.
---

# Плагин-провайдер MemuAI Proxy

Когда пользователь просит «добавь провайдер <сервис>», «сделай плагин для
<сайт/API>» или «подключи <модель> через Proxy» — пиши плагин по этой
инструкции. Не трогай встроенные провайдеры (`app/providers/*.py`, кроме
`plugins.py`) и не правь реестр вручную: плагин подхватывается сам.

## Где лежит плагин

Папка плагинов: `%APPDATA%\MemuAI\providers` (переменная окружения
`MEMUAI_PROVIDERS_DIR`). Туда же при первом запуске копируются примеры:
`kimi2api.py` (обёртка над сайтом Kimi), `web_wrapper_template.py`
(шаблон 2api-обёртки), `README.md` (полная документация).

## Шаг 1. Выясни, что за сервис

1. **OpenAI-совместимый API** (`/v1/chat/completions` или `/v1/responses`):
   декларативный плагин, кода не нужно. Спроси у пользователя только
   `base_url` и какие модели — остальное из документации сервиса.
2. **Веб-сайт как API** (как Kimi, Antigravity, ZCode): кодовый плагин по
   шаблону `web_wrapper_template.py`. Найди в интернете (или попроси
   пользователя показать из DevTools): endpoint чата, имя куки/токена
   сессии, формат запроса и ответа (SSE? Connect-RPC? обычный JSON?).
   Если точный формат не найти — скажи честно и сделай плагин с
   помеченными `TODO`-местами и честными ошибками, а не «на авось».

## Шаг 2. Напиши файл

### Декларативный (`<id>.yaml`)

```yaml
plugin_api: 1
id: example-api
title: "Example API"
description: "Мой OpenAI-совместимый сервис"
kind: openai-chat            # или openai-responses
base_url: https://api.example.com/v1
auth: bearer                 # bearer | header | none
# auth_header: X-Api-Key     # для auth: header
models: [model-a, model-b]
```

### Кодовый (`<id>.py`)

Обязательно в начале файла: `PLUGIN_API = 1`. Класс — наследник
`app.providers.base.Provider`:

```python
PLUGIN_API = 1
from app.providers.base import Field, Provider, ProviderAuthError, ...
from app.providers import http as hub_http

class MyProvider(Provider):
    id = "my-api"   # ^[a-z][a-z0-9-]{1,31}$, не занят встроенными
    title = "Мой API"
    icon = "◆"
    description = "..."

    def account_fields(self):
        return [Field("api_key", "API-ключ", "password")]

    def seed_models(self):
        return [("model-a", "Модель A")]

    async def check(self, account):
        return True, "OK"

    async def chat_stream(self, account, model, messages, params):
        yield {"type": "text", "text": "..."}
```

Правила:
- `chat_stream` — async-генератор событий `{"type": "text"|"reasoning",
  "text": str}`. Никаких других типов событий.
- История: `normalize_messages(messages)` → `(system, history, final_user)`.
- HTTP — только через `hub_http.get_session(proxy, impersonate, url)`
  (curl_cffi, TLS-имитация Chrome); SSE — `hub_http.aiter_lines_str(resp, ...)`.
- Ошибки — только `ProviderAuthError` (ключ/кука отклонены),
  `ProviderServerError`, `ProviderNetworkError`,
  `ProviderRateLimitError`, `ProviderModelUnavailableError`.
  Никогда не логируй и не возвращай значения полей аккаунта.
- `validate_fields()` — проверь формат ключа/куки до сохранения аккаунта.
- `dynamic_models()` — если у сервиса есть живой список моделей.
- Не импортируй ничего вне `app.providers.*`, `app.config`, stdlib.
- Не читай файлы, не открывай сокеты напрямую, не запускай процессы.

## Шаг 3. Проверь ДО установки

1. `python -m py_compile <файл>` — должен пройти.
2. Запусти загрузчик в изоляции (см. `tests/plugins_test.py`):
   валидный плагин появляется в реестре, битый — в статусе с ошибкой,
   Proxy не падает.
3. **Проверка без ключа пользователя невозможна** — попроси пользователя
   вставить ключ/куку в админке Proxy и нажать «Проверить» (`check()`),
   затем отправь пробное сообщение в чат. Только после успешного пробного
   чата плагин считается установленным.

## Шаг 4. Установи

Установи из админки Proxy (вкладка «Аккаунты» → «Плагины провайдеров» →
«Установить из файла») или скопируй файл в папку плагинов и вызови
`POST /api/admin/providers/reload` (или мягкий перезапуск Proxy).
Проверь статус: `GET /api/admin/providers/plugins` — файл должен быть
`ok: true`, а провайдер — в `GET /api/admin/providers`. Провайдер после
установки доступен и через MCP-сервер Proxy (`POST /mcp`, инструменты
`list_providers` / `chat`) — см. README.md из папки плагинов.

## Честность

- Не знаешь формат запроса/ответа — так и пиши в коде (`TODO`) и в ответе
  пользователю; не выдумывай endpoint «по аналогии» и не помечай
  недоделанное как готовое.
- Плагин выполняется с полными правами процесса Proxy (песочницы нет) —
  напомни пользователю ставить только код, которому он доверяет.
