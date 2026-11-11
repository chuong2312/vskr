import numpy as np

from .bm25 import tokenize
from .fusion import rrf_fuse
from .knowledge_graph import search_graph


class Retriever:
    def __init__(self, index, embedder, rcfg):
        self.index, self.embedder, self.cfg = index, embedder, rcfg
        self._semantic_terms_cache = {}

    def _semantic_graph_terms(self, vector_scores, query, limit=5):
        """Use E5-near chunks to add their graph concepts as soft query anchors."""
        cache_key = query.casefold().strip()
        if cache_key in self._semantic_terms_cache:
            return self._semantic_terms_cache[cache_key]
        concepts_by_source = {}
        nodes = {node["id"]: node for node in self.index.graph.get("nodes", [])}
        for edge in self.index.graph.get("edges", []):
            if edge.get("type") != "MENTIONS":
                continue
            concept = nodes.get(edge.get("from"), {})
            source = edge.get("to")
            if concept.get("kind") == "concept" and source:
                concepts_by_source.setdefault(source, set()).add(concept.get("label", ""))

        query_terms = set(tokenize(query))
        ranked_chunks = np.argsort(-vector_scores)[:limit]
        semantic_terms = {}
        for idx in ranked_chunks:
            similarity = max(0.0, min(1.0, float(vector_scores[idx])))
            if similarity < 0.35:
                continue
            source_id = f"source:{self.index.chunks[int(idx)]['chunk_id']}"
            weight = 0.5 + 0.5 * similarity
            for label in concepts_by_source.get(source_id, ()):
                label_tokens = set(tokenize(label))
                if label_tokens and label_tokens <= query_terms:
                    continue
                semantic_terms[label] = max(semantic_terms.get(label, 0.0), weight)
        if len(self._semantic_terms_cache) >= 512:
            self._semantic_terms_cache.clear()
        self._semantic_terms_cache[cache_key] = semantic_terms
        return semantic_terms

    def search(self, query, mode=None, top_k=None, experiment=None):
        mode = mode or self.cfg.get("mode", "hybrid_graph")
        top_k = top_k or self.cfg.get("top_k", 5)
        cand = self.cfg.get("candidate_k", 20)
        idx = self.index
        experiment = experiment or {}
        graph_options = {
            "idf_weights": idx.bm25.idf if experiment.get("idf_weighting") else None,
            "ppr": bool(experiment.get("ppr")),
            "damping": float(experiment.get("damping", 0.5)),
            "seed_weight": float(experiment.get("seed_weight", 0.05)),
        }
        if mode == "graph":
            if not idx.graph.get("nodes"):
                raise RuntimeError("Chỉ mục chưa có Đồ thị tri thức. Hãy chạy lại: vskr index")
            if experiment.get("semantic_expansion"):
                query_vector = self.embedder.encode_query(query)
                graph_options["semantic_terms"] = self._semantic_graph_terms(idx.vectors @ query_vector, query)
            return search_graph(idx.graph, idx.chunks, query, top_k=top_k, **graph_options)
        if mode == "hybrid_graph" and not idx.graph.get("nodes"):
            raise RuntimeError("Chỉ mục chưa có Đồ thị tri thức. Hãy chạy lại: vskr index")
        vec_scores = None
        if mode in ("vector", "hybrid", "hybrid_graph"):
            q = self.embedder.encode_query(query)
            vec_scores = idx.vectors @ q
            thr = self.cfg.get("min_vector_score")
            if mode == "vector" and thr is not None and float(vec_scores.max()) < thr:
                return []
        if mode == "vector":
            order = np.argsort(-vec_scores)[:top_k]
            pairs = [(int(i), float(vec_scores[i])) for i in order]
        elif mode == "bm25":
            s = np.array(idx.bm25.scores(tokenize(query)))
            order = [int(i) for i in np.argsort(-s)[:top_k] if s[i] > 0]
            pairs = [(i, float(s[i])) for i in order]
        elif mode in ("hybrid", "hybrid_graph"):
            vr = [int(i) for i in np.argsort(-vec_scores)[:cand]]
            thr = self.cfg.get("min_vector_score")
            if mode == "hybrid_graph" and thr is not None and float(vec_scores.max()) < thr:
                vr = []
            s = np.array(idx.bm25.scores(tokenize(query)))
            br = [int(i) for i in np.argsort(-s)[:cand] if s[i] > 0]
            rankings = [vr, br]
            if mode == "hybrid_graph":
                if experiment.get("semantic_expansion"):
                    graph_options["semantic_terms"] = self._semantic_graph_terms(vec_scores, query)
                graph_hits = search_graph(idx.graph, idx.chunks, query, top_k=cand, **graph_options)
                graph_ranking = [hit["idx"] for hit in graph_hits]
                rankings.append(graph_ranking)
            fused = rrf_fuse(rankings, k=self.cfg.get("rrf_k", 60))[:top_k]
            graph_meta = {hit["idx"]: hit for hit in graph_hits} if mode == "hybrid_graph" else {}
            pairs = [(i, sc, graph_meta.get(i)) for i, sc in fused]
        else:
            raise ValueError(f"mode không hợp lệ: {mode}")
        if mode in ("hybrid", "hybrid_graph"):
            return [
                {"idx": i, "score": sc, "chunk": idx.chunks[i],
                 **({"matched_concepts": meta.get("matched_concepts", [])} if meta else {})}
                for i, sc, meta in pairs
            ]
        return [{"idx": i, "score": sc, "chunk": idx.chunks[i]} for i, sc in pairs]
