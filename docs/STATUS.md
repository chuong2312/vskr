# Trạng thái dự án (cập nhật 2026-11-28)

## Đã xong
- PR #5–#18 đã gộp vào `main`:
  - Vá bảo mật 4 vòng (#5), dọn dẹp (#6), OCR + VLM trong ingest (#7), bộ đánh giá + sửa graph + test chạy trên thư mục tạm (#8–#10)
  - 36 câu hỏi đánh giá đã duyệt: 23 lời giảng, 10 chữ trên màn hình, 3 hình vẽ (#11, #13); lọc chữ rác OCR (#13)
  - Chế độ tìm kiếm mặc định `hybrid` (#14)
  - Sắp xếp code theo khâu (`pipeline/`, `retrieval/`, `access/`, `api/handlers/`), gom cấu hình, gộp code trùng, docstring tiếng Việt (#15, #16)
  - Giới hạn hỏi đáp vào `models.yaml`, cookie SameSite, `doctor`, rà lại `docs/SECURITY-REVIEW.md` với code (#17)
  - Quy tắc lọc chữ cố định ở giữa màn hình `persistent_ratio`, đang tắt, kèm biến thể đo (#18)
- Video thứ 3 ("[Khóa học sử dụng SQL server] - Bài 2: Tạo database") đã chạy đủ ASR, khung hình, OCR, VLM; chỉ mục thật giờ có 196 đoạn từ 3 video
- Đã sinh câu trả lời của hệ thống cho 36 câu (trung bình 1,8 giây/câu) và dựng trang chấm `data/eval/drafts/answers-review.md`
- PR #19–#23 đã gộp:
  - Sắp xếp thư mục: tài liệu vào `docs/`, `scripts/bench/` và `scripts/eval/`, `tests/` chia theo khâu, thêm `docs/ARCHITECTURE.md` (#19)
  - Sửa trang Thử hỏi để khung video không che ô câu hỏi (#20), sau đó chủ dự án revert ở #21; lý do chưa ghi lại
  - Bộ test không gọi tới Ollama thật đang chạy (#22); khung chat người học xoá chữ ngay khi gửi (#23)
- Đã ghi lộ trình nâng cấp vào [ROADMAP.md](ROADMAP.md): local và Gemini kết hợp, dò trạng thái slide, đồ thị trạng thái bài giảng, tầng đóng gói (đề xuất, chưa triển khai)

## Đang mở
- PR #24 (`feat/context-packing`): chia đều chỗ cho 5 nguồn, giữ mẩu liên quan khi vượt `num_ctx`. Đo offline: số câu bị bỏ nguồn 17/36 → 0; tỉ lệ giữ thuật ngữ của đáp án 0,950 → 0,990. Chưa so câu trả lời thật: `data/eval/drafts/answers-compare.md` chưa có

## Kết quả đo (36 câu, 3 video, 196 đoạn, Recall@5 / MRR)
Index gồm lời giảng + OCR đã lọc + VLM (giống index thật):

| `hybrid` (mặc định) | `hybrid_graph` | `vector` | `bm25` | `graph` |
|---|---|---|---|---|
| 0.92 / 0.79 | 0.89 / 0.71 | 0.83 / 0.76 | 0.86 / 0.72 | 0.74 / 0.53 |

Theo nhóm câu hỏi (`hybrid`): chỉ transcript 0.91/0.82 (lời giảng) · 0.80/0.73 (màn hình) · 1.00/1.00 (hình vẽ); thêm OCR đã lọc và VLM: 0.91/0.80 · 0.90/0.71 · 1.00/1.00.

Quy tắc mới `persistent_ratio: 0.6`: `hybrid` 0.92/0.80 (chỉ pg-07 đổi hạng), `vector` 0.86/0.75, `bm25` 0.89/0.73, `hybrid_graph` 0.89/0.70; bỏ thêm 42 dòng rác. Để tắt vì chế độ mặc định không đổi đáng kể.

Câu trả lời (đếm tự động, chưa phải điểm chấm): 35/36 câu có trích dẫn; 30/36 câu có ít nhất một nguồn trích trùng nguồn đúng.

Bộ nhỏ: chênh 1 câu ≈ 0.04 (lời giảng), 0.10 (màn hình), 0.33 (hình vẽ).

## Đang chờ chủ dự án
- Chạy script so sánh câu trả lời cho PR #24 (cần Ollama bật), chấm các câu thay đổi, rồi quyết gộp hay không
- Chấm 36 câu trả lời trong `data/eval/drafts/answers-review.md` (3 nhãn: trả lời đúng, dựa vào nguồn, trích dẫn đúng); ngày 2026-11-28 file mới có 1 ô được tick
- Duyệt 18 câu hỏi nháp trong `data/eval/drafts/extra-review.md` (có 5 câu nhiều bước, cần để đo graph)
- Chọn bước tiếp theo trong [ROADMAP.md](ROADMAP.md) mục 9
- Tài khoản `copilot-test-20261116-0751` trong DB thật: xoá hay giữ
- Xoá 11 nhánh đã gộp trên GitHub (nội dung đều đã có trong `main`); nhánh `feat/vskr-hardening` còn 1 commit chưa gộp, giữ hay bỏ

## Việc tiếp theo (theo thứ tự)
1. Sau khi chấm: tổng hợp bằng `scripts/eval/summarize_answer_eval.py` và đưa vào báo cáo
2. Phía sinh câu trả lời, bước 2: kiểm chi tiết kỹ thuật không có trong nguồn và gắn dòng cảnh báo (sau khi chốt PR #24)
3. Các bước nâng cấp trong [ROADMAP.md](ROADMAP.md) mục 9: dò trạng thái slide → đồ thị trạng thái v2 → khái niệm kiểu LinearRAG + PPR → tầng đóng gói → bộ chọn đường local/Gemini → đánh giá 4 cách. Kế hoạch tìm kiếm đã duyệt ngày 2026-11-22 (chia tập chỉnh/tập đo, convex combination, Contextual Retrieval, reranker) vẫn giữ; phần graph của kế hoạch đó nay nằm trong ROADMAP
4. Thêm video và câu hỏi: câu không có đáp án (để hiệu chỉnh `min_vector_score`), câu về đoạn code OCR đọc sai
5. Cookie khách hết hạn phía server (`docs/SECURITY-REVIEW.md` #2)
6. Tách `web/user/app.js` và `web/admin/admin.js` (cần thử tay trên trình duyệt); trang người học còn ghi cứng 500 ký tự
7. CI chạy test tự động trên GitHub

## Tạm hoãn
- Đọc code tốt hơn (VLM đọc nguyên văn, VietOCR, Surya): câu màn hình đã đạt 0.90. Ứng viên mới là SenOCR-Vi (chạy bằng transformers, không cần Paddle), cần duyệt tải khoảng 4 GB; xem [ROADMAP.md](ROADMAP.md) mục 6
- Lọc chữ trên poster trong vùng camera: chỉ 7 dòng, quy tắc theo tần suất dễ xoá nhầm nội dung
- Video tối đa 5 tiếng; firewall chặn SSRF (cần khi mở cho người dùng thật)
- `fitz` → `pymupdf`; dọn cache uv (~20 GB) và thu gọn ổ ảo WSL

## Ghi chú
- Ollama chạy bằng `ollama serve` (WSL không có systemd), nên đóng terminal là tắt Ollama. Ngày 2026-11-20 đã bật lại ở chế độ tách khỏi terminal; log ở `~/.ollama/logs/serve.log`
- Server cần "Tải lại chỉ mục" (trang admin) hoặc khởi động lại sau mỗi lần chạy `vskr index`
- Ngày 2026-11-21 WSL khởi động lại, Ollama và server đều tắt; bật lại theo thứ tự: `ollama serve` rồi `vskr serve`
- Backup: ~/vskr-backup-2026-11-19 · Ổ C: còn 28 GB (đầy 95%) ngày 2026-11-28, kiểm `df -h /mnt/c` trước khi tải model
- Ngày 2026-11-28 bản `~/vskr` còn chậm 2 commit so với GitHub (#22, #23): cần `git pull` khi server đang tắt
