import unittest

from scripts.benchmark_asr import edit_distance, normalize, score_text


class TestAsrBenchmarkMetrics(unittest.TestCase):
    def test_normalize_vietnamese_and_punctuation(self):
        self.assertEqual(normalize("  BẮT đầu,  học! "), "bắt đầu học")

    def test_edit_distance(self):
        self.assertEqual(edit_distance(["một", "hai"], ["một", "ba"]), 1)
        self.assertEqual(edit_distance([], ["một"]), 1)

    def test_identical_transcript_scores_zero(self):
        self.assertEqual(score_text("Xin chào Việt Nam!", "xin chào việt nam"), {"wer": 0.0, "cer": 0.0})


if __name__ == "__main__":
    unittest.main()
