"""Đo bộ dò trạng thái slide so với nhãn gán tay: Precision, Recall, F1 và danh sách dò thừa, dò thiếu.

    python scripts/eval/eval_slide_states.py --video user-4a6f11cb6615
    python scripts/eval/eval_slide_states.py --video <id> --labels /mnt/c/Users/<tên>/Downloads/<id>.json

Đọc ``data/videos/<id>/slide_states.json`` (do ``vskr slides`` ghi) và file nhãn từ trang gán nhãn
(mặc định ``data/eval/slides/<id>.json``). Chỉ tính trong đoạn [start, end] của file nhãn. Một lần dò
khớp một nhãn nếu lệch không quá ``--tolerance`` giây, và mỗi nhãn khớp tối đa một lần dò. Mặc định cho
lệch 3 giây vì nhãn lấy từ ảnh cứ 2 giây một ảnh còn bộ dò lấy 1 mẫu mỗi giây. Trạng thái màn hình trống
(chuyển cảnh đen hoặc trắng) mặc định không tính là một lần đổi; thêm ``--count-blank`` để tính.

Kết quả in ra màn hình và ghi vào ``data/eval/slides/<id>.eval.json``.
"""
import argparse
from datetime import datetime, timezone

from vskr.config import data_dir, load_config, video_dir
from vskr.util import fmt_time, read_json, write_json


def match_changes(labels, detected, tolerance):
    """Ghép một-một nhãn với lần dò, ưu tiên cặp lệch ít nhất; trả về số đếm, P/R/F1 và các danh sách."""
    pairs = sorted(
        (abs(d - l), li, di)
        for li, l in enumerate(labels) for di, d in enumerate(detected)
        if abs(d - l) <= tolerance
    )
    used_labels, used_detected, matched = set(), set(), []
    for _gap, li, di in pairs:
        if li in used_labels or di in used_detected:
            continue
        used_labels.add(li)
        used_detected.add(di)
        matched.append((labels[li], detected[di]))
    tp = len(matched)
    fp = len(detected) - tp
    fn = len(labels) - tp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
        "matched": sorted(matched),
        "missed": [l for i, l in enumerate(labels) if i not in used_labels],
        "extra": [d for i, d in enumerate(detected) if i not in used_detected],
    }


def evaluate(states_payload, labels_payload, tolerance=3.0, count_blank=False):
    """So các lần đổi trong ``slide_states.json`` với file nhãn, chỉ trong đoạn [start, end] của nhãn."""
    start = float(labels_payload.get("start", 0.0))
    end = float(labels_payload["end"])
    labels = sorted(float(t) for t in labels_payload.get("changes", []) if start <= float(t) <= end)
    states = [
        s for s in states_payload.get("states", [])
        if s.get("kind") != "start" and start <= float(s["start"]) <= end and (count_blank or not s.get("blank"))
    ]
    by_time = {float(s["start"]): s for s in states}
    result = match_changes(labels, sorted(by_time), tolerance)
    result["extra_states"] = [by_time[t] for t in result["extra"]]
    result.update({"start": start, "end": end, "tolerance": tolerance, "count_blank": count_blank,
                   "label_count": len(labels), "detected_count": len(by_time)})
    return result


def format_report(video_id, result, params=None):
    """Bản tóm tắt dễ đọc của kết quả ``evaluate``."""
    lines = [
        f"Video {video_id}, đoạn {fmt_time(result['start'])}–{fmt_time(result['end'])}, "
        f"cho lệch ±{result['tolerance']:g} giây",
        f"Nhãn: {result['label_count']} · Dò được: {result['detected_count']} · Khớp: {result['tp']}",
        f"Precision {result['precision']:.3f} · Recall {result['recall']:.3f} · F1 {result['f1']:.3f}",
    ]
    if result["missed"]:
        lines.append("Dò thiếu (có nhãn, không dò ra): " + ", ".join(fmt_time(t) for t in result["missed"]))
    if result["extra_states"]:
        lines.append("Dò thừa (dò ra, không có nhãn):")
        for s in result["extra_states"]:
            lines.append(f"  {fmt_time(s['start'])} {s['kind']} vùng đổi {s['change_area']} khung {s['bbox']}")
    if params:
        lines.append(f"Tham số: {params}")
    return "\n".join(lines)


def main():
    """Đọc kết quả dò và file nhãn của một video, in và ghi bản đo."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--video", required=True, help="video_id")
    ap.add_argument("--labels", help="file nhãn (mặc định data/eval/slides/<video_id>.json)")
    ap.add_argument("--tolerance", type=float, default=3.0, help="lệch tối đa giữa nhãn và lần dò, giây")
    ap.add_argument("--count-blank", action="store_true", help="tính cả trạng thái màn hình trống")
    args = ap.parse_args()

    cfg = load_config()
    states_file = video_dir(cfg, args.video) / "slide_states.json"
    if not states_file.is_file():
        ap.error(f"chưa có {states_file}; chạy trước: vskr slides --video {args.video}")
    slides_dir = data_dir(cfg) / "eval" / "slides"
    labels_file = args.labels or (slides_dir / f"{args.video}.json")
    states_payload = read_json(states_file)
    labels_payload = read_json(labels_file)
    result = evaluate(states_payload, labels_payload, args.tolerance, args.count_blank)
    print(format_report(args.video, result, states_payload.get("params")))
    write_json(slides_dir / f"{args.video}.eval.json", {
        "video_id": args.video,
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "labels_file": str(labels_file),
        "params": states_payload.get("params"),
        "algorithm_version": states_payload.get("algorithm_version"),
        **result,
    })


if __name__ == "__main__":
    main()
