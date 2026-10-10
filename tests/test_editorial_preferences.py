import copy
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from audiodigest.closing_quotes import ClosingQuote
from audiodigest.config import load_settings
from audiodigest.constants import DATA_SECTION_DEFINITION, editorial_section_definitions
from audiodigest.editorial import (
    NEWSPAPER_MAX_PROSE_WORDS,
    NEWSPAPER_MAX_TOTAL_WORDS,
    NEWSPAPER_TARGET_PROSE_WORDS,
    NEWSPAPER_TARGET_TOTAL_WORDS,
    EditorialPipeline,
    _bound_closing_comment,
    _deduplicate_newspaper_articles,
    _exact_highlight_candidates,
    _newspaper_article_limits,
    _normalize_newspaper_percentages,
    _normalize_script_section_order,
    _remove_enforced_script_order_issues,
    _repair_newspaper_decorations,
    _stories_validator,
    _validate_newspaper_word_budget,
)
from audiodigest.evidence import SourceEvidenceIndex
from audiodigest.models import (
    AntigravityMetadata,
    DialogueTurn,
    NewspaperArticle,
    Story,
    VerificationResult,
)


class ClosingCommentBoundsTests(TestCase):
    def _script(self, quotation, comment):
        text = f"'{quotation.text}' — {quotation.author}. {comment}"
        return SimpleNamespace(
            hosts=["Dalia"], sign_off=[DialogueTurn("Dalia", text)], sign_off_text=text
        )

    def test_short_witty_comment_and_exact_quote_are_preserved(self):
        quote = ClosingQuote("Learn carefully.", "An Author", "https://example.com/source")
        comment = "A useful reminder: the database backup should not be an act of faith."
        script = self._script(quote, comment)
        _bound_closing_comment(script, quote)
        self.assertEqual(2, len(script.sign_off))
        self.assertIn(quote.text, script.sign_off[0].text)
        self.assertIn(quote.author, script.sign_off[0].text)
        self.assertEqual(comment, script.sign_off[1].text)

    def test_overlong_single_sentence_is_omitted_never_cropped(self):
        quote = ClosingQuote("Learn carefully.", "An Author", "https://example.com/source")
        script = self._script(quote, " ".join(["development"] * 30) + ".")
        _bound_closing_comment(script, quote)
        self.assertEqual(1, len(script.sign_off))
        self.assertIn(quote.text, script.sign_off[0].text)

    def test_only_complete_short_sentence_can_survive_the_guard(self):
        quote = ClosingQuote("Learn carefully.", "An Author", "https://example.com/source")
        script = self._script(
            quote, "The backup deserves checking. " + " ".join(["detail"] * 30) + "."
        )
        _bound_closing_comment(script, quote)
        self.assertEqual("The backup deserves checking.", script.sign_off[1].text)

    def test_quote_length_is_not_counted_against_comment_limit(self):
        quote = ClosingQuote(" ".join(["evidence"] * 30) + ".", "An Author", "https://example.com/source")
        script = self._script(quote, "The database is not impressed by our optimism.")
        _bound_closing_comment(script, quote)
        self.assertEqual(2, len(script.sign_off))
        self.assertIn(quote.text, script.sign_off[0].text)


class EditorialPreferenceTests(TestCase):
    def test_conversation_instructions_require_useful_answers_without_extra_ai_pass(self):
        settings = load_settings("config.example.toml")
        settings.hosts.count = 2
        settings.hosts.dialogue_style = "conversation"
        engine = EditorialPipeline(settings, Mock())
        engine.antigravity.invoke.return_value = (Mock(word_count=4000), Mock())
        quote = ClosingQuote("Learn carefully.", "An Author", "https://example.com/source")
        engine.generate_script([], date(2026, 10, 6), quote)
        self.assertEqual(1, engine.antigravity.invoke.call_count)
        instruction = engine.antigravity.invoke.call_args.args[0]
        self.assertIn("response must answer it", instruction.replace("\n", " "))
        self.assertIn("Do not force equal-length turns", instruction)
        self.assertIn("not repeat", instruction)

    def test_data_scope_is_case_insensitive_without_renaming_custom_sections(self):
        self.assertEqual(
            {"DATA": DATA_SECTION_DEFINITION}, editorial_section_definitions(("AI", "DATA"))
        )
        self.assertEqual({"Data": DATA_SECTION_DEFINITION}, editorial_section_definitions(()))
        self.assertEqual({}, editorial_section_definitions(("Sports",)))

    def test_data_scope_reaches_all_five_existing_editorial_calls_without_extra_calls(self):
        settings = load_settings("config.example.toml")
        settings.podcast.sections = ("DATA",)
        engine = EditorialPipeline(settings, Mock())
        engine.antigravity.invoke.return_value = (
            Mock(word_count=4000, approved=True, issues=[]),
            Mock(),
        )
        story = Story.from_dict(
            {
                "story_id": "pipeline",
                "section": "DATA",
                "headline": "ETL pipeline release",
                "facts": ["A database tool adds an ETL connector."],
                "why_it_matters": "Teams can connect database tools.",
                "source_ids": ["newsletter"],
                "source_urls": [],
                "confidence": 0.9,
                "rank_score": 1,
            },
            allowed_sections=("DATA",),
        )
        quote = ClosingQuote("Learn carefully.", "An Author", "https://example.com/source")
        engine.extract_stories([], date(2026, 10, 6))
        from audiodigest.models import SourceItem, source_prompt_dicts

        source = SourceItem(
            "newsletter", "Example News", "editor@example.com", "ETL release",
            datetime(2026, 10, 6, tzinfo=UTC), story.facts[0],
        )
        engine._source_evidence = SourceEvidenceIndex([source], source_prompt_dicts([source]))
        story.source_ids = ["source-0001"]
        engine._source_evidence.bind(story, {"evidence_refs": [["source-0001:mail:001"]]})
        story.source_ids = ["newsletter"]
        engine.generate_script([story], date(2026, 10, 6), quote)
        engine.generate_newspaper([story], date(2026, 10, 6))
        engine.verify_newspaper([story], Mock())
        engine.verify([story], Mock(), quote)
        self.assertEqual(5, engine.antigravity.invoke.call_count)
        for invocation in engine.antigravity.invoke.call_args_list:
            self.assertEqual(
                {"DATA": DATA_SECTION_DEFINITION}, invocation.args[1]["section_definitions"]
            )
        writing = engine.antigravity.invoke.call_args_list[1].args[0]
        self.assertIn("ONE specific", writing)
        self.assertIn("aim for 12-20 words", writing)
        self.assertIn("never more than\n25 words", writing)
        self.assertIn("SEPARATE sign_off turn", writing)
        self.assertIn("natural contractions as the default", writing)
        self.assertIn("Write for listening", writing)
        self.assertIn("Do not modernize or paraphrase the approved closing quotation", writing)
        self.assertIn("emotion/SSML tags", writing)
        verification = engine.antigravity.invoke.call_args_list[4].args[0]
        self.assertIn("smallest local wording repair", verification)

    def test_percentage_normalization_preserves_story_metadata_ids(self):
        normalized = _normalize_newspaper_percentages(
            {
                "story_ids": ["operating-margin-percent"],
                "body": "The operating margin improved by 50 percent.",
            }
        )

        self.assertEqual(
            ["operating-margin-percent"],
            normalized["story_ids"],
        )
        self.assertEqual(
            "The operating margin improved by 50%.",
            normalized["body"],
        )

    def test_newspaper_article_scale_adapts_to_the_requested_edition(self):
        settings = SimpleNamespace(podcast=SimpleNamespace(newspaper_edition_scale="focused"))
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
                issues=["The section order is incorrect: 'Sports' appears before 'AI'."],
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
                        "facts": ["Andy Burnham succeeded Keir Starmer after a party vote."],
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
                        "facts": ["Andy Burnham became UK prime minister after Keir Starmer."],
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
                "after the governing party vote. "
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
                standfirst=(f"Company {index} deployed a measured system into production."),
                body=(
                    "The operating team documented a 50% reduction in processing "
                    "time while preserving the existing review controls."
                ),
                story_ids=[f"story-{index}"],
                source_urls=[f"https://example.com/report-{index}"],
                bullet_points=["The operating team documented a 50% reduction in processing time."],
                highlights=["invented phrase"],
            )
            for index in range(5)
        ]
        issue = SimpleNamespace(articles=articles)

        _repair_newspaper_decorations(issue)

        for article in articles:
            article_text = f"{article.standfirst} {article.body}".casefold()
            # A similar sentence missing the original qualifier is not an exact
            # duplicate. Semantic repetition remains the model reviewer's job.
            self.assertEqual(1, len(article.bullet_points))
            self.assertGreaterEqual(len(article.highlights), 2)
            self.assertTrue(
                all(highlight.casefold() in article_text for highlight in article.highlights)
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

    def test_short_script_is_not_expanded_just_to_fill_time(self):
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
            word_count=150,
            to_dict=lambda: {"word_count": 150},
        )
        antigravity = Mock()
        antigravity.invoke.return_value = (
            short_script,
            AntigravityMetadata(input_tokens=10, output_tokens=20),
        )
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

        self.assertIs(result, short_script)
        self.assertEqual(10, metadata.input_tokens)
        self.assertEqual(20, metadata.output_tokens)
        self.assertEqual(1, antigravity.invoke.call_count)
        instruction, payload = antigravity.invoke.call_args.args[:2]
        self.assertIn("NOT a minimum", instruction)
        self.assertLess(payload["episode_budget"]["max_words"], 1000)
        self.assertEqual(
            ["verified-ai"],
            payload["required_story_ids"],
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
                "story_id": "priority-ai-percent",
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
                        "The documented system moved from controlled testing into live operations."
                    ),
                    "story_ids": ["priority-ai-percent"],
                },
                {
                    "value": "IMPACT",
                    "label": "Operational measurement now matters",
                    "detail": (
                        "Production use makes reliable performance evidence "
                        "materially more consequential."
                    ),
                    "story_ids": ["priority-ai-percent"],
                },
                {
                    "value": "WATCH",
                    "label": "Results remain the open test",
                    "detail": (
                        "The supplied evidence does not yet establish long-term production results."
                    ),
                    "story_ids": ["priority-ai-percent"],
                },
            ],
            "articles": [
                {
                    "section_label": "AI",
                    "title": "A measured deployment",
                    "standfirst": "A system moved into production.",
                    "body": "The source documented the operational change.",
                    "story_ids": ["priority-ai-percent"],
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
                            "story_ids": ["priority-ai-percent"],
                        },
                        {
                            "value": "WATCH",
                            "label": "Performance evidence",
                            "detail": "Results are still being measured.",
                            "story_ids": ["priority-ai-percent"],
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

        invented_citations = copy.deepcopy(data)
        tracker_url = "https://tracker.invalid/click/opaque-token"
        invented_citations["articles"][0]["source_urls"] = [tracker_url]
        invented_citations["briefs"] = [
            {
                "text": "The live deployment creates a separate measurement requirement.",
                "story_ids": ["priority-ai-percent"],
                "source_urls": [tracker_url],
            }
        ]
        invented_citations["visuals"][0]["source_urls"] = [tracker_url]
        invented_citations["sources"] = [f"Tracker - {tracker_url}"]
        repaired_citations = validator(invented_citations)
        self.assertEqual(
            ["https://example.com/report"],
            repaired_citations.articles[0].source_urls,
        )
        self.assertEqual(
            ["https://example.com/report"],
            repaired_citations.briefs[0].source_urls,
        )
        self.assertEqual(
            ["https://example.com/report"],
            repaired_citations.visuals[0].source_urls,
        )
        self.assertEqual(
            ["https://example.com/report"],
            repaired_citations.sources,
        )

        unsupported_citation = copy.deepcopy(invented_citations)
        unsupported_citation["briefs"][0]["story_ids"] = ["unsupported-story"]
        with self.assertRaisesRegex(ValueError, "unsupported story IDs"):
            validator(unsupported_citation)

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
