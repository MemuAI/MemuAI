---
title: Proxy AI companion
summary: The built-in companion that explains provider errors and suggests what to do.
---

# Proxy AI companion

The MemuAI Proxy panel has a built-in **AI companion**. It knows how the providers and the Proxy itself work, sees your account states and recent errors, and answers questions such as "why does Kiro say 401" or "how do I connect the Proxy to Cursor".

## Opening it

- **AI companion** at the bottom of the sidebar;
- the round ✦ button in the bottom-right corner of the window;
- the **Ask AI** button next to an error.

"Ask AI" appears on failing accounts (invalid), on failed requests in the Logs tab and in error notifications. It opens the companion and sends the question right away with the details: provider, model, response code, error text and the request's last stages.

The model detector can hand its result over too, with **Discuss with the companion**.

## What it knows

| Source | Contents |
| --- | --- |
| Knowledge base | how each provider works (Genspark, Kiro, Antigravity, Gemini Web, Grok, custom API, local models), common errors and fixes, the API, settings, the detector |
| This Proxy's state | version, address, balancing mode, accounts with their states and counters, recent errors |
| Error context | whatever the "Ask AI" button passed along |

Only the relevant knowledge goes into each question: the overview, the error reference and the files of the providers you use or mention.

> [!IMPORTANT]
> Cookies, tokens and API keys are **never** passed to the companion — neither in the Proxy state nor in the error context. And it will never ask you to paste them into the chat.

## Which model answers

The companion works through any enabled model of the catalogue. Choose it in the **Companion model** line of its panel:

- **Auto** — the Proxy picks a strong connected model (Claude, GPT, Gemini Pro, DeepSeek, Qwen — in that order);
- or any specific model, including a local one.

The choice is saved in the Proxy settings. If there is no model at all — on the very first start with no accounts, for example — the companion says so.

## The conversation

- **Enter** sends, **Shift+Enter** adds a line.
- While it answers, "Ask" turns into "Stop".
- An answer can be copied whole, and a code block has its own copy button.
- **⟲** starts a new conversation.
- **Esc** closes the panel.

The conversation is kept in this window and survives a Proxy restart. If the model fails, the companion shows the real error text rather than an empty answer.

## What to expect

The companion names the most likely cause first, then gives steps with tab and button names as they appear in the panel. It tells a problem on your side (an expired cookie, a busy port, a proxy) from a change on the service's side — unofficial providers sometimes change their protocol without warning, and then the fix has to happen in MemuAI itself.

When the knowledge base has no answer, the companion says it is not sure instead of inventing menu items.

## For developers

The knowledge base is a set of Markdown files in `app/companion_kb`. Every new provider needs its own `provider-<id>.md`: a project test checks that each built-in provider has one.

See also: [MemuAI Proxy](doc:proxy), [Troubleshooting](doc:troubleshooting).

## Edit an error question

“Ask AI” on an error opens the companion with an editable draft. Add context, change the wording or select another model. The request is sent only by Submit or Enter; existing typed text is preserved.
