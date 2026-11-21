# DrishtiGuide phone client (Android)

Plain-Views Kotlin app. Volume Up is the trigger: quick press = photo question, press-and-hold = photo plus a recorded Malayalam voice question. The laptop runs the FastAPI server and all AI work.

## Build

Requires JDK 17 and the Android SDK (platform 34, build-tools 34). With `ANDROID_HOME`/`sdk.dir` set:

```sh
./gradlew assembleDebug
./gradlew testDebugUnitTest
```

APK output: `app/build/outputs/apk/debug/app-debug.apk`.

## Install

```sh
adb install app/build/outputs/apk/debug/app-debug.apk
```

or copy the APK to the phone and open it (allow installs from that source).

## First-run checklist

1. Enter the server address, e.g. `http://100.x.y.z:8000` (Tailscale or LAN IP of the PC, port from `.env` `PORT`), and the `PHONE_TOKEN`.
2. Tap **Grant camera, microphone and notification permissions** and accept.
3. If the service entry is greyed out (common on Android 13+ for sideloaded APKs): tap **Allow restricted settings**, then on the App info page open the ⋮ menu (top right) and choose **Allow restricted settings**, then come back and enable **DrishtiGuide**. Installing via `adb install -r app-debug.apk` avoids this restriction entirely.
4. Tap **Accessibility settings** and enable **DrishtiGuide** (the key service). Return to the app.
5. Tap **Test connection** — it should say Connected.
6. Tap **Start service** (the app must be in the foreground; the service then runs with camera/mic access).
7. In system settings, disable battery optimization for DrishtiGuide so the service survives screen-off.

## Server address and token

On the PC, in the project directory:

```sh
grep PHONE_TOKEN .env
```

The server address is the PC's Tailscale (`100.x.y.z`) or LAN IP plus `PORT` from `.env`.

## Gestures

- **Volume Up tap** — photo only.
- **Volume Up hold** — photo at once, vibrate, record while held; release to send photo + voice. Holds over ~0.3 s of audio send the recording; shorter holds send photo only. Recording caps at 20 s.
- Press during reply playback stops the reply and starts a new request. Press while a request is uploading is ignored with an error tone.
- The big on-screen **TEST** button mirrors the same gestures for checking without the hardware keys.

## Limitations

- Android does not expose power-button hold/release to apps, so Volume Up is the trigger and it is consumed while the service runs (media volume keys pause).
- Whether key events reach the accessibility service with the screen off varies by device/OEM; disable battery optimization and keep the phone awake if presses are missed.
