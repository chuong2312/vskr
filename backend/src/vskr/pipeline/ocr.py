"""Đọc chữ trên các khung hình do :mod:`vskr.pipeline.frames` chọn.

Dùng EasyOCR (``['vi', 'en']``) trong venv của bước (``.venv-vlm``, dùng chung với VLM).
PaddleOCR đã bị bỏ: từ điển nhận dạng của nó thiếu các chữ hai dấu (ấ, ữ, ệ, ớ…), nên "Truy vấn
dữ liệu" bị đọc thành "Truy vn d liu". Engine chỉ được import khi bước này chạy.
"""
import time

from ..config import ROOT, video_dir
from ..util import read_json, write_json


def group_lines(words, overlap=0.5):
    """Ghép các hộp chữ trên cùng một hàng thành một dòng, từ trái sang phải.

    EasyOCR trả về một hộp cho mỗi từ hoặc cụm từ, nên một dòng code như ``SELECT u.name FROM users``
    bị tách thành nhiều mảnh. Hai hộp cùng hàng khi phần chồng theo chiều dọc đạt ít nhất ``overlap``
    chiều cao của hộp thấp hơn. Mỗi phần tử ``words`` có dạng ``{"text", "score", "box"}``, ``box``
    gồm 4 điểm.
    """
    def span(box):
        """Khung bao ``(x_min, y_min, x_max, y_max)`` của một hộp chữ."""
        ys = [float(y) for _x, y in box]
        xs = [float(x) for x, _y in box]
        return min(xs), min(ys), max(xs), max(ys)

    rows = []
    for word in sorted(words, key=lambda w: span(w["box"])[1]):
        x0, y0, x1, y1 = span(word["box"])
        for row in rows:
            ry0, ry1 = row["y0"], row["y1"]
            shared = min(y1, ry1) - max(y0, ry0)
            if shared >= overlap * min(y1 - y0, ry1 - ry0):
                row["words"].append((x0, word))
                row["y0"], row["y1"] = min(ry0, y0), max(ry1, y1)
                row["x0"], row["x1"] = min(row["x0"], x0), max(row["x1"], x1)
                break
        else:
            rows.append({"y0": y0, "y1": y1, "x0": x0, "x1": x1, "words": [(x0, word)]})
    lines = []
    for row in sorted(rows, key=lambda r: r["y0"]):
        parts = [w for _x, w in sorted(row["words"], key=lambda item: item[0])]
        lines.append({
            "text": " ".join(w["text"] for w in parts),
            "score": round(sum(w["score"] for w in parts) / len(parts), 4),
            "box": [[row["x0"], row["y0"]], [row["x1"], row["y0"]], [row["x1"], row["y1"]], [row["x0"], row["y1"]]],
        })
    return lines


def _engine(ocfg):
    """Trả về hàm ``read(path) -> [dòng]`` cho engine OCR đã cấu hình."""
    engine = ocfg.get("engine", "easyocr")
    if engine != "easyocr":
        raise ValueError(f"OCR engine không được hỗ trợ: {engine}")
    import easyocr

    langs = ocfg.get("lang", ["vi", "en"])
    reader = easyocr.Reader(
        [langs] if isinstance(langs, str) else list(langs),
        gpu=ocfg.get("device", "gpu") != "cpu",
        model_storage_directory=str(ROOT / "cache" / "easyocr"),
        verbose=False,
    )
    batch_size = int(ocfg.get("batch_size", 8))
    threshold = float(ocfg.get("score_threshold", 0.4))

    def read(path):
        # Bỏ từ không chắc chắn trước khi ghép, để một từ đọc sai trong dòng code không làm mất cả dòng.
        """Đọc chữ trong một ảnh, bỏ các từ có điểm tin cậy thấp rồi ghép thành dòng."""
        words = [{"text": str(text).strip(), "score": float(score), "box": [[float(x), float(y)] for x, y in box]}
                 for box, text, score in reader.readtext(str(path), batch_size=batch_size)
                 if str(text).strip() and float(score) >= threshold]
        return group_lines(words)

    return read


def polygon_area(box):
    """Diện tích hộp chữ ``[[x, y], ...]`` theo công thức shoelace; hộp thiếu hoặc suy biến cho 0."""
    try:
        points = [(float(x), float(y)) for x, y in box]
    except (TypeError, ValueError):
        return 0.0
    if len(points) < 3:
        return 0.0
    twice = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))
    return abs(twice) / 2.0


def frame_stats(frame, lines, size):
    """Thống kê chữ của một khung hình: số dòng, tỉ lệ diện tích có chữ và một đoạn chữ ngắn (cho VLM tham khảo)."""
    width, height = size
    covered = sum(polygon_area(line.get("box")) for line in lines)
    return {
        "path": frame["path"],
        "t": frame["t"],
        "n_lines": len(lines),
        "text_area": round(min(1.0, covered / float(width * height)), 4) if width and height else 0.0,
        "text": " ".join(line["text"] for line in lines)[:300],
    }


def merge_runs(frame_lines):
    """Gộp một dòng xuất hiện ở nhiều khung hình liên tiếp thành một mục kéo dài qua các khung hình đó.

    ``frame_lines`` là ``[(frame, [dòng, ...]), ...]`` theo thứ tự thời gian. Không gộp thì một slide
    chiếu lâu sẽ lặp lại cùng một đoạn chữ ở mọi khung hình được lấy mẫu.
    """
    rows, open_runs = [], {}
    for frame, lines in frame_lines:
        current = {}
        for line in lines:
            text = line["text"]
            if text in current:
                continue
            row = open_runs.get(text)
            if row is not None:
                row["end"] = frame["t"]
                row["score"] = max(row["score"], line["score"])
            else:
                row = {"start": frame["t"], "end": frame["t"], "text": text, "type": "ocr",
                       "score": line["score"], "box": line["box"], "frame": frame["path"]}
                rows.append(row)
            current[text] = row
        open_runs = current
    return rows


def run(cfg, video_ids=None, force=False):
    """Đọc chữ trên các khung hình trong ``frames.json`` của từng video, ghi ``screen_text.json`` và ``ocr_info.json``.

    Engine OCR được nạp một lần cho cả lượt (sau khi bảo Ollama nhả VRAM). Số khung hình bị giới hạn
    bởi ``ocr.max_frames`` và rải đều trên cả bài. ``ocr_info.json`` lưu tỉ lệ chữ của từng khung
    hình để bước VLM bỏ qua slide/code mà OCR đã đọc.
    """
    from .frames import cap_evenly

    from PIL import Image

    ocfg = cfg.get("ocr", {})
    engine = None
    results = []
    for video_id in video_ids or []:
        vdir = video_dir(cfg, video_id)
        fp = vdir / "frames.json"
        if not fp.exists():
            print(f"[ocr] bỏ qua {video_id}: chưa có frames.json")
            continue
        out = vdir / "screen_text.json"
        if out.exists() and not force:
            print(f"[ocr] bỏ qua {video_id} (đã có screen_text.json)")
            continue
        # Rải giới hạn số khung hình trên cả bài thay vì chỉ giữ phần đầu.
        frame_rows = cap_evenly(read_json(fp).get("frames", []), int(ocfg.get("max_frames", 2000)))
        if engine is None:
            if ocfg.get("unload_ollama_first", True):
                from ..qa import unload_ollama

                unload_ollama(cfg.get("llm", {}))
            loading = time.time()
            engine = _engine(ocfg)
            print(f"[ocr] đã nạp {ocfg.get('engine', 'easyocr')} ({time.time() - loading:.0f}s)")
        frame_lines, per_frame, frame_size = [], [], None
        started = time.time()
        for n, frame in enumerate(frame_rows, start=1):
            image = vdir / frame["path"]
            lines = engine(image)
            frame_lines.append((frame, lines))
            with Image.open(image) as img:
                per_frame.append(frame_stats(frame, lines, img.size))
                frame_size = frame_size or list(img.size)
            if n % 50 == 0 or n == len(frame_rows):
                elapsed = time.time() - started
                print(f"[ocr] {video_id}: {n}/{len(frame_rows)} frame ({elapsed:.0f}s, {elapsed / n:.2f}s/frame)", flush=True)
        rows = merge_runs(frame_lines)
        write_json(out, rows)
        info = {"video_id": video_id, "n_frames": len(frame_rows), "n_lines": len(rows),
                "n_raw_lines": sum(len(lines) for _frame, lines in frame_lines),
                "seconds": round(time.time() - started, 1), "frame_size": frame_size,
                # Tỉ lệ chữ của từng khung hình giúp bước VLM bỏ qua slide/code mà OCR đã đọc.
                "frames": per_frame}
        write_json(vdir / "ocr_info.json", info)
        print(f"[ocr] {video_id}: {len(rows)} dòng (gộp từ {info['n_raw_lines']}) trên {len(frame_rows)} frame ({info['seconds']}s)")
        results.append(info)
    return results
