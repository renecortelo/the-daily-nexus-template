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
    _normalize_script_closing,
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
        for key in ("introduction", "conclusion", "closing_comment"):
            self.assertEqual({"Nox"}, {t["host"] for t in example[key]})
        self.assertIsNone(payload["output_contract"]["maximum_words_per_turn"])

    def test_listening_guide_preserves_facts_names_and_delivery_modes_without_extra_calls(self):
        for count, style in ((1, "broadcast"), (2, "broadcast"), (2, "conversation")):
            with self.subTest(count=count, style=style):
                instruction, _payload = self.script_request(self.engine(count=count, style=style))
                self.assertIn("subject and action before a cluster of figures", instruction)
                self.assertIn("each exact figure", instruction)
                self.assertIn("not echo the", instruction)
                self.assertIn("without simulated exchanges", instruction)
                self.assertIn("original spelling", instruction)
                self.assertIn(
                    "Do not modernize or paraphrase the approved closing quotation", instruction,
                )
                self.assertIn("hard\ndelivery ceiling of 30 minutes", instruction)

    def test_listening_review_is_advisory_and_keeps_original_evidence_gate(self):
        engine = self.engine()
        engine.antigravity.invoke.return_value = (Mock(issues=[]), Mock())
        script = Mock()
        script.sections = []
        script.show_notes = []
        script.to_dict.return_value = {"title": "Fictional example"}
        engine.verify([], script, ClosingQuote("Learn.", "An Author", "https://example.com/quote"))
        self.assertEqual(1, engine.antigravity.invoke.call_count)
        instruction = engine.antigravity.invoke.call_args.args[0]
        self.assertIn("listening advice, not grounds to reject", instruction)
        self.assertIn("original source_evidence.passages", instruction)
        self.assertIn("Set factual_approved explicitly", instruction)
        self.assertIn("Do not demand phonetic respellings", instruction)

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

    def closing_draft(self):
        engine = self.engine(count=1, solo="Nox")
        instruction, _payload = self.script_request(engine, [story("first")])
        draft = json.loads(instruction.split("Return JSON only:\n", 1)[1])
        draft["introduction"] = [{
            "host": "Nox", "text": "I'm Nox. Edited and produced by Dario Novelli.",
        }]
        draft["sections"][0].update(name="AI", story_ids=["first"])
        draft["show_notes"] = []
        return draft, engine.antigravity.invoke.call_args.args[2]

    def test_missing_quote_author_and_source_are_inserted_without_another_model_call(self):
        draft, validate = self.closing_draft()
        draft["closing_comment"] = [{
            "host": "Nox",
            "text": "The pilot remains limited; optimism still needs a test environment.",
        }]
        result = validate(draft)
        self.assertEqual('Learn "carefully". — An Author.', result.sign_off[0].text)
        self.assertEqual(draft["closing_comment"][0]["text"], result.sign_off[1].text)
        self.assertIn("https://example.com/quote", result.show_notes[0])
        self.assertNotIn("closing_comment", result.to_dict())
        self.assertEqual([], draft["show_notes"])
        self.assertNotIn("sign_off", draft)

    def test_empty_or_absent_comment_still_produces_the_approved_closing(self):
        for omitted in (False, True):
            draft, validate = self.closing_draft()
            draft["closing_comment"] = []
            if omitted:
                del draft["closing_comment"]
            with self.subTest(omitted=omitted):
                result = validate(draft)
                self.assertEqual(1, len(result.sign_off))
                self.assertEqual('Learn "carefully". — An Author.', result.sign_off[0].text)

    def test_legacy_wrong_or_incomplete_quote_never_becomes_an_original_observation(self):
        for legacy in ("A paraphrased quote from a wrong author.",
                       'Learn "carefully". Missing attribution.'):
            draft, validate = self.closing_draft()
            del draft["closing_comment"]
            draft["sign_off"] = legacy
            with self.subTest(legacy=legacy):
                result = validate(draft)
                self.assertEqual(1, len(result.sign_off))
                self.assertEqual('Learn "carefully". — An Author.', result.sign_off[0].text)

    def test_legacy_separate_comment_survives_replaced_quote_and_no_duplicate_source(self):
        draft, validate = self.closing_draft()
        del draft["closing_comment"]
        draft["sign_off"] = [
            {"host": "Nox", "text": "A paraphrased quote from a wrong author."},
            {"host": "Nox", "text": "The pilot still needs evidence, not applause."},
        ]
        draft["show_notes"] = ["Quotation - https://example.com/quote"]
        result = validate(draft)
        self.assertEqual('Learn "carefully". — An Author.', result.sign_off[0].text)
        self.assertEqual(draft["sign_off"][1]["text"], result.sign_off[1].text)
        self.assertEqual(draft["show_notes"], result.show_notes)
        # Re-normalization of a stored script is stable.
        again = validate(result.to_dict())
        self.assertEqual(result.to_dict(), again.to_dict())

    def test_legacy_combined_exact_quote_and_comment_remains_supported(self):
        draft, validate = self.closing_draft()
        del draft["closing_comment"]
        draft["sign_off"] = 'Learn "carefully". — An Author. The pilot needs evidence.'
        result = validate(draft)
        self.assertEqual("The pilot needs evidence.", result.sign_off[1].text)

    def test_long_comment_is_omitted_without_cutting_or_rejecting_the_script(self):
        draft, validate = self.closing_draft()
        draft["closing_comment"] = [{"host": "Nox", "text": " ".join(["detail"] * 80) + "."}]
        result = validate(draft)
        self.assertEqual(1, len(result.sign_off))
        self.assertIn("An Author", result.sign_off[0].text)

    def test_copied_approved_quote_is_not_spoken_twice(self):
        draft, validate = self.closing_draft()
        draft["closing_comment"] = [{"host": "Nox", "text": 'Learn "carefully". — An Author.'}]
        self.assertEqual(1, len(validate(draft).sign_off))

    def test_unknown_comment_host_and_unsupported_sources_remain_rejected(self):
        draft, validate = self.closing_draft()
        draft["closing_comment"] = [{"host": "Unconfigured", "text": "A brief observation."}]
        with self.assertRaisesRegex(ValueError, "host"):
            validate(draft)
        draft["closing_comment"] = []
        draft["show_notes"] = ["Invented source - https://example.com/unsupported"]
        with self.assertRaisesRegex(ValueError, "not supported"):
            validate(draft)

    def test_malformed_comment_and_notes_remain_structural_errors(self):
        for comment in ("Not a dialogue list", [None], [{"host": "Nox"}],
                        [{"host": "Nox", "text": "One."}] * 2):
            draft, validate = self.closing_draft()
            draft["closing_comment"] = comment
            with self.subTest(comment=comment), self.assertRaisesRegex(
                ValueError, "closing_comment",
            ):
                validate(draft)
        draft, validate = self.closing_draft()
        draft["show_notes"] = [None]
        with self.assertRaisesRegex(ValueError, "show_notes"):
            validate(draft)

    def test_closing_normalizer_keeps_input_and_host_assignment_stable(self):
        quote = ClosingQuote("Learn.", "An Author", "https://example.com/quote")
        for hosts in (["Dalia"], ["Nox"], ["Dalia", "Nox"]):
            comment = [{"host": hosts[-1], "text": "The pilot needs evidence."}]
            raw = {"closing_comment": comment}
            normalized = _normalize_script_closing(raw, quote, hosts)
            self.assertEqual(hosts[0], normalized["sign_off"][0]["host"])
            self.assertEqual(hosts[-1], normalized["sign_off"][1]["host"])
            self.assertEqual({"closing_comment": comment}, raw)

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
