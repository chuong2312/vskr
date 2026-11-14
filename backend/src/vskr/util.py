import json
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


def fmt_time(sec):
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_srt_time(sec):
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


def is_youtube_url(url):
    try:
        host = (urlsplit(url or "").hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host in {"youtu.be", "youtube.com", "youtube-nocookie.com"} or host.endswith(
        (".youtube.com", ".youtube-nocookie.com")
    )


def youtube_watch_url(video_id, start=None):
    query = f"v={quote(str(video_id), safe='')}"
    if start is not None:
        query += f"&t={max(0, int(start))}s"
    return f"https://www.youtube.com/watch?{query}"


def source_link(url, video_id, start):
    """Build a timestamp link where the platform convention is known; otherwise keep source URL."""
    if not url:
        return youtube_watch_url(video_id, start)
    if is_youtube_url(url):
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["t"] = f"{int(start)}s"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return url


def yt_link(video_id, start):
    """Backward-compatible YouTube timestamp helper."""
    return source_link(None, video_id, start)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
