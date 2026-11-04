import tempfile
import unittest
from pathlib import Path

from vskr.frames import candidate_times, cap_evenly, dedupe_hashes, dhash, hamming


class TestFrames(unittest.TestCase):
    def test_interval_only(self):
        c = candidate_times(30, [], interval_s=10)
        self.assertEqual([t for t, _ in c], [0.0, 10.0, 20.0])
        self.assertTrue(all(s == "interval" for _, s in c))

    def test_scene_replaces_close_interval(self):
        c = candidate_times(30, [9.5], interval_s=10, offset_s=1.0, min_gap_s=2.0)
        self.assertIn((10.5, "scene"), c)
        self.assertNotIn((10.0, "interval"), c)

    def test_sorted_and_within_duration(self):
        c = candidate_times(25, [3, 3.2, 24.9], interval_s=5)
        ts = [t for t, _ in c]
        self.assertEqual(ts, sorted(ts))
        self.assertTrue(all(0 <= t <= 24.5 for t in ts))
        self.assertTrue(all(b - a >= 2.0 for a, b in zip(ts, ts[1:])))

    def test_cap(self):
        items = list(range(100))
        out = cap_evenly(items, 10)
        self.assertEqual(len(out), 10)
        self.assertEqual(out[0], 0)
        self.assertEqual(cap_evenly(items, None), items)
        self.assertEqual(cap_evenly(items, 200), items)

    def test_dedupe_compares_to_last_kept(self):
        base = 0
        hs = [base, base ^ 0b1, base ^ 0b11, base ^ 0b111, base ^ 0b1111, 0xFFFF]
        # khác frame đã giữ (0) tối đa 2 bit thì bỏ; tích lũy 3 bit trở lên thì giữ
        # 0b1, 0b11: <=2 bit so với frame 0 -> bỏ; 0b111: 3 bit -> giữ; 0b1111: 1 bit so với 0b111 -> bỏ; 0xFFFF: xa hẳn -> giữ
        self.assertEqual(dedupe_hashes(hs, 2), [0, 3, 5])
        self.assertEqual(hamming(0b1010, 0b0101), 4)

    def test_dhash_similar_vs_different(self):
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as d:
            def img(name, text_pos):
                im = Image.new("RGB", (320, 180), "white")
                dr = ImageDraw.Draw(im)
                for k in range(text_pos):
                    dr.rectangle([10, 10 + k * 20, 200, 20 + k * 20], fill="black")
                p = Path(d) / name
                im.save(p)
                return dhash(p)

            a, a2, b = img("a.png", 3), img("a2.png", 3), img("b.png", 8)
            self.assertEqual(hamming(a, a2), 0)
            self.assertGreater(hamming(a, b), 0)


if __name__ == "__main__":
    unittest.main()
