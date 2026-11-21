"""Opt-in smoke tests using the configured phone and Sarvam account."""

import os
import unittest

import requests

from camera import DEFAULT_URL, Camera
from sarvam_client import SarvamClient
from settings import load_environment
from task_engine import malayalam


@unittest.skipUnless(os.getenv("DRISHTIGUIDE_LIVE") == "1", "set DRISHTIGUIDE_LIVE=1 to use the phone and Sarvam")
class LiveTests(unittest.TestCase):
    def setUp(self):
        load_environment()
        self.ai = SarvamClient()
        self.addCleanup(self.ai.close)
        self.assertTrue(self.ai.key, "Set SARVAM_API_KEY in .env.")

    def test_phone_and_vision(self):
        camera = Camera(os.getenv("CAMERA_URL", DEFAULT_URL), int(os.getenv("CAMERA_ROTATION", "0")))
        camera.start()
        try:
            with camera.changed:
                ready = camera.changed.wait_for(lambda: camera.frame is not None, timeout=15)
            self.assertTrue(ready, camera.status()["detail"] or "No valid MJPEG frame in 15 seconds.")
            status = camera.status()
            shot = camera.snapshot(int(os.getenv("IMAGE_MAX_WIDTH", "1280")),
                                   int(os.getenv("JPEG_QUALITY", "82")))
        finally:
            camera.close()
        result, vision_ms = self.ai.guide(shot.jpeg, {}, "inspect", "")
        self.assertTrue(result.device_name or result.needs_new_image,
                        "Expected a recognized device or a request for a clearer view.")
        self.assertTrue(malayalam(result.message_ml), "Expected a written reply in Malayalam.")
        print(f"Camera {status['connect_ms']} ms, frame age {shot.frame_age_ms} ms, "
              f"capture {shot.capture_ms} ms, JPEG {shot.jpeg_ms} ms, Sarvam vision {vision_ms} ms.")

    def test_sarvam_model_available(self):
        try:
            response = self.ai.task_http.get(
                "https://api.sarvam.ai/v2/models", headers={"api-subscription-key": self.ai.key},
                timeout=(5, 10), allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise AssertionError(f"Could not reach api.sarvam.ai ({type(exc).__name__}).") from None
        self.assertEqual(response.status_code, 200, "Sarvam model list request failed.")
        models = {model["id"] for model in response.json()["data"]}
        self.assertIn(self.ai.vision_model, models)
        print(f"Sarvam lists {self.ai.vision_model} for the configured key.")

    def test_sarvam_malayalam_voice(self):
        clip, latency, _ = self.ai.speak("ക്യാമറ തയ്യാറാണ്. ചിത്രമെടുത്ത് പരിശോധിക്കാം.")
        is_mp3 = clip.startswith(b"ID3") or (len(clip) > 1 and clip[0] == 255 and clip[1] & 224 == 224)
        self.assertTrue(is_mp3, "Expected an MP3 from Sarvam's Malayalam voice endpoint.")
        print(f"Bulbul Malayalam voice {latency} ms, {len(clip)} bytes.")


if __name__ == "__main__":
    unittest.main()
