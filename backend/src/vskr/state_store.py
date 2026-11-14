"""Small SQLite store for state that must survive an application restart."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import MutableMapping
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import data_dir, load_config

_schema_lock = threading.Lock()
_initialized_paths = set()


@contextmanager
def _connect():
    path = data_dir(load_config()) / "vskr.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    try:
        with _schema_lock:
            required_tables = {"jobs", "sessions", "visibility", "question_logs"}
            existing_tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if path not in _initialized_paths or not required_tables.issubset(existing_tables):
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        token_hash TEXT PRIMARY KEY,
                        created REAL NOT NULL,
                        last_seen REAL NOT NULL,
                        csrf TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS visibility (
                        kind TEXT NOT NULL CHECK(kind IN ('video', 'document')),
                        item_id TEXT NOT NULL,
                        is_public INTEGER NOT NULL CHECK(is_public IN (0, 1)),
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(kind, item_id)
                    );
                    CREATE TABLE IF NOT EXISTS question_logs (
                        request_id TEXT PRIMARY KEY,
                        question TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        found INTEGER,
                        feedback TEXT CHECK(feedback IN ('up', 'down') OR feedback IS NULL)
                    );
                    CREATE INDEX IF NOT EXISTS question_logs_created ON question_logs(created_at DESC);
                    """
                )
                _initialized_paths.add(path)
        with connection:
            yield connection
    finally:
        connection.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def save_job(job_id: str, job: dict) -> None:
    created_at = str(job.get("created_at") or _now())
    with _connect() as db:
        db.execute(
            "INSERT INTO jobs(job_id, payload, created_at) VALUES(?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload, created_at=excluded.created_at",
            (job_id, json.dumps(job, ensure_ascii=False), created_at),
        )


def load_job(job_id: str) -> dict | None:
    with _connect() as db:
        row = db.execute("SELECT payload FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return json.loads(row["payload"]) if row else None


def all_jobs() -> list[dict]:
    with _connect() as db:
        rows = db.execute("SELECT payload FROM jobs ORDER BY created_at, rowid").fetchall()
    return [json.loads(row["payload"]) for row in rows]


def update_job(job_id: str, changes: dict) -> dict | None:
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT payload FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        job = json.loads(row["payload"])
        job.update(changes)
        db.execute(
            "UPDATE jobs SET payload=?, created_at=? WHERE job_id=?",
            (json.dumps(job, ensure_ascii=False), str(job.get("created_at") or _now()), job_id),
        )
        return job


def delete_job(job_id: str) -> None:
    with _connect() as db:
        db.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))


def recover_interrupted_jobs() -> None:
    """Mark work interrupted by a process exit retryable instead of losing it."""
    with _connect() as db:
        rows = db.execute("SELECT job_id, payload FROM jobs").fetchall()
        for row in rows:
            job = json.loads(row["payload"])
            if job.get("status") not in {"queued", "running"}:
                continue
            job.update(
                status="error",
                phase="error",
                message="Tác vụ bị gián đoạn khi máy chủ khởi động lại. Có thể thử lại.",
                _error="InterruptedJob",
                _error_detail="The server stopped before this job completed.",
            )
            db.execute("UPDATE jobs SET payload=? WHERE job_id=?", (json.dumps(job, ensure_ascii=False), row["job_id"]))


class DurableJobs(MutableMapping):
    """Dictionary-shaped adapter retained for existing callers and test fixtures."""

    def __init__(self):
        self._items = {}

    def __getitem__(self, key):
        return self._items[key]

    def __setitem__(self, key, value):
        self._items[key] = dict(value)
        save_job(key, value)

    def __delitem__(self, key):
        del self._items[key]
        delete_job(key)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def clear(self):
        keys = list(self._items)
        self._items.clear()
        for key in keys:
            delete_job(key)

    def copy(self):
        return {key: dict(job) for key, job in self._items.items()}

    def update(self, other=(), /, **kwargs):
        values = dict(other, **kwargs)
        for key, value in values.items():
            self[key] = value

    def pop(self, key, default=None):
        if key not in self._items:
            return default
        value = self._items.pop(key)
        delete_job(key)
        return value

    def load_persisted(self):
        for job in all_jobs():
            if job.get("job_id"):
                self._items[job["job_id"]] = job


def save_session(token: str, session: dict) -> None:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as db:
        db.execute(
            "DELETE FROM sessions WHERE created < ? OR last_seen < ?",
            (session["created"] - 8 * 60 * 60, session["last_seen"] - 60 * 60),
        )
        db.execute(
            "INSERT OR REPLACE INTO sessions(token_hash, created, last_seen, csrf) VALUES(?, ?, ?, ?)",
            (token_hash, session["created"], session["last_seen"], session["csrf"]),
        )


def load_session(token: str) -> dict | None:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as db:
        row = db.execute("SELECT created, last_seen, csrf FROM sessions WHERE token_hash=?", (token_hash,)).fetchone()
    return dict(row) if row else None


def touch_session(token: str, last_seen: float) -> None:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as db:
        db.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (last_seen, token_hash))


def delete_session(token: str) -> None:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


def visibility(kind: str, item_id: str) -> bool:
    with _connect() as db:
        row = db.execute("SELECT is_public FROM visibility WHERE kind=? AND item_id=?", (kind, item_id)).fetchone()
    return True if row is None else bool(row["is_public"])


def set_visibility(kind: str, item_id: str, is_public: bool) -> None:
    if kind not in {"video", "document"}:
        raise ValueError("kind must be video or document")
    with _connect() as db:
        db.execute(
            "INSERT INTO visibility(kind, item_id, is_public, updated_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(kind, item_id) DO UPDATE SET is_public=excluded.is_public, updated_at=excluded.updated_at",
            (kind, item_id, int(is_public), _now()),
        )


def private_item_ids() -> dict[str, set[str]]:
    with _connect() as db:
        rows = db.execute("SELECT kind, item_id FROM visibility WHERE is_public=0").fetchall()
    result = {"video": set(), "document": set()}
    for row in rows:
        result[row["kind"]].add(row["item_id"])
    return result


def delete_visibility(kind: str, item_id: str) -> None:
    with _connect() as db:
        db.execute("DELETE FROM visibility WHERE kind=? AND item_id=?", (kind, item_id))


def log_question(question: str, request_id: str) -> None:
    with _connect() as db:
        db.execute(
            "INSERT INTO question_logs(request_id, question, created_at) VALUES(?, ?, ?)",
            (request_id, question[:500], _now()),
        )
        db.execute(
            "DELETE FROM question_logs WHERE request_id NOT IN "
            "(SELECT request_id FROM question_logs ORDER BY rowid DESC LIMIT 10000)"
        )


def finish_question(request_id: str, *, found: bool | None = None) -> None:
    with _connect() as db:
        db.execute("UPDATE question_logs SET found=? WHERE request_id=?", (None if found is None else int(found), request_id))


def submit_feedback(request_id: str, feedback: str) -> bool:
    if feedback not in {"up", "down"}:
        return False
    with _connect() as db:
        cursor = db.execute("UPDATE question_logs SET feedback=? WHERE request_id=?", (feedback, request_id))
        return cursor.rowcount > 0


def recent_questions(limit: int = 200) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with _connect() as db:
        rows = db.execute(
            "SELECT request_id, question, created_at, found, feedback FROM question_logs "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,),
        ).fetchall()
    return [
        {"request_id": row["request_id"], "question": row["question"], "created_at": row["created_at"],
         "found": None if row["found"] is None else bool(row["found"]), "feedback": row["feedback"]}
        for row in rows
    ]
