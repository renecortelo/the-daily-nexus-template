import json
from unittest import TestCase
from unittest.mock import patch

from audiodigest.actions_usage import budget_plan, collect_jobs, job_usage


class ActionsUsageTests(TestCase):
    def test_rounding_is_per_job_and_missing_jobs_are_not_zero_usage(self):
        job = {
            "started_at": "2026-01-01T01:00:00Z",
            "completed_at": "2026-01-01T01:00:01Z",
            "labels": ["ubuntu-24.04"],
        }
        result = job_usage([job, job, {"status": "queued"}])
        self.assertEqual(2, result["linux_rounded_minutes"])
        self.assertEqual(1, result["unmeasured_jobs"])
        self.assertNotIn("2026", json.dumps(result))
        self.assertEqual(
            0, job_usage([{**job, "labels": ["unrecognized-private-label"]}])["measured_jobs"]
        )

    def test_balance_is_dashboard_input_not_job_time_or_an_implicit_zero(self):
        self.assertIsNone(budget_plan(included=2000)["dashboard_remaining_minutes"])
        plan = budget_plan(included=2000, used=1615, reserve=50, days=16, daily_runs=2)
        self.assertEqual(385, plan["dashboard_remaining_minutes"])
        self.assertEqual(10.47, plan["target_average_run_minutes"])
        self.assertEqual(0, budget_plan(included=100, used=200)["planned_available_minutes"])
        for value in (-1, True, float("inf")):
            with self.assertRaises(ValueError):
                budget_plan(included=value)

    def test_account_collection_includes_failure_setup_cleanup_and_uses_all_attempts(self):
        payloads = [
            {"login": "synthetic-owner"},
            [
                {
                    "private": True,
                    "owner": {"login": "synthetic-owner"},
                    "full_name": "synthetic-owner/repo",
                }
            ],
            {"workflow_runs": [{"id": 1, "conclusion": "failure"}]},
            {
                "jobs": [
                    {
                        "started_at": "2026-01-01T00:00:00Z",
                        "completed_at": "2026-01-01T00:02:01Z",
                        "labels": ["ubuntu-24.04"],
                    }
                ]
            },
        ]
        from types import SimpleNamespace

        with patch("audiodigest.actions_usage.shutil.which", return_value="/tools/gh"), patch(
            "audiodigest.actions_usage.subprocess.run",
            side_effect=[
                SimpleNamespace(returncode=0, stdout=json.dumps(data)) for data in payloads
            ],
        ) as gh:
            result = collect_jobs("2026-01")
        self.assertEqual(3, result["linux_rounded_minutes"])
        self.assertIn("filter=all", gh.call_args.args[0][-1])
        self.assertNotIn("synthetic", json.dumps(result))
