"""scripts/eval_variants.py: mỗi biến thể chỉ thấy đúng nguồn của nó và không đụng data/ thật."""
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.eval_variants import VARIANTS, apply_chunk_overrides, format_report, read_questions, run_variants
from tests.test_integration import FakeEmbedder
from vskr.config import load_config, video_dir
from vskr.util import write_json


def _snapshot(root):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*"))


class TestEvalVariants(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = load_config()
        self.cfg["paths"]["data_dir"] = str(self.tmp / "data")
        self.cfg["chunk"]["min_chars"] = 5
        vd = video_dir(self.cfg, "v1")
        write_json(vd / "meta.json", {"video_id": "v1", "title": "Bài 1", "duration": 300, "url": "u"})
        write_json(vd / "transcript.json", {"segments": [
            {"start": 0, "end": 40, "text": "hôm nay giảng về chỉ mục btree trong cơ sở dữ liệu"},
            {"start": 100, "end": 140, "text": "phần này nói về giao dịch và khóa bản ghi"},
            {"start": 200, "end": 240, "text": "cuối cùng là sao lưu và phục hồi dữ liệu"},
        ]})
        write_json(vd / "scenes.json", {"scene_starts": [0, 100, 200]})
        write_json(vd / "screen_text.json", [{"start": 110, "end": 130, "text": "WALSEGMENT", "type": "ocr"}])
        write_json(vd / "captions.json", [{"start": 210, "end": 210, "text": "SODOREPLICA", "type": "caption"}])
        (vd / "video.mp4").write_bytes(b"not copied")
        self.questions = [
            {"id": "sp", "type": "fact", "evidence": "spoken", "question": "btree",
             "relevant": [{"video_id": "v1", "start": 0, "end": 40}]},
            {"id": "sc", "type": "fact", "evidence": "screen", "question": "WALSEGMENT",
             "relevant": [{"video_id": "v1", "start": 100, "end": 140}]},
            {"id": "fg", "type": "fact", "evidence": "figure", "question": "SODOREPLICA",
             "relevant": [{"video_id": "v1", "start": 200, "end": 240}]},
        ]

    def _run(self, variants):
        before = _snapshot(self.tmp / "data")
        with redirect_stdout(StringIO()):
            results = run_variants(self.cfg, self.questions, variants, FakeEmbedder(), self.tmp / "work")
        self.assertEqual(_snapshot(self.tmp / "data"), before, "data/ thật không được thay đổi")
        self.assertFalse((self.tmp / "data" / "index").exists())
        return results

    def _top1(self, result, qid, mode="bm25"):
        detail = next(d for d in result["details"] if d["id"] == qid)
        return detail["methods"][mode]["mrr"]

    def test_each_variant_sees_only_its_sources(self):
        results = self._run(["transcript", "ocr", "ocr_vlm"])
        self.assertEqual(self._top1(results["transcript"], "sp"), 1.0)
        self.assertEqual(self._top1(results["transcript"], "sc"), 0.0)
        self.assertEqual(self._top1(results["ocr"], "sc"), 1.0)
        self.assertEqual(self._top1(results["ocr"], "fg"), 0.0)
        self.assertEqual(self._top1(results["ocr_vlm"], "fg"), 1.0)
        self.assertEqual(results["transcript"]["chunks_with_visual_text"], 0)
        self.assertEqual(results["ocr_vlm"]["chunks_with_visual_text"], 2)
        self.assertEqual(results["ocr"]["files_found"], {"screen_text.json": 1, "ocr_info.json": 0})
        self.assertFalse((self.tmp / "work" / "ocr" / "videos" / "v1" / "video.mp4").exists())
        self.assertEqual(set(results["ocr"]["summary"]), {"vector", "bm25", "hybrid", "graph", "hybrid_graph"})

    def test_variant_overrides_merge_into_the_configured_filter(self):
        chunk = {"window_s": 50, "screen_text_filter": {"enabled": True, "frequent_ratio": 0.3}}
        merged = apply_chunk_overrides(chunk, VARIANTS["ocr_vlm_persistent"][1])
        self.assertEqual(merged["screen_text_filter"], {"enabled": True, "frequent_ratio": 0.3, "persistent_ratio": 0.6})
        raw = apply_chunk_overrides(chunk, VARIANTS["ocr_raw"][1])
        self.assertFalse(raw["screen_text_filter"]["enabled"])
        self.assertEqual(chunk["screen_text_filter"], {"enabled": True, "frequent_ratio": 0.3}, "cấu hình gốc không bị sửa")

    def test_report_has_one_column_per_evidence_group(self):
        # Mốc thời gian nằm ngoài mọi chunk: báo cáo phải liệt kê câu này để người duyệt xem lại.
        self.questions.append({"id": "bad", "type": "fact", "question": "mốc sai",
                               "relevant": [{"video_id": "v1", "start": 900, "end": 910}]})
        report = format_report(self._run(["transcript", "ocr"]))
        self.assertIn("lời nói (1)", report)
        self.assertIn("màn hình (1)", report)
        self.assertIn("chưa gắn (1)", report)
        self.assertIn("tất cả (4)", report)
        never = report.split("Câu không chế độ")[1]
        self.assertIn("bad: mốc sai", never)
        self.assertNotIn("btree", never)

    def test_read_questions_validates_and_rejects_duplicates(self):
        path = self.tmp / "q.jsonl"
        path.write_text("\n".join(json.dumps({**q, "answer": "bỏ qua"}) for q in self.questions) + "\n",
                        encoding="utf-8")
        self.assertEqual([q["id"] for q in read_questions(path)], ["sp", "sc", "fg"])
        self.assertNotIn("answer", read_questions(path)[0])
        path.write_text(json.dumps(self.questions[0]) + "\n" + json.dumps(self.questions[0]) + "\n",
                        encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "trùng id"):
            read_questions(path)


if __name__ == "__main__":
    unittest.main()
