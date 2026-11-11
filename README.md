# VSKR — phát triển local bằng venv

VSKR chuyển video bài giảng thành transcript có timestamp, lập chỉ mục để hỏi đáp tiếng Việt và trả lời kèm nguồn. Giai đoạn hiện tại phát triển/test trực tiếp trong `.venv`; chưa cấu hình Docker. OCR, VLM, SQLite và frontend SvelteKit chưa thuộc luồng MVP hiện tại.

## Thành phần đang dùng

- `backend/src/vskr`: package Python của ứng dụng. Môi trường venv dùng Python 3.12, FFmpeg, yt-dlp, faster-whisper, PySceneDetect, E5-small, BM25/RRF và FastAPI. Embedding chạy CPU; ASR dùng GPU qua CTranslate2.
- QA gọi Ollama API tại `localhost:11434`; cần cài/chạy Ollama riêng. VSKR chạy trực tiếp trong venv.
- Dữ liệu nằm trong `./data`, model/download cache trong `./cache`.
- Hỏi đáp mặc định kết hợp **vector E5 + BM25 + truy xuất đồ thị**, rồi hợp nhất thứ hạng bằng RRF. Người dùng chỉ cần đặt câu hỏi; có thể chọn riêng `hybrid`, `vector`, `bm25` hoặc `graph` để so sánh. Chỉ mục được quét trực tiếp bằng NumPy; FAISS để dành khi dữ liệu lớn hơn.
- Knowledge Graph hỗ trợ node phân cảnh video có timestamp, node mục tài liệu, cạnh khái niệm, liên kết video–tài liệu bằng embedding và truy vấn nhiều bước. Với video đã ingest kèm hình (`vskr ingest --with-video`), chạy `vskr frames --video <id>`; nếu `frames.json` đã cũ thì thêm `--force`. Sau đó chạy `vskr index`. Các liên kết liên phương thức chỉ được giữ khi điểm cosine vượt ngưỡng và có khái niệm trùng (hoặc điểm rất cao). Có thể chọn riêng `graph` để xem kết quả chỉ từ đồ thị.
- Muốn trích xuất quan hệ có kiểu như `DEPENDS_ON`, `PART_OF`, `CAUSES` từ transcript và tài liệu, bật `VSKR__KNOWLEDGE_GRAPH__EXTRACT_RELATIONS=true` rồi chạy lại `vskr index`. Ollama sẽ xử lý tối đa số đoạn đặt trong `knowledge_graph.max_relation_chunks`; mọi quan hệ được lưu kèm bằng chứng văn bản và bị bỏ qua nếu không khớp nội dung nguồn. Tính năng này tắt mặc định để index vẫn chạy độc lập Ollama.

PySceneDetect được cài dưới tên package `scenedetect-headless` và import trong Python là `scenedetect`. Nó tìm điểm đổi cảnh; FFmpeg trích frame. Đây không phải package `pyscenecut`.

## Yêu cầu cho môi trường phát triển

- Windows + WSL2 Ubuntu, dự án đặt trong filesystem Linux như `~/vskr`.
- Python 3.12 (có thể cài riêng bằng uv), FFmpeg và Node hoặc Deno cho một số nguồn yt-dlp.
- NVIDIA RTX 4060 Laptop 8 GB và driver NVIDIA để chạy ASR GPU; có thể chuyển ASR sang CPU nếu cần.
- Ollama chỉ cần khi chạy QA; nếu không dùng câu hỏi đáp thì các bước ingest, ASR và index không cần Ollama.

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
vskr serve --host 0.0.0.0
```

Thêm tài liệu TXT, MD, RST, PDF hoặc DOCX bằng `vskr doc <đường_dẫn_tệp>` hoặc tại màn **Thư viện** trên giao diện web. Tài liệu được chia mục, lập chỉ mục và có thể truy xuất cùng video. Giao diện cũng cho phép xóa nội dung và thử lại tác vụ lỗi.

Màn **Đánh giá** nhận bộ câu hỏi thật qua giao diện hoặc `data/eval/questions.jsonl`; mỗi dòng có dạng `{"id":"...","question":"...","type":"fact","relevant":[{"video_id":"...","start":12,"end":28}]}` hoặc dùng `document_id` và `section`. `multihop` có thể có nhiều mục đúng. Recall@5 tính một câu fact là tìm thấy nếu có ít nhất một mục đúng; với multihop là tỷ lệ mục đúng được phủ. MRR là trung bình nghịch đảo thứ hạng của kết quả đúng đầu tiên. Đánh giá chỉ chạy retrieval, không gọi LLM. Repo không kèm câu hỏi hay kết quả mẫu; số liệu chỉ xuất hiện sau khi bạn thêm bộ dữ liệu.

Trong giao diện, các thử nghiệm IDF, mở rộng khái niệm từ những đoạn gần nghĩa theo embedding E5 và Personalized PageRank (PPR) chạy trên cùng bộ câu hỏi với baseline. Mỗi lựa chọn được đo riêng; nếu bật nhiều lựa chọn, giao diện cũng đo tổ hợp của chúng. Các điều khiển ở trang graph áp dụng ngay cho truy vấn đang xem và đồng bộ với cấu hình đánh giá. PPR dùng `damping` để điều khiển mức lan truyền và `seed_weight` để phân bổ điểm ban đầu cho các nguồn khớp trực tiếp.

Mở `http://localhost:8000` để dùng giao diện. `Ctrl+C` dừng server. Trong giao diện, người dùng có thể dán URL được yt-dlp hỗ trợ hoặc upload MP4/MOV/MKV/WebM/AVI; mỗi video tối đa 30 phút và 1 GB. Job ingest → ASR → index chạy tuần tự, giao diện cập nhật trạng thái. YouTube có player nhúng; video upload phát bằng HTML5 player; nền tảng khác mở URL gốc để đối chiếu.

API hiện chưa có xác thực. Chạy với `--host 0.0.0.0` chỉ trong mạng tin cậy; endpoint nhận URL để yt-dlp tải nội dung.

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

Sửa `configs/models.yaml`; venv đọc file này trực tiếp mỗi lần chạy lệnh. Nếu đổi `embedding.model`, tiền tố embedding hoặc tham số chunk, hãy chạy lại `vskr index`. Có thể ghi đè cấu hình bằng biến môi trường, ví dụ `VSKR__ASR__DEVICE=cpu` hoặc `VSKR__RETRIEVAL__MODE=hybrid`.

Để giảm tải GPU nếu Whisper float16 thiếu VRAM, đặt `asr.compute_type` thành `int8_float16`. Để chạy ASR bằng CPU, đặt `asr.device: cpu` và `asr.compute_type: int8`; tốc độ sẽ chậm hơn.

## Dữ liệu và lưu trữ

- `data/videos/<id>/`: video/audio, metadata, transcript và timestamp.
- `data/index/`: chunks, vectors và metadata chỉ mục.
- `cache/`: model cache của Hugging Face và yt-dlp.

Không xóa `data/` hoặc `cache/` nếu còn cần video, transcript, index hay model đã tải. Ollama lưu model theo cách cài đặt Ollama đang dùng.

## Phạm vi hiện tại

Knowledge Graph là baseline nghiên cứu, chưa phải bản SceneRAG/InstructKG đầy đủ: ranh giới video lấy từ PySceneDetect (cắt cảnh hình ảnh), không dùng LLM để sửa ranh giới theo mạch bài giảng; trích quan hệ có kiểu là tùy chọn Ollama; liên kết video–tài liệu được kiểm tra bằng ngưỡng embedding và trùng khái niệm, chưa có người duyệt. Truy vấn graph duyệt tối đa 3 cạnh; hãy đánh giá với bộ câu hỏi có đáp án và nguồn đúng trước khi kết luận graph tốt hơn hybrid. OCR/PaddleOCR, VLM, FAISS (requirements riêng tại `backend/requirements/faiss.txt`), SQLite project/job và frontend SvelteKit chưa được bật mặc định trong luồng MVP. Docker sẽ được cấu hình lại ở giai đoạn đóng gói cuối.

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
├── web/                   # giao diện demo MVP
├── data/                  # video, transcript, index (local, không commit)
├── cache/                 # model/download cache (local, không commit)
├── requirements.lock.txt  # phiên bản đã freeze cho core venv
└── README.md
```

`data/`, `cache/`, `.venv/` và `.env` là dữ liệu/môi trường local, không đưa vào Git. Không xóa chúng nếu còn cần video, model đã tải hoặc môi trường venv.
