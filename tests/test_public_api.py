import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vskr.api import app, handlers
from vskr.api.public import _safe_source_link

from tests.asgi_client import ASGITestClient


class TestPublicCatalog(unittest.TestCase):
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
            "documents": [{"document_id": "doc-ready", "title": "doc-ready", "sections": 2}],
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
        result = response.json()
        self.assertNotIn("timing", result)
        self.assertNotIn("matched_concepts", result)
        self.assertNotIn("chunk_id", result["sources"][0])
        self.assertNotIn("score", result["sources"][0])
        self.assertEqual(result["sources"][0]["link"], "https://lectures.example/watch")
        self.assertNotIn("token", response.text)

    def test_source_links_only_allow_safe_hosts_and_strip_query_tokens(self):
        self.assertEqual(
            _safe_source_link({"link": "https://youtube.com/watch?v=old", "video_id": "vid", "start": 12}),
            "https://www.youtube.com/watch?v=vid&t=12s",
        )
        self.assertIsNone(_safe_source_link({"link": "https://user:secret@example.com/watch", "video_id": "vid"}))


if __name__ == "__main__":
    unittest.main()
