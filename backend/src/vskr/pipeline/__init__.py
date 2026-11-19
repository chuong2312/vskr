"""Khâu xử lý đầu vào: biến video và tài liệu thành dữ liệu thô để lập chỉ mục.

Gồm tải video (ingest, safe_ytdlp, url_safety), nhận dạng giọng nói (asr), trích khung
hình (frames), đọc chữ trên màn hình (ocr), mô tả hình (vlm) và đọc tài liệu (documents).
Mỗi bước ghi kết quả vào ``data/videos/<id>/`` hoặc ``data/documents/<id>/``; khâu lập
chỉ mục (:mod:`vskr.retrieval`) đọc lại các file đó.
"""
