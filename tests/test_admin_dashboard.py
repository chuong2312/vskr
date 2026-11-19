import json
import os
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from vskr.access import auth
from vskr.api import app, handlers
from vskr.access.auth import hash_password

from tests.asgi_client import ASGITestClient

TEST_PASSWORD = secrets.token_urlsafe(24)
TEST_HASH = hash_password(TEST_PASSWORD)


class TestAdminDashboardApi(unittest.TestCase):
    def test_admin_api_client_does_not_replace_global_fetch(self):
        admin_bundle = Path(__file__).parents[1] / "web" / "admin" / "admin.js"
        source = admin_bundle.read_text(encoding="utf-8")
        self.assertIn("async function adminFetch", source)
        self.assertNotIn("window.fetch =", source)
        self.assertGreaterEqual(source.count("adminFetch("), 10)

    def test_library_deletion_actions_require_a_confirmation_prompt(self):
        admin_bundle = Path(__file__).parents[1] / "web" / "admin" / "admin.js"
        self.assertIn("window.confirm(`Xóa ${kindLabel}", admin_bundle.read_text(encoding="utf-8"))
        self.assertIn("window.confirm(\"Xóa tác vụ lỗi", admin_bundle.read_text(encoding="utf-8"))

    def test_dashboard_reads_current_and_legacy_index_metadata(self):
        service = type("ServiceStub", (), {})()
        service.meta = {}
        service.document_meta = {}
        service.index = type("IndexStub", (), {
            "info": {"n_chunks": 7, "created_at": "2026-11-15T00:00:00+00:00", "version": 1},
            "chunks": [{"video_id": "v1"}] * 7,
        })()
        cache = {"checked_at": time.monotonic(), "connected": True}
        with mock.patch.dict(handlers.state, {"service": service, "error": None}), \
                mock.patch.object(handlers, "status", return_value={"ready": True, "error": None}), \
                mock.patch.object(handlers, "_ollama_status_cache", cache):
            result = handlers.admin_status()
        self.assertEqual(result["index"], {
            "created_at": "2026-11-15T00:00:00+00:00", "version": 1, "chunk_count": 7,
        })

    def test_eval_results_are_marked_stale_when_questions_change_without_count_change(self):
        eval_dir = Path(self.data_dir.name) / "eval"
        eval_dir.mkdir()
        original_question = {
            "id": "q1", "question": "Câu cũ?", "type": "fact",
            "relevant": [{"video_id": "v1", "start": 1, "end": 2}],
        }
        result = {
            "question_count": 1,
            "question_fingerprint": handlers._questions_fingerprint([original_question]),
            "summary": {"hybrid": {"all": {"recall_at_5": 1.0}}},
        }
        (eval_dir / "results.json").write_text(json.dumps(result), encoding="utf-8")
        changed_question = {**original_question, "question": "Câu mới?"}
        (eval_dir / "questions.jsonl").write_text(json.dumps(changed_question) + "\n", encoding="utf-8")

        with mock.patch.dict(os.environ, self.env):
            stale = handlers.get_eval_results()
            (eval_dir / "questions.jsonl").write_text(json.dumps(original_question) + "\n", encoding="utf-8")
            current = handlers.get_eval_results()

        self.assertTrue(stale["stale"])
        self.assertFalse(current["stale"])

    def setUp(self):
        self.data_dir = tempfile.TemporaryDirectory()
        self.original_jobs = handlers._jobs.copy()
        handlers._jobs.clear()
        with auth._sessions_lock:
            auth._sessions.clear()
        self.env = {
            "VSKR__PATHS__DATA_DIR": self.data_dir.name,
            "VSKR_ADMIN_USER": "dashboard-admin",
            "VSKR_ADMIN_PASSWORD_HASH": TEST_HASH,
            "VSKR_COOKIE_SECURE": "false",
        }

    def tearDown(self):
        handlers._jobs.clear()
        handlers._jobs.update(self.original_jobs)
        self.data_dir.cleanup()

    def test_admin_job_list_is_bounded_and_does_not_expose_job_source_paths(self):
        handlers._jobs["job-safe"] = {
            "job_id": "job-safe", "status": "error", "phase": "error", "message": "internal",
            "created_at": "2026-11-14T00:00:00+00:00", "title": "Lecture",
            "_source": {"path": "/srv/secret/video.mp4"},
            "_error": "RuntimeError", "_error_detail": "Unable to open /srv/secret/video.mp4",
        }
        with mock.patch.dict(os.environ, self.env):
            with ASGITestClient(app) as client:
                login = client.post("/api/auth/login", json={
                    "username": "dashboard-admin", "password": TEST_PASSWORD,
                }, headers={"Origin": "http://localhost"})
                self.assertEqual(login.status_code, 200)
                result = client.get("/api/admin/jobs")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json()), 1)
        item = result.json()[0]
        self.assertEqual(item["job_id"], "job-safe")
        self.assertIn("Tác vụ thất bại", item["message"])
        self.assertNotIn("_source", item)
        self.assertNotIn("/srv/secret", result.text)
        self.assertEqual(item["error_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
