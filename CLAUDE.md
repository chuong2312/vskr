# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Ngôn ngữ trả lời
Luôn trả lời, giải thích và tóm tắt cho người dùng bằng tiếng Việt (giữ nguyên tên lệnh, đường dẫn, tên hàm và thuật ngữ kỹ thuật bằng tiếng Anh khi cần).

VSKR (Video Semantic Knowledge Retrieval) turns lecture videos and documents into a Vietnamese Q&A source with citations. It is a student research project (đồ án). README.md, UI strings, error messages (`detail`) and code comments are in Vietnamese; keep new user-facing text in Vietnamese.

## Working agreement (the owner's rules)
- Each task: short plan → owner approves → do it → run checks → report what changed. One small step per commit so it can be reverted.
- Ask first, item by item, before deleting files, restructuring, adding/installing libraries or changing direction. An unanswered item in a list of recommendations is not approval.
- A request marked read-only/report-only ends at the report.
- Do not start the app or long jobs (`vskr serve`, `ingest`, `asr`, `frames`, `ocr`, `vlm`, `index`) on the owner's data unless asked. The owner runs them to watch the logs. Check readiness, then hand over the command.
- Say "chưa chắc" instead of guessing; back claims with `file:line` evidence.
- End of session: summarize and update `STATUS.md` (once it exists).

## Environment & commands
Runs directly in `.venv` (Python 3.12, WSL2). There's no Docker. Always activate with the script, because it sets `PYTHONPATH=backend/src`, `HF_HOME=cache/huggingface` and the cuBLAS/cuDNN paths for faster-whisper:

    source scripts/activate-venv.sh

- Install: see README "Cài đặt venv" (uv + split requirement files in `backend/requirements/*.txt`; `uv pip install --editable backend --no-deps`). `requirements.lock.txt` pins the core venv. `docs.txt` adds PDF/DOCX reading for `vskr doc`.
- OCR (EasyOCR) and VLM share their **own venv** `.venv-vlm` (`backend/requirements/vlm.txt`; see README), because both need CUDA torch while `.venv` keeps CPU torch for the embedder. Never install CUDA torch into `.venv`. PaddleOCR was tried and dropped: its recognition dictionaries (incl. PP-OCRv6) lack Vietnamese two-diacritic letters (ấ, ữ, ệ, ớ…). Its GPU build also pins older cuDNN/cuBLAS/numpy than ASR uses and does not resolve together with torch 2.15.
- Env check: `vskr doctor --gpu-test`
- Pipeline CLI (`backend/src/vskr/cli.py`): `vskr ingest <url>` → `vskr asr --video <id>` → (`vskr frames` → `vskr ocr` → `vskr vlm`) → `vskr index`; `vskr doc <file>`; `vskr ask "..." [--mode hybrid|vector|bm25|graph|hybrid_graph]`; `vskr serve --host 127.0.0.1 --port 8000`; `vskr admin hash-password`.
- Tests use stdlib `unittest` (pytest also works as a runner):
  - all: `python -m unittest discover -s tests -t .`
  - one: `python -m unittest tests.test_access_control` or `tests.test_auth.TestX.test_y`
  - Without a local `.env`, set `VSKR_COOKIE_SECURE=false` or 2 project tests in `test_public_api` fail.
  - In a git worktree, point `PYTHONPATH` at that worktree's `backend/src`; the activate script points at `~/vskr`.
  - Node: `node tests/test_admin_url_safety.cjs`; `node --check web/admin/admin.js` after editing frontend JS.
- There's no linter or formatter config.

External runtime deps: Ollama at `localhost:11434` (QA + typed-relation extraction; index still builds without it), FFmpeg, yt-dlp, NVIDIA GPU (RTX 4060 Laptop, 8 GB VRAM) for ASR/OCR/VLM.

## Configuration
- `configs/models.yaml` is the single config file, re-read on each load (`config.load_config`, cached by mtime + env). Override any `section.key` with `VSKR__SECTION__KEY=<yaml value>`; `VSKR_CONFIG` points to an alternate file. The repo root is found by locating `configs/models.yaml`.
- `.env` at the root is loaded without overriding exported vars: `VSKR_ADMIN_USER`, `VSKR_ADMIN_PASSWORD_HASH` (Argon2id; admin routes are disabled if missing), `VSKR_COOKIE_SECURE` (default true; false only for http://localhost), `VSKR_TRUSTED_PROXIES`, `VSKR_EGRESS_FIREWALL_CONFIRMED`.
- Changing `embedding.*` or `chunk` requires `vskr index`. `Service` refuses to load an index built with different embedding/chunk settings.
- `ocr.enabled` / `vlm.enabled` toggle the visual steps in ingest jobs; `ocr.python` / `vlm.python` name the step venvs.
- Upload, document and eval-import limits and the Ollama URL live only in models.yaml; code reads those keys directly, with no fallback numbers (`config.role_limit` falls back from `limits.<role>` to `ingest`). A partial config built in a test must set the keys the code under test reads.

## Architecture
Package: `backend/src/vskr/`, grouped by stage: `pipeline/` (ingest, frames, ASR, OCR, VLM, documents), `retrieval/` (chunking, embedding, BM25, knowledge graph, search), `access/` (accounts, auth, audit, visibility) and `api/` (routes in `public.py`/`admin.py`, business logic in `api/handlers/`). `cli`, `config`, `util`, `service`, `qa` and `evaluation` stay at the top level.

- **Pipeline modules:** `ingest` (yt-dlp via `safe_ytdlp`; `url_safety` checks URLs and raises `UnsafeUrlError`, which the API maps to 400) → `frames.detect_scenes_only` (PySceneDetect, installed as `scenedetect-headless`) → `asr` (faster-whisper, 15-min segments, original timestamps kept) → visual steps → `index` (chunker + E5 `embedder` + `bm25` + `knowledge_graph`). `documents` handles TXT/MD/RST/PDF/DOCX sections.
- **Visual steps** (`handlers.jobs._run_visual_steps`, after ASR, each under `_gpu_model_lock`; a failure only adds a job warning and indexing continues from the transcript):
  - `frames.run` reuses `scenes.json` and grabs frames in parallel (`frames.workers`).
  - `ocr` (EasyOCR `['vi','en']`) drops low-score words, joins words on the same row into lines (`group_lines`), and merges a line seen in consecutive frames into one time span. It writes `screen_text.json`, plus per-frame text coverage in `ocr_info.json`.
  - `vlm` (Qwen2.5-VL-3B, 4-bit) captions only figure-like frames: low OCR text coverage, dHash-deduplicated, evenly capped. It uses an English prompt that asks for a Vietnamese answer: a Vietnamese instruction made the 3B model opt out even on clear diagrams. The frame's OCR text goes into the prompt, and "NONE"/"no figure" answers are dropped. It writes `captions.json`.
  - `index` merges `screen_text.json` + `captions.json` into chunks by time overlap.
  - `pipeline.steps.run_step` runs a step as `<venv>/bin/python -m vskr.cli <step>` in a child process (`VSKR_STEP_INPROCESS=1`, core-venv CUDA paths stripped from `LD_LIBRARY_PATH`). Each step's VRAM is freed when its child exits.
- **Retrieval:** `retriever.Retriever.search(mode=...)` with modes `vector`, `bm25`, `hybrid`, `graph` and `hybrid_graph`, fused by RRF (`fusion`). The admin/CLI default is `hybrid` (`retrieval.mode`); the admin page preselects it in `web/admin/index.html`, and a test keeps the two in sync. The index is a brute-force NumPy scan; FAISS isn't enabled. `allowed_sources` masks results to what the viewer may see.
- **Service:** `service.Service` holds the loaded index, embedder, retriever and QA (`qa.answer_question` → Ollama). One shared instance lives in `api/handlers/runtime.py`.
- **API:** `api/__init__.py` builds the FastAPI `app` from `public.router` (learner/guest pages + `/api/*`), `admin.auth_pages` and `admin.router`, plus pure-ASGI middleware: `UploadBodyLimitMiddleware` (role-based body limits per upload path) and `SecurityHeadersMiddleware` (CSP etc.).
- **api/handlers/** holds the business logic: `runtime` (shared service, locks, job state, status), `jobs` (ingest queue, visual steps, index rebuild), `library` (catalog, deletion, file access, visibility), `ask`, `evaluation`, `graph`. Shared mutable state lives only in `runtime`, and imports run one way (runtime ← jobs ← library; runtime ← ask, evaluation, graph). Routes call through the module (`handlers.jobs.start_ingest(...)`), so a test patches the module that looks the name up, e.g. `mock.patch.object(handlers.jobs, "load_config")`. Jobs run in-memory (`_jobs` + a single-worker `ThreadPoolExecutor`, so jobs run serially to protect the 8 GB VRAM), under `_gpu_model_lock`, `_index_build_lock` and service ref-counting (`_acquire_service`/`_replace_service`). Job state and admin sessions are lost on restart. The server imports some modules lazily, so editing code under a running server can load half-edited modules; use a git worktree for changes while the owner tests.
- **Auth, three identities:**
  - Admin: `access/auth.py`, in-RAM sessions, CSRF, login throttling.
  - Learner: `access/accounts.py`, SQLite at `data/accounts.sqlite3`, which stores accounts, sessions and conversation history; self-registration is rate-limited per IP.
  - Guest: identified by a cookie, with lower quotas (`limits.guest` vs `limits.user` in models.yaml).
- **Visibility:** `visibility.is_visible` is the single rule for the catalog, file access and retrieval. New content is `private` (owner only); an owner can propose `pending`, and an admin approves it to `public`. Legacy items without an owner are public. Learners and guests are always restricted to hybrid vector+BM25; a client-supplied mode is ignored outside `/api/admin/ask`.
- **Access matrix:** `docs/ACCESS_MATRIX.md` lists the permission for every route. Classify any new `/api/*` route there, and enforce the permission on the server.
- **Frontend:** plain HTML/CSS/JS with no build step. `web/user` is the learner PWA (`sw.js` caches static assets only, never the API), `web/admin` is the admin bundle (served only after admin auth through routes, not a public static mount), and `web/shared` holds `api.js` and `tokens.css`.
- **Evaluation:** `evaluation.py` runs retrieval only (Recall@5, MRR), with questions from `data/eval/questions.jsonl`. That file is empty on purpose: the admin deleted the old set, per `audit.jsonl`.

## Data
`data/` holds videos, transcripts, frames/OCR/caption artifacts, the index, documents, eval data, `audit.jsonl` and the accounts DB. `cache/` holds HF (incl. the ~7.5 GB VLM model) and yt-dlp caches. Both are git-ignored and local (only `data/eval/*.schema.json` and `questions.jsonl` are tracked). Don't delete them. The C: drive hosting WSL has limited free space; check `df -h /mnt/c` before large downloads.

## Tests
Tests don't use Starlette's TestClient. Use `tests/asgi_client.ASGITestClient(app, authenticated_learner=...)`, which overrides `optional_learner_or_admin`. Tests reset module-level state (e.g. `auth._sessions`, `auth._failures` under their locks) and use temp data dirs. Visual-step tests mock `vskr.pipeline.frames.run` / `vskr.pipeline.steps.run_step`, so they need neither GPU nor the step venvs.

`tests/__init__.py` points `VSKR__PATHS__DATA_DIR` at a temp dir for the whole suite. This is needed because `accounts`/`audit` call `load_config()` themselves and once rewrote the real `data/` from a test. `tests/test_data_isolation.py` fails if that breaks. Never build data paths as `ROOT / "data"`; use `data_dir(cfg)`.

## Security notes
`SECURITY-REVIEW.md`: yt-dlp URL ingest can bypass SSRF checks (redirects, HLS→ffmpeg). Egress firewalling is required before exposing ingest to untrusted users. Keep SSRF/visibility/CSRF checks server-side when touching these paths.
