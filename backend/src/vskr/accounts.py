"""SQLite-backed learner accounts and browser sessions."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException, Request

from . import auth
from .auth import _same_origin, _secure_cookie_request, hash_password
from .config import data_dir, index_dir, load_config
from .util import read_json, write_json

COOKIE_NAME = "vskr_user"
GUEST_COOKIE_NAME = "vskr_guest"
SESSION_TTL = 14 * 24 * 60 * 60
SESSION_IDLE_TTL = 7 * 24 * 60 * 60
REGISTRATION_WINDOW = 60 * 60
REGISTRATION_LIMIT = 5
GUEST_IDENTITY_WINDOW = 60 * 60
GUEST_IDENTITY_LIMIT = 10
PROJECTS_PER_OWNER_LIMIT = 20
_registration_attempts: OrderedDict[str, deque[float]] = OrderedDict()
_registration_lock = threading.Lock()
_guest_identity_attempts: OrderedDict[str, deque[float]] = OrderedDict()
_guest_identity_lock = threading.Lock()
_guest_owner_migration_lock = threading.Lock()
_guest_owner_migrated_roots: set[str] = set()


def _database_path() -> Path:
    return data_dir(load_config()) / "accounts.sqlite3"


@contextmanager
def _connect():
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS user_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            csrf_token TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            last_seen INTEGER NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            source_id TEXT,
            source_type TEXT NOT NULL DEFAULT 'library'
        )""")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(conversations)")}
        if "source_id" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN source_id TEXT")
        if "source_type" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN source_type TEXT NOT NULL DEFAULT 'library'")
        if "project_id" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN project_id TEXT")
        if "scope_type" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'library'")
        connection.execute("""UPDATE conversations
                              SET scope_type = CASE
                                WHEN source_id IS NULL OR source_type = 'library' THEN 'library'
                                ELSE 'source'
                              END
                              WHERE scope_type IS NULL OR scope_type = ''
                                 OR (source_id IS NOT NULL AND source_type != 'library'
                                     AND scope_type = 'library')""")
        connection.execute("""CREATE TABLE IF NOT EXISTS conversation_turns (
            turn_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            found INTEGER NOT NULL,
            cited INTEGER NOT NULL,
            sources_json TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS ingest_usage (
            usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at INTEGER NOT NULL
        )""")
        connection.commit()
        yield connection
        connection.commit()
    finally:
        connection.close()


def check_registration_rate(ip: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _registration_lock:
        attempts = _registration_attempts.setdefault(ip, deque())
        _registration_attempts.move_to_end(ip)
        while attempts and now - attempts[0] >= REGISTRATION_WINDOW:
            attempts.popleft()
        if len(attempts) >= REGISTRATION_LIMIT:
            retry_after = max(1, int(attempts[0] + REGISTRATION_WINDOW - now + 0.999))
            raise HTTPException(429, "Đã tạo quá nhiều yêu cầu đăng ký. Hãy thử lại sau.",
                                headers={"Retry-After": str(retry_after)})
        attempts.append(now)
        while len(_registration_attempts) > 10_000:
            _registration_attempts.popitem(last=False)


def check_guest_identity_rate(ip: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _guest_identity_lock:
        attempts = _guest_identity_attempts.setdefault(ip, deque())
        _guest_identity_attempts.move_to_end(ip)
        while attempts and now - attempts[0] >= GUEST_IDENTITY_WINDOW:
            attempts.popleft()
        if len(attempts) >= GUEST_IDENTITY_LIMIT:
            retry_after = max(1, int(attempts[0] + GUEST_IDENTITY_WINDOW - now + 0.999))
            raise HTTPException(429, "Đã tạo quá nhiều danh tính khách. Hãy thử lại sau.",
                                headers={"Retry-After": str(retry_after)})
        attempts.append(now)
        while len(_guest_identity_attempts) > 10_000:
            _guest_identity_attempts.popitem(last=False)


def reserve_ingest_job(owner_id: str, max_jobs_per_day: int, now: int | None = None) -> int:
    now = int(time.time()) if now is None else int(now)
    cutoff = now - 24 * 60 * 60
    with _connect() as db:
        db.execute("DELETE FROM ingest_usage WHERE created_at < ?", (cutoff,))
        count = db.execute(
            "SELECT COUNT(*) FROM ingest_usage WHERE owner_id = ? AND created_at >= ?",
            (owner_id, cutoff),
        ).fetchone()[0]
        if count >= max_jobs_per_day:
            raise HTTPException(429, "Bạn đã đạt giới hạn số tác vụ trong ngày.")
        cursor = db.execute(
            "INSERT INTO ingest_usage(owner_id, created_at) VALUES (?, ?)",
            (owner_id, now),
        )
        return int(cursor.lastrowid)


def release_ingest_job(owner_id: str, usage_id: int) -> bool:
    with _connect() as db:
        cursor = db.execute(
            "DELETE FROM ingest_usage WHERE usage_id = ? AND owner_id = ?",
            (usage_id, owner_id),
        )
    return bool(cursor.rowcount)


def register(username: str, password: str):
    username = username.strip()
    if "@" in username:
        if len(username) > 254 or username.count("@") != 1 or username.startswith("@"):
            raise HTTPException(422, "Email không hợp lệ.")
    elif not 3 <= len(username) <= 64 or not all(ch.isalnum() or ch in "._-" for ch in username):
        raise HTTPException(422, "Tên tài khoản không hợp lệ.")
    if len(password) < 8 or len(password) > 1024:
        raise HTTPException(422, "Mật khẩu cần ít nhất 8 ký tự.")
    user_id = uuid.uuid4().hex
    try:
        with _connect() as db:
            db.execute("INSERT INTO users(user_id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                       (user_id, username, hash_password(password), int(time.time())))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Tên tài khoản đã được dùng.") from exc
    return {"user_id": user_id, "username": username}


def create_session(username: str, password: str):
    with _connect() as db:
        row = db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)).fetchone()
        # Argon2 verification is performed for existing accounts; unknown names return the same response.
        valid = False
        if row is not None:
            from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
            from .auth import _password_hasher
            try:
                valid = _password_hasher().verify(row["password_hash"], password)
            except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
                valid = False
        if not row or not valid or row["disabled"]:
            raise HTTPException(401, "Sai tên đăng nhập hoặc mật khẩu.")
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = int(time.time())
        db.execute("INSERT INTO user_sessions VALUES (?, ?, ?, ?, ?)",
                   (_token_hash(token), row["user_id"], csrf, now, now))
        return token, {"user_id": row["user_id"], "username": row["username"], "csrf_token": csrf}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _guest_owner_key(root: Path) -> bytes:
    path = root / ".guest-owner-key"
    root.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        key = path.read_bytes()
    else:
        key = secrets.token_bytes(32)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
    if len(key) != 32:
        raise RuntimeError(f"Invalid guest owner key at {path}")
    return key


def _hashed_guest_owner(owner_id: str, key: bytes) -> str:
    if not owner_id.startswith("g:") or owner_id.startswith("g:h:"):
        return owner_id
    digest = hmac.new(key, owner_id[2:].encode("utf-8"), hashlib.sha256).hexdigest()
    return f"g:h:{digest}"


def _migrate_owner_fields(path: Path, key: bytes) -> None:
    if not path.is_file():
        return
    value = read_json(path)
    changed = False
    records = value if isinstance(value, list) else [value]
    for record in records:
        if not isinstance(record, dict):
            continue
        for field in ("owner", "owner_id"):
            owner_id = record.get(field)
            if isinstance(owner_id, str):
                replacement = _hashed_guest_owner(owner_id, key)
                if replacement != owner_id:
                    record[field] = replacement
                    changed = True
    if changed:
        write_json(path, value)


def migrate_legacy_guest_owners() -> None:
    root = data_dir(load_config())
    root_key = str(root.resolve())
    with _guest_owner_migration_lock:
        if root_key in _guest_owner_migrated_roots:
            return
        key = _guest_owner_key(root)
        with _connect() as db:
            applied = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 'guest-owner-hmac-v1'"
            ).fetchone()
        if applied:
            _guest_owner_migrated_roots.add(root_key)
            return

        for pattern in ("videos/*/meta.json", "documents/*/meta.json"):
            for path in root.glob(pattern):
                _migrate_owner_fields(path, key)
        _migrate_owner_fields(index_dir(load_config()) / "chunks.json", key)

        with _connect() as db:
            for table in ("projects", "ingest_usage"):
                owners = [row[0] for row in db.execute(f"SELECT DISTINCT owner_id FROM {table}")]
                for owner_id in owners:
                    replacement = _hashed_guest_owner(owner_id, key)
                    if replacement != owner_id:
                        db.execute(f"UPDATE {table} SET owner_id = ? WHERE owner_id = ?",
                                   (replacement, owner_id))
            db.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                ("guest-owner-hmac-v1", int(time.time())),
            )
        _guest_owner_migrated_roots.add(root_key)


def guest_owner_id(token: str) -> str:
    migrate_legacy_guest_owners()
    root = data_dir(load_config())
    digest = hmac.new(_guest_owner_key(root), token.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"g:h:{digest}"


def get_session(token: str | None, *, touch=True):
    if not token:
        return None
    now = int(time.time())
    with _connect() as db:
        row = db.execute("""SELECT s.*, u.username, u.disabled FROM user_sessions s
                          JOIN users u USING(user_id) WHERE s.token_hash = ?""", (_token_hash(token),)).fetchone()
        if not row:
            return None
        if (row["disabled"] or now - row["created_at"] >= SESSION_TTL
                or now - row["last_seen"] >= SESSION_IDLE_TTL):
            db.execute("DELETE FROM user_sessions WHERE token_hash = ?", (_token_hash(token),))
            return None
        if touch:
            db.execute("UPDATE user_sessions SET last_seen = ? WHERE token_hash = ?", (now, _token_hash(token)))
        return {"user_id": row["user_id"], "username": row["username"], "csrf_token": row["csrf_token"]}


def list_users():
    with _connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT user_id, username, created_at, disabled FROM users ORDER BY created_at DESC"
        )]


def _project_row(row):
    return {"project_id": row["project_id"], "name": row["name"],
            "created_at": row["created_at"]}


def list_projects(owner_id: str):
    with _connect() as db:
        rows = db.execute(
            "SELECT project_id, name, owner_id, created_at FROM projects "
            "WHERE owner_id = ? ORDER BY created_at, project_id", (owner_id,)
        ).fetchall()
    return [_project_row(row) for row in rows]


def create_project(owner_id: str, name: str):
    name = " ".join(str(name or "").split())
    if not name or len(name) > 120:
        raise HTTPException(422, "Tên project cần từ 1 đến 120 ký tự.")
    project_id = uuid.uuid4().hex
    created_at = int(time.time())
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("SELECT COUNT(*) FROM projects WHERE owner_id = ?", (owner_id,)).fetchone()[0]
        if count >= PROJECTS_PER_OWNER_LIMIT:
            raise HTTPException(429, "Bạn đã đạt giới hạn số project.")
        db.execute("INSERT INTO projects(project_id, owner_id, name, created_at) VALUES (?, ?, ?, ?)",
                   (project_id, owner_id, name, created_at))
    return {"project_id": project_id, "name": name, "created_at": created_at}


def get_project(owner_id: str, project_id: str | None):
    if not project_id:
        raise HTTPException(422, "project_id là bắt buộc.")
    with _connect() as db:
        row = db.execute(
            "SELECT project_id, name, owner_id, created_at FROM projects "
            "WHERE project_id = ? AND owner_id = ?", (project_id, owner_id)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Không tìm thấy project.")
    return _project_row(row)


def get_or_create_default_project(owner_id: str):
    with _connect() as db:
        row = db.execute(
            "SELECT project_id, name, owner_id, created_at FROM projects "
            "WHERE owner_id = ? ORDER BY created_at, project_id LIMIT 1", (owner_id,)
        ).fetchone()
        if row:
            return _project_row(row)
        project_id = uuid.uuid4().hex
        created_at = int(time.time())
        db.execute("INSERT INTO projects(project_id, owner_id, name, created_at) VALUES (?, ?, ?, ?)",
                   (project_id, owner_id, "Mặc định", created_at))
        return {"project_id": project_id, "name": "Mặc định", "created_at": created_at}


def set_user_disabled(user_id: str, disabled: bool):
    with _connect() as db:
        cursor = db.execute("UPDATE users SET disabled = ? WHERE user_id = ?", (int(disabled), user_id))
        if not cursor.rowcount:
            raise HTTPException(404, "Không tìm thấy tài khoản.")
        if disabled:
            db.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))


def _conversation_scope(source_id: str | None, source_type: str, scope_type: str | None = None):
    scope_type = scope_type or ("library" if source_type == "library" else "source")
    if scope_type not in {"source", "library"}:
        raise HTTPException(422, "Phạm vi cuộc trò chuyện không hợp lệ.")
    if scope_type == "library":
        return scope_type, None
    if not source_id or source_type == "library":
        raise HTTPException(422, "Hội thoại theo nguồn cần source_id.")
    return scope_type, source_id


def create_conversation(user_id: str, title: str, source_id: str | None, source_type: str,
                        project_id: str | None = None, scope_type: str | None = None):
    if source_type not in {"video", "document", "library"}:
        raise HTTPException(422, "Loại nguồn không hợp lệ.")
    scope_type, source_id = _conversation_scope(source_id, source_type, scope_type)
    now = int(time.time())
    conversation_id = uuid.uuid4().hex
    title = " ".join(title.split())[:72] or "Cuộc trò chuyện mới"
    with _connect() as db:
        db.execute("""INSERT INTO conversations
                   (conversation_id, user_id, title, created_at, updated_at, source_id, source_type, scope_type, project_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (conversation_id, user_id, title, now, now, source_id, source_type, scope_type, project_id))
    return {"conversation_id": conversation_id, "title": title, "source_id": source_id,
            "source_type": source_type, "scope_type": scope_type, "project_id": project_id}


def save_exchange(user_id: str, conversation_id: str | None, question: str, result: dict,
                  *, source_id: str | None = None, source_type: str = "library",
                  project_id: str | None = None, scope_type: str | None = None):
    now = int(time.time())
    scope_type, source_id = _conversation_scope(source_id, source_type, scope_type)
    with _connect() as db:
        if conversation_id:
            row = db.execute("SELECT source_id, source_type, scope_type, project_id FROM conversations WHERE conversation_id = ? AND user_id = ?",
                             (conversation_id, user_id)).fetchone()
            if not row:
                raise HTTPException(404, "Không tìm thấy cuộc trò chuyện.")
            if (row["source_id"] != source_id or row["source_type"] != source_type
                    or row["scope_type"] != scope_type):
                raise HTTPException(409, "Nguồn của cuộc trò chuyện không thể thay đổi.")
            project_id = row["project_id"]
        else:
            conversation_id = uuid.uuid4().hex
            title = " ".join(question.split())[:72] or "Cuộc trò chuyện mới"
            db.execute("""INSERT INTO conversations
                       (conversation_id, user_id, title, created_at, updated_at, source_id, source_type, scope_type, project_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (conversation_id, user_id, title, now, now, source_id, source_type, scope_type, project_id))
        db.execute("""INSERT INTO conversation_turns
                   (turn_id, conversation_id, question, answer, found, cited, sources_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                   (uuid.uuid4().hex, conversation_id, question, result["answer"],
                    int(bool(result.get("found"))), int(bool(result.get("cited"))),
                    json.dumps(result.get("sources", []), ensure_ascii=False), now))
        db.execute("UPDATE conversations SET updated_at = ? WHERE conversation_id = ?", (now, conversation_id))
    return conversation_id


def list_conversations(user_id: str, project_id: str | None = None):
    with _connect() as db:
        query = """SELECT c.conversation_id, c.title, c.created_at,
                   c.updated_at, c.source_id, c.source_type, c.scope_type, c.project_id, COUNT(t.turn_id) AS turn_count
                   FROM conversations c LEFT JOIN conversation_turns t USING(conversation_id)
                   WHERE c.user_id = ?"""
        params: list[str] = [user_id]
        if project_id:
            query += " AND c.project_id = ?"
            params.append(project_id)
        query += " GROUP BY c.conversation_id ORDER BY c.updated_at DESC"
        return [dict(row) for row in db.execute(query, params)]


def get_conversation(user_id: str, conversation_id: str):
    with _connect() as db:
        conversation = db.execute("""SELECT conversation_id, title, created_at, updated_at, source_id, source_type, scope_type, project_id
                                   FROM conversations
                                   WHERE conversation_id = ? AND user_id = ?""",
                                  (conversation_id, user_id)).fetchone()
        if not conversation:
            raise HTTPException(404, "Không tìm thấy cuộc trò chuyện.")
        turns = db.execute("""SELECT question, answer, found, cited, sources_json, created_at
                            FROM conversation_turns WHERE conversation_id = ? ORDER BY created_at, rowid""",
                           (conversation_id,)).fetchall()
        return {**dict(conversation), "source_deleted": False, "turns": [
            {"question": row["question"], "answer": row["answer"], "found": bool(row["found"]),
             "cited": bool(row["cited"]), "sources": json.loads(row["sources_json"]), "created_at": row["created_at"]}
            for row in turns
        ]}


def rename_conversation(user_id: str, conversation_id: str, title: str):
    title = " ".join(title.split())
    if not title:
        raise HTTPException(422, "Tên cuộc trò chuyện không được để trống.")
    if len(title) > 72:
        raise HTTPException(422, "Tên cuộc trò chuyện tối đa 72 ký tự.")
    with _connect() as db:
        cursor = db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE conversation_id = ? AND user_id = ?",
            (title, int(time.time()), conversation_id, user_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "Không tìm thấy cuộc trò chuyện.")
    return {"conversation_id": conversation_id, "title": title}


def delete_conversation(user_id: str, conversation_id: str):
    with _connect() as db:
        cursor = db.execute("DELETE FROM conversations WHERE conversation_id = ? AND user_id = ?",
                            (conversation_id, user_id))
        if not cursor.rowcount:
            raise HTTPException(404, "Không tìm thấy cuộc trò chuyện.")


def require_user(request: Request):
    if not _secure_cookie_request(request):
        raise HTTPException(403, "Cookie bảo mật chỉ có thể tắt khi truy cập localhost.")
    session = get_session(request.cookies.get(COOKIE_NAME))
    if not session:
        raise HTTPException(401, "Cần đăng nhập người học.")
    if request.method.upper() not in {"GET", "HEAD"}:
        csrf = request.headers.get("x-csrf-token", "")
        if not _same_origin(request) or not csrf or not hmac.compare_digest(csrf, session["csrf_token"]):
            raise HTTPException(403, "Yêu cầu CSRF không hợp lệ.")
    request.state.user = session
    return session


def require_learner_or_admin(request: Request):
    try:
        return require_user(request)
    except HTTPException as learner_error:
        # A few management API paths are shared by both apps; a valid admin session
        # may use them, while anonymous requests are still denied.
        try:
            return {"admin": True, **auth.validate_admin_request(request)}
        except HTTPException as admin_error:
            if admin_error.status_code in (401, 404):
                raise learner_error
            raise


def optional_learner_or_admin(request: Request):
    """Return a signed-in learner/admin or a shared-library-only guest."""
    user_token = request.cookies.get(COOKIE_NAME)
    if user_token and get_session(user_token) is not None:
        # Preserve CSRF and transport checks for valid account sessions.
        return require_user(request)

    admin_token = request.cookies.get(auth.COOKIE_NAME)
    if admin_token and auth.get_session(admin_token) is not None:
        try:
            return {"admin": True, **auth.validate_admin_request(request)}
        except HTTPException as exc:
            if exc.status_code not in (401, 404):
                raise

    guest = request.cookies.get(GUEST_COOKIE_NAME)
    if not guest:
        guest = secrets.token_urlsafe(32)
        request.state.new_guest_token = guest
    return {"guest": True, "user_id": None, "owner": guest_owner_id(guest), "guest_token": guest}


def require_learner_or_guest(request: Request):
    user = optional_learner_or_admin(request)
    if user.get("admin"):
        raise HTTPException(403, "Tài khoản quản trị không thể thêm nội dung.")
    return user


def revoke_session(token: str | None):
    if token:
        with _connect() as db:
            db.execute("DELETE FROM user_sessions WHERE token_hash = ?", (_token_hash(token),))


def set_cookie(response, token: str):
    from .auth import cookie_secure
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL, httponly=True,
                        secure=cookie_secure(), samesite="strict", path="/")


def set_guest_cookie(response, token: str):
    from .auth import cookie_secure
    response.set_cookie(GUEST_COOKIE_NAME, token, max_age=30 * 24 * 60 * 60,
                        httponly=True, secure=cookie_secure(), samesite="strict", path="/")


def clear_cookie(response):
    from .auth import cookie_secure
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True,
                           secure=cookie_secure(), samesite="lax")
