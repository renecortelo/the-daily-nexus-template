from __future__ import annotations

import hashlib
import json
import re
import signal
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from audiodigest.closing_quotes import load_closing_quotes, quote_ids_from_episodes
from audiodigest.config import Settings
from audiodigest.database import StateDatabase
from audiodigest.execution_budget import RunBudget
from audiodigest.jobs import (
    GenerationParameters,
    JobValidationError,
    ScheduledJob,
    apply_generation_parameters,
)
from audiodigest.pipeline import Pipeline
from audiodigest.progress import ProgressReporter, increment
from audiodigest.publisher import load_remote_publication
from audiodigest.web_runner import FirebaseWebRunnerClient, WebRunnerError

MANUAL_REQUEST_EXPIRY_DAYS = 2


class TerminalTaskFailure(RuntimeError):
    """A claimed cloud task failed after its terminal status was recorded.

    The dedicated exception lets the GitHub wrapper distinguish a normal,
    non-retryable generation failure from an infrastructure failure that
    happened before a task could be claimed safely.
    """

    exit_code = 20

    def __init__(self, *, execution_id: str, episode_date: date, error_name: str):
        super().__init__(f"{error_name}: claimed generation task failed")
        self.result = {
            "status": "failed",
            "execution_id": execution_id,
            "episode_date": episode_date.isoformat(),
            "error_type": error_name,
        }


class GenerationInterrupted(RuntimeError):
    """A protected runner received a cancellation signal before completion."""


def _raise_generation_interrupted(signum: int, _frame: object) -> None:
    raise GenerationInterrupted(f"generation interrupted by signal {signum}")


def _install_generation_interrupt_handlers() -> dict[int, object]:
    """Convert CI cancellation into a terminal Firestore task status."""

    previous: dict[int, object] = {}
    for candidate in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[candidate] = signal.signal(
                candidate,
                _raise_generation_interrupted,
            )
        except (AttributeError, ValueError):
            continue
    return previous


def _restore_generation_interrupt_handlers(previous: dict[int, object]) -> None:
    for candidate, handler in previous.items():
        signal.signal(candidate, handler)


def _targeted_schedule_episode_date(
    schedule: ScheduledJob,
    *,
    current: datetime,
    schedule_date: date,
) -> date | None:
    """Validate one clock occurrence and return its intended episode date.

    Cloud alarms carry the schedule's *local* date.  Keeping that date avoids
    a late alarm crossing midnight silently turning into tomorrow's run.  A
    delayed alarm remains valid after its start time, but it cannot create a
    run before the saved clock time or on a weekday the schedule does not use.
    """
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


def _request_date(item: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(item.get("requestedDate", "")))
    except ValueError:
        return None


def _request_submitted_at(item: dict[str, Any]) -> datetime | None:
    value = item.get("updatedAt") or item.get("requestedAt")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _active_manual_requests(
    client: FirebaseWebRunnerClient,
    items: list[dict[str, Any]],
    *,
    current: datetime,
) -> list[dict[str, Any]]:
    """Return runnable web requests and retire obsolete historical requests.

    Browser-created requests are intentionally durable, but a forgotten test from
    a prior day must never be picked up by a later cloud schedule.
    """
    cutoff = current - timedelta(days=MANUAL_REQUEST_EXPIRY_DAYS)
    active: list[dict[str, Any]] = []
    for item in items:
        if item.get("status") != "queued":
            continue
        request_id = str(item.get("document_id", ""))
        requested_date = _request_date(item)
        if requested_date is None:
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
        status = client.private_execution_status(
            execution_id,
            requested_date.isoformat(),
        )
        if status == "completed":
            client.patch_private_document(
                "runRequests",
                request_id,
                {
                    "status": "published",
                    "updatedAt": datetime.now(UTC),
                    "finishedAt": datetime.now(UTC),
                    "detail": "Generation completed and the private feed was published.",
                },
            )
            continue
        if status == "failed":
            client.patch_private_document(
                "runRequests",
                request_id,
                {
                    "status": "failed",
                    "updatedAt": datetime.now(UTC),
                    "finishedAt": datetime.now(UTC),
                    "detail": (
                        "An earlier private runner attempt failed. Requeue to create a fresh retry."
                    ),
                },
            )
            continue
        if status == "running":
            continue
        active.append(item)
    return active


def _runner_status(
    client: FirebaseWebRunnerClient,
    *,
    state: str,
    active_task: str = "",
    detail: str = "",
) -> None:
    data = {
        "state": state,
        "activeTask": active_task[:120],
        "detail": detail[:500],
        "checkedAt": datetime.now(UTC),
        "schemaVersion": 1,
    }
    if state == "running":
        data["startedAt"] = datetime.now(UTC)
    client.set_private_document(
        "runner",
        "status",
        data,
    )


def _save_timing_profile(client: FirebaseWebRunnerClient, profile: dict) -> None:
    try:
        client.set_private_document("runner", "lastProfile", profile)
    except Exception:
        # A status outage must not change a completed/failed execution claim.
        increment("progress_updates_failed")


def _published_metadata(
    settings: Settings,
    database: StateDatabase,
    *,
    episode_date: date,
    execution_id: str,
    publication_label: str,
    publication_sequence: int,
) -> tuple[str, dict[str, Any]]:
    episode = database.episode_for_date(episode_date)
    if episode is None:
        raise WebRunnerError("completed runner task did not create an episode record")
    status = str(episode.get("status", "staged"))
    audio_url = ""
    newspaper_url = ""
    references = [str(note) for note in episode.get("show_notes", []) if isinstance(note, str)]
    transcript: list[dict[str, Any]] = []
    source_mix: dict[str, Any] = {}
    newspaper_pages = 0
    newspaper_status = "unknown"
    closing_quote_id = ""
    manifest_path = Path(str(episode.get("manifest_path", "")))
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("newspaper_status") in {"ready", "failed"}:
                newspaper_status = manifest["newspaper_status"]
            previews = manifest.get("newspaper_preview_paths", [])
            if isinstance(previews, list) and 1 <= len(previews) <= 3:
                newspaper_pages = len(previews)
            candidate_quote_id = manifest.get("closing_quote_id", "")
            if (
                isinstance(candidate_quote_id, str)
                and len(candidate_quote_id) == 24
                and all(character in "0123456789abcdef" for character in candidate_quote_id)
            ):
                closing_quote_id = candidate_quote_id
            raw_source_mix = manifest.get("source_mix", {})
            if isinstance(raw_source_mix, dict):
                source_mix = {
                    key: value
                    for key, value in raw_source_mix.items()
                    if key
                    in {
                        "mode",
                        "newsletter_messages",
                        "newsletter_backed_stories",
                        "safe_article_links",
                        "safe_articles_retrieved",
                        "research_sources",
                    }
                    and isinstance(value, (str, int))
                    and not isinstance(value, bool)
                }
            transcript_path = Path(str(manifest.get("transcript_path", "")))
            raw_segments = json.loads(transcript_path.read_text(encoding="utf-8")).get(
                "segments", []
            )
            if isinstance(raw_segments, list):
                for segment in raw_segments[:500]:
                    if not isinstance(segment, dict):
                        continue
                    text = str(segment.get("text", "")).strip()
                    if not text:
                        continue
                    transcript.append(
                        {
                            "host": str(segment.get("host", "Host"))[:40],
                            "text": text[:2_000],
                            "startMs": int(segment.get("start_ms", 0)),
                        }
                    )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # The episode itself remains playable if optional owner-only details are absent.
            transcript = []
    if status == "published":
        root = f"{settings.firebase.base_url}/p/{settings.firebase.secret_path}"
        filename = f"{episode_date.isoformat()}-{episode['guid']}"
        audio_url = f"{root}/audio/{filename}.mp3"
        newspaper_path = episode.get("newspaper_path")
        if newspaper_path and Path(str(newspaper_path)).is_file():
            newspaper_url = f"{root}/read/{filename}.pdf"
    metadata = {
        "episodeDate": episode_date.isoformat(),
        "title": str(episode["title"]),
        "durationMinutes": round(float(episode["duration_seconds"]) / 60, 1),
        "status": status,
        "audioUrl": audio_url,
        "newspaperUrl": newspaper_url,
        "newspaperPages": newspaper_pages,
        "newspaperStatus": "ready" if newspaper_url else newspaper_status,
        "references": references[:100],
        "sourceMix": source_mix,
        "transcript": transcript,
        "executionId": execution_id,
        "publicationLabel": publication_label,
        "publicationSequence": publication_sequence,
        "publicationSequenceVersion": 2,
        "updatedAt": datetime.now(UTC),
        "schemaVersion": 1,
        "closingQuoteId": closing_quote_id,
    }
    return f"{episode_date.isoformat()}-{execution_id}"[:160], metadata


def _publication_sequence_key(label: str) -> str:
    normalized = " ".join(label.split()).casefold()
    return "publication-sequence-v2-" + hashlib.sha256(normalized.encode()).hexdigest()[:24]


def _next_publication_sequence(
    settings: Settings,
    client: FirebaseWebRunnerClient,
    *,
    episode_date: date,
    label: str,
) -> int:
    """Return the next all-time sequence for a series, independent of date.

    The immutable execution ID remains the document and media identity.  The
    compact numeric suffix is purely reader-facing and lets manually repeated
    runs sort predictably without exposing opaque identifiers. Only published
    editions count; old per-day suffixes are not interpreted as global numbers.
    The existing cloud concurrency group serializes publication. Owner-only
    high-water state survives archive/media retention, while the complete
    archive recovers a number if its checkpoint write was interrupted.
    """

    normalized_label = " ".join(label.split()).casefold()
    title_pattern = re.compile(
        r"^\d{2}/\d{2}/\d{4}\s+"
        + re.escape(settings.podcast.title)
        + r"\s+-\s+(?P<label>.+)\s+-\s+(?P<number>\d+)$",
        re.IGNORECASE,
    )
    checkpoint = client.get_private_runner_document(_publication_sequence_key(label))
    highest = 0
    if checkpoint is not None:
        value = checkpoint.get("lastSequence")
        if (
            checkpoint.get("schemaVersion") != 2
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise WebRunnerError("publication sequence checkpoint is invalid")
        highest = value
    published_identities: set[str] = set()
    for item in client.list_private_collection(
        "episodes",
        field_mask=[
            "episodeDate",
            "publicationLabel",
            "title",
            "publicationSequence",
            "publicationSequenceVersion",
            "status",
            "audioUrl",
            "executionId",
        ],
        all_pages=True,
    ):
        if item.get("status") != "published":
            continue
        stored_label = " ".join(str(item.get("publicationLabel", "")).split()).casefold()
        title = str(item.get("title", "")).strip()
        if not stored_label:
            match = title_pattern.fullmatch(title)
            stored_label = " ".join(match["label"].split()).casefold() if match else ""
        if stored_label != normalized_label:
            continue
        identity = item.get("audioUrl") or item.get("executionId") or item.get("document_id")
        if not identity:
            raise WebRunnerError("published edition lacks a stable numbering identity")
        published_identities.add(str(identity))
        value = item.get("publicationSequence")
        if (
            item.get("publicationSequenceVersion") == 2
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
        ):
            highest = max(highest, value)
    # Hosting activation can succeed even if the subsequent Firestore write
    # fails. Never reuse a new-format number already delivered to subscribers.
    for remote in load_remote_publication(
        settings,
        maximum_episodes=settings.app.retention_days + 1,
    ):
        match = title_pattern.fullmatch(remote.title.strip())
        if (
            match
            and len(match["number"]) >= 4
            and " ".join(match["label"].split()).casefold() == normalized_label
        ):
            highest = max(highest, int(match["number"]))
    return max(highest, len(published_identities)) + 1


def _execution_database_path(settings: Settings, execution_id: str) -> Path:
    """Return an isolated local cache for one immutable cloud execution."""
    digest = hashlib.sha256(execution_id.strip().encode("utf-8")).hexdigest()
    return settings.app.runtime_dir / "web-executions" / f"{digest[:24]}.sqlite3"


def _execute_generation(
    settings: Settings,
    client: FirebaseWebRunnerClient,
    *,
    execution_id: str,
    display_name: str,
    parameters: GenerationParameters,
    episode_date: date,
    pipeline_factory: Callable[[Settings], Pipeline],
    request_id: str = "",
) -> dict[str, Any]:
    configured = apply_generation_parameters(settings, parameters)
    # The hosted console exists to create a finished private episode.  Keeping
    # an old browser-saved `publish: false` flag would silently leave a cloud
    # run local to an ephemeral GitHub worker, where it is unusable afterwards.
    # Desktop/local runs retain their optional publishing behaviour.
    configured.firebase.publish_enabled = True
    configured.firebase.publish_mode = "automatic"
    configured.database_override_path = _execution_database_path(
        configured,
        execution_id,
    )
    database = StateDatabase(configured.database_path)
    publication_label = parameters.run_name if request_id else display_name
    publication_label = " ".join(publication_label.split()) or "Run"
    publication_sequence = _next_publication_sequence(
        configured,
        client,
        episode_date=episode_date,
        label=publication_label,
    )
    if not client.claim_private_execution(
        execution_id,
        episode_date.isoformat(),
    ):
        return {
            "status": "already-claimed",
            "execution_id": execution_id,
            "episode_date": episode_date.isoformat(),
        }
    database.claim_scheduled_execution(execution_id, episode_date)
    _runner_status(
        client,
        state="running",
        active_task=display_name,
        detail=f"Preparing {episode_date.isoformat()}.",
    )
    if request_id:
        client.patch_private_document(
            "runRequests",
            request_id,
            {
                "status": "running",
                "startedAt": datetime.now(UTC),
                "updatedAt": datetime.now(UTC),
            },
        )
    previous_signal_handlers = _install_generation_interrupt_handlers()
    reporter = ProgressReporter(
        callback=lambda profile: client.patch_private_document(
            "runner",
            "status",
            {"progress": profile, "checkedAt": datetime.now(UTC)},
        )
    )
    reporter.start()
    budget = RunBudget()
    publication_confirmed = False
    try:
        budget.start_from_environment()
        # One small owner-only query spans all schedules. Do not use the first
        # page of the archive (which may contain only the oldest episodes).
        quote_episodes = client.list_private_collection(
            "episodes",
            limit=40,
            order_by="updatedAt desc",
            field_mask=["closingQuoteId", "references", "status", "updatedAt"],
        )
        recent_quote_ids = quote_ids_from_episodes(
            load_closing_quotes(configured.podcast.closing_quotes_path),
            [item for item in quote_episodes if item.get("status") == "published"],
        )
        result = pipeline_factory(configured).run(
            requested_date=episode_date,
            execution_id=execution_id,
            run_name=publication_label,
            run_sequence=publication_sequence,
            recent_quote_ids=recent_quote_ids,
        )
        publication_confirmed = result.get("status") == "published"
        episode_id, metadata = _published_metadata(
            configured,
            database,
            episode_date=episode_date,
            execution_id=execution_id,
            publication_label=publication_label,
            publication_sequence=publication_sequence,
        )
        client.set_private_document("episodes", episode_id, metadata)
        if metadata.get("status") == "published":
            try:
                client.set_private_document(
                    "runner",
                    _publication_sequence_key(publication_label),
                    {
                        "lastSequence": publication_sequence,
                        "seriesLabel": publication_label,
                        "schemaVersion": 2,
                        "updatedAt": datetime.now(UTC),
                    },
                )
            except WebRunnerError:
                # The completed archive record remains the recovery source.
                # Never turn an already published episode into an automatic retry.
                print("Numbering checkpoint unavailable; published archive preserves the number.")
        database.finish_scheduled_execution(
            execution_id,
            episode_date,
            "completed",
        )
        client.finish_private_execution(
            execution_id,
            episode_date.isoformat(),
            status="completed",
        )
        if request_id:
            request_status = "published" if metadata.get("status") == "published" else "completed"
            request_detail = (
                "Generation completed and the private feed was published."
                if request_status == "published"
                else "Generation and verification completed."
            )
            if metadata.get("newspaperStatus") == "failed":
                request_detail += " Podcast is ready; newspaper generation failed."
            client.patch_private_document(
                "runRequests",
                request_id,
                {
                    "status": request_status,
                    "finishedAt": datetime.now(UTC),
                    "updatedAt": datetime.now(UTC),
                    "detail": request_detail,
                },
            )
        profile = reporter.finish("completed")
        _save_timing_profile(client, profile)
        _runner_status(
            client,
            state="idle",
            detail=f"Last task completed in {profile['elapsed_seconds']:.0f} seconds.",
        )
        return {
            "status": str(result.get("status", "completed")),
            "execution_id": execution_id,
            "episode_date": episode_date.isoformat(),
        }
    except Exception as exc:
        # The feed is authoritative once the publisher verified it. A later
        # administrative write must not mark the episode failed or regenerate it.
        try:
            local_episode = database.episode_for_date(episode_date)
        except Exception:
            local_episode = None
            increment("progress_updates_failed")
        if publication_confirmed or (local_episode and local_episode.get("status") == "published"):
            budget.close()  # Use the reserved shutdown margin for state recovery.
            profile = reporter.finish("completed", reason="sync_pending")
            actions = []
            try:
                recovered_id, recovered_metadata = _published_metadata(
                    configured,
                    database,
                    episode_date=episode_date,
                    execution_id=execution_id,
                    publication_label=publication_label,
                    publication_sequence=publication_sequence,
                )
                actions.append(
                    lambda: client.set_private_document(
                        "episodes", recovered_id, recovered_metadata
                    )
                )
            except Exception:
                increment("progress_updates_failed")
            actions.extend(
                [
                    lambda: client.set_private_document(
                        "runner",
                        _publication_sequence_key(publication_label),
                        {
                            "lastSequence": publication_sequence,
                            "seriesLabel": publication_label,
                            "schemaVersion": 2,
                            "updatedAt": datetime.now(UTC),
                        },
                    ),
                    lambda: database.finish_scheduled_execution(
                        execution_id, episode_date, "completed"
                    ),
                    lambda: client.finish_private_execution(
                        execution_id, episode_date.isoformat(), status="completed"
                    ),
                ]
            )
            if request_id:
                actions.append(
                    lambda: client.patch_private_document(
                        "runRequests",
                        request_id,
                        {
                            "status": "published",
                            "finishedAt": datetime.now(UTC),
                            "updatedAt": datetime.now(UTC),
                            "detail": ("Feed verified; monitor synchronization needs attention."),
                        },
                    )
                )
            actions.append(
                lambda: _runner_status(
                    client,
                    state="idle",
                    detail=(
                        "Episode published and verified. Monitor synchronization needs attention; "
                        "do not regenerate it."
                    ),
                )
            )
            for action in actions:
                try:
                    action()
                except Exception:
                    increment("progress_updates_failed")
            _save_timing_profile(client, profile)
            return {
                "status": "published",
                "synchronization_pending": True,
                "execution_id": execution_id,
                "episode_date": episode_date.isoformat(),
            }
        error_name = type(exc).__name__
        reason = {
            "GenerationInterrupted": "interrupted",
            "RunBudgetExceeded": "time_budget",
            "TimeoutExpired": "subprocess_timeout",
            "AntigravityCLIError": "model_failure",
            "VerificationError": "verification_failure",
            "AudioGenerationError": "audio_failure",
            "PublishError": "publish_failure",
            "NoContentError": "no_content",
            "AntigravityPaymentRiskError": "cost_guard",
            "AntigravityConfigurationError": "cost_guard",
        }.get(error_name, "other")
        profile = reporter.finish(
            "interrupted" if isinstance(exc, GenerationInterrupted) else "failed",
            reason=reason,
        )
        _save_timing_profile(client, profile)
        local_detail = f"{error_name}: {exc}"[:4000]
        remote_detail = (
            f"Generation stopped at stage {profile['stage']}/8 ({profile['label']}) "
            f"after {profile['elapsed_seconds']:.0f} seconds ({reason}); "
            "inspect the private local runner log."
        )
        if profile.get("failure_code"):
            remote_detail += f" Diagnostic: {profile['failure_code']}."
        database.finish_scheduled_execution(
            execution_id,
            episode_date,
            "failed",
            local_detail,
        )
        client.finish_private_execution(
            execution_id,
            episode_date.isoformat(),
            status="failed",
        )
        if request_id:
            client.patch_private_document(
                "runRequests",
                request_id,
                {
                    "status": "failed",
                    "finishedAt": datetime.now(UTC),
                    "updatedAt": datetime.now(UTC),
                    "detail": remote_detail,
                },
            )
        _runner_status(client, state="error", detail=remote_detail)
        raise TerminalTaskFailure(
            execution_id=execution_id,
            episode_date=episode_date,
            error_name=error_name,
        ) from exc
    finally:
        budget.close()
        reporter.close()
        _restore_generation_interrupt_handlers(previous_signal_handlers)


def run_web_runner_tick(
    settings: Settings,
    *,
    now: datetime | None = None,
    client: FirebaseWebRunnerClient | None = None,
    pipeline_factory: Callable[[Settings], Pipeline] = Pipeline,
    schedule_id: str | None = None,
    schedule_date: date | None = None,
) -> dict[str, Any]:
    """Run one due web task.

    ``schedule_id`` and its local ``schedule_date`` are supplied by the private
    cloud clock.  They make the runner deterministic: a clock alarm may run
    *only* that saved schedule occurrence and never fall through to an
    unrelated manual request. The generic browser wake-up path deliberately
    retains its oldest-due/manual-queue selection behaviour.
    """
    if not settings.web.enabled:
        raise WebRunnerError("the V4 web runner is disabled")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise WebRunnerError("runner time must include a timezone")
    requested_schedule_id = (schedule_id or "").strip()
    if schedule_date is not None and not requested_schedule_id:
        raise WebRunnerError("schedule date requires an exact schedule id")
    active_client = client or FirebaseWebRunnerClient(settings)
    active_client.authenticate()

    schedules: list[ScheduledJob] = []
    invalid_schedule_count = 0
    for item in active_client.list_private_collection("schedules"):
        document_id = str(item.pop("document_id", ""))
        try:
            schedules.append(ScheduledJob.from_dict(item, schedule_id=document_id))
        except JobValidationError:
            # Keep a malformed legacy schedule isolated. It can be repaired in
            # the web console without blocking valid schedules or manual work.
            invalid_schedule_count += 1

    if requested_schedule_id:
        selected = next(
            (schedule for schedule in schedules if schedule.schedule_id == requested_schedule_id),
            None,
        )
        if selected is None:
            _runner_status(
                active_client,
                state="idle",
                detail="The requested scheduled task is no longer available.",
            )
            return {
                "status": "idle",
                "checked_at": current.isoformat(),
                "schedule_id": requested_schedule_id,
                "detail": "requested schedule is unavailable",
            }
        episode_date = (
            _targeted_schedule_episode_date(
                selected,
                current=current,
                schedule_date=schedule_date,
            )
            if schedule_date is not None
            else (selected.episode_date(current) if selected.is_due(current) else None)
        )
        if episode_date is None:
            _runner_status(
                active_client,
                state="idle",
                detail="The requested scheduled task is not due yet.",
            )
            return {
                "status": "idle",
                "checked_at": current.isoformat(),
                "schedule_id": requested_schedule_id,
                "detail": "requested schedule is not due",
            }
        execution_status = active_client.private_execution_status(
            selected.schedule_id,
            episode_date.isoformat(),
        )
        if execution_status:
            detail = (
                "The requested scheduled task previously failed; it will not retry automatically."
                if execution_status == "failed"
                else "The requested scheduled task was already claimed."
            )
            _runner_status(
                active_client,
                state="idle",
                detail=detail,
            )
            return {
                "status": "already-claimed",
                "execution_id": selected.schedule_id,
                "episode_date": episode_date.isoformat(),
                "detail": detail,
            }
        return _execute_generation(
            settings,
            active_client,
            execution_id=selected.schedule_id,
            display_name=selected.name,
            parameters=selected.parameters,
            episode_date=episode_date,
            pipeline_factory=pipeline_factory,
        )

    due = [schedule for schedule in schedules if schedule.is_due(current)]
    runnable_due = [
        schedule
        for schedule in due
        if active_client.private_execution_status(
            schedule.schedule_id,
            schedule.episode_date(current).isoformat(),
        )
        == ""
    ]
    if runnable_due:
        selected = min(runnable_due, key=lambda job: job.start_time)
        return _execute_generation(
            settings,
            active_client,
            execution_id=selected.schedule_id,
            display_name=selected.name,
            parameters=selected.parameters,
            episode_date=selected.episode_date(current),
            pipeline_factory=pipeline_factory,
        )

    queued = _active_manual_requests(
        active_client,
        active_client.list_private_collection("runRequests"),
        current=current,
    )
    if queued:
        selected_request = min(
            queued,
            key=lambda item: str(item.get("requestedAt", "")),
        )
        request_id = str(selected_request["document_id"])
        requested_date = _request_date(selected_request)
        if requested_date is None:  # guarded by _active_manual_requests
            raise WebRunnerError("queued request contains an invalid episode date")
        return _execute_generation(
            settings,
            active_client,
            execution_id=f"request-{request_id}"[:120],
            display_name=f"Manual web request {requested_date.isoformat()}",
            parameters=GenerationParameters.from_dict(selected_request.get("parameters", {})),
            episode_date=requested_date,
            pipeline_factory=pipeline_factory,
            request_id=request_id,
        )

    detail = "No task is due."
    if invalid_schedule_count:
        detail = "No task is due; one invalid saved schedule was skipped."
    _runner_status(active_client, state="idle", detail=detail)
    return {
        "status": "idle",
        "checked_at": current.isoformat(),
        "schedule_count": len(schedules),
    }
