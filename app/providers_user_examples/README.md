# Плагины провайдеров MemuAI Proxy

Свой прокси — без правок исходников MemuAI: положите файл в папку плагинов,
и провайдер появится в списке Proxy рядом со встроенными.

## Где лежит папка плагинов

`%APPDATA%\MemuAI\providers` (на Windows). Это папка данных пользователя —
обновление MemuAI её не трогает. Другой путь — переменной окружения
`MEMUAI_PROVIDERS_DIR`. При первом запуске сюда копируются примеры
(`kimi2api.py`, `web_wrapper_template.py`) и этот README; ваши правки
никогда не перезаписываются.

Управление: админка Proxy → вкладка «Аккаунты», блок «Плагины провайдеров»:
список файлов со статусом (загружен / ошибка с текстом / выключен), кнопки
вкл/выкл, удаление, установка из файла, галерея шаблонов (OpenAI-совместимый,
Kimi2API, веб-обёртка, пустой кодовый), кнопка «Открыть папку», инструкция
«Как написать свой плагин» и раздел MCP. Тот же эффект, что «мягкий
перезапуск» Proxy — `POST /api/admin/providers/reload` (перечитать плагины
без перезапуска процесса). API управления:

* `GET /api/admin/providers/plugins` — статус файлов + папка + шаблоны;
* `POST /api/admin/providers/plugins/toggle` — `{"file", "disabled": true/false}`;
* `POST /api/admin/providers/plugins/install` — `{"name", "content_base64"}`;
* `POST /api/admin/providers/plugins/template` — `{"template", "name?", "id?"}`;
* `DELETE /api/admin/providers/plugins/{file}` — удалить файл плагина;
* `POST /api/admin/providers/plugins/open-folder` — открыть папку в проводнике.

## MCP-сервер: провайдеры и плагины для любого клиента

MemuAI Proxy — это ещё и MCP-сервер (Streamable HTTP, MCP 2025-06-18):
любой MCP-клиент может вызывать модели и плагины Proxy по протоколу MCP.

* Адрес: `http://127.0.0.1:8317/mcp` (тот же порт, что и админка).
* Авторизация: тот же ключ API хаба, что и у `/v1` — заголовок
  `Authorization: Bearer <ключ>` (ключ создаётся в админке: раздел
  «Настройки → API» или `POST /api/admin/keys`). Если ключей нет,
  с этого компьютера работает и без ключа, как и `/v1`.
* Инструменты: `list_providers`, `list_models`, `list_plugins`,
  `reload_plugins`, `chat` (нестриминговый вызов модели).
* Ресурсы: `memuai://providers`, `memuai://models`, `memuai://plugins`.

Пример конфига для Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "memuai": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8317/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_HUB_API_KEY"
      }
    }
  }
}
```

Проверить, что сервер отвечает, можно из админки (блок «Плагины
провайдеров» → «MCP: провайдеры для любого клиента» → «Проверить MCP»)
или curl'ом:

```bash
curl -X POST http://127.0.0.1:8317/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

## Два вида плагинов

### 1. Кодовый плагин (`*.py`)

Класс-наследник `app.providers.base.Provider` в файле с `PLUGIN_API = 1`:

```python
PLUGIN_API = 1
from app.providers.base import Field, Provider, ProviderAuthError, ...

class MyProvider(Provider):
    id = "my-api"            # латиница, цифры, дефис; не занят встроенными
    title = "Мой API"
    icon = "◆"
    description = "..."

    def account_fields(self):
        return [Field("api_key", "API-ключ", "password")]

    def seed_models(self):
        return [("model-a", "Модель A")]

    async def chat_stream(self, account, model, messages, params):
        # yield {"type": "text", "text": "..."} / {"type": "reasoning", ...}
        ...

    async def check(self, account):
        return True, "OK"   # (True/False/None, сообщение)
```

Контракт провайдера (всё остальное — по умолчанию из `Provider`):

* `account_fields()` — поля аккаунта (тип: text/password/url/select/textarea;
  `required`, `options`, `default`, `placeholder`, `help`).
* `settings_fields()` — настройки провайдера (необязательно).
* `seed_models()` — стартовый каталог `[(модель, примечание)]`.
* `validate_fields(fields)` — проверка при сохранении аккаунта (по умолчанию:
  обязательные поля непустые). Возвращает текст ошибки или `None`.
* `chat_stream(account, model, messages, params)` — **обязателен**:
  async-генератор нормализованных событий `{"type": "text"|"reasoning",
  "text": str}`. Ошибки — исключениями `ProviderAuthError` (кука/ключ
  отклонены), `ProviderServerError`, `ProviderNetworkError`,
  `ProviderRateLimitError`, `ProviderModelUnavailableError`.
* `check(account)` — проверка аккаунта: `(True/False/None, сообщение)`.
* `dynamic_models(account)` — живой список моделей (или `None`).
* `usage(account)` — лимиты/квоты для карточки (или `None`).
* `close()` — закрыть свои сессии при перезагрузке реестра.

Готовые помощники (импортируйте из `app.providers...`):

* `app.providers.http.get_session(proxy, impersonate, url)` — пул
  curl_cffi-сессий с TLS-имитацией Chrome; `aiter_lines_str(resp, ...)`
  для SSE, `network_error_text(exc)` для текста сетевых ошибок.
* `app.providers.base.normalize_messages(messages)` — `(system, history,
  final_user)`; `content_to_text()`, `transcript_prompt()`.
* `app.providers.geminiweb_cookies.parse_cookie_import(raw)` — разбор кук
  из браузера (JSON/Netscape/DevTools/`name=value`).

События `chat_stream` понимает весь Proxy: стриминг в `/v1/chat/completions`,
подсчёт токенов, ретраи по аккаунтам, фоновая проверка аккаунтов.

### 2. Декларативный плагин (`*.yaml` / `*.json`)

OpenAI-совместимый endpoint без кода:

```yaml
plugin_api: 1
id: example-api
title: "Example API"
description: "Мой OpenAI-совместимый сервис"
kind: openai-chat            # или openai-responses
base_url: https://api.example.com/v1
auth: bearer                 # bearer | header | none
# auth_header: X-Api-Key     # для auth: header
# chat_path: /chat/completions
models: [model-a, model-b]
# extra_headers: {X-Foo: bar}
# allow_private_network: false
```

Чат выполняется встроенным движком «Собственного API» (SSE, ретраи, пул
ключей — всё как у Custom). У аккаунта такого провайдера — только поле
API-ключа; модели берутся из описания и из `/models` upstream.

## Версионирование

`PLUGIN_API` — версия интерфейса плагинов в этой сборке MemuAI (сейчас 1).
Файл без `PLUGIN_API` или с версией новее отклоняется с понятным сообщением.
Несовместимый плагин никогда не роняет Proxy: он показывается в статусе
как ошибочный, остальные работают.

## Правила загрузки

* Загружаются `*.py`, `*.yaml`, `*.yml`, `*.json` из папки плагинов.
  Файлы с именем на `_` или `.` пропускаются (черновики).
* Один файл может дать несколько провайдеров (несколько классов).
* `id` — `^[a-z][a-z0-9-]{1,31}$`, не занят встроенными (`genspark`, `kiro`,
  `antigravity`, `geminiweb`, `grok`, `zcode`, `custom`, `local`, …) и не
  повторяется. Дубликат отклоняется с ошибкой.
* Ошибка в одном файле не мешает остальным. Статус каждого файла — в
  `GET /api/admin/providers/plugins`.

## Безопасность — прочитайте

Плагин выполняется **внутри процесса MemuAI Proxy с полными правами этого
процесса**. Песочницы нет: код плагина может читать файлы, ходить в сеть
и — теоретически — прочитать ключи других аккаунтов из `accounts.json`
и из памяти. Ставьте только код, которому доверяете, как доверяете любому
исполняемому файлу на своём компьютере. Не скачивайте плагины из случайных
источников и не вставляйте в них чужие куки/ключи, не понимая, куда они уйдут.

## Изоляция (на будущее, не реализовано)

План: запускать `chat_stream` плагина в отдельном процессе, а хост общается
с ним по JSON-RPC поверх stdio и даёт узкий API: HTTP только через пул Proxy,
чтение только своих полей аккаунта, запись — только события текста. Тогда
вредоносный плагин не сможет прочитать чужие аккаунты и файлы. Это требует
протокола хост↔плагин, таймаутов/перезапуска процесса и переноса состояния —
отдельная работа, не часть API v1.

## Kimi2API — статус примера

`kimi2api.py` — экспериментальный плагин-обёртка над сайтом Kimi
(www.kimi.com, Connect-RPC). Проверены: endpoint, авторизация JWT
(`kimi-auth`), 5-байтный Connect-конверт, разбор событий ответа
(`block.text`/`block.think`), проверка сессии (`GET /api/user`).
**Не проверены живым аккаунтом точные имена полей тела запроса** —
`build_chat_payload()` помечена в коде, сверьте её с реальным запросом
из DevTools (www.kimi.com → Network → Chat), если шлюз вернёт
`invalid_argument`. Старый `kimi.moonshot.cn/api/chat` мёртв (307).

## MemuCode: сгенерировать плагин

Скилл `provider-plugin` (папка `memucode-skill/provider-plugin/`):
скопируйте её в `%APPDATA%\MemuAI\memucode\skills\provider-plugin\`
(или в `<проект>/.memucode/skills/provider-plugin/`). Дальше попросите
MemuCode: «добавь провайдер для <сервис>» — он напишет файл плагина,
проверит его (`check()` + пробный чат) и положит в папку плагинов.
