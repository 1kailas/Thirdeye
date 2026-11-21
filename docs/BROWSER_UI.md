# Browser UI (optional)

The original DrishtiGuide interface is a single-page browser app served by the same FastAPI process at `http://127.0.0.1:8000`. It uses an IP Webcam stream (or the phone client) for photos and dictation from the laptop microphone. The phone client in the main README is the recommended experience; this page documents the browser UI.

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


