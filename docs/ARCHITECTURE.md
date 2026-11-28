# Kiến trúc VSKR

VSKR biến video bài giảng và tài liệu thành nguồn hỏi đáp tiếng Việt. Mỗi câu trả lời trích dẫn đoạn video (kèm mốc thời gian) hoặc mục tài liệu mà nó dựa vào. Cả hệ thống chạy trên một máy: WSL2, GPU RTX 4060 Laptop 8 GB.

Các hình dưới đây viết bằng Mermaid nên GitHub tự vẽ. Tên module trong hình tính từ `backend/src/vskr/`.

## Các khối chính

```mermaid
flowchart TB
    u_learner["Người học / Khách<br/>web/user (PWA)"]
    u_admin["Quản trị viên<br/>web/admin"]
    u_cli["Dòng lệnh vskr<br/>cli.py"]

    subgraph g_api["Lớp API · api/"]
        a_mw["Middleware<br/>chặn upload quá lớn, header bảo mật"]
        a_public["public.py<br/>trang và API cho người học, khách"]
        a_admin["admin.py<br/>trang quản trị, /api/admin, /api/eval"]
        a_limits["limits.py<br/>giới hạn câu hỏi"]
    end

    acc["access/<br/>auth · accounts · visibility · audit"]

    subgraph g_handlers["Nghiệp vụ · api/handlers/"]
        h_jobs["jobs<br/>hàng đợi nạp, dựng chỉ mục"]
        h_library["library<br/>thư viện, xoá, quyền mở file"]
        h_ask["ask<br/>nhận câu hỏi"]
        h_eval["evaluation<br/>bộ câu hỏi, chấm điểm"]
        h_graph["graph<br/>xem đồ thị tri thức"]
        h_runtime["runtime<br/>service dùng chung, khoá GPU"]
    end

    subgraph g_core["Lõi xử lý"]
        c_pipeline["pipeline/<br/>tải video, dò cảnh, ASR, frame, OCR, VLM, tài liệu"]
        c_retrieval["retrieval/<br/>chia đoạn, lọc chữ rác, E5, BM25, đồ thị, tìm 5 chế độ"]
        c_service["service.py + qa.py<br/>tìm kiếm rồi gọi LLM, gắn trích dẫn"]
    end

    s_data[("data/<br/>videos, documents, index, eval,<br/>accounts.sqlite3, audit.jsonl")]
    x_ollama["Ollama · qwen3.5:4b<br/>localhost:11434"]

    u_learner -->|HTTP + cookie phiên| a_public
    u_admin -->|HTTP + CSRF| a_admin
    g_api -->|phiên, quyền xem| acc
    g_api -->|gọi handlers.module| g_handlers
    g_handlers -->|gọi lõi| g_core
    u_cli -.->|gọi thẳng lõi, bỏ qua API và phân quyền| g_core
    c_pipeline -->|ghi file từng bước| s_data
    c_retrieval -->|ghi, đọc index/| s_data
    acc -->|tài khoản, nhật ký| s_data
    c_service -->|/api/chat| x_ollama
```

- Trình duyệt chỉ gọi lớp API. API hỏi `access/` về phiên và quyền, rồi gọi `api/handlers/`; handlers mới gọi xuống lõi.
- Import trong `api/handlers/` chỉ đi một chiều: `runtime ← jobs ← library` và `runtime ← ask, evaluation, graph`. Trạng thái dùng chung (service, các khoá, danh sách job) chỉ nằm trong `runtime`.
- Server có 66 đường dẫn: 41 trong `public.py`, 25 trong `admin.py`. Quyền của từng đường dẫn ghi ở [ACCESS_MATRIX.md](ACCESS_MATRIX.md).
- Job và phiên admin nằm trong RAM nên mất khi khởi động lại server. Tài khoản, phiên và lịch sử hội thoại của người học nằm trong SQLite.

## Nạp video

```mermaid
flowchart LR
    subgraph g_visual["Bước có ảnh: lỗi chỉ thêm cảnh báo"]
        s4["4 · Trích frame<br/>frames.py"]
        s5["5 · OCR · GPU<br/>ocr.py"]
        s6["6 · VLM · GPU<br/>vlm.py"]
    end
    s1["1 · Tải video<br/>ingest.py"]
    s2["2 · Dò phân cảnh<br/>frames.py"]
    s3["3 · ASR · GPU<br/>asr.py"]
    s7["7 · Lập chỉ mục<br/>retrieval/index.py"]
    s8["8 · Nạp lại service<br/>api/handlers/runtime.py"]
    d1["Tài liệu TXT, MD, RST, PDF, DOCX<br/>pipeline/documents.py"]
    s1 --> s2 --> s3 --> s4 --> s5 --> s6 --> s7 --> s8
    d1 --> s7
```

`POST /api/ingest` đưa video vào hàng đợi; mỗi lúc chỉ chạy một job. Mỗi bước ghi kết quả ra file, bước sau đọc file của bước trước, nên chạy lại riêng một bước được (`vskr ingest` → `asr` → `frames` → `ocr` → `vlm` → `index`).

| Bước | Công cụ | Ghi ra |
|---|---|---|
| 1 · Tải video | yt-dlp qua `safe_ytdlp`, kiểm tra URL bằng `url_safety` | `data/videos/ID/source.*`, `audio.wav`, `meta.json` |
| 2 · Dò phân cảnh | PySceneDetect | `scenes.json` |
| 3 · ASR | faster-whisper `large-v3-turbo`, mỗi phần 15 phút | `transcript.json`, `.srt`, `.txt` |
| 4 · Trích frame | ffmpeg, chạy song song | `frames/`, `frames.json` |
| 5 · OCR | EasyOCR `vi` + `en`, gộp dòng lặp lại giữa các frame | `screen_text.json`, `ocr_info.json` |
| 6 · VLM | Qwen2.5-VL-3B 4-bit, chỉ mô tả frame ít chữ | `captions.json`, `vlm_info.json` |
| 7 · Lập chỉ mục | chia đoạn, lọc chữ rác OCR, E5, BM25, đồ thị (Ollama trích quan hệ) | `data/index/chunks.json`, `vectors.npy`, `knowledge_graph.json`, `index_info.json` |
| 8 · Nạp lại | service mới thay service cũ | (trong RAM) |

- OCR và VLM chạy trong tiến trình con bằng Python của `.venv-vlm`. Bước 4–6 nếu lỗi thì job chỉ ghi cảnh báo và vẫn lập chỉ mục từ lời giảng.
- Bước 7 bỏ chữ rác OCR (menu, watermark, dãy số dài như mã sinh viên) trước khi chia đoạn; file `screen_text.json` gốc không bị sửa.
- Chỉ mục mới được dựng trong thư mục tạm, xong mới thay thư mục cũ. Câu hỏi đang chạy dùng nốt service cũ; service cũ chỉ đóng khi câu cuối cùng trả lời xong.
- Tài liệu (`POST /api/documents`) được chia mục vào `data/documents/ID/` (`meta.json`, `sections.json`) rồi vào cùng chỉ mục.

## Hỏi đáp

```mermaid
flowchart LR
    q["Câu hỏi<br/>POST /api/ask"] --> c1{"không trống,<br/>≤ 500 ký tự?"}
    c1 -->|không| r422["422"]
    c1 --> c2{"còn chỗ?<br/>tối đa 3 câu cùng lúc"}
    c2 -->|không| r503a["503"]
    c2 --> c3{"≤ 10 câu/phút,<br/>≤ 100 câu/giờ mỗi IP?"}
    c3 -->|không| r429["429"]
    c3 --> vis["Lọc nguồn<br/>chỉ đoạn người hỏi được xem"]
    vis -->|hỏi riêng 1 nguồn không được xem| r404["404"]
    vis --> find["Tìm hybrid<br/>E5 + BM25, gộp RRF, lấy 5 đoạn"]
    find --> c4{"có khoá GPU<br/>trong 10 giây?"}
    c4 -->|không| r503b["503 + Retry-After"]
    c4 --> llm["Ollama trả lời<br/>có trích dẫn [n]"]
    llm --> out["Service đổi [n] thành mốc thời gian,<br/>link YouTube hoặc mục tài liệu"]
```

- Ba kiểm tra đầu nằm ở `api/public.py` và `api/limits.py`, các con số lấy từ mục `limits` trong `configs/models.yaml`; lọc nguồn, tìm kiếm và gọi LLM nằm ở `service.py`, `retrieval/retriever.py`, `qa.py`.
- Người học và khách luôn dùng chế độ `hybrid`. Admin hỏi qua `/api/admin/ask` và chọn được cả 5 chế độ.
- LLM chỉ nhận 5 đoạn đã lọc, và prompt không chứa mốc thời gian.
- Khi 5 đoạn vượt `llm.num_ctx`, `qa.build_messages` chia đều chỗ cho từng đoạn và giữ các mẩu chứa từ hiếm của câu hỏi (theo IDF của BM25), chỗ bị lược ghi "…" (`llm.context_packing: extract`). Cách cũ `prefix` cắt đuôi và có thể bỏ hẳn các đoạn cuối.

Vì sao mặc định là `hybrid`: đo ngày 20/11/2026 trên 36 câu hỏi đã duyệt, 3 video, 196 đoạn (chỉ mục có cả chữ OCR và mô tả VLM).

| Chế độ | Cách tìm | Recall@5 | MRR |
|---|---|---:|---:|
| `hybrid` (mặc định) | vector E5 + BM25, gộp thứ hạng bằng RRF | 0.92 | 0.79 |
| `hybrid_graph` | như hybrid, thêm kết quả từ đồ thị tri thức | 0.89 | 0.71 |
| `bm25` | khớp từ khoá | 0.86 | 0.72 |
| `vector` | so nghĩa bằng model nhúng E5 | 0.83 | 0.76 |
| `graph` | chỉ đi theo đồ thị khái niệm | 0.74 | 0.53 |

Recall@5: câu một ý được 1 điểm nếu có đoạn đúng trong 5 kết quả đầu; câu nhiều ý (6/36 câu) được tính theo tỉ lệ ý tìm thấy. MRR càng gần 1 thì đoạn đúng càng đứng đầu.

## GPU và các tiến trình

```mermaid
flowchart LR
    job["Job nạp video<br/>1 job mỗi lúc"] -->|ASR, OCR, VLM| lock["Khoá GPU<br/>_gpu_model_lock"]
    ask["Câu hỏi<br/>chờ tối đa 10 giây"] -->|LLM| lock
    lock --> asr["ASR · faster-whisper<br/>trong tiến trình vskr serve"]
    lock --> ocrvlm["OCR, VLM<br/>tiến trình con .venv-vlm"]
    lock --> llm["Ollama · qwen3.5:4b<br/>tiến trình riêng"]
    asr --> gpu[("GPU 8 GB VRAM")]
    ocrvlm --> gpu
    llm --> gpu
```

- GPU 8 GB không chứa nổi cùng lúc model ASR, OCR, VLM và LLM, nên ASR, OCR, VLM và bước LLM của câu hỏi đều phải xin một khoá chung.
- OCR và VLM dùng venv riêng vì cần torch bản CUDA; `.venv` giữ torch bản CPU cho model nhúng E5. E5 chạy CPU nên không cần khoá.
- Trước khi OCR và VLM nạp model, bước đó bảo Ollama nhả model khỏi VRAM (`keep_alive: 0`).
- Ollama chạy riêng bằng `ollama serve`. WSL không có systemd, nên tắt terminal đang chạy Ollama là hỏi đáp sẽ lỗi.

## Quyền xem

```mermaid
stateDiagram-v2
    [*] --> private: tải lên
    private --> pending: chủ sở hữu đề xuất
    pending --> private: chủ sở hữu rút lại
    pending --> public: admin duyệt
    public --> private: chủ sở hữu hoặc admin gỡ
```

`access/visibility.py` (hàm `is_visible`) là quy tắc duy nhất cho thư viện, mở file và tìm kiếm: admin thấy tất cả, `public` ai cũng thấy, còn lại chỉ chủ sở hữu thấy. Chủ sở hữu chỉ đặt được `private` hoặc `pending`; admin đặt được trạng thái bất kỳ.

| Người dùng | Đăng nhập | Lưu ở đâu | Quyền, hạn mức |
|---|---|---|---|
| Admin | tài khoản trong `.env`, mật khẩu băm Argon2id; có CSRF, chặn khi đăng nhập sai nhiều lần | phiên trong RAM | xem và đổi trạng thái mọi học liệu |
| Người học | tự đăng ký (giới hạn số lần theo IP), giữ phiên bằng cookie | `data/accounts.sqlite3`: tài khoản, phiên, hội thoại | video 1 GiB, dài 2 giờ; tài liệu 100 MB; 2 job cùng lúc, 20 job mỗi ngày |
| Khách | không cần tài khoản, nhận diện bằng cookie khách | cookie trên trình duyệt; không lưu lịch sử hội thoại | chỉ tải file lên, chưa được nạp bằng URL; video 100 MB, dài 15 phút; tài liệu 20 MB; 1 job cùng lúc, 3 job mỗi ngày |

Hạn mức lấy từ mục `limits` trong `configs/models.yaml`. Khách chỉ nạp được URL khi bật `ingest.guest_url_ingest_enabled` và đã xác nhận có firewall chặn kết nối ra mạng nội bộ.

## Thư mục

| Đường dẫn | Việc |
|---|---|
| `backend/src/vskr/cli.py` | lệnh `vskr`: ingest, asr, frames, slides, ocr, vlm, index, doc, ask, serve, doctor, admin |
| `backend/src/vskr/config.py`, `util.py` | đọc cấu hình (`models.yaml`, `.env`, biến `VSKR__*`); hàm dùng chung |
| `backend/src/vskr/service.py`, `qa.py` | giữ chỉ mục và bộ tìm kiếm; dựng prompt, gọi Ollama, tách trích dẫn |
| `backend/src/vskr/evaluation.py`, `doctor.py` | chấm Recall@5, MRR; kiểm tra môi trường |
| `backend/src/vskr/pipeline/` | ingest, safe_ytdlp, url_safety, frames, slide_change (thử nghiệm, chạy tay), asr, ocr, vlm, steps, documents |
| `backend/src/vskr/retrieval/` | chunker, screen_filter, embedder, bm25, fusion, knowledge_graph, index, retriever |
| `backend/src/vskr/access/` | auth, accounts, visibility, audit |
| `backend/src/vskr/api/` | app FastAPI và middleware; route trong `public.py`, `admin.py`; `limits.py`; logic trong `handlers/` |
| `backend/requirements/` | thư viện chia nhóm; `core.lock.txt` là phiên bản đã chốt cho `.venv` |
| `web/user/`, `web/admin/`, `web/shared/` | trang người học (PWA), trang quản trị, file dùng chung |
| `configs/models.yaml` | file cấu hình duy nhất |
| `scripts/` | `activate-venv.sh`; `bench/` đo hiệu năng; `eval/` đánh giá truy hồi, câu trả lời và bộ dò trạng thái slide |
| `tests/` | chia thư mục giống backend (`pipeline/`, `retrieval/`, `access/`, `api/`), thêm `web/` và `scripts/`; test chạy xuyên nhiều khâu nằm ở gốc `tests/` |
| `docs/` | tài liệu này; [STATUS.md](./STATUS.md), [DECISIONS.md](./DECISIONS.md), [SECURITY-REVIEW.md](./SECURITY-REVIEW.md), [ACCESS_MATRIX.md](./ACCESS_MATRIX.md), [ROADMAP.md](./ROADMAP.md) (lộ trình nâng cấp, chưa triển khai) |
| `data/`, `cache/` | video, transcript, chỉ mục, tài khoản; model đã tải. Chỉ có trên máy, không đưa lên git |
