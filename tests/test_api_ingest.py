import asyncio
import os
import secrets
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vskr import api
from vskr.access import accounts
from vskr.api import handlers
from vskr.access import auth
from vskr.access.auth import hash_password
from vskr.config import load_config

from tests.asgi_client import ASGITestClient

TEST_PASSWORD = secrets.token_urlsafe(24)
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


class TestIngestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        with auth._failures_lock:
            auth._failures.clear()
        with accounts._registration_lock:
            accounts._registration_attempts.clear()
        with auth._sessions_lock:
            auth._sessions.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post_upload(self, content, env=None, duration=20, limits=None):
        env = {
            "VSKR__PATHS__DATA_DIR": str(self.tmp),
            "VSKR_COOKIE_SECURE": "false",
            **(env or {}),
        }
        with mock.patch.dict(os.environ, env):
            config = load_config()
            if limits:
                config["limits"]["user"].update(limits)
            with mock.patch.object(handlers, "load_config", return_value=config), \
                    mock.patch.object(handlers._job_executor, "submit") as submit:
                with mock.patch("vskr.api.handlers.probe_duration", return_value=duration):
                    with ASGITestClient(api.app, authenticated_learner=False) as client:
                        headers = {"Origin": "http://localhost"}
                        registration = client.post(
                            "/api/user/auth/register",
                            json={"username": "ingest-test", "password": TEST_PASSWORD},
                            headers=headers,
                        )
                        self.assertEqual(registration.status_code, 200, registration.text)
                        headers["X-CSRF-Token"] = registration.json()["csrf_token"]
                        project = client.post(
                            "/api/projects", json={"name": "Ingest test"}, headers=headers,
                        )
                        self.assertEqual(project.status_code, 200, project.text)
                        response = client.post(
                            "/api/ingest",
                            files={"file": ("lecture.mp4", content, "video/mp4")},
                            data={"project_id": project.json()["project_id"]},
                            headers=headers,
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
            b"0123456789", limits={"max_upload_bytes": 4}
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

    def test_guest_upload_is_allowed_with_an_explicit_project(self):
        with mock.patch.dict(os.environ, {
            "VSKR__PATHS__DATA_DIR": str(self.tmp),
            "VSKR_ADMIN_USER": "",
            "VSKR_ADMIN_PASSWORD_HASH": "",
            "VSKR_COOKIE_SECURE": "false",
        }), mock.patch.object(handlers, "probe_duration", return_value=20), \
                mock.patch.object(handlers._job_executor, "submit"):
            with ASGITestClient(api.app, authenticated_learner=False) as client:
                listing = client.get("/api/projects")
                self.assertEqual(listing.status_code, 200, listing.text)
                project = client.post("/api/projects", json={"name": "Guest upload"})
                self.assertEqual(project.status_code, 200, project.text)
                response = client.post(
                    "/api/ingest",
                    data={"project_id": project.json()["project_id"]},
                    files={"file": ("lecture.mp4", b"payload", "video/mp4")},
                )
        self.assertEqual(response.status_code, 202, response.text)

    def test_guest_url_ingest_blocks_non_youtube_and_admin_is_denied(self):
        submit = mock.AsyncMock(return_value={"job_id": "accepted-admin-url", "status": "queued"})
        headers = {"Origin": "http://localhost"}
        with mock.patch.dict(os.environ, {
            "VSKR__PATHS__DATA_DIR": str(self.tmp),
            "VSKR_ADMIN_USER": "test-admin",
            "VSKR_ADMIN_PASSWORD_HASH": TEST_PASSWORD_HASH,
            "VSKR_COOKIE_SECURE": "false",
            "VSKR_EGRESS_FIREWALL_CONFIRMED": "true",
        }), mock.patch.object(handlers, "start_ingest", new=submit), \
                mock.patch("vskr.api.public.load_config", return_value={
                    "ingest": {
                        "allowed_domains": ["youtube.com", "youtu.be", "youtube-nocookie.com"],
                        "guest_url_ingest_enabled": True,
                    },
                }):
            with ASGITestClient(api.app, authenticated_learner=False) as client:
                project = client.post("/api/projects", json={"name": "URL test"})
                self.assertEqual(project.status_code, 200, project.text)
                learner_response = client.post(
                    "/api/ingest",
                    data={"url": "https://example.com/redirect", "project_id": project.json()["project_id"]},
                    headers=headers,
                )
                self.assertEqual(learner_response.status_code, 400, learner_response.text)
                submit.assert_not_awaited()

            with ASGITestClient(api.app, authenticated_learner=False) as client:
                login = client.post(
                    "/api/auth/login",
                    json={"username": "test-admin", "password": TEST_PASSWORD},
                    headers=headers,
                )
                self.assertEqual(login.status_code, 200, login.text)
                admin_response = client.post(
                    "/api/ingest",
                    data={"url": "https://example.com/video", "project_id": "unused"},
                    headers={**headers, "X-CSRF-Token": login.json()["csrf_token"]},
                )
        self.assertEqual(admin_response.status_code, 403, admin_response.text)
        submit.assert_not_awaited()

    def test_guest_url_ingest_requires_firewall_confirmation_and_explicit_enable(self):
        self.assertFalse(load_config()["ingest"]["guest_url_ingest_enabled"])
        config = {
            "ingest": {
                "allowed_domains": ["youtube.com"],
                "guest_url_ingest_enabled": True,
            },
        }
        cases = (
            ("false", True, 403),
            ("true", False, 403),
            ("true", True, 202),
        )
        submit = mock.AsyncMock(return_value={"job_id": "guest-url", "status": "queued"})
        with mock.patch.object(handlers, "start_ingest", new=submit), \
                mock.patch("vskr.api.public.load_config", return_value=config), \
                mock.patch("vskr.api.public.validate_learner_ingest_url", side_effect=lambda url, **_kwargs: url):
            for firewall_confirmed, enabled, expected_status in cases:
                with self.subTest(firewall_confirmed=firewall_confirmed, enabled=enabled), \
                        tempfile.TemporaryDirectory() as temp_dir, \
                        mock.patch.dict(os.environ, {
                            "VSKR__PATHS__DATA_DIR": temp_dir,
                            "VSKR_COOKIE_SECURE": "false",
                            "VSKR_EGRESS_FIREWALL_CONFIRMED": firewall_confirmed,
                        }):
                    config["ingest"]["guest_url_ingest_enabled"] = enabled
                    with accounts._guest_identity_lock:
                        accounts._guest_identity_attempts.clear()
                    with ASGITestClient(api.app, authenticated_learner=False) as client:
                        project = client.post("/api/projects", json={"name": "URL test"})
                        response = client.post("/api/ingest", data={
                            "url": "https://youtube.com/watch?v=abc",
                            "project_id": project.json()["project_id"],
                        })
                    self.assertEqual(response.status_code, expected_status, response.text)

        self.assertEqual(submit.await_count, 1)

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
                response = asyncio.run(handlers.upload_document(
                    FakeUpload(), owner_id="learner-a", project_id="project-a",
                ))
        self.assertEqual(response["status"], "queued")
        source = submit.call_args.args[2]
        self.assertEqual(source["kind"], "document")
        self.assertEqual(source["title"], "notes")
        self.assertTrue(Path(source["path"]).is_file())
        handlers._jobs.pop(response["job_id"], None)

    def test_upload_over_duration_has_direct_duration_message(self):
        response, submit = self._post_upload(
            b"fake video bytes", duration=1801, limits={"max_duration_s": 1800}
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

    def test_quota_rejection_does_not_leave_uploaded_video_directory(self):
        class FakeUpload:
            filename = "lecture.mp4"

            def __init__(self):
                self.content = b"video"

            async def read(self, _size):
                data, self.content = self.content, b""
                return data

            async def close(self):
                pass

        with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": str(self.tmp)}), \
                mock.patch.object(handlers, "probe_duration", return_value=20), \
                mock.patch.object(accounts, "reserve_ingest_job", side_effect=api.HTTPException(429, "quota")):
            with self.assertRaises(api.HTTPException) as raised:
                asyncio.run(handlers.start_ingest(
                    url=None, file=FakeUpload(), owner_id="g:quota-reject", project_id="project",
                ))

        self.assertEqual(raised.exception.status_code, 429)
        video_root = self.tmp / "videos"
        self.assertEqual(list(video_root.iterdir()) if video_root.exists() else [], [])

    def test_concurrent_uploads_do_not_exceed_per_owner_active_job_limit(self):
        class FakeUpload:
            filename = "lecture.mp4"

            def __init__(self):
                self.content = b"video"

            async def read(self, _size):
                await asyncio.sleep(0)
                data, self.content = self.content, b""
                return data

            async def close(self):
                pass

        config = {
            "paths": {"data_dir": str(self.tmp)},
            "ingest": {"max_queue_length": 20, "max_upload_bytes": 1000},
            "limits": {"guest": {
                "max_active_jobs": 2, "max_jobs_per_day": 0,
                "max_upload_bytes": 1000, "max_duration_s": 900,
            }},
        }

        async def submit_requests():
            return await asyncio.gather(*[
                handlers.start_ingest(url=None, file=FakeUpload(), owner_id="g:concurrent", project_id="project")
                for _ in range(5)
            ], return_exceptions=True)

        with mock.patch.object(handlers, "load_config", return_value=config), \
                mock.patch.object(handlers, "probe_duration", return_value=20), \
                mock.patch.object(handlers._job_executor, "submit"):
            results = asyncio.run(submit_requests())

        accepted = [item for item in results if isinstance(item, dict)]
        rejected = [item for item in results if isinstance(item, api.HTTPException)]
        try:
            self.assertEqual(len(accepted), 2)
            self.assertEqual([item.status_code for item in rejected], [429, 429, 429])
            self.assertEqual(sum(
                job.get("owner_id") == "g:concurrent" and job.get("status") in {"queued", "running"}
                for job in handlers.list_jobs()
            ), 2)
        finally:
            for item in accepted:
                handlers._jobs.pop(item["job_id"], None)

    def test_document_upload_consumes_daily_job_quota(self):
        class FakeUpload:
            filename = "notes.md"

            def __init__(self):
                self.content = b"# Notes"

            async def read(self, _size):
                data, self.content = self.content, b""
                return data

            async def close(self):
                pass

        config = {
            "paths": {"data_dir": str(self.tmp)},
            "ingest": {"max_queue_length": 20},
            "limits": {"guest": {"max_active_jobs": 4, "max_jobs_per_day": 1}},
        }
        with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": str(self.tmp)}), \
                mock.patch.object(handlers, "load_config", return_value=config), \
                mock.patch.object(handlers._job_executor, "submit"):
            first = asyncio.run(handlers.upload_document(
                FakeUpload(), owner_id="g:document-quota", project_id="project",
            ))
            with self.assertRaises(api.HTTPException) as raised:
                asyncio.run(handlers.upload_document(
                    FakeUpload(), owner_id="g:document-quota", project_id="project",
                ))

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(len(list((self.tmp / "documents" / ".uploads").iterdir())), 1)
        handlers._jobs.pop(first["job_id"], None)


if __name__ == "__main__":
    unittest.main()
