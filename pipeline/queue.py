"""SQLite-backed job queue for the video pipeline."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    status      TEXT    NOT NULL DEFAULT 'pending',
    brief_json  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    started_at  TEXT,
    completed_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_log   TEXT,
    output_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
"""

# Valid statuses
PENDING   = "pending"
RUNNING   = "running"
DONE      = "done"
FAILED    = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobQueue:
    """Thread-safe SQLite job queue.

    All public methods acquire a reentrant lock so they can be called
    from multiple threads (APScheduler + asyncio thread pool).
    """

    def __init__(self, db_path: str | Path = "pipeline_jobs.db") -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, brief: dict) -> int:
        """Add a job to the queue. Returns the new job ID."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO jobs (brief_json, created_at) VALUES (?, ?)",
                (json.dumps(brief), _now()),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def dequeue_batch(self, limit: int = 2) -> List[dict]:
        """Claim up to *limit* pending jobs atomically. Returns list of job rows."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id LIMIT ?",
                (PENDING, limit),
            ).fetchall()
            ids = [r["id"] for r in rows]
            if ids:
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE jobs SET status=?, started_at=? WHERE id IN ({placeholders})",
                    [RUNNING, _now()] + ids,
                )
            jobs = [dict(r) for r in rows]
            for j in jobs:
                j["status"] = RUNNING
            return jobs

    def mark_done(self, job_id: int, output_path: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, completed_at=?, output_path=? WHERE id=?",
                (DONE, _now(), output_path, job_id),
            )

    def mark_failed(self, job_id: int, error: str, *, retry: bool = False) -> None:
        """Move job to failed or re-queue for one automatic retry."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT retry_count FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                return
            retries = row["retry_count"]
            if retry and retries < 1:
                conn.execute(
                    "UPDATE jobs SET status=?, retry_count=?, error_log=?, started_at=NULL "
                    "WHERE id=?",
                    (PENDING, retries + 1, error, job_id),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET status=?, completed_at=?, error_log=? WHERE id=?",
                    (FAILED, _now(), error, job_id),
                )

    def get_pending_count(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as n FROM jobs WHERE status=?", (PENDING,)
            ).fetchone()
            return row["n"]

    def get_stats(self) -> dict:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM jobs GROUP BY status"
            ).fetchall()
            return {r["status"]: r["n"] for r in rows}

    def get_completed_today(self) -> List[dict]:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? AND completed_at LIKE ?",
                (DONE, f"{today}%"),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_failed_today(self) -> List[dict]:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? AND completed_at LIKE ?",
                (FAILED, f"{today}%"),
            ).fetchall()
            return [dict(r) for r in rows]

    def reset_stuck_running(self) -> int:
        """Re-queue any jobs stuck in RUNNING state (e.g. after a crash)."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status=?, started_at=NULL WHERE status=?",
                (PENDING, RUNNING),
            )
            return cur.rowcount
