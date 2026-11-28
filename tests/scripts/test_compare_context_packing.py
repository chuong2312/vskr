"""scripts/eval/compare_context_packing.py: hỏi đủ hai cách, câu có prompt giống nhau chỉ hỏi 1 lần, không gọi Ollama."""
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts.eval import compare_context_packing as compare


class FakeService:
    """Service giả: trả lời theo cách thu gọn đang đặt trong cfg và đếm số lần được hỏi."""

    def __init__(self, not_found=()):
        self.cfg = {"llm": {}}
        self.index = SimpleNamespace(bm25=SimpleNamespace(idf={}))
        self.retriever = SimpleNamespace(search=lambda question, mode: [])
        self.calls = []
        self.not_found = set(not_found)

    def ask(self, question, admin=False):
        mode = self.cfg["llm"]["context_packing"]
        self.calls.append((question, mode))
        found = (question, mode) not in self.not_found
        return {"answer": f"{mode}: {question}", "found": found,
                "sources": [{"n": 1, "video_id": "v", "start": 10.0, "end": 40.0, "text": "đoạn nguồn"}]}


QUESTIONS = [
    {"id": "q1", "question": "câu một", "relevant": [{"video_id": "v", "start": 0, "end": 30}]},
    {"id": "q2", "question": "câu hai", "relevant": [{"video_id": "v", "start": 100, "end": 130}], "evidence": "screen"},
]


def fake_prompts(question, hits, *, num_ctx, packing, term_weights):
    # q1: hai cách cho prompt giống nhau; q2: khác nhau.
    content = question if question == "câu một" else f"{packing}|{question}"
    return [{"role": "system", "content": ""}, {"role": "user", "content": content}]


class TestCompareContextPacking(unittest.TestCase):
    def _collect(self, service):
        with mock.patch.object(compare, "build_messages", side_effect=fake_prompts):
            return compare.collect(service, QUESTIONS, 4096, log=lambda line: None)

    def test_same_prompt_is_asked_once_and_different_prompt_twice(self):
        service = FakeService()
        rows = self._collect(service)
        self.assertEqual(service.calls, [("câu một", "extract"), ("câu hai", "extract"), ("câu hai", "prefix")])
        self.assertEqual([r["same_prompt"] for r in rows], [True, False])
        self.assertIs(rows[0]["prefix"], rows[0]["extract"])
        self.assertEqual(rows[1]["prefix"]["answer"], "prefix: câu hai")

    def test_sources_are_checked_against_the_reference(self):
        rows = self._collect(FakeService())
        self.assertTrue(rows[0]["extract"]["sources"][0]["matches_reference"])
        self.assertFalse(rows[1]["extract"]["sources"][0]["matches_reference"])

    def test_review_page_only_lists_changed_questions(self):
        rows = self._collect(FakeService(not_found={("câu hai", "prefix")}))
        page = compare.build_review_page(rows, {"q2": "đáp án hai"}, "qwen3.5:4b", "2026-11-28 10:00")
        self.assertIn("### q2 · screen", page)
        self.assertNotIn("### q1", page)
        self.assertIn("**Đáp án mong đợi:** đáp án hai", page)
        self.assertIn("1/2 câu có prompt khác nhau", page)
        self.assertIn("cũ 1, mới 0", page)
        self.assertIn("- [ ] Trả lời đúng", page)
        self.assertIn("> prefix: câu hai", page)


if __name__ == "__main__":
    unittest.main()
