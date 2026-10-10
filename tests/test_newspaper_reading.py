import json
import tempfile
from pathlib import Path
from unittest import TestCase

from audiodigest.newspaper_reading import MAX_READING_BYTES, approved_reading_copy


def example_issue():
    return {
        "headline": "A regional investment takes shape",
        "deck": "Verified context from the selected reporting.",
        "lead": "The council approved funding, subject to a safety review.",
        "articles": [
            {
                "title": "Funding approved",
                "body": "Funding is conditional.\n\nThe review comes next.",
                "standfirst": "The vote has conditions.",
                "section_label": "Business",
                "bullet_points": ["The review is due next month."],
                "highlights": ["Funding is conditional.", "Invented emphasis"],
                "source_urls": ["https://example.org/news"],
                "story_ids": ["synthetic-internal-story"],
            }
        ],
        "data_points": ["Two review stages."],
        "sources": ["Synthetic publisher"],
        "briefs": [{"text": "A second report details the next vote.", "story_ids": ["hidden"]}],
        "visuals": [
            {
                "kind": "timeline",
                "title": "What happens next",
                "caption": "Two stages.",
                "source_urls": ["https://example.org/review"],
                "items": [
                    {
                        "label": "Review",
                        "value": "First",
                        "detail": "Inspect the plans.",
                        "story_ids": ["hidden"],
                    },
                    {"label": "Vote", "value": "Second", "detail": "Only after approval."},
                ],
            }
        ],
        "source_evidence": "Must not be projected",
        "local_path": "synthetic-private-path",
    }


class NewspaperReadingTests(TestCase):
    def test_complete_copy_preserves_paragraphs_visuals_and_valid_emphasis_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "newspaper.json"
            path.write_text(json.dumps(example_issue()), encoding="utf-8")
            result = approved_reading_copy(path)
            self.assertIsNone(
                approved_reading_copy(path, expected={"headline": "A stale artifact"})
            )
        self.assertEqual(1, result["version"])
        self.assertEqual(
            "Funding is conditional.\n\nThe review comes next.", result["articles"][0]["body"]
        )
        self.assertEqual(["Funding is conditional."], result["articles"][0]["highlights"])
        self.assertEqual("Only after approval.", result["visuals"][0]["items"][1]["detail"])
        encoded = json.dumps(result)
        for private_field in [
            "source_urls",
            "story_ids",
            "source_evidence",
            "local_path",
            "example.org",
            "hidden",
        ]:
            self.assertNotIn(private_field, encoded)

    def test_missing_malformed_oversized_and_legacy_copy_fall_back_without_cropping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "newspaper.json"
            self.assertIsNone(approved_reading_copy(path))
            for value in [
                "[]",
                "{broken",
                json.dumps({**example_issue(), "lead": "In today's episode we report the vote."}),
                json.dumps({**example_issue(), "lead": "x" * (MAX_READING_BYTES + 1)}),
                "\xff",
            ]:
                path.write_text(value, encoding="utf-8")
                self.assertIsNone(approved_reading_copy(path))
