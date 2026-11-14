import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from vskr.api import app, handlers
from vskr.api.limits import _ask_slots, _ask_attempts, _lock, check_ask_rate, enter_ask_slot, leave_ask_slot
from vskr.service import ModelBusyError, Service

from tests.asgi_client import ASGITestClient


class TestAskLimits(unittest.TestCase):
    def setUp(self):
        with _lock:
            _ask_attempts.clear()

    def test_contextless_followups_are_rejected_without_calling_the_answer_service(self):
        service = SimpleNamespace(ask=mock.Mock())
        questions = (
            "cái hàm hồi nãy là sao",
            "cho mình mốc thời gian của ý trên",
            "ý vừa rồi nghĩa là gì",
            "cái đó là gì",
        )
        with mock.patch.dict(handlers.state, {"service": service, "error": None}):
            with ASGITestClient(app) as client:
                for question in questions:
                    with self.subTest(question=question):
                        response = client.post("/api/ask", json={"question": question})
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json(), {
                            "answer": "Mình chưa thấy câu hỏi trước nên chưa biết bạn đang nhắc đến phần nào. Bạn hãy nêu rõ tên khái niệm hoặc nội dung cần hỏi, ví dụ: «Hàm sigmoid là gì?».",
                            "found": False, "cited": False, "sources": [],
                        })
        service.ask.assert_not_called()

    def test_specific_and_regular_questions_still_reach_the_answer_service(self):
        service = SimpleNamespace(ask=mock.Mock(return_value={
            "answer": "Trả lời [1]", "found": True, "cited": True, "sources": [],
        }))
        questions = (
            "Hồi nãy thầy nói gì về LASSO?",
            "Tại sao tăng lambda thì bị phạt nặng hơn?",
            "Regularization là gì?",
            "LASSO khác Ridge ở điểm nào?",
        )
        with mock.patch.dict(handlers.state, {"service": service, "error": None}):
            with ASGITestClient(app) as client:
                for question in questions:
                    with self.subTest(question=question):
                        response = client.post("/api/ask", json={"question": question})
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()["answer"], "Trả lời [1]")
        self.assertEqual(service.ask.call_count, len(questions))

    def test_public_ask_returns_vietnamese_string_errors_for_invalid_questions(self):
        bad_questions = (
            ("", "Câu hỏi không được để trống."),
            ("   ", "Câu hỏi không được để trống."),
            ("x" * 501, "Câu hỏi tối đa 500 ký tự."),
        )
        with ASGITestClient(app) as client:
            for question, detail in bad_questions:
                with self.subTest(length=len(question)):
                    response = client.post("/api/ask", json={"question": question})
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json()["detail"], detail)
                    self.assertIsInstance(response.json()["detail"], str)

    def test_public_ask_accepts_exactly_500_characters(self):
        service = SimpleNamespace(ask=mock.Mock(return_value={
            "answer": "Trả lời [1]", "found": True, "cited": True, "sources": [],
        }))
        with mock.patch.dict(handlers.state, {"service": service, "error": None}):
            with ASGITestClient(app) as client:
                response = client.post("/api/ask", json={"question": "x" * 500})
        self.assertEqual(response.status_code, 200)
        service.ask.assert_called_once()

    def test_busy_ask_slot_does_not_consume_rate_limit_attempt(self):
        ip = "198.51.100.31"
        for _ in range(3):
            enter_ask_slot()
        try:
            with mock.patch("vskr.api.public.auth.client_ip", return_value=ip):
                with ASGITestClient(app) as client:
                    response = client.post("/api/ask", json={"question": "Câu hỏi?"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["retry-after"], "10")
            self.assertEqual(response.json()["detail"], "Hệ thống đang xử lý nhiều câu hỏi cùng lúc, bạn thử lại sau vài giây.")
            with _lock:
                self.assertFalse(_ask_attempts.get(ip))
        finally:
            for _ in range(3):
                leave_ask_slot()

    def test_ask_rejects_questions_longer_than_500_characters(self):
        with ASGITestClient(app) as client:
            response = client.post("/api/ask", json={"question": "x" * 501})
        self.assertEqual(response.status_code, 422)

    def test_public_ask_hides_unexpected_model_errors(self):
        service = SimpleNamespace(ask=mock.Mock(side_effect=TimeoutError("/srv/private/model-state")))
        with mock.patch.dict(handlers.state, {"service": service, "error": None}):
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
        old_service = handlers.state["service"]
        old_error = handlers.state["error"]
        old_timeout = handlers.GPU_LOCK_TIMEOUT_S
        service = object.__new__(Service)
        service.retriever = SimpleNamespace(search=lambda *_args, **_kwargs: [])
        service.cfg = {"llm": {}}
        service.meta = {}
        service.document_meta = {}
        handlers.state["service"] = service
        handlers.state["error"] = None
        handlers.GPU_LOCK_TIMEOUT_S = 0.02
        handlers._gpu_model_lock.acquire()
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
            handlers._gpu_model_lock.release()
            handlers.GPU_LOCK_TIMEOUT_S = old_timeout
            handlers.state["service"] = old_service
            handlers.state["error"] = old_error
            data_dir.cleanup()
