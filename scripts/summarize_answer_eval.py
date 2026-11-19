"""Validate human answer-review JSONL and summarize its three quality labels."""

import argparse
import json
import math
from pathlib import Path


_LABELS = ("answer_correct", "grounded", "citation_correct")


def read_jsonl(path):
    records = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
    return records


def validate_ground_truth(records):
    identifiers = []
    for position, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise ValueError(f"Ground-truth record {position} must be an object.")
        if not isinstance(record.get("id"), str) or not record["id"].strip():
            raise ValueError(f"Ground-truth record {position} needs a non-empty id.")
        if not isinstance(record.get("question"), str) or not record["question"].strip():
            raise ValueError(f"{record['id']}: question must be a non-empty string.")
        if not isinstance(record.get("expected_answer"), str) or not record["expected_answer"].strip():
            raise ValueError(f"{record['id']}: expected_answer must be a non-empty string.")
        references = record.get("relevant")
        if not isinstance(references, list) or not references:
            raise ValueError(f"{record['id']}: relevant must be a non-empty array.")
        for reference in references:
            if not isinstance(reference, dict) or (
                bool(reference.get("video_id")) == bool(reference.get("document_id"))
            ):
                raise ValueError(f"{record['id']}: each relevant item needs video_id or document_id.")
            if reference.get("video_id"):
                start, end = reference.get("start"), reference.get("end")
                if (
                    not isinstance(start, (int, float))
                    or isinstance(start, bool)
                    or not isinstance(end, (int, float))
                    or isinstance(end, bool)
                    or not math.isfinite(start)
                    or not math.isfinite(end)
                    or start < 0
                    or end < start
                ):
                    raise ValueError(f"{record['id']}: video references need valid start/end times.")
            elif (
                not isinstance(reference.get("section"), int)
                or isinstance(reference.get("section"), bool)
                or reference["section"] < 1
            ):
                raise ValueError(f"{record['id']}: document references need a positive section.")
        identifiers.append(record["id"])
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Ground-truth ids must be unique.")
    if not identifiers:
        raise ValueError("Ground-truth file has no records.")
    return identifiers


def validate_reviews(reviews, ground_truth_ids):
    identifiers = []
    expected_ids = set(ground_truth_ids)
    for position, review in enumerate(reviews, 1):
        if not isinstance(review, dict):
            raise ValueError(f"Review record {position} must be an object.")
        identifier = review.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"Review record {position} needs a non-empty id.")
        for label in _LABELS:
            if not isinstance(review.get(label), bool):
                raise ValueError(f"{identifier}: {label} must be a boolean.")
        identifiers.append(identifier)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Review ids must be unique.")
    missing = expected_ids - set(identifiers)
    extra = set(identifiers) - expected_ids
    if missing or extra:
        raise ValueError(
            f"Review ids must match ground truth (missing: {sorted(missing)}, extra: {sorted(extra)})."
        )
    return reviews


def summarize_reviews(reviews):
    if not reviews:
        raise ValueError("At least one human review is required.")
    return {
        "records": len(reviews),
        **{
            f"{label}_rate": sum(review[label] for review in reviews) / len(reviews)
            for label in _LABELS
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--reviews", required=True, type=Path)
    args = parser.parse_args()
    try:
        ground_truth_ids = validate_ground_truth(read_jsonl(args.ground_truth))
        reviews = validate_reviews(read_jsonl(args.reviews), ground_truth_ids)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(summarize_reviews(reviews), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
