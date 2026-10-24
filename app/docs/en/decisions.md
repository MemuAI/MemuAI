---
title: Decision models (Decisions)
summary: What decision models (Jev, Kev, Span, Solar, Mercury, Tev…) and the Decisions format are, how it differs from an ordinary AI, how to read its probabilities and how MemuCode relies on it.
---

# Decision models (Decisions)

A **decision model** is a new kind of AI that appeared in September 2026. It does not write text. It is given a situation and a question with answer options defined in advance, and it returns a **number**: how likely each option is. The first such model is **Jev** by TypeSafe AI; the request format is called **Decisions**. Others followed — Kev 4B, Span-01, Solar Decide, Mercury Decide, Tev1 4B and more; OpenRouter lists them all under one heading and MemuCode reads that list live.

MemuAI uses a decision model in [MemuCode CLI](doc:memucode-cli): wherever the agent has to decide quickly "is this safe?", "is the task hard?", "is the goal reached?" — without spending the seconds and tokens of a large model.

## A plain explanation

An ordinary language model is like an employee who was asked a question: they think, write an answer, and sometimes get it wrong or answer something else. A decision model is like a turnstile with a very clever sensor: it explains nothing, but instantly says "let through" or "do not" — and how sure it is.

Developers describe it as a "smart `if`". Instead of a rigid condition in code —

```text
if the command contains "rm -rf" → ask the user
```

— you can ask by meaning:

```text
question: "Does this command delete the user's data?"
answer:   0.03   (almost certainly not)
```

The name **System One** comes from Daniel Kahneman's "Thinking, Fast and Slow": System 1 is fast intuition, System 2 is slow reasoning. Ordinary models with "thinking" are System 2. Jev is System 1: an instant judgement with no reasoning.

## How it differs from an ordinary model

| | Ordinary model (GPT, Claude, Gemini) | Decision model (Jev) |
| --- | --- | --- |
| What it returns | text | numbers: probabilities of predefined answers |
| How it works | writes the answer token by token | one pass: every question is evaluated at once, in parallel |
| Speed | seconds | fractions of a second (TypeSafe states 70–500 ms, usually about 100 ms) |
| Price | $0.2 to $10 and more per million input tokens | about $0.042 per million input tokens, the answer is free |
| Can make up an answer | yes | no: it can only choose from what you gave it |
| Can explain why | yes | no |
| What it accepts | text, images, files | text and JSON only, up to ~32,000 tokens |

The main consequence: a decision model **cannot "hallucinate" an answer**. It will not invent a command or an option that does not exist — the answer holds only your options and their probabilities.

## Three kinds of questions

A request consists of a **state** (`state` — the text or JSON being asked about) and one or more **questions** (`questions`).

### Noul — "yes or no"

A statement about the state; the answer is the probability from 0 to 1 that it is true.

```json
{
  "state": "The user wrote: please refund my last payment",
  "questions": {
    "refund": { "type": "noul", "instructions": "The message asks for a refund" }
  }
}
```

Answer: `{"refund": {"type": "noul", "noul": 0.98}}`. A value near `0.5` is an honest "I do not know": it is not a "no" but a third outcome, and it deserves its own handling.

### Choice — picking an option

Up to 255 options, each with a description. The answer is the chosen option, the probability of each one and an overall confidence.

```json
{
  "state": "The invoice shows two charges for the Pro plan",
  "questions": {
    "team": {
      "type": "choice",
      "instructions": "Which team should handle this?",
      "criteria": {
        "billing": "Charges, invoices, refunds",
        "technical": "Bugs, outages, features",
        "account": "Sign-in, password, profile"
      }
    }
  }
}
```

Answer: `{"choice": "billing", "probabilities": {"billing": 0.97, "technical": 0.02, "account": 0.01}, "confidence": 0.95}`.

### Score — a rating on a scale

From 2 to 10 ordered levels described in words. The answer is a position on the scale (a probability-weighted mean) and the probability of each level. This is how MemuCode rates the risk of an action: "read-only" → "an ordinary project edit" → "hard to undo" → "destructive".

## How to read the numbers

- **The probability is calibrated.** TypeSafe trained Jev so that answers given a 0.9 probability turn out right about 90% of the time. An ordinary model saying "I am 90% sure" guarantees nothing of the kind. Calibration holds *on average over many answers*, not for each one separately.
- **`confidence`** (for Choice and Score) shows how concentrated the probability is on one option. High confidence means the model is not hesitating; it does not yet mean it is right.
- **The caller picks the thresholds.** The usual scheme: high confidence — act on your own, medium — ask a person, low — do not decide automatically.

> [!IMPORTANT]
> A decision model is a helper, not a judge. It rates what it was shown and can be wrong on an unusual case. That is why MemuCode uses it to *ask less often about safe things*, while destructive actions still need your confirmation.

## What a decision model cannot do

- write text, code or explanations;
- count: arithmetic, counting characters, comparing large numbers and dates go badly;
- extract values ("find the order number") — it only chooses from what is offered;
- see images, hear audio, call tools or hold a conversation;
- reason in several steps — ordinary models are for that.

Jev's weights are closed and there is no architecture paper; the speed, price and calibration figures come from TypeSafe AI and OpenRouter themselves.

## How MemuCode uses a decision model

| Where | What is asked | What happens with the answer |
| --- | --- | --- |
| The "Auto" permission mode | the risk of the action on a four-level scale (with the tool, the folder and the last answers of yours as context); whether it serves your request; whether it sends data to an outsider; whether it destroys what cannot be restored | the action runs without a question only when the risk is below 0.34 and the model is sure of it, the fit to the task above 0.55, the leak probability below 0.3 and the destruction probability below 0.25; otherwise MemuCode asks you. A short list of unmistakable cases (`git push`, publishing, secrets, `sudo`…) always asks and never reaches the model |
| `/model auto`, `/agents` | how complex the request is; which of several models fits (the options are the models with your remarks about them) | complexity from 0.33 — the everyday level, from 0.62 — the hard one; among several models of a level the one the decision model chooses |
| Skill hint and plan mode | which skill fits the request (at least 50%); whether the task is large (at least 80%) | a hint to the model and a line for you; a dialog offering to plan first (not more than once in five turns) |
| `/goal` | whether the agent's report shows the goal is reached; whether the user's help is needed | the goal counts as reached at a probability of 0.85 or more |
| `/decide` and the `Decide` tool | any question of yours | a number in the answer |

All questions of one decision go in **one request**: independent questions are evaluated in parallel, which is cheaper and faster than asking them one by one. Before a request starts, the questions of its model about difficulty, skill and plan go together in one call, with a limit of a few seconds. Identical requests are served from a cache for ten minutes. A model built for one kind of question (Span: yes/no; Tev1: pick one) is not asked another.

When no decision model is connected or it did not answer, MemuCode does not stop: the fast model (`/small-model`) rates the risk, and without one — the built-in rules. A decision model just makes it faster, cheaper and more accurate.

## Connecting

The `/decisions` command in MemuCode CLI:

| Option | What it needs |
| --- | --- |
| **OpenRouter** (recommended) | your OpenRouter key — or the OpenRouter connection you already added in `/proxy add`; then **any** decision model from the live catalogue: Jev Latest (always the newest Jev), Jev 1.13, Kev 4B, Span-01, Span-01 Lite (free), Solar Decide (512k context), Mercury Decide (free), Tev1 4B Experimental and what is added next. The picker shows the price, the context, what each model answers and your own remarks; search by name, maker or "free" |
| **TypeSafe directly** | a key from typesafe.ai (or the `TYPESAFE_API_KEY` variable), model `jev-latest` |
| **A compatible endpoint** | any service with the same request format |
| **An ordinary model** | no key: a chat model answers in the same format. Slower and with no calibration guarantee, so it is not used for the "Auto" mode or for model routing |

The same menu has **A model for each check** (a free model for the skill hint, a careful one for Auto's risk check), **Backup models** (asked in order when the chosen one fails), **What to use it for** (switches individual uses on and off), **Remarks about decision models** and **Test the connection**, which tries every model in use and shows each one's latency. The spend is visible in `/usage` as the "Decision model" row; a typical check costs thousandths of a cent.

> [!NOTE]
> Sign-up on typesafe.ai is sometimes paused because of demand. Through OpenRouter the model is available with an ordinary OpenRouter key.

## Asking by hand

```text
/decide The service went down after the update, customers cannot sign in ? This is an urgent incident
/decide Hi, how are you ? What kind of message is this | question | greeting | complaint
```

The first form is a "yes or no" question (Noul), the second a choice between options (Choice). MemuCode's main model can also ask such questions itself — for example to sort a hundred log lines cheaply.

## Frequent questions

**Do I need a decision model to use MemuCode?** No. Everything works without one; in the "Auto" mode the fast model or the rules simply make the decisions.

**Is my code sent to TypeSafe?** A request holds only what the question is about: the text of your request and the proposed action (a command, a fragment of an edit) — up to a few thousand characters. The whole project is not sent.

**Why did it answer 0.5?** Because the data it was shown does not settle the question. That is a normal, useful answer: MemuCode asks you in that case.

**Will it replace ordinary models?** No. They are different tools: an ordinary model does the work, a decision model makes the small decisions around it.

See also: [MemuCode CLI](doc:memucode-cli#decision-models), [Working effectively with MemuCode CLI](doc:memucode-guide), [Glossary](doc:glossary).
