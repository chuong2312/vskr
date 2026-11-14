"""FastAPI application composed from explicit public and admin routers."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from ..auth import warn_if_unconfigured
from ..config import load_config
from .. import auth
from . import admin, public
from .runtime import init_service, recover_jobs


class _BodyTooLarge(Exception):
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    warn_if_unconfigured()
    recover_jobs()
    init_service()
    yield


app = FastAPI(
    title="VSKR",
    lifespan=lifespan,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)
app.include_router(public.router)
app.include_router(admin.router)


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

        path = scope.get("path", "")
        method = scope.get("method", "").upper()
        limit = None
        if method == "POST":
            if path == "/api/ingest":
                limit = int(load_config().get("ingest", {}).get("max_upload_bytes", 1_073_741_824)) + 1_048_576
            elif path == "/api/documents":
                limit = 100 * 1024 * 1024 + 1_048_576
            elif path == "/api/eval/questions/import":
                limit = 5 * 1024 * 1024 + 1_048_576
        body_receive = receive
        if limit is not None:
            try:
                # These routes use multipart forms. Authenticate before FastAPI can
                # parse or spool any part of an anonymous request body.
                auth.validate_admin_request(Request(scope, receive=receive))
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
                await response(scope, receive, send_with_headers)
                return
            try:
                raw_length = next((value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == b"content-length"), None)
                if raw_length is not None and int(raw_length) > limit:
                    response = JSONResponse({"detail": "Kích thước request vượt giới hạn cho phép."}, status_code=413)
                    await response(scope, receive, send_with_headers)
                    return
            except ValueError:
                response = JSONResponse({"detail": "Content-Length không hợp lệ."}, status_code=400)
                await response(scope, receive, send_with_headers)
                return
            received = 0

            async def receive_limited():
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > limit:
                        raise _BodyTooLarge
                return message

            body_receive = receive_limited
        response_started = False

        async def track_response(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send_with_headers(message)

        try:
            await self.app(scope, body_receive, track_response)
        except _BodyTooLarge:
            if not response_started:
                response = JSONResponse({"detail": "Kích thước request vượt giới hạn cho phép."}, status_code=413)
                await response(scope, receive, send_with_headers)


app.add_middleware(SecurityHeadersMiddleware)
