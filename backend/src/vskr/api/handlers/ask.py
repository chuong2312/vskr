"""Hỏi đáp: kiểm tra câu hỏi, mượn service dùng chung rồi gọi truy hồi + LLM.

Câu hỏi chỉ nhắc tới ngữ cảnh cũ ("ý trên là gì?") bị từ chối sớm, vì mỗi câu được truy hồi độc lập.
"""
import logging
import re
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .runtime import (
    _acquire_service, _gpu_model_lock, _init_service, _release_service, _safe_diagnostic, state,
)


class AskRequest(BaseModel):
    question: str = Field(max_length=500)
    mode: Optional[str] = None


_CONTEXT_REFERENCE = re.compile(
    r"\b(?:hồi\s+nãy|vừa\s+rồi|ý\s+trên|câu\s+trước|ở\s+trên|như\s+trên|cái\s+đó|nó)\b",
    re.IGNORECASE,
)
_SPECIFIC_TOPIC = re.compile(r"\bvề\s+([\w-]+)", re.IGNORECASE)
_FOLLOWUP_FILLER = {
    "cái", "đó", "nó", "ý", "là", "gì", "sao", "thế", "vậy", "ở", "trên",
    "vừa", "rồi", "hồi", "nãy", "trước", "như", "cho", "mình", "tôi", "bạn",
    "của", "về", "thì", "này", "giúp", "nói", "thầy", "cô", "em", "nghĩa",
    "mốc", "thời", "gian",
}
_FOLLOWUP_FILLER_PHRASES = re.compile(r"\b(?:nghĩa\s+là\s+gì|mốc\s+thời\s+gian)\b", re.IGNORECASE)
_GENERIC_TOPICS = {"cái", "đó", "nó", "này", "điều", "ý", "phần"}


def _is_contextless_followup(question: str) -> bool:
    normalized = re.sub(r"[^\w\s-]", " ", question.casefold())
    if not _CONTEXT_REFERENCE.search(normalized):
        return False
    topic = _SPECIFIC_TOPIC.search(normalized)
    if topic and topic.group(1) not in _GENERIC_TOPICS:
        return False
    remaining = _CONTEXT_REFERENCE.sub(" ", normalized)
    remaining = _FOLLOWUP_FILLER_PHRASES.sub(" ", remaining)
    words = [word for word in re.findall(r"\w+", remaining) if word not in _FOLLOWUP_FILLER]
    return len(words) <= 3


GPU_LOCK_TIMEOUT_S = 10


def ask(req: AskRequest, *, mode_override=None, use_request_mode=True, admin=False, owner_id=None,
        shared_only=False, allowed_sources=None, project_id=None):
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "Câu hỏi trống")
    mode = req.mode if use_request_mode else mode_override
    if mode not in (None, "vector", "bm25", "hybrid", "graph", "hybrid_graph"):
        raise HTTPException(400, "mode phải là vector, bm25, hybrid, graph hoặc hybrid_graph")
    if _is_contextless_followup(q):
        return {
            "answer": "Mình chưa thấy câu hỏi trước nên chưa biết bạn đang nhắc đến phần nào. Bạn hãy nêu rõ tên khái niệm hoặc nội dung cần hỏi, ví dụ: «Hàm sigmoid là gì?».",
            "found": False, "cited": False, "sources": [],
        }
    try:
        service = _acquire_service()
        if service is None:
            _init_service()
            service = _acquire_service()
        if service is None:
            logging.error("Question request rejected: VSKR service is not ready: %s", state["error"])
            raise HTTPException(
                503,
                "Hệ thống đang cập nhật học liệu, thử lại sau ít phút",
                headers={"Retry-After": "10"},
            )
        try:
            return service.ask(q, mode=mode, llm_lock=_gpu_model_lock, lock_timeout=GPU_LOCK_TIMEOUT_S,
                               owner_id=owner_id, shared_only=shared_only, allowed_sources=allowed_sources,
                               project_id=project_id, admin=admin)
        finally:
            _release_service(service)
    except HTTPException:
        raise
    except RuntimeError as e:
        from ...service import ModelBusyError

        if isinstance(e, ModelBusyError):
            raise HTTPException(
                503,
                "Mô hình đang bận xử lý tác vụ khác. Vui lòng thử lại sau ít phút.",
                headers={"Retry-After": "10"},
            ) from e
        logging.exception("Question answering failed")
        detail = _safe_diagnostic(e) if admin else "Hiện chưa thể trả lời câu hỏi. Vui lòng thử lại sau."
        raise HTTPException(502, detail) from e
    except Exception as e:
        logging.exception("Question answering failed")
        detail = _safe_diagnostic(e) if admin else "Hiện chưa thể trả lời câu hỏi. Vui lòng thử lại sau."
        raise HTTPException(502, detail) from e
