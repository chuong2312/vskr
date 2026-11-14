import asyncio
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vskr import auth
from vskr.api import app, handlers, jobs as jobs_api, library as library_api, runtime
from vskr import state_store
from vskr.auth import hash_password

from tests.asgi_client import ASGITestClient


class TestHardening(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = {"VSKR__PATHS__DATA_DIR": self.temp.name}
        self.password_hash = hash_password("test-password")
        with auth._failures_lock:
            auth._failures.clear()

    def tearDown(self):
        self.temp.cleanup()

    def test_upload_authentication_happens_before_body_is_read(self):
        env = {
            **self.env,
            "VSKR_ADMIN_USER": "admin",
            "VSKR_ADMIN_PASSWORD_HASH": self.password_hash,
            "VSKR_COOKIE_SECURE": "false",
        }
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/api/ingest",
            "raw_path": b"/api/ingest", "query_string": b"",
            "headers": [(b"host", b"localhost"), (b"content-length", b"1000000"),
                        (b"content-type", b"multipart/form-data; boundary=x")],
            "client": ("127.0.0.1", 1234), "server": ("localhost", 80),
        }
        body_reads = 0
        sent = []

        async def receive():
            nonlocal body_reads
            body_reads += 1
            return {"type": "http.request", "body": b"x" * 100, "more_body": False}

        async def send(message):
            sent.append(message)

        with mock.patch.dict(os.environ, env):
            asyncio.run(app(scope, receive, send))

        response_start = next(message for message in sent if message["type"] == "http.response.start")
        self.assertEqual(response_start["status"], 401)
        self.assertEqual(body_reads, 0)

    def test_authenticated_upload_with_oversized_content_length_is_rejected_before_read(self):
        env = {
            **self.env,
            "VSKR_ADMIN_USER": "admin",
            "VSKR_ADMIN_PASSWORD_HASH": self.password_hash,
            "VSKR_COOKIE_SECURE": "false",
        }
        with mock.patch.dict(os.environ, env):
            token, session = auth.create_session()
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/api/documents",
            "raw_path": b"/api/documents", "query_string": b"",
            "headers": [
                (b"host", b"localhost"),
                (b"content-length", str(101 * 1024 * 1024 + 1).encode()),
                (b"content-type", b"multipart/form-data; boundary=x"),
                (b"cookie", f"{auth.COOKIE_NAME}={token}".encode()),
                (b"origin", b"http://localhost"),
                (b"x-csrf-token", session["csrf"].encode()),
            ],
            "client": ("127.0.0.1", 1234), "server": ("localhost", 80),
        }
        body_reads = 0
        sent = []

        async def receive():
            nonlocal body_reads
            body_reads += 1
            return {"type": "http.request", "body": b"x", "more_body": False}

        async def send(message):
            sent.append(message)

        with mock.patch.dict(os.environ, env):
            asyncio.run(app(scope, receive, send))

        response_start = next(message for message in sent if message["type"] == "http.response.start")
        self.assertEqual(response_start["status"], 413)
        self.assertEqual(body_reads, 0)

    def test_delete_failed_job_keeps_existing_video_content(self):
        video_id = "same-url-video"
        video_dir = Path(self.temp.name) / "videos" / video_id
        video_dir.mkdir(parents=True)
        sentinel = video_dir / "meta.json"
        sentinel.write_text(json.dumps({"video_id": video_id}), encoding="utf-8")
        job_id = "failed-reingest"
        old_jobs = runtime._jobs.copy()
        runtime._jobs.clear()
        runtime._jobs[job_id] = {
            "job_id": job_id, "status": "error", "video_id": video_id,
            "_source": {"kind": "url", "url": "https://example.org/video"},
        }
        try:
            with mock.patch.dict(os.environ, self.env):
                result = library_api.delete_job(job_id)
            self.assertEqual(result, {"deleted": job_id})
            self.assertTrue(sentinel.is_file())
        finally:
            runtime._jobs.clear()
            runtime._jobs.update(old_jobs)

    def test_admin_home_redirects_to_login_when_session_is_missing(self):
        env = {
            **self.env,
            "VSKR_ADMIN_USER": "admin",
            "VSKR_ADMIN_PASSWORD_HASH": self.password_hash,
            "VSKR_COOKIE_SECURE": "false",
        }
        with mock.patch.dict(os.environ, env):
            with ASGITestClient(app) as client:
                response = client.get("/admin", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/login")

    def test_index_info_includes_dashboard_metadata(self):
        import numpy as np

        from vskr import index

        cfg = {
            "paths": {"data_dir": str(Path(self.temp.name))},
            "chunk": {"window_s": 10, "overlap_s": 1, "min_chars": 1},
            "embedding": {"model": "test", "query_prefix": "", "passage_prefix": "", "max_seq_length": 8},
            "knowledge_graph": {"extract_relations": False},
        }
        videos = Path(self.temp.name) / "videos" / "vid"
        videos.mkdir(parents=True)
        (videos / "meta.json").write_text(json.dumps({"video_id": "vid"}), encoding="utf-8")
        (videos / "transcript.json").write_text(json.dumps({"segments": [{"start": 0, "end": 2, "text": "A sufficiently long test segment."}]}), encoding="utf-8")

        class FakeEmbedder:
            def encode_passages(self, _texts):
                return np.ones((1, 2), dtype=np.float32)

        with mock.patch.object(index, "build_graph", return_value={"version": 1, "nodes": [], "edges": []}):
            result = index.build(cfg, FakeEmbedder())
        self.assertEqual(result["chunk_count"], 1)
        self.assertTrue(result["created_at"])
        self.assertTrue(result["version"])

    def test_dashboard_backfills_display_metadata_for_legacy_index(self):
        import time

        info_dir = Path(self.temp.name) / "index"
        info_dir.mkdir()
        info_path = info_dir / "index_info.json"
        info_path.write_text(json.dumps({"n_chunks": 1}), encoding="utf-8")
        service = SimpleNamespace(
            meta={}, document_meta={},
            index=SimpleNamespace(info={"n_chunks": 1}, chunks=[{"video_id": "legacy"}], graph={"version": 2}),
        )
        with (
            mock.patch.dict(os.environ, self.env),
            mock.patch.dict(runtime.state, {"service": service, "error": None}),
            mock.patch.object(library_api, "_ollama_status_cache", {"checked_at": time.monotonic(), "connected": False}),
        ):
            result = library_api.admin_status()["index"]
        self.assertEqual(result["chunk_count"], 1)
        self.assertTrue(result["created_at"])
        self.assertEqual(result["version"], 1)

    def test_public_catalog_defaults_old_items_to_public_then_respects_item_flag(self):
        video_id = "legacy-video"
        service = SimpleNamespace(
            meta={video_id: {"video_id": video_id, "title": "Bài cũ", "url": "https://example.org/v"}},
            index=SimpleNamespace(chunks=[{"video_id": video_id}]),
        )
        with mock.patch.dict(os.environ, self.env), mock.patch.dict(runtime.state, {"service": service}):
            self.assertEqual(len(library_api.public_videos()), 1)
            state_store.set_visibility("video", video_id, False)
            self.assertEqual(library_api.public_videos(), [])

    def test_public_ask_passes_only_visible_chunks_to_service(self):
        captured = {}

        class FakeService:
            meta = {}
            document_meta = {}
            index = SimpleNamespace(chunks=[
                {"chunk_id": "public-chunk", "video_id": "public-video"},
                {"chunk_id": "private-chunk", "video_id": "private-video"},
            ])

            def ask(self, question, **options):
                captured.update(options)
                return {"answer": "ok", "found": False, "cited": False, "sources": []}

        with mock.patch.dict(os.environ, self.env), mock.patch.dict(runtime.state, {"service": FakeService()}):
            state_store.set_visibility("video", "private-video", False)
            handlers.ask(handlers.AskRequest(question="câu hỏi"), public_only=True)
        self.assertEqual(captured["allowed_chunk_ids"], {"public-chunk"})

    def test_feedback_is_saved_without_recording_client_ip(self):
        from vskr.api import public as public_api

        answer = {"answer": "Trả lời", "found": True, "cited": False, "sources": []}
        with mock.patch.dict(os.environ, self.env), mock.patch.object(public_api.handlers, "ask", return_value=answer):
            with ASGITestClient(app) as client:
                result = client.post("/api/ask", json={"question": "Câu hỏi cần log"})
                self.assertEqual(result.status_code, 200)
                request_id = result.json()["request_id"]
                feedback = client.post(
                    "/api/feedback", json={"request_id": request_id, "feedback": "up"},
                    headers={"Origin": "http://localhost"},
                )
                question = state_store.recent_questions(1)[0]
        self.assertEqual(feedback.status_code, 200)
        self.assertEqual(question["question"], "Câu hỏi cần log")
        self.assertEqual(question["feedback"], "up")
        self.assertNotIn("ip", question)

    def test_sessions_are_read_from_sqlite_after_in_memory_cache_is_cleared(self):
        with mock.patch.dict(os.environ, self.env):
            token, session = auth.create_session()
            with auth._sessions_lock:
                auth._sessions.clear()
            self.assertEqual(auth.get_session(token, touch=False), session)

    def test_durable_running_job_is_recovered_as_retryable_error(self):
        job_id = "interrupted-job"
        with mock.patch.dict(os.environ, self.env):
            state_store.save_job(job_id, {
                "job_id": job_id, "status": "running", "phase": "asr",
                "created_at": "2026-01-01T00:00:00+00:00", "_source": {"kind": "url", "url": "https://example.org"},
            })
            state_store.recover_interrupted_jobs()
            recovered = state_store.load_job(job_id)
        self.assertEqual(recovered["status"], "error")
        self.assertEqual(recovered["_error"], "InterruptedJob")

    def test_failed_upload_job_deletion_removes_its_unindexed_upload(self):
        video_id = "upload-failed"
        video_dir = Path(self.temp.name) / "videos" / video_id
        video_dir.mkdir(parents=True)
        source = video_dir / "source.mp4"
        source.write_bytes(b"not indexed")
        job_id = "failed-upload"
        with mock.patch.dict(os.environ, self.env):
            runtime._jobs.clear()
            runtime._jobs[job_id] = {
                "job_id": job_id, "status": "error", "video_id": video_id,
                "_source": {"kind": "upload", "path": str(source), "video_id": video_id},
            }
            library_api.delete_job(job_id)
        self.assertFalse(video_dir.exists())

    def test_failed_upload_job_deletion_preserves_video_in_persisted_index(self):
        video_id = "indexed-upload"
        video_dir = Path(self.temp.name) / "videos" / video_id
        video_dir.mkdir(parents=True)
        source = video_dir / "source.mp4"
        source.write_bytes(b"indexed content")
        index_dir = Path(self.temp.name) / "index"
        index_dir.mkdir()
        (index_dir / "chunks.json").write_text(
            json.dumps([{"video_id": video_id, "chunk_id": "indexed"}]), encoding="utf-8"
        )
        job_id = "failed-indexed-upload"
        with mock.patch.dict(os.environ, self.env), mock.patch.dict(runtime.state, {"service": None}):
            runtime._jobs[job_id] = {
                "job_id": job_id, "status": "error", "video_id": video_id,
                "_source": {"kind": "upload", "path": str(source), "video_id": video_id},
            }
            library_api.delete_job(job_id)
        self.assertTrue(source.is_file())
        self.assertTrue(video_dir.is_dir())

    def test_retry_failed_job_persists_new_job_and_submits_worker(self):
        job_id = "failed-url"
        with mock.patch.dict(os.environ, self.env):
            runtime._jobs.clear()
            runtime._jobs[job_id] = {
                "job_id": job_id, "status": "error", "title": "Lecture",
                "_source": {"kind": "url", "url": "https://example.org/video"},
            }
            with mock.patch.object(runtime._job_executor, "submit") as submit:
                result = jobs_api.retry_job(job_id)
            self.assertEqual(state_store.load_job(result["job_id"])["status"], "queued")
        submit.assert_called_once()

    def test_audit_log_rotates_and_keeps_recent_events(self):
        from vskr import audit

        with mock.patch.dict(os.environ, self.env), mock.patch.object(audit, "MAX_BYTES", 240):
            for number in range(8):
                audit.write_event("test.rotation", target_id=str(number))
            recent = audit.recent_events(200)
        self.assertTrue((Path(self.temp.name) / "audit.jsonl.1").is_file())
        self.assertLess(Path(self.temp.name, "audit.jsonl").stat().st_size, 240)
        self.assertTrue(recent)
        self.assertEqual(recent[-1]["target_id"], "7")

    def test_retriever_filters_private_chunks_before_returning_hits(self):
        import numpy as np

        from vskr.retriever import Retriever

        chunks = [
            {"chunk_id": "private", "text": "private", "video_id": "hidden"},
            {"chunk_id": "public", "text": "public", "video_id": "visible"},
        ]
        index = SimpleNamespace(
            chunks=chunks, vectors=np.asarray([[1.0, 0.0], [0.8, 0.6]], dtype=np.float32),
            graph={"nodes": [], "edges": []}, bm25=SimpleNamespace(idf={}, scores=lambda _tokens: [1.0, 0.5]),
        )

        class FakeEmbedder:
            def encode_query(self, _query):
                return np.asarray([1.0, 0.0], dtype=np.float32)

        retriever = Retriever(index, FakeEmbedder(), {"mode": "vector", "top_k": 2, "candidate_k": 2})
        hits = retriever.search("query", mode="vector", allowed_chunk_ids={"public"})
        self.assertEqual([hit["chunk"]["chunk_id"] for hit in hits], ["public"])

    def test_graph_retrieval_excludes_edges_evidenced_only_by_hidden_chunks(self):
        import numpy as np

        from vskr.retriever import Retriever

        chunks = [
            {"chunk_id": "private", "text": "secretterm commonterm", "video_id": "hidden"},
            {"chunk_id": "public", "text": "commonterm", "video_id": "visible"},
        ]
        graph = {
            "nodes": [
                {"id": "concept:secretterm", "kind": "concept", "label": "secretterm"},
                {"id": "concept:commonterm", "kind": "concept", "label": "commonterm"},
                {"id": "source:private", "kind": "video_segment", "chunk_id": "private"},
                {"id": "source:public", "kind": "video_segment", "chunk_id": "public"},
            ],
            "edges": [
                {"from": "concept:secretterm", "to": "source:private", "type": "MENTIONS", "evidence_chunk_id": "private"},
                {"from": "concept:commonterm", "to": "source:private", "type": "MENTIONS", "evidence_chunk_id": "private"},
                {"from": "concept:secretterm", "to": "concept:commonterm", "type": "CO_OCCURS", "evidence_chunk_id": "private"},
                {"from": "concept:commonterm", "to": "source:public", "type": "MENTIONS", "evidence_chunk_id": "public"},
            ],
        }
        index = SimpleNamespace(
            chunks=chunks, vectors=np.asarray([[1.0, 0.0], [0.8, 0.6]], dtype=np.float32),
            graph=graph, bm25=SimpleNamespace(idf={}, scores=lambda _tokens: [1.0, 0.5]),
        )

        class FakeEmbedder:
            def encode_query(self, _query):
                return np.asarray([1.0, 0.0], dtype=np.float32)

        retriever = Retriever(index, FakeEmbedder(), {"mode": "graph", "top_k": 5, "candidate_k": 5})
        hits = retriever.search("secretterm", mode="graph", allowed_chunk_ids={"public"})
        self.assertEqual(hits, [])

    def test_ask_does_not_hold_service_lock_during_model_work(self):
        rendezvous = threading.Barrier(2)

        class FakeService:
            def ask(self, question, **_options):
                rendezvous.wait(timeout=2)
                return {"answer": question, "found": False, "cited": False, "sources": []}

        with mock.patch.dict(runtime.state, {"service": FakeService(), "error": None}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                calls = [pool.submit(handlers.ask, handlers.AskRequest(question=f"q{i}"), use_request_mode=False) for i in range(2)]
                results = [call.result(timeout=3) for call in calls]
        self.assertEqual({item["answer"] for item in results}, {"q0", "q1"})


if __name__ == "__main__":
    unittest.main()
