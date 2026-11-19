"""Lập chỉ mục và tìm kiếm trên lời giảng, chữ trên màn hình và tài liệu.

Luồng dựng chỉ mục: chunker → screen_filter → embedder (vector E5) + bm25 + knowledge_graph
→ index. Luồng tìm kiếm: retriever chạy vector / BM25 / graph rồi hợp nhất thứ hạng bằng
RRF (fusion).
"""
