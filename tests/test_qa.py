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


def _source_ids(user_prompt):
    import re
    return [int(n) for n in re.findall(r'<source id="(\d+)">', user_prompt)]


def _budget(num_ctx=4096):
    return (num_ctx - min(512, max(32, num_ctx // 4))) * 2


class TestContextPacking(unittest.TestCase):
    """Thu gọn nguồn cho vừa num_ctx: extract giữ chỗ cho mọi nguồn, prefix là cách cũ."""

    def _long_hits(self, count=5):
        return [{"chunk": {"text": f"đoạn{i} " + "chữ lấp đầy " * 600}} for i in range(1, count + 1)]

    def test_short_sources_are_sent_unchanged(self):
        hits = [{"chunk": {"text": "nguồn một"}}, {"chunk": {"text": "nguồn hai"}}]
        user = build_messages("hỏi?", hits)[1]["content"]
        self.assertIn("nguồn một", user)
        self.assertIn("nguồn hai", user)
        self.assertNotIn("…", user)

    def test_every_long_source_keeps_a_place_within_budget(self):
        messages = build_messages("câu hỏi?", self._long_hits())
        self.assertEqual(_source_ids(messages[1]["content"]), [1, 2, 3, 4, 5])
        self.assertLessEqual(sum(len(m["content"]) for m in messages), _budget())

    def test_prefix_mode_keeps_old_behaviour(self):
        # Cách cũ: nguồn đầu chiếm hết chỗ, các nguồn cuối bị bỏ hẳn.
        user = build_messages("câu hỏi?", self._long_hits(), packing="prefix")[1]["content"]
        self.assertNotIn('<source id="5">', user)
        self.assertIn("[đã cắt]", user)

    def test_unknown_packing_is_rejected(self):
        with self.assertRaises(ValueError):
            build_messages("hỏi?", [{"chunk": {"text": "a"}}], packing="khac")

    def test_keeps_the_screen_text_that_matches_the_question(self):
        # Giống câu os-s4: đáp án nằm giữa phần chữ trên màn hình của một nguồn dài.
        screen = " | ".join(["Menu File Edit"] * 40
                            + ["Registry Editor", "HKEY_CURRENT_CONFIG Software Fonts", "New Value #1 REG_DWORD"]
                            + ["Help About"] * 40)
        target = {"chunk": {"text": "giảng viên nói chuyện khác " * 200 + "\n[Trên màn hình] " + screen}}
        hits = [{"chunk": {"text": "nội dung lấp " * 400}} for _ in range(3)] + [target]
        user = build_messages("Trong demo Registry Editor, giá trị mới có kiểu gì?", hits)[1]["content"]
        self.assertEqual(_source_ids(user), [1, 2, 3, 4])
        self.assertIn("HKEY_CURRENT_CONFIG", user)
        self.assertIn("REG_DWORD", user)
        self.assertIn("[Trên màn hình] …", user)

    def test_index_idf_decides_which_piece_is_kept(self):
        # Ba mẩu 30 từ, chỉ đủ chỗ cho một mẩu: mẩu chứa từ có IDF cao hơn được giữ.
        text = " ".join(["đỏ"] + ["chữ"] * 29 + ["lấp"] * 30 + ["xanh"] + ["chữ"] * 29)
        kept, shortened = qa._extract_relevant(text, {"đỏ", "xanh"}, {"xanh": 5.0, "đỏ": 0.01}, 140)
        self.assertTrue(shortened)
        self.assertIn("xanh", kept)
        self.assertNotIn("đỏ", kept)
        kept, _ = qa._extract_relevant(text, {"đỏ", "xanh"}, {"xanh": 0.01, "đỏ": 5.0}, 140)
        self.assertIn("đỏ", kept)
        self.assertNotIn("xanh", kept)

    def test_answer_question_reads_packing_from_config(self):
        captured = {}

        def fake_call(_lcfg, messages):
            captured["user"] = messages[1]["content"]
            return "Trả lời [1]."

        with mock.patch.object(qa, "call_ollama", side_effect=fake_call):
            qa.answer_question("câu hỏi?", self._long_hits(), {"num_ctx": 4096, "context_packing": "prefix"})
        self.assertNotIn('<source id="5">', captured["user"])
        with mock.patch.object(qa, "call_ollama", side_effect=fake_call):
            qa.answer_question("câu hỏi?", self._long_hits(), {"num_ctx": 4096})
        self.assertEqual(_source_ids(captured["user"]), [1, 2, 3, 4, 5])


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
