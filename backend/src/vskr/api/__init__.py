"""FastAPI application composed from explicit public and admin routers."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..access.auth import warn_if_unconfigured
from . import admin, handlers, public


@asynccontextmanager
async def lifespan(app: FastAPI):
    warn_if_unconfigured()
    handlers._init_service()
    yield


app = FastAPI(
    title="VSKR",
    lifespan=lifespan,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)
app.include_router(public.router)
app.include_router(admin.auth_pages)
app.include_router(admin.router)


class UploadPayloadTooLarge(Exception):
    pass


class UploadBodyLimitMiddleware:
    """Bound multipart bodies before Starlette spools uploads to temporary files.

    Upload routes parse their forms manually after authentication, so unauthenticated
    callers are rejected before the receive wrapper is consumed.
    """

    OVERHEAD_BYTES = 1024 * 1024

    def __init__(self, app, *, limits=None):
        self.app = app
        self._test_limits = limits

    @staticmethod
    def _cookie(scope, name):
        from http.cookies import SimpleCookie

        cookie = SimpleCookie()
        cookie.load(dict((key.decode().lower(), value.decode()) for key, value in scope.get("headers", [])).get("cookie", ""))
        return cookie[name].value if name in cookie else None

    def _limit_for_scope(self, scope) -> int | None:
        path = scope.get("path", "")
        if self._test_limits is not None:
            return self._test_limits.get(path)
        from ..access import accounts
        from ..config import load_config

        cfg = load_config()
        limits = cfg.get("limits", {})
        learner = bool(self._cookie(scope, accounts.COOKIE_NAME)
                       and accounts.get_session(self._cookie(scope, accounts.COOKIE_NAME)))
        role = limits.get("user" if learner else "guest", {})
        overhead = UploadBodyLimitMiddleware.OVERHEAD_BYTES
        if path == "/api/ingest":
            default = cfg.get("ingest", {}).get("max_upload_bytes", 1_073_741_824)
            return int(role.get("max_upload_bytes", default)) + overhead
        if path == "/api/documents":
            return (100 if learner else 20) * 1024 * 1024 + overhead
        if path == "/api/eval/questions/import":
            return 5 * 1024 * 1024 + overhead
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for_scope(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
            except ValueError:
                response = JSONResponse({"detail": "Content-Length không hợp lệ."}, status_code=400)
                await response(scope, receive, send)
                return
            if declared_length < 0 or declared_length > limit:
                response = JSONResponse({"detail": "Request vượt giới hạn dung lượng cho phép."}, status_code=413)
                await response(scope, receive, send)
                return

        received = 0

        async def receive_with_limit():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise UploadPayloadTooLarge
            return message

        try:
            await self.app(scope, receive_with_limit, send)
        except UploadPayloadTooLarge:
            response = JSONResponse({"detail": "Request vượt giới hạn dung lượng cho phép."}, status_code=413)
            await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """Pure ASGI middleware keeps response headers local to the app stack."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        csp = (
            "default-src 'self'; "
            "script-src 'self' https://www.youtube.com https://s.ytimg.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://i.ytimg.com https://img.youtube.com; "
            "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
            "connect-src 'self' https://www.youtube.com https://youtubei.googleapis.com; "
            "media-src 'self' https://*.googlevideo.com; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; "
            "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'"
        )

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {key.lower() for key, _value in headers}
                additions = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"content-security-policy", csp.encode("ascii")),
                ]
                if path.startswith("/admin"):
                    additions.extend([
                        (b"x-robots-tag", b"noindex, nofollow"),
                        (b"cache-control", b"no-store"),
                    ])
                if path.startswith("/api/"):
                    additions.append((b"cache-control", b"no-store"))
                for key, value in additions:
                    if key not in existing:
                        headers.append((key, value))
                        existing.add(key)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(UploadBodyLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


def __getattr__(name):
    """Keep the historic ``vskr.api`` import path while implementation lives in handlers."""
    try:
        return getattr(handlers, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
