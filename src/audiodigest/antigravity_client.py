from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from audiodigest.config import AntigravitySettings
from audiodigest.models import AntigravityMetadata, DataValidationError

T = TypeVar("T")


class AntigravityCLIError(RuntimeError):
    pass


class AntigravityConfigurationError(AntigravityCLIError):
    pass


class AntigravityPaymentRiskError(AntigravityCLIError):
    pass


PAYMENT_RISK_PATTERNS = (
    "set up billing",
    "billing account",
    "buy ai credits",
    "purchase ai credits",
    "use g1 credits",
    "use ai credits",
    "upgrade your plan",
    "pay-as-you-go",
    "vertex ai",
    "api key",
    "insufficient credits",
)

_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def assert_safe_antigravity_settings(settings: AntigravitySettings) -> dict[str, Any]:
    try:
        raw = json.loads(settings.settings_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise AntigravityConfigurationError(
            "Antigravity safety settings are missing. Run scripts\\authenticate.ps1."
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AntigravityConfigurationError(
            f"Could not read Antigravity settings at {settings.settings_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise AntigravityConfigurationError("Antigravity settings.json must be a JSON object")
    if raw.get("useG1Credits") is not False:
        raise AntigravityPaymentRiskError(
            "Antigravity useG1Credits must be explicitly false; run aborted"
        )
    if raw.get("enableTelemetry") is not False:
        raise AntigravityConfigurationError(
            "Antigravity enableTelemetry must be explicitly false; run aborted"
        )
    return raw


def enforce_safe_antigravity_settings(
    settings: AntigravitySettings,
) -> dict[str, Any]:
    try:
        raw = json.loads(settings.settings_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise AntigravityConfigurationError(
            "Antigravity safety settings are missing. Run scripts\\authenticate.ps1."
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AntigravityConfigurationError(
            f"Could not read Antigravity settings at {settings.settings_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise AntigravityConfigurationError("Antigravity settings.json must be a JSON object")
    if raw.get("useG1Credits") is True:
        raise AntigravityPaymentRiskError(
            "Antigravity useG1Credits is enabled; run aborted"
        )
    if raw.get("enableTelemetry") is True:
        raise AntigravityConfigurationError(
            "Antigravity enableTelemetry is enabled; run aborted"
        )

    # Antigravity 1.1.7 normalizes the default false credit setting by removing the
    # property when it saves settings. Restore both explicit values before and after
    # every AudioDigest call so the project's safety contract remains unambiguous.
    if raw.get("useG1Credits") is not False or raw.get("enableTelemetry") is not False:
        raw["useG1Credits"] = False
        raw["enableTelemetry"] = False
        temporary = settings.settings_path.with_name(
            f"{settings.settings_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, settings.settings_path)
        finally:
            temporary.unlink(missing_ok=True)
    return assert_safe_antigravity_settings(settings)


def _json_from_response(value: str) -> dict[str, Any]:
    text = _ANSI_ESCAPE.sub("", value).strip()
    fence = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fence:
        text = fence.group(1)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataValidationError(
            f"Antigravity response was not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise DataValidationError("Antigravity response must be a JSON object")
    return parsed


def _int_value(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return 0


def _metadata_from_wrapper(
    wrapper: dict[str, Any],
    *,
    elapsed_ms: int,
) -> AntigravityMetadata:
    usage = wrapper.get("usage", wrapper.get("stats", {}))
    if not isinstance(usage, dict):
        usage = {}
    model = wrapper.get("model", usage.get("model", ""))
    return AntigravityMetadata(
        model=str(model or ""),
        input_tokens=_int_value(
            usage,
            "input_tokens",
            "inputTokens",
            "prompt_tokens",
            "promptTokens",
        ),
        output_tokens=_int_value(
            usage,
            "output_tokens",
            "outputTokens",
            "completion_tokens",
            "completionTokens",
        ),
        cache_read_tokens=_int_value(
            usage,
            "cache_read_tokens",
            "cacheReadTokens",
        ),
        latency_ms=_int_value(
            usage,
            "latency_ms",
            "latencyMs",
        )
        or elapsed_ms,
    )


def _response_from_cli_output(
    output: str,
    *,
    elapsed_ms: int,
) -> tuple[str, AntigravityMetadata]:
    clean = _ANSI_ESCAPE.sub("", output).strip()
    if not clean:
        raise AntigravityCLIError("Antigravity CLI returned no output")
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        return clean, AntigravityMetadata(latency_ms=elapsed_ms)
    if not isinstance(parsed, dict):
        raise AntigravityCLIError("Antigravity CLI JSON output must be an object")
    if parsed.get("error"):
        error = str(parsed["error"])
        if any(pattern in error.lower() for pattern in PAYMENT_RISK_PATTERNS):
            raise AntigravityPaymentRiskError(error)
        raise AntigravityCLIError(error)
    status = str(parsed.get("status", "")).upper()
    if status and status not in {"SUCCESS", "COMPLETED", "OK"}:
        raise AntigravityCLIError(
            f"Antigravity CLI reported status {status}"
        )
    metadata = _metadata_from_wrapper(parsed, elapsed_ms=elapsed_ms)
    for key in ("response", "result", "output", "text"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value, metadata
        if isinstance(value, dict):
            for nested_key in ("text", "content", "response"):
                nested = value.get(nested_key)
                if isinstance(nested, str) and nested.strip():
                    return nested, metadata
    if any(key in parsed for key in ("response", "result", "output", "text")):
        raise AntigravityCLIError("Antigravity CLI returned no response text")
    # Some CLI builds emit the model's requested JSON object directly.
    return clean, metadata


class AntigravityCLI:
    def __init__(self, settings: AntigravitySettings):
        self.settings = settings

    def _command(self, request_path: Path) -> list[str]:
        command = [
            self.settings.executable,
            "--sandbox",
            "--add-dir",
            str(self.settings.workspace_dir),
            "--agent",
            self.settings.agent_name,
            "--output-format",
            "json",
            "--print-timeout",
            f"{self.settings.timeout_seconds}s",
        ]
        if self.settings.model:
            command.extend(["--model", self.settings.model])
        command.extend(
            [
                "-p",
                (
                    f"Read the complete {str(request_path)!r} file with view_file. "
                    "Follow its instruction using only its payload, then return the requested "
                    "JSON object and nothing else."
                ),
            ]
        )
        return command

    def _prepare_workspace(
        self,
        instruction: str,
        payload: dict[str, Any],
    ) -> Path:
        if not self.settings.agent_path.is_file():
            raise AntigravityConfigurationError(
                f"Antigravity agent definition is missing: {self.settings.agent_path}"
            )
        agent_dir = (
            self.settings.workspace_dir
            / ".agents"
            / "agents"
            / self.settings.agent_name
        )
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_destination = agent_dir / "agent.md"
        if (
            not agent_destination.exists()
            or agent_destination.read_bytes() != self.settings.agent_path.read_bytes()
        ):
            shutil.copy2(self.settings.agent_path, agent_destination)
        request_path = self.settings.workspace_dir / f"request-{uuid.uuid4().hex}.json"
        request_path.write_text(
            json.dumps(
                {"instruction": instruction, "payload": payload},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        return request_path

    def invoke(
        self,
        instruction: str,
        payload: dict[str, Any],
        validator: Callable[[dict[str, Any]], T],
        *,
        retries: int = 1,
    ) -> tuple[T, AntigravityMetadata]:
        enforce_safe_antigravity_settings(self.settings)
        env = dict(os.environ)
        for name in (
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "VERTEX_AI_PROJECT",
        ):
            env.pop(name, None)

        current_payload = payload
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            rejected_response: dict[str, Any] | None = None
            request_path = self._prepare_workspace(instruction, current_payload)
            started = time.perf_counter()
            try:
                completed = subprocess.run(
                    self._command(request_path),
                    cwd=self.settings.workspace_dir,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=self.settings.timeout_seconds + 30,
                    check=False,
                    env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            finally:
                request_path.unlink(missing_ok=True)
                enforce_safe_antigravity_settings(self.settings)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if completed.returncode != 0:
                detail = f"{completed.stderr}\n{completed.stdout}".strip()
                if any(pattern in detail.lower() for pattern in PAYMENT_RISK_PATTERNS):
                    raise AntigravityPaymentRiskError(
                        "Antigravity requested billing, paid credentials, or credits; "
                        "run aborted"
                    )
                last_error = AntigravityCLIError(
                    f"Antigravity CLI failed with exit code {completed.returncode}: "
                    f"{detail[:1000]}"
                )
                continue
            try:
                response, metadata = _response_from_cli_output(
                    completed.stdout,
                    elapsed_ms=elapsed_ms,
                )
                rejected_response = _json_from_response(response)
                return validator(rejected_response), metadata
            except AntigravityPaymentRiskError:
                raise
            except (
                json.JSONDecodeError,
                DataValidationError,
                AntigravityCLIError,
                ValueError,
            ) as exc:
                last_error = exc
                if attempt < retries:
                    print(
                        "Antigravity draft needs correction; retrying with the "
                        f"rejected draft ({exc}).",
                        flush=True,
                    )
                    current_payload = {
                        **payload,
                        "previous_response": rejected_response,
                        "validation_error": str(exc),
                        "repair_instruction": (
                            "Return corrected JSON only. Preserve valid supported "
                            "material from the rejected response. If a word target "
                            "failed, improve useful evidence coverage rather than "
                            "padding or repeating text."
                        ),
                    }
        raise AntigravityCLIError(
            f"Antigravity output failed validation: {last_error}"
        )
