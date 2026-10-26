---
title: Module catalogue
summary: Install repository additions, update or roll back packages, contribute modules and choose another source.
---

# Module catalogue

Open **Settings → Modules → Catalogue**. Search by name, author or tags, choose a
category, or show installed modules only. Select a card to inspect its description,
author, license, package size, version and permissions before installation.

The official source is `module-catalog/index.json` in the
[MemuAI/MemuAI repository](https://github.com/MemuAI/MemuAI). Maintainers review
source manually before merging pull requests. MemuAI checks package size and
SHA-256 against the entry and matches manifest permissions with those shown.
Checksums protect download integrity; they do not replace source review.

## Inspect source before installing

Open **Source code** in the module details. MemuAI downloads the package, verifies
its size, SHA-256 and format, then shows the manifest, HTML, CSS, JavaScript,
translations and declared additional files. Source is displayed as text: the module
is neither installed nor executed. Switch files, copy the selected source, or
download individual files and the complete verified package.

Inspection also works for packages requiring a newer app version, while installation
remains unavailable. This shows the current catalogue package, not local edits to
an already installed module.

## Install, update and restore

Choose **Install** in the module details. The new module stays disabled; return
to **My modules** and enable it. Updated modules are disabled too. Updates preserve
module data, launcher placement and window settings.

When the package changes, the card shows **Update available**. MemuAI retains
the previous code before replacing it. Local code edits require explicit consent
to replace. **Restore previous version** rolls back the code; the two most recent
code backups are retained. Saved module data is not rolled back. If new code
changed the data format, compatibility with old code depends on the author.

A catalogue package cannot replace a built-in module or an unrelated module
with the same ID. Packages requiring a newer app version or unknown permissions
cannot be installed.

Modules and the catalogue source belong to the whole application. Changing the
active profile during an install rejects the stale action; profiles do not have
separate module collections.

## Offline use

MemuAI caches the downloaded index. If the official source is unavailable on
first use, it shows the bundled copy with an explicit notice. Bundled packages
can be installed offline. A cached remote index alone does not include its packages.

## Contribute a module

Choose **Contribute a module**, select a custom module and enter its author,
license, category and tags. **Prepare files** creates a portable JSON package and
a catalogue entry with the correct size and SHA-256. Saved personal module data
is excluded from the export.

Place the package in `module-catalog/packages/`, append the entry to `modules`
in `module-catalog/index.json`, and run `python tools/check_module_catalog.py --write`.
This also refreshes the bundled fallback. Open a pull request and follow the
[contribution guide](https://github.com/MemuAI/MemuAI/blob/main/module-catalog/CONTRIBUTING.md).
Every official entry requires manual maintainer review.

## Another source

Expand **Catalogue source** at the bottom. Enter a compatible public HTTPS JSON
catalogue URL and save. Readable GitHub file URLs are converted to raw URLs.
Packages must be hosted on the same origin. Local addresses, URL credentials
and nonstandard ports are not supported.

This lets you choose a community-maintained catalogue if official development
stops. Its owner is responsible for its code; the MemuAI team does not review
third-party entries. **Official** restores the original source URL. Existing
installed modules remain in place.
