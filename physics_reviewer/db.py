import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from physics_reviewer.config import get_settings
from physics_reviewer.schemas import ReviewResponse, ReviewTaskStatusResponse


def _db_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise RuntimeError("Only sqlite:/// DATABASE_URL is supported by the local task backend.")
    return Path(url.removeprefix("sqlite:///"))


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_tasks (
                task_id TEXT PRIMARY KEY,
                batch_id TEXT,
                filename TEXT,
                title TEXT,
                status TEXT NOT NULL,
                error TEXT,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_review_tasks_batch ON review_tasks(batch_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_review_tasks_status ON review_tasks(status)")


def fail_incomplete_tasks() -> int:
    """Close tasks whose in-memory worker disappeared during a service restart."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE review_tasks
            SET status = 'failed',
                error = 'Task interrupted by a service restart; upload this PDF again.',
                updated_at = ?
            WHERE status IN ('queued', 'running')
            """,
            (_now(),),
        )
    return cursor.rowcount


def create_task(task_id: str, batch_id: str | None, filename: str | None, title: str | None) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO review_tasks (
                task_id, batch_id, filename, title, status, error, result_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'queued', NULL, NULL, ?, ?)
            """,
            (task_id, batch_id, filename, title, now, now),
        )


def mark_running(task_id: str) -> None:
    _update_task(task_id, status="running")


def mark_succeeded(task_id: str, result: ReviewResponse) -> None:
    _update_task(task_id, status="succeeded", result_json=result.model_dump_json())


def mark_failed(task_id: str, error: str) -> None:
    _update_task(task_id, status="failed", error=error)


def get_task(task_id: str) -> ReviewTaskStatusResponse | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM review_tasks WHERE task_id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def list_batch(batch_id: str) -> list[ReviewTaskStatusResponse]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM review_tasks WHERE batch_id = ? ORDER BY created_at ASC",
            (batch_id,),
        ).fetchall()
    return [_row_to_task(row) for row in rows]


def _update_task(
    task_id: str,
    *,
    status: str,
    error: str | None = None,
    result_json: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE review_tasks
            SET status = ?, error = ?, result_json = COALESCE(?, result_json), updated_at = ?
            WHERE task_id = ?
            """,
            (status, error, result_json, _now(), task_id),
        )


def _row_to_task(row: sqlite3.Row) -> ReviewTaskStatusResponse:
    result = None
    if row["result_json"]:
        result = ReviewResponse.model_validate(json.loads(row["result_json"]))
    return ReviewTaskStatusResponse(
        task_id=row["task_id"],
        batch_id=row["batch_id"],
        filename=row["filename"],
        title=row["title"],
        status=row["status"],
        error=row["error"],
        result=result,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
