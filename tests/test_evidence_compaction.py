import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from test_source_evidence import extracted_story, supported_story, synthetic_source

from audiodigest.config import load_settings
from audiodigest.editorial import EditorialPipeline
from audiodigest.evidence import review_evidence
from audiodigest.models import AntigravityMetadata, DataValidationError, StoryEvidence


class EvidenceCompactionTests(TestCase):
    def two_paragraphs(self):
        source = synthetic_source(text=(
            "El pilot va créixer un 12%.\n"
            "Els resultats són provisionals, no una promesa de rendiment futur."
        ))
        raw = extracted_story(
            facts=["The pilot grew 12%.", "The results remain provisional."],
            evidence_refs=[["source-0001:mail:001"], ["source-0001:mail:002"]],
        )
        return supported_story(source, raw)

    def packet(self, story, index=None):
        return review_evidence([story], {source: "source-0001" for source in story.source_ids},
                               index)

    def test_exact_originals_share_one_record_without_mutating_persisted_evidence(self):
        story, index = self.two_paragraphs()
        original = copy.deepcopy(story.to_dict())
        packet = self.packet(story, index)
        self.assertEqual("source-passages-v1", packet["version"])
        self.assertEqual("collected-this-run", packet["origin"])
        self.assertEqual(1, len(packet["passages"]))
        ref = packet["passages"][0]["id"]
        self.assertEqual([[ref], [ref]], [item["passage_ids"] for item in packet["fact_support"]])
        self.assertIn("no una promesa", packet["passages"][0]["text"])
        self.assertEqual(original, story.to_dict())
        self.assertEqual(packet["passages"], self.packet(story)["passages"])
        self.assertEqual(packet["fact_support"], self.packet(story)["fact_support"])

    def test_each_fact_retains_exact_text_and_attribution(self):
        story, index = self.two_paragraphs()
        packet = self.packet(story, index)
        lookup = {item["id"]: item for item in packet["passages"]}
        for claim in packet["fact_support"]:
            support = next(item for item in story.evidence
                           if item.fact_index == claim["fact_index"])
            record = lookup[claim["passage_ids"][0]]
            self.assertEqual(support.excerpt, record["text"])
            self.assertEqual(support.publication, record["publication"])
            self.assertEqual(support.source_type, record["source_type"])
            self.assertEqual(support.kind, record["kind"])
            self.assertEqual(support.url, record["url"])

    def test_duplicate_support_of_one_fact_remains_nonempty(self):
        story, index = self.two_paragraphs()
        story.evidence.append(replace(story.evidence[1], fact_index=0))
        packet = self.packet(story, index)
        self.assertEqual(1, len(packet["fact_support"][0]["passage_ids"]))
        self.assertEqual(3, len(story.evidence))

    def test_context_overlap_is_not_a_duplicate(self):
        story, index = supported_story(raw=extracted_story(
            facts=["The pilot grew 12%.", "The results are preliminary."],
            evidence_refs=[["source-0001:mail:001"], ["source-0001:mail:002"]],
        ))
        self.assertEqual(2, len(self.packet(story, index)["passages"]))

    def test_different_source_groups_or_attribution_are_not_merged(self):
        for field, value in (
            ("passage_id", "source-0001:article-001:001"),
            ("publication", "Another Daily"), ("source_type", "research"),
            ("kind", "article"), ("url", "https://example.com/another-report"),
        ):
            with self.subTest(field=field):
                story, _ = self.two_paragraphs()
                story.evidence[1] = replace(story.evidence[1], **{field: value})
                self.assertEqual(2, len(self.packet(story)["passages"]))

    def test_equal_text_from_two_newsletters_keeps_both_sources(self):
        first, _ = self.two_paragraphs()
        second = copy.deepcopy(first)
        second.story_id = "second-pilot"
        second.source_ids = ["synthetic-second"]
        second.evidence = [replace(item, source_id="synthetic-second",
                                   passage_id=item.passage_id.replace("source-0001", "source-0002"))
                           for item in second.evidence]
        packet = review_evidence([first, second], {
            first.source_ids[0]: "source-0001", second.source_ids[0]: "source-0002",
        }, None)
        self.assertEqual(2, len(packet["passages"]))
        self.assertEqual({"source-0001", "source-0002"},
                         {item["source_id"] for item in packet["passages"]})

    def test_integrity_failure_cannot_hide_behind_an_exact_duplicate(self):
        story, index = self.two_paragraphs()
        story.evidence[1].excerpt_sha256 = "0" * 64
        with self.assertRaisesRegex(DataValidationError, "integrity"):
            self.packet(story, index)
        story, index = self.two_paragraphs()
        story.evidence[1].publication = "Forged attribution"
        with self.assertRaisesRegex(DataValidationError, "differs"):
            self.packet(story, index)

    def test_equal_minimized_text_is_not_equal_original_evidence(self):
        story, _ = self.two_paragraphs()
        before = story.evidence[0].excerpt
        after = before + " reader@example.com"
        story.evidence[1] = replace(story.evidence[1], excerpt=after,
                                   excerpt_sha256=hashlib.sha256(after.encode()).hexdigest())
        with patch("audiodigest.evidence.minimize_editorial_text", return_value="Same safe text"):
            self.assertEqual(2, len(self.packet(story)["passages"]))
            story.evidence[1].passage_id = story.evidence[0].passage_id
            with self.assertRaisesRegex(DataValidationError, "conflicting"):
                self.packet(story)

    def test_size_bound_applies_before_compaction(self):
        story, index = self.two_paragraphs()
        with patch("audiodigest.evidence.MAX_REVIEW_EVIDENCE_CHARS",
                   len(story.evidence[0].excerpt)):
            with self.assertRaisesRegex(DataValidationError, "bounded review"):
                self.packet(story, index)

    def test_serialized_evidence_keeps_original_ids_and_hashes(self):
        story, index = self.two_paragraphs()
        before = [StoryEvidence.from_dict(item) for item in story.to_dict()["evidence"]]
        self.packet(story, index)
        self.assertEqual(before, story.evidence)
        for item in story.evidence:
            index.check(item)

    def test_distinct_articles_at_the_same_url_are_not_merged(self):
        story, _ = self.two_paragraphs()
        for number, item in enumerate(story.evidence, 1):
            story.evidence[number-1] = replace(
                item, kind="article", passage_id=f"source-0001:article-{number:03d}:001",
            )
        self.assertEqual(2, len(self.packet(story)["passages"]))

    def test_compacted_packet_is_deterministic_and_does_not_expose_local_ids(self):
        story, index = self.two_paragraphs()
        first = self.packet(story, index)
        self.assertEqual(first, self.packet(story, index))
        self.assertNotIn("synthetic-newsletter", json.dumps(first))
        self.assertNotIn("reader@example.com", json.dumps(first))

    def test_wrong_candidate_still_fails_existing_factual_review(self):
        story, index = self.two_paragraphs()
        story.facts[0] = "The pilot grew 35%."
        client = Mock()
        engine = EditorialPipeline(load_settings("config.example.toml"), client)
        engine._source_evidence = index

        def review(_instruction, payload, validator, **kwargs):
            self.assertEqual(1, kwargs["retries"])
            packet = payload["source_evidence"]
            self.assertEqual(1, len(packet["passages"]))
            self.assertIn("12%", packet["passages"][0]["text"])
            self.assertIn("no una promesa", packet["passages"][0]["text"])
            self.assertNotIn("35%", json.dumps(packet))
            result = validator({"approved": False, "factual_approved": False,
                                "issues": ["The original reports 12%, not 35%."]})
            return result, AntigravityMetadata()

        client.invoke.side_effect = review
        result, _ = engine.verify_newspaper(
            [story], SimpleNamespace(to_dict=lambda: {"lead": "The pilot grew 35%."}),
        )
        self.assertFalse(result.factually_safe)
        self.assertFalse(result.approved)
        self.assertEqual(1, client.invoke.call_count)
