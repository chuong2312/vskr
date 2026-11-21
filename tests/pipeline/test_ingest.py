"""Nạp video vskr.pipeline.ingest: tìm lại file nguồn khi đường dẫn đổi và giới hạn thời lượng."""
import shutil
import tempfile
import unittest
from pathlib import Path

from vskr.pipeline.ingest import check_duration, resolve_source


class TestResolveSource(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "source.mp4").write_bytes(b"x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_relative_name(self):
        self.assertEqual(resolve_source(self.tmp, {"source_file": "source.mp4"}), self.tmp / "source.mp4")

    def test_old_absolute_path_from_another_machine(self):
        # meta.json tạo trên máy khác: đường dẫn tuyệt đối cũ không còn, vẫn khôi phục bằng tên file.
        meta = {"source_file": "/home/someone/vskr/data/videos/vid1/source.mp4"}
        self.assertEqual(resolve_source(self.tmp, meta), self.tmp / "source.mp4")

    def test_missing(self):
        self.assertIsNone(resolve_source(self.tmp, {"source_file": "nope.mp4"}))
        self.assertIsNone(resolve_source(self.tmp, {}))


class TestIngestDurationLimit(unittest.TestCase):
    def test_accepts_limit_and_shorter(self):
        cfg = {"ingest": {"max_duration_s": 1800}}
        self.assertIsNone(check_duration(1800, cfg))
        self.assertIsNone(check_duration(45, cfg))

    def test_rejects_long_video_with_readable_limit(self):
        with self.assertRaisesRegex(ValueError, "giới hạn hiện tại là 30 phút"):
            check_duration(1801, {"ingest": {"max_duration_s": 1800}})


if __name__ == "__main__":
    unittest.main()
