# Trạng thái dự án (cập nhật 2026-11-19)

## Đã xong
- Áp 4 vòng vá security review (PR #5); dọn dẹp an toàn (PR #6)
- OCR (EasyOCR) + VLM (Qwen2.5-VL-3B) chạy tự động trong ingest (PR #7)
- Đọc PDF/DOCX cho `vskr doc`; CLAUDE.md

## Đang chờ
- Review và gộp PR #5 → #6 → #7 theo thứ tự
- Chạy OCR/VLM trên video thật để đo thời gian và chỉnh `vlm.max_text_area`

## Việc tiếp theo
1. Bộ câu hỏi đánh giá mới (so sánh: transcript / +OCR / +OCR+VLM)
2. Sửa test đang chạm vào `data/` thật
3. Cập nhật SECURITY-REVIEW.md cho khớp code
4. Dọn code bước 5–8 (gom hardcode vào yaml, kiểm tra link YouTube, SSRF, tách handlers.py)

## Tạm hoãn
- Video tối đa 5 tiếng; firewall chặn SSRF (cần khi mở cho người dùng thật)
- Đọc code tốt hơn (VietOCR/Surya/VLM đọc nguyên văn); `fitz` → `pymupdf`

## Ghi chú
- Backup: ~/vskr-backup-2026-11-19 · Ổ C: còn ~49 GB
