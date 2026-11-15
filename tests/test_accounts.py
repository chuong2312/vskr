import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from vskr.accounts import COOKIE_NAME
from vskr.api import app
from vskr.api import handlers
from vskr.bm25 import BM25, tokenize
from vskr.index import Index
from vskr.service import Service
from tests.asgi_client import ASGITestClient


class TestLearnerAccounts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = {
            "VSKR__PATHS__DATA_DIR": self.temp.name,
            "VSKR_USER_INVITE_CODE": "class-invite-2026",
            "VSKR_COOKIE_SECURE": "false",
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_invite_registration_creates_session_and_logout_revokes_it(self):
        with mock.patch.dict(os.environ, self.env):
            with ASGITestClient(app, authenticated_learner=False) as client:
                denied = client.get("/api/catalog")
                self.assertEqual(denied.status_code, 401)
                created = client.post("/api/user/auth/register", json={
                    "username": "minhanh", "password": "a-long-test-password", "invite_code": "class-invite-2026",
                }, headers={"Origin": "http://localhost"})
                self.assertEqual(created.status_code, 200, created.text)
                self.assertIn(COOKIE_NAME, client.cookies)
                self.assertEqual(client.get("/api/user/auth/me").json()["username"], "minhanh")
                csrf = created.json()["csrf_token"]
                logout = client.post("/api/user/auth/logout", headers={
                    "Origin": "http://localhost", "X-CSRF-Token": csrf,
                })
                self.assertEqual(logout.status_code, 200, logout.text)
                self.assertEqual(client.get("/api/user/auth/me").status_code, 401)

    def test_registration_requires_invite_code_and_unique_username(self):
        with mock.patch.dict(os.environ, self.env):
            with ASGITestClient(app, authenticated_learner=False) as client:
                payload = {"username": "student1", "password": "a-long-test-password", "invite_code": "wrong"}
                headers = {"Origin": "http://localhost"}
                self.assertEqual(client.post("/api/user/auth/register", json=payload, headers=headers).status_code, 403)
                payload["invite_code"] = "class-invite-2026"
                self.assertEqual(client.post("/api/user/auth/register", json=payload, headers=headers).status_code, 200)
                duplicate = client.post("/api/user/auth/register", json=payload, headers=headers)
                self.assertEqual(duplicate.status_code, 409)

    def test_conversation_history_is_persisted_and_private_to_the_account(self):
        env = {**self.env, "VSKR_ADMIN_USER": "", "VSKR_ADMIN_PASSWORD_HASH": ""}
        result = {
            "answer": "Vector là biểu diễn số [1].", "found": True, "cited": True,
            "sources": [{"n": 1, "source_type": "video", "video_id": "shared-video",
                         "title": "Đại số", "start": 12, "end": 24,
                         "label": "0:12-0:24", "text": "Vector được biểu diễn bằng số.",
                         "link": "https://www.youtube.com/watch?v=abc&t=12s", "cited": True}],
        }
        with mock.patch.dict(os.environ, env), mock.patch.object(handlers, "ask", return_value=result):
            with ASGITestClient(app, authenticated_learner=False) as client:
                headers = {"Origin": "http://localhost"}
                learner_a = client.post("/api/user/auth/register", json={
                    "username": "learner-a", "password": "a-long-test-password", "invite_code": "class-invite-2026",
                }, headers=headers).json()
                csrf_a = learner_a["csrf_token"]
                answer = client.post("/api/ask", json={"question": "Vector là gì?"}, headers={
                    **headers, "X-CSRF-Token": csrf_a,
                })
                self.assertEqual(answer.status_code, 200, answer.text)
                conversation_id = answer.json()["conversation_id"]
                saved = client.get(f"/api/conversations/{conversation_id}")
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()["turns"][0]["question"], "Vector là gì?")
                self.assertEqual(saved.json()["turns"][0]["sources"][0]["video_id"], "shared-video")
                self.assertEqual(client.post("/api/user/auth/logout", headers={
                    **headers, "X-CSRF-Token": csrf_a,
                }).status_code, 200)

                learner_b = client.post("/api/user/auth/register", json={
                    "username": "learner-b", "password": "a-long-test-password", "invite_code": "class-invite-2026",
                }, headers=headers).json()
                self.assertNotEqual(learner_a["user"]["user_id"], learner_b["user"]["user_id"])
                self.assertEqual(client.get("/api/conversations").json(), [])
                self.assertEqual(client.get(f"/api/conversations/{conversation_id}").status_code, 404)

    def test_learner_retrieval_only_receives_shared_and_owned_chunks(self):
        chunks = [
            {"chunk_id": "shared:1", "video_id": "shared", "text": "shared topic"},
            {"chunk_id": "mine:1", "video_id": "mine", "owner_id": "user-a", "text": "private topic"},
            {"chunk_id": "theirs:1", "video_id": "theirs", "owner_id": "user-b", "text": "secret topic"},
        ]
        index = Index(chunks, np.asarray([[1., 0.], [0., 1.], [.5, .5]]),
                      BM25([tokenize(chunk["text"]) for chunk in chunks]), {},
                      {"version": 1, "nodes": [], "edges": []})
        service = object.__new__(Service)
        service.index = index
        service.meta = {"shared": {"title": "Shared"}, "mine": {"title": "Mine"}, "theirs": {"title": "Theirs"}}
        service.document_meta = {}
        service.cfg = {"retrieval": {"mode": "hybrid", "top_k": 3}, "llm": {}}
        service.embedder = mock.Mock()
        service.embedder.encode_query.return_value = np.asarray([1., 0.])
        service.retriever = None
        observed = {}

        def answer(_question, hits, _cfg):
            observed["chunks"] = [hit["chunk"]["chunk_id"] for hit in hits]
            return {"answer": "ok", "found": True, "cited": False, "source_ns": []}

        with mock.patch("vskr.service.answer_question", side_effect=answer):
            result = service.ask("private topic", owner_id="user-a")
        self.assertEqual(set(observed["chunks"]), {"shared:1", "mine:1"})
        self.assertNotIn("theirs:1", observed["chunks"])
        self.assertEqual(result["answer"], "ok")
