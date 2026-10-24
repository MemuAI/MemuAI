---
title: Context
summary: The model's context window, the meter in the top bar, automatic history compression and per-model limits.
---

# Context

The **context window** is how much text a model sees at once: system instructions, the conversation history, attachments and your new question. It is measured in tokens — pieces of words; in English one token is roughly 4–5 characters, in Russian 3–4.

When a conversation outgrows the window, the model stops seeing its beginning or the service returns an error. MemuAI watches for this by itself.

## The context meter

The indicator in the top bar shows how much of the current model's window is used. Hover over it for a breakdown: text, images and the total, and where the number comes from.

The meter counts everything that really goes to the model:

- the messages of the conversation (after compaction — its summary instead of the old messages);
- the system prompt: MemuAI's instructions, your instructions, skills, tools;
- images — by their size in pixels and by how the model's family counts them (Claude, Gemini and GPT use different rules);
- video — the frames and the speech transcript that are actually sent;
- tool results still needed for the answer.

**Where the number comes from.** When the provider reports its own token count (custom APIs, Antigravity), the meter takes it for the last request and adds an estimate of what appeared since. When it does not, the meter shows MemuAI's estimate from the text. The tooltip says which one it is.

Clicking the meter toggles whether images are counted — useful when a model handles them separately and the estimate gets in the way.

## Automatic optimisation

**Settings → Chat & replies → Long chats → Compress history automatically** is on by default and does two things:

1. **Compresses history.** When the conversation fills the set share of the window, older messages are turned in the background into a structured summary: decisions, facts, agreements, open questions. The latest messages stay verbatim.
2. **Selects code.** In Work and MemuCode the model receives only the relevant files and fragments, not the whole project.

| Setting | Default | Meaning |
| --- | --- | --- |
| Compress at | 90 % | the share of the window after which compression starts |
| Recent messages kept whole | 20 | how many fresh messages always go verbatim |

> [!IMPORTANT]
> Compression does not delete history. The whole conversation stays on disk and fully visible in the chat; only the model receives the summary. Editing or deleting a message inside the compressed part rebuilds the summary.

## How the window is detected

MemuAI finds each model's window by itself. Where the number came from shows in the meter tooltip ("Model window: …") and in **Settings → Chat & replies → Context window by model** ("Detected: …"):

| Source | When |
| --- | --- |
| set manually | you entered your own value for this model |
| llama.cpp launch setting | a local model — the context comes from its settings |
| reported by the provider | the provider sent the window size in its model list |
| known model | the model is in the catalogue of known models; names such as `prefix-claude-opus-5.5` and `claude-opus-5-5` count as the same model |
| estimate for the model family | no exact entry, but the family is known |
| default — not detected | nothing matched — the "When the window cannot be detected" value is used |

## Per-model limits

Services do not always report the window size reliably. In **Context window by model** you can correct the automatic value:

- **Override (tokens)** for the selected model — beats the automatic value; "Back to automatic" removes it;
- **When the window cannot be detected** — 128,000 tokens, used only for models whose window is unknown.

Examples: `260000` for a model of about 258 thousand tokens, `1000000` for a million-token model. Compression starts before that ceiling.

## Saving context

- Start a new chat for a new topic — the model does not need the old one's history.
- Attach large files only when needed; in Work the model finds what it needs itself.
- Choose models with a large window for long documents.
- Prompt caching (**Settings → Models**) lowers the cost of repeated instructions with services that support it.

See also: [Models](doc:models), [Work](doc:work).
