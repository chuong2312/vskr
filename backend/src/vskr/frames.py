"""Trích frame đại diện từ video cho OCR (giai đoạn mở rộng).

Chiến lược: PySceneDetect tìm ranh giới cảnh + lấy mẫu định kỳ (để không sót khi gõ code liên tục),
gộp hai nguồn thời điểm, rồi lọc frame trùng bằng dHash. Phần logic thuần nằm ở các hàm đầu file (có test)."""
import subprocess
import time

from .config import video_dir
from .ingest import resolve_source
from .util import read_json, write_json


# ---------- logic thuần (có test) ----------
def candidate_times(duration, scene_starts, interval_s=8.0, offset_s=1.0, min_gap_s=2.0):
    """Gộp thời điểm đầu cảnh (+offset để tránh frame đang chuyển cảnh) và mẫu định kỳ.
    Trả về [(t, source)] tăng dần; hai mốc gần nhau hơn min_gap_s thì giữ mốc 'scene'."""
    last = max(0.0, duration - 0.5)
    cands = []
    for s in scene_starts:
        cands.append((min(s + offset_s, last), "scene"))
    t = 0.0
    while t < duration:
        cands.append((min(t, last), "interval"))
        t += interval_s
    cands.sort(key=lambda x: x[0])
    kept = []
    for t, src in cands:
        if kept and t - kept[-1][0] < min_gap_s:
            if src == "scene" and kept[-1][1] == "interval":
                kept[-1] = (t, src)
            continue
        kept.append((t, src))
    return [(round(t, 2), s) for t, s in kept]


def cap_evenly(items, max_n):
    if max_n is None or len(items) <= max_n:
        return list(items)
    step = len(items) / max_n
    return [items[int(i * step)] for i in range(max_n)]


def hamming(a, b):
    return bin(a ^ b).count("1")


def dedupe_hashes(hashes, max_dist):
    """Trả về chỉ số các frame được giữ: giữ frame nếu khác frame ĐÃ GIỮ gần nhất hơn max_dist bit.
    (So với frame đã giữ, không phải frame liền trước, để thay đổi nhỏ tích lũy dần vẫn được bắt.)"""
    keep, last = [], None
    for i, h in enumerate(hashes):
        if last is None or hamming(h, last) > max_dist:
            keep.append(i)
            last = h
    return keep


def dhash(path):
    from PIL import Image

    img = Image.open(path).convert("L").resize((9, 8))
    px = list(img.tobytes())  # getdata() đã bị Pillow đánh dấu lỗi thời (bỏ ở Pillow 14)
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (1 if px[r * 9 + c] > px[r * 9 + c + 1] else 0)
    return bits


# ---------- phần chạy thật (cần scenedetect, ffmpeg, Pillow) ----------
def detect_scene_starts(video_path, threshold=27.0, frame_skip=0):
    from scenedetect import ContentDetector, SceneManager, open_video

    video = open_video(str(video_path))
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold))
    sm.detect_scenes(video, show_progress=True, frame_skip=frame_skip)
    return [start.get_seconds() for start, _end in sm.get_scene_list()]


def grab_frame(video_path, t, out_path, max_width=1280):
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video_path),
            "-frames:v", "1", "-vf", f"scale='min({max_width},iw)':-2", "-q:v", "3", str(out_path),
        ],
        check=True,
    )


def run(cfg, video_id, force=False):
    fc = cfg["frames"]
    vdir = video_dir(cfg, video_id)
    meta = read_json(vdir / "meta.json")
    vpath = resolve_source(vdir, meta)
    if not meta.get("has_video_file") or vpath is None:
        raise RuntimeError(f"{video_id}: chưa có file video. Chạy lại: ingest <url> --with-video")
    out_json = vdir / "frames.json"
    if out_json.exists() and not force:
        print(f"[frames] bỏ qua {video_id} (đã có frames.json, dùng --force để chạy lại)")
        return

    t0 = time.time()
    try:
        starts = detect_scene_starts(vpath, fc.get("scene_threshold", 27.0), fc.get("frame_skip", 0))
    except Exception as e:  # thiếu thư viện hoặc lỗi giải mã: vẫn chạy được bằng lấy mẫu định kỳ
        print(f"[frames] PySceneDetect lỗi ({type(e).__name__}: {e}); chỉ dùng lấy mẫu định kỳ")
        starts = []
    t_scene = time.time() - t0

    cands = candidate_times(
        float(meta["duration"]), starts, fc.get("interval_s", 8.0),
        fc.get("scene_offset_s", 1.0), fc.get("min_gap_s", 2.0),
    )
    n_raw = len(cands)
    cands = cap_evenly(cands, fc.get("max_frames", 2000))

    fdir = vdir / "frames"
    fdir.mkdir(exist_ok=True)
    rows = []
    for t, src in cands:
        p = fdir / f"f_{t:09.2f}.jpg"
        grab_frame(vpath, t, p, fc.get("max_width", 1280))
        rows.append({"t": t, "source": src, "path": f"frames/{p.name}", "hash": dhash(p)})
    keep = set(dedupe_hashes([r["hash"] for r in rows], fc.get("hash_dist", 4)))
    kept = []
    for i, r in enumerate(rows):
        if i in keep:
            r["hash"] = format(r["hash"], "016x")
            kept.append(r)
        else:
            (vdir / r["path"]).unlink(missing_ok=True)
    write_json(
        out_json,
        {
            "video_id": video_id,
            "params": fc,
            "n_scene_starts": len(starts),
            "n_candidates_before_cap": n_raw,
            "n_extracted": len(rows),
            "n_kept_after_dedupe": len(kept),
            "scene_detect_seconds": round(t_scene, 1),
            "total_seconds": round(time.time() - t0, 1),
            "frames": kept,
        },
    )
    print(
        f"[frames] {video_id}: {len(starts)} đầu cảnh, {len(rows)} frame trích, "
        f"{len(kept)} sau lọc trùng ({time.time() - t0:.0f}s)"
    )
