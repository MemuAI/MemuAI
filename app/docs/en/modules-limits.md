---
title: Limits and performance
summary: Every numeric limit in one place.
---

# Limits and performance

The limits protect MemuAI from accidental overload. Below are all the numbers worth knowing.

## Code and description

| Item | Limit |
| --- | --- |
| Identifier | 41 characters, latin/digits/hyphen, starts with a letter |
| HTML, CSS or JS each | 400 000 characters |
| `instructions` | 20 000 characters |
| Title, description | 80 and 400 characters |
| Saved keys in total | 32 |
| Key name length | 40 characters |
| Fields per single save | 200 |
| Field value | 2 MB of JSON |

## Requests

| Item | Limit |
| --- | --- |
| History entries per request | last 40 |
| Length of one history entry | 200 000 characters |
| Images per request | 8 |
| Documents per request | 8 |
| Document text length | 400 000 characters |
| Regular request timeout | 5 minutes |
| Video timeout | 30 minutes |
| Image timeout | 15 minutes |
| Transcription timeout | 10 minutes |

## Window

| Item | Range |
| --- | --- |
| Width | 240–2400 |
| Height | 200–1600 |
| Window minimum | 200×140 |
| Window maximum | 4000×4000 |

## Performance

- The module page is assembled once, on open. Everything after that runs in memory.
- Cache aggressively: do not redraw a whole list on every keystroke.
- Split heavy work through `requestAnimationFrame` or `setTimeout`, otherwise the interface freezes.
- For long lists render the first 100 rows and a "show more" button.
- Do not keep images in `persist`: closing the module becomes noticeably slower.

## Isolation

| Item | How |
| --- | --- |
| Access to the MemuAI page | none |
| Direct network access | none |
| Reading files from disk | none |
| Writing files | only through `Memu.download`, into Downloads/MemuAI |
| Access to chats, memory, profile | only through the bridge within permissions |
| Windows windows | never created |
