import time

from . import index as index_mod
from .embedder import Embedder
from .qa import answer_question
from .retriever import Retriever
from .bm25 import BM25, tokenize
from .util import fmt_time, source_link
from .visibility import is_visible


class ModelBusyError(RuntimeError):
    """The shared GPU slot stayed busy past the interactive wait limit."""


class Service:
    def __init__(self, cfg):
        self.cfg = cfg
        self.meta = index_mod.load_meta(cfg)
        from .documents import list_documents

        self.document_meta = {item["document_id"]: item for item in list_documents(cfg)}
        self.index = index_mod.load(cfg)
        built_with = (self.index.info.get("embedding") or {}).get("model")
        if built_with and built_with != cfg["embedding"]["model"]:
            raise RuntimeError(
                f"Chỉ mục được tạo bằng embedding '{built_with}' nhưng cấu hình hiện là "
                f"'{cfg['embedding']['model']}'. Chạy lại: vskr index"
            )
        built_embedding = self.index.info.get("embedding") or {}
        keys = ("model", "query_prefix", "passage_prefix", "max_seq_length")
        if any(built_embedding.get(key) != cfg["embedding"].get(key) for key in keys):
            raise RuntimeError("Cấu hình embedding đã đổi (tiền tố hoặc tham số mã hóa). Chạy lại: vskr index")
        built_chunk = self.index.info.get("chunk")
        if built_chunk is not None and built_chunk != cfg["chunk"]:
            raise RuntimeError("Cấu hình chia đoạn đã đổi so với chỉ mục. Chạy lại: vskr index")
        self.embedder = Embedder(cfg["embedding"])
        self.retriever = Retriever(self.index, self.embedder, cfg["retrieval"])
        self.indexed_video_ids = {c.get("video_id") for c in self.index.chunks if c.get("video_id")}
        self.indexed_document_ids = {c.get("document_id") for c in self.index.chunks if c.get("document_id")}

    def videos(self):
        return [
            {
                "video_id": m["video_id"],
                "title": m.get("title"),
                "duration": m.get("duration"),
                "url": m.get("url"),
                "source_type": m.get("source_type", "url"),
                "file_url": (
                    f"/api/videos/{m['video_id']}/file"
                    if m.get("source_type") == "upload" and m.get("has_video_file")
                    else None
                ),
            }
            for m in self.meta.values()
        ]

    def ask(self, question, mode=None, llm_lock=None, lock_timeout=10, owner_id=None,
            shared_only=False, allowed_sources=None, project_id=None, admin=False):
        t0 = time.time()
        retriever = self.retriever
        if retriever is None:
            retriever = Retriever(self.index, self.embedder, self.cfg["retrieval"])
        visible_sources = set()
        for chunk in self.index.chunks:
            source_id = chunk.get("video_id") or chunk.get("document_id")
            if not source_id or (project_id is not None and chunk.get("project_id") != project_id):
                continue
            metadata = (self.meta if chunk.get("video_id") else self.document_meta).get(source_id, {})
            if not metadata.get("owner") and not metadata.get("owner_id") and chunk.get("owner_id"):
                metadata = {**metadata, "owner_id": chunk["owner_id"]}
            if is_visible(metadata, owner_id, shared_only=shared_only, admin=admin):
                visible_sources.add(source_id)
        if project_id is not None:
            allowed_sources = visible_sources if allowed_sources is None else set(allowed_sources) & visible_sources
        elif admin:
            pass
        elif allowed_sources is None:
            allowed_sources = visible_sources
        else:
            allowed_sources = set(allowed_sources) & visible_sources
        hits = retriever.search(question, mode=mode, allowed_sources=allowed_sources)
        t1 = time.time()
        if llm_lock is not None and not llm_lock.acquire(timeout=lock_timeout):
            raise ModelBusyError("Shared GPU slot did not become available before the request deadline.")
        try:
            res = answer_question(question, hits, self.cfg["llm"])
        finally:
            if llm_lock is not None:
                llm_lock.release()
        t2 = time.time()
        sources = []
        for n in res["source_ns"]:
            c = hits[n - 1]["chunk"]
            document_id = c.get("document_id")
            is_document = bool(document_id)
            m = self.document_meta.get(document_id, {}) if is_document else self.meta.get(c.get("video_id"), {})
            section = c.get("section")
            sources.append(
                {
                    "n": n,
                    "chunk_id": c["chunk_id"],
                    "source_type": "document" if is_document else "video",
                    "video_id": c.get("video_id"),
                    "youtube_id": m.get("youtube_id"),
                    "document_id": document_id,
                    "section": section,
                    "title": m.get("title") or c.get("title"),
                    "start": c.get("start"),
                    "end": c.get("end"),
                    "label": f"Mục {section}" if is_document else f"{fmt_time(c['start'])}-{fmt_time(c['end'])}",
                    "text": c["text"],
                    "link": f"/api/documents/{document_id}/sections/{section}" if is_document else (
                        f"/api/videos/{c['video_id']}/file#t={int(c['start'])}"
                        if m.get("source_type") == "upload"
                        else source_link(m.get("url"), c["video_id"], c["start"])
                    ),
                    "cited": res["cited"],
                    "score": hits[n - 1]["score"],
                    "matched_concepts": hits[n - 1].get("matched_concepts", []),
                }
            )
        return {
            "answer": res["answer"],
            "found": res["found"],
            "cited": res["cited"],
            "no_sources": res.get("no_sources", not res["found"]),
            "sources": sources,
            "matched_concepts": sorted({term for hit in hits for term in hit.get("matched_concepts", [])}),
            "mode": mode or self.cfg["retrieval"].get("mode", "hybrid_graph"),
            "timing": {"retrieve_s": round(t1 - t0, 3), "llm_s": round(t2 - t1, 2)},
        }
