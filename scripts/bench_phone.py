#!/usr/bin/env python3
"""Benchmark /api/phone/ask latency: N photo-only + N voice requests.

Reads SARVAM_API_KEY and PHONE_TOKEN from .env (never prints them), synthesises a
Malayalam WAV question via Sarvam TTS, generates a JPEG with PIL, and prints a
markdown table of median/p95 timings from the response debug.timings_ms plus the
client-side round trip.
"""

import base64
import json
import statistics
import sys
import time
import urllib.request
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
QUESTION = "മേശയിൽ എന്താണ്?"


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def sarvam_tts(key, text):
    body = {"text": text, "model": "bulbul:v4-flash", "language_code": "ml-IN",
            "speaker": "simran_en_customer", "output_audio_codec": "wav"}
    req = urllib.request.Request(
        "https://api.sarvam.ai/text-to-speech", data=json.dumps(body).encode(),
        headers={"api-subscription-key": key, "Content-Type": "application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return base64.b64decode(data["audios"][0])


def make_jpeg():
    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([80, 90, 720, 480], outline="black", width=6)
    draw.ellipse([250, 180, 550, 420], fill="red")
    draw.text((330, 40), "STOP", fill="blue")
    out = BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def ask(base, token, image_b64, audio_b64=None, audio_type=None):
    payload = {"image_b64": image_b64, "audio_b64": audio_b64, "audio_type": audio_type}
    started = time.perf_counter()
    response = requests.post(base + "/api/phone/ask", json=payload, timeout=120,
                             headers={"Authorization": f"Bearer {token}"})
    elapsed_ms = (time.perf_counter() - started) * 1000
    data = response.json()
    timings = (data.get("debug") or {}).get("timings_ms") or {}
    return {"http": response.status_code, "ok": data.get("ok"),
            "route": (data.get("debug") or {}).get("route"),
            "reply": data.get("reply"), "elapsed_ms": round(elapsed_ms, 1),
            "stt": timings.get("stt"), "vision": timings.get("vision"),
            "tts": timings.get("tts"), "total": timings.get("total")}


def stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return "-", "-"
    return f"{statistics.median(values):.0f}", f"{sorted(values)[int(0.95 * (len(values) - 1))]:.0f}"


def main():
    env = load_env()
    token = env.get("PHONE_TOKEN", "")
    if not token:
        sys.exit("PHONE_TOKEN missing from .env")
    key = env.get("SARVAM_API_KEY", "")
    port = env.get("PORT", "8000")
    base = f"http://127.0.0.1:{port}"

    jpeg_b64 = base64.b64encode(make_jpeg()).decode()
    wav_b64 = None
    if key:
        try:
            wav_b64 = base64.b64encode(sarvam_tts(key, QUESTION)).decode()
        except Exception as exc:
            print(f"TTS synthesis failed ({exc}); voice requests skipped")

    photo_rows, voice_rows = [], []
    for i in range(N):
        photo_rows.append(ask(base, token, jpeg_b64))
        if wav_b64:
            voice_rows.append(ask(base, token, jpeg_b64, wav_b64, "audio/wav"))

    rows = [("photo", r) for r in photo_rows] + [("voice", r) for r in voice_rows]
    print(f"| kind | http | route | stt med/p95 | vision med/p95 | tts med/p95 | total med/p95 | rtt med/p95 |")
    print("|---|---|---|---|---|---|---|---|")
    for kind, group in (("photo", photo_rows), ("voice", voice_rows)):
        if not group:
            continue
        stt_m, stt_p = stats([r["stt"] for r in group])
        vis_m, vis_p = stats([r["vision"] for r in group])
        tts_m, tts_p = stats([r["tts"] for r in group])
        tot_m, tot_p = stats([r["total"] for r in group])
        rtt_m, rtt_p = stats([r["elapsed_ms"] for r in group])
        routes = "/".join(sorted({str(r["route"]) for r in group}))
        codes = "/".join(sorted({str(r["http"]) for r in group}))
        print(f"| {kind} | {codes} | {routes} | {stt_m}/{stt_p} | {vis_m}/{vis_p} | "
              f"{tts_m}/{tts_p} | {tot_m}/{tot_p} | {rtt_m}/{rtt_p} |")
    for kind, r in rows:
        print(f"  {kind:>5} {r['elapsed_ms']:>7} ms  http={r['http']} route={r['route']} "
              f"ok={r['ok']} reply={str(r['reply'])[:60]!r}")


if __name__ == "__main__":
    main()
