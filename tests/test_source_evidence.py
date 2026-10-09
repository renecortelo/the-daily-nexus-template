import copy
import hashlib
import json
import tempfile
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from audiodigest.closing_quotes import ClosingQuote
from audiodigest.config import load_settings
from audiodigest.editorial import (
    EditorialPipeline,
    _remove_enforced_script_order_issues,
    _source_verification_validator,
    _stories_validator,
    _validate_reference_urls,
)
from audiodigest.evidence import SourceEvidenceIndex, _passages, review_evidence
from audiodigest.models import (
    AntigravityMetadata,
    ArticleReference,
    DataValidationError,
    EpisodeScript,
    SourceItem,
    Story,
    VerificationResult,
    source_prompt_dicts,
)
from audiodigest.pipeline import Pipeline


def synthetic_source(*, message_id="synthetic-newsletter", text=None, article=False):
    return SourceItem(
        message_id=message_id, publication="Example Daily", sender="reader@example.com",
        subject="Product pilot", received_at=datetime(2026, 10, 9, tzinfo=UTC),
        email_text=text or (
            "El producte encara no està disponible per a tothom.\n"
            "El pilot va créixer un 12% el 8 d'octubre, segons l'equip.\n"
            "Els resultats són provisionals, no una promesa de rendiment futur."
        ),
        article_urls=["https://example.com/report"],
        articles=[ArticleReference(
            "https://example.com/report", "https://example.com/canonical", "Pilot report",
            "The product pilot grew 12%. These are preliminary results.",
        )] if article else [],
    )


def extracted_story(**changes):
    raw = {
        "story_id": "product-pilot", "section": "AI", "headline": "Product pilot reports growth",
        "facts": ["The team reported preliminary pilot growth of 12% on October 8."],
        "why_it_matters": "The pilot is growing but not yet available to everyone.",
        "source_ids": ["source-0001"], "source_urls": ["https://example.com/report"],
        "evidence_refs": [["source-0001:mail:002"]], "confidence": 0.9, "rank_score": 2,
    }
    return {**raw, **changes}


def supported_story(source=None, raw=None):
    source = source or synthetic_source()
    index = SourceEvidenceIndex([source], source_prompt_dicts([source]))
    raw = raw or extracted_story()
    story = Story.from_dict(raw)
    index.bind(story, raw)
    story.source_ids = [source.message_id]
    return story, index


class SourceEvidenceTests(TestCase):
    def engine(self):
        settings = load_settings("config.example.toml")
        settings.podcast.sections = ()
        return EditorialPipeline(settings, Mock())

    def script(self, fact):
        return EpisodeScript.from_dict({
            "title": "The Daily Nexus", "hosts": ["Dalia"],
            "introduction": "I'm Dalia; Dario Novelli edited and produced this edition.",
            "sections": [{"name": "AI", "narration": fact, "story_ids": ["product-pilot"]}],
            "conclusion": "That is today's briefing.", "sign_off": "Learn carefully. An Author.",
            "show_notes": ["Pilot - https://example.com/report", "https://example.com/quote"],
        })

    def test_source_passages_preserve_all_words_without_duplicating_full_bodies(self):
        source = synthetic_source(text="A short lead.\n" + "reproducible " * 180, article=True)
        index = SourceEvidenceIndex([source], source_prompt_dicts([source]))
        prompt = index.prompts[0]
        self.assertNotIn("email_text", prompt)
        self.assertNotIn("text", prompt["articles"][0])
        self.assertEqual(
            " ".join(source.email_text.split()),
            " ".join(item["text"] for item in prompt["newsletter_passages"]),
        )
        self.assertTrue(all(len(part) <= 900 for part in _passages(source.email_text)))

    def test_excerpt_keeps_adjacent_qualification_and_original_language(self):
        story, index = supported_story()
        excerpt = story.evidence[0].excerpt
        self.assertIn("encara no està disponible", excerpt)
        self.assertIn("12%", excerpt)
        self.assertIn("provisionals", excerpt)
        index.check(story.evidence[0])

    def test_every_fact_must_reference_a_real_passage_from_its_cited_sources(self):
        source = synthetic_source()
        index = SourceEvidenceIndex([source], source_prompt_dicts([source]))
        cases = [
            {"evidence_refs": []}, {"evidence_refs": [["forged-passage"]]},
            {"evidence_refs": [[True]]}, {"evidence_refs": [[]]},
            {"evidence_refs": [["source-0001:mail:001"]] * 2},
            {"source_ids": ["unknown-source"]},
            {"evidence_refs": [["source-0001:mail:001"] * 3]},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                raw = extracted_story(**changes)
                with self.assertRaises(DataValidationError):
                    index.bind(Story.from_dict(raw), raw)

    def test_unrelated_links_passages_and_unused_source_ids_fail_locally(self):
        first = synthetic_source()
        second = synthetic_source(message_id="synthetic-second", text="Unrelated civic reporting.")
        second.article_urls = ["https://example.com/unrelated"]
        index = SourceEvidenceIndex([first, second], source_prompt_dicts([first, second]))
        for changes in (
            {"source_urls": ["https://example.com/unrelated"]},
            {"source_urls": ["https://example.com/invented"]},
            {"evidence_refs": [["source-0002:mail:001"]]},
            {"source_ids": ["source-0001", "source-0002"]},
        ):
            raw = extracted_story(**changes)
            with self.subTest(changes=changes), self.assertRaises(DataValidationError):
                index.bind(Story.from_dict(raw), raw)

    def test_newsletter_without_links_is_valid_and_fetched_canonical_link_is_valid(self):
        source = synthetic_source()
        source.article_urls = []
        story, _ = supported_story(source, extracted_story(source_urls=[]))
        self.assertEqual([], story.source_urls)
        story, _ = supported_story(
            synthetic_source(article=True),
            extracted_story(source_urls=["https://example.com/canonical"],
                            evidence_refs=[["source-0001:article-001:001"]]),
        )
        self.assertEqual("article", story.evidence[0].kind)

    def test_deduplication_rebinds_fact_indexes_and_keeps_all_source_support(self):
        first, second = synthetic_source(), synthetic_source(message_id="synthetic-second")
        index = SourceEvidenceIndex([first, second], source_prompt_dicts([first, second]))
        records = [extracted_story(), extracted_story(
            story_id="second-report", source_ids=["source-0002"],
            facts=["The pilot remains preliminary.", extracted_story()["facts"][0]],
            evidence_refs=[["source-0002:mail:003"], ["source-0002:mail:002"]],
        )]
        aliases = {"source-0001": first.message_id, "source-0002": second.message_id}
        def bind(story, raw):
            index.bind(story, raw)
            story.source_ids = [aliases[alias] for alias in story.source_ids]
        merged = _stories_validator({"stories": records}, validate_source=bind)
        self.assertEqual(1, len(merged))
        self.assertEqual(2, len(merged[0].facts))
        packet = review_evidence(merged, {value: key for key, value in aliases.items()}, index)
        first_fact = next(item for item in packet["fact_support"] if item["fact_index"] == 0)
        self.assertEqual(2, len(first_fact["passage_ids"]))
        self.assertEqual(2, len(merged[0].source_ids))

    def test_duplicate_raw_story_ids_are_rejected_before_coverage_can_be_confused(self):
        raw = extracted_story()
        with self.assertRaisesRegex(ValueError, "must be unique"):
            _stories_validator({"stories": [raw, raw]}, validate_source=lambda *_: None)

    def test_excerpt_tampering_and_missing_legacy_evidence_fail_before_model_review(self):
        story, index = supported_story()
        engine = self.engine()
        engine._source_evidence = index
        story.evidence[0].excerpt = "The pilot grew 35%."
        with self.assertRaisesRegex(DataValidationError, "integrity"):
            engine.verify_newspaper([story], SimpleNamespace(to_dict=lambda: {}))
        engine.antigravity.invoke.assert_not_called()
        story = Story.from_dict(extracted_story())
        story.source_ids = ["synthetic-newsletter"]
        engine._source_evidence = None
        with self.assertRaisesRegex(DataValidationError, "recollect newsletters"):
            engine.verify_newspaper([story], SimpleNamespace(to_dict=lambda: {}))
        engine.antigravity.invoke.assert_not_called()

    def test_saved_evidence_round_trips_without_mailbox_identity_in_review_payload(self):
        source = synthetic_source(text="Costs grew 12%.\nContact reader@example.com.")
        story, _ = supported_story(
            source, extracted_story(evidence_refs=[["source-0001:mail:001"]]),
        )
        restored = Story.from_dict(json.loads(json.dumps(story.to_dict())))
        packet = review_evidence([restored], {source.message_id: "source-0001"}, None)
        encoded = json.dumps(packet)
        self.assertNotIn(source.message_id, encoded)
        self.assertNotIn(source.sender, encoded)
        self.assertEqual("saved-source-excerpts", packet["origin"])

    def test_corrupted_extraction_repeated_faithfully_fails_both_existing_review_calls(self):
        engine = self.engine()
        def extract(_instruction, _payload, validator, **_kwargs):
            return (
                validator({"stories": [extracted_story(facts=["The pilot grew 35%."])]}),
                AntigravityMetadata(),
            )
        engine.antigravity.invoke.side_effect = extract
        stories, _ = engine.extract_stories([synthetic_source()], date(2026, 10, 9))
        def factual_review(instruction, payload, validator, **_kwargs):
            self.assertIn("NOT independent evidence", instruction)
            original = " ".join(item["text"] for item in payload["source_evidence"]["passages"])
            self.assertIn("12%", original)
            self.assertNotIn("35%", original)
            self.assertIn("35%", payload["stories"][0]["facts"][0])
            self.assertNotIn("synthetic-newsletter", json.dumps(payload))
            return validator({
                "approved": False, "factual_approved": False,
                "issues": ["The original source reports 12%, not 35%."],
            }), AntigravityMetadata()
        engine.antigravity.invoke.side_effect = factual_review
        quote = ClosingQuote("Learn carefully.", "An Author", "https://example.com/quote")
        script_review, _ = engine.verify(stories, self.script("The pilot grew 35%."), quote)
        paper_review, _ = engine.verify_newspaper(
            stories, SimpleNamespace(to_dict=lambda: {"lead": "The pilot grew 35%."}),
        )
        self.assertFalse(script_review.approved)
        self.assertFalse(paper_review.factually_safe)
        self.assertEqual(3, engine.antigravity.invoke.call_count)  # Extraction + same two reviews.

    def test_supported_catalan_translation_survives_without_another_review_call(self):
        story, index = supported_story()
        engine = self.engine()
        engine._source_evidence = index
        def review(instruction, payload, validator, **_kwargs):
            self.assertIn("translation is allowed", instruction)
            self.assertIn("provisionals", payload["source_evidence"]["passages"][0]["text"])
            return validator({
                "approved": True, "factual_approved": True, "issues": [],
            }), AntigravityMetadata()
        engine.antigravity.invoke.side_effect = review
        quote = ClosingQuote("Learn carefully.", "An Author", "https://example.com/quote")
        result, _ = engine.verify([story], self.script(story.facts[0]), quote)
        self.assertTrue(result.approved)
        self.assertEqual(1, engine.antigravity.invoke.call_count)

    def test_reference_membership_is_strict_and_known_personalizers_are_not_saved(self):
        notes = ["Pilot - https://example.com/report?cs_email=synthetic."]
        _validate_reference_urls(notes, {"https://example.com/report"})
        self.assertEqual(["Pilot - https://example.com/report."], notes)
        for url in ("https://example.com/forged", "https://example.com/report?token=synthetic",
                    "http://example.com/report"):
            with self.assertRaisesRegex(ValueError, "not supported"):
                _validate_reference_urls([url], {"https://example.com/report"})

    def test_source_review_cannot_omit_factual_classification_or_bypass_it_with_order_issue(self):
        with self.assertRaisesRegex(ValueError, "explicitly classify"):
            _source_verification_validator({"approved": True, "issues": []})
        review = _remove_enforced_script_order_issues(
            VerificationResult(False, ["Change section order"], factual_approved=False),
        )
        self.assertFalse(review.approved)
        self.assertFalse(review.factually_safe)

    def test_nested_evidence_is_not_repeated_in_normal_draft_payloads(self):
        story, _ = supported_story()
        engine = self.engine()
        prompt = engine._story_prompt_dict(copy.deepcopy(story))
        self.assertNotIn("evidence", prompt)
        self.assertNotIn("synthetic-newsletter", json.dumps(prompt))

    def test_live_evidence_rejects_changed_url_and_rehashed_fabricated_excerpt(self):
        story, index = supported_story()
        aliases = {"synthetic-newsletter": "source-0001"}
        story.source_urls = ["https://example.com/forged"]
        with self.assertRaisesRegex(DataValidationError, "source records"):
            review_evidence([story], aliases, index)
        story.source_urls = ["https://example.com/report"]
        fake = "Invented growth of 35%."
        story.evidence[0] = replace(
            story.evidence[0], excerpt=fake,
            excerpt_sha256=hashlib.sha256(fake.encode()).hexdigest(),
        )
        with self.assertRaisesRegex(DataValidationError, "collected source passage"):
            review_evidence([story], aliases, index)

    def test_opaque_blob_does_not_drop_ordinary_newsletter_reporting(self):
        parts = _passages("Growth was 12%. " + "x" * 3000 + " Results remain provisional.")
        self.assertEqual(
            "Growth was 12%. [oversized-source-token-omitted] Results remain provisional.",
            " ".join(parts),
        )

    def test_oversized_review_fails_explicitly_instead_of_truncating_evidence(self):
        story, index = supported_story()
        with patch("audiodigest.evidence.MAX_REVIEW_EVIDENCE_CHARS", 10):
            with self.assertRaisesRegex(DataValidationError, "bounded review size"):
                review_evidence([story], {"synthetic-newsletter": "source-0001"}, index)

    def test_repair_drafts_receive_originals_without_changing_call_count(self):
        story, index = supported_story()
        engine = self.engine()
        engine._source_evidence = index
        engine.antigravity.invoke.return_value = (
            self.script(story.facts[0]), AntigravityMetadata(),
        )
        quote = ClosingQuote("Learn carefully.", "An Author", "https://example.com/quote")
        engine.generate_script([story], date(2026, 10, 9), quote,
                               repair_issues=["The original reports 12%, not 35%."])
        engine.generate_newspaper([story], date(2026, 10, 9),
                                  repair_issues=["The original reports 12%, not 35%."])
        self.assertEqual(2, engine.antigravity.invoke.call_count)
        for call in engine.antigravity.invoke.call_args_list:
            payload = call.args[1]
            self.assertIn("12%", payload["source_evidence"]["passages"][0]["text"])
            self.assertNotIn("synthetic-newsletter", json.dumps(payload))

    def test_legacy_newspaper_rebuild_stops_before_model_calls_or_file_replacement(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"stories": [extracted_story()]}), encoding="utf-8")
            pipeline = SimpleNamespace(
                settings=load_settings("config.example.toml"), database=Mock(),
                editorial=Mock(), _generate_verified_newspaper=Mock(),
            )
            pipeline.database.episode_for_date.return_value = {
                "manifest_path": str(manifest), "audio_path": str(root / "episode.mp3"),
            }
            with patch("audiodigest.pipeline.run_cost_guard"):
                with self.assertRaisesRegex(DataValidationError, "recollect newsletters"):
                    Pipeline.rebuild_existing_newspaper(pipeline, date(2026, 10, 9))
            pipeline._generate_verified_newspaper.assert_not_called()
            self.assertEqual(["manifest.json"], [path.name for path in root.iterdir()])
