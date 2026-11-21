"""Local FastAPI server and the DrishtiGuide interaction controller."""

import asyncio
import logging
import os
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from camera import DEFAULT_URL, Camera, CameraError
from sarvam_client import SarvamClient, SarvamError
from settings import load_environment
from task_engine import TaskSession, malayalam

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
               "സ്ഥിരീകരിക്കൂ", "പരിശോധിക്കൂ", "ഒന്ന് പരിശോധിക്കൂ", "ചെയ്തു നോക്കൂ", "ശരിയായോ"),
    "next": ("അടുത്തത്", "അടുത്ത ഘട്ടം", "അടുത്ത ഘട്ടം പറയൂ", "ഇനി എന്ത് ചെയ്യണം"),
    "repeat": ("വീണ്ടും പറയൂ", "ഒന്നുകൂടി പറയൂ", "വീണ്ടും പറയുക", "ആവർത്തിക്കൂ", "ഒന്നൂടെ പറയൂ"),
    "locate": ("എവിടെയാണ്", "ബട്ടൺ എവിടെയാണ്", "ഏത് ബട്ടൺ", "ഏത് ബട്ടണാണ്", "ഏത് നോബ്"),
    "simplify": ("ലളിതമായി പറയൂ", "കുറച്ചുകൂടി ലളിതമായി പറയൂ", "എളുപ്പത്തിൽ പറയൂ"),
    "explain": ("കുറച്ചുകൂടി വിശദമായി പറയൂ", "വിശദമായി പറയൂ", "ഒന്ന് വിശദീകരിക്കൂ"),
    "stop": ("നിർത്തൂ", "നിർത്തുക", "നിർത്ത്", "ഇപ്പോൾ നിർത്തൂ", "മതി"),
    "continue": ("സ്ഥിരീകരിക്കാതെ തുടരൂ", "സ്ഥിരീകരിക്കാതെ തുടരുക", "ഫോട്ടോ സ്ഥിരീകരണമില്ലാതെ തുടരൂ"),
    "reset": ("പുതിയ ദൗത്യം", "ദൗത്യം മായ്ക്കൂ", "ദൗത്യം പുനരാരംഭിക്കൂ"),
}
PHRASES = {phrase: action for action, words in VOICE_COMMANDS.items() for phrase in words}


def intent(text):
    normalized = unicodedata.normalize("NFC", text)
    normalized = " ".join(normalized.strip(" .!?…,।\t\n").split())
    return PHRASES.get(normalized, "query")


class Runtime:
    def __init__(self):
        rotation = int(os.getenv("CAMERA_ROTATION", "0"))
        self.width = int(os.getenv("IMAGE_MAX_WIDTH", "1280"))
        self.quality = int(os.getenv("JPEG_QUALITY", "82"))
        if rotation not in (0, 90, 180, 270) or not 320 <= self.width <= 2560 or not 40 <= self.quality <= 95:
            raise ValueError("Check CAMERA_ROTATION, IMAGE_MAX_WIDTH and JPEG_QUALITY in .env.")
        self.camera = Camera(os.getenv("CAMERA_URL", DEFAULT_URL), rotation)
        self.ai = SarvamClient()
        self.session = TaskSession()
        self.revision = 0
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

    async def run(self, action, text, crop, revision):
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
            return await self.verify(task, crop, revision)
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
        shot = await self.capture(crop, revision) if capture else task.latest
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

    async def verify(self, task, crop, revision):
        if not task.step or task.closed_step:
            return "പരിശോധിക്കാൻ ഇപ്പോൾ ചെയ്യേണ്ട ഒരു ഘട്ടമില്ല."
        if not task.step.visually_verifiable:
            return "ഈ മാറ്റം ഫോട്ടോയിൽ പരിശോധിക്കാനാവില്ല. ചെയ്തുകഴിഞ്ഞാൽ സ്ഥിരീകരിക്കാതെ തുടരുക."
        before = task.baseline
        after = await self.capture(crop, revision, fresh=True)
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
    runtime.camera.start()
    try:
        yield
    finally:
        await asyncio.to_thread(runtime.camera.close)
        runtime.ai.close()


app = FastAPI(title="DrishtiGuide AI", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


@app.middleware("http")
async def local_only(request: Request, call_next):
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


async def execute(rt, action, text, crop, *, audio=None, content_type=None):
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
        message = await rt.run(action, text, crop, revision)
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    host = os.getenv("HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost"):
        raise SystemExit("Set HOST to 127.0.0.1 or localhost.")
    uvicorn.run(app, host=host, port=int(os.getenv("PORT", "8000")), access_log=False)
