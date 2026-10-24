---
title: Installation and updates
summary: The installer, what the bundle contains, updating in place, uninstalling and the first start.
---

# Installation and updates

## Requirements

| What | Requirement |
| --- | --- |
| System | Windows 10 or 11, 64-bit |
| Component | Microsoft Edge WebView2 (built into Windows 11, installed with Edge on Windows 10) |
| Disk space | a few hundred megabytes for the program; local models separately, usually 1 to 10 GB each |
| Internet | needed for cloud models; local models work without it |

## The installer

Run **MemuAI-Setup-<version>.exe** (for example `MemuAI-Setup-3.0.exe`). The installer speaks Russian and English; switch with the RU/EN buttons in its window. No administrator rights are needed: everything is installed for your account in `%LOCALAPPDATA%\Programs\MemuAI`.

**Install** on the first screen walks you through the steps. Each step already has the recommended choice, so pressing **Continue** is enough; **Back** (or Esc) goes one step back:

| Step | What to choose |
| --- | --- |
| Edition | the full bundle (MemuAI, Proxy and MemuCode CLI) or Proxy only — when you only need the API for other programs; MemuCode CLI can be turned off |
| Location | where to install; the installer shows how much space is needed and how much is free |
| Options | start the Proxy with Windows (on by default) so the API and schedules work right after sign-in; separately — open MemuAI after sign-in; MemuAI and Proxy shortcuts on the desktop (optional). Start menu entries are always created |
| Review | a summary of your choices; **Change** next to any line opens that step. Nothing is written until you press **Install** here |

**Express setup** on the first screen goes straight to the summary with the recommended settings — nothing is installed without your confirmation. For an update, a reinstall or going back to an older version the installer first shows what will happen (which version replaces which, where the program is, what is kept), and the options can be changed right there. The last screen offers **Open MemuAI**, "What's new" and "Open folder".

The installer verifies every file against its checksum: a damaged download is not installed and the previous version stays in place. If the installer window lacks the WebView2 component, it offers to download it from Microsoft or to install with default settings without a window.

Administrators can install silently: `MemuAI-Setup-3.0.exe /VERYSILENT` (or `/S`); `/DIR="path"`, `/COMPONENTS=memuai,memucode`, `/TASKS=autostart,desktopicon`, `/LANG=en` and `/LOG` work as well.

## First start

1. Open **MemuAI**. If the Proxy is not running yet, MemuAI starts it — an icon appears in the tray next to the clock.
2. On the first start MemuAI offers a short introduction: your name and a few words about yourself. You can skip it.
3. Add an account — see [Accounts](doc:accounts). Without accounts or local models there is nobody to answer.

## Updating

A new version installs over the old one with the same installer — including versions installed by the previous Inno Setup installer. There is no need to close the programs by hand: the installer asks them to exit and, if one stays open, offers to close it for you. Program folders are replaced as a whole, so no stale files of the old version remain. Chats, settings, accounts and models are kept: they live in `%APPDATA%\MemuAI`, not in the program folder. After an update the installer asks Windows to re-read the shortcut icons, so the new MemuAI and MemuAI Proxy icons show up on the taskbar and in the Start menu straight away.

With **automatic updates** on (Settings → General), MemuAI checks for a new version at start and every 15 minutes, downloads it in the background and shows a blue install button at the bottom of the sidebar, next to the Proxy status.

## Uninstalling

Uninstall through "Windows Settings → Apps" or the uninstall shortcut in the Start menu. Personal data in `%APPDATA%\MemuAI` is **not** removed by default: reinstalling brings everything back. To erase it too, turn off "Keep my chats, settings and accounts" in the uninstall window — or delete that folder by hand.

> [!WARNING]
> The data folder holds your accounts and chats. Save what you need before deleting it: **Settings → Data** exports every chat as one file.

## If the program does not start

- A message with a path to `startup-error.log` means the start failed early. The file is in `%APPDATA%\MemuAI\memuai\` and contains the cause — send it to the developer or the AI companion.
- A white or empty window usually means WebView2 is missing or damaged. Install "Microsoft Edge WebView2 Runtime" from Microsoft's website.
- If port 8317 is taken by another program, change the Proxy port in **Proxy → Settings → Advanced → Server and API**.

More in [Troubleshooting](doc:troubleshooting).
