def rrf_fuse(rankings, k=60):
    """Reciprocal Rank Fusion. rankings: list các danh sách id đã xếp hạng (tốt nhất trước).
    Trả về [(id, score)] giảm dần theo score."""
    score = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, start=1):
            score[doc] = score.get(doc, 0.0) + 1.0 / (k + rank)
    return sorted(score.items(), key=lambda x: (-x[1], x[0]))
