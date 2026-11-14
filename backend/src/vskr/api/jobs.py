"""Ingest pipeline orchestration and durable job API operations."""

import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import File, Form, HTTPException, UploadFile

from .. import audit
from ..config import data_dir, index_dir, load_config, video_dir
from ..ingest import check_duration, ingest, ingest_file, probe_duration
from ..util import fmt_time
from .ssrf import validate_ingest_url
from .diagnostics import safe_diagnostic as _safe_diagnostic
from .runtime import (
    _gpu_model_lock,
    _index_build_lock,
    _job_executor,
    _jobs,
    _jobs_lock,
    init_service as _init_service,
    state,
)
from .library import admin_status


def _update_job(job_id, **changes):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(changes)
            _jobs[job_id] = job


def _process_ingest_job(job_id, source):
    cfg = load_config()
    try:
        if source["kind"] == "document":
            _update_job(job_id, status="running", phase="document", message="Đang đọc và chia mục tài liệu…")
            from ..documents import ingest_document

            meta = ingest_document(source["path"], cfg, document_id=source["document_id"])
            _update_job(job_id, document_id=meta["document_id"], title=meta["title"])
        else:
            _update_job(job_id, status="running", phase="ingest", message="Đang tải/đọc video và tách audio…")
            if source["kind"] == "url":
                meta = ingest(source["url"], cfg, with_video=True)
            else:
                meta = ingest_file(source["path"], cfg, source["video_id"], title=source["title"])
            video_id = meta["video_id"]
            _update_job(job_id, phase="scenes", message="Đang phát hiện phân cảnh trong video…")
            from ..frames import detect_scenes_only

            detect_scenes_only(cfg, video_id)
            _update_job(job_id, video_id=video_id, title=meta.get("title"), phase="asr", message="Đang nhận dạng lời nói…")

            from .. import asr

            def report_asr_progress(_video_id, part, total, start, end):
                _update_job(
                    job_id,
                    phase="asr",
                    message=f"Nhận dạng phần {part}/{total} ({fmt_time(start)}–{fmt_time(end)} theo video gốc)…",
                )

            with _gpu_model_lock:
                asr.run(cfg, [video_id], progress=report_asr_progress)
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
        audit.write_event("ingest.complete", target_id=job_id, kind=source["kind"], result="success")
        if source["kind"] == "document":
            Path(source["path"]).unlink(missing_ok=True)
    except Exception as exc:
        logging.exception("Ingest pipeline job %s failed", job_id)
        _update_job(
            job_id,
            status="error",
            phase="error",
            message="Tác vụ thất bại. Xem nhật ký máy chủ.",
            _error=type(exc).__name__,
            _error_detail=_safe_diagnostic(exc),
        )
        audit.write_event("ingest.complete", target_id=job_id, kind=source.get("kind"), result="error")


def _rebuild_index(cfg=None, job_id=None):
    cfg = cfg or load_config()
    from .. import index as index_mod
    from ..embedder import Embedder

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
            "title": title or source.get("title"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "_source": source,
        }
    _job_executor.submit(_process_ingest_job, job_id, source)
    return job_id


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
        validate_ingest_url(clean_url)
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
            logging.exception("Could not inspect uploaded video")
            destination.unlink(missing_ok=True)
            vdir.rmdir()
            raise HTTPException(400, "Không đọc được video. Hãy kiểm tra định dạng và nhật ký máy chủ.") from exc
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
        logging.exception("Could not save uploaded document")
        path.unlink(missing_ok=True)
        raise HTTPException(400, "Không lưu được tài liệu. Hãy kiểm tra dung lượng và nhật ký máy chủ.") from exc
    finally:
        await file.close()
    source = {"kind": "document", "path": str(path), "document_id": document_id, "title": Path(filename).stem}
    job_id = _queue_job(source, source["title"])
    return {"job_id": job_id, "status": "queued"}


def get_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Không tìm thấy job này trong phiên server hiện tại.")
        result = {key: value for key, value in job.items() if not key.startswith("_")}
        if result.get("status") == "error":
            result["message"] = "Tác vụ thất bại. Xem nhật ký máy chủ."
            result["error_type"] = job.get("_error", "pipeline")
            result["error_detail"] = job.get("_error_detail", "")
        return result


def list_jobs(limit: int = 100):
    with _jobs_lock:
        snapshot = list(_jobs.values())
        jobs = [dict(job) for job in reversed(snapshot[-max(1, min(int(limit), 100)):])]
    result = []
    for job in jobs:
        item = {key: value for key, value in job.items() if not key.startswith("_")}
        if item.get("status") == "error":
            item["message"] = "Tác vụ thất bại. Xem nhật ký máy chủ."
            item["error_type"] = str(job.get("_error", "pipeline"))[:80]
            item["error_detail"] = _safe_diagnostic(job.get("_error_detail", ""))
        result.append(item)
    return result


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


def reload_index():
    """Nạp lại chỉ mục sau khi chạy lại `index` (đổi chunk/embedding) mà không cần khởi động lại server."""
    _init_service()
    return admin_status()
