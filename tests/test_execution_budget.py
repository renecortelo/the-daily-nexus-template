import os
from unittest import TestCase
from unittest.mock import patch

from audiodigest.execution_budget import (
    RunBudget,
    RunBudgetExceeded,
    operation_timeout,
    reserve_time,
)


class ExecutionBudgetTests(TestCase):
    def test_optional_phase_reserves_completion_time_and_restores_global_deadline(self):
        budget = RunBudget()
        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "TDN_RUNNER_DEADLINE_EPOCH": "140"}),
            patch("audiodigest.execution_budget.time.time", return_value=100),
            patch("audiodigest.execution_budget.time.monotonic", return_value=20),
        ):
            try:
                budget.start_from_environment()
                with reserve_time(15):
                    self.assertEqual(25, operation_timeout(930))
                self.assertEqual(40, operation_timeout(930))
                with self.assertRaises(RunBudgetExceeded):
                    with reserve_time(45):
                        self.fail("Optional work must not consume the completion reserve")
                self.assertEqual(40, operation_timeout(930))
            finally:
                budget.close()

    def test_operations_share_one_deadline_and_cleanup_restores_local_behavior(self):
        budget = RunBudget()
        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "TDN_RUNNER_DEADLINE_EPOCH": "140"}),
            patch("audiodigest.execution_budget.time.time", return_value=100),
            patch("audiodigest.execution_budget.time.monotonic", return_value=20),
        ):
            try:
                budget.start_from_environment()
                self.assertEqual(40, operation_timeout(930))
                self.assertEqual(10, operation_timeout(10))
                with patch("audiodigest.execution_budget.time.monotonic", return_value=61):
                    with self.assertRaises(RunBudgetExceeded):
                        operation_timeout(10)
            finally:
                budget.close()
                budget.close()
        self.assertEqual(930, operation_timeout(930))

    def test_invalid_or_expired_cloud_deadlines_fail_closed(self):
        for value in ("nan", "inf", "bad", "90", "5000"):
            with (
                patch.dict(
                    os.environ, {"GITHUB_ACTIONS": "true", "TDN_RUNNER_DEADLINE_EPOCH": value}
                ),
                patch("audiodigest.execution_budget.time.time", return_value=100),
            ):
                with self.assertRaises(RunBudgetExceeded):
                    RunBudget().start_from_environment()

    def test_local_runs_do_not_inherit_a_cloud_time_limit(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "TDN_RUNNER_DEADLINE_EPOCH": "1"}):
            budget = RunBudget()
            budget.start_from_environment()
            budget.close()
            self.assertEqual(930, operation_timeout(930))
