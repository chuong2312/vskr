"""Tiện ích dùng chung: định dạng thời gian, phụ đề SRT, link nguồn có mốc thời gian, đọc/ghi JSON."""
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SAFE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_json_write_lock = threading.Lock()


def safe_video_id(raw) -> str:
    """Id an toàn để làm tên thư mục: giữ nguyên id hợp lệ, id lạ thì băm SHA-256 (tiền tố ``ext-``)."""
    value = str(raw or "")
    if _SAFE_VIDEO_ID.fullmatch(value):
        return value
    return "ext-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def fmt_time(sec):
    """Định dạng số giây thành ``mm:ss`` hoặc ``h:mm:ss`` để hiển thị."""
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_srt_time(sec):
    """Định dạng số giây theo chuẩn SRT: ``hh:mm:ss,mmm``."""
    ms = max(0, int(round(sec * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments):
    """segments: [{start,end,text}] -> nội dung file .srt (bỏ đoạn rỗng, đánh số lại từ 1)."""
    blocks = []
    for s in segments:
        text = s["text"].strip()
        if not text:
            continue
        blocks.append(f"{len(blocks) + 1}\n{fmt_srt_time(s['start'])} --> {fmt_srt_time(s['end'])}\n{text}\n")
    return "\n".join(blocks)


_YOUTUBE_DOMAINS = ("youtube.com", "youtube-nocookie.com")


def is_youtube_host(host):
    """Host có phải YouTube không: youtu.be, youtube.com, youtube-nocookie.com hoặc tên miền con của chúng.

    So khớp trọn từng phần của tên miền, nên ``evilyoutube.com`` không được tính là YouTube.
    """
    host = (host or "").lower()
    return host == "youtu.be" or any(host == domain or host.endswith("." + domain) for domain in _YOUTUBE_DOMAINS)


def source_link(url, video_id, start):
    """Link tới đúng mốc thời gian: link YouTube được thêm tham số ``t``, nền tảng khác giữ nguyên URL.

    Không có URL thì dựng link YouTube từ ``video_id``.
    """
    if not url:
        return f"https://www.youtube.com/watch?v={video_id}&t={int(start)}s"
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if is_youtube_host(host):
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["t"] = f"{int(start)}s"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return url


def read_json(path):
    """Đọc một file JSON (UTF-8)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    """Ghi JSON (UTF-8, có thụt lề) theo kiểu nguyên tử: ghi ra file tạm rồi thay thế file đích."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # os.replace là nguyên tử trên cùng một ổ đĩa: tiến trình bị ngắt giữa chừng cũng không để lại
    # file metadata ghi dở.
    with _json_write_lock:
        temporary = target.with_name(target.name + ".tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, target)
