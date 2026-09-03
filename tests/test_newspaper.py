import tempfile
from datetime import date
from pathlib import Path
from unittest import TestCase

from audiodigest.config import load_settings
from audiodigest.constants import AI_DISCLOSURE
from audiodigest.models import EpisodeScript, NewspaperIssue
from audiodigest.newspaper import (
    NewspaperRenderer,
    _bullet_adds_distinct_information,
    _limited_words,
    _percent_symbols,
    is_legacy_script_style_issue,
    newspaper_from_verified_script,
)


class NewspaperTests(TestCase):
    def test_percent_word_is_normalized_for_reader_copy(self):
        self.assertEqual("Revenue rose 12%.", _percent_symbols("Revenue rose 12 percent."))

    def test_redundant_article_bullet_is_removed_from_reader_copy(self):
        article = (
            "The source documented a full codebase rewrite into Rust in 11 days."
        )
        self.assertFalse(
            _bullet_adds_distinct_information(
                "A full codebase rewrite into Rust took 11 days.",
                article,
            )
        )
        self.assertTrue(
            _bullet_adds_distinct_information(
                "The migration consumed $165,000 in API tokens.",
                article,
            )
        )

    def test_fallback_excerpt_ends_at_a_real_sentence(self):
        text = (
            "The U.S. House approved the measure after a long debate. "
            "A second, deliberately lengthy sentence contains more detail than "
            "the compact edition can safely carry without cutting its final words."
        )
        excerpt = _limited_words(text, 13)
        self.assertEqual(
            excerpt,
            "The U.S. House approved the measure after a long debate.",
        )

    def test_fallback_omits_host_intro_but_is_marked_legacy(self):
        script = EpisodeScript.from_dict(
            {
                "title": "The Daily Nexus - July 27, 2026",
                "introduction": "I'm Dario, and welcome to The Daily Nexus.",
                "sections": [
                    {
                        "name": "AI",
                        "narration": "A verified model update led the technology news.",
                        "story_ids": ["ai"],
                    }
                ],
                "conclusion": "That is the signal.",
                "sign_off": "A closing quotation.",
                "show_notes": [],
                "disclosure": AI_DISCLOSURE,
            }
        )

        issue = newspaper_from_verified_script(script)

        self.assertNotIn("Dario", issue.lead)
        self.assertTrue(is_legacy_script_style_issue(issue))
        self.assertLessEqual(issue.word_count, 1_400)

    def test_fallback_keeps_history_out_of_executive_and_visual_summaries(self):
        script = EpisodeScript.from_dict(
            {
                "title": "The Daily Nexus - July 28, 2026",
                "introduction": "I am Dalia. Edited and produced by Dario Novelli.",
                "sections": [
                    {
                        "name": "TIH: Today in History",
                        "narration": (
                            "We open today with history. A documented event happened "
                            "on this date. Its consequences were recorded later."
                        ),
                        "story_ids": ["history"],
                    },
                    {
                        "name": "AI",
                        "narration": (
                            "A company deployed a verified system. The deployment "
                            "changed its measured operating process."
                        ),
                        "story_ids": ["current-ai"],
                    },
                ],
                "conclusion": "The operating consequence deserves continued attention.",
                "sign_off": "A closing quotation.",
                "show_notes": [],
                "disclosure": AI_DISCLOSURE,
            }
        )

        issue = newspaper_from_verified_script(script)

        executive_ids = {
            story_id
            for item in issue.executive_summary
            for story_id in item.story_ids
        }
        visual_ids = {
            story_id
            for visual in issue.visuals
            for item in visual.items
            for story_id in item.story_ids
        }
        self.assertNotIn("history", executive_ids)
        self.assertNotIn("history", visual_ids)
        self.assertEqual(
            "TIH: Today in History",
            NewspaperRenderer._split_articles(issue.articles)[0][0].section_label,
        )

    def test_renderer_creates_exactly_two_pages_and_previews(self):
        settings = load_settings("config.example.toml")
        issue = NewspaperIssue.from_dict(
            {
                "headline": "A measured signal from a changing world",
                "deck": "Technology and policy moved together in today's briefing.",
                "lead": (
                    "The central development connected practical artificial "
                    "intelligence deployment with a new policy response. This "
                    "single-page edition wording came from a legacy saved issue."
                ),
                "articles": [
                    {
                        "section_label": "Technology",
                        "title": "Systems move into production",
                        "standfirst": "Measured deployment replaced speculative promise.",
                        "body": (
                            "Teams reported a controlled deployment backed by "
                            "measured performance. The evidence remained limited "
                            "to the supplied reporting."
                        ),
                        "source_urls": ["https://example.com/technology"],
                        "bullet_points": ["Deployment remained controlled."],
                        "story_ids": ["technology-deployment"],
                    },
                    {
                        "section_label": "Policy",
                        "title": "Policy catches the signal",
                        "standfirst": "Officials moved from observation to response.",
                        "body": (
                            "Officials described a focused response. The details "
                            "were presented without predictions beyond the source."
                        ),
                        "source_urls": ["https://example.com/policy"],
                        "bullet_points": ["The response stayed within the supplied evidence."],
                        "story_ids": ["policy-response"],
                    },
                ],
                "kicker": "Systems and policy",
                "pull_quote": (
                    "The consequential shift is the connection between deployment "
                    "and the rules forming around it."
                ),
                "briefs": [
                    "Production teams emphasized measured performance.",
                    "Officials described a focused policy response.",
                ],
                "executive_summary": [
                    {
                        "value": "SHIFT",
                        "label": "Systems moved into production",
                        "detail": "Measured deployment replaced speculative promise.",
                    },
                    {
                        "value": "IMPACT",
                        "label": "Policy moved alongside deployment",
                        "detail": "Officials began defining practical boundaries.",
                    },
                    {
                        "value": "WATCH",
                        "label": "Performance evidence remains decisive",
                        "detail": "The reporting identifies measurement as the open question.",
                    },
                ],
                "data_points": ["Two independent source records supported the edition."],
                "visuals": [
                    {
                        "kind": "bar_chart",
                        "title": "Measured movement",
                        "caption": "Comparable evidence-backed quantities.",
                        "items": [
                            {
                                "value": "72%",
                                "label": "Controlled deployment",
                                "detail": "Teams measured performance.",
                                "magnitude": 72,
                            },
                            {
                                "value": "48%",
                                "label": "Focused response",
                                "detail": "Officials set boundaries.",
                                "magnitude": 48,
                            },
                        ],
                        "source_urls": [
                            "https://example.com/technology",
                            "https://example.com/policy",
                        ],
                    }
                ],
                "sources": ["Example - https://example.com/technology"],
            }
        )
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            result = NewspaperRenderer(settings).render(
                issue,
                date(2026, 7, 27),
                root / "edition.pdf",
                root / "edition-1.png",
            )
            self.assertTrue(result.pdf_path.is_file())
            self.assertTrue(result.preview_path.is_file())
            self.assertEqual(2, len(result.preview_paths))
            self.assertTrue(result.preview_paths[1].is_file())
            self.assertGreater(result.pdf_path.stat().st_size, 1000)
            self.assertGreater(result.preview_path.stat().st_size, 1000)
            import fitz

            with fitz.open(result.pdf_path) as document:
                self.assertEqual(2, document.page_count)
                text = "\n".join(page.get_text() for page in document)
                self.assertIn("two-page edition", text)
                self.assertNotIn("single-page edition", text)

    def test_renderer_uses_a_third_page_only_for_readable_overflow(self):
        settings = load_settings("config.example.toml")
        articles = [
            {
                "section_label": "TIH: Today in History",
                "title": "A concise historical marker",
                "standfirst": "One documented event provides historical context.",
                "body": (
                    "The historical source records a specific event on this date. "
                    "Its documented consequence remains separate from current reporting."
                ),
                "source_urls": ["https://example.com/history"],
                "bullet_points": [],
                "story_ids": ["history"],
            }
        ]
        for index in range(1, 8):
            body = " ".join(
                (
                    f"Company {index} recorded a measurable operating change across "
                    f"its regional platform during reporting period {sentence}. "
                    "The verified source described the affected product, the named "
                    "organization, and the concrete implementation detail."
                )
                for sentence in range(1, 5)
            )
            articles.append(
                {
                    "section_label": f"Desk {index}",
                    "title": f"Development {index} changes an operating decision",
                    "standfirst": (
                        f"The {index}th development carries distinct facts that must "
                        "remain readable in the executive edition."
                    ),
                    "body": body,
                    "source_urls": [f"https://example.com/story-{index}"],
                    "bullet_points": [
                        f"The measured result for development {index} remained source-backed."
                    ],
                    "story_ids": [f"story-{index}"],
                }
            )
        issue = NewspaperIssue.from_dict(
            {
                "headline": "A dense day of consequential operating developments",
                "deck": "The edition preserves complete reporting without unreadably small type.",
                "lead": (
                    "Several independent developments changed practical decisions across "
                    "technology, policy, and markets."
                ),
                "articles": articles,
                "kicker": "Executive morning edition",
                "pull_quote": (
                    "The useful distinction is between visible announcements and "
                    "measurable operational change."
                ),
                "briefs": [],
                "executive_summary": [],
                "data_points": [],
                "visuals": [],
                "sources": [],
            }
        )

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            result = NewspaperRenderer(settings).render(
                issue,
                date(2026, 7, 20),
                root / "edition.pdf",
                root / "edition-1.png",
            )

            self.assertEqual(3, len(result.preview_paths))
            self.assertTrue(all(path.is_file() for path in result.preview_paths))
            import fitz

            with fitz.open(result.pdf_path) as document:
                self.assertEqual(3, document.page_count)
                page_three_text = document.load_page(2).get_text()
                self.assertIn("3 OF 3", page_three_text)
                self.assertIn("Development 7", page_three_text)

    def test_column_balancing_can_interleave_long_and_short_articles(self):
        renderer = NewspaperRenderer(load_settings("config.example.toml"))
        heights = [300, 300, 200, 200]
        renderer._article_blocks = lambda *_args, **_kwargs: [
            {"height": height} for height in heights
        ]

        columns = renderer._fit_two_columns(
            [object(), object(), object(), object()],
            width=240,
            available_height=500,
        )

        self.assertEqual(
            [500, 500],
            [sum(block["height"] for block in column) for column in columns],
        )

    def test_legacy_issue_without_visuals_remains_loadable(self):
        issue = NewspaperIssue.from_dict(
            {
                "headline": "A legacy local edition",
                "deck": "Persisted editions can still be rendered.",
                "lead": "The renderer supplies a useful topic map when visual data is absent.",
                "articles": [
                    {
                        "title": "A retained story",
                        "body": "The saved article remains available to the reader.",
                        "source_urls": [],
                        "bullet_points": [],
                    }
                ],
                "data_points": [],
                "sources": [],
            }
        )

        self.assertEqual([], issue.visuals)
        self.assertEqual("Briefing", issue.articles[0].section_label)

    def test_structured_briefs_retain_secondary_story_coverage(self):
        issue = NewspaperIssue.from_dict(
            {
                "headline": "A compact executive signal",
                "deck": "Primary analysis and secondary developments share one edition.",
                "lead": "The hierarchy preserves detail without turning into a transcript.",
                "articles": [
                    {
                        "title": "The lead development",
                        "body": "The principal verified development receives fuller analysis.",
                        "source_urls": ["https://example.com/lead"],
                        "bullet_points": [],
                        "story_ids": ["lead-story"],
                    }
                ],
                "briefs": [
                    {
                        "text": "A secondary verified development remains visible.",
                        "story_ids": ["secondary-story"],
                        "source_urls": ["https://example.com/secondary"],
                    }
                ],
                "data_points": [],
                "sources": [],
            }
        )

        self.assertEqual("secondary-story", issue.briefs[0].story_ids[0])
        self.assertEqual(
            "A secondary verified development remains visible.",
            issue.to_dict()["briefs"][0]["text"],
        )
