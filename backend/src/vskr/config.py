"""Đọc cấu hình từ ``configs/models.yaml`` (file cấu hình duy nhất) và file ``.env``.

``ROOT`` là thư mục gốc dự án, tìm bằng cách đi ngược lên tới nơi có ``configs/models.yaml``.
Các hàm ``*_dir`` dựng đường dẫn dữ liệu từ ``paths.data_dir``.
"""
import copy
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

_MODULE_PATH = Path(__file__).resolve()
ROOT = next(
    (parent for parent in _MODULE_PATH.parents if (parent / "configs" / "models.yaml").is_file()),
    _MODULE_PATH.parents[2],
)


def load_local_environment(root=None):
    """Nạp file ``.env`` ở thư mục gốc; biến đã export trong tiến trình được ưu tiên hơn."""
    load_dotenv(Path(root or ROOT) / ".env", override=False)


load_local_environment()

ENV_PREFIX = "VSKR__"
_config_cache = {}


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
    """Đọc models.yaml (hoặc file trong ``VSKR_CONFIG``), áp ghi đè ``VSKR__MỤC__KHÓA`` rồi trả về bản sao.

    Kết quả được cache theo thời điểm sửa file và các biến ``VSKR__*``; ``paths.data_dir`` tương đối
    được đổi thành đường dẫn tuyệt đối tính từ ``ROOT``.
    """
    p = Path(path or os.environ.get("VSKR_CONFIG") or ROOT / "configs" / "models.yaml")
    env_values = tuple(sorted((k, v) for k, v in os.environ.items() if k.startswith(ENV_PREFIX)))
    signature = (str(p), p.stat().st_mtime_ns, hash(env_values))
    cached = _config_cache.get(signature)
    if cached is not None:
        return copy.deepcopy(cached)
    with open(p, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _apply_env_overrides(cfg)
    d = Path(cfg["paths"]["data_dir"])
    if not d.is_absolute():
        d = ROOT / d
    cfg["paths"]["data_dir"] = str(d)
    _config_cache.clear()
    _config_cache[signature] = cfg
    # Luôn trả bản sao: trả chính bản trong cache thì caller nào sửa cfg cũng làm hỏng mọi lần gọi sau.
    return copy.deepcopy(cfg)


def data_dir(cfg):
    """Thư mục dữ liệu (``paths.data_dir``)."""
    return Path(cfg["paths"]["data_dir"])


def videos_dir(cfg):
    """Thư mục chứa dữ liệu của mọi video: ``<data_dir>/videos``."""
    return data_dir(cfg) / "videos"


def video_dir(cfg, video_id):
    """Thư mục dữ liệu của một video: ``<data_dir>/videos/<video_id>``."""
    return videos_dir(cfg) / video_id


def index_dir(cfg):
    """Thư mục chỉ mục tìm kiếm: ``<data_dir>/index``."""
    return data_dir(cfg) / "index"


def role_limit(role_limits, ingest_cfg, key):
    """Hạn mức ``key`` của một vai trò (``limits.guest`` / ``limits.user``) trong models.yaml.

    Vai trò không khai báo khoá đó thì dùng giá trị chung trong ``ingest``. Chỉ đọc ``ingest`` khi cần,
    nên cấu hình chỉ khai báo theo vai trò vẫn hợp lệ.
    """
    return int(role_limits[key] if key in role_limits else ingest_cfg[key])
