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
    def test_accessible_section_controls_share_labels_and_updated_static_shell(self):
        page = Path("web/index.html").read_text(encoding="utf-8")
        styles = Path("web/styles.css").read_text(encoding="utf-8")
        worker = Path("web/service-worker.js").read_text(encoding="utf-8")
        self.assertEqual(2, page.count('class="section-order-status visually-hidden"'))
        for prefix in ("gen", "schedule"):
            self.assertIn(f'for="{prefix}-section-input"', page)
            self.assertIn(f'id="{prefix}-section-input"', page)
            self.assertIn(f'aria-describedby="{prefix}-section-help"', page)
        self.assertIn('id="dismiss-alert-button"', page)
        self.assertIn(':where(button, a, [tabindex]):focus-visible', styles)
        self.assertIn('.weekday-picker input:focus-visible + span', styles)
        self.assertIn('--mini-player-height', styles)
        self.assertIn('/app.js?v=4.1.21', page)
        self.assertIn('/styles.css?v=4.1.21', page)
        self.assertIn('tdn-shell-v4-51', worker)

    def test_compact_console_keeps_touch_zoom_and_motion_safeguards(self):
        styles = Path("web/styles.css").read_text(encoding="utf-8")
        page = Path("web/index.html").read_text(encoding="utf-8")
        self.assertIn('class="source-fields"', page)
        self.assertEqual(2, page.count('class="editorial-fields"'))
        for marker in (
            ".monitor-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }",
            ".host-layout > label:nth-child(8)",
            "@keyframes console-enter", "@keyframes console-acquire",
            "@media (prefers-reduced-motion: reduce)", "animation: none !important",
            "font-size: var(--font-input); min-height: 2.75rem;",
        ):
            self.assertIn(marker, styles)
        self.assertNotIn("user-scalable=no", page)

    def test_harmonized_console_uses_shared_scales_and_native_utility_selects(self):
        styles = Path("web/styles.css").read_text(encoding="utf-8")
        source = Path("web/app.js").read_text(encoding="utf-8")
        for token in ("--font-xs:", "--font-sm:", "--font-ui:", "--font-body:",
                      "--font-input:", "--page-gutter:", "--card-inset:"):
            self.assertIn(token, styles)
        self.assertIn("text-size-adjust: 100%", styles)
        self.assertIn("align-items: start", styles)
        self.assertIn(".select-shell:focus-within", styles)
        self.assertIn('caption.setAttribute("aria-hidden", "true")', source)
        self.assertIn('caption.textContent = selected?.textContent || ""', source)
        self.assertIn("function setupCompactSelects()", source)

    def test_mobile_selects_do_not_expand_document_or_disable_zoom(self):
        styles = Path("web/styles.css").read_text(encoding="utf-8")
        page = Path("web/index.html").read_text(encoding="utf-8")
        select_style = styles.split("\nselect {", 1)[1].split("}", 1)[0]
        for rule in ("appearance: none", "min-inline-size: 0", "overflow: hidden",
                     "text-overflow: ellipsis", "background-image:", "var(--amber)"):
            self.assertIn(rule, select_style)
        self.assertIn("select:not([multiple]) { padding-inline-end: 1.85rem; }", styles)
        self.assertNotIn("user-scalable=no", page)
        self.assertNotIn("maximum-scale=1", page)
        # Reading pages, archives and navigation retain their internal scrolling.
        self.assertIn("overflow-x: auto", styles)
        self.assertIn(".edition-pages", styles)

    def test_native_utility_inputs_have_compact_resting_and_readable_edit_states(self):
        source = Path("web/app.js").read_text(encoding="utf-8")
        styles = Path("web/styles.css").read_text(encoding="utf-8")
        self.assertIn('function setupCompactInputs()', source)
        self.assertIn('  setupCompactInputs();', source)
        for rule in ('.compact-input-shell input:focus', 'scale(0.8125)',
                     'width: calc(100% * 16 / 13)', 'transform: none',
                     '.compact-input-shell:focus-within',
                     '.terminal-list.empty-state', 'min-height: 6rem'):
            self.assertIn(rule, styles)
        self.assertNotIn('inputmode="none"', Path("web/index.html").read_text(encoding="utf-8"))

    def test_home_and_dynamic_controls_keep_local_motion_and_native_semantics(self):
        source = Path("web/app.js").read_text(encoding="utf-8")
        page = Path("web/index.html").read_text(encoding="utf-8")
        styles = Path("web/styles.css").read_text(encoding="utf-8")
        self.assertIn('id="view-home" class="view active"', page)
        self.assertIn('data-view="home"', page)
        self.assertNotIn('id="home-tour-pause"', page)
        self.assertNotIn('id="home-tour-replay"', page)
        self.assertIn('A short illustration, not a live generation.', page)
        self.assertIn('select:not([multiple])', source)
        self.assertIn('syncCompactSelects(chapters)', source)
        self.assertIn(
            'control.addEventListener("change", () => syncCompactSelects(control))', source
        )
        self.assertIn('control.closest?.(".chapter-control")', source)
        self.assertIn('.chapter-control .select-shell', styles)
        self.assertIn('.mini-player.levels-open .mini-transport', styles)
        self.assertNotIn('setupHomeTour', source)
        self.assertNotIn('<iframe', page)
        self.assertNotIn('youtube.com', page)
        for part in ('choose', 'review', 'deliver'):
            rule = styles.split(f'.view.active .tour-scene-{part} {{', 1)[1].split('}', 1)[0]
            self.assertIn('infinite', rule)
        self.assertIn('prefers-reduced-motion: reduce', styles)

    def test_every_native_text_field_and_layout_audit_use_complete_inventory(self):
        source = Path('web/app.js').read_text(encoding='utf-8')
        audit = Path('scripts/audit-web-layout.cjs').read_text(encoding='utf-8')
        page = Path('web/index.html').read_text(encoding='utf-8')
        self.assertIn(
            'input:not([type="checkbox"]):not([type="range"]):not([type="hidden"])',
            source,
        )
        self.assertIn("page.locator('input,select,textarea,button')", audit)
        self.assertIn('item.wrapped&&Math.abs(item.paintedFont-13)', audit)
        self.assertIn("['auth','home','gen','play','read','schedule','about']", audit)
        self.assertIn('Mobile header too tall', audit)
        self.assertIn('inventory-', audit)
        self.assertEqual(page.count('id="signed-in-user"'), 1)
        self.assertIn('aria-label="Sign out"', page)
        self.assertIn('aria-label="Install app"', page)

    def test_publication_titles_accept_legacy_and_four_or_more_digit_numbers(self):
        source = Path("web/app.js").read_text(encoding="utf-8")
        self.assertIn(r"\d{3,}$/i.test(title)", source)
        self.assertIn('String(sequence).padStart(4, "0")', source)

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

    def test_newspaper_toggle_is_shared_and_backwards_compatible(self):
        source = Path("web/app.js").read_text(encoding="utf-8")
        page = Path("web/index.html").read_text(encoding="utf-8")
        rules = Path("firestore.rules").read_text(encoding="utf-8")
        self.assertEqual(2, page.count('name="includeNewspaper" type="checkbox" checked'))
        self.assertIn('includeTih: true, includeNewspaper: true', source)
        self.assertIn('control ? control.checked : fallback', source)
        self.assertIn('control.checked = value', source)
        self.assertIn('setupParameterForm(form)', source)
        self.assertIn('applyParametersToForm(generationForm, profile.parameters)', source)
        self.assertIn('applyParametersToForm(scheduleForm, { ...parameters', source)
        self.assertIn("PODCAST ONLY // NEWSPAPER NOT REQUESTED", source)
        self.assertIn(
            "!('includeNewspaper' in parameters) || parameters.includeNewspaper is bool", rules
        )

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
