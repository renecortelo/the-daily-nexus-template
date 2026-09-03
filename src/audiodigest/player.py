from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


class PlaybackError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedPlayback:
    """A pitch-preserved media file that is safe to prepare off the UI thread."""

    source_path: Path
    media_path: Path
    speed: float


class WindowsAudioPlayer:
    """MCI playback backed by pitch-preserving FFmpeg tempo caches."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.alias = "daily_nexus_episode"
        self.current_path: Path | None = None
        self.playback_path: Path | None = None
        self.cache_dir = cache_dir or (
            Path(os.getenv("LOCALAPPDATA", Path.cwd())) / "AudioDigest" / "playback-cache"
        )
        self._opened = False
        self._lock = threading.RLock()
        self._active_speed = 1.0
        self._logical_length_ms = 0
        self.volume_percent = 80
        self.speed = 1.0

    @staticmethod
    def _mci(command: str, *, response_length: int = 256) -> str:
        if os.name != "nt":
            raise PlaybackError("In-app playback is currently supported on Windows.")
        buffer = ctypes.create_unicode_buffer(response_length)
        result = ctypes.windll.winmm.mciSendStringW(  # type: ignore[attr-defined]
            command,
            buffer,
            response_length,
            None,
        )
        if result:
            error_buffer = ctypes.create_unicode_buffer(256)
            ctypes.windll.winmm.mciGetErrorStringW(  # type: ignore[attr-defined]
                result,
                error_buffer,
                256,
            )
            raise PlaybackError(error_buffer.value or f"Windows playback error {result}")
        return buffer.value.strip()

    @staticmethod
    def _hidden_creation_flags() -> int:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def _cache_path(self, source: Path, speed: float) -> Path:
        stat = source.stat()
        identity = (
            f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{speed:.3f}"
        ).encode()
        digest = hashlib.sha256(identity).hexdigest()[:24]
        return self.cache_dir / f"{digest}-{speed:.2f}x.mp3"

    def _prune_cache(self, *, keep: int = 12) -> None:
        try:
            candidates = sorted(
                self.cache_dir.glob("*.mp3"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for candidate in candidates[keep:]:
            try:
                candidate.unlink()
            except OSError:
                pass

    def _prepare_playback_path(self, source: Path, speed: float) -> Path:
        if abs(speed - 1.0) < 0.001:
            return source
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise PlaybackError(
                "Pitch-preserving speed needs FFmpeg, but FFmpeg was not found."
            )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._cache_path(source, speed)
        if target.is_file() and target.stat().st_size > 1_000:
            return target
        partial = target.with_suffix(".partial.mp3")
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-filter:a",
            f"atempo={speed:.3f}",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-map_metadata",
            "-1",
            "-y",
            str(partial),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            creationflags=self._hidden_creation_flags(),
        )
        if completed.returncode != 0 or not partial.is_file():
            partial.unlink(missing_ok=True)
            detail = completed.stderr.strip()[:500]
            raise PlaybackError(
                f"Could not prepare pitch-preserving playback: {detail or 'FFmpeg failed'}"
            )
        partial.replace(target)
        self._prune_cache()
        return target

    def _close_open_alias(self) -> None:
        if not self._opened:
            return
        try:
            self._mci(f"stop {self.alias}")
        finally:
            try:
                self._mci(f"close {self.alias}")
            finally:
                self._opened = False
                self.playback_path = None
                self._logical_length_ms = 0

    def _open_prepared(self, source: Path, prepared: Path, speed: float) -> None:
        if '"' in str(prepared):
            raise PlaybackError("Episode path contains an unsupported quote character.")
        self._mci(f'open "{prepared}" type mpegvideo alias {self.alias}')
        self._opened = True
        self.current_path = source
        self.playback_path = prepared
        self._active_speed = speed
        self._apply_volume()
        try:
            raw_length = int(self._mci(f"status {self.alias} length") or 0)
        except ValueError:
            raw_length = 0
        self._logical_length_ms = round(raw_length * speed)

    @staticmethod
    def _validate_speed(multiplier: float) -> float:
        if not 0.5 <= multiplier <= 2.0:
            raise PlaybackError("Playback speed must be between 0.5x and 2.0x.")
        return multiplier

    def prepare(
        self,
        path: Path,
        speed: float | None = None,
    ) -> PreparedPlayback:
        """Prepare tempo-adjusted media without opening an MCI device.

        FFmpeg work can safely happen on a worker thread. Windows MCI commands must
        remain on the Tk interface thread, so callers apply the returned object there.
        """

        resolved = path.resolve()
        if not resolved.is_file():
            raise PlaybackError(f"Episode audio does not exist: {resolved}")
        if '"' in str(resolved):
            raise PlaybackError("Episode path contains an unsupported quote character.")
        requested_speed = self._validate_speed(
            self.speed if speed is None else float(speed)
        )
        prepared = self._prepare_playback_path(resolved, requested_speed)
        return PreparedPlayback(
            source_path=resolved,
            media_path=prepared,
            speed=requested_speed,
        )

    def request_speed(self, multiplier: float) -> None:
        requested = self._validate_speed(multiplier)
        with self._lock:
            self.speed = requested

    def play_prepared(self, prepared: PreparedPlayback) -> None:
        with self._lock:
            self.speed = prepared.speed
            self._close_open_alias()
            self._open_prepared(
                prepared.source_path,
                prepared.media_path,
                prepared.speed,
            )
            self._mci(f"play {self.alias}")

    def switch_prepared(self, prepared: PreparedPlayback) -> None:
        """Replace the open media while retaining logical time and play state."""

        with self._lock:
            if self.current_path != prepared.source_path or not self._opened:
                self.speed = prepared.speed
                return
            logical_position = self._position_ms_unlocked()
            mode = self._status_unlocked()
            self.speed = prepared.speed
            self._close_open_alias()
            self._open_prepared(
                prepared.source_path,
                prepared.media_path,
                prepared.speed,
            )
            raw_position = round(logical_position / prepared.speed)
            self._mci(f"seek {self.alias} to {raw_position}")
            if mode == "playing":
                self._mci(f"play {self.alias}")
            elif mode == "paused":
                self._mci(f"play {self.alias}")
                self._mci(f"pause {self.alias}")

    def play(self, path: Path) -> None:
        self.play_prepared(self.prepare(path))

    def _apply_volume(self) -> None:
        self._mci(f"setaudio {self.alias} volume to {self.volume_percent * 10}")

    def set_volume(self, percent: int) -> None:
        if not 0 <= percent <= 100:
            raise PlaybackError("Volume must be between 0 and 100.")
        with self._lock:
            self.volume_percent = percent
            if self._opened:
                self._apply_volume()

    def set_speed(self, multiplier: float) -> None:
        requested = self._validate_speed(multiplier)
        with self._lock:
            if abs(requested - self.speed) < 0.001:
                return
            source = self.current_path
            self.speed = requested
            if not self._opened or source is None:
                return

        prepared = self.prepare(source, requested)
        with self._lock:
            if self.current_path != source or abs(self.speed - requested) >= 0.001:
                return
        self.switch_prepared(prepared)

    def pause(self) -> None:
        with self._lock:
            if self._opened:
                self._mci(f"pause {self.alias}")

    def resume(self) -> None:
        with self._lock:
            if self._opened:
                # "resume" is not consistently implemented by the MPEGVideo MCI
                # driver. "play" reliably continues from the paused position.
                self._mci(f"play {self.alias}")

    def stop(self) -> None:
        with self._lock:
            self._close_open_alias()
            self.current_path = None
            self._active_speed = 1.0

    def _status_unlocked(self) -> str:
        if not self._opened:
            return "stopped"
        return self._mci(f"status {self.alias} mode") or "stopped"

    def status(self) -> str:
        with self._lock:
            return self._status_unlocked()

    def _position_ms_unlocked(self) -> int:
        if not self._opened:
            return 0
        value = self._mci(f"status {self.alias} position")
        try:
            raw_position = int(value or 0)
        except ValueError:
            return 0
        return round(raw_position * self._active_speed)

    def position_ms(self) -> int:
        with self._lock:
            return self._position_ms_unlocked()

    def length_ms(self) -> int:
        with self._lock:
            if not self._opened:
                return 0
            if self._logical_length_ms:
                return self._logical_length_ms
            value = self._mci(f"status {self.alias} length")
            try:
                return round(int(value or 0) * self._active_speed)
            except ValueError:
                return 0

    def seek_ms(self, logical_position_ms: int) -> None:
        with self._lock:
            if not self._opened:
                return
            length = self.length_ms()
            target = min(max(0, logical_position_ms), length or logical_position_ms)
            mode = self._status_unlocked()
            raw_target = round(target / self._active_speed)
            self._mci(f"seek {self.alias} to {raw_target}")
            if mode == "playing":
                self._mci(f"play {self.alias}")
            elif mode == "paused":
                self._mci(f"play {self.alias}")
                self._mci(f"pause {self.alias}")
