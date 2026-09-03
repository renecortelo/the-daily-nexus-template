import json
import tempfile
from pathlib import Path
from unittest import TestCase

from audiodigest.config import (
    AntigravitySettings,
    AppSettings,
    ArticleSettings,
    AudioSettings,
    FirebaseSettings,
    GmailSettings,
    PodcastSettings,
    ResearchSettings,
    SafetySettings,
    Settings,
)
from audiodigest.cost_guard import (
    CostSafetyError,
    assert_no_paid_credentials,
    validate_firebase_json,
    validate_spark_confirmation,
    write_spark_confirmation,
)


def settings_for(root: Path) -> Settings:
    runtime = root / "runtime"
    return Settings(
        project_dir=root,
        app=AppSettings(runtime_dir=runtime),
        gmail=GmailSettings(client_secret_path=root / "client_secret.json"),
        antigravity=AntigravitySettings(
            workspace_dir=root / "antigravity-workspace",
            settings_path=root / "antigravity-settings.json",
            agent_path=root / "agent.md",
        ),
        articles=ArticleSettings(),
        research=ResearchSettings(),
        audio=AudioSettings(),
        firebase=FirebaseSettings(
            project_id="safe-project",
            executable="firebase",
            public_dir=root / "hosting",
            base_url="https://safe-project.web.app",
            secret_path="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",  # noqa: S106
            publish_enabled=True,
        ),
        podcast=PodcastSettings(),
        safety=SafetySettings(),
    )


class CostGuardTests(TestCase):
    def test_paid_credential_is_rejected(self):
        with self.assertRaises(CostSafetyError):
            assert_no_paid_credentials({"OPENAI_API_KEY": "not-allowed"})

    def test_empty_environment_is_safe(self):
        assert_no_paid_credentials({})

    def test_cloud_run_rewrite_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            path = Path(name) / "firebase.json"
            path.write_text(
                json.dumps(
                    {
                        "hosting": {
                            "public": "hosting",
                            "rewrites": [{"source": "**", "run": {"serviceId": "paid"}}],
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(CostSafetyError):
                validate_firebase_json(path)

    def test_spark_confirmation_is_bound_to_project(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = settings_for(Path(name))
            write_spark_confirmation(settings)
            validate_spark_confirmation(settings)
            settings.firebase.project_id = "another-project"
            with self.assertRaises(CostSafetyError):
                validate_spark_confirmation(settings)
