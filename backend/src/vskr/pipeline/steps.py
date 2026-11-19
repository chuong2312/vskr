"""Chạy các bước GPU tuỳ chọn (OCR, VLM) trong môi trường Python riêng.

Hai bước này cần torch bản CUDA, còn ``.venv`` chính giữ torch bản CPU cho mô hình nhúng, nên
chúng nằm trong ``.venv-vlm``. Tiến trình chính chạy ``<venv>/bin/python -m vskr.cli <bước>``
thành tiến trình con; tiến trình con thấy ``VSKR_STEP_INPROCESS=1`` nên chạy bước đó trực tiếp.
VRAM được giải phóng khi tiến trình con kết thúc.
"""
import importlib
import os
import subprocess
import sys
from pathlib import Path

from ..config import ROOT

IN_STEP_ENV = "VSKR_STEP_INPROCESS"


class StepUnavailable(RuntimeError):
    """Chưa có môi trường Python đã cấu hình cho một bước tuỳ chọn."""


def step_python(cfg, step):
    """Trình thông dịch Python của bước (``<bước>.python`` trong models.yaml), hoặc None nếu không cấu hình."""
    raw = (cfg.get(step) or {}).get("python")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


def should_delegate(cfg, step):
    """Có chuyển bước sang tiến trình con không: bước có venv riêng và ta chưa ở trong tiến trình con."""
    return os.environ.get(IN_STEP_ENV) != "1" and step_python(cfg, step) is not None


def _child_env():
    env = dict(os.environ)
    env[IN_STEP_ENV] = "1"
    src = str(ROOT / "backend" / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # activate-venv.sh trỏ LD_LIBRARY_PATH vào cuBLAS/cuDNN của venv chính cho faster-whisper. Venv của
    # bước mang bản CUDA riêng; trộn hai bản làm tiến trình con lỗi CUDNN_STATUS_SUBLIBRARY_LOADING_FAILED.
    # Vì vậy bỏ đường dẫn của venv đang chạy tiến trình cha (dù nằm ở đâu, ví dụ worktree dùng
    # ~/vskr/.venv) và của .venv trong repo.
    core_venvs = {str(ROOT / ".venv") + os.sep, str(Path(sys.prefix)) + os.sep}
    kept = [p for p in env.get("LD_LIBRARY_PATH", "").split(os.pathsep)
            if p and not any(p.startswith(prefix) for prefix in core_venvs)]
    if kept:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(kept)
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def run_external(cfg, step, video_ids, force=False):
    """Chạy bước trong venv riêng bằng ``python -m vskr.cli <bước> --video ...``.

    Ném ``StepUnavailable`` nếu thiếu venv. Tiến trình con nạp mã nguồn của dự án qua ``PYTHONPATH``
    và bị dừng sau ``<bước>.timeout_s`` giây (mặc định 3600).
    """
    python = step_python(cfg, step)
    if python is None or not python.is_file():
        raise StepUnavailable(
            f"Chưa có venv cho bước {step} tại {python}; xem mục cài đặt {step.upper()} trong README."
        )
    cmd = [str(python), "-m", "vskr.cli", step]
    for video_id in video_ids:
        cmd += ["--video", video_id]
    if force:
        cmd.append("--force")
    timeout = int((cfg.get(step) or {}).get("timeout_s", 3600))
    subprocess.run(cmd, cwd=ROOT, env=_child_env(), check=True, timeout=timeout)


def run_step(cfg, step, video_ids, force=False):
    """Chạy ``step`` cho ``video_ids``: trong venv riêng nếu có cấu hình, không thì ngay trong tiến trình này."""
    if should_delegate(cfg, step):
        run_external(cfg, step, video_ids, force=force)
        return
    module = importlib.import_module(f"{__package__}.{step}")  # vskr.pipeline.ocr / vskr.pipeline.vlm
    module.run(cfg, video_ids, force=force)
