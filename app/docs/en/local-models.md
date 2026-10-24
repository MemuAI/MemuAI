---
title: Local models
summary: GGUF models on your own computer: the model wizard, installing the engine, searching Hugging Face, files from disk, settings and errors.
---

# Local models

The **Local models** tab in MemuAI Proxy runs open models right on your computer. Answering needs neither internet nor an account: the internet is needed once, to download the engine and the model itself.

Local models work like any other: they appear in the MemuAI model list and in the Proxy API with the `local-` prefix, for example `local-qwen3-8b-q4-k-m`. A "Local models" account is created for them automatically on the Accounts tab.

## What you need

| What | Why |
| --- | --- |
| The llama.cpp engine | the `llama-server` program that loads a model and answers requests |
| A `.gguf` model file | the model weights |
| RAM or video memory | the whole model is loaded into memory |

The right side of the tab shows how much memory the computer has, which graphics card and how many CPU threads. MemuAI uses them to estimate which models will run here.

## The model wizard

Not sure which model to pick? Press **Find a model** on the card at the top of the tab. The wizard has four steps:

1. **Computer** — what was found: the graphics card and its video memory (exact, through DXGI and `nvidia-smi`), the CPU (cores, threads, AVX2/AVX-512), the amount and type of RAM, free disk space and whether the engine is installed. **Another computer…** lets you enter video memory and RAM by hand — for example to plan for another PC.
2. **Tasks** — conversation, coding, agents and tools (MemuCode), images and screenshots, hard reasoning, long documents; the language you will use (Russian, English, both).
3. **Priorities** — quality or speed, context length (auto or 8K–128K), how much memory to leave for other programs, a licence condition (any, commercial use allowed, Apache 2.0/MIT only) and, optionally, the MTP speed-up.
4. **Recommendations** — ranked models. Each shows the quantisation (you can pick another), the estimated speed in tokens per second, where it runs (fully on the GPU, GPU + RAM, CPU), the download size, context, licence, languages and capabilities, an explanation "why" and warnings. Models that did not fit are listed below with the reason.

**Install and set up** does everything at once: installs the engine if it is missing, downloads the right file (and the image projector when you need images), and configures the model as soon as the download ends — name, context, recommended sampling parameters, CPU threads. **Start and try** then loads it and shows the first answer and the speed; **Open in chat** takes it to the Chat tab.

Speed is an estimate from memory bandwidth (±25%): real speed depends on the driver, conversation length and load. Quality scores are comparative, from the models' published benchmarks. The wizard takes its models from a built-in catalogue; the **refresh data** link at the bottom re-reads files, sizes and model parameters from Hugging Face.

## Step 1. The engine

Press **Install the engine**. MemuAI downloads the latest llama.cpp build from the official GitHub releases page into the data folder. It takes 30 to 400 MB depending on the build.

| Build | For |
| --- | --- |
| Pick by graphics card | NVIDIA → CUDA, other cards → Vulkan, no card → CPU |
| CUDA | NVIDIA cards; needs a recent driver. The CUDA runtime libraries come with it |
| Vulkan | almost any card: AMD, Intel, NVIDIA |
| CPU only | no graphics card, or when the other builds do not start |

**Update** later downloads a fresh build. If you already have your own `llama-server` (built for ROCm, for example), set its path in the tab's **Settings** — then the built-in engine is not needed.

> [!NOTE]
> Automatic installation works on Windows. On other systems set the path to your own `llama-server`.

## Step 2. A model

### Searching Hugging Face

Type a name — `qwen3 8b` or `gemma 3` — and press **Search**. Only repositories with GGUF files are listed. Sorting: most downloaded, trending, most liked, recently updated.

Click a repository to open its files. Each shows its quantisation, size and whether it will run here:

| Estimate | Meaning |
| --- | --- |
| Fits in VRAM | the model fits in the graphics card entirely — the fastest option |
| GPU + RAM | some layers on the graphics card, the rest in system memory — llama.cpp splits the model itself |
| Runs from RAM | the model runs from system memory — slower, but it works |
| Tight fit | memory is barely enough, the computer may slow down |
| Too big | it will most likely not start |

The estimate counts the weights plus about 8 thousand tokens of context. One file is marked **recommended** — the largest one that fits well, preferring the common quantisations.

Split models (`…-00001-of-00003.gguf`) appear as one row and download whole. When the repository has an **image projector** (`mmproj`), a choice appears above the table — with it the model can look at pictures.

Downloads run in the background with progress, speed and time left. They can be cancelled; after a dropped connection they resume where they stopped. A finished download appears in the list straight away.

> [!TIP]
> **Which quantisation.** `Q4_K_M` is the best balance of quality and size. `Q5_K_M` and `Q6_K` are slightly better and heavier. `Q8_0` is nearly lossless. `Q3`, `Q2` and `IQ` are for weak computers, with a visible loss of quality. As a guide, a 7–8 billion parameter model in `Q4_K_M` is about 5 GB.

### Gated repositories

Some models (Llama, Gemma) are behind a licence. Accept it on the Hugging Face site, create an access token and paste it into **Proxy → Local models → Settings → Hugging Face token**. The token stays on this computer and is sent only to huggingface.co.

### A file from disk

**＋ File from disk** opens a file picker. The model can stay where it is — the Proxy then uses it in place and never deletes it — or be copied into the MemuAI folder. For a split model pick the first part.

## Step 3. Using the model

| Button | What it does |
| --- | --- |
| Load | loads the model into memory now so the first answer does not wait |
| Unload | frees the memory |
| Try it | sends a short question and shows the answer and speed |
| Configure | the model's parameters |
| ⋯ | server log, enable/disable, file path, Hugging Face page, check in the detector, delete |

Local models support tool calls (function calling) through the Proxy API — they work in MemuCode and agents when the model itself can do it. The model's thinking (`<think>`) is shown apart from the answer.

Loading by hand is optional: the model loads itself on the first request. That first answer takes longer — from a few seconds to a minute.

### Model settings

| Parameter | Meaning |
| --- | --- |
| Display name | how the model is labelled in lists |
| Available in the API | a disabled model leaves the catalogue |
| Context length | how many tokens the model keeps; more means more memory |
| Layers on the GPU | `-1` — auto: as many as fit in video memory (a recent llama.cpp picks them itself), `0` — CPU only |
| CPU threads, batch size | `0` — llama.cpp decides |
| Unload after idle | minutes without requests before memory is freed; `0` — keep loaded |
| Answer limit | the answer length cap when the client sets none |
| Sampling parameters | temperature, top P, top K, min P, repeat penalty — used when the client sends none |
| System prompt | added when the request has no system message of its own |
| Image projector | the `mmproj` file for vision models |
| Extra arguments | any `llama-server` flags, for example `--flash-attn on` |

The name, sampling parameters and system prompt apply at once. Context, layers, threads and arguments apply on the next load: a model already in memory is unloaded so it starts with the new values.

### General settings

**Settings** above the model list:

- **Models in memory at once** — one by default. Requesting another model unloads the least recently used one.
- **Default CPU threads**.
- **Your own llama-server** — the path to your own build.
- **Hugging Face token**.

## Other local servers

If Ollama, LM Studio, llamafile, Jan, KoboldCpp, text-generation-webui or vLLM already run on the computer, press **Scan this computer**. The Proxy checks their standard ports (11434, 1234, 8080, 1337, 5001, 5000, 8000) and offers to **Connect** what it finds as a "Custom API". Their models appear with the server's prefix, for example `ollama-llama3.2`.

## Errors

| Message | What to do |
| --- | --- |
| llama-server exited with code … | open ⋯ → **Server log**. `failed to allocate` or `out of memory` — lower the context or GPU layers, or take a smaller quantisation. `unknown argument` — remove the extra argument or update the engine |
| The model did not load in time | a very large model on a slow disk — retry |
| The model cannot see images | add an `mmproj` projector in the settings or do not attach a picture |
| llama-server crashed: memory access violation (code 0xC0000005) | most often a draft file (an MTP head) was picked instead of a model: it carries a whole model's metadata but only a few tensors. MemuAI recognises such files and warns in advance — download the main file from the same repository and set the draft file in "Draft model for speed-up" |
| Hugging Face answered HTTP 401/403 | the repository is gated: accept the licence and set a token |
| The conversation does not fit into the local model's context | raise "Context length" in the model settings, start a new chat or send less text |
| This local model's chat template cannot handle tools | the model cannot call functions — for agents and MemuCode pick a model marked "tools" in the wizard |
| Could not download the engine | GitHub is unreachable or rate-limited — retry later or set your own `llama-server` |

Any of these errors can be handed to the [AI companion](doc:proxy-companion) — it knows this tab.

## Where the files are

```
%APPDATA%\MemuAI\local-models\
  catalog.json   the model list and settings
  models\        downloaded and copied models
  runtime\       the llama.cpp engine
```

Deleting a model downloaded through MemuAI can delete its file too. Files added "in place" are never deleted by the Proxy.

See also: [Model detector](doc:model-detector), [Proxy API](doc:proxy-api).
