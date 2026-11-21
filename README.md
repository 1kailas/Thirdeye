# Thirdeye · DrishtiGuide AI

## Quick start (3 steps)

1. Run the server: `./run.sh` (reads `.env`; see Setup below).
2. On this PC open `http://127.0.0.1:8000/pair` and scan the QR code with the
   phone camera — it pairs the app with the server address and token.
3. In the app tap **Start service**, grant camera/microphone, and enable the
   key service in Accessibility settings. Hold **Volume Up** to ask.

Docs: [architecture](docs/ARCHITECTURE.md) · [demo script](docs/DEMO.md) ·
[Android build/install](android/README.md)

## Problem statement

Blind and low-vision people in Kerala often cannot use everyday appliances independently. Washing-machine panels, TV remotes and fan regulators rely on small printed labels, symbols and touch-less displays. Most existing visual-assistance tools are English-first, need a screen interaction to start, or describe a scene without helping the user act on it safely.

Thirdeye (DrishtiGuide AI) is a Malayalam-first, hands-free visual assistant. The user holds a phone, presses a hardware key, and either gets an immediate spoken description of what the camera sees or asks a question by voice. For appliances it guides one verified step at a time.

## How it works

| Piece | Role |
| --- | --- |
| Android phone app (`android/`) | Captures the trigger (Volume Up), takes the photo with the rear camera, records the question while the key is held, uploads both, and plays the spoken Malayalam answer. Works with the screen off through a foreground service and an accessibility key filter. Has a debug history screen. |
| PC server (`app.py`, FastAPI) | Holds the Sarvam API key, validates and re-encodes the photo, runs speech-to-text, picks a route, calls the vision model, synthesises Malayalam speech and returns it. Also serves the browser UI and a `/debug` page. |
| Sarvam AI | `saaras:v4` speech-to-text (auto language detection), `gemma4` vision, `bulbul:v4-flash` Malayalam text-to-speech. |

Gestures on the phone:

- **Quick press of Volume Up**: take a photo and hear a description of the scene.
- **Press and hold**: the photo is taken at once and the phone vibrates. Speak while holding, release to send photo and voice, and hear the answer.

Routing on the server:

- **General route**: a question about the photo (what is this, read this, what is ahead) is answered directly in Malayalam, whatever language the user spoke.
- **Appliance route**: requests to operate or locate a control on a washer, remote or fan use structured guidance, one step at a time. A step is only confirmed when before and after photos show the change, and gas, mains, medical and industrial equipment are refused with a safety note.
- Voice commands work in Malayalam and English (`repeat`, `done`, `next step`, `where is the button`, `stop`, `start over`, …); anything else is treated as a question about the photo and answered directly in one to three short Malayalam sentences.

Security: the phone talks to the server with a bearer token (`PHONE_TOKEN`) over Tailscale or the local network. The browser UI and debug pages stay reachable from the PC only. Photos and voice go to Sarvam for processing, and the Sarvam key never leaves the PC.

## Status

Working prototype. The server has an offline test suite (`python -m unittest discover -s tests`) and the Android app has JVM unit tests for the trigger state machine, WAV writer and history store. Whether Volume Up is delivered with the screen off varies by phone model. Android does not expose power-button hold and release to apps, so the volume key is the trigger.

DrishtiGuide helps blind and low-vision users locate controls on everyday appliances and work through one task at a time in spoken and written Malayalam. It runs as one local Python process on the laptop. An optional IP Webcam stream on the Android phone supplies the camera for the browser UI, and the same phone can instead run the DrishtiGuide phone client, which takes the photo itself, records a Malayalam voice note, uploads both, and plays the spoken reply. Sarvam supplies image analysis, Malayalam speech output, and optional microphone transcription.

## Setup on EndeavourOS / Arch Linux

The simplest setup uses the phone client below and needs no IP Webcam stream. For the browser UI with a live preview, continue with the camera steps.

1. In IP Webcam on the Android phone, select the **rear camera** and start the server. Use the highest stable resolution that lets you read the panel. If possible, keep the phone on the same angle while taking before and after photos. Skip this step when using only the phone client; set `CAMERA_URL=off` in `.env` instead.
2. Connect the phone and laptop through Tailscale or a shared network. From the laptop, check that the camera responds. Replace the address below with the one shown by IP Webcam.

   ```sh
   curl -sS --connect-timeout 4 --max-time 5 -D - -o /dev/null http://100.109.140.21:8080/video
   ```

   Look for HTTP 200 and a multipart content type. The command can reach its time limit after receiving data because the stream stays open. DrishtiGuide independently requires a decoded JPEG before reporting the camera ready.

3. If the project does not have a `.env` file yet, copy the template and set `SARVAM_API_KEY`. An environment variable set before launch takes precedence.

   ```sh
   cp -n .env.example .env
   chmod 600 .env
   ```

4. From the project directory, start DrishtiGuide. The launcher creates `.venv` if needed and installs Uvicorn and the other Python dependencies there.

   ```sh
   ./run.sh
   ```

5. Open `http://127.0.0.1:8000` in Firefox or Chromium. Activate **തുടങ്ങാൻ സഹായം കേൾക്കുക** to hear the camera status and how to start. Allow microphone access if you choose to dictate a question or command.

After setup, you can also launch with `.venv/bin/python app.py`.

## ഉപയോഗിക്കേണ്ട വിധം

- പാനലിലെ അക്ഷരങ്ങൾ തെളിഞ്ഞു കാണുന്ന വിധം ഫോൺ വയ്ക്കുക. **ചിത്രമെടുത്ത് പരിശോധിക്കുക** അമർത്തുക.
- എന്ത് ചെയ്യണമെന്ന് എഴുതുകയോ **ചോദ്യം പറയുക** അമർത്തി പറയുകയോ ചെയ്യുക. വീണ്ടും അമർത്തുമ്പോൾ റെക്കോർഡിങ് അയയ്ക്കും. മൈക്രോഫോൺ അനുമതി നൽകിയ ശേഷം `V` അമർത്തിപ്പിടിച്ച് സംസാരിക്കുകയും ചെയ്യാം.
- ബട്ടണോ നോബോ കണ്ടെത്താൻ **നിയന്ത്രണം എവിടെ?** അമർത്തുക. തിരിച്ചറിയാൻ പറ്റുന്ന ഭാഗവുമായി താരതമ്യപ്പെടുത്തി അതിന്റെ സ്ഥാനം പറയും. **ഈ ഘട്ടം മറ്റൊരു രീതിയിൽ വിശദീകരിക്കുക** തുറന്ന് ലളിതമായോ വിശദമായോ നിർദേശം കേൾക്കാം.
- ഒരു ഘട്ടം ചെയ്ത ശേഷം **ചെയ്തു · പരിശോധിക്കുക** അമർത്തുക, അല്ലെങ്കിൽ **ചെയ്തു** എന്നു പറയുക. ചിത്രം വ്യക്തമായില്ലെങ്കിൽ സഹായിയുടെ നിർദേശം അനുസരിച്ച് മറ്റൊരു ചിത്രം എടുക്കുക.
- റിമോട്ട് അമർത്തിയതുപോലുള്ള ഒരു പ്രവൃത്തിയുടെ ഫലം ഫോട്ടോയിൽ കാണാനാകില്ലെങ്കിൽ, നിങ്ങൾ ചെയ്തുകഴിഞ്ഞെന്ന് ഉറപ്പായ ശേഷം **സ്ഥിരീകരിക്കാതെ തുടരുക** അമർത്തുക.
- അക്ഷരങ്ങൾ ചെറുതാണെങ്കിൽ **ക്യാമറയും ചിത്രഭാഗവും** തുറന്ന് വലുപ്പവും ചിത്രഭാഗത്തിന്റെ സ്ഥാനവും തിരഞ്ഞെടുക്കുക. ആ ഭാഗം അടുത്ത ചിത്രത്തിന് ഉപയോഗിക്കും.
- **വീണ്ടും കേൾക്കുക** അമർത്തിയാൽ ഏറ്റവും പുതിയ മറുപടി വീണ്ടും വായിക്കും. **ശബ്ദവേഗം** ഉപയോഗിച്ച് പതുക്കെയോ വേഗത്തിലോ കേൾക്കാം. **നിർത്തുക** അമർത്തിയാൽ ശബ്ദവും ഇപ്പോഴത്തെ അഭ്യർത്ഥനയും നിർത്തും.
- **ദൗത്യം മായ്ക്കുക** അമർത്തിയാൽ ദൗത്യവും അതിന്റെ ചിത്രങ്ങളും മായും.

The interface includes skip links, visible keyboard focus, and live status messages. You can turn off single-letter shortcuts. If you use a screen reader and prefer one voice, uncheck **മറുപടികൾ സ്വയം വായിക്കുക**. You can still request an individual spoken reply. If the browser blocks automatic playback, activate **വീണ്ടും കേൾക്കുക** to play the prepared clip.

## Configuration and behavior

- `CAMERA_URL` defaults to the Tailscale URL above. Set it to `off` (or `none`, case-insensitive) to run without a camera stream; the browser UI still works and the phone client supplies every photo. Use **ക്യാമറയും ചിത്രഭാഗവും** to change it for the running session. You can paste the IP Webcam home address from the browser, such as `http://192.168.1.50:8080/`. DrishtiGuide uses its `/video` endpoint. Set it in `.env` to keep it across restarts. `CAMERA_ROTATION` accepts 0, 90, 180, or 270 degrees. The rotation button makes a temporary adjustment.
- `IMAGE_MAX_WIDTH` caps the long edge of the analysis image. The app crops from the original frame before resizing. `JPEG_QUALITY` defaults to 82. The preview uses smaller local JPEGs and recovers automatically after interrupted connections.
- `VISION_MODEL` defaults to `gemma4`. If the selected model is unavailable to your account, the interface shows the Sarvam HTTP status and the configured alternative. To test GLM on a difficult panel, set `VISION_MODEL=glm5.3-flash` and restart. GLM uses low reasoning effort. Compare the accuracy of readable labels and before-and-after decisions on the same panel before switching. [Sarvam Chat Completion V2](https://docs.sarvam.ai/api-reference/chat/chat-completions-v2)
- Speech output uses `TTS_MODEL=bulbul:v4-flash` with Malayalam (`ml-IN`). Set `TTS_VOICE` to another compatible Sarvam voice or `TTS_PACE` to change the base speaking pace. The playback speed selector works independently. [Sarvam Bulbul](https://docs.sarvam.ai/api/api-guides-tutorials/models/bulbul)
- `STT_MODEL=saaras:v4` uses transcription mode with `LANGUAGE_CODE=ml-IN`. Voice recordings use the laptop browser microphone and are capped at 20 seconds. [Sarvam Speech-to-Text REST](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/rest-api)
- The server binds to `HOST` (default `127.0.0.1`). Any non-localhost bind requires a `PHONE_TOKEN` of at least 24 characters or the app refuses to start. The browser UI and the phone API both stay reachable only from this machine or with the bearer token. It holds one in-memory session shared by tabs and the phone. Requested still photographs, microphone recordings, relevant task context, and text to be read aloud go to Sarvam. Camera video stays local. The interface and application logs exclude the API key and the phone token.
- This is an assistive prototype for low-risk household controls, especially washing-machine panels and remotes. It needs visible evidence to confirm a change. An explicit bypass records the step as unverified.

## Phone client

The Android app in `android/` turns the phone into the client: it takes the photo with its own camera, records a Malayalam voice note, uploads both to the laptop, and plays the spoken Malayalam reply. The laptop keeps doing all the vision, transcription, and speech work.

Server setup:

1. In `.env`, set `CAMERA_URL=off` (the phone supplies every photo, so no IP Webcam stream is needed), `HOST=0.0.0.0`, and `PHONE_TOKEN` to a random secret of at least 24 characters. Generate one with `python -c "import secrets;print(secrets.token_urlsafe(32))"` and keep `.env` at `chmod 600`.
2. The easiest pairing is the QR page: with the server running, open `http://127.0.0.1:<port>/pair` on the PC and scan the QR with the phone camera — the `thirdeye://pair` link fills in the server address and token automatically. You can also enter `http://<address>:8000` plus the token by hand. The app talks plain HTTP, so use Tailscale or a trusted LAN, never a public network.
3. `GET /api/phone/ping` returns `{"ok":true,"ai_ready":bool}` and `POST /api/phone/ask` accepts `{"image_b64","audio_b64","audio_type"}` and always answers `{"ok","reply","transcript","audio_b64","audio_type"}`. Both require `Authorization: Bearer <PHONE_TOKEN>`.

Building and installing the app (see `android/README.md` for details):

```sh
cd android && ./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

First run: grant Camera, Microphone and notification permissions, enable the **DrishtiGuide key service** in Accessibility settings, tap **Start service**, and disable battery optimization for the app so the foreground service survives screen-off. On Android 13+ a sideloaded APK may show the service greyed out under "Restricted settings" — use the app's **Allow restricted settings** button (App info → ⋮ menu), or install with `adb install` which is not restricted.

Using it: press and release **Volume Up** for a quick photo-and-answer, or press and hold **Volume Up** to take a photo immediately, keep holding to record a voice question, and release to send both. A press while a request is still uploading is ignored with an error tone; a press during reply playback stops the reply and starts the new request. The on-screen test button offers the same gestures.

Limitations: Android does not expose power-button hold or release to apps, so Volume Up is the trigger. Whether a key press reaches the service while the screen is off varies by device and OEM; keep the phone awake or disable battery optimization if presses are missed. While the service runs, Volume Up is consumed for DrishtiGuide instead of changing the media volume.

## Test and measure

Run the offline suite with dependencies installed.

```sh
python -m unittest discover -s tests -v
```

The suite exercises the worker with JPEG fixtures and simulated interruptions, the guidance and verification state transitions, and the Sarvam request and error handling. Complete these checks on your hardware with the camera and a live Sarvam key.

With the virtual environment active, the following smoke test checks the configured Sarvam model, captures a real phone image for a Malayalam reply, and requests a Malayalam clip from Bulbul. It prints the measured latencies. [Sarvam model list](https://docs.sarvam.ai/api-reference/chat/models-v2)

```sh
DRISHTIGUIDE_LIVE=1 python -m unittest discover -s tests -p test_live.py -v
```

| Scenario | Live check |
| --- | --- |
| Washing machine | Show a readable panel and ask to select a specific programme. Confirm a single instruction, then photograph the dial after turning it. |
| TV remote | Show the remote and ask where volume-up is. Check the written position against the remote's actual landmarks. |
| Unreadable label | Blur or cover the relevant label. The app should ask for a clearer photo. Try selecting a crop and capturing again. |
| Follow-up | With a step active, ask **ഏത് ബട്ടണാണ്?**. Check that the answer refers to the same control and the step stays active. |
| Malayalam text and speech | Type **ഇത് എന്താണ്?** or dictate **ഫോട്ടോ എടുക്കൂ**. Check the displayed reply, then listen for the same reply. Test replay, slower speed, and stop. |
| Keyboard and screen reader | Start at the skip links. Activate the help, capture, location, and verify buttons with the keyboard. Check the spoken and live status changes and confirm focus stays on the active button. |
| Camera failure | Stop the IP Webcam server and confirm the connection error appears and is announced when app audio is enabled. Restart the phone server and wait for the preview to recover. |
| Visual verification | Capture two clear angles with the dial unchanged first, then turn it and photograph it again from the original angle. Cover the indicator and check that the app asks for another photo. |
| API failure | Launch a separate run with an intentionally invalid test key. Capture and check the displayed Sarvam HTTP status and recovery instructions. Restart with the valid key. The offline suite covers timeouts. |

Open **കീബോർഡ് കുറുക്കുവഴികളും സമയവും** to see measured camera connection time, local frame age, capture and JPEG encoding times, Sarvam API latencies, and the elapsed browser time until the written reply and the start of its playback. Frame age measures time since the laptop received the data.

The offline timing check processed 30 synthetic 1920×1080 images. Median local resize and JPEG encoding took approximately 29 ms. Live phone and Sarvam timings require the hardware and account checks above.

## Roadmap

- Direct power-button trigger — not possible on Android (the OS does not expose power-button hold/release to apps); Volume Up is the current trigger.
- On-device fallback — run STT/vision/TTS on the phone when the PC is unreachable.
- More languages — extend beyond Malayalam/English/Hindi input while keeping Malayalam output.
- Wearable camera — support a glasses or clip-on camera as the capture device.
