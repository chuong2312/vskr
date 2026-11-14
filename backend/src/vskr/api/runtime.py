"""Application state, model lifetime leases, and process-local coordination."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

from .. import state_store
from ..config import load_config

state = {"service": None, "error": None}
GPU_LOCK_TIMEOUT_S = 10
_lock = threading.Lock()
_service_use_lock = threading.RLock()
_service_condition = threading.Condition(threading.RLock())
_service_active = {}
_retired_services = {}
_gpu_model_lock = threading.Lock()
_jobs_lock = threading.Lock()
_index_build_lock = threading.RLock()
_ollama_status_lock = threading.Lock()
_ollama_status_cache = {"checked_at": 0.0, "connected": False}
_jobs = state_store.DurableJobs()
_job_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vskr-pipeline")


@contextmanager
def service_lease():
    with _service_condition:
        service = state["service"]
        if service is not None:
            key = id(service)
            _service_active[key] = _service_active.get(key, 0) + 1
    try:
        yield service
    finally:
        if service is not None:
            close = None
            with _service_condition:
                key = id(service)
                remaining = _service_active.get(key, 1) - 1
                if remaining:
                    _service_active[key] = remaining
                else:
                    _service_active.pop(key, None)
                    close = _retired_services.pop(key, None)
            if close is not None:
                close.embedder.close()


def replace_service(replacement, error=None):
    close = None
    with _service_use_lock:
        with _service_condition:
            previous = state["service"]
            state["service"] = replacement
            state["error"] = error
            if previous is not None and previous is not replacement:
                key = id(previous)
                if _service_active.get(key, 0):
                    _retired_services[key] = previous
                else:
                    close = previous
    if close is not None:
        close.embedder.close()


def init_service():
    """Load a replacement service and retire the previous one after its users finish."""
    with _lock:
        try:
            from ..service import Service

            replace_service(Service(load_config()))
        except Exception as exc:  # Missing index and optional model libraries are expected at startup.
            logging.exception("Could not initialize VSKR service")
            replace_service(None, f"{type(exc).__name__}: {exc}")


def recover_jobs():
    state_store.recover_interrupted_jobs()
    with _jobs_lock:
        _jobs.load_persisted()
