---
title: Model detector
summary: How to check which model really answers under a chosen name: the tokenizer, hidden instructions, the knowledge cutoff — and a visual test with no AI grading.
---

# Model detector

Third-party APIs and resellers sometimes serve a cheaper model under an expensive name. The **Model detector** tab in MemuAI Proxy asks the model a series of questions and estimates from the answers which family it belongs to: Claude, GPT, Gemini, Llama, Qwen, DeepSeek, Kimi, GLM, Grok, Mistral and others.

The result is an estimate, not proof. But several independent signals that agree usually give a clear picture.

## Running a check

1. Pick the **model to check** — any model in the Proxy catalogue, local ones included.
2. The **claimed family** is usually taken from the name: `genspark-claude-opus-5` → Claude. When the name has no family, set it by hand or leave "Auto" — the detector then just names the most likely one.
3. Pick the **depth**: quick — 14 requests, full — 17.
4. Optionally pick a model for **AI analysis of the answers**.
5. Press **Run the check**.

Requests go through the normal Proxy pipeline at temperature 0 and show on the Logs tab. A check can be stopped at any moment.

## What is checked

| Probe | How it works | How reliable |
| --- | --- | --- |
| Tokenizer | the model receives three fixed texts — prose with code, digits and symbols, other scripts — and the detector takes the input token counts reported by the provider itself. Every family has its own tokenizer and counts differently; the counts are compared with references | very high: a system prompt cannot fake token counts. Does not work when the provider reports none |
| Hidden instructions | the token count of an empty request shows how many tokens the provider adds to every message on its own. The full check also asks the model directly whether it was given instructions and what persona they set | the measurement is exact; the persona is the model's own word |
| Special tokens | the model repeats lines containing markers of different families verbatim: `<\|im_end\|>`, `<\|eot_id\|>`, `<end_of_turn>`, `[/INST]` and more. Its own end-of-turn marker cuts the answer | high, unless the provider escapes these markers |
| Knowledge cutoff | questions about dated events from 2022–2026. Where the answers stop being right is roughly the training cutoff, compared with the known date of the claimed model | medium |
| Self-identification | the model is asked in several ways who it is and who trained it, in English and in Russian; the full check repeats one question literally | low: a reseller's system prompt can fake the answer |
| Reasoning tasks | eight small tasks in one request; the number solved gives a coarse ability tier | helps spot a swap for a simpler model |
| Speed | time to the first character and answer speed — in tokens per second when the provider reports them | for reference |

Three probes of earlier versions are gone: the "greeting" (any system prompt rewrites typical phrasing), the four separate "accuracy tasks" (the reasoning batch covers them) and the "needle in a long passage" (every current model passes it). They added requests, not evidence.

### How the tokenizer check works

There are five requests: an empty one, three with the texts and the empty one again. The count of the empty request is subtracted from each text's count — whatever the provider adds on its own drops out, and the tokens of the text itself remain. The three resulting numbers are the tokenizer's fingerprint.

The references come from three sources, and the detector says which one matched:

- **computed** from published tokenizers — OpenAI (GPT-4o … GPT-5.x, the o-series, gpt-oss), Gemini and Gemma, Qwen, DeepSeek, Llama, Mistral, GLM, MiniMax, Hunyuan, Yi;
- **measured at the vendor** — Claude 3 … 4.6 and Haiku 4.5 (through Google Vertex);
- **observed through third-party gateways** — Claude Opus 4.7 and newer, GLM 5.x. Such a reference is a hint, not proof, and the detector says so.

| Result | Meaning |
| --- | --- |
| Matches | the token counts are those of the claimed family's tokenizer |
| Foreign | the counts match another family's tokenizer. With a reliable reference the verdict becomes "A substitution is likely" whatever the model says about itself |
| No reference | the counts match no known tokenizer: the model has a new tokenizer the detector does not know yet (true of Grok and Kimi today) |
| Unlike the claimed one | the counts are far from the tokenizers of the claimed family and match nobody's |
| Token counts are made up | the provider reports numbers no tokenizer produces. The tokenizer cannot be checked — and that provider's billing counts are arbitrary too |
| No token counts | the provider reports no token counts; Proxy estimates them from the text length, which is not a fingerprint |
| Recounted by the gateway | the counts match an OpenAI tokenizer, but the model names the claimed family in every phrasing and the provider adds no room for a hidden persona prompt. That happens when a reseller gateway does not pass the model's own count on and counts tokens itself with OpenAI's library (tiktoken). It is not taken as a substitution, but it confirms nothing either: confidence never reaches "high" |

At some gateways the hidden part of the request changes from one request to the next. The detector sees that from the two empty requests and still recognises the fingerprint — but marks the match as "corrected" and treats it as strong evidence rather than strict proof.

Families with the same counts (DeepSeek and Llama 3 are nearly indistinguishable, for example) are both named.

## AI analysis

When an analyst model is selected, it receives every question and answer after the probes and gives its own opinion: a distribution by family, the most likely model and a short explanation. Its estimate is blended 50/50 with the detector's heuristics.

Prefer a strong analyst from a **different** family than the model being checked, so its opinion is not biased.

## Reading the result

### Verdict

| Verdict | Meaning |
| --- | --- |
| Looks like the claimed model | the claimed family leads with at least 55 % |
| Mixed signals | the claimed family is present (25 % or more) but does not dominate. Happens with a reseller system prompt or fine-tuned models |
| Likely substituted | the answers point to a different family |
| Family identified | the model name named no family — the most likely one is shown |
| Could not tell | the model gave no recognisable signals |

Next to it is the **confidence**: high, medium or low. It depends on how many independent probes produced a signal. The collapsible **“Why this confidence — …”** block lists what raised it and what held it back: how many signals fired, whether the tokenizer was measured, whether differently-worded answers agree, how many probes gave no answer.

### Answer quality

The detector scores only the model's **visible answer**:

- **Reasoning is removed.** When a provider sends the reasoning inside the text (`<think>…</think>`, `◁think▷`, gpt-oss's `analysis` channel), it is cut out before scoring: musing “am I Claude or GPT?” no longer counts as a self-report. Such a probe is tagged “think”.
- **A budget spent on thinking.** If a reasoning model used up the reply limit on reasoning and answered nothing, the probe is repeated once with a 3000-token limit (tag “retry: thinking”). Only that probe pays for an extra request.
- **Refusals.** An answer like “sorry, I can't disclose that” is tagged “refused” and ignored — neither a self-report nor “does not know”.
- **Denials.** “I'm not ChatGPT”, “unlike Gemini”, “OpenAI-compatible API”, “I can't search Google” do not count as naming a family.
- **Guesses in the quiz.** “Probably X” or “X or Y” is not counted as knowledge. Easy-to-guess questions (the defending champion, the favourite, an heir known in advance) move the cutoff estimate only together with other right answers of the same period. A reply without a list (a paragraph, a refusal) is not lined up against the questions; it is reported as unparsed.
- **The literal repeat** in the full check counts as stable when the model names the same family and version in other words.
- **Timeouts.** Each probe waits at most 150 s (tokenizer probes 75 s), the whole check at most 10 minutes. Four probes run at once, the longest first. A transient gateway error (429, 5xx, a dropped connection, “high demand”) is retried up to twice with a pause. Probes that do not make it are tagged “timeout” and the verdict is built from what came back; if the overall limit cut the check short or a third of the probes gave no answer, confidence stays at “medium” or below.

### Family and version

The detector scores the **family** (GPT, Claude, Gemini…) and the **version** within it separately. The top-right block of the result shows two percentages labelled "family" and "version". The overall verdict uses both:

| Verdict | Meaning |
| --- | --- |
| Right family, version not contradicted | the family is confirmed and the version evidence does not contradict it |
| Right family, but the version is doubtful | the family is right, but the knowledge window and other signals fit another version better |
| Right family, probably a different version | the version evidence points to another model of the same family |

Verdicts of the "Version" block: "Version confirmed", "Version not contradicted", "Another version is more likely" (another version scored clearly higher — 1.4 times and at least 10 points), "The model names another version", "A newer model is answering", "Probably an older version", "Looks like a weaker model". A newer generation cannot know less than the previous one: a model that knows nothing newer than the previous version's cutoff counts as a sign of an older model.

### Distribution

Bars like "Claude 70 % · GPT 20 % · Qwen 10 %" show the share of signals pointing at each family, not a probability in the strict sense. The claimed family is marked with a diamond.

### Details

- **Estimated cutoff** and the date expected for the claimed model. A model that knows about events much later than it should is a strong sign of a swap for a newer one.
- **Tokenizer** — whose tokenizer it is, the three token counts and, when the provider adds something to the request, how many tokens are hidden. Hidden tokens are billed as input on every request.
- **Token that cut the answer** — the marker the model stopped at. The tile appears only when the signal fired: if the provider escapes the markers or forbids them in requests, the probe is silent and “Why” says so in one line.
- **Called itself** — what the model said about itself.
- **Every prompt and answer** — the full text of each probe; for tokenizer probes, the input token count and whose it is: the provider's or Proxy's estimate.

## Visual test

The **Visual test** block under the result is a check with no AI grading. The model from the "Model to check" field writes a page from a demanding prompt, and you judge it with your own eyes. That shows a strong model from a weak one at a glance: by a fish that has fins, or a pelican whose bicycle looks like a bicycle.

The two large buttons are ready-made tasks:

- **Aquarium** — a 3D glass aquarium with three clownfish in Three.js, one HTML file, no images or models;
- **Pelican** — an animated SVG of a pelican riding a bicycle.

Below is a list of 63 more large 3D prompts (cities, nature, paintings, simulators); **Prompt text** shows a prompt in full.

**Copy the prompt.** Next to the large Aquarium and Pelican buttons there is a **Prompt** button: it copies the task to the clipboard so you can run it in any other chat and compare models outside Proxy. The document icon beside it shows the prompt text. Prompts from the list have a **Copy prompt** button, and so do the “Prompt text” window and every row of the runs list. The model's answer window has **Copy the answer**, and in “Every prompt and answer” each probe's prompt and answer can be copied. Copying works in the app window, in a browser and when the panel is opened by its local-network address.

Press a task and the button shows the progress (seconds and characters). When the page is ready, **the same button opens the result** in a new browser tab; the ↻ next to it runs the task again. Several tasks and several models can run at once — each run appears in the list with its time, page size and token count. From the list a result can be opened, saved as an `.html` file, read as the model's full answer, or deleted; the last 40 runs are kept.

**How other AIs draw the pelican.** Under the Pelican tile a link “How different AIs draw this test” opens [pelicanbenchmark.com](https://pelicanbenchmark.com/?track=animated_html) in the browser — answers of many models to the same test.

**Side by side.** Once a task has at least two finished runs (for example on two models), their rows get a **Compare** button: a window with two live pages next to each other. Each half can show any finished run of the same task; scenes can be rotated and animations watched right there, and **Open separately** opens a half in a new tab. On a narrow screen the halves stack.

If the gateway dropped the request before the first character (“the model did not start in time”, 5xx), it is retried once by itself. If the model's answer was cut off midway, the page is saved anyway and marked "cut off". If the answer has neither HTML nor SVG, the run counts as failed and the model's answer stays available under **Answer**.

> [!NOTE]
> The page is code written by a model. It opens in the browser's sandboxed mode: scripts and CDN libraries work, but the page has no access to the Proxy panel, its data or its management.

## History and sharing

Checks are kept in the **History** column — up to the last 60. Click one to reopen the full result. A result can be **copied as a summary**, **saved as JSON** or **discussed with the companion**, which explains what the signals mean and what to do next.

## Limitations

- The tokenizer check works only where the provider reports its own token counts: custom APIs and Antigravity do; Genspark, Kiro, Grok and Gemini Web do not, and the other probes remain for them.
- New models sometimes ship a new tokenizer the detector does not know yet. "No reference" is not a sign of a swap.
- Many resellers add a system prompt like "you are assistant X". Self-identification then lies, which is why it carries little weight; the tokenizer and the cutoff are sturdier.
- The tokenizer identifies the **family** and sometimes the generation (Claude up to 4.6 and from 4.7 on use different ones), not the exact version: it will not tell Claude Sonnet from Claude Opus. The version is judged by the cutoff and the reasoning tasks.
- Fine-tuned models inherit the tokenizer and the traits of their base model.

For the best accuracy use the full check with AI analysis by a model from another provider.

See also: [Local models](doc:local-models), [MemuAI Proxy](doc:proxy).
