"""OCR frames selected by :mod:`vskr.frames`.

The model is imported only when this command runs. This keeps the core image
small and lets the Paddle image own the Paddle/CUDA compatibility decision.
"""
import time

from .config import video_dir
from .util import read_json, write_json


def _lines(result):
    """Normalize the common PaddleOCR v2/v3 result shapes to plain rows."""
    rows = []
    for item in result or []:
        data = getattr(item, "json", None)
        if callable(data):
            item = data()
        if isinstance(item, dict) and "res" in item:
            item = item["res"]
        if not isinstance(item, dict):
            continue
        texts = item.get("rec_texts", item.get("texts", []))
        scores = item.get("rec_scores", item.get("scores", []))
        boxes = item.get("rec_polys", item.get("polys", item.get("dt_polys", [])))
        for i, text in enumerate(texts or []):
            text = str(text).strip()
            score = float(scores[i]) if i < len(scores or []) else 1.0
            if text:
                rows.append({"text": text, "score": score, "box": boxes[i].tolist() if i < len(boxes or []) and hasattr(boxes[i], "tolist") else (boxes[i] if i < len(boxes or []) else None)})
    return rows


def run(cfg, video_ids=None, force=False):
    from paddleocr import PaddleOCR

    ocfg = cfg.get("ocr", {})
    if ocfg.get("unload_ollama_first", True):
        from .qa import unload_ollama

        unload_ollama(cfg.get("llm", {}))
    device = ocfg.get("device", "gpu")
    engine = PaddleOCR(lang=ocfg.get("lang", "vi"), device=device)
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
        frame_rows = read_json(fp).get("frames", [])[: int(ocfg.get("max_frames", 2000))]
        rows = []
        started = time.time()
        for frame in frame_rows:
            image = vdir / frame["path"]
            raw = engine.predict(str(image)) if hasattr(engine, "predict") else engine.ocr(str(image), cls=True)
            for line in _lines(raw):
                if line["score"] >= float(ocfg.get("score_threshold", 0.5)):
                    rows.append({"start": frame["t"], "end": frame["t"], "text": line["text"], "type": "ocr", "score": line["score"], "box": line["box"], "frame": frame["path"]})
        write_json(out, rows)
        info = {"video_id": video_id, "n_frames": len(frame_rows), "n_lines": len(rows), "seconds": round(time.time() - started, 1)}
        write_json(vdir / "ocr_info.json", info)
        print(f"[ocr] {video_id}: {len(rows)} dòng từ {len(frame_rows)} frame ({info['seconds']}s)")
        results.append(info)
    return results
