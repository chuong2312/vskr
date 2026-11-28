"""scripts/eval/label_slide_changes.py và eval_slide_states.py: trang gán nhãn và phép đo P/R/F1."""
import json
import re
import unittest

from scripts.eval.eval_slide_states import evaluate, format_report, match_changes
from scripts.eval.label_slide_changes import build_label_page, thumb_times, thumbnail_cmd


class TestMatchChanges(unittest.TestCase):
    def test_exact_and_tolerant_matches(self):
        result = match_changes([10.0, 40.0, 90.0], [11.0, 43.0, 200.0], tolerance=3.0)
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (2, 1, 1))
        self.assertEqual(result["missed"], [90.0])
        self.assertEqual(result["extra"], [200.0])
        self.assertAlmostEqual(result["precision"], 0.667)
        self.assertAlmostEqual(result["recall"], 0.667)

    def test_each_label_matches_at_most_one_detection(self):
        result = match_changes([10.0], [9.0, 10.5], tolerance=3.0)
        self.assertEqual(result["matched"], [(10.0, 10.5)])
        self.assertEqual(result["extra"], [9.0])

    def test_closest_pairs_win(self):
        # Ghép tham lam theo thứ tự nhãn sẽ lấy 12 cho nhãn 10 rồi bỏ sót nhãn 13.
        result = match_changes([10.0, 13.0], [9.0, 12.0], tolerance=3.0)
        self.assertEqual(result["tp"], 2)

    def test_empty_inputs(self):
        self.assertEqual(match_changes([], [], 3.0)["f1"], 0.0)
        self.assertEqual(match_changes([5.0], [], 3.0)["recall"], 0.0)


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.states = {"states": [
            {"start": 0.0, "kind": "start", "blank": False},
            {"start": 12.0, "kind": "slide", "blank": False, "change_area": 0.4, "bbox": [0, 0, 1, 1]},
            {"start": 30.0, "kind": "slide", "blank": True, "change_area": 0.9, "bbox": [0, 0, 1, 1]},
            {"start": 33.0, "kind": "partial", "blank": False, "change_area": 0.01, "bbox": [0.5, 0.5, 0.6, 0.6]},
            {"start": 700.0, "kind": "slide", "blank": False, "change_area": 0.5, "bbox": [0, 0, 1, 1]},
        ]}
        self.labels = {"video_id": "v1", "start": 0, "end": 600, "every": 2, "changes": [12, 34, 650]}

    def test_only_counts_inside_the_labelled_range_and_skips_blank(self):
        result = evaluate(self.states, self.labels)
        self.assertEqual(result["label_count"], 2)
        self.assertEqual(result["detected_count"], 2)
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (2, 0, 0))

    def test_blank_states_can_be_counted(self):
        result = evaluate(self.states, self.labels, count_blank=True)
        self.assertEqual(result["fp"], 1)
        self.assertEqual(result["extra_states"][0]["start"], 30.0)
        report = format_report("v1", result)
        self.assertIn("Dò thừa", report)
        self.assertIn("00:30", report)


class TestLabelPage(unittest.TestCase):
    def test_thumbnail_times_and_command(self):
        self.assertEqual(thumb_times(3, 600, 2), [600.0, 602.0, 604.0])
        cmd = thumbnail_cmd("lecture.mp4", 0, 600, 2, 320, "thumbs/t_%05d.jpg")
        self.assertLess(cmd.index("-protocol_whitelist"), cmd.index("-i"))
        self.assertLess(cmd.index("-ss"), cmd.index("-i"))
        self.assertIn("fps=1/2.0,scale=320:-2", cmd)

    def test_page_escapes_title_and_lists_every_thumbnail(self):
        items = [(0.0, "thumbs/t_00001.jpg"), (2.0, "thumbs/t_00002.jpg")]
        page = build_label_page("v1", "Bài <script>alert(1)</script>", items, 0.0, 600.0, 2.0)
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn('src="thumbs/t_00002.jpg"', page)
        self.assertIn('data-t="2.0"', page)
        self.assertIn("Tải file nhãn", page)
        self.assertNotIn("http", page)  # trang chạy trên máy, không tải gì từ mạng
        settings = re.search(r"const SETTINGS = (\{.*?\});", page).group(1)
        self.assertEqual(json.loads(settings)["video_id"], "v1")


if __name__ == "__main__":
    unittest.main()
