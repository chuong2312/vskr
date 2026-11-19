# VSKR — hỏi đáp bài giảng và quản trị học liệu

VSKR chuyển video bài giảng và tài liệu thành nguồn hỏi đáp tiếng Việt có trích dẫn. Khách mở app Người học ở `/` và hỏi trên thư viện chung mà không cần tài khoản; đăng nhập để lưu và xem lịch sử, hoặc thêm học liệu riêng. Bảng Quản trị ở `/admin` yêu cầu admin đăng nhập. Giai đoạn hiện tại chạy trực tiếp trong `.venv`; chưa cấu hình Docker. SQLite lưu tài khoản, phiên đăng nhập và lịch sử hội thoại; học liệu, job và index vẫn nằm trên filesystem. OCR/VLM là bước tùy chọn, chưa chạy tự động trong luồng MVP; frontend dùng HTML/CSS/JavaScript thuần.

## Thành phần đang dùng

- `backend/src/vskr`: package Python của ứng dụng. Môi trường venv dùng Python 3.12, FFmpeg, yt-dlp, faster-whisper, PySceneDetect, E5-small, BM25/RRF và FastAPI. Embedding chạy CPU; ASR dùng GPU qua CTranslate2.
- QA gọi Ollama API tại `localhost:11434`; cần cài/chạy Ollama riêng. VSKR chạy trực tiếp trong venv.
- Dữ liệu nằm trong `./data`, model/download cache trong `./cache`.
- Admin và CLI mặc định kết hợp **vector E5 + BM25 + truy xuất đồ thị**, rồi hợp nhất thứ hạng bằng RRF. App Người học lọc dữ liệu theo quyền tài khoản và dùng hybrid vector + BM25; admin có thể so sánh `hybrid`, `vector`, `bm25` và `graph`. Lệnh CLI `vskr ask` cũng nhận cờ `--mode`. Chỉ mục được quét trực tiếp bằng NumPy; FAISS để dành khi dữ liệu lớn hơn.
- Knowledge Graph hỗ trợ node phân cảnh có timestamp, node mục tài liệu, cạnh khái niệm, liên kết video–tài liệu bằng embedding và truy vấn tối đa ba bước. Trong giao diện và lệnh `vskr ingest`, video URL mặc định tải cả hình 720p để phát hiện cảnh; cấu hình mặc định bỏ qua 4 frame giữa các lần phân tích để giảm thời gian quét. Nếu bộ giải mã không đọc được cảnh, hệ thống vẫn tiếp tục lập chỉ mục từ transcript. Dùng `--audio-only` nếu không cần scene graph. Chạy `vskr index` để dựng lại sau khi đổi học liệu/cấu hình. Liên kết video–tài liệu dựa trên cosine và khái niệm chung (hoặc cosine rất cao), chưa có bước người duyệt.
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
uv pip install -r backend/requirements/docs.txt   # đọc PDF/DOCX cho `vskr doc`
uv pip install --editable backend --no-deps
vskr doctor --gpu-test
```

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

Thêm tài liệu TXT, MD, RST, PDF hoặc DOCX bằng `vskr doc <đường_dẫn_tệp>` hoặc thư viện Người học. Khách và người học có thể thêm video/tài liệu qua giao diện; nội dung mới mặc định là `private`, chủ sở hữu có thể đề xuất `pending`, rồi admin duyệt thành `public`. Admin không thêm nội dung qua API ingest; màn **Kiểm duyệt học liệu** chỉ dùng để duyệt, từ chối hoặc xóa. Tài liệu được chia mục, lập chỉ mục và có thể truy xuất cùng video.

Màn **Đánh giá** nhận bộ câu hỏi qua giao diện hoặc `data/eval/questions.jsonl`; mỗi dòng có dạng `{"id":"...","question":"...","type":"fact","relevant":[{"video_id":"...","start":12,"end":28}]}` hoặc dùng `document_id` và `section`. `multihop` có thể có nhiều mục đúng. Recall@5 tính một câu fact là tìm thấy nếu có ít nhất một mục đúng; với multihop là tỷ lệ mục đúng được phủ. MRR là trung bình nghịch đảo thứ hạng của kết quả đúng đầu tiên. Đánh giá chỉ chạy retrieval, không gọi LLM; nó không đo tính đúng của câu trả lời, mức độ trung thực hay độ chính xác của trích dẫn. Bộ câu hỏi và kết quả lưu trong `data/`, vốn bị Git bỏ qua; checkout mới không có ground truth cho tới khi người quản trị nhập câu hỏi và kiểm chứng nguồn đúng. Không dùng kết quả trên bộ nhỏ hoặc chưa được rà soát để kết luận graph hay phương thức nào tốt hơn. Tối thiểu nên có 20 câu đã được người hiểu học liệu kiểm tra, gồm câu fact và multihop, trước khi so sánh retrieval; cần đánh giá riêng câu trả lời và citation trước khi kết luận về chất lượng RAG. Nếu bộ câu hỏi thay đổi, giao diện sẽ đánh dấu kết quả cũ; admin cần chạy lại để có số liệu khớp. Kết quả mới được lưu tại `data/eval/results.json`.

`min_vector_score` vẫn mặc định `null`, tức là chưa bật bộ lọc ngưỡng. Sau khi có ground truth retrieval do con người xác minh, có thể chạy `python scripts/calibrate_retrieval_threshold.py --questions data/eval/questions.jsonl` để xem phân phối điểm và ngưỡng ứng viên theo phân vị 5% của điểm tốt nhất trên nguồn liên quan. Đây chỉ là ứng viên thiên về recall; nếu chưa có câu hỏi không thể trả lời đã được gán nhãn thì chưa đo được tỷ lệ nhận nhầm, vì vậy không bật ngưỡng chỉ dựa trên báo cáo này.

Đánh giá chất lượng câu trả lời dùng ground truth riêng do người hiểu học liệu tạo, không sinh dữ liệu mẫu tự động. Mỗi dòng `data/eval/answer-ground-truth.jsonl` tuân theo `data/eval/answer-ground-truth.schema.json` và ghi `id`, câu hỏi, đáp án kỳ vọng cùng nguồn đúng; sau khi đọc câu trả lời và nguồn thực tế, người đánh giá ghi các nhãn boolean `answer_correct`, `grounded`, `citation_correct` vào `data/eval/answer-reviews.jsonl` theo `data/eval/answer-review.schema.json`. Tổng hợp nhãn bằng `python scripts/summarize_answer_eval.py --ground-truth data/eval/answer-ground-truth.jsonl --reviews data/eval/answer-reviews.jsonl`. Script chỉ kiểm tra tính đầy đủ/định dạng và tổng hợp phán đoán của người đánh giá; không tự chấm ngữ nghĩa và không tạo ground truth.

Trong giao diện, các thử nghiệm IDF, mở rộng khái niệm từ những đoạn gần nghĩa theo embedding E5 và Personalized PageRank (PPR) chạy trên cùng bộ câu hỏi với baseline. Mỗi lựa chọn được đo riêng; nếu bật nhiều lựa chọn, giao diện cũng đo tổ hợp của chúng. Các điều khiển ở trang graph áp dụng ngay cho truy vấn đang xem và đồng bộ với cấu hình đánh giá. PPR dùng `damping` để điều khiển mức lan truyền và `seed_weight` để phân bổ điểm ban đầu cho các nguồn khớp trực tiếp.

Mở `http://localhost:8000/` để dùng ngay, không cần đăng nhập: khách xem thư viện chung, hỏi bài và thêm nội dung trong hạn mức khách; khách không có lịch sử lưu lâu dài. Khách có thể hỏi phần công khai và nội dung private do cookie guest hiện tại sở hữu, nhưng không thể hỏi private của guest khác. Cookie có thời hạn 30 ngày ở trình duyệt; backend hiện chưa tự từ chối token guest cũ nếu client vẫn gửi lại cookie đã sao chép. Người học tự tạo tài khoản từ nút **Đăng ký** trên web, không cần admin duyệt hoặc mã mời; giới hạn số lần đăng ký theo địa chỉ IP. Đăng nhập để lưu và xem lại lịch sử, đồng thời nhận hạn mức cao hơn. Nội dung mới chỉ chủ sở hữu thấy cho đến khi admin duyệt công khai; trong lần khởi động đầu tiên, migration đánh dấu học liệu legacy không có owner là `public`, còn học liệu có owner nhưng thiếu visibility là `private`. Sau migration, trường visibility bị thiếu được xem là `private`. Câu hỏi của learner tìm trong phần công khai và phần riêng của tài khoản hiện tại, không giới hạn ở video đang mở. Truy xuất phía người học dùng hybrid vector + BM25 trên dữ liệu đã lọc theo quyền; graph vẫn sẵn trong khu admin để thử nghiệm. `min_vector_score` hiện chưa được hiệu chỉnh và mặc định là `null`; không coi kết quả top-k là bằng chứng câu hỏi có liên quan khi ngưỡng này chưa được đo trên bộ dữ liệu thật. Giao diện đặt hội thoại cạnh video; trên màn nhỏ, video nằm phía trên. Nút thư viện mở danh sách bài giảng/tài liệu, ô tìm nhanh lọc theo tên. **Cuộc hỏi mới** bắt đầu hội thoại khác nhưng không xóa lịch sử cũ. Mỗi câu được truy xuất độc lập, không dựa vào câu trước; câu như “ý trên là gì?” cần nêu rõ khái niệm. Thẻ nguồn dài được thu gọn và có nút **Xem thêm**. Bấm nguồn video khác sẽ chuyển sang video đó; bấm nguồn tài liệu sẽ mở đúng mục.
Ô hỏi giới hạn 500 ký tự; khi chạm giới hạn, dòng gợi ý báo phần dài hơn sẽ bị bỏ. Câu rỗng và câu quá dài nhận thông báo tiếng Việt. Khi hết chỗ xử lý cùng lúc, app báo đang xử lý nhiều câu hỏi và cho thử lại; khi đang cập nhật học liệu, thông báo riêng vẫn được giữ.
Admin mở `/admin` (chưa đăng nhập sẽ chuyển tới `/admin/login`) bằng tài khoản cấu hình trong `.env`. Màn **Tài khoản** cho admin xem, tắt và mở lại tài khoản người học. Admin chuẩn bị nội dung dùng chung, quản lý backend và thử nghiệm retrieval/graph; người học tự thêm nội dung cá nhân sau khi đăng nhập. PWA lưu trang đăng nhập và tài nguyên tĩnh; không cache API, video hoặc tài liệu. Cần mạng để tải thư viện và gửi câu hỏi. `Ctrl+C` dừng server.

Khách có thể dán link YouTube hoặc upload video tối đa 100 MB/15 phút, tối đa 1 job đang chạy và 3 job/ngày; người học có hạn mức 1 GB/2 giờ, 2 job đang chạy và 20 job/ngày. Tài liệu khách tối đa 20 MB. Request bị giới hạn tổng body ở tầng ứng dụng; tệp JSONL đánh giá tối đa 5 MB. URL video được tải ở mức tối đa 720p để phát hiện phân cảnh; audio tự chia thành các phần 15 phút để nhận dạng và giữ timestamp gốc. Khi có scene boundary, index tạo scene graph và liên kết video–tài liệu bằng embedding. Caption VLM chỉ được thêm vào tìm kiếm nếu admin đã chạy bước VLM riêng; OCR cũng là bước tùy chọn. Quan hệ có kiểu kèm bằng chứng được Ollama trích trên tối đa 24 đoạn/mục mỗi lần dựng index. Job ingest → phát hiện cảnh → ASR → index chạy lần lượt; nếu phát hiện cảnh lỗi, index tiếp tục bằng transcript. YouTube có player nhúng; video upload phát bằng HTML5 player; nền tảng khác mở URL gốc để đối chiếu.

URL ingest kiểm tra scheme, DNS, IP và domain allowlist; native HTTP transport kiểm tra và ghim IP công khai khi kết nối, đồng thời kiểm tra lại mỗi redirect. Trước khi tải, VSKR chỉ cho phép các downloader native `HttpFD` và `DashSegmentsFD`; từ chối `FFmpegFD`, `HlsFD` (kể cả HLS mã hóa có thể fallback) và các downloader/giao thức khác có thể tự mở kết nối. FFmpeg vẫn có thể xử lý file cục bộ sau tải. Guest URL ingest mặc định tắt; chỉ bật `ingest.guest_url_ingest_enabled` sau khi egress firewall thực sự được cấu hình và xác nhận bằng `VSKR_EGRESS_FIREWALL_CONFIRMED=true`. Biến xác nhận là cam kết cấu hình của operator, không phải phép tự phát hiện firewall. Startup log cảnh báo nếu chưa xác nhận. **Egress firewall/container chặn toàn bộ địa chỉ không global trên IPv4 và IPv6, bao gồm loopback, link-local, private, CGNAT và địa chỉ metadata, vẫn là điều kiện bắt buộc khi nhận URL từ người dùng không tin cậy**; kiểm tra ứng dụng không thay thế ranh giới mạng này.

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

Sửa `configs/models.yaml`; venv đọc file này trực tiếp mỗi lần chạy lệnh. Nếu đổi `embedding.model`, tiền tố embedding hoặc tham số chunk, hãy chạy lại `vskr index`. Có thể ghi đè cấu hình bằng biến môi trường, ví dụ `VSKR__ASR__DEVICE=cpu`, `VSKR__FRAMES__FRAME_SKIP=4` hoặc `VSKR__RETRIEVAL__MODE=hybrid`. Ứng dụng cũng nạp `.env` ở thư mục gốc; biến đã export trong môi trường tiến trình được ưu tiên hơn nội dung `.env`.

### Đo rebuild và thời gian chờ hỏi

Với dữ liệu thật, `PYTHONPATH=backend/src python scripts/benchmark_rebuild.py --limits 1,5,20,all`
đo wall time và embedding time ở các quy mô nguồn khác nhau. Script chỉ sao chép
JSON transcript/tài liệu sang thư mục tạm, không ghi đè `data/index`; graph vẫn
có thể gọi Ollama theo cấu hình hiện tại.
`python scripts/benchmark_ask_lock.py --question "..." --hold-seconds 12 --ask-timeout 10`
đo retrieval và thời gian chờ của `Service.ask` khi benchmark giữ shared lock.
Lock trong script được giả lập, không đại diện cho thời lượng ASR hoặc GPU thực.
`python scripts/benchmark_bm25_scopes.py --chunks 10000 --scopes 200 --repeats 3`
đo tốc độ chấm BM25 khi lọc theo nhiều tập quyền khác nhau; dùng dữ liệu tổng hợp,
không đọc `data/`.

### Tài khoản quản trị

Tạo `.env` từ mẫu rồi sinh hash bằng lệnh tương tác. Lệnh không nhận mật khẩu qua argv và không ghi mật khẩu thô ra file:

```bash
cp .env.example .env
source scripts/activate-venv.sh
vskr admin hash-password
```

Đặt `VSKR_ADMIN_USER` và dán hash Argon2id được in vào `VSKR_ADMIN_PASSWORD_HASH` trong `.env`. Không đặt mật khẩu mặc định và không commit `.env`. Khi chưa có cả hai giá trị hợp lệ, các route quản trị bị tắt; login báo admin chưa được cấu hình. `VSKR_COOKIE_SECURE` mặc định là `true`. Chỉ đặt `false` cho phát triển qua `http://localhost`; ứng dụng từ chối cookie không Secure trên host bên ngoài. `VSKR_TRUSTED_PROXIES` nhận danh sách IP/CIDR, phân cách bằng dấu phẩy; nếu để trống, app bỏ qua `X-Forwarded-For`.

Đăng ký người học mở trực tiếp trên `/register` hoặc từ nút **Đăng ký**; không cần quản trị viên tạo tài khoản, duyệt hoặc cấp mã mời. Mỗi IP bị giới hạn số lần tạo tài khoản trong một giờ. Tài khoản, phiên và lịch sử hội thoại được lưu trong `data/accounts.sqlite3`; chỉ tài khoản đã đăng nhập mới lưu, xem hoặc xóa được lịch sử của chính mình. Admin có thể thu hồi quyền vào bằng cách tắt tài khoản tại `/admin` → **Tài khoản**. Học liệu cũ chưa có `owner_id` được xem là thư viện dùng chung, còn nội dung mới của người học được giới hạn theo tài khoản.

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

Chỉ điền `VSKR_TRUSTED_PROXIES` bằng địa chỉ/CIDR thực của proxy trực tiếp gọi app, ví dụ `127.0.0.1/32,::1/128` khi Caddy chạy cùng máy. Proxy phải ghi đè `X-Forwarded-Proto` và `X-Forwarded-Host`; ứng dụng chỉ dùng hai header này khi socket peer thuộc danh sách tin cậy. Uvicorn không tự áp dụng proxy headers để giữ nguyên peer cho phép kiểm tra. Không tin proxy hoặc header do client tùy ý cung cấp. Không cần bật CORS; frontend và API được phục vụ từ cùng origin.

Sao lưu thư mục `data/` định kỳ để giữ video, audio, transcript, chỉ mục, tài liệu, kết quả đánh giá và `audit.jsonl`. Tạo bản sao nhất quán bằng cách dừng VSKR trước khi sao chép, hoặc dùng snapshot filesystem. Sao lưu `.env` và `configs/models.yaml` riêng ở kho mã hóa có quyền truy cập hạn chế; `.env` chứa hash xác thực. `cache/` chỉ chứa model/download cache và có thể tải lại, nhưng giữ nó giúp tránh tải model lớn lần nữa.

Admin session được giữ trong RAM, hết sau 8 giờ tuyệt đối hoặc 60 phút không hoạt động; restart server thu hồi mọi session. Hàng đợi tác vụ cũng ở RAM nên trạng thái job hiện tại mất khi restart. URL ingest có kiểm tra/pin địa chỉ và từ chối downloader giao thức không an toàn; guest URL ingest mặc định tắt cho đến khi operator bật cấu hình và xác nhận egress firewall.

Để giảm tải GPU nếu Whisper float16 thiếu VRAM, đặt `asr.compute_type` thành `int8_float16`. Để chạy ASR bằng CPU, đặt `asr.device: cpu` và `asr.compute_type: int8`; tốc độ sẽ chậm hơn.

## Dữ liệu và lưu trữ

- `data/videos/<id>/`: video/audio, metadata, transcript và timestamp.
- `data/index/`: chunks, vectors và metadata chỉ mục.
- `cache/`: model cache của Hugging Face và yt-dlp.

Không xóa `data/` hoặc `cache/` nếu còn cần video, transcript, index hay model đã tải. Ollama lưu model theo cách cài đặt Ollama đang dùng.

## Phạm vi hiện tại

Knowledge Graph là baseline nghiên cứu, chưa phải bản SceneRAG/InstructKG đầy đủ: ranh giới cảnh lấy từ PySceneDetect, không dùng LLM để sửa theo mạch bài giảng; trích quan hệ bằng Ollama có giới hạn số đoạn; liên kết video–tài liệu dựa trên embedding và khái niệm, chưa có người duyệt. Truy vấn graph duyệt tối đa 3 cạnh; cần bộ câu hỏi đủ lớn, có đáp án và nguồn đúng trước khi kết luận graph tốt hơn hybrid. OCR/PaddleOCR, VLM, FAISS (requirements riêng tại `backend/requirements/faiss.txt`), SQLite cho job/index và frontend SvelteKit chưa bật mặc định. Docker sẽ được cấu hình ở giai đoạn đóng gói.

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
├── web/                   # giao diện demo MVP
├── data/                  # video, transcript, index (local, không commit)
├── cache/                 # model/download cache (local, không commit)
├── requirements.lock.txt  # phiên bản đã freeze cho core venv
└── README.md
```

`data/`, `cache/`, `.venv/` và `.env` là dữ liệu/môi trường local, không đưa vào Git. Không xóa chúng nếu còn cần video, model đã tải hoặc môi trường venv.
