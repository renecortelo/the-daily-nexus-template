import json
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from audiodigest.antigravity_client import (
    AntigravityCLI,
    AntigravityConfigurationError,
    AntigravityPaymentRiskError,
    _json_from_response,
    _response_from_cli_output,
    assert_safe_antigravity_settings,
    enforce_safe_antigravity_settings,
)
from audiodigest.config import AntigravitySettings


class AntigravityParsingTests(TestCase):
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
            self.assertFalse(
                assert_safe_antigravity_settings(settings)["useG1Credits"]
            )

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
            self.assertFalse(
                settings.settings_path.read_bytes().startswith(b"\xef\xbb\xbf")
            )

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
                envelope = json.loads(requests[0].read_text(encoding="utf-8"))
                self.assertEqual(envelope["payload"], {"source": "fixture"})
                self.assertIn("--sandbox", command)
                self.assertIn("--add-dir", command)
                self.assertIn("--output-format", command)
                self.assertIn("--agent", command)
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
                payloads.append(
                    json.loads(request.read_text(encoding="utf-8"))["payload"]
                )
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
