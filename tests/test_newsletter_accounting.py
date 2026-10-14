import json
from datetime import date
from unittest import TestCase
from unittest.mock import Mock

from audiodigest.config import load_settings
from audiodigest.editorial import EditorialPipeline
from audiodigest.evidence import SourceEvidenceIndex
from audiodigest.models import DataValidationError, source_prompt_dicts
from audiodigest.newsletter_accounting import accounting_counts, newsletter_accounting
from tests.test_source_evidence import extracted_story, supported_story, synthetic_source


class NewsletterAccountingTests(TestCase):
    def ledger(self, sources, stories=(), reported=None, configured=False):
        index = SourceEvidenceIndex(sources, source_prompt_dicts(sources))
        return newsletter_accounting(
            sources, index.prompts, list(stories), reported, configured_sections=configured,
        )

    def test_observed_missing_input_is_not_a_model_reason(self):
        source = synthetic_source()
        source.email_text = ""
        rows = self.ledger([source], reported=[{
            "source_id": "source-0001", "reason": "promotional",
        }])
        self.assertEqual("no_supplied_text", rows[0]["reason"])
        self.assertEqual("observed", rows[0]["reason_basis"])
        self.assertEqual(1, accounting_counts(rows)["newsletter_no_supplied_text"])

    def test_missing_body_with_article_passages_is_not_empty_input(self):
        source = synthetic_source(article=True)
        source.email_text = ""
        rows = self.ledger([source])
        self.assertEqual("unknown", rows[0]["reason"])
        self.assertGreater(rows[0]["article_passages"], 0)

    def test_newsletter_without_links_and_non_english_text_has_no_invented_cause(self):
        source = synthetic_source()
        source.article_urls = []
        rows = self.ledger([source])
        self.assertEqual("unknown", rows[0]["reason"])
        self.assertGreater(rows[0]["newsletter_passages"], 0)

    def test_each_model_reason_is_unverified_and_outside_sections_requires_configuration(self):
        source = synthetic_source()
        for reason in (
            "promotional", "no_substantive_news", "insufficient_evidence", "outside_sections",
        ):
            with self.subTest(reason=reason):
                rows = self.ledger([source], reported=[{
                    "source_id": "source-0001", "reason": reason,
                }], configured=True)
                self.assertEqual(reason, rows[0]["reason"])
                self.assertEqual("model_reported", rows[0]["reason_basis"])
                self.assertEqual(1, accounting_counts(rows)[f"newsletter_model_{reason}"])
        self.assertEqual("unknown", self.ledger([source], reported=[{
            "source_id": "source-0001", "reason": "outside_sections",
        }])[0]["reason"])

    def test_diagnostics_do_not_override_validated_story_lineage_or_include_research(self):
        source = synthetic_source()
        research = synthetic_source(message_id="synthetic-research")
        research.source_type = "history"
        story, _ = supported_story(source)
        rows = self.ledger([source, research], [story], [{
            "source_id": "source-0001", "reason": "promotional",
        }])
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["retained_news"])
        self.assertEqual("retained_news", rows[0]["reason"])
        self.assertEqual(0, sum(accounting_counts(rows).values()))

    def test_malformed_conflicting_or_injected_optional_rows_remain_unknown(self):
        source = synthetic_source()
        row = {"source_id": "source-0001", "reason": "promotional"}
        for reported in (
            "synthetic-private-text", {}, [True], [dict(row, detail="synthetic-private-text")],
            [dict(row, reason=["promotional"])], [dict(row, reason="access_blocked")],
            [dict(row, source_id=source.message_id)], [row, row],
        ):
            with self.subTest(reported=reported):
                rows = self.ledger([source], reported=reported)
                self.assertEqual("unknown", rows[0]["reason"])
                self.assertNotIn("synthetic-private-text", json.dumps(rows))
                self.assertNotIn(source.message_id, json.dumps(rows))
                self.assertNotIn(source.sender, json.dumps(rows))
                self.assertNotIn(source.email_text, json.dumps(rows))
        second = synthetic_source(message_id="synthetic-second")
        rows = self.ledger([source, second], reported=[row, row])
        self.assertEqual(["unknown", "unknown"], [item["reason"] for item in rows])

    def test_caps_are_independent_input_flags_not_explanations_for_omission(self):
        source = synthetic_source(text="News. " * 9000, article=True)
        source.articles[0].text = "Report. " * 7000
        counts = accounting_counts(self.ledger([source]))
        self.assertEqual(1, counts["newsletter_body_input_capped"])
        self.assertEqual(1, counts["newsletter_article_input_capped"])
        self.assertEqual(1, counts["newsletter_reason_unknown"])

    def test_optional_accounting_does_not_add_calls_or_reject_valid_extraction(self):
        settings = load_settings("config.example.toml")
        settings.podcast.sections = ()
        source = synthetic_source()
        client = Mock()
        engine = EditorialPipeline(settings, client)
        raw = extracted_story()
        client.invoke.side_effect = lambda _instruction, _payload, validate, **_kwargs: (
            validate({"stories": [raw], "source_accounting": {"invalid": "synthetic"}}), None,
        )
        stories, _ = engine.extract_stories([source], date(2026, 10, 12))
        client.invoke.assert_called_once()
        self.assertEqual(1, client.invoke.call_args.kwargs["retries"])
        self.assertEqual(1, len(stories))
        self.assertTrue(engine.newsletter_accounting[0]["retained_news"])
        self.assertIn("unverified editorial explanations", client.invoke.call_args.args[0])

    def test_no_content_still_accounts_for_every_newsletter(self):
        settings = load_settings("config.example.toml")
        client = Mock()
        engine = EditorialPipeline(settings, client)
        client.invoke.side_effect = lambda _instruction, _payload, validate, **_kwargs: (
            validate({"stories": []}), None,
        )
        engine.extract_stories([synthetic_source()], date(2026, 10, 12))
        counts = accounting_counts(engine.newsletter_accounting)
        self.assertEqual(1, counts["newsletter_reason_unknown"])

    def test_merged_duplicate_reports_keep_both_sources_represented(self):
        settings = load_settings("config.example.toml")
        settings.podcast.sections = ()
        sources = [synthetic_source(), synthetic_source(message_id="synthetic-second")]
        records = [extracted_story(), extracted_story(
            story_id="second-report", source_ids=["source-0002"],
            evidence_refs=[["source-0002:mail:002"]],
        )]
        client = Mock()
        engine = EditorialPipeline(settings, client)
        client.invoke.side_effect = lambda _instruction, _payload, validate, **_kwargs: (
            validate({"stories": records}), None,
        )
        stories, _ = engine.extract_stories(sources, date(2026, 10, 12))
        self.assertEqual(1, len(stories))
        self.assertEqual(
            [True, True], [row["retained_news"] for row in engine.newsletter_accounting],
        )
        counts = accounting_counts(engine.newsletter_accounting)
        self.assertEqual(0, counts["newsletter_reason_unknown"])

    def test_optional_explanation_cannot_approve_an_unsupported_source(self):
        settings = load_settings("config.example.toml")
        client = Mock()
        engine = EditorialPipeline(settings, client)
        client.invoke.side_effect = lambda _instruction, _payload, validate, **_kwargs: (
            validate({"stories": [extracted_story(evidence_refs=[["forged-passage"]])],
                      "source_accounting": [
                          {"source_id": "source-0001", "reason": "promotional"},
                      ]}),
            None,
        )
        with self.assertRaises(DataValidationError):
            engine.extract_stories([synthetic_source()], date(2026, 10, 12))
        self.assertEqual([], engine.newsletter_accounting)
