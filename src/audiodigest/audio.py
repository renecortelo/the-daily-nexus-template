from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import warnings
import wave
from dataclasses import dataclass
from pathlib import Path

from audiodigest.config import AudioSettings, HostSettings
from audiodigest.models import DialogueTurn, EpisodeScript
from audiodigest.preferences import voice_profile


class AudioGenerationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AudioResult:
    path: Path
    duration_seconds: float
    transcript_segments: tuple[TranscriptSegment, ...] = ()


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    host: str
    text: str
    start_ms: int
    end_ms: int
    is_heading: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "is_heading": self.is_heading,
        }


def _require_binary(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise AudioGenerationError(f"Required executable not found: {name}")
    return resolved


def _safe_concat_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", r"'\''")


def _combine_wav_chunks(chunks: list[Path], output: Path) -> None:
    """Stream compatible WAV chunks together without loading the episode into memory."""

    if not chunks:
        raise AudioGenerationError("No speech chunks were available for the episode")
    expected: tuple[int, int, int, str] | None = None
    with wave.open(str(output), "wb") as destination:
        for chunk in chunks:
            with wave.open(str(chunk), "rb") as source:
                signature = (
                    source.getnchannels(),
                    source.getsampwidth(),
                    source.getframerate(),
                    source.getcomptype(),
                )
                if expected is None:
                    expected = signature
                    destination.setnchannels(signature[0])
                    destination.setsampwidth(signature[1])
                    destination.setframerate(signature[2])
                    destination.setcomptype(signature[3], source.getcompname())
                elif signature != expected:
                    raise AudioGenerationError(
                        f"Incompatible local audio chunk: {chunk.name}"
                    )
                while True:
                    frames = source.readframes(24_000)
                    if not frames:
                        break
                    destination.writeframesraw(frames)


class KokoroAudioRenderer:
    def __init__(self, settings: AudioSettings, hosts: HostSettings):
        self.settings = settings
        self.hosts = hosts

    def _pipeline(self, language_code: str):
        try:
            from kokoro import KPipeline
        except ImportError as exc:
            raise AudioGenerationError(
                "Kokoro is not installed. Run the Windows setup script with audio dependencies."
            ) from exc
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="dropout option adds dropout.*num_layers greater than 1.*",
                category=UserWarning,
            )
            warnings.filterwarnings(
                "ignore",
                message=".*torch.nn.utils.weight_norm.*deprecated.*",
                category=FutureWarning,
            )
            return KPipeline(
                lang_code=language_code,
                repo_id="hexgrad/Kokoro-82M",
            )

    def _write_speech_chunk(
        self,
        pipeline,
        text: str,
        output: Path,
        *,
        voice: str,
    ) -> None:
        try:
            import soundfile as sf
        except ImportError as exc:
            raise AudioGenerationError("soundfile is required for local audio rendering") from exc
        audio_parts = []
        for _graphemes, _phonemes, audio in pipeline(
            text,
            voice=voice,
            speed=1.0,
            split_pattern=r"\n+",
        ):
            audio_parts.append(audio)
        if not audio_parts:
            raise AudioGenerationError("Kokoro returned no audio")
        try:
            import numpy as np
        except ImportError as exc:
            raise AudioGenerationError("numpy is required for local audio rendering") from exc
        combined = np.concatenate(audio_parts)
        sf.write(str(output), combined, 24_000)

    def _voice_for_host(self, host_name: str) -> str:
        if host_name.casefold() == self.hosts.primary_name.casefold():
            return self.hosts.primary_voice
        if host_name.casefold() == self.hosts.secondary_name.casefold():
            return self.hosts.secondary_voice
        raise AudioGenerationError(f"No local voice is configured for host {host_name!r}")

    def _spoken_blocks(self, script: EpisodeScript) -> list[tuple[DialogueTurn, bool]]:
        lead_host = self.hosts.active_names[0]
        blocks = [(DialogueTurn(lead_host, script.disclosure), False)]
        blocks.extend((turn, False) for turn in script.introduction)
        for section in script.sections:
            blocks.append((DialogueTurn(lead_host, section.name.value), True))
            blocks.extend((turn, False) for turn in section.dialogue)
        blocks.extend((turn, False) for turn in script.conclusion)
        blocks.extend((turn, False) for turn in script.sign_off)
        return blocks

    @staticmethod
    def _write_silence(output: Path, milliseconds: int) -> None:
        import numpy as np
        import soundfile as sf

        samples = int(24_000 * milliseconds / 1000)
        sf.write(str(output), np.zeros(samples, dtype=np.float32), 24_000)

    def render(self, script: EpisodeScript, output: Path) -> AudioResult:
        ffmpeg = _require_binary(self.settings.ffmpeg)
        ffprobe = _require_binary(self.settings.ffprobe)
        output.parent.mkdir(parents=True, exist_ok=True)
        pipelines = {}

        with tempfile.TemporaryDirectory(prefix="audiodigest-audio-") as temp_name:
            temp = Path(temp_name)
            chunks: list[Path] = []
            transcript_segments: list[TranscriptSegment] = []
            timeline_ms = 0
            spoken_blocks = self._spoken_blocks(script)

            sentence_silence = temp / "sentence-silence.wav"
            section_silence = temp / "section-silence.wav"
            self._write_silence(sentence_silence, 150)
            self._write_silence(section_silence, 500)
            total_blocks = len(spoken_blocks)
            print(f"Synthesizing {total_blocks} audio dialogue blocks...", flush=True)

            for index, (turn, is_section_heading) in enumerate(spoken_blocks):
                if not turn.text.strip():
                    continue
                percent = int(((index + 1) / total_blocks) * 100)
                heading_marker = " [heading]" if is_section_heading else ""
                print(
                    f"[{index + 1}/{total_blocks}] Rendering {turn.host}"
                    f"{heading_marker} ({percent}%)...",
                    flush=True,
                )
                voice = self._voice_for_host(turn.host)
                language_code = voice_profile(voice).language_code
                if language_code not in pipelines:
                    pipelines[language_code] = self._pipeline(language_code)
                chunk = temp / f"speech-{index:04d}.wav"
                self._write_speech_chunk(
                    pipelines[language_code],
                    turn.text,
                    chunk,
                    voice=voice,
                )
                with wave.open(str(chunk), "rb") as speech:
                    speech_ms = round(
                        (speech.getnframes() / max(1, speech.getframerate())) * 1000
                    )
                pause_ms = 500 if is_section_heading else 150
                transcript_segments.append(
                    TranscriptSegment(
                        host=turn.host,
                        text=turn.text,
                        start_ms=timeline_ms,
                        end_ms=timeline_ms + speech_ms + pause_ms,
                        is_heading=is_section_heading,
                    )
                )
                timeline_ms += speech_ms + pause_ms
                chunks.append(chunk)
                chunks.append(section_silence if is_section_heading else sentence_silence)

            concat_file = temp / "concat.txt"
            concat_file.write_text(
                "\n".join(f"file '{_safe_concat_path(path)}'" for path in chunks),
                encoding="utf-8",
            )
            command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-ac",
                "1",
                "-ar",
                str(self.settings.sample_rate),
                "-b:a",
                self.settings.bitrate,
                "-af",
                (f"loudnorm=I={self.settings.target_lufs}:TP={self.settings.true_peak_db}:LRA=11"),
                "-y",
                str(output),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            concat_succeeded = False
            if (
                completed.returncode == 0
                and output.is_file()
                and output.stat().st_size >= 1_000
            ):
                check_probe = subprocess.run(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "json",
                        str(output),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if check_probe.returncode == 0:
                    try:
                        dur = float(json.loads(check_probe.stdout)["format"]["duration"])
                        timeline_target = (
                            (timeline_ms / 1000) * 0.75
                            if timeline_ms > 0
                            else self.settings.min_duration_seconds
                        )
                        min_expected = min(
                            self.settings.min_duration_seconds,
                            timeline_target,
                        )
                        if dur >= min_expected:
                            concat_succeeded = True
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        concat_succeeded = False

            if not concat_succeeded:
                output.unlink(missing_ok=True)
                combined_wav = temp / "combined.wav"
                _combine_wav_chunks(chunks, combined_wav)
                fallback_command = [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(combined_wav),
                    "-ac",
                    "1",
                    "-ar",
                    str(self.settings.sample_rate),
                    "-b:a",
                    self.settings.bitrate,
                    "-af",
                    (
                        f"loudnorm=I={self.settings.target_lufs}:"
                        f"TP={self.settings.true_peak_db}:LRA=11"
                    ),
                    "-y",
                    str(output),
                ]
                fallback = subprocess.run(
                    fallback_command,
                    capture_output=True,
                    text=True,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if (
                    fallback.returncode != 0
                    or not output.is_file()
                    or output.stat().st_size < 1_000
                ):
                    concat_detail = completed.stderr.strip()[:500]
                    fallback_detail = fallback.stderr.strip()[:500]
                    raise AudioGenerationError(
                        "FFmpeg could not create the local episode after a safe retry. "
                        f"Concat exit={completed.returncode}"
                        f"{f': {concat_detail}' if concat_detail else ''}; "
                        f"fallback exit={fallback.returncode}"
                        f"{f': {fallback_detail}' if fallback_detail else ''}"
                    )

        probe = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if probe.returncode != 0:
            raise AudioGenerationError(f"FFprobe failed: {probe.stderr.strip()}")
        try:
            duration = float(json.loads(probe.stdout)["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AudioGenerationError("FFprobe returned an invalid duration") from exc
        if duration < self.settings.min_duration_seconds:
            raise AudioGenerationError(
                f"Episode duration {duration:.1f}s is below the safety minimum"
            )
        return AudioResult(
            path=output,
            duration_seconds=duration,
            transcript_segments=tuple(transcript_segments),
        )
