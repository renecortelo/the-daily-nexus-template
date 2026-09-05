import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from audiodigest.cost_guard import validate_firebase_json
from audiodigest.publisher import _copy_static_web_app

TEST_TEMP_ROOT = Path(__file__).resolve().parents[1] / "tmp" / "test-scratch"


@contextmanager
def temporary_test_directory():
    TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TEMP_ROOT / f"case-{uuid4().hex}"
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class WebAppSecurityTests(TestCase):
    def test_web_auth_is_session_only_and_owner_gated(self):
        source = Path("web/app.js").read_text(encoding="utf-8")
        styles = Path("web/styles.css").read_text(encoding="utf-8")
        self.assertIn("browserSessionPersistence", source)
        self.assertNotIn("browserLocalPersistence", source)
        self.assertIn('doc(appState.db, "owners", user.uid)', source)
        self.assertIn("IDLE_LIMIT_MS", source)
        self.assertNotIn("const FIREBASE_HOSTS", source)
        self.assertIn("`${firebaseConfig.projectId}.web.app`", source)
        self.assertIn("appState.firebaseHosts.has(url.hostname)", source)
        self.assertIn("firebaseConfig.authDomain = window.location.hostname", source)
        self.assertIn("[hidden]", styles)
        self.assertIn("display: none !important", styles)

    def test_service_worker_never_caches_private_feed_paths(self):
        source = Path("web/service-worker.js").read_text(encoding="utf-8")
        self.assertNotIn('"/p/', source)
        self.assertNotIn('"/__/', source)
        self.assertIn("STATIC_ASSETS.has(url.pathname)", source)

    def test_web_archive_uses_clickable_cards_with_accessible_reader_controls(self):
        source = Path("web/app.js").read_text(encoding="utf-8")
        page = Path("web/index.html").read_text(encoding="utf-8")
        styles = Path("web/styles.css").read_text(encoding="utf-8")
        self.assertNotIn("OPEN IN READER", page)
        self.assertIn('readCard.setAttribute("role", pdfURL ? "button" : "article")', source)
        self.assertIn('link.title = reference.url', source)
        self.assertIn('id="edition-zoom-in"', page)
        self.assertIn('id="edition-zoom-out"', page)
        self.assertIn(".edition-list-item.selected", styles)

    def test_web_generation_and_schedule_controls_publish_verified_runs(self):
        source = Path("web/app.js").read_text(encoding="utf-8")
        page = Path("web/index.html").read_text(encoding="utf-8")

        self.assertIn("Generate a Podcast + Paper", page)
        self.assertIn(">GENERATE</button>", page)
        self.assertNotIn("QUEUE SECURE RUN", page)
        self.assertNotIn('name="publish"', page)
        self.assertIn("publish: true", source)
        self.assertIn('id="update-profile-button"', page)
        self.assertIn('id="delete-profile-button"', page)
        self.assertIn("function updateFavoriteProfile", source)
        self.assertIn("function deleteFavoriteProfile", source)
        self.assertIn("function deleteRunRequest", source)
        self.assertIn("function toggleScheduleEnabled", source)
        self.assertNotIn("schedule-clock-note", page)
        self.assertNotIn("The web app stores parameters", page)

    def test_schedule_timezone_uses_the_browser_iana_zone(self):
        source = Path("web/app.js").read_text(encoding="utf-8")
        page = Path("web/index.html").read_text(encoding="utf-8")

        self.assertIn("function setupTimezonePicker()", source)
        self.assertIn('Intl.supportedValuesOf("timeZone")', source)
        self.assertIn('resolvedOptions().timeZone || "UTC"', source)
        self.assertIn('timezone: String(data.timezone || "UTC")', source)
        self.assertNotIn('data.timezone || "Europe/Madrid"', source)
        self.assertIn('<option value="UTC">UTC</option>', page)

    def test_firebase_config_remains_static_spark_compatible(self):
        validate_firebase_json(Path("firebase.json"))
        config = json.loads(Path("firebase.json").read_text(encoding="utf-8"))
        self.assertIn("firestore", config)
        self.assertNotIn("functions", config)
        self.assertNotIn("storage", config)
        csp = config["hosting"]["headers"][0]["headers"][0]["value"]
        self.assertIn("https://apis.google.com", csp)
        self.assertIn("frame-src 'self'", csp)

    def test_cloud_clock_boundary_contains_only_timing_and_owner_authentication(self):
        source = Path("web/app.js").read_text(encoding="utf-8")
        worker = Path("cloud-clock/src/index.js").read_text(encoding="utf-8")
        rules = Path("firestore.rules").read_text(encoding="utf-8")
        projection = source[
            source.index("function clockProjection"):
            source.index("function projectionSignature")
        ]
        self.assertIn("clockSchedules", source)
        self.assertIn('credentials: "omit"', source)
        self.assertIn('authorization: `Bearer ${token}`', source)
        self.assertNotIn("gmailLabel", projection)
        self.assertNotIn("parameters", projection)
        self.assertIn("validClockSchedule", rules)
        self.assertIn("getAfter", rules)
        self.assertIn("TDN_GITHUB_DISPATCH_TOKEN", worker)
        self.assertIn("TDN_OWNER_UID", worker)
        for forbidden in (
            "TDN_GMAIL_TOKEN_JSON",
            "TDN_ANTIGRAVITY_KEYRING_JSON",
            "TDN_FIREBASE_REFRESH_TOKEN",
            "TDN_FIREBASE_DEPLOY_TOKEN",
        ):
            self.assertNotIn(forbidden, worker)

    def test_publisher_copies_only_allowlisted_web_assets(self):
        with temporary_test_directory() as name:
            project = Path(name) / "project"
            public = Path(name) / "public"
            (project / "web").mkdir(parents=True)
            (project / "assets").mkdir()
            for filename in (
                "index.html",
                "styles.css",
                "app.js",
                "manifest.webmanifest",
                "service-worker.js",
            ):
                (project / "web" / filename).write_text(filename, encoding="utf-8")
            (project / "web" / "private.txt").write_text("do not copy", encoding="utf-8")
            for filename in ("tdn-icon.png", "tdn-icon-transparent.png", "google-g.png"):
                (project / "assets" / filename).write_bytes(b"image")

            _copy_static_web_app(project, public)

            self.assertTrue((public / "index.html").is_file())
            self.assertTrue((public / "cloud-clock-config.js").is_file())
            self.assertTrue((public / "assets" / "tdn-icon.png").is_file())
            self.assertTrue((public / "assets" / "tdn-icon-transparent.png").is_file())
            self.assertFalse((public / "private.txt").exists())

    def test_publisher_writes_cloud_clock_endpoint_only_from_environment(self):
        with temporary_test_directory() as name:
            project = Path(name) / "project"
            public = Path(name) / "public"
            (project / "web").mkdir(parents=True)
            (project / "assets").mkdir()
            for filename in (
                "index.html", "styles.css", "app.js",
                "manifest.webmanifest", "service-worker.js",
            ):
                (project / "web" / filename).write_text(filename, encoding="utf-8")
            for filename in ("tdn-icon.png", "tdn-icon-transparent.png", "google-g.png"):
                (project / "assets" / filename).write_bytes(b"image")
            with patch.dict(
                os.environ,
                {"TDN_CLOUD_CLOCK_URL": "https://private-clock.example.workers.dev"},
                clear=False,
            ):
                _copy_static_web_app(project, public)
            config = (public / "cloud-clock-config.js").read_text(encoding="utf-8")
            self.assertIn("https://private-clock.example.workers.dev", config)

    def test_web_console_can_be_deployed_before_cloud_clock_setup(self):
        source = Path("scripts/deploy-private-web-console.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("[string]$CloudClockUrl = ''", source)
        self.assertIn("$_ -eq '' -or $_ -match", source)
