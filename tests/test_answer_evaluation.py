import unittest

from scripts.eval.calibrate_retrieval_threshold import percentile
from scripts.eval.summarize_answer_eval import (
    summarize_reviews,
    validate_ground_truth,
    validate_reviews,
)


class TestAnswerEvaluation(unittest.TestCase):
    def test_retrieval_threshold_candidate_uses_interpolated_percentile(self):
        self.assertEqual(percentile([0.0, 1.0], 0.05), 0.05)

    def test_retrieval_threshold_candidate_rejects_empty_scores(self):
        with self.assertRaisesRegex(ValueError, "At least one score"):
            percentile([], 0.05)

    def test_summary_uses_human_judgments_without_claiming_automatic_scoring(self):
        reviews = [
            {"id": "q1", "answer_correct": True, "grounded": True, "citation_correct": True},
            {"id": "q2", "answer_correct": False, "grounded": True, "citation_correct": False},
        ]

        self.assertEqual(summarize_reviews(reviews), {
            "records": 2,
            "answer_correct_rate": 0.5,
            "grounded_rate": 1.0,
            "citation_correct_rate": 0.5,
        })

    def test_reviews_must_cover_exactly_the_ground_truth_ids(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_reviews(
                [{"id": "q1", "answer_correct": True, "grounded": True, "citation_correct": True}],
                ["q1", "q2"],
            )

    def test_ground_truth_requires_a_valid_source_reference(self):
        record = {
            "id": "q1",
            "question": "Question",
            "expected_answer": "Expected answer",
            "relevant": [{"video_id": "video-a", "start": 2, "end": 4}],
        }
        self.assertEqual(validate_ground_truth([record]), ["q1"])

        record["relevant"] = [{"video_id": "video-a", "document_id": "doc-a"}]
        with self.assertRaisesRegex(ValueError, "video_id or document_id"):
            validate_ground_truth([record])

    def test_review_boolean_fields_reject_ambiguous_values(self):
        with self.assertRaisesRegex(ValueError, "boolean"):
            validate_reviews(
                [{"id": "q1", "answer_correct": "yes", "grounded": True, "citation_correct": True}],
                ["q1"],
            )


if __name__ == "__main__":
    unittest.main()
