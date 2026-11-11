import mimetypes
import json
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import ROOT, data_dir, index_dir, load_config, video_dir, videos_dir
from .index import load_meta
from .ingest import check_duration, ingest, ingest_file, probe_duration, resolve_source
from .util import read_json, write_json


class AskRequest(BaseModel):
    question: str
    mode: Optional[str] = None


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


state = {"service": None, "error": None}
_lock = threading.Lock()
_service_use_lock = threading.RLock()
_gpu_model_lock = threading.Lock()
_jobs_lock = threading.Lock()
_index_build_lock = threading.RLock()
_jobs = {}
_job_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vskr-pipeline")


def _init_service():
    """Nạp chỉ mục + embedder. Có thể gọi lại: nhờ vậy chạy `index` xong là dùng được ngay,
    không cần khởi động lại server (quan trọng khi `serve` và `index` chạy ở hai container khác nhau)."""
    with _lock:
        try:
            from .service import Service

            replacement = Service(load_config())
            with _service_use_lock:
                previous = state["service"]
                state["service"] = replacement
                state["error"] = None
                if previous is not None:
                    previous.embedder.close()
        except Exception as e:  # chưa có chỉ mục, thiếu thư viện...
            previous = state["service"]
            with _service_use_lock:
                state["service"] = None
                if previous is not None:
                    previous.embedder.close()
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
            "status": _video_status(meta),
            "job_id": _video_status(meta).get("job_id"),
            "file_url": (
                f"/api/videos/{meta['video_id']}/file"
                if meta.get("source_type") == "upload" and meta.get("has_video_file")
                else None
            ),
        }
        for meta in metadata.values()
    ]


def _video_status(meta):
    vid = meta["video_id"]
    with _jobs_lock:
        jobs = list(_jobs.values())
    for job in reversed(jobs):
        if job.get("video_id") == vid and job.get("status") in ("queued", "running", "error"):
            return {"state": job["status"], "label": job.get("message") or job["status"], "job_id": job["job_id"]}
    cfg = load_config()
    transcript_exists = (video_dir(cfg, vid) / "transcript.json").is_file()
    indexed = bool(state["service"] and any(c.get("video_id") == vid for c in state["service"].index.chunks))
    if indexed:
        return {"state": "ready", "label": "Đã lập chỉ mục"}
    if not transcript_exists:
        return {"state": "error", "label": "Chưa có transcript"}
    return {"state": "processing", "label": "Chưa lập chỉ mục"}


@app.get("/api/library")
def library():
    cfg = load_config()
    video_items = []
    for item in videos():
        video_items.append({"id": item["video_id"], "kind": "video", "title": item.get("title") or item["video_id"],
                            "status": item["status"], "source_type": item["source_type"], "duration": item.get("duration"),
                            "file_url": item.get("file_url"), "url": item.get("url"), "job_id": item.get("job_id")})
    from .documents import list_documents
    doc_items = []
    for item in list_documents(cfg):
        section_file = data_dir(cfg) / "documents" / item["document_id"] / "sections.json"
        count = len(read_json(section_file).get("chunks", [])) if section_file.is_file() else 0
        with _jobs_lock:
            doc_job = next((job for job in reversed(list(_jobs.values())) if job.get("document_id") == item["document_id"] and job.get("status") in ("queued", "running", "error")), None)
        indexed = bool(state["service"] and any(c.get("document_id") == item["document_id"] for c in state["service"].index.chunks))
        status_info = ({"state": doc_job["status"], "label": doc_job.get("message"), "job_id": doc_job["job_id"]}
                       if doc_job else {"state": "ready" if indexed else "processing", "label": "Đã chia mục" if indexed else "Đang lập chỉ mục"})
        doc_items.append({"id": item["document_id"], "kind": "document", "title": item.get("title") or item.get("filename"),
                          "filename": item.get("filename"), "sections": count,
                          "status": status_info, "job_id": status_info.get("job_id")})
    with _jobs_lock:
        jobs = [dict(job) for job in _jobs.values() if job.get("status") in ("queued", "running", "error")]
    known = {(item["kind"], item["id"]) for item in video_items + doc_items}
    for job in jobs:
        kind = "document" if job.get("_source", {}).get("kind") == "document" else "video"
        item_id = job.get("document_id") if kind == "document" else job.get("video_id")
        if item_id and (kind, item_id) in known:
            continue
        video_items.append({"id": f"job:{job['job_id']}", "kind": kind,
                            "title": job.get("title") or "Nội dung đang xử lý", "status": {
                                "state": job["status"], "label": job.get("message") or job["status"]},
                            "job_id": job["job_id"]})
    return video_items + doc_items


def _rebuild_or_clear():
    try:
        _rebuild_index()
    except RuntimeError as exc:
        if "Không có nội dung để lập chỉ mục" not in str(exc):
            raise
        shutil.rmtree(index_dir(load_config()), ignore_errors=True)
    _init_service()


def _forget_jobs(kind, item_id):
    with _jobs_lock:
        matches = [(job_id, job) for job_id, job in _jobs.items()
                   if job.get("status") not in ("queued", "running") and
                   (job.get("document_id") if kind == "document" else job.get("video_id")) == item_id]
        for job_id, _job in matches:
            _jobs.pop(job_id, None)
            source_path = (_job.get("_source") or {}).get("path")
            if source_path and kind == "document":
                Path(source_path).unlink(missing_ok=True)


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: str):
    cfg = load_config()
    root = videos_dir(cfg).resolve()
    target = video_dir(cfg, video_id).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(404, "Không tìm thấy video.")
    with _index_build_lock:
        if not target.is_dir():
            return {"deleted": video_id, "already_absent": True, "ready": state["service"] is not None}
        with _jobs_lock:
            if any(job.get("video_id") == video_id and job.get("status") in ("queued", "running") for job in _jobs.values()):
                raise HTTPException(409, "Video đang được xử lý; hãy chờ tác vụ kết thúc trước khi xóa.")
        trash = data_dir(cfg) / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        tombstone = trash / f"video-{uuid.uuid4().hex}"
        os.replace(target, tombstone)
        try:
            _rebuild_or_clear()
        except Exception as exc:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tombstone, target)
            raise HTTPException(500, f"Chưa xóa được video; dữ liệu gốc đã được giữ lại. Lỗi lập chỉ mục: {exc}") from exc
        shutil.rmtree(tombstone, ignore_errors=True)
        _forget_jobs("video", video_id)
    return {"deleted": video_id, "already_absent": False, "ready": state["service"] is not None}


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str):
    cfg = load_config()
    root = (data_dir(cfg) / "documents").resolve()
    target = (root / document_id).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(404, "Không tìm thấy tài liệu.")
    with _index_build_lock:
        if not (target / "meta.json").is_file():
            return {"deleted": document_id, "already_absent": True, "ready": state["service"] is not None}
        with _jobs_lock:
            if any(job.get("document_id") == document_id and job.get("status") in ("queued", "running") for job in _jobs.values()):
                raise HTTPException(409, "Tài liệu đang được xử lý; hãy chờ tác vụ kết thúc trước khi xóa.")
        trash = data_dir(cfg) / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        tombstone = trash / f"document-{uuid.uuid4().hex}"
        os.replace(target, tombstone)
        try:
            _rebuild_or_clear()
        except Exception as exc:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tombstone, target)
            raise HTTPException(500, f"Chưa xóa được tài liệu; dữ liệu gốc đã được giữ lại. Lỗi lập chỉ mục: {exc}") from exc
        shutil.rmtree(tombstone, ignore_errors=True)
        _forget_jobs("document", document_id)
    return {"deleted": document_id, "already_absent": False, "ready": state["service"] is not None}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") in ("queued", "running"):
            raise HTTPException(409, "Chỉ có thể xóa tác vụ đã hoàn tất hoặc bị lỗi.")
        source = job.get("_source", {})
        video_id = job.get("video_id")
    if source.get("path"):
        path = Path(source["path"])
        if source.get("kind") == "upload" and path.parent.is_dir():
            shutil.rmtree(path.parent, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    if source.get("document_id"):
        shutil.rmtree(data_dir(load_config()) / "documents" / source["document_id"], ignore_errors=True)
    if video_id:
        shutil.rmtree(video_dir(load_config(), video_id), ignore_errors=True)
    with _jobs_lock:
        _jobs.pop(job_id, None)
    return {"deleted": job_id}


@app.get("/api/documents/{document_id}/sections/{section}")
def document_section(document_id: str, section: int):
    cfg = load_config()
    root = (data_dir(cfg) / "documents").resolve()
    doc_dir = (root / document_id).resolve()
    if not doc_dir.is_relative_to(root):
        raise HTTPException(404, "Không tìm thấy tài liệu.")
    meta_path, sections_path = doc_dir / "meta.json", doc_dir / "sections.json"
    if not meta_path.is_file() or not sections_path.is_file():
        raise HTTPException(404, "Không tìm thấy tài liệu.")
    meta = read_json(meta_path)
    chunks = read_json(sections_path).get("chunks", [])
    match = next((c for c in chunks if c.get("section") == section), None)
    if match is None:
        raise HTTPException(404, "Không tìm thấy mục tài liệu.")
    return {"document_id": document_id, "title": meta.get("title"), "section": section, "text": match["text"]}


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
    with _jobs_lock:
        running = next((job for job in _jobs.values() if job.get("status") == "running"), None)
    return {"ready": state["service"] is not None, "error": state["error"],
            "gpu_busy": _gpu_model_lock.locked(), "phase": running.get("phase") if running else None}


def _update_job(job_id, **changes):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(changes)


def _process_ingest_job(job_id, source):
    cfg = load_config()
    try:
        if source["kind"] == "document":
            _update_job(job_id, status="running", phase="document", message="Đang đọc và chia mục tài liệu…")
            from .documents import ingest_document

            meta = ingest_document(source["path"], cfg, document_id=source["document_id"])
            _update_job(job_id, document_id=meta["document_id"], title=meta["title"])
        else:
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

        _update_job(job_id, phase="index", message="Đang chia đoạn và lập chỉ mục…")
        _rebuild_index(cfg, job_id=job_id)
        _update_job(job_id, phase="reload", message="Đang nạp lại chỉ mục…")
        _init_service()
        if state["service"] is None:
            raise RuntimeError(state["error"] or "Không nạp được chỉ mục mới.")
        _update_job(job_id, status="done", phase="done", message="Nội dung đã sẵn sàng để hỏi đáp.")
        if source["kind"] == "document":
            Path(source["path"]).unlink(missing_ok=True)
    except Exception as exc:
        _update_job(job_id, status="error", phase="error", message=str(exc))


def _rebuild_index(cfg=None, job_id=None):
    cfg = cfg or load_config()
    from . import index as index_mod
    from .embedder import Embedder

    idir = index_dir(cfg)
    idir.parent.mkdir(parents=True, exist_ok=True)
    staging = idir.parent / f".{idir.name}.staging-{uuid.uuid4().hex}"
    backup = idir.parent / f".{idir.name}.backup-{uuid.uuid4().hex}"
    with _index_build_lock:
        try:
            embedder = Embedder(cfg["embedding"])
            try:
                progress = (lambda phase, message: _update_job(job_id, phase=phase, message=message)) if job_id else None
                index_mod.build(cfg, embedder, progress=progress, output_dir=staging)
            finally:
                embedder.close()
            moved_old = False
            try:
                if idir.exists():
                    os.replace(idir, backup)
                    moved_old = True
                os.replace(staging, idir)
            except Exception:
                if moved_old and not idir.exists():
                    os.replace(backup, idir)
                raise
            if moved_old:
                shutil.rmtree(backup, ignore_errors=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _queue_job(source, title=None):
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id, "status": "queued", "phase": "queued",
            "message": "Đang chờ các tác vụ xử lý trước hoàn tất…",
            "video_id": source.get("video_id"), "document_id": source.get("document_id"),
            "title": title or source.get("title"), "_source": source,
        }
    _job_executor.submit(_process_ingest_job, job_id, source)
    return job_id


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
        except ValueError as exc:
            destination.unlink(missing_ok=True)
            vdir.rmdir()
            raise HTTPException(400, str(exc)) from exc
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

    job_id = _queue_job(source, source.get("title"))
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/documents", status_code=202)
async def upload_document(file: UploadFile = File(...)):
    filename = Path(file.filename or "document.txt").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".txt", ".md", ".rst", ".pdf", ".docx"}:
        await file.close()
        raise HTTPException(415, "Định dạng chưa hỗ trợ. Hãy tải TXT, MD, PDF hoặc DOCX.")
    cfg = load_config()
    upload_root = data_dir(cfg) / "documents" / ".uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    document_id = f"doc-{uuid.uuid4().hex[:12]}"
    path = upload_root / f"{document_id}{suffix}"
    size = 0
    try:
        with path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise HTTPException(413, "Tài liệu vượt giới hạn 100 MB.")
                out.write(chunk)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"Không lưu được tài liệu: {exc}") from exc
    finally:
        await file.close()
    source = {"kind": "document", "path": str(path), "document_id": document_id, "title": Path(filename).stem}
    job_id = _queue_job(source, source["title"])
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Không tìm thấy job này trong phiên server hiện tại.")
        return {key: value for key, value in job.items() if not key.startswith("_")}


@app.post("/api/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") != "error":
            raise HTTPException(404, "Không tìm thấy tác vụ lỗi để thử lại.")
        source = dict(job.get("_source") or {})
    if not source or (source.get("path") and not Path(source["path"]).is_file()):
        raise HTTPException(410, "Không còn file nguồn để thử lại. Hãy tải nội dung lên lần nữa.")
    if source.get("video_id"):
        (video_dir(load_config(), source["video_id"]) / "transcript.json").unlink(missing_ok=True)
    new_job_id = _queue_job(source, job.get("title"))
    return {"job_id": new_job_id, "status": "queued"}


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
    if req.mode not in (None, "vector", "bm25", "hybrid", "graph"):
        raise HTTPException(400, "mode phải là vector, bm25, hybrid hoặc graph")
    try:
        if state["service"] is None:
            _init_service()
        if state["service"] is None:
            raise HTTPException(503, state["error"] or "Dịch vụ chưa sẵn sàng")
        with _service_use_lock:
            return state["service"].ask(q, mode=req.mode, llm_lock=_gpu_model_lock)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.get("/api/eval/questions")
def get_eval_questions():
    return _read_questions()


@app.get("/api/eval/results")
def get_eval_results():
    path = _eval_dir() / "results.json"
    return read_json(path) if path.is_file() else None


@app.post("/api/eval/questions")
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


@app.post("/api/eval/questions/import")
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


@app.delete("/api/eval/questions/{question_id}")
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


@app.post("/api/eval/run")
def run_retrieval_evaluation():
    questions = _read_questions()
    if not questions:
        raise HTTPException(409, "Chưa có câu hỏi đánh giá. Hãy thêm câu hỏi và nguồn đúng trước.")
    if state["service"] is None:
        _init_service()
    if state["service"] is None:
        raise HTTPException(503, state["error"] or "Dịch vụ chưa sẵn sàng")
    modes = ("vector", "bm25", "hybrid", "graph")
    hits = {mode: {} for mode in modes}
    with _service_use_lock:
        service = state["service"]
        try:
            for question in questions:
                for mode in modes:
                    hits[mode][question["id"]] = service.retriever.search(question["question"], mode=mode, top_k=5)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
    from .evaluation import score_results

    scored = score_results(questions, hits, k=5)
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "question_count": len(questions),
        "retrieval_config": {**service.cfg["retrieval"], "modes": list(modes), "k": 5},
        **scored,
    }
    write_json(_eval_dir() / "results.json", result)
    return result


@app.get("/api/graph")
def inspect_graph(query: str, adjacent: bool = False):
    if state["service"] is None:
        _init_service()
    if state["service"] is None:
        raise HTTPException(503, state["error"] or "Dịch vụ chưa sẵn sàng")
    with _service_use_lock:
        service = state["service"]
        graph = service.index.graph
        if not graph.get("nodes"):
            raise HTTPException(409, "Chỉ mục chưa có Đồ thị tri thức. Hãy chạy lại: vskr index")
        hits = service.retriever.search(query, mode="graph", top_k=8)
        chunks = service.index.chunks
    hit_by_id = {hit["chunk"]["chunk_id"]: hit for hit in hits}
    source_ids = {f"source:{chunk_id}" for chunk_id in hit_by_id}
    by_video = {}
    if adjacent:
        for chunk in chunks:
            if chunk.get("video_id") and chunk.get("start") is not None:
                by_video.setdefault(chunk["video_id"], []).append(chunk)
        for group in by_video.values():
            group.sort(key=lambda chunk: chunk.get("start", 0))
            positions = {chunk["chunk_id"]: i for i, chunk in enumerate(group)}
            for chunk_id in list(hit_by_id):
                hit = hit_by_id[chunk_id]
                video_id = hit["chunk"].get("video_id")
                if not video_id or chunk_id not in positions:
                    continue
                i = positions[chunk_id]
                for j in (i - 1, i + 1):
                    if 0 <= j < len(group):
                        neighbor = group[j]
                        neighbor_id = neighbor["chunk_id"]
                        hit_by_id.setdefault(neighbor_id, {"chunk": neighbor, "score": 0.0, "matched_concepts": []})
                        source_ids.add(f"source:{neighbor_id}")
    concept_labels = {label for hit in hits for label in hit.get("matched_concepts", [])}
    concept_ids = {f"concept:{label}" for label in concept_labels}
    selected_ids = source_ids | concept_ids
    for edge in graph.get("edges", []):
        if edge.get("type") == "PART_OF_SCENE" and edge.get("from") in source_ids:
            selected_ids.add(edge["to"])
    node_scores = {f"source:{chunk_id}": hit["score"] for chunk_id, hit in hit_by_id.items()}
    nodes = []
    for node in graph.get("nodes", []):
        if node["id"] not in selected_ids:
            continue
        item = dict(node)
        if item["id"] in node_scores:
            item["score"] = node_scores[item["id"]]
            chunk = hit_by_id.get(item.get("chunk_id"), {}).get("chunk", {})
            item["title"] = item.get("title") or chunk.get("title")
            item["section"] = item.get("section") or chunk.get("section")
            if item.get("video_id"):
                item["title"] = item.get("title") or service.meta.get(item["video_id"], {}).get("title")
        nodes.append(item)
    edges = [edge for edge in graph.get("edges", []) if edge["from"] in selected_ids and edge["to"] in selected_ids]
    if adjacent:
        chunk_positions = {}
        for video_id, group in by_video.items():
            for i, chunk in enumerate(group):
                chunk_positions[(video_id, chunk["chunk_id"])] = (i, group)
        existing = {(edge["from"], edge["to"]) for edge in edges}
        for chunk_id, hit in hit_by_id.items():
            video_id = hit["chunk"].get("video_id")
            pos = chunk_positions.get((video_id, chunk_id))
            if not pos:
                continue
            i, group = pos
            for j in (i - 1, i + 1):
                if 0 <= j < len(group):
                    neighbor_id = f"source:{group[j]['chunk_id']}"
                    src_id = f"source:{chunk_id}"
                    pair = (src_id, neighbor_id)
                    if neighbor_id in source_ids and pair not in existing:
                        edges.append({"from": src_id, "to": neighbor_id, "type": "NEXT_SEGMENT", "weight": 1})
                        existing.add(pair)
    return {"query": query, "nodes": nodes, "edges": edges,
            "matched_concepts": sorted(concept_labels), "result_count": len(hits), "adjacent": adjacent,
            "score_name": "điểm truy xuất graph hiện tại"}
