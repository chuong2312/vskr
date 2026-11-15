import asyncio
import os
import secrets
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vskr import api
from vskr import accounts
from vskr.api import handlers
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
            with mock.patch.object(handlers._job_executor, "submit") as submit:
                with mock.patch("vskr.api.handlers.probe_duration", return_value=duration):
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
                    handlers._jobs.pop(response.json()["job_id"], None)
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

    def test_unauthenticated_upload_is_rejected_before_multipart_parsing(self):
        with mock.patch.dict(os.environ, {
            "VSKR__PATHS__DATA_DIR": str(self.tmp),
            "VSKR_ADMIN_USER": "",
            "VSKR_ADMIN_PASSWORD_HASH": "",
        }):
            with mock.patch("starlette.requests.Request.form", side_effect=AssertionError("parsed before auth")):
                with ASGITestClient(api.app, authenticated_learner=False) as client:
                    response = client.post(
                        "/api/ingest",
                        files={"file": ("lecture.mp4", b"payload", "video/mp4")},
                    )
        self.assertEqual(response.status_code, 401)

    def test_learner_url_ingest_blocks_redirect_hosts_but_admin_can_use_other_yt_dlp_sources(self):
        original_overrides = dict(api.app.dependency_overrides)
        submit = mock.AsyncMock(return_value={"job_id": "accepted-admin-url", "status": "queued"})
        headers = {"Origin": "http://localhost"}
        try:
            api.app.dependency_overrides[accounts.require_learner_or_admin] = lambda: {"user_id": "learner-a"}
            with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": str(self.tmp)}), \
                    mock.patch.object(handlers, "start_ingest", new=submit):
                with ASGITestClient(api.app, authenticated_learner=False) as client:
                    learner_response = client.post(
                        "/api/ingest", data={"url": "https://example.com/redirect"}, headers=headers,
                    )
                self.assertEqual(learner_response.status_code, 400)
                submit.assert_not_awaited()

                api.app.dependency_overrides[accounts.require_learner_or_admin] = lambda: {"admin": True}
                with ASGITestClient(api.app, authenticated_learner=False) as client:
                    admin_response = client.post(
                        "/api/ingest", data={"url": "https://example.com/video"}, headers=headers,
                    )
            self.assertEqual(admin_response.status_code, 202, admin_response.text)
            submit.assert_awaited_once_with(url="https://example.com/video", file=None, owner_id=None)
        finally:
            api.app.dependency_overrides.clear()
            api.app.dependency_overrides.update(original_overrides)

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
            with mock.patch.object(handlers._job_executor, "submit") as submit:
                response = asyncio.run(handlers.upload_document(FakeUpload()))
        self.assertEqual(response["status"], "queued")
        source = submit.call_args.args[2]
        self.assertEqual(source["kind"], "document")
        self.assertEqual(source["title"], "notes")
        self.assertTrue(Path(source["path"]).is_file())
        handlers._jobs.pop(response["job_id"], None)

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

        with mock.patch.dict(os.environ, env), mock.patch.object(handlers, "_rebuild_or_clear"):
            first = handlers.delete_video(video_id)
            second = handlers.delete_video(video_id)

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
            handlers, "_rebuild_or_clear", side_effect=RuntimeError("index failed")
        ):
            with self.assertRaises(api.HTTPException) as raised:
                handlers.delete_video(video_id)

        self.assertEqual(raised.exception.status_code, 500)
        self.assertTrue(marker.is_file())

    def test_delete_job_preserves_video_that_is_still_in_the_index(self):
        video_id = "indexed-video"
        target = self.tmp / "videos" / video_id
        target.mkdir(parents=True)
        marker = target / "source.mp4"
        marker.write_bytes(b"keep")
        index = self.tmp / "index"
        index.mkdir()
        (index / "chunks.json").write_text(
            '[{"video_id":"indexed-video","chunk_id":"indexed-video:0000"}]', encoding="utf-8"
        )
        job_id = "failed-indexed-job"
        handlers._jobs[job_id] = {
            "job_id": job_id, "status": "error", "video_id": video_id,
            "_source": {"kind": "url", "video_id": video_id},
        }
        try:
            with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": str(self.tmp)}):
                handlers.delete_job(job_id)
            self.assertEqual(marker.read_bytes(), b"keep")
            self.assertNotIn(job_id, handlers._jobs)
        finally:
            handlers._jobs.pop(job_id, None)

    def test_delete_job_removes_unindexed_partial_video(self):
        video_id = "partial-video"
        target = self.tmp / "videos" / video_id
        target.mkdir(parents=True)
        marker = target / "partial.bin"
        marker.write_bytes(b"discard")
        job_id = "failed-partial-job"
        handlers._jobs[job_id] = {
            "job_id": job_id, "status": "error", "video_id": video_id,
            "_source": {"kind": "url", "video_id": video_id},
        }
        try:
            with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": str(self.tmp)}):
                handlers.delete_job(job_id)
            self.assertFalse(target.exists())
        finally:
            handlers._jobs.pop(job_id, None)

    def test_delete_job_removes_failed_document_upload_but_keeps_indexed_document(self):
        document_id = "indexed-doc"
        document = self.tmp / "documents" / document_id
        document.mkdir(parents=True)
        marker = document / "meta.json"
        marker.write_text('{"document_id":"indexed-doc"}', encoding="utf-8")
        uploads = self.tmp / "documents" / ".uploads"
        uploads.mkdir()
        source_path = uploads / "notes.pdf"
        source_path.write_bytes(b"temp")
        index = self.tmp / "index"
        index.mkdir()
        (index / "chunks.json").write_text(
            '[{"document_id":"indexed-doc","chunk_id":"indexed-doc:0000"}]', encoding="utf-8"
        )
        job_id = "failed-indexed-document-job"
        handlers._jobs[job_id] = {
            "job_id": job_id, "status": "error", "document_id": document_id,
            "_source": {"kind": "document", "document_id": document_id, "path": str(source_path)},
        }
        try:
            with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": str(self.tmp)}):
                handlers.delete_job(job_id)
            self.assertTrue(marker.is_file())
            self.assertFalse(source_path.exists())
        finally:
            handlers._jobs.pop(job_id, None)

    def test_delete_job_never_unlinks_a_document_source_outside_upload_staging(self):
        outside_file = self.tmp / "keep.txt"
        outside_file.write_text("keep", encoding="utf-8")
        job_id = "failed-document-with-invalid-path"
        handlers._jobs[job_id] = {
            "job_id": job_id, "status": "error", "document_id": "unindexed-doc",
            "_source": {"kind": "document", "document_id": "unindexed-doc", "path": str(outside_file)},
        }
        try:
            with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": str(self.tmp)}):
                handlers.delete_job(job_id)
            self.assertEqual(outside_file.read_text(encoding="utf-8"), "keep")
        finally:
            handlers._jobs.pop(job_id, None)

    def test_delete_video_rejects_id_that_resolves_to_the_video_root(self):
        video_root = self.tmp / "videos"
        video_root.mkdir()
        marker = video_root / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": str(self.tmp)}):
            with self.assertRaises(api.HTTPException) as raised:
                handlers.delete_video(".")
        self.assertEqual(raised.exception.status_code, 404)
        self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main()
