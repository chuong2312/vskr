import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from vskr.api import app, handlers, runtime
from vskr.api.limits import _ask_slots, _ask_attempts, _lock, check_ask_rate, enter_ask_slot, leave_ask_slot
from vskr.service import ModelBusyError, Service

from tests.asgi_client import ASGITestClient


class TestAskLimits(unittest.TestCase):
    def setUp(self):
        with _lock:
            _ask_attempts.clear()

    def test_ask_rejects_questions_longer_than_500_characters(self):
        with ASGITestClient(app) as client:
            response = client.post("/api/ask", json={"question": "x" * 501})
        self.assertEqual(response.status_code, 422)

    def test_public_ask_hides_unexpected_model_errors(self):
        service = SimpleNamespace(ask=mock.Mock(side_effect=TimeoutError("/srv/private/model-state")))
        with mock.patch.dict(runtime.state, {"service": service, "error": None}):
            with ASGITestClient(app) as client:
                response = client.post("/api/ask", json={"question": "Câu hỏi?"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Hiện chưa thể trả lời câu hỏi. Vui lòng thử lại sau.")
        self.assertNotIn("/srv/private", response.text)

    def test_ask_is_rate_limited_to_ten_per_minute_and_one_hundred_per_hour(self):
        now = 10_000.0
        for _ in range(10):
            check_ask_rate("198.51.100.20", now=now)
        with self.assertRaises(Exception) as raised:
            check_ask_rate("198.51.100.20", now=now)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.headers["Retry-After"], "60")

        with _lock:
            _ask_attempts.clear()
        now = 20_000.0
        for attempt in range(100):
            check_ask_rate("198.51.100.21", now=now + attempt * 6)
        with self.assertRaises(Exception) as raised:
            check_ask_rate("198.51.100.21", now=now + 600)
        self.assertEqual(raised.exception.status_code, 429)

    def test_only_three_concurrent_ask_slots_are_available(self):
        for _ in range(3):
            enter_ask_slot()
        try:
            with self.assertRaises(Exception) as raised:
                enter_ask_slot()
            self.assertEqual(raised.exception.status_code, 503)
        finally:
            for _ in range(3):
                leave_ask_slot()

    def test_gpu_lock_wait_expires_with_friendly_503_and_retry_after(self):
        data_dir = tempfile.TemporaryDirectory()
        old_service = runtime.state["service"]
        old_error = runtime.state["error"]
        old_timeout = runtime.GPU_LOCK_TIMEOUT_S
        service = object.__new__(Service)
        service.retriever = SimpleNamespace(search=lambda *_args, **_kwargs: [])
        service.cfg = {"llm": {}}
        service.meta = {}
        service.document_meta = {}
        runtime.state["service"] = service
        runtime.state["error"] = None
        runtime.GPU_LOCK_TIMEOUT_S = 0.02
        runtime._gpu_model_lock.acquire()
        try:
            with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": data_dir.name}):
                with ASGITestClient(app) as client:
                    started = time.monotonic()
                    response = client.post("/api/ask", json={"question": "Câu hỏi?", "mode": "bm25"})
                    elapsed = time.monotonic() - started
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["retry-after"], "10")
            self.assertEqual(response.json()["detail"], "Hệ thống đang cập nhật học liệu, thử lại sau ít phút")
            self.assertLess(elapsed, 1)
        finally:
            runtime._gpu_model_lock.release()
            runtime.GPU_LOCK_TIMEOUT_S = old_timeout
            runtime.state["service"] = old_service
            runtime.state["error"] = old_error
            data_dir.cleanup()
