"""ASGI test client that avoids the installed Starlette TestClient/AnyIO portal."""

import asyncio
from unittest import mock

import httpx


async def _inline_run_sync(call, *args, **kwargs):
    kwargs.pop("limiter", None)
    kwargs.pop("abandon_on_cancel", None)
    return call(*args, **kwargs)


class ASGITestClient:
    def __init__(self, app, base_url="http://localhost", authenticated_learner=True):
        self.app = app
        self.base_url = base_url
        self.loop = None
        self.client = None
        self._patches = []
        self.authenticated_learner = authenticated_learner
        self._previous_overrides = None

    def __enter__(self):
        if self.authenticated_learner:
            from vskr.accounts import require_learner_or_admin
            self._previous_overrides = dict(self.app.dependency_overrides)
            self.app.dependency_overrides[require_learner_or_admin] = lambda: {"admin": True, "csrf_token": "test-csrf"}
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._patches = [
            mock.patch("fastapi.routing.run_in_threadpool", new=_inline_run_sync),
            mock.patch("anyio.to_thread.run_sync", new=_inline_run_sync),
        ]
        for patcher in self._patches:
            patcher.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url=self.base_url,
            follow_redirects=False,
        )
        self.loop.run_until_complete(self.client.__aenter__())
        return self

    @property
    def cookies(self):
        return self.client.cookies

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.client is not None:
                self.loop.run_until_complete(self.client.__aexit__(exc_type, exc, tb))
        finally:
            if self._previous_overrides is not None:
                self.app.dependency_overrides.clear()
                self.app.dependency_overrides.update(self._previous_overrides)
            for patcher in reversed(self._patches):
                patcher.stop()
            self.loop.close()
            asyncio.set_event_loop(None)

    def get(self, *args, **kwargs):
        return self.loop.run_until_complete(self.client.get(*args, **kwargs))

    def post(self, *args, **kwargs):
        return self.loop.run_until_complete(self.client.post(*args, **kwargs))

    def delete(self, *args, **kwargs):
        return self.loop.run_until_complete(self.client.delete(*args, **kwargs))
