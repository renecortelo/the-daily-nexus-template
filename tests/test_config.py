import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from audiodigest.config import load_settings


class ConfigTests(TestCase):
    def test_antigravity_executable_expands_environment_path(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            config_path = root / "config.toml"
            config_path.write_text(
                """
[antigravity]
executable = "%LOCALAPPDATA%/agy/bin/agy.exe"
use_g1_credits = false
telemetry = false
""".strip(),
                encoding="utf-8",
            )
            local_app_data = root / "LocalAppData"
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(local_app_data)},
            ):
                settings = load_settings(config_path)
            self.assertEqual(
                Path(settings.antigravity.executable),
                local_app_data / "agy" / "bin" / "agy.exe",
            )
