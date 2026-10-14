import copy
import io
import json
import subprocess
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from test_source_evidence import supported_story, synthetic_source

from audiodigest.antigravity_client import AntigravityCLI, AntigravityCLIError
from audiodigest.closing_quotes import ClosingQuote
from audiodigest.config import AntigravitySettings, load_settings
from audiodigest.constants import AI_DISCLOSURE
from audiodigest.draft_repairs import draft_repair_plan
from audiodigest.editorial import EditorialPipeline
from audiodigest.episode_budget import plan_episode
from audiodigest.models import AntigravityMetadata, DataValidationError, EpisodeScript
from audiodigest.pipeline import Pipeline, VerificationError
from audiodigest.progress import ProgressReporter, safe_event
from audiodigest.script_patches import apply_script_edits, script_repair_contract


def draft(hosts=None):
    hosts = hosts or ["Dalia"]
    return {
        "title": "The Daily Nexus", "hosts": hosts,
        "introduction": [{"host": host, "text": f"I'm {host}."} for host in hosts],
        "sections": [{"name": "AI", "story_ids": ["product-pilot"], "dialogue": [
            {"host": hosts[0], "text": "The pilot grew 35%."},
            {"host": hosts[-1], "text": "The product is not yet available to everyone."},
        ]}],
        "conclusion": [{"host": hosts[-1], "text": "Thanks for listening."}],
        "sign_off": [
            {"host": hosts[-1], "text": "Learn carefully. — An Author."},
            {"host": hosts[0], "text": "A pilot is not a crystal ball."},
        ],
        "show_notes": [], "disclosure": AI_DISCLOSURE,
    }


def edit(text="The team reported provisional pilot growth of 12% on October 8.",
         *, expected="The pilot grew 35%.", turn=0):
    return {"section_index": 0, "turn_index": turn,
            "expected": expected, "replacement": text}


class ScriptPatchShapeTests(TestCase):
    def test_only_requested_text_changes_and_original_remains_untouched(self):
        original = draft()
        before = copy.deepcopy(original)
        result = apply_script_edits(original, {"edits": [edit()]})
        expected = copy.deepcopy(before)
        expected["sections"][0]["dialogue"][0]["text"] = edit()["replacement"]
        self.assertEqual(expected, result)
        self.assertEqual(before, original)

    def test_bad_batches_are_rejected_atomically_with_fixed_private_safe_errors(self):
        original = draft()
        before = copy.deepcopy(original)
        proposals = [None, [], {}, {"edits": []}, {"edits": None},
                     {"edits": [None]}, {"edits": [edit()] * 13},
                     {"edits": [edit(), edit()]},
                     {"edits": [edit(), edit(expected="stale private anchor", turn=1)]},
                     {"edits": [edit()], "title": "override"}]
        for field in ("host", "story_ids", "url", "path", "code", "introduction"):
            proposals.append({"edits": [{**edit(), field: "forbidden"}]})
        for field in ("section_index", "turn_index"):
            for invalid in (True, False, -1, 99, "0", None, 0.0, [], {}):
                proposals.append({"edits": [{**edit(), field: invalid}]})
        for field in ("expected", "replacement"):
            for invalid in (None, [], {}, 9, "", " ", "x" * 10001):
                proposals.append({"edits": [{**edit(), field: invalid}]})
        for invalid in ("The pilot grew 35%.", " leading space", "trailing space "):
            proposals.append({"edits": [edit(invalid)]})
        proposals.append({"edits": [{"section_index": 0, "turn_index": 0,
                                     "replacement": "missing anchor"}]})
        for proposal in proposals:
            with self.subTest(proposal=proposal), self.assertRaises(DataValidationError) as error:
                apply_script_edits(original, proposal)
            self.assertEqual("script_shape", error.exception.failure_code)
            self.assertEqual("script repair must use bounded exact news-turn replacements",
                             str(error.exception))
            self.assertEqual(before, original)

    def test_conversation_limit_is_enforced_without_cropping_or_splitting(self):
        proposal = {"edits": [edit(" ".join(["supported"] * 116))]}
        with self.assertRaises(DataValidationError):
            apply_script_edits(draft(), proposal, maximum_words_per_turn=115)
        # Solo/broadcast keeps its existing episode ceiling, not the conversation quota.
        result = apply_script_edits(draft(), proposal)
        self.assertEqual(116, len(result["sections"][0]["dialogue"][0]["text"].split()))

    def test_targets_are_current_draft_indexes_not_a_search_for_similar_text(self):
        original = draft()
        original["sections"][0]["dialogue"][1]["text"] = "The pilot grew 35%."
        result = apply_script_edits(original, {"edits": [edit(turn=1)]})
        self.assertEqual("The pilot grew 35%.", result["sections"][0]["dialogue"][0]["text"])
        self.assertEqual(edit()["replacement"], result["sections"][0]["dialogue"][1]["text"])

    def test_twelve_distinct_edits_are_allowed(self):
        original = draft()
        original["sections"][0]["dialogue"] = [
            {"host": "Dalia", "text": f"Old supported copy {i}."} for i in range(12)
        ]
        proposals = [edit(f"Corrected supported copy {i}.",
                          expected=f"Old supported copy {i}.", turn=i) for i in range(12)]
        result = apply_script_edits(original, {"edits": proposals})
        self.assertEqual(12, len(result["sections"][0]["dialogue"]))

    def test_retry_guidance_uses_the_union_contract_for_all_existing_error_categories(self):
        contract = script_repair_contract(115)
        schema = contract["preferred_response_schema"]
        self.assertFalse(schema["additionalProperties"])
        item = schema["properties"]["edits"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertEqual("integer", item["properties"]["section_index"]["type"])
        self.assertEqual("integer", item["properties"]["turn_index"]["type"])
        payload = {"repair_response_contract": contract}
        for code in ("json_invalid", "model_incomplete", "script_shape", "script_length",
                     "script_coverage", "script_turn_length", "script_closing_comment",
                     "script_editor_credit", "source_evidence", "model_structure"):
            plan = draft_repair_plan(payload, {"edits": [edit()]}, code)
            self.assertIn("exact expected text from previous_script", plan["action"])
            self.assertIn("complete script", plan["action"])
            self.assertTrue(plan["validation_still_required"])
            self.assertTrue(plan["never_expand_to_reach_a_minimum"])


class ScriptPatchEditorialTests(TestCase):
    def fixture(self, hosts=None, style="conversation"):
        settings = load_settings("config.example.toml")
        hosts = hosts or ["Dalia"]
        settings.hosts.count = len(hosts)
        settings.hosts.solo_name = hosts[0]
        settings.hosts.dialogue_style = style
        settings.podcast.sections = ()
        story, index = supported_story()
        engine = EditorialPipeline(settings, Mock())
        engine._source_evidence = index
        quote = ClosingQuote("Learn carefully.", "An Author", "https://example.com/quote")
        raw = draft(hosts)
        raw["introduction"][0]["text"] += " Dario Novelli edited and produced this edition."
        previous = EpisodeScript.from_dict(raw)
        return engine, story, quote, previous

    def repair(self, engine, story, quote, previous, proposal, **kwargs):
        engine.antigravity.invoke.side_effect = lambda _i, _p, validator, **_k: (
            validator(copy.deepcopy(proposal)), AntigravityMetadata(),
        )
        return engine.generate_script(
            [story], date(2026, 10, 9), quote, previous_script=previous,
            repair_issues=["Use the original provisional 12% figure, not 35%."], **kwargs,
        )[0]

    def test_patch_preserves_closing_host_comment_coverage_and_other_valid_turns(self):
        for hosts, style in ((["Dalia"], "broadcast"), (["Nox"], "broadcast"),
                             (["Dalia", "Nox"], "broadcast"),
                             (["Dalia", "Nox"], "conversation")):
            with self.subTest(hosts=hosts, style=style):
                engine, story, quote, previous = self.fixture(hosts, style)
                before = copy.deepcopy(previous.to_dict())
                repaired = self.repair(engine, story, quote, previous, {"edits": [edit()]})
                self.assertEqual(before, previous.to_dict())
                self.assertEqual(before["sign_off"], repaired.to_dict()["sign_off"])
                for field in ("title", "hosts", "introduction", "conclusion", "disclosure"):
                    self.assertEqual(before[field], repaired.to_dict()[field])
                self.assertEqual(before["sections"][0]["story_ids"],
                                 repaired.to_dict()["sections"][0]["story_ids"])
                self.assertEqual(before["sections"][0]["dialogue"][1],
                                 repaired.to_dict()["sections"][0]["dialogue"][1])
                call = engine.antigravity.invoke.call_args
                self.assertEqual(1, engine.antigravity.invoke.call_count)
                self.assertEqual(1, call.kwargs["retries"])
                self.assertIn("repair_response_contract", call.args[1])
                self.assertEqual(before, call.args[1]["previous_script"])
                self.assertIn(
                    "provisionals", call.args[1]["source_evidence"]["passages"][0]["text"],
                )
                self.assertEqual(2, len(repaired.show_notes))

    def test_broader_full_script_response_works_in_the_same_single_call(self):
        engine, story, quote, previous = self.fixture()
        full = previous.to_dict()
        full["sections"][0]["dialogue"][0]["text"] = edit()["replacement"]
        full["introduction"][0]["text"] += " Here is a corrected introduction."
        result = self.repair(engine, story, quote, previous, full)
        self.assertIn("corrected introduction", result.introduction_text)
        self.assertEqual(1, engine.antigravity.invoke.call_count)

    def test_initial_draft_cannot_accept_edits_or_offer_patch_schema(self):
        engine, story, quote, _previous = self.fixture()
        engine.antigravity.invoke.side_effect = lambda _i, _p, validator, **_k: (
            validator({"edits": [edit()]}), AntigravityMetadata(),
        )
        with self.assertRaises(DataValidationError):
            engine.generate_script([story], date(2026, 10, 9), quote)
        self.assertNotIn("repair_response_contract", engine.antigravity.invoke.call_args.args[1])

    def test_cloud_adoption_counts_only_accepted_responses_and_never_private_anchors(self):
        engine, story, quote, previous = self.fixture()
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        reporter.start()
        try:
            self.repair(engine, story, quote, previous, {"edits": [edit()]})
            with self.assertRaises(DataValidationError):
                self.repair(engine, story, quote, previous, {
                    "edits": [edit(expected="stale synthetic private anchor")],
                })
            self.repair(engine, story, quote, previous, previous.to_dict())
            profile = reporter.finish("completed")
        finally:
            reporter.close()
        clean = safe_event(profile)
        self.assertEqual(1, clean["counters"]["script_patch_repairs"])
        self.assertEqual(1, clean["counters"]["script_full_repairs"])
        self.assertEqual(1, clean["counters"]["script_patch_edits"])
        self.assertGreater(clean["counters"]["script_full_response_bytes"],
                           clean["counters"]["script_patch_response_bytes"])
        self.assertNotIn("anchor", json.dumps(clean))
        self.assertNotIn("35%", json.dumps(clean))
        for invalid in (True, -1, float("inf"), "private text"):
            profile["counters"] = {"script_patch_repairs": invalid, "script_full_repairs": invalid}
            self.assertEqual({}, safe_event(profile)["counters"])

    def test_reconstructed_draft_still_obeys_episode_word_ceiling(self):
        engine, story, quote, previous = self.fixture()
        budget = replace(plan_episode([story], engine.settings.app), max_words=10)
        with self.assertRaisesRegex(ValueError, "evidence-sized maximum"):
            self.repair(engine, story, quote, previous, {"edits": [edit()]}, episode_budget=budget)

    def test_patch_cannot_bypass_previous_draft_host_credit_coverage_or_quote_checks(self):
        for field in ("hosts", "credit", "coverage", "quote"):
            with self.subTest(field=field):
                engine, story, quote, previous = self.fixture()
                if field == "hosts":
                    previous.hosts = ["Nox"]
                elif field == "credit":
                    previous.introduction[0].text = "I'm Dalia."
                elif field == "coverage":
                    previous.sections[0].story_ids = []
                else:
                    previous.sign_off[0].text = "A different quotation."
                with self.assertRaises((ValueError, DataValidationError)):
                    self.repair(engine, story, quote, previous, {"edits": [edit()]})

    def test_shape_valid_but_false_patch_receives_full_original_source_review(self):
        engine, story, quote, previous = self.fixture()
        unsupported = "The product is now available to everyone and growth is guaranteed."
        repaired = self.repair(engine, story, quote, previous, {"edits": [edit(unsupported)]})
        def review(instruction, payload, validator, **kwargs):
            self.assertEqual(1, kwargs["retries"])
            self.assertIn("original source_evidence.passages", instruction)
            self.assertIn(
                "encara no està disponible", payload["source_evidence"]["passages"][0]["text"],
            )
            self.assertEqual(repaired.to_dict(), payload["script"])
            return validator({"approved": False, "factual_approved": False,
                              "issues": ["The source does not support general availability."]}), \
                AntigravityMetadata()
        engine.antigravity.invoke.side_effect = review
        verification, _ = engine.verify([story], repaired, quote)
        self.assertFalse(verification.approved)
        self.assertFalse(verification.factually_safe)
        self.assertEqual(2, engine.antigravity.invoke.call_count)

    def test_later_patch_anchors_current_correction_not_the_superseded_draft(self):
        engine, story, quote, previous = self.fixture()
        first = self.repair(engine, story, quote, previous, {"edits": [edit()]})
        with self.assertRaises(DataValidationError):
            self.repair(engine, story, quote, first, {
                "edits": [edit("Provisional growth is 12%.")],
            })
        second = self.repair(engine, story, quote, first, {"edits": [edit(
            "The team reported provisional growth of 12% in the pilot.",
            expected=edit()["replacement"],
        )]})
        self.assertIn("12%", second.sections[0].dialogue[0].text)
        self.assertEqual(first.sections[0].dialogue[1], second.sections[0].dialogue[1])

    def test_pipeline_rejects_false_patches_before_rendering_or_publication(self):
        with tempfile.TemporaryDirectory() as name:
            engine, story, quote, previous = self.fixture()
            engine.settings.app.runtime_dir = Path(name)
            engine.settings.podcast.max_script_repairs = 2
            pipeline = Pipeline(engine.settings)
            pipeline.editorial = engine
            pipeline._load_sources = Mock(return_value=[synthetic_source()])
            pipeline._enrich_articles = Mock()
            engine.extract_stories = Mock(return_value=([story], AntigravityMetadata()))
            observed_drafts = []
            def invoke(_instruction, payload, validator, **kwargs):
                self.assertEqual(1, kwargs["retries"])
                if "script" in payload:
                    observed_drafts.append(payload["script"])
                    self.assertIn("provisionals",
                                  payload["source_evidence"]["passages"][0]["text"])
                    return validator({"approved": False, "factual_approved": False,
                                      "issues": ["Synthetic unsupported guarantee."]}), \
                        AntigravityMetadata()
                if payload["previous_script"] is None:
                    result = previous.to_dict()
                else:
                    old = payload["previous_script"]["sections"][0]["dialogue"][0]["text"]
                    result = {"edits": [edit(
                        f"Unsupported guaranteed growth claim {len(observed_drafts)}.",
                        expected=old,
                    )]}
                return validator(result), AntigravityMetadata()
            engine.antigravity.invoke.side_effect = invoke
            with (
                patch("audiodigest.pipeline.run_cost_guard"),
                patch("audiodigest.pipeline.select_closing_quote", return_value=quote),
                patch("audiodigest.pipeline.KokoroAudioRenderer") as renderer,
                patch("audiodigest.pipeline.FirebasePublisher") as publisher,
            ):
                with self.assertRaisesRegex(VerificationError, "unsupported guarantee"):
                    pipeline.run(requested_date=date(2026, 10, 9))
            self.assertEqual(3, len(observed_drafts))
            self.assertEqual(6, engine.antigravity.invoke.call_count)
            renderer.assert_not_called()
            publisher.assert_not_called()


class ScriptPatchCliTests(TestCase):
    def client(self, root):
        agent = root / "agent.md"
        agent.write_text("---\nname: audio-digest\n---\nRead only.", encoding="utf-8")
        settings_path = root / "settings.json"
        settings_path.write_text('{"useG1Credits":false,"enableTelemetry":false}')
        return AntigravityCLI(AntigravitySettings(
            executable="agy", workspace_dir=root / "workspace",
            settings_path=settings_path, agent_path=agent,
        ))

    def invoke_with_responses(self, client, responses, requests):
        responses = iter(responses)
        def process(command, **kwargs):
            request = next(Path(kwargs["cwd"]).glob("request-*.json"))
            requests.append(json.loads(request.read_text(encoding="utf-8"))["payload"])
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps({"response": json.dumps(next(responses))}), stderr="",
            )
        payload = {"previous_script": draft(),
                   "repair_response_contract": script_repair_contract(115)}
        with patch("audiodigest.antigravity_client.subprocess.run", side_effect=process):
            return client.invoke(
                "Return exact edits or a complete script.", payload,
                lambda value: apply_script_edits(payload["previous_script"], value), retries=1,
            )[0]

    def test_existing_single_cli_retry_preserves_current_anchors_and_patch_contract(self):
        with tempfile.TemporaryDirectory() as name:
            client = self.client(Path(name))
            requests = []
            result = self.invoke_with_responses(client, [
                {"edits": [edit(expected="stale synthetic anchor")]}, {"edits": [edit()]},
            ], requests)
            self.assertEqual(2, len(requests))
            self.assertEqual(requests[0]["previous_script"], requests[1]["previous_script"])
            self.assertIn("repair_response_contract", requests[1])
            self.assertIn("exact expected text from previous_script",
                          requests[1]["repair_plan"]["action"])
            self.assertEqual(edit()["replacement"], result["sections"][0]["dialogue"][0]["text"])

    def test_repeated_invalid_patches_stop_at_two_cli_responses_with_private_safe_code(self):
        with tempfile.TemporaryDirectory() as name:
            client = self.client(Path(name))
            requests = []
            with self.assertRaises(AntigravityCLIError) as error:
                self.invoke_with_responses(client, [
                    {"edits": [edit(expected="stale synthetic anchor")]},
                    {"edits": [edit(expected="stale synthetic anchor")]},
                ], requests)
            self.assertEqual(2, len(requests))
            self.assertEqual("script_shape", error.exception.failure_code)
            self.assertNotIn("stale synthetic anchor", str(error.exception))
