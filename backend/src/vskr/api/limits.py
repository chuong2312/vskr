"""Small in-memory sliding-window limits for public question requests."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException

_lock = threading.Lock()
_ask_attempts: OrderedDict[str, deque[float]] = OrderedDict()
_ask_slots = threading.BoundedSemaphore(3)


def check_ask_rate(ip: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    limits = ((60.0, 10), (3600.0, 100))
    with _lock:
        attempts = _ask_attempts.setdefault(ip, deque())
        _ask_attempts.move_to_end(ip)
        while attempts and now - attempts[0] >= 3600:
            attempts.popleft()
        for window, maximum in limits:
            recent = [stamp for stamp in attempts if now - stamp < window]
            if len(recent) >= maximum:
                retry_after = max(1, int(recent[0] + window - now + 0.999))
                raise HTTPException(
                    429,
                    "Bạn đã gửi quá nhiều câu hỏi. Vui lòng thử lại sau.",
                    headers={"Retry-After": str(retry_after)},
                )
        attempts.append(now)
        if len(_ask_attempts) > 10_000:
            while len(_ask_attempts) > 10_000:
                _ask_attempts.popitem(last=False)


def enter_ask_slot() -> None:
    if not _ask_slots.acquire(blocking=False):
        raise HTTPException(
            503,
            "Hệ thống đang cập nhật học liệu, thử lại sau ít phút",
            headers={"Retry-After": "10"},
        )


def leave_ask_slot() -> None:
    _ask_slots.release()
