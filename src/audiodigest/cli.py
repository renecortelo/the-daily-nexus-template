from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from audiodigest.config import generate_secret_path, load_settings
from audiodigest.cost_guard import write_spark_confirmation
from audiodigest.diagnostics import checks_as_json, run_doctor
from audiodigest.gmail_client import GmailClient
from audiodigest.pipeline import Pipeline
from audiodigest.publishing_setup import (
    configure_private_publishing,
    disable_private_publishing,
    enable_private_publishing,
)
from audiodigest.web_runner import authenticate_web_runner, unpair_web_runner
from audiodigest.web_scheduler import run_web_runner_tick


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _print_result(result: dict) -> None:
    safe_result = dict(result)
    if safe_result.pop("feed_url", ""):
        safe_result["private_feed_url"] = (
            "stored locally; use the private-feed-url command or the app's Copy button"
        )
    print(json.dumps(safe_result, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audiodigest",
        description="Generate a private daily podcast from selected Gmail newsletters.",
    )
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Check local tools and zero-cost safety settings")
    doctor.add_argument("--json", action="store_true", dest="as_json")

    run = sub.add_parser("run", help="Run the daily pipeline")
    run.add_argument("--date", type=_date, dest="episode_date")
    run.add_argument("--fixture", type=Path)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--no-article-fetch", action="store_true")
    run.add_argument("--skip-audio", action="store_true")

    publish = sub.add_parser(
        "publish",
        help="Publish one completed local episode to the configured private feed",
    )
    publish.add_argument("--date", type=_date, required=True, dest="episode_date")

    newspaper = sub.add_parser(
        "render-newspaper",
        help=(
            "Render a local edition from existing verified editorial data "
            "(two-page target, three-page maximum)"
        ),
    )
    newspaper.add_argument("--date", type=_date, required=True, dest="episode_date")
    rebuild_newspaper = sub.add_parser(
        "rebuild-newspaper",
        help=(
            "Recreate a local Read edition independently from saved verified "
            "newsletter stories"
        ),
    )
    rebuild_newspaper.add_argument(
        "--date",
        type=_date,
        required=True,
        dest="episode_date",
    )

    sub.add_parser("generate-secret", help="Generate a 128-bit private feed path")
    sub.add_parser(
        "confirm-spark",
        help="Record the operator's confirmation that Firebase is on Spark with no billing",
    )
    configure_publishing = sub.add_parser(
        "configure-publishing",
        help="Configure a dedicated Firebase project while keeping publishing disabled",
    )
    configure_publishing.add_argument("--project-id", required=True)
    sub.add_parser(
        "enable-publishing",
        help="Enable private publishing after the Spark confirmation is recorded",
    )
    sub.add_parser(
        "disable-publishing",
        help="Disable every manual and automatic upload",
    )
    sub.add_parser(
        "private-feed-url",
        help="Print the secret Apple Podcasts RSS URL on explicit request",
    )
    sub.add_parser(
        "authenticate-gmail",
        help="Authorize read-only Gmail access and verify the AudioDigest source label",
    )
    sub.add_parser(
        "logout-gmail",
        help="Revoke Gmail access at Google and remove the local authorization",
    )
    sub.add_parser(
        "authenticate-web-runner",
        help="Pair this trusted runner with the private Firebase owner",
    )
    sub.add_parser(
        "unpair-web-runner",
        help="Remove this runner's Firebase authorization",
    )
    web_runner = sub.add_parser(
        "web-runner",
        help="Check private web schedules and run at most one due task",
    )
    web_runner.add_argument(
        "--schedule-id",
        help="Run only this exact due saved schedule (private cloud clock use).",
    )
    web_runner.add_argument(
        "--schedule-date",
        type=_date,
        help="Local YYYY-MM-DD occurrence for --schedule-id.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "generate-secret":
        print(generate_secret_path())
        return
    config_path = Path(args.config)
    if args.command == "configure-publishing":
        result = configure_private_publishing(config_path, args.project_id)
        print(
            json.dumps(
                {
                    "status": "configured-disabled",
                    "project_id": result.project_id,
                    "base_url": result.base_url,
                    "private_secret": (
                        "created and stored locally"
                        if result.created_new_secret
                        else "existing local secret preserved"
                    ),
                },
                indent=2,
            )
        )
        return
    if args.command == "disable-publishing":
        disable_private_publishing(config_path)
        print("Private publishing is disabled.")
        return
    if args.command == "enable-publishing":
        enable_private_publishing(config_path)
        print("Private publishing is enabled with the recorded Spark confirmation.")
        return
    settings = load_settings(args.config)
    if args.command == "private-feed-url":
        if (
            not settings.firebase.project_id
            or "REPLACE_" in settings.firebase.project_id
            or len(settings.firebase.secret_path) < 32
        ):
            parser.error("private publishing is not configured")
        print(
            f"{settings.firebase.base_url}/p/"
            f"{settings.firebase.secret_path}/feed.xml"
        )
        return
    if args.command == "doctor":
        checks = run_doctor(settings)
        if args.as_json:
            print(checks_as_json(checks))
        else:
            for check in checks:
                symbol = "OK" if check.ok else "FAIL"
                print(f"[{symbol:4}] {check.name}: {check.detail}")
        if any(not check.ok for check in checks):
            raise SystemExit(1)
        return
    if args.command == "confirm-spark":
        confirmation = write_spark_confirmation(settings)
        print(
            f"Recorded Spark confirmation for {confirmation.project_id} at "
            f"{confirmation.confirmed_at}"
        )
        return
    if args.command == "authenticate-gmail":
        client = GmailClient(settings)
        account_email = client.account_email()
        label_id = client.verify_label()
        print(
            f"Read-only Gmail authorization succeeded for {account_email}. "
            f"Found {settings.app.gmail_label!r} ({label_id})."
        )
        return
    if args.command == "logout-gmail":
        result = GmailClient(settings).logout()
        if not result.had_authorization:
            print(result.detail)
            return
        if result.remote_revoked:
            print("Gmail authorization was revoked at Google and removed from this computer.")
            return
        if result.local_deleted:
            print(
                "Gmail authorization was removed from this computer, but Google revocation "
                f"could not be confirmed. {result.detail}"
            )
            raise SystemExit(2)
        print(f"Gmail sign-out failed. {result.detail}")
        raise SystemExit(1)
    if args.command == "authenticate-web-runner":
        identity = authenticate_web_runner(settings)
        print(
            "Secure runner paired for "
            f"{identity.email}. Firebase owner UID: {identity.uid}"
        )
        return
    if args.command == "unpair-web-runner":
        removed = unpair_web_runner(settings)
        print(
            "Secure runner authorization removed."
            if removed
            else "Secure runner was already unpaired."
        )
        return
    if args.command == "web-runner":
        _print_result(
            run_web_runner_tick(
                settings,
                schedule_id=args.schedule_id,
                schedule_date=args.schedule_date,
            )
        )
        return
    if args.command == "run":
        result = Pipeline(settings).run(
            requested_date=args.episode_date,
            fixture=args.fixture,
            dry_run=args.dry_run,
            fetch_articles=not args.no_article_fetch,
            skip_audio=args.skip_audio,
        )
        _print_result(result)
        return
    if args.command == "publish":
        result = Pipeline(settings).publish_episode(args.episode_date)
        _print_result(result)
        return
    if args.command == "render-newspaper":
        result = Pipeline(settings).render_existing_newspaper(args.episode_date)
        _print_result(result)
        return
    if args.command == "rebuild-newspaper":
        result = Pipeline(settings).rebuild_existing_newspaper(args.episode_date)
        _print_result(result)
        return
    parser.error("unknown command")


if __name__ == "__main__":
    main()
