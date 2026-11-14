# VSKR — hỏi đáp bài giảng và quản trị học liệu

VSKR chuyển video bài giảng thành transcript có timestamp, lập chỉ mục để hỏi đáp tiếng Việt và trả lời kèm nguồn. App Người dùng công khai ở `/`; bảng Quản trị ở `/admin` yêu cầu admin đăng nhập. Video/tài liệu cũ được giữ công khai; nội dung mới chỉ xuất hiện sau khi index và mặc định công khai, có thể tắt riêng từng mục trong Quản trị. Giai đoạn hiện tại chạy trực tiếp trong `.venv`; chưa cấu hình Docker. SQLite lưu session, job, cờ hiển thị và phản hồi câu hỏi.

## Thành phần đang dùng

- `backend/src/vskr`: package Python của ứng dụng. Môi trường venv dùng Python 3.12, FFmpeg, yt-dlp, faster-whisper, PySceneDetect, E5-small, BM25/RRF và FastAPI. Embedding chạy CPU; ASR dùng GPU qua CTranslate2.
- QA gọi Ollama API tại `localhost:11434`; cần cài/chạy Ollama riêng. VSKR chạy trực tiếp trong venv.
- Dữ liệu nằm trong `./data`, model/download cache trong `./cache`.
- Hỏi đáp mặc định kết hợp **vector E5 + BM25 + truy xuất đồ thị**, rồi hợp nhất thứ hạng bằng RRF. App Người dùng dùng mode mặc định; admin có thể chọn `hybrid`, `vector`, `bm25` hoặc `graph` để so sánh. Lệnh CLI `vskr ask` cũng nhận cờ `--mode`. Chỉ mục được quét trực tiếp bằng NumPy; FAISS để dành khi dữ liệu lớn hơn.
- Knowledge Graph hỗ trợ node phân cảnh có timestamp, node mục tài liệu, cạnh khái niệm, liên kết video–tài liệu bằng embedding và truy vấn tối đa ba bước. Trong giao diện và lệnh `vskr ingest`, video URL mặc định tải cả hình 720p để tự phát hiện cảnh; dùng `--audio-only` nếu không cần scene graph. Chạy `vskr index` để dựng lại đồ thị sau khi thay đổi học liệu/cấu hình. Liên kết video–tài liệu tự động dựa trên cosine và khái niệm chung (hoặc cosine rất cao), chưa có bước người duyệt.
- Quan hệ có kiểu như `DEPENDS_ON`, `PART_OF`, `CAUSES` được trích từ transcript/tài liệu bằng Ollama trên tối đa 24 đoạn mỗi lần dựng index. Quan hệ chỉ được lưu nếu chủ thể, đối tượng và câu bằng chứng xuất hiện trong nguồn. Nếu Ollama không sẵn sàng, index vẫn tạo graph khái niệm và liên kết video–tài liệu.

PySceneDetect được cài dưới tên package `scenedetect-headless` và import trong Python là `scenedetect`. Nó tìm điểm đổi cảnh; FFmpeg trích frame. Đây không phải package `pyscenecut`.

## Yêu cầu cho môi trường phát triển

- Windows + WSL2 Ubuntu, dự án đặt trong filesystem Linux như `~/vskr`.
- Python 3.12 (có thể cài riêng bằng uv), FFmpeg và Node hoặc Deno cho một số nguồn yt-dlp.
- NVIDIA RTX 4060 Laptop 8 GB và driver NVIDIA để chạy ASR GPU; có thể chuyển ASR sang CPU nếu cần.
- Ollama cần cho hỏi đáp và trích quan hệ có kiểu khi dựng index; nếu không sẵn sàng, phần trích quan hệ tự bỏ qua để index cơ bản vẫn chạy.

## Cài đặt venv

Mở terminal Ubuntu/WSL:

```bash
cd ~/vskr
uv python install 3.12
uv venv --python 3.12 .venv
source scripts/activate-venv.sh
uv pip install -r backend/requirements/base.txt -r backend/requirements/video.txt
uv pip install torch --index-url https://download.pytorch.org/whl/cpu
uv pip install -r backend/requirements/asr.txt -r backend/requirements/embedding.txt
uv pip install --editable backend --no-deps
vskr doctor --gpu-test
```

Để chạy kiểm tra và lint trong môi trường phát triển, cài thêm `backend/requirements/dev.txt`, rồi chạy `pytest -q` và `ruff check backend/src/vskr`.

Các thư viện CUDA tải lần đầu khá lớn. Script kích hoạt venv đặt `HF_HOME`, `PYTHONPATH` và đường dẫn cuBLAS/cuDNN cần cho faster-whisper. Chỉ tiếp tục khi `doctor --gpu-test` báo `OK`.

## Chạy pipeline trong venv

Nếu thư mục dữ liệu local đã có video mẫu `NbJ45r9sbiE` cùng transcript và index, có thể hỏi ngay:

```bash
source scripts/activate-venv.sh
vskr ask "Ý chính của video mẫu là gì?"
```

Repo không chứa video, transcript hay index mẫu. Với bản clone mới, hãy ingest video trước theo các bước bên dưới.

Để ingest video mới, lấy ID trong kết quả ingest rồi dùng ID đó cho ASR:

```bash
vskr ingest "<URL_VIDEO>"
vskr asr --video <video_id>
vskr index
vskr ask "câu hỏi về video"                 # mặc định vector + BM25 + đồ thị
vskr ask "câu hỏi về video" --mode graph    # chỉ truy xuất đồ thị
vskr serve --host 127.0.0.1
```

Thêm tài liệu TXT, MD, RST, PDF hoặc DOCX bằng `vskr doc <đường_dẫn_tệp>` hoặc tại màn **Học liệu** của giao diện Quản trị. Tài liệu được chia mục, lập chỉ mục và có thể truy xuất cùng video. Giao diện cũng cho phép xóa nội dung và thử lại tác vụ lỗi.

Màn **Đánh giá** nhận bộ câu hỏi thật qua giao diện hoặc `data/eval/questions.jsonl`; mỗi dòng có dạng `{"id":"...","question":"...","type":"fact","relevant":[{"video_id":"...","start":12,"end":28}]}` hoặc dùng `document_id` và `section`. `multihop` có thể có nhiều mục đúng. Recall@5 tính một câu fact là tìm thấy nếu có ít nhất một mục đúng; với multihop là tỷ lệ mục đúng được phủ. MRR là trung bình nghịch đảo thứ hạng của kết quả đúng đầu tiên. Đánh giá chỉ chạy retrieval, không gọi LLM. Repo không kèm câu hỏi hay kết quả mẫu; số liệu chỉ xuất hiện sau khi bạn thêm bộ dữ liệu.

Trong giao diện, các thử nghiệm IDF, mở rộng khái niệm từ những đoạn gần nghĩa theo embedding E5 và Personalized PageRank (PPR) chạy trên cùng bộ câu hỏi với baseline. Mỗi lựa chọn được đo riêng; nếu bật nhiều lựa chọn, giao diện cũng đo tổ hợp của chúng. Các điều khiển ở trang graph áp dụng ngay cho truy vấn đang xem và đồng bộ với cấu hình đánh giá. PPR dùng `damping` để điều khiển mức lan truyền và `seed_weight` để phân bổ điểm ban đầu cho các nguồn khớp trực tiếp.

Mở `http://localhost:8000` để dùng app Người dùng. Admin mở `/admin`; đăng nhập bằng tài khoản cấu hình trong `.env`. Người dùng ẩn danh chỉ thấy học liệu đã lập chỉ mục và được bật công khai; retrieval cũng loại các chunk riêng tư trước khi gọi mô hình. Câu hỏi được lưu trong SQLite để quản trị xem tối đa 200 câu gần nhất; IP không được ghi, và người dùng có thể đánh giá câu trả lời bằng 👍/👎. Giao diện Quản trị có Học liệu, Tác vụ, Thử hỏi, Đánh giá, Sơ đồ, Nhật ký và Phản hồi. App Người dùng có thể cài như PWA; khi ngoại tuyến chỉ khung giao diện được cache, không cache API hay video. `Ctrl+C` dừng server.

Trong giao diện Quản trị có thể dán URL được yt-dlp hỗ trợ hoặc upload MP4/MOV/MKV/WebM/AVI; mỗi video tối đa 2 giờ và 1 GB. Video URL được tải ở mức tối đa 720p để phát hiện phân cảnh; video tải lên cũng được phân tích cảnh tự động. Audio tự chia thành các phần 15 phút để nhận dạng, transcript giữ mốc thời gian gốc, rồi index tạo scene graph và liên kết video với tài liệu bằng embedding. Quan hệ có kiểu kèm bằng chứng được Ollama trích trên tối đa 24 đoạn/mục mỗi lần dựng index; nếu Ollama không sẵn sàng, index vẫn dựng khái niệm và liên kết nguồn. Job ingest → phát hiện cảnh → ASR → index chạy tuần tự, giao diện cập nhật tiến độ từng phần. YouTube có player nhúng; video upload phát bằng HTML5 player; nền tảng khác mở URL gốc để đối chiếu.

URL ingest được kiểm tra scheme, DNS và IP trước khi tải. yt-dlp có thể đi theo redirect sau bước kiểm tra ban đầu; chỉ cho admin đáng tin cậy dùng chức năng ingest URL và đặt server sau reverse proxy/firewall phù hợp.

Nếu Ollama đang chạy bằng service Windows/WSL, để Ollama phục vụ `localhost:11434`. Nếu cài native Ollama, pull model một lần bằng `ollama pull qwen3.5:4b`; không cài/chạy hai Ollama cùng lúc trên một cổng.

## Benchmark ASR tiếng Việt (tùy chọn)

Mặc định vẫn là faster-whisper `large-v3-turbo` vì đã chạy nhanh trên máy này. Để so độ chính xác với ứng viên PhoWhisper-medium, tạo 10–20 clip ngắn từ nhiều đoạn/giọng khác nhau, rồi tự ghi transcript chuẩn đã nghe và sửa tay; **không dùng transcript ASR làm đáp án chuẩn**.

```bash
mkdir -p data/asr_eval
ffmpeg -y -ss 00:00:32 -i data/videos/NbJ45r9sbiE/audio.wav -t 20 \
  -ac 1 -ar 16000 -c:a pcm_s16le data/asr_eval/clip_001.wav
```

Tạo `data/asr_eval/manifest.jsonl`, mỗi dòng một clip:

```json
{"audio":"data/asr_eval/clip_001.wav","reference":"Nội dung đã nghe và sửa chính xác."}
```

Sau khi điền các transcript chuẩn, chạy:

```bash
source scripts/activate-venv.sh
python scripts/benchmark_asr.py data/asr_eval/manifest.jsonl
```

Script so sánh model ASR hiện tại với `vinai/PhoWhisper-medium`, báo WER/CER và thời gian, rồi lưu kết quả vào `data/asr_eval/results.json`. Candidate chạy CPU theo mặc định vì venv core đang dùng PyTorch CPU; do đó chỉ so WER/CER, **không so tốc độ CPU candidate với baseline GPU**. Không đổi model mặc định cho đến khi kết quả trên audio bài giảng của bạn tốt hơn.

## Cấu hình

Sửa `configs/models.yaml`; venv đọc file này trực tiếp mỗi lần chạy lệnh. Nếu đổi `embedding.model`, tiền tố embedding hoặc tham số chunk, hãy chạy lại `vskr index`. Có thể ghi đè cấu hình bằng biến môi trường, ví dụ `VSKR__ASR__DEVICE=cpu` hoặc `VSKR__RETRIEVAL__MODE=hybrid`. Ứng dụng cũng nạp `.env` ở thư mục gốc; biến đã export trong môi trường tiến trình được ưu tiên hơn nội dung `.env`.

### Tài khoản quản trị

Tạo `.env` từ mẫu rồi sinh hash bằng lệnh tương tác. Lệnh không nhận mật khẩu qua argv và không ghi mật khẩu thô ra file:

```bash
cp .env.example .env
source scripts/activate-venv.sh
vskr admin hash-password
```

Đặt `VSKR_ADMIN_USER` và dán hash Argon2id được in vào `VSKR_ADMIN_PASSWORD_HASH` trong `.env`. Không đặt mật khẩu mặc định và không commit `.env`. Khi chưa có cả hai giá trị hợp lệ, các route quản trị bị tắt; login báo admin chưa được cấu hình. `VSKR_COOKIE_SECURE` mặc định là `true`. Chỉ đặt `false` cho phát triển qua `http://localhost`; ứng dụng từ chối cookie không Secure trên host bên ngoài. `VSKR_TRUSTED_PROXIES` nhận danh sách IP/CIDR, phân cách bằng dấu phẩy; nếu để trống, app bỏ qua `X-Forwarded-For`.

## Triển khai

Chạy app chỉ trên loopback rồi để reverse proxy kết thúc HTTPS. Ví dụ với Caddy, cấu hình domain trỏ tới server:

```caddyfile
vskr.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Trong terminal khác:

```bash
source scripts/activate-venv.sh
vskr serve --host 127.0.0.1 --port 8000
```

Caddy tự cấp/chăm sóc chứng chỉ TLS cho domain công khai đủ điều kiện. Với Cloudflare Tunnel, trỏ tunnel tới `http://127.0.0.1:8000` và giữ cổng ứng dụng không mở ra Internet. Nếu chạy app trong container hoặc cần bind `0.0.0.0`, chỉ mở cổng đó trong mạng nội bộ giữa proxy và app; firewall không cho client truy cập trực tiếp. Giữ `VSKR_COOKIE_SECURE=true` khi người dùng vào qua HTTPS và bảo đảm reverse proxy chuyển tiếp đúng `Host`.

Chỉ điền `VSKR_TRUSTED_PROXIES` bằng địa chỉ/CIDR thực của proxy trực tiếp gọi app, ví dụ `127.0.0.1/32,::1/128` khi Caddy chạy cùng máy. Không tin proxy hoặc header do client tùy ý cung cấp. Không cần bật CORS; frontend và API được phục vụ từ cùng origin.

Sao lưu thư mục `data/` định kỳ để giữ video, audio, transcript, chỉ mục, tài liệu, kết quả đánh giá, SQLite và nhật ký. Tạo bản sao nhất quán bằng cách dừng VSKR trước khi sao chép, hoặc dùng snapshot filesystem. Sao lưu `.env` và `configs/models.yaml` riêng ở kho mã hóa có quyền truy cập hạn chế; `.env` chứa hash xác thực. `cache/` chỉ chứa model/download cache và có thể tải lại, nhưng giữ nó giúp tránh tải model lớn lần nữa.

Session admin và job được lưu trong `data/vskr.sqlite3`. Session hết sau 8 giờ tuyệt đối hoặc 60 phút không hoạt động và vẫn có hiệu lực qua lần restart nếu chưa hết hạn. Job đang chạy lúc server dừng được đánh dấu lỗi để admin có thể retry; executor vẫn chạy một worker trong một tiến trình. SQLite giữ trạng thái khi restart, nhưng không biến pipeline/GPU thành dịch vụ multi-worker an toàn: hãy chạy một worker và giữ rate limit trong một tiến trình. Câu hỏi gần nhất được giữ tối đa 10.000 bản ghi; audit JSONL xoay vòng ở 5 MiB, giữ ba file cũ.

Upload `/api/ingest`, `/api/documents` và `/api/eval/questions/import` được xác thực trước khi FastAPI đọc multipart body, đồng thời bị giới hạn kích thước trong middleware. Cấu hình giới hạn ở reverse proxy nữa để từ chối body quá lớn trước khi chuyển tới ứng dụng. Ví dụ sau dùng `request_body` của Caddy 2.10+ (directive được đánh dấu experimental trong [tài liệu Caddy](https://caddyserver.com/docs/caddyfile/directives/request_body)); các mức gồm giới hạn file mặc định cộng 1 MiB multipart overhead:

```caddyfile
vskr.example.com {
    route {
        @videoUpload path /api/ingest
        request_body @videoUpload {
            max_size 1025MiB
        }
        @documentUpload path /api/documents
        request_body @documentUpload {
            max_size 101MiB
        }
        @evaluationImport path /api/eval/questions/import
        request_body @evaluationImport {
            max_size 6MiB
        }
        reverse_proxy 127.0.0.1:8000
    }
}
```

`/api/ingest` và URL tải bằng yt-dlp được kiểm tra DNS/IP trước khi tải, nhưng downloader có thể đi theo redirect sau bước kiểm tra ban đầu. Chỉ cho admin đáng tin cậy dùng URL ingest; nếu cần chặn SSRF mạnh hơn, giới hạn egress của tiến trình ở firewall/network policy.

Để giảm tải GPU nếu Whisper float16 thiếu VRAM, đặt `asr.compute_type` thành `int8_float16`. Để chạy ASR bằng CPU, đặt `asr.device: cpu` và `asr.compute_type: int8`; tốc độ sẽ chậm hơn.

## Dữ liệu và lưu trữ

- `data/videos/<id>/`: video/audio, metadata, transcript và timestamp.
- `data/index/`: chunks, vectors và metadata chỉ mục.
- `data/vskr.sqlite3`: session admin, job pipeline, quyền hiển thị và log câu hỏi/phản hồi.
- `data/audit.jsonl*`: nhật ký quản trị xoay vòng.
- `cache/`: model cache của Hugging Face và yt-dlp.

Không xóa `data/` hoặc `cache/` nếu còn cần video, transcript, index hay model đã tải. Ollama lưu model theo cách cài đặt Ollama đang dùng.

## Phạm vi hiện tại

Knowledge Graph là baseline nghiên cứu, chưa phải bản SceneRAG/InstructKG đầy đủ: ranh giới cảnh lấy từ PySceneDetect (cắt cảnh hình ảnh), không dùng LLM để sửa ranh giới theo mạch bài giảng; trích quan hệ có kiểu dùng Ollama với giới hạn số đoạn; liên kết video–tài liệu được kiểm tra bằng ngưỡng embedding và trùng khái niệm, chưa có người duyệt. Truy vấn graph duyệt tối đa 3 cạnh; hãy đánh giá với bộ câu hỏi có đáp án và nguồn đúng trước khi kết luận graph tốt hơn hybrid. OCR/PaddleOCR, VLM, FAISS (requirements riêng tại `backend/requirements/faiss.txt`) và frontend SvelteKit chưa được bật mặc định trong luồng MVP. Docker sẽ được cấu hình lại ở giai đoạn đóng gói cuối.

## Cấu trúc dự án

```text
vskr/
├── backend/
│   ├── src/vskr/          # mã nguồn Python
│   ├── requirements/      # dependencies chia theo nhóm chức năng
│   └── pyproject.toml     # metadata và entry point CLI
├── configs/models.yaml    # cấu hình model và pipeline
├── scripts/               # tiện ích phát triển local (venv)
├── tests/                 # unit/integration tests
├── docs/                  # ma trận quyền truy cập
├── .github/workflows/     # CI: pytest + Ruff
├── web/                   # giao diện demo MVP
├── data/                  # video, transcript, index (local, không commit)
├── cache/                 # model/download cache (local, không commit)
├── requirements.lock.txt  # phiên bản đã freeze cho core venv
└── README.md
```

`data/`, `cache/`, `.venv/` và `.env` là dữ liệu/môi trường local, không đưa vào Git. Không xóa chúng nếu còn cần video, model đã tải hoặc môi trường venv.
