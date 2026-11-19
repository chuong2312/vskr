"""Nghiệp vụ phía server cho các route trong ``public.py`` và ``admin.py``.

- ``runtime``: service dùng chung, các khoá GPU/index, trạng thái hệ thống.
- ``jobs``: hàng đợi nạp học liệu (ASR, OCR/VLM, dựng lại chỉ mục).
- ``library``: thư viện học liệu, xoá, mở file, quyền xem.
- ``ask``: hỏi đáp.
- ``evaluation``: câu hỏi đánh giá và chạy đánh giá truy hồi.
- ``graph``: xem đồ thị tri thức.

Phụ thuộc một chiều, không vòng: runtime ← jobs ← library; runtime ← ask, evaluation, graph.
Route gọi qua tên module (``handlers.jobs.start_ingest``) để test có thể patch đúng chỗ.
"""
from . import ask, evaluation, graph, jobs, library, runtime  # noqa: F401
