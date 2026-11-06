#!/usr/bin/env python3
"""Compare the current faster-whisper model with a Vietnamese PhoWhisper checkpoint.

Manifest format: one JSON object per line, with an audio WAV path and a manually
corrected reference transcript. Paths are relative to the repository root.
"""
import argparse
import gc
import json
import time
import unicodedata
import wave
from pathlib import Path

from vskr.config import ROOT, load_config


def normalize(text):
    """Normalize Unicode/case/punctuation while retaining Vietnamese diacritics."""
    chars = []
    for char in unicodedata.normalize("NFC", text).lower():
        chars.append(char if char.isalnum() else " ")
    return " ".join("".join(chars).split())


def edit_distance(left, right):
    """Levenshtein distance using O(min(n, m)) working memory."""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, start=1):
        current = [i]
        for j, b in enumerate(right, start=1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def score_text(reference, hypothesis):
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    ref_words, hyp_words = ref.split(), hyp.split()
    return {
        "wer": edit_distance(ref_words, hyp_words) / max(len(ref_words), 1),
        "cer": edit_distance(ref, hyp) / max(len(ref), 1),
    }


def load_manifest(path):
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Manifest dòng {line_number} không phải JSON hợp lệ: {exc}") from exc
        if not isinstance(item, dict) or not item.get("audio") or not item.get("reference"):
            raise ValueError(f"Manifest dòng {line_number} cần có 'audio' và 'reference'.")
        audio = Path(item["audio"])
        if not audio.is_absolute():
            audio = ROOT / audio
        if not audio.is_file():
            raise FileNotFoundError(f"Manifest dòng {line_number}: không tìm thấy audio {audio}")
        if audio.suffix.lower() != ".wav":
            raise ValueError(f"Chỉ nhận WAV PCM 16 kHz mono để so sánh công bằng: {audio}")
        with wave.open(str(audio), "rb") as wav:
            if wav.getframerate() != 16000 or wav.getnchannels() != 1:
                raise ValueError(f"Audio cần mono 16 kHz: {audio}")
            duration = wav.getnframes() / wav.getframerate()
        if duration > 30.0:
            raise ValueError(f"Clip cần tối đa 30 giây (hiện {duration:.1f}s): {audio}")
        rows.append({"audio": audio, "reference": item["reference"].strip(), "duration_s": duration})
    if not rows:
        raise ValueError("Manifest trống; cần ít nhất một clip có transcript chuẩn do bạn sửa tay.")
    return rows


def transcribe_faster_whisper(rows, cfg, device):
    from faster_whisper import WhisperModel

    settings = cfg["asr"]
    compute_type = settings["compute_type"] if device == "cuda" else "int8"
    model = WhisperModel(
        settings["model"], device=device, compute_type=compute_type
    )
    results = []
    try:
        for row in rows:
            started = time.perf_counter()
            segments, _info = model.transcribe(
                str(row["audio"]),
                language=settings.get("language", "vi"),
                vad_filter=settings.get("vad_filter", True),
                beam_size=settings.get("beam_size", 5),
                condition_on_previous_text=settings.get("condition_on_previous_text", False),
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            results.append({"text": text, "seconds": time.perf_counter() - started})
    finally:
        del model
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
    return results


def transcribe_phowhisper(rows, model_id, device):
    import torch
    from transformers import pipeline

    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "Venv hiện dùng PyTorch CPU nên không có CUDA cho PhoWhisper. "
            "Chạy candidate bằng --phowhisper-device cpu hoặc chuẩn bị venv riêng có PyTorch CUDA."
        )
    pipeline_device = 0 if device == "cuda" else -1
    dtype = torch.float16 if device == "cuda" else torch.float32
    asr = pipeline(
        "automatic-speech-recognition",
        model=model_id,
        device=pipeline_device,
        torch_dtype=dtype,
    )
    results = []
    try:
        for row in rows:
            started = time.perf_counter()
            output = asr(str(row["audio"]))
            results.append({"text": output["text"].strip(), "seconds": time.perf_counter() - started})
    finally:
        del asr
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    return results


def aggregate(rows, outputs):
    word_errors = word_count = char_errors = char_count = 0
    elapsed = total_audio = 0.0
    for row, output in zip(rows, outputs):
        ref = normalize(row["reference"])
        hyp = normalize(output["text"])
        ref_words, hyp_words = ref.split(), hyp.split()
        word_errors += edit_distance(ref_words, hyp_words)
        word_count += len(ref_words)
        char_errors += edit_distance(ref, hyp)
        char_count += len(ref)
        elapsed += output["seconds"]
        total_audio += row["duration_s"]
    return {
        "wer_percent": 100 * word_errors / max(word_count, 1),
        "cer_percent": 100 * char_errors / max(char_count, 1),
        "elapsed_seconds": elapsed,
        "realtime_factor": elapsed / max(total_audio, 1e-9),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSONL gồm audio WAV và transcript chuẩn đã sửa tay")
    parser.add_argument("--phowhisper-model", default="vinai/PhoWhisper-medium")
    parser.add_argument("--phowhisper-device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--baseline-device", choices=("cpu", "cuda"))
    parser.add_argument("--output", type=Path, default=Path("data/asr_eval/results.json"))
    args = parser.parse_args()

    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    rows = load_manifest(manifest)
    cfg = load_config()
    baseline_device = args.baseline_device or cfg["asr"].get("device", "cuda")

    if cfg["asr"].get("unload_ollama_first", True):
        from vskr.qa import unload_ollama

        unload_ollama(cfg["llm"])

    print(f"Baseline faster-whisper {cfg['asr']['model']} ({baseline_device})...")
    baseline = transcribe_faster_whisper(rows, cfg, baseline_device)
    print(f"Candidate {args.phowhisper_model} ({args.phowhisper_device})...")
    candidate = transcribe_phowhisper(rows, args.phowhisper_model, args.phowhisper_device)

    details = []
    for row, base_out, candidate_out in zip(rows, baseline, candidate):
        details.append({
            "audio": str(row["audio"].relative_to(ROOT)) if row["audio"].is_relative_to(ROOT) else str(row["audio"]),
            "duration_seconds": row["duration_s"],
            "reference": row["reference"],
            "baseline": {**base_out, **score_text(row["reference"], base_out["text"])},
            "candidate": {**candidate_out, **score_text(row["reference"], candidate_out["text"])},
        })

    report = {
        "metric_note": "WER tokenizes Vietnamese by whitespace/syllable; CER counts normalized characters. Lower is better.",
        "baseline_model": cfg["asr"]["model"],
        "baseline_device": baseline_device,
        "candidate_model": args.phowhisper_model,
        "candidate_device": args.phowhisper_device,
        "baseline_summary": aggregate(rows, baseline),
        "candidate_summary": aggregate(rows, candidate),
        "samples": details,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\nModel                         WER      CER      Time      RTF")
    for name, summary in ((cfg["asr"]["model"], report["baseline_summary"]),
                          (args.phowhisper_model, report["candidate_summary"])):
        print(f"{name:<28} {summary['wer_percent']:6.2f}%  {summary['cer_percent']:6.2f}%  "
              f"{summary['elapsed_seconds']:7.1f}s  {summary['realtime_factor']:.3f}")
    print(f"\nChi tiết: {output_path}")


if __name__ == "__main__":
    main()
