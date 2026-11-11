"""Kiểm tra môi trường (giai đoạn 0). Không sửa gì, chỉ báo cáo.

    python -m vskr.cli doctor              # kiểm tra nhanh
    python -m vskr.cli doctor --gpu-test   # thêm: chạy thử faster-whisper thật trên GPU (tải model 'tiny' ~75MB)
"""
import os
import platform
import shutil
import subprocess
import sys

from .config import data_dir


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as e:
        return f"LỖI: {e}"


def _cpu_name():
    try:  # Linux/WSL: platform.processor() chỉ trả 'x86_64', tên thật nằm ở /proc/cpuinfo
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "không rõ"


def _gpu_test(cfg):
    """cuBLAS/cuDNN thường chỉ bị nạp khi chạy suy luận thật, nên phải chạy thử một đoạn âm thanh."""
    import numpy as np
    from faster_whisper import WhisperModel

    a = cfg["asr"]
    try:
        m = WhisperModel("tiny", device=a.get("device", "cuda"), compute_type=a.get("compute_type", "float16"))
        segs, _ = m.transcribe(np.zeros(16000 * 3, dtype=np.float32), vad_filter=False)
        list(segs)
        print(f"gpu-test    : OK (faster-whisper chạy được trên {a.get('device', 'cuda')}, {a.get('compute_type')})")
    except Exception as e:
        print(f"gpu-test    : LỖI {type(e).__name__}: {e}")
        print("              Thường là thiếu/lệch thư viện cuBLAS/cuDNN cho CUDA 12; xem phần cài đặt venv trong README.")


def main(cfg, gpu_test=False):
    print(f"Python      : {sys.version.split()[0]} ({platform.platform()})")
    print(f"CPU         : {_cpu_name()} | số luồng: {os.cpu_count()}")
    d = data_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(d).free / 1e9
    print(f"Ổ đĩa trống : {free:.0f} GB tại {d}" + ("  (nên >= 30 GB)" if free < 30 else ""))
    print(f"ffmpeg      : {'OK' if shutil.which('ffmpeg') else 'THIẾU (cần cài và thêm vào PATH)'}")
    js = [r for r in ("deno", "node", "bun") if shutil.which(r)]
    print(f"JS runtime  : {', '.join(js) if js else 'THIẾU'}  (một số nền tảng yt-dlp cần deno hoặc node; kiểm tra tài liệu yt-dlp)")
    for mod in ("yt_dlp", "faster_whisper", "ctranslate2", "sentence_transformers", "fastapi", "httpx", "numpy", "scenedetect", "cv2", "PIL"):
        try:
            __import__(mod)
            print(f"{mod:<22}: OK")
        except Exception:
            print(f"{mod:<22}: THIẾU")
    try:
        import ctranslate2

        n = ctranslate2.get_cuda_device_count()
        print(f"ctranslate2 : thấy {n} GPU CUDA (đây là thứ faster-whisper dùng; chưa chứng minh cuBLAS/cuDNN đủ, dùng --gpu-test)")
    except Exception:
        pass
    try:
        import torch

        ok = torch.cuda.is_available()
        print(f"torch       : {torch.__version__}, CUDA={'có' if ok else 'KHÔNG (bình thường nếu embedding.device=cpu)'}")
        if ok:
            free_b, total_b = torch.cuda.mem_get_info()
            print(f"GPU         : {torch.cuda.get_device_name(0)} | VRAM trống {free_b/1e9:.1f}/{total_b/1e9:.1f} GB")
    except Exception:
        print("torch       : chưa cài hoặc lỗi")
    if shutil.which("nvidia-smi"):
        print("nvidia-smi  :", _run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu", "--format=csv,noheader"]))
    try:
        import httpx

        lc = cfg["llm"]
        r = httpx.get(lc["base_url"].rstrip("/") + "/api/tags", timeout=5)
        names = [m["name"] for m in r.json().get("models", [])]
        has = any(n == lc["model"] or n.split(":")[0] == lc["model"] for n in names)
        print(f"Ollama      : chạy tại {lc['base_url']}, {len(names)} model; '{lc['model']}' {'đã có' if has else 'CHƯA pull'}")
    except Exception:
        print(f"Ollama      : không kết nối được tại {cfg['llm'].get('base_url')} (chưa chạy hoặc chưa cài)")
    if gpu_test:
        _gpu_test(cfg)
