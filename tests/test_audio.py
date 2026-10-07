import json
import subprocess
import tempfile
import wave
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from audiodigest.audio import KokoroAudioRenderer, _combine_wav_chunks, _complete_audio_duration
from audiodigest.config import AudioSettings, HostSettings
from audiodigest.models import EpisodeScript


class AudioHostTests(TestCase):
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
