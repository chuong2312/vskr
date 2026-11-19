"""Đánh giá retrieval trên bộ câu hỏi và nguồn đúng do người dùng cung cấp."""
import uuid
from collections import defaultdict

# Đáp án của câu hỏi nằm ở đâu trong video: lời giảng (spoken), chữ trên màn hình mà giảng viên
# không đọc ra (screen), hay chỉ có trong sơ đồ/hình (figure). Dùng để tách điểm theo nguồn, ví dụ
# đo OCR giúp được bao nhiêu cho câu hỏi "screen".
EVIDENCE_TYPES = ("spoken", "screen", "figure")


def validate_question(raw):
    """Chuẩn hóa một câu hỏi đánh giá; ném ValueError kèm thông báo tiếng Việt nếu sai định dạng.

    Chỉ giữ các trường id, question, type, relevant và evidence (nếu có). Trường khác bị bỏ.
    """
    if not isinstance(raw, dict):
        raise ValueError("Mỗi dòng câu hỏi cần là một object JSON.")
    item = dict(raw)
    item["id"] = str(item.get("id") or uuid.uuid4().hex[:12])
    item["question"] = str(item.get("question") or "").strip()
    if not item["question"]:
        raise ValueError("Câu hỏi đánh giá không được để trống.")
    if item.get("type") not in ("fact", "multihop"):
        raise ValueError("type phải là fact hoặc multihop.")
    evidence = item.get("evidence")
    if evidence is not None and evidence not in EVIDENCE_TYPES:
        raise ValueError("evidence phải là spoken, screen hoặc figure.")
    refs = item.get("relevant")
    if not isinstance(refs, list) or not refs:
        raise ValueError("Mỗi câu hỏi cần ít nhất một mục nguồn đúng trong relevant.")
    clean_refs = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError("Mỗi mục relevant phải là một object.")
        if ref.get("document_id"):
            try:
                section = int(ref["section"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Nguồn tài liệu cần document_id và section.") from exc
            if section < 1:
                raise ValueError("section phải bắt đầu từ 1.")
            clean_refs.append({"document_id": str(ref["document_id"]), "section": section})
        elif ref.get("video_id"):
            try:
                start, end = float(ref["start"]), float(ref["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Nguồn video cần video_id, start và end.") from exc
            if start < 0 or end < start:
                raise ValueError("Khoảng thời gian video không hợp lệ.")
            clean_refs.append({"video_id": str(ref["video_id"]), "start": start, "end": end})
        else:
            raise ValueError("Nguồn đúng cần video_id hoặc document_id.")
    clean = {"id": item["id"], "question": item["question"], "type": item["type"], "relevant": clean_refs}
    if evidence is not None:
        clean["evidence"] = evidence
    return clean


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


def _mean(records):
    return {
        "recall_at_5": sum(r["recall_at_5"] for r in records) / len(records),
        "mrr": sum(r["mrr"] for r in records) / len(records),
        "questions": len(records),
    }


def score_results(questions, results_by_mode, k=5):
    """Tính Recall@k theo tỷ lệ mục đúng được phủ, và MRR theo thứ hạng đúng đầu tiên.

    summary[mode] chia theo type (fact/multihop/all); by_evidence[mode] chia theo nơi chứa đáp án,
    câu chưa gắn evidence nằm ở nhóm "unspecified".
    """
    detail = []
    grouped = defaultdict(lambda: defaultdict(list))
    by_evidence_records = defaultdict(lambda: defaultdict(list))
    for question in questions:
        refs = question["relevant"]
        evidence = question.get("evidence") or "unspecified"
        q_detail = {"id": question["id"], "type": question["type"], "question": question["question"],
                    "evidence": evidence, "methods": {}}
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
            by_evidence_records[mode][evidence].append(metrics)
        detail.append(q_detail)
    summary = {}
    for mode, by_type in grouped.items():
        summary[mode] = {qtype: _mean(records) for qtype, records in by_type.items()}
        summary[mode]["all"] = _mean([r for records in by_type.values() for r in records])
    by_evidence = {
        mode: {evidence: _mean(records) for evidence, records in groups.items()}
        for mode, groups in by_evidence_records.items()
    }
    return {"summary": summary, "by_evidence": by_evidence, "details": detail, "k": k}
