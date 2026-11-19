# VSKR Security Review Summary

## Verdict

**Chưa nên mở ingest URL cho người dùng không tin cậy nếu chưa có egress
firewall/container chặn đích không-public trên cả IPv4 và IPv6.** Native
yt-dlp HTTP requests trong patch vòng 3 được kiểm tra redirect và ghim IP, nhưng
đã xác nhận bằng đọc code rằng HLS không hỗ trợ có thể được chuyển sang ffmpeg,
vượt ngoài transport này.

Các patch vòng 1-3 được đánh giá trên bản sao sạch từ commit
`ca095d6a90f71ac138ab57e418dea373ac4506e9`. Patch vòng 3 là artifact riêng,
không nằm trong commit này. Line references dưới đây là theo bản sao sau khi áp
các patch, không phải line numbers của HEAD hiện tại. Ngày 2026-11-20 code được chia lại theo khâu: đường dẫn
trong bảng đã đổi sang vị trí mới, còn số dòng vẫn là của bản được review.

## Findings

| # | Severity | File | Lines | Vulnerability / evidence | Confidence |
|---|----------|------|-------|--------------------------|------------|
| 1 | 🟠 HIGH | `backend/src/vskr/pipeline/safe_ytdlp.py` (R3 patch), `README.md` | 38-127; README 79 | SSRF DNS rebinding/redirect được xử lý trong native HTTP transport và kiểm tra bằng mock. Tuy nhiên yt-dlp 2026.10.26 `downloader/hls.py:124-126` có thể giao HLS không hỗ trợ cho ffmpeg. Firewall egress chặn địa chỉ non-global là điều kiện bắt buộc; chưa kiểm thử firewall hoặc tải mạng thật. | 8/10 |
| 2 | 🟡 MEDIUM | `backend/src/vskr/access/accounts.py`, `README.md` | 592-611, 633-637; README 73 | Guest cookie có `Max-Age=30 ngày` ở browser, nhưng backend chỉ HMAC token và không kiểm tra thời điểm hết hạn. Token cũ bị sao chép vẫn được nhận nếu client gửi lại. Chưa đổi semantics hoặc migration vì sẽ ảnh hưởng identity/content legacy. | 9/10 |
| 3 | 🟡 MEDIUM | `backend/src/vskr/api/handlers/jobs.py`, `backend/src/vskr/retrieval/index.py` | 776-790; 82, 139 | Mỗi rebuild đọc corpus và gọi embedder trên toàn bộ chunks. Test dùng embedder giả xác nhận số chunks được nhúng tăng từ 2 lên 10; benchmark thật chưa chạy. | 8/10 |
| 4 | 🟡 MEDIUM | `backend/src/vskr/service.py`, `backend/src/vskr/api/handlers/ask.py` | 85; 134, 744 | Ask đợi shared GPU lock tối đa 10 giây rồi trả lỗi bận. Test mô phỏng lock bị giữ; chưa đo đồng thời với ASR/GPU thật. | 8/10 |
| 5 | ⚪ LOW | `configs/models.yaml`, `scripts/calibrate_retrieval_threshold.py` (R3 patch) | `min_vector_score: null` | Ngưỡng từ chối câu hỏi vẫn chưa bật. Script hiệu chỉnh chỉ báo phân phối điểm và candidate từ positive references; `data/eval/questions.jsonl` hiện rỗng nên chưa thể tính ngưỡng hay false-accept rate. | 9/10 |
| 6 | ⚪ LOW | `requirements.lock.txt`, `vskr-ui-redesign.zip`, `vskr-ui-redesign.zip:Zone.Identifier` | — | Hai ZIP-like files vẫn tracked; không có `pyproject.toml` hoặc GitHub Actions workflow được theo dõi. Không thay đổi hạng mục repo hygiene trong vòng này. | 10/10 |

### Đã xác minh, không còn là finding chưa xử lý

- **A3 access matrix:** guest được hỏi public và private thuộc guest cookie hiện tại; không được hỏi private của guest khác. Learner chỉ được hỏi public và private của mình; admin bypass visibility. Anonymous không có owner ID chỉ thấy public. Test ma trận và test guest rate-limit chạy xanh. Cookie bị thiếu hoặc giả mạo không truy cập private. Expiry phía server vẫn là finding #2.
- **B7:** ingest từ chối livestream và `duration=None` trước download; video thường có duration hợp lệ vẫn đi tiếp. Đã kiểm tra bằng yt-dlp giả, không tải video thật.
- **C3/C4:** test prompt injection và câu hỏi/source được escape, test câu trả lời không citation có `sources=[]`, và test ngân sách prompt với chuỗi dài/Unicode đều xanh.
- **C5:** BM25 tính IDF từ tập `allowed_docs`; permission mask được cache theo `frozenset` các source được phép, giới hạn LRU 512. Test scope khác nhau trả mask khác nhau. Trên Python 3.12.16, benchmark tổng hợp 10k chunks, 200 truy vấn/repeat: median khoảng **0.720 ms/query** với một scope lặp và **0.747 ms/query** với 200 scope khác nhau. Đây là benchmark synthetic, không phải số đo production.
- **D2:** forwarded HTTPS chỉ được tin từ peer khớp `VSKR_TRUSTED_PROXIES`; test IPv4/IPv6 trusted peer và untrusted peer xanh. Chưa thử qua reverse proxy thật.
- **C2:** đã thêm JSON schemas và script tổng hợp nhãn người đánh giá cho correctness/grounding/citation; không có ground truth hoặc nhãn mẫu tự tạo, và chưa có kết quả chất lượng.
- **D5 repo hygiene:** chỉ được kiểm kê, không được thay đổi.

## Test changes: audit vòng 1-3

Các test được thay đổi/thêm so với HEAD là:

| Test file | Lý do | Đánh giá độ chặt |
|---|---|---|
| `tests/test_access_control.py` | Đưa `/api/projects` vào danh sách route được phân loại; cô lập state auth giữa test. | Không bỏ assertion quyền. |
| `tests/test_accounts.py` | Quota project/guest, HMAC migration, retrieval theo owner; vòng 3 thêm token opacity và cookie Max-Age. | Migration chạy lặp được kiểm tra byte-for-byte; không nới quyền. |
| `tests/test_admin_url_safety.cjs` | Test URL không cho phép `javascript:` và chỉ chấp nhận path cùng origin. | File test mới. |
| `tests/test_api_ingest.py` | Cập nhật test theo guest/learner ingest cần `project_id`; thêm cleanup, quota, concurrent active-job và document quota. | Test “unauthenticated upload denied” được thay bằng test guest upload vì guest ingest là route public theo hợp đồng; admin bị từ chối URL ingest được kiểm tra riêng. |
| `tests/test_ask_limits.py` | Fixture Service có `index.chunks` cho lọc visibility mới. | Chỉ bổ sung fixture. |
| `tests/test_auth.py` | Trusted proxy, IPv6 và tách learner/admin failure counters. | Thêm coverage, không xóa kiểm tra cũ. |
| `tests/test_integration.py` | Metadata fixture khai báo `public`; cập nhật kỳ vọng câu trả lời không citation thành `sources=[]`. | Đây là thay đổi hợp đồng có chủ đích; đã ghi vào ACCESS_MATRIX. |
| `tests/test_logic.py` | Kiểm tra BM25 IDF chỉ dùng tài liệu được phép. | Thêm kiểm tra riêng. |
| `tests/test_public_api.py` | Project/API contract, guest owner HMAC, visibility, hybrid mode và rate limit. | `shared_only=True` đổi thành `False` cho ask guest để hỗ trợ private của chính guest; test guest khác vẫn bị từ chối. |
| `tests/test_retriever.py` | LRU cache giới hạn và phân biệt tập quyền. | Vòng 3 siết assertion từ `<513` thành `==512`; không làm yếu kiểm tra. |
| `tests/test_risk_probes.py` | Mock probes cho C3/C4, B1/B4/B7, C1 và D2/D4. | Mọi kết luận từ mocks được ghi rõ; không coi là đo GPU/Ollama/proxy/network thật. |
| `tests/test_answer_evaluation.py` | Kiểm tra percentile candidate và validation/tổng hợp nhãn người đánh giá. | File mới, không chứa dữ liệu ground truth. |
| `tests/test_safe_ytdlp.py` | DNS rebinding, IP pinning, redirect/private IP, IPv6/CGNAT và không có HTTP fallback không pin. | Mock DNS/socket, không có truy cập mạng ngoài. |
| `tests/test_visibility.py` | Ma trận anonymous/guest/learner/admin × public/private. | File mới. |

## Verification

- Baseline vòng 1+2: **185 passed, 22 subtests** (đã xác nhận trước khi tiếp tục).
- Full Python suite sau vòng 3, Python 3.12.16, `VSKR_COOKIE_SECURE=false`,
  data/TMPDIR cô lập: **206 passed, 41 subtests**.
- Node suite: **2 passed**.
- `git apply --check` vòng 3 thành công khi áp sau patch vòng 1 và vòng 2.
- `git diff --check` không có lỗi whitespace.
- Repo gốc không bị thay đổi bởi quá trình review; file này là commit summary được yêu cầu riêng.

## Chưa đo trên môi trường thật

- SSRF: không gọi mạng thật; firewall egress chưa được triển khai/xác minh.
- B1: chạy `PYTHONPATH=backend/src python scripts/benchmark_rebuild.py --limits 1,5,20,all` trên host có corpus, embedding model và Ollama thật.
- B4/GPU: `PYTHONPATH=backend/src python scripts/benchmark_ask_lock.py --question "..." --hold-seconds 12 --ask-timeout 10` chỉ mô phỏng lock. Đo ASR thật cần chạy ingest video hợp lệ trong khi gửi ask đồng thời và thu latency/error rate trên GPU đích.
- D2: cấu hình `VSKR_TRUSTED_PROXIES` bằng IP/CIDR của reverse proxy thật; kiểm thử HTTPS GET không có Origin qua proxy và một request từ peer không tin cậy.
- 200 người dùng: chưa có load-test scenario với 200 identity/IP profile; không dùng một IP để giả lập 200 guest vì rate limit sẽ làm sai kết quả.
- C1/C2: cần bộ câu hỏi, positive/negative cases và nhãn câu trả lời/citation do người hiểu học liệu cung cấp.
