"""Offline tests for the Android phone client endpoints."""

import base64
import json
import os
import unittest
from contextlib import ExitStack, chdir
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

try:
    from starlette.testclient import TestClient
except ImportError:
    TestClient = None

import app as server
from sarvam_client import SarvamError
from task_engine import Answer, Locate, TaskSession
from test_task_engine import snapshot, step, verdict, washer

TEST_TOKEN = "test-phone-token-0123456789abcdef"
CLIP = b"ID3phone-clip"


def jpeg_bytes(size=(640, 480), color="white"):
    image = Image.new("RGB", size, color)
    out = BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue()


def png_bytes():
    out = BytesIO()
    Image.new("RGB", (8, 8)).save(out, "PNG")
    return out.getvalue()


def b64(data):
    return base64.b64encode(data).decode("ascii")


def photo_payload(**changes):
    return {"image_b64": b64(jpeg_bytes()), "audio_b64": None, "audio_type": None} | changes


@unittest.skipUnless(TestClient is not None, "httpx is required for the phone API tests")
class PhoneTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(chdir(self.stack.enter_context(TemporaryDirectory())))
        self.stack.enter_context(patch.dict(os.environ, {"CAMERA_URL": "off", "PHONE_TOKEN": TEST_TOKEN}))
        self.client = TestClient(server.app, base_url="http://localhost:8000",
                                 client=("127.0.0.1", 4321))
        self.stack.enter_context(self.client)
        self.addCleanup(self.stack.close)
        self.rt = server.app.state.runtime
        server.phone_debug_records.clear()
        for name in ("guide", "verify", "transcribe", "speak", "answer", "locate"):
            setattr(self, name, self.stack.enter_context(patch.object(self.rt.ai, name)))
        self.speak.side_effect = lambda text: (CLIP, 1.0, False)
        self.guide.side_effect = lambda *args: (washer(message_ml="ഫോട്ടോ ലഭിച്ചു."), 1.0)
        self.verify.side_effect = lambda *args: (verdict(), 1.0)
        self.transcribe.side_effect = lambda audio, content_type: ("ചെയ്തു", 1.0)
        self.answer.side_effect = lambda *args: (Answer(is_device_task=True, wants_location=False, answer_ml="ok"), 1.0)

    def tearDown(self):
        server.phone_debug_records.clear()

    def ask(self, payload, token=TEST_TOKEN):
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        return self.client.post("/api/phone/ask", json=payload, headers=headers)

    def ping(self, token=TEST_TOKEN):
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        return self.client.get("/api/phone/ping", headers=headers)

    def begin_task(self):
        session = TaskSession()
        before = snapshot(1)
        session.accept_guidance(washer("first"), before, "inspect")
        answer = washer(intent="new_task", task_understood="wash cotton clothes",
                        current_step=step())
        session.accept_guidance(answer, before, "query", "wash cotton clothes")
        self.rt.session = session
        self.assertIsNotNone(session.step)
        return before

    def test_location_question_uses_locate_route_and_remembers_target(self):
        grid = Locate(device_name="remote", rows=[["POWER", "MUTE"], ["EXIT", "OK"]], target_label="EXIT",
                      target_row=2, target_col=1, finger_visible=False, finger_row=None, finger_col=None,
                      upright=True, readable=True, needs_new_image=False, message_ml="x")
        self.answer.side_effect = lambda *args: (Answer(
            is_device_task=False, wants_location=True, answer_ml="ശരി"), 1.0)
        self.locate.side_effect = lambda *args: (grid, 1.0)
        self.transcribe.side_effect = lambda audio, content_type: ("Where is the exit button?", 1.0)
        audio = {"audio_b64": b64(b"RIFF" + bytes(60)), "audio_type": "audio/wav"}
        data = self.ask(photo_payload(**audio)).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["debug"]["route"], "locate")
        self.assertIn("'EXIT'", data["reply"])
        self.assertIn("മുകളിൽ നിന്ന് രണ്ടാമത്തെ വരിയിൽ", data["reply"])
        self.guide.assert_not_called()
        self.assertEqual(self.locate.call_args.args[2], None)
        self.assertEqual(self.rt.locate_target, "EXIT")
        self.ask(photo_payload(**audio))
        self.assertEqual(self.locate.call_args.args[2], "EXIT")
        record = server.phone_debug_records[-1]
        self.assertEqual(record["meta"]["locate"]["rows"], [["POWER", "MUTE"], ["EXIT", "OK"]])

    def test_turn_direction_and_silent_retake_use_current_and_reference_photos(self):
        self.begin_task()  # A previous task must not supply the new task's reference.
        self.answer.side_effect = lambda *args: (Answer(
            is_device_task=False, wants_location=True, answer_ml="ശരി"), 1.0)
        self.transcribe.side_effect = lambda *args: ("എങ്ങോട്ട് തിരിച്ചാലാണ് വേഗം കുറയുക?", 1.0)
        self.guide.side_effect = [
            (washer("different", intent="new_task", task_understood="കോട്ടൺ തിരഞ്ഞെടുക്കുക",
                    needs_new_image=True, current_step=None), 1.0),
            (washer("same", current_step=step()), 1.0),
        ]
        first = self.ask(photo_payload(audio_b64=b64(b"RIFF" + bytes(60)), audio_type="audio/wav")).json()
        self.assertEqual(first["debug"]["route"], "guide")
        self.locate.assert_not_called()
        reference = self.guide.call_args.args[0]
        second = self.ask(photo_payload(image_b64=b64(jpeg_bytes(color="blue")))).json()
        current, context, mode, _, previous = self.guide.call_args.args
        self.assertEqual(previous, reference)
        self.assertNotEqual(current, previous)
        self.assertEqual(mode, "resume")
        self.assertEqual(context["task"], "കോട്ടൺ തിരഞ്ഞെടുക്കുക")
        self.assertIn(step().instruction_ml, second["reply"])
        self.assertFalse(self.rt.session.needs_new_image)

    def test_stream_returns_ordered_ndjson_matching_reply(self):
        reply = "ഇത് ആദ്യത്തെ വാചകം ആണ്. ഇത് രണ്ടാമത്തെ വാചകം ആണ്. ഇത് മൂന്നാമത്തെ വാചകം ആണ്. ഇത് നാലാമത്തെ വാചകം ആണ്."
        self.answer.side_effect = lambda *args: (Answer(
            is_device_task=False, wants_location=False, answer_ml=reply), 1.0)
        self.speak.side_effect = lambda text: (b"ID3" + text.encode(), 1.0, False)
        response = self.ask(photo_payload(stream=True))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/x-ndjson"))
        lines = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([line["type"] for line in lines][0], "meta")
        self.assertEqual(lines[-1]["type"], "done")
        meta = lines[0]
        self.assertEqual(meta["reply"], reply)
        audio = [line for line in lines if line["type"] == "audio"]
        self.assertEqual([line["index"] for line in audio], list(range(meta["parts"])))
        self.assertLessEqual(meta["parts"], 3)
        spoken = " ".join(base64.b64decode(line["audio_b64"])[3:].decode() for line in audio)
        self.assertEqual(spoken, reply)
        self.assertIsNotNone(lines[-1]["debug"]["ttfa_ms"])
        record = server.phone_debug_records[-1]
        self.assertEqual(record["meta"]["parts"], meta["parts"])
        self.assertTrue((record["dir"] / "meta.json").exists())

    def test_stream_failed_part_is_null_and_stream_continues(self):
        reply = "ഇത് ആദ്യത്തെ വാചകം ആണ്. ഇത് രണ്ടാമത്തെ വാചകം ആണ്."
        self.answer.side_effect = lambda *args: (Answer(
            is_device_task=False, wants_location=False, answer_ml=reply), 1.0)

        def speak(text):
            if "ആദ്യത്തെ" in text:
                raise SarvamError("tts down")
            return b"ID3ok", 1.0, False
        self.speak.side_effect = speak
        lines = [json.loads(line) for line in self.ask(photo_payload(stream=True)).text.splitlines()]
        audio = [line for line in lines if line["type"] == "audio"]
        self.assertIsNone(audio[0]["audio_b64"])
        self.assertIsNotNone(audio[1]["audio_b64"])
        self.assertEqual(lines[-1]["type"], "done")

    def test_stream_errors_before_reply_stay_plain_json(self):
        self.assertEqual(self.ask(photo_payload(stream=True), token="wrong").status_code, 401)
        bad = self.ask({"image_b64": "!!!", "stream": True})
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(bad.json()["ok"])
        self.guide.side_effect = SarvamError("down")
        self.answer.side_effect = SarvamError("down")
        failed = self.ask(photo_payload(stream=True))
        self.assertEqual(failed.status_code, 503)
        self.assertFalse(failed.json()["ok"])

    def test_non_stream_response_shape_is_unchanged(self):
        data = self.ask(photo_payload()).json()
        self.assertEqual(set(data), {"ok", "reply", "transcript", "audio_b64", "audio_type", "debug"})


    def test_unauthorized_requests_get_401(self):
        for response in (self.ask(photo_payload(), token=None), self.ask(photo_payload(), token="wrong"),
                         self.ping(token=None), self.ping(token="wrong")):
            self.assertEqual(response.status_code, 401)
            data = response.json()
            self.assertFalse(data["ok"])
            self.assertTrue(data["reply"])
            self.assertIsNone(data["audio_b64"])

    def test_ping_reports_ai_readiness(self):
        response = self.ping()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "ai_ready": bool(self.rt.ai.key)})

    def test_photo_only_uses_phone_jpeg_without_camera(self):
        with patch.object(self.rt.camera, "snapshot") as camera_shot:
            response = self.ask(photo_payload())
        camera_shot.assert_not_called()
        self.transcribe.assert_not_called()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["reply"], "ഫോട്ടോ ലഭിച്ചു.")
        self.assertIsNone(data["transcript"])
        self.assertEqual(data["audio_type"], "audio/mpeg")
        self.assertEqual(base64.b64decode(data["audio_b64"]), CLIP)
        self.guide.assert_called_once()
        image_arg = self.guide.call_args.args[0]
        self.assertTrue(image_arg.startswith(b"\xff\xd8"))
        self.assertGreaterEqual(self.rt.session.latest.number, server.PHONE_SHOT_BASE)

    def test_photo_with_audio_transcribes_and_verifies_with_shot(self):
        before = self.begin_task()
        wav = b"RIFFfake-wav-bytes"
        with patch.object(self.rt.camera, "snapshot") as camera_shot:
            response = self.ask({"image_b64": b64(jpeg_bytes(color="blue")),
                                 "audio_b64": b64(wav), "audio_type": "audio/wav"})
        camera_shot.assert_not_called()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["transcript"], "ചെയ്തു")
        self.transcribe.assert_called_once()
        self.assertEqual(self.transcribe.call_args.args, (wav, "audio/wav"))
        self.verify.assert_called_once()
        self.assertEqual(self.verify.call_args.args[0], before.jpeg)
        self.assertEqual(self.verify.call_args.args[1], self.rt.session.latest.jpeg)
        self.assertGreaterEqual(self.rt.session.latest.number, server.PHONE_SHOT_BASE)
        self.assertEqual(self.rt.session.verification, "VERIFIED")
        self.assertTrue(self.rt.session.finished)
        self.assertTrue(data["reply"])

    def test_bad_image_inputs_get_400(self):
        for payload in ({}, photo_payload(image_b64=None), photo_payload(image_b64="%%%"),
                        photo_payload(image_b64=b64(b"not an image")),
                        photo_payload(image_b64=b64(png_bytes())),
                        photo_payload(image_b64=b64(b""))):
            with self.subTest(payload=payload):
                response = self.ask(payload)
                self.assertEqual(response.status_code, 400)
                data = response.json()
                self.assertFalse(data["ok"])
                self.assertTrue(data["reply"])
                self.assertEqual(base64.b64decode(data["audio_b64"]), CLIP)

    def test_bad_audio_inputs_get_400(self):
        for payload in (
            photo_payload(audio_b64=b64(b"wav"), audio_type=None),
            photo_payload(audio_b64=b64(b"wav"), audio_type="audio/aac"),
            photo_payload(audio_b64=b64(b"wav"), audio_type=123),
            photo_payload(audio_b64="%%%", audio_type="audio/wav"),
            photo_payload(audio_b64="", audio_type="audio/wav"),
            photo_payload(audio_b64=b64(b""), audio_type="audio/wav"),
            photo_payload(audio_b64=123, audio_type="audio/wav"),
        ):
            with self.subTest(payload=payload):
                response = self.ask(payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()["ok"])

    def test_oversize_inputs_get_413(self):
        big_image = b"\xff\xd8\xff" + b"\x00" * 6_500_000
        response = self.ask(photo_payload(image_b64=b64(big_image)))
        self.assertEqual(response.status_code, 413)
        self.assertFalse(response.json()["ok"])
        big_audio = photo_payload(audio_b64=b64(b"\x00" * 3_500_000), audio_type="audio/wav")
        self.assertEqual(self.ask(big_audio).status_code, 413)
        huge = self.client.post("/api/phone/ask", content=b'{"image_b64":"' + b"A" * 11_000_000 + b'"}',
                                headers={"Authorization": f"Bearer {TEST_TOKEN}",
                                         "Content-Type": "application/json"})
        self.assertEqual(huge.status_code, 413)

    def test_busy_returns_409(self):
        self.rt.busy = True
        response = self.ask(photo_payload())
        self.assertEqual(response.status_code, 409)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertEqual(base64.b64decode(data["audio_b64"]), CLIP)
        self.guide.assert_not_called()

    def test_tts_failure_still_returns_reply(self):
        self.speak.side_effect = SarvamError("Sarvam down")
        response = self.ask(photo_payload())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["reply"], "ഫോട്ടോ ലഭിച്ചു.")
        self.assertIsNone(data["audio_b64"])

    def test_general_route_photo_only_skips_guide(self):
        self.answer.side_effect = lambda *args: (
            Answer(is_device_task=False, wants_location=False, answer_ml="നിങ്ങളുടെ മുന്നിൽ ഒരു tv cabinet ഉണ്ട്."), 1.0)
        response = self.ask(photo_payload())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["reply"], "നിങ്ങളുടെ മുന്നിൽ ഒരു tv cabinet ഉണ്ട്.")
        self.assertIsNone(data["transcript"])
        self.guide.assert_not_called()
        self.answer.assert_called_once()
        image_arg, question, history, task_ctx = self.answer.call_args.args
        self.assertTrue(image_arg.startswith(b"\xff\xd8"))
        self.assertEqual(question, "")
        self.assertEqual(history, [])
        self.assertIsNone(task_ctx)
        self.assertEqual(data["debug"]["route"], "general")
        self.assertEqual(data["debug"]["action"], "capture")
        record = server.phone_debug_records[-1]
        self.assertTrue((record["dir"] / "image.jpg").is_file())
        self.assertTrue((record["dir"] / "meta.json").is_file())
        meta = json.loads((record["dir"] / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["route"], "general")
        self.assertEqual(meta["reply"], data["reply"])
        self.assertTrue(meta["image_bytes"] > 0)
        self.assertEqual(len(self.rt.phone_history), 1)

    def test_general_route_uses_transcript_and_history(self):
        def transcribe(audio, content_type):
            self.rt.ai.last_language = "en-IN"
            return ("what is in front of me", 1.0)
        self.transcribe.side_effect = transcribe
        self.answer.side_effect = lambda *args: (
            Answer(is_device_task=False, wants_location=False, answer_ml="മുന്നിൽ ഒരു comment."), 1.0)
        wav = b"RIFF" + b"\x00" * 40
        response = self.ask({"image_b64": b64(jpeg_bytes()), "audio_b64": b64(wav),
                             "audio_type": "audio/wav"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["transcript"], "what is in front of me")
        self.assertEqual(data["debug"]["route"], "general")
        self.assertEqual(data["debug"]["transcript_language"], "en-IN")
        _, question, _, _ = self.answer.call_args.args
        self.assertEqual(question, "what is in front of me")
        record = server.phone_debug_records[-1]
        meta = json.loads((record["dir"] / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["stt_language"], "en-IN")
        self.assertEqual(meta["route"], "general")
        self.assertTrue((record["dir"] / "audio.wav").is_file())

    def test_device_task_falls_through_to_guide(self):
        self.answer.side_effect = lambda *args: (Answer(is_device_task=True, wants_location=False, answer_ml="ok"), 1.0)
        response = self.ask(photo_payload())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["reply"], "ഫോട്ടോ ലഭിച്ചു.")
        self.guide.assert_called_once()
        self.assertEqual(data["debug"]["route"], "guide")
        self.assertEqual(len(self.rt.phone_history), 0)

    def test_active_task_bypasses_answer(self):
        self.begin_task()
        response = self.ask(photo_payload())
        self.assertEqual(response.status_code, 200)
        self.answer.assert_not_called()
        self.guide.assert_called()

    def test_active_task_unrelated_question_uses_general(self):
        self.begin_task()
        self.transcribe.side_effect = lambda a, c: ("what time is it", 1.0)
        self.answer.side_effect = lambda *args: (
            Answer(is_device_task=False, wants_location=False, answer_ml="മുന്നിൽ ഒരു cabinet ഉണ്ട്."), 1.0)
        response = self.ask({"image_b64": b64(jpeg_bytes()),
                             "audio_b64": b64(b"wav"), "audio_type": "audio/wav"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["transcript"], "what time is it")
        self.assertEqual(data["debug"]["route"], "general")
        self.assertEqual(data["reply"], "മുന്നിൽ ഒരു cabinet ഉണ്ട്.")
        _, _, _, task_ctx = self.answer.call_args.args
        self.assertIsNotNone(task_ctx)
        self.assertIn("active_task", task_ctx)

    def test_active_task_related_question_uses_guide(self):
        self.begin_task()
        self.transcribe.side_effect = lambda a, c: ("how do I continue", 1.0)
        self.answer.side_effect = lambda *args: (Answer(is_device_task=True, wants_location=False, answer_ml="ok"), 1.0)
        response = self.ask({"image_b64": b64(jpeg_bytes()),
                             "audio_b64": b64(b"wav"), "audio_type": "audio/wav"})
        data = response.json()
        self.assertEqual(data["debug"]["route"], "guide")
        self.guide.assert_called()

    def test_intent_english_and_malayalam(self):
        cases = {
            "Repeat!": "repeat", "say it again?": "repeat", "DONE!!": "verify",
            "I did it.": "verify", "Next step": "next", "what now?": "next",
            "Where is the button?": "locate", "which button": "locate",
            "simpler please": "query", "explain in detail": "explain",
            "tell me more": "explain", "be quiet": "stop", "shut up!": "stop",
            "start over": "reset", "clear task": "reset",
            "skip verification": "continue", "continue without checking": "continue",
            "വീണ്ടും പറയൂ": "repeat", "ഒന്നൂടെ പറയൂ": "repeat",
            "ചെയ്തു": "verify", "അടുത്തത്": "next",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(server.intent(text), expected)
        self.assertEqual(server.intent("what is this"), "query")
        self.assertEqual(server.intent("take a photo"), "query")

    def test_debug_endpoints_and_id_validation(self):
        self.ask(photo_payload())
        items = self.client.get("/api/debug/items")
        self.assertEqual(items.status_code, 200)
        item = items.json()[0]
        self.assertEqual(item["route"], "guide")
        self.assertIsNone(item.get("audio_file"))
        self.assertTrue(item["image_bytes"] > 0)
        image = self.client.get(f"/api/debug/{item['id']}/image")
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.content.startswith(b"\xff\xd8"))
        page = self.client.get("/debug")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Phone activity", page.text)
        self.assertEqual(self.client.get("/api/debug/20200101-000000-000/image").status_code, 404)
        self.assertEqual(self.client.get("/api/debug/not-an-id/image").status_code, 404)
        self.assertEqual(self.client.get("/api/debug/20200101-000000-000/audio").status_code, 404)
        with TestClient(server.app, base_url="http://localhost:8000",
                        client=("10.9.9.9", 1234)) as remote:
            self.assertEqual(remote.get("/api/debug/items").status_code, 403)
            self.assertEqual(remote.get("/debug").status_code, 403)

    def test_pair_page_loopback_only(self):
        response = self.client.get("/pair")
        self.assertEqual(response.status_code, 200)
        self.assertIn("<svg", response.text)
        self.assertIn("thirdeye://pair", response.text)
        self.assertIn("do not share the screen", response.text)
        with TestClient(server.app, base_url="http://localhost:8000",
                        client=("10.9.9.9", 1234)) as remote:
            self.assertEqual(remote.get("/pair").status_code, 403)

    def test_pair_page_without_token(self):
        with patch.dict(os.environ, {"PHONE_TOKEN": ""}):
            response = self.client.get("/pair")
            self.assertEqual(response.status_code, 200)
            self.assertIn("PHONE_TOKEN is not set", response.text)
            self.assertNotIn("<svg", response.text)

    def test_debug_disabled_returns_404(self):
        with patch.dict(os.environ, {"PHONE_DEBUG": "0"}):
            self.assertEqual(self.client.get("/debug").status_code, 404)
            self.assertEqual(self.client.get("/api/debug/items").status_code, 404)
            response = self.ask(photo_payload())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["debug"], {})
            self.assertEqual(len(server.phone_debug_records), 0)

    def test_host_binding_requires_token_for_public_hosts(self):
        ok = server.host_binding_ok
        self.assertTrue(ok("127.0.0.1", ""))
        self.assertTrue(ok("localhost", ""))
        self.assertFalse(ok("0.0.0.0", ""))
        self.assertFalse(ok("0.0.0.0", "short"))
        self.assertFalse(ok("192.168.1.10", "x" * 23))
        self.assertTrue(ok("0.0.0.0", "x" * 24))
        self.assertTrue(ok("192.168.1.10", "y" * 43))


@unittest.skipUnless(TestClient is not None, "httpx is required for the phone API tests")
class PhoneBindingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(chdir(self.stack.enter_context(TemporaryDirectory())))
        self.stack.enter_context(patch.dict(os.environ, {"CAMERA_URL": "off", "PHONE_TOKEN": TEST_TOKEN}))
        self.client = TestClient(server.app, base_url="http://localhost:8000",
                                 client=("127.0.0.1", 4321))
        self.stack.enter_context(self.client)
        self.addCleanup(self.stack.close)

    def test_non_phone_paths_stay_on_loopback(self):
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        with TestClient(server.app, base_url="http://localhost:8000",
                        client=("10.9.9.9", 1234)) as remote:
            self.assertEqual(remote.get("/api/status").status_code, 403)
            ping = remote.get("/api/phone/ping",
                              headers={"Authorization": f"Bearer {TEST_TOKEN}"})
            self.assertEqual(ping.status_code, 200)
            self.assertTrue(ping.json()["ok"])
        with TestClient(server.app, base_url="http://printer.local:8000",
                        client=("127.0.0.1", 1234)) as bad_host:
            self.assertEqual(bad_host.get("/api/status").status_code, 403)


if __name__ == "__main__":
    unittest.main()
