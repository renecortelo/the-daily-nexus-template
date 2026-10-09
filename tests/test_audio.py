import json
import math
import shutil
import subprocess
import tempfile
import wave
from array import array
from importlib.util import find_spec
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch

from audiodigest.audio import (
    AudioGenerationError,
    KokoroAudioRenderer,
    _combine_wav_chunks,
    _complete_audio_duration,
    _DeliveryBlock,
    _speech_encoding_filter,
)
from audiodigest.audio_quality import AudioQuality
from audiodigest.config import AudioSettings, HostSettings
from audiodigest.models import DialogueTurn, EpisodeScript


class AudioHostTests(TestCase):
    def test_quoted_question_uses_response_gap_without_changing_phase_priorities(self):
        following = _DeliveryBlock(DialogueTurn("Nox", "Not yet."), False, "section-0")
        for question in ("‘Ready?’", "«Ready?»", "(Ready?)"):
            block = _DeliveryBlock(DialogueTurn("Dalia", question), False, "section-0")
            self.assertEqual(160, KokoroAudioRenderer._pause_after(block, following))
            self.assertEqual(question, block.turn.text)
            next_phase = _DeliveryBlock(following.turn, False, "conclusion")
            self.assertEqual(420, KokoroAudioRenderer._pause_after(block, next_phase))
            heading = _DeliveryBlock(DialogueTurn("Dalia", "AI"), True, "section-1")
            self.assertEqual(600, KokoroAudioRenderer._pause_after(block, heading))
            self.assertEqual(0, KokoroAudioRenderer._pause_after(block, None))

    def test_balanced_filter_keeps_configured_target_and_has_no_timing_transform(self):
        for loudness, peak in ((-16.0, -1.0), (-18.0, -2.0), (-70.0, -9.0), (-5.0, 0.0)):
            value = _speech_encoding_filter(AudioSettings(target_lufs=loudness, true_peak_db=peak))
            self.assertIn(f"loudnorm=I={loudness}:TP={max(-9.0, peak - 2.0)}:", value)
            self.assertIn("ratio=2:attack=5:release=100:makeup=1", value)
            self.assertIn("offset=0.5", value)
            for forbidden in ("atempo", "asetrate", "atrim", "apad", "alimiter", "dual_mono"):
                self.assertNotIn(forbidden, value)

    @skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "optional local encoder")
    def test_real_balanced_encoder_preserves_complete_pcm_duration(self):
        # Synthetic four-second signal, not a voice/model/provider request.
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            source, encoded = root / "synthetic.wav", root / "synthetic.mp3"
            rate, seconds = 24_000, 4
            samples = array(
                "h", (round(5000 * math.sin(2 * math.pi * 180 * i / rate))
                      for i in range(rate * seconds))
            )
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(rate)
                output.writeframes(samples.tobytes())
            complete = subprocess.run(
                [shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-i", str(source), "-ac", "1", "-ar", "44100", "-b:a", "64k",
                 "-af", _speech_encoding_filter(AudioSettings()), str(encoded)],
                capture_output=True, check=False, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(0, complete.returncode)
            probe = subprocess.run(
                [shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(encoded)],
                capture_output=True, text=True, check=False, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(0, probe.returncode)
            actual = float(json.loads(probe.stdout)["format"]["duration"])
            self.assertAlmostEqual(seconds, actual, delta=0.1)

    def test_duration_cap_rejects_complete_speech_instead_of_truncating_it(self):
        script = EpisodeScript.from_dict(
            {
                "title": "The Daily Nexus",
                "hosts": ["Nox"],
                "introduction": "Introduction",
                "sections": [
                    {"name": "AI", "narration": "Supported evidence", "story_ids": ["story"]}
                ],
                "conclusion": "Conclusion",
                "sign_off": "Closing quotation",
                "show_notes": [],
            }
        )
        renderer = KokoroAudioRenderer(
            AudioSettings(min_duration_seconds=1), HostSettings(count=1, solo_name="Nox")
        )
        with (
            tempfile.TemporaryDirectory() as name,
            patch("audiodigest.audio.MAX_EPISODE_SECONDS", 30),
            patch("audiodigest.audio._require_binary", side_effect=lambda value: value),
            patch.object(renderer, "_pipeline", return_value=object()),
            patch.object(
                renderer,
                "_write_speech_chunk",
                side_effect=lambda _p, _t, path, **_kw: self._write_test_wav(path),
            ),
            patch.object(
                renderer, "_write_silence", side_effect=lambda path, _ms: self._write_test_wav(path)
            ),
            patch("audiodigest.audio.subprocess.run") as encode,
        ):
            with self.assertRaisesRegex(AudioGenerationError, "not cut or published"):
                renderer.render(script, Path(name) / "episode.mp3")
            encode.assert_not_called()

    def test_full_duration_check_rejects_truncation_and_invalid_values(self):
        for duration in (180, 900, 1500, float("nan"), float("inf")):
            self.assertFalse(_complete_audio_duration(duration, 1_200_000))
        self.assertTrue(_complete_audio_duration(1200.06, 1_200_000))
        self.assertFalse(_complete_audio_duration(0, 0))

    def test_contextual_gaps_keep_original_transcript_and_match_actual_files(self):
        script = EpisodeScript.from_dict(
            {
                "title": "The Daily Nexus",
                "hosts": ["Dalia", "Nox"],
                "introduction": [
                    {"host": "Dalia", "text": "An API update?"},
                    {"host": "Nox", "text": "IT teams are testing it."},
                ],
                "sections": [
                    {
                        "name": "AI",
                        "story_ids": ["test"],
                        "dialogue": [{"host": "Dalia", "text": "GPU demand changed."}],
                    }
                ],
                "conclusion": [{"host": "Nox", "text": "That is the evidence."}],
                "sign_off": [{"host": "Dalia", "text": "The closing quotation stays exact."}],
                "show_notes": [],
            }
        )
        renderer = KokoroAudioRenderer(AudioSettings(min_duration_seconds=1), HostSettings(count=2))
        spoken = []
        wave_total_ms = 0

        def speech(_pipeline, text, path, **_kwargs):
            spoken.append(text)
            # 100 ms quiet at each side, 300 ms speech, with every sample retained.
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes(bytes(4800) + bytes([64, 0]) * 7200 + bytes(4800))

        def silence(path, milliseconds):
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes(bytes(48 * milliseconds))

        def run(command, **_kwargs):
            nonlocal wave_total_ms
            if "-show_entries" in command:
                return subprocess.CompletedProcess(
                    command, 0, json.dumps({"format": {"duration": wave_total_ms / 1000}}), ""
                )
            concat = Path(command[command.index("-i") + 1])
            self.assertEqual(
                _speech_encoding_filter(renderer.settings), command[command.index("-af") + 1]
            )
            for line in concat.read_text(encoding="utf-8").splitlines():
                path = Path(line.removeprefix("file '").removesuffix("'"))
                with wave.open(str(path), "rb") as frames:
                    wave_total_ms += round(frames.getnframes() / frames.getframerate() * 1000)
            Path(command[-1]).write_bytes(b"x" * 1100)
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name,
            patch("audiodigest.audio._require_binary", side_effect=lambda value: value),
            patch.object(renderer, "_pipeline", return_value=object()),
            patch.object(renderer, "_write_speech_chunk", side_effect=speech),
            patch.object(renderer, "_write_silence", side_effect=silence),
            patch("audiodigest.audio.subprocess.run", side_effect=run),
            patch("audiodigest.audio.measure_encoded_audio", return_value=AudioQuality()),
        ):
            result = renderer.render(script, Path(name) / "episode.mp3")

        self.assertIn("[API](/ˌApˌiˈI/)", spoken[1])
        self.assertEqual("An API update?", result.transcript_segments[1].text)
        self.assertEqual("IT teams are testing it.", result.transcript_segments[2].text)
        # Existing 200 ms of model silence exceeds a 160 ms question-response gap.
        question_segment = result.transcript_segments[1]
        self.assertEqual(500, question_segment.end_ms - question_segment.start_ms)
        self.assertEqual(wave_total_ms, result.transcript_segments[-1].end_ms)
        self.assertEqual(7, len(result.transcript_segments))
        self.assertEqual("balanced_speech_v1", result.quality.to_dict()["encoding_profile"])
        for first, second in zip(
            result.transcript_segments, result.transcript_segments[1:], strict=False
        ):
            self.assertEqual(first.end_ms, second.start_ms)

    @staticmethod
    def _write_test_wav(path: Path) -> None:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24_000)
            output.writeframes(bytes([1, 0]) * (24_000 * 20))

    def test_wav_fallback_streams_chunks_in_order(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            chunks = [root / "one.wav", root / "two.wav"]
            for index, chunk in enumerate(chunks, start=1):
                with wave.open(str(chunk), "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(24_000)
                    output.writeframes(bytes([index, 0]) * 240)

            combined = root / "combined.wav"
            _combine_wav_chunks(chunks, combined)

            with wave.open(str(combined), "rb") as result:
                self.assertEqual(480, result.getnframes())
                frames = result.readframes(480)
            self.assertEqual(bytes([1, 0]) * 240, frames[:480])
            self.assertEqual(bytes([2, 0]) * 240, frames[480:])

    def test_renderer_retries_a_truncated_concat_with_streamed_wav(self):
        script = EpisodeScript.from_dict(
            {
                "title": "The Daily Nexus",
                "hosts": ["Nox"],
                "introduction": [{"host": "Nox", "text": "I'm Nox."}],
                "sections": [
                    {
                        "name": "AI",
                        "dialogue": [{"host": "Nox", "text": "A verified signal."}],
                        "story_ids": ["ai"],
                    }
                ],
                "conclusion": [{"host": "Nox", "text": "That is the signal."}],
                "sign_off": [{"host": "Nox", "text": "A closing quotation."}],
                "show_notes": [],
            }
        )
        renderer = KokoroAudioRenderer(
            AudioSettings(min_duration_seconds=60),
            HostSettings(count=1, solo_name="Nox"),
        )
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            output = Path(name) / "episode.mp3"
            calls = 0
            probe_calls = 0

            def run(command, **_kwargs):
                nonlocal calls, probe_calls
                calls += 1
                if "-show_entries" in command:
                    probe_calls += 1
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        (
                            '{"format": {"duration": "61.0"}}'
                            if probe_calls == 1
                            else '{"format": {"duration": "120.8"}}'
                        ),
                        "",
                    )
                self.assertEqual(
                    _speech_encoding_filter(renderer.settings), command[command.index("-af") + 1]
                )
                Path(command[-1]).write_bytes(b"x" * 1_100)
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "",
                    "",
                )

            with (
                patch("audiodigest.audio._require_binary", side_effect=lambda value: value),
                patch.object(renderer, "_pipeline", return_value=object()),
                patch.object(
                    renderer,
                    "_write_speech_chunk",
                    side_effect=lambda _pipeline, _text, path, **_kwargs: self._write_test_wav(
                        path
                    ),
                ),
                patch.object(
                    renderer,
                    "_write_silence",
                    side_effect=lambda path, _milliseconds: self._write_test_wav(path),
                ),
                patch("audiodigest.audio.subprocess.run", side_effect=run),
                patch("audiodigest.audio.measure_encoded_audio", return_value=AudioQuality()),
            ):
                result = renderer.render(script, output)

            self.assertEqual(4, calls)
            self.assertEqual(2, probe_calls)
            self.assertEqual(120.8, result.duration_seconds)
            self.assertEqual(6, len(result.transcript_segments))
            self.assertTrue(result.transcript_segments[2].is_heading)
            self.assertEqual("AI", result.transcript_segments[2].text)
            self.assertEqual(
                sorted(item.start_ms for item in result.transcript_segments),
                [item.start_ms for item in result.transcript_segments],
            )
            self.assertTrue(all(item.end_ms > item.start_ms for item in result.transcript_segments))

    @skipUnless(find_spec("numpy") and find_spec("soundfile"), "optional audio dependencies")
    def test_invalid_synthesis_samples_are_rejected_before_pcm_encoding(self):
        import numpy as np

        renderer = KokoroAudioRenderer(AudioSettings(), HostSettings())
        for samples in (
            np.array([]), np.zeros(20), np.array([0.1, np.nan]),
            np.array([0.1, np.inf]), np.zeros((20, 2)),
        ):
            with (
                self.subTest(samples=samples),
                patch("soundfile.write") as write,
                self.assertRaises(AudioGenerationError),
            ):
                renderer._write_speech_chunk(
                    lambda *_args, _samples=samples, **_kwargs: [("", "", _samples)], "fictional",
                    Path("fictional.wav"), voice="af_heart",
                )
            write.assert_not_called()

    @skipUnless(find_spec("numpy") and find_spec("soundfile"), "optional audio dependencies")
    def test_oversized_samples_are_attenuated_before_pcm_clipping_without_compression(self):
        import numpy as np

        renderer = KokoroAudioRenderer(AudioSettings(), HostSettings())
        for samples in (np.array([0.2, -0.3]), np.array([0.4, -1.2, 0.8])):
            with patch("soundfile.write") as write:
                renderer._write_speech_chunk(
                    lambda *_args, _samples=samples, **_kwargs: [("", "", _samples)], "fictional",
                    Path("fictional.wav"), voice="af_heart",
                )
                actual = write.call_args.args[1]
            expected = samples if np.max(np.abs(samples)) < 1 else samples * (0.999 / 1.2)
            np.testing.assert_allclose(expected, actual)
            self.assertLess(np.max(np.abs(actual)), 1)

    def test_solo_nox_reads_disclosure_and_section_headings(self):
        hosts = HostSettings(count=1, solo_name="Nox")
        script = EpisodeScript.from_dict(
            {
                "title": "The Daily Nexus",
                "hosts": ["Nox"],
                "introduction": [{"host": "Nox", "text": "I'm Nox."}],
                "sections": [
                    {
                        "name": "AI",
                        "dialogue": [{"host": "Nox", "text": "The signal is clear."}],
                        "story_ids": ["ai"],
                    }
                ],
                "conclusion": [{"host": "Nox", "text": "That is the signal."}],
                "sign_off": [{"host": "Nox", "text": "A closing quotation."}],
                "show_notes": [],
            }
        )

        blocks = KokoroAudioRenderer(AudioSettings(), hosts)._spoken_blocks(script)

        self.assertTrue(blocks)
        self.assertEqual({"Nox"}, {turn.host for turn, _is_heading in blocks})
