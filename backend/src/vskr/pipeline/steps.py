"""Run optional GPU steps (OCR, VLM) inside their own virtualenv.

Both need a CUDA build of torch while the core venv keeps the CPU build, so
they live in ``.venv-vlm`` instead of the core venv. The core process starts
``<venv>/bin/python -m vskr.cli <step>`` as a child; the child sees
``VSKR_STEP_INPROCESS=1`` and runs the step directly.
"""
import importlib
import os
import subprocess
import sys
from pathlib import Path

from ..config import ROOT

IN_STEP_ENV = "VSKR_STEP_INPROCESS"


class StepUnavailable(RuntimeError):
    """The configured venv for an optional step is missing."""


def step_python(cfg, step):
    raw = (cfg.get(step) or {}).get("python")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


def should_delegate(cfg, step):
    return os.environ.get(IN_STEP_ENV) != "1" and step_python(cfg, step) is not None


def _child_env():
    env = dict(os.environ)
    env[IN_STEP_ENV] = "1"
    src = str(ROOT / "backend" / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # activate-venv.sh points LD_LIBRARY_PATH at the core venv's cuBLAS/cuDNN for
    # faster-whisper. The step venv brings its own CUDA build; mixing the two
    # makes the child fail with CUDNN_STATUS_SUBLIBRARY_LOADING_FAILED. Strip
    # the venv this (parent) process runs in, wherever it lives (e.g. a git
    # worktree using ~/vskr/.venv), plus the repo's own .venv.
    core_venvs = {str(ROOT / ".venv") + os.sep, str(Path(sys.prefix)) + os.sep}
    kept = [p for p in env.get("LD_LIBRARY_PATH", "").split(os.pathsep)
            if p and not any(p.startswith(prefix) for prefix in core_venvs)]
    if kept:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(kept)
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def run_external(cfg, step, video_ids, force=False):
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
    """Run ``step`` for ``video_ids`` in its configured venv, or in-process when none is set."""
    if should_delegate(cfg, step):
        run_external(cfg, step, video_ids, force=force)
        return
    module = importlib.import_module(f"{__package__}.{step}")  # vskr.pipeline.ocr / vskr.pipeline.vlm
    module.run(cfg, video_ids, force=force)
