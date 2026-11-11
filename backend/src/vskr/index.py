"""Xây và nạp chỉ mục: chunks.json + vectors.npy (numpy, tích vô hướng trên vector đã chuẩn hóa)."""
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .bm25 import BM25, tokenize
from .chunker import make_chunks
from .config import data_dir, index_dir, video_dir, videos_dir
from .knowledge_graph import build_graph
from .util import read_json, write_json


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
    graph = build_graph(chunks)
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
