---
title: MemuFiles
summary: The MemuAI suite's file manager — tabs, fast search, an operations queue, preview and an AI assistant; archives open like folders and can be changed right inside.
---

# MemuFiles

MemuFiles is a separate program of the MemuAI suite, a fast file manager for Windows. You pick it in the MemuAI Setup; if it was not selected, install, open, update or remove it in **Settings → MemuFiles**. Everything but the AI assistant works without MemuAI Proxy.

## What it does

- **Speed.** Folders with 100,000 files open instantly and scroll smoothly.
- **Tabs.** Pinned folders, session restore, colour tags, a two-pane mode.
- **Search** by name, mask, size, date and content — in a folder or across the PC.
- **Operations.** Drag-and-drop copy and move, an operations queue with pause and undo; deletes go to the Recycle Bin with ++Ctrl+Z++, system folders are protected.
- **Preview** of text, code, images, documents and archives in the right-hand pane.
- **AI assistant** answers questions about files and their contents and tidies up: a plan first, then your confirmation. It uses the shared [skills](doc:skills).
- **Instead of Explorer** — only if you want it (Settings → MemuFiles), undone in one click.

## Search by description

The same field accepts names, filters and short descriptions. Try `black image`, `square png`, `image 1920x1080`, `large videos from last month`, or `pdf containing "invoice number"`. Use `content:"invoice number"` to search for a phrase in text, code, PDFs with a text layer, and supported Office documents.

Color comes from image pixels. File properties supply size and date. Inspection runs locally and checks the beginning of long documents. Narrow the folder or file type for a more focused search. Quote words you want to find in the filename.

## Appearance

By default MemuFiles looks like MemuAI: the same theme, colours, accent and a matching design. Each part can be chosen on its own — in the MemuFiles settings, **Appearance** section, or quickly in **More → Look**:

- **Theme** — same as MemuAI, dark or light.
- **Palette** — the window colours: MemuAI's palettes (classic, Nord, Catppuccin, Dracula, Solarized and others) plus GitHub, Gruvbox, Monokai, One, Tokyo Night and Sepia. Each has a dark and a light face.
- **Accent colour** — selection, buttons and active items: same as MemuAI, from the palette, one of MemuAI's twelve colours or **a custom colour**. A colour that is too light or too dark is fitted to the background, so selected files and labels stay readable.
- **Design** — the shape of the window: classic (like Explorer), soft, sheet, glass, studio, grid, zen (the bars appear on hover) and brutal. "Same as MemuAI" picks the closest match to MemuAI's design.
- **Folder colour** — yellow like Windows, blue, the accent colour or neutral.

Changes apply at once, without a restart. The same settings are in MemuAI: **Settings → MemuFiles**.

## Archives

An archive opens like a folder: browse its folders, preview files, open them and extract everything, the selection, or by dragging into an ordinary folder. ZIP (plus `.jar`, `.apk`, `.epub`), TAR with `.tar.gz`, `.tar.bz2`, `.tar.xz`, and single `.gz`, `.bz2`, `.xz` files are read directly; 7z, RAR, ISO, CAB and other formats go through an installed 7-Zip.

The **Archives** section of the MemuFiles settings sets what double-clicking an archive does, the format and compression of a new archive, where to extract by default, and "Open archives in MemuFiles" — associate archives with MemuFiles in Windows.

### Archive editor

ZIP, TAR (including `.tar.gz`, `.tar.bz2`, `.tar.xz`) and — with 7-Zip installed — 7z are changed right inside, without unpacking by hand:

| Action | How |
| --- | --- |
| New folder | ++Ctrl+Shift+N++ or **New folder** on the archive bar |
| Rename | ++F2++, one item at a time |
| Delete | ++Del++ → **Delete from archive** |
| Move | ++Ctrl+X++ and ++Ctrl+V++ into another folder of the same archive, or drag |
| Add files | drag or paste (++Ctrl+V++) from the disk; when a name is taken — "Replace" or "Skip" |
| Extract | **Extract all…**, **Extract selected…** or drag into an ordinary folder |

Right-clicking inside an archive opens a menu with these actions, and the **Archive tools** submenu adds:

- **Test the archive** — reads every file and checks the checksums; damaged files are listed in the operation's result;
- **Comment…** — the comment archivers show when the archive is opened (ZIP only);
- **Convert to another format…** — ZIP, 7z, TAR.GZ, TAR.XZ, TAR.BZ2 or TAR, with its own compression and a password for ZIP and 7z (7-Zip sets it). The new archive appears next to the old one, which stays as it is.

Every change is an operation in the shared queue, visible in the status bar. The previous version of the archive goes to the Recycle Bin, so ++Ctrl+Z++ brings everything back. ZIP files move into the new version without recompression — the compression method, encryption and comment are kept; TAR keeps hard links. For a protected archive MemuFiles asks for the password and remembers it until the window closes.

RAR, ISO, CAB, single `.gz`, `.bz2`, `.xz` files and 7z without 7-Zip stay read-only: files can only be extracted from them. Copying inside one archive and moving straight between two archives is not possible — extract the files and add them again.
