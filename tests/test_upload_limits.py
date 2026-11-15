import asyncio
import unittest

from vskr.api import UploadBodyLimitMiddleware


class TestUploadBodyLimitMiddleware(unittest.TestCase):
    def _run(self, middleware, scope, receive):
        messages = []

        async def send(message):
            messages.append(message)

        asyncio.run(middleware(scope, receive, send))
        return messages

    def test_rejects_declared_oversize_before_calling_application_or_reading_body(self):
        called = {"app": False, "receive": False}

        async def app(_scope, _receive, _send):
            called["app"] = True

        async def receive():
            called["receive"] = True
            return {"type": "http.request", "body": b"12345", "more_body": False}

        middleware = UploadBodyLimitMiddleware(app, limits={"/upload": 4})
        messages = self._run(middleware, {
            "type": "http", "method": "POST", "path": "/upload",
            "headers": [(b"content-length", b"5")],
        }, receive)

        self.assertFalse(called["app"])
        self.assertFalse(called["receive"])
        self.assertEqual(messages[0]["status"], 413)

    def test_enforces_limit_for_chunked_body_without_content_length(self):
        chunks = iter((b"abc", b"de"))

        async def receive():
            try:
                body = next(chunks)
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": body, "more_body": bool(chunks.__length_hint__())}

        async def app(_scope, limited_receive, _send):
            while True:
                message = await limited_receive()
                if not message.get("more_body"):
                    break

        middleware = UploadBodyLimitMiddleware(app, limits={"/upload": 4})
        messages = self._run(middleware, {
            "type": "http", "method": "POST", "path": "/upload", "headers": [],
        }, receive)

        self.assertEqual(messages[0]["status"], 413)


if __name__ == "__main__":
    unittest.main()
