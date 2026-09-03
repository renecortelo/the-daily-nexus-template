import os
from pathlib import Path
from unittest import TestCase

from audiodigest.launcher import (
    FORBIDDEN_ENVIRONMENT_VARIABLES,
    build_publish_command,
    build_run_command,
    sanitized_environment,
)


class LauncherTests(TestCase):
    def test_environment_removes_all_paid_credentials(self):
        source = {
            "PATH": "existing",
            "SAFE_VALUE": "keep-me",
            **{name: "forbidden" for name in FORBIDDEN_ENVIRONMENT_VARIABLES},
        }
        result = sanitized_environment(source, Path("C:/AudioDigest"))
        self.assertEqual("keep-me", result["SAFE_VALUE"])
        self.assertEqual("1", result["PYTHONUTF8"])
        self.assertTrue(result["PATH"].endswith(f"{os.pathsep}existing"))
        for name in FORBIDDEN_ENVIRONMENT_VARIABLES:
            self.assertNotIn(name, result)

    def test_local_only_command_always_has_dry_run(self):
        command = build_run_command(
            "python.exe",
            Path("config.toml"),
            "2026-07-25",
            local_only=True,
        )
        self.assertIn("--dry-run", command)
        self.assertEqual("2026-07-25", command[-2])

    def test_publish_command_respects_configuration(self):
        command = build_run_command(
            "python.exe",
            Path("config.toml"),
            "2026-07-25",
            local_only=False,
        )
        self.assertNotIn("--dry-run", command)

    def test_manual_publish_command_targets_selected_date(self):
        command = build_publish_command(
            "python.exe",
            Path("config.toml"),
            "2026-07-27",
        )
        self.assertEqual("publish", command[-3])
        self.assertEqual("2026-07-27", command[-1])
