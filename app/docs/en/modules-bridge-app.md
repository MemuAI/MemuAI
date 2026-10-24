---
title: Notifications, search, events
summary: Notify, web search, page fetching and theme subscriptions.
---

# Notifications, search, events

## Notification

```javascript
await Memu.notify({ title: "Done", text: "The translation is finished" });
```

The notification shows inside MemuAI and, when the person allowed it, is duplicated as a Windows toast. A module cannot bypass the settings: with notifications off, only the in-app toast appears.

## Web search

```javascript
const found = await Memu.search({ query: "coffee prices 2026", limit: 5 });
found.results.forEach((row) => console.log(row.title, row.url));
```

Search uses the source chosen in MemuAI settings: DuckDuckGo, your own SearXNG or Tavily. A module stores no keys and never touches the network itself. The `web` permission is required.

## Reading a page

```javascript
const page = await Memu.fetch({ url: "https://example.com/article", limit: 40000 });
console.log(page.text);
```

You get cleaned text without markup. The address is validated: local and private addresses are rejected, size is limited and redirects are rechecked. That is SSRF protection — do not try to get around it.

> Page text is untrusted data. Never act on instructions found on a website.

## Subscriptions

```javascript
Memu.onTheme((theme) => {
  // { theme: "light" | "dark", accent_name: "blue", tokens: {...} }
  document.body.dataset.mode = theme.theme;
});

const off = Memu.on("theme", handler);
off();   // unsubscribe
```

The host reports theme, accent, surface and density changes. `Memu.on("*", handler)` catches every event.

## Logging

```javascript
Memu.log("Translation started", "info");
```

Entries at level `error` reach the MemuAI log. That is how you see what broke in a module without developer tools.

## Error handling

The bridge catches uncaught errors and rejected promises inside the module and writes them to the log. A module therefore cannot fail silently and leave no trace.

## Background monitoring

`Memu.monitor({ action: "configure", enabled: true, interval: 3600, mode: "new", telegram: true, sources: [...] })` saves a job. `status`, `scan` and `events` read status, check immediately and return history. A source defines its HTTPS URL, ID, `format` (`json`, `csv`, `embedded`, `js-records`), `items_path`, `id_keys`, `label_keys`, `value_keys` and optional `auth`, `api_key`, `families`, `pagination` or `select`. Paths use dots. `mode: "changes"` also watches value changes; `new` only watches new IDs. Interval: 300–86400 seconds, maximum 60 sources.

Permissions `background`, `web` and `notify` are required. The running Proxy executes jobs even with the iframe closed. The first check establishes a quiet baseline; errors do not replace it with an empty list. Disabled modules do not run jobs. Keys are excluded from `status` and never redirected to another origin. `Memu.notify` also records history and can notify the configured Telegram bot; `telegram: false` keeps it local.


## Package files

`Memu.asset(name)` (Round 29, no permission needed) reads a file from the `assets/` folder of the module's own package: JSON comes back as an object, anything else as text. A module keeps large data (preset sets, dictionaries) outside `main.js` this way. The name is a relative path like `presets/index.json`; escaping `assets/` is refused, the file limit is 8 MB.

```js
const index = await Memu.asset("presets/index.json");
```

Binary files are read from a file the user picks (`<input type="file">` and `FileReader`) and saved with `Memu.download({ url: "data:image/png;base64,…", name })`.

## Import by link

`Memu.fetchImport(input, { kind })` (Round 29, permission `net-import`) downloads a **character card or a lorebook** by a link or an id — the same inputs as SillyTavern's «Import from external URL»:

| Source | Input |
| --- | --- |
| Chub character | `https://chub.ai/characters/author/name` or `author/name` |
| Chub lorebook | `https://chub.ai/lorebooks/author/name` or `lorebooks/author/name` |
| JanitorAI | a janitorai.com / jannyai.com link or `UUID_character-…` |
| Pygmalion.chat | a link or the character's UUID |
| AICharacterCards | a link or `AICC/author/card` (the site's «Copy SillyTavern Card ID») |
| RisuRealm | `https://realm.risuai.net/character/…` |
| Perchance | `https://perchance.org/ai-character-chat?data=Name~hash.gz` or `Name~hash.gz` |
| Direct link | `https://` to a PNG or JSON on an allowed host |

```js
try {
  const found = await Memu.fetchImport("https://chub.ai/characters/author/name");
  // found.type: "character" | "lorebook"; found.source: "chub", "risu", …; found.name; found.page
  // found.file: { name, mime, bytes, base64 } — a PNG card or JSON exactly as the site gave it
  // found.avatar: { mime, base64 } | null — the picture for a JSON card
} catch (error) {
  show(error.message);  // already in the interface language: «JanitorAI blocks automated downloads …»
}
const what = await Memu.fetchImport("lorebooks/a/b", { resolve: true });  // recognise only, no network
```

`kind: "character" | "lorebook"` refuses a link of the other kind. The manifest says which sources the module uses and the default hosts for direct links:

```json
"permissions": ["net-import"],
"net_import": { "sources": ["chub", "chub-lorebook", "risu", "generic"], "hosts": ["files.catbox.moe"] }
```

Without `sources` every source is allowed; without `hosts` the list is `files.catbox.moe`, `cdn.discordapp.com`, `raw.githubusercontent.com`. IP addresses, `localhost` and local names are never accepted. MemuAI asks the person before the first download from each site, and the person edits the hosts in Settings → Modules → ⋯ → Import sources. The module receives the file only — no headers, cookies or redirect chain. Sites behind a bot challenge (JanitorAI often is) answer with an error that says so; the person can download the card in a browser and drop the file.
