"""Compare legacy vs streaming /api/phone/ask: time to first sound and total (needs a running server).

Usage: python scripts/bench_stream.py [image.jpg] [runs]. Reads PHONE_TOKEN and SARVAM_API_KEY from .env."""

import base64
import json
import os
import statistics
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from settings import load_environment

load_environment()
tok = os.environ["PHONE_TOKEN"]
key = os.environ["SARVAM_API_KEY"]


def tts_wav(text, lang):
    r = requests.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": key},
                      json={"text": text, "model": "bulbul:v4-flash", "language_code": lang,
                            "speaker": "simran_en_customer"}, timeout=30)
    return base64.b64decode(r.json()["audios"][0])


Q = {"loc": tts_wav("Where is the exit button?", "en-IN"),
     "gen": tts_wav("What is in this picture? Describe it in detail.", "en-IN")}
IMG = Path(sys.argv[1] if len(sys.argv) > 1 else "remote.jpg").read_bytes()
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 4


def ask(kind, stream):
    body = {"image_b64": base64.b64encode(IMG).decode(), "audio_b64": base64.b64encode(Q[kind]).decode(),
            "audio_type": "audio/wav", "stream": stream}
    t = time.time()
    first = None
    meta = None
    r = requests.post("http://127.0.0.1:8000/api/phone/ask", json=body,
                      headers={"Authorization": "Bearer " + tok}, timeout=90, stream=stream)
    if stream:
        for line in r.iter_lines():
            o = json.loads(line)
            if o["type"] == "meta":
                meta = o
            if o["type"] == "audio" and o["audio_b64"] and first is None:
                first = time.time() - t
        return first, time.time() - t, meta
    j = r.json()
    total = time.time() - t
    return total, total, j


for kind in ("loc", "gen"):
    for stream in (False, True):
        ts = [ask(kind, stream) for _ in range(RUNS)]
        fs = sorted(x[0] for x in ts)
        tt = sorted(x[1] for x in ts)
        print(kind, "stream" if stream else "legacy",
              "first-sound median %.2f max %.2f | total median %.2f" % (statistics.median(fs), fs[-1], statistics.median(tt)), flush=True)
        m = ts[-1][2]
        if stream:
            print("   reply:", m["reply"][:140], "| parts", m["parts"], "| route", m["debug"].get("route"), flush=True)
