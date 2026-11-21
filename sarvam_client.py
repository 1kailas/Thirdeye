"""Sarvam's documented REST endpoints. The subscription key stays on the backend."""

import base64
import json
import logging
import os
from collections import OrderedDict
from time import perf_counter

import requests
from pydantic import ValidationError

from task_engine import ANSWER_SYSTEM, Answer, Guidance, SYSTEM, Verification, VERIFY_SYSTEM

log = logging.getLogger("drishtiguide")


class SarvamError(Exception):
    pass


def elapsed(start):
    return round((perf_counter() - start) * 1000, 1)


MODE_PROMPTS = {
    "inspect": "Describe the device and relevant visible controls. Do not propose an action.",
    "query": (
        "Classify the user request. If it expresses a new goal (including asking where to find a "
        "specific control without an active task), set intent=new_task and task_understood to "
        "the goal in Malayalam. A clarification of a pending step is intent=answer. Give any "
        "clarification in message_ml, keep current_step=null, and do not replace the step. "
        "If the user is explicitly asking to proceed with their existing task and no step is "
        "pending, use intent=advance. On new_task or advance, propose one supported step if possible. "
        "If the scene is too unclear, retain the user goal and ask for a suitable image."
    ),
    "next": "Use the completed steps and the photo. Propose one remaining action for the existing goal.",
    "resume": "Use the new photo to propose one action for the existing goal if unambiguous.",
    "simplify": ("Explain the pending instruction in simpler Malayalam without changing the control "
                 "or action. Set intent=answer and current_step=null. Do not advance the task."),
    "explain": ("Explain the pending step for someone who cannot see the photo. In up to four short "
                "Malayalam sentences, start at a visible physical landmark, describe the control's "
                "position and shape, explain the same single action, and explain how to check it "
                "if possible. Do not add an action. Set intent=answer and current_step=null."),
}


class SarvamClient:
    def __init__(self):
        self.key = os.getenv("SARVAM_API_KEY", "").strip()
        self.vision_model = os.getenv("VISION_MODEL", "gemma4")
        self.fallback_model = os.getenv("VISION_FALLBACK_MODEL", "glm5.3-flash")
        self.stt_model = os.getenv("STT_MODEL", "saaras:v4")
        self.language = os.getenv("LANGUAGE_CODE", "ml-IN")
        self.last_language = None
        self.tts_model = os.getenv("TTS_MODEL", "bulbul:v4-flash")
        default_voice = "simran" if self.tts_model == "bulbul:v3" else "simran_en_customer"
        self.voice = os.getenv("TTS_VOICE", default_voice)
        self.pace = float(os.getenv("TTS_PACE", "0.95"))
        if not 0.5 <= self.pace <= 2:
            raise ValueError("TTS_PACE must be between 0.5 and 2.")
        self.task_http = requests.Session()
        self.speech_http = requests.Session()
        self.speech_cache = OrderedDict()

    def close(self):
        self.task_http.close()
        self.speech_http.close()

    def _post(self, path, label, model, timeout, *, client=None, **kwargs):
        if not self.key:
            raise SarvamError(".env ൽ SARVAM_API_KEY നൽകി ആപ്പ് വീണ്ടും തുടങ്ങുക.")
        started = perf_counter()
        status = "connection"
        try:
            session = self.task_http if client is None else client
            response = session.post("https://api.sarvam.ai" + path,
                                    headers={"api-subscription-key": self.key},
                                    timeout=(5, timeout), allow_redirects=False, **kwargs)
            status = response.status_code
            if not 200 <= status < 300:
                reason = {
                    400: "ക്രമീകരണങ്ങൾ പരിശോധിക്കുക",
                    401: "API കീ പരിശോധിക്കുക",
                    402: "Sarvam ക്രെഡിറ്റ് പരിശോധിക്കുക",
                    403: "API കീയും മോഡലിനുള്ള അനുമതിയും പരിശോധിക്കുക",
                    404: "മോഡൽ ലഭ്യമാണോ എന്ന് പരിശോധിക്കുക",
                    413: "ചിത്രം അല്ലെങ്കിൽ റെക്കോർഡിങ് ചെറുതാക്കുക",
                    422: "അഭ്യർത്ഥന വായിക്കാനായില്ല",
                    429: "അൽപസമയം കഴിഞ്ഞ് വീണ്ടും ശ്രമിക്കുക",
                    503: "സേവനം ഇപ്പോൾ ലഭ്യമല്ല",
                }.get(status, "വീണ്ടും ശ്രമിക്കുക")
                if status in (403, 404) and path == "/v2/chat/completions":
                    if self.fallback_model and self.fallback_model != model:
                        reason += f". ആ മോഡൽ ലഭ്യമല്ലെങ്കിൽ VISION_MODEL={self.fallback_model} പരീക്ഷിക്കുക"
                raise SarvamError(f"Sarvam {label} · {model} · HTTP {status}. {reason}.")
            try:
                data = response.json()
            except ValueError:
                raise SarvamError(f"Sarvam {label} മറുപടി വായിക്കാനായില്ല. വീണ്ടും ശ്രമിക്കുക.") from None
            return data, elapsed(started)
        except requests.Timeout:
            status = "timeout"
            raise SarvamError(f"Sarvam {label} · {model}: സമയപരിധി കഴിഞ്ഞു. വീണ്ടും ശ്രമിക്കുക.") from None
        except requests.RequestException:
            raise SarvamError(f"Sarvam {label} · {model}: ബന്ധം ലഭിച്ചില്ല. ഇന്റർനെറ്റ് പരിശോധിക്കുക.") from None
        finally:
            log.info("Sarvam %s %s %s %.1f ms", path, model, status, elapsed(started))

    def guide(self, image, context, mode, question):
        request = {
            "operation": MODE_PROMPTS[mode],
            "device_relation_rule": (
                "Use first when no prior device is known. Reused session photos show the same "
                "device. For a new photo compare visible features with context, choosing different "
                "only for clear evidence of a different device, and unclear if correspondence "
                "cannot be established."
            ),
            "context": context, "user_request": question,
        }
        return self._vision(Guidance, SYSTEM, request, [("current image", image)])

    def verify(self, before, after, context):
        return self._vision(Verification, VERIFY_SYSTEM, context,
                            [("before", before), ("after", after)])

    def answer(self, image, question, history):
        request = {"user_question": question or "", "recent": history}
        return self._vision(Answer, ANSWER_SYSTEM, request, [("current image", image)])

    def _vision(self, schema, system, request, images):
        content = [{"type": "text", "text": json.dumps(request, ensure_ascii=False)}]
        for name, jpeg in images:
            content.append({"type": "text", "text": name})
            url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": url}})
        payload = {
            "model": self.vision_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": 0.1,
            "max_tokens": 2000,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": schema.__name__.lower(), "strict": True, "schema": schema.model_json_schema()
            }},
        }
        if self.vision_model == "glm5.3-flash":
            payload.update(reasoning_effort="low", max_tokens=3000)
        data, duration = self._post("/v2/chat/completions", "ചിത്രം",
                                    self.vision_model, 30, json=payload)
        try:
            result = schema.model_validate_json(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValidationError):
            raise SarvamError("ചിത്രത്തിന് ശരിയായ മറുപടി ലഭിച്ചില്ല. വീണ്ടും ശ്രമിക്കുക.") from None
        return result, duration

    def transcribe(self, audio, content_type):
        suffix = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4", "audio/wav": "wav"}
        data, duration = self._post("/speech-to-text", "ശബ്ദം തിരിച്ചറിയൽ",
                                    self.stt_model, 15, data={
                                        "model": self.stt_model, "language_code": self.language,
                                        "mode": "transcribe",
                                    }, files={"file": ("command." + suffix[content_type], audio, content_type)})
        self.last_language = data.get("language_code") if isinstance(data, dict) else None
        text = data.get("transcript") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise SarvamError("പറഞ്ഞത് തിരിച്ചറിയാനായില്ല. വീണ്ടും സംസാരിക്കുക.")
        return text.strip()[:500], duration

    def speak(self, text):
        if text in self.speech_cache:
            self.speech_cache.move_to_end(text)
            return self.speech_cache[text], 0.0, True
        data, duration = self._post(
            "/text-to-speech", "മലയാള ശബ്ദം", self.tts_model, 15, client=self.speech_http,
            json={"text": text, "model": self.tts_model, "language_code": "ml-IN",
                  "speaker": self.voice, "pace": self.pace, "output_audio_codec": "mp3"},
        )
        try:
            clip = base64.b64decode(data["audios"][0], validate=True)
            if not clip:
                raise ValueError("Empty audio")
        except (KeyError, IndexError, TypeError, ValueError):
            raise SarvamError("മലയാള ശബ്ദം ലഭിച്ചില്ല. വീണ്ടും കേൾക്കുക അമർത്തുക.") from None
        self.speech_cache[text] = clip
        if len(self.speech_cache) > 24:
            self.speech_cache.popitem(last=False)
        return clip, duration, False
