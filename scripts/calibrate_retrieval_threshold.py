"""Báo cáo phân bố điểm vector và đề xuất một ngưỡng ưu tiên recall."""

import argparse
import json
import math
import statistics
from pathlib import Path

import numpy as np

from vskr.config import load_config
from vskr.retrieval.embedder import Embedder
from vskr.evaluation import matches_reference
from vskr.retrieval.index import load


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("At least one score is required.")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=Path("data/eval/questions.jsonl"))
    args = parser.parse_args()
    if not args.questions.is_file():
        parser.error(f"Question file not found: {args.questions}")
    questions = [
        json.loads(line)
        for line in args.questions.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not questions:
        parser.error("Question file has no records.")

    cfg = load_config()
    index = load(cfg)
    embedder = Embedder(cfg["embedding"])
    try:
        positive_best_scores = []
        corpus_best_scores = []
        missing_references = []
        for question in questions:
            scores = index.vectors @ embedder.encode_query(question["question"])
            corpus_best_scores.append(float(np.max(scores)))
            matching_scores = [
                float(scores[position])
                for position, chunk in enumerate(index.chunks)
                if any(matches_reference(chunk, ref) for ref in question["relevant"])
            ]
            if not matching_scores:
                missing_references.append(question.get("id", question["question"]))
            else:
                positive_best_scores.append(max(matching_scores))
    finally:
        embedder.close()

    if missing_references:
        parser.error(
            "Ground-truth references did not match indexed chunks for: "
            + ", ".join(map(str, missing_references[:10]))
        )
    candidate = percentile(positive_best_scores, 0.05)
    print(json.dumps({
        "question_count": len(questions),
        "positive_reference_score_quantiles": {
            "min": min(positive_best_scores),
            "p05": candidate,
            "median": statistics.median(positive_best_scores),
            "p95": percentile(positive_best_scores, 0.95),
            "max": max(positive_best_scores),
        },
        "top_corpus_score_quantiles": {
            "min": min(corpus_best_scores),
            "p05": percentile(corpus_best_scores, 0.05),
            "median": statistics.median(corpus_best_scores),
            "p95": percentile(corpus_best_scores, 0.95),
            "max": max(corpus_best_scores),
        },
        "recall_oriented_candidate_min_vector_score": candidate,
        "warning": (
            "Candidate is the 5th percentile of best known-relevant scores. "
            "This dataset has no verified unanswerable queries and cannot estimate false accepts; "
            "do not enable the threshold based on this report alone."
        ),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
