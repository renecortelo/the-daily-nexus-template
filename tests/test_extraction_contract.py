import copy
import io
import json
from datetime import date
from unittest import TestCase
from unittest.mock import Mock

from audiodigest.antigravity_client import _json_from_response, _validation_code
from audiodigest.config import load_settings
from audiodigest.draft_repairs import draft_repair_plan
from audiodigest.editorial import EditorialPipeline, _stories_validator
from audiodigest.extraction_contract import normalize_extraction
from audiodigest.models import DataValidationError
from audiodigest.progress import (
    ProgressReporter,
    clear_failure_code,
    record_failure_code,
    record_validation,
    safe_event,
    stage,
)
from audiodigest.resource_metrics import safe_sample
from tests.test_source_evidence import extracted_story, synthetic_source


class ExtractionContractTests(TestCase):
    def validate(self, raw, sections=()):
        settings = load_settings("config.example.toml")
        settings.podcast.sections = sections
        client = Mock()
        client.invoke.side_effect = lambda _instruction, _payload, validator, **_kwargs: (
            validator(raw), None,
        )
        engine = EditorialPipeline(settings, client)
        stories, _ = engine.extract_stories([synthetic_source()], date(2026, 10, 13))
        client.invoke.assert_called_once()
        self.assertEqual(1, client.invoke.call_args.kwargs["retries"])
        return stories

    def test_unambiguous_metadata_repair_preserves_claims_references_and_input(self):
        raw = {"stories": [extracted_story(confidence="0.9", rank_score="5", section=" ai ")]}
        original = copy.deepcopy(raw)
        normalized, repaired = normalize_extraction(raw, ("AI",))
        self.assertEqual(3, repaired)
        self.assertEqual(original, raw)
        for key in ("facts", "why_it_matters", "source_ids", "source_urls", "evidence_refs"):
            self.assertEqual(raw["stories"][0][key], normalized["stories"][0][key])
        stories = self.validate(raw, ("AI",))
        self.assertEqual(0.9, stories[0].confidence)
        self.assertEqual(5, stories[0].rank_score)

    def test_generated_support_objects_are_discarded_then_rebuilt_from_real_passages(self):
        raw = {"stories": [extracted_story(evidence="synthetic-forged-support")]}
        story = self.validate(raw)[0]
        self.assertEqual("source-0001:mail:002", story.evidence[0].passage_id)
        self.assertNotIn("synthetic-forged-support", json.dumps(story.to_dict()))

    def test_unambiguous_and_symbol_uses_exact_configured_spelling_not_a_new_desk(self):
        stories = self.validate({"stories": [extracted_story(section="Cloud and Software")]},
                                ("Cloud & Software",))
        self.assertEqual("Cloud & Software", stories[0].section.value)

    def test_duplicate_local_ids_are_unique_without_dropping_records_or_changing_facts(self):
        raw = {"stories": [extracted_story(), extracted_story(),
                           extracted_story(story_id="extracted-0002")]}
        normalized, repaired = normalize_extraction(raw, None)
        self.assertEqual(1, repaired)
        self.assertEqual(3, len(normalized["stories"]))
        ids = [row["story_id"] for row in normalized["stories"]]
        self.assertEqual(3, len(set(ids)))
        self.assertEqual(raw["stories"][0], normalized["stories"][0])
        self.assertEqual(raw["stories"][1]["facts"], normalized["stories"][1]["facts"])
        self.assertEqual(1, len(self.validate(raw)))

    def test_unsupported_section_is_not_silently_reclassified(self):
        with self.assertRaises(DataValidationError) as error:
            self.validate({"stories": [extracted_story(section="Unrelated") ]}, ("AI",))
        self.assertEqual("extraction_section", error.exception.failure_code)
        self.assertEqual({"field": "section", "value_kind": "string", "item_index": 0,
                          "rule": "section_not_allowed"},
                         error.exception.diagnostic)

    def test_bad_scores_remain_rejected_and_boolean_nonfinite_and_huge_scores_are_safe(self):
        for field, code in (
            ("confidence", "extraction_confidence"), ("rank_score", "extraction_rank"),
        ):
            for value in (None, True, "high", float("nan"), float("inf"), 10**1000):
                with self.subTest(field=field, kind=type(value).__name__):
                    with self.assertRaises(DataValidationError) as error:
                        self.validate({"stories": [extracted_story(**{field: value})]})
                    self.assertEqual(code, error.exception.failure_code)
                    self.assertEqual(field, error.exception.diagnostic["field"])

    def test_empty_required_field_and_invalid_fact_array_keep_field_and_story_position(self):
        for field, value in (
            ("headline", None), ("facts", []), ("source_ids", "synthetic-private"),
        ):
            with self.subTest(field=field), self.assertRaises(DataValidationError) as error:
                self.validate({"stories": [extracted_story(**{field: value})]})
            self.assertEqual(field, error.exception.diagnostic["field"])
            self.assertEqual(0, error.exception.diagnostic["item_index"])

    def test_invalid_support_still_fails_after_nonfactual_metadata_repair(self):
        cases = (
            ({"evidence_refs": []}, "source_fact_alignment"),
            ({"evidence_refs": [[]]}, "source_fact_support_shape"),
            ({"evidence_refs": [["synthetic-private-id"]]}, "source_passage_unknown"),
            ({"source_ids": ["synthetic-private-id"]}, "source_alias_unknown"),
            ({"source_urls": ["https://example.com/unrelated"]}, "source_url_unowned"),
        )
        for changes, code in cases:
            with self.subTest(code=code), self.assertRaises(DataValidationError) as error:
                self.validate({"stories": [extracted_story(confidence="0.9", **changes)]})
            self.assertEqual(code, error.exception.failure_code)
            self.assertEqual(0, error.exception.diagnostic["item_index"])
            self.assertNotIn("synthetic-private", json.dumps(error.exception.diagnostic))

    def test_direct_persisted_validation_still_rejects_duplicate_ids(self):
        raw = extracted_story()
        with self.assertRaises(DataValidationError) as error:
            _stories_validator({"stories": [raw, raw]}, validate_source=lambda *_: None)
        self.assertEqual("extraction_duplicate_id", error.exception.failure_code)

    def test_specific_constraints_have_specific_already_budgeted_repair_plans(self):
        for code in ("extraction_section", "extraction_confidence", "extraction_rank",
                     "source_fact_alignment", "source_passage_unknown"):
            plan = draft_repair_plan({"sources": []}, {}, code)
            self.assertTrue(plan["validation_still_required"])
            self.assertIn("complete corrected extraction", plan["action"])
            self.assertIn("Never invent", plan["action"])

    def test_json_positions_do_not_keep_rejected_text(self):
        with self.assertRaises(DataValidationError) as error:
            _json_from_response('{"stories": synthetic-private-text}')
        self.assertEqual("json_invalid", _validation_code(error.exception))
        self.assertEqual("root", error.exception.diagnostic["field"])
        self.assertGreater(error.exception.diagnostic["json_column"], 0)
        self.assertNotIn("synthetic-private", str(error.exception))


class DiagnosticHistoryTests(TestCase):
    def test_histories_survive_recovery_and_terminal_profiles_without_private_values(self):
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        reporter.start()
        try:
            stage(3)
            record_failure_code("extraction_section")
            record_validation("extraction_section", 1, {
                "field": "section", "item_index": 2, "value_kind": "string",
                "value": "synthetic-private", "source_id": "synthetic-private-id",
            })
            clear_failure_code()
            profile = reporter.finish("completed")
            record_validation("model_structure", 2)
        finally:
            reporter.close()
        self.assertNotIn("failure_code", profile)
        self.assertEqual(1, len(profile["validation_history"]))
        self.assertEqual(2, safe_sample(profile)["validation_history"][0]["item_index"])
        self.assertNotIn("synthetic-private", json.dumps(profile))

    def test_history_is_bounded_and_invalid_nested_values_are_stripped(self):
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        reporter.start()
        try:
            for _ in range(25):
                record_validation("extraction_rank", 1, {"field": "rank_score"})
            profile = reporter.finish("failed", failure_code="extraction_rank")
        finally:
            reporter.close()
        self.assertEqual(16, len(profile["validation_history"]))
        profile["validation_history"] = [{
            "code": "extraction_rank", "stage": 3, "attempt": 1, "elapsed_seconds": 1,
            "field": "synthetic-private", "value_kind": "synthetic-private",
            "item_index": True, "value": "synthetic-private",
        }, {"code": "synthetic-private", "stage": 3, "attempt": 1, "elapsed_seconds": 1}]
        clean = safe_event(profile)
        self.assertEqual(1, len(clean["validation_history"]))
        self.assertNotIn("synthetic-private", json.dumps(clean))
