"""Offline tests for the decision rules, image handling and Sarvam HTTP contracts."""

import base64
import json
import os
import threading
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

import requests
from PIL import Image, ImageDraw
from pydantic import ValidationError

from camera import Camera, CameraError, Snapshot, jpeg_encode, validate_url
from sarvam_client import SarvamClient, SarvamError
from settings import load_environment
from task_engine import Answer, Control, Guidance, ProposedStep, TaskSession, Verification


def panel_jpeg(selected):
    image = Image.new("RGB", (900, 450), "white")
    drawing = ImageDraw.Draw(image)
    drawing.text((30, 30), "WASHING MACHINE", fill="black")
    drawing.ellipse((280, 120, 520, 360), outline="black", width=6)
    drawing.text((345, 90), "Cotton", fill="black")
    drawing.text((520, 225), "Rinse", fill="black")
    endpoint = (400, 127) if selected else (518, 240)
    drawing.line(((400, 240), endpoint), fill="black", width=12)
    return jpeg_encode(image, 1280, 82)


def snapshot(number, selected=False):
    return Snapshot(number, panel_jpeg(selected), 10.0, 0.5, 2.0)


def washer(relation="same", **changes):
    fields = dict(
        device_name="വാഷിംഗ് മെഷീൻ", device_relation=relation, category="washer",
        visible_controls=[Control(id="dial", label="Cotton", symbol=None, kind="dial",
                                  position_ml="പാനലിന്റെ ഇടതുവശത്തെ വലിയ നോബ്.",
                                  appearance_ml="വൃത്താകൃതിയിലുള്ള നോബ്.",
                                  possible_function_ml=None, confidence="clear")],
        task_understood=None, intent="describe", current_step=None,
        message_ml="വാഷിംഗ് മെഷീനിന്റെ പാനലിൽ ഒരു നോബ് കാണുന്നു.",
        needs_new_image=False, uncertainty_reason=None, safety_note=None,
    )
    return Guidance(**(fields | changes))


def step(**changes):
    return ProposedStep(**(dict(
        action_ml="കോട്ടൺ തിരഞ്ഞെടുക്കുക", control_id="dial",
        instruction_ml="നോബിന്റെ മുകൾഭാഗം ഇടത്തേക്ക് വരത്തക്കവിധം കാൽ വട്ടം തിരിക്കുക.",
        expected_result="സൂചിക കോട്ടൺ അടയാളത്തിനുനേരെ കാണുന്നു.",
        visually_verifiable=True, final_candidate=True,
    ) | changes))


def verdict(**changes):
    return Verification(**(dict(
        verification_state="VERIFIED", same_device=True, same_control=True,
        before_evidence="സൂചിക റിൻസ് അടയാളത്തിനുനേരെയാണ്.",
        after_evidence="സൂചിക കോട്ടൺ അടയാളത്തിനുനേരെയാണ്.",
        changed=True, expected_observed=True,
    ) | changes))


class SessionTests(unittest.TestCase):
    goal = "എനിക്ക് കോട്ടൺ വസ്ത്രങ്ങൾ കഴുകണം."

    def setUp(self):
        self.session = TaskSession()
        self.before = snapshot(1)
        self.after = snapshot(2, True)

    def begin(self, **changes):
        answer = washer(intent="new_task", task_understood=self.goal,
                        current_step=step(**changes))
        self.session.accept_guidance(washer("first"), self.before, "inspect")
        return self.session.accept_guidance(answer, self.before, "query", self.goal)

    def test_washer_passes_only_after_expected_visible_change(self):
        self.assertIn("ഇടതുവശത്തെ", self.begin())
        self.assertEqual(self.session.state(True), "AWAITING_ACTION")
        self.assertIs(self.session.baseline, self.before)
        self.session.apply_verification(verdict(), self.after)
        self.assertEqual(self.session.verification, "VERIFIED")
        self.assertEqual(self.session.state(True), "TASK_COMPLETED")
        self.assertFalse(self.session.public()["current_instruction"])

    def test_spoken_step_starts_at_the_physical_landmark(self):
        spoken = self.begin()
        self.assertEqual(spoken, f"{self.session.step_control.position_ml} {step().instruction_ml}")
        self.assertEqual(self.session.response, spoken)

    def test_dial_distance_uses_pointer_and_target_positions(self):
        for start, target, direction, amount, side in (
                (3, 12, "counterclockwise", "കാൽ വട്ടം", "ഇടത്തേക്ക്"),
                (12, 3, "clockwise", "കാൽ വട്ടം", "വലത്തേക്ക്"),
                (12, 3, "counterclockwise", "അല്പം", "ഇടത്തേക്ക്"),
                (None, 3, "clockwise", "അല്പം", "വലത്തേക്ക്"),
                (12, None, "clockwise", "അല്പം", "വലത്തേക്ക്")):
            with self.subTest(start=start, target=target, direction=direction):
                self.session = TaskSession()
                spoken = self.begin(instruction_ml="അഞ്ചിൽ നിന്ന് ഒന്നിലേക്ക് തിരിക്കുക.",
                                    dial_turn=dict(direction=direction, pointer_clock=start,
                                                   target_clock=target))
                self.assertNotIn("ഒന്നിലേക്ക്", spoken)
                self.assertIn(amount, spoken)
                self.assertIn(side, spoken)
                self.assertIn("ഫോട്ടോയിൽ", spoken)
                self.assertIn(self.session.public()["current_instruction"], spoken)
                self.assertIn(self.session.context()["pending_step"]["action_ml"], spoken)

    def test_unchanged_control_is_not_verified(self):
        self.begin()
        self.session.apply_verification(verdict(
            verification_state="NOT_VERIFIED", changed=False, expected_observed=False,
            after_evidence="സൂചിക ഇപ്പോഴും റിൻസ് അടയാളത്തിനുനേരെയാണ്.",
        ), self.after)
        self.assertEqual(self.session.verification, "NOT_VERIFIED")
        self.assertFalse(self.session.finished)
        self.assertFalse(self.session.public()["can_continue"])

    def test_glare_or_inconsistent_evidence_requires_new_image(self):
        for changes in (
            {"after_evidence": None},
            {"before_evidence": "സൂചിക കോട്ടൺ അടയാളത്തിനുനേരെയാണ്."},
            {"changed": False},
            {"same_control": False},
            {"expected_observed": False},
        ):
            with self.subTest(changes=changes):
                self.begin()
                self.session.apply_verification(verdict(**changes), self.after)
                self.assertEqual(self.session.verification, "UNCERTAIN")
                self.assertTrue(self.session.public()["needs_new_image"])
                self.assertFalse(self.session.finished)

    def test_blurry_label_cannot_authorize_a_step(self):
        self.session.accept_guidance(washer("first"), self.before, "inspect")
        control = washer().visible_controls[0].model_copy(
            update={"label": None, "symbol": None, "confidence": "unclear"})
        reply = washer(intent="new_task", task_understood=self.goal, current_step=step(),
                       visible_controls=[control])
        message = self.session.accept_guidance(reply, self.before, "query", self.goal)
        self.assertIn("വ്യക്തമായി", message)
        self.assertIsNone(self.session.step)
        self.assertTrue(self.session.needs_new_image)

    def test_clearer_panel_can_resume_a_task_without_a_prior_device_identity(self):
        cloudy = washer("first", device_name=None, category="unclear", visible_controls=[],
                        intent="new_task", task_understood=self.goal, needs_new_image=True)
        self.session.accept_guidance(cloudy, self.before, "query", self.goal)
        self.session.accept_guidance(washer("first", current_step=step()), self.after, "resume")
        self.assertEqual(self.session.number, 1)
        self.assertIs(self.session.baseline, self.after)

    def test_readable_retake_with_no_step_reports_observation(self):
        self.session.accept_guidance(washer("first", intent="new_task", task_understood=self.goal,
                                             needs_new_image=True), self.before, "query", self.goal)
        reply = washer(observation_ml="നോബിന് ചുറ്റും ഒന്നു മുതൽ നാലു വരെ അക്കങ്ങൾ കാണുന്നു.",
                       message_ml="നോബ് തിരിക്കുക.")
        self.assertEqual(self.session.accept_guidance(reply, self.after, "resume"), reply.observation_ml)
        self.assertIsNone(self.session.step)
        self.assertFalse(self.session.needs_new_image)

    def test_retake_with_unconfirmed_identity_explains_remaining_uncertainty(self):
        first = washer("first", intent="new_task", task_understood=self.goal, needs_new_image=True)
        self.session.accept_guidance(first, self.before, "query", self.goal)
        reason = "സൂചിക ഏത് അടയാളത്തിലാണ് എന്ന് തിരിച്ചറിയാനായില്ല."
        retry = washer("unclear", needs_new_image=True, uncertainty_reason=reason, current_step=step())
        response = self.session.accept_guidance(retry, self.after, "resume")
        self.assertIn(reason, response)
        self.assertIn("Cotton", response)
        self.assertNotIn(step().instruction_ml, response)
        self.assertIsNone(self.session.step)
        self.assertIs(self.session.latest, self.before)

    def test_followup_preserves_step_and_baseline(self):
        self.begin()
        previous = self.session.step
        question = "ഏത് ബട്ടണാണ്?"
        reply = washer(intent="answer", message_ml="പാനലിന്റെ ഇടതുവശത്തെ വൃത്താകൃതിയിലുള്ള നോബ്.")
        response = self.session.accept_guidance(reply, self.before, "query", question)
        self.assertIn("ഇടതുവശത്തെ", response)
        self.assertIs(self.session.step, previous)
        self.assertIs(self.session.baseline, self.before)
        self.assertEqual(self.session.context()["recent"][-1]["question"], question)

    def test_location_uses_confirmed_landmarks_and_shape_without_advancing(self):
        self.begin()
        self.assertIn("ഇടതുവശത്തെ", self.session.control_location())
        self.assertIn("വൃത്താകൃതിയിലുള്ള", self.session.control_location())
        self.assertEqual(self.session.number, 1)
        self.assertIs(self.session.baseline, self.before)
        self.session.apply_verification(verdict(same_device=False), self.after)
        self.assertIsNone(self.session.control_location())

    def test_remote_symbol_has_location_and_requires_explicit_continue(self):
        control = Control(id="vol", label="VOL", symbol="+", kind="button", confidence="clear",
                          position_ml="പവർ ബട്ടണിന് താഴെ വലതുവശത്ത്.", appearance_ml="നീളമുള്ള ബട്ടൺ.",
                          possible_function_ml=None)
        proposal = step(control_id="vol", visually_verifiable=False, expected_result=None,
                        instruction_ml="വോളിയം കൂട്ടാൻ പ്ലസ് ബട്ടൺ അമർത്തുക.")
        reply = washer("first", device_name="ടിവി റിമോട്ട്", category="remote", visible_controls=[control],
                       intent="new_task", task_understood="വോളിയം കൂട്ടുക", current_step=proposal)
        response = self.session.accept_guidance(reply, self.before, "query", "വോളിയം കൂട്ടണം")
        self.assertIn("പവർ ബട്ടണിന് താഴെ", response)
        self.assertIn("ഫോട്ടോയിൽ പരിശോധിക്കാനാവില്ല", response)
        view = self.session.public()
        self.assertTrue(view["can_continue"])
        self.assertFalse(view["can_verify"])
        self.assertFalse(view["finished"])
        self.session.without_visual_verification()
        self.assertEqual(self.session.verification, "UNAVAILABLE")
        self.assertTrue(self.session.public()["unverified_steps"])

    def test_unlabeled_plus_is_insufficient_for_volume_action(self):
        control = washer().visible_controls[0].model_copy(update={"label": None, "symbol": "+"})
        reply = washer("first", category="remote", visible_controls=[control], intent="new_task",
                       task_understood="വോളിയം കൂട്ടുക", current_step=step())
        self.session.accept_guidance(reply, self.before, "query", "വോളിയം കൂട്ടുക")
        self.assertIsNone(self.session.step)
        self.assertTrue(self.session.needs_new_image)

    def test_different_camera_view_resets_previous_device_task_and_baseline(self):
        self.begin()
        self.session.accept_guidance(washer("different", device_name="റിമോട്ട്", category="remote",
                                            visible_controls=[]), self.after, "inspect")
        self.assertEqual(self.session.scene.device_name, "റിമോട്ട്")
        self.assertIsNone(self.session.baseline)
        self.assertIsNone(self.session.task)

    def test_uncertain_device_hides_action(self):
        self.begin()
        self.session.apply_verification(verdict(same_device=False), self.after)
        self.assertFalse(self.session.public()["current_instruction"])
        self.assertFalse(self.session.public()["can_continue"])
        self.assertTrue(self.session.public()["can_verify"])

    def test_hazard_clears_pending_action(self):
        self.begin()
        self.session.accept_guidance(washer("different", category="hazardous", current_step=step()),
                                     self.after, "inspect")
        self.assertIsNone(self.session.step)
        self.assertIn("സുരക്ഷിതമായി", self.session.response)

    def test_only_one_step_and_completion_requires_last_step(self):
        self.begin(final_candidate=False)
        self.session.apply_verification(verdict(), self.after)
        self.assertEqual(self.session.state(True), "STEP_COMPLETED")
        self.session.accept_guidance(washer(current_step=step()), self.after, "next")
        self.assertEqual(self.session.number, 2)
        self.assertFalse(self.session.finished)
        self.assertIs(self.session.baseline, self.after)

    def test_schema_rejects_invalid_types_or_missing_fields(self):
        with self.assertRaises(ValidationError):
            Guidance.model_validate({"device_name": "washer"})
        with self.assertRaises(ValidationError):
            Verification.model_validate(verdict().model_dump() | {"same_device": "true"})


class SettingsTests(unittest.TestCase):
    def test_env_loads_quotes_comments_and_uses_existing_environment_first(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"VISION_MODEL": "existing"}, clear=True):
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "SARVAM_API_KEY=older\n"
                "export SARVAM_API_KEY = 'sample#key' # comment\n"
                "CAMERA_URL=http://camera:8080/video # comment\n"
                "VISION_MODEL=gemma4\n",
                encoding="utf-8",
            )
            load_environment(env_file)
            client = SarvamClient()
            self.addCleanup(client.close)
            self.assertEqual(client.key, "sample#key")
            self.assertEqual(client.vision_model, "existing")
            self.assertEqual(os.getenv("CAMERA_URL"), "http://camera:8080/video")


class CameraTests(unittest.TestCase):
    def test_ip_webcam_home_url_uses_video_endpoint(self):
        for url in ("http://camera:8080", "http://camera:8080/"):
            with self.subTest(url=url):
                self.assertEqual(Camera(url).url, "http://camera:8080/video")
        self.assertEqual(validate_url("http://camera:8080/custom"), "http://camera:8080/custom")

    def test_reader_validates_mjpeg_and_reconnects(self):
        camera = Camera()
        release = threading.Event()
        jpeg = panel_jpeg(False)
        packet = b"--part\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"

        class Stream:
            headers = {"Content-Type": "multipart/x-mixed-replace; boundary=part"}
            status_code = 200

            def __init__(self, reconnect=False):
                self.reconnect = reconnect

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def raise_for_status(self):
                pass

            def iter_content(self, _):
                yield b"garbage\xff"
                yield b"\xd8bad\xff\xd9" + packet[:40]
                yield packet[40:]
                if self.reconnect:
                    release.wait(3)
                else:
                    release.wait(.25)
                    raise requests.ConnectionError("simulated disconnected phone")

        with patch("requests.Session.get", side_effect=[Stream(), Stream(True)]) as connect:
            camera.start()
            try:
                with camera.changed:
                    self.assertTrue(camera.changed.wait_for(lambda: camera.frame is not None, 2))
                first = camera.snapshot(640, 82, 2, .5, .5)
                self.assertEqual(Image.open(BytesIO(first.jpeg)).size, (450, 225))
                camera_status = camera.status()
                self.assertEqual(camera_status["state"], "ready")
                self.assertIsNotNone(camera_status["connect_ms"])
                self.assertLess(first.frame_age_ms, 500)
                with camera.changed:
                    self.assertTrue(camera.changed.wait_for(lambda: camera.state == "reconnecting", 2))
                self.assertIn("Tailscale", camera.status()["detail"])
                with self.assertRaises(CameraError):
                    camera.snapshot(640, 82)
                with camera.changed:
                    self.assertTrue(camera.changed.wait_for(lambda: camera.state == "ready", 3))
                self.assertGreater(camera.snapshot(640, 82).number, first.number)
                self.assertEqual(connect.call_count, 2)
                camera.configure("disconnect")
                self.assertEqual(camera.status()["state"], "disconnected")
            finally:
                release.set()
                camera.close()

    def test_rotation_and_fresh_capture_are_based_on_frame_availability(self):
        camera = Camera(rotation=90)
        camera.configure("connect")
        camera._publish(panel_jpeg(False), camera.generation, monotonic(), monotonic())
        self.assertEqual((camera.status()["width"], camera.status()["height"]), (450, 900))
        newest = threading.Timer(.02, camera._publish,
                                 (panel_jpeg(True), camera.generation, monotonic() + .02, monotonic()))
        newest.start()
        try:
            shot = camera.snapshot(1280, 82, fresh=True)
            self.assertEqual(shot.number, 2)
            self.assertEqual(Image.open(BytesIO(shot.jpeg)).size, (450, 900))
        finally:
            newest.join()

    def test_rejects_non_stream_http_response(self):
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "text/html"
        with patch("requests.Session.get", return_value=response):
            with self.assertRaisesRegex(CameraError, "MJPEG"):
                response._content = b"camera settings page"
                response._content_consumed = True
                Camera()._read(requests.Session(), "http://127.0.0.1:8080/", 0)

    def test_invalid_camera_urls_fail_early(self):
        for url in ("100.109.140.21", "file:///video", "http://user:password@camera/video", "http://cam:abc"):
            with self.subTest(url=url), self.assertRaises(CameraError):
                validate_url(url)


def cloud_reply(data, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(data).encode()
    return response


class SarvamTests(unittest.TestCase):
    def setUp(self):
        with patch.dict("os.environ", {"SARVAM_API_KEY": "test-placeholder"}, clear=True):
            self.ai = SarvamClient()
        self.addCleanup(self.ai.close)

    def test_vision_uses_inline_jpeg_and_structured_payload(self):
        before, after = panel_jpeg(False), panel_jpeg(True)
        answer = cloud_reply({"choices": [{"message": {"content": verdict().model_dump_json()}}]})
        with patch.object(self.ai.task_http, "post", return_value=answer) as post:
            observed, latency = self.ai.verify(before, after, {"expected_result": step().expected_result})
        self.assertEqual(observed.verification_state, "VERIFIED")
        self.assertGreaterEqual(latency, 0)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.sarvam.ai/v2/chat/completions")
        payload = kwargs["json"]
        self.assertEqual(payload["model"], "gemma4")
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        photos = [item["image_url"]["url"] for item in payload["messages"][1]["content"]
                  if item["type"] == "image_url"]
        self.assertEqual([base64.b64decode(item.split(",")[1]) for item in photos], [before, after])

    def test_malformed_vision_response_is_recoverable(self):
        answer = cloud_reply({"choices": [{"message": {"content": "{\"device_name\":\"washer\"}"}}]})
        with patch.object(self.ai.task_http, "post", return_value=answer):
            with self.assertRaisesRegex(SarvamError, "വീണ്ടും"):
                self.ai.guide(panel_jpeg(False), {}, "inspect", "")

    def test_vision_retries_once_then_succeeds(self):
        bad = cloud_reply({"choices": [{"message": {"content": "{\"device_name\":\"washer\"}"}}]})
        good = cloud_reply({"choices": [{"message": {"content": verdict().model_dump_json()}}]})
        with patch.object(self.ai.task_http, "post", side_effect=[bad, good]) as post:
            observed, _ = self.ai.verify(panel_jpeg(False), panel_jpeg(True),
                                         {"expected_result": step().expected_result})
        self.assertEqual(observed.verification_state, "VERIFIED")
        self.assertEqual(post.call_count, 2)

    def test_vision_two_bad_responses_raise(self):
        bad = cloud_reply({"choices": [{"message": {"content": "{\"device_name\":\"washer\"}"}}]})
        with patch.object(self.ai.task_http, "post", return_value=bad) as post:
            with self.assertRaises(SarvamError):
                self.ai.guide(panel_jpeg(False), {}, "inspect", "")
        self.assertEqual(post.call_count, 2)

    def test_vision_retries_on_length_finish(self):
        truncated = cloud_reply({"choices": [{"finish_reason": "length",
            "message": {"content": verdict().model_dump_json()}}]})
        good = cloud_reply({"choices": [{"message": {"content": verdict().model_dump_json()}}]})
        with patch.object(self.ai.task_http, "post", side_effect=[truncated, good]) as post:
            observed, _ = self.ai.verify(panel_jpeg(False), panel_jpeg(True),
                                         {"expected_result": step().expected_result})
        self.assertEqual(observed.verification_state, "VERIFIED")
        self.assertEqual(post.call_count, 2)

    def test_dial_and_followup_routes_send_nonvisual_guidance(self):
        image = panel_jpeg(False)
        question = "How do I turn it from 5 to 1?"
        with patch.object(self.ai, "_vision") as vision:
            self.ai.answer(image, question, [])
            for mode in ("query", "resume", "simplify", "explain"):
                self.ai.guide(image, {}, mode, question)
            self.ai.locate(image, "Which knob?")
        for call in vision.call_args_list:
            prompt = call.args[1]
            self.assertIn("guiding a blind person", prompt)
            self.assertIn("turn from 5 to 1", prompt)
            self.assertIn("Count clicks only when", prompt)

    def test_vision_system_prompt_requests_compact_json(self):
        marker = "single compact line"
        payloads = [verdict().model_dump_json(), washer().model_dump_json(),
                    Answer(is_device_task=False, wants_location=False, answer_ml="ok").model_dump_json()]
        replies = [cloud_reply({"choices": [{"message": {"content": p}}]}) for p in payloads]
        with patch.object(self.ai.task_http, "post", side_effect=replies) as post:
            self.ai.verify(panel_jpeg(False), panel_jpeg(True), {"expected_result": "x"})
            self.assertIn(marker, post.call_args.kwargs["json"]["messages"][0]["content"])
            self.ai.guide(panel_jpeg(False), {}, "inspect", "")
            self.assertIn(marker, post.call_args.kwargs["json"]["messages"][0]["content"])
            self.ai.answer(panel_jpeg(False), "q", [])
            self.assertIn(marker, post.call_args.kwargs["json"]["messages"][0]["content"])

    def test_api_auth_model_and_timeout_errors(self):
        with patch.object(self.ai.task_http, "post", return_value=cloud_reply({"secret": self.ai.key}, 403)):
            with self.assertRaises(SarvamError) as result:
                self.ai.guide(panel_jpeg(False), {}, "inspect", "")
            self.assertIn("HTTP 403", str(result.exception))
            self.assertNotIn(self.ai.key, str(result.exception))
        with patch.object(self.ai.task_http, "post", return_value=cloud_reply({}, 404)) as post:
            with self.assertRaisesRegex(SarvamError, "VISION_MODEL=glm5"):
                self.ai.guide(panel_jpeg(False), {}, "inspect", "")
            post.assert_called_once()
        with patch.object(self.ai.task_http, "post", side_effect=requests.Timeout):
            with self.assertRaisesRegex(SarvamError, "സമയപരിധി"):
                self.ai.guide(panel_jpeg(False), {}, "inspect", "")

    def test_malayalam_voice_input_request(self):
        text = "ഫോട്ടോ എടുക്കൂ."
        with patch.object(self.ai.task_http, "post", return_value=cloud_reply({"transcript": text})) as post:
            self.assertEqual(self.ai.transcribe(b"fixture", "audio/webm")[0], text)
            self.assertEqual(post.call_args.kwargs["data"],
                             {"model": "saaras:v4", "language_code": "ml-IN", "mode": "transcribe"})
            self.assertEqual(post.call_args.kwargs["files"]["file"][0], "command.webm")

    def test_bulbul_synthesizes_malayalam_and_caches_replay(self):
        text = "ക്യാമറ തയ്യാറാണ്."
        clip = b"ID3audio fixture"
        response = cloud_reply({"audios": [base64.b64encode(clip).decode()]})
        with patch.object(self.ai.speech_http, "post", return_value=response) as post:
            self.assertEqual(self.ai.speak(text)[0], clip)
            self.assertEqual(self.ai.speak(text), (clip, 0.0, True))
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://api.sarvam.ai/text-to-speech")
        payload = post.call_args.kwargs["json"]
        self.assertEqual((payload["model"], payload["language_code"], payload["output_audio_codec"]),
                         ("bulbul:v4-flash", "ml-IN", "mp3"))
        self.assertEqual(payload["text"], text)

    def test_invalid_speech_is_reported_and_not_cached(self):
        with patch.object(self.ai.speech_http, "post", return_value=cloud_reply({"audios": ["%"]})):
            with self.assertRaisesRegex(SarvamError, "മലയാള ശബ്ദം"):
                self.ai.speak("ക്യാമറ തയ്യാറാണ്.")
        self.assertFalse(self.ai.speech_cache)


if __name__ == "__main__":
    unittest.main()
