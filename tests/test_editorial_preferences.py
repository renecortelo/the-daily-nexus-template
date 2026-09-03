import copy
from datetime import date
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from audiodigest.closing_quotes import ClosingQuote
from audiodigest.editorial import (
    NEWSPAPER_MAX_PROSE_WORDS,
    NEWSPAPER_MAX_TOTAL_WORDS,
    NEWSPAPER_TARGET_PROSE_WORDS,
    NEWSPAPER_TARGET_TOTAL_WORDS,
    EditorialPipeline,
    _deduplicate_newspaper_articles,
    _exact_highlight_candidates,
    _newspaper_article_limits,
    _normalize_script_section_order,
    _remove_enforced_script_order_issues,
    _repair_newspaper_decorations,
    _stories_validator,
    _validate_newspaper_word_budget,
)
from audiodigest.models import (
    AntigravityMetadata,
    NewspaperArticle,
    Story,
    VerificationResult,
)


class EditorialPreferenceTests(TestCase):
    def test_newspaper_article_scale_adapts_to_the_requested_edition(self):
        settings = SimpleNamespace(
            podcast=SimpleNamespace(newspaper_edition_scale="focused")
        )
        self.assertEqual(
            ("focused", 2, 4),
            _newspaper_article_limits(settings, 10),
        )
        settings.podcast.newspaper_edition_scale = "comprehensive"
        self.assertEqual(
            ("comprehensive", 5, 8),
            _newspaper_article_limits(settings, 10),
        )
        self.assertEqual(
            ("comprehensive", 1, 8),
            _newspaper_article_limits(settings, 1),
        )

    def test_structurally_impossible_section_order_rejection_is_removed(self):
        review = _remove_enforced_script_order_issues(
            VerificationResult(
                approved=False,
                issues=[
                    "The section order is incorrect: 'Sports' appears before 'AI'."
                ],
            )
        )

        self.assertTrue(review.approved)
        self.assertEqual([], review.issues)

    def test_section_order_filter_retains_substantive_verification_issues(self):
        review = _remove_enforced_script_order_issues(
            VerificationResult(
                approved=False,
                issues=[
                    "The section sequence is incorrect.",
                    "Remove an unsupported date.",
                ],
            )
        )

        self.assertFalse(review.approved)
        self.assertEqual(["Remove an unsupported date."], review.issues)

    def test_script_sections_are_deterministically_reordered(self):
        raw = {
            "title": "The Daily Nexus",
            "sections": [
                {"name": "Sports", "story_ids": ["sports"]},
                {"name": "AI", "story_ids": ["ai"]},
                {
                    "name": "TIH: Today in History",
                    "story_ids": ["history"],
                },
            ],
        }

        normalized = _normalize_script_section_order(
            raw,
            ("TIH: Today in History", "AI", "Sports"),
        )

        self.assertEqual(
            ["TIH: Today in History", "AI", "Sports"],
            [section["name"] for section in normalized["sections"]],
        )
        self.assertEqual("Sports", raw["sections"][0]["name"])

    def test_three_page_ceiling_is_larger_than_two_page_target(self):
        self.assertGreater(
            NEWSPAPER_MAX_PROSE_WORDS,
            NEWSPAPER_TARGET_PROSE_WORDS,
        )
        self.assertGreater(
            NEWSPAPER_MAX_TOTAL_WORDS,
            NEWSPAPER_TARGET_TOTAL_WORDS,
        )
        article = SimpleNamespace(
            standfirst="",
            body=" ".join(["verified"] * 1_100),
            bullet_points=[],
        )
        issue = SimpleNamespace(
            articles=[article],
            briefs=[],
            word_count=1_400,
        )

        _validate_newspaper_word_budget(
            issue,
            priority_story_count=10,
            layout_repair=True,
        )

        issue.word_count = NEWSPAPER_MAX_TOTAL_WORDS + 1
        with self.assertRaisesRegex(ValueError, "three-page safety ceiling"):
            _validate_newspaper_word_budget(
                issue,
                priority_story_count=10,
                layout_repair=True,
            )

    def test_cross_section_duplicate_story_prefers_current_news(self):
        stories = _stories_validator(
            {
                "stories": [
                    {
                        "story_id": "world-leadership",
                        "section": "World Politics and News",
                        "headline": "Andy Burnham succeeds Keir Starmer as UK prime minister",
                        "facts": [
                            "Andy Burnham succeeded Keir Starmer after a party vote."
                        ],
                        "why_it_matters": "The change reshapes UK government leadership.",
                        "source_ids": ["newsletter-1"],
                        "source_urls": ["https://example.com/news"],
                        "confidence": 0.9,
                        "rank_score": 9.0,
                    },
                    {
                        "story_id": "tih-world-snapshot",
                        "section": "TIH: Today in History",
                        "headline": "UK leadership changes from Starmer to Burnham",
                        "facts": [
                            "Andy Burnham became UK prime minister after Keir Starmer."
                        ],
                        "why_it_matters": "The current-world snapshot records the transition.",
                        "source_ids": ["current-world"],
                        "source_urls": ["https://example.com/snapshot"],
                        "confidence": 0.8,
                        "rank_score": 7.0,
                    },
                ]
            }
        )

        self.assertEqual(1, len(stories))
        self.assertEqual("World Politics and News", stories[0].section.value)
        self.assertEqual(
            {"newsletter-1", "current-world"},
            set(stories[0].source_ids),
        )

    def test_duplicate_current_fact_is_removed_from_tih_article(self):
        current = NewspaperArticle(
            section_label="World",
            title="UK leadership changes",
            standfirst="A governing-party vote produced a new prime minister.",
            body=(
                "Andy Burnham succeeded Keir Starmer as UK Prime Minister "
                "after the governing party vote."
            ),
            story_ids=["world-leadership"],
            source_urls=[],
            bullet_points=[],
        )
        history = NewspaperArticle(
            section_label="Today in History",
            title="July 20 milestones and global snapshot",
            standfirst="Historical milestones share the date with current developments.",
            body=(
                "Andy Burnham succeeded Keir Starmer as UK Prime Minister "
                "following a party leadership election. "
                "Apollo 11 landed on the Moon on July 20, 1969."
            ),
            story_ids=["tih-world-snapshot", "tih-apollo"],
            source_urls=[],
            bullet_points=[],
        )
        issue = SimpleNamespace(articles=[current, history])

        _deduplicate_newspaper_articles(
            issue,
            {"tih-world-snapshot", "tih-apollo"},
        )

        self.assertIn("Andy Burnham", current.body)
        self.assertNotIn("Andy Burnham", history.body)
        self.assertIn("Apollo 11", history.body)

    def test_newspaper_decorations_are_repaired_without_ai_retry(self):
        articles = [
            NewspaperArticle(
                section_label="AI",
                title=f"Measured deployment {index}",
                standfirst=(
                    f"Company {index} deployed a measured system into production."
                ),
                body=(
                    "The operating team documented a 50% reduction in processing "
                    "time while preserving the existing review controls."
                ),
                story_ids=[f"story-{index}"],
                source_urls=[f"https://example.com/report-{index}"],
                bullet_points=[
                    "The operating team documented a 50% reduction in processing time."
                ],
                highlights=["invented phrase"],
            )
            for index in range(5)
        ]
        issue = SimpleNamespace(articles=articles)

        _repair_newspaper_decorations(issue)

        for article in articles:
            article_text = f"{article.standfirst} {article.body}".casefold()
            self.assertEqual([], article.bullet_points)
            self.assertGreaterEqual(len(article.highlights), 2)
            self.assertTrue(
                all(
                    highlight.casefold() in article_text
                    for highlight in article.highlights
                )
            )

    def test_highlight_repair_never_leaves_a_fact_truncated_at_a_number(self):
        candidates = _exact_highlight_candidates(
            "Colin Gray was sentenced to 15 years in prison after the court hearing."
        )

        self.assertIn("Colin Gray was sentenced to 15 years", candidates)
        self.assertNotIn("Colin Gray was sentenced to 15", candidates)

    def test_two_host_conversation_requests_reactive_dialogue(self):
        settings = SimpleNamespace(
            app=SimpleNamespace(target_min_words=1000, target_max_words=1500),
            podcast=SimpleNamespace(tone="formal"),
            hosts=SimpleNamespace(
                count=2,
                solo_name="Dalia",
                dialogue_style="conversation",
                primary_name="Dalia",
                primary_tone="warm",
                secondary_name="Nox",
                secondary_tone="dry_wit",
            ),
        )
        antigravity = Mock()
        antigravity.invoke.return_value = (
            SimpleNamespace(word_count=1200),
            "metadata",
        )
        pipeline = EditorialPipeline(settings, antigravity)

        pipeline.generate_script(
            [],
            date(2026, 7, 27),
            ClosingQuote(
                text="Simplicity is the ultimate sophistication.",
                author="Leonardo da Vinci",
                source_url="https://example.com/quote",
            ),
        )

        instruction = antigravity.invoke.call_args.args[0]
        payload = antigravity.invoke.call_args.args[1]
        self.assertIn("natural two-host news conversation", instruction)
        self.assertIn("genuine questions", instruction)
        self.assertEqual("conversation", payload["dialogue_style"])
        self.assertEqual(["Dalia", "Nox"], [item["name"] for item in payload["hosts"]])

    def test_selected_tone_is_included_in_script_instruction(self):
        settings = SimpleNamespace(
            app=SimpleNamespace(target_min_words=1000, target_max_words=1500),
            podcast=SimpleNamespace(tone="formal"),
            hosts=SimpleNamespace(
                count=1,
                solo_name="Nox",
                primary_name="Dalia",
                primary_tone="formal",
                secondary_name="Nox",
                secondary_tone="dry_wit",
            ),
        )
        antigravity = Mock()
        antigravity.invoke.return_value = (
            SimpleNamespace(word_count=1200),
            "metadata",
        )
        pipeline = EditorialPipeline(settings, antigravity)

        pipeline.generate_script(
            [],
            date(2026, 7, 27),
            ClosingQuote(
                text="Simplicity is the ultimate sophistication.",
                author="Leonardo da Vinci",
                source_url="https://example.com/quote",
            ),
        )

        instruction = antigravity.invoke.call_args.args[0]
        payload = antigravity.invoke.call_args.args[1]
        self.assertIn("intelligent dry wit", instruction)
        self.assertEqual(
            [{"name": "Nox", "tone": "dry_wit"}],
            payload["hosts"],
        )

    def test_short_script_is_expanded_from_existing_verified_stories(self):
        settings = SimpleNamespace(
            app=SimpleNamespace(target_min_words=1000, target_max_words=1500),
            podcast=SimpleNamespace(tone="formal"),
            hosts=SimpleNamespace(
                count=1,
                solo_name="Nox",
                primary_name="Dalia",
                primary_tone="formal",
                secondary_name="Nox",
                secondary_tone="dry_wit",
            ),
        )
        story = Story.from_dict(
            {
                "story_id": "verified-ai",
                "section": "AI",
                "headline": "A measured deployment",
                "facts": ["The system entered a measured production deployment."],
                "why_it_matters": "The deployment changed the operating process.",
                "source_ids": ["message-1"],
                "source_urls": ["https://example.com/report"],
                "confidence": 0.9,
                "rank_score": 9.0,
            }
        )
        short_script = SimpleNamespace(
            word_count=850,
            to_dict=lambda: {"word_count": 850},
        )
        expanded_script = SimpleNamespace(word_count=1120)
        antigravity = Mock()
        antigravity.invoke.side_effect = [
            (short_script, AntigravityMetadata(input_tokens=10, output_tokens=20)),
            (expanded_script, AntigravityMetadata(input_tokens=30, output_tokens=40)),
        ]
        pipeline = EditorialPipeline(settings, antigravity)

        result, metadata = pipeline.generate_script(
            [story],
            date(2026, 7, 27),
            ClosingQuote(
                text="Simplicity is the ultimate sophistication.",
                author="Leonardo da Vinci",
                source_url="https://example.com/quote",
            ),
        )

        self.assertIs(result, expanded_script)
        self.assertEqual(40, metadata.input_tokens)
        self.assertEqual(60, metadata.output_tokens)
        expansion_instruction = antigravity.invoke.call_args_list[1].args[0]
        expansion_payload = antigravity.invoke.call_args_list[1].args[1]
        self.assertIn("underdeveloped stories", expansion_instruction)
        self.assertEqual(
            {"word_count": 850},
            expansion_payload["previous_script"],
        )
        self.assertEqual(
            ["verified-ai"],
            expansion_payload["required_story_ids"],
        )

    def test_newspaper_is_written_from_stories_without_the_audio_script(self):
        antigravity = Mock()
        antigravity.invoke.return_value = ("newspaper", "metadata")
        pipeline = EditorialPipeline(SimpleNamespace(), antigravity)

        pipeline.generate_newspaper([], date(2026, 7, 27))

        instruction = antigravity.invoke.call_args.args[0]
        payload = antigravity.invoke.call_args.args[1]
        self.assertIn("sibling products", instruction)
        self.assertIn("visual must communicate the reporting itself", instruction)
        self.assertEqual([], payload["stories"])
        self.assertNotIn("verified_script", payload)
        self.assertNotIn("hosts", payload)

    def test_newspaper_validator_requires_priority_story_coverage(self):
        story = Story.from_dict(
            {
                "story_id": "priority-ai",
                "section": "AI",
                "headline": "A measured AI deployment",
                "facts": ["The supplied evidence documented the deployment."],
                "why_it_matters": "The deployment moved a system into production.",
                "source_ids": ["message-1"],
                "source_urls": ["https://example.com/report"],
                "confidence": 0.9,
                "rank_score": 9.0,
            }
        )
        antigravity = Mock()
        antigravity.invoke.return_value = ("newspaper", "metadata")
        pipeline = EditorialPipeline(SimpleNamespace(), antigravity)
        pipeline.generate_newspaper([story], date(2026, 7, 27))
        validator = antigravity.invoke.call_args.args[2]
        data = {
            "headline": "The production signal",
            "deck": "A verified system moved from testing into operation.",
            "lead": "The evidence supports one material change and its practical importance.",
            "pull_quote": (
                "Production deployment makes reliable measurement a material "
                "operating requirement rather than a laboratory preference."
            ),
            "kicker": "Executive briefing",
            "briefs": ["The evidence documented a measured deployment."],
            "executive_summary": [
                {
                    "value": "SHIFT",
                    "label": "Measured deployment entered production",
                    "detail": (
                        "The documented system moved from controlled testing "
                        "into live operations."
                    ),
                    "story_ids": ["priority-ai"],
                },
                {
                    "value": "IMPACT",
                    "label": "Operational measurement now matters",
                    "detail": (
                        "Production use makes reliable performance evidence "
                        "materially more consequential."
                    ),
                    "story_ids": ["priority-ai"],
                },
                {
                    "value": "WATCH",
                    "label": "Results remain the open test",
                    "detail": (
                        "The supplied evidence does not yet establish "
                        "long-term production results."
                    ),
                    "story_ids": ["priority-ai"],
                },
            ],
            "articles": [
                {
                    "section_label": "AI",
                    "title": "A measured deployment",
                    "standfirst": "A system moved into production.",
                    "body": "The source documented the operational change.",
                    "story_ids": ["priority-ai"],
                    "source_urls": ["https://example.com/report"],
                    "bullet_points": [],
                }
            ],
            "data_points": [],
            "visuals": [
                {
                    "kind": "news_grid",
                    "title": "The production development to know",
                    "caption": "The verified change and its concrete operational consequence.",
                    "items": [
                        {
                            "value": "SHIFT",
                            "label": "System deployment",
                            "detail": "System moved into live production.",
                            "story_ids": ["priority-ai"],
                        },
                        {
                            "value": "WATCH",
                            "label": "Performance evidence",
                            "detail": "Results are still being measured.",
                            "story_ids": ["priority-ai"],
                        },
                    ],
                    "source_urls": ["https://example.com/report"],
                }
            ],
            "sources": ["Example - https://example.com/report"],
        }

        self.assertEqual("The production signal", validator(data).headline)
        invalid = copy.deepcopy(data)
        invalid["articles"][0]["story_ids"] = []
        with self.assertRaisesRegex(ValueError, "omits priority story IDs"):
            validator(invalid)

        spoken = copy.deepcopy(data)
        spoken["articles"][0]["body"] = (
            "Indeed, Dalia, the source documented the operational change."
        )
        with self.assertRaisesRegex(ValueError, "spoken-script phrasing"):
            validator(spoken)

        reported_name = copy.deepcopy(data)
        reported_name["articles"][0]["body"] = (
            "Dalia was named in the source's account of the operational change."
        )
        self.assertEqual(
            "A measured deployment",
            validator(reported_name).articles[0].title,
        )

        repeated = copy.deepcopy(data)
        repeated["executive_summary"][0]["detail"] = repeated["lead"]
        with self.assertRaisesRegex(ValueError, "repeats a full sentence"):
            validator(repeated)

        redundant_decoration = copy.deepcopy(data)
        redundant_decoration["articles"][0]["highlights"] = [
            "phrase that does not exist in the article"
        ]
        redundant_decoration["articles"][0]["bullet_points"] = [
            "The source documented the operational change."
        ]
        repaired_issue = validator(redundant_decoration)
        self.assertEqual([], repaired_issue.articles[0].highlights)
        self.assertEqual([], repaired_issue.articles[0].bullet_points)

        percentage_copy = copy.deepcopy(data)
        percentage_copy["articles"][0]["body"] = (
            "The source documented a 50 percent operating improvement."
        )
        normalized_issue = validator(percentage_copy)
        self.assertEqual(
            "The source documented a 50% operating improvement.",
            normalized_issue.articles[0].body,
        )

    def test_newspaper_gets_an_independent_quality_review(self):
        antigravity = Mock()
        antigravity.invoke.return_value = ("review", "metadata")
        pipeline = EditorialPipeline(SimpleNamespace(), antigravity)
        issue = SimpleNamespace(to_dict=lambda: {"headline": "A complete edition"})

        pipeline.verify_newspaper([], issue)

        instruction = antigravity.invoke.call_args.args[0]
        payload = antigravity.invoke.call_args.args[1]
        self.assertIn("incomplete, mechanically cropped", instruction)
        self.assertIn("dedicated TIH article", instruction)
        self.assertEqual({"headline": "A complete edition"}, payload["newspaper"])
