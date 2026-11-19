"""VSKR (Video Semantic Knowledge Retrieval): hỏi đáp có trích dẫn trên video bài giảng và tài liệu.

- ``pipeline/``: tải video/tài liệu, nhận dạng giọng nói, khung hình, OCR, VLM.
- ``retrieval/``: chia đoạn, nhúng vector, BM25, đồ thị tri thức, tìm kiếm.
- ``access/``: tài khoản, đăng nhập, quyền xem, nhật ký audit.
- ``api/``: ứng dụng web FastAPI (route ở ``public.py``/``admin.py``, nghiệp vụ ở ``handlers/``).
- ``service.py`` + ``qa.py``: ghép tìm kiếm với LLM để trả lời; ``evaluation.py``: chấm Recall@k, MRR.
- ``cli.py``: lệnh ``vskr``; ``config.py``: đọc ``configs/models.yaml``.
"""
