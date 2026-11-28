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
| 2026-11-19 | Giữ đủ bộ lọc OCR khi dùng `hybrid` | Chênh 2/36 câu chưa đủ kết luận; bộ lọc bỏ mã số sinh viên và chữ giao diện khỏi nguồn trích dẫn |
| 2026-11-20 | Gom code theo khâu: `pipeline/`, `retrieval/`, `access/`, `api/handlers/` | Cấu trúc khớp sơ đồ hệ thống trong báo cáo; `handlers.py` 1341 dòng gánh 6 việc; phần lõi thôi phụ thuộc FastAPI |
| 2026-11-20 | Giới hạn upload và địa chỉ Ollama chỉ nằm trong models.yaml, code không giữ số dự phòng | Sửa một chỗ là đủ; thiếu khoá thì báo lỗi rõ tên khoá thay vì âm thầm dùng số khác |
| 2026-11-20 | Hoãn tách `app.js`/`admin.js` | Giao diện chưa có test chạy trên trình duyệt; tách JS cần thử tay từng màn hình |
| 2026-11-20 | Quy tắc lọc chữ cố định ở giữa màn hình (`persistent_ratio`) đưa vào code nhưng để tắt | Chế độ mặc định gần như không đổi (0.92/0.79 → 0.92/0.80); bật cần dựng lại chỉ mục |
| 2026-11-20 | Không lọc chữ trên poster trong vùng camera | Chỉ 7 dòng; quy tắc theo tần suất không phân biệt được với slide, dễ xoá nhầm |
| 2026-11-20 | Câu trả lời của LLM do chủ dự án chấm, AI không tự chấm | Kết quả chấm phải do người hiểu học liệu đánh giá mới đáng tin để đưa vào báo cáo |
| 2026-11-28 | Dò trạng thái slide bằng tỉ lệ vùng đổi, chưa kiểm bằng OCR, chạy tay bằng `vskr slides` | SSIM cả khung gần như không đổi khi chỉ thêm 1 node; muốn OCR chạy đúng tại các trạng thái thì phải sửa bước frames và OCR, nên để sang bước gắn vào luồng chính |
