"""ASGI test client that avoids the installed Starlette TestClient/AnyIO portal."""

import asyncio
from unittest import mock

import httpx


async def _inline_run_sync(call, *args, **kwargs):
    return call(*args, **kwargs)


class ASGITestClient:
    def __init__(self, app, base_url="http://localhost"):
        self.app = app
        self.base_url = base_url
        self.loop = None
        self.client = None
        self._patch = None

    def __enter__(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._patch = mock.patch("fastapi.routing.run_in_threadpool", new=_inline_run_sync)
        self._patch.start()
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
            if self._patch is not None:
                self._patch.stop()
            self.loop.close()
            asyncio.set_event_loop(None)

    def get(self, *args, **kwargs):
        return self.loop.run_until_complete(self.client.get(*args, **kwargs))

    def post(self, *args, **kwargs):
        return self.loop.run_until_complete(self.client.post(*args, **kwargs))

    def delete(self, *args, **kwargs):
        return self.loop.run_until_complete(self.client.delete(*args, **kwargs))
