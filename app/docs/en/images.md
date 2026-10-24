---
title: Images
summary: Creating pictures, format, references, the viewer, saving, and sending images to models.
---

# Images

MemuAI can both draw pictures and show them to models with vision.

## Creating an image

Ways to do it:

- just ask in an ordinary chat — “draw …”, “show me what … looks like”: the AI works out that a picture is needed and draws it with the model from **Settings → Models → Images, video and decisions**. Details in [Pictures and video in the chat](doc:media);
- the `/image <description>` command (or `/img`) or **＋ → Create an image** — the image model draws it right away without changing the chat's model;
- pick a drawing model in the model list and write a description.

While a drawing model is selected, a **format** choice appears in the top bar: Auto, 1:1, 16:9, 9:16, 4:3, 3:4. The format is remembered.

Pictures attached to a message for a drawing model go to it as **references** — samples of style, composition or a character.

> [!NOTE]
> Web services sometimes require a browser check (a captcha) for generation. If an error about a check arrives instead of a picture, open the service's site in a browser, pass the check and refresh the cookie in the Proxy.

## Viewing

Clicking a picture — yours or a generated one — opens the viewer:

| Action | How |
| --- | --- |
| Zoom | the mouse wheel or the ＋ − buttons |
| Fit / 100 % | double-click or the "Fit" button |
| Download | the download button |
| Open in another program | the "Open" button |
| Menu | right-click the picture |

## Saving

Downloaded images and files go to `Downloads\MemuAI`. After saving, a **Show in folder** button appears.

## Pictures for models

Paste an image from the clipboard (++Ctrl+V++), drag it into the window or pick it with the paper clip. PNG, JPEG, GIF and WebP are supported.

Models with vision see the picture directly. For the rest there is the **private vision bridge**: a vision model first describes the image and the chat model receives the text description. The behaviour is set in **Settings → Images & video → Sending images** — details in [Models](doc:models#how-images-are-sent).

Retrying or editing a message sends its pictures again rather than losing them.

The image analysis model (**Settings → Images & video → Recognition model**) is **shared with [MemuCode CLI](doc:memucode-cli)**: the `/vision` command in the terminal changes the same setting, and the terminal decides by itself when to use a description (see "Images and the image model" in the MemuCode CLI help).

## Images and context

Pictures take room in the model's context window. The [context meter](doc:context) counts them; clicking the meter stops counting images if the estimate gets in the way.

## The MemuAI Studio module

For serious image work there is the built-in **MemuAI Studio** module: models, sizes, style, exclusions, references, a result grid, variations, video and speech on one screen. Modules are enabled in **Settings → Modules** — see [Built-in modules](doc:modules-builtins).

See also: [Composer](doc:composer), [Models](doc:models).
