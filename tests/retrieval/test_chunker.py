"""Chia transcript thành đoạn có chồng lấn và gộp chữ trên màn hình theo thời gian."""
import unittest

from vskr.retrieval.chunker import make_chunks


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


if __name__ == "__main__":
    unittest.main()
