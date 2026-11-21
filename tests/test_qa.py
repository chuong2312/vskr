"""Phần hỏi đáp vskr.qa: trích dẫn, nhận ra câu "không tìm thấy", dựng prompt và nhả model Ollama."""
import sys
import types
import unittest
from unittest import mock

from vskr import qa
from vskr.qa import NOT_FOUND, build_messages, extract_citations, is_not_found, strip_think


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

    def test_prompt_tells_comparisons_to_stay_with_source_topics(self):
        hits = [{"chunk": {"text": "Nguồn chỉ nói về K-means."}}]
        messages = build_messages("K-means khác DBSCAN ở điểm nào?", hits)
        self.assertIn("Nếu một đối tượng được hỏi không xuất hiện trong nguồn, nói rõ điều đó và không thay bằng đối tượng khác.", messages[0]["content"])


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
