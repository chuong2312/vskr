import numpy as np

from .bm25 import tokenize
from .fusion import rrf_fuse


class Retriever:
    def __init__(self, index, embedder, rcfg):
        self.index, self.embedder, self.cfg = index, embedder, rcfg

    def search(self, query, mode=None, top_k=None):
        mode = mode or self.cfg.get("mode", "hybrid")
        top_k = top_k or self.cfg.get("top_k", 5)
        cand = self.cfg.get("candidate_k", 20)
        idx = self.index
        vec_scores = None
        if mode in ("vector", "hybrid"):
            q = self.embedder.encode_query(query)
            vec_scores = idx.vectors @ q
            thr = self.cfg.get("min_vector_score")
            if thr is not None and float(vec_scores.max()) < thr:
                return []
        if mode == "vector":
            order = np.argsort(-vec_scores)[:top_k]
            pairs = [(int(i), float(vec_scores[i])) for i in order]
        elif mode == "bm25":
            s = np.array(idx.bm25.scores(tokenize(query)))
            order = [int(i) for i in np.argsort(-s)[:top_k] if s[i] > 0]
            pairs = [(i, float(s[i])) for i in order]
        elif mode == "hybrid":
            vr = [int(i) for i in np.argsort(-vec_scores)[:cand]]
            s = np.array(idx.bm25.scores(tokenize(query)))
            br = [int(i) for i in np.argsort(-s)[:cand] if s[i] > 0]
            fused = rrf_fuse([vr, br], k=self.cfg.get("rrf_k", 60))[:top_k]
            pairs = [(i, sc) for i, sc in fused]
        else:
            raise ValueError(f"mode không hợp lệ: {mode}")
        return [{"idx": i, "score": sc, "chunk": idx.chunks[i]} for i, sc in pairs]
