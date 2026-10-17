import io
import json
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from audiodigest import execution_budget as budget
from audiodigest.config import load_settings
from audiodigest.execution_budget import RunBudgetExceeded, operation_timeout, reserve_time
from audiodigest.paper_budget import completion_reserve, paper_allowance
from audiodigest.paper_diagnostics import paper_phase
from audiodigest.pipeline import Pipeline, VerificationError
from audiodigest.progress import ProgressReporter, record_failure_code, safe_event


class PaperBudgetTests(TestCase):
    def test_final_paper_cause_survives_successful_podcast_without_private_text(self):
        def reject(*_args):
            paper_phase("recheck", 2)
            raise VerificationError("synthetic private reviewer text",
                                    failure_code="paper_verification",
                                    diagnostic={"field": "facts", "password": "synthetic-private"})
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        reporter.start()
        try:
            record_failure_code("json_invalid")  # Earlier rejection is not the final cause.
            with patch("audiodigest.pipeline.remaining_time", return_value=None):
                issue, metadata, _ = Pipeline._prepare_newspaper_artifact(
                    SimpleNamespace(_generate_verified_newspaper=reject), [], date(2026, 1, 1),
                    Path("unused"), Path("unused"))
            profile = reporter.finish("completed")
        finally:
            reporter.close()
        self.assertIsNone(issue)
        self.assertNotIn("failure_code", profile)
        result = profile["paper_result"]
        self.assertEqual(("failed", "recheck", 2, "paper_verification"),
                         tuple(result[k] for k in ("outcome", "phase", "attempt", "code")))
        self.assertEqual(result, metadata[0]["diagnostic"])
        self.assertEqual(result, safe_event(profile)["paper_result"])
        self.assertNotIn("synthetic", json.dumps(profile))

    def test_completion_reserve_grows_without_claiming_guaranteed_synthesis_time(self):
        self.assertEqual(900, completion_reserve(None))
        self.assertEqual(900, completion_reserve(100))
        self.assertGreater(completion_reserve(2600), completion_reserve(2100))
        self.assertEqual(600, paper_allowance(3000, 1000))
        self.assertEqual(0, paper_allowance(800, 900))
        self.assertIsNone(paper_allowance(None, 1000))
        for value in (True, -1, "private-text", float("inf")):
            with self.assertRaises(ValueError):
                completion_reserve(value)

    def test_phase_cap_and_completion_reserve_do_not_extend_outer_deadline(self):
        token = budget._DEADLINE.set(1000)
        try:
            with patch.object(budget.time, "monotonic", return_value=100):
                with reserve_time(200, maximum_seconds=300):
                    self.assertEqual(300, operation_timeout(900))
                self.assertEqual(900, operation_timeout(1000))
                with self.assertRaises(ValueError):
                    with reserve_time(-1):
                        self.fail("Negative reserve would extend the budget")
        finally:
            budget._DEADLINE.reset(token)

    def test_owned_posix_alarm_restores_elapsed_outer_time_after_optional_expiry(self):
        token = budget._DEADLINE.set(1000)
        clock = Mock(return_value=100)
        try:
            with (patch.object(budget.time, "monotonic", clock),
                  patch.object(budget.signal, "SIGALRM", 14, create=True),
                  patch.object(budget.signal, "ITIMER_REAL", 0, create=True),
                  patch.object(budget.signal, "getsignal", return_value=budget.RunBudget._expired),
                  patch.object(budget.signal, "getitimer", return_value=(900, 0), create=True),
                  patch.object(budget.signal, "setitimer", return_value=(900, 0),
                               create=True) as alarm):
                with self.assertRaises(RunBudgetExceeded):
                    with reserve_time(200, maximum_seconds=300):
                        clock.return_value = 400
                self.assertEqual(300, alarm.call_args_list[0].args[1])
                self.assertEqual(600, alarm.call_args_list[-1].args[1])
                self.assertEqual(600, operation_timeout(900))
        finally:
            budget._DEADLINE.reset(token)

    def test_insufficient_window_starts_no_model_or_renderer_and_logs_only_metrics(self):
        pipeline = SimpleNamespace(_generate_verified_newspaper=Mock())
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        reporter.start()
        try:
            with (patch("audiodigest.pipeline.remaining_time", return_value=950),
                  patch("audiodigest.pipeline.NewspaperRenderer") as renderer):
                issue, metadata, previews = Pipeline._prepare_newspaper_artifact(
                    pipeline, [], date(2026, 1, 1), Path("unused"), Path("unused"),
                    script_words=100,
                )
            profile = reporter.finish("completed")
        finally:
            reporter.close()
        self.assertIsNone(issue)
        self.assertEqual([], previews)
        self.assertEqual("budget_insufficient", metadata[0]["reason"])
        self.assertEqual(1, profile["counters"]["paper_budget_skipped"])
        pipeline._generate_verified_newspaper.assert_not_called()
        renderer.assert_not_called()
        self.assertNotIn("unused", json.dumps(profile))

    def test_rendering_has_same_protected_deadline_and_failed_artifact_is_not_promoted(self):
        token = budget._DEADLINE.set(2000)
        pipeline = SimpleNamespace(settings=load_settings("config.example.toml"),
                                   _generate_verified_newspaper=Mock(return_value=(
                                       Mock(to_dict=lambda: {}), [],
                                   )))
        clock = Mock(return_value=100)
        def render(*_args, **_kwargs):
            self.assertEqual(600, operation_timeout(900))
            clock.return_value = 701
            return Mock(preview_paths=[])
        try:
            with (tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder,
                  patch.object(budget.time, "monotonic", clock),
                  patch("audiodigest.pipeline.NewspaperRenderer") as renderer,
                  patch("audiodigest.pipeline._promote_episode_files") as promote):
                renderer.return_value.render.side_effect = render
                issue, metadata, _ = Pipeline._prepare_newspaper_artifact(
                    pipeline, [], date(2026, 1, 1), Path(folder), Path(folder), script_words=100,
                )
                self.assertIsNone(issue)
                self.assertEqual("budget_exhausted", metadata[0]["reason"])
                promote.assert_not_called()
                self.assertEqual(1299, operation_timeout(2000))
        finally:
            budget._DEADLINE.reset(token)

    def test_global_deadline_exhaustion_still_aborts_not_paper_only_failure(self):
        token = budget._DEADLINE.set(100)
        try:
            with patch.object(budget.time, "monotonic", return_value=100):
                with self.assertRaises(RunBudgetExceeded):
                    Pipeline._prepare_newspaper_artifact(
                        SimpleNamespace(_generate_verified_newspaper=Mock()), [], date(2026, 1, 1),
                        Path("unused"), Path("unused"),
                    )
        finally:
            budget._DEADLINE.reset(token)

    def test_short_model_timeout_is_not_mislabeled_as_paper_budget_exhaustion(self):
        token = budget._DEADLINE.set(2000)
        failure = subprocess.TimeoutExpired("synthetic-command", 30)
        failure.diagnostic = None  # Missing optional details cannot break podcast fallback.
        pipeline = SimpleNamespace(_generate_verified_newspaper=Mock(
            side_effect=failure,
        ))
        try:
            with patch.object(budget.time, "monotonic", return_value=100):
                issue, metadata, previews = Pipeline._prepare_newspaper_artifact(
                    pipeline, [], date(2026, 1, 1), Path("unused"), Path("unused"),
                    script_words=100,
                )
            self.assertIsNone(issue)
            self.assertEqual([], previews)
            self.assertEqual("newspaper-failed", metadata[0]["stage"])
            self.assertEqual("TimeoutExpired", metadata[0]["error_type"])
            self.assertEqual("paper_timeout", metadata[0]["diagnostic"]["code"])
        finally:
            budget._DEADLINE.reset(token)
