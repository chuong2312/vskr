"""Measure a full rebuild on a temporary copy of selected transcript inputs."""

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from vskr.api.handlers.jobs import _rebuild_index
from vskr.config import data_dir, index_dir, load_config


_VIDEO_INPUTS = ("meta.json", "transcript.json", "screen_text.json", "captions.json", "scenes.json", "frames.json")


def _sources(root):
    results = []
    for kind, filename in (("video", "transcript.json"), ("document", "sections.json")):
        source_root = root / ("videos" if kind == "video" else "documents")
        if source_root.is_dir():
            results.extend((kind, path.parent) for path in sorted(source_root.glob(f"*/{filename}")))
    return results


def _copy_source(kind, source, destination):
    destination.mkdir(parents=True)
    names = _VIDEO_INPUTS if kind == "video" else ("meta.json", "sections.json")
    for name in names:
        path = source / name
        if path.is_file():
            shutil.copyfile(path, destination / name)


def _parse_limits(value, source_count):
    limits = []
    for item in value.split(","):
        item = item.strip().lower()
        limit = source_count if item == "all" else int(item)
        if limit < 1:
            raise ValueError("Mọi giới hạn nguồn phải lớn hơn 0.")
        limits.append(min(limit, source_count))
    return sorted(set(limits))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limits", default="1,5,20,all",
                        help="số video/tài liệu để dựng lại, phân cách dấu phẩy; hỗ trợ all")
    args = parser.parse_args()
    cfg = load_config()
    original_root = data_dir(cfg)
    sources = _sources(original_root)
    if not sources:
        parser.error(f"Không tìm thấy transcript/sections trong {original_root}.")

    results = []
    for limit in _parse_limits(args.limits, len(sources)):
        with tempfile.TemporaryDirectory(prefix="vskr-rebuild-bench-") as tmp:
            bench_cfg = {**cfg, "paths": {**cfg["paths"], "data_dir": str(Path(tmp) / "data")}}
            bench_root = data_dir(bench_cfg)
            for kind, source in sources[:limit]:
                base = bench_root / ("videos" if kind == "video" else "documents")
                _copy_source(kind, source, base / source.name)
            started = time.monotonic()
            _rebuild_index(bench_cfg)
            elapsed = time.monotonic() - started
            info_path = index_dir(bench_cfg) / "index_info.json"
            info = json.loads(info_path.read_text(encoding="utf-8"))
            results.append({
                "source_count": limit,
                "chunk_count": info["chunk_count"],
                "wall_seconds": round(elapsed, 3),
                "embed_seconds": info.get("embed_seconds"),
            })
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
