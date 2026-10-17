import copy
from datetime import date
from unittest import TestCase
from unittest.mock import Mock

from audiodigest.config import load_settings
from audiodigest.editorial import EditorialPipeline
from audiodigest.evidence import SourceEvidenceIndex
from audiodigest.models import DataValidationError, Story, source_prompt_dicts
from tests.test_source_evidence import extracted_story, synthetic_source


class SourceAttributionTests(TestCase):
    def extract(self, rows, sources=None):
        client = Mock()
        client.invoke.side_effect = lambda _instruction, _payload, validator, **_kw: (
            validator(rows),
            None,
        )
        engine = EditorialPipeline(load_settings("config.example.toml"), client)
        result = engine.extract_stories(sources or [synthetic_source()], date(2026, 1, 1))[0]
        client.invoke.assert_called_once()
        self.assertEqual(1, client.invoke.call_args.kwargs["retries"])
        return result

    def test_redundant_invented_url_and_unused_alias_do_not_replace_real_evidence(self):
        raw = {
            "stories": [
                extracted_story(
                    source_ids=["invented", "source-0001"],
                    source_urls=["https://example.com/not-collected"],
                )
            ]
        }
        original = copy.deepcopy(raw)
        result = self.extract(raw)[0]
        self.assertEqual(original, raw)
        self.assertEqual(["synthetic-newsletter"], result.source_ids)
        self.assertEqual([], result.source_urls)
        self.assertEqual(original["stories"][0]["facts"], result.facts)
        self.assertEqual("source-0001:mail:002", result.evidence[0].passage_id)

    def test_model_can_omit_both_application_owned_fields(self):
        row = extracted_story()
        del row["source_ids"], row["source_urls"]
        self.assertEqual(1, len(self.extract({"stories": [row]})))

    def test_unknown_or_partial_fact_support_still_fails_with_exact_location(self):
        for refs in ([], [[]], [["forged"]], [[True]], [["source-0001:mail:002"], ["forged"]]):
            row = extracted_story(evidence_refs=refs)
            if len(refs) == 2:
                row["facts"].append("A second assertion without real support.")
            with self.subTest(refs=refs), self.assertRaises(DataValidationError) as failure:
                self.extract({"stories": [row]})
            self.assertEqual(0, failure.exception.diagnostic["item_index"])
            self.assertEqual("evidence_refs", failure.exception.diagnostic["field"])

    def test_article_and_mixed_source_attribution_come_from_the_actual_passages(self):
        sources = [synthetic_source(article=True), synthetic_source(message_id="second-source")]
        row = extracted_story(
            evidence_refs=[["source-0001:article-001:001", "source-0002:mail:002"]]
        )
        story = self.extract({"stories": [row]}, sources)[0]
        self.assertEqual(["synthetic-newsletter", "second-source"], story.source_ids)
        self.assertEqual(["https://example.com/canonical"], story.source_urls)
        self.assertEqual(2, len(story.evidence))

    def test_legacy_strict_binder_still_rejects_changed_saved_attribution(self):
        source = synthetic_source()
        index = SourceEvidenceIndex([source], source_prompt_dicts([source]))
        row = extracted_story(source_urls=["https://example.com/forged"])
        with self.assertRaises(DataValidationError):
            index.bind(Story.from_dict(row), row)
