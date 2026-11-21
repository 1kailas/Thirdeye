"""Local FastAPI server and the DrishtiGuide interaction controller."""

import asyncio
import base64
import binascii
import hmac
import html
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import unicodedata
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from io import BytesIO
from pathlib import Path
from time import perf_counter
from typing import Literal
from urllib.parse import quote, urlencode, urlsplit

import uvicorn
import segno
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from camera import DEFAULT_URL, Camera, CameraError, Snapshot, jpeg_encode
from sarvam_client import SarvamClient, SarvamError
from settings import load_environment
from task_engine import TaskSession, compose_location, malayalam

ROOT = Path(__file__).resolve().parent
load_environment()
log = logging.getLogger("drishtiguide")


class Crop(BaseModel):
    zoom: float = Field(1, ge=1, le=4)
    x: float = Field(0.5, ge=0, le=1)
    y: float = Field(0.5, ge=0, le=1)


class Command(BaseModel):
    action: Literal["capture", "query", "next", "verify", "continue", "locate", "simplify", "explain",
                    "reset", "stop"]
    text: str = Field("", max_length=500)
    crop: Crop = Field(default_factory=Crop)


class CameraCommand(BaseModel):
    action: Literal["connect", "disconnect", "refresh", "rotate"]
    url: str | None = Field(None, max_length=600)
    rotation: Literal[0, 90, 180, 270] | None = None


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=750)
    revision: int = Field(ge=0)


VOICE_COMMANDS = {
    "capture": ("ഇത് എന്താണ്", "ഇതെന്താണ്", "ഫോട്ടോ എടുക്കൂ", "ഫോട്ടോ എടുക്കുക", "ചിത്രം എടുക്കൂ",
                "പടം എടുക്കൂ", "ഫോട്ടോ എടുക്ക്", "ഫോട്ടോ എടുക്കണം", "ചിത്രം എടുക്കുക"),
    "verify": ("ചെയ്തു", "ചെയ്തിട്ടുണ്ട്", "ഞാൻ ചെയ്തു", "കഴിഞ്ഞു", "ചെയ്തു കഴിഞ്ഞു",
               "സ്ഥിരീകരിക്കൂ", "പരിശോധിക്കൂ", "ഒന്ന് പരിശോധിക്കൂ", "ചെയ്തു നോക്കൂ", "ശരിയായോ",
               "done", "i did it", "i have done it", "check it", "check now"),
    "next": ("അടുത്തത്", "അടുത്ത ഘട്ടം", "അടുത്ത ഘട്ടം പറയൂ", "ഇനി എന്ത് ചെയ്യണം",
             "next", "next step", "what next", "what now"),
    "repeat": ("വീണ്ടും പറയൂ", "ഒന്നുകൂടി പറയൂ", "വീണ്ടും പറയുക", "ആവർത്തിക്കൂ", "ഒന്നൂടെ പറയൂ", "repeat", "repeat that", "say that again", "say it again", "come again"),
    "locate": ("എവിടെയാണ്", "ബട്ടൺ എവിടെയാണ്", "ഏത് ബട്ടൺ", "ഏത് ബട്ടണാണ്", "ഏത് നോബ്",
               "where is it", "where is the button", "which button", "where is the control"),
    "simplify": ("ലളിതമായി പറയൂ", "കുറച്ചുകൂടി ലളിതമായി പറയൂ", "എളുപ്പത്തിൽ പറയൂ",
                 "simpler", "say it simply", "explain simply", "simplify"),
    "explain": ("കുറച്ചുകൂടി വിശദമായി പറയൂ", "വിശദമായി പറയൂ", "ഒന്ന് വിശദീകരിക്കൂ",
                "explain more", "more detail", "explain in detail", "tell me more"),
    "stop": ("നിർത്തൂ", "നിർത്തുക", "നിർത്ത്", "ഇപ്പോൾ നിർത്തൂ", "മതി",
             "stop", "cancel", "be quiet", "quiet", "shut up"),
    "continue": ("സ്ഥിരീകരിക്കാതെ തുടരൂ", "സ്ഥിരീകരിക്കാതെ തുടരുക", "ഫോട്ടോ സ്ഥിരീകരണമില്ലാതെ തുടരൂ",
                 "skip verification", "continue without checking"),
    "reset": ("പുതിയ ദൗത്യം", "ദൗത്യം മായ്ക്കൂ", "ദൗത്യം പുനരാരംഭിക്കൂ",
              "new task", "start over", "reset", "clear task"),
}
PHRASES = {phrase: action for action, words in VOICE_COMMANDS.items() for phrase in words}


def intent(text):
    normalized = unicodedata.normalize("NFC", text).casefold()
    normalized = " ".join(normalized.strip(" \t\n\r.!?,;:'\"“”‘’()[]{}<>«»–—-।").split())
    return PHRASES.get(normalized, "query")


class Runtime:
    def __init__(self):
        rotation = int(os.getenv("CAMERA_ROTATION", "0"))
        self.width = int(os.getenv("IMAGE_MAX_WIDTH", "1280"))
        self.quality = int(os.getenv("JPEG_QUALITY", "82"))
        if rotation not in (0, 90, 180, 270) or not 320 <= self.width <= 2560 or not 40 <= self.quality <= 95:
            raise ValueError("Check CAMERA_ROTATION, IMAGE_MAX_WIDTH and JPEG_QUALITY in .env.")
        camera_url = os.getenv("CAMERA_URL", DEFAULT_URL)
        self.camera_enabled = camera_url.strip().lower() not in ("", "off", "none")
        self.camera = Camera(camera_url if self.camera_enabled else DEFAULT_URL, rotation)
        self.ai = SarvamClient()
        self.session = TaskSession()
        self.revision = 0
        self.phone_seq = 0
        self.locate_target = None
        self.phone_history = deque(maxlen=2)
        self.busy = False
        self.phase = None
        self.error = None
        self.metrics = {}
        self.speech_lock = asyncio.Lock()

    def interrupt(self, *, clear=False):
        self.revision += 1
        self.phase = None
        self.error = None
        if clear:
            self.session = TaskSession()
            self.metrics = {}
            self.locate_target = None

    def check(self, revision):
        if self.revision != revision:
            raise HTTPException(409, "ഈ അഭ്യർത്ഥന നിർത്തി.")

    def status(self):
        camera = self.camera.status()
        return dict(camera=camera, phase=self.phase, busy=self.busy, error=self.error,
                    state=self.phase or ("ERROR" if self.error or camera["state"] == "reconnecting"
                                         else self.session.state(camera["state"] == "ready")),
                    ai_ready=bool(self.ai.key), revision=self.revision,
                    session=self.session.public(), metrics=self.metrics)

    async def run(self, action, text, crop, revision, shot=None):
        task = self.session
        if action == "repeat":
            return task.response or "ആദ്യം ഒരു ചിത്രം എടുക്കുക."
        if action == "locate":
            if location := task.control_location():
                return location
            if text:
                action = "query"
            else:
                return "ഉപകരണത്തിന്റെ ചിത്രം എടുത്ത് എന്ത് ചെയ്യണമെന്ന് പറയുക."
        if action in ("reset", "stop"):
            self.interrupt(clear=action == "reset")
            return "ദൗത്യം മായ്ച്ചു." if action == "reset" else "അഭ്യർത്ഥന നിർത്തി."
        if action == "continue":
            if task.public()["can_continue"]:
                return task.without_visual_verification()
            return "ഈ ഘട്ടത്തിൽ പുതിയ ചിത്രം എടുത്ത് മാറ്റം പരിശോധിക്കുക."
        if action == "verify":
            return await self.verify(task, crop, revision, shot)
        if action == "next" and (task.finished or not task.task or (task.step and not task.closed_step)):
            if task.finished:
                return "ഈ ദൗത്യം പൂർത്തിയായി. പുതിയ ദൗത്യത്തിനായി എന്ത് ചെയ്യണമെന്ന് പറയുക."
            if not task.task:
                return "ഉപകരണത്തിൽ എന്ത് ചെയ്യണമെന്ന് പറയുകയോ എഴുതുകയോ ചെയ്യുക."
            if task.step.visually_verifiable:
                return "ഈ ഘട്ടത്തിന് ശേഷം പുതിയ ചിത്രം എടുത്ത് മാറ്റം പരിശോധിക്കുക."
            return "ഈ ഘട്ടം ഫോട്ടോയിൽ പരിശോധിക്കാനാവില്ല. ചെയ്തുകഴിഞ്ഞാൽ സ്ഥിരീകരിക്കാതെ തുടരുക."
        if action in ("simplify", "explain") and not task.public()["current_instruction"]:
            return "ലളിതമായി വിശദീകരിക്കാൻ ആദ്യം ഒരു ദൗത്യം തുടങ്ങുക."

        mode = action
        if action == "capture":
            mode = ("resume" if task.task and not task.finished and
                    (task.step is None or (task.closed_step and task.needs_new_image)) else "inspect")
        capture = action == "capture" or task.latest is None
        # After a step without a usable after-image, base the next instruction on a new view.
        capture = capture or (action == "next" and task.verification == "UNAVAILABLE")
        shot = shot or (await self.capture(crop, revision) if capture else task.latest)
        context = task.context()
        self.phase = "ANALYZING"
        reply, ms = await asyncio.to_thread(self.ai.guide, shot.jpeg, context, mode, text)
        self.check(revision)
        self.metrics["vision_ms"] = ms
        return task.accept_guidance(reply, shot, mode, text)

    async def capture(self, crop, revision, *, fresh=False):
        self.phase = "CAPTURING"
        snap = await asyncio.to_thread(self.camera.snapshot, self.width, self.quality,
                                       **crop.model_dump(), fresh=fresh)
        self.check(revision)
        self.metrics.update(frame_age_ms=snap.frame_age_ms, capture_ms=snap.capture_ms, jpeg_ms=snap.jpeg_ms)
        log.info("Snapshot capture %.1f ms, JPEG %.1f ms, frame age %.1f ms",
                 snap.capture_ms, snap.jpeg_ms, snap.frame_age_ms)
        return snap

    async def verify(self, task, crop, revision, shot=None):
        if not task.step or task.closed_step:
            return "പരിശോധിക്കാൻ ഇപ്പോൾ ചെയ്യേണ്ട ഒരു ഘട്ടമില്ല."
        if not task.step.visually_verifiable:
            return "ഈ മാറ്റം ഫോട്ടോയിൽ പരിശോധിക്കാനാവില്ല. ചെയ്തുകഴിഞ്ഞാൽ സ്ഥിരീകരിക്കാതെ തുടരുക."
        before = task.baseline
        after = shot or await self.capture(crop, revision, fresh=True)
        if after.number <= before.number or after.jpeg == before.jpeg:
            return task.uncertain()
        self.phase = "VERIFYING"
        context = {
            "device": task.scene.device_name,
            "task": task.task,
            "control": task.step_control.model_dump(),
            "action": task.step.action_ml,
            "expected_result": task.step.expected_result,
        }
        verdict, ms = await asyncio.to_thread(self.ai.verify, before.jpeg, after.jpeg, context)
        self.check(revision)
        self.metrics["vision_ms"] = ms
        return task.apply_verification(verdict, after)


@asynccontextmanager
async def lifespan(app):
    runtime = app.state.runtime = Runtime()
    if runtime.camera_enabled:
        runtime.camera.start()
    try:
        yield
    finally:
        await asyncio.to_thread(runtime.camera.close)
        runtime.ai.close()


app = FastAPI(title="DrishtiGuide AI", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    if not request.url.path.startswith("/api/phone/"):
        client = request.client.host if request.client else ""
        host = urlsplit("//" + request.headers.get("host", "")).hostname or ""
        if client not in ("127.0.0.1", "::1") or host not in ("localhost", "127.0.0.1"):
            return Response(status_code=403)
        if request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
            return Response(status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return Response(status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=()"
    return response


@app.get("/")
async def index():
    return FileResponse(ROOT / "index.html", media_type="text/html")


@app.get("/api/status")
async def status(request: Request):
    return request.app.state.runtime.status()


@app.get("/api/preview")
async def preview(request: Request):
    camera = request.app.state.runtime.camera

    async def frames():
        after = 0
        while not await request.is_disconnected():
            frame = camera.preview(after)
            if frame:
                after, data = frame
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                       str(len(data)).encode("ascii") + b"\r\n\r\n" + data + b"\r\n")
            await asyncio.sleep(0.12)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.post("/api/camera")
async def configure_camera(command: CameraCommand, request: Request):
    rt = request.app.state.runtime
    try:
        changed = rt.camera.configure(command.action, command.url if command.action == "connect" else None,
                                      command.rotation if command.action == "rotate" else None)
    except CameraError as exc:
        raise HTTPException(400, str(exc)) from None
    if command.action != "rotate":
        rt.interrupt(clear=changed)
    return rt.status()


def service_failure(rt, revision, exc):
    rt.check(revision)
    rt.error = str(exc)
    return JSONResponse({"detail": rt.error, "state": "ERROR"}, status_code=503)


async def execute(rt, action, text, crop, *, audio=None, content_type=None, shot=None):
    if action in ("stop", "reset"):
        message = await rt.run(action, text, crop, rt.revision)
        rt.session.respond(message)
        return rt.status() | {"stopped": action == "stop"}
    if rt.busy:
        raise HTTPException(409, "മുൻ അഭ്യർത്ഥന തീരുന്നതുവരെ കാത്തിരിക്കുക.")
    rt.busy, rt.error = True, None
    revision = rt.revision
    transcript = None
    if action in ("capture", "query", "verify", "next", "locate", "simplify", "explain"):
        rt.metrics = {}
    try:
        if audio is not None:
            rt.phase = "TRANSCRIBING"
            rt.metrics = {}
            transcript, ms = await asyncio.to_thread(rt.ai.transcribe, audio, content_type)
            rt.check(revision)
            rt.metrics["stt_ms"] = ms
            action, text = intent(transcript), transcript
        message = await rt.run(action, text, crop, revision, shot)
        rt.session.respond(message)
    except (CameraError, SarvamError) as exc:
        return service_failure(rt, revision, exc)
    finally:
        rt.busy, rt.phase = False, None
    return rt.status() | {"transcript": transcript, "stopped": action == "stop"}


@app.post("/api/command")
async def command(payload: Command, request: Request):
    text = payload.text.strip()
    if payload.action == "query" and not text:
        raise HTTPException(400, "ഒരു ചോദ്യം അല്ലെങ്കിൽ ദൗത്യം നൽകുക.")
    action = intent(text) if payload.action == "query" else payload.action
    return await execute(request.app.state.runtime, action, text, payload.crop)


@app.post("/api/voice")
async def voice(request: Request, zoom: float = Query(1, ge=1, le=4),
                x: float = Query(0.5, ge=0, le=1), y: float = Query(0.5, ge=0, le=1)):
    content_type = request.headers.get("content-type", "").split(";")[0].strip()
    if content_type not in ("audio/webm", "audio/ogg", "audio/mp4", "audio/wav"):
        raise HTTPException(415, "ഈ റെക്കോർഡിങ് ഫോർമാറ്റ് പിന്തുണയ്ക്കുന്നില്ല.")
    audio = bytearray()
    async for chunk in request.stream():
        if len(audio) + len(chunk) > 3_000_000:
            raise HTTPException(413, "റെക്കോർഡിങ് വളരെ വലുതാണ്. ചുരുക്കി വീണ്ടും സംസാരിക്കുക.")
        audio.extend(chunk)
    if not audio:
        raise HTTPException(400, "ശബ്ദം റെക്കോർഡ് ചെയ്തില്ല. വീണ്ടും സംസാരിക്കുക.")
    return await execute(request.app.state.runtime, "voice", "", Crop(zoom=zoom, x=x, y=y),
                         audio=bytes(audio), content_type=content_type)


@app.post("/api/speech")
async def speech(payload: SpeechRequest, request: Request):
    rt = request.app.state.runtime
    text = payload.text.strip()
    if not malayalam(text):
        raise HTTPException(400, "മലയാളത്തിലുള്ള വാചകം നൽകുക.")
    try:
        async with rt.speech_lock:
            rt.check(payload.revision)
            clip, ms, cached = await asyncio.to_thread(rt.ai.speak, text)
            rt.check(payload.revision)
    except SarvamError as exc:
        raise HTTPException(503, str(exc)) from None
    return Response(clip, media_type="audio/mpeg", headers={
        "X-TTS-Ms": str(ms), "X-TTS-Cache": "hit" if cached else "miss",
    })


PHONE_SHOT_BASE = 1_000_000
PHONE_BODY_LIMIT = 10_000_000
PHONE_IMAGE_LIMIT = 6_000_000
PHONE_AUDIO_LIMIT = 3_000_000
PHONE_MAX_PIXELS = 16_000_000
PHONE_AUDIO_TYPES = {"audio/wav", "audio/mp4", "audio/webm", "audio/ogg"}
PHONE_UNAUTHORIZED = {"ok": False, "reply": "അംഗീകൃത ആക്‌സസ് കോഡ് നൽകുക.",
                      "transcript": None, "audio_b64": None, "audio_type": "audio/mpeg",
                      "debug": {}}


def phone_allowed(request):
    token = os.getenv("PHONE_TOKEN", "")
    authorization = request.headers.get("authorization", "")
    return (bool(token) and authorization.startswith("Bearer ")
            and hmac.compare_digest(authorization[7:].encode(), token.encode()))


def phone_speech(text):
    if len(text) > 700:
        head = text[:700]
        boundary = max(head.rfind("।"), head.rfind(". "))
        if boundary > 200:
            head = head[:boundary + 1]
        else:
            space = head.rfind(" ")
            head = head[:space] if space > 200 else head
        text = head.strip()
    return text if malayalam(text) else None


async def phone_clip(rt, text):
    spoken = phone_speech(text)
    if spoken is None:
        return None
    try:
        started = perf_counter()
        async with rt.speech_lock:
            clip, _, _ = await asyncio.to_thread(rt.ai.speak, spoken)
        rt.metrics["tts_ms"] = round((perf_counter() - started) * 1000, 1)
        return base64.b64encode(clip).decode("ascii")
    except Exception:
        return None


def phone_parts(text):
    spoken = phone_speech(text)
    if spoken is None:
        return []
    pieces = [p.strip() for p in re.split(r"(?<=[।?!])\s+|(?<=\.)\s+|\n+", spoken) if p.strip()]
    merged = []
    for piece in pieces:
        if merged and len(merged[-1]) < 15:
            merged[-1] += " " + piece
        else:
            merged.append(piece)
    if len(merged) > 1 and len(merged[-1]) < 15:
        merged[-2] += " " + merged.pop()
    if len(merged) <= 3:
        return merged or [spoken]
    first, rest = merged[0], merged[1:]
    cut = min(range(1, len(rest)), key=lambda i: abs(len(" ".join(rest[:i])) - len(" ".join(rest[i:]))))
    return [first, " ".join(rest[:cut]), " ".join(rest[cut:])]


async def phone_part_clip(rt, text, gate):
    async with gate:
        try:
            clip, _, _ = await asyncio.to_thread(rt.ai.speak, text)
            return base64.b64encode(clip).decode("ascii")
        except Exception:
            return None


def ndjson(obj):
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


async def phone_stream(rt, message, transcript, debug, started):
    parts = phone_parts(message)
    gate = asyncio.Semaphore(3)
    tasks = [asyncio.create_task(phone_part_clip(rt, part, gate)) for part in parts]
    if debug is not None:
        debug["meta"].update(parts=len(parts))
    yield ndjson({"type": "meta", "ok": True, "reply": message, "transcript": transcript,
                  "audio_type": "audio/mpeg", "parts": len(parts), "debug": phone_debug_public(debug)})
    ttfa = None
    try:
        for index, task in enumerate(tasks):
            clip = await task
            if ttfa is None and clip:
                ttfa = round((perf_counter() - started) * 1000, 1)
            yield ndjson({"type": "audio", "index": index, "audio_b64": clip})
        timings = {**(debug["meta"]["timings_ms"] if debug is not None else {}), "ttfa": ttfa}
        yield ndjson({"type": "done", "debug": {**phone_debug_public(debug), "timings_ms": timings,
                                                "ttfa_ms": ttfa}})
    finally:
        for task in tasks:
            task.cancel()
        if debug is not None:
            debug["meta"]["timings_ms"] = {**debug["meta"]["timings_ms"], "ttfa": ttfa,
                                           "total_stream": round((perf_counter() - started) * 1000, 1)}
            phone_debug_finish(debug)


PHONE_DEBUG_LIMIT = 30
PHONE_DEBUG_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9]{3}")
PHONE_AUDIO_EXT = {"audio/wav": "wav", "audio/mp4": "m4a", "audio/webm": "webm", "audio/ogg": "ogg"}
phone_debug_records = deque(maxlen=PHONE_DEBUG_LIMIT)


def phone_debug_enabled():
    return os.getenv("PHONE_DEBUG", "1") != "0"


def phone_debug_timings(rt, record):
    metrics = rt.metrics or {}
    return {"stt": metrics.get("stt_ms"), "vision": metrics.get("vision_ms"),
            "tts": metrics.get("tts_ms"), "locate": metrics.get("locate_ms"),
            "total": round((perf_counter() - record["started"]) * 1000, 1)}


def phone_debug_public(record):
    if record is None:
        return {}
    meta = record["meta"]
    return {"action": meta.get("action"), "route": meta.get("route"),
            "transcript_language": meta.get("stt_language"),
            "timings_ms": meta.get("timings_ms") or {}}


def phone_debug_start(jpeg, audio, audio_type, *, image_size, audio_seconds):
    if not phone_debug_enabled():
        return None
    stamp = datetime.now()
    record_id = stamp.strftime("%Y%m%d-%H%M%S-%f")[:-3]
    directory = Path("debug") / record_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "image.jpg").write_bytes(jpeg)
    if audio is not None:
        (directory / f"audio.{PHONE_AUDIO_EXT.get(audio_type, 'bin')}").write_bytes(audio)
    record = {"id": record_id, "time": stamp.isoformat(timespec="milliseconds"),
              "dir": directory, "started": perf_counter(),
              "meta": {"ok": None, "http_status": None, "action": None, "route": "error",
                       "transcript": None, "stt_language": None, "reply": None, "error": None,
                       "timings_ms": {}, "image_bytes": len(jpeg),
                       "audio_bytes": len(audio) if audio is not None else 0,
                       "image_size": image_size, "audio_seconds": audio_seconds}}
    phone_debug_records.append(record)
    root = Path("debug")
    if root.is_dir():
        for old in sorted(p for p in root.iterdir() if p.is_dir())[:-PHONE_DEBUG_LIMIT]:
            shutil.rmtree(old, ignore_errors=True)
    return record


def phone_debug_finish(record, **fields):
    if record is None:
        return
    record["meta"].update(fields)
    meta = {"id": record["id"], "time": record["time"], **record["meta"]}
    (record["dir"] / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def phone_debug_find(record_id):
    if not PHONE_DEBUG_ID.fullmatch(record_id):
        return None
    return next((record for record in phone_debug_records if record["id"] == record_id), None)


def wav_seconds(data):
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    rate = int.from_bytes(data[24:28], "little")
    size = int.from_bytes(data[40:44], "little")
    return round(size / (rate * 2), 2) if rate else None


async def phone_fail(rt, status, message, transcript=None, debug=None):
    if debug is not None:
        phone_debug_finish(debug, ok=False, http_status=status, reply=message, error=message,
                           timings_ms=phone_debug_timings(rt, debug))
    return JSONResponse({"ok": False, "reply": message, "transcript": transcript,
                         "audio_b64": await phone_clip(rt, message),
                         "audio_type": "audio/mpeg", "debug": phone_debug_public(debug)},
                        status_code=status)


def phone_decode(data):
    try:
        return base64.b64decode("".join(data.split()), validate=True)
    except (binascii.Error, ValueError):
        return None


@app.get("/api/phone/ping")
async def phone_ping(request: Request):
    if not phone_allowed(request):
        return JSONResponse(PHONE_UNAUTHORIZED, status_code=401)
    return {"ok": True, "ai_ready": bool(request.app.state.runtime.ai.key)}


@app.post("/api/phone/ask")
async def phone_ask(request: Request):
    rt = request.app.state.runtime
    started = perf_counter()
    if not phone_allowed(request):
        return JSONResponse(PHONE_UNAUTHORIZED, status_code=401)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > PHONE_BODY_LIMIT:
            return await phone_fail(rt, 413, "അഭ്യർത്ഥന വളരെ വലുതാണ്. ചെറുതാക്കി വീണ്ടും ശ്രമിക്കുക.")
        body.extend(chunk)
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return await phone_fail(rt, 400, "അഭ്യർത്ഥന വായിക്കാനായില്ല. വീണ്ടും ശ്രമിക്കുക.")
    image_b64 = payload.get("image_b64")
    audio_b64 = payload.get("audio_b64")
    audio_type = payload.get("audio_type")
    if not isinstance(image_b64, str) or not image_b64.strip():
        return await phone_fail(rt, 400, "ഫോട്ടോ ലഭിച്ചില്ല. വീണ്ടും എടുക്കുക.")
    jpeg_data = phone_decode(image_b64)
    if jpeg_data is None:
        return await phone_fail(rt, 400, "ഫോട്ടോ അയയ്ക്കാൻ കഴിഞ്ഞില്ല. വീണ്ടും ശ്രമിക്കുക.")
    if not jpeg_data:
        return await phone_fail(rt, 400, "ശരിയായ ഫോട്ടോ ലഭിച്ചില്ല. വീണ്ടും എടുക്കുക.")
    if len(jpeg_data) > PHONE_IMAGE_LIMIT:
        return await phone_fail(rt, 413, "ഫോട്ടോ വളരെ വലുതാണ്. ചെറുതാക്കി വീണ്ടും എടുക്കുക.")
    try:
        with Image.open(BytesIO(jpeg_data)) as source:
            if source.format != "JPEG":
                return await phone_fail(rt, 400, "ശരിയായ ഫോട്ടോ ലഭിച്ചില്ല. വീണ്ടും എടുക്കുക.")
            if source.width * source.height > PHONE_MAX_PIXELS:
                return await phone_fail(rt, 400, "ഫോട്ടോ വളരെ വലുതാണ്. ചെറുതാക്കി വീണ്ടും എടുക്കുക.")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return await phone_fail(rt, 400, "ശരിയായ ഫോട്ടോ ലഭിച്ചില്ല. വീണ്ടും എടുക്കുക.")
    stream = payload.get("stream") is True
    audio = None
    if audio_b64 is not None:
        if not isinstance(audio_type, str) or audio_type not in PHONE_AUDIO_TYPES:
            return await phone_fail(rt, 400, "ഈ റെക്കോർഡിങ് ഫോർമാറ്റ് പിന്തുണയ്ക്കുന്നില്ല.")
        if not isinstance(audio_b64, str):
            return await phone_fail(rt, 400, "ശബ്ദം അയയ്ക്കാൻ കഴിഞ്ഞില്ല. വീണ്ടും ശ്രമിക്കുക.")
        audio = phone_decode(audio_b64)
        if audio is None:
            return await phone_fail(rt, 400, "ശബ്ദം അയയ്ക്കാൻ കഴിഞ്ഞില്ല. വീണ്ടും ശ്രമിക്കുക.")
        if not audio:
            return await phone_fail(rt, 400, "ശബ്ദം റെക്കോർഡ് ചെയ്തില്ല. വീണ്ടും സംസാരിക്കുക.")
        if len(audio) > PHONE_AUDIO_LIMIT:
            return await phone_fail(rt, 413, "റെക്കോർഡിങ് വളരെ വലുതാണ്. ചുരുക്കി വീണ്ടും സംസാരിക്കുക.")
    rt.phone_seq += 1
    shot = Snapshot(PHONE_SHOT_BASE + rt.phone_seq,
                    jpeg_encode(image, rt.width, rt.quality), 0.0, 0.0, 0.0)
    debug = phone_debug_start(shot.jpeg, audio, audio_type,
                              image_size=f"{image.width}x{image.height}",
                              audio_seconds=wav_seconds(audio) if audio_type == "audio/wav" else None)
    if rt.busy:
        return await phone_fail(rt, 409, "മുൻ അഭ്യർത്ഥന തീരുന്നതുവരെ കാത്തിരിക്കുക.", debug=debug)
    rt.busy, rt.error = True, None
    revision = rt.revision
    rt.metrics = {}
    transcript = None
    action = route = stt_language = None
    try:
        if audio is not None:
            rt.phase = "TRANSCRIBING"
            transcript, ms = await asyncio.to_thread(rt.ai.transcribe, audio, audio_type)
            rt.check(revision)
            rt.metrics["stt_ms"] = ms
            stt_language = rt.ai.last_language
            action, text = intent(transcript), transcript
        else:
            action, text = "capture", ""
        debug["meta"].update(action=action, transcript=transcript, stt_language=stt_language) \
            if debug is not None else None
        has_task = bool(rt.session.task and not rt.session.finished)
        if action == "query" or (action == "capture" and not has_task):
            rt.phase = "ANSWERING"
            task_ctx = None
            if has_task:
                task_ctx = {"active_task": rt.session.task,
                            "pending_instruction": rt.session.public().get("current_instruction")}
            result, ms = await asyncio.to_thread(
                rt.ai.answer, shot.jpeg, text, list(rt.phone_history), task_ctx)
            rt.check(revision)
            rt.metrics["vision_ms"] = ms
            if result.wants_location:
                rt.phase = "LOCATING"
                located, ms = await asyncio.to_thread(rt.ai.locate, shot.jpeg, text, rt.locate_target)
                rt.check(revision)
                rt.metrics["locate_ms"] = ms
                route = "locate"
                message = compose_location(located)
                rt.locate_target = located.target_label or rt.locate_target
                if debug is not None:
                    debug["meta"]["locate"] = located.model_dump()
            elif result.is_device_task:
                route = "guide"
                message = await rt.run(action, text, Crop(), revision, shot)
            else:
                route = "general"
                message = result.answer_ml
            rt.session.respond(message)
            if route in ("general", "locate"):
                rt.phone_history.append({"question": text[:220], "answer": message[:280]})
        else:
            route = "guide"
            message = await rt.run(action, text, Crop(), revision, shot)
            rt.session.respond(message)
        clip = None if stream else await phone_clip(rt, message)
    except (CameraError, SarvamError) as exc:
        if debug is not None:
            debug["meta"].update(route=route or "error")
        return await phone_fail(rt, 503, str(exc), transcript, debug=debug)
    except HTTPException as exc:
        if debug is not None:
            debug["meta"].update(route=route or "error")
        return await phone_fail(rt, exc.status_code, str(exc.detail), transcript, debug=debug)
    finally:
        rt.busy, rt.phase = False, None
    if debug is not None:
        debug["meta"].update(ok=True, http_status=200, route=route, reply=message, error=None,
                             timings_ms=phone_debug_timings(rt, debug))
        if not stream:
            phone_debug_finish(debug)
    if stream:
        return StreamingResponse(phone_stream(rt, message, transcript, debug, started),
                                 media_type="application/x-ndjson")
    return JSONResponse({"ok": True, "reply": message, "transcript": transcript,
                         "audio_b64": clip, "audio_type": "audio/mpeg",
                         "debug": phone_debug_public(debug)})


DEBUG_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>DrishtiGuide debug</title>
<style>body{background:#14181c;color:#e8ecef;font-family:sans-serif;margin:16px}
.card{background:#1e242b;border-radius:10px;padding:12px;margin:12px 0}
img{max-width:220px;border-radius:6px;cursor:zoom-in}
.meta{font-size:13px;line-height:1.5}.reply{color:#9fd3a8}.err{color:#e08f8f}
audio{width:100%;margin-top:6px}a{color:#8ab4f8}
.grid{border-collapse:collapse;margin-top:8px}.grid td{border:1px solid #445;padding:4px 10px;text-align:center}.tgt{background:#2e7d32}.fng{outline:2px solid #f2b544}</style></head>
<body><h2>Phone requests</h2><div id="list">loading…</div>
<script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const grid=l=>l&&l.rows?`<table class="grid">${l.rows.map((row,r)=>`<tr>${row.map((k,c)=>`<td class="${r+1==l.target_row&&c+1==l.target_col?'tgt':''} ${l.finger_visible&&r+1==l.finger_row&&c+1==l.finger_col?'fng':''}">${esc(k)||'·'}</td>`).join('')}</tr>`).join('')}</table>`:'';
async function load(){
  const items=await (await fetch('/api/debug/items')).json();
  document.getElementById('list').innerHTML=items.map(m=>{
    const t=m.timings_ms||{};
    return `<div class="card"><a href="/api/debug/${m.id}/image" target="_blank"><img src="/api/debug/${m.id}/image" loading="lazy"></a>
    <div class="meta"><b>${esc(m.time)}</b> · ${esc(m.action)} · route=${esc(m.route)} · http ${m.http_status} · ok=${m.ok}
    ${m.stt_language?` · stt=${esc(m.stt_language)}`:''}<br>
    transcript: ${esc(m.transcript)}<br>
    reply: <span class="reply">${esc(m.reply)}</span>
    ${m.error?`<br><span class="err">${esc(m.error)}</span>`:''}
    <br>timings: stt=${t.stt??'-'} vision=${t.vision??'-'} tts=${t.tts??'-'} locate=${t.locate??'-'} ttfa=${t.ttfa??'-'} total=${t.total??'-'} ms
    · image ${m.image_bytes}B ${esc(m.image_size)}${m.audio_bytes?` · audio ${m.audio_bytes}B${m.audio_seconds!=null?` (${m.audio_seconds}s)`:''}`:''}</div>
    ${grid(m.locate)}
    ${m.audio_file?`<audio controls src="/api/debug/${m.id}/audio"></audio>`:''}</div>`;
  }).join('')||'no requests yet';
}
load();setInterval(load,5000);
</script></body></html>"""


@app.get("/debug")
async def debug_page():
    if not phone_debug_enabled():
        raise HTTPException(404)
    return HTMLResponse(DEBUG_HTML)


@app.get("/api/debug/items")
async def debug_items():
    if not phone_debug_enabled():
        raise HTTPException(404)
    items = []
    for record in reversed(phone_debug_records):
        audio = next(record["dir"].glob("audio.*"), None)
        items.append(dict(record["meta"], id=record["id"], time=record["time"],
                          audio_file=audio.name if audio else None))
    return items


@app.get("/api/debug/{record_id}/image")
async def debug_image(record_id: str):
    record = phone_debug_find(record_id)
    if record is None or not phone_debug_enabled():
        raise HTTPException(404)
    return FileResponse(record["dir"] / "image.jpg")


@app.get("/api/debug/{record_id}/audio")
async def debug_audio(record_id: str):
    record = phone_debug_find(record_id)
    if record is None or not phone_debug_enabled():
        raise HTTPException(404)
    audio = next(record["dir"].glob("audio.*"), None)
    if audio is None:
        raise HTTPException(404)
    return FileResponse(audio)


TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def pair_addresses():
    found = []
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True,
                             text=True, timeout=5).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "inet":
                addr = parts[3].split("/")[0]
                if not addr.startswith("127."):
                    found.append(addr)
    except Exception:
        pass
    if not found:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            found.append(sock.getsockname()[0])
            sock.close()
        except Exception:
            pass
    return found


def pair_candidates():
    port = os.getenv("PORT", "8000")
    candidates = []
    for addr in pair_addresses():
        try:
            label = "Tailscale" if ipaddress.ip_address(addr) in TAILSCALE_NET else "Wi-Fi/LAN"
        except ValueError:
            label = "Wi-Fi/LAN"
        candidates.append((label, f"http://{addr}:{port}"))
    public = os.getenv("PUBLIC_URL", "").strip()
    if public:
        candidates.insert(0, ("public", public.rstrip("/")))
    return candidates


def pair_link(base, token):
    return "thirdeye://pair?" + urlencode({"url": base, "token": token})


PAIR_HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DrishtiGuide pairing</title>
<style>body{background:#14181c;color:#e8ecef;font-family:sans-serif;max-width:640px;margin:24px auto;padding:0 16px}
.card{background:#1e242b;border-radius:12px;padding:16px;margin:16px 0}
svg{display:block;margin:12px auto;max-width:100%}
code{display:block;background:#0d1117;padding:10px;border-radius:8px;word-break:break-all;margin:8px 0}
.warn{color:#e8b64c}.btn{display:inline-block;background:#2e7d32;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;margin-top:8px}</style></head>
<body><h2>Pair your phone</h2>
<p class="warn">This QR contains your access token; do not share the screen.</p>
<p>Scan with your phone camera. If it does not open DrishtiGuide, copy the link text and tap Paste pairing link in the app.</p>
__CARDS__
</body></html>"""


@app.get("/pair")
async def pair_page():
    token = os.getenv("PHONE_TOKEN", "")
    if not token:
        return HTMLResponse(PAIR_HTML.replace("__CARDS__",
            "<p>PHONE_TOKEN is not set in .env — generate one and restart the server.</p>"))
    cards = []
    for label, base in pair_candidates():
        link = pair_link(base, token)
        try:
            svg = segno.make(link, error="m").svg_inline(scale=4, border=2,
                                                       dark="#e8ecef", light="#1e242b")
        except Exception:
            svg = ""
        safe_base, safe_link = html.escape(base), html.escape(link)
        cards.append(
            f'<div class="card"><b>{html.escape(label)}</b><br><code>{safe_base}</code>'
            f'<code>{safe_link}</code>{svg}'
            f'<a class="btn" href="{safe_link}">Open in DrishtiGuide</a></div>')
    return HTMLResponse(PAIR_HTML.replace("__CARDS__",
        "".join(cards) or "<p>No network address found.</p>"))


def host_binding_ok(host, token):
    return host in ("127.0.0.1", "localhost") or len(token or "") >= 24


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    if not host_binding_ok(host, os.getenv("PHONE_TOKEN", "")):
        raise SystemExit("HOST other than 127.0.0.1 or localhost requires PHONE_TOKEN of at least "
                         "24 characters in .env. Generate one with: "
                         "python -c \"import secrets;print(secrets.token_urlsafe(32))\"")
    if os.getenv("PHONE_TOKEN"):
        log.info("Phone API enabled at http://%s:%d/api/phone/ (bearer token required)", host, port)
        log.info("Pair a phone: open http://127.0.0.1:%d/pair on this PC", port)
    else:
        log.info("Phone API disabled at http://%s:%d: PHONE_TOKEN is not set.", host, port)
    uvicorn.run(app, host=host, port=port, access_log=False)
