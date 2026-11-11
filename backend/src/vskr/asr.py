"""ASR bằng faster-whisper. Nạp model một lần cho tất cả video rồi giải phóng."""
import gc
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .config import video_dir
from .ingest import probe_duration
from .util import fmt_time, to_srt, write_json


@contextmanager
def _audio_parts(audio_path, duration, part_duration, temp_root):
    """Yield bounded WAV files with their offsets in the original audio."""
    if duration <= part_duration:
        yield [(0.0, Path(audio_path), duration)]
        return

    with tempfile.TemporaryDirectory(prefix="asr-parts-", dir=temp_root) as temp_dir:
        parts = []
        start = 0.0
        part_number = 0
        while start < duration:
            length = min(part_duration, duration - start)
            part_path = Path(temp_dir) / f"part-{part_number:04d}.wav"
            proc = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{start:.3f}", "-i", str(audio_path), "-t", f"{length:.3f}",
                    "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(part_path),
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0 or not part_path.is_file():
                raise RuntimeError(f"ffmpeg không thể cắt audio phần {part_number + 1}: {proc.stderr[-800:]}")
            parts.append((start, part_path, length))
            start += length
            part_number += 1
        yield parts


def run(cfg, video_ids, force=False, progress=None):
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
            out = []
            audio_path = vdir / "audio.wav"
            audio_duration = probe_duration(audio_path)
            part_duration = max(60, int(a.get("segment_duration_s", 900)))
            detected_info = None
            with _audio_parts(audio_path, audio_duration, part_duration, vdir) as parts:
                for part_index, (offset, part_path, part_length) in enumerate(parts, 1):
                    if progress:
                        progress(vid, part_index, len(parts), offset, offset + part_length)
                    segments, info = model.transcribe(
                        str(part_path),
                        language=a.get("language"),
                        vad_filter=a.get("vad_filter", True),
                        beam_size=a.get("beam_size", 5),
                        condition_on_previous_text=a.get("condition_on_previous_text", False),
                        initial_prompt=a.get("initial_prompt") or None,
                    )
                    if detected_info is None:
                        detected_info = info
                    for s in segments:
                        out.append(
                            {
                                "id": len(out),
                                "start": round(offset + s.start, 2),
                                "end": round(offset + s.end, 2),
                                "text": s.text.strip(),
                                "avg_logprob": round(s.avg_logprob, 3),
                                "no_speech_prob": round(s.no_speech_prob, 3),
                            }
                        )
            elapsed = time.time() - t0
            dur = audio_duration
            info = detected_info
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
                    "part_duration_s": part_duration,
                    "parts_processed": max(1, (int(dur) + part_duration - 1) // part_duration),
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
