import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import ROOT, load_config


class AskRequest(BaseModel):
    question: str
    mode: Optional[str] = None


state = {"service": None, "error": None}
_lock = threading.Lock()


def _init_service():
    """Nạp chỉ mục + embedder. Có thể gọi lại: nhờ vậy chạy `index` xong là dùng được ngay,
    không cần khởi động lại server (quan trọng khi `serve` và `index` chạy ở hai container khác nhau)."""
    with _lock:
        try:
            from .service import Service

            state["service"] = Service(load_config())
            state["error"] = None
        except Exception as e:  # chưa có chỉ mục, thiếu thư viện...
            state["service"] = None
            state["error"] = f"{type(e).__name__}: {e}"


@asynccontextmanager
async def lifespan(app):
    _init_service()
    yield


app = FastAPI(title="VSKR", lifespan=lifespan)


def _svc():
    if state["service"] is None:
        _init_service()  # thử lại: có thể người dùng vừa chạy xong `index`
    if state["service"] is None:
        raise HTTPException(503, state["error"] or "Dịch vụ chưa sẵn sàng")
    return state["service"]


@app.get("/")
def home():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/videos")
def videos():
    return _svc().videos()


@app.get("/api/status")
def status():
    return {"ready": state["service"] is not None, "error": state["error"]}


@app.post("/api/reload")
def reload_index():
    """Nạp lại chỉ mục sau khi chạy lại `index` (đổi chunk/embedding) mà không cần khởi động lại server."""
    _init_service()
    return status()


@app.post("/api/ask")
def ask(req: AskRequest):
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "Câu hỏi trống")
    if req.mode not in (None, "vector", "bm25", "hybrid"):
        raise HTTPException(400, "mode phải là vector, bm25 hoặc hybrid")
    try:
        return _svc().ask(q, mode=req.mode)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
