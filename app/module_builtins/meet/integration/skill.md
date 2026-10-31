MemuMeet records meetings and calls and keeps them in the module store
(`<DATA_DIR>/memuai/modules/store/meet/`): one key per meeting with its
transcript, segments and summary, and one key with the audio recording.
There are no server handlers for MemuMeet and no `meet.*` commands with
`run.handler`: every command below only opens the module
(`run.open`), and everything else happens inside the module window, with
the user's explicit button press.

- "покажи встречи / show meetings" → meet.list. It opens MemuMeet with
  the list of meetings. The module itself shows the recordings, the
  transcript and the summary; the AI does not read meeting contents
  from the chat.
- "начни запись встречи / record this meeting" → meet.record. It opens
  MemuMeet on the recording page. Recording starts only from the button
  in the module (the browser may require a user gesture); if the
  microphone bridge does not respond, say so — never claim a recording
  happened.
- "импортируй аудиофайл / import a meeting audio file" → meet.import.
  It opens the import view; the user picks the file (WAV, MP3, M4A, OGG,
  WebM, FLAC, up to 25 MB) in the module.
- "субтитры встречи / live captions" → meet.captions. It opens the
  captions page of the latest meeting. Live captions exist only while
  system audio is being recorded (the audio is transcribed in chunks);
  microphone recordings get captions only after stopping, because
  MemuAI has no streaming STT. Never present captions that were not
  transcribed.

Inside the module, and only there:

- Transcription uses the Live/STT settings (`voice_stt_provider`,
  `voice_stt_local_model`, `voice_stt_api_model`, `voice_language`) via
  the existing `Memu.transcribe` route. There is no MemuMeet model list
  for recognition and no key handling: never ask for, repeat or invent
  a recognition key or provider setup.
- The summary, decisions and tasks are made by the chat model the user
  chose in the MemuMeet settings, only after the user presses "Make a
  summary". The model is told to work only from the transcript and to
  mark uncertainty. Nothing is sent anywhere on its own.
- Speaker labels are a pause/energy heuristic, not exact diarization:
  the recognition service returns plain text without speakers or word
  timings, and segment text boundaries are approximate. Say so when a
  user asks who said what, and offer to fix the speaker names in the
  module instead of asserting an identity.
- System-audio capture depends on the OS and the browser: if
  `getDisplayMedia` gives no audio stream, the module reports it as
  unavailable. In the module window (a sandboxed iframe with an opaque
  origin) display-capture is disallowed, so the module never calls
  `getDisplayMedia` there and records with the microphone only,
  saying why. Never claim system audio was captured when it was not.
- Deleting a meeting (in the module, with confirmation) removes the
  recording, transcript and summary from the store.
- Scheduling is not a MemuMeet command: there is no planning
  contribution mechanism for modules. If the user wants a follow-up or
  a reminder from a summary, offer the usual chat scheduling
  (`/schedule` and the planning flow of MemuAI itself) with the task
  text taken from the summary, and only after the user confirms it.
- Never invent a meeting id, a transcript, a summary, a recording
  path or a model id. If MemuMeet is disabled, say it is off and offer
  to enable it in the modules list — do not describe meetings you
  cannot see.

Model picker (shared component):

- The summary model is chosen with the shared MemuModelSelector
  (dropdown preset), inlined into the module document by the host.
  Provider names come from the model catalogue
  (`provider_label` / account / id prefix) — a Custom API connection
  is shown under its own connection name, never as "custom".
- Settings autosave: every settings change (model, language, toggles,
  microphone, source) is saved automatically after a short debounce,
  with a "Saved" indicator. There is no lost change on a quick edit
  burst: only the last state is written, in order.

Recording:

- The recording page is a wizard: sources → sound check → recording →
  processing. The sound check is split: the microphone shows a live
  level meter (AnalyserNode, no screen capture), or a real 2-second
  test clip through the bridge where a live meter is unavailable in
  the sandboxed window (discarded); system audio shows a level meter
  only when capture is actually available, and is disabled with the
  reason otherwise.
- System audio is guarded by a pre-check (Permissions-Policy /
  Feature-Policy, plus the opaque origin of the sandboxed module
  window): when display-capture is disallowed, the module never calls
  `getDisplayMedia` and explains the block instead of failing. The
  Permissions-Policy API is absent in the module window's
  Chromium/WebView2 context, so the opaque origin is the signal
  that survives there.
- "Microphone + system audio" records both tracks in parallel into
  ONE meeting: system audio is the primary track, the microphone track
  is stored under `meetings/<id>/audio-mic`. Transcription runs per
  track and the merged transcript labels microphone speech ("You")
  separately from system speech ("System"). The tracks are not mixed
  into a single file (the sandboxed page cannot open the microphone
  stream itself; see backend.py).
- The microphone list asks for microphone permission (`getUserMedia`)
  before `enumerateDevices`, so the browser can return real device
  names; devices without names are shown honestly as unlabelled,
  with part of their id, never as numbered "Microphone 1/2/3". The
  choice is saved in settings. The host bridge opens the microphone,
  so the saved device id is a preference the host honours when it
  can; the sandboxed module page itself cannot open the microphone.
