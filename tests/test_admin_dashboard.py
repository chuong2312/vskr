import os
import secrets
import tempfile
import unittest
from unittest import mock

from vskr import auth
from vskr.api import app, handlers
from vskr.auth import hash_password

from tests.asgi_client import ASGITestClient

TEST_PASSWORD = secrets.token_urlsafe(24)
TEST_HASH = hash_password(TEST_PASSWORD)


class TestAdminDashboardApi(unittest.TestCase):
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
