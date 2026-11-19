# Trạng thái dự án (cập nhật 2026-11-19, tối)

## Đã xong
- Áp 4 vòng vá security review (PR #5); dọn dẹp an toàn (PR #6)
- OCR (EasyOCR) + VLM (Qwen2.5-VL-3B) chạy tự động trong ingest (PR #7)
- Đọc PDF/DOCX cho `vskr doc`; CLAUDE.md
- Trường `evidence` (spoken/screen/figure) + script `scripts/eval_variants.py` so sánh transcript / +OCR / +OCR+VLM (PR #8)
- Sửa xếp hạng graph: trước đây mọi đoạn cùng điểm 1.0 (PR #9)
- Bộ test chạy trên thư mục dữ liệu tạm, không còn ghi vào `data/` thật (PR #10)
- 23 câu hỏi đánh giá (lời nói, 17 fact + 6 multihop) đã được chủ dự án nghe và duyệt → `data/eval/questions.jsonl`

## Kết quả đo hiện tại (23 câu, chỉ transcript, code của PR #9)
| Chế độ | Recall@5 | MRR |
|---|---|---|
| vector | 0.91 | 0.85 |
| hybrid | 0.91 | 0.82 |
| hybrid_graph (mặc định) | 0.89 | 0.76 |
| bm25 | 0.87 | 0.75 |
| graph | 0.78 | 0.57 |

Bộ nhỏ: chênh 1 câu ≈ 0.04, chưa đủ để kết luận chế độ nào hơn.

## Đang chờ
- Review và gộp PR theo thứ tự #5 → #6 → … → #11 (merge commit, xoá nhánh sau mỗi lần gộp)
- Chạy OCR/VLM trên 2 video thật để đo thời gian và chỉnh `vlm.max_text_area`

## Việc tiếp theo (theo thứ tự)
1. Soạn ~10 câu về chữ trên màn hình/hình vẽ (sau khi chạy OCR/VLM), rồi chạy `eval_variants.py` cho 3 biến thể
2. Đọc chữ trong code tốt hơn: thử VLM đọc nguyên văn trước (không cài thêm), sau đó VietOCR/Surya (cần duyệt cài đặt, kiểm tra license)
3. Chọn chế độ mặc định (`hybrid_graph` hay `hybrid`) dựa trên bộ đủ ~33 câu
4. Cập nhật SECURITY-REVIEW.md cho khớp code
5. Dọn code bước 5–8 (gom hardcode vào yaml, kiểm tra link YouTube, SSRF, tách handlers.py)

## Tạm hoãn
- Video tối đa 5 tiếng; firewall chặn SSRF (cần khi mở cho người dùng thật)
- `fitz` → `pymupdf`; dọn cache uv (~20 GB) và thu gọn ổ ảo WSL

## Ghi chú
- Backup: ~/vskr-backup-2026-11-19 · Ổ C: còn ~49 GB
- Trong DB tài khoản thật có tài khoản `copilot-test-20261116-0751` (có vẻ do Copilot tạo khi thử nghiệm 16/11); chủ dự án quyết có xoá không
