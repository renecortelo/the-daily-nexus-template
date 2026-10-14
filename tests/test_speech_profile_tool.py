import importlib.util
import io
import json
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "speech_profile_tool", Path(__file__).parents[1] / "scripts/profile-speech-runtime.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SpeechProfileToolTests(TestCase):
    def test_profile_parent_never_inherits_application_secrets_or_forwards_library_output(self):
        tool = load_tool()
        output = io.StringIO()
        with (patch.object(tool.sys, "argv", ["profile", "--timeout", "30"]),
              patch.dict(tool.os.environ, {"TDN_EXAMPLE_SECRET": "synthetic-private",
                                           "GH_TOKEN": "synthetic-private"}),
              patch.object(tool.subprocess, "run", return_value=subprocess.CompletedProcess(
                  [], 0, 'dependency-output\n{"status":"completed","secret":"synthetic-private"}',
                  "synthetic-private",
              )) as run, redirect_stdout(output)):
            self.assertEqual(0, tool.main())
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("TDN_EXAMPLE_SECRET", environment)
        self.assertNotIn("GH_TOKEN", environment)
        self.assertEqual("1", environment["HF_HUB_OFFLINE"])
        self.assertEqual(30, run.call_args.kwargs["timeout"])
        self.assertNotIn("synthetic-private", output.getvalue())
        self.assertNotIn("dependency-output", output.getvalue())

    def test_timeout_retains_fixed_phase_without_private_trace(self):
        tool = load_tool()
        output = io.StringIO()
        error = subprocess.TimeoutExpired([], 30, output=(
            b'synthetic-private\n{"phase":"pipeline_initialization"}\n{"phase":["private"]}'
        ))
        with (patch.object(tool.sys, "argv", ["profile", "--timeout", "30"]),
              patch.object(tool.subprocess, "run", side_effect=error), redirect_stdout(output)):
            self.assertEqual(2, tool.main())
        result = json.loads(output.getvalue())
        self.assertEqual("incomplete", result["status"])
        self.assertEqual("pipeline_initialization", result["phase"])
        self.assertNotIn("private", output.getvalue())

    def test_nested_samples_only_keep_finite_numeric_metrics(self):
        tool = load_tool()
        result = tool.safe_result({"status": "completed", "cpu_threads": True,
                                  "samples": [{"voice_kind": "female", "round": 1,
                                               "seconds": float("inf"),
                                               "counters": {"speech_samples": 24000,
                                                            "secret": "synthetic-private"},
                                               "operations": {"speech_pipeline": {"seconds": 2},
                                                              "synthetic-private": {
                                                                  "seconds": 3}}}]})
        self.assertNotIn("cpu_threads", result)
        self.assertNotIn("seconds", result["samples"][0])
        self.assertEqual(24000, result["samples"][0]["counters"]["speech_samples"])
        self.assertNotIn("synthetic-private", json.dumps(result))
