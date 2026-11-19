"""Quản lý bộ câu hỏi đánh giá (``data/eval/questions.jsonl``) và chạy đánh giá truy hồi cho trang admin.

Việc chấm điểm (Recall@k, MRR) nằm ở :mod:`vskr.evaluation`; module này chỉ đọc/ghi câu hỏi và
chạy truy hồi cho từng chế độ.
"""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ...config import data_dir, load_config
from ...evaluation import score_results, validate_question
from ...util import read_json, write_json
from .runtime import _acquire_service, _init_service, _release_service


class RetrievalExperiment(BaseModel):
    """Tuỳ chọn thử nghiệm cho truy vấn graph khi chạy đánh giá: trọng số IDF, mở rộng theo nghĩa, PPR."""
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


def _questions_fingerprint(questions):
    canonical = json.dumps(questions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_eval_question(raw):
    try:
        return validate_question(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def get_eval_questions():
    """Đọc bộ câu hỏi đánh giá từ ``questions.jsonl``."""
    return _read_questions()


def get_eval_results():
    """Kết quả lần đánh giá gần nhất (``results.json``) kèm cờ ``stale`` nếu bộ câu hỏi đã đổi; chưa có thì None."""
    path = _eval_dir() / "results.json"
    if not path.is_file():
        return None
    result = read_json(path)
    if not isinstance(result, dict):
        return None
    questions = _read_questions()
    result["stale"] = (
        result.get("question_fingerprint") != _questions_fingerprint(questions)
        or result.get("question_count") != len(questions)
    )
    return result


def save_eval_questions(payload: dict):
    """Thêm một hoặc nhiều câu hỏi đã kiểm tra định dạng (trùng id thì 409), ghi lại ``questions.jsonl`` và xoá kết quả cũ."""
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
    """Đọc câu hỏi từ tệp JSONL tải lên (tối đa ``limits.eval_import_bytes``) rồi lưu như ``save_eval_questions``."""
    limit = int(load_config()["limits"]["eval_import_bytes"])
    content = await file.read(limit + 1)
    await file.close()
    if len(content) > limit:
        raise HTTPException(413, f"Tệp JSONL vượt giới hạn {limit // (1024 * 1024)} MB.")
    try:
        raw_items = [json.loads(line) for line in content.decode("utf-8-sig").splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"Không đọc được JSONL: {exc}") from exc
    return save_eval_questions({"questions": raw_items})


def delete_eval_question(question_id: str):
    """Xoá một câu hỏi và kết quả đánh giá cũ; không có câu đó thì 404."""
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
    """Chạy truy hồi cho mọi câu hỏi ở cả 5 chế độ (thêm các biến thể graph nếu bật thử nghiệm).

    Chấm Recall@5 và MRR, lưu vào ``results.json`` rồi trả về kết quả.
    """
    questions = _read_questions()
    if not questions:
        raise HTTPException(409, "Chưa có câu hỏi đánh giá. Hãy thêm câu hỏi và nguồn đúng trước.")
    modes = ("vector", "bm25", "hybrid", "graph", "hybrid_graph")
    hits = {mode: {} for mode in modes}
    method_labels = {
        "hybrid_graph": "Kết hợp + đồ thị",
        "hybrid": "Ngữ nghĩa + từ khóa (mặc định)",
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
    service = _acquire_service()
    if service is None:
        _init_service()
        service = _acquire_service()
    if service is None:
        raise HTTPException(503, "Dịch vụ chưa sẵn sàng. Hãy kiểm tra nhật ký máy chủ.")
    try:
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
        retrieval_config = dict(service.cfg["retrieval"])
    finally:
        _release_service(service)
    scored = score_results(questions, hits, k=5)
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "question_count": len(questions),
        "question_fingerprint": _questions_fingerprint(questions),
        "retrieval_config": {
            **retrieval_config, "modes": list(hits), "k": 5,
            "experiments": {**selected, "damping": options.damping, "seed_weight": options.seed_weight},
        },
        "method_labels": method_labels,
        **scored,
    }
    write_json(_eval_dir() / "results.json", result)
    return result
