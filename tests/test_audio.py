import subprocess
import tempfile
import wave
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from audiodigest.audio import KokoroAudioRenderer, _combine_wav_chunks
from audiodigest.config import AudioSettings, HostSettings
from audiodigest.models import EpisodeScript


class AudioHostTests(TestCase):
    @staticmethod
    def _write_test_wav(path: Path) -> None:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24_000)
            output.writeframes(bytes([1, 0]) * 240)

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
                            '{"format": {"duration": "0.01"}}'
                            if probe_calls == 1
                            else '{"format": {"duration": "120.0"}}'
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
                    side_effect=lambda _pipeline, _text, path, **_kwargs: (
                        self._write_test_wav(path)
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
            self.assertEqual(120.0, result.duration_seconds)
            self.assertEqual(6, len(result.transcript_segments))
            self.assertTrue(result.transcript_segments[2].is_heading)
            self.assertEqual("AI", result.transcript_segments[2].text)
            self.assertEqual(
                sorted(item.start_ms for item in result.transcript_segments),
                [item.start_ms for item in result.transcript_segments],
            )
            self.assertTrue(
                all(
                    item.end_ms > item.start_ms
                    for item in result.transcript_segments
                )
            )

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
