# Các quyết định quan trọng

| Ngày | Quyết định | Lý do |
|---|---|---|
| 2026-11-19 | Mục tiêu là đồ án/nghiên cứu, hoãn firewall SSRF | Chưa mở cho người dùng thật |
| 2026-11-19 | Áp 4 bản vá Copilot | Đã thử trên bản sao: 213 test pass, sửa 10 test đang hỏng |
| 2026-11-19 | Không khôi phục 15 câu hỏi đánh giá cũ | audit.jsonl cho thấy admin đã chủ ý xoá |
| 2026-11-19 | Giữ faiss.txt, xoá llm.txt | FAISS có thể dùng sau; LLM chạy qua Ollama |
| 2026-11-19 | OCR/VLM chạy tự động, có cờ bật/tắt | Tìm được nội dung trên màn hình; tắt được khi cần ingest nhanh |
| 2026-11-19 | OCR + VLM dùng venv riêng `.venv-vlm` | Cần torch CUDA; gộp vào `.venv` sẽ hạ cuDNN/numpy mà ASR đang dùng |
| 2026-11-19 | EasyOCR thay PaddleOCR | Từ điển của Paddle thiếu chữ hai dấu (ấ, ữ, ệ…) |
| 2026-11-19 | Prompt VLM viết bằng tiếng Anh, yêu cầu trả lời tiếng Việt | Prompt tiếng Việt khiến model 3B trả lời "không có hình" cả với sơ đồ |
| 2026-11-19 | Quy trình: kế hoạch → duyệt → làm → kiểm tra → báo cáo | Lấy lại quyền kiểm soát dự án; xoá file hay cài thư viện phải hỏi từng mục |
| 2026-11-19 | Lọc chữ rác OCR trước khi lập chỉ mục (bật mặc định) | Bỏ menu/watermark lặp ở mép màn hình và dòng có mã số sinh viên. Với `hybrid`: Recall@5 0.97 → 0.92 (2 câu vốn ở hạng 4), MRR 0.80 → 0.81; cần bộ câu hỏi lớn hơn để kết luận |
| 2026-11-19 | Chế độ mặc định `hybrid` thay cho `hybrid_graph` | 36 câu đã duyệt, OCR đã lọc: `hybrid` 0.92 / 0.81, `hybrid_graph` 0.90 / 0.68 (Recall@5 / MRR); graph vẫn giữ để thử nghiệm |
| 2026-11-19 | Hoãn "đọc code tốt hơn" (VLM đọc nguyên văn, VietOCR, Surya) | Câu hỏi về màn hình đã đạt Recall@5 0.90; VLM tốn ~5 giây/frame; cần thêm câu hỏi về đoạn code OCR đọc sai mới đo được |
