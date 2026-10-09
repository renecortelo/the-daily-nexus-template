import json
from datetime import date
from unittest import TestCase
from unittest.mock import Mock

from audiodigest.closing_quotes import ClosingQuote
from audiodigest.config import load_settings
from audiodigest.constants import AI_DISCLOSURE
from audiodigest.editorial import (
    CONVERSATION_TURN_MAX_WORDS,
    EditorialPipeline,
    _script_output_contract,
)
from audiodigest.models import Story


def story(identifier, section="AI"):
    return Story.from_dict({
        "story_id": identifier, "section": section, "headline": "A documented pilot",
        "facts": ["The source documented a limited pilot."],
        "why_it_matters": "The source describes a controlled test.",
        "source_ids": ["private-mailbox-identity"], "source_urls": [],
        "confidence": 0.9, "rank_score": 5,
    }, allowed_sections=(section,))


class EditorialDraftContractTests(TestCase):
    def engine(self, count=2, solo="Dalia", style="conversation"):
        settings = load_settings("config.example.toml")
        settings.hosts.count = count
        settings.hosts.solo_name = solo
        settings.hosts.dialogue_style = style
        client = Mock()
        client.invoke.return_value = (Mock(word_count=200), Mock())
        return EditorialPipeline(settings, client)

    def script_request(self, engine, stories=None):
        engine.generate_script(
            stories or [], date(2026, 10, 9),
            ClosingQuote('Learn "carefully".', "An Author", "https://example.com/quote"),
        )
        self.assertEqual(1, engine.antigravity.invoke.call_count)
        call = engine.antigravity.invoke.call_args
        return call.args[0], call.args[1]

    def test_script_json_example_is_valid_and_introduces_both_real_hosts(self):
        instruction, payload = self.script_request(self.engine())
        example = json.loads(instruction.split("Return JSON only:\n", 1)[1])
        self.assertEqual(AI_DISCLOSURE, example["disclosure"])
        self.assertEqual(["Dalia", "Nox"], example["hosts"])
        self.assertEqual(["Dalia", "Nox"], [t["host"] for t in example["introduction"]])
        self.assertNotIn('"host": "exact active host"', instruction)
        self.assertEqual(CONVERSATION_TURN_MAX_WORDS,
                         payload["output_contract"]["maximum_words_per_turn"])
        self.assertIn(f"exceed {CONVERSATION_TURN_MAX_WORDS} words", instruction)

    def test_solo_nox_example_never_suggests_a_missing_host(self):
        instruction, payload = self.script_request(self.engine(count=1, solo="Nox"))
        example = json.loads(instruction.split("Return JSON only:\n", 1)[1])
        self.assertEqual(["Nox"], example["hosts"])
        for key in ("introduction", "conclusion", "sign_off"):
            self.assertEqual({"Nox"}, {t["host"] for t in example[key]})
        self.assertIsNone(payload["output_contract"]["maximum_words_per_turn"])

    def test_contract_orders_only_nonempty_sections_and_maps_exact_coverage(self):
        stories = [story("first"), story("second"), story("third", "DATA")]
        contract = _script_output_contract(
            stories, ("DATA", "Empty", "AI"), ["first", "third"],
            ["Dalia", "Nox"], "conversation",
        )
        self.assertEqual(["DATA", "AI"], [s["name"] for s in contract["sections"]])
        self.assertEqual(["third"], contract["sections"][0]["required_story_ids"])
        self.assertEqual(["first"], contract["sections"][1]["required_story_ids"])
        self.assertEqual(["first", "second"],
                         contract["sections"][1]["available_story_ids"])
        self.assertTrue(all(s["both_hosts_required_if_two_or_more_stories_cited"]
                            for s in contract["sections"]))
        self.assertNotIn("private-mailbox-identity", json.dumps(contract))
        self.assertEqual(1, contract["introduction"]["editor_credit_occurrences"])

    def test_broadcast_contract_does_not_apply_conversation_only_limits(self):
        contract = _script_output_contract(
            [story("first")], ("AI",), ["first"], ["Dalia", "Nox"], "broadcast",
        )
        self.assertIsNone(contract["maximum_words_per_turn"])
        self.assertFalse(contract["sections"][0][
            "both_hosts_required_if_two_or_more_stories_cited"])

    def test_contract_never_bypasses_existing_coverage_or_host_validation(self):
        engine = self.engine(count=1, solo="Nox")
        instruction, _payload = self.script_request(engine, [story("first")])
        example = json.loads(instruction.split("Return JSON only:\n", 1)[1])
        example["introduction"] = [{
            "host": "Nox", "text": "I'm Nox. Edited and produced by Dario Novelli.",
        }]
        example["sections"][0].update(name="AI", story_ids=["first"])
        example["sign_off"] = [{"host": "Nox", "text": 'Learn "carefully". — An Author.'}]
        example["show_notes"] = ["Quotation - https://example.com/quote"]
        validate = engine.antigravity.invoke.call_args.args[2]
        self.assertEqual(["Nox"], validate(example).hosts)
        example["sections"][0]["story_ids"] = ["unrelated"]
        with self.assertRaisesRegex(ValueError, "unsupported story IDs"):
            validate(example)
        example["sections"][0]["story_ids"] = ["first"]
        example["hosts"] = ["Dalia"]
        with self.assertRaisesRegex(ValueError, "host list|unknown host|active host"):
            validate(example)

    def test_json_example_escapes_configured_host_names(self):
        engine = self.engine(count=1)
        engine.settings.hosts.primary_name = 'Host "A"'
        engine.settings.hosts.solo_name = 'Host "A"'
        instruction, _payload = self.script_request(engine)
        example = json.loads(instruction.split("Return JSON only:\n", 1)[1])
        self.assertEqual(['Host "A"'], example["hosts"])
        self.assertEqual('Host "A"', example["introduction"][0]["host"])

    def test_paper_shape_has_three_labels_and_one_concrete_visual_kind(self):
        engine = self.engine()
        engine.generate_newspaper([story("first")], date(2026, 10, 9))
        self.assertEqual(1, engine.antigravity.invoke.call_count)
        instruction = engine.antigravity.invoke.call_args.args[0]
        example = json.loads(instruction.split("Return JSON only:\n", 1)[1])
        self.assertEqual(["SHIFT", "IMPACT", "WATCH"],
                         [s["value"] for s in example["executive_summary"]])
        self.assertEqual("news_grid", example["visuals"][0]["kind"])
        self.assertIn("Use 12-32 words", instruction)
        self.assertIn("Use 2-12 words", instruction)
        self.assertIn("small evidence set", instruction)
        self.assertNotIn("previous_script", engine.antigravity.invoke.call_args.args[1])

    def test_broad_paper_retains_original_word_guide_and_factual_review_notice(self):
        engine = self.engine()
        engine.generate_newspaper([story(f"story-{i}") for i in range(5)], date(2026, 10, 9))
        instruction = engine.antigravity.invoke.call_args.args[0]
        self.assertIn("Aim for 760-980 words", instruction)
        self.assertNotIn("small evidence set", instruction)
        self.assertIn("Factual review follows", instruction)
        self.assertEqual(1, engine.antigravity.invoke.call_count)
