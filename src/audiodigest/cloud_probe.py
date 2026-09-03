from __future__ import annotations

import argparse
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from audiodigest.config import load_settings
from audiodigest.jobs import JobValidationError, ScheduledJob
from audiodigest.web_runner import FirebaseWebRunnerClient, WebRunnerError


def _record_probe_status(
    client: FirebaseWebRunnerClient,
    *,
    state: str,
    detail: str,
) -> None:
    """Refresh the owner monitor for every lightweight cloud poll.

    The probe uses only the queue credential, so this creates no model work and
    no access to Gmail or Antigravity.  It prevents a successful idle poll from
    looking like a stale runner in the web console.
    """

    client.set_private_document(
        "runner",
        "status",
        {
            "state": state,
            "activeTask": "",
            "detail": detail,
            "checkedAt": datetime.now(UTC),
            "schemaVersion": 1,
        },
    )


def _request_submitted_at(item: dict[str, object]) -> datetime | None:
    value = item.get("updatedAt") or item.get("requestedAt")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _targeted_schedule_episode_date(
    schedule: ScheduledJob,
    *,
    current: datetime,
    schedule_date: date,
) -> date | None:
    """Validate the exact local schedule occurrence requested by the clock."""
    if not schedule.enabled or schedule_date.weekday() not in schedule.weekdays:
        return None
    local_start = datetime.combine(
        schedule_date,
        schedule.start_time,
        schedule.local_now(current).tzinfo,
    )
    if current < local_start:
        return None
    if schedule.parameters.date_mode == "previous_day":
        return schedule_date - timedelta(days=1)
    return schedule_date


def cloud_work_is_due(
    config_path: Path,
    *,
    now: datetime | None = None,
    schedule_id: str | None = None,
    schedule_date: date | None = None,
) -> bool:
    """Return whether the protected runner should start.

    A supplied schedule id originates from the private Cloudflare clock.  That
    path is intentionally narrow: it may start exactly one due schedule, but
    must never wake a manual request or another schedule by accident.
    """
    requested_schedule_id = (schedule_id or "").strip()
    if schedule_date is not None and not requested_schedule_id:
        raise WebRunnerError("schedule date requires an exact schedule id")
    settings = load_settings(config_path)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise WebRunnerError("cloud probe time must include a timezone")
    client = FirebaseWebRunnerClient(settings)
    client.authenticate()
    invalid_schedule_count = 0
    for item in client.list_private_collection("schedules"):
        document_id = str(item.pop("document_id", ""))
        if requested_schedule_id and document_id != requested_schedule_id:
            continue
        try:
            schedule = ScheduledJob.from_dict(item, schedule_id=document_id)
        except JobValidationError:
            # A legacy or manually edited schedule must not prevent unrelated
            # valid queue work from being discovered by this lightweight probe.
            invalid_schedule_count += 1
            continue
        target_episode_date = (
            _targeted_schedule_episode_date(
                schedule,
                current=current,
                schedule_date=schedule_date,
            )
            if schedule_date is not None
            else (schedule.episode_date(current) if schedule.is_due(current) else None)
        )
        if target_episode_date is None:
            if requested_schedule_id:
                _record_probe_status(
                    client,
                    state="idle",
                    detail="The requested scheduled task is not due or was already claimed.",
                )
                return False
            continue
        episode_date = target_episode_date.isoformat()
        execution_status = client.private_execution_status(
            schedule.schedule_id,
            episode_date,
        )
        # A failed occurrence is terminal.  Retrying it on every Cloud Clock
        # tick was the source of an expensive Actions loop.  The next dated
        # occurrence is independent; an owner can also submit a fresh manual
        # request when an immediate retry is appropriate.
        if not execution_status:
            _record_probe_status(
                client,
                state="queued",
                detail="A scheduled task is ready for the private cloud runner.",
            )
            return True
        if requested_schedule_id:
            _record_probe_status(
                client,
                state="idle",
                detail="The requested scheduled task is not due or was already claimed.",
            )
            return False
    if requested_schedule_id:
        detail = "The requested scheduled task is no longer available."
        if invalid_schedule_count:
            detail = "The requested scheduled task needs correction."
        _record_probe_status(client, state="idle", detail=detail)
        return False
    cutoff = current - timedelta(days=2)
    for item in client.list_private_collection("runRequests"):
        if item.get("status") != "queued":
            continue
        request_id = str(item.get("document_id", ""))
        try:
            requested_date = date.fromisoformat(str(item.get("requestedDate", "")))
        except ValueError:
            client.patch_private_document(
                "runRequests",
                request_id,
                {
                    "status": "failed",
                    "updatedAt": datetime.now(UTC),
                    "finishedAt": datetime.now(UTC),
                    "detail": "Request has an invalid episode date.",
                },
            )
            continue
        submitted_at = _request_submitted_at(item)
        if submitted_at is None or submitted_at < cutoff:
            client.patch_private_document(
                "runRequests",
                request_id,
                {
                    "status": "expired",
                    "updatedAt": datetime.now(UTC),
                    "finishedAt": datetime.now(UTC),
                    "detail": "Request expired before the private runner could start it.",
                },
            )
            continue
        execution_id = f"request-{request_id}"[:120]
        status = client.private_execution_status(execution_id, requested_date.isoformat())
        if status == "failed":
            client.patch_private_document(
                "runRequests",
                request_id,
                {
                    "status": "failed",
                    "updatedAt": datetime.now(UTC),
                    "finishedAt": datetime.now(UTC),
                    "detail": (
                        "An earlier private runner attempt failed. "
                        "Requeue to create a fresh retry."
                    ),
                },
            )
            continue
        # Only an execution that has never been claimed is eligible for an
        # automatic wake-up.  Failed requests stay visible for the owner to
        # requeue explicitly as a fresh request.
        if not status:
            _record_probe_status(
                client,
                state="queued",
                detail="A manual generation request is ready for the private cloud runner.",
            )
            return True
    detail = "No task is due."
    if invalid_schedule_count:
        detail = "No task is due; one or more saved schedules need correction."
    _record_probe_status(client, state="idle", detail=detail)
    return False


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="audiodigest-cloud-probe")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--schedule-id",
        help="Run only this due saved schedule; do not consume the manual queue.",
    )
    parser.add_argument(
        "--schedule-date",
        type=date.fromisoformat,
        help="Local YYYY-MM-DD occurrence for an exact saved schedule.",
    )
    args = parser.parse_args(argv)
    due = cloud_work_is_due(
        args.config.resolve(),
        schedule_id=args.schedule_id,
        schedule_date=args.schedule_date,
    )
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise WebRunnerError("cloud probe requires the protected GitHub output channel")
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"run={'true' if due else 'false'}\n")
    print("Private cloud queue contains work." if due else "No private cloud task is due.")


if __name__ == "__main__":
    main()
