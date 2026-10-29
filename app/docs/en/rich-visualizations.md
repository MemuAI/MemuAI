---
title: Visualizations in answers
summary: Flows, rankings, entity cards, interactive tools and readable text copying.
---

# Visualizations in answers

MemuAI can place branching flows, sortable rankings, comparisons, charts, tables, timelines, illustrated cards, tabs, expandable explanations and small calculators anywhere in an answer. Native blocks follow the app theme and support keyboard interaction. Charts include an expandable data table.

An underlined entity name opens a panel on the right with a summary, images, facts and sources supplied in that message. The panel does not independently verify those facts: the model should provide reliable sources. Remote images pass through the protected media proxy; local network destinations are blocked.

Under **Settings → Chat & replies → What answers may contain**, disable all visualizations, just custom HTML blocks or just images. Disabled blocks retain readable text. The `/visual` command explicitly requests a visual answer for the current chat.

Copying and exporting messages preserves names, values, steps and explanations rather than JSON or interface code. Custom blocks use their model-supplied text summary.

## Tools for models and prompt authors

`visual_schema` returns supported shapes and components. `visual_validate` checks a block before output. These local tools do not call a provider or modify files. Regular chats and Teams share the renderer.

Native blocks contain JSON in a `memuai-visual` fence:

```json
{"type":"text","title":"Options","blocks":[{"type":"ranking","max":10,"items":[{"name":"First","value":9,"detail":"Evidence for the rating"}]}]}
```

Components include `flow`, `branch`, `ranking`, `comparison`, `entity`, `chart`, `table`, `timeline`, `metrics`, `cards`, `columns`, `tabs`, `accordion`, `calculator`, `progress`, `callout`, `list`, `code`, `quote` and text. Calculator formulas accept arithmetic and a limited set of mathematical functions without executing JavaScript.

For clickable terms, supply a `memuai-entity` JSON fence with `id`, `name`, `summary`, optional `image`, `facts`, `sources`, and `inline_only:true`, then write `[[entity:id|Visible name]]` outside code. The panel uses only the supplied data.

Custom variant: `{"type":"custom","summary":"Readable text explanation","html":"<div>…</div>"}`. It runs in a separate iframe without MemuAI data, cookies, storage, external scripts or network access. Legacy HTML visualizations remain compatible. Scientific charts and publication materials are better produced as standalone files.
