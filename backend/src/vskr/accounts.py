"""SQLite-backed learner accounts and browser sessions."""

from __future__ import annotations

import hashlib
import hmac
import json
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
from .config import data_dir, load_config

COOKIE_NAME = "vskr_user"
GUEST_COOKIE_NAME = "vskr_guest"
SESSION_TTL = 14 * 24 * 60 * 60
SESSION_IDLE_TTL = 7 * 24 * 60 * 60
REGISTRATION_WINDOW = 60 * 60
REGISTRATION_LIMIT = 5
_registration_attempts: OrderedDict[str, deque[float]] = OrderedDict()
_registration_lock = threading.Lock()


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


def set_user_disabled(user_id: str, disabled: bool):
    with _connect() as db:
        cursor = db.execute("UPDATE users SET disabled = ? WHERE user_id = ?", (int(disabled), user_id))
        if not cursor.rowcount:
            raise HTTPException(404, "Không tìm thấy tài khoản.")
        if disabled:
            db.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))


def create_conversation(user_id: str, title: str, source_id: str | None, source_type: str):
    if source_type not in {"video", "document", "library"}:
        raise HTTPException(422, "Loại nguồn không hợp lệ.")
    if source_type != "library" and not source_id:
        raise HTTPException(422, "Hội thoại cần có nguồn.")
    now = int(time.time())
    conversation_id = uuid.uuid4().hex
    title = " ".join(title.split())[:72] or "Cuộc trò chuyện mới"
    with _connect() as db:
        db.execute("""INSERT INTO conversations
                   (conversation_id, user_id, title, created_at, updated_at, source_id, source_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                   (conversation_id, user_id, title, now, now, source_id, source_type))
    return {"conversation_id": conversation_id, "title": title, "source_id": source_id, "source_type": source_type}


def save_exchange(user_id: str, conversation_id: str | None, question: str, result: dict,
                  *, source_id: str | None = None, source_type: str = "library"):
    now = int(time.time())
    with _connect() as db:
        if conversation_id:
            row = db.execute("SELECT source_id, source_type FROM conversations WHERE conversation_id = ? AND user_id = ?",
                             (conversation_id, user_id)).fetchone()
            if not row:
                raise HTTPException(404, "Không tìm thấy cuộc trò chuyện.")
            if row["source_id"] != source_id or row["source_type"] != source_type:
                raise HTTPException(409, "Nguồn của cuộc trò chuyện không thể thay đổi.")
        else:
            conversation_id = uuid.uuid4().hex
            title = " ".join(question.split())[:72] or "Cuộc trò chuyện mới"
            db.execute("""INSERT INTO conversations
                       (conversation_id, user_id, title, created_at, updated_at, source_id, source_type)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                       (conversation_id, user_id, title, now, now, source_id, source_type))
        db.execute("""INSERT INTO conversation_turns
                   (turn_id, conversation_id, question, answer, found, cited, sources_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                   (uuid.uuid4().hex, conversation_id, question, result["answer"],
                    int(bool(result.get("found"))), int(bool(result.get("cited"))),
                    json.dumps(result.get("sources", []), ensure_ascii=False), now))
        db.execute("UPDATE conversations SET updated_at = ? WHERE conversation_id = ?", (now, conversation_id))
    return conversation_id


def list_conversations(user_id: str):
    with _connect() as db:
        return [dict(row) for row in db.execute("""SELECT c.conversation_id, c.title, c.created_at,
                   c.updated_at, c.source_id, c.source_type, COUNT(t.turn_id) AS turn_count
                   FROM conversations c LEFT JOIN conversation_turns t USING(conversation_id)
                   WHERE c.user_id = ? GROUP BY c.conversation_id ORDER BY c.updated_at DESC""", (user_id,))]


def get_conversation(user_id: str, conversation_id: str):
    with _connect() as db:
        conversation = db.execute("""SELECT conversation_id, title, created_at, updated_at, source_id, source_type
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
    return {"guest": True, "user_id": None, "owner": f"g:{guest}", "guest_token": guest}


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
