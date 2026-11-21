"""Giới hạn tần suất câu hỏi công khai, lưu trong RAM theo cửa sổ trượt.

Mỗi IP tối đa ``limits.ask_per_minute`` câu mỗi phút và ``limits.ask_per_hour`` câu mỗi giờ; cả server
xử lý tối đa ``limits.ask_concurrent`` câu cùng lúc (các giá trị nằm trong configs/models.yaml).
"""

from __future__ import annotations

import threading
import time
import itertools
from collections import OrderedDict, deque

from fastapi import HTTPException

from ..config import load_config

_lock = threading.Lock()
_ask_attempts: OrderedDict[str, deque[tuple[int, float]]] = OrderedDict()
_attempt_ids = itertools.count(1)
_ask_slots = threading.BoundedSemaphore(int(load_config()["limits"]["ask_concurrent"]))


def check_ask_rate(ip: str, now: float | None = None) -> int:
    """Ghi nhận một câu hỏi của IP; vượt ``ask_per_minute`` câu/phút hoặc ``ask_per_hour`` câu/giờ thì báo 429 kèm Retry-After. Trả về id lượt để hoàn lại nếu cần."""
    now = time.time() if now is None else now
    cfg = load_config()["limits"]
    limits = ((60.0, int(cfg["ask_per_minute"])), (3600.0, int(cfg["ask_per_hour"])))
    with _lock:
        attempts = _ask_attempts.setdefault(ip, deque())
        _ask_attempts.move_to_end(ip)
        while attempts and now - attempts[0][1] >= 3600:
            attempts.popleft()
        for window, maximum in limits:
            recent = [stamp for _attempt_id, stamp in attempts if now - stamp < window]
            if len(recent) >= maximum:
                retry_after = max(1, int(recent[0] + window - now + 0.999))
                raise HTTPException(
                    429,
                    "Bạn đã gửi quá nhiều câu hỏi. Vui lòng thử lại sau.",
                    headers={"Retry-After": str(retry_after)},
                )
        attempt_id = next(_attempt_ids)
        attempts.append((attempt_id, now))
        if len(_ask_attempts) > 10_000:
            while len(_ask_attempts) > 10_000:
                _ask_attempts.popitem(last=False)
        return attempt_id


def rollback_ask_rate(ip: str, attempt_id: int | None) -> None:
    """Không tính lượt hỏi không trả lời được vì lỗi phía server."""
    if attempt_id is None:
        return
    with _lock:
        attempts = _ask_attempts.get(ip)
        if not attempts:
            return
        for attempt in tuple(attempts):
            if attempt[0] == attempt_id:
                attempts.remove(attempt)
                break
        if not attempts:
            _ask_attempts.pop(ip, None)


def enter_ask_slot() -> None:
    """Giữ một chỗ xử lý câu hỏi; hết chỗ thì báo 503 kèm Retry-After."""
    if not _ask_slots.acquire(blocking=False):
        raise HTTPException(
            503,
            "Hệ thống đang xử lý nhiều câu hỏi cùng lúc, bạn thử lại sau vài giây.",
            headers={"Retry-After": "10"},
        )


def leave_ask_slot() -> None:
    """Trả chỗ xử lý câu hỏi đã giữ."""
    _ask_slots.release()
