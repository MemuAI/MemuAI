---
title: Model requests
summary: request, stream, history, permissions and full instruction replacement.
---

# Model requests

## One complete answer

```javascript
const answer = await Memu.request({
  model: "genspark-claude-opus-5",
  instructions: "Translate into English. Return only the translation.",
  text: "Hello, world",
  images: ["data:image/png;base64,..."],
  documents: [{ name: "brief.txt", text: "..." }],
  keep_in_chat: false,
  thinking: true,
  web_search: false,
  temperature: 0.3
});
console.log(answer.text);
```

The answer is `{ text, model, images, documents, chat, keep_in_chat }`. The `chat` field appears only when `keep_in_chat` is true — that is chat `module-<id>`, immediately visible in the MemuAI chat list.

## Streaming

```javascript
await Memu.stream({
  model: Memu.saved.model,
  history: messages,        // [{role: "user"|"assistant", content: "..."}]
  text: "Tell me about the moon"
}, {
  onDelta(chunk) { output.textContent += chunk; },
  onStatus(status) { status.textContent = status; },
  onDone(result) { status.textContent = "Done"; },
  onError(message) { status.textContent = message; }
});
```

`onStatus` receives `queued`, `working`, `streaming` and `done`. While the model is thinking the host sends `tick` events so the interface can show progress.

`Memu.stream` returns a promise with a `cancel()` method:

```javascript
const job = Memu.stream(body, handlers);
document.querySelector("#stop").onclick = () => job.cancel();
```

Cancelling stops the request at the provider instead of only hiding the indicator.

## History

`history` holds the previous turns. The bridge passes the last 40 entries, each truncated to 200 000 characters, and always appends the current `text` as the final user message.

```javascript
const conversation = [
  { role: "user", content: "What is 2+2?" },
  { role: "assistant", content: "4" },
];
await Memu.stream({ history: conversation, text: "And times three?" }, handlers);
```

To read what already accumulated in the module chat:

```javascript
const chat = await Memu.history();
console.log(chat.messages);
```

## Replacing the instructions

`instructions` replaces the system text instead of adding to it. That is what turns a module into a translator, an editor, a tutor — anything:

```javascript
instructions: [
  "You are an editor. You explain nothing.",
  "You return only the corrected text.",
  "You keep the author's style, names and numbers."
].join(" ")
```

Instructions are built in module code, so they can change as the module works: add a line about the language, the answer format, the tone.

## Models

```javascript
const models = await Memu.models();
// [{ id, name, provider, image: true, vision: true, thinking: true, web_search: true }]
```

The list comes from MemuAI and needs no keys. For a model picker, default to `Memu.manifest.bridge.model`, and when it is empty to the first model in the list without `image: true`. A request with no model at all (`model: ""`) is answered by MemuAI's default chat model. Keep the person's choice in `persist` (for example, a `model` field).

## The module chat

`keep_in_chat: true` appends the request and the answer to chat `module-<id>`. Such a chat:

- appears in the MemuAI chat list;
- is titled after the module;
- grows with each message pair;
- stays separate from ordinary conversations.

It is useful when a module does something substantial and the result should be reread or continued in a normal chat later.


## A full message array

`Memu.complete` (Round 29) sends the model a ready message array with `system`, `user` and `assistant` roles in any order — like the Chat Completion API — plus sampler parameters. It needs the `chat` permission. The answer streams like `Memu.stream`, plus the thoughts and the real token usage.

```js
const job = Memu.complete({
  model: "provider/model",
  messages: [
    { role: "system", content: "You are a storyteller." },
    { role: "user", content: "Begin the story." },
    { role: "assistant", content: "Once upon" }          // prefill: the model continues it
  ],
  params: { temperature: 0.9, top_p: 0.95, max_tokens: 800, presence_penalty: 0.3,
            top_k: 40, min_p: 0.05, seed: 7, stop: ["\nUser:"], effort: "high", thinking: true,
            prompt_caching: true }
}, {
  onDelta(text) { /* a piece of the answer */ },
  onReasoning(text) { /* a piece of the thoughts */ },
  onUsage(usage) { /* prompt_tokens, completion_tokens */ }
});
const result = await job;   // { text, reasoning, usage, finish_reason, model, ms }
job.cancel();               // stop it
```

- `content` is a string or an array of `text` / `image_url` parts (`data:image/...` only).
- Neutral values (`top_k: 0`, `0` penalties, `repetition_penalty: 1`, `seed: -1`) are not sent: many APIs reject unknown fields.
- Prompt caching is on by default where the provider supports it.
- The built-in [Characters](doc:characters) module uses it.
