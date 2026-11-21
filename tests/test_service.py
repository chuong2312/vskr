"""Service từ chối chỉ mục được dựng bằng model nhúng khác với cấu hình hiện tại."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vskr.config import load_config


class TestIndexEmbeddingMismatch(unittest.TestCase):
    def test_service_refuses_stale_index(self):
        from tests.test_integration import FakeEmbedder
        from vskr.retrieval import index as index_mod
        from vskr.config import video_dir
        from vskr.service import Service
        from vskr.util import write_json

        tmp = Path(tempfile.mkdtemp())
        try:
            cfg = load_config()
            cfg["paths"]["data_dir"] = str(tmp)
            vd = video_dir(cfg, "v")
            write_json(vd / "meta.json", {"video_id": "v", "title": "T", "duration": 60, "url": "u"})
            write_json(vd / "transcript.json", {"segments": [{"start": 0, "end": 30, "text": "nội dung đủ dài để thành chunk"}]})
            cfg["chunk"]["min_chars"] = 5
            index_mod.build(cfg, FakeEmbedder())
            cfg["embedding"] = dict(cfg["embedding"], model="BAAI/bge-m3")  # đổi model mà chưa index lại
            with mock.patch("vskr.service.Embedder", return_value=FakeEmbedder()):
                with self.assertRaises(RuntimeError) as cm:
                    Service(cfg)
            self.assertIn("index", str(cm.exception))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
