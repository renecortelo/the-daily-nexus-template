import io
import json
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from audiodigest.antigravity_client import (
    AntigravityCLI,
    AntigravityCLIError,
    AntigravityConfigurationError,
    AntigravityPaymentRiskError,
    _json_from_response,
    _response_from_cli_output,
    _validation_code,
)
from audiodigest.cli_protocol import CLI_FAILURE_CODES, provider_failure
from audiodigest.config import AntigravitySettings
from audiodigest.editorial import _stories_validator
from audiodigest.evidence import SourceEvidenceIndex
from audiodigest.extraction_contract import normalize_extraction
from audiodigest.models import DataValidationError, source_prompt_dicts
from audiodigest.progress import FAILURE_CODES, ProgressReporter, safe_event
from tests.test_source_evidence import extracted_story, synthetic_source

GOOD = json.dumps({"status": "SUCCESS", "response": '{"approved":true}'})


class CLIProtocolTests(TestCase):
    def _call(self, outputs, *, validator=None, remaining=None, retries=1):
        """Completely synthetic provider; payloads never leave a disposable workspace."""
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            agent = root / "agent.md"
            agent.write_text("Read only synthetic fixtures.", encoding="utf-8")
            settings = AntigravitySettings(
                executable="unused", workspace_dir=root / "workspace",
                settings_path=root / "settings.json", agent_path=agent,
            )
            settings.settings_path.write_text('{"useG1Credits":false,"enableTelemetry":false}')
            payloads, responses = [], iter(outputs)
            def run(command, **kwargs):
                request = next(Path(kwargs["cwd"]).glob("request-*.json"))
                payloads.append(json.loads(request.read_text())["payload"])
                code, stdout, stderr = next(responses)
                return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)
            output = io.StringIO()
            reporter = ProgressReporter(interval=0, stream=output)
            reporter.start()
            error, result = None, None
            validator = validator or Mock(side_effect=lambda value: value)
            try:
                with (patch("audiodigest.antigravity_client.subprocess.run", side_effect=run),
                      patch("audiodigest.antigravity_client.remaining_time",
                            return_value=remaining),
                      patch("sys.stdout", output)):
                    try:
                        result, _ = AntigravityCLI(settings).invoke(
                            "Return synthetic JSON.", {"source": "synthetic-original"}, validator,
                            retries=retries,
                        )
                    except AntigravityCLIError as exc:
                        error = exc
                profile = reporter.finish("failed" if error else "completed")
            finally:
                reporter.close()
            self.assertEqual([], list(settings.workspace_dir.glob("request-*.json")))
            return result, error, payloads, profile, output.getvalue(), validator

    def test_envelope_failures_have_fixed_categories_without_provider_values(self):
        cases = [
            ("", "cli_empty"), ("[]", "cli_shape"),
            ('{"status":"synthetic-private-provider-detail"}', "cli_status"),
            ('{"response":null}', "cli_response_missing"),
            ('{"error":{"message":"synthetic-private-provider-detail"}}', "cli_error"),
            ('{"status":"ERROR","status":"SUCCESS"}', "cli_shape"),
            ('{"denied_actions":["synthetic-private-provider-detail"]}', "cli_action_denied"),
        ]
        for output, code in cases:
            with self.subTest(code=code), self.assertRaises(AntigravityCLIError) as rejected:
                _response_from_cli_output(output, elapsed_ms=1)
            self.assertEqual(code, _validation_code(rejected.exception))
            self.assertEqual("cli", rejected.exception.diagnostic["layer"])
            self.assertNotIn("synthetic-private", str(rejected.exception))

    def test_only_explicit_structured_codes_determine_provider_category(self):
        for wrapper, expected in (
            ({"error": {"code": 401}}, "cli_auth"),
            ({"error": {"status": "PERMISSION_DENIED"}}, "cli_access"),
            ({"error": {"code": 429}}, "cli_rate_limit"),
            ({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}, "cli_quota"),
            ({"error": {"status": "QUOTA_EXCEEDED"}}, "cli_quota"),
            ({"error": {"code": 503}}, "cli_unavailable"),
            ({"error": {"status": "INVALID_ARGUMENT"}}, "cli_request"),
            ({"error": {"code": 402}}, "billing"),
            ({"error": {"message": "permission denied in a private example"}}, "cli_error"),
            ({"code": True, "status": []}, "cli_error"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(expected, provider_failure(wrapper)[0])
        self.assertTrue(CLI_FAILURE_CODES <= FAILURE_CODES)
        self.assertEqual(("cli_error", 418), provider_failure({"error": {"code": 418}}))
        self.assertEqual(("cli_quota", 429), provider_failure({
            "error": {"code": 429, "status": "RESOURCE_EXHAUSTED"},
        }))
        self.assertEqual(("cli_error", None), provider_failure({"error": {"code": "999"}}))

    def test_optional_bad_usage_does_not_crash_or_bypass_body_validation(self):
        for usage in ({"input_tokens": float("nan"), "output_tokens": float("inf")},
                      {"input_tokens": -1, "output_tokens": 10**30}, ["synthetic-private"]):
            with self.subTest(usage=usage):
                response, metadata = _response_from_cli_output(
                    json.dumps({"usage": usage, "response": '{"approved":true}'}), elapsed_ms=10,
                )
                self.assertTrue(_json_from_response(response)["approved"])
                self.assertEqual(0, metadata.input_tokens)
                self.assertEqual(0, metadata.output_tokens)
        with self.assertRaises(DataValidationError):
            response, _ = _response_from_cli_output(
                '{"usage":{"output_tokens":NaN},"response":"{\\"score\\":NaN}"}', elapsed_ms=1,
            )
            _json_from_response(response)

    def test_transient_cli_failure_retries_unchanged_request_without_editorial_repair(self):
        for first in ("", '{"error":{"code":503,"message":"synthetic-private-detail"}}',
                      '{"response":null}', '[]'):
            with self.subTest(first=first):
                result, error, payloads, profile, output, validator = self._call(
                    [(0, first, ""), (0, GOOD, "")],
                )
                self.assertIsNone(error)
                self.assertTrue(result["approved"])
                self.assertEqual(2, len(payloads))
                self.assertEqual(payloads[0], payloads[1])
                self.assertNotIn("previous_response", payloads[1])
                self.assertNotIn("repair_plan", payloads[1])
                self.assertEqual(1, validator.call_count)
                self.assertEqual(1, profile["counters"]["cli_recoveries"])
                self.assertEqual(1, profile["counters"]["cli_retries"])
                self.assertNotIn("model_validation_failures", profile["counters"])
                history = profile["validation_history"][0]
                self.assertEqual("cli", history["layer"])
                self.assertEqual(0, history["exit_code"])
                self.assertGreaterEqual(history["stdout_bytes"], 0)
                self.assertNotIn("failure_code", profile)
                self.assertNotIn("synthetic-private", output)

    def test_cli_recovery_then_missing_local_story_id_accepts_supported_story(self):
        source = synthetic_source()
        evidence = SourceEvidenceIndex([source], source_prompt_dicts([source]))
        row = extracted_story()
        del row["story_id"]

        def validate(data):
            normalized, repairs = normalize_extraction(data, ("AI",))
            self.assertEqual(1, repairs)
            return _stories_validator(
                normalized, allowed_sections=("AI",),
                validate_source=lambda story, raw: evidence.bind(story, raw),
            )

        result, error, payloads, profile, output, _ = self._call(
            [(0, '{"error":{"message":"synthetic-private-provider-detail"}}', ""),
             (0, json.dumps({"status": "SUCCESS", "response": json.dumps({"stories": [row]})}),
              "")], validator=validate,
        )
        self.assertIsNone(error)
        self.assertEqual("extracted-0001", result[0].story_id)
        self.assertEqual("source-0001:mail:002", result[0].evidence[0].passage_id)
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(2, len(payloads))
        self.assertEqual(1, profile["counters"]["cli_recoveries"])
        self.assertNotIn("model_validation_failures", profile["counters"])
        self.assertNotIn("synthetic-private", output)

    def test_permanent_cli_failures_stop_after_first_call_and_never_validate_partial_text(self):
        for error_body, code in (({"code": 401}, "cli_auth"),
                                 ({"code": 403}, "cli_access"),
                                 ({"status": "QUOTA_EXCEEDED"}, "cli_quota"),
                                 ({"code": 400}, "cli_request")):
            for exit_code in (0, 1):
                with self.subTest(code=code, exit_code=exit_code):
                    wrapper = json.dumps({"error": error_body, "response": '{"approved":true}'})
                    _, error, payloads, profile, output, validator = self._call(
                        [(exit_code, wrapper, "synthetic-private-stderr")],
                    )
                    self.assertEqual(code, error.failure_code)
                    self.assertEqual(1, len(payloads))
                    validator.assert_not_called()
                    self.assertNotIn("cli_retries", profile["counters"])
                    self.assertNotIn("synthetic-private", output + str(error))

    def test_cli_failure_does_not_reset_existing_valid_editorial_repair_context(self):
        validator = Mock(side_effect=[DataValidationError("synthetic-reject"), {"approved": True}])
        _, error, payloads, profile, _, _ = self._call(
            [(0, GOOD, ""), (0, '{"error":{"code":503}}', "")], validator=validator,
        )
        self.assertEqual("cli_unavailable", error.failure_code)
        self.assertEqual(2, len(payloads))  # Both failure classes share one existing retry.
        self.assertIn("previous_response", payloads[1])
        self.assertEqual(["editorial", "cli"], [h["layer"] for h in profile["validation_history"]])
        self.assertEqual(1, validator.call_count)

    def test_repeated_cli_failures_and_short_remaining_budget_never_loop(self):
        for remaining, calls in ((None, 2), (20, 1)):
            with self.subTest(remaining=remaining):
                _, error, payloads, profile, _, validator = self._call(
                    [(0, "", ""), (0, "", "")], remaining=remaining,
                )
                self.assertEqual("cli_empty", error.failure_code)
                self.assertEqual(calls, len(payloads))
                self.assertEqual(calls, len(profile["validation_history"]))
                validator.assert_not_called()

    def test_invalid_retry_configuration_never_starts_a_provider_call(self):
        for retries in (True, 2, -1, "1", None):
            with self.subTest(retries=retries):
                _, error, payloads, _, _, validator = self._call([], retries=retries)
                self.assertIsInstance(error, AntigravityConfigurationError)
                self.assertEqual([], payloads)
                validator.assert_not_called()

    def test_partial_denied_and_billing_results_never_accept_supplied_content(self):
        for wrapper, stderr, expected in (
            (GOOD, "print-timeout", "cli_incomplete"),
            (json.dumps({"denied_actions": ["blocked"], "response": '{"approved":true}'}),
             "", "cli_action_denied"),
            ('{"error":{"code":402,"message":"synthetic-private"}}', "", "billing"),
            ('{"error":{"message":"buy AI credits synthetic-private"}}', "", "billing"),
            ('{"error":{"code":402},"denied_actions":["blocked"]}', "", "billing"),
        ):
            with self.subTest(expected=expected):
                _, error, payloads, _, output, validator = self._call(
                    [(0, wrapper, stderr), (0, wrapper, stderr)],
                )
                self.assertIsNotNone(error)
                validator.assert_not_called()
                if expected == "billing":
                    self.assertIsInstance(error, AntigravityPaymentRiskError)
                    self.assertEqual(1, len(payloads))
                else:
                    self.assertEqual(expected, error.failure_code)
                self.assertNotIn("synthetic-private", output + str(error))

    def test_telemetry_filters_unknown_layer_status_and_private_metadata(self):
        profile = ProgressReporter(interval=0, stream=io.StringIO()).snapshot()
        profile["validation_history"] = [{
            "code": "cli_error", "stage": 3, "attempt": 1, "elapsed_seconds": 5,
            "layer": "cli", "field": "cli_output", "rule": "cli_error",
            "stdout_bytes": 12, "stderr_bytes": 4, "exit_code": 0,
            "http_status": 503, "status": "synthetic-private", "message": "synthetic-private",
            "raw": "synthetic-private", "account": "synthetic-private",
        }]
        clean = safe_event(profile)
        self.assertEqual("cli", clean["validation_history"][0]["layer"])
        self.assertEqual(503, clean["validation_history"][0]["http_status"])
        self.assertNotIn("synthetic-private", json.dumps(clean))
        profile["validation_history"][0]["layer"] = "synthetic-private"
        self.assertNotIn("layer", safe_event(profile)["validation_history"][0])
