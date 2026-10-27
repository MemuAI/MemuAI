---
title: What a module remembers
summary: Saved keys, where state comes from, and how to clear it.
---

# What a module remembers

A module never writes files itself. It asks the host to remember values, and the host decides which keys are allowed.

## Three sources of state

```javascript
Memu.saved          // fields from the last run (allowed keys only)
Memu.manifest       // the public part of the module description
Memu.env            // language, theme, accent, font scale, platform
```

`Memu.saved` is available the moment the page loads: the bridge embeds it in the document without waiting for a round trip. The screen therefore renders with previous data immediately, with no flicker.

## Writing

```javascript
await Memu.save({ text: "hello", model: "genspark-claude-opus-5" });
```

- A key longer than 40 characters is truncated.
- A key missing from `persist` is ignored.
- A value larger than 2 MB of JSON is not written.
- One save handles at most 200 fields.
- The full current state comes back.

The write is atomic: `persist/<id>.json` is replaced as a whole and a corrupt file is recovered automatically from MemuAI's ring backups.

```javascript
const state = await Memu.save({ draft: text });
console.log(state.draft);   // the same value, read back
```

## What to save

- User settings: model, language, panel sizes.
- Entered text or a draft.
- A small history (the last 20–50 entries).

What not to save: generated images in full, gigantic logs, anything cheap to recompute.

## Clearing

Each module card has "Clear saved fields". Deleting your own module removes its fields too. Resetting a built-in module returns the code to the shipped version while its saved fields stay — a code reset should not cost the person their data.


## The file store

`persist` fields suit settings. For large data — chats, cards with avatars, libraries — use `Memu.store` (the `storage` permission): every key is its own JSON file in `modules/store/<id>/`.

```js
await Memu.store.set("chats/alice/chat-1", { messages: [] });
const chat = await Memu.store.get("chats/alice/chat-1", null);   // the second argument is the default
const many = await Memu.store.getMany(["settings", "index"]);
const keys = await Memu.store.list("chats/alice/");               // [{ key, bytes, modified }]
await Memu.store.remove("chats/alice/chat-1");
const usage = await Memu.store.usage();                           // { bytes, keys, limit, value_limit }
```

- A key has up to three `/`-separated parts of Latin letters, digits, `.`, `_`, `-`; `..`, absolute paths and Windows device names are refused.
- One value is up to 40 MB, the whole module store up to 2 GB.
- A module only sees its own store; it is not part of the module package export.
