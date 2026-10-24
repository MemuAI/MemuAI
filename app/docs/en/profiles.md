---
title: Profiles
summary: Several profiles with their own chats and memory, a PIN, avatars and statistics.
---

# Profiles

A profile is a separate "self" in MemuAI: its own chats, memory, scheduled tasks and Work projects. Profiles help when several people use one computer, or when you want to keep work and personal things apart.

## Profile and personalisation

Clicking the profile card at the bottom of the sidebar opens the profile menu, and **Open profile settings** opens the profile page:

| Field | What it is |
| --- | --- |
| Name or nickname | how to address you; the name goes into the context of normal chats |
| About you | occupation, preferences, goals — the model takes them into account |
| Your avatar | a photo or a colour gradient |
| Assistant avatar | an icon or a gradient; also changes the MemuAI icon in Windows right away — the window, taskbar, tray and shortcuts. A photo is shown in a circle, another gradient in the MemuAI speech-bubble shape; the default gradient brings back the regular app icon |
| Profile PIN | an optional lock |
| Memory facts | see [Memory](doc:memory) |

Changes are saved automatically.

## Statistics

The same page shows how many chats, messages, text tokens, images, active days and Work file changes the profile has. Statistics are computed on this computer and never sent anywhere.

## Several profiles

The profile menu (click the card):

- the profile list — click to switch;
- **Up** / **Down** — the order;
- **Create profile** — a name and an optional PIN;
- **Lock profile** — close the current profile until the PIN is entered.

Switching reloads the chosen profile's chats, memory and tasks. Appearance settings, Proxy accounts and models are shared by all profiles.

## PIN

A PIN (up to 12 characters) protects a profile from being opened by accident. When set, MemuAI asks for it when starting with that profile and when switching to it. Switching can be cancelled.

The PIN is stored as a hash and additionally protected by your Windows account. **Remove PIN** lifts the lock.

> [!NOTE]
> A PIN guards against someone at your computer, it does not encrypt data. Files in `%APPDATA%\MemuAI` are readable by anyone with access to your Windows account.

## Deleting a profile

**Delete this profile** at the bottom of the profile page. The button is shown only when there is more than one profile. The profile's chats, memory, tasks and Work project list are removed; other profiles are untouched. Your project files on disk are not touched.

See also: [Memory](doc:memory), [Where data is stored](doc:data).
