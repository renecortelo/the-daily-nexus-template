from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from audiodigest.cli_protocol import RECOVERABLE_CLI_CODES, provider_failure
from audiodigest.config import AntigravitySettings
from audiodigest.draft_repairs import draft_repair_plan
from audiodigest.execution_budget import operation_timeout, remaining_time
from audiodigest.models import AntigravityMetadata, DataValidationError
from audiodigest.private_store import (
    PrivateStoreError,
    assert_private_directory,
    delete_private_value,
    write_private_value,
)
from audiodigest.progress import (
    FAILURE_CODES,
    add_metric,
    clear_failure_code,
    increment,
    record_failure_code,
    record_validation,
    timed_operation,
)
from audiodigest.validation_diagnostics import safe_detail, value_kind

T = TypeVar("T")


class AntigravityCLIError(RuntimeError):
    def __init__(self, message: str, *, failure_code: str = "", diagnostic: dict | None = None):
        super().__init__(message)
        self.failure_code = failure_code
        self.diagnostic = safe_detail(diagnostic)


def _cli_failure(code: str, *, field="cli_output", rule="cli_error", **detail):
    return AntigravityCLIError(
        f"Antigravity CLI result rejected: {code}; provider text withheld for privacy",
        failure_code=code,
        diagnostic={"layer": "cli", "field": field, "rule": rule, **detail},
    )


def _provider_error(wrapper: dict) -> AntigravityCLIError:
    code, http_status = provider_failure(wrapper)
    if code == "billing":
        raise AntigravityPaymentRiskError("Antigravity requested payment or credits; run aborted")
    detail = {} if http_status is None else {"http_status": http_status}
    return _cli_failure(code, **detail)


def _validation_code(exc: Exception) -> str:
    explicit = getattr(exc, "failure_code", "")
    if isinstance(explicit, str) and explicit in FAILURE_CODES:
        return explicit
    if isinstance(exc, json.JSONDecodeError) or "not valid json" in str(exc).casefold():
        return "json_invalid"
    text = str(exc).casefold()
    for terms, code in (
        (("credit editor",), "script_editor_credit"),
        (("reproduce the selected quotation",), "script_quote_text"),
        (("name the quotation author",), "script_quote_author"),
        (("include the closing quotation source",), "script_quote_source"),
        (("closing_comment",), "script_closing_comment"),
        (("partial", "truncated", "timed-out"), "model_incomplete"),
        (("unsupported story",), "script_unsupported_reference"),
        (("omits required",), "script_coverage"),
        (("host", "conversation turn"), "script_hosts"),
        (("quotation", "sign-off", "credit editor"), "script_closing"),
        (("the maximum", "maximum is", "word count"), "script_length"),
    ):
        if any(term in text for term in terms):
            return code
    return "model_structure"


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


def _shared_settings_path(settings: AntigravitySettings) -> Path | None:
    # Only the canonical CLI layout has a shared Antigravity profile. Custom
    # settings paths used by isolated deployments/tests must not touch home.
    if settings.settings_path.parent.name != "antigravity-cli":
        return None
    return settings.settings_path.parent.parent / "config" / "config.json"


def _safe_shared_settings(settings: AntigravitySettings, *, enforce: bool) -> None:
    path = _shared_settings_path(settings)
    if path is None:
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise AntigravityConfigurationError("Shared Antigravity settings are unreadable") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("userSettings", {}), dict):
        raise AntigravityConfigurationError("Shared Antigravity settings must be an object")
    preferences = raw.setdefault("userSettings", {})
    for key in ("useAiCredits", "useG1Credits", "telemetryEnabled", "enableTelemetry"):
        if preferences.get(key) is True:
            if key in {"useAiCredits", "useG1Credits"}:
                raise AntigravityPaymentRiskError("Shared Antigravity AI credits enabled; aborted")
            raise AntigravityConfigurationError("Shared Antigravity telemetry enabled; aborted")
        if not enforce and preferences.get(key) is not False:
            raise AntigravityConfigurationError("Shared Antigravity safety flags are not explicit")
    if enforce and any(
        preferences.get(key) is not False
        for key in ("useAiCredits", "useG1Credits", "telemetryEnabled", "enableTelemetry")
    ):
        preferences.update(
            useAiCredits=False, useG1Credits=False, telemetryEnabled=False, enableTelemetry=False
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


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
    _safe_shared_settings(settings, enforce=False)
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
        raise AntigravityPaymentRiskError("Antigravity useG1Credits is enabled; run aborted")
    if raw.get("enableTelemetry") is True:
        raise AntigravityConfigurationError("Antigravity enableTelemetry is enabled; run aborted")

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
    _safe_shared_settings(settings, enforce=True)
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
    def object_fields(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise DataValidationError("JSON object contains duplicate keys",
                                          failure_code="json_invalid", diagnostic={
                                              "field": "root", "rule": "json_duplicate_key",
                                          })
            result[key] = item
        return result

    def invalid_constant(_value):
        raise DataValidationError("JSON must not contain non-finite numeric constants",
                                  failure_code="json_invalid", diagnostic={
                                      "field": "root", "rule": "json_nonfinite",
                                  })

    try:
        parsed = json.loads(text, object_pairs_hook=object_fields, parse_constant=invalid_constant)
    except json.JSONDecodeError as exc:
        raise DataValidationError(
            "Antigravity response was not valid JSON", failure_code="json_invalid",
            diagnostic={"field": "root", "json_line": exc.lineno,
                        "json_column": exc.colno, "json_position": exc.pos, "rule": "json_syntax"},
        ) from exc
    if not isinstance(parsed, dict):
        raise DataValidationError("Antigravity response must be a JSON object",
                                  failure_code="model_object_shape", diagnostic={"field": "root"})
    return parsed


def _int_value(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and 0 <= value <= 10**9):
            return int(value)
        if value is not None:
            # Usage is optional telemetry, never permission to accept invalid content.
            # NaN/Infinity/huge values previously crashed int() before body validation.
            increment("cli_usage_discarded")
    return 0


def _metadata_from_wrapper(
    wrapper: dict[str, Any],
    *,
    elapsed_ms: int,
) -> AntigravityMetadata:
    usage = wrapper.get("usage", wrapper.get("stats", {}))
    if not isinstance(usage, dict):
        increment("cli_usage_discarded")
        usage = {}
    model = wrapper.get("model", usage.get("model", ""))
    return AntigravityMetadata(
        model=model if isinstance(model, str) else "",
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
        raise _cli_failure("cli_empty", rule="cli_empty")
    def wrapper_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise _cli_failure("cli_shape", rule="cli_duplicate_key")
            result[key] = value
        return result
    try:
        parsed = json.loads(clean, object_pairs_hook=wrapper_fields)
    except json.JSONDecodeError:
        return clean, AntigravityMetadata(latency_ms=elapsed_ms)
    except (ValueError, RecursionError):
        raise _cli_failure("cli_shape", rule="cli_object") from None
    if not isinstance(parsed, dict):
        raise _cli_failure("cli_shape", rule="cli_object", value_kind=value_kind(parsed))
    if parsed.get("error"):
        error = str(parsed["error"])
        if any(pattern in error.lower() for pattern in PAYMENT_RISK_PATTERNS):
            raise AntigravityPaymentRiskError(
                "Antigravity requested payment or credits; run aborted"
            )
        # CLI errors can contain account details or source excerpts. Keep these
        # out of process monitors, Actions logs and public diagnostic reports.
        raise _provider_error(parsed)
    if parsed.get("denied_actions"):
        raise _cli_failure("cli_action_denied", rule="cli_action_denied")
    raw_status = parsed.get("status", "")
    status = raw_status.strip().upper() if isinstance(raw_status, str) else "INVALID"
    if status and status not in {"SUCCESS", "COMPLETED", "OK"}:
        failure = _provider_error(parsed)
        if failure.failure_code == "cli_error":
            failure = _cli_failure("cli_status", field="cli_status", rule="cli_unsuccessful",
                                   value_kind=value_kind(raw_status))
        raise failure
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
        raise _cli_failure("cli_response_missing", field="cli_response", rule="cli_text_missing")
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
        agent_dir = self.settings.workspace_dir / ".agents" / "agents" / self.settings.agent_name
        agent_destination = agent_dir / "agent.md"
        request_path = self.settings.workspace_dir / f"request-{uuid.uuid4().hex}.json"
        try:
            assert_private_directory(agent_dir)
            write_private_value(
                agent_destination, self.settings.agent_path.read_text(encoding="utf-8"),
            )
            request = json.dumps(
                {"instruction": instruction, "payload": payload},
                ensure_ascii=False, separators=(",", ":"),
            )
            write_private_value(
                request_path, request,
                maximum_bytes=16 * 1024 * 1024,
            )
            add_metric("model_request_bytes", len(request.encode("utf-8")))
        except (OSError, PrivateStoreError) as exc:
            raise AntigravityConfigurationError(
                "Could not secure the editorial request file"
            ) from exc
        return request_path

    def invoke(
        self,
        instruction: str,
        payload: dict[str, Any],
        validator: Callable[[dict[str, Any]], T],
        *,
        retries: int = 1,
    ) -> tuple[T, AntigravityMetadata]:
        if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 1:
            raise AntigravityConfigurationError("At most one editorial/CLI retry is permitted")
        enforce_safe_antigravity_settings(self.settings)
        env = dict(os.environ)
        env.update({"AGY_CLI_HIDE_ACCOUNT_INFO": "1", "DO_NOT_TRACK": "1"})
        for name in (
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "VERTEX_AI_PROJECT",
            "TDN_GMAIL_TOKEN_JSON",
            "TDN_FIREBASE_REFRESH_TOKEN",
            "TDN_FIREBASE_DEPLOY_TOKEN",
            "TDN_FIREBASE_OWNER_UID",
            "TDN_FIREBASE_API_KEY",
            "TDN_FIREBASE_PROJECT_ID",
            "TDN_FIREBASE_SECRET_PATH",
            "TDN_ANTIGRAVITY_KEYRING_JSON",
            "TDN_CLOUD_CLOCK_URL",
            "TDN_CLOUD_CLOCK_DISPATCH_SECRET",
            "FIREBASE_TOKEN",
            "GITHUB_TOKEN",
            "GH_TOKEN",
        ):
            env.pop(name, None)

        current_payload = payload
        last_error: Exception | None = None
        validation_failed = False
        cli_failed = False
        for attempt in range(retries + 1):
            if attempt:
                increment("model_retries")
            rejected_response: dict[str, Any] | None = None
            request_path = self._prepare_workspace(instruction, current_payload)
            started = time.perf_counter()
            try:
                with timed_operation("model"):
                    completed = subprocess.run(
                        self._command(request_path),
                        cwd=self.settings.workspace_dir,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        capture_output=True,
                        timeout=operation_timeout(self.settings.timeout_seconds + 30),
                        check=False,
                        env=env,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            except subprocess.TimeoutExpired:
                record_failure_code("model_timeout")
                record_validation("model_timeout", attempt + 1, {
                    "layer": "cli", "field": "cli_output", "rule": "cli_timeout",
                    "attempt_ms": int((time.perf_counter() - started) * 1000),
                })
                raise
            finally:
                delete_private_value(request_path)
                enforce_safe_antigravity_settings(self.settings)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            sizes = {
                "stdout_bytes": len(completed.stdout.encode("utf-8")),
                "stderr_bytes": len(completed.stderr.encode("utf-8")),
            }
            add_metric("cli_stdout_bytes", sizes["stdout_bytes"])
            add_metric("cli_stderr_bytes", sizes["stderr_bytes"])
            cli_error = None
            if completed.returncode != 0:
                detail = f"{completed.stderr}\n{completed.stdout}".strip()
                if any(pattern in detail.lower() for pattern in PAYMENT_RISK_PATTERNS):
                    raise AntigravityPaymentRiskError(
                        "Antigravity requested billing, paid credentials, or credits; run aborted"
                    )
                # Exit failures never permit partial content, even if stdout looks valid.
                try:
                    wrapper = json.loads(_ANSI_ESCAPE.sub("", completed.stdout).strip())
                except (ValueError, TypeError, RecursionError):
                    wrapper = {}
                cli_error = _provider_error(wrapper)
                if cli_error.failure_code == "cli_error":
                    cli_error = _cli_failure("model_exit", rule="cli_exit")
            else:
                try:
                    if re.search(
                        r"\b(?:truncated|truncation|print[- ]timeout|timed out|timeout expired)\b",
                        completed.stderr, re.IGNORECASE,
                    ):
                        raise _cli_failure("cli_incomplete", rule="cli_partial")
                    response, metadata = _response_from_cli_output(
                        completed.stdout, elapsed_ms=elapsed_ms,
                    )
                except AntigravityPaymentRiskError:
                    raise
                except AntigravityCLIError as exc:
                    cli_error = exc
                except (ValueError, OverflowError, RecursionError):
                    cli_error = _cli_failure("cli_shape", rule="cli_object")
            if cli_error is not None:
                cli_failed = True
                code = _validation_code(cli_error)
                detail = {**cli_error.diagnostic, **sizes, "attempt_ms": elapsed_ms}
                if completed.returncode >= 0:
                    detail["exit_code"] = completed.returncode
                else:
                    detail["exit_signal"] = -completed.returncode
                add_metric("cli_failed_ms", elapsed_ms)
                increment("cli_failures")
                record_failure_code(code)
                record_validation(code, attempt + 1, detail)
                remaining = remaining_time()  # Global exhaustion still aborts, not another retry.
                if (code not in RECOVERABLE_CLI_CODES or attempt >= retries
                        or remaining is not None and remaining < 60):
                    raise cli_error
                increment("cli_retries")
                print(f"Antigravity CLI unavailable ({code}); retrying the same request "
                      "within the existing attempt budget, without draft repair.", flush=True)
                # Keep current_payload exactly: no invented draft or provider error in the prompt.
                continue
            try:
                add_metric("model_response_bytes", len(response.encode("utf-8")))
                rejected_response = _json_from_response(response)
                result = validator(rejected_response)
                if validation_failed:
                    # Count recovered calls, not a guarantee of factual approval.
                    increment("model_validation_recoveries")
                if cli_failed:
                    increment("cli_recoveries")
                clear_failure_code()
                add_metric("model_accepted_ms", elapsed_ms)
                return result, metadata
            except AntigravityPaymentRiskError:
                raise
            except (
                json.JSONDecodeError,
                DataValidationError,
                AntigravityCLIError,
                ValueError,
            ) as exc:
                validation_failed = True
                add_metric("model_rejected_ms", elapsed_ms)
                increment("model_validation_failures")
                failure_code = _validation_code(exc)
                record_failure_code(failure_code)
                detail = dict(getattr(exc, "diagnostic", {}) or {})
                detail.setdefault(
                    "layer", "model_json" if rejected_response is None else "editorial",
                )
                detail["attempt_ms"] = elapsed_ms
                if isinstance(exc, json.JSONDecodeError):
                    detail.update(
                        json_line=exc.lineno, json_column=exc.colno, json_position=exc.pos,
                    )
                record_validation(failure_code, attempt + 1, detail)
                last_error = exc
                if attempt < retries:
                    print(
                        "Antigravity draft needs correction; retrying with the "
                        f"rejected draft ({failure_code}).",
                        flush=True,
                    )
                    current_payload = {
                        **payload,
                        "previous_response": rejected_response,
                        "validation_error": str(exc),
                        "validation_diagnostic": detail,
                        "repair_plan": draft_repair_plan(
                            payload, rejected_response, failure_code, diagnostic=detail,
                        ),
                        "repair_instruction": (
                            "Return corrected JSON only. Preserve valid supported "
                            "material from the rejected response. Change only the rejected "
                            "fields or sections; keep valid story coverage and attribution. "
                            "Never guess missing text in a truncated JSON response. "
                            "Follow repair_plan for the rejected constraint. Respect "
                            "upper word limits; never expand merely to reach a minimum "
                            "or pad, repeat or remove required supported coverage."
                        ),
                    }
        raise AntigravityCLIError(
            f"Antigravity output failed validation: {_validation_code(last_error)}; "
            "raw validation details withheld for privacy",
            failure_code=_validation_code(last_error),
        )
