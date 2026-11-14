"""Question handling shared by the public and administrator APIs."""

import logging
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .. import state_store
from . import runtime
from .diagnostics import safe_diagnostic


class AskRequest(BaseModel):
    question: str = Field(max_length=500)
    mode: Optional[str] = None


def ask(req: AskRequest, *, mode_override=None, use_request_mode=True, admin=False, public_only=False):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Câu hỏi trống")
    mode = req.mode if use_request_mode else mode_override
    if mode not in (None, "vector", "bm25", "hybrid", "graph", "hybrid_graph"):
        raise HTTPException(400, "mode phải là vector, bm25, hybrid, graph hoặc hybrid_graph")
    try:
        if runtime.state["service"] is None:
            runtime.init_service()
        if runtime.state["service"] is None:
            logging.error("Question request rejected: VSKR service is not ready: %s", runtime.state["error"])
            raise HTTPException(
                503,
                "Hệ thống đang cập nhật học liệu, thử lại sau ít phút",
                headers={"Retry-After": "10"},
            )
        with runtime.service_lease() as service:
            if service is None:
                raise HTTPException(
                    503,
                    "Hệ thống đang cập nhật học liệu, thử lại sau ít phút",
                    headers={"Retry-After": "10"},
                )
            allowed_chunk_ids = None
            if public_only and hasattr(service, "index"):
                private_ids = state_store.private_item_ids()
                allowed_chunk_ids = set()
                for chunk in service.index.chunks:
                    video_id = chunk.get("video_id")
                    document_id = chunk.get("document_id")
                    if video_id and video_id not in private_ids["video"]:
                        allowed_chunk_ids.add(chunk["chunk_id"])
                    elif document_id and document_id not in private_ids["document"]:
                        allowed_chunk_ids.add(chunk["chunk_id"])
            return service.ask(
                question,
                mode=mode,
                llm_lock=runtime._gpu_model_lock,
                lock_timeout=runtime.GPU_LOCK_TIMEOUT_S,
                allowed_chunk_ids=allowed_chunk_ids,
            )
    except HTTPException:
        raise
    except RuntimeError as exc:
        from ..service import ModelBusyError

        if isinstance(exc, ModelBusyError):
            raise HTTPException(
                503,
                "Hệ thống đang cập nhật học liệu, thử lại sau ít phút",
                headers={"Retry-After": "10"},
            ) from exc
        logging.exception("Question answering failed")
        detail = safe_diagnostic(exc) if admin else "Hiện chưa thể trả lời câu hỏi. Vui lòng thử lại sau."
        raise HTTPException(502, detail) from exc
    except Exception as exc:
        logging.exception("Question answering failed")
        detail = safe_diagnostic(exc) if admin else "Hiện chưa thể trả lời câu hỏi. Vui lòng thử lại sau."
        raise HTTPException(502, detail) from exc
