import asyncio
import os
import secrets
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vskr import api
from vskr.api import jobs as jobs_api
from vskr.api import library as library_api
from vskr.api import runtime
from vskr import auth
from vskr.auth import hash_password

from tests.asgi_client import ASGITestClient

TEST_PASSWORD = secrets.token_urlsafe(24)
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


class TestIngestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        with auth._failures_lock:
            auth._failures.clear()
        with auth._sessions_lock:
            auth._sessions.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post_upload(self, content, env=None, duration=20):
        env = {
            "VSKR__PATHS__DATA_DIR": str(self.tmp),
            "VSKR_ADMIN_USER": "test-admin",
            "VSKR_ADMIN_PASSWORD_HASH": TEST_PASSWORD_HASH,
            "VSKR_COOKIE_SECURE": "false",
            **(env or {}),
        }
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(runtime._job_executor, "submit") as submit:
                with mock.patch.object(jobs_api, "probe_duration", return_value=duration):
                    with ASGITestClient(api.app) as client:
                        login = client.post(
                            "/api/auth/login",
                            json={"username": "test-admin", "password": TEST_PASSWORD},
                            headers={"Origin": "http://localhost"},
                        )
                        self.assertEqual(login.status_code, 200)
                        response = client.post(
                            "/api/ingest",
                            files={"file": ("lecture.mp4", content, "video/mp4")},
                            headers={"Origin": "http://localhost", "X-CSRF-Token": login.json()["csrf_token"]},
                        )
                if response.status_code == 202:
                    runtime._jobs.pop(response.json()["job_id"], None)
                return response, submit

    def test_video_list_is_empty_before_first_index(self):
        with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": str(self.tmp)}):
            with ASGITestClient(api.app) as client:
                response = client.get("/api/videos")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_upload_queues_pipeline_job_and_saves_file(self):
        response, submit = self._post_upload(b"fake video bytes")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")
        source = submit.call_args.args[2]
        self.assertEqual(source["kind"], "upload")
        self.assertTrue(Path(source["path"]).is_file())

    def test_upload_rejects_file_over_configured_size(self):
        response, submit = self._post_upload(
            b"0123456789", env={"VSKR__INGEST__MAX_UPLOAD_BYTES": "4"}
        )
        self.assertEqual(response.status_code, 413)
        submit.assert_not_called()
        self.assertEqual(list((self.tmp / "videos").iterdir()), [])

    def test_upload_rejects_video_over_duration_limit(self):
        response, submit = self._post_upload(b"fake video bytes", duration=7201)
        self.assertEqual(response.status_code, 400)
        self.assertIn("giới hạn hiện tại là 2 giờ", response.json()["detail"])
        submit.assert_not_called()
        self.assertEqual(list((self.tmp / "videos").iterdir()), [])

    def test_document_upload_queues_processing_job(self):
        class FakeUpload:
            filename = "notes.md"

            def __init__(self):
                self.content = b"# Python\n\nBien va khai bao bien."

            async def read(self, _size):
                data, self.content = self.content, b""
                return data

            async def close(self):
                pass

        with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": str(self.tmp)}):
            with mock.patch.object(runtime._job_executor, "submit") as submit:
                response = asyncio.run(jobs_api.upload_document(FakeUpload()))
        self.assertEqual(response["status"], "queued")
        source = submit.call_args.args[2]
        self.assertEqual(source["kind"], "document")
        self.assertEqual(source["title"], "notes")
        self.assertTrue(Path(source["path"]).is_file())
        runtime._jobs.pop(response["job_id"], None)

    def test_upload_over_duration_has_direct_duration_message(self):
        response, submit = self._post_upload(
            b"fake video bytes", env={"VSKR__INGEST__MAX_DURATION_S": "1800"}, duration=1801
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Video dài 30.0 phút", response.json()["detail"])
        self.assertNotIn("Không đọc được video", response.json()["detail"])
        submit.assert_not_called()

    def test_delete_video_is_idempotent_for_stale_library_entry(self):
        video_id = "upload-stale"
        target = self.tmp / "videos" / video_id
        target.mkdir(parents=True)
        (target / "meta.json").write_text("{}", encoding="utf-8")
        env = {"VSKR__PATHS__DATA_DIR": str(self.tmp)}

        with mock.patch.dict(os.environ, env), mock.patch("vskr.api.library._rebuild_or_clear"):
            first = library_api.delete_video(video_id)
            second = library_api.delete_video(video_id)

        self.assertFalse(first["already_absent"])
        self.assertTrue(second["already_absent"])
        self.assertFalse(target.exists())

    def test_delete_video_restores_data_if_index_rebuild_fails(self):
        video_id = "upload-keep"
        target = self.tmp / "videos" / video_id
        target.mkdir(parents=True)
        marker = target / "meta.json"
        marker.write_text("{}", encoding="utf-8")
        env = {"VSKR__PATHS__DATA_DIR": str(self.tmp)}

        with mock.patch.dict(os.environ, env), mock.patch.object(
            library_api, "_rebuild_or_clear", side_effect=RuntimeError("index failed")
        ):
            with self.assertRaises(api.HTTPException) as raised:
                library_api.delete_video(video_id)

        self.assertEqual(raised.exception.status_code, 500)
        self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main()
