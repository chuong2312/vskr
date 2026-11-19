"""Hàng đợi nạp học liệu: tải video hoặc tài liệu, chạy ASR, OCR/VLM rồi dựng lại chỉ mục.

Mỗi job đi qua: giữ chỗ (``_reserve_job_slot``) → xếp hàng (``_queue_job``) → chạy trên luồng
riêng (``_process_ingest_job``). Bước OCR/VLM lỗi chỉ thành cảnh báo; video vẫn được lập chỉ mục
từ lời giảng.
"""
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import File, Form, HTTPException, UploadFile

from ...config import data_dir, index_dir, load_config, video_dir, videos_dir
from ...pipeline.ingest import check_duration, ingest, ingest_file, probe_duration
from ...util import fmt_time, read_json, write_json
from ...access import audit, accounts
from ...pipeline.url_safety import UnsafeUrlError, validate_ingest_url
from .runtime import (
    _gpu_model_lock, _index_build_lock, _init_service, _job_executor, _job_reservations, _jobs, _jobs_lock,
    _safe_diagnostic, state,
)


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


def delete_job(job_id: str):
    cfg = load_config()
    with _index_build_lock:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job or job.get("status") not in ("done", "error"):
                raise HTTPException(409, "Chỉ có thể xóa tác vụ đã hoàn tất hoặc bị lỗi.")
            previous_status = job["status"]
            job["status"] = "deleting"
            source = dict(job.get("_source") or {})
            video_id = job.get("video_id") or source.get("video_id")
            document_id = job.get("document_id") or source.get("document_id")

        def is_indexed(kind, item_id):
            if not item_id:
                return False
            service = state.get("service")
            if service and any(chunk.get(f"{kind}_id") == item_id for chunk in service.index.chunks):
                return True
            chunks_path = index_dir(cfg) / "chunks.json"
            if not chunks_path.is_file():
                return False
            try:
                return any(chunk.get(f"{kind}_id") == item_id for chunk in read_json(chunks_path))
            except (OSError, ValueError, TypeError):
                # If the index cannot be inspected safely, preserve the source.
                return True

        try:
            video_is_indexed = is_indexed("video", video_id)
            document_is_indexed = is_indexed("document", document_id)

            if video_id and not video_is_indexed:
                root = videos_dir(cfg).resolve()
                target = video_dir(cfg, video_id).resolve()
                if target != root and target.is_relative_to(root):
                    shutil.rmtree(target, ignore_errors=True)

            document_root = (data_dir(cfg) / "documents").resolve()
            if document_id and not document_is_indexed:
                target = (document_root / document_id).resolve()
                if target != document_root and target.is_relative_to(document_root):
                    shutil.rmtree(target, ignore_errors=True)

            source_path = source.get("path")
            if source_path and source.get("kind") == "document":
                upload_root = (document_root / ".uploads").resolve()
                path = Path(source_path).resolve()
                if path != upload_root and path.is_relative_to(upload_root) and path.is_file():
                    path.unlink(missing_ok=True)

            with _jobs_lock:
                _jobs.pop(job_id, None)
        except Exception:
            with _jobs_lock:
                current = _jobs.get(job_id)
                if current and current.get("status") == "deleting":
                    current["status"] = previous_status
            raise
    return {"deleted": job_id}


def _update_job(job_id, **changes):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(changes)


def _process_ingest_job(job_id, source):
    cfg = load_config()
    if source.get("limits"):
        cfg = dict(cfg)
        cfg["ingest"] = {**cfg.get("ingest", {}), **source["limits"]}
    warnings = []
    try:
        if source["kind"] == "document":
            _update_job(job_id, status="running", phase="document", message="Đang đọc và chia mục tài liệu…")
            from ...pipeline.documents import ingest_document

            meta = ingest_document(source["path"], cfg, document_id=source["document_id"])
            if source.get("owner_id"):
                meta["owner_id"] = source["owner_id"]
            if source.get("project_id"):
                meta["project_id"] = source["project_id"]
            write_json(data_dir(cfg) / "documents" / source["document_id"] / "meta.json", meta)
            _update_job(job_id, document_id=meta["document_id"], title=meta["title"])
        else:
            _update_job(job_id, status="running", phase="ingest", message="Đang tải/đọc video và tách audio…")
            if source["kind"] == "url":
                meta = ingest(source["url"], cfg, with_video=True, video_id=source.get("video_id"))
            else:
                meta = ingest_file(source["path"], cfg, source["video_id"], title=source["title"])
            video_id = meta["video_id"]
            if source.get("owner_id"):
                meta["owner_id"] = source["owner_id"]
            if source.get("project_id"):
                meta["project_id"] = source["project_id"]
            write_json(video_dir(cfg, video_id) / "meta.json", meta)
            _update_job(job_id, phase="scenes", message="Đang quét phân cảnh; video dài có thể mất vài phút…")
            from ...pipeline.frames import detect_scenes_only

            scene_result = detect_scenes_only(cfg, video_id)
            if scene_result.get("detection_status") == "fallback":
                warnings.append("Không đọc được phân cảnh; đã tiếp tục lập chỉ mục bằng transcript.")
            _update_job(job_id, video_id=video_id, title=meta.get("title"), phase="asr", message="Đang nhận dạng lời nói…")

            from ...pipeline import asr

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
            _run_visual_steps(cfg, job_id, video_id, warnings)

        _update_job(job_id, phase="index", message="Đang chia đoạn và lập chỉ mục…")
        _rebuild_index(cfg, job_id=job_id)
        _update_job(job_id, phase="reload", message="Đang nạp lại chỉ mục…")
        _init_service(force=True)
        if state["service"] is None:
            raise RuntimeError(state["error"] or "Không nạp được chỉ mục mới.")
        message = "Nội dung đã sẵn sàng để hỏi đáp."
        if warnings:
            message += " " + " ".join(warnings)
        _update_job(job_id, status="done", phase="done", message=message)
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


def _run_visual_steps(cfg, job_id, video_id, warnings):
    """Extract frames, then OCR, then VLM, each when enabled; failures only add warnings.

    On-screen text and figure captions improve search, but a lecture stays
    usable from its transcript, so these steps must not fail the ingest job.
    VLM runs after OCR because it uses OCR's per-frame text coverage to
    caption only figure-like frames.
    """
    ocr_on = bool(cfg.get("ocr", {}).get("enabled"))
    vlm_on = bool(cfg.get("vlm", {}).get("enabled"))
    if not (ocr_on or vlm_on):
        return
    meta = read_json(video_dir(cfg, video_id) / "meta.json")
    if not meta.get("has_video_file"):
        return
    from ...pipeline.steps import run_step

    try:
        _update_job(job_id, phase="frames", message="Đang trích frame từ video…")
        from ...pipeline.frames import run as extract_frames

        extract_frames(cfg, video_id)
    except Exception as exc:
        logging.warning("Frame extraction failed for %s; indexing transcript only (%s: %s)",
                        video_id, type(exc).__name__, exc)
        warnings.append("Không trích được frame; đã lập chỉ mục bằng transcript.")
        return
    steps = []
    if ocr_on:
        steps.append(("ocr", "Đang đọc chữ trên màn hình (OCR)…",
                      "Không đọc được chữ trên màn hình (OCR); đã lập chỉ mục bằng transcript."))
    if vlm_on:
        steps.append(("vlm", "Đang mô tả sơ đồ/hình minh họa (VLM)…",
                      "Không mô tả được hình minh họa (VLM); phần còn lại vẫn được lập chỉ mục."))
    for step, running, failed in steps:
        try:
            _update_job(job_id, phase=step, message=running)
            with _gpu_model_lock:
                run_step(cfg, step, [video_id])
        except Exception as exc:
            logging.warning("%s step failed for %s (%s: %s)", step.upper(), video_id, type(exc).__name__, exc)
            warnings.append(failed)


def _rebuild_index(cfg=None, job_id=None):
    cfg = cfg or load_config()
    from ...retrieval import index as index_mod
    from ...retrieval.embedder import Embedder

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


def _reserve_job_slot(owner_id, role_limits, ingest_cfg):
    with _jobs_lock:
        active = sum(
            job.get("owner_id") == owner_id and job.get("status") in {"queued", "running"}
            for job in _jobs.values()
        ) + sum(reserved_owner == owner_id for reserved_owner in _job_reservations.values())
        if active >= int(role_limits.get("max_active_jobs", 2)):
            raise HTTPException(429, "Bạn đã đạt số tác vụ đang xử lý tối đa.")
        queued = sum(job.get("status") in {"queued", "running"} for job in _jobs.values())
        if queued + len(_job_reservations) >= int(ingest_cfg.get("max_queue_length", 20)):
            raise HTTPException(503, "Hàng đợi xử lý đang đầy.", headers={"Retry-After": "30"})
        reservation_id = uuid.uuid4().hex
        _job_reservations[reservation_id] = owner_id
        return reservation_id


def _release_job_slot(reservation_id):
    if reservation_id:
        with _jobs_lock:
            _job_reservations.pop(reservation_id, None)


def _queue_job(source, title=None, *, reservation_id=None):
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        if reservation_id:
            if _job_reservations.get(reservation_id) != source.get("owner_id"):
                raise RuntimeError("Ingest job slot reservation was lost.")
            _job_reservations.pop(reservation_id)
        _jobs[job_id] = {
            "job_id": job_id, "status": "queued", "phase": "queued",
            "message": "Đang chờ các tác vụ xử lý trước hoàn tất…",
            "video_id": source.get("video_id"), "document_id": source.get("document_id"),
            "title": title or source.get("title"),
            "owner_id": source.get("owner_id"),
            "project_id": source.get("project_id"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "_source": source,
        }
        try:
            _job_executor.submit(_process_ingest_job, job_id, source)
        except Exception:
            _jobs.pop(job_id, None)
            raise
    return job_id


async def start_ingest(url: Optional[str] = Form(None), file: Optional[UploadFile] = File(None),
                       owner_id=None, project_id=None):
    """Queue one URL or local video upload; pipeline jobs are serialized to protect GPU VRAM."""
    if not project_id:
        raise HTTPException(422, "project_id là bắt buộc.")
    clean_url = (url or "").strip()
    if bool(clean_url) == bool(file):
        raise HTTPException(400, "Hãy nhập một URL hoặc chọn một file video, không chọn cả hai.")

    cfg = load_config()
    ingest_cfg = cfg.get("ingest", {})
    guest = str(owner_id or "").startswith("g:")
    role_limits = cfg.get("limits", {}).get("guest" if guest else "user", {})
    max_bytes = int(role_limits.get("max_upload_bytes", ingest_cfg.get("max_upload_bytes", 1_073_741_824)))
    max_duration = int(role_limits.get("max_duration_s", ingest_cfg.get("max_duration_s", 7200)))
    if clean_url:
        try:
            validate_ingest_url(clean_url)
        except UnsafeUrlError as exc:
            raise HTTPException(400, str(exc)) from exc
    filename = None
    suffix = None
    if file:
        filename = Path(file.filename or "video").name
        suffix = Path(filename).suffix.lower()
        allowed = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpeg", ".mpg"}
        if suffix not in allowed:
            await file.close()
            raise HTTPException(415, "Định dạng chưa hỗ trợ. Hãy tải MP4, MOV, MKV, WebM hoặc AVI.")

    reservation_id = None
    usage_id = None
    vdir = None
    destination = None
    queued = False
    cfg = dict(cfg)
    cfg["ingest"] = dict(ingest_cfg, max_upload_bytes=max_bytes, max_duration_s=max_duration)
    try:
        reservation_id = _reserve_job_slot(owner_id, role_limits, ingest_cfg)
        max_jobs_per_day = int(role_limits.get("max_jobs_per_day", 0))
        if max_jobs_per_day > 0:
            usage_id = accounts.reserve_ingest_job(owner_id, max_jobs_per_day)
        if clean_url:
            video_id = f"user-{uuid.uuid4().hex[:12]}" if owner_id else None
            source = {"kind": "url", "url": clean_url, "video_id": video_id,
                      "owner_id": owner_id, "project_id": project_id,
                      "limits": {"max_upload_bytes": max_bytes, "max_duration_s": max_duration}}
        else:
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
                raise
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except Exception as exc:
                logging.exception("Could not inspect uploaded video")
                raise HTTPException(400, "Không đọc được video. Hãy kiểm tra định dạng và nhật ký máy chủ.") from exc
            source = {
                "kind": "upload",
                "path": str(destination),
                "video_id": video_id,
                "title": Path(filename).stem or video_id,
                "owner_id": owner_id,
                "project_id": project_id,
                "limits": {"max_upload_bytes": max_bytes, "max_duration_s": max_duration},
            }

        job_id = _queue_job(source, source.get("title"), reservation_id=reservation_id)
        reservation_id = None
        usage_id = None
        queued = True
        return {"job_id": job_id, "status": "queued", "project_id": project_id}
    finally:
        if not queued:
            if vdir is not None:
                shutil.rmtree(vdir, ignore_errors=True)
            if usage_id is not None:
                accounts.release_ingest_job(owner_id, usage_id)
            _release_job_slot(reservation_id)
        if file:
            await file.close()


async def upload_document(file: UploadFile = File(...), owner_id=None, project_id=None):
    if not project_id:
        await file.close()
        raise HTTPException(422, "project_id là bắt buộc.")
    filename = Path(file.filename or "document.txt").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".txt", ".md", ".rst", ".pdf", ".docx"}:
        await file.close()
        raise HTTPException(415, "Định dạng chưa hỗ trợ. Hãy tải TXT, MD, PDF hoặc DOCX.")
    cfg = load_config()
    guest = str(owner_id or "").startswith("g:")
    role_limits = cfg.get("limits", {}).get("guest" if guest else "user", {})
    reservation_id = None
    usage_id = None
    queued = False
    upload_root = data_dir(cfg) / "documents" / ".uploads"
    document_id = f"doc-{uuid.uuid4().hex[:12]}"
    path = upload_root / f"{document_id}{suffix}"
    try:
        reservation_id = _reserve_job_slot(owner_id, role_limits, cfg.get("ingest", {}))
        max_jobs_per_day = int(role_limits.get("max_jobs_per_day", 0))
        if max_jobs_per_day > 0:
            usage_id = accounts.reserve_ingest_job(owner_id, max_jobs_per_day)
        upload_root.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with path.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    document_limit = 20 * 1024 * 1024 if guest else 100 * 1024 * 1024
                    if size > document_limit:
                        raise HTTPException(413, "Tài liệu vượt giới hạn cho phép.")
                    out.write(chunk)
        except HTTPException:
            raise
        except Exception as exc:
            logging.exception("Could not save uploaded document")
            raise HTTPException(400, "Không lưu được tài liệu. Hãy kiểm tra dung lượng và nhật ký máy chủ.") from exc

        source = {"kind": "document", "path": str(path), "document_id": document_id, "title": Path(filename).stem}
        source["owner_id"] = owner_id
        source["project_id"] = project_id
        job_id = _queue_job(source, source["title"], reservation_id=reservation_id)
        reservation_id = None
        usage_id = None
        queued = True
        return {"job_id": job_id, "status": "queued", "project_id": project_id}
    finally:
        if not queued:
            path.unlink(missing_ok=True)
            if usage_id is not None:
                accounts.release_ingest_job(owner_id, usage_id)
            _release_job_slot(reservation_id)
        await file.close()


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
