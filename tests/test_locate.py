"""Offline tests for the spatial locate composer and TTS part splitting."""

import unittest

import app as server
from task_engine import Locate, compose_location

REMOTE = [["POWER", "MUTE"], ["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"],
          ["EXIT", "0", "OK"], ["◀", "▲", "▼", "▶"]]


def locate(**changes):
    base = dict(device_name="TV remote", rows=REMOTE, target_label="EXIT", target_row=5, target_col=1,
                finger_visible=False, finger_row=None, finger_col=None, upright=True, readable=True,
                needs_new_image=False, message_ml="ഒരു വ്യക്തമായ ചിത്രം എടുക്കുക.")
    return Locate(**(base | changes))


class ComposeLocationTests(unittest.TestCase):
    def test_target_position_from_top_bottom_and_left(self):
        text = compose_location(locate())
        self.assertIn("'EXIT'", text)
        self.assertIn("മുകളിൽ നിന്ന് അഞ്ചാമത്തെ വരിയിൽ", text)
        self.assertIn("ഇടത്ത് നിന്ന് ഒന്നാമത്തെ ബട്ടൺ", text)
        self.assertIn("താഴെ നിന്ന് രണ്ടാമത്തെ വരി", text)

    def test_neighbour_landmark_prefers_left_then_right_then_above(self):
        self.assertIn("സമീപത്ത് '0' ബട്ടൺ", compose_location(locate()))
        self.assertIn("സമീപത്ത് '0' ബട്ടൺ", compose_location(locate(target_label="OK", target_col=3, target_row=5)))
        self.assertIn("സമീപത്ത് 'POWER' ബട്ടൺ", compose_location(
            locate(rows=[["POWER", "MUTE"], ["1", "2"]], target_label="MUTE", target_row=1, target_col=2)))
        self.assertIn("സമീപത്ത് '1' ബട്ടൺ", compose_location(
            locate(rows=[["1", "2", "3"], ["4"]], target_label="4", target_row=2, target_col=1)))
        text = compose_location(locate(rows=[["A"], [""]], target_label=None, target_row=2, target_col=1))
        self.assertIn("'ആ'", text)
        self.assertIn("സമീപത്ത് 'A'", text)

    def test_large_numbers_use_digits(self):
        rows = [[str(i) for i in range(12)] for _ in range(12)]
        text = compose_location(locate(rows=rows, target_label="11", target_row=11, target_col=11))
        self.assertIn("11-ാമത്തെ", text)

    def test_finger_moves_in_each_direction(self):
        base = dict(finger_visible=True)
        text = compose_location(locate(**base, finger_row=3, finger_col=2))
        self.assertIn("ഒരു ബട്ടൺ ഇടത്തേക്ക്", text)
        self.assertIn("രണ്ട് വരി താഴേക്ക്", text)
        self.assertIn("വിരൽ നീക്കുക.", text)
        right = compose_location(locate(**base, target_row=5, target_col=3, finger_row=6, finger_col=1))
        self.assertIn("രണ്ട് ബട്ടൺ വലത്തേക്ക്", right)
        self.assertIn("ഒരു വരി മുകളിലേക്ക്", right)

    def test_finger_on_target_and_row_length_mismatch(self):
        same = compose_location(locate(finger_visible=True, finger_row=5, finger_col=1))
        self.assertIn("ശരിയായ ബട്ടണിൽ തന്നെയാണ്", same)
        approx = compose_location(locate(finger_visible=True, finger_row=6, finger_col=2))
        self.assertIn("ഏകദേശം", approx)
        exact = compose_location(locate(finger_visible=True, finger_row=3, finger_col=2))
        self.assertNotIn("ഏകദേശം", exact)

    def test_unusable_results_fall_back_to_model_message(self):
        message = "ഒരു വ്യക്തമായ ചിത്രം എടുക്കുക."
        for changes in (dict(readable=False), dict(needs_new_image=True), dict(upright=False),
                        dict(rows=[]), dict(target_row=None), dict(target_col=None),
                        dict(target_row=9), dict(target_col=4), dict(target_row=0)):
            self.assertEqual(compose_location(locate(**changes)), message, changes)
        generic = compose_location(locate(readable=False, message_ml="not malayalam"))
        self.assertIn("അടുത്ത ചിത്രം എടുക്കുക", generic)

    def test_invalid_finger_position_is_ignored(self):
        text = compose_location(locate(finger_visible=True, finger_row=9, finger_col=1))
        self.assertIn("മുകളിൽ നിന്ന് അഞ്ചാമത്തെ", text)


class PhonePartsTests(unittest.TestCase):
    def test_single_sentence_is_one_part(self):
        self.assertEqual(server.phone_parts("മുന്നിൽ ഒരു മേശ ഉണ്ട്."), ["മുന്നിൽ ഒരു മേശ ഉണ്ട്."])

    def test_at_most_three_parts_first_is_first_sentence(self):
        text = " ".join(f"ഇത് വാചകം നമ്പർ {i} ആണ്." for i in range(1, 8))
        parts = server.phone_parts(text)
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], "ഇത് വാചകം നമ്പർ 1 ആണ്.")
        self.assertEqual(" ".join(parts), text)

    def test_short_pieces_merge_and_marks_split(self):
        parts = server.phone_parts("ശരി. ഇപ്പോൾ മുന്നിൽ ഒരു വലിയ മേശ ഉണ്ട്। അതിന്റെ മുകളിൽ ഒരു പുസ്തകം ഉണ്ട്.")
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].startswith("ശരി."))

    def test_decimal_numbers_and_non_malayalam(self):
        self.assertEqual(server.phone_parts("വില 3.5 രൂപ ആണ്."), ["വില 3.5 രൂപ ആണ്."])
        self.assertEqual(server.phone_parts("only english"), [])


if __name__ == "__main__":
    unittest.main()
