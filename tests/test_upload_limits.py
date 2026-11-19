import asyncio
import unittest
from unittest import mock

from vskr.api import UploadBodyLimitMiddleware
from vskr.config import load_config, role_limit


class TestUploadBodyLimitMiddleware(unittest.TestCase):
    def test_guest_ingest_body_cap_is_lower_than_learner_cap(self):
        cfg = {
            "ingest": {"max_upload_bytes": 1024 * 1024 * 1024},
            "limits": {"guest": {"max_upload_bytes": 100}, "user": {"max_upload_bytes": 1000}},
        }
        middleware = UploadBodyLimitMiddleware(lambda *_args: None)
        with mock.patch("vskr.config.load_config", return_value=cfg), \
                mock.patch("vskr.access.accounts.get_session", return_value=None):
            guest_scope = {"path": "/api/ingest", "headers": []}
            self.assertEqual(middleware._limit_for_scope(guest_scope), 100 + middleware.OVERHEAD_BYTES)
        with mock.patch("vskr.config.load_config", return_value=cfg), \
                mock.patch("vskr.access.accounts.get_session", return_value={"user_id": "u1"}):
            learner_scope = {"path": "/api/ingest", "headers": [(b"cookie", b"vskr_user=token")]}
            self.assertEqual(middleware._limit_for_scope(learner_scope), 1000 + middleware.OVERHEAD_BYTES)

    def _run(self, middleware, scope, receive):
        messages = []

        async def send(message):
            messages.append(message)

        asyncio.run(middleware(scope, receive, send))
        return messages

    def test_document_and_eval_import_caps_come_from_models_yaml(self):
        cfg = load_config()
        middleware = UploadBodyLimitMiddleware(lambda *_args: None)
        overhead = middleware.OVERHEAD_BYTES
        with mock.patch("vskr.access.accounts.get_session", return_value=None):
            documents = middleware._limit_for_scope({"path": "/api/documents", "headers": []})
            eval_import = middleware._limit_for_scope({"path": "/api/eval/questions/import", "headers": []})
        self.assertEqual(documents, cfg["limits"]["guest"]["max_document_bytes"] + overhead)
        self.assertEqual(eval_import, cfg["limits"]["eval_import_bytes"] + overhead)
        self.assertLess(cfg["limits"]["guest"]["max_document_bytes"], cfg["limits"]["user"]["max_document_bytes"])

    def test_role_limit_reads_the_shared_ingest_value_only_when_the_role_has_none(self):
        self.assertEqual(role_limit({"max_duration_s": 900}, {}, "max_duration_s"), 900)
        self.assertEqual(role_limit({}, {"max_duration_s": 7200}, "max_duration_s"), 7200)

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
