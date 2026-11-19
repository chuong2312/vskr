"""Trạng thái dùng chung của server: service (index + model), các khoá GPU/index và trạng thái hệ thống.

Mọi module khác trong ``handlers`` lấy trạng thái từ đây, nên đây là nơi duy nhất giữ biến toàn cục.
Job chạy tuần tự trên một luồng (``_job_executor``) để không vượt 8 GB VRAM; trạng thái job và phiên
admin chỉ nằm trong RAM, mất khi khởi động lại.
"""
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ...config import load_config
from ...retrieval.index import load_meta
from ...access import accounts


state = {"service": None, "error": None, "failed_at": 0.0}


_lock = threading.Lock()
_service_use_lock = threading.RLock()
_active_service_users = {}
_gpu_model_lock = threading.Lock()
_jobs_lock = threading.Lock()
_index_build_lock = threading.RLock()
_ollama_status_lock = threading.Lock()
_ollama_status_cache = {"checked_at": 0.0, "connected": False}
_jobs = {}
_job_reservations = {}
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
            accounts.migrate_legacy_guest_owners()
            from ...service import Service

            replacement = Service(load_config())
            _replace_service(replacement)
            state["failed_at"] = 0.0
        except Exception as e:  # chưa có chỉ mục, thiếu thư viện...
            logging.exception("Could not initialize VSKR service")
            state["failed_at"] = time.time()
            state["error"] = f"{type(e).__name__}: {e}"


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


def reload_index():
    """Nạp lại chỉ mục sau khi chạy lại `index` (đổi chunk/embedding) mà không cần khởi động lại server."""
    _init_service(force=True)
    return admin_status()
