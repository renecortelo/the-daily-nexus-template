import io
import json
from datetime import date
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from audiodigest.audio import _speech_runtime_context
from audiodigest.config import load_settings
from audiodigest.editorial import EditorialPipeline
from audiodigest.models import Story
from audiodigest.progress import ProgressReporter, safe_event
from audiodigest.script_contract import _script_output_contract, allocate_script_words
from tests.test_source_evidence import extracted_story


class SpeedPlanningTests(TestCase):
    def test_word_plan_keeps_all_sections_and_more_room_for_more_distinct_facts(self):
        stories = [
            Story.from_dict(extracted_story(section="AI")),
            Story.from_dict(
                extracted_story(story_id="second", section="Data", facts=["One.", "Two.", "Three."])
            ),
        ]
        contract = _script_output_contract(
            stories, ("AI", "Data"), [s.story_id for s in stories], ["Dalia"], "broadcast"
        )
        for ceiling in (260, 1300, 3400):
            result = allocate_script_words(contract, stories, ceiling)
            rows = result["sections"]
            self.assertEqual(["AI", "Data"], [row["name"] for row in rows])
            self.assertGreater(rows[1]["planned_words"], rows[0]["planned_words"])
            self.assertEqual(
                result["planned_total_words"],
                result["shared_opening_closing_words"] + sum(row["planned_words"] for row in rows),
            )
            self.assertLess(result["planned_total_words"], ceiling)
            self.assertEqual(["One.", "Two.", "Three."], stories[1].facts)

    def test_newspaper_example_uses_a_real_id_and_no_placeholder_urls(self):
        client = Mock(invoke=Mock(return_value=(None, None)))
        engine = EditorialPipeline(load_settings("config.example.toml"), client)
        engine.generate_newspaper([Story.from_dict(extracted_story())], date(2026, 1, 1))
        instruction, payload = client.invoke.call_args.args[:2]
        example = json.loads(instruction.split("Return JSON only:\n", 1)[1])
        self.assertEqual(["product-pilot"], example["articles"][0]["story_ids"])
        self.assertEqual([], example["sources"])
        self.assertEqual(1300, payload["output_contract"]["word_plan"]["complete_edition"])
        client.invoke.assert_called_once()

    def test_runtime_context_is_numeric_and_never_changes_threads_or_speech(self):
        torch = SimpleNamespace(
            get_num_threads=Mock(return_value=2), get_num_interop_threads=Mock(return_value=1)
        )
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        reporter.start()
        try:
            _speech_runtime_context(torch)
            profile = reporter.finish("completed")
        finally:
            reporter.close()
        self.assertEqual(2, profile["counters"]["speech_torch_threads"])
        self.assertEqual(24000, safe_event(profile)["counters"]["speech_sample_rate"])
        self.assertTrue(all(isinstance(x, (int, float)) for x in profile["counters"].values()))
        with patch.object(torch, "get_num_threads", side_effect=RuntimeError("private text")):
            _speech_runtime_context(torch)  # Optional observation outage is harmless.
