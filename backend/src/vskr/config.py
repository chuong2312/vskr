import os
from pathlib import Path

import yaml

_MODULE_PATH = Path(__file__).resolve()
ROOT = next(
    (parent for parent in _MODULE_PATH.parents if (parent / "configs" / "models.yaml").is_file()),
    _MODULE_PATH.parents[2],
)

ENV_PREFIX = "VSKR__"


def _apply_env_overrides(cfg):
    """Ghi đè cấu hình bằng biến môi trường dạng VSKR__<MỤC>__<KHÓA>=<giá trị YAML>.

    Ví dụ: VSKR__LLM__BASE_URL=http://ollama:11434, VSKR__ASR__DEVICE=cpu, VSKR__RETRIEVAL__TOP_K=8.
    Dùng khi phát triển/thí nghiệm để khỏi sửa models.yaml. Giá trị được đọc như YAML (true, 8, 0.5, null...)."""
    for name, raw in os.environ.items():
        if not name.startswith(ENV_PREFIX):
            continue
        parts = name[len(ENV_PREFIX):].lower().split("__")
        if len(parts) != 2 or not all(parts):
            continue
        section, key = parts
        cfg.setdefault(section, {})[key] = yaml.safe_load(raw)


def load_config(path=None):
    p = Path(path or os.environ.get("VSKR_CONFIG") or ROOT / "configs" / "models.yaml")
    with open(p, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _apply_env_overrides(cfg)
    d = Path(cfg["paths"]["data_dir"])
    if not d.is_absolute():
        d = ROOT / d
    cfg["paths"]["data_dir"] = str(d)
    return cfg


def data_dir(cfg):
    return Path(cfg["paths"]["data_dir"])


def videos_dir(cfg):
    return data_dir(cfg) / "videos"


def video_dir(cfg, video_id):
    return videos_dir(cfg) / video_id


def index_dir(cfg):
    return data_dir(cfg) / "index"
