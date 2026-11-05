# VSKR — local MVP

VSKR chuyển video bài giảng thành transcript có timestamp, lập chỉ mục để hỏi đáp tiếng Việt và trả lời kèm nguồn. Hướng dẫn này chỉ dựng đường chạy MVP giai đoạn 1 trên Docker Compose; OCR, VLM, SQLite và frontend SvelteKit chưa thuộc luồng chạy này.

## Thành phần đang dùng

- `backend/src/vskr`: package Python của ứng dụng. Image core có Python 3.12, FFmpeg, yt-dlp + Deno, faster-whisper, PySceneDetect, E5-small, BM25/RRF và FastAPI. Embedding chạy CPU; ASR dùng GPU.
- `ollama`: Qwen theo `configs/models.yaml`. Chỉ nạp LLM khi hỏi đáp; ASR yêu cầu Ollama nhả model khỏi VRAM trước khi chạy.
- Dữ liệu nằm trong `./data`, cache model và yt-dlp trong `./cache`, còn Ollama lưu model trong volume Docker `ollama`.
- Tìm kiếm mặc định là **hybrid**: vector E5 tìm theo ngữ nghĩa + BM25 tìm từ khóa, sau đó RRF hợp nhất thứ hạng. Có thể chọn riêng `vector` hoặc `bm25` để so sánh. Với kho hiện tại chỉ 12 chunks, NumPy quét trực tiếp là đủ; FAISS để dành khi dữ liệu lớn hơn.

PySceneDetect được cài dưới tên package `scenedetect-headless` và import trong Python là `scenedetect`. Nó tìm điểm đổi cảnh; FFmpeg trích frame. Đây không phải package `pyscenecut`.

## Yêu cầu

- Windows 11 + WSL2 Ubuntu, Docker Desktop đang chạy và đã bật WSL Integration cho Ubuntu.
- NVIDIA RTX 4060 Laptop 8 GB cùng driver NVIDIA hiện hành; Docker Desktop phải chuyển GPU vào container được.
- Nên có ít nhất 30 GB trống cho image, model và video. Đặt dự án trong filesystem Linux của WSL, ví dụ `~/vskr`, không đặt dưới `/mnt/c`.
- Tắt Ollama cài trực tiếp trong WSL/Windows nếu nó đã chiếm cổng 11434 hoặc GPU.

## Cài đặt và chạy

Mở terminal Ubuntu trong WSL:

```bash
cd ~/vskr
mkdir -p data cache
cp .env.example .env
id -u
id -g
```

Đảm bảo hai giá trị `HOST_UID` và `HOST_GID` trong `.env` bằng kết quả hai lệnh cuối. Sau đó build core và khởi động Ollama:

```bash
docker compose build vskr
docker compose up -d ollama
docker compose exec ollama ollama pull qwen3.5:4b
```

Mặc định dùng Qwen 4B để giảm RAM/VRAM. Nếu muốn dùng model 9B (đã tải ở workspace hiện tại) và máy còn đủ bộ nhớ, đổi `llm.model` trong `configs/models.yaml` thành `qwen3.5:9b`. Ollama được cấu hình nhả model sau 30 giây không hoạt động để giảm bộ nhớ nền.

Kiểm tra container nhìn thấy GPU và các phụ thuộc:

```bash
docker run --rm --gpus all ubuntu nvidia-smi
docker compose run --rm vskr doctor --gpu-test
```

`doctor --gpu-test` tải model Whisper `tiny` lần đầu và chạy inference thử thật. Chỉ tiếp tục nếu kết quả GPU test là OK.

Dependency ASR giới hạn PyAV dưới 19 vì `faster-whisper` hiện dùng `metadata_errors` trong `av.open()`, tham số PyAV 19 đã loại bỏ. Nếu image đã được build trước khi thêm giới hạn này, chạy lại:

```bash
docker compose build vskr
docker compose run --rm vskr doctor --gpu-test
docker compose run --rm vskr asr --video NbJ45r9sbiE
```

## Chạy video mẫu đầu-cuối

Workspace hiện đã có video tiếng Việt dài 477 giây (7 phút 57 giây), ID `NbJ45r9sbiE`, cùng `audio.wav` 16 kHz mono. Có thể chạy ASR trực tiếp trên dữ liệu này, không cần tải lại:

```bash
docker compose run --rm vskr asr --video NbJ45r9sbiE
```

Đọc `data/videos/NbJ45r9sbiE/transcript.txt`; trong `transcript.json` kiểm tra `detected_language`, timestamp, `realtime_factor` và số đoạn. Nếu bước này thành công, tiếp tục lập chỉ mục và hỏi thử:

```bash
docker compose run --rm vskr index
docker compose run --rm vskr ask "Docker Compose giúp giải quyết vấn đề gì?" --mode hybrid
```

Muốn thử một video khác thì thay bằng pipeline ingest trước:

```bash
docker compose run --rm vskr ingest "<URL_VIDEO>"
docker compose run --rm vskr asr --video <video_id>
docker compose run --rm vskr index
docker compose run --rm vskr ask "câu hỏi về video" --mode hybrid
```

Khi transcript và câu trả lời có nguồn hợp lý, khởi động giao diện:

```bash
docker compose up -d
```

Mở `http://localhost:8000`. Có thể ingest URL từ các nền tảng yt-dlp hỗ trợ; YouTube có player nhúng và nhảy timestamp, nền tảng khác mở URL gốc để đối chiếu (timestamp không có chuẩn dùng chung). Nếu chạy lại `index` khi giao diện đã mở, bấm **Reload** hoặc gọi `curl -X POST http://localhost:8000/api/reload`.

## Các lệnh khác

```bash
docker compose run --rm vskr doctor
docker compose run --rm vskr frames --video <video_id>
docker compose run --rm vskr ask "câu hỏi" --mode vector
docker compose run --rm vskr ask "câu hỏi" --mode bm25
docker compose down
```

`frames` cần video tải kèm hình: dùng `ingest --with-video` khi ingest. Lệnh này chưa chạy OCR; OCR/VLM là giai đoạn sau và chưa được đưa vào cấu hình khởi chạy MVP để tránh trộn stack Paddle với PyTorch khi chưa kiểm thử tương thích.

## Cấu hình

Sửa `configs/models.yaml`; compose mount thư mục config và `backend/src` trực tiếp nên thay đổi có hiệu lực mà không cần build lại. Nếu đổi `embedding.model`, tiền tố embedding hoặc tham số chunk, hãy chạy lại `index`. Có thể ghi đè cấu hình bằng biến môi trường dạng `VSKR__ASR__DEVICE=cpu` hoặc `VSKR__RETRIEVAL__MODE=hybrid`.

Để giảm tải GPU nếu Whisper float16 thiếu VRAM, đặt `asr.compute_type` thành `int8_float16`. Để chạy ASR bằng CPU, đặt `asr.device: cpu` và `asr.compute_type: int8`; tốc độ sẽ chậm hơn.

## Dữ liệu và lưu trữ

- `data/videos/<id>/`: video/audio, metadata, transcript và timestamp.
- `data/index/`: chunks, vectors và metadata chỉ mục.
- `cache/`: model cache của Hugging Face và yt-dlp.
- Volume `ollama`: model Qwen.

`docker compose down` không xóa các dữ liệu này. Không xóa `data/`, `cache/` hay volume `ollama` nếu còn cần video, transcript hoặc model đã tải.

## Phạm vi hiện tại

Đã kiểm chứng core venv trên video mẫu: faster-whisper GPU tạo transcript, E5 lập index và Qwen/Ollama trả lời có nguồn. Theo log người dùng gửi, cùng core pipeline cũng chạy trong Docker và API `/api/status` trả `ready: true`; bước còn cần xác nhận thủ công là bấm nguồn trong trình duyệt để kiểm tra video nhảy đúng timestamp. OCR/PaddleOCR, VLM, xử lý tài liệu, FAISS (requirements riêng tại `backend/requirements/faiss.txt`), SQLite project/job và frontend SvelteKit chưa được bật trong luồng MVP.

## Cấu trúc dự án

```text
vskr/
├── backend/
│   ├── src/vskr/          # mã nguồn Python
│   ├── requirements/      # dependencies chia theo nhóm chức năng
│   └── pyproject.toml     # metadata và entry point CLI
├── configs/models.yaml    # cấu hình model và pipeline
├── docker/                # Dockerfile theo nhóm service
├── scripts/               # tiện ích phát triển local (venv)
├── tests/                 # unit/integration tests
├── web/                   # giao diện demo MVP
├── data/                  # video, transcript, index (local, không commit)
├── cache/                 # model/download cache (local, không commit)
├── docker-compose.yml     # orchestration local: vskr + ollama
├── requirements.lock.txt  # phiên bản đã freeze cho core venv
├── .env.example           # mẫu biến môi trường; copy thành .env
└── README.md
```

`data/`, `cache/`, `.venv/` và `.env` là dữ liệu/môi trường local, không đưa vào Git hoặc Docker build context. Không xóa chúng nếu còn cần video, model đã tải hoặc môi trường venv.

## Phát triển bằng venv trước Docker

Đây là môi trường thử nghiệm trên WSL; Docker Compose vẫn là cách chạy/đóng gói cuối cùng. Dùng Python 3.12 để khớp với `Dockerfile.core`. Nếu máy đang dùng Python khác, có thể cài bản riêng bằng [uv](https://docs.astral.sh/uv/guides/install-python/); không cần thay Python hệ thống:

```bash
uv python install 3.12
uv venv --python 3.12 .venv
source scripts/activate-venv.sh
uv pip install -r backend/requirements/base.txt -r backend/requirements/video.txt
uv pip install torch --index-url https://download.pytorch.org/whl/cpu
uv pip install -r backend/requirements/asr.txt -r backend/requirements/embedding.txt
uv pip install --editable backend --no-deps
vskr doctor --gpu-test
```

`scripts/activate-venv.sh` kích hoạt `.venv`, trỏ mã nguồn Python và model cache về repo, đồng thời nạp đường dẫn cuBLAS/cuDNN mà faster-whisper cần. PyTorch CPU là đủ cho E5 vì embedding đang chạy CPU; faster-whisper dùng GPU qua CTranslate2. Chỉ chạy ASR thật sau khi `doctor --gpu-test` báo OK. OCR/Paddle và VLM/PyTorch CUDA sẽ được thử trong môi trường tách riêng theo từng giai đoạn để tránh xung đột thư viện.
