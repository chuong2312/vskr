"""Tải video/âm thanh từ các nguồn yt-dlp hỗ trợ, tách audio 16kHz mono."""
import subprocess
from pathlib import Path

from .config import video_dir
from .util import write_json


def probe_duration(path):
    """Read media duration using ffprobe without decoding the whole file."""
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Không đọc được thời lượng video:\n{proc.stderr[-800:]}")
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("ffprobe không trả về thời lượng hợp lệ.") from exc


def check_duration(duration, cfg):
    limit = int(cfg.get("ingest", {}).get("max_duration_s", 7200))
    if duration is not None and float(duration) > limit:
        limit_label = f"{limit // 3600} giờ" if limit >= 3600 and limit % 3600 == 0 else f"{limit // 60} phút"
        raise ValueError(
            f"Video dài {float(duration) / 60:.1f} phút; giới hạn hiện tại là {limit_label}. "
            "VSKR tự chia video thành các phần nhỏ khi nhận dạng lời nói."
        )


def _extract_audio(src, vdir):
    audio_path = Path(vdir) / "audio.wav"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(audio_path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg lỗi khi tách audio:\n{proc.stderr[-800:]}")
    return audio_path


def ingest_file(path, cfg, video_id, title=None):
    """Register a user-uploaded local video and extract 16 kHz mono audio."""
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


def ingest(url, cfg, with_video=False):
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    check_duration(info.get("duration"), cfg)
    vid = info["id"]
    vdir = video_dir(cfg, vid)
    vdir.mkdir(parents=True, exist_ok=True)

    opts = {
        "quiet": True,
        "noplaylist": True,
        "outtmpl": str(vdir / "source.%(ext)s"),
        "format": "bestaudio/best",
        "max_filesize": int(cfg.get("ingest", {}).get("max_upload_bytes", 1_073_741_824)),
    }
    if with_video:
        opts["format"] = "bv*[height<=720]+ba/b[height<=720]"
        opts["merge_output_format"] = "mp4"
    with yt_dlp.YoutubeDL(opts) as ydl:
        res = ydl.extract_info(url, download=True)
    src = Path(res["requested_downloads"][0]["filepath"])
    duration = info.get("duration") or probe_duration(src)
    check_duration(duration, cfg)
    _extract_audio(src, vdir)
    meta = {
        "video_id": vid,
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": duration,
        "url": res.get("webpage_url") or info.get("webpage_url") or url,
        "extractor": res.get("extractor_key") or info.get("extractor_key"),
        "source_type": "url",
        "has_video_file": bool(with_video),
        "source_file": src.name,
    }
    write_json(vdir / "meta.json", meta)
    return meta
