# Tóm tắt rà soát bảo mật VSKR

## Kết luận

**Chưa nên mở ingest URL cho người dùng không tin cậy nếu chưa có egress firewall/container chặn đích
không-public trên cả IPv4 và IPv6.** Trong code, kết nối tải của yt-dlp đã được ghim IP, mọi lần chuyển
hướng được kiểm tra lại, và bộ tải FFmpeg/HLS gốc với URL mạng bị từ chối. Firewall vẫn là lớp phòng thủ
thứ hai bắt buộc trước khi mở cho người ngoài.

Các patch vòng 1–4 của đợt review đã được áp vào repo (PR #5, ngày 2026-11-19). Bảng dưới được đối chiếu
lại với code ngày 2026-11-20. Cột "Vị trí" ghi tên hàm thay cho số dòng, để không bị lệch khi code thay đổi.

## Các phát hiện

| # | Mức | Vị trí | Trạng thái (đối chiếu 2026-11-20) | Độ tin cậy |
|---|-----|--------|-----------------------------------|------------|
| 1 | 🟠 HIGH | `pipeline/safe_ytdlp.py` (`SafeYoutubeDL.dl`, `PinnedHTTPHandler`, `PinnedRedirectHandler`), `pipeline/url_safety.py` | **Đã giảm nhẹ trong code, còn mở cho tới khi có firewall.** Kết nối tải ghim vào IP đã kiểm tra (chống DNS rebinding), chuyển hướng được kiểm tra lại, và FFmpeg/HLS gốc với URL mạng bị từ chối. Có test: `test_live_hls_selected_for_ffmpeg_is_rejected_before_network_download`, `test_native_hls_and_external_protocol_downloaders_fail_closed`. Khách chỉ được nạp URL khi đã bật `ingest.guest_url_ingest_enabled` và đặt `VSKR_EGRESS_FIREWALL_CONFIRMED=true`. Chưa thử firewall hay mạng thật. | 8/10 |
| 2 | 🟡 MEDIUM | `access/accounts.py` (`set_guest_cookie`, `guest_owner_id`, `optional_learner_or_admin`) | **Còn mở.** Cookie khách có `Max-Age` 30 ngày ở trình duyệt, nhưng server chỉ băm HMAC token và không kiểm tra hạn; token cũ bị sao chép vẫn được nhận. Sửa cần lưu thời điểm tạo danh tính khách và tính tới dữ liệu khách cũ. | 9/10 |
| 3 | 🟡 MEDIUM | `retrieval/index.py` (`build`), `api/handlers/jobs.py` (`_rebuild_index`) | **Còn mở (thiết kế).** Mỗi lần dựng lại chỉ mục, toàn bộ đoạn được nhúng lại. Đo thật ngày 2026-11-20: 196 đoạn nhúng trong 15 giây trên CPU (E5-small). Ổn ở quy mô hiện tại; kho lớn hơn sẽ cần nhúng tăng dần. | 8/10 |
| 4 | 🟡 MEDIUM | `api/handlers/ask.py` (`GPU_LOCK_TIMEOUT_S`, `ask`), `service.py` (`Service.ask`) | **Thiết kế có chủ đích.** Câu hỏi chờ khe GPU dùng chung tối đa 10 giây rồi trả 503 kèm `Retry-After`. Có test mô phỏng khoá bị giữ; chưa đo khi ASR/OCR thật đang chạy. | 8/10 |
| 5 | ⚪ LOW | `configs/models.yaml` (`retrieval.min_vector_score: null`), `scripts/eval/calibrate_retrieval_threshold.py` | **Còn mở.** Ngưỡng từ chối câu hỏi chưa bật. Đã có 36 câu hỏi đã duyệt trong `data/eval/questions.jsonl`, nên chạy được script hiệu chỉnh cho phía có đáp án; vẫn thiếu câu hỏi không có đáp án để đo tỉ lệ nhận nhầm. | 9/10 |
| 6 | ⚪ LOW | repo | **Phần chính đã xử lý:** `vskr-ui-redesign.zip` và `:Zone.Identifier` đã gỡ khỏi repo (PR #6). Còn lại: chưa có CI (GitHub Actions) tự chạy test. | 10/10 |

### Đã xử lý thêm sau đợt review

- Bộ test từng ghi vào `data/` thật (tạo khoá khách, DB tài khoản, chạy migration). Nay cả bộ test chạy
  trên thư mục tạm, có test canh `tests/test_data_isolation.py` (PR #10).
- OCR đọc cả khung chat của buổi học, trong đó có mã số sinh viên. Các dòng có dãy từ 9 chữ số bị bỏ trước
  khi lập chỉ mục (PR #13); file OCR gốc vẫn nằm trên máy, trong `data/` (không đưa lên git).
- `util.source_link` từng coi `evilyoutube.com` là YouTube. Nay chỉ nhận đúng tên miền YouTube và tên miền
  con của nó (`util.is_youtube_host`, PR #15).
- Kiểm tra URL báo lỗi `UnsafeUrlError` và không còn phụ thuộc FastAPI; API vẫn trả 400 với thông báo cũ (PR #15).
- Giới hạn upload, import câu hỏi, hỏi đáp và địa chỉ Ollama chỉ còn nằm trong `configs/models.yaml`
  (PR #15 và PR sửa lỗi nhỏ ngày 2026-11-20).
- Cookie người học được xoá với cùng thuộc tính `SameSite=Strict` như lúc đặt.

### Đã xác minh trong đợt review

- **A3 access matrix:** guest được hỏi public và private thuộc guest cookie hiện tại; không được hỏi private của guest khác. Learner chỉ được hỏi public và private của mình; admin bypass visibility. Anonymous không có owner ID chỉ thấy public. Test ma trận và test guest rate-limit chạy xanh. Cookie bị thiếu hoặc giả mạo không truy cập private. Expiry phía server vẫn là finding #2.
- **B7:** ingest từ chối livestream và `duration=None` trước download; video thường có duration hợp lệ vẫn đi tiếp. Đã kiểm tra bằng yt-dlp giả, không tải video thật.
- **C3/C4:** test prompt injection và câu hỏi/source được escape, test câu trả lời không citation có `sources=[]`, và test ngân sách prompt với chuỗi dài/Unicode đều xanh.
- **C5:** BM25 tính IDF từ tập `allowed_docs`; permission mask được cache theo `frozenset` các source được phép, giới hạn LRU 512. Test scope khác nhau trả mask khác nhau. Trên Python 3.12.16, benchmark tổng hợp 10k chunks, 200 truy vấn/repeat: median khoảng **0.720 ms/query** với một scope lặp và **0.747 ms/query** với 200 scope khác nhau. Đây là benchmark synthetic, không phải số đo production.
- **D2:** forwarded HTTPS chỉ được tin từ peer khớp `VSKR_TRUSTED_PROXIES`; test IPv4/IPv6 trusted peer và untrusted peer xanh. Chưa thử qua reverse proxy thật.
- **C2:** đã thêm JSON schemas và script tổng hợp nhãn người đánh giá cho correctness/grounding/citation; không có ground truth hoặc nhãn mẫu tự tạo, và chưa có kết quả chất lượng.
- **D5 repo hygiene:** đã dọn phần file rác (PR #6); phần còn lại xem phát hiện #6.

## Thay đổi test trong đợt review vòng 1–3 (lịch sử)

Các test được thay đổi hoặc thêm so với HEAD lúc đó:

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

## Kiểm chứng

- Baseline vòng 1+2: **185 passed, 22 subtests** (đã xác nhận trước khi tiếp tục).
- Full Python suite sau vòng 3, Python 3.12.16, `VSKR_COOKIE_SECURE=false`,
  data/TMPDIR cô lập: **206 passed, 41 subtests**.
- Node suite: **2 passed**.
- `git apply --check` vòng 3 thành công khi áp sau patch vòng 1 và vòng 2.
- `git diff --check` không có lỗi whitespace.
- Repo gốc không bị thay đổi bởi quá trình review; file này là commit summary được yêu cầu riêng.
- Ngày 2026-11-20 (sau khi sắp xếp lại code và sửa lỗi nhỏ): **274 test Python** và **2 test Node** đều đạt.

## Chưa đo trên môi trường thật

- SSRF: không gọi mạng thật; firewall egress chưa được triển khai/xác minh.
- B1: đã có số đo dựng lại thật ở phát hiện #3; muốn đo theo nhiều cỡ kho thì chạy `PYTHONPATH=backend/src python scripts/bench/benchmark_rebuild.py --limits 1,5,20,all` trên máy có dữ liệu, embedding model và Ollama thật.
- B4/GPU: `PYTHONPATH=backend/src python scripts/bench/benchmark_ask_lock.py --question "..." --hold-seconds 12 --ask-timeout 10` chỉ mô phỏng lock. Đo ASR thật cần chạy ingest video hợp lệ trong khi gửi ask đồng thời và thu latency/error rate trên GPU đích.
- D2: cấu hình `VSKR_TRUSTED_PROXIES` bằng IP/CIDR của reverse proxy thật; kiểm thử HTTPS GET không có Origin qua proxy và một request từ peer không tin cậy.
- 200 người dùng: chưa có load-test scenario với 200 identity/IP profile; không dùng một IP để giả lập 200 guest vì rate limit sẽ làm sai kết quả.
- C1/C2: đã có 36 câu hỏi có đáp án (đã duyệt); vẫn cần câu hỏi không có đáp án và nhãn đánh giá câu trả lời/trích dẫn do người hiểu học liệu chấm.
