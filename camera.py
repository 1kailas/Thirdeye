"""One MJPEG reader, with a bounded buffer and a freshness-checked still capture."""

from dataclasses import dataclass
from io import BytesIO
from threading import Condition, Thread
from time import monotonic, perf_counter
from urllib.parse import urlsplit

import requests
from PIL import Image, UnidentifiedImageError

DEFAULT_URL = "http://100.109.140.21:8080/video"
MAX_AGE = 2.5
MAX_JPEG = 8 * 1024 * 1024


class CameraError(Exception):
    pass


def validate_url(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port
        if (parsed.scheme in ("http", "https") and parsed.hostname
                and parsed.username is None and not parsed.fragment
                and (port is None or port > 0)
                and not any(c.isspace() or ord(c) < 32 for c in url)
                and "\\" not in url):
            if parsed.path in ("", "/"):
                return parsed._replace(path="/video").geturl()
            return url
    except ValueError:
        pass
    raise CameraError("http:// അല്ലെങ്കിൽ https:// ഉപയോഗിച്ചുള്ള ക്യാമറ വിലാസം നൽകുക.")


@dataclass(frozen=True)
class Frame:
    number: int
    received: float
    image: Image.Image
    preview: bytes


@dataclass(frozen=True)
class Snapshot:
    number: int
    jpeg: bytes
    frame_age_ms: float
    capture_ms: float
    jpeg_ms: float


def jpeg_encode(image, max_width, quality):
    width, height = image.size
    # Bound the long edge too when the phone is in portrait orientation.
    ratio = min(1, max_width / max(width, height))
    if ratio < 1:
        image = image.resize((max(1, round(width * ratio)), max(1, round(height * ratio))),
                             Image.Resampling.LANCZOS)
    out = BytesIO()
    image.save(out, "JPEG", quality=quality)
    return out.getvalue()


class Camera:
    def __init__(self, url=DEFAULT_URL, rotation=0):
        self.url = validate_url(url)
        self.rotation = rotation
        self.changed = Condition()
        self.enabled = False
        self.closed = False
        self.generation = 0
        self.number = 0
        self.frame = None
        self.state = "disconnected"
        self.detail = ""
        self.connect_ms = None
        self.worker = Thread(target=self._run, name="phone-camera", daemon=True)

    def start(self):
        self.worker.start()
        self.configure("connect")

    def close(self):
        with self.changed:
            self.closed = True
            self.changed.notify_all()
        self.worker.join(timeout=4)

    def configure(self, action, url=None, rotation=None):
        url = validate_url(url) if url is not None else None
        with self.changed:
            source_changed = url is not None and url != self.url
            if url is not None:
                self.url = url
            if rotation is not None:
                self.rotation = rotation
            if action != "rotate":
                self.enabled = action != "disconnect"
                self._set("connecting" if self.enabled else "disconnected")
                self.generation += 1
                self.connect_ms = None
            elif self.frame is not None:
                # Prevent a capture using the old orientation while the next frame arrives.
                self.frame = None
            self.changed.notify_all()
            return source_changed

    def _set(self, state, detail=""):
        self.state, self.detail = state, detail
        self.frame = None

    def status(self):
        with self.changed:
            frame = self.frame
            age = (monotonic() - frame.received) * 1000 if frame else None
            state = self.state
            detail = self.detail
            if state == "ready" and (age is None or age > MAX_AGE * 1000):
                state = "connecting" if age is None else "reconnecting"
                detail = "" if age is None else "പുതിയ ദൃശ്യം ലഭിക്കുന്നില്ല."
            return dict(state=state, detail=detail, url=self.url, rotation=self.rotation,
                        width=frame.image.width if frame else None,
                        height=frame.image.height if frame else None,
                        frame_age_ms=round(age, 1) if age is not None else None,
                        connect_ms=self.connect_ms)

    def preview(self, after):
        with self.changed:
            frame = self.frame
            if frame and frame.number > after and monotonic() - frame.received <= MAX_AGE:
                return frame.number, frame.preview
        return None

    def snapshot(self, width, quality, zoom=1, x=0.5, y=0.5, *, fresh=False):
        started = perf_counter()
        cutoff = monotonic()
        with self.changed:
            generation = self.generation
            while self.enabled and self.generation == generation and not self.closed:
                frame = self.frame
                if (frame and monotonic() - frame.received <= MAX_AGE
                        and (not fresh or frame.received >= cutoff)):
                    break
                if (not fresh or self.state != "ready" or monotonic() >= cutoff + 1.5):
                    frame = None
                    break
                self.changed.wait(timeout=max(0, cutoff + 1.5 - monotonic()))
            else:
                frame = None
            if frame is None:
                raise CameraError("പുതിയ ക്യാമറ ചിത്രം ലഭ്യമല്ല. ക്യാമറ ബന്ധം പരിശോധിച്ച് വീണ്ടും ശ്രമിക്കുക.")
        image = frame.image
        if zoom > 1:
            w, h = image.size
            cw, ch = max(1, round(w / zoom)), max(1, round(h / zoom))
            left, top = round((w - cw) * x), round((h - ch) * y)
            image = image.crop((left, top, left + cw, top + ch))
        capture_ms = (perf_counter() - started) * 1000
        encode_start = perf_counter()
        jpeg = jpeg_encode(image, width, quality)
        return Snapshot(frame.number, jpeg, round((monotonic() - frame.received) * 1000, 1),
                        round(capture_ms, 1), round((perf_counter() - encode_start) * 1000, 1))

    def _run(self):
        with requests.Session() as session:
            # The Tailscale camera is a direct connection, even on laptops with a proxy.
            session.trust_env = False
            delay = 1
            while True:
                with self.changed:
                    self.changed.wait_for(lambda: self.closed or self.enabled)
                    if self.closed:
                        return
                    generation, url = self.generation, self.url
                try:
                    self._read(session, url, generation)
                except (requests.RequestException, CameraError) as exc:
                    message = self._connection_error(exc)
                    with self.changed:
                        if self.generation != generation or self.closed:
                            delay = 1
                            continue
                        if self.state == "ready":
                            delay = 1
                        self._set("reconnecting", message)
                        self.changed.notify_all()
                        self.changed.wait_for(
                            lambda: self.closed or self.generation != generation, timeout=delay)
                        delay = min(delay * 2, 8)
                else:
                    delay = 1

    @staticmethod
    def _connection_error(exc):
        if isinstance(exc, CameraError):
            return str(exc)
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return f"ക്യാമറ HTTP {exc.response.status_code}. IP Webcam വിലാസം പരിശോധിക്കുക."
        if isinstance(exc, requests.Timeout):
            return "ക്യാമറ മറുപടി നൽകിയില്ല. ഫോണിലെ IP Webcam പരിശോധിക്കുക."
        return "ക്യാമറ ബന്ധം ലഭിച്ചില്ല. Tailscale, IP Webcam, പോർട്ട് എന്നിവ പരിശോധിക്കുക."

    def _read(self, session, url, generation):
        opened = last_valid = monotonic()
        buf = bytearray()
        with session.get(url, stream=True, timeout=(2.5, 2.5), allow_redirects=False) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if response.status_code != 200 or not content_type.startswith("multipart/"):
                raise CameraError(f"HTTP {response.status_code}: MJPEG സ്ട്രീം ലഭിച്ചില്ല. /video വിലാസം പരിശോധിക്കുക.")
            for chunk in response.iter_content(8192):
                with self.changed:
                    if self.generation != generation or self.closed:
                        return
                now = monotonic()
                if now - last_valid > 3:
                    raise CameraError("സ്ട്രീമിൽ ശരിയായ ക്യാമറ ചിത്രങ്ങൾ ലഭിച്ചില്ല.")
                buf.extend(chunk)
                while True:
                    start = buf.find(b"\xff\xd8")
                    if start < 0:
                        del buf[:-1]
                        break
                    end = buf.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        if len(buf) - start > MAX_JPEG:
                            raise CameraError("ക്യാമറ ഫ്രെയിം വളരെ വലുതാണ്. IP Webcam റെസല്യൂഷൻ കുറയ്ക്കുക.")
                        del buf[:start]
                        break
                    raw = bytes(buf[start:end + 2])
                    del buf[:end + 2]
                    if now - last_valid >= 0.12 or last_valid == opened:
                        if self._publish(raw, generation, now, opened):
                            last_valid = now
            raise CameraError("ക്യാമറ സ്ട്രീം നിലച്ചു. വീണ്ടും ബന്ധിപ്പിക്കുന്നു.")

    def _publish(self, raw, generation, now, opened):
        with self.changed:
            rotation = self.rotation
        try:
            with Image.open(BytesIO(raw), formats=("JPEG",)) as source:
                if source.width * source.height > 16_000_000:
                    return False
                image = source.convert("RGB")
            if rotation:
                image = image.rotate(-rotation, expand=True)
            preview = jpeg_encode(image, 720, 72)
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
            return False
        with self.changed:
            if self.generation != generation or self.closed or self.rotation != rotation:
                return False
            if self.state != "ready":
                self.connect_ms = round((monotonic() - opened) * 1000, 1)
            self.number += 1
            self.frame = Frame(self.number, now, image, preview)
            self.state, self.detail = "ready", ""
            self.changed.notify_all()
        return True
