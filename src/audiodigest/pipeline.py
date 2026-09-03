from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from audiodigest.antigravity_client import AntigravityCLI, AntigravityCLIError
from audiodigest.audio import AudioResult, KokoroAudioRenderer
from audiodigest.closing_quotes import quote_for_date
from audiodigest.config import Settings
from audiodigest.cost_guard import run_cost_guard
from audiodigest.daily_research import DailyResearchError, WikimediaDailyResearch
from audiodigest.database import StateDatabase
from audiodigest.dates import day_window, previous_day_window
from audiodigest.editorial import (
    NEWSPAPER_TARGET_PROSE_WORDS,
    NEWSPAPER_TARGET_TOTAL_WORDS,
    EditorialPipeline,
    newspaper_prose_word_count,
)
from audiodigest.gmail_client import GmailClient, fixture_sources
from audiodigest.models import (
    DataValidationError,
    EpisodeScript,
    NewspaperIssue,
    SourceItem,
    Story,
)
from audiodigest.newspaper import (
    NewspaperRenderer,
    is_legacy_script_style_issue,
)
from audiodigest.publisher import FirebasePublisher, PublishResult
from audiodigest.web_fetcher import ArticleFetchError, SafeArticleFetcher, UnsafeURLError


class NoContentError(RuntimeError):
    pass


class VerificationError(RuntimeError):
    pass


def _stage(number: int, total: int, label: str) -> None:
    print(f"Stage {number}/{total}: {label}", flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _promote_episode_files(files: list[tuple[Path, Path]]) -> None:
    pending_files: list[tuple[Path, Path]] = []
    try:
        for source, destination in files:
            destination.parent.mkdir(parents=True, exist_ok=True)
            pending = destination.with_name(f"{destination.name}.new")
            pending.unlink(missing_ok=True)
            shutil.copy2(source, pending)
            pending_files.append((pending, destination))
        for pending, destination in pending_files:
            pending.replace(destination)
    finally:
        for pending, _destination in pending_files:
            pending.unlink(missing_ok=True)


def _remove_stale_preview_files(
    directory: Path,
    active_paths: list[Path],
) -> None:
    active_names = {path.name.casefold() for path in active_paths}
    for candidate in directory.glob("edition-[0-9]*.png"):
        if candidate.name.casefold() not in active_names:
            candidate.unlink(missing_ok=True)


def _write_progress_status(
    directory: Path,
    *,
    day: date,
    stage: int,
    message: str,
    status: str = "in-progress",
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "status.json"
    temporary = path.with_suffix(".json.new")
    temporary.write_text(
        json.dumps(
            {
                "episode_date": day.isoformat(),
                "status": status,
                "stage": stage,
                "message": message,
                "updated_at": datetime.now().astimezone().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _episode_guid(day: date, execution_id: str | None = None) -> str:
    """Return a stable identity for one published run, not merely its date."""
    if not execution_id:
        # Preserve the original local CLI identity for existing installations.
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"audiodigest:{day.isoformat()}"))
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"audiodigest:{day.isoformat()}:{execution_id.strip()}",
        )
    )


def _execution_storage_key(execution_id: str | None) -> str:
    """Return a safe, opaque directory component for one cloud execution."""
    if not execution_id:
        return ""
    digest = hashlib.sha256(execution_id.strip().encode("utf-8")).hexdigest()
    return f"run-{digest[:16]}"


def _published_episode_title(
    show_title: str,
    *,
    episode_date: date,
    run_name: str | None,
    sequence: int,
) -> str:
    """Give a cloud edition a stable, readable publication name.

    The GUID remains the storage and feed identity.  It must not be exposed as
    the reader-facing episode suffix: a person should be able to distinguish
    two runs for one day from the title alone.
    """
    canonical_show_title = " ".join(show_title.split()) or "The Daily Nexus"
    # Editorial drafts still carry a spoken date in their working title. The
    # published name owns the date prefix, so remove only that known suffix.
    canonical_show_title = re.sub(
        r"\s+-\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}$",
        "",
        canonical_show_title,
        flags=re.IGNORECASE,
    ) or "The Daily Nexus"
    label = (run_name or "").strip()
    if label.casefold() in {"", "daily nexus", "the daily nexus"}:
        label = "Run"
    safe_sequence = max(1, int(sequence))
    return (
        f"{episode_date.strftime('%m/%d/%Y')} {canonical_show_title} - {label} "
        f"- {safe_sequence:03d}"
    )


def _article_failure_category(exc: ArticleFetchError | UnsafeURLError) -> str:
    message = str(exc).lower()
    if "robots.txt" in message:
        return "robots"
    if "http 401" in message or "http 403" in message or "http 429" in message:
        return "access blocked"
    if "redirect" in message:
        return "redirects"
    if "timed out" in message:
        return "timeouts"
    if "readable text" in message:
        return "unreadable"
    if "tracking link" in message:
        return "tracking"
    if isinstance(exc, UnsafeURLError):
        return "unsafe URLs"
    return "other"


def _format_article_summary(stats: Counter[str]) -> str:
    attempted = stats["attempted"]
    retrieved = stats["retrieved"]
    fetch_skipped = attempted - retrieved
    ignored = stats["tracking_skipped"] + stats["utility_skipped"]
    details = [
        f"{stats[name]} {name}"
        for name in (
            "robots",
            "access blocked",
            "redirects",
            "timeouts",
            "unreadable",
            "tracking",
            "unsafe URLs",
            "other",
        )
        if stats[name]
    ]
    message = f"Article enrichment: {retrieved} retrieved from {attempted} direct links"
    if fetch_skipped:
        message += f"; {fetch_skipped} fetches skipped ({', '.join(details)})"
    if ignored:
        message += f"; {ignored} tracking or utility links ignored before fetching"
    if stats["unwrapped"]:
        message += f"; {stats['unwrapped']} tracking destinations safely decoded"
    return f"{message}."


def _evidence_mix(
    sources: list[SourceItem],
    stories: list[Story] | None = None,
) -> dict[str, int | str]:
    """Summarize provenance without retaining newsletter bodies in public metadata."""

    newsletters = [item for item in sources if item.source_type == "newsletter"]
    newsletter_ids = {item.message_id for item in newsletters}
    mix: dict[str, int | str] = {
        "mode": "newsletter_first",
        "newsletter_messages": len(newsletters),
        "safe_article_links": sum(len(item.article_urls) for item in newsletters),
        "safe_articles_retrieved": sum(len(item.articles) for item in newsletters),
        "research_sources": sum(
            item.source_type in {"history", "current_world"} for item in sources
        ),
    }
    if stories is not None:
        mix["newsletter_backed_stories"] = sum(
            bool(set(story.source_ids) & newsletter_ids) for story in stories
        )
    return mix


class Pipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = StateDatabase(settings.database_path)
        self.antigravity = AntigravityCLI(settings.antigravity)
        self.editorial = EditorialPipeline(settings, self.antigravity)

    def _episode_day(self, requested: date | None) -> date:
        if requested:
            return requested
        return previous_day_window(datetime.now(self.settings.timezone), self.settings.timezone).day

    def _load_sources(self, day: date, fixture: Path | None) -> list[SourceItem]:
        if fixture:
            sources = fixture_sources(fixture)
        else:
            window = day_window(day, self.settings.timezone)
            newsletters = GmailClient(self.settings).fetch_newsletters(window)
            sources = [
                item for item in newsletters if not self.database.is_processed(item.message_id)
            ]
            if (
                self.settings.research.enabled
                and self.settings.podcast.include_today_in_history
                and getattr(
                    self.settings.podcast, "evidence_mode", "newsletter_first"
                )
                != "newsletter_only"
            ):
                try:
                    sources.extend(WikimediaDailyResearch(self.settings.research).fetch(day))
                except DailyResearchError as exc:
                    if self.settings.research.required:
                        raise
                    print(f"Warning: daily research was skipped: {exc}")
        if not self.settings.podcast.include_today_in_history:
            sources = [
                item
                for item in sources
                if item.source_type not in {"history", "current_world"}
            ]
        if getattr(self.settings.podcast, "evidence_mode", "newsletter_first") == "newsletter_only":
            sources = [item for item in sources if item.source_type == "newsletter"]
        return sources

    def _enrich_articles(self, sources: list[SourceItem], *, fetch_articles: bool) -> None:
        evidence_mode = getattr(
            self.settings.podcast, "evidence_mode", "newsletter_first"
        )
        if not fetch_articles or evidence_mode == "newsletter_only":
            if evidence_mode == "newsletter_only":
                print("Article enrichment: disabled by newsletter-only evidence mode.")
            return
        fetcher = SafeArticleFetcher(self.settings.articles)
        stats: Counter[str] = Counter()
        for source in sources:
            if source.source_type != "newsletter":
                continue
            stats.update(source.link_stats)
            for url in source.article_urls[: self.settings.app.max_articles_per_newsletter]:
                stats["attempted"] += 1
                try:
                    source.articles.append(fetcher.extract(url))
                    stats["retrieved"] += 1
                except (ArticleFetchError, UnsafeURLError) as exc:
                    stats[_article_failure_category(exc)] += 1
                    continue
        print(_format_article_summary(stats))

    def _generate_verified_newspaper(
        self,
        stories: list[Story],
        day: date,
    ) -> tuple[NewspaperIssue, list[dict[str, Any]]]:
        metadata: list[dict[str, Any]] = []
        structural_repairs: dict[str, tuple[str, list[str]]] = {
            "spoken-script phrasing": (
                "Newspaper draft used spoken-script phrasing; requesting a fresh "
                "reader-only edition.",
                [
                    "Write a reader-facing newspaper, not dialogue. Remove host "
                    "speaker labels, direct address, greetings, reactions, and "
                    "first-person host introductions. A person sharing a host's "
                    "name may appear only as third-person, evidence-backed reporting."
                ],
            ),
            "executive signal labels": (
                "Newspaper executive labels were too generic; requesting a "
                "specific reader-ready rewrite.",
                [
                    "Keep SHIFT, IMPACT, and WATCH as the executive values, but give "
                    "each a concrete 2-12 word label naming the actor, development, "
                    "or consequence. Labels such as AI, News, Update, Shift, Impact, "
                    "or Watch alone are not acceptable."
                ],
            ),
            "compact articles": (
                "Newspaper draft missed its edition-scale article range; requesting "
                "a structured rewrite.",
                [
                    "Use the required article range for this edition scale. For a "
                    "focused label, consolidate related reporting into complete "
                    "articles and retain distinct secondary facts as briefs. Do not "
                    "pad by repeating facts or splitting one story artificially."
                ],
            ),
            "visual item details": (
                "Newspaper visual copy missed a display limit; requesting a "
                "reader-ready visual rewrite.",
                [
                    "Rewrite the primary visual with reader-ready display copy. Every "
                    "visual detail must be optional or a complete 5-7 word phrase of "
                    "34 characters or fewer; never shorten a phrase mid-thought or use "
                    "an ellipsis. Prefer a shorter complete consequence over a generic "
                    "label.",
                ],
            ),
            "repeats a full sentence across sections": (
                "Newspaper copy repeated a full sentence; requesting a "
                "deduplicated reader-ready rewrite.",
                [
                    "Create a fresh reader-facing edition with no repeated full "
                    "sentences across the lead, executive signal, briefs, articles, "
                    "or visual copy. Preserve each verified development once, then "
                    "use a distinct concise synthesis where context is needed."
                ],
            ),
        }
        repair_issues: list[str] | None = None
        # A generated edition can expose more than one independent display
        # defect. Retry a small, bounded sequence of known structural repairs
        # rather than failing a complete podcast after the first correction.
        for structural_attempt in range(3):
            try:
                newspaper, newspaper_meta = self.editorial.generate_newspaper(
                    stories,
                    day,
                    repair_issues=repair_issues,
                )
                break
            except AntigravityCLIError as exc:
                error_detail = str(exc).casefold()
                matched = next(
                    (
                        repair
                        for marker, repair in structural_repairs.items()
                        if marker in error_detail
                    ),
                    None,
                )
                if matched is None or structural_attempt == 2:
                    raise
                message, repair_issues = matched
                print(message, flush=True)
        else:  # pragma: no cover - loop either returns or raises above
            raise VerificationError("newspaper structural repair unexpectedly ended")
        metadata.append({"stage": "newspaper", **newspaper_meta.to_dict()})
        if (
            newspaper.word_count > NEWSPAPER_TARGET_TOTAL_WORDS
            or newspaper_prose_word_count(newspaper)
            > NEWSPAPER_TARGET_PROSE_WORDS
        ):
            print(
                "Independent newspaper draft is being compressed for the "
                "two-page layout.",
                flush=True,
            )
            newspaper, compact_meta = self.editorial.generate_newspaper(
                stories,
                day,
                repair_issues=[
                    "Compress the rejected draft toward no more than 1,300 total "
                    "structured words and 1,050 article/brief words. These are the "
                    "two-page targets; never exceed the three-page safety ceiling.",
                    "Remove repeated facts and sentences instead of cutting "
                    "sentences or deleting distinct high-priority developments.",
                ],
                previous_issue=newspaper,
            )
            metadata.append(
                {"stage": "newspaper-compact", **compact_meta.to_dict()}
            )
            if (
                newspaper.word_count > NEWSPAPER_TARGET_TOTAL_WORDS
                or newspaper_prose_word_count(newspaper)
                > NEWSPAPER_TARGET_PROSE_WORDS
            ):
                print(
                    "Newspaper remains above the two-page target but within the "
                    "three-page safety ceiling; the renderer will use page 3 only "
                    "if legibility requires it.",
                    flush=True,
                )
        review, review_meta = self.editorial.verify_newspaper(
            stories,
            newspaper,
        )
        metadata.append({"stage": "newspaper-review", **review_meta.to_dict()})
        if review.approved:
            return newspaper, metadata

        repair_attempt = 0
        repair_limit = max(
            2,
            int(getattr(self.settings.podcast, "max_script_repairs", 2)),
        )
        while not review.approved and repair_attempt < repair_limit:
            repair_attempt += 1
            print(
                "Newspaper quality review requested an independent rewrite "
                f"({repair_attempt}/{repair_limit}).",
                flush=True,
            )
            try:
                newspaper, repair_meta = self.editorial.generate_newspaper(
                    stories,
                    day,
                    repair_issues=review.issues,
                    previous_issue=newspaper,
                )
            except AntigravityCLIError as exc:
                if repair_attempt < repair_limit:
                    print(
                        "Newspaper rewrite did not pass structural validation; "
                        "starting the next independent rewrite.",
                        flush=True,
                    )
                    continue
                recoverable_rewrite_failure = (
                    "timeout waiting for response" in str(exc).lower()
                    or "visual item details" in str(exc).lower()
                )
                if recoverable_rewrite_failure:
                    # The existing edition has already passed the structural
                    # generator validation. A transient CLI timeout or an
                    # optional visual-copy rewrite failure must not discard a
                    # complete audio episode and independently written paper.
                    print(
                        "Warning: newspaper quality rewrite could not improve "
                        "the prior independent edition; keeping it for this run.",
                        flush=True,
                    )
                    break
                raise VerificationError(
                    "newspaper rewrites could not satisfy the three-page "
                    f"structural ceiling after {repair_limit} attempts: {exc}"
                ) from exc
            metadata.append(
                {
                    "stage": f"newspaper-repair-{repair_attempt}",
                    **repair_meta.to_dict(),
                }
            )
            review, recheck_meta = self.editorial.verify_newspaper(
                stories,
                newspaper,
            )
            metadata.append(
                {
                    "stage": f"newspaper-recheck-{repair_attempt}",
                    **recheck_meta.to_dict(),
                }
            )
        if not review.approved:
            print(
                "Warning: newspaper quality review: " + "; ".join(review.issues),
                flush=True,
            )
        return newspaper, metadata

    def _write_manifest(
        self,
        path: Path,
        *,
        day: date,
        source_ids: list[str],
        source_mix: dict[str, int | str],
        stories,
        script: EpisodeScript,
        newspaper: NewspaperIssue,
        newspaper_path: Path,
        preview_paths: list[Path],
        transcript_path: Path,
        antigravity_metadata: list[dict[str, Any]],
        duration_seconds: float,
        checksum: str,
        guid: str,
        edition_name: str = "",
    ) -> None:
        manifest = {
            "episode_date": day.isoformat(),
            "guid": guid,
            "source_message_ids": source_ids,
            "source_mix": source_mix,
            "stories": [item.to_dict() for item in stories],
            "script": script.to_dict(),
            "newspaper": newspaper.to_dict(),
            "newspaper_path": str(newspaper_path),
            "newspaper_preview_path": str(preview_paths[0]),
            "newspaper_preview_paths": [str(path) for path in preview_paths],
            "transcript_path": str(transcript_path),
            "antigravity_calls": antigravity_metadata,
            "duration_seconds": duration_seconds,
            "audio_sha256": checksum,
            "edition_name": " ".join(edition_name.split()),
        }
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def run(
        self,
        *,
        requested_date: date | None = None,
        fixture: Path | None = None,
        dry_run: bool = False,
        fetch_articles: bool = True,
        skip_audio: bool = False,
        execution_id: str | None = None,
        run_name: str | None = None,
        run_sequence: int = 1,
    ) -> dict[str, Any]:
        publish = (
            self.settings.firebase.publish_enabled
            and self.settings.firebase.publish_mode == "automatic"
            and not dry_run
        )
        run_cost_guard(self.settings, publishing=publish)
        day = self._episode_day(requested_date)
        guid = _episode_guid(day, execution_id)
        execution_storage_key = _execution_storage_key(execution_id)
        self.database.begin_run(day)
        episode_dir = self.settings.episodes_dir / day.isoformat()
        if execution_storage_key:
            # A cloud batch can intentionally create distinct editions for one
            # calendar day. Keep their working artefacts separate until each
            # GUID has been copied into the private feed.
            episode_dir = episode_dir / execution_storage_key
        in_progress_dir = episode_dir / "in-progress"
        staging = self.settings.staging_dir / day.isoformat()
        if execution_storage_key:
            staging = self.settings.staging_dir / f"{day.isoformat()}-{execution_storage_key}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        source_payload_path = staging / "sources.json"
        try:
            evidence_mode = getattr(
                self.settings.podcast, "evidence_mode", "newsletter_first"
            )
            research_label = (
                " and daily research"
                if (
                    self.settings.podcast.include_today_in_history
                    and evidence_mode != "newsletter_only"
                )
                else ""
            )
            _stage(1, 8, f"Loading approved Gmail newsletters{research_label}")
            sources = self._load_sources(day, fixture)
            if not sources:
                raise NoContentError(f"No eligible newsletter or research sources found for {day}")
            _stage(2, 8, "Retrieving safe public article text")
            newsletter_count = sum(
                source.source_type == "newsletter" for source in sources
            )
            if not newsletter_count:
                raise NoContentError(
                    "No eligible newsletter messages were found; independent research "
                    "will not replace the selected Gmail label."
                )
            newsletter_links = sum(
                len(source.article_urls)
                for source in sources
                if source.source_type == "newsletter"
            )
            print(
                "Newsletter scan: "
                f"{newsletter_count} Gmail newsletters scanned; "
                f"{newsletter_links} direct article links found.",
                flush=True,
            )
            self._enrich_articles(sources, fetch_articles=fetch_articles)
            source_mix = _evidence_mix(sources)
            source_mix["mode"] = evidence_mode
            print(
                "Evidence mix: "
                f"mode={evidence_mode}; {source_mix['newsletter_messages']} newsletter "
                f"bodies; {source_mix['safe_article_links']} safe direct links; "
                f"{source_mix['safe_articles_retrieved']} public articles retrieved; "
                f"{source_mix['research_sources']} independent research sources.",
                flush=True,
            )
            source_payload_path.write_text(
                json.dumps(
                    [item.to_prompt_dict() for item in sources],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            _stage(3, 8, "Extracting and ranking evidence-backed stories")
            stories, extract_meta = self.editorial.extract_stories(sources, day)
            if not stories:
                raise NoContentError("Antigravity found no substantive stories")
            source_mix = _evidence_mix(sources, stories)
            source_mix["mode"] = evidence_mode
            if not source_mix["newsletter_backed_stories"]:
                raise NoContentError(
                    "No newsletter-backed stories were extracted. The selected Gmail "
                    "label was not replaced with independent research."
                )
            print(
                "Evidence coverage: "
                f"{source_mix['newsletter_backed_stories']} newsletter-backed stories "
                "will lead the editorial.",
                flush=True,
            )
            closing_quote = quote_for_date(self.settings.podcast.closing_quotes_path, day)
            _stage(4, 8, "Drafting the host script")
            script, script_meta = self.editorial.generate_script(stories, day, closing_quote)
            _stage(5, 8, "Fact-checking the script")
            verification, verify_meta = self.editorial.verify(stories, script, closing_quote)
            metadata = [
                {"stage": "extract", **extract_meta.to_dict()},
                {"stage": "script", **script_meta.to_dict()},
                {"stage": "verify", **verify_meta.to_dict()},
            ]
            repair_issues: list[str] = []
            repair_attempt = 0
            repair_limit = max(3, self.settings.podcast.max_script_repairs)
            while (
                not verification.approved
                and repair_attempt < repair_limit
            ):
                repair_attempt += 1
                repair_issues.extend(
                    issue for issue in verification.issues if issue not in repair_issues
                )
                _stage(
                    5,
                    8,
                    (
                        "Repairing verifier issues "
                        f"({repair_attempt}/{repair_limit})"
                    ),
                )
                script, repair_meta = self.editorial.generate_script(
                    stories,
                    day,
                    closing_quote,
                    repair_issues=repair_issues,
                    previous_script=script,
                )
                verification, final_verify_meta = self.editorial.verify(
                    stories, script, closing_quote
                )
                metadata.extend(
                    [
                        {
                            "stage": f"repair-{repair_attempt}",
                            **repair_meta.to_dict(),
                        },
                        {
                            "stage": f"reverify-{repair_attempt}",
                            **final_verify_meta.to_dict(),
                        },
                    ]
                )
            if not verification.approved:
                raise VerificationError("; ".join(verification.issues))
            if execution_id:
                script.title = _published_episode_title(
                    self.settings.podcast.title,
                    episode_date=day,
                    run_name=run_name,
                    sequence=run_sequence,
                )

            working_episode_dir = staging / "episode"
            working_episode_dir.mkdir(parents=True, exist_ok=True)
            script_work_path = working_episode_dir / "script.json"
            script_work_path.write_text(
                json.dumps(script.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _promote_episode_files(
                [(script_work_path, in_progress_dir / "script.json")]
            )
            _write_progress_status(
                in_progress_dir,
                day=day,
                stage=6,
                message=(
                    "Verified script available; preparing the two-page edition "
                    "with a third page reserved only for readable overflow."
                ),
            )

            _stage(
                6,
                8,
                "Writing the newspaper edition (two-page target; three-page maximum)",
            )
            try:
                newspaper, newspaper_metadata = self._generate_verified_newspaper(
                    stories,
                    day,
                )
            except Exception as exc:
                raise VerificationError(
                    "Independent newspaper generation failed. The audio script "
                    "was not reused for Read mode: "
                    f"{exc}"
                ) from exc
            metadata.extend(newspaper_metadata)
            newspaper_json_work_path = working_episode_dir / "newspaper.json"
            newspaper_json_work_path.write_text(
                json.dumps(
                    newspaper.to_dict(),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            _promote_episode_files(
                [
                    (
                        newspaper_json_work_path,
                        in_progress_dir / "newspaper.json",
                    )
                ]
            )
            newspaper_work_path = working_episode_dir / "edition.pdf"
            preview_work_path = working_episode_dir / "edition-1.png"
            newspaper_result = NewspaperRenderer(self.settings).render(
                newspaper,
                day,
                newspaper_work_path,
                preview_work_path,
                edition_name=run_name if execution_id else "",
            )
            preview_work_paths = list(newspaper_result.preview_paths)
            in_progress_preview_paths = [
                in_progress_dir / f"edition-{index}.png"
                for index in range(1, len(preview_work_paths) + 1)
            ]
            _promote_episode_files(
                [
                    (script_work_path, in_progress_dir / "script.json"),
                    (newspaper_json_work_path, in_progress_dir / "newspaper.json"),
                    (newspaper_work_path, in_progress_dir / "edition.pdf"),
                    *zip(
                        preview_work_paths,
                        in_progress_preview_paths,
                        strict=True,
                    ),
                ]
            )
            _remove_stale_preview_files(
                in_progress_dir,
                in_progress_preview_paths,
            )
            _write_progress_status(
                in_progress_dir,
                day=day,
                stage=7,
                message="Script and newspaper available; rendering local audio.",
            )

            script_path = episode_dir / "script.json"
            newspaper_json_path = episode_dir / "newspaper.json"
            newspaper_path = episode_dir / "edition.pdf"
            preview_paths = [
                episode_dir / f"edition-{index}.png"
                for index in range(1, len(preview_work_paths) + 1)
            ]
            preview_path = preview_paths[0]
            if skip_audio:
                _promote_episode_files(
                    [
                        (script_work_path, script_path),
                        (newspaper_json_work_path, newspaper_json_path),
                        (newspaper_work_path, newspaper_path),
                        *zip(preview_work_paths, preview_paths, strict=True),
                    ]
                )
                _remove_stale_preview_files(episode_dir, preview_paths)
                self.database.finish_run(
                    day,
                    "dry-run",
                    "script and newspaper generated; audio skipped",
                )
                shutil.rmtree(in_progress_dir, ignore_errors=True)
                return {
                    "status": "dry-run",
                    "episode_date": day.isoformat(),
                    "script_path": str(script_path),
                    "newspaper_path": str(newspaper_path),
                    "preview_path": str(preview_path),
                    "preview_paths": [str(path) for path in preview_paths],
                    "word_count": script.word_count,
                }

            _stage(7, 8, "Rendering local multi-voice audio")
            audio_work_path = working_episode_dir / "episode.mp3"
            audio_path = episode_dir / "episode.mp3"
            audio_result: AudioResult = KokoroAudioRenderer(
                self.settings.audio,
                self.settings.hosts,
            ).render(script, audio_work_path)
            transcript_work_path = working_episode_dir / "transcript.json"
            transcript_work_path.write_text(
                json.dumps(
                    {
                        "episode_date": day.isoformat(),
                        "segments": [
                            segment.to_dict()
                            for segment in audio_result.transcript_segments
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            _promote_episode_files(
                [
                    (audio_work_path, in_progress_dir / "episode.mp3"),
                    (transcript_work_path, in_progress_dir / "transcript.json"),
                ]
            )
            _write_progress_status(
                in_progress_dir,
                day=day,
                stage=8,
                message="Audio complete; finalizing the local episode.",
            )
            checksum = _sha256(audio_work_path)
            manifest_work_path = working_episode_dir / "manifest.json"
            manifest_path = episode_dir / "manifest.json"
            transcript_path = episode_dir / "transcript.json"
            self._write_manifest(
                manifest_work_path,
                day=day,
                source_ids=[
                    item.message_id for item in sources if item.source_type == "newsletter"
                ],
                source_mix=source_mix,
                stories=stories,
                script=script,
                newspaper=newspaper,
                newspaper_path=newspaper_path,
                preview_paths=preview_paths,
                transcript_path=transcript_path,
                antigravity_metadata=metadata,
                duration_seconds=audio_result.duration_seconds,
                checksum=checksum,
                guid=guid,
                edition_name=run_name if execution_id else "",
            )
            _promote_episode_files(
                [
                    (script_work_path, script_path),
                    (newspaper_json_work_path, newspaper_json_path),
                    (newspaper_work_path, newspaper_path),
                    *zip(preview_work_paths, preview_paths, strict=True),
                    (audio_work_path, audio_path),
                    (transcript_work_path, transcript_path),
                    (manifest_work_path, manifest_path),
                ]
            )
            _remove_stale_preview_files(episode_dir, preview_paths)
            self.database.stage_episode(
                episode_date=day,
                guid=guid,
                title=script.title,
                audio_path=audio_path,
                manifest_path=manifest_path,
                checksum=checksum,
                duration_seconds=audio_result.duration_seconds,
                show_notes=script.show_notes,
                newspaper_path=newspaper_path,
                preview_path=preview_path,
            )
            publish_result: PublishResult | None = None
            if publish:
                _stage(
                    8,
                    8,
                    "Publishing and remotely verifying the private Apple RSS feed",
                )
                publish_result = FirebasePublisher(self.settings, self.database).publish(day)
                status = "published"
            else:
                _stage(8, 8, "Finalizing the local episode")
                status = "staged"
            if publish:
                self.database.mark_messages_processed(
                    [item.message_id for item in sources if item.source_type == "newsletter"],
                    day,
                )
            self.database.finish_run(day, status)
            shutil.rmtree(in_progress_dir, ignore_errors=True)

            if self.settings.app.backup_dir:
                backup = self.settings.app.backup_dir / day.isoformat()
                if execution_storage_key:
                    backup = backup / execution_storage_key
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(audio_path, backup / audio_path.name)
                shutil.copy2(manifest_path, backup / manifest_path.name)
                shutil.copy2(newspaper_path, backup / newspaper_path.name)

            return {
                "status": status,
                "episode_date": day.isoformat(),
                "audio_path": str(audio_path),
                "manifest_path": str(manifest_path),
                "newspaper_path": str(newspaper_path),
                "preview_path": str(preview_path),
                "preview_paths": [str(path) for path in preview_paths],
                "word_count": script.word_count,
                "duration_seconds": audio_result.duration_seconds,
                "feed_url": publish_result.feed_url if publish_result else "",
                "feed_episode_count": (
                    publish_result.episode_count if publish_result else 0
                ),
                "hosted_megabytes": (
                    round(publish_result.hosted_bytes / (1024 * 1024), 2)
                    if publish_result
                    else 0
                ),
                "remote_verified": (
                    publish_result.remote_verified if publish_result else False
                ),
            }
        except Exception as exc:
            self.database.finish_run(day, "failed", str(exc))
            if in_progress_dir.exists():
                _write_progress_status(
                    in_progress_dir,
                    day=day,
                    stage=0,
                    status="failed",
                    message=str(exc)[:500],
                )
            raise
        finally:
            if self.settings.safety.delete_source_payloads_after_success and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def publish_episode(self, episode_date: date) -> dict[str, Any]:
        run_cost_guard(self.settings, publishing=True)
        episode = self.database.episode_for_date(episode_date)
        if episode is None:
            raise NoContentError(
                f"No completed local episode exists for {episode_date.isoformat()}"
            )
        if episode["status"] == "published":
            feed_url = (
                f"{self.settings.firebase.base_url}/p/{self.settings.firebase.secret_path}/feed.xml"
            )
            return {
                "status": "published",
                "episode_date": episode_date.isoformat(),
                "feed_url": feed_url,
                "already_published": True,
            }
        _stage(1, 1, "Publishing and remotely verifying the selected private episode")
        try:
            result = FirebasePublisher(
                self.settings,
                self.database,
            ).publish(episode_date)
            manifest = json.loads(Path(episode["manifest_path"]).read_text(encoding="utf-8"))
            self.database.mark_messages_processed(
                list(manifest.get("source_message_ids", [])),
                episode_date,
            )
            self.database.finish_run(episode_date, "published")
        except Exception as exc:
            self.database.finish_run(
                episode_date,
                "publish-failed",
                str(exc),
            )
            raise
        return {
            "status": "published",
            "episode_date": episode_date.isoformat(),
            "feed_url": result.feed_url,
            "episode_count": result.episode_count,
            "hosted_megabytes": round(result.hosted_bytes / (1024 * 1024), 2),
            "remote_verified": result.remote_verified,
            "already_published": False,
        }

    def render_existing_newspaper(self, episode_date: date) -> dict[str, Any]:
        episode = self.database.episode_for_date(episode_date)
        if episode is None:
            raise NoContentError(
                f"No completed local episode exists for {episode_date.isoformat()}"
            )
        episode_dir = Path(episode["audio_path"]).parent
        newspaper_json_path = episode_dir / "newspaper.json"
        if not newspaper_json_path.is_file():
            raise VerificationError(
                "No independent newspaper data exists. Use rebuild-newspaper "
                "to create it from the saved verified stories."
            )
        try:
            newspaper = NewspaperIssue.from_dict(
                json.loads(newspaper_json_path.read_text(encoding="utf-8"))
            )
        except DataValidationError as exc:
            raise VerificationError(
                "The saved newspaper data is invalid. Use rebuild-newspaper "
                "to recreate it independently."
            ) from exc
        if is_legacy_script_style_issue(newspaper):
            raise VerificationError(
                "This edition came from the retired audio-script fallback. "
                "Use rebuild-newspaper to recreate it from verified stories."
            )
        manifest_path = Path(episode["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        edition_name = str(manifest.get("edition_name", "")).strip()
        newspaper_path = episode_dir / "edition.pdf"
        preview_path = episode_dir / "edition-1.png"
        newspaper_result = NewspaperRenderer(self.settings).render(
            newspaper,
            episode_date,
            newspaper_path,
            preview_path,
            edition_name=edition_name,
        )
        preview_paths = list(newspaper_result.preview_paths)
        _remove_stale_preview_files(episode_dir, preview_paths)
        self.database.set_newspaper_paths(
            episode_date,
            newspaper_path,
            preview_path,
        )
        manifest["newspaper"] = newspaper.to_dict()
        manifest["newspaper_path"] = str(newspaper_path)
        manifest["newspaper_preview_path"] = str(preview_path)
        manifest["newspaper_preview_paths"] = [str(path) for path in preview_paths]
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "status": "rendered",
            "episode_date": episode_date.isoformat(),
            "newspaper_path": str(newspaper_path),
            "preview_path": str(preview_path),
            "preview_paths": [str(path) for path in preview_paths],
        }

    def rebuild_existing_newspaper(self, episode_date: date) -> dict[str, Any]:
        run_cost_guard(self.settings, publishing=False)
        episode = self.database.episode_for_date(episode_date)
        if episode is None:
            raise NoContentError(
                f"No completed local episode exists for {episode_date.isoformat()}"
            )
        episode_dir = Path(episode["audio_path"]).parent
        manifest_path = Path(episode["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_stories = manifest.get("stories")
        if not isinstance(raw_stories, list) or not raw_stories:
            raise NoContentError(
                "The completed episode does not contain saved verified stories. "
                "Run the episode again to rebuild its Read edition independently."
            )
        stories = [
            Story.from_dict(item, allowed_sections=None)
            for item in raw_stories
            if isinstance(item, dict)
        ]
        if not stories:
            raise NoContentError("No valid verified stories were saved for this episode.")

        _stage(1, 3, "Loading saved verified newsletter stories")
        _stage(2, 3, "Writing and quality-checking an independent Read edition")
        newspaper, metadata = self._generate_verified_newspaper(
            stories,
            episode_date,
        )

        working_dir = (
            self.settings.staging_dir
            / f"{episode_date.isoformat()}-newspaper-rebuild"
        )
        if working_dir.exists():
            shutil.rmtree(working_dir)
        working_dir.mkdir(parents=True)
        try:
            newspaper_json_work = working_dir / "newspaper.json"
            newspaper_json_work.write_text(
                json.dumps(newspaper.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            newspaper_work = working_dir / "edition.pdf"
            preview_work = working_dir / "edition-1.png"
            result = NewspaperRenderer(self.settings).render(
                newspaper,
                episode_date,
                newspaper_work,
                preview_work,
                edition_name=str(manifest.get("edition_name", "")).strip(),
            )
            preview_work_paths = list(result.preview_paths)
            _stage(3, 3, "Replacing only the local Read edition")

            old_json = episode_dir / "newspaper.json"
            old_pdf = episode_dir / "edition.pdf"
            backup_json = episode_dir / "newspaper-before-independent-rebuild.json"
            backup_pdf = episode_dir / "edition-before-independent-rebuild.pdf"
            if old_json.is_file() and not backup_json.exists():
                shutil.copy2(old_json, backup_json)
            if old_pdf.is_file() and not backup_pdf.exists():
                shutil.copy2(old_pdf, backup_pdf)

            newspaper_path = episode_dir / "edition.pdf"
            preview_path = episode_dir / "edition-1.png"
            preview_paths = [
                episode_dir / f"edition-{index}.png"
                for index in range(1, len(preview_work_paths) + 1)
            ]
            updated_manifest = dict(manifest)
            updated_manifest["newspaper"] = newspaper.to_dict()
            updated_manifest["newspaper_path"] = str(newspaper_path)
            updated_manifest["newspaper_preview_path"] = str(preview_path)
            updated_manifest["newspaper_preview_paths"] = [
                str(path) for path in preview_paths
            ]
            updated_manifest.setdefault("antigravity_calls", []).extend(
                {
                    **item,
                    "stage": f"independent-rebuild-{item['stage']}",
                }
                for item in metadata
            )
            manifest_work = working_dir / "manifest.json"
            manifest_work.write_text(
                json.dumps(updated_manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _promote_episode_files(
                [
                    (newspaper_json_work, old_json),
                    (newspaper_work, newspaper_path),
                    *zip(preview_work_paths, preview_paths, strict=True),
                    (manifest_work, manifest_path),
                ]
            )
            _remove_stale_preview_files(episode_dir, preview_paths)
            self.database.set_newspaper_paths(
                episode_date,
                newspaper_path,
                preview_path,
            )
        finally:
            shutil.rmtree(working_dir, ignore_errors=True)
        return {
            "status": "rebuilt",
            "episode_date": episode_date.isoformat(),
            "newspaper_path": str(newspaper_path),
            "preview_path": str(preview_path),
            "preview_paths": [str(path) for path in preview_paths],
            "story_count": len(stories),
        }
