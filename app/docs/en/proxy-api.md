---
title: Proxy API
summary: Connecting other programs to MemuAI Proxy: address, keys, endpoints, examples and errors.
---

# Proxy API

The Proxy serves every model of your accounts through an OpenAI-format API. Almost any program that speaks OpenAI can therefore connect to it: code editors, chat clients, scripts.

## Address

```
http://127.0.0.1:8317/v1
```

Host and port are changed in **Proxy → Settings → Network**. A new port takes effect after the Proxy restarts. The current address is always shown on the Home tab.

## Keys

While **Settings → Access & security → Client API keys** is empty, the API is open to programs on this computer and closed to websites in a browser. Any text works in the client's "API key" field.

Once at least one key exists, every request must carry it, otherwise the answer is `401`:

```
Authorization: Bearer <key>
```

The `x-api-key: <key>` header is accepted too.

> [!WARNING]
> With the host set to `0.0.0.0` the Proxy is visible on the local network. Without keys the API then refuses every request, even from the same computer: create a key and change the panel password.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/models` | enabled models |
| `GET` | `/v1/models/{id}` | one model |
| `POST` | `/v1/chat/completions` | a model answer, whole or streamed |
| `POST` | `/v1/messages` | Anthropic Messages for Claude Code |
| `POST` | `/v1/responses` | OpenAI Responses for Codex CLI |
| `GET` | `/health` | the Proxy is alive |
| any | `/p/<name>/<path>` | direct pass-through to a "Custom API" connection, CORS included |

### A model request

The usual OpenAI fields are supported: `model`, `messages`, `stream`, `temperature`, `top_p`, `max_tokens`. Images are `image_url` parts with a `data:` URL.

MemuAI adds two fields:

| Field | Values | Meaning |
| --- | --- | --- |
| `web_search` | `true` / `false` | enable web search on a model that supports it |
| `effort` | `auto`, `minimal`, `low`, `medium`, `high`, `max` | reasoning depth for reasoning models |

## Examples

### curl

```bash
curl http://127.0.0.1:8317/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "genspark-claude-sonnet-5", "messages": [{"role": "user", "content": "Hello"}]}'
```

### Python

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8317/v1", api_key="your-key")
reply = client.chat.completions.create(
    model="kiro-claude-sonnet-4-5",
    messages=[{"role": "user", "content": "Briefly: what is SSE?"}],
)
print(reply.choices[0].message.content)
```

### JavaScript, streamed

```javascript
const response = await fetch("http://127.0.0.1:8317/v1/chat/completions", {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: "Bearer your-key" },
  body: JSON.stringify({ model: "local-qwen3-8b-q4-k-m", stream: true,
    messages: [{ role: "user", content: "Three blog name ideas" }] }),
});
// The answer arrives as "data: {...}" lines and ends with "data: [DONE]".
```

## Ready-made programs

In every program choose the **OpenAI compatible** connection type (or "OpenAI API" with a custom address):

| Client field | Value |
| --- | --- |
| Base URL / API base | `http://127.0.0.1:8317/v1` |
| API key | your key, or any text when there are no keys |
| Model | an id from `/v1/models`, for example `genspark-claude-sonnet-5` |

This is how Cursor, Continue, Cline, Roo Code, Open WebUI, SillyTavern, Chatbox and others connect. While a model thinks, the Proxy sends keep-alive pings, so long answers in Cline and Continue are not cut by a timeout.

## Claude Code and Codex CLI

In **Proxy → Settings → Integrations**, select the models and apply the settings, then restart the CLI. MemuAI keeps a backup of the previous configuration files and can restore their settings. When the Proxy has a client API key, the key is stored in the CLI's local settings file (`~/.claude/settings.json` or `~/.codex/config.toml`).

For agent actions (reading and editing files, running commands), select a **Custom API** model whose upstream server supports OpenAI-compatible `tools` / `tool_calls`. The Proxy carries calls between the model and the CLI; the CLI performs the actions. A web provider without native function calling returns an explicit `400` for a request with tools, while ordinary chat remains available.

If you already have a separate API, choose **Own API directly** in the same panel and enter its Base URL, key and model ID. The CLI then connects without going through the Proxy. Claude Code requires an Anthropic Messages endpoint (`/v1/messages`); Codex CLI requires OpenAI Responses (`/v1/responses`) with tool calls. Re-enter the key when applying the settings again.

## Pass-through to your API (`/p/`)

"Custom API" connections have a direct address:

```
http://127.0.0.1:8317/p/<name>/<path>
```

The Proxy inserts the connection's key and extra headers and adds CORS headers, so a browser app can call your API without a server of its own. The request must carry an `x-hub-key` header with a Proxy client key.

## Errors

Errors use the OpenAI format and carry a hint as well:

```json
{
  "error": {
    "message": "Все аккаунты «kiro» недоступны (Work): HTTP 429",
    "message_en": "Every “kiro” account failed (Work): HTTP 429",
    "type": "rate_limit_error",
    "code": 429,
    "hint_en": "The provider is throttling requests. A second account usually helps."
  }
}
```

| Code | Common cause |
| --- | --- |
| 401 | a wrong or missing client key |
| 404 | the model is unknown or disabled in the catalogue |
| 400 | tools were requested from a provider without function calling |
| 429 | every suitable account is rate-limited |
| 502 | the provider returned an error, changed its protocol or did not answer in time |
| 503 | no working account of that provider, or the Proxy is switched off |

Every request is visible on the **Logs** tab with its error, and **Ask AI** explains it in plain words.

## Music generation

Proxy account music models appear in `/v1/models` with `capabilities.music: true`. Generate using **POST `/v1/audio/music`** and the same client key:

```json
{
  "model": "elevenlabs-music-music_v2_5",
  "request_id": "my-track-1",
  "prompt": "Soft evening piano",
  "style": "ambient piano",
  "duration": 60,
  "tempo": 90,
  "instrumental": true,
  "lyrics": ""
}
```

Use the exact ID returned by `/v1/models`. The JSON response contains `audio` (data URL), `mime`, `model` and `provider`. Send `action: "cancel"` with the same `request_id` to stop waiting; cancellation belongs to the same client key. This is a MemuAI Proxy extension, separate from the standard TTS endpoint. Parameters and availability depend on the configured music service.

See also: [MemuAI Proxy](doc:proxy), [Models](doc:models), [Accounts](doc:accounts).
