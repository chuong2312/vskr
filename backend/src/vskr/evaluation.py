"""Đánh giá retrieval trên bộ câu hỏi và nguồn đúng do người dùng cung cấp."""
from collections import defaultdict


def matches_reference(chunk, reference):
    if reference.get("document_id"):
        return (chunk.get("document_id") == reference["document_id"]
                and chunk.get("section") == reference.get("section"))
    if reference.get("video_id"):
        if chunk.get("video_id") != reference["video_id"]:
            return False
        start, end = chunk.get("start"), chunk.get("end")
        ref_start, ref_end = reference.get("start"), reference.get("end")
        return (start is not None and end is not None and ref_start is not None and ref_end is not None
                and start <= ref_end and ref_start <= end)
    return False


def score_results(questions, results_by_mode, k=5):
    """Tính Recall@k theo tỷ lệ mục đúng được phủ, và MRR theo thứ hạng đúng đầu tiên."""
    detail = []
    grouped = defaultdict(lambda: defaultdict(list))
    for question in questions:
        refs = question["relevant"]
        q_detail = {"id": question["id"], "type": question["type"], "question": question["question"], "methods": {}}
        for mode, result_map in results_by_mode.items():
            hits = result_map[question["id"]][:k]
            covered = {i for i, ref in enumerate(refs) if any(matches_reference(hit["chunk"], ref) for hit in hits)}
            reciprocal_rank = 0.0
            for rank, hit in enumerate(hits, start=1):
                if any(matches_reference(hit["chunk"], ref) for ref in refs):
                    reciprocal_rank = 1.0 / rank
                    break
            recall = (1.0 if covered else 0.0) if question["type"] == "fact" else (len(covered) / len(refs) if refs else 0.0)
            metrics = {"recall_at_5": recall, "mrr": reciprocal_rank, "covered": len(covered), "relevant": len(refs)}
            q_detail["methods"][mode] = metrics
            grouped[mode][question["type"]].append(metrics)
        detail.append(q_detail)
    summary = {}
    for mode, by_type in grouped.items():
        summary[mode] = {}
        for qtype, records in by_type.items():
            summary[mode][qtype] = {
                "recall_at_5": sum(r["recall_at_5"] for r in records) / len(records),
                "mrr": sum(r["mrr"] for r in records) / len(records),
                "questions": len(records),
            }
        all_records = [r for records in by_type.values() for r in records]
        summary[mode]["all"] = {
            "recall_at_5": sum(r["recall_at_5"] for r in all_records) / len(all_records),
            "mrr": sum(r["mrr"] for r in all_records) / len(all_records),
            "questions": len(all_records),
        }
    return {"summary": summary, "details": detail, "k": k}
