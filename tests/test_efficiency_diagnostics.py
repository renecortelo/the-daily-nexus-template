import copy
import io
import json
import tempfile
from datetime import date
from importlib.util import find_spec
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import Mock

from audiodigest.audio import KokoroAudioRenderer
from audiodigest.closing_quotes import ClosingQuote
from audiodigest.config import AudioSettings, HostSettings, load_settings
from audiodigest.draft_repairs import draft_repair_plan
from audiodigest.editorial import EditorialPipeline
from audiodigest.models import DataValidationError, EpisodeScript, Story
from audiodigest.progress import ProgressReporter, add_metric, safe_event
from audiodigest.validation_diagnostics import safe_detail


def candidate(identifier):
    return Story.from_dict({
        "story_id": identifier, "section": "AI", "headline": "Fictional pilot",
        "facts": ["The fictional source describes a limited pilot."],
        "why_it_matters": "The pilot is provisional.", "source_ids": ["fictional-source"],
        "source_urls": [], "confidence": 0.9, "rank_score": 5,
    }, allowed_sections=("AI",))


class EfficiencyDiagnosticsTests(TestCase):
    def request(self, count=2):
        settings = load_settings("config.example.toml")
        settings.hosts.count = count
        settings.hosts.dialogue_style = "conversation"
        client = Mock()
        client.invoke.return_value = (Mock(word_count=100), Mock())
        engine = EditorialPipeline(settings, client)
        engine.generate_script([candidate("first"), candidate("second")], date(2026, 1, 1),
                               ClosingQuote("Learn.", "An Author", "https://example.com/quote"))
        instruction, payload, validate = client.invoke.call_args.args
        return engine, json.loads(instruction.split("Return JSON only:\n", 1)[1]), payload, validate

    def test_multistory_host_failure_has_rule_and_section_without_private_copy(self):
        _engine, draft, _payload, validate = self.request()
        draft["sections"][0]["dialogue"] = [{"host": "Dalia", "text": "Supported pilot."}]
        with self.assertRaises(DataValidationError) as failure:
            validate(draft)
        self.assertEqual("script_hosts", failure.exception.failure_code)
        self.assertEqual({"field": "dialogue", "rule": "host_participation", "section_index": 0,
                          "actual_count": 1, "expected_count": 2}, failure.exception.diagnostic)
        self.assertNotIn("Supported pilot", json.dumps(failure.exception.diagnostic))
        self.assertIn("both speak", draft_repair_plan({}, draft, "script_hosts")["action"])
        plan = draft_repair_plan({}, draft, "script_hosts", diagnostic=failure.exception.diagnostic)
        self.assertIn("indicated conversation section", plan["action"])
        self.assertEqual(0, plan["constraint"]["section_index"])
        intro = draft_repair_plan({}, draft, "script_hosts",
                                 diagnostic={"rule": "host_introduction", "secret": "fictional"})
        self.assertIn("Correct only introduction", intro["action"])
        self.assertNotIn("secret", intro["constraint"])

    def test_bad_turn_and_section_have_nested_locations(self):
        for mutation, field, turn in (({"host": "Dalia", "text": None}, "text", 0),
                                      (None, "dialogue", 0)):
            _engine, draft, _payload, validate = self.request()
            draft["sections"][0]["dialogue"][0] = mutation
            with self.assertRaises(DataValidationError) as failure:
                validate(draft)
            self.assertEqual(0, failure.exception.diagnostic["section_index"])
            self.assertEqual(turn, failure.exception.diagnostic["turn_index"])
            self.assertEqual(field, failure.exception.diagnostic["field"])
        _engine, draft, _payload, validate = self.request()
        draft["sections"][0]["name"] = "Unconfigured fictional desk"
        with self.assertRaises(DataValidationError) as failure:
            validate(draft)
        self.assertEqual(0, failure.exception.diagnostic["section_index"])
        self.assertNotIn("Unconfigured", json.dumps(failure.exception.diagnostic))

    def test_host_count_uniqueness_and_unknown_speaker_remain_rejected(self):
        _engine, draft, _payload, _validate = self.request()
        for hosts, rule in ((["Dalia", "Dalia"], "host_unique"),
                            (["A", "B", "C"], "host_count")):
            with self.assertRaises(DataValidationError) as failure:
                EpisodeScript.from_dict({**draft, "hosts": hosts, "sign_off": "Learn."})
            self.assertEqual("script_hosts", failure.exception.failure_code)
            self.assertEqual(rule, failure.exception.diagnostic["rule"])

    def test_draft_projection_removes_only_scores_and_compact_example_keeps_schema(self):
        engine, draft, payload, _validate = self.request()
        source = candidate("first")
        before = copy.deepcopy(source.to_dict())
        full = engine._story_prompt_dict(source)
        expected = {key: value for key, value in full.items()
                    if key not in {"rank_score", "confidence"}}
        self.assertEqual(expected, payload["stories"][0])
        self.assertEqual(before, source.to_dict())
        compact = json.dumps(draft, ensure_ascii=False, separators=(",", ":"))
        pretty = json.dumps(draft, ensure_ascii=False, indent=2)
        self.assertLess(len(compact.encode()), len(pretty.encode()))
        self.assertEqual(json.loads(compact), json.loads(pretty))
        self.assertEqual(1, engine.antigravity.invoke.call_count)
        self.assertEqual(1, engine.antigravity.invoke.call_args.kwargs["retries"])

    def test_new_measurements_are_allowlisted_bounded_and_cannot_change_finished_profile(self):
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        reporter.start()
        try:
            add_metric("model_request_bytes", 10)
            add_metric("model_request_bytes", 20)
            for value in (True, -1, 1e100, "fictional private copy"):
                add_metric("model_request_bytes", value)
            add_metric("fictional-private-key", 30)
            final = reporter.finish("completed")
            add_metric("model_request_bytes", 50)
            self.assertEqual(final, reporter.snapshot("finish"))
        finally:
            reporter.close()
        self.assertEqual(30, final["counters"]["model_request_bytes"])
        self.assertNotIn("fictional", json.dumps(final))
        self.assertEqual({"field": "text", "section_index": 2, "turn_index": 3, "attempt_ms": 40},
                         safe_detail({"field": "text", "section_index": 2, "turn_index": 3,
                                      "attempt_ms": 40, "raw": "fictional-private-copy",
                                      "rule": "fictional-secret", "expected_count": True}))

    @skipUnless(find_spec("numpy") and find_spec("soundfile"), "optional audio dependencies")
    def test_speech_instrumentation_preserves_exact_pcm_and_pipeline_arguments(self):
        import numpy as np

        renderer = KokoroAudioRenderer(AudioSettings(), HostSettings())
        output_stream = io.StringIO()
        for parts in ([np.array([0.2, -0.3], dtype=np.float32)],
                      [np.array([1.1, -1.2]), np.array([0.5])]):
            pipeline = Mock(side_effect=lambda *_a, parts=parts, **_k: iter(
                [("fictional", "phonemes", part) for part in parts]
            ))
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "synthetic.wav"
                renderer._write_speech_chunk(pipeline, "Fictional text.", output, voice="af_heart")
                expected = output.read_bytes()
                reporter = ProgressReporter(interval=0, stream=output_stream)
                reporter.start()
                try:
                    renderer._write_speech_chunk(
                        pipeline, "Fictional text.", output, voice="af_heart",
                    )
                    profile = reporter.finish("completed")
                finally:
                    reporter.close()
                self.assertEqual(expected, output.read_bytes())
            self.assertEqual(1, profile["operations"]["speech_pipeline"]["count"])
            self.assertEqual(1, profile["operations"]["speech_write"]["count"])
            self.assertEqual(sum(part.size for part in parts),
                             profile["counters"]["speech_samples"])
            self.assertEqual(len("Fictional text."), profile["counters"]["speech_characters"])
            self.assertEqual({"voice": "af_heart", "speed": 1.0, "split_pattern": r"\n+"},
                             pipeline.call_args.kwargs)
            self.assertEqual(profile["operations"], safe_event(profile)["operations"])
        self.assertNotIn("Fictional", output_stream.getvalue())
