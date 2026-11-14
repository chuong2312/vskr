"""FastAPI application composed from explicit public and admin routers."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..auth import warn_if_unconfigured
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

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeadersMiddleware)


def __getattr__(name):
    """Keep the historic ``vskr.api`` import path while implementation lives in handlers."""
    try:
        return getattr(handlers, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
