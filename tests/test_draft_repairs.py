import copy
from unittest import TestCase

from audiodigest.antigravity_client import _validation_code
from audiodigest.draft_repairs import draft_repair_plan


class DraftRepairTests(TestCase):
    def test_shape_and_turn_repairs_remain_bounded_and_preserve_material(self):
        for code in ("script_shape", "script_section_name", "script_duplicate_section",
                     "model_dialogue_shape", "script_turn_length", "source_evidence"):
            plan = draft_repair_plan({}, None, code)
            self.assertTrue(plan["validation_still_required"])
            self.assertTrue(plan["never_expand_to_reach_a_minimum"])
        self.assertIn("Never manufacture", draft_repair_plan({}, {}, "source_evidence")["action"])

    def test_extraction_shape_repair_does_not_accidentally_request_host_dialogue(self):
        plan = draft_repair_plan({"sources": []}, {}, "model_required_field")
        self.assertIn("not a podcast script", plan["action"])
        self.assertIn("facts and evidence_refs", plan["action"])
        self.assertNotIn("nonempty dialogue", plan["action"])

    def test_shared_field_codes_keep_paper_and_verification_on_their_own_schema(self):
        for payload in ({"stories": []}, {"script": {}}):
            plan = draft_repair_plan(payload, {}, "model_required_field")
            self.assertIn("own requested schema", plan["action"])
            self.assertNotIn("nonempty dialogue arrays", plan["action"])
        plan = draft_repair_plan({}, {}, "script_reference_url")
        self.assertIn("Fix only show_notes", plan["action"])
    def test_actual_evidence_ceiling_error_uses_compression_not_expansion(self):
        code = _validation_code(ValueError(
            "script contains 3500 words; the evidence-sized maximum is 3400"
        ))
        self.assertEqual("script_length", code)
        plan = draft_repair_plan({"episode_budget": {"max_words": 3400}}, {}, code)
        self.assertEqual(3400, plan["maximum_words"])
        self.assertIn("do not add words", plan["action"])
        self.assertTrue(plan["validation_still_required"])

    def test_missing_coverage_is_mapped_from_trusted_contract_not_rejected_draft(self):
        payload = {"required_story_ids": ["one", "two"], "output_contract": {
            "sections": [{"name": "AI", "required_story_ids": ["one"]},
                         {"name": "DATA", "required_story_ids": ["two"]}]}}
        response = {"sections": [{"name": "wrong", "story_ids": ["one", "unknown"]}]}
        before = copy.deepcopy((payload, response))
        plan = draft_repair_plan(payload, response, "script_coverage")
        self.assertEqual([{"name": "DATA", "story_ids": ["two"]}],
                         plan["missing_required_by_section"])
        self.assertNotIn("unknown", str(plan))
        self.assertIn("not merely attach IDs", plan["action"])
        self.assertEqual(before, (payload, response))

    def test_optional_closing_fix_does_not_request_a_whole_script_rewrite(self):
        plan = draft_repair_plan({}, {}, "script_closing_comment")
        self.assertIn("Fix only closing_comment", plan["action"])
        self.assertIn("empty list is allowed", plan["action"])
        self.assertIn("do not rewrite them", plan["action"])

    def test_malformed_response_requires_complete_json_not_guessed_text(self):
        for code in ("json_invalid", "model_incomplete"):
            plan = draft_repair_plan({}, None, code)
            self.assertIn("Do not guess", plan["action"])
            self.assertNotIn("missing_required_by_section", plan)

    def test_generic_and_malformed_contracts_never_waive_validation(self):
        for payload in ({}, {"output_contract": []}, {"required_story_ids": None}):
            for code in ("script_coverage", "model_structure", "script_length"):
                plan = draft_repair_plan(payload, None, code)
                self.assertTrue(plan["validation_still_required"])
                self.assertTrue(plan["never_expand_to_reach_a_minimum"])
        self.assertNotIn("maximum_words", draft_repair_plan(
            {"episode_budget": {"max_words": True}}, {}, "script_length"
        ))
