---
title: Voice and MemuAI Live
summary: Dictation with local Whisper or an API, text cleanup, reading answers aloud and voice conversation.
---

# Voice and MemuAI Live

Everything voice-related is in **Settings → Voice & Live**.

## Dictation

The microphone next to the send button records speech. When you stop, the recording is recognised and the text appears in the composer — you can fix it before sending. The button stays inactive until the chosen recogniser is ready.

### The recogniser

| Source | How it works |
| --- | --- |
| Local | a Whisper model is downloaded once and then works offline; speech never leaves the computer |
| Cloud service or custom API | transcription through the selected service's API; requires its address, key and model when needed |

Choose a source, then **Service**: **OpenAI, Groq, Google Gemini AI Studio, ElevenLabs Scribe, Deepgram, Mistral Voxtral, Microsoft Azure Speech**, or a **custom OpenAI-compatible API**. MemuAI uses the selected service's native request format. The address and suggested models are filled in; **Other** accepts your own model ID. For Azure, supply your Speech resource address and key; no separate model ID is required. Availability and prices depend on the service and your account.

Each service keeps its own address, key and model. Switching services does not copy another provider's key. A separate button can reuse your Gemini or ElevenLabs speech-output key. **Test with an audio file** saves the settings and transcribes a recording you select; the result or a readable error appears beside it. Select a language or use automatic detection where the model supports it.

Local models:

| Model | Size | Notes |
| --- | --- | --- |
| Whisper Tiny | 75 MB | the fastest, for modest hardware |
| Whisper Base | 142 MB | fast, for short notes |
| Whisper Small | 466 MB | the best balance of accuracy, speed and size |
| Whisper Medium | 1.5 GB | more accurate on long speech and difficult accents |
| Whisper Large v3 Turbo | 1.6 GB | large-model accuracy with faster decoding |
| Distil-Whisper Large v3 | 1.5 GB | fast for long English recordings; Distil Large v2 is also English-only |
| Whisper Large v3 | 3.1 GB | the best accuracy, the slowest |

Only the chosen model is downloaded. Progress shows received bytes and a percentage when the total size is known; downloading can be cancelled. The model becomes available after verification and installation, and cancelled temporary files are cleaned up. Change the models folder when no download/installation is active, or add your own model. Completing a download does not change your selected recogniser automatically.

### Recording

| Setting | Meaning |
| --- | --- |
| Speech language | "Automatic" suits mixed-language speech best |
| Input device | the system microphone copes better with headsets reconnecting |
| Noise suppression, echo cancellation, auto gain | sound processing by the browser engine |
| Maximum recording | the limit of one recording in seconds |

### Cleaning up dictation

**Post-processing** is an optional step: a text model of your choice fixes punctuation and case, writes spoken numbers as digits, removes slips and filler words, and splits paragraphs. It does not answer the text, summarise it or change its meaning. The processing instruction can be edited and saved as a preset.

## Reading answers aloud

The menu of any answer in a normal chat → **Read aloud**. Before reading, Markdown, tables, formulas and code are turned into natural speech.

What speaks is one list in **Settings → Voice & Live → Reading replies aloud → Speak with**:

| Option | What it needs |
| --- | --- |
| Windows voices | nothing: the voices installed in the system; works offline |
| Downloaded Piper voice | neural voices, downloaded once and then offline; the message language is detected |
| OpenAI | an OpenAI API key; models `gpt-4o-mini-tts`, `tts-1`, `tts-1-hd`, voices alloy, nova, coral and others |
| Google Gemini (AI Studio) | a Google AI Studio key; 30 Gemini voices (Kore, Puck, Zephyr…), multilingual |
| ElevenLabs | an ElevenLabs key; the voice is a `voice_id` from your ElevenLabs library |
| Microsoft Azure Speech | the Speech resource key and its region in the address (instead of `westeurope`); hundreds of neural voices |
| Other OpenAI-compatible API | the address (the API root — `/audio/speech` is appended), key, model and voice |
| Your local server | an OpenAI-compatible speech server on this computer (Kokoro-FastAPI, XTTS…) |

For cloud services MemuAI fills in the address, suggests models and voices and gives a **Get a key →** link. Each service gets requests in its own native format, and a long answer is split by the service's limit. When something is wrong — a bad key, no such model, the quota is used up — you see a short readable message instead of the service's error page. If the voice or the key belongs to another service (say, the OpenAI voice "alloy" chosen for Gemini), a warning appears under the field — and switching the service resets the model and voice to its defaults. **▶ Test** saves the settings and plays a sample the same way real speech is played. Reading the same answer aloud again comes from a cache and is not paid for twice.

Older "TTS API" settings keep working: the service is detected from the address.

Speed (when the service supports it; Gemini and ElevenLabs do not) and volume are adjustable. Speech runs in a separate process: if the voice engine crashes, MemuAI shows an error and keeps working.

## MemuAI Live

The Live button next to send opens a voice conversation: you speak, the model listens, answers and — with **Read answers automatically** on — reads the answer aloud. Both sides of the conversation are saved in the current normal chat.

To stop, close Live or click the sphere. Closing while recording cancels the recording.

Live has its own short conversational instruction — **Live personality and presets**: make the partner stricter, briefer or chattier.

## Real-time Live models

Classic Live is a chain: speech recognition → chat model → speech. **Live models** (GPT-Live-1, Gemini Live, Grok Voice and compatible ones) listen and speak themselves: no pause for transcription, a natural voice of their own, and you can interrupt them. The **Use a real-time model in MemuAI Live** switch in **Settings → Voice & Live → Real-time Live models** picks the mode; turning it off brings back the classic chain.

### Providers

| Provider | Default models | Key |
| --- | --- | --- |
| OpenAI GPT-Live | `gpt-live-1` | platform.openai.com → API keys |
| OpenAI Realtime | `gpt-realtime`, `gpt-realtime-mini` | the same OpenAI key |
| Google Gemini Live | `gemini-3.8-live`, `gemini-3.8-live-extended-thinking` | aistudio.google.com → Get API key |
| xAI Grok Voice | `grok-voice-latest` | console.x.ai → API Keys |
| Custom · OpenAI Realtime | any ID | as the server requires |
| Custom · Gemini Live | any ID | as the server requires |

"Custom" works with any API compatible with the OpenAI Realtime or Gemini Live protocol: enter a `wss://…` address (`ws://127.0.0.1` is allowed for a server on this computer). Built-in providers accept an address override too, for a proxy or a region.

### Setting up

1. Turn the switch on and pick a provider.
2. Paste the key — it is stored in the MemuAI data folder and never reaches the window: MemuAI itself opens the connection to the provider.
3. Optionally set the model and voice — the lists suggest values, but any ID can be typed.
4. Click **Test connection**: MemuAI opens a session, waits for confirmation and closes it right away.

**Advanced** holds turn detection (semantic waits for a finished thought, server VAD reacts to silence) and, for a custom API, the protocol dialect: GA or the older beta.

### How the conversation goes

Click Live and **Start talking** — MemuAI asks for the microphone and starts listening. The orb breathes with the voice: yours while you speak and the model's while the answer plays. Under the orb are captions of both sides; every turn is saved to the current chat as an ordinary message.

- Speak during an answer or click the orb — the model stops and listens.
- The microphone button mutes you without ending the conversation.
- The keyboard button (when the model supports it) lets you type a turn.
- **End** closes the session at the provider.

The model gets the selected Live personality with priority over ordinary chat tone, the meme's instructions, your general instructions, the chat's instructions, your profile and the latest messages of the conversation as context.

### Camera and screen sharing

During a conversation two buttons appear next to **End**: **camera** and **screen**. The active source shows in a small window under the orb; only one works at a time — the other replaces it. Pressing a button again stops sharing.

| Provider | How the model sees |
| --- | --- |
| Gemini Live | watches the video while you talk: about one frame a second |
| OpenAI Realtime | a fresh frame is attached to each of your turns |
| GPT-Live | does not see it itself: the latest frame goes to the MemuAI model that GPT-Live hands hard questions to |
| Grok Voice | not supported: the model takes sound only — there are no buttons |

For screen sharing MemuAI first opens the system picker of a screen or a window. If that is unavailable, MemuAI captures the screen itself — the whole main monitor, once a second; the Live window then shows a small copy of what goes to the model.

> [!WARNING]
> The frames go to the provider of the Live model. Before sharing the screen, close what must not reach it: conversations, passwords, documents.

### Web search in a conversation

The **Web search in Live** switch in the Live model settings (on by default) lets the model search during a conversation. While it searches, a "Searching: …" line shows under the orb; the pages it found are attached to the answer in the chat as an ordinary search step.

| Provider | How it searches |
| --- | --- |
| Gemini Live | the model's own Google Search. If the model refuses it — which happens on a key's free tier, where search has no quota — MemuAI notices and searches itself, with the source from [Web search](doc:web-search); the conversation starts as usual |
| OpenAI Realtime and custom APIs | the model asks MemuAI to search and open pages — with the same source |
| Grok Voice | its own search of the web and of X; MemuAI's search if the server refuses it |
| GPT-Live | hands the question to the backend model, which searches |

### Hard questions in GPT-Live

GPT-Live runs the conversation and hands tasks that need knowledge and reasoning to a backend. By default the backend is a **MemuAI model**: the chat's current model or the one chosen in **Advanced → MemuAI model for delegated questions** (any provider in Proxy). The answer goes back to GPT-Live, which says it in its own words; while delegating, the orb shows "Thinking it over". **OpenAI Responses** with an OpenAI model is available as well.

> [!NOTE]
> Live models are paid at the providers and use their limits. The conversation goes straight to the provider through MemuAI; MemuAI Proxy is not needed for it.

## Hiding the buttons

The microphone and Live can be removed from the composer with "Microphone next to send" and "Show Live next to send".

## Keys and privacy

API keys are kept in MemuAI's local data folder. With local recognition and Windows or Piper voices, no audio is sent anywhere.

See also: [Composer](doc:composer).

## Voice messages in chat

Live saves both sides as replayable voice messages with a transcript. Live text appears incrementally when the provider sends partial transcriptions. Replay is independent of the current session, whose audio continues in real time. In transcription → regular model → synthesis mode, the input recording and spoken answer are saved; with auto-play off, synthesis is saved silently. Temporary chats keep recordings until closed; regular chats use the active profile's folder. Long Live turns retain the first three minutes of audio.

The selected Live personality takes priority over ordinary chat tone. A small icon marks Live chats in the sidebar; disable it in Settings → General → Mark Live chats.

Both sides use a wide player with playback, seeking and a custom “⋮” menu for speed, downloading the recording and copying its transcript. Short turns keep the controls roomy. Playback position survives message updates; arrow keys seek five seconds, and Home and End move to the beginning and end.

Personality supplies response habits, conversational pace, initiative and example lines. The primary style takes priority; additional styles add accents. Romantic style, for example, asks for tender, imaginative wording and emotional responses, while professional style asks for clear businesslike phrasing. Changing personality during a call reconnects Live with updated instructions. The selected model also influences the resulting delivery.
