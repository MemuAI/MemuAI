# API MemuAI Proxy

Адрес по умолчанию: `http://127.0.0.1:8317/v1` (хост и порт — Настройки → Сеть). Совместим с клиентами OpenAI.

## Эндпоинты
- `GET /v1/models` — включённые модели каталога. id: `<префикс>-<модель>` (например `genspark-claude-sonnet-5`, `kiro-claude-sonnet-4-5`, `local-qwen2.5-7b-instruct-q4-k-m`). Если в настройках выключены префиксы, id без префикса.
- `GET /v1/models/{id}` — одна модель.
- `POST /v1/chat/completions` — чат. Поля: `model`, `messages`, `stream`, `temperature`, `max_tokens`, `top_p`; расширения MemuAI: `web_search` (bool), `effort` (`auto|minimal|low|medium|high|max`). Изображения — части `image_url` с data-URL.
- `GET /health` — проверка, что Proxy жив.
- `/p/<имя>/<путь>` — CORS-passthrough к «Собственному API» с именем `<имя>`: браузерное приложение шлёт запрос сюда с заголовком `x-hub-key`, Proxy подставляет ключ и заголовки подключения.

## Ключи
Если в «Настройки → Доступ и безопасность → API-ключи клиентов» нет ни одного ключа — API открыт для программ этого компьютера. Как только создан ключ, каждый запрос должен нести `Authorization: Bearer <ключ>`, иначе 401.

## Примеры
```bash
curl http://127.0.0.1:8317/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"genspark-claude-sonnet-5","messages":[{"role":"user","content":"Привет"}],"stream":false}'
```
```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8317/v1", api_key="ключ-или-любая-строка")
reply = client.chat.completions.create(model="kiro-claude-sonnet-4-5", messages=[{"role": "user", "content": "Hi"}])
```
Для клиентов вроде Cursor, Continue, Cline, Open WebUI, SillyTavern: тип «OpenAI compatible», Base URL `http://127.0.0.1:8317/v1`, ключ — созданный ключ (или любой текст, если ключей нет), модель — id из `/v1/models`.

## Доступ из сети
По умолчанию Proxy слушает только `127.0.0.1`. Хост `0.0.0.0` открывает его в локальную сеть — тогда обязательно создать API-ключи и сменить пароль панели (по умолчанию «admin»).
