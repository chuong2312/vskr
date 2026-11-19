import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_ask_requests_can_retrieve_concurrently(self):
        active = 0
        max_active = 0
        lock = threading.Lock()
        both_started = threading.Barrier(2)

        def ask(*_args, **_kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                try:
                    both_started.wait(timeout=0.2)
                except threading.BrokenBarrierError:
                    pass
                return {"answer": "ok", "found": True, "cited": False, "sources": []}
            finally:
                with lock:
                    active -= 1

        service = SimpleNamespace(ask=ask)
        def run_ask():
            return handlers.ask(
                handlers.AskRequest(question="Hàm sigmoid là gì?"),
                mode_override="hybrid", use_request_mode=False,
            )

        with mock.patch.dict(handlers.state, {"service": service, "error": None}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _index: run_ask(), range(2)))

        self.assertEqual(len(results), 2)
        self.assertEqual(max_active, 2)

    def test_index_reload_keeps_active_service_alive_until_its_users_finish(self):
        old_service = SimpleNamespace(embedder=SimpleNamespace(close=mock.Mock()))
        new_service = SimpleNamespace(embedder=SimpleNamespace(close=mock.Mock()))
        with handlers._service_use_lock:
            handlers._active_service_users.clear()
        with mock.patch.dict(handlers.state, {"service": old_service, "error": None}), \
                mock.patch("vskr.service.Service", return_value=new_service), \
                mock.patch.object(handlers, "load_config", return_value={}):
            leased = handlers._acquire_service()
            handlers._init_service()
            old_service.embedder.close.assert_not_called()
            self.assertIs(handlers.state["service"], new_service)
            handlers._release_service(leased)
            old_service.embedder.close.assert_called_once_with()

    def test_gpu_lock_wait_expires_with_friendly_503_and_retry_after(self):
        data_dir = tempfile.TemporaryDirectory()
        old_service = handlers.state["service"]
        old_error = handlers.state["error"]
        old_timeout = handlers.GPU_LOCK_TIMEOUT_S
        service = object.__new__(Service)
        service.retriever = SimpleNamespace(search=lambda *_args, **_kwargs: [])
        service.index = SimpleNamespace(chunks=[])
        service.cfg = {"llm": {}}
        service.meta = {}
        service.document_meta = {}
        handlers.state["service"] = service
        handlers.state["error"] = None
        handlers.GPU_LOCK_TIMEOUT_S = 0.02
        handlers._gpu_model_lock.acquire()
        try:
            ip = "198.51.100.32"
            with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": data_dir.name}), \
                    mock.patch("vskr.api.public.auth.client_ip", return_value=ip):
                with ASGITestClient(app) as client:
                    started = time.monotonic()
                    response = client.post("/api/ask", json={"question": "Câu hỏi?", "mode": "bm25"})
                    elapsed = time.monotonic() - started
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["retry-after"], "10")
            self.assertEqual(response.json()["detail"], "Mô hình đang bận xử lý tác vụ khác. Vui lòng thử lại sau ít phút.")
            self.assertLess(elapsed, 1)
            with _lock:
                self.assertFalse(_ask_attempts.get(ip))
        finally:
            handlers._gpu_model_lock.release()
            handlers.GPU_LOCK_TIMEOUT_S = old_timeout
            handlers.state["service"] = old_service
            handlers.state["error"] = old_error
            data_dir.cleanup()
