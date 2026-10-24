---
title: Browser and Computer Use
summary: Isolated Browser, connecting your Chrome through the extension, Computer Use and permission boundaries.
---

# Browser and Computer Use

In Work the model has two skills for tasks that files alone cannot solve:

| Skill | What it can do |
| --- | --- |
| **Browser** | opens sites in a separate hidden Edge, reads, clicks, scrolls, fills in fields |
| **Computer Use** | sees the screens and controls the mouse and keyboard in your Windows session |

## Who decides to use them

The skills are not manual modes. The main model decides whether the current task needs a browser or computer control. The `@Browser` and `@Computer` mentions only ask it to consider that. There are no buttons to force a run.

The skills are switched off in **Settings → Work & agents → AI skills**. A disabled skill is not available to the model at all.

## Which model acts

A skill needs a model with vision: it looks at page or screen captures and decides where to click. Each skill can have its own model; with none chosen, the Vision model from the image recognition settings is used. Without a suitable model MemuAI says one has to be added in the Proxy first.

A step limit keeps a skill from running forever: 16 actions for Browser and 12 for Computer Use by default.

## Browser

- It runs in a **separate Edge profile**, not your normal browser: it has none of your passwords, bookmarks or site sign-ins.
- The page, the actions and the log are shown on the right, in a panel inside MemuAI. The panel can be hidden.
- Only `http` and `https` addresses are allowed. The local network is closed, except the `localhost` of the project you work on, so the model can check its own site.
- File downloads and entering passwords or payment data are blocked.
- Page text is untrusted data: the model does not follow instructions found on a site.

## Extension for your Chrome

To read pages and tabs using your Chrome sign-ins, connect the [Chrome · MemuAI extension](doc:integrations): **Settings → Integrations → Chrome · MemuAI**, download the ZIP, **Load unpacked** in `chrome://extensions`, then pair with the one-time code. The extension adds `chrome_*` tools and the chat side panel. Page context, control, [DFY in your Chrome](doc:dfy) and unattended schedules each have separate opt-ins. Quick Ask prefers the connected extension and retains its existing reading method for other browsers. The **Browser** skill above continues to use a separate Edge profile.

## Computer Use

- Before handing over control, MemuAI shows what the model is going to do and **waits for your confirmation**.
- While the model controls the computer, a frame is shown around every monitor.
- **++Esc++ stops the work immediately**.
- Actions with secrets, purchases, publishing and deleting data are always forbidden, whatever the settings or requests.

> [!WARNING]
> Computer Use acts in your real Windows session. Close windows with personal data before a run and do not leave the computer unattended while the model works.

## The result

The skill's report — what was done, what was seen, an error or a cancellation — goes back to the main model, which continues the task. The report is treated as untrusted data too.

See also: [Work](doc:work), [Commands](doc:commands).
