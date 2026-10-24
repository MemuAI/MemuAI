---
title: Stuck detector
summary: When the AI goes in circles in Work, Do For You, Browser, Computer Use or MemuCode, the work is paused and you decide — continue, give a hint, change the approach or stop.
---

# Stuck detector

MemuAI agents work on their own for many steps in a row: they continue the plan and the goal in [Work](doc:work), press buttons in [Do For You](doc:dfy), drive [Browser and Computer Use](doc:browser-computer) and call tools in [MemuCode](doc:memucode-cli). Sometimes a model gets stuck: it clicks the same button that does nothing, hits the same error or writes "continuing" without changing anything. Such a loop used to burn steps and money until it reached the limit.

The stuck detector notices it and **pauses** the work — it does not end it, it holds it until you decide. While the pause is open nothing runs: the action that kept repeating is not performed one more time.

## What counts as being stuck

- **The same action with no effect** — the same click, input or tool call with the same arguments while the page, the screen or the files do not change.
- **Going in circles** — the steps alternate (A → B → A → B) and the state comes back to where it was.
- **The same error** several times.
- **Nothing new** — many steps in a row only lead to pages or screens already seen.
- **A wait that drags on** — the model keeps waiting and nothing has changed for a long time.
- **The plan step does not move** — several Work plan continuations on the same step without file changes.
- **Progress in words only** — the model answers "continuing" again and again while files and state stay the same.
- **Many steps without results** — the step budget is spent without new data.
- **The model said so itself**: the DFY, Browser and Computer Use agents can send a special "stuck" action.

Normal work is not treated as a loop: waiting for a page or a build, scrolling that reveals new content, edits of different files in a row, typing into different form fields.

## The pause card

A card appears: what repeats (the last steps with a "×N" counter) and why the agent stopped. The buttons:

- **Continue** — the agent takes a fresh look and tries again. The detector becomes more patient so it does not stop you again at the same spot.
- **With a hint…** — a field opens: write what you know ("the search button is top right", "sign in first"). The text reaches the model as your note and the work goes on.
- **Change approach** — the model is told to drop the current strategy and make a new plan.
- **Stop** — the work ends. In the main window the button asks for a second click so a misclick does not end the task.

If nobody answers a pause for 30 minutes, the work stops, as before.

## Where the card shows

- **Work** (plan, goal and tool rounds), **Browser** and **Computer Use** — above the message field in the MemuAI window. If the window is minimized, a system notification comes. A new message in that chat also counts as an answer: the pause closes and the model sees your message.
- **Do For You** — in the task card in the chat and in [Quick Ask](doc:quick-ask): the task waits for an answer like with any question.
- **MemuCode CLI** — a question in the terminal with the same options; your own text is a hint. Without the interface (`-p`) nobody can answer, so the turn ends and the goal is paused (`/goal continue` resumes it).

## Sensitivity

**Settings → Work & agents → Stuck detector → Sensitivity**:

| Value | When it pauses |
| --- | --- |
| Off | Never. A clear loop just ends the run, as before |
| Low | Only on obvious loops (for example 6 identical actions in a row) |
| Normal | Recommended (4 identical actions, 3 identical errors, 5 plan iterations without changes) |
| High | Sooner, at the first signs (3 identical actions) |

The setting is shared by Work, Do For You, Browser, Computer Use and MemuCode CLI. Scrolling and arrow keys get twice the patience: they can move a list that the page snapshot does not see.

## For developers

The core is `app/stuck_detector.py` (`StuckDetector.observe()` before each step, `acknowledge()` after the decision), its copy for the window is `pgstatic/stuck.js` (`window.MemuStuck`). Open pauses: `GET /api/stuck/state`; the answer: `POST /api/stuck/decide` with `{source: browser|computer|dfy, task_id?, card_id?, decision: continue|hint|replan|stop, hint?}`. In DFY a pause is a question with `question.kind = "stuck"`, which can also be answered with `POST /api/dfy/tasks/{id}/answer` and `{decision, hint}`.
