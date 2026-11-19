"""Server-side admin sessions, CSRF protection, and login throttling."""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

COOKIE_NAME = "vskr_admin"
ABSOLUTE_TTL = 8 * 60 * 60
IDLE_TTL = 60 * 60
LOGIN_WINDOW = 15 * 60
LOGIN_FAILURES = 5
LOGIN_IP_FAILURES = 30
_sessions: dict[str, dict] = {}
_sessions_lock = threading.RLock()
_failures: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
_failures_lock = threading.Lock()


def _password_hasher():
    try:
        from argon2 import PasswordHasher, Type
    except ImportError:
        return None
    return PasswordHasher(type=Type.ID)


def admin_credentials():
    username = os.environ.get("VSKR_ADMIN_USER", "").strip()
    password_hash = os.environ.get("VSKR_ADMIN_PASSWORD_HASH", "").strip()
    if not username or not password_hash or not password_hash.startswith("$argon2id$"):
        return None
    hasher = _password_hasher()
    if hasher is None:
        return None
    from argon2 import extract_parameters
    from argon2.exceptions import InvalidHashError

    try:
        params = extract_parameters(password_hash)
    except (InvalidHashError, ValueError):
        return None
    from argon2 import Type

    if params.type is not Type.ID:
        return None
    return username, password_hash


def admin_is_configured() -> bool:
    return admin_credentials() is not None


def cookie_secure() -> bool:
    value = os.environ.get("VSKR_COOKIE_SECURE", "true").strip().lower()
    return value not in {"false", "0", "no", "off"}


def warn_if_unconfigured(host: str | None = None) -> None:
    if not admin_is_configured():
        logging.warning(
            "VSKR admin is disabled: set VSKR_ADMIN_USER and an argon2id "
            "VSKR_ADMIN_PASSWORD_HASH before enabling the admin app."
        )
    if host == "0.0.0.0" and not admin_is_configured():
        logging.warning("VSKR is bound to 0.0.0.0 with admin authentication unconfigured.")


def _is_local_host(value: str | None) -> bool:
    if not value:
        return False
    try:
        hostname = urlsplit("//" + value).hostname
    except ValueError:
        return False
    return bool(hostname and hostname.lower() in {"localhost", "127.0.0.1", "::1"})


def _secure_cookie_request(request: Request) -> bool:
    origin = request.headers.get("origin")
    try:
        parsed_origin = urlsplit(origin) if origin else None
        local_origin = bool(parsed_origin and _is_local_host(parsed_origin.netloc))
    except ValueError:
        return False
    local_host = _is_local_host(request.headers.get("host"))
    if cookie_secure():
        if parsed_origin is not None:
            return parsed_origin.scheme == "https" or local_origin
        return local_host or request.url.scheme == "https"
    return local_host and (not parsed_origin or local_origin)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    if not origin or not host:
        return False
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and parsed.username is None
            and parsed.password is None
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and hmac.compare_digest(parsed.netloc.lower(), host.lower())
        )
    except ValueError:
        return False


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    trusted_value = os.environ.get("VSKR_TRUSTED_PROXIES", "").strip()
    if not trusted_value:
        return peer
    try:
        peer_ip = ipaddress.ip_address(peer)
        trusted_ranges = [
            ipaddress.ip_network(item.strip(), strict=False)
            for item in trusted_value.split(",")
            if item.strip()
        ]
        if not trusted_ranges or not any(peer_ip in network for network in trusted_ranges):
            return peer
        forwarded = [item.strip() for item in request.headers.get("x-forwarded-for", "").split(",") if item.strip()]
        chain = [ipaddress.ip_address(item.split("%", 1)[0]) for item in forwarded]
        current = peer_ip
        for candidate in reversed(chain):
            if not any(current in network for network in trusted_ranges):
                break
            current = candidate
        return str(current)
    except ValueError:
        return peer


def _prune_failures(now: float) -> None:
    for key in list(_failures):
        attempts = _failures[key]
        while attempts and now - attempts[0] >= LOGIN_WINDOW:
            attempts.popleft()
        if not attempts:
            _failures.pop(key, None)
    while len(_failures) > 10_000:
        _failures.popitem(last=False)


def _login_keys(ip: str, username: str, scope: str = "admin"):
    normalized_username = username.strip().casefold()
    return ("pair", scope, f"{ip}\0{normalized_username}"), ("ip", scope, ip)


def _login_failure_limit(key: tuple[str, ...]) -> int:
    return LOGIN_IP_FAILURES if key[0] == "ip" else LOGIN_FAILURES


def login_retry_after(ip: str, username: str, now: float | None = None, *,
                      scope: str = "admin") -> int | None:
    now = time.time() if now is None else now
    with _failures_lock:
        _prune_failures(now)
        attempts = [
            (list(_failures.get(key, ())), _login_failure_limit(key))
            for key in _login_keys(ip, username, scope)
        ]
    retry = [values[0] + LOGIN_WINDOW - now for values, limit in attempts if len(values) >= limit]
    return max(1, int(max(retry) + 0.999)) if retry else None


def _record_login_failure(ip: str, username: str, now: float | None = None, *,
                          scope: str = "admin") -> None:
    now = time.time() if now is None else now
    with _failures_lock:
        _prune_failures(now)
        for key in _login_keys(ip, username, scope):
            _failures.setdefault(key, deque()).append(now)
            _failures.move_to_end(key)
        _prune_failures(now)


def _clear_login_failures(ip: str, username: str, *, scope: str = "admin") -> None:
    with _failures_lock:
        # A successful login clears that account's pair-specific lock. Keep the
        # IP-wide spray budget so repeated failures across usernames still count.
        pair_key = _login_keys(ip, username, scope)[0]
        _failures.pop(pair_key, None)


def verify_login(username: str, password: str) -> bool:
    credentials = admin_credentials()
    hasher = _password_hasher()
    if credentials is None or hasher is None:
        return False
    configured_user, password_hash = credentials
    from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

    try:
        password_ok = hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
        password_ok = False
    username_ok = hmac.compare_digest(username.encode("utf-8"), configured_user.encode("utf-8"))
    return bool(password_ok & username_ok)


def create_session() -> tuple[str, dict]:
    token = secrets.token_urlsafe(32)
    now = time.time()
    session = {"created": now, "last_seen": now, "csrf": secrets.token_urlsafe(32)}
    with _sessions_lock:
        _sessions[token] = session
    return token, dict(session)


def revoke_session(token: str | None) -> None:
    if token:
        with _sessions_lock:
            _sessions.pop(token, None)


def get_session(token: str | None, *, touch: bool = True, now: float | None = None):
    if not token:
        return None
    now = time.time() if now is None else now
    with _sessions_lock:
        session = _sessions.get(token)
        if session is None:
            return None
        if now - session["created"] >= ABSOLUTE_TTL or now - session["last_seen"] >= IDLE_TTL:
            _sessions.pop(token, None)
            return None
        if touch:
            session["last_seen"] = now
        return dict(session)


def validate_admin_request(request: Request):
    if not admin_is_configured():
        raise HTTPException(404, "Not found")
    if not _secure_cookie_request(request):
        raise HTTPException(403, "Secure cookie chỉ có thể tắt khi truy cập localhost.")
    token = request.cookies.get(COOKIE_NAME)
    session = get_session(token)
    if session is None:
        raise HTTPException(401, "Cần đăng nhập quản trị.")
    if request.method.upper() not in {"GET", "HEAD"}:
        csrf = request.headers.get("x-csrf-token", "")
        if not _same_origin(request) or not csrf or not hmac.compare_digest(csrf, session["csrf"]):
            raise HTTPException(403, "Yêu cầu CSRF không hợp lệ.")
    request.state.admin_session = session
    return session


async def require_admin(request: Request):
    return validate_admin_request(request)


def set_session_cookie(response, token: str, *, secure: bool | None = None) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=ABSOLUTE_TTL,
        httponly=True,
        secure=cookie_secure() if secure is None else secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=cookie_secure(), samesite="strict")


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Mật khẩu không được để trống.")
    if len(password) > 1024:
        raise ValueError("Mật khẩu quá dài.")
    hasher = _password_hasher()
    if hasher is None:
        raise RuntimeError("argon2-cffi chưa được cài; cài các dependency của VSKR trước.")
    return hasher.hash(password)


def login(request: Request, username: str, password: str):
    """Verify credentials and return (session token, CSRF token) on success."""
    if admin_credentials() is None:
        raise HTTPException(
            503,
            "Quản trị chưa được cấu hình. Hãy đặt VSKR_ADMIN_USER và VSKR_ADMIN_PASSWORD_HASH (argon2id).",
        )
    if not _secure_cookie_request(request):
        raise HTTPException(403, "Cookie Secure chỉ có thể tắt khi truy cập localhost.")
    ip = client_ip(request)
    retry_after = login_retry_after(ip, username)
    if retry_after is not None:
        raise HTTPException(429, "Quá nhiều lần đăng nhập thất bại.", headers={"Retry-After": str(retry_after)})
    old_token = request.cookies.get(COOKIE_NAME)
    valid = verify_login(username, password)
    if not valid:
        revoke_session(old_token)
        _record_login_failure(ip, username)
        return None
    _clear_login_failures(ip, username)
    revoke_session(old_token)
    return create_session()
