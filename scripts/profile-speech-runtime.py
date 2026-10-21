"""Opt-in offline synthetic Kokoro profile; no episodes, accounts or model downloads."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
import wave
from array import array
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

PHASES = {
    "preparation",
    "runtime_import",
    "pipeline_initialization",
    "voice_warmup",
    "speech_measurement",
}


def safe_result(raw: dict) -> dict:
    """Rebuild child output; never forward arbitrary dependency diagnostics."""
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("status"), str)
        or raw["status"] not in {"completed", "unavailable"}
    ):
        raise ValueError("unexpected_profile_shape")
    result = {"status": raw["status"]}
    for key, allowed in (
        ("phase", PHASES),
        ("platform", {"windows", "linux", "other"}),
        ("reason", {"offline_runtime_or_cache_unavailable"}),
        (
            "error_kind",
            {
                "ImportError",
                "ModuleNotFoundError",
                "RuntimeError",
                "OSError",
                "FileNotFoundError",
                "LocalEntryNotFoundError",
                "ValueError",
                "other",
            },
        ),
    ):
        if isinstance(raw.get(key), str) and raw[key] in allowed:
            result[key] = raw[key]

    def number(value):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0 <= value <= 10**9
            and math.isfinite(value)
        )

    for key in ("cpu_threads", "initialization_seconds", "cpu_count", "interop_threads"):
        if number(raw.get(key)):
            result[key] = raw[key]
    if raw.get("synthetic_only") is True:
        result["synthetic_only"] = True
    if type(raw.get("comparison")) is bool:
        result["comparison"] = raw["comparison"]
    timings = raw.get("phase_seconds")
    if isinstance(timings, dict):
        result["phase_seconds"] = {
            key: value for key, value in timings.items() if key in PHASES and number(value)
        }
    result["samples"] = []
    for item in (raw.get("samples") if isinstance(raw.get("samples"), list) else [])[:16]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("voice_kind"), str)
            or item["voice_kind"] not in {"female", "male"}
        ):
            continue
        row = {"voice_kind": item["voice_kind"]}
        if isinstance(item.get("case_kind"), str) and item["case_kind"] in {"short", "long"}:
            row["case_kind"] = item["case_kind"]
        for key in (
            "round",
            "seconds",
            "cpu_threads",
            "pcm_max_delta",
            "pcm_rms_delta",
            "pcm_reference_rms",
        ):
            if number(item.get(key)):
                row[key] = item[key]
        if type(item.get("repeat_pcm_equal")) is bool or item.get("repeat_pcm_equal") is None:
            row["repeat_pcm_equal"] = item.get("repeat_pcm_equal")
        counters = item.get("counters") if isinstance(item.get("counters"), dict) else {}
        operations = item.get("operations") if isinstance(item.get("operations"), dict) else {}
        row["counters"] = {
            key: value
            for key, value in counters.items()
            if key in {"speech_samples", "speech_characters"} and number(value)
        }
        row["operations"] = {
            key: {
                field: value
                for field, value in values.items()
                if field in {"count", "seconds"} and number(value)
            }
            for key, values in operations.items()
            if key in {"speech_pipeline", "speech_write"} and isinstance(values, dict)
        }
        result["samples"].append(row)
    return result


def pcm_difference(reference: bytes, candidate: bytes) -> dict:
    """Numeric PCM16 deltas, not a perceptual-quality verdict."""
    if len(reference) != len(candidate) or len(reference) % 2:
        raise ValueError("sample_shape_mismatch")
    baseline, current = array("h"), array("h")
    baseline.frombytes(reference)
    current.frombytes(candidate)
    if sys.byteorder != "little":
        baseline.byteswap()
        current.byteswap()
    maximum, squared, reference_squared = 0, 0, 0
    for expected, actual in zip(baseline, current, strict=True):
        delta = expected - actual
        maximum = max(maximum, abs(delta))
        squared += delta * delta
        reference_squared += expected * expected
    count = max(1, len(baseline))
    return {
        "pcm_max_delta": maximum,
        "pcm_rms_delta": round(math.sqrt(squared / count), 6),
        "pcm_reference_rms": round(math.sqrt(reference_squared / count), 6),
    }


def partial_result(raw) -> dict:
    """Salvage fixed measurements only; partial work is never a completed comparison."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    result = {"status": "unavailable", "synthetic_only": True, "samples": []}
    for line in str(raw or "").splitlines():
        try:
            item = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("phase"), str) and item["phase"] in PHASES:
            result["phase"] = item["phase"]
        if isinstance(item.get("phase_seconds"), dict):
            result["phase_seconds"] = item["phase_seconds"]
        if isinstance(item.get("measurement"), dict) and len(result["samples"]) < 16:
            result["samples"].append(item["measurement"])
    return safe_result(result)


def worker(threads: int, progress: dict, *, compare=False) -> dict:
    # Enforce no network, including dependencies that ignore Hub offline flags.
    def deny_network(*_args, **_kwargs):
        raise RuntimeError("offline_profile_network_denied")

    phase_started = time.perf_counter()
    timings = {}

    def phase(name):
        nonlocal phase_started
        now = time.perf_counter()
        previous = progress.get("phase")
        if previous in PHASES:
            timings[previous] = round(timings.get(previous, 0) + now - phase_started, 3)
        phase_started = now
        progress["phase"] = name
        print(
            json.dumps({"phase": name, "phase_seconds": timings}), file=sys.__stdout__, flush=True
        )

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    phase("runtime_import")
    if importlib.util.find_spec("en_core_web_sm") is None:
        raise RuntimeError("offline_language_runtime_unavailable")
    import torch
    from kokoro import KPipeline

    from audiodigest.audio import KokoroAudioRenderer
    from audiodigest.config import AudioSettings, HostSettings
    from audiodigest.progress import ProgressReporter

    torch.set_num_threads(1 if compare else threads)
    # This disposable worker only; never deployment settings. Inter-op stays fixed.
    started = time.perf_counter()
    phase("pipeline_initialization")
    pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device="cpu")
    initialization = time.perf_counter() - started
    renderer = KokoroAudioRenderer(AudioSettings(), HostSettings())
    records = []
    with tempfile.TemporaryDirectory(prefix="tdn-synthetic-profile-") as folder:
        output = Path(folder) / "synthetic.wav"
        for kind, voice in (("female", "af_heart"), ("male", "am_eric")):
            # Warm separately; do not conflate cold model/voice setup with throughput.
            phase("voice_warmup")
            renderer._write_speech_chunk(pipeline, "A fictional pilot.", output, voice=voice)
            cases = [("short", "The fictional pilot remains provisional.")]
            if compare:
                cases.append(
                    (
                        "long",
                        "The fictional research team compared several designs before "
                        "announcing its provisional results. The pilot is small, "
                        "the evidence "
                        "is incomplete, and the next review will test whether the approach "
                        "remains useful under different conditions.",
                    )
                )
            for case, text in cases:
                first_digest = None
                first_pcm = None
                candidates = (1, 2, 1) if compare else (threads, threads)
                for round_index, candidate in enumerate(candidates):
                    phase("speech_measurement")
                    torch.set_num_threads(candidate)
                    reporter = ProgressReporter(interval=0, stream=io.StringIO())
                    reporter.start()
                    torch.manual_seed(123)
                    started = time.perf_counter()
                    try:
                        renderer._write_speech_chunk(pipeline, text, output, voice=voice)
                        elapsed = time.perf_counter() - started
                        profile = reporter.finish("completed")
                    finally:
                        reporter.close()
                    with wave.open(str(output), "rb") as audio:
                        if audio.getsampwidth() != 2 or audio.getnchannels() != 1:
                            raise ValueError("sample_shape_mismatch")
                        pcm = audio.readframes(audio.getnframes())
                    digest = hashlib.sha256(pcm).digest()
                    record = {
                        "voice_kind": kind,
                        "case_kind": case,
                        "round": round_index + 1,
                        "cpu_threads": candidate,
                        "seconds": round(elapsed, 3),
                        "operations": profile["operations"],
                        "counters": profile["counters"],
                        "repeat_pcm_equal": (
                            None if first_digest is None else digest == first_digest
                        ),
                    }
                    if first_pcm is not None:
                        record.update(pcm_difference(first_pcm, pcm))
                    records.append(record)
                    print(json.dumps({"measurement": record}), file=sys.__stdout__, flush=True)
                    if first_digest is None:
                        first_digest = digest
                        first_pcm = pcm
    phase("speech_measurement")  # Account for the last completed measured operation.
    return {
        "status": "completed",
        "synthetic_only": True,
        "comparison": compare,
        "phase_seconds": timings,
        "platform": "windows"
        if sys.platform == "win32"
        else "linux"
        if sys.platform.startswith("linux")
        else "other",
        "cpu_threads": torch.get_num_threads(),
        "cpu_count": os.cpu_count() or 1,
        "interop_threads": torch.get_num_interop_threads(),
        "initialization_seconds": round(initialization, 3),
        "samples": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--timeout", type=int, choices=range(30, 301), default=120)
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Offline 1/2/1 thread comparison, both voices and short/long turns",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        progress = {"phase": "preparation"}
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                result = worker(args.threads, progress, compare=args.compare)
            except Exception as error:
                # Fixed exception classes only; no provider/path/error message is emitted.
                kind = type(error).__name__
                result = {
                    "status": "unavailable",
                    "reason": "offline_runtime_or_cache_unavailable",
                    "phase": progress["phase"],
                    "error_kind": kind
                    if kind
                    in {
                        "ImportError",
                        "ModuleNotFoundError",
                        "RuntimeError",
                        "OSError",
                        "FileNotFoundError",
                        "LocalEntryNotFoundError",
                        "ValueError",
                    }
                    else "other",
                }
        print(json.dumps(result))
        return 0 if result["status"] == "completed" else 2
    # Only runtime/cache locations are inherited, never application or provider secrets.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "HF_HUB_CACHE",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment.update(
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1"
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                "--threads",
                str(args.threads),
                *(["--compare"] if args.compare else []),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            timeout=args.timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        result = safe_result(json.loads(completed.stdout.splitlines()[-1]))
        if result["status"] != "completed":
            result = {
                **partial_result(completed.stdout),
                **result,
                "samples": partial_result(completed.stdout)["samples"],
            }
    except subprocess.TimeoutExpired as error:
        result = {
            **partial_result(error.stdout),
            "status": "incomplete",
            "reason": "bounded_profile_timeout",
        }
    except (OSError, ValueError, IndexError):
        result = {"status": "unavailable", "reason": "offline_runtime_or_cache_unavailable"}
    print(json.dumps(result))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
