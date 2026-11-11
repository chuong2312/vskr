import unittest

from vskr.evaluation import matches_reference, score_results


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


if __name__ == "__main__":
    unittest.main()
