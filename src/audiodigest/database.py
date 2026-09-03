from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path


class StateDatabase:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_id TEXT PRIMARY KEY,
                    episode_date TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_runs (
                    episode_date TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    detail TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_date TEXT PRIMARY KEY,
                    guid TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    audio_path TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    published_at TEXT,
                    status TEXT NOT NULL,
                    show_notes_json TEXT NOT NULL,
                    newspaper_path TEXT,
                    preview_path TEXT
                );
                CREATE TABLE IF NOT EXISTS scheduled_executions (
                    schedule_id TEXT NOT NULL,
                    episode_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    detail TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (schedule_id, episode_date)
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(episodes)").fetchall()}
            if "newspaper_path" not in columns:
                db.execute("ALTER TABLE episodes ADD COLUMN newspaper_path TEXT")
            if "preview_path" not in columns:
                db.execute("ALTER TABLE episodes ADD COLUMN preview_path TEXT")

    def begin_run(self, episode_date: date) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as db:
            existing = db.execute(
                "SELECT status FROM daily_runs WHERE episode_date = ?", (episode_date.isoformat(),)
            ).fetchone()
            if existing and existing["status"] == "published":
                raise RuntimeError(f"{episode_date} is already published")
            db.execute(
                """
                INSERT INTO daily_runs (episode_date, status, started_at, finished_at, detail)
                VALUES (?, 'running', ?, NULL, '')
                ON CONFLICT(episode_date) DO UPDATE SET
                    status='running', started_at=excluded.started_at, finished_at=NULL, detail=''
                """,
                (episode_date.isoformat(), now),
            )

    def finish_run(self, episode_date: date, status: str, detail: str = "") -> None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE daily_runs
                SET status = ?, finished_at = ?, detail = ?
                WHERE episode_date = ?
                """,
                (
                    status,
                    datetime.now(UTC).isoformat(),
                    detail[:4000],
                    episode_date.isoformat(),
                ),
            )

    def is_processed(self, message_id: str) -> bool:
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
                ).fetchone()
                is not None
            )

    def mark_messages_processed(self, message_ids: list[str], episode_date: date) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as db:
            db.executemany(
                """
                INSERT OR IGNORE INTO processed_messages
                (message_id, episode_date, processed_at) VALUES (?, ?, ?)
                """,
                [(item, episode_date.isoformat(), now) for item in message_ids],
            )

    def stage_episode(
        self,
        *,
        episode_date: date,
        guid: str,
        title: str,
        audio_path: Path,
        manifest_path: Path,
        checksum: str,
        duration_seconds: float,
        show_notes: list[str],
        newspaper_path: Path | None = None,
        preview_path: Path | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO episodes (
                    episode_date, guid, title, audio_path, manifest_path, checksum,
                    duration_seconds, published_at, status, show_notes_json,
                    newspaper_path, preview_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'staged', ?, ?, ?)
                ON CONFLICT(episode_date) DO UPDATE SET
                    guid=excluded.guid,
                    title=excluded.title,
                    audio_path=excluded.audio_path,
                    manifest_path=excluded.manifest_path,
                    checksum=excluded.checksum,
                    duration_seconds=excluded.duration_seconds,
                    status='staged',
                    show_notes_json=excluded.show_notes_json,
                    newspaper_path=excluded.newspaper_path,
                    preview_path=excluded.preview_path
                """,
                (
                    episode_date.isoformat(),
                    guid,
                    title,
                    str(audio_path),
                    str(manifest_path),
                    checksum,
                    duration_seconds,
                    json.dumps(show_notes, ensure_ascii=False),
                    str(newspaper_path) if newspaper_path else None,
                    str(preview_path) if preview_path else None,
                ),
            )

    def mark_published(
        self,
        episode_date: date,
        *,
        published_at: str | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE episodes
                SET status='published', published_at=?
                WHERE episode_date=?
                """,
                (
                    published_at or datetime.now(UTC).isoformat(),
                    episode_date.isoformat(),
                ),
            )

    def mark_episode_failed(self, episode_date: date) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE episodes SET status='failed' WHERE episode_date=?",
                (episode_date.isoformat(),),
            )

    def feed_episodes(self, include_staged_date: date | None, limit: int) -> list[dict]:
        if include_staged_date:
            query = """
                SELECT * FROM episodes
                WHERE status = ? OR (status = 'staged' AND episode_date = ?)
                ORDER BY episode_date DESC
                LIMIT ?
            """
            params: tuple[object, ...] = (
                "published",
                include_staged_date.isoformat(),
                limit,
            )
        else:
            query = """
                SELECT * FROM episodes
                WHERE status = ?
                ORDER BY episode_date DESC
                LIMIT ?
            """
            params = ("published", limit)
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["show_notes"] = json.loads(item.pop("show_notes_json"))
            result.append(item)
        return result

    def episode_for_date(self, episode_date: date) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM episodes WHERE episode_date=?",
                (episode_date.isoformat(),),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["show_notes"] = json.loads(item.pop("show_notes_json"))
        return item

    def run_for_date(self, episode_date: date) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM daily_runs WHERE episode_date=?",
                (episode_date.isoformat(),),
            ).fetchone()
        return dict(row) if row is not None else None

    def set_newspaper_paths(
        self,
        episode_date: date,
        newspaper_path: Path,
        preview_path: Path,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE episodes
                SET newspaper_path=?, preview_path=?
                WHERE episode_date=?
                """,
                (
                    str(newspaper_path),
                    str(preview_path),
                    episode_date.isoformat(),
                ),
            )

    def claim_scheduled_execution(
        self,
        schedule_id: str,
        episode_date: date,
    ) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO scheduled_executions (
                    schedule_id, episode_date, status, started_at, finished_at, detail
                ) VALUES (?, ?, 'running', ?, NULL, '')
                """,
                (
                    schedule_id,
                    episode_date.isoformat(),
                    datetime.now(UTC).isoformat(),
                ),
            )
        return cursor.rowcount == 1

    def finish_scheduled_execution(
        self,
        schedule_id: str,
        episode_date: date,
        status: str,
        detail: str = "",
    ) -> None:
        if status not in {"completed", "failed", "skipped"}:
            raise ValueError("scheduled execution status is invalid")
        with self.connect() as db:
            db.execute(
                """
                UPDATE scheduled_executions
                SET status=?, finished_at=?, detail=?
                WHERE schedule_id=? AND episode_date=?
                """,
                (
                    status,
                    datetime.now(UTC).isoformat(),
                    detail[:4000],
                    schedule_id,
                    episode_date.isoformat(),
                ),
            )

    def scheduled_execution(
        self,
        schedule_id: str,
        episode_date: date,
    ) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT * FROM scheduled_executions
                WHERE schedule_id=? AND episode_date=?
                """,
                (schedule_id, episode_date.isoformat()),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_episodes(self, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM episodes
                WHERE status IN ('staged', 'published')
                ORDER BY episode_date DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["show_notes"] = json.loads(item.pop("show_notes_json"))
            result.append(item)
        return result

    def estimated_run_seconds(self, default: int = 1500) -> int:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT
                    (julianday(finished_at) - julianday(started_at)) * 86400
                FROM daily_runs
                WHERE status IN ('staged', 'dry-run', 'published')
                  AND finished_at IS NOT NULL
                ORDER BY finished_at DESC
                LIMIT 5
                """
            ).fetchall()
        values = sorted(
            int(row[0]) for row in rows if row[0] is not None and 60 <= float(row[0]) <= 7200
        )
        if not values:
            return default
        return values[len(values) // 2]
