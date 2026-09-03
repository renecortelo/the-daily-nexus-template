from unittest import TestCase

from audiodigest.constants import AI_DISCLOSURE
from audiodigest.models import DataValidationError, EpisodeScript, Story


class ModelTests(TestCase):
    def test_story_rejects_unknown_section(self):
        with self.assertRaises(DataValidationError):
            Story.from_dict(
                {
                    "story_id": "x",
                    "section": "Made Up",
                    "headline": "Headline",
                    "facts": ["Fact"],
                    "why_it_matters": "Reason",
                    "source_ids": ["source"],
                    "source_urls": [],
                    "confidence": 0.9,
                    "rank_score": 2,
                }
            )

    def test_script_requires_section_order(self):
        with self.assertRaises(DataValidationError):
            EpisodeScript.from_dict(
                {
                    "title": "The Daily Nexus",
                    "introduction": "Hello.",
                    "sections": [
                        {
                            "name": "Sports",
                            "narration": "Sports.",
                            "story_ids": ["sports"],
                        },
                        {
                            "name": "AI",
                            "narration": "AI.",
                            "story_ids": ["ai"],
                        },
                    ],
                    "conclusion": "Goodbye.",
                    "sign_off": "A closing quotation.",
                    "show_notes": [],
                }
            )

    def test_narration_begins_with_disclosure(self):
        script = EpisodeScript.from_dict(
            {
                "title": "The Daily Nexus",
                "introduction": "Hello.",
                "sections": [{"name": "AI", "narration": "AI news.", "story_ids": ["ai"]}],
                "conclusion": "Goodbye.",
                "sign_off": "A closing quotation.",
                "show_notes": [],
            }
        )
        self.assertTrue(script.narration.startswith(AI_DISCLOSURE))

    def test_two_host_dialogue_preserves_speaker_turns(self):
        script = EpisodeScript.from_dict(
            {
                "title": "The Daily Nexus",
                "hosts": ["Dalia", "Nox"],
                "introduction": [
                    {"host": "Dalia", "text": "I'm Dalia."},
                    {"host": "Nox", "text": "And I'm Nox."},
                ],
                "sections": [
                    {
                        "name": "AI",
                        "dialogue": [
                            {"host": "Dalia", "text": "The model shipped."},
                            {"host": "Nox", "text": "The data supports it."},
                        ],
                        "story_ids": ["ai"],
                    }
                ],
                "conclusion": [{"host": "Dalia", "text": "That is the signal."}],
                "sign_off": [{"host": "Nox", "text": "A closing quotation."}],
                "show_notes": [],
            }
        )

        self.assertEqual(["Dalia", "Nox"], script.hosts)
        self.assertIn("Nox: The data supports it.", script.transcript)
        self.assertEqual(6, len(script.dialogue_turns))
