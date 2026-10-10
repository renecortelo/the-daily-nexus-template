import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from audiodigest.antigravity_client import (
    AntigravityCLI,
    AntigravityCLIError,
    AntigravityConfigurationError,
    AntigravityPaymentRiskError,
    _json_from_response,
    _response_from_cli_output,
    _validation_code,
    assert_safe_antigravity_settings,
    enforce_safe_antigravity_settings,
)
from audiodigest.config import AntigravitySettings


class AntigravityParsingTests(TestCase):
    def test_validation_codes_are_safe_categories_not_exception_text(self):
        self.assertEqual(
            "script_coverage",
            _validation_code(ValueError("script omits required verified story IDs: ['opaque']")),
        )
        self.assertEqual(
            "script_hosts", _validation_code(ValueError("both configured hosts must speak"))
        )
        self.assertEqual(
            "model_structure", _validation_code(ValueError("sensitive arbitrary details"))
        )
        self.assertEqual("json_invalid", _validation_code(json.JSONDecodeError("bad", "", 0)))

    def test_structured_cli_error_does_not_leak_raw_diagnostics(self):
        with self.assertRaises(AntigravityCLIError) as error:
            _response_from_cli_output(
                json.dumps({"error": {"code": "AGY_ERROR", "message": "private detail"}}),
                elapsed_ms=1,
            )
        self.assertNotIn("private detail", str(error.exception))

    def test_closing_diagnostics_distinguish_fixed_conditions_without_private_text(self):
        for message, code in (
            ("the introduction must credit editor and producer Dario Novelli exactly once",
             "script_editor_credit"),
            ("the sign-off must reproduce the selected quotation", "script_quote_text"),
            ("the sign-off must name the quotation author", "script_quote_author"),
            ("show notes must include the closing quotation source", "script_quote_source"),
            ("closing_comment requires a nonempty host and text", "script_closing_comment"),
        ):
            with self.subTest(code=code):
                self.assertEqual(code, _validation_code(ValueError(message + " private detail")))

    def test_failed_status_never_accepts_partial_response(self):
        with self.assertRaises(AntigravityCLIError):
            _response_from_cli_output(
                json.dumps({"status": "ERROR", "response": '{"approved":true}'}),
                elapsed_ms=1,
            )

    def test_structured_billing_error_still_aborts(self):
        with self.assertRaises(AntigravityPaymentRiskError):
            _response_from_cli_output(
                json.dumps({"error": {"message": "buy AI credits"}}),
                elapsed_ms=1,
            )

    def test_denied_tools_do_not_silently_return_a_successful_result(self):
        with self.assertRaises(AntigravityCLIError):
            _response_from_cli_output(
                json.dumps(
                    {
                        "status": "SUCCESS",
                        "response": '{"approved":true}',
                        "denied_actions": ["private tool request"],
                    }
                ),
                elapsed_ms=1,
            )

    def test_json_fence_is_accepted(self):
        self.assertEqual(
            _json_from_response('```json\n{"approved": true}\n```'),
            {"approved": True},
        )

    def test_non_object_is_rejected(self):
        with self.assertRaises(ValueError):
            _json_from_response('["not", "an", "object"]')

    def test_cli_wrapper_response_and_usage_are_extracted(self):
        response, metadata = _response_from_cli_output(
            json.dumps(
                {
                    "response": '{"approved":true}',
                    "model": "Gemini 3.5 Flash (High)",
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 4,
                        "cache_read_tokens": 7,
                    },
                }
            ),
            elapsed_ms=50,
        )
        self.assertEqual(response, '{"approved":true}')
        self.assertEqual(metadata.input_tokens, 20)
        self.assertEqual(metadata.output_tokens, 4)
        self.assertEqual(metadata.cache_read_tokens, 7)
        self.assertEqual(metadata.latency_ms, 50)


class AntigravitySafetyTests(TestCase):
    def test_shared_credit_setting_cannot_override_safe_legacy_settings(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            settings = self._settings(root)
            settings.settings_path = root / "antigravity-cli" / "settings.json"
            settings.settings_path.parent.mkdir()
            settings.settings_path.write_text(
                '{"useG1Credits":false,"enableTelemetry":false}',
                encoding="utf-8",
            )
            shared = root / "config" / "config.json"
            shared.parent.mkdir()
            shared.write_text('{"userSettings":{"useAiCredits":true}}', encoding="utf-8")
            with self.assertRaises(AntigravityPaymentRiskError):
                enforce_safe_antigravity_settings(settings)

    def test_shared_missing_flags_are_filled_without_changing_unrelated_preferences(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            settings = self._settings(root)
            settings.settings_path = root / "antigravity-cli" / "settings.json"
            settings.settings_path.parent.mkdir()
            settings.settings_path.write_text(
                '{"useG1Credits":false,"enableTelemetry":false}',
                encoding="utf-8",
            )
            shared = root / "config" / "config.json"
            shared.parent.mkdir()
            shared.write_text(
                '{"plugins":{},"userSettings":{"themeMode":"dark"}}', encoding="utf-8"
            )
            enforce_safe_antigravity_settings(settings)
            value = json.loads(shared.read_text())
            self.assertEqual(value["userSettings"]["themeMode"], "dark")
            self.assertIs(value["userSettings"]["useAiCredits"], False)
            self.assertIs(value["userSettings"]["telemetryEnabled"], False)

    def _settings(self, root: Path) -> AntigravitySettings:
        agent_path = root / "agent.md"
        agent_path.write_text("---\nname: audio-digest\n---\nRead only.", encoding="utf-8")
        return AntigravitySettings(
            executable="agy",
            workspace_dir=root / "workspace",
            settings_path=root / "settings.json",
            agent_path=agent_path,
        )

    def test_safe_global_settings_are_required(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            settings.settings_path.write_text(
                '{"useG1Credits": false, "enableTelemetry": false}',
                encoding="utf-8",
            )
            self.assertFalse(assert_safe_antigravity_settings(settings)["useG1Credits"])

    def test_missing_telemetry_setting_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            settings.settings_path.write_text(
                '{"useG1Credits": false}',
                encoding="utf-8",
            )
            with self.assertRaises(AntigravityConfigurationError):
                assert_safe_antigravity_settings(settings)

    def test_g1_credits_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            settings.settings_path.write_text(
                '{"useG1Credits": true, "enableTelemetry": false}',
                encoding="utf-8",
            )
            with self.assertRaises(AntigravityPaymentRiskError):
                assert_safe_antigravity_settings(settings)

    def test_cli_normalized_missing_credit_setting_is_restored(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            settings.settings_path.write_text(
                '{"enableTelemetry": false}',
                encoding="utf-8",
            )
            enforced = enforce_safe_antigravity_settings(settings)
            self.assertIs(enforced["useG1Credits"], False)
            self.assertFalse(settings.settings_path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_headless_call_uses_isolated_request_and_removes_it(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            settings = self._settings(root)
            settings.settings_path.write_text(
                '{"useG1Credits": false, "enableTelemetry": false}',
                encoding="utf-8",
            )

            def fake_run(command, **kwargs):
                workspace = Path(kwargs["cwd"])
                requests = list(workspace.glob("request-*.json"))
                self.assertEqual(len(requests), 1)
                if os.name == "posix":
                    self.assertEqual(0o600, requests[0].stat().st_mode & 0o777)
                    self.assertEqual(0o700, workspace.stat().st_mode & 0o777)
                envelope = json.loads(requests[0].read_text(encoding="utf-8"))
                self.assertEqual(envelope["payload"], {"source": "fixture"})
                self.assertIn("--sandbox", command)
                self.assertIn("--add-dir", command)
                self.assertIn("--output-format", command)
                self.assertIn("--agent", command)
                self.assertEqual(kwargs["env"]["AGY_CLI_HIDE_ACCOUNT_INFO"], "1")
                self.assertEqual(kwargs["env"]["DO_NOT_TRACK"], "1")
                self.assertEqual(command[-2], "-p")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "response": '{"approved":true,"issues":[]}',
                            "usage": {"input_tokens": 3, "output_tokens": 2},
                        }
                    ),
                    stderr="",
                )

            client = AntigravityCLI(settings)
            with patch(
                "audiodigest.antigravity_client.subprocess.run",
                side_effect=fake_run,
            ):
                result, metadata = client.invoke(
                    "Return verification JSON.",
                    {"source": "fixture"},
                    lambda value: value,
                    retries=0,
                )
            self.assertTrue(result["approved"])
            self.assertEqual(metadata.output_tokens, 2)
            self.assertEqual(list(settings.workspace_dir.glob("request-*.json")), [])

    def test_child_environment_excludes_unrelated_credentials_but_keeps_cli_auth_transport(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            settings.settings_path.write_text('{"useG1Credits":false,"enableTelemetry":false}')
            blocked = {key: "synthetic-secret" for key in (
                "TDN_GMAIL_TOKEN_JSON", "TDN_FIREBASE_REFRESH_TOKEN", "FIREBASE_TOKEN",
                "GH_TOKEN", "GITHUB_TOKEN", "TDN_FIREBASE_SECRET_PATH",
                "TDN_ANTIGRAVITY_KEYRING_JSON", "TDN_CLOUD_CLOCK_URL",
            )}
            with (
                patch.dict(os.environ, {**blocked, "DBUS_SESSION_BUS_ADDRESS": "test-transport"}),
                patch("audiodigest.antigravity_client.subprocess.run") as run,
            ):
                run.return_value = subprocess.CompletedProcess(
                    [], 0, stdout='{"response":"{\\"approved\\":true}"}', stderr="",
                )
                AntigravityCLI(settings).invoke("Check.", {}, lambda value: value, retries=0)
                child = run.call_args.kwargs["env"]
            self.assertTrue(set(blocked).isdisjoint(child))
            self.assertEqual("test-transport", child["DBUS_SESSION_BUS_ADDRESS"])
            self.assertEqual("synthetic-secret", blocked["GH_TOKEN"])

    def test_timed_out_child_still_removes_private_request(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            settings.settings_path.write_text('{"useG1Credits":false,"enableTelemetry":false}')
            with patch("audiodigest.antigravity_client.subprocess.run",
                       side_effect=subprocess.TimeoutExpired("synthetic-cli", 1)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    AntigravityCLI(settings).invoke("Check.", {}, lambda value: value, retries=0)
            self.assertEqual([], list(settings.workspace_dir.glob("request-*.json")))

    def test_request_over_credential_limit_is_not_clipped(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            client = AntigravityCLI(settings)
            source = "x" * (2 * 1024 * 1024 + 1)
            request = client._prepare_workspace("Check.", {"source": source})
            self.assertEqual(source, json.loads(request.read_text())["payload"]["source"])
            request.unlink()

    def test_failed_private_write_prevents_cli_start(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            settings.settings_path.write_text('{"useG1Credits":false,"enableTelemetry":false}')
            with (
                patch("audiodigest.antigravity_client.write_private_value",
                      side_effect=OSError("synthetic failure")),
                patch("audiodigest.antigravity_client.subprocess.run") as run,
            ):
                with self.assertRaisesRegex(AntigravityConfigurationError, "secure"):
                    AntigravityCLI(settings).invoke("Check.", {}, lambda value: value, retries=0)
                run.assert_not_called()

    def test_linked_workspace_cannot_receive_a_private_request(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            settings = self._settings(root)
            target = root / "keep"
            target.mkdir()
            alias = root / "linked-workspace"
            try:
                alias.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("symbolic links are unavailable")
            settings.workspace_dir = alias
            with self.assertRaises(AntigravityConfigurationError):
                AntigravityCLI(settings)._prepare_workspace("Check.", {"private": "fixture"})
            self.assertEqual([], list(target.iterdir()))

    def test_oversized_request_fails_without_cropping_or_leaving_payload(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            with self.assertRaises(AntigravityConfigurationError):
                AntigravityCLI(settings)._prepare_workspace(
                    "Check.", {"source": "x" * (16 * 1024 * 1024)},
                )
            self.assertEqual([], list(settings.workspace_dir.glob("request-*.json")))

    def test_validation_retry_receives_the_rejected_response(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            settings = self._settings(root)
            settings.settings_path.write_text(
                '{"useG1Credits": false, "enableTelemetry": false}',
                encoding="utf-8",
            )
            payloads = []
            responses = iter(
                [
                    {"approved": False, "issues": ["too short"]},
                    {"approved": True, "issues": []},
                ]
            )

            def fake_run(command, **kwargs):
                request = next(Path(kwargs["cwd"]).glob("request-*.json"))
                payloads.append(json.loads(request.read_text(encoding="utf-8"))["payload"])
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({"response": json.dumps(next(responses))}),
                    stderr="",
                )

            def validator(value):
                if not value["approved"]:
                    raise ValueError("draft is too short")
                return value

            client = AntigravityCLI(settings)
            with patch(
                "audiodigest.antigravity_client.subprocess.run",
                side_effect=fake_run,
            ):
                result, _ = client.invoke(
                    "Return verification JSON.",
                    {"source": "fixture"},
                    validator,
                    retries=1,
                )

            self.assertTrue(result["approved"])
            self.assertEqual(
                {"approved": False, "issues": ["too short"]},
                payloads[1]["previous_response"],
            )
