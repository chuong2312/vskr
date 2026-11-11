"""Kiểm tra luồng index -> retriever -> service với embedder giả và LLM giả (không cần GPU/mạng)."""
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from vskr import index as index_mod
from vskr import qa
from vskr.config import load_config, video_dir
from vskr.util import write_json


class FakeEmbedder:
    """Bag-of-words băm vào 64 chiều: đủ để câu hỏi chứa cùng từ thì gần nhau."""

    def _vec(self, text):
        v = np.zeros(64, dtype=np.float32)
        for w in text.lower().split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
        n = np.linalg.norm(v)
        return v / n if n else v

    def encode_passages(self, texts):
        return np.stack([self._vec(t) for t in texts])

    def encode_query(self, t):
        return self._vec(t)


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = load_config()
        self.cfg["paths"]["data_dir"] = str(self.tmp)
        vd = video_dir(self.cfg, "vid1")
        write_json(vd / "meta.json", {"video_id": "vid1", "title": "Bài 1", "duration": 300, "url": "https://www.youtube.com/watch?v=vid1"})
        segs = [
            {"start": 0, "end": 20, "text": "hôm nay ta học khai báo biến trong python bằng dấu bằng"},
            {"start": 20, "end": 45, "text": "biến x gán giá trị mười viết x bằng mười"},
            {"start": 100, "end": 130, "text": "vòng lặp for duyệt qua danh sách phần tử"},
            {"start": 130, "end": 160, "text": "vòng lặp while lặp đến khi điều kiện sai"},
            {"start": 200, "end": 240, "text": "hàm def định nghĩa hàm với tham số và return"},
        ]
        write_json(vd / "transcript.json", {"segments": segs})
        self.emb = FakeEmbedder()
        self.cfg["chunk"].update({"window_s": 50, "overlap_s": 10, "min_chars": 5})
        index_mod.build(self.cfg, self.emb)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _retriever(self):
        from vskr.retriever import Retriever

        return Retriever(index_mod.load(self.cfg), self.emb, self.cfg["retrieval"])

    def test_all_modes_find_variable_chunk(self):
        r = self._retriever()
        for mode in ("vector", "bm25", "hybrid", "graph"):
            hits = r.search("khai báo biến python", mode=mode, top_k=2)
            self.assertTrue(hits, mode)
            self.assertEqual(hits[0]["chunk"]["start"], 0, mode)

    def test_default_mode_is_hybrid(self):
        self.assertEqual(self.cfg["retrieval"]["mode"], "hybrid")
        hits = self._retriever().search("khai báo biến python")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["chunk"]["start"], 0)

    def test_bm25_no_match_returns_empty(self):
        self.assertEqual(self._retriever().search("zzzz qqqq", mode="bm25"), [])

    def test_min_vector_score_blocks(self):
        r = self._retriever()
        r.cfg = dict(r.cfg, min_vector_score=0.99)
        self.assertEqual(r.search("câu hỏi lạc đề hoàn toàn", mode="vector"), [])

    def test_service_maps_ids_to_timestamps(self):
        from vskr.service import Service

        with mock.patch("vskr.service.Embedder", return_value=self.emb):
            svc = Service(self.cfg)
        with mock.patch.object(qa, "call_ollama", return_value="<think>x</think>Dùng dấu bằng để khai báo biến [1]."):
            res = svc.ask("khai báo biến python")
        self.assertTrue(res["found"] and res["cited"])
        s = res["sources"][0]
        self.assertEqual((s["n"], s["video_id"], s["start"]), (1, "vid1", 0))
        self.assertIn("watch?v=vid1&t=0s", s["link"])
        self.assertEqual(s["title"], "Bài 1")

    def test_service_hallucinated_citation_falls_back(self):
        from vskr.service import Service

        with mock.patch("vskr.service.Embedder", return_value=self.emb):
            svc = Service(self.cfg)
        with mock.patch.object(qa, "call_ollama", return_value="Câu trả lời [9]."):
            res = svc.ask("khai báo biến python")
        self.assertFalse(res["cited"])
        self.assertTrue(1 <= len(res["sources"]) <= 3)

    def test_service_not_found(self):
        from vskr.service import Service

        with mock.patch("vskr.service.Embedder", return_value=self.emb):
            svc = Service(self.cfg)
        with mock.patch.object(qa, "call_ollama", return_value=qa.NOT_FOUND):
            res = svc.ask("khai báo biến python")
        self.assertFalse(res["found"])
        self.assertEqual(res["sources"], [])

    def test_service_handles_document_chunks_created_by_documents_module(self):
        from vskr.documents import ingest_document
        from vskr.service import Service

        doc = self.tmp / "python-notes.txt"
        doc.write_text("Python cryptography dùng khóa công khai để mã hóa dữ liệu; phần này chỉ có trong tài liệu.", encoding="utf-8")
        ingest_document(doc, self.cfg, document_id="doc-real")
        index_mod.build(self.cfg, self.emb)
        with mock.patch("vskr.service.Embedder", return_value=self.emb):
            svc = Service(self.cfg)
        with mock.patch.object(qa, "call_ollama", return_value="Có thể khai báo biến bằng phép gán [1]."):
            for mode in ("hybrid", "graph"):
                result = svc.ask("cryptography", mode=mode)
                self.assertTrue(result["sources"], mode)
                source = result["sources"][0]
                self.assertEqual(source["source_type"], "document")
                self.assertEqual(source["document_id"], "doc-real")
                self.assertEqual(source["section"], 1)
                self.assertIsNone(source["start"])
                self.assertEqual(source["label"], "Mục 1")
                self.assertIn("/api/documents/doc-real/sections/1", source["link"])

    def test_service_returns_video_and_document_sources_together(self):
        from vskr.documents import ingest_document
        from vskr.service import Service

        doc = self.tmp / "python-reference.txt"
        doc.write_text("Python khai báo biến trong chương trình bằng dấu bằng. Giá trị được gán cho tên biến.", encoding="utf-8")
        ingest_document(doc, self.cfg, document_id="doc-python")
        index_mod.build(self.cfg, self.emb)
        self.cfg["retrieval"]["top_k"] = 20
        with mock.patch("vskr.service.Embedder", return_value=self.emb):
            svc = Service(self.cfg)
        with mock.patch.object(qa, "call_ollama", return_value="Nguồn video và tài liệu [1] [2] [3] [4] [5] [6] [7] [8] [9] [10]."):
            result = svc.ask("Python khai báo biến", mode="hybrid")
        kinds = {source["source_type"] for source in result["sources"]}
        self.assertEqual(kinds, {"video", "document"})


if __name__ == "__main__":
    unittest.main()
