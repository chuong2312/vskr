import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from vskr.api import app, handlers, limits
from vskr import accounts
from vskr.api.public import _safe_source_link
from vskr.bm25 import BM25, tokenize
from vskr.index import Index
from vskr.retriever import Retriever
from vskr.service import Service

from tests.asgi_client import ASGITestClient


class TestPublicCatalog(unittest.TestCase):
    def setUp(self):
        with accounts._guest_identity_lock:
            accounts._guest_identity_attempts.clear()
        with accounts._registration_lock:
            accounts._registration_attempts.clear()
        with limits._lock:
            limits._ask_attempts.clear()

    def test_ingest_job_carries_role_specific_limits(self):
        queued_source = {}
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": temp_dir}), \
                mock.patch.object(handlers, "validate_ingest_url"), \
                mock.patch.object(
                    handlers, "_queue_job",
                    side_effect=lambda source, _title, **_kwargs: queued_source.update(source) or "job",
                ) as queue_job:
            result = asyncio.run(handlers.start_ingest(
                url="https://example.com/video", file=None, owner_id="g:guest", project_id="project",
            ))
            handlers._release_job_slot(queue_job.call_args.kwargs["reservation_id"])
        self.assertEqual(result["job_id"], "job")
        self.assertEqual(queued_source["limits"]["max_duration_s"], 900)
        self.assertEqual(queued_source["limits"]["max_upload_bytes"], 100 * 1024 * 1024)

    def test_index_reload_can_bypass_initialization_backoff(self):
        fake_service = SimpleNamespace(meta={}, document_meta={})
        with mock.patch.dict(handlers.state, {"service": None, "failed_at": 1.0, "error": "old"}), \
                mock.patch("time.time", return_value=2.0), \
                mock.patch("vskr.api.handlers.load_config", return_value={}), \
                mock.patch("vskr.service.Service", return_value=fake_service) as service_ctor:
            handlers._init_service()
            self.assertIsNone(handlers.state["service"])
            handlers._init_service(force=True)
            self.assertEqual(service_ctor.call_count, 1)
            self.assertIs(handlers.state["service"], fake_service)

    def test_failed_index_reload_keeps_the_last_ready_service(self):
        previous = SimpleNamespace(meta={"ready": {}}, document_meta={})
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": temp_dir}), \
                mock.patch.dict(handlers.state, {"service": previous, "failed_at": 0.0, "error": None}), \
                mock.patch("vskr.service.Service", side_effect=RuntimeError("index unavailable")):
            handlers._init_service(force=True)
            self.assertIs(handlers.state["service"], previous)
            self.assertIn("index unavailable", handlers.state["error"])

    def test_approved_owned_content_is_visible_to_other_learners_and_guests(self):
        service = SimpleNamespace(
            meta={"public-video": {
                "video_id": "public-video", "title": "Đã duyệt", "owner_id": "u1",
                "visibility": "public", "url": "https://youtu.be/public",
            }},
            document_meta={},
            index=SimpleNamespace(chunks=[{"video_id": "public-video", "owner_id": "u1"}]),
        )
        with mock.patch.dict(handlers.state, {"service": service}):
            learner = handlers.public_videos(owner_id="u2")
            guest = handlers.public_videos(owner_id="g:guest", shared_only=True)
            self.assertTrue(handlers.source_is_visible("video", "public-video", "u2"))
        self.assertEqual([item["video_id"] for item in learner], ["public-video"])
        self.assertEqual([item["video_id"] for item in guest], ["public-video"])

    def test_project_filter_keeps_shared_material(self):
        service = SimpleNamespace(
            meta={
                "shared": {"video_id": "shared", "title": "Chung", "url": "https://youtu.be/shared"},
                "owned": {"video_id": "owned", "title": "Riêng", "owner_id": "u1", "project_id": "p1",
                          "url": "https://youtu.be/owned"},
            },
            index=SimpleNamespace(chunks=[{"video_id": "shared"}, {"video_id": "owned", "owner_id": "u1"}]),
        )
        with mock.patch.dict(handlers.state, {"service": service}):
            videos = handlers.public_videos(owner_id="u1", project_id="p1")
        self.assertEqual({item["video_id"] for item in videos}, {"shared", "owned"})

    def test_guest_can_poll_own_job_but_not_another_guests_job(self):
        job = {"job_id": "job-a", "owner_id": accounts.guest_owner_id("guest-a"), "status": "running",
               "phase": "download", "message": "Đang tải", "title": "Bài học"}
        with mock.patch.object(handlers, "get_job", return_value=job):
            with ASGITestClient(app, authenticated_learner=False) as first:
                first.cookies.set("vskr_guest", "guest-a")
                own = first.get("/api/jobs/job-a")
            with ASGITestClient(app, authenticated_learner=False) as second:
                second.cookies.set("vskr_guest", "guest-b")
                other = second.get("/api/jobs/job-a")
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.json()["job_id"], "job-a")
        self.assertEqual(other.status_code, 404, other.text)

    def test_guest_projects_are_isolated_and_catalog_accepts_project(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": temp_dir}):
            with ASGITestClient(app, authenticated_learner=False) as client:
                created = client.post("/api/projects", json={"name": "Guest project"})
                self.assertEqual(created.status_code, 200, created.text)
                project = created.json()
                listed = client.get("/api/projects")
                catalog = client.get(f"/api/catalog?project_id={project['project_id']}")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["project_id"], project["project_id"])
        self.assertEqual(catalog.status_code, 200)

    def test_get_projects_does_not_create_a_default_project(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.dict(os.environ, {
                    "VSKR__PATHS__DATA_DIR": temp_dir,
                    "VSKR_COOKIE_SECURE": "false",
                }):
            with ASGITestClient(app, authenticated_learner=False) as client:
                response = client.get("/api/projects")
                token = client.cookies.get(accounts.GUEST_COOKIE_NAME)

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), [])
            owner_id = accounts.guest_owner_id(token)
            self.assertEqual(accounts.list_projects(owner_id), [])
            with accounts._connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0)

    def test_clearing_guest_cookie_is_rate_limited_without_project_growth(self):
        with accounts._guest_identity_lock:
            accounts._guest_identity_attempts.clear()
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.dict(os.environ, {
                    "VSKR__PATHS__DATA_DIR": temp_dir,
                    "VSKR_COOKIE_SECURE": "false",
                }):
            with ASGITestClient(app, authenticated_learner=False) as client:
                responses = []
                for _ in range(accounts.GUEST_IDENTITY_LIMIT):
                    client.cookies.clear()
                    responses.append(client.get("/api/projects"))
                client.cookies.clear()
                limited = client.get("/api/projects")

            self.assertTrue(all(response.status_code == 200 for response in responses))
            self.assertEqual(limited.status_code, 429, limited.text)
            with accounts._connect() as db:
                project_count = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            self.assertEqual(project_count, 0)
        with accounts._guest_identity_lock:
            accounts._guest_identity_attempts.clear()

    def test_guest_cookie_rotation_cannot_bypass_project_creation_rate_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.dict(os.environ, {
                    "VSKR__PATHS__DATA_DIR": temp_dir,
                    "VSKR_COOKIE_SECURE": "false",
                }):
            with ASGITestClient(app, authenticated_learner=False) as client:
                responses = []
                for index in range(accounts.GUEST_IDENTITY_LIMIT + 2):
                    client.cookies.set(accounts.GUEST_COOKIE_NAME, f"attacker-chosen-{index}")
                    responses.append(client.post("/api/projects", json={"name": "Flood"}))

            self.assertEqual(
                [response.status_code for response in responses],
                [200] * accounts.GUEST_IDENTITY_LIMIT + [429, 429],
            )
            with accounts._connect() as db:
                project_count = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            self.assertEqual(project_count, accounts.GUEST_IDENTITY_LIMIT)

    def test_guest_token_is_not_exposed_in_project_or_job_responses(self):
        token = "guest-secret-token"
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.dict(os.environ, {
                    "VSKR__PATHS__DATA_DIR": temp_dir,
                    "VSKR_COOKIE_SECURE": "false",
                }), \
                mock.patch("vskr.api.public.validate_learner_ingest_url", side_effect=lambda url, **_kwargs: url), \
                mock.patch.object(handlers, "validate_ingest_url"), \
                mock.patch.object(handlers._job_executor, "submit"):
            with ASGITestClient(app, authenticated_learner=False) as client:
                client.cookies.set(accounts.GUEST_COOKIE_NAME, token)
                project_response = client.post("/api/projects", json={"name": "Guest"})
                self.assertEqual(project_response.status_code, 200, project_response.text)
                project = project_response.json()
                self.assertNotIn(token, project_response.text)
                self.assertNotIn("owner_id", project)

                queued = client.post("/api/ingest", data={
                    "url": "https://youtube.com/watch?v=example",
                    "project_id": project["project_id"],
                })
                self.assertEqual(queued.status_code, 202, queued.text)
                self.assertNotIn(token, queued.text)
                jobs = client.get("/api/my/jobs")
                self.assertEqual(jobs.status_code, 200, jobs.text)
                self.assertNotIn(token, jobs.text)
                job = handlers._jobs[queued.json()["job_id"]]
                self.assertNotIn(token, str(job["owner_id"]))
                self.assertNotIn("user-" + str(job["owner_id"])[:10], job["video_id"])
                handlers._jobs.pop(queued.json()["job_id"], None)

    def test_project_upload_requires_project_id(self):
        with self.assertRaises(Exception) as raised:
            asyncio.run(handlers.start_ingest(url="https://example.com/video", owner_id="g:test"))
        self.assertEqual(raised.exception.status_code, 422)
        with self.assertRaises(Exception) as raised:
            file = mock.Mock()
            file.close = mock.AsyncMock()
            asyncio.run(handlers.upload_document(file=file, owner_id="g:test"))
        self.assertEqual(raised.exception.status_code, 422)

    def test_projects_are_isolated_by_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": temp_dir}):
            first = accounts.create_project("g:first", "First")
            second = accounts.create_project("g:second", "Second")
            self.assertEqual(accounts.get_project("g:first", first["project_id"])["name"], "First")
            with self.assertRaises(Exception) as raised:
                accounts.get_project("g:second", first["project_id"])
        self.assertEqual(raised.exception.status_code, 404)
        self.assertNotEqual(first["project_id"], second["project_id"])

    def test_conversation_history_can_be_scoped_to_a_project(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": temp_dir}):
            with ASGITestClient(app, authenticated_learner=False) as client:
                registered = client.post("/api/user/auth/register", json={
                    "username": "project-history",
                    "password": "a-long-test-password",
                }, headers={"Origin": "http://localhost"})
                self.assertEqual(registered.status_code, 200, registered.text)
                headers = {
                    "Origin": "http://localhost",
                    "X-CSRF-Token": registered.json()["csrf_token"],
                }
                first_response = client.post("/api/projects", json={"name": "First"}, headers=headers)
                second_response = client.post("/api/projects", json={"name": "Second"}, headers=headers)
                self.assertEqual(first_response.status_code, 200, first_response.text)
                self.assertEqual(second_response.status_code, 200, second_response.text)
                first = first_response.json()
                second = second_response.json()
                for title, project in (("First", first), ("Second", second)):
                    created = client.post("/api/conversations", json={
                        "title": title,
                        "source_type": "library",
                        "project_id": project["project_id"],
                    }, headers=headers)
                    self.assertEqual(created.status_code, 200, created.text)
                scoped = client.get(f"/api/conversations?project_id={first['project_id']}")
                self.assertEqual(scoped.status_code, 200)
                self.assertEqual([item["title"] for item in scoped.json()], ["First"])

    def test_guest_catalog_contains_shared_but_not_account_owned_material(self):
        service = SimpleNamespace(
            meta={
                "shared-video": {"video_id": "shared-video", "title": "Bài chung", "url": "https://youtu.be/shared"},
                "private-video": {"video_id": "private-video", "title": "Bài riêng", "owner_id": "student-a", "url": "https://youtu.be/private"},
                "mixed-video": {"video_id": "mixed-video", "title": "Metadata thiếu chủ", "url": "https://youtu.be/mixed"},
            },
            index=SimpleNamespace(chunks=[
                {"video_id": "shared-video"}, {"video_id": "private-video", "owner_id": "student-a"},
                {"video_id": "mixed-video", "owner_id": "student-a"},
                {"document_id": "shared-doc"}, {"document_id": "private-doc", "owner_id": "student-a"},
                {"document_id": "mixed-doc", "owner_id": "student-a"},
            ]),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            document_root = Path(temp_dir) / "documents"
            for document_id, owner_id in (("shared-doc", None), ("private-doc", "student-a"), ("mixed-doc", None)):
                root = document_root / document_id
                root.mkdir(parents=True)
                metadata = {"document_id": document_id, "title": document_id}
                if owner_id:
                    metadata["owner_id"] = owner_id
                (root / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")
                (root / "sections.json").write_text(json.dumps({"chunks": [{"section": 1}]}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": temp_dir}), \
                    mock.patch.dict(handlers.state, {"service": service}):
                with ASGITestClient(app, authenticated_learner=False) as client:
                    response = client.get("/api/catalog")

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual([item["video_id"] for item in result["videos"]], ["shared-video"])
        self.assertEqual([item["document_id"] for item in result["documents"]], ["shared-doc"])

    def test_guest_catalog_enforces_shared_only_scope_at_route(self):
        with mock.patch("vskr.api.public.handlers.public_catalog", return_value={"videos": [], "documents": []}) as catalog:
            with ASGITestClient(app, authenticated_learner=False) as client:
                response = client.get("/api/catalog")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(catalog.call_args.kwargs["shared_only"])

    def test_guest_private_library_scope_is_empty(self):
        with ASGITestClient(app, authenticated_learner=False) as client:
            response = client.get("/api/catalog?scope=mine")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"videos": [], "documents": []})

    def test_video_catalog_includes_only_indexed_ready_metadata(self):
        service = SimpleNamespace(
            meta={
                "video-ready": {
                    "video_id": "video-ready", "title": "Bài giảng", "duration": 180,
                    "url": "https://lectures.example/watch?id=123&token=private", "source_type": "url",
                },
                "video-processing": {
                    "video_id": "video-processing", "title": "Đang xử lý", "url": "https://example.com/",
                },
            },
            index=SimpleNamespace(chunks=[{"video_id": "video-ready"}]),
        )
        with mock.patch.dict(handlers.state, {"service": service}):
            result = handlers.public_videos()
        self.assertEqual(result, [{
            "video_id": "video-ready", "title": "Bài giảng", "duration": 180,
            "playback_url": "https://lectures.example/watch",
            "project_id": None,
        }])

    def test_catalog_only_lists_documents_present_in_ready_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            documents = Path(temp_dir) / "documents"
            for document_id in ("doc-ready", "doc-processing"):
                root = documents / document_id
                root.mkdir(parents=True)
                (root / "meta.json").write_text(json.dumps({
                    "document_id": document_id, "title": document_id, "filename": f"{document_id}.pdf",
                }), encoding="utf-8")
                (root / "sections.json").write_text(json.dumps({"chunks": [{"section": 1}, {"section": 2}]}), encoding="utf-8")
            service = SimpleNamespace(
                meta={},
                index=SimpleNamespace(chunks=[{"document_id": "doc-ready"}]),
            )
            with mock.patch.dict(os.environ, {"VSKR__PATHS__DATA_DIR": temp_dir}), \
                    mock.patch.dict(handlers.state, {"service": service}):
                result = handlers.public_catalog()
        self.assertEqual(result, {
            "videos": [],
            "documents": [{"document_id": "doc-ready", "title": "doc-ready", "sections": 2,
                           "project_id": None}],
        })

    def test_public_ask_ignores_client_mode_and_sanitizes_sources(self):
        fake_result = {
            "answer": "Câu trả lời [1]", "found": True, "cited": True,
            "timing": {"llm_s": 5}, "matched_concepts": ["nội bộ"],
            "sources": [{
                "n": 1, "source_type": "video", "video_id": "vid", "title": "Bài giảng",
                "start": 25, "end": 40, "label": "00:25-00:40", "text": "Đoạn nguồn",
                "link": "https://lectures.example/watch?token=private", "score": 0.99,
                "chunk_id": "internal-id", "matched_concepts": ["private"],
            }],
        }
        with mock.patch("vskr.api.public.handlers.ask", return_value=fake_result) as ask:
            with ASGITestClient(app) as client:
                response = client.post("/api/ask", json={"question": "Hỏi bài", "mode": "graph"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(ask.call_args.args[0].mode)
        self.assertFalse(ask.call_args.kwargs["use_request_mode"])
        self.assertEqual(ask.call_args.kwargs["mode_override"], "hybrid")
        result = response.json()
        self.assertNotIn("timing", result)
        self.assertNotIn("matched_concepts", result)
        self.assertNotIn("chunk_id", result["sources"][0])
        self.assertNotIn("score", result["sources"][0])
        self.assertEqual(result["sources"][0]["link"], "https://lectures.example/watch")
        self.assertNotIn("token", response.text)

    def test_public_ask_passes_hybrid_mode_to_retriever(self):
        chunks = [{"chunk_id": "doc:1", "document_id": "doc", "section": 1, "text": "Bài học."}]
        index = Index(
            chunks, np.asarray([[1.0, 0.0]]),
            BM25([tokenize(chunks[0]["text"])]), {},
            {"version": 1, "nodes": [], "edges": []},
        )
        embedder = mock.Mock()
        embedder.encode_query.return_value = np.asarray([1.0, 0.0])
        service = object.__new__(Service)
        service.index = index
        service.meta = {}
        service.document_meta = {}
        service.cfg = {"retrieval": {"mode": "hybrid_graph", "top_k": 1}, "llm": {}}
        service.embedder = embedder
        service.retriever = Retriever(index, embedder, service.cfg["retrieval"])
        search = mock.Mock(wraps=service.retriever.search)
        service.retriever.search = search
        answer = {"answer": "Bài học.", "found": True, "cited": False,
                  "no_sources": False, "source_ns": []}

        with mock.patch.dict(handlers.state, {"service": service}), \
                mock.patch("vskr.service.answer_question", return_value=answer), \
                ASGITestClient(app, authenticated_learner=False) as client:
            response = client.post("/api/ask", json={"question": "Hỏi bài", "mode": "graph"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(search.call_args.kwargs["mode"], "hybrid")

    def test_guest_can_ask_without_a_session_and_does_not_create_history(self):
        fake_result = {"answer": "Dựa trên bài giảng [1].", "found": True, "cited": True, "sources": []}
        with mock.patch("vskr.api.public.handlers.ask", return_value=fake_result) as ask:
            with ASGITestClient(app, authenticated_learner=False) as client:
                page = client.get("/")
                answer = client.post("/api/ask", json={"question": "Hỏi khách"})
                history = client.get("/api/conversations")

        self.assertEqual(page.status_code, 200)
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertNotIn("conversation_id", answer.json())
        self.assertEqual(history.status_code, 401)
        self.assertTrue(ask.call_args.kwargs["shared_only"])
        self.assertTrue(ask.call_args.kwargs["owner_id"].startswith("g:"))

    def test_conversation_source_is_forwarded_as_an_index_scope(self):
        fake_result = {"answer": "A", "found": True, "cited": False, "sources": []}
        with mock.patch("vskr.api.public.handlers.source_is_visible", return_value=True), \
                mock.patch("vskr.api.public.handlers.ask", return_value=fake_result) as ask:
            with ASGITestClient(app) as client:
                response = client.post("/api/ask", json={
                    "question": "Hỏi video A", "source_id": "video-a", "source_type": "video",
                })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(ask.call_args.kwargs["allowed_sources"], {"video-a"})

    def test_private_guessed_source_is_not_queryable(self):
        with mock.patch("vskr.api.public.handlers.source_is_visible", return_value=False), \
                mock.patch("vskr.api.public.handlers.ask") as ask:
            with ASGITestClient(app) as client:
                response = client.post("/api/ask", json={
                    "question": "Đoán nguồn riêng", "source_id": "private-video-a", "source_type": "video",
                })
        self.assertEqual(response.status_code, 404)
        ask.assert_not_called()

    def test_guest_can_query_public_source_but_not_private_source(self):
        fake_result = {"answer": "A", "found": True, "cited": False, "sources": []}
        with mock.patch("vskr.api.public.handlers.source_is_visible", side_effect=lambda _type, source, *_args, **_kwargs: source == "public-video"), \
                mock.patch("vskr.api.public.handlers.ask", return_value=fake_result) as ask:
            with ASGITestClient(app, authenticated_learner=False) as client:
                public_response = client.post("/api/ask", json={
                    "question": "Hỏi nguồn chung", "source_id": "public-video", "source_type": "video",
                })
                private_response = client.post("/api/ask", json={
                    "question": "Hỏi nguồn riêng", "source_id": "private-video", "source_type": "video",
                })
        self.assertEqual(public_response.status_code, 200, public_response.text)
        self.assertEqual(private_response.status_code, 404)
        self.assertEqual(ask.call_args.kwargs["allowed_sources"], {"public-video"})

    def test_source_links_only_allow_safe_hosts_and_strip_query_tokens(self):
        self.assertEqual(
            _safe_source_link({"link": "https://youtube.com/watch?v=old", "video_id": "vid", "start": 12}),
            "https://www.youtube.com/watch?v=vid&t=12s",
        )
        self.assertIsNone(_safe_source_link({"link": "https://user:secret@example.com/watch", "video_id": "vid"}))


if __name__ == "__main__":
    unittest.main()
