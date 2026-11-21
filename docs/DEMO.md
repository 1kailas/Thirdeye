# DrishtiGuide — 3-minute demo script

Setup before judging: server running (`./run.sh`), phone paired (scan the QR on
`http://127.0.0.1:8000/pair` or the app is already configured), ThirdEye
service started, key filter enabled. Have `/debug` open on the laptop in a
second window to show what the phone sent and received.

## Scene 1 — read a label, Malayalam (≈45 s)

Hold a medicine strip or a packet label in front of the phone camera.
**Hold Volume Up**, ask in Malayalam: *"ഈ ലേബലിൽ എന്താണ് എഴുതിയിരിക്കുന്നത്?"*
Release. The phone vibrates at hold start, you hear a send tone, quiet ticks
while it thinks, then a spoken Malayalam reading of the label text.
Show `/debug`: the uploaded photo, the transcript, `route=general`, timings.

## Scene 2 — same, in English (≈30 s)

Ask *"What does this label say?"* in English. STT auto-detects `en-IN` and the
answer still comes back in Malayalam. Point at `/debug` `stt_language=en-IN`.

## Scene 3 — "what is ahead" (≈20 s)

Quick-press Volume Up (no voice). The phone describes the scene ahead —
objects, colors, obstacles and their rough position.

## Scene 3b — "where is the exit button?" on a remote (≈45 s)

Hold a TV remote in front of the camera. **Hold Volume Up** and ask *"Where is the
exit button?"* The reply counts for you: *"'EXIT' is in the fifth row from the
top, first key from the left, second row from the bottom, next to '0'."* Now put
a finger on any key, ask again, and hear *"Move one key left and two rows down."*
Show `/debug`: the key grid the model returned, with the target highlighted and
the finger cell outlined, so the audience sees that Python did the counting.

## Scene 4 — appliance task with verification (≈60 s)

Point at a washing-machine panel. Ask *"How do I start this?"* — the guide
route gives one step at a time. Do the step, then say *"done"* (or "ചെയ്തു"):
the server takes a fresh photo and verifies the change before advancing.
Show the verified-step flow in `/debug` (`route=guide`).

## Scene 5 — voice commands (≈20 s)

With a task active: *"repeat"* / *"repeat that"*, *"where is the button"*,
*"stop"*, *"next step"*, *"start over"*. English and Malayalam phrases both work.

## Scene 6 — safety + honesty (≈20 s)

Show a gas cylinder or mains socket and ask how to operate it: the answer is a
short Malayalam safety caution, not instructions. Hold the camera at an
unreadable/blurred label and ask: the reply says it can't read it clearly and
asks for a better photo — it never invents text.

## Close — debug surfaces (≈10 s)

Open the app's **Debug history**: every turn with the exact photo uploaded,
the recording, transcript, reply, route and timings. Clear it live.

## What makes this different

- **Hands-free hardware trigger** — Volume Up quick-press / hold, no screen
  interaction needed; works with the screen off via a foreground service.
- **Malayalam-first, multilingual ears** — answers always in spoken Malayalam,
  but understands Malayalam, English and Hindi speech (`language_code=unknown`
  auto-detection).
- **Acts, not just describes** — appliance tasks go one verified step at a
  time with before/after photo confirmation.
- **Safety refusals** — gas, mains, medical and industrial equipment get a
  caution instead of instructions; unclear scenes are admitted, not invented.
- **Token-secured local server** — bearer-token API, loopback-only debug and
  browser UI, QR pairing page, request debug log with the exact uploaded media.
- **No cloud storage in the app** — photos/voice go to Sarvam for processing
  and the debug history stays on the phone.

## Honest limitations

- Android does not expose power-button hold/release to apps, so Volume Up is
  the trigger; whether the key reaches the service with the screen off varies
  by phone model (ColorOS tested).
- While the service runs, Volume Up presses are consumed for DrishtiGuide.
- Vision/STT/TTS latency is Sarvam-bound (~1 s photo-only, ~3 s with voice,
  ~3–4.5 s end-to-end on device).
- Screen-reader output quality depends on the Sarvam models; very small or
  reflective labels may need a closer/steadier shot.
