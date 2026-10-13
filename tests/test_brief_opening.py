import copy
import json
from dataclasses import replace
from datetime import date
from unittest import TestCase
from unittest.mock import Mock

from test_source_evidence import supported_story

from audiodigest.closing_quotes import ClosingQuote
from audiodigest.config import load_settings
from audiodigest.constants import AI_DISCLOSURE
from audiodigest.editorial import EditorialPipeline
from audiodigest.episode_budget import plan_episode
from audiodigest.models import AntigravityMetadata, EpisodeScript, Story
from audiodigest.script_contract import _brief_introduction


class BriefOpeningTests(TestCase):
    day = date(2031, 1, 6)

    def fixture(self, hosts=("Dalia",), style="broadcast", ceiling=663, newsletters=2):
        settings = load_settings("config.example.toml")
        settings.hosts.count = len(hosts)
        settings.hosts.solo_name = hosts[0]
        settings.hosts.dialogue_style = style
        settings.podcast.sections = ()
        story = Story.from_dict(
            {
                "story_id": "documented-pilot",
                "section": "AI",
                "headline": "Documented pilot",
                "facts": ["The team reported a provisional result of 12%."],
                "why_it_matters": "The report describes a limited pilot.",
                "source_ids": ["fictional-source"],
                "source_urls": ["https://example.com/pilot"],
                "confidence": 0.9,
                "rank_score": 5,
            },
            allowed_sections=("AI",),
        )
        budget = replace(
            plan_episode([story], settings.app), max_words=ceiling, newsletter_count=newsletters
        )
        engine = EditorialPipeline(settings, Mock())
        quote = ClosingQuote("Learn carefully.", "An Author", "https://example.com/quote")
        raw = {
            "title": "The Daily Nexus",
            "hosts": list(hosts),
            "introduction": [{"host": host, "text": f"I'm {host}."} for host in hosts],
            "sections": [
                {
                    "name": "AI",
                    "story_ids": [story.story_id],
                    "dialogue": [{"host": host, "text": story.facts[0]} for host in hosts],
                }
            ],
            "conclusion": [{"host": hosts[-1], "text": "Thanks for listening."}],
            "closing_comment": [{"host": hosts[-1], "text": "A pilot is not a crystal ball."}],
            "show_notes": [],
            "disclosure": AI_DISCLOSURE,
        }
        raw["introduction"][0]["text"] += " Edited and produced by Dario Novelli."
        return engine, story, quote, budget, raw

    def generate(self, engine, story, quote, budget, raw, previous=None):
        engine.antigravity.invoke.side_effect = lambda _i, _p, validator, **_k: (
            validator(copy.deepcopy(raw)),
            AntigravityMetadata(),
        )
        kwargs = (
            {"previous_script": previous, "repair_issues": ["Correct the news figure."]}
            if previous is not None
            else {}
        )
        return engine.generate_script([story], self.day, quote, episode_budget=budget, **kwargs)[0]

    def test_solo_and_duo_modes_keep_exact_identity_date_and_one_credit(self):
        for hosts, style in (
            (["Dalia"], "broadcast"),
            (["Nox"], "broadcast"),
            (["Dalia", "Nox"], "broadcast"),
            (["Dalia", "Nox"], "conversation"),
        ):
            with self.subTest(hosts=hosts, style=style):
                engine, story, quote, budget, raw = self.fixture(hosts, style)
                before = copy.deepcopy(raw)
                # A verbose legacy model opening cannot become spoken news/agenda.
                raw["introduction"][0]["text"] += " " + "Unnecessary greeting. " * 60
                result = self.generate(engine, story, quote, budget, raw)
                expected = _brief_introduction(hosts, self.day)
                self.assertEqual(expected, [turn.to_dict() for turn in result.introduction])
                self.assertLessEqual(len(result.introduction_text.split()), 24)
                self.assertEqual(1, result.introduction_text.count("Dario Novelli"))
                self.assertIn("January 6, 2031", result.introduction_text)
                for host in hosts:
                    self.assertIn(f"I'm {host}.", result.introduction_text)
                self.assertEqual(before["sections"], result.to_dict()["sections"])
                self.assertEqual(before["conclusion"], result.to_dict()["conclusion"])
                self.assertEqual(AI_DISCLOSURE, result.disclosure)
                self.assertEqual(f"{quote.text} — {quote.author}.", result.sign_off[0].text)
                self.assertEqual(before["closing_comment"][0], result.sign_off[1].to_dict())
                self.assertIn("https://example.com/pilot", result.show_notes[0])
                call = engine.antigravity.invoke.call_args
                self.assertEqual(1, engine.antigravity.invoke.call_count)
                self.assertEqual(1, call.kwargs["retries"])
                self.assertEqual(story.to_dict()["facts"], call.args[1]["stories"][0]["facts"])
                self.assertTrue(
                    call.args[1]["output_contract"]["introduction"]["application_owned"]
                )
                self.assertIn("All supported news facts, qualifiers and attribution", call.args[0])
                example = json.loads(call.args[0].split("Return JSON only:\n", 1)[1])
                self.assertEqual(expected, example["introduction"])
                self.assertEqual(AI_DISCLOSURE, example["disclosure"])

    def test_threshold_is_evidence_budget_not_number_of_newsletters(self):
        for ceiling, newsletters, owns in (
            (1300, 30, True),
            (1301, 1, False),
            (3400, 30, False),
            (260, 30, True),
        ):
            with self.subTest(ceiling=ceiling, newsletters=newsletters):
                engine, story, quote, budget, raw = self.fixture(
                    ceiling=ceiling,
                    newsletters=newsletters,
                )
                raw["introduction"][0]["text"] += " Here is a supported editorial preview."
                result = self.generate(engine, story, quote, budget, raw)
                intro_contract = engine.antigravity.invoke.call_args.args[1]["output_contract"][
                    "introduction"
                ]
                self.assertEqual(owns, intro_contract["application_owned"])
                self.assertEqual(
                    _brief_introduction(["Dalia"], self.day) if owns else raw["introduction"],
                    [t.to_dict() for t in result.introduction],
                )

    def test_legacy_full_repair_does_not_silently_discard_introductory_reporting(self):
        engine, story, quote, budget, raw = self.fixture()
        raw["introduction"][0]["text"] += " The source reports a limited pilot."
        normalized = {
            **raw,
            "sign_off": [
                {"host": "Dalia", "text": f"{quote.text} — {quote.author}."},
            ],
        }
        previous = EpisodeScript.from_dict(normalized)
        result = self.generate(engine, story, quote, budget, raw, previous)
        self.assertEqual(previous.introduction, result.introduction)
        self.assertFalse(
            engine.antigravity.invoke.call_args.args[1]["output_contract"]["introduction"][
                "application_owned"
            ]
        )

    def test_compact_full_repair_keeps_opening_and_existing_news_corrections(self):
        engine, story, quote, budget, raw = self.fixture()
        previous = self.generate(engine, story, quote, budget, raw)
        revised = previous.to_dict()
        revised["introduction"][0]["text"] += " An unnecessary new agenda."
        revised["sections"][0]["dialogue"][0]["text"] = (
            "The source calls its 12% result provisional."
        )
        result = self.generate(engine, story, quote, budget, revised, previous)
        self.assertEqual(previous.introduction, result.introduction)
        self.assertEqual(revised["sections"], result.to_dict()["sections"])
        self.assertEqual(previous.sign_off, result.sign_off)
        self.assertEqual(2, engine.antigravity.invoke.call_count)

    def test_compact_patch_preserves_every_nonnews_field_exactly(self):
        engine, story, quote, budget, raw = self.fixture()
        previous = self.generate(engine, story, quote, budget, raw)
        before = copy.deepcopy(previous.to_dict())
        proposal = {
            "edits": [
                {
                    "section_index": 0,
                    "turn_index": 0,
                    "expected": previous.sections[0].dialogue[0].text,
                    "replacement": "The team reports a provisional pilot result of 12%.",
                }
            ]
        }
        result = self.generate(engine, story, quote, budget, proposal, previous)
        after = result.to_dict()
        for field in ("introduction", "disclosure", "sign_off", "conclusion", "hosts", "title"):
            self.assertEqual(before[field], after[field])
        self.assertEqual(before["sections"][0]["story_ids"], after["sections"][0]["story_ids"])

    def test_missing_story_or_invalid_news_host_still_fails_before_audio(self):
        for defect in ("missing_id", "wrong_host", "over_budget"):
            with self.subTest(defect=defect):
                engine, story, quote, budget, raw = self.fixture()
                if defect == "missing_id":
                    raw["sections"][0]["story_ids"] = ["invented-story"]
                elif defect == "wrong_host":
                    raw["sections"][0]["dialogue"][0]["host"] = "Unknown"
                else:
                    raw["sections"][0]["dialogue"][0]["text"] = "word " * 800
                with self.assertRaises(ValueError):
                    self.generate(engine, story, quote, budget, raw)

    def test_factual_review_still_runs_against_the_actual_compact_script(self):
        engine, story, quote, budget, raw = self.fixture()
        story, index = supported_story()
        engine._source_evidence = index
        raw["sections"][0]["story_ids"] = [story.story_id]
        raw["sections"][0]["dialogue"][0]["text"] = story.facts[0]
        budget = replace(plan_episode([story], engine.settings.app), max_words=663)
        script = self.generate(engine, story, quote, budget, raw)

        def review(_instruction, payload, validator, **kwargs):
            self.assertEqual(script.to_dict(), payload["script"])
            self.assertTrue(payload["source_evidence"]["passages"])
            self.assertEqual(1, kwargs["retries"])
            return validator(
                {
                    "approved": False,
                    "factual_approved": False,
                    "issues": ["A fictional unsupported claim needs correction."],
                }
            ), AntigravityMetadata()

        engine.antigravity.invoke.side_effect = review
        verdict, _ = engine.verify([story], script, quote)
        self.assertFalse(verdict.factually_safe)
        self.assertEqual(2, engine.antigravity.invoke.call_count)

    def test_missing_original_evidence_is_not_bypassed_by_a_compact_opening(self):
        engine, story, quote, budget, raw = self.fixture()
        script = self.generate(engine, story, quote, budget, raw)
        with self.assertRaises(ValueError):
            engine.verify([story], script, quote)
        self.assertEqual(1, engine.antigravity.invoke.call_count)

    def test_calendar_months_and_leap_day_are_explicit_english_not_system_locale(self):
        for month, expected in ((1, "January"), (12, "December")):
            self.assertIn(expected, _brief_introduction(["Nox"], date(2031, month, 1))[0]["text"])
        self.assertIn(
            "February 29, 2032", _brief_introduction(["Nox"], date(2032, 2, 29))[0]["text"]
        )
