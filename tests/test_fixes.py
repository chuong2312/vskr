"""Test cho các chỗ đã sửa/thêm: phát hiện 'không tìm thấy', ghi đè cấu hình bằng env,
xuất SRT, tìm file nguồn khi đường dẫn đổi (host <-> container), chỉ mục lệch embedding."""
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from vskr import qa
from vskr.config import load_config
from vskr.ingest import check_duration, resolve_source
from vskr.util import fmt_srt_time, source_link, to_srt


class TestNotFound(unittest.TestCase):
    def test_exact_variants(self):
        self.assertTrue(qa.is_not_found(qa.NOT_FOUND))
        self.assertTrue(qa.is_not_found("  " + qa.NOT_FOUND + "\n"))
        self.assertTrue(qa.is_not_found(qa.NOT_FOUND.rstrip(".")))

    def test_partial_answer_keeps_sources(self):
        # Quy tắc 5 của prompt cho phép trả lời một phần rồi nêu phần còn thiếu: không được coi là 'không tìm thấy'.
        partial = (
            "Biến được khai báo bằng dấu bằng [1]. Về kiểu dữ liệu động: "
            + qa.NOT_FOUND
            + " Phần còn lại chưa có trong các đoạn."
        )
        self.assertFalse(qa.is_not_found(partial))

    def test_normal_answer(self):
        self.assertFalse(qa.is_not_found("Dùng dấu bằng để gán giá trị [1]."))


class TestEnvOverride(unittest.TestCase):
    def test_override_types_and_names(self):
        env = {
            "VSKR__LLM__BASE_URL": "http://ollama:11434",
            "VSKR__RETRIEVAL__TOP_K": "8",
            "VSKR__ASR__UNLOAD_OLLAMA_FIRST": "false",
            "VSKR__RETRIEVAL__MIN_VECTOR_SCORE": "0.8",
            "VSKR__ASR__DEVICE": "cpu",
            "VSKR__PATHS__DATA_DIR": "/app/data",
            "VSKR__BAD": "x",  # thiếu khóa: bị bỏ qua
            "VSKR__A__B__C": "x",  # quá sâu: bị bỏ qua
        }
        with mock.patch.dict(os.environ, env):
            cfg = load_config()
        self.assertEqual(cfg["llm"]["base_url"], "http://ollama:11434")
        self.assertEqual(cfg["retrieval"]["top_k"], 8)
        self.assertIs(cfg["asr"]["unload_ollama_first"], False)
        self.assertAlmostEqual(cfg["retrieval"]["min_vector_score"], 0.8)
        self.assertEqual(cfg["asr"]["device"], "cpu")
        self.assertEqual(cfg["paths"]["data_dir"], "/app/data")
        self.assertNotIn("bad", cfg)
        self.assertNotIn("a", cfg)

    def test_no_env_keeps_yaml(self):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("VSKR__")}
        with mock.patch.dict(os.environ, clean, clear=True):
            cfg = load_config()
        self.assertEqual(cfg["llm"]["base_url"], "http://localhost:11434")


class TestSrt(unittest.TestCase):
    def test_time(self):
        self.assertEqual(fmt_srt_time(3661.5), "01:01:01,500")
        self.assertEqual(fmt_srt_time(0), "00:00:00,000")
        self.assertEqual(fmt_srt_time(32.499), "00:00:32,499")

    def test_blocks_skip_empty_and_renumber(self):
        segs = [
            {"start": 0, "end": 2.5, "text": " Xin chào "},
            {"start": 2.5, "end": 3, "text": "   "},
            {"start": 3, "end": 5, "text": "Biến trong Python"},
        ]
        self.assertEqual(
            to_srt(segs),
            "1\n00:00:00,000 --> 00:00:02,500\nXin chào\n\n2\n00:00:03,000 --> 00:00:05,000\nBiến trong Python\n",
        )


class TestSourceLink(unittest.TestCase):
    def test_youtube_gets_timestamp_without_losing_other_query(self):
        self.assertEqual(
            source_link("https://www.youtube.com/watch?v=abc&list=xyz", "abc", 42.8),
            "https://www.youtube.com/watch?v=abc&list=xyz&t=42s",
        )

    def test_other_platform_keeps_original_url(self):
        url = "https://example.com/watch/abc?token=private"
        self.assertEqual(source_link(url, "abc", 42), url)


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


class TestIndexEmbeddingMismatch(unittest.TestCase):
    def test_service_refuses_stale_index(self):
        from tests.test_integration import FakeEmbedder
        from vskr import index as index_mod
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


class TestUnloadOllama(unittest.TestCase):
    """Dùng httpx giả để kiểm tra đúng yêu cầu gửi đi và việc nuốt lỗi (không phụ thuộc httpx thật hay mạng)."""

    def _fake_httpx(self, post):
        fake = types.ModuleType("httpx")
        fake.post = post
        return mock.patch.dict(sys.modules, {"httpx": fake})

    def test_sends_keep_alive_zero(self):
        calls = []
        with self._fake_httpx(lambda url, json=None, timeout=None: calls.append((url, json))):
            ok = qa.unload_ollama({"base_url": "http://ollama:11434/", "model": "qwen3:8b"})
        self.assertTrue(ok)
        self.assertEqual(calls, [("http://ollama:11434/api/generate", {"model": "qwen3:8b", "keep_alive": 0})])

    def test_swallows_connection_error(self):
        def boom(*a, **k):
            raise ConnectionError("Ollama chưa chạy")

        with self._fake_httpx(boom):
            self.assertFalse(qa.unload_ollama({"base_url": "http://x:1", "model": "m"}))


if __name__ == "__main__":
    unittest.main()
