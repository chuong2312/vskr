import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import data_dir, index_dir, load_config, video_dir, videos_dir
from ..index import load_meta
from ..ingest import check_duration, ingest, ingest_file, probe_duration, resolve_source
from ..util import fmt_time, read_json, write_json
from .. import audit, accounts
from ..visibility import is_visible
from .ssrf import validate_ingest_url


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


class RetrievalExperiment(BaseModel):
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


state = {"service": None, "error": None, "failed_at": 0.0}
GPU_LOCK_TIMEOUT_S = 10
_lock = threading.Lock()
_service_use_lock = threading.RLock()
_active_service_users = {}
_gpu_model_lock = threading.Lock()
_jobs_lock = threading.Lock()
_index_build_lock = threading.RLock()
_ollama_status_lock = threading.Lock()
_ollama_status_cache = {"checked_at": 0.0, "connected": False}
_jobs = {}
_job_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vskr-pipeline")


def _acquire_service():
    """Lease the current service so reload can swap it without closing active readers."""
    with _service_use_lock:
        service = state["service"]
        if service is not None:
            service_id = id(service)
            _active_service_users[service_id] = _active_service_users.get(service_id, 0) + 1
        return service


def _close_service(service):
    embedder = getattr(service, "embedder", None)
    close = getattr(embedder, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logging.exception("Could not close retired VSKR embedder")


def _replace_service(service, error=None):
    close_previous = None
    with _service_use_lock:
        previous = state["service"]
        state["service"] = service
        state["error"] = error
        if previous is not None and _active_service_users.get(id(previous), 0) == 0:
            close_previous = previous
    if close_previous is not None:
        _close_service(close_previous)


def _release_service(service):
    close_retired = False
    service_id = id(service)
    with _service_use_lock:
        active = _active_service_users.get(service_id, 0)
        if active <= 1:
            _active_service_users.pop(service_id, None)
            close_retired = state["service"] is not service
        else:
            _active_service_users[service_id] = active - 1
    if close_retired:
        _close_service(service)


def _init_service(*, force=False):
    """Nạp chỉ mục + embedder. Có thể gọi lại: nhờ vậy chạy `index` xong là dùng được ngay,
    không cần khởi động lại server (quan trọng khi `serve` và `index` chạy ở hai container khác nhau)."""
    with _lock:
        if (not force and state["service"] is None and state.get("failed_at", 0)
                and time.time() - state["failed_at"] < 30):
            return
        try:
            from ..service import Service

            replacement = Service(load_config())
            _replace_service(replacement)
            state["failed_at"] = 0.0
        except Exception as e:  # chưa có chỉ mục, thiếu thư viện...
            logging.exception("Could not initialize VSKR service")
            state["failed_at"] = time.time()
            _replace_service(None, error=f"{type(e).__name__}: {e}")


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


def status():
    with _jobs_lock:
        running = next((job for job in _jobs.values() if job.get("status") == "running"), None)
    return {"ready": state["service"] is not None, "error": state["error"],
            "gpu_busy": _gpu_model_lock.locked(), "phase": running.get("phase") if running else None}


def _safe_diagnostic(value):
    text = str(value or "").splitlines()[0].strip()
    text = re.sub(r"(?i)\b(password|token|secret|api[_ -]?key)\s*[:=]\s*\S+", r"\1=[đã ẩn]", text)
    text = re.sub(r"(?i)\b(bearer|authorization)\s+\S+", r"\1 [đã ẩn]", text)
    text = re.sub(r"https?://[^\s/@:]+:[^\s/@]+@", "https://[credential]@", text)
    text = re.sub(r"https?://[^\s/?#]+\?[^\s#]+", lambda match: match.group(0).split("?", 1)[0] + "?[tham số ẩn]", text)
    text = re.sub(r"(?<![\w])(?:[A-Za-z]:)?[/\\](?:[^\s,;]+)", "[đường dẫn ẩn]", text)
    return text[:300]


def admin_status():
    full = status()
    service = state["service"]
    if full["error"]:
        error_type, _, message = full["error"].partition(":")
        full["error"] = {"type": error_type[:80], "detail": _safe_diagnostic(message)}
    videos_count = len(service.meta) if service else len(load_meta(load_config()))
    documents_count = len(service.document_meta) if service else 0
    index_info = {}
    if service:
        info = service.index.info or {}
        index_info = {
            key: info[key]
            for key in ("created_at", "version")
            if isinstance(info.get(key), (str, int, float))
        }
        index_info["chunk_count"] = info.get("chunk_count", info.get("n_chunks", len(service.index.chunks)))
        documents_count = len({chunk.get("document_id") for chunk in service.index.chunks if chunk.get("document_id")})
        videos_count = len({chunk.get("video_id") for chunk in service.index.chunks if chunk.get("video_id")})
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


def _owner_is_visible(item_owner_id, owner_id=None, *, shared_only=False):
    return is_visible({"owner_id": item_owner_id}, owner_id, shared_only=shared_only)


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
    from ..documents import list_documents
    item = next((entry for entry in list_documents(load_config())
                 if entry.get("document_id") == source_id), None)
    return bool(item and _meta_is_visible(item, owner_id, shared_only=shared_only)
                and _indexed_document(source_id, owner_id, shared_only=shared_only))


def _project_owner(owner_id):
    return owner_id[2:] if isinstance(owner_id, str) and owner_id.startswith("u:") else owner_id


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
        if not item_project and item_owner:
            item_project = accounts.get_or_create_default_project(_project_owner(item_owner))["project_id"]
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
    from ..documents import list_documents

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
        if not item_project and item_owner:
            item_project = accounts.get_or_create_default_project(_project_owner(item_owner))["project_id"]
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
            from ..documents import ingest_document

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
            from ..frames import detect_scenes_only

            scene_result = detect_scenes_only(cfg, video_id)
            if scene_result.get("detection_status") == "fallback":
                warnings.append("Không đọc được phân cảnh; đã tiếp tục lập chỉ mục bằng transcript.")
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
            "owner_id": source.get("owner_id"),
            "project_id": source.get("project_id"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "_source": source,
        }
    _job_executor.submit(_process_ingest_job, job_id, source)
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
    with _jobs_lock:
        active = sum(1 for job in _jobs.values()
                     if job.get("owner_id") == owner_id and job.get("status") in {"queued", "running"})
        if active >= int(role_limits.get("max_active_jobs", 2)):
            raise HTTPException(429, "Bạn đã đạt số tác vụ đang xử lý tối đa.")
        if sum(1 for job in _jobs.values() if job.get("status") in {"queued", "running"}) >= int(ingest_cfg.get("max_queue_length", 20)):
            raise HTTPException(503, "Hàng đợi xử lý đang đầy.", headers={"Retry-After": "30"})
    cfg = dict(cfg)
    cfg["ingest"] = dict(ingest_cfg, max_upload_bytes=max_bytes, max_duration_s=max_duration)
    source = None
    if clean_url:
        validate_ingest_url(clean_url)
        video_id = f"user-{owner_id[:10]}-{uuid.uuid4().hex[:12]}" if owner_id else None
        source = {"kind": "url", "url": clean_url, "video_id": video_id,
                  "owner_id": owner_id, "project_id": project_id,
                  "limits": {"max_upload_bytes": max_bytes, "max_duration_s": max_duration}}
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
            "owner_id": owner_id,
            "project_id": project_id,
            "limits": {"max_upload_bytes": max_bytes, "max_duration_s": max_duration},
        }

    job_id = _queue_job(source, source.get("title"))
    return {"job_id": job_id, "status": "queued", "project_id": project_id}


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
    with _jobs_lock:
        active = sum(1 for job in _jobs.values()
                     if job.get("owner_id") == owner_id and job.get("status") in {"queued", "running"})
        if active >= int(role_limits.get("max_active_jobs", 2)):
            raise HTTPException(429, "Bạn đã đạt số tác vụ đang xử lý tối đa.")
    upload_root = data_dir(cfg) / "documents" / ".uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    document_id = f"doc-{uuid.uuid4().hex[:12]}"
    path = upload_root / f"{document_id}{suffix}"
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
        path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        logging.exception("Could not save uploaded document")
        path.unlink(missing_ok=True)
        raise HTTPException(400, "Không lưu được tài liệu. Hãy kiểm tra dung lượng và nhật ký máy chủ.") from exc
    finally:
        await file.close()
    source = {"kind": "document", "path": str(path), "document_id": document_id, "title": Path(filename).stem}
    source["owner_id"] = owner_id
    source["project_id"] = project_id
    job_id = _queue_job(source, source["title"])
    return {"job_id": job_id, "status": "queued", "project_id": project_id}


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
    _init_service(force=True)
    return admin_status()


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
                               project_id=project_id)
        finally:
            _release_service(service)
    except HTTPException:
        raise
    except RuntimeError as e:
        from ..service import ModelBusyError

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


def get_eval_questions():
    return _read_questions()


def get_eval_results():
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
    content = await file.read(5 * 1024 * 1024 + 1)
    await file.close()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "Tệp JSONL vượt giới hạn 5 MB.")
    try:
        raw_items = [json.loads(line) for line in content.decode("utf-8-sig").splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"Không đọc được JSONL: {exc}") from exc
    return save_eval_questions({"questions": raw_items})


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


def run_retrieval_evaluation(options: RetrievalExperiment = RetrievalExperiment()):
    questions = _read_questions()
    if not questions:
        raise HTTPException(409, "Chưa có câu hỏi đánh giá. Hãy thêm câu hỏi và nguồn đúng trước.")
    modes = ("vector", "bm25", "hybrid", "graph", "hybrid_graph")
    hits = {mode: {} for mode in modes}
    method_labels = {
        "hybrid_graph": "Kết hợp + đồ thị (mặc định)",
        "hybrid": "Ngữ nghĩa + từ khóa",
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
    from ..evaluation import score_results

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


def inspect_graph(query: str, adjacent: bool = False, idf_weighting: bool = False,
                  semantic_expansion: bool = False, ppr: bool = False,
                  damping: float = 0.5, seed_weight: float = 0.05):
    if not 0.0 <= damping <= 0.99 or not 0.0 <= seed_weight <= 1.0:
        raise HTTPException(422, "Tham số lan truyền graph phải nằm trong khoảng hợp lệ.")
    service = _acquire_service()
    if service is None:
        _init_service()
        service = _acquire_service()
    if service is None:
        raise HTTPException(503, "Dịch vụ chưa sẵn sàng. Hãy kiểm tra nhật ký máy chủ.")
    try:
        graph = service.index.graph
        if not graph.get("nodes"):
            raise HTTPException(409, "Chỉ mục chưa có Đồ thị tri thức. Hãy chạy lại: vskr index")
        hits = service.retriever.search(query, mode="graph", top_k=8, experiment={
            "idf_weighting": idf_weighting, "semantic_expansion": semantic_expansion,
            "ppr": ppr, "damping": damping, "seed_weight": seed_weight,
        })
        chunks = service.index.chunks
        video_meta = service.meta
    finally:
        _release_service(service)
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
                item["title"] = item.get("title") or video_meta.get(item["video_id"], {}).get("title")
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
            "experiments": {"idf_weighting": idf_weighting, "semantic_expansion": semantic_expansion,
                           "ppr": ppr, "damping": damping, "seed_weight": seed_weight},
            "score_name": "điểm graph có áp dụng tùy chọn thử nghiệm nếu được bật"}
