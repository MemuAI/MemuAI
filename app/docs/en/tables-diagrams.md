---
title: Tables and diagrams
summary: Tables without broken rows while streaming, sorting, filtering, export to Excel and as an image; charts and schemes right in answers.
---

# Tables and diagrams

MemuAI shows tables and diagrams as real objects: sort them, copy them, save them to files and style them as images. Everything works offline — in the main window and in [Quick Access](doc:quick-ask).

## Tables while the answer streams

While the AI is writing a table, you see a neat **"Building the table…"** placeholder with a row counter instead of half-written rows full of pipes. The table appears whole as soon as it is finished: a normal line of text follows it or the answer ends.

## Table actions

Hover a table — a toolbar appears at its top right:

![A table in an answer](docs-img/en/tables.webp "Hover a table for its toolbar: filter, copy, download, image, full screen and edit")

| Button | What it does |
|---|---|
| **Filter** | A search box for the table. Understands column conditions: `price>100`, `city:london`, `status=done` |
| **Copy** | Markdown, for Excel (TSV — paste into cell A1), CSV, HTML (for Word and email) or as an image |
| **Export** | CSV, Excel (XLSX), Markdown, JSON, HTML, PNG, SVG — the file goes to Downloads/MemuAI |
| **As an image** | The window that styles the table as a picture |
| **Fullscreen** | A large table in its own window with the same actions |
| **Edit and send back to the AI** | An editor with the table's data; after your changes the table lands in the message box |

Also:

- **sorting** — click a column header: ascending, descending, unsorted. Numbers ("1,234.5", "$1,200", "12%"), dates and text are compared correctly;
- **column width** — drag the right edge of a header; a double click resets it.

## Your own table in a message

The **"+"** menu next to the message box → **Table**. The editor opens:

- **New grid** of the size you need, buttons to add and remove rows and columns, alignment, swapping rows and columns;
- **Paste from Excel/CSV** — a range from Excel, Google Sheets or LibreOffice; ++Ctrl+V++ in a cell pastes a range starting there;
- **Import a file** — `.csv`, `.tsv`, `.txt`, `.md`, `.json` and Excel workbooks `.xlsx` (you pick the sheet; the first 2000 rows and 60 columns). You can also drop the file into the window;
- navigation: ++Tab++ and ++Enter++ — next cell, arrow keys, ++Ctrl+Z++ — undo.

**Attach** the finished table to the message as a Markdown file or **insert it as text** into the message box.

## Table as an image

The "Table as an image" window renders the table to PNG or SVG with a live preview:

- **styles**: minimal ones (Minimal, Clean, Lines, Zebra, GitHub, Notion, Spreadsheet, Print) and bold ones (Newspaper, Paper, Terminal, Pastel, Sunset, Ocean, Glass, Midnight, Neon, Blueprint, Mint);
- **background**: none, a color, a gradient with an angle or your own image with dimming;
- colors of the text, header, lines, zebra stripes and title; font and size; spacing; corner radius; shadow; header style;
- **title** and **caption** (for example, the data source);
- **scale** 1×–4× for a crisp image on any screen.

The last style you used is remembered and used by the quick "Copy → As an image" and "Export → PNG".

## Charts and schemes

The AI can draw:

![A chart in an answer](docs-img/en/chart.webp "A chart drawn from the model's answer; the buttons on top: style, copy, download, code and full screen")

- **charts**: line, bar (grouped, stacked, horizontal), area, pie and donut, scatter and bubble, radar, combo;
- **schemes**: flowcharts, sequence diagrams, mind maps, ER diagrams, Gantt charts, timelines, class and state diagrams.

Just ask: "show this as a chart", "draw a flowchart of the process", "make a Gantt chart". Without a request the AI draws only when it truly helps — for example, a series of numbers that is easier to see than to read. Simple questions never get diagrams.

### Setting

**Settings → Chat & replies → Charts and diagrams**:

| Mode | Behavior |
|---|---|
| **Only when really useful** | On request and where numbers or a complex structure clearly read better as a picture |
| **Only on request, otherwise offer** | Draws only on request; when a chart would help, the AI briefly offers one |
| **Off** | The AI does not draw diagrams on its own |

### Diagram actions

The toolbar above a diagram:

- **Customize** — chart type (bars ↔ lines ↔ area ↔ pie…), theme, palette (including a colorblind-safe one), font and text size, legend, grid, value labels, smoothing, stacking, line width, size; for schemes — the Mermaid theme, the hand-drawn style, edge shape and accent color. The look can be applied to all new diagrams;
- **Copy as an image**;
- **Download** — PNG 1×–4×, SVG, and for charts also the data: CSV, XLSX, a Markdown table and the source JSON;
- **Code** — show the diagram's source;
- **Fullscreen** — ++Ctrl++ + mouse wheel zooms a large scheme.

Clicking a legend item hides or shows that data series.

### If a diagram could not be drawn

Instead of an empty space you see a clear error message, a **Code** button and **Fix with AI** — it asks the AI to send a corrected version.

## Chat export

When you [export a chat](doc:chat-export), tables and diagrams are kept: as vectors in HTML and PDF, as images in Word.
