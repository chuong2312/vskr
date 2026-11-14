"""Evaluation dataset and retrieval benchmark operations."""

import json
import uuid
from datetime import datetime, timezone

from fastapi import File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..config import data_dir, load_config
from ..util import read_json, write_json
from .runtime import _service_use_lock, init_service as _init_service, state


class RetrievalExperiment(BaseModel):
    idf_weighting: bool = False
    semantic_expansion: bool = False
    ppr: bool = False
    damping: float = Field(default=0.5, ge=0.0, le=0.99)
    seed_weight: float = Field(default=0.05, ge=0.0, le=1.0)


def _eval_dir():
    return data_dir(load_config()) / "eval"


def _read_questions():
    path = _eval_dir() / "questions.jsonl"
    if not path.is_file():
        return []
    records = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise HTTPException(500, f"questions.jsonl lỗi ở dòng {number}: {exc.msg}") from exc
    return records


def _validate_eval_question(raw):
    if not isinstance(raw, dict):
        raise HTTPException(422, "Mỗi dòng câu hỏi cần là một object JSON.")
    item = dict(raw)
    item["id"] = str(item.get("id") or uuid.uuid4().hex[:12])
    item["question"] = str(item.get("question") or "").strip()
    if not item["question"]:
        raise HTTPException(422, "Câu hỏi đánh giá không được để trống.")
    if item.get("type") not in ("fact", "multihop"):
        raise HTTPException(422, "type phải là fact hoặc multihop.")
    refs = item.get("relevant")
    if not isinstance(refs, list) or not refs:
        raise HTTPException(422, "Mỗi câu hỏi cần ít nhất một mục nguồn đúng trong relevant.")
    clean_refs = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise HTTPException(422, "Mỗi mục relevant phải là một object.")
        if ref.get("document_id"):
            try:
                section = int(ref["section"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(422, "Nguồn tài liệu cần document_id và section.") from exc
            if section < 1:
                raise HTTPException(422, "section phải bắt đầu từ 1.")
            clean_refs.append({"document_id": str(ref["document_id"]), "section": section})
        elif ref.get("video_id"):
            try:
                start, end = float(ref["start"]), float(ref["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(422, "Nguồn video cần video_id, start và end.") from exc
            if start < 0 or end < start:
                raise HTTPException(422, "Khoảng thời gian video không hợp lệ.")
            clean_refs.append({"video_id": str(ref["video_id"]), "start": start, "end": end})
        else:
            raise HTTPException(422, "Nguồn đúng cần video_id hoặc document_id.")
    return {"id": item["id"], "question": item["question"], "type": item["type"], "relevant": clean_refs}


def get_eval_questions():
    return _read_questions()


def get_eval_results():
    path = _eval_dir() / "results.json"
    return read_json(path) if path.is_file() else None


def save_eval_questions(payload: dict):
    raw_items = payload.get("questions") if isinstance(payload.get("questions"), list) else [payload]
    if not raw_items:
        raise HTTPException(422, "Không có câu hỏi để lưu.")
    existing = _read_questions()
    by_id = {item["id"]: item for item in existing}
    for raw in raw_items:
        item = _validate_eval_question(raw)
        if item["id"] in by_id:
            raise HTTPException(409, f"ID câu hỏi đã tồn tại: {item['id']}.")
        by_id[item["id"]] = item
    path = _eval_dir() / "questions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in by_id.values():
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    (_eval_dir() / "results.json").unlink(missing_ok=True)
    return list(by_id.values())


async def import_eval_questions(file: UploadFile = File(...)):
    content = await file.read(5 * 1024 * 1024 + 1)
    await file.close()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "Tệp JSONL vượt giới hạn 5 MB.")
    try:
        raw_items = [json.loads(line) for line in content.decode("utf-8-sig").splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"Không đọc được JSONL: {exc}") from exc
    return save_eval_questions({"questions": raw_items})


def delete_eval_question(question_id: str):
    items = _read_questions()
    remaining = [item for item in items if item["id"] != question_id]
    if len(remaining) == len(items):
        raise HTTPException(404, "Không tìm thấy câu hỏi đánh giá.")
    path = _eval_dir() / "questions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in remaining:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    (_eval_dir() / "results.json").unlink(missing_ok=True)
    return {"deleted": question_id}


def run_retrieval_evaluation(options: RetrievalExperiment = RetrievalExperiment()):
    questions = _read_questions()
    if not questions:
        raise HTTPException(409, "Chưa có câu hỏi đánh giá. Hãy thêm câu hỏi và nguồn đúng trước.")
    if state["service"] is None:
        _init_service()
    if state["service"] is None:
        raise HTTPException(503, "Dịch vụ chưa sẵn sàng. Hãy kiểm tra nhật ký máy chủ.")
    modes = ("vector", "bm25", "hybrid", "graph", "hybrid_graph")
    hits = {mode: {} for mode in modes}
    method_labels = {
        "hybrid_graph": "Kết hợp + đồ thị (mặc định)",
        "hybrid": "Ngữ nghĩa + từ khóa",
        "vector": "Ngữ nghĩa (vector)",
        "bm25": "Từ khóa (BM25)",
        "graph": "Đồ thị tri thức (graph)",
    }
    selected = {
        "idf_weighting": options.idf_weighting,
        "semantic_expansion": options.semantic_expansion,
        "ppr": options.ppr,
    }
    experiment_variants = []
    experiment_labels = {
        "idf_weighting": ("idf", "Graph tăng trọng số từ hiếm (IDF)"),
        "semantic_expansion": ("e5", "Graph mở rộng khái niệm bằng embedding E5"),
        "ppr": ("ppr", "Graph lan truyền điểm (PPR)"),
    }
    for option_name, enabled in selected.items():
        if enabled:
            key, label = experiment_labels[option_name]
            experiment_variants.append((f"hybrid_graph_{key}", label, {option_name: True}))
    active = {name: True for name, enabled in selected.items() if enabled}
    if len(active) > 1:
        labels = [experiment_labels[name][1] for name in active]
        experiment_variants.append(("hybrid_graph_combined", "Kết hợp thử nghiệm: " + " + ".join(labels), active))
    for key, label, _experiment in experiment_variants:
        method_labels[key] = label
        hits[key] = {}
    with _service_use_lock:
        service = state["service"]
        try:
            for question in questions:
                for mode in modes:
                    hits[mode][question["id"]] = service.retriever.search(question["question"], mode=mode, top_k=5)
                for key, _label, experiment in experiment_variants:
                    params = {**experiment, "damping": options.damping, "seed_weight": options.seed_weight}
                    hits[key][question["id"]] = service.retriever.search(
                        question["question"], mode="hybrid_graph", top_k=5, experiment=params
                    )
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
    from ..evaluation import score_results

    scored = score_results(questions, hits, k=5)
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "question_count": len(questions),
        "retrieval_config": {
            **service.cfg["retrieval"], "modes": list(hits), "k": 5,
            "experiments": {**selected, "damping": options.damping, "seed_weight": options.seed_weight},
        },
        "method_labels": method_labels,
        **scored,
    }
    write_json(_eval_dir() / "results.json", result)
    return result
