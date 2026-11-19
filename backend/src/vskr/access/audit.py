"""Nhật ký audit của admin (``data/audit.jsonl``): chỉ ghi thêm, chỉ nhận một tập trường hẹp có chủ đích."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from ..config import data_dir, load_config

_lock = threading.Lock()


def write_event(event: str, *, actor: str = "admin", target_id: str | None = None, kind: str | None = None,
                result: str = "success") -> None:
    """Ghi một sự kiện audit, cắt ngắn từng trường. Không nhận mật khẩu, cookie, token hay IP thô."""
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": str(event)[:80],
        "actor": str(actor)[:120],
        "result": str(result)[:32],
    }
    if target_id is not None:
        entry["target_id"] = str(target_id)[:160]
    if kind is not None:
        entry["kind"] = str(kind)[:32]
    path = data_dir(load_config()) / "audit.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        logging.exception("Could not append VSKR audit event %s", event)


def recent_events(limit: int = 200) -> list[dict]:
    """Tối đa ``limit`` (không quá 200) sự kiện audit gần nhất; dòng hỏng bị bỏ qua, mỗi sự kiện chỉ giữ các trường được phép."""
    path = data_dir(load_config()) / "audit.jsonl"
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(1, min(int(limit), 200)):]
    except OSError:
        logging.exception("Could not read VSKR audit log")
        return []
    events = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append({
                "at": str(item.get("at", ""))[:40],
                "event": str(item.get("event", ""))[:80],
                "actor": str(item.get("actor", "admin"))[:120],
                "result": str(item.get("result", "success"))[:32],
                **({"target_id": str(item["target_id"])[:160]} if item.get("target_id") is not None else {}),
                **({"kind": str(item["kind"])[:32]} if item.get("kind") is not None else {}),
            })
    return events
