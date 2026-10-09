import tempfile
import wave
from array import array
from pathlib import Path
from unittest import TestCase

from audiodigest.audio import _wav_edge_silence_ms
from audiodigest.speech import (
    _NEWS_NAME_PRONUNCIATIONS,
    boundary_pause_ms,
    ends_with_spoken_question,
    prepare_speech,
    validate_pronunciations,
)


class SpeechPreparationTests(TestCase):
    def test_public_news_names_are_exact_synthesis_only_overrides(self):
        text = "Carles Puigdemont and Sánchez; unknown surname remains unchanged."
        prepared = prepare_speech(text)
        self.assertIn("[Carles Puigdemont](/kˈɑɾləs puʒðəmˈɔn/)", prepared)
        self.assertIn("[Sánchez](/sˈɑnʧɛs/)", prepared)
        self.assertIn("unknown surname remains unchanged", prepared)
        self.assertEqual(prepared, prepare_speech(prepared))
        self.assertEqual(text, "Carles Puigdemont and Sánchez; unknown surname remains unchanged.")
        self.assertIn(
            "[Sánchez](/sˈɑnʧɛθ/)",
            prepare_speech("Sánchez", pronunciations={"Sánchez": "sˈɑnʧɛθ"}),
        )

    def test_documented_phone_inventory_accepts_supported_variants_only(self):
        self.assertEqual(
            _NEWS_NAME_PRONUNCIATIONS, validate_pronunciations(_NEWS_NAME_PRONUNCIATIONS)
        )
        self.assertEqual({"Sample": "ᵊᵻɾˈaː"}, validate_pronunciations({"Sample": "ᵊᵻɾˈaː"}))
        for value in ("n\u200bm", "n\tm", "🙂", "<&>", "sˈanxes"):
            with self.assertRaises(ValueError):
                validate_pronunciations({"Sample": value})
    def test_technical_acronyms_use_letter_names_without_touching_other_words(self):
        self.assertEqual(
            "[IT](/ˌItˈi/) uses an [API](/ˌApˌiˈI/) for [SQL](/ˌɛskjˌuˈɛl/) "
            "and [LLMs](/ˌɛlˌɛlˈɛmz/).",
            prepare_speech("IT uses an API for SQL and LLMs."),
        )
        self.assertEqual(
            "It is valid; it fits. SQLite and OpenAI.",
            prepare_speech("It is valid; it fits. SQLite and OpenAI."),
        )

    def test_numbers_dates_names_and_uncertainty_are_not_rewritten(self):
        text = (
            "A newsletter reported €1.25 million on July 20; an analyst said it may change by 3.2%."
        )
        self.assertEqual(text, prepare_speech(text))

    def test_versions_and_punctuation_are_preserved(self):
        self.assertEqual(
            "[GPT](/ʤˌipˌitˈi/) 5 point 6, [API](/ˌApˌiˈI/) version 1 point 2 point 0?",
            prepare_speech("GPT-5.6, API v1.2.0?"),
        )
        self.assertEqual("Python 3 point 12 point 15.", prepare_speech("Python 3.12.15."))
        self.assertEqual("Python 1.2.3.4.5", prepare_speech("Python 1.2.3.4.5"))

    def test_local_name_dictionary_is_exact_nonrecursive_and_does_not_mutate_config(self):
        names = {"Example Tool": "ˈɛɡzæmpəl tul"}
        self.assertEqual(
            "[Example Tool](/ˈɛɡzæmpəl tul/) and Example Toolkit.",
            prepare_speech("Example Tool and Example Toolkit.", pronunciations=names),
        )
        self.assertEqual({"Example Tool": "ˈɛɡzæmpəl tul"}, names)
        self.assertEqual("It", prepare_speech("It", pronunciations=names))

    def test_pronunciation_dictionary_rejects_directions_urls_and_markup(self):
        for value in (
            [],
            {"Name": "https://example.com"},
            {"Name": "<break/>"},
            {"Name": "[laugh]"},
            {"Name": "line\nline"},
            {"[Name]": "nAm"},
            {"Name": ""},
            {"Name": 3},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_pronunciations(value)

    def test_existing_phoneme_annotations_are_not_nested(self):
        prepared = prepare_speech("IT uses an API.")
        self.assertEqual(prepared, prepare_speech(prepared))

    def test_annotations_protect_version_labels_and_surrounding_text_still_prepares(self):
        annotated = "[Python 3.12](/pˈaIθən/)"
        text = f"{annotated}, GPT-5.6 and [API](/ˈAp/)."
        expected = f"{annotated}, [GPT](/ʤˌipˌitˈi/) 5 point 6 and [API](/ˈAp/)."
        self.assertEqual(expected, prepare_speech(text))
        self.assertEqual(expected, prepare_speech(expected))

    def test_multiple_annotations_and_local_name_override_remain_exact(self):
        text = "[version 1.2](/nAm/) then Sánchez and [GPT-5.6](/tɛst/)."
        prepared = prepare_speech(text, pronunciations={"Sánchez": "sˈɑnʧɛθ"})
        self.assertEqual(
            "[version 1.2](/nAm/) then [Sánchez](/sˈɑnʧɛθ/) and [GPT-5.6](/tɛst/).",
            prepared,
        )
        self.assertEqual(prepared, prepare_speech(prepared))

    def test_terminal_questions_allow_closing_quotes_but_not_following_statements(self):
        for text in (
            "Ready?", 'She asked, “Ready?”', "‘Ready?’", "(Ready?)", "«Ready?»", "Ready?') \n",
        ):
            with self.subTest(text=text):
                self.assertTrue(ends_with_spoken_question(text))
        for text in ("", "Ready.", "Ready? Not yet.", "The label is '?' in this example."):
            with self.subTest(text=text):
                self.assertFalse(ends_with_spoken_question(text))

    def test_tih_heading_is_speakable_but_body_text_is_unchanged(self):
        text = "TIH: Today in History"
        self.assertEqual("Today in History.", prepare_speech(text, is_heading=True))
        self.assertEqual(text, prepare_speech(text))

    def test_pause_rules_distinguish_headings_responses_and_same_host_copy(self):
        defaults = dict(
            is_heading=False,
            next_is_heading=False,
            phase_changed=False,
            host_changed=False,
            ends_with_question=False,
        )
        self.assertEqual(300, boundary_pause_ms(**defaults))
        for flag, expected in (
            ("next_is_heading", 600),
            ("is_heading", 250),
            ("phase_changed", 420),
            ("host_changed", 200),
            ("ends_with_question", 160),
        ):
            self.assertEqual(expected, boundary_pause_ms(**{**defaults, flag: True}))

    def test_edge_detection_preserves_quiet_speech_and_reads_both_edges(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            path = Path(name) / "speech.wav"
            frames = array("h", [0] * 2400 + [40] * 4800 + [0] * 4800)
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes(frames.tobytes())
            original = path.read_bytes()
            self.assertEqual((100, 200), _wav_edge_silence_ms(path))
            self.assertEqual(original, path.read_bytes())

    def test_edge_detection_is_bounded_and_does_not_guess_unsupported_formats(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            path = Path(name) / "silence.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes(bytes(48000))
            self.assertEqual((600, 600), _wav_edge_silence_ms(path))
            with wave.open(str(path), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes(bytes(9600))
            self.assertEqual((0, 0), _wav_edge_silence_ms(path))
