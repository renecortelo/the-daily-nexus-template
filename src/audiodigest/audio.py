from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import tempfile
import warnings
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from audiodigest.audio_quality import AudioQuality, measure_encoded_audio
from audiodigest.config import AudioSettings, HostSettings
from audiodigest.episode_budget import MAX_EPISODE_SECONDS
from audiodigest.execution_budget import check_budget, operation_timeout
from audiodigest.models import DialogueTurn, EpisodeScript
from audiodigest.preferences import voice_profile
from audiodigest.progress import counts, timed_operation
from audiodigest.speech import boundary_pause_ms, prepare_speech


class AudioGenerationError(RuntimeError):
    pass


def _complete_audio_duration(duration: float, timeline_ms: int) -> bool:
    # MP3 encoder padding and per-block millisecond rounding are small, not
    # minutes. Check both short and overlong output, including NaN/Infinity.
    expected = timeline_ms / 1000
    return math.isfinite(duration) and expected > 0 and abs(duration - expected) <= 2.0


@dataclass(frozen=True, slots=True)
class _DeliveryBlock:
    turn: DialogueTurn
    is_heading: bool
    phase: str


@dataclass(frozen=True, slots=True)
class AudioResult:
    path: Path
    duration_seconds: float
    transcript_segments: tuple[TranscriptSegment, ...] = ()
    quality: AudioQuality = AudioQuality()


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
                    raise AudioGenerationError(f"Incompatible local audio chunk: {chunk.name}")
                while True:
                    frames = source.readframes(24_000)
                    if not frames:
                        break
                    destination.writeframesraw(frames)


def _wav_edge_silence_ms(path: Path) -> tuple[int, int]:
    """Measure near-silent edges only; never trim or rewrite spoken samples.

    Kokoro/soundfile emits mono PCM16. Unsupported formats are conservatively
    treated as having no silence. Read at most 600 ms at either edge.
    """

    import sys

    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            return 0, 0
        rate = source.getframerate()
        frame_count = source.getnframes()
        edge_frames = min(frame_count, round(rate * 0.6))
        leading = array("h", source.readframes(edge_frames))
        source.setpos(max(0, frame_count - edge_frames))
        trailing = array("h", source.readframes(edge_frames))
        if sys.byteorder != "little":
            leading.byteswap()
            trailing.byteswap()

    def silence_ms(samples) -> int:
        # A low threshold avoids treating quiet speech as a pause. The samples
        # stay in the file regardless: this controls added silence, not trimming.
        silent_frames = next(
            (index for index, sample in enumerate(samples) if abs(sample) > 32),
            len(leading),
        )
        return round(silent_frames * 1000 / max(1, rate))

    return silence_ms(leading), silence_ms(reversed(trailing))


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
            speed=self.settings.synthesis_speed,
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
        if combined.ndim != 1 or not combined.size or not np.isfinite(combined).all():
            raise AudioGenerationError("Kokoro returned invalid mono speech samples")
        peak = float(np.max(np.abs(combined)))
        if peak <= 1e-6:
            raise AudioGenerationError("Kokoro returned silent speech samples")
        # PCM16 otherwise clips values beyond full scale before loudnorm can help.
        # Only attenuate oversized waveforms; never boost or compress quiet speech.
        if peak >= 1:
            combined = combined * (0.999 / peak)
        sf.write(str(output), combined, 24_000)

    def _voice_for_host(self, host_name: str) -> str:
        if host_name.casefold() == self.hosts.primary_name.casefold():
            return self.hosts.primary_voice
        if host_name.casefold() == self.hosts.secondary_name.casefold():
            return self.hosts.secondary_voice
        raise AudioGenerationError(f"No local voice is configured for host {host_name!r}")

    def _delivery_blocks(self, script: EpisodeScript) -> list[_DeliveryBlock]:
        lead_host = self.hosts.active_names[0]
        blocks = [_DeliveryBlock(DialogueTurn(lead_host, script.disclosure), False, "disclosure")]
        blocks.extend(_DeliveryBlock(turn, False, "introduction") for turn in script.introduction)
        for index, section in enumerate(script.sections):
            phase = f"section-{index}"
            blocks.append(_DeliveryBlock(DialogueTurn(lead_host, section.name.value), True, phase))
            blocks.extend(_DeliveryBlock(turn, False, phase) for turn in section.dialogue)
        blocks.extend(_DeliveryBlock(turn, False, "conclusion") for turn in script.conclusion)
        blocks.extend(_DeliveryBlock(turn, False, "sign_off") for turn in script.sign_off)
        return [block for block in blocks if block.turn.text.strip()]

    def _spoken_blocks(self, script: EpisodeScript) -> list[tuple[DialogueTurn, bool]]:
        blocks = self._delivery_blocks(script)
        return [(block.turn, block.is_heading) for block in blocks]

    @staticmethod
    def _pause_after(block: _DeliveryBlock, following: _DeliveryBlock | None) -> int:
        if following is None:
            return 0
        return boundary_pause_ms(
            is_heading=block.is_heading,
            next_is_heading=following.is_heading,
            phase_changed=block.phase != following.phase,
            host_changed=block.turn.host.casefold() != following.turn.host.casefold(),
            ends_with_question=block.turn.text.rstrip().endswith(("?", '?"', "?'", "?\u201d")),
        )

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
            spoken_blocks = self._delivery_blocks(script)
            rendered: list[tuple[Path, int, int, int]] = []
            total_blocks = len(spoken_blocks)
            counts(voice_blocks=0, voice_total=total_blocks)
            print(f"Synthesizing {total_blocks} audio dialogue blocks...", flush=True)

            for index, block in enumerate(spoken_blocks):
                check_budget()
                turn, is_section_heading = block.turn, block.is_heading
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
                    with timed_operation("voice_model"):
                        pipelines[language_code] = self._pipeline(language_code)
                chunk = temp / f"speech-{index:04d}.wav"
                with timed_operation("speech"):
                    self._write_speech_chunk(
                        pipelines[language_code],
                        prepare_speech(
                            turn.text,
                            is_heading=is_section_heading,
                            pronunciations=self.settings.pronunciations,
                        ),
                        chunk,
                        voice=voice,
                    )
                counts(voice_blocks=index + 1)
                with wave.open(str(chunk), "rb") as speech:
                    speech_ms = round((speech.getnframes() / max(1, speech.getframerate())) * 1000)
                leading_ms, trailing_ms = _wav_edge_silence_ms(chunk)
                if speech_ms <= 0:
                    raise AudioGenerationError("Kokoro returned an empty speech chunk")
                rendered.append((chunk, speech_ms, leading_ms, trailing_ms))

            # Both neighbouring waveforms are available now, so existing model
            # pauses count toward the gap. Transcript timing follows actual files.
            silences: dict[int, Path] = {}
            for index, block in enumerate(spoken_blocks):
                chunk, speech_ms, _leading_ms, trailing_ms = rendered[index]
                following = spoken_blocks[index + 1] if index + 1 < total_blocks else None
                next_leading_ms = rendered[index + 1][2] if following else 0
                pause_ms = max(
                    0, self._pause_after(block, following) - trailing_ms - next_leading_ms
                )
                transcript_segments.append(
                    TranscriptSegment(
                        host=block.turn.host,
                        text=block.turn.text,
                        start_ms=timeline_ms,
                        end_ms=timeline_ms + speech_ms + pause_ms,
                        is_heading=block.is_heading,
                    )
                )
                timeline_ms += speech_ms + pause_ms
                if timeline_ms > MAX_EPISODE_SECONDS * 1000:
                    raise AudioGenerationError(
                        "Complete speech exceeds the 30-minute delivery ceiling; "
                        "audio was not cut or published"
                    )
                chunks.append(chunk)
                if pause_ms:
                    if pause_ms not in silences:
                        silence = temp / f"silence-{pause_ms}.wav"
                        self._write_silence(silence, pause_ms)
                        silences[pause_ms] = silence
                    chunks.append(silences[pause_ms])

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
            with timed_operation("audio_encode"):
                completed = subprocess.run(
                    command,
                    timeout=operation_timeout(600),
                    capture_output=True,
                    text=True,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            concat_succeeded = False
            if completed.returncode == 0 and output.is_file() and output.stat().st_size >= 1_000:
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
                    timeout=operation_timeout(30),
                    text=True,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if check_probe.returncode == 0:
                    try:
                        dur = float(json.loads(check_probe.stdout)["format"]["duration"])
                        if _complete_audio_duration(dur, timeline_ms):
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
                with timed_operation("audio_encode"):
                    fallback = subprocess.run(
                        fallback_command,
                        timeout=operation_timeout(600),
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
            timeout=operation_timeout(30),
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
        if not _complete_audio_duration(duration, timeline_ms):
            raise AudioGenerationError(
                "Encoded episode duration does not match the complete speech timeline"
            )
        if duration > MAX_EPISODE_SECONDS:
            raise AudioGenerationError("Encoded episode exceeds the 30-minute delivery ceiling")
        if duration < self.settings.min_duration_seconds:
            raise AudioGenerationError(
                f"Episode duration {duration:.1f}s is below the safety minimum"
            )
        quality = measure_encoded_audio(
            ffmpeg, output,
            target_lufs=self.settings.target_lufs,
            true_peak_db=self.settings.true_peak_db,
        )
        if quality.integrated_lufs is not None:
            print(
                f"Final encoded audio: {quality.integrated_lufs:.1f} LUFS; "
                f"true peak {quality.true_peak_db:.1f} dBTP; {quality.status}.",
                flush=True,
            )
        else:
            print(f"Final audio measurement unavailable: {quality.reason}.", flush=True)
        return AudioResult(
            path=output,
            duration_seconds=duration,
            transcript_segments=tuple(transcript_segments),
            quality=quality,
        )
