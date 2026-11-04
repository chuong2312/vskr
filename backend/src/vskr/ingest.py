"""Tải video/âm thanh từ các nguồn yt-dlp hỗ trợ, tách audio 16kHz mono."""
import subprocess
from pathlib import Path

from .config import video_dir
from .util import write_json


def resolve_source(vdir, meta):
    """Tìm file nguồn (video/âm thanh gốc) của một video. meta['source_file'] chỉ lưu TÊN file
    (tương đối so với thư mục video) để chuyển dữ liệu giữa máy/Docker không bị gãy đường dẫn.
    Tương thích bản cũ lưu đường dẫn tuyệt đối: nếu đường dẫn đó không còn, dùng tên file trong vdir."""
    name = meta.get("source_file")
    if not name:
        return None
    p = Path(name)
    if p.is_absolute() and p.exists():
        return p
    q = Path(vdir) / p.name
    return q if q.exists() else None


def ingest(url, cfg, with_video=False):
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    vid = info["id"]
    vdir = video_dir(cfg, vid)
    vdir.mkdir(parents=True, exist_ok=True)

    opts = {
        "quiet": True,
        "noplaylist": True,
        "outtmpl": str(vdir / "source.%(ext)s"),
        "format": "bestaudio/best",
    }
    if with_video:
        opts["format"] = "bv*[height<=720]+ba/b[height<=720]"
        opts["merge_output_format"] = "mp4"
    with yt_dlp.YoutubeDL(opts) as ydl:
        res = ydl.extract_info(url, download=True)
    src = Path(res["requested_downloads"][0]["filepath"])

    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(vdir / "audio.wav")],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg lỗi khi tách audio:\n{proc.stderr[-800:]}")
    meta = {
        "video_id": vid,
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "url": res.get("webpage_url") or info.get("webpage_url") or url,
        "extractor": res.get("extractor_key") or info.get("extractor_key"),
        "has_video_file": bool(with_video),
        "source_file": src.name,
    }
    write_json(vdir / "meta.json", meta)
    return meta
