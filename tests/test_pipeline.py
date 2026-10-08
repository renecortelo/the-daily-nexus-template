import json
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from audiodigest.antigravity_client import (
    AntigravityCLIError,
    AntigravityConfigurationError,
    AntigravityPaymentRiskError,
)
from audiodigest.audio import AudioResult
from audiodigest.config import load_settings
from audiodigest.gmail_client import fixture_sources
from audiodigest.models import (
    AntigravityMetadata,
    EpisodeScript,
    NewspaperArticle,
    NewspaperIssue,
    Story,
    VerificationResult,
)
from audiodigest.pipeline import (
    Pipeline,
    VerificationError,
    _episode_guid,
    _execution_storage_key,
    _format_article_summary,
    _promote_episode_files,
    _published_episode_title,
    _remove_stale_preview_files,
    _write_progress_status,
)
from audiodigest.publisher import PublishResult


class EpisodeIdentityTests(TestCase):
    def test_cloud_execution_guid_is_unique_per_execution_and_stable_per_retry(self):
        day = date(2026, 8, 2)
        first = _episode_guid(day, "request-first")
        second = _episode_guid(day, "request-second")
        self.assertNotEqual(first, second)
        self.assertEqual(first, _episode_guid(day, "request-first"))
        self.assertNotEqual(first, _episode_guid(day))

    def test_cloud_title_uses_readable_date_label_and_sequence(self):
        self.assertEqual(
            "12/31/2026 The Daily Nexus - TDN All - 0002",
            _published_episode_title(
                "The Daily Nexus - December 31, 2026",
                episode_date=date(2026, 12, 31),
                run_name="TDN All",
                sequence=2,
            ),
        )

    def test_cloud_execution_uses_separate_local_artifact_directory(self):
        self.assertNotEqual(
            _execution_storage_key("first-run"),
            _execution_storage_key("second-run"),
        )
        self.assertTrue(_execution_storage_key("first-run").startswith("run-"))

    def test_publication_sequence_does_not_wrap_after_four_digits(self):
        for value, suffix in ((999, "0999"), (1000, "1000"), (10000, "10000")):
            title = _published_episode_title(
                "The Daily Nexus",
                episode_date=date(2026, 12, 31),
                run_name="An edition",
                sequence=value,
            )
            self.assertTrue(title.endswith(f" - {suffix}"))


class ArticleSummaryTests(TestCase):
    def test_summary_balances_successes_and_skip_reasons(self):
        stats = Counter(
            {
                "attempted": 10,
                "retrieved": 6,
                "robots": 2,
                "access blocked": 1,
                "unreadable": 1,
                "tracking_skipped": 20,
                "utility_skipped": 5,
                "unwrapped": 3,
            }
        )
        self.assertEqual(
            _format_article_summary(stats),
            (
                "Article enrichment: 6 retrieved from 10 direct links; "
                "4 fetches skipped (2 robots, 1 access blocked, 1 unreadable); "
                "25 tracking or utility links ignored before fetching; "
                "3 tracking destinations safely decoded."
            ),
        )


class NewspaperRepairTests(TestCase):
    def test_initial_spoken_script_failure_gets_a_reader_only_retry(self):
        issue = NewspaperIssue(
            headline="A complete executive edition",
            deck="One material change moved from planning into operation.",
            lead="The verified reporting describes a specific operating development.",
            articles=[],
            data_points=[],
            sources=[],
        )
        metadata = AntigravityMetadata()
        editorial = Mock()
        editorial.generate_newspaper.side_effect = [
            AntigravityCLIError("newspaper contains host dialogue or spoken-script phrasing"),
            (issue, metadata),
        ]
        editorial.verify_newspaper.return_value = (
            VerificationResult(approved=True, issues=[]),
            metadata,
        )
        pipeline = SimpleNamespace(
            editorial=editorial,
            settings=SimpleNamespace(podcast=SimpleNamespace(max_script_repairs=2)),
        )

        result, result_metadata = Pipeline._generate_verified_newspaper(
            pipeline,
            [],
            date(2026, 8, 3),
        )

        self.assertIs(issue, result)
        self.assertEqual(2, editorial.generate_newspaper.call_count)
        self.assertIn(
            "reader-facing newspaper",
            editorial.generate_newspaper.call_args.kwargs["repair_issues"][0],
        )
        self.assertEqual(
            ["newspaper", "newspaper-review"],
            [item["stage"] for item in result_metadata],
        )

    def test_structural_repairs_can_correct_multiple_known_newspaper_defects(self):
        issue = NewspaperIssue(
            headline="A complete executive edition",
            deck="One material change moved from planning into operation.",
            lead="The verified reporting describes a specific operating development.",
            articles=[],
            data_points=[],
            sources=[],
        )
        metadata = AntigravityMetadata()
        editorial = Mock()
        editorial.generate_newspaper.side_effect = [
            AntigravityCLIError("newspaper contains host dialogue or spoken-script phrasing"),
            AntigravityCLIError("executive signal labels must be specific and concise"),
            (issue, metadata),
        ]
        editorial.verify_newspaper.return_value = (
            VerificationResult(approved=True, issues=[]),
            metadata,
        )
        pipeline = SimpleNamespace(
            editorial=editorial,
            settings=SimpleNamespace(podcast=SimpleNamespace(max_script_repairs=2)),
        )

        result, _metadata = Pipeline._generate_verified_newspaper(
            pipeline,
            [],
            date(2026, 9, 3),
        )

        self.assertIs(issue, result)
        self.assertEqual(3, editorial.generate_newspaper.call_count)
        self.assertIn(
            "concrete 2-12 word label",
            editorial.generate_newspaper.call_args.kwargs["repair_issues"][0],
        )

    def test_visual_copy_limit_gets_a_reader_ready_retry(self):
        issue = NewspaperIssue(
            headline="A complete executive edition",
            deck="One material change moved from planning into operation.",
            lead="The verified reporting describes a specific operating development.",
            articles=[],
            data_points=[],
            sources=[],
        )
        metadata = AntigravityMetadata()
        editorial = Mock()
        editorial.generate_newspaper.side_effect = [
            AntigravityCLIError("visual item details must be 34 characters or fewer"),
            (issue, metadata),
        ]
        editorial.verify_newspaper.return_value = (
            VerificationResult(approved=True, issues=[]),
            metadata,
        )
        pipeline = SimpleNamespace(
            editorial=editorial,
            settings=SimpleNamespace(podcast=SimpleNamespace(max_script_repairs=2)),
        )

        result, _metadata = Pipeline._generate_verified_newspaper(
            pipeline,
            [],
            date(2026, 8, 11),
        )

        self.assertIs(issue, result)
        self.assertEqual(2, editorial.generate_newspaper.call_count)
        self.assertIn(
            "complete 5-7 word phrase",
            editorial.generate_newspaper.call_args.kwargs["repair_issues"][0],
        )

    def test_duplicate_sentence_failure_gets_a_deduplicated_retry(self):
        issue = NewspaperIssue(
            headline="A complete executive edition",
            deck="One material change moved from planning into operation.",
            lead="The verified reporting describes a specific operating development.",
            articles=[],
            data_points=[],
            sources=[],
        )
        metadata = AntigravityMetadata()
        editorial = Mock()
        editorial.generate_newspaper.side_effect = [
            AntigravityCLIError("reader-facing copy repeats a full sentence across sections"),
            (issue, metadata),
        ]
        editorial.verify_newspaper.return_value = (
            VerificationResult(approved=True, issues=[]),
            metadata,
        )
        pipeline = SimpleNamespace(
            editorial=editorial,
            settings=SimpleNamespace(podcast=SimpleNamespace(max_script_repairs=2)),
        )

        result, _metadata = Pipeline._generate_verified_newspaper(
            pipeline,
            [],
            date(2026, 8, 12),
        )

        self.assertIs(issue, result)
        self.assertEqual(2, editorial.generate_newspaper.call_count)
        self.assertIn(
            "no repeated full sentences",
            editorial.generate_newspaper.call_args.kwargs["repair_issues"][0],
        )

    def test_structural_repair_failure_uses_the_next_rewrite_attempt(self):
        issue = NewspaperIssue(
            headline="A complete executive edition",
            deck="One material change moved from planning into operation.",
            lead="The verified reporting describes a specific operating development.",
            articles=[
                NewspaperArticle(
                    title="A measured change",
                    body=("The verified source documented one complete operational change."),
                    source_urls=["https://example.com/report"],
                    bullet_points=[],
                    section_label="Operations",
                    standfirst="The change entered a measured operating phase.",
                    story_ids=["story-1"],
                )
            ],
            data_points=[],
            sources=["Example - https://example.com/report"],
        )
        metadata = AntigravityMetadata()
        rejected = VerificationResult(
            approved=False,
            issues=["Remove one repeated fact."],
        )
        approved = VerificationResult(approved=True, issues=[])
        editorial = Mock()
        editorial.generate_newspaper.side_effect = [
            (issue, metadata),
            AntigravityCLIError("draft exceeded the two-page word target"),
            (issue, metadata),
        ]
        editorial.verify_newspaper.side_effect = [
            (rejected, metadata),
            (approved, metadata),
        ]
        pipeline = SimpleNamespace(
            editorial=editorial,
            settings=SimpleNamespace(podcast=SimpleNamespace(max_script_repairs=2)),
        )

        result, result_metadata = Pipeline._generate_verified_newspaper(
            pipeline,
            [],
            date(2026, 7, 13),
        )

        self.assertIs(issue, result)
        self.assertEqual(3, editorial.generate_newspaper.call_count)
        self.assertEqual(
            [
                "newspaper",
                "newspaper-review",
                "newspaper-repair-2",
                "newspaper-recheck-2",
            ],
            [item["stage"] for item in result_metadata],
        )

    def test_explicitly_factually_safe_newspaper_warns_and_returns_newspaper(self):
        issue = NewspaperIssue(
            headline="A complete executive edition",
            deck="One material change moved from planning into operation.",
            lead="The verified reporting describes a specific operating development.",
            articles=[
                NewspaperArticle(
                    title="A measured change",
                    body=("The verified source documented one complete operational change."),
                    source_urls=["https://example.com/report"],
                    bullet_points=[],
                    section_label="Operations",
                    standfirst="The change entered a measured operating phase.",
                    story_ids=["story-1"],
                )
            ],
            data_points=[],
            sources=["Example - https://example.com/report"],
        )
        metadata = AntigravityMetadata()
        rejected = VerificationResult(
            approved=False,
            issues=["Remove minor repetition of facts."],
            factual_approved=True,
        )
        editorial = Mock()
        editorial.generate_newspaper.return_value = (issue, metadata)
        editorial.verify_newspaper.return_value = (rejected, metadata)
        pipeline = SimpleNamespace(
            editorial=editorial,
            settings=SimpleNamespace(podcast=SimpleNamespace(max_script_repairs=2)),
        )

        result, result_metadata = Pipeline._generate_verified_newspaper(
            pipeline,
            [],
            date(2026, 7, 13),
        )

        self.assertIs(issue, result)
        self.assertEqual(3, editorial.generate_newspaper.call_count)
        self.assertEqual(3, editorial.verify_newspaper.call_count)

    def test_final_newspaper_rewrite_timeout_keeps_prior_edition(self):
        issue = NewspaperIssue(
            headline="A complete executive edition",
            deck="One material change moved from planning into operation.",
            lead="The verified reporting describes a specific operating development.",
            articles=[],
            data_points=[],
            sources=[],
        )
        metadata = AntigravityMetadata()
        rejected = VerificationResult(
            approved=False, issues=["Remove repetition."], factual_approved=True
        )
        editorial = Mock()
        editorial.generate_newspaper.side_effect = [
            (issue, metadata),
            AntigravityCLIError("timeout waiting for response"),
            AntigravityCLIError("timeout waiting for response"),
        ]
        editorial.verify_newspaper.return_value = (rejected, metadata)
        pipeline = SimpleNamespace(
            editorial=editorial,
            settings=SimpleNamespace(podcast=SimpleNamespace(max_script_repairs=2)),
        )

        result, result_metadata = Pipeline._generate_verified_newspaper(
            pipeline,
            [],
            date(2026, 8, 10),
        )

        self.assertIs(issue, result)
        self.assertEqual(3, editorial.generate_newspaper.call_count)
        self.assertEqual(
            ["newspaper", "newspaper-review"],
            [item["stage"] for item in result_metadata],
        )

    def test_final_visual_copy_rewrite_failure_keeps_prior_edition(self):
        issue = NewspaperIssue(
            headline="A complete executive edition",
            deck="One material change moved from planning into operation.",
            lead="The verified reporting describes a specific operating development.",
            articles=[],
            data_points=[],
            sources=[],
        )
        metadata = AntigravityMetadata()
        rejected = VerificationResult(
            approved=False, issues=["Improve visual copy."], factual_approved=True
        )
        editorial = Mock()
        editorial.generate_newspaper.side_effect = [
            (issue, metadata),
            AntigravityCLIError("visual item details must contain 5 to 7 complete words"),
            AntigravityCLIError("visual item details must contain 5 to 7 complete words"),
        ]
        editorial.verify_newspaper.return_value = (rejected, metadata)
        pipeline = SimpleNamespace(
            editorial=editorial,
            settings=SimpleNamespace(podcast=SimpleNamespace(max_script_repairs=2)),
        )

        result, result_metadata = Pipeline._generate_verified_newspaper(
            pipeline,
            [],
            date(2026, 8, 13),
        )

        self.assertIs(issue, result)
        self.assertEqual(3, editorial.generate_newspaper.call_count)
        self.assertEqual(
            ["newspaper", "newspaper-review"],
            [item["stage"] for item in result_metadata],
        )

    def test_factual_and_unclassified_rejections_block_after_configured_repairs(self):
        issue = NewspaperIssue(
            headline="Synthetic test",
            deck="Test",
            lead="Test",
            articles=[],
            data_points=[],
            sources=[],
        )
        for factual in (False, None):
            editorial = Mock()
            editorial.generate_newspaper.return_value = (issue, AntigravityMetadata())
            editorial.verify_newspaper.return_value = (
                VerificationResult(False, ["Unsupported amount."], factual),
                AntigravityMetadata(),
            )
            pipeline = SimpleNamespace(
                editorial=editorial,
                settings=SimpleNamespace(podcast=SimpleNamespace(max_script_repairs=1)),
            )
            with self.assertRaises(VerificationError):
                Pipeline._generate_verified_newspaper(pipeline, [], date(2026, 8, 10))
            self.assertEqual(2, editorial.generate_newspaper.call_count)


class OptionalNewspaperTests(TestCase):
    def test_failed_paper_does_not_publish_rejected_copy(self):
        pipeline = SimpleNamespace(
            _generate_verified_newspaper=Mock(side_effect=VerificationError("Unsupported fact")),
        )
        with patch("audiodigest.pipeline.NewspaperRenderer") as renderer:
            issue, metadata, previews = Pipeline._prepare_newspaper_artifact(
                pipeline, [], date(2026, 8, 10), Path("unused"), Path("unused")
            )
        self.assertIsNone(issue)
        self.assertEqual([], previews)
        self.assertEqual("newspaper-failed", metadata[0]["stage"])
        self.assertNotIn("Unsupported fact", json.dumps(metadata))
        renderer.assert_not_called()

    def test_payment_guard_cannot_be_downgraded_to_optional_paper_failure(self):
        pipeline = SimpleNamespace(
            _generate_verified_newspaper=Mock(side_effect=AntigravityPaymentRiskError("guard")),
        )
        with self.assertRaises(AntigravityPaymentRiskError):
            Pipeline._prepare_newspaper_artifact(
                pipeline, [], date(2026, 8, 10), Path("unused"), Path("unused")
            )

    def test_unsafe_settings_cannot_be_downgraded_to_optional_paper_failure(self):
        pipeline = SimpleNamespace(
            _generate_verified_newspaper=Mock(side_effect=AntigravityConfigurationError("guard")),
        )
        with self.assertRaises(AntigravityConfigurationError):
            Pipeline._prepare_newspaper_artifact(
                pipeline, [], date(2026, 8, 10), Path("unused"), Path("unused")
            )

    def test_verified_podcast_can_publish_without_a_paper_end_to_end(self):
        self._assert_podcast_without_paper(include_newspaper=True)

    def test_newspaper_opt_out_publishes_podcast_without_paper_calls(self):
        self._assert_podcast_without_paper(include_newspaper=False)

    def _assert_podcast_without_paper(self, *, include_newspaper):
        sources = fixture_sources(Path("tests/fixtures/newsletters.json"))
        story = Story.from_dict(
            {
                "story_id": "test-story",
                "section": "AI",
                "headline": "Synthetic test",
                "facts": ["Synthetic evidence"],
                "why_it_matters": "Test evidence",
                "source_ids": [sources[0].message_id],
                "source_urls": [],
                "confidence": 1,
                "rank_score": 1,
            }
        )
        script = EpisodeScript.from_dict(
            {
                "title": "The Daily Nexus",
                "hosts": ["Nox"],
                "introduction": "Introduction",
                "sections": [
                    {"name": "AI", "narration": "Synthetic evidence", "story_ids": ["test-story"]}
                ],
                "conclusion": "Conclusion",
                "sign_off": "Quotation",
                "show_notes": [],
            }
        )
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = load_settings("config.example.toml")
            settings.app.runtime_dir = Path(name)
            settings.firebase.publish_enabled = True
            settings.firebase.publish_mode = "automatic"
            settings.podcast.include_newspaper = include_newspaper
            pipeline = Pipeline(settings)
            pipeline._load_sources = Mock(return_value=sources)
            pipeline._enrich_articles = Mock()
            pipeline.editorial = Mock()
            pipeline.editorial.extract_stories.return_value = ([story], AntigravityMetadata())
            pipeline.editorial.generate_script.return_value = (script, AntigravityMetadata())
            pipeline.editorial.verify.return_value = (
                VerificationResult(True, []),
                AntigravityMetadata(),
            )
            pipeline._generate_verified_newspaper = Mock(side_effect=VerificationError("Rejected"))

            def render(_script, output):
                output.write_bytes(b"audio" * 300)
                return AudioResult(output, 1200)

            with (
                patch("audiodigest.pipeline.run_cost_guard"),
                patch("audiodigest.pipeline.KokoroAudioRenderer") as renderer,
                patch("audiodigest.pipeline.FirebasePublisher") as publisher,
            ):
                renderer.return_value.render.side_effect = render
                publisher.return_value.publish.return_value = PublishResult(
                    "https://example.com/feed", 1, 1500
                )
                result = pipeline.run(requested_date=date(2026, 8, 10))
            self.assertEqual("published", result["status"])
            expected_status = "failed" if include_newspaper else "skipped"
            self.assertEqual(expected_status, result["newspaper_status"])
            self.assertEqual(
                int(include_newspaper), pipeline._generate_verified_newspaper.call_count
            )
            self.assertEqual("", result["newspaper_path"])
            self.assertTrue(Path(result["audio_path"]).is_file())
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertIsNone(manifest["newspaper"])
            self.assertEqual(expected_status, manifest["newspaper_status"])
            self.assertEqual([], manifest["newspaper_preview_paths"])
            self.assertIsNone(
                pipeline.database.episode_for_date(date(2026, 8, 10))["newspaper_path"]
            )
            publisher.return_value.publish.assert_called_once()


class EpisodePromotionTests(TestCase):
    def test_stale_preview_is_removed_when_page_count_returns_to_two(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            active = [root / "edition-1.png", root / "edition-2.png"]
            stale = root / "edition-3.png"
            unrelated = root / "cover.png"
            for path in [*active, stale, unrelated]:
                path.write_bytes(b"preview")

            _remove_stale_preview_files(root, active)

            self.assertTrue(all(path.is_file() for path in active))
            self.assertFalse(stale.exists())
            self.assertTrue(unrelated.is_file())

    def test_verified_work_has_a_readable_in_progress_status(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            progress = Path(name) / "2026-07-20" / "in-progress"

            _write_progress_status(
                progress,
                day=date(2026, 7, 20),
                stage=7,
                message="Script available; rendering audio.",
            )

            payload = json.loads((progress / "status.json").read_text(encoding="utf-8"))
            self.assertEqual("2026-07-20", payload["episode_date"])
            self.assertEqual("in-progress", payload["status"])
            self.assertEqual(7, payload["stage"])
            self.assertIn("Script available", payload["message"])

    def test_incomplete_retry_does_not_replace_existing_episode(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            existing = root / "episode.mp3"
            replacement = root / "replacement.mp3"
            existing.write_bytes(b"completed-edition")
            replacement.write_bytes(b"new-edition")

            with self.assertRaises(FileNotFoundError):
                _promote_episode_files(
                    [
                        (replacement, existing),
                        (root / "missing-manifest.json", root / "manifest.json"),
                    ]
                )

            self.assertEqual(existing.read_bytes(), b"completed-edition")
            self.assertFalse((root / "episode.mp3.new").exists())
