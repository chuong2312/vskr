"""ASR bằng faster-whisper. Nạp model một lần cho tất cả video rồi giải phóng."""
import gc
import time

from .config import video_dir
from .util import fmt_time, to_srt, write_json


def run(cfg, video_ids, force=False):
    a = cfg["asr"]
    todo = []
    for vid in video_ids:
        vdir = video_dir(cfg, vid)
        if (vdir / "transcript.json").exists() and not force:
            print(f"[asr] bỏ qua {vid} (đã có transcript.json, dùng --force để chạy lại)")
            continue
        if not (vdir / "audio.wav").exists():
            print(f"[asr] {vid}: thiếu audio.wav, hãy chạy ingest trước")
            continue
        todo.append(vid)
    if not todo:
        return

    if a.get("unload_ollama_first", True):
        from .qa import unload_ollama

        if unload_ollama(cfg["llm"]):
            print("[asr] đã yêu cầu Ollama nhả model khỏi VRAM")

    from faster_whisper import WhisperModel

    model = WhisperModel(a["model"], device=a["device"], compute_type=a["compute_type"])
    try:
        for vid in todo:
            vdir = video_dir(cfg, vid)
            t0 = time.time()
            segments, info = model.transcribe(
                str(vdir / "audio.wav"),
                language=a.get("language"),
                vad_filter=a.get("vad_filter", True),
                beam_size=a.get("beam_size", 5),
                condition_on_previous_text=a.get("condition_on_previous_text", False),
                initial_prompt=a.get("initial_prompt") or None,
            )
            out = []
            for s in segments:
                out.append(
                    {
                        "id": len(out),
                        "start": round(s.start, 2),
                        "end": round(s.end, 2),
                        "text": s.text.strip(),
                        "avg_logprob": round(s.avg_logprob, 3),
                        "no_speech_prob": round(s.no_speech_prob, 3),
                    }
                )
            elapsed = time.time() - t0
            dur = float(info.duration or 0)
            write_json(
                vdir / "transcript.json",
                {
                    "video_id": vid,
                    "engine": a.get("engine", "faster-whisper"),
                    "model": a["model"],
                    "compute_type": a["compute_type"],
                    "language": a.get("language"),
                    "detected_language": getattr(info, "language", None),
                    "language_probability": round(float(getattr(info, "language_probability", 0) or 0), 3),
                    "vad_filter": a.get("vad_filter", True),
                    "initial_prompt": a.get("initial_prompt") or "",
                    "audio_duration_s": dur,
                    "elapsed_s": round(elapsed, 1),
                    "realtime_factor": round(elapsed / dur, 3) if dur else None,
                    "segments": out,
                },
            )
            with open(vdir / "transcript.txt", "w", encoding="utf-8") as f:
                for s in out:
                    f.write(f"[{fmt_time(s['start'])}] {s['text']}\n")
            with open(vdir / "transcript.srt", "w", encoding="utf-8") as f:
                f.write(to_srt(out))
            print(f"[asr] {vid}: {len(out)} đoạn, {elapsed:.0f}s xử lý cho {dur:.0f}s âm thanh")
    finally:
        del model
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
