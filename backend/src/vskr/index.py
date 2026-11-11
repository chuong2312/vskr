"""Xây và nạp chỉ mục: chunks.json + vectors.npy (numpy, tích vô hướng trên vector đã chuẩn hóa)."""
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .bm25 import BM25, tokenize
from .chunker import make_chunks, make_scene_chunks
from .config import data_dir, index_dir, video_dir, videos_dir
from .knowledge_graph import build_graph
from .util import read_json, write_json


def _relation_extractor(cfg):
    kg_cfg = cfg.get("knowledge_graph", {})
    if not kg_cfg.get("extract_relations", False):
        return None
    from .qa import call_ollama

    max_chunks = max(0, int(kg_cfg.get("max_relation_chunks", 100)))
    quotas = {"video": (max_chunks + 1) // 2, "document": max_chunks // 2}
    calls = {"video": 0, "document": 0}
    available = True

    def extract(chunk):
        nonlocal available
        if not available:
            return []
        kind = "document" if chunk.get("document_id") else "video"
        if calls[kind] >= quotas[kind]:
            return []
        calls[kind] += 1
        prompt = (
            "Trích xuất tối đa 8 quan hệ tri thức được nêu rõ trong đoạn bài giảng. "
            "Chỉ trả JSON array, mỗi phần tử có head, relation, tail, evidence. "
            "relation dùng nhãn ngắn như DEPENDS_ON, PART_OF, CAUSES, USES, DEFINES. "
            "head, tail và evidence phải xuất hiện nguyên văn trong đoạn. Không suy diễn.\n\n"
            f"Đoạn: {chunk['text']}"
        )
        try:
            raw = call_ollama(cfg["llm"], [{"role": "user", "content": prompt}]).strip()
        except Exception as exc:
            available = False
            print(f"[graph] không gọi được mô hình trích quan hệ; tiếp tục dựng graph bằng khái niệm và nguồn: {exc}")
            return []
        start, end = raw.find("["), raw.rfind("]")
        if start < 0 or end < start:
            return []
        try:
            import json

            values = json.loads(raw[start:end + 1])
        except (ValueError, TypeError):
            return []
        return values if isinstance(values, list) else []

    return extract


@dataclass
class Index:
    chunks: list
    vectors: np.ndarray
    bm25: BM25
    info: dict
    graph: dict


def list_video_ids(cfg):
    d = videos_dir(cfg)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if (p / "meta.json").exists())


def load_meta(cfg):
    return {vid: read_json(video_dir(cfg, vid) / "meta.json") for vid in list_video_ids(cfg)}


def build(cfg, embedder, progress=None, output_dir=None):
    ch = cfg["chunk"]
    chunks = []
    for vid in list_video_ids(cfg):
        vdir = video_dir(cfg, vid)
        tp = vdir / "transcript.json"
        if not tp.exists():
            print(f"[index] bỏ qua {vid}: chưa có transcript.json")
            continue
        segs = read_json(tp)["segments"]
        extras = None
        sp = vdir / "screen_text.json"
        if ch.get("use_screen_text", True) and sp.exists():
            extras = read_json(sp)
        scene_file = vdir / "scenes.json"
        frame_file = vdir / "frames.json"
        scene_starts = (read_json(scene_file).get("scene_starts", []) if scene_file.exists()
                        else read_json(frame_file).get("scene_starts", []) if frame_file.exists() else [])
        if scene_starts:
            chunks += make_scene_chunks(
                vid, segs, scene_starts, ch["window_s"], ch["overlap_s"],
                ch.get("min_chars", 20), extras,
            )
        else:
            chunks += make_chunks(
                vid, segs, ch["window_s"], ch["overlap_s"], ch.get("min_chars", 20), extras
            )
    document_root = data_dir(cfg) / "documents"
    if document_root.exists():
        for section_file in sorted(document_root.glob("*/sections.json")):
            chunks.extend(read_json(section_file).get("chunks", []))
    if not chunks:
        raise RuntimeError("Không có nội dung để lập chỉ mục. Hãy thêm video và chạy ASR, hoặc nhập tài liệu.")
    t0 = time.time()
    vectors = embedder.encode_passages([c["text"] for c in chunks])
    if progress:
        progress("graph", "Đang dựng đồ thị khái niệm…")
    idir = Path(output_dir) if output_dir is not None else index_dir(cfg)
    idir.mkdir(parents=True, exist_ok=True)
    kg_cfg = cfg.get("knowledge_graph", {})
    graph = build_graph(
        chunks,
        vectors=vectors,
        link_threshold=float(kg_cfg.get("link_threshold", 0.78)),
        max_links=int(kg_cfg.get("max_links_per_section", 2)),
        relation_extractor=_relation_extractor(cfg),
    )
    np.save(idir / "vectors.npy", vectors)
    write_json(idir / "chunks.json", chunks)
    write_json(idir / "knowledge_graph.json", graph)
    info = {
        "n_chunks": len(chunks),
        "embedding": cfg["embedding"],
        "chunk": ch,
        "embed_seconds": round(time.time() - t0, 1),
    }
    write_json(idir / "index_info.json", info)
    print(f"[index] {len(chunks)} chunk, nhúng trong {info['embed_seconds']}s")
    return info


def load(cfg):
    idir = index_dir(cfg)
    if not (idir / "chunks.json").exists():
        raise FileNotFoundError("Chưa có chỉ mục. Chạy: python -m vskr.cli index")
    chunks = read_json(idir / "chunks.json")
    vectors = np.load(idir / "vectors.npy")
    bm25 = BM25([tokenize(c["text"]) for c in chunks])
    info = read_json(idir / "index_info.json")
    graph_path = idir / "knowledge_graph.json"
    graph = read_json(graph_path) if graph_path.exists() else {"version": 1, "nodes": [], "edges": []}
    return Index(chunks, vectors, bm25, info, graph)
