"""Lọc chữ rác khỏi kết quả OCR trước khi lập chỉ mục; screen_text.json gốc giữ nguyên.

Đo trên video thật: menu/thanh công cụ của phần mềm (pgAdmin), watermark kênh và thanh trạng thái
lặp lại ở gần như mọi frame, nằm ở mép trên/dưới màn hình, làm loãng từ khóa của mọi đoạn. OCR đọc
chúng hơi khác nhau mỗi frame ("WECOMMIT.COM.VN" / "WECOMMIT COMVN"), nên đếm theo TỪ chứ không theo
cả dòng: một dòng là rác nếu phần lớn từ của nó có mặt ở nhiều frame (đã lọc trùng dHash, nên một
slide chiếu lâu chỉ còn vài frame) và nó nằm ở mép màn hình. Dòng ở giữa màn hình luôn được giữ.

Ngoài ra bỏ dòng có dãy số dài: thường là mã số sinh viên trong khung chat họp trực tuyến.
"""
import bisect
import re

_TOKEN = re.compile(r"\w+")
_LONG_NUMBER = re.compile(r"\d{9,}")

DEFAULTS = {
    "enabled": True,
    "frequent_ratio": 0.3,
    "frequent_share": 0.6,
    "top_band": 0.25,
    "bottom_band": 0.1,
    "min_frames": 20,
    "drop_long_numbers": True,
}


def _tokens(text):
    return {t for t in _TOKEN.findall(text.lower()) if len(t) >= 2 and not t.isdigit()}


def _frames_covered(row, frame_times):
    """Chỉ số các frame mà dòng (đã gộp qua merge_runs thành khoảng start–end) có mặt."""
    lo = bisect.bisect_left(frame_times, row["start"] - 1e-6)
    hi = bisect.bisect_right(frame_times, row["end"] + 1e-6)
    return range(lo, hi)


def _y_center(box):
    ys = [point[1] for point in box]
    return (min(ys) + max(ys)) / 2


def frame_height(rows, frame_size=None):
    """Chiều cao frame: lấy từ ocr_info nếu có, không thì ước lượng bằng mép dưới thấp nhất của chữ."""
    if frame_size and len(frame_size) == 2 and frame_size[1]:
        return float(frame_size[1])
    bottoms = [max(point[1] for point in row["box"]) for row in rows if row.get("box")]
    return max(bottoms) if bottoms else None


def filter_screen_text(rows, frame_times, height, options=None):
    """Trả về (các dòng giữ lại, thống kê). rows là screen_text.json; frame_times là t của các frame đã OCR."""
    opts = {**DEFAULTS, **(options or {})}
    stats = {"rows": len(rows), "kept": len(rows), "boilerplate": 0, "long_number": 0}
    if not opts["enabled"] or not rows:
        return list(rows), stats
    times = sorted(frame_times)
    use_frequency = len(times) >= int(opts["min_frames"]) and bool(height)
    ratio = {}
    if use_frequency:
        token_frames = {}
        for row in rows:
            covered = _frames_covered(row, times)
            for token in _tokens(row["text"]):
                token_frames.setdefault(token, set()).update(covered)
        ratio = {token: len(frames) / len(times) for token, frames in token_frames.items()}
    kept = []
    for row in rows:
        if opts["drop_long_numbers"] and _LONG_NUMBER.search(row["text"]):
            stats["long_number"] += 1
            continue
        if use_frequency and row.get("box"):
            tokens = _tokens(row["text"])
            if tokens:
                share = sum(ratio.get(t, 0.0) >= opts["frequent_ratio"] for t in tokens) / len(tokens)
                y = _y_center(row["box"]) / height
                at_edge = y <= opts["top_band"] or y >= 1 - opts["bottom_band"]
                if share >= opts["frequent_share"] and at_edge:
                    stats["boilerplate"] += 1
                    continue
        kept.append(row)
    stats["kept"] = len(kept)
    return kept, stats
