import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from audiodigest.config import load_settings


class ConfigTests(TestCase):
    def test_loudness_settings_reject_nonfinite_and_unsupported_filter_values(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            path = Path(name) / "config.toml"
            for field, values in (
                ("target_lufs", ("nan", "inf", "-71", "-4")),
                ("true_peak_db", ("nan", "-inf", "-10", "1")),
            ):
                for value in values:
                    path.write_text(f'[audio]\n{field} = "{value}"\n', encoding="utf-8")
                    with (
                        self.subTest(field=field, value=value),
                        self.assertRaisesRegex(ValueError, field),
                    ):
                        load_settings(path)
            path.write_text('[audio]\ntarget_lufs = -18\ntrue_peak_db = -2\n', encoding="utf-8")
            self.assertEqual(-18, load_settings(path).audio.target_lufs)
            self.assertEqual(-2, load_settings(path).audio.true_peak_db)

    def test_optional_local_pronunciation_table_is_validated(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            path = Path(name) / "config.toml"
            path.write_text('[audio.pronunciations]\nKokoro = "kˈOkəɹO"\n', encoding="utf-8")
            self.assertEqual({"Kokoro": "kˈOkəɹO"}, load_settings(path).audio.pronunciations)
            path.write_text('[audio.pronunciations]\nKokoro = "<break/>"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pronunciations"):
                load_settings(path)

    def test_synthesis_speed_defaults_to_normal_and_rejects_extremes(self):
        self.assertEqual(1.0, load_settings("config.example.toml").audio.synthesis_speed)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            path = Path(name) / "config.toml"
            for value in ("0.8", "1.5", "nan", "inf"):
                path.write_text(f'[audio]\nsynthesis_speed = "{value}"\n', encoding="utf-8")
                with self.subTest(value=value), self.assertRaisesRegex(ValueError,
                                                                         "synthesis_speed"):
                    load_settings(path)
            path.write_text('[audio]\nsynthesis_speed = 0.98\n', encoding="utf-8")
            self.assertEqual(0.98, load_settings(path).audio.synthesis_speed)

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
