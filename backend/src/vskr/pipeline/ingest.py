"""Tải video/âm thanh từ các nguồn yt-dlp hỗ trợ, tách audio 16kHz mono."""
import subprocess
from pathlib import Path

from ..config import video_dir
from ..util import safe_video_id, write_json


def probe_duration(path):
    """Đọc thời lượng file media bằng ffprobe, không cần giải mã cả file."""
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", "-protocol_whitelist", "file,pipe",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Không đọc được thời lượng video:\n{proc.stderr[-800:]}")
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("ffprobe không trả về thời lượng hợp lệ.") from exc


def check_duration(duration, cfg):
    """Ném ``ValueError`` (thông báo tiếng Việt) nếu video dài hơn ``ingest.max_duration_s``; không rõ thời lượng thì bỏ qua."""
    limit = int(cfg["ingest"]["max_duration_s"])
    if duration is not None and float(duration) > limit:
        limit_label = f"{limit // 3600} giờ" if limit >= 3600 and limit % 3600 == 0 else f"{limit // 60} phút"
        raise ValueError(
            f"Video dài {float(duration) / 60:.1f} phút; giới hạn hiện tại là {limit_label}. "
            "VSKR tự chia video thành các phần nhỏ khi nhận dạng lời nói."
        )


def _extract_audio(src, vdir):
    audio_path = Path(vdir) / "audio.wav"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-protocol_whitelist", "file,pipe", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", str(audio_path)],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg lỗi khi tách audio:\n{proc.stderr[-800:]}")
    return audio_path


def ingest_file(path, cfg, video_id, title=None):
    """Đăng ký video người dùng tải lên, tách audio 16 kHz mono và ghi ``meta.json``."""
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"Không tìm thấy file video: {src}")
    duration = probe_duration(src)
    check_duration(duration, cfg)
    vdir = video_dir(cfg, video_id)
    vdir.mkdir(parents=True, exist_ok=True)
    _extract_audio(src, vdir)
    meta = {
        "video_id": video_id,
        "title": title or src.stem,
        "uploader": None,
        "duration": duration,
        "url": None,
        "source_type": "upload",
        "has_video_file": True,
        "source_file": src.name,
        "visibility": "public",
    }
    write_json(vdir / "meta.json", meta)
    return meta


def resolve_source(vdir, meta):
    """Tìm file nguồn (video/âm thanh gốc) của một video. meta['source_file'] chỉ lưu TÊN file
    (tương đối so với thư mục video) để chuyển dữ liệu giữa máy/môi trường chạy không gãy đường dẫn.
    Tương thích bản cũ lưu đường dẫn tuyệt đối: nếu đường dẫn đó không còn, dùng tên file trong vdir."""
    name = meta.get("source_file")
    if not name:
        return None
    p = Path(name)
    if p.is_absolute() and p.exists():
        return p
    q = Path(vdir) / p.name
    return q if q.exists() else None


def ingest(url, cfg, with_video=False, video_id=None):
    """Tải audio (thêm video nếu ``with_video``) từ URL qua bộ tải yt-dlp an toàn, rồi tách audio 16 kHz mono.

    Từ chối livestream, video không rõ thời lượng hoặc dài quá ``ingest.max_duration_s``. Ghi và trả
    về metadata của video (``meta.json``).
    """
    import yt_dlp
    from .safe_ytdlp import make_safe_youtube_dl

    youtube_dl = make_safe_youtube_dl(yt_dlp)

    with youtube_dl({"quiet": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("is_live") or info.get("duration") is None:
        raise ValueError("Không hỗ trợ livestream hoặc video không xác định được thời lượng.")
    check_duration(info.get("duration"), cfg)
    youtube_id = str(info["id"])
    vid = video_id or youtube_id
    vid = safe_video_id(vid)
    vdir = video_dir(cfg, vid)
    vdir.mkdir(parents=True, exist_ok=True)

    opts = {
        "quiet": True,
        "noplaylist": True,
        "outtmpl": str(vdir / "source.%(ext)s"),
        "format": "bestaudio/best",
        "max_filesize": int(cfg["ingest"]["max_upload_bytes"]),
    }
    if with_video:
        opts["format"] = "bv*[height<=720]+ba/b[height<=720]"
        opts["merge_output_format"] = "mp4"
    with youtube_dl(opts) as ydl:
        res = ydl.extract_info(url, download=True)
    src = Path(res["requested_downloads"][0]["filepath"])
    duration = info.get("duration") or probe_duration(src)
    check_duration(duration, cfg)
    _extract_audio(src, vdir)
    meta = {
        "video_id": vid,
        "youtube_id": youtube_id,
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": duration,
        "url": res.get("webpage_url") or info.get("webpage_url") or url,
        "extractor": res.get("extractor_key") or info.get("extractor_key"),
        "source_type": "url",
        "has_video_file": bool(with_video),
        "source_file": src.name,
        "visibility": "public",
    }
    write_json(vdir / "meta.json", meta)
    return meta
