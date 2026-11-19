"""Thư viện học liệu: liệt kê, xoá, mở file và kiểm tra quyền xem video/tài liệu.

Quy tắc quyền xem duy nhất nằm ở :func:`vskr.access.visibility.is_visible`; các hàm ở đây chỉ áp
quy tắc đó lên metadata của từng học liệu.
"""
import logging
import mimetypes
import os
import shutil
import uuid
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from fastapi.responses import FileResponse

from ...config import data_dir, index_dir, load_config, video_dir, videos_dir
from ...retrieval.index import load_meta
from ...pipeline.ingest import resolve_source
from ...util import read_json
from ...access.visibility import is_visible
from .runtime import _index_build_lock, _init_service, _jobs, _jobs_lock, state
from .jobs import _forget_jobs, _rebuild_index


def videos():
    cfg = load_config()
    metadata = state["service"].meta if state["service"] is not None else load_meta(cfg)
    return [
        {
            "video_id": meta["video_id"],
            "title": meta.get("title"),
            "duration": meta.get("duration"),
            "url": _safe_source_url(meta.get("url")),
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


def _safe_source_url(raw_url):
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
    video_items = []
    for item in videos():
        video_items.append({"id": item["video_id"], "kind": "video", "title": item.get("title") or item["video_id"],
                            "status": item["status"], "source_type": item["source_type"], "duration": item.get("duration"),
                            "file_url": item.get("file_url"), "url": item.get("url"), "job_id": item.get("job_id")})
    from ...pipeline.documents import list_documents
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
    _init_service(force=True)


def delete_video(video_id: str):
    cfg = load_config()
    root = videos_dir(cfg).resolve()
    target = video_dir(cfg, video_id).resolve()
    if target == root or not target.is_relative_to(root):
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
    return {"deleted": video_id, "already_absent": False, "ready": state["service"] is not None}


def delete_document(document_id: str):
    cfg = load_config()
    root = (data_dir(cfg) / "documents").resolve()
    target = (root / document_id).resolve()
    if target == root or not target.is_relative_to(root):
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
    return {"deleted": document_id, "already_absent": False, "ready": state["service"] is not None}


def document_section(document_id: str, section: int, owner_id=None, *, shared_only=False):
    cfg = load_config()
    root = (data_dir(cfg) / "documents").resolve()
    doc_dir = (root / document_id).resolve()
    if not doc_dir.is_relative_to(root):
        raise HTTPException(404, "Không tìm thấy tài liệu.")
    meta_path, sections_path = doc_dir / "meta.json", doc_dir / "sections.json"
    if not meta_path.is_file() or not sections_path.is_file():
        raise HTTPException(404, "Không tìm thấy tài liệu.")
    meta = read_json(meta_path)
    if not _meta_is_visible(meta, owner_id, shared_only=shared_only):
        raise HTTPException(404, "Không tìm thấy mục tài liệu.")
    chunks = read_json(sections_path).get("chunks", [])
    match = next((c for c in chunks if c.get("section") == section), None)
    if match is None:
        raise HTTPException(404, "Không tìm thấy mục tài liệu.")
    return {"document_id": document_id, "title": meta.get("title"), "section": section, "text": match["text"]}


def uploaded_video_file(video_id: str, owner_id=None, *, shared_only=False):
    cfg = load_config()
    video_root = videos_dir(cfg).resolve()
    vdir = video_dir(cfg, video_id)
    if not vdir.resolve().is_relative_to(video_root):
        raise HTTPException(404, "Không tìm thấy video.")
    meta_path = vdir / "meta.json"
    if not meta_path.is_file():
        raise HTTPException(404, "Không tìm thấy video.")
    meta = read_json(meta_path)
    if not _meta_is_visible(meta, owner_id, shared_only=shared_only):
        raise HTTPException(404, "Không tìm thấy video.")
    if meta.get("source_type") != "upload" or not meta.get("has_video_file"):
        raise HTTPException(404, "Video này không có file upload để phát trực tiếp.")
    source_path = resolve_source(vdir, meta)
    if source_path is None or not source_path.resolve().is_relative_to(vdir.resolve()):
        raise HTTPException(404, "File video không còn trên máy.")
    media_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
    return FileResponse(source_path, media_type=media_type)


def _meta_is_visible(meta, owner_id=None, *, shared_only=False):
    return is_visible(meta, owner_id, shared_only=shared_only)


def _indexed_video(video_id: str, owner_id=None, *, shared_only=False) -> bool:
    service = state["service"]
    return bool(service and any(
        chunk.get("video_id") == video_id
        and _meta_is_visible(
            (service.meta.get(video_id) if
             (service.meta.get(video_id, {}).get("owner") or service.meta.get(video_id, {}).get("owner_id"))
             else {**service.meta.get(video_id, {}), "owner_id": chunk.get("owner_id")}),
            owner_id, shared_only=shared_only)
        for chunk in service.index.chunks
    ))


def _indexed_document(document_id: str, owner_id=None, *, shared_only=False) -> bool:
    service = state["service"]
    return bool(service and any(
        chunk.get("document_id") == document_id
        and _meta_is_visible(
            (service.document_meta.get(document_id) if
             (service.document_meta.get(document_id, {}).get("owner") or service.document_meta.get(document_id, {}).get("owner_id"))
             else {**service.document_meta.get(document_id, {}), "owner_id": chunk.get("owner_id")}),
            owner_id, shared_only=shared_only)
        for chunk in service.index.chunks
    ))


def source_is_visible(source_type: str, source_id: str | None, owner_id=None, *, shared_only=False) -> bool:
    if source_type == "library":
        return True
    if not source_id or source_type not in {"video", "document"}:
        return False
    if source_type == "video":
        meta = (state["service"].meta if state["service"] else {}).get(source_id)
        return bool(meta and _meta_is_visible(meta, owner_id, shared_only=shared_only)
                    and _indexed_video(source_id, owner_id, shared_only=shared_only))
    from ...pipeline.documents import list_documents
    item = next((entry for entry in list_documents(load_config())
                 if entry.get("document_id") == source_id), None)
    return bool(item and _meta_is_visible(item, owner_id, shared_only=shared_only)
                and _indexed_document(source_id, owner_id, shared_only=shared_only))


def public_videos(owner_id=None, *, shared_only=False, owned_only=False, project_id=None):
    if state["service"] is None:
        _init_service()
    service = state["service"]
    if service is None:
        return []
    video_owners = {}
    for chunk in service.index.chunks:
        video_id = chunk.get("video_id")
        if video_id:
            video_owners.setdefault(video_id, set()).add(chunk.get("owner_id"))
    result = []
    for meta in service.meta.values():
        video_id = meta.get("video_id")
        owners = video_owners.get(video_id, set())
        item_owner = meta.get("owner") or meta.get("owner_id") or next(
            (owner for owner in owners if owner), None
        )
        visibility_meta = meta if (meta.get("owner") or meta.get("owner_id")) else {
            **meta, "owner_id": item_owner,
        }
        if not _meta_is_visible(visibility_meta, owner_id, shared_only=shared_only):
            continue
        item_project = meta.get("project_id")
        if project_id and item_owner and item_project != project_id:
            continue
        if owned_only and (not owner_id or not owners or
                           any(item_owner not in {owner_id, f"u:{owner_id}"} for item_owner in owners)):
            continue
        if not owners:
            continue
        is_upload = meta.get("source_type") == "upload" and bool(meta.get("has_video_file"))
        host = (urlsplit(meta.get("url") or "").hostname or "").lower()
        playback_url = f"/api/videos/{video_id}/file" if is_upload else None
        if not playback_url and (
            host == "youtu.be"
            or host == "youtube.com"
            or host.endswith(".youtube.com")
            or host == "youtube-nocookie.com"
            or host.endswith(".youtube-nocookie.com")
        ):
            playback_url = f"https://www.youtube.com/watch?v={meta.get('youtube_id') or video_id}"
        if not playback_url:
            playback_url = _safe_source_url(meta.get("url"))
        result.append({
            "video_id": video_id,
            "title": meta.get("title") or video_id,
            "duration": meta.get("duration"),
            "playback_url": playback_url,
            "project_id": item_project,
        })
    return result


def public_catalog(owner_id=None, *, shared_only=False, owned_only=False, project_id=None):
    videos = public_videos(owner_id=owner_id, shared_only=shared_only, owned_only=owned_only,
                           project_id=project_id)
    cfg = load_config()
    from ...pipeline.documents import list_documents

    documents = []
    service = state["service"]
    document_owners = {}
    if service:
        for chunk in service.index.chunks:
            document_id = chunk.get("document_id")
            if document_id:
                document_owners.setdefault(document_id, set()).add(chunk.get("owner_id"))
    for item in list_documents(cfg):
        document_id = item.get("document_id")
        owners = document_owners.get(document_id, set())
        item_owner = item.get("owner") or item.get("owner_id") or next(
            (owner for owner in owners if owner), None
        )
        visibility_meta = item if (item.get("owner") or item.get("owner_id")) else {
            **item, "owner_id": item_owner,
        }
        if not _meta_is_visible(visibility_meta, owner_id, shared_only=shared_only):
            continue
        item_project = item.get("project_id")
        if project_id and item_owner and item_project != project_id:
            continue
        if owned_only and (not owner_id or not owners or
                           any(item_owner not in {owner_id, f"u:{owner_id}"} for item_owner in owners)):
            continue
        if not owners:
            continue
        section_file = data_dir(cfg) / "documents" / document_id / "sections.json"
        count = len(read_json(section_file).get("chunks", [])) if section_file.is_file() else 0
        documents.append({
            "document_id": document_id,
            "title": item.get("title") or item.get("filename") or document_id,
            "sections": count,
            "project_id": item_project,
        })
    return {"videos": videos, "documents": documents}
