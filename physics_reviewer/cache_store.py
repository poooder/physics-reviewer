import hashlib
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from physics_reviewer.config import get_settings


_LOCK_POOLS: dict[str, list[threading.Lock]] = {}
_LOCK_POOLS_GUARD = threading.Lock()
logger = logging.getLogger(__name__)


def cache_key(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def get_cached(namespace: str, key: str, max_age_seconds: int | None = None) -> Any | None:
    if not get_settings().cache_enabled or _database_path() is None:
        return None

    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT value_json, created_at FROM cache_entries WHERE namespace = ? AND cache_key = ?",
                (namespace, key),
            ).fetchone()
            if row is None:
                return None
            if max_age_seconds is not None and time.time() - row["created_at"] > max_age_seconds:
                conn.execute(
                    "DELETE FROM cache_entries WHERE namespace = ? AND cache_key = ?",
                    (namespace, key),
                )
                return None
    except sqlite3.Error:
        logger.warning("Cache read failed for namespace=%s", namespace, exc_info=True)
        return None

    try:
        return json.loads(row["value_json"])
    except (TypeError, json.JSONDecodeError):
        delete_cached(namespace, key)
        return None


def set_cached(namespace: str, key: str, value: Any) -> None:
    if not get_settings().cache_enabled or _database_path() is None:
        return

    try:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO cache_entries (namespace, cache_key, value_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, cache_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    created_at = excluded.created_at
                """,
                (namespace, key, payload, time.time()),
            )
    except (TypeError, ValueError, sqlite3.Error):
        logger.warning("Cache write failed for namespace=%s", namespace, exc_info=True)


def delete_cached(namespace: str, key: str) -> None:
    if not get_settings().cache_enabled or _database_path() is None:
        return
    try:
        with _connect() as conn:
            conn.execute(
                "DELETE FROM cache_entries WHERE namespace = ? AND cache_key = ?",
                (namespace, key),
            )
    except sqlite3.Error:
        logger.warning("Cache delete failed for namespace=%s", namespace, exc_info=True)


@contextmanager
def cache_lock(namespace: str, key: str) -> Iterator[None]:
    lock = _cache_lock_for(namespace, key)
    with lock:
        yield


def _cache_lock_for(namespace: str, key: str) -> threading.Lock:
    with _LOCK_POOLS_GUARD:
        pool = _LOCK_POOLS.get(namespace)
        if pool is None:
            pool = [threading.Lock() for _ in range(64)]
            _LOCK_POOLS[namespace] = pool
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return pool[int.from_bytes(digest[:2], "big") % len(pool)]


def _database_path() -> Path | None:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        return None
    return Path(url.removeprefix("sqlite:///"))


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _database_path()
    if path is None:
        raise RuntimeError("SQLite cache is unavailable for this DATABASE_URL.")
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cache_entries (
            namespace TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (namespace, cache_key)
        )
        """
    )
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
