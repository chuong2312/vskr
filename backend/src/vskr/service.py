import time

from . import index as index_mod
from .embedder import Embedder
from .qa import answer_question
from .retriever import Retriever
from .util import fmt_time, source_link


class Service:
    def __init__(self, cfg):
        self.cfg = cfg
        self.meta = index_mod.load_meta(cfg)
        self.index = index_mod.load(cfg)
        built_with = (self.index.info.get("embedding") or {}).get("model")
        if built_with and built_with != cfg["embedding"]["model"]:
            raise RuntimeError(
                f"Chỉ mục được tạo bằng embedding '{built_with}' nhưng cấu hình hiện là "
                f"'{cfg['embedding']['model']}'. Chạy lại: python -m vskr.cli index"
            )
        self.embedder = Embedder(cfg["embedding"])
        self.retriever = Retriever(self.index, self.embedder, cfg["retrieval"])

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

    def ask(self, question, mode=None):
        t0 = time.time()
        hits = self.retriever.search(question, mode=mode)
        t1 = time.time()
        res = answer_question(question, hits, self.cfg["llm"])
        t2 = time.time()
        sources = []
        for n in res["source_ns"]:
            c = hits[n - 1]["chunk"]
            m = self.meta.get(c["video_id"], {})
            sources.append(
                {
                    "n": n,
                    "chunk_id": c["chunk_id"],
                    "video_id": c["video_id"],
                    "title": m.get("title"),
                    "start": c["start"],
                    "end": c["end"],
                    "label": f"{fmt_time(c['start'])}-{fmt_time(c['end'])}",
                    "text": c["text"],
                    "link": (
                        f"/api/videos/{c['video_id']}/file#t={int(c['start'])}"
                        if m.get("source_type") == "upload"
                        else source_link(m.get("url"), c["video_id"], c["start"])
                    ),
                    "cited": res["cited"],
                    "score": hits[n - 1]["score"],
                }
            )
        return {
            "answer": res["answer"],
            "found": res["found"],
            "cited": res["cited"],
            "sources": sources,
            "mode": mode or self.cfg["retrieval"].get("mode", "vector"),
            "timing": {"retrieve_s": round(t1 - t0, 3), "llm_s": round(t2 - t1, 2)},
        }
