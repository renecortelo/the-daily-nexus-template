"""Offline, aggregate-only profiling of allowlisted terminal runner events.

No network, credentials, generation, log persistence or provider quota lookup.
Operation time is nested within stage time and must never be added to it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from statistics import median
from typing import Any

from audiodigest.progress import PREFIX, STAGES
from audiodigest.resource_metrics import safe_sample

MAX_SAMPLES = 200
MAX_LINE_CHARACTERS = 1024 * 1024
LIMITATIONS = (
    "Terminal generation samples only; missing finish events are not reconstructed.",
    "Operations are already included in stages: do not add the two totals.",
    "Timing differences do not establish equivalent content, quality or a speed improvement.",
    "Retry counters do not measure time spent on retries separately.",
    "Job observations are cumulative within a batch, exclude final cleanup and are not summed.",
    "Account-wide billed minutes, remaining allowance, Hosting usage and transfer are unknown.",
)


def terminal_samples(lines: Iterable[str]) -> list[dict]:
    """Accept GitHub-prefixed log lines; retain no raw lines or arbitrary fields."""
    samples: dict[str, dict] = {}
    for line in lines:
        if len(line) > MAX_LINE_CHARACTERS:
            continue
        marker = line.find(PREFIX)
        if marker < 0:
            continue
        try:
            sample = safe_sample(json.loads(line[marker + len(PREFIX):]))
        except (ValueError, TypeError, OverflowError, RecursionError):
            sample = None
        if sample is None:
            continue
        # One finish can appear in both the streaming log and final synchronization.
        samples.pop(sample["at"], None)
        samples[sample["at"]] = sample
        if len(samples) > MAX_SAMPLES:
            del samples[next(iter(samples))]
    return list(samples.values())


def distribution(values: list[float]) -> dict:
    if not values:
        return {"samples": 0}
    ordered = sorted(values)
    return {
        "samples": len(values),
        "total_seconds": round(sum(values), 3),
        "median_seconds": round(median(values), 3),
        "p90_seconds": round(ordered[math.ceil(len(ordered) * 0.9) - 1], 3),
        "min_seconds": ordered[0],
        "max_seconds": ordered[-1],
    }


def _timing_breakdown(samples: list[dict]) -> dict:
    stage_values: dict[str, list] = {}
    operation_values: dict[str, list] = {}
    for sample in samples:
        for key, seconds in sample.get("stage_seconds", {}).items():
            stage_values.setdefault(key, []).append(seconds)
        for key, operation in sample.get("operations", {}).items():
            if "seconds" in operation:
                operation_values.setdefault(key, []).append(operation["seconds"])
    stage_total = sum(sum(values) for values in stage_values.values())
    stages = []
    for key, values in stage_values.items():
        row = {"stage": int(key), "label": STAGES[int(key)], **distribution(values)}
        if stage_total:
            row["share_of_measured_stage_percent"] = round(sum(values) * 100 / stage_total, 1)
        stages.append(row)
    return {
        "generation": distribution([sample["elapsed_seconds"] for sample in samples]),
        "stages": sorted(stages, key=lambda row: (-row["total_seconds"], row["stage"])),
        "operations_nested_in_stages": {
            key: distribution(values) for key, values in sorted(operation_values.items())
        },
    }


def summarize(raw_samples: Iterable[Any]) -> dict:
    """Re-sanitize even direct API inputs; output only fixed keys and aggregates."""
    unique: dict[str, dict] = {}
    for raw in raw_samples:
        try:
            sample = safe_sample(raw)
        except (ValueError, TypeError, OverflowError, RecursionError):
            sample = None
        if sample:
            unique.pop(sample["at"], None)
            unique[sample["at"]] = sample
            if len(unique) > MAX_SAMPLES:
                del unique[next(iter(unique))]
    samples = list(unique.values())
    completed = [sample for sample in samples if sample["status"] == "completed"]
    unsuccessful = [sample for sample in samples if sample["status"] != "completed"]
    measured_targets = [sample for sample in completed
                        if "ready_by_late_seconds" in sample["measurements"]]
    codes = Counter(sample["failure_code"] for sample in unsuccessful if "failure_code" in sample)
    counters: dict[str, float] = {}
    for sample in samples:
        for key, value in sample.get("counters", {}).items():
            counters[key] = counters.get(key, 0) + value
    return {
        "sample_limit": MAX_SAMPLES,
        "sample_count": len(samples),
        "outcomes": dict(sorted(Counter(sample["status"] for sample in samples).items())),
        "all_attempts": _timing_breakdown(samples),
        "completed_only": _timing_breakdown(completed),
        "unsuccessful_generation": distribution(
            [sample["elapsed_seconds"] for sample in unsuccessful]
        ),
        "failure_codes": dict(sorted(codes.items())),
        "counters_observed": dict(sorted(counters.items())),
        "completed_ready_by": {
            "measured_samples": len(measured_targets),
            "late_samples": sum(sample["measurements"]["ready_by_late_seconds"] > 0
                                for sample in measured_targets),
        },
        "limitations": list(LIMITATIONS),
    }


def render_text(report: dict) -> str:
    rows = [f"OFFLINE RUNTIME REVIEW // {report['sample_count']} terminal samples"]
    rows.append("OUTCOMES // " + (" · ".join(
        f"{key}: {value}" for key, value in report["outcomes"].items()
    ) or "not measured"))
    for key, label in (("all_attempts", "ALL ATTEMPTS"), ("completed_only", "COMPLETED ONLY")):
        group = report[key]
        timing = group["generation"]
        if not timing["samples"]:
            rows.append(f"{label} // no measurements")
            continue
        rows.append(
            f"{label} // median {timing['median_seconds'] / 60:.1f} min · "
            f"p90 {timing['p90_seconds'] / 60:.1f} min · "
            f"generation total {timing['total_seconds'] / 60:.1f} min (not billed minutes)"
        )
        for stage in group["stages"]:
            share = stage.get("share_of_measured_stage_percent")
            suffix = f" · {share:.1f}% of measured stages" if share is not None else ""
            rows.append(
                f"  {stage['label']}: {stage['total_seconds'] / 60:.1f} min total"
                f" · {stage['samples']} measured samples{suffix}"
            )
        for name, operation in group["operations_nested_in_stages"].items():
            rows.append(
                f"  Nested {name}: {operation['total_seconds'] / 60:.1f} min total"
                f" · {operation['samples']} measured samples"
            )
    unsuccessful = report["unsuccessful_generation"]
    if unsuccessful["samples"]:
        rows.append(f"UNSUCCESSFUL GENERATION // {unsuccessful['total_seconds'] / 60:.1f} min")
    for code, count in report["failure_codes"].items():
        rows.append(f"TERMINAL CODE // {code}: {count}")
    for key, value in report["counters_observed"].items():
        rows.append(f"COUNTER // {key}: {value:g}")
    rows.extend(f"LIMIT // {item}" for item in report["limitations"])
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, help="Log file; default reads stdin without saving it"
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    try:
        if args.input:
            with args.input.open(encoding="utf-8-sig", errors="replace") as source:
                samples = terminal_samples(source)
        else:
            samples = terminal_samples(sys.stdin)
    except OSError:
        # A path or operating-system error can contain personal information.
        parser.exit(2, "Runtime input could not be read; no input details were retained.\n")
    report = summarize(samples)
    print(json.dumps(report, indent=2) if args.format == "json" else render_text(report))
    if not samples:
        parser.exit(2, "No valid terminal measurements; this does not mean no job ran.\n")


if __name__ == "__main__":
    main()
