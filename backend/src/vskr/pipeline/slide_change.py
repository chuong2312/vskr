"""Dò trạng thái nội dung trên màn hình của video bài giảng (slide, sơ đồ vẽ dần, code).

Một trạng thái mới bắt đầu khi đủ nhiều điểm ảnh đổi so với **trạng thái đã giữ gần nhất** và màn hình
đứng yên lại trong vài mẫu liên tiếp. Vì so với trạng thái đã giữ chứ không so với mẫu liền trước, việc
giảng viên thêm từng node hay từng dòng lên cùng một slide vẫn được cộng dồn và bắt được, điều mà dò cắt
cảnh không thấy. Điểm ảnh đổi liên tục suốt video (webcam, khung chat, đồng hồ) bị bỏ khỏi phép so; đốm
nhỏ cỡ con trỏ chuột bị bỏ qua; animation và chớp sáng bị bỏ vì chưa đứng yên.

Mỗi trạng thái có ``kind``: ``start`` (trạng thái đầu video), ``slide`` (vùng đổi trải khắp màn hình,
thường là slide mới) hoặc ``partial`` (đổi một chỗ: thêm hay sửa nội dung trên slide đang có). Slide mới
cùng mẫu nền chỉ đổi phần chữ nên phân loại theo độ trải rộng của vùng đổi, không theo số điểm ảnh đổi. ``bbox`` là khung bao vùng vừa đổi,
theo tỉ lệ 0–1 của khung hình, để sau này cắt gửi đúng phần chênh.

Bản thử nghiệm: chưa dùng khi ingest hay lập chỉ mục, chạy tay bằng ``vskr slides --video <id>``. Kết
quả nằm trong ``slide_states.json``, kèm các lần đổi bị loại và lý do để truy vết. Chưa kiểm lại bằng
chữ OCR (để sang bước gắn vào luồng xử lý chính, xem docs/ROADMAP.md).
"""
import subprocess
import tempfile
import time

import cv2
import numpy as np

from ..config import video_dir
from ..util import read_json, write_json
from .ingest import resolve_source

# Đổi khi thay thuật toán, để kết quả cũ trong slide_states.json không bị dùng lại nhầm.
ALGORITHM_VERSION = 1
PARAM_KEYS = (
    "sample_fps", "work_width", "pixel_threshold", "min_blob_area", "min_change_area",
    "stable_frames", "stable_area", "busy_ratio", "slide_area", "ignore_top", "ignore_bottom",
)
# Nới vùng đổi theo chiều ngang nhiều hơn chiều dọc để các chữ cái cạnh nhau gộp thành một đốm: một dòng
# chữ mới không bị coi là nhiều đốm nhỏ như con trỏ chuột. Kích thước (rộng, cao) tính cho ảnh rộng ~256 px.
_MERGE_KERNEL = (5, 3)
# Sau khi bỏ vùng đổi liên tục mà phần còn lại để so ít hơn tỉ lệ này thì coi là video không có slide
# (ví dụ chỉ quay camera) và không chia trạng thái.
_MIN_COMPARED_RATIO = 0.2
_BLANK_STD = 4.0          # độ lệch chuẩn độ sáng dưới mức này: màn hình trống (đen, trắng, chuyển cảnh)
_MAX_REJECTED = 500       # chỉ ghi chi tiết chừng này lần bị loại; tổng số vẫn được đếm


# ---------- logic thuần (có test) ----------
def ignore_mask(frames, pixel_threshold, busy_ratio, ignore_top=0.0, ignore_bottom=0.0):
    """Mặt nạ các điểm ảnh bỏ khỏi phép so (True = bỏ qua).

    Gồm điểm ảnh đổi ở ít nhất ``busy_ratio`` số cặp mẫu liền nhau (webcam, khung chat, đồng hồ), nới
    thêm 1 điểm ảnh quanh vùng đó, và hai dải trên/dưới theo tỉ lệ chiều cao.
    """
    height, width = frames[0].shape
    mask = np.zeros((height, width), dtype=bool)
    if len(frames) >= 3:
        counts = np.zeros((height, width), dtype=np.uint32)
        for prev, cur in zip(frames, frames[1:]):
            counts += cv2.absdiff(prev, cur) > pixel_threshold
        busy = counts >= busy_ratio * (len(frames) - 1)
        if busy.any():
            busy = cv2.dilate(busy.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        mask |= busy
    top = int(round(height * float(ignore_top)))
    bottom = int(round(height * float(ignore_bottom)))
    if top:
        mask[:top] = True
    if bottom:
        mask[height - bottom:] = True
    return mask


def change_area(a, b, ignore, pixel_threshold, min_blob_area):
    """Tỉ lệ vùng so sánh bị đổi giữa hai ảnh xám, sau khi bỏ đốm nhỏ; kèm khung bao vùng đổi.

    Điểm ảnh lệch hơn ``pixel_threshold`` được nới ra (gộp chữ cái thành dòng), rồi đốm nào nhỏ hơn
    ``min_blob_area`` (tỉ lệ khung hình) bị bỏ. Tỉ lệ tính trên phần đã nới. Trả về
    ``(tỉ lệ, [x0, y0, x1, y1])`` với toạ độ theo tỉ lệ 0–1, hoặc ``(0.0, None)`` nếu không có gì đổi.
    """
    diff = (cv2.absdiff(a, b) > pixel_threshold) & ~ignore
    if not diff.any():
        return 0.0, None
    kernel = np.ones((_MERGE_KERNEL[1], _MERGE_KERNEL[0]), np.uint8)
    merged = cv2.dilate(diff.astype(np.uint8), kernel)
    merged[ignore] = 0
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(merged, connectivity=8)
    height, width = a.shape
    min_pixels = float(min_blob_area) * height * width
    kept = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= min_pixels]
    if not kept:
        return 0.0, None
    compared = int((~ignore).sum())
    area = sum(int(stats[i, cv2.CC_STAT_AREA]) for i in kept) / compared
    x0 = min(int(stats[i, cv2.CC_STAT_LEFT]) for i in kept)
    y0 = min(int(stats[i, cv2.CC_STAT_TOP]) for i in kept)
    x1 = max(int(stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH]) for i in kept)
    y1 = max(int(stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT]) for i in kept)
    return area, [round(x0 / width, 3), round(y0 / height, 3), round(x1 / width, 3), round(y1 / height, 3)]


def _state(index, start, frame_t, kind, change, bbox, frame, ignore):
    """Một bản ghi trạng thái; ``end`` được điền khi biết trạng thái kế tiếp."""
    return {
        "id": index,
        "start": round(float(start), 2),
        "end": round(float(start), 2),
        "frame_t": round(float(frame_t), 2),
        "kind": kind,
        "change_area": None if change is None else round(float(change), 4),
        "bbox": bbox,
        "blank": bool(float(frame[~ignore].std()) < _BLANK_STD),
    }


def detect_states(frames, times, params):
    """Chia dãy ảnh xám đã thu nhỏ (cùng cỡ) thành các trạng thái nội dung. Hàm thuần, không đọc file.

    ``times[i]`` là giây của ``frames[i]``. Trạng thái mới được chốt khi mẫu khác trạng thái đã giữ ít
    nhất ``min_change_area`` và đứng yên ``stable_frames`` mẫu liên tiếp (hai mẫu liền nhau lệch dưới
    ``stable_area``); trạng thái bắt đầu ở mẫu đầu tiên của đoạn đứng yên đó.
    """
    p = params
    if not frames:
        return {"status": "empty", "ignored_ratio": 0.0, "state_count": 0, "states": [],
                "rejected_count": 0, "rejected": []}
    ignore = ignore_mask(frames, p["pixel_threshold"], p["busy_ratio"], p["ignore_top"], p["ignore_bottom"])
    ignored_ratio = round(float(ignore.mean()), 3)
    if 1.0 - ignored_ratio < _MIN_COMPARED_RATIO:
        return {"status": "too_dynamic", "ignored_ratio": ignored_ratio, "state_count": 0, "states": [],
                "rejected_count": 0, "rejected": []}

    def area(a, b):
        return change_area(a, b, ignore, p["pixel_threshold"], p["min_blob_area"])

    ref = frames[0]
    states = [_state(0, times[0], times[0], "start", None, None, ref, ignore)]
    rejected = []
    rejected_count = 0
    episode = None    # mẫu đầu tiên khác trạng thái đã giữ (để ghi lý do nếu lần đổi này bị loại)
    run_start = None  # mẫu đầu của đoạn đứng yên đang xét

    def reject(start_index, end_time, reason):
        nonlocal rejected_count
        rejected_count += 1
        if len(rejected) < _MAX_REJECTED:
            rejected.append({"start": round(float(times[start_index]), 2), "end": round(float(end_time), 2),
                             "reason": reason})

    for i in range(1, len(frames)):
        diff_ref, _bbox = area(ref, frames[i])
        if diff_ref < p["min_change_area"]:
            if episode is not None:
                reject(episode, times[i], "thoáng qua rồi trở lại như cũ")
            episode = run_start = None
            continue
        if episode is None:
            episode = i
        moving = area(frames[i - 1], frames[i])[0] >= p["stable_area"]
        if run_start is None or moving:
            run_start = i
        if i - run_start + 1 >= int(p["stable_frames"]):
            change, bbox = area(ref, frames[i])
            spread = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) if bbox else 0.0
            kind = "slide" if spread >= p["slide_area"] else "partial"
            states[-1]["end"] = round(float(times[run_start]), 2)
            states.append(_state(len(states), times[run_start], times[i], kind, change, bbox, frames[i], ignore))
            ref = frames[i]
            episode = run_start = None
    if episode is not None:
        reject(episode, times[-1], "chưa đứng yên tới hết video")
    states[-1]["end"] = round(float(times[-1]), 2)
    return {"status": "complete", "ignored_ratio": ignored_ratio, "state_count": len(states), "states": states,
            "rejected_count": rejected_count, "rejected": rejected}


def work_size(src_width, src_height, width):
    """Cỡ ảnh (rộng, cao) để so: thu về chiều rộng ``width`` (không phóng to), giữ tỉ lệ, cạnh chẵn."""
    w = min(int(width), int(src_width))
    w -= w % 2
    h = max(2, int(round(src_height * w / src_width / 2)) * 2)
    return w, h


def params_from(cfg):
    """Tham số dò trạng thái lấy từ mục ``slide_change`` trong models.yaml."""
    section = cfg["slide_change"]
    return {key: section[key] for key in PARAM_KEYS}


# ---------- phần chạy thật (cần ffmpeg, ffprobe) ----------
def probe_size(video_path):
    """Chiều rộng, chiều cao của luồng hình đầu tiên, đọc bằng ffprobe."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video_path),
        ],
        capture_output=True, text=True, check=True, timeout=120,
    )
    width, height = (int(x) for x in out.stdout.strip().splitlines()[0].split(",")[:2])
    return width, height


def read_gray_frames(video_path, fps, size):
    """Giải mã video một lần: ``fps`` ảnh xám mỗi giây, cỡ ``size`` (rộng, cao), làm mờ nhẹ để bớt nhiễu nén.

    Ảnh đọc thẳng từ pipe của ffmpeg, không ghi ra đĩa. Toàn bộ ảnh được giữ trong RAM vì bước tìm vùng
    đổi liên tục cần cả video: ảnh 256×144 ở 1 mẫu/giây tốn khoảng 133 MB cho mỗi giờ video.
    """
    width, height = size
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file,pipe",
        "-i", str(video_path), "-an", "-sn",
        "-vf", f"fps={fps},scale={width}:{height}:flags=area,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    frame_bytes = width * height
    frames = []
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors)
        try:
            while True:
                buf = proc.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    break
                image = np.frombuffer(buf, dtype=np.uint8).reshape(height, width)
                frames.append(cv2.GaussianBlur(image, (3, 3), 0))
        finally:
            proc.stdout.close()
            code = proc.wait()
        if code != 0:
            errors.seek(0)
            detail = errors.read().decode("utf-8", "replace").strip()[-500:]
            raise RuntimeError(f"ffmpeg giải mã lỗi (mã {code}): {detail}")
    return frames


def run(cfg, video_id, force=False):
    """Dò trạng thái cho một video và ghi ``slide_states.json``.

    Đã có file với cùng tham số và cùng phiên bản thuật toán thì dùng lại, trừ khi ``force``.
    """
    params = params_from(cfg)
    vdir = video_dir(cfg, video_id)
    meta = read_json(vdir / "meta.json")
    vpath = resolve_source(vdir, meta)
    if not meta.get("has_video_file") or vpath is None:
        raise RuntimeError(f"{video_id}: chưa có file video nên không dò được trạng thái slide.")
    out = vdir / "slide_states.json"
    if out.is_file() and not force:
        cached = read_json(out)
        if cached.get("params") == params and cached.get("algorithm_version") == ALGORITHM_VERSION:
            print(f"[slides] bỏ qua {video_id} (đã có slide_states.json cùng tham số, dùng --force để chạy lại)")
            return cached

    t0 = time.time()
    size = work_size(*probe_size(vpath), params["work_width"])
    frames = read_gray_frames(vpath, params["sample_fps"], size)
    t_decode = time.time() - t0
    times = [i / float(params["sample_fps"]) for i in range(len(frames))]
    result = detect_states(frames, times, params)
    duration = meta.get("duration")
    if result["states"] and duration:
        result["states"][-1]["end"] = round(float(duration), 2)
    payload = {
        "video_id": video_id,
        "algorithm_version": ALGORITHM_VERSION,
        "params": params,
        "frame_size": list(size),
        "sample_count": len(frames),
        **result,
        "decode_seconds": round(t_decode, 1),
        "total_seconds": round(time.time() - t0, 1),
    }
    write_json(out, payload)
    kinds = {}
    for state in result["states"]:
        kinds[state["kind"]] = kinds.get(state["kind"], 0) + 1
    print(
        f"[slides] {video_id}: {len(frames)} mẫu, {result['state_count']} trạng thái {kinds}, "
        f"{result['rejected_count']} lần đổi bị loại, bỏ {result['ignored_ratio']:.0%} màn hình "
        f"({payload['total_seconds']:.0f}s, trạng thái: {result['status']})"
    )
    return payload
