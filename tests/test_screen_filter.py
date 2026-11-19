"""Bộ lọc chữ rác OCR: bỏ menu/watermark lặp ở mép màn hình và mã số; giữ nội dung ở giữa."""
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tests.test_integration import FakeEmbedder
from vskr import index as index_mod
from vskr.config import load_config, video_dir
from vskr.screen_filter import filter_screen_text, frame_height
from vskr.util import read_json, write_json

H = 720


def _row(text, t0, t1, y, x=100):
    return {"text": text, "start": float(t0), "end": float(t1), "type": "ocr", "score": 0.9,
            "box": [[x, y - 10], [x + 300, y - 10], [x + 300, y + 10], [x, y + 10]]}


FRAMES = [float(t) for t in range(0, 400, 10)]  # 40 frame


class TestFilterScreenText(unittest.TestCase):
    def setUp(self):
        self.rows = [
            _row("WECOMMIT.COM.VN TRANQUOCHUY", 0, 390, 705),            # watermark, mọi frame, mép dưới
            _row("Object Explorer Dashboard Properties", 0, 200, 60),    # menu, nửa video, mép trên
            _row("Object Explorer Dashbcard Properties", 210, 300, 62),  # OCR đọc lệch 1 từ: vẫn là menu
            _row("Kiến trúc vật lý của PostgreSQL", 100, 110, 360),      # nội dung slide, giữa màn hình
            _row("hire_date timestamp without time zone", 300, 310, 300),
            _row("Bảng employees", 20, 30, 50),                          # mép trên nhưng từ hiếm → giữ
            _row("1721031511 Nguyen Van A", 200, 200, 400),             # mã số sinh viên trong khung chat
        ]

    def _kept(self, **options):
        kept, stats = filter_screen_text(self.rows, FRAMES, H, options)
        return [r["text"] for r in kept], stats

    def test_drops_edge_boilerplate_and_long_numbers_keeps_content(self):
        kept, stats = self._kept()
        self.assertEqual(kept, ["Kiến trúc vật lý của PostgreSQL", "hire_date timestamp without time zone",
                                "Bảng employees"])
        self.assertEqual(stats, {"rows": 7, "kept": 3, "boilerplate": 3, "long_number": 1})

    def test_frequent_line_in_the_middle_of_the_screen_is_kept(self):
        # Một slide nội dung có thể lặp nhiều, nhưng ở giữa màn hình thì không bị coi là rác.
        self.rows.append(_row("WECOMMIT.COM.VN TRANQUOCHUY", 0, 390, 360))
        kept, _ = self._kept()
        self.assertIn("WECOMMIT.COM.VN TRANQUOCHUY", kept)

    def test_few_frames_disable_the_frequency_rule(self):
        kept, stats = filter_screen_text(self.rows, FRAMES[:5], H, {})
        self.assertEqual(stats["boilerplate"], 0)
        self.assertEqual(stats["long_number"], 1)
        self.assertEqual(len(kept), 6)

    def test_disabled_keeps_everything(self):
        kept, stats = self._kept(enabled=False)
        self.assertEqual(len(kept), 7)
        self.assertEqual(stats["kept"], 7)

    def test_long_number_rule_can_be_turned_off(self):
        kept, _ = self._kept(drop_long_numbers=False)
        self.assertIn("1721031511 Nguyen Van A", kept)

    def test_frame_height_prefers_ocr_info_then_estimates(self):
        self.assertEqual(frame_height(self.rows, [1280, 720]), 720.0)
        self.assertEqual(frame_height(self.rows, None), 715.0)
        self.assertIsNone(frame_height([], None))


class TestIndexUsesFilter(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = load_config()
        self.cfg["paths"]["data_dir"] = str(self.tmp)
        self.cfg["chunk"]["min_chars"] = 5
        self.cfg["knowledge_graph"]["extract_relations"] = False
        self.vd = video_dir(self.cfg, "v1")
        write_json(self.vd / "meta.json", {"video_id": "v1", "title": "T", "duration": 400, "url": "u"})
        write_json(self.vd / "transcript.json", {"segments": [
            {"start": 0, "end": 40, "text": "mở đầu bài học về cơ sở dữ liệu"},
            {"start": 100, "end": 140, "text": "phần kiến trúc vật lý của hệ thống"},
        ]})
        write_json(self.vd / "screen_text.json", [
            _row("WECOMMIT.COM.VN TRANQUOCHUY", 0, 390, 705),
            _row("Kiến trúc vật lý của PostgreSQL", 100, 110, 360),
        ])
        write_json(self.vd / "ocr_info.json", {"frames": [{"t": t} for t in FRAMES], "frame_size": [1280, 720]})
        write_json(self.vd / "captions.json", [{"start": 120, "end": 120, "text": "Sơ đồ ba tầng", "type": "caption"}])

    def _chunk_text(self):
        with redirect_stdout(StringIO()) as out:
            index_mod.build(self.cfg, FakeEmbedder())
        return " ".join(c["text"] for c in read_json(self.tmp / "index" / "chunks.json")), out.getvalue()

    def test_index_drops_watermark_but_keeps_slide_text_and_captions(self):
        text, log = self._chunk_text()
        self.assertNotIn("WECOMMIT", text)
        self.assertIn("Kiến trúc vật lý của PostgreSQL", text)
        self.assertIn("Sơ đồ ba tầng", text)
        self.assertIn("OCR giữ 1/2 dòng", log)
        self.assertEqual(len(read_json(self.vd / "screen_text.json")), 2, "file gốc không bị sửa")

    def test_filter_can_be_disabled_in_config(self):
        self.cfg["chunk"]["screen_text_filter"] = {"enabled": False}
        text, _ = self._chunk_text()
        self.assertIn("WECOMMIT", text)


if __name__ == "__main__":
    unittest.main()
