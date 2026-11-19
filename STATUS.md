# Trạng thái dự án (cập nhật 2026-11-19, cuối ngày)

## Đã xong
- PR #5–#13 đã gộp vào `main`:
  - 4 vòng vá security review (#5); dọn dẹp an toàn (#6)
  - OCR (EasyOCR) + VLM (Qwen2.5-VL-3B) chạy tự động trong ingest (#7). Đã chạy trên 2 video thật: Hệ điều hành khoảng 1 phút, PostgreSQL khoảng 11 phút
  - Trường `evidence` + `scripts/eval_variants.py` (#8); sửa xếp hạng graph (#9); bộ test chạy trên thư mục dữ liệu tạm (#10)
  - 36 câu hỏi đánh giá đã được chủ dự án duyệt: 23 câu lời nói, 10 câu chữ trên màn hình, 3 câu hình vẽ (#11, #13)
  - Lọc chữ rác OCR trước khi lập chỉ mục (menu/watermark lặp ở mép màn hình, dòng có dãy ≥ 9 chữ số); `load_config` không còn trả bản trong cache (#13)
- Index thật đã dựng lại với bộ lọc: 179 đoạn; video PostgreSQL giữ 3950/4413 dòng OCR, video Hệ điều hành giữ 531/547
- Chế độ mặc định chuyển sang `hybrid` (PR kèm file này)

## Kết quả đo (36 câu, Recall@5 / MRR)
Chế độ `hybrid`, theo nguồn dữ liệu đưa vào index:

| Index gồm | Lời nói (23) | Màn hình (10) | Hình vẽ (3) | Tất cả |
|---|---|---|---|---|
| Chỉ transcript | 0.91 / 0.82 | 0.80 / 0.75 | 1.00 / 1.00 | 0.89 / 0.81 |
| + OCR chưa lọc | 0.96 / 0.80 | 1.00 / 0.73 | 1.00 / 1.00 | 0.97 / 0.80 |
| + OCR đã lọc | 0.91 / 0.82 | 0.90 / 0.72 | 1.00 / 1.00 | 0.92 / 0.81 |
| + OCR đã lọc + VLM (index thật hiện nay) | 0.91 / 0.80 | 0.90 / 0.72 | 1.00 / 1.00 | 0.92 / 0.80 |

So các chế độ:

| Index gồm | `hybrid` | `hybrid_graph` | `vector` | `bm25` | `graph` |
|---|---|---|---|---|---|
| transcript + OCR đã lọc | 0.92 / 0.81 | 0.90 / 0.68 | 0.89 / 0.77 | 0.86 / 0.73 | 0.75 / 0.52 |
| transcript + OCR đã lọc + VLM | 0.92 / 0.80 | 0.88 / 0.70 | 0.86 / 0.78 | 0.89 / 0.73 | 0.76 / 0.52 |

Bộ nhỏ: chênh 1 câu ≈ 0.04 (lời nói), 0.10 (màn hình), 0.33 (hình vẽ). `pg-02` hầu như không chế độ nào tìm được vì ASR nghe sai từ khóa.

## Đang chờ
- Gộp PR chế độ mặc định `hybrid`, rồi khởi động lại server
- Chủ dự án quyết: với `hybrid`, OCR đã lọc thấp hơn chưa lọc (0.92 so với 0.97, 2 câu vốn ở hạng 4). Giữ cả bộ lọc, hay chỉ giữ luật bỏ dòng có mã số
- Tài khoản `copilot-test-20261116-0751` trong DB thật: xoá hay giữ

## Việc tiếp theo (theo thứ tự)
1. Cập nhật SECURITY-REVIEW.md cho khớp code
2. Dọn code bước 5–8: gom hardcode vào yaml; kiểm tra host YouTube (`util.source_link` còn dùng `endswith("youtube.com")`, khớp cả `evilyoutube.com`); kiểm tra URL thành lỗi nghiệp vụ thay cho `HTTPException`; tách `handlers.py`
3. Thêm câu hỏi về những đoạn code mà OCR đọc sai, rồi mới thử cách đọc code tốt hơn
4. Bộ lọc OCR còn sót chữ giao diện ở giữa màn hình và chữ trên poster

## Tạm hoãn
- Đọc code tốt hơn (VLM đọc nguyên văn, VietOCR, Surya): câu màn hình đã đạt 0.90; VLM tốn khoảng 5 giây/frame
- Video tối đa 5 tiếng; firewall chặn SSRF (cần khi mở cho người dùng thật)
- `fitz` → `pymupdf`; dọn cache uv (~20 GB) và thu gọn ổ ảo WSL
- Xoá các nhánh đã gộp trên GitHub và worktree tạm

## Ghi chú
- Backup: ~/vskr-backup-2026-11-19 · Ổ C: còn ~54 GB
- Có 1 video tải lên chưa có `transcript.json` nên `vskr index` bỏ qua (chưa rõ nguyên nhân)
