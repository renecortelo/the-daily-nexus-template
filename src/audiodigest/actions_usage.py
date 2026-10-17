"""Read-only account job accounting; observed runtime is not a billing balance."""

import argparse
import calendar
import json
import math
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path


def job_usage(jobs):
    if not isinstance(jobs, list):
        raise ValueError("invalid_jobs")
    result = {
        "linux_rounded_minutes": 0,
        "other_os_seconds": 0,
        "unmeasured_jobs": 0,
        "measured_jobs": 0,
    }
    for job in jobs:
        try:
            start = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00"))
            seconds = (end - start).total_seconds()
            labels = job["labels"]
            if (
                not start.tzinfo
                or not end.tzinfo
                or not 0 <= seconds <= 7 * 86400
                or not isinstance(labels, list)
                or any(not isinstance(x, str) for x in labels)
            ):
                raise ValueError("unmeasured")
        except (KeyError, TypeError, ValueError, AttributeError):
            result["unmeasured_jobs"] += 1
            continue
        if "self-hosted" in labels:
            result["unmeasured_jobs"] += 1
            continue
        if any(label in {"ubuntu-latest", "ubuntu-22.04", "ubuntu-24.04"} for label in labels):
            result["linux_rounded_minutes"] += math.ceil(seconds / 60)
        elif any(label.startswith(("windows-", "macos-")) for label in labels):
            result["other_os_seconds"] += seconds
        else:
            result["unmeasured_jobs"] += 1
            continue
        result["measured_jobs"] += 1
    return result


def budget_plan(*, included, used=None, reserve=200, other=0, days=31, daily_runs=2):
    values = (included, reserve, other, days, daily_runs)
    if any(type(x) is not int or not 0 <= x <= 10**9 for x in values) or not days or not daily_runs:
        raise ValueError("invalid_budget")
    if used is not None and (type(used) is not int or used < 0):
        raise ValueError("invalid_usage")
    available = max(0, included - (used or 0) - reserve - other)
    result = {
        "included_minutes": included,
        "reserve_minutes": reserve,
        "other_work_minutes": other,
        "planning_days": days,
        "runs_per_day": daily_runs,
        "planned_available_minutes": available,
        "target_daily_minutes": round(available / days, 2),
        "target_average_run_minutes": round(available / (days * daily_runs), 2),
    }
    if used is not None:
        result.update(
            dashboard_used_minutes=used, dashboard_remaining_minutes=max(0, included - used)
        )
    else:
        result["dashboard_remaining_minutes"] = None
    return result


def collect_jobs(month):
    """Bounded gh reads using existing permissions; no secrets/IDs in the result."""
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("invalid_month")
    year, number = map(int, month.split("-"))
    last = calendar.monthrange(year, number)[1]
    deadline = time.monotonic() + 180
    executable = shutil.which("gh")
    if not executable:
        raise RuntimeError("github_cli_unavailable")

    def api(path):
        if time.monotonic() >= deadline:
            raise RuntimeError("review_time_limit")
        response = subprocess.run(
            [executable, "api", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=min(30, max(1, deadline - time.monotonic())),
        )
        if response.returncode:
            raise RuntimeError("github_read_unavailable")
        return json.loads(response.stdout)

    def pages(path, key=None):
        rows = []
        for page in range(1, 11):
            payload = api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
            batch = payload[key] if key else payload
            if not isinstance(batch, list):
                raise RuntimeError("github_shape_unavailable")
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise RuntimeError("review_page_limit")  # Never present truncated usage as complete.

    owner = api("user")["login"]
    repos = [
        repo
        for repo in pages("user/repos?affiliation=owner&visibility=private")
        if repo.get("private") is True and repo.get("owner", {}).get("login") == owner
    ]
    runs = []
    for repo in repos:
        name = repo["full_name"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
            raise RuntimeError("github_shape_unavailable")
        runs.extend(
            (name, run)
            for run in pages(
                f"repos/{name}/actions/runs?created={month}-01..{month}-{last:02d}", "workflow_runs"
            )
        )

    def read_jobs(item):
        name, run = item
        identity = run["id"]
        if type(identity) is not int or identity <= 0:
            raise RuntimeError("github_shape_unavailable")
        # filter=all includes old job attempts rather than only the latest retry.
        return pages(f"repos/{name}/actions/runs/{identity}/jobs?filter=all", "jobs")

    jobs = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for batch in pool.map(read_jobs, runs):
            jobs.extend(batch)
    return {
        "private_repositories_checked": len(repos),
        "runs_checked": len(runs),
        **job_usage(jobs),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--github-month", help="YYYY-MM; read all accessible owned private repositories"
    )
    parser.add_argument("--input", type=Path, help="Existing JSON array of job records; no network")
    parser.add_argument("--included-minutes", type=int, required=True)
    parser.add_argument(
        "--used-minutes", type=int, help="Current account meter, entered from GitHub Billing"
    )
    parser.add_argument("--reserve-minutes", type=int, default=200)
    parser.add_argument("--other-work-minutes", type=int, default=0)
    parser.add_argument("--days", type=int, default=31)
    parser.add_argument("--runs-per-day", type=int, default=2)
    args = parser.parse_args()
    try:
        if args.github_month and args.input:
            raise ValueError("choose_one_input")
        usage = (
            collect_jobs(args.github_month)
            if args.github_month
            else (
                job_usage(json.loads(args.input.read_text(encoding="utf-8")))
                if args.input
                else None
            )
        )
        report = {
            "planning": budget_plan(
                included=args.included_minutes,
                used=args.used_minutes,
                reserve=args.reserve_minutes,
                other=args.other_work_minutes,
                days=args.days,
                daily_runs=args.runs_per_day,
            ),
            "observed_usage": usage,
            "limitations": [
                "Job runtime includes setup, failures and cleanup, rounded per job.",
                "Observed private Linux job minutes are not the account billed balance.",
                "Waived jobs, other OS pricing and reporting delay can differ from the meter.",
                "Unknown jobs are counted as unmeasured, never as zero usage.",
                "Planning targets are not generation timeouts or a delivery guarantee.",
            ],
        }
        print(json.dumps(report))
        return 0
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, subprocess.TimeoutExpired):
        print(json.dumps({"review": "incomplete", "reason": "inputs_or_provider_read_unavailable"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
