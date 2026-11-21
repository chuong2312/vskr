# Trạng thái dự án (cập nhật 2026-11-20, chiều)

## Đã xong
- PR #5–#16 đã gộp vào `main`:
  - Vá bảo mật 4 vòng (#5), dọn dẹp (#6), OCR + VLM trong ingest (#7), bộ đánh giá + sửa graph + test chạy trên thư mục tạm (#8–#10)
  - 36 câu hỏi đánh giá đã duyệt: 23 lời giảng, 10 chữ trên màn hình, 3 hình vẽ (#11, #13); lọc chữ rác OCR (#13)
  - Chế độ tìm kiếm mặc định `hybrid` (#14)
  - Sắp xếp code theo khâu (`pipeline/`, `retrieval/`, `access/`, `api/handlers/`), gom cấu hình, gộp code trùng, docstring tiếng Việt (#15, #16)
- Video thứ 3 ("[Khóa học sử dụng SQL server] - Bài 2: Tạo database") đã chạy đủ ASR, khung hình, OCR, VLM; chỉ mục thật giờ có 196 đoạn từ 3 video
- Đã sinh câu trả lời của hệ thống cho 36 câu (trung bình 1,8 giây/câu) và dựng trang chấm `data/eval/drafts/answers-review.md`
- Đang chờ gộp:
  - PR #17: giới hạn hỏi đáp vào `models.yaml`, cookie SameSite, import thừa, `doctor`; cập nhật nội dung `SECURITY-REVIEW.md`
  - PR #18: quy tắc lọc chữ cố định ở giữa màn hình (`persistent_ratio`, đang tắt) + biến thể đo; file này

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
- Gộp PR #17 và #18
- Chấm 36 câu trả lời trong `data/eval/drafts/answers-review.md` (3 nhãn: trả lời đúng, dựa vào nguồn, trích dẫn đúng)
- Tài khoản `copilot-test-20261116-0751` trong DB thật: xoá hay giữ
- Xoá 11 nhánh đã gộp trên GitHub (nội dung đều đã có trong `main`); nhánh `feat/vskr-hardening` còn 1 commit chưa gộp, giữ hay bỏ

## Việc tiếp theo (theo thứ tự)
1. Sau khi chấm: tổng hợp bằng `scripts/summarize_answer_eval.py` và đưa vào báo cáo
2. Thêm video và câu hỏi: câu không có đáp án (để hiệu chỉnh `min_vector_score`), câu về đoạn code OCR đọc sai
3. Cookie khách hết hạn phía server (`SECURITY-REVIEW.md` #2)
4. Tách `web/user/app.js` và `web/admin/admin.js` (cần thử tay trên trình duyệt); trang người học còn ghi cứng 500 ký tự
5. CI chạy test tự động trên GitHub

## Tạm hoãn
- Đọc code tốt hơn (VLM đọc nguyên văn, VietOCR, Surya): câu màn hình đã đạt 0.90
- Lọc chữ trên poster trong vùng camera: chỉ 7 dòng, quy tắc theo tần suất dễ xoá nhầm nội dung
- Video tối đa 5 tiếng; firewall chặn SSRF (cần khi mở cho người dùng thật)
- `fitz` → `pymupdf`; dọn cache uv (~20 GB) và thu gọn ổ ảo WSL

## Ghi chú
- Ollama chạy bằng `ollama serve` (WSL không có systemd), nên đóng terminal là tắt Ollama. Ngày 2026-11-20 đã bật lại ở chế độ tách khỏi terminal; log ở `~/.ollama/logs/serve.log`
- Server cần "Tải lại chỉ mục" (trang admin) hoặc khởi động lại sau mỗi lần chạy `vskr index`
- Backup: ~/vskr-backup-2026-11-19 · Ổ C: còn ~44 GB
