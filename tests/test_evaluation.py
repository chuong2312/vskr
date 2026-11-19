import unittest

from vskr.evaluation import matches_reference, score_results, validate_question


class TestRetrievalEvaluation(unittest.TestCase):
    def test_matches_video_time_overlap_and_document_section(self):
        self.assertTrue(matches_reference(
            {"video_id": "v1", "start": 10, "end": 30},
            {"video_id": "v1", "start": 25, "end": 40},
        ))
        self.assertFalse(matches_reference(
            {"video_id": "v1", "start": 10, "end": 20},
            {"video_id": "v2", "start": 10, "end": 20},
        ))
        self.assertTrue(matches_reference(
            {"document_id": "d1", "section": 2}, {"document_id": "d1", "section": 2}
        ))

    def test_fact_and_multihop_recall_and_mrr(self):
        questions = [
            {"id": "fact", "type": "fact", "question": "q1",
             "relevant": [{"video_id": "v1", "start": 10, "end": 20}]},
            {"id": "multi", "type": "multihop", "question": "q2", "relevant": [
                {"document_id": "d1", "section": 1},
                {"video_id": "v2", "start": 30, "end": 40},
            ]},
        ]
        results = {"hybrid": {
            "fact": [
                {"chunk": {"video_id": "other", "start": 10, "end": 20}},
                {"chunk": {"video_id": "v1", "start": 15, "end": 25}},
            ],
            "multi": [
                {"chunk": {"document_id": "d1", "section": 1}},
                {"chunk": {"video_id": "v2", "start": 35, "end": 45}},
            ],
        }}
        scored = score_results(questions, results)
        self.assertEqual(scored["summary"]["hybrid"]["fact"]["recall_at_5"], 1.0)
        self.assertEqual(scored["summary"]["hybrid"]["fact"]["mrr"], 0.5)
        self.assertEqual(scored["summary"]["hybrid"]["multihop"]["recall_at_5"], 1.0)
        self.assertEqual(scored["summary"]["hybrid"]["multihop"]["mrr"], 1.0)

    def test_scores_are_split_by_evidence(self):
        ref = [{"video_id": "v1", "start": 10, "end": 20}]
        questions = [
            {"id": "s1", "type": "fact", "question": "a", "evidence": "spoken", "relevant": ref},
            {"id": "s2", "type": "fact", "question": "b", "evidence": "spoken", "relevant": ref},
            {"id": "o1", "type": "fact", "question": "c", "evidence": "screen", "relevant": ref},
            {"id": "x1", "type": "fact", "question": "d", "relevant": ref},
        ]
        hit = [{"chunk": {"video_id": "v1", "start": 12, "end": 18}}]
        miss = [{"chunk": {"video_id": "v1", "start": 50, "end": 60}}]
        results = {"bm25": {"s1": hit, "s2": miss, "o1": miss, "x1": hit}}
        scored = score_results(questions, results)
        groups = scored["by_evidence"]["bm25"]
        self.assertEqual(groups["spoken"], {"recall_at_5": 0.5, "mrr": 0.5, "questions": 2})
        self.assertEqual(groups["screen"]["recall_at_5"], 0.0)
        self.assertEqual(groups["unspecified"]["questions"], 1)
        self.assertEqual(scored["summary"]["bm25"]["all"]["questions"], 4)
        self.assertEqual([d["evidence"] for d in scored["details"]], ["spoken", "spoken", "screen", "unspecified"])


class TestValidateQuestion(unittest.TestCase):
    BASE = {"id": "q1", "question": " Hỏi gì? ", "type": "fact",
            "relevant": [{"video_id": "v1", "start": "1.5", "end": 9}]}

    def test_keeps_known_fields_and_drops_the_rest(self):
        item = validate_question({**self.BASE, "evidence": "screen", "answer": "bỏ đi", "note": 1})
        self.assertEqual(item, {"id": "q1", "question": "Hỏi gì?", "type": "fact", "evidence": "screen",
                                "relevant": [{"video_id": "v1", "start": 1.5, "end": 9.0}]})

    def test_evidence_is_optional_and_not_added_when_missing(self):
        # Không thêm khóa evidence cho câu cũ, để dấu vân tay của bộ câu hỏi không đổi.
        self.assertNotIn("evidence", validate_question(self.BASE))

    def test_rejects_unknown_evidence(self):
        with self.assertRaisesRegex(ValueError, "evidence"):
            validate_question({**self.BASE, "evidence": "slide"})

    def test_rejects_bad_references(self):
        for refs in ([], [{"video_id": "v1", "start": 5, "end": 1}], [{"foo": 1}],
                     [{"document_id": "d1", "section": 0}]):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                validate_question({**self.BASE, "relevant": refs})

    def test_api_wrapper_maps_errors_to_422(self):
        from fastapi import HTTPException

        from vskr.api.handlers.evaluation import _validate_eval_question

        with self.assertRaises(HTTPException) as ctx:
            _validate_eval_question({**self.BASE, "type": "other"})
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("fact hoặc multihop", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
