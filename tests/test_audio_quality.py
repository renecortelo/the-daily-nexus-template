import subprocess
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from audiodigest.audio_quality import AudioQuality, _parse_summary, measure_encoded_audio
from audiodigest.execution_budget import RunBudgetExceeded


def summary(loudness="-16.0", peak="-1.1", spread="2.8"):
    return (
        f"Summary:\n  Integrated loudness:\n    I: {loudness} LUFS\n"
        f"  Loudness range:\n    LRA: {spread} LU\n"
        f"  True peak:\n    Peak: {peak} dBFS\n"
    )


class EncodedAudioQualityTests(TestCase):
    def measure(self):
        return measure_encoded_audio(
            "ffmpeg", Path("fictional.mp3"), target_lufs=-16, true_peak_db=-1
        )

    def test_final_summary_only_and_complete_finite_fields(self):
        self.assertEqual((-16.0, -1.1, 2.8), _parse_summary(summary()))
        self.assertEqual((-18.0, -0.5, 2.8), _parse_summary(summary() + summary("-18", "-0.5")))
        for raw in (
            "", summary().replace("Summary:", ""), summary("nan"), summary(peak="-inf"),
            summary(spread="nan"), summary("-70"), summary("999"), summary(spread="101"),
            summary().replace("True peak:", "Sample peak:"),
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(_parse_summary(raw))

    def test_observer_decodes_without_reencoding_and_never_exposes_decoder_output(self):
        diagnostics = "private decoder path and metadata\n" + summary()
        with patch("audiodigest.audio_quality.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", diagnostics)
            quality = self.measure()
        self.assertEqual("within_target", quality.status)
        command = run.call_args.args[0]
        self.assertEqual(["-f", "null", "-"], command[-3:])
        self.assertIn("-nostdin", command)
        self.assertNotIn("-y", command)
        self.assertEqual(30, run.call_args.kwargs["timeout"])
        self.assertNotIn("private", str(quality.to_dict()))
        self.assertEqual(-16, quality.integrated_lufs)

    def test_quiet_or_overshooting_peak_is_a_warning_not_a_generation_retry(self):
        for raw in (summary("-17.2"), summary(peak="-0.8"), summary("-14.5")):
            with patch("audiodigest.audio_quality.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, "", raw)
                quality = self.measure()
                run.assert_called_once()
            self.assertEqual("outside_target", quality.status)

    def test_skip_when_publication_reserve_would_be_consumed(self):
        with (
            patch("audiodigest.audio_quality.operation_timeout", return_value=149.9),
            patch("audiodigest.audio_quality.subprocess.run") as run,
        ):
            quality = self.measure()
            run.assert_not_called()
        self.assertEqual("publication_reserve", quality.reason)
        self.assertNotIn("integrated_lufs", quality.to_dict())

    def test_measurement_outage_is_not_a_false_zero_or_decoder_details(self):
        failures = (
            subprocess.TimeoutExpired("private command", 30, stderr="private output"),
            OSError("private path"), UnicodeError("private metadata"),
        )
        for error in failures:
            with patch("audiodigest.audio_quality.subprocess.run", side_effect=error):
                result = self.measure().to_dict()
            self.assertEqual("unavailable", result["status"])
            self.assertNotIn("private", str(result))
            self.assertNotIn("integrated_lufs", result)
        for code, raw in ((1, summary()), (0, "broken")):
            with patch("audiodigest.audio_quality.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], code, "", raw)
                self.assertEqual("invalid_measurement", self.measure().reason)

    def test_global_execution_deadline_is_not_swallowed(self):
        with patch(
            "audiodigest.audio_quality.subprocess.run", side_effect=RunBudgetExceeded("expired")
        ):
            with self.assertRaises(RunBudgetExceeded):
                self.measure()

    def test_unmeasured_values_are_omitted_instead_of_zero(self):
        self.assertEqual(
            {"status": "unavailable", "reason": "not_measured"}, AudioQuality().to_dict()
        )
        self.assertNotIn("integrated_lufs", AudioQuality(integrated_lufs=float("nan")).to_dict())

    def test_processing_profile_is_fixed_and_not_untrusted_diagnostic_text(self):
        quality = AudioQuality(encoding_profile="balanced_speech_v1").to_dict()
        self.assertEqual("balanced_speech_v1", quality["encoding_profile"])
        self.assertNotIn(
            "encoding_profile", AudioQuality(encoding_profile="arbitrary decoder text").to_dict()
        )
