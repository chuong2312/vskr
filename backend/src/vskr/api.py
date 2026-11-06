import mimetypes
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import ROOT, load_config, video_dir, videos_dir
from .index import load_meta
from .ingest import check_duration, ingest, ingest_file, probe_duration, resolve_source
from .util import read_json


class AskRequest(BaseModel):
    question: str
    mode: Optional[str] = None


state = {"service": None, "error": None}
_lock = threading.Lock()
_gpu_model_lock = threading.Lock()
_jobs_lock = threading.Lock()
_jobs = {}
_job_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vskr-pipeline")


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
    cfg = load_config()
    metadata = state["service"].meta if state["service"] is not None else load_meta(cfg)
    return [
        {
            "video_id": meta["video_id"],
            "title": meta.get("title"),
            "duration": meta.get("duration"),
            "url": meta.get("url"),
            "source_type": meta.get("source_type", "url"),
            "file_url": (
                f"/api/videos/{meta['video_id']}/file"
                if meta.get("source_type") == "upload" and meta.get("has_video_file")
                else None
            ),
        }
        for meta in metadata.values()
    ]


@app.get("/api/videos/{video_id}/file")
def uploaded_video_file(video_id: str):
    cfg = load_config()
    video_root = videos_dir(cfg).resolve()
    vdir = video_dir(cfg, video_id)
    if not vdir.resolve().is_relative_to(video_root):
        raise HTTPException(404, "Không tìm thấy video.")
    meta_path = vdir / "meta.json"
    if not meta_path.is_file():
        raise HTTPException(404, "Không tìm thấy video.")
    meta = read_json(meta_path)
    if meta.get("source_type") != "upload" or not meta.get("has_video_file"):
        raise HTTPException(404, "Video này không có file upload để phát trực tiếp.")
    source_path = resolve_source(vdir, meta)
    if source_path is None or not source_path.resolve().is_relative_to(vdir.resolve()):
        raise HTTPException(404, "File video không còn trên máy.")
    media_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
    return FileResponse(source_path, media_type=media_type)


@app.get("/api/status")
def status():
    return {"ready": state["service"] is not None, "error": state["error"]}


def _update_job(job_id, **changes):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(changes)


def _process_ingest_job(job_id, source):
    cfg = load_config()
    try:
        _update_job(job_id, status="running", phase="ingest", message="Đang tải/đọc video và tách audio…")
        if source["kind"] == "url":
            meta = ingest(source["url"], cfg)
        else:
            meta = ingest_file(source["path"], cfg, source["video_id"], title=source["title"])
        video_id = meta["video_id"]
        _update_job(job_id, video_id=video_id, title=meta.get("title"), phase="asr", message="Đang nhận dạng lời nói…")

        from . import asr

        with _gpu_model_lock:
            asr.run(cfg, [video_id])
        vdir = video_dir(cfg, video_id)
        if not (vdir / "transcript.json").is_file():
            raise RuntimeError("ASR chưa tạo transcript; kiểm tra file audio và cấu hình ASR.")

        _update_job(job_id, phase="index", message="Đang chia đoạn và tạo chỉ mục tìm kiếm…")
        from . import index as index_mod
        from .embedder import Embedder

        embedder = Embedder(cfg["embedding"])
        try:
            index_mod.build(cfg, embedder)
        finally:
            embedder.close()

        _update_job(job_id, phase="reload", message="Đang nạp chỉ mục mới…")
        _init_service()
        if state["service"] is None:
            raise RuntimeError(state["error"] or "Không nạp được chỉ mục mới.")
        _update_job(job_id, status="done", phase="done", message="Video đã sẵn sàng để hỏi đáp.")
    except Exception as exc:
        _update_job(job_id, status="error", phase="error", message=str(exc))


@app.post("/api/ingest", status_code=202)
async def start_ingest(url: Optional[str] = Form(None), file: Optional[UploadFile] = File(None)):
    """Queue one URL or local video upload; pipeline jobs are serialized to protect GPU VRAM."""
    clean_url = (url or "").strip()
    if bool(clean_url) == bool(file):
        raise HTTPException(400, "Hãy nhập một URL hoặc chọn một file video, không chọn cả hai.")

    cfg = load_config()
    ingest_cfg = cfg.get("ingest", {})
    max_bytes = int(ingest_cfg.get("max_upload_bytes", 1_073_741_824))
    source = None
    if clean_url:
        parsed = urlsplit(clean_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(400, "URL video phải bắt đầu bằng http:// hoặc https://.")
        source = {"kind": "url", "url": clean_url}
    else:
        filename = Path(file.filename or "video").name
        suffix = Path(filename).suffix.lower()
        allowed = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpeg", ".mpg"}
        if suffix not in allowed:
            await file.close()
            raise HTTPException(415, "Định dạng chưa hỗ trợ. Hãy tải MP4, MOV, MKV, WebM hoặc AVI.")

        video_id = f"upload-{uuid.uuid4().hex[:12]}"
        vdir = video_dir(cfg, video_id)
        vdir.mkdir(parents=True, exist_ok=False)
        destination = vdir / f"source{suffix}"
        size = 0
        try:
            with destination.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(413, f"File vượt giới hạn {max_bytes // (1024 * 1024)} MB.")
                    out.write(chunk)
            duration = probe_duration(destination)
            check_duration(duration, cfg)
        except HTTPException:
            destination.unlink(missing_ok=True)
            vdir.rmdir()
            raise
        except Exception as exc:
            destination.unlink(missing_ok=True)
            vdir.rmdir()
            raise HTTPException(400, f"Không đọc được video: {exc}") from exc
        finally:
            await file.close()
        source = {
            "kind": "upload",
            "path": str(destination),
            "video_id": video_id,
            "title": Path(filename).stem or video_id,
        }

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "phase": "queued",
            "message": "Đang chờ các job GPU khác hoàn tất…",
            "video_id": source.get("video_id"),
            "title": source.get("title"),
        }
    _job_executor.submit(_process_ingest_job, job_id, source)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Không tìm thấy job này trong phiên server hiện tại.")
        return dict(job)


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
        with _gpu_model_lock:
            return _svc().ask(q, mode=req.mode)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
