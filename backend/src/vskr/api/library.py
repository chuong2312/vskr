"""Library, visibility, and content lifecycle operations."""

import logging
import mimetypes
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from fastapi.responses import FileResponse

from .. import state_store
from ..config import data_dir, index_dir, load_config, video_dir, videos_dir
from ..index import load_meta
from ..ingest import resolve_source
from ..util import is_youtube_url, read_json, youtube_watch_url
from .diagnostics import safe_diagnostic as _safe_diagnostic
from .runtime import (
    _gpu_model_lock, _index_build_lock, _jobs, _jobs_lock,
    _ollama_status_cache, _ollama_status_lock, init_service as _init_service, state,
)


def videos():
    cfg = load_config()
    metadata = state["service"].meta if state["service"] is not None else load_meta(cfg)
    return [
        {
            "video_id": meta["video_id"],
            "title": meta.get("title"),
            "duration": meta.get("duration"),
            "url": safe_source_url(meta.get("url")),
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


def safe_source_url(raw_url):
    if not raw_url:
        return None
    try:
        parsed = urlsplit(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        netloc = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except ValueError:
        return None


def _video_status(meta):
    vid = meta["video_id"]
    with _jobs_lock:
        jobs = list(_jobs.values())
    for job in reversed(jobs):
        if job.get("video_id") == vid and job.get("status") in ("queued", "running", "error"):
            label = "Tác vụ thất bại. Xem nhật ký máy chủ." if job["status"] == "error" else job.get("message") or job["status"]
            return {"state": job["status"], "label": label, "job_id": job["job_id"]}
    cfg = load_config()
    transcript_exists = (video_dir(cfg, vid) / "transcript.json").is_file()
    indexed = bool(state["service"] and any(c.get("video_id") == vid for c in state["service"].index.chunks))
    if indexed:
        return {"state": "ready", "label": "Đã lập chỉ mục"}
    if not transcript_exists:
        return {"state": "error", "label": "Chưa có transcript"}
    return {"state": "processing", "label": "Chưa lập chỉ mục"}


def library():
    cfg = load_config()
    private_items = state_store.private_item_ids()
    video_items = []
    for item in videos():
        video_items.append({"id": item["video_id"], "kind": "video", "title": item.get("title") or item["video_id"],
                            "status": item["status"], "source_type": item["source_type"], "duration": item.get("duration"),
                            "file_url": item.get("file_url"), "url": item.get("url"), "job_id": item.get("job_id"),
                            "is_public": item["video_id"] not in private_items["video"]})
    from ..documents import list_documents
    doc_items = []
    for item in list_documents(cfg):
        section_file = data_dir(cfg) / "documents" / item["document_id"] / "sections.json"
        count = len(read_json(section_file).get("chunks", [])) if section_file.is_file() else 0
        with _jobs_lock:
            doc_job = next((job for job in reversed(list(_jobs.values())) if job.get("document_id") == item["document_id"] and job.get("status") in ("queued", "running", "error")), None)
        indexed = bool(state["service"] and any(c.get("document_id") == item["document_id"] for c in state["service"].index.chunks))
        label = "Tác vụ thất bại. Xem nhật ký máy chủ." if doc_job and doc_job["status"] == "error" else (doc_job.get("message") if doc_job else None)
        status_info = ({"state": doc_job["status"], "label": label, "job_id": doc_job["job_id"]}
                       if doc_job else {"state": "ready" if indexed else "processing", "label": "Đã chia mục" if indexed else "Đang lập chỉ mục"})
        doc_items.append({"id": item["document_id"], "kind": "document", "title": item.get("title") or item.get("filename"),
                          "filename": item.get("filename"), "sections": count,
                          "status": status_info, "job_id": status_info.get("job_id"),
                          "is_public": item["document_id"] not in private_items["document"]})
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
        from .jobs import _rebuild_index

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
            logging.exception("Failed to remove video %s", video_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tombstone, target)
            raise HTTPException(500, "Chưa xóa được video; dữ liệu gốc đã được giữ lại. Hãy kiểm tra nhật ký máy chủ.") from exc
        shutil.rmtree(tombstone, ignore_errors=True)
        _forget_jobs("video", video_id)
        state_store.delete_visibility("video", video_id)
    return {"deleted": video_id, "already_absent": False, "ready": state["service"] is not None}


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
            logging.exception("Failed to remove document %s", document_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tombstone, target)
            raise HTTPException(500, "Chưa xóa được tài liệu; dữ liệu gốc đã được giữ lại. Hãy kiểm tra nhật ký máy chủ.") from exc
        shutil.rmtree(tombstone, ignore_errors=True)
        _forget_jobs("document", document_id)
        state_store.delete_visibility("document", document_id)
    return {"deleted": document_id, "already_absent": False, "ready": state["service"] is not None}


def delete_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") in ("queued", "running"):
            raise HTTPException(409, "Chỉ có thể xóa tác vụ đã hoàn tất hoặc bị lỗi.")
        source = job.get("_source", {})
        video_id = job.get("video_id")
    cfg = load_config()
    with _index_build_lock:
        if source.get("path"):
            path = Path(source["path"]).resolve()
            if source.get("kind") == "upload":
                root = videos_dir(cfg).resolve()
                indexed = bool(
                    video_id
                    and (
                        video_is_indexed(video_id)
                        or _persisted_index_contains("video_id", video_id)
                    )
                )
                if path.is_relative_to(root) and not indexed:
                    path.unlink(missing_ok=True)
                    if video_id:
                        target = video_dir(cfg, video_id).resolve()
                        if target != root and target.is_relative_to(root):
                            shutil.rmtree(target, ignore_errors=True)
            else:
                root = (data_dir(cfg) / "documents" / ".uploads").resolve()
                if path.is_relative_to(root):
                    path.unlink(missing_ok=True)
        document_id = source.get("document_id")
        indexed_document = bool(
            document_id
            and (
                document_is_indexed(document_id)
                or _persisted_index_contains("document_id", document_id)
            )
        )
        if document_id and not indexed_document:
            root = (data_dir(cfg) / "documents").resolve()
            document_path = (root / document_id).resolve()
            if document_path != root and document_path.is_relative_to(root):
                shutil.rmtree(document_path, ignore_errors=True)
        with _jobs_lock:
            _jobs.pop(job_id, None)
    return {"deleted": job_id}


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


def status():
    with _jobs_lock:
        running = next((job for job in _jobs.values() if job.get("status") == "running"), None)
    return {"ready": state["service"] is not None, "error": state["error"],
            "gpu_busy": _gpu_model_lock.locked(), "phase": running.get("phase") if running else None}


def service_ready() -> bool:
    return state["service"] is not None


def admin_status():
    full = status()
    service = state["service"]
    if full["error"]:
        error_type, _, message = full["error"].partition(":")
        full["error"] = {"type": error_type[:80], "detail": _safe_diagnostic(message)}
    videos_count = len(service.meta) if service else len(load_meta(load_config()))
    documents_count = len(service.document_meta) if service else 0
    index_info = {}
    cfg = load_config()
    info_path = index_dir(cfg) / "index_info.json"
    if service or info_path.is_file():
        info = (service.index.info if service else read_json(info_path)) or {}
        index_info = {
            key: info[key]
            for key in ("chunk_count", "created_at", "version")
            if isinstance(info.get(key), (str, int, float))
        }
        if service:
            index_info.setdefault("chunk_count", len(service.index.chunks))
            documents_count = len({chunk.get("document_id") for chunk in service.index.chunks if chunk.get("document_id")})
            videos_count = len({chunk.get("video_id") for chunk in service.index.chunks if chunk.get("video_id")})
        else:
            index_info.setdefault("chunk_count", int(info.get("n_chunks") or 0))
        if "created_at" not in index_info and info_path.is_file():
            modified = datetime.fromtimestamp(info_path.stat().st_mtime, tz=timezone.utc)
            index_info["created_at"] = modified.isoformat()
        index_info.setdefault("version", 1)
    now = time.monotonic()
    with _ollama_status_lock:
        if now - _ollama_status_cache["checked_at"] >= 5:
            connected = False
            try:
                import httpx

                base_url = load_config().get("llm", {}).get("base_url", "http://localhost:11434").rstrip("/")
                connected = httpx.get(base_url + "/api/tags", timeout=1.0).is_success
            except Exception:
                pass
            _ollama_status_cache.update({"checked_at": now, "connected": connected})
        ollama_connected = _ollama_status_cache["connected"]
    full.update({
        "video_count": videos_count,
        "document_count": documents_count,
        "index": index_info,
        "ollama_connected": ollama_connected,
    })
    return full


def video_is_indexed(video_id: str) -> bool:
    service = state["service"]
    return bool(service and any(chunk.get("video_id") == video_id for chunk in service.index.chunks))


def document_is_indexed(document_id: str) -> bool:
    service = state["service"]
    return bool(service and any(chunk.get("document_id") == document_id for chunk in service.index.chunks))


def _persisted_index_contains(field: str, item_id: str) -> bool:
    if field not in {"video_id", "document_id"}:
        raise ValueError("Unsupported index field")
    chunks_path = index_dir(load_config()) / "chunks.json"
    if chunks_path.is_file():
        return any(chunk.get(field) == item_id for chunk in read_json(chunks_path))
    return False


def public_videos():
    if state["service"] is None:
        _init_service()
    service = state["service"]
    if service is None:
        return []
    indexed_video_ids = {chunk.get("video_id") for chunk in service.index.chunks if chunk.get("video_id")}
    private_video_ids = state_store.private_item_ids()["video"]
    result = []
    for meta in service.meta.values():
        video_id = meta.get("video_id")
        if not video_id or video_id not in indexed_video_ids or video_id in private_video_ids:
            continue
        is_upload = meta.get("source_type") == "upload" and bool(meta.get("has_video_file"))
        playback_url = f"/api/videos/{video_id}/file" if is_upload else None
        if not playback_url and is_youtube_url(meta.get("url")):
            playback_url = youtube_watch_url(video_id)
        if not playback_url:
            playback_url = safe_source_url(meta.get("url"))
        result.append({
            "video_id": video_id,
            "title": meta.get("title") or video_id,
            "duration": meta.get("duration"),
            "playback_url": playback_url,
        })
    return result


def public_catalog():
    videos = public_videos()
    cfg = load_config()
    from ..documents import list_documents

    documents = []
    service = state["service"]
    private_document_ids = state_store.private_item_ids()["document"]
    indexed_document_ids = {
        chunk.get("document_id") for chunk in service.index.chunks if chunk.get("document_id")
    } if service else set()
    for item in list_documents(cfg):
        document_id = item.get("document_id")
        if not document_id or document_id not in indexed_document_ids or document_id in private_document_ids:
            continue
        section_file = data_dir(cfg) / "documents" / document_id / "sections.json"
        count = len(read_json(section_file).get("chunks", [])) if section_file.is_file() else 0
        documents.append({
            "document_id": document_id,
            "title": item.get("title") or item.get("filename") or document_id,
            "sections": count,
        })
    return {"videos": videos, "documents": documents}


def set_library_visibility(kind: str, item_id: str, is_public: bool):
    if kind == "video":
        exists = item_id in load_meta(load_config())
    elif kind == "document":
        from ..documents import list_documents

        exists = any(item.get("document_id") == item_id for item in list_documents(load_config()))
    else:
        raise HTTPException(404, "Không tìm thấy học liệu.")
    if not exists:
        raise HTTPException(404, "Không tìm thấy học liệu.")
    state_store.set_visibility(kind, item_id, is_public)
    return {"kind": kind, "id": item_id, "is_public": is_public}
