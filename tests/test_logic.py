import unittest

from vskr.bm25 import BM25, tokenize
from vskr.chunker import make_chunks
from vskr.fusion import rrf_fuse
from vskr.qa import NOT_FOUND, build_messages, extract_citations, is_not_found, strip_think
from vskr.util import fmt_time, yt_link


def segs(n, step=10, length=10):
    return [{"start": i * step, "end": i * step + length, "text": f"câu số {i} nói về biến"} for i in range(n)]


class TestChunker(unittest.TestCase):
    def test_window_overlap(self):
        ch = make_chunks("v", segs(12), window_s=50, overlap_s=10, min_chars=1)
        # mỗi chunk tối đa 50s, chunk sau bắt đầu sau chunk trước đúng 40s
        self.assertTrue(all(c["end"] - c["start"] <= 50 for c in ch))
        starts = [c["start"] for c in ch]
        self.assertEqual(starts[:3], [0, 40, 80])
        self.assertEqual(ch[0]["chunk_id"], "v:0000")

    def test_covers_everything_no_infinite_loop(self):
        ch = make_chunks("v", segs(50), window_s=30, overlap_s=10, min_chars=1)
        self.assertEqual(ch[0]["start"], 0)
        self.assertEqual(ch[-1]["end"], 500)
        covered = set()
        for c in ch:
            covered.update(range(int(c["start"]), int(c["end"])))
        self.assertEqual(len(covered), 500)

    def test_long_single_segment(self):
        ch = make_chunks("v", [{"start": 0, "end": 120, "text": "một đoạn rất dài " * 5}], 50, 10, 1)
        self.assertEqual(len(ch), 1)

    def test_empty_and_short_dropped(self):
        self.assertEqual(make_chunks("v", [], 50, 10), [])
        ch = make_chunks("v", [{"start": 0, "end": 2, "text": "ừ"}], 50, 10, min_chars=20)
        self.assertEqual(ch, [])

    def test_bad_overlap(self):
        with self.assertRaises(ValueError):
            make_chunks("v", segs(3), 30, 30)

    def test_extras_merged_by_time(self):
        extras = [
            {"start": 5, "end": 8, "text": "x = 10", "type": "ocr"},
            {"start": 6, "end": 9, "text": "x = 10", "type": "ocr"},
            {"start": 300, "end": 301, "text": "ngoài khoảng", "type": "ocr"},
        ]
        ch = make_chunks("v", segs(3), 50, 10, 1, extras=extras)
        self.assertIn("[Trên màn hình] x = 10", ch[0]["text"])
        self.assertEqual(ch[0]["text"].count("x = 10"), 1)
        self.assertNotIn("ngoài khoảng", ch[0]["text"])
        self.assertEqual(ch[0]["source_types"], ["speech", "ocr"])


class TestRetrievalLogic(unittest.TestCase):
    def test_tokenize_nfc(self):
        a = tokenize("Biến số")
        b = tokenize("Bie\u0302\u0301n so\u0302\u0301")  # dạng tổ hợp
        self.assertEqual(tokenize("Biến"), tokenize("Bie\u0302\u0301n"))
        self.assertEqual(a[0], "biến")
        self.assertEqual(len(b), 2)

    def test_bm25_ranks_relevant_first(self):
        docs = ["khai báo biến trong python", "vòng lặp for và while", "hàm và tham số"]
        bm = BM25([tokenize(d) for d in docs])
        s = bm.scores(tokenize("khai báo biến"))
        self.assertEqual(max(range(3), key=lambda i: s[i]), 0)
        self.assertEqual(bm.scores(tokenize("không có từ này")), [0.0, 0.0, 0.0])

    def test_rrf(self):
        fused = rrf_fuse([[1, 2, 3], [3, 1, 4]], k=60)
        ids = [i for i, _ in fused]
        self.assertEqual(ids[0], 1)  # 1: hạng 1 và 2; 3: hạng 3 và 1 -> 1 cao hơn
        self.assertEqual(set(ids), {1, 2, 3, 4})
        self.assertGreater(fused[0][1], fused[-1][1])


class TestQA(unittest.TestCase):
    def test_citations(self):
        self.assertEqual(extract_citations("A [1]. B [Nguồn 3]. C [1, 2].", 3), [1, 3, 2])

    def test_invalid_citations_dropped(self):
        self.assertEqual(extract_citations("A [7]. B [0]. C [2].", 3), [2])

    def test_no_citation(self):
        self.assertEqual(extract_citations("không có nguồn", 3), [])

    def test_not_found_detection(self):
        self.assertTrue(is_not_found(NOT_FOUND))
        self.assertTrue(is_not_found("Không tìm thấy thông tin này trong video"))
        self.assertFalse(is_not_found("Biến được khai báo bằng dấu bằng [1]."))

    def test_strip_think(self):
        self.assertEqual(strip_think("<think>nghĩ\nnhiều</think>\nTrả lời [1]"), "Trả lời [1]")

    def test_prompt_has_no_timestamps(self):
        hits = [{"chunk": {"text": "nội dung A", "start": 32, "end": 40, "video_id": "abc"}}]
        msgs = build_messages("câu hỏi?", hits)
        user = msgs[1]["content"]
        self.assertIn("[Nguồn 1]", user)
        self.assertNotIn("abc", user)
        self.assertNotIn("32", user)


class TestUtil(unittest.TestCase):
    def test_fmt(self):
        self.assertEqual(fmt_time(32), "00:32")
        self.assertEqual(fmt_time(3725), "1:02:05")

    def test_link(self):
        self.assertEqual(yt_link("ID", 32.9), "https://www.youtube.com/watch?v=ID&t=32s")


if __name__ == "__main__":
    unittest.main()
