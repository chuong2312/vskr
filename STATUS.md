# Trạng thái dự án (cập nhật 2026-11-19)

## Đã xong
- Áp 4 vòng vá security review (PR #5); dọn dẹp an toàn (PR #6)
- OCR (EasyOCR) + VLM (Qwen2.5-VL-3B) chạy tự động trong ingest (PR #7)
- Đọc PDF/DOCX cho `vskr doc`; CLAUDE.md
- Trường `evidence` (spoken/screen/figure) cho câu hỏi đánh giá + script `scripts/eval_variants.py` so sánh transcript / +OCR / +OCR+VLM (nhánh `feat/eval-set`, chưa push)

## Đang chờ
- Review và gộp PR #5 → #6 → #7 theo thứ tự
- Chạy OCR/VLM trên video thật để đo thời gian và chỉnh `vlm.max_text_area`
- Duyệt 23 câu nháp (lời nói) trong `data/eval/drafts/spoken-review.md`; câu đã duyệt mới được chép vào `questions.jsonl`

## Việc tiếp theo
1. Bộ câu hỏi đánh giá: sau khi chạy OCR/VLM, soạn ~10 câu về chữ trên màn hình/hình vẽ, rồi chạy `eval_variants.py`
   - Phát hiện: chế độ `graph` cho Recall@5 = 0 trên 23 câu nháp. Mọi chunk khớp đều cùng điểm 1.0 nên kết quả theo thứ tự index; khái niệm ngắn còn khớp như chuỗi con ("chi" trong "chia"). Cần sửa (chưa duyệt)
2. Sửa test đang chạm vào `data/` thật
3. Cập nhật SECURITY-REVIEW.md cho khớp code
4. Dọn code bước 5–8 (gom hardcode vào yaml, kiểm tra link YouTube, SSRF, tách handlers.py)

## Tạm hoãn
- Video tối đa 5 tiếng; firewall chặn SSRF (cần khi mở cho người dùng thật)
- Đọc code tốt hơn (VietOCR/Surya/VLM đọc nguyên văn); `fitz` → `pymupdf`

## Ghi chú
- Backup: ~/vskr-backup-2026-11-19 · Ổ C: còn ~49 GB
