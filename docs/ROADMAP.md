# Lộ trình nâng cấp VSKR

Cập nhật: 2026-11-28. Đây là **đề xuất, chưa triển khai**. Mỗi bước ở mục 9 làm thành một PR riêng và chỉ bắt đầu khi chủ dự án duyệt. Tài liệu này ghép ba nguồn:
- Bản tổng hợp ý tưởng "Local AI Video & Data Optimizer" của chủ dự án (27/11/2026, không lưu trong repo).
- Hướng graph đã bàn trước đó: ghi chú sửa Graph RAG (21/11) và AtlasKV (23/11).
- Kết quả tìm trên mạng ngày 27–28/11/2026 (mục 10).

Con số lấy từ nguồn bên ngoài có ghi rõ nguồn. Chỗ nào chưa kiểm chứng thì ghi "chưa chắc".

## 1. Mục tiêu

- Máy local xử lý tối đa: nhìn, nghe, đọc, lọc trùng, đồng bộ, đóng gói.
- Gemini chỉ nhận phần dữ liệu cần thiết, có cấu trúc và có bằng chứng.
- Giảm token và số request nhưng **không làm mất thông tin mới**. Khi người dùng muốn phân tích toàn bộ, không được tự ý tóm tắt.
- Mọi kết quả truy ngược được về frame, slide, trang tài liệu hoặc timestamp gốc.
- Dữ liệu đã xử lý thì dùng lại, không xử lý hay gửi lại.
- Không dùng LLM lớn cho việc mà thuật toán hoặc model chuyên dụng làm được.

## 2. Ý tưởng chính: local và Gemini kết hợp

Không phải chọn "Ollama **hay** Gemini". Hai bên chạy chung trong một luồng, mỗi bên làm phần mình giỏi.

### 2.1. Đơn vị thông tin là trạng thái nội dung

Với video bài giảng, đơn vị thông tin không phải frame hay cảnh quay, mà là **trạng thái nội dung**. Mỗi lần giảng viên thêm một node, một mũi tên hay một dòng công thức là có một trạng thái mới.
- Giữ mọi trạng thái mới, vì bỏ sót là mất thông tin.
- Bỏ mọi trạng thái lặp lại, vì gửi lại là phí token.

### 2.2. Lúc xử lý video (mỗi video một lần)

**Local làm hết:** ASR, dò trạng thái slide, OCR, VLM, đồ thị trạng thái bài giảng, khái niệm, thẻ dữ kiện.

**Gemini, tuỳ chọn, 1 request mỗi video, chỉ với nội dung được phép:**
- Đọc gói đầy đủ và tạo **lớp hiểu sâu**: giải thích từng bước (ví dụ Dijkstra), quan hệ giữa khái niệm (prerequisite_of, example_of…), tóm tắt theo phần, sửa caption mơ hồ.
- Bên local kiểm từng ý (mục 8, dòng 4). Ý nào qua được mới lưu vào `insights.json`, gắn nhãn "Gemini suy luận".
- Mọi câu hỏi sau đều dùng lại lớp này, nên hợp với quota thấp. Nó cũng thay việc trích quan hệ bằng model 4B, vốn chỉ đúng 13/30 quan hệ.

### 2.3. Lúc hỏi đáp (mỗi câu hỏi), theo bậc thang

1. Local tìm kiếm (hybrid, graph cộng điểm qua PPR), rồi đóng gói (mục 4).
2. Local trả lời trước bằng Ollama.
3. Chỉ đưa lên Gemini khi:
   - người dùng bấm "Hỏi sâu";
   - local trả "không tìm thấy";
   - hoặc câu trả lời local không qua bước kiểm (trích dẫn sai, chi tiết không có trong nguồn).
4. Local kiểm câu trả lời của Gemini: số trích dẫn phải tồn tại, chi tiết kỹ thuật phải có trong nguồn.
5. Hết quota hoặc mất mạng → Ollama trả lời và ghi rõ "trả lời bằng model local".
6. Nội dung `private` không rời máy, trừ khi chủ sở hữu tự bật.

## 3. Đồ thị trạng thái bài giảng (graph v2)

### 3.1. Vì sao graph hiện tại yếu

Trên bộ 36 câu:
- `graph` đạt 0.74 / 0.53.
- `hybrid_graph` đạt 0.89 / 0.71, thấp hơn `hybrid` (0.92 / 0.79).

Nguyên nhân:
- `keywords()` ([knowledge_graph.py](../backend/src/vskr/retrieval/knowledge_graph.py)) tạo ra 618 khái niệm, cái nào cũng chỉ 1 âm tiết; đứng đầu là "em", "anh".
- Chỉ mục thật có 0 quan hệ có kiểu, vì Ollama tắt lúc dựng chỉ mục (2026-11-20).
- Có 9025 cạnh CO_OCCURS nhiễu.
- [fusion.py](../backend/src/vskr/retrieval/fusion.py) dùng RRF nên graph có một phiếu ngang hàng với vector và BM25.
- Khi thử trên 10 đoạn, model 4B trích được 30 quan hệ, chỉ 13 dùng được, và trong số đó vẫn có quan hệ sai.

GraphRAG-Bench (mục 10.3) cho thấy graph chỉ thắng ở câu suy luận nhiều bước, còn câu tra cứu một dữ kiện thì hoà. Bộ 36 câu chủ yếu là câu tra cứu.

### 3.2. Cấu trúc mới

Graph không còn là "chế độ tìm thứ 5" đi thi với hybrid. Nó là **khung xương của gói bằng chứng**:

```
Bài giảng ─PART_OF─ Phần/chủ đề ─PART_OF─ Trạng thái slide
                                            ├─ NEXT_STATE → trạng thái sau, kèm phần chênh (+ node D, + cạnh C→D)
                                            ├─ EXPLAINS ← đoạn lời giảng (khớp theo giờ)
                                            ├─ HAS_EVIDENCE → dòng OCR, caption VLM, ảnh frame, timestamp
                                            └─ MENTIONS → Khái niệm (nhiều âm tiết, có tên gọi khác)
Khái niệm ─ cùng khái niệm ở video khác ─ nối các bài giảng
Thẻ dữ kiện: khoá → giá trị, kèm nguyên văn và timestamp
```

- **Cạnh PART_OF, NEXT_STATE, EXPLAINS, HAS_EVIDENCE do thuật toán tạo**, theo thời gian và kết quả dò trạng thái. Chúng chắc chắn đúng và không cần LLM.
- **Chỉ phần khái niệm cần LLM**, theo kiểu LinearRAG: chỉ trích thực thể, không trích quan hệ. Kết quả lưu cache theo từng đoạn, và khái niệm phải có nguyên văn trong nguồn.
- **Quan hệ có kiểu giữa các khái niệm là tuỳ chọn.** Chỉ giữ quan hệ có câu bằng chứng nằm nguyên văn trong nguồn, hoặc quan hệ lấy từ lớp hiểu sâu đã qua kiểm.

### 3.3. Graph dùng vào việc gì

1. **Tìm kiếm:** `hybrid` vẫn là mặc định. Graph chỉ cộng điểm qua PPR với trọng số `retrieval.graph_weight` (mặc định 0). Graph chỉ bật cho câu nhiều bước hoặc liên video.
2. **Đóng gói:** tỉa theo tầng, một ý mượn từ HiKVP của AtlasKV nhưng làm lúc đóng gói chứ không phải trong attention. Câu hỏi → khái niệm → trạng thái → kèm tiêu đề phần và trạng thái trước đó → cắt cho vừa ngân sách token.
3. **Câu nhiều bước và câu liên video.**
4. **Truy ngược và kiểm trích dẫn:** nút nào cũng có nguồn gốc.

### 3.4. Mượn gì từ AtlasKV

AtlasKV (mục 10.3) có hai ý: chèn tri thức từ đồ thị vào attention của LLM (KG2KV), và tỉa theo tầng (HiKVP).
- **Không dựng lại nguyên bản.** Lý do:
  - Nó dùng LLaMA 3.1 8B và phải huấn luyện.
  - Nó sửa lớp attention của model, nên không chạy qua Ollama được, và càng không làm được với Gemini (model đóng).
  - Bài báo chỉ đánh giá câu một dữ kiện.
- **Chỉ mượn 2 ý, không cần huấn luyện:**
  - Thẻ dữ kiện "khoá → giá trị", lấy từ KG2KV.
  - Tỉa theo tầng, lấy từ HiKVP.

## 4. Tầng đóng gói (Data Optimizer)

| Lớp dữ liệu | Trong VSKR |
|---|---|
| Raw Archive | Video gốc trong `data/videos/<id>/` |
| Full Extraction | transcript, `scenes.json`, `screen_text.json`, `ocr_info.json`, `captions.json`; thêm `slide_states.json`, `fact_cards.json`, `insights.json` |
| Index & Metadata | `data/index/`: các đoạn có timestamp, nguồn, quyền xem; đồ thị trạng thái |
| API Payload | Gói gửi model. Lưu cache trong `data/packages/`, ghi số token vào `data/usage.jsonl` |

Cách đóng gói:
- **Trạng thái gốc + phần chênh:** trạng thái đầu gửi đầy đủ, các trạng thái sau chỉ gửi phần chênh. Cứ vài trạng thái lại xen một bản đầy đủ. Nếu cần ảnh thì chỉ cắt vùng vừa thay đổi.
- **Gửi chữ hay ảnh:** chữ hoặc code mà OCR tự tin cao thì gửi chữ. Sơ đồ, hoặc OCR kém tin cậy, thì gửi ảnh cắt nhỏ để Gemini tự đọc.
- **Thẻ dữ kiện** cho câu tra cứu, ví dụ tên tham số hay giá trị cấu hình.
- **Tỉa theo tầng** để vừa ngân sách token.
- **Che thông tin cá nhân** trước khi gửi ra ngoài: dãy từ 9 chữ số trở lên (đã có trong [screen_filter.py](../backend/src/vskr/retrieval/screen_filter.py)), email, số điện thoại.
- **Lọc trùng nhưng giữ số lần lặp:** giảng viên nhắc lại một ý nhiều lần thường là ý quan trọng.
- **Hai chế độ:**
  - *Relevant Extraction*: chỉ phần liên quan đến câu hỏi. Đây là cách RAG hiện tại đang làm.
  - *Full Fidelity*: đi theo NEXT_STATE từ đầu đến cuối, chia nhiều request nếu cần.
- Xây tiếp trên `llm.context_packing` của PR #24, không viết song song.

## 5. Cấu trúc dự án sau nâng cấp

Giữ cách chia theo khâu, chỉ thêm module (`[mới]`, `[sửa]`, còn lại giữ nguyên):

```
backend/src/vskr/
├── pipeline/
│   ├── frames.py           [sửa]  lấy mẫu dày bằng ảnh nhỏ cho bước dò trạng thái
│   ├── slide_change.py     [mới]  → slide_states.json, kèm lý do giữ/bỏ từng frame
│   ├── enrich.py           [mới]  lớp hiểu sâu do Gemini tạo, cache theo hash của gói
│   ├── ocr.py, vlm.py      [sửa nhẹ] chỉ chạy trên trạng thái mới; đổi engine/model qua config
│   └── asr, ingest, documents…
├── retrieval/
│   ├── chunker.py          [sửa]  ranh giới đoạn = trạng thái slide
│   ├── knowledge_graph.py  [viết lại] đồ thị trạng thái + khái niệm + PPR (giữ bản cũ để so)
│   ├── fusion.py           [sửa]  thêm graph_weight bên cạnh RRF
│   └── reranker.py         [mới]
├── packaging/              [mới]  package.py, tokens.py, redact.py
├── llm/                    [mới]  router.py (bậc thang), gemini.py, quota.py
├── qa.py                   [sửa]  nhận gói từ packaging
├── evaluation.py           [sửa]  thêm F1 dò đổi slide, token và request trước/sau
└── api/handlers/ask.py, graph.py   [sửa nhẹ]
scripts/trace/                       [mới]  vì sao frame/trạng thái được giữ hay bỏ
scripts/eval/compare_inputs.py       [mới]  cùng câu hỏi, nhiều cách đưa dữ liệu
tests/pipeline|retrieval|packaging|llm/…   [mới]
```

- Chuyển `call_ollama` từ `qa.py` sang `llm/ollama.py` là đổi cấu trúc, sẽ hỏi riêng khi tới bước đó.
- **Cấu hình mới** trong `configs/models.yaml`: `slide_change`, `packaging`, `graph`, `llm.local`, `llm.remote`, `routing`, `gemini.daily_budget`, `gemini.allowed_visibility`.
- **`.env`** thêm `GEMINI_API_KEY`.
- **`docs/ACCESS_MATRIX.md`** ghi rõ ai được kích hoạt Gemini.
- **`docs/ARCHITECTURE.md`** cập nhật khi thêm thư mục.
- **Không đổi:** `access/`, phần lớn route trong `api/`, giao diện web, quy tắc quyền xem.
- **Thư viện:** các bước dò trạng thái, graph và đóng gói không cần thư viện mới (đã có OpenCV, numpy). Bộ tách từ tiếng Việt (underthesea hoặc pyvi), nếu cần, phải hỏi duyệt. Gemini có thể gọi qua HTTP để khỏi thêm SDK.

## 6. Chọn công nghệ

| Thành phần | Bản ý tưởng chọn | Đề xuất |
|---|---|---|
| Đổi slide | OCR diff + SSIM | Bản 1 (2026-11-28) dùng **tỉ lệ vùng đổi** thay SSIM, vì SSIM cả khung gần như không đổi khi chỉ thêm 1 node; kiểm bằng chữ OCR để sang bước 3. Bài MDPI 2026 vẫn là mốc tham khảo |
| Cắt cảnh | PERSIST | Tuỳ chọn. Có thật (BMVC 2026) nhưng học trên video thông thường; ý "thay đổi phải bền" lấy được bằng debounce |
| Mốc cắt cảnh | PySceneDetect | Giữ |
| Đối chứng | OmniShotCut | Bỏ: nhắm YouTube/TikTok, không phải bài giảng |
| Chọn frame | FOCUS/QCA | Thay bằng ý của MarKey (thông tin mới so với tập đã chọn). Chọn frame theo câu hỏi thì chế độ agentic của Gemini đã làm sẵn |
| OCR | PP-OCRv6 | Thử **SenOCR-Vi**, giữ EasyOCR làm mốc. PP-OCRv6 thiếu chữ hai dấu ([DECISIONS.md](DECISIONS.md)) |
| Bố cục, bảng | PP-StructureV3 | Bỏ: họ Paddle xung đột với torch; SenOCR-Vi đã đọc được bảng và công thức |
| Hiểu frame | Florence-2-large | Thử **Qwen3.5-2B** thay Qwen2.5-VL-3B |
| Âm thanh | faster-whisper | Giữ. Có thể thử Qwen3-ASR-1.7B trên một mẫu |
| Ghép slide | MaViLS | Giữ ý. Chỉ cần khi có file slide PDF tải lên riêng; khi đó lấy chữ từ PDF thay OCR |
| Tìm kiếm | BGE-M3 + FAISS | Đo e5-large hoặc bge-m3 kết hợp BM25. FAISS chưa cần với 196 đoạn |
| Đóng gói | Python | Giữ (mục 4) |
| API | CountTokens + Files API | Thêm chế độ agentic làm mốc; chọn model theo quota; bỏ Batch vì không có bản miễn phí |

**Không làm lúc này:**
- TransVLM, Florence-2, WhisperX, FAISS.
- AtlasKV nguyên bản.
- Dời thư mục theo kiểu `xla__tracking`.
- Xử lý trên điện thoại. Điện thoại chỉ chụp tài liệu rồi tải lên qua PWA; nếu sau này cần chạy trên máy thì Gemma 4 E2B/E4B là ứng viên thực tế nhất.

## 7. Đánh giá

- **Phép thử giữ thông tin:** cùng bộ câu hỏi, so 4 cách:
  - chỉ local;
  - Gemini đọc video thô;
  - Gemini ở chế độ agentic;
  - kết hợp.

  Đo độ đúng, số token, số request, tỉ lệ câu trả lời được ở local. Đổi ngưỡng sẽ cho một đường cong token–độ đúng.
- **Dò đổi slide:** F1 trên video đã đánh dấu thời điểm đổi slide.
- **Graph:** chia tập chỉnh và tập đo, so `hybrid` với `hybrid` + PPR. Chỉ giữ graph nếu nó thắng hơn mức chênh 1 câu trên tập đo, nhất là ở câu nhiều bước.
- **Bẫy cần tránh:** câu hỏi đánh giá khớp nguồn theo mốc thời gian chồng lấn ([evaluation.py](../backend/src/vskr/evaluation.py) `matches_reference`), nên đổi cách chia đoạn không làm hỏng đáp án. Nhưng đoạn dài hơn thì dễ chồng lên đáp án hơn, nên Recall tăng ảo. Phải báo kèm độ dài đoạn trung bình, và so ở cùng ngân sách ký tự.

## 8. Lợi, hại và cách khắc phục

**Lợi so với chỉ dùng local:**
- Câu khó được trả lời sâu hơn.
- Quan hệ và lời giải thích trong graph tốt hơn.

**Lợi so với chỉ dùng Gemini:**
- Vừa quota thấp, vì phần lớn câu hỏi trả lời ở local.
- Chạy được khi offline.
- Nội dung `private` không rời máy.
- Có mốc local để đo lặp lại được.

**Nguyên tắc chung:**
- Mọi thứ mới đều tắt mặc định (`enabled: false`, `graph_weight: 0`). Thứ cũ chạy song song cho tới khi thứ mới thắng trên tập đo.
- Dừng sau bước nào cũng còn một hệ thống hoàn chỉnh. Nếu không kịp làm phần Gemini, đồ án vẫn đứng được ở phần local.

| # | Điểm hại | Cách khắc phục | Còn lại |
|---|---|---|---|
| 1 | Thêm một nhánh xử lý, thêm code và test | Ollama và Gemini dùng chung giao diện `generate(messages)`. Bộ chọn đường là hàm thuần. Test Gemini bằng địa chỉ giả `127.0.0.1:9` và JSON mẫu, giống PR #22 | Vẫn phải viết thêm test, nhưng không test nào gọi Gemini thật |
| 2 | Bộ chọn đường chọn sai | Dùng bậc thang (mục 2.3), luôn thử local trước. Ghi mọi quyết định vào `usage.jsonl`. Ngân sách quota theo ngày, chừa sẵn phần cho lớp hiểu sâu | Câu khó chậm thêm khoảng 2 giây vì phải đợi local chạy trước |
| 3 | Chất lượng và giọng văn khác nhau giữa hai đường | Cùng prompt, cùng định dạng `[n]`, cùng hậu xử lý (`extract_citations`, `strip_think`). Ghi rõ "Trả lời bởi". Lớp hiểu sâu làm nguồn cho Ollama | Model 4B vẫn yếu ở câu cần suy luận |
| 4 | Ý sai trong lớp hiểu sâu bị dùng lại | Kiểm 3 lớp (mã trạng thái có thật, có nguyên văn trong nguồn, thuật ngữ có trong nguồn). Ý qua được ở trạng thái `pending` cho tới khi admin duyệt. Không bao giờ được trích một mình. Lưu kèm model, ngày, hash | Ý sai tinh vi chỉ người duyệt mới bắt được |
| 5 | Khoá API, kết nối ra ngoài, quyền riêng tư | Khoá trong `.env`, không ghi vào log. Chỉ gửi nội dung mà `visibility.is_visible` coi là public; khách không kích hoạt được Gemini. Chạy `redact.py` trước khi gửi; gửi chữ và ảnh cắt, không gửi video gốc. Host Gemini cố định trong config | Điều khoản bản miễn phí vẫn cho Google dùng dữ liệu (mục 10.1) |
| 6 | Kết quả Gemini khó lặp lại | Ghim tên model có phiên bản. Cache phản hồi theo hash (gói + prompt + model). Kết luận chính dựa trên đường local | — |
| 7 | Ngưỡng dò slide phụ thuộc từng video | Tự loại vùng đổi liên tục (webcam, khung chat) và dải trên/dưới màn hình. SSIM chỉ đề cử, chữ OCR thay đổi mới xác nhận. Ngưỡng tính theo phân bố của từng video. Lấy mẫu 8 giây vẫn chạy song song. Có trang local để bấm đúng/sai | Slide có animation chạy liên tục vẫn có thể bị đếm thừa |
| 8 | Graph v2 có thể vẫn không thắng hybrid | Giữ `graph.version: 1\|2`. PPR có trọng số mặc định 0, chỉ bật cho câu nhiều bước. Kiểm tra Ollama trước khi lập chỉ mục, báo rõ nếu Ollama tắt. Cache theo đoạn. API graph giữ định dạng `nodes/edges` | Cần câu nhiều bước mới chứng minh được |
| 9 | Đóng gói trùng PR #24; phần chênh làm model nhỏ hiểu sai | Xây trên `llm.context_packing`. Phần chênh chỉ dùng cho Gemini và Full Fidelity; Ollama nhận bản đầy đủ. Bật hay tắt theo từng model | — |
| 10 | Tải nhiều GB; phải lập chỉ mục lại; SenOCR-Vi chậm, có thể bịa chữ | Chạy `df -h /mnt/c` trước mỗi lần tải (ngày 28/11 ổ C còn 28 GB). Thử trên chỉ mục riêng bằng `scripts/eval/eval_variants.py`. OCR bậc thang: SenOCR-Vi chỉ chạy ở chỗ EasyOCR kém tự tin, đánh dấu dòng nghi bịa. Đọc code `trust_remote_code`, ghim model theo commit. VRAM đã có `_gpu_model_lock` và mỗi bước chạy trong tiến trình con | Ổ C vẫn là giới hạn cứng |
| 11 | Đo bằng Gemini tốn quota | Cache phản hồi. Vòng dò dùng Flash-Lite, vòng chốt dùng Flash | Vòng chốt phải rải ra vài ngày |
| 12 | Tốn thời gian chấm của chủ dự án | Chấm tự động trước, chỉ chấm câu khác nhau giữa các cách. Dùng trang chấm local có nút bấm, tự lưu JSON. Chấm theo lô 10 câu | Đây vẫn là nút thắt lớn nhất |
| 13 | Recall tăng ảo; nhãn chấm cũ chỉ đúng với câu trả lời cũ | Báo kèm độ dài đoạn và so ở cùng ngân sách ký tự (mục 7). File chấm cũ giữ nguyên, chỉ chấm lại câu thay đổi | — |

## 9. Các bước (mỗi bước là 1 PR, duyệt riêng)

0. **Việc của chủ dự án:**
   - Chạy so sánh câu trả lời cho PR #24 và chấm.
   - Lưu `answers-review.md`.
   - Duyệt 18 câu nháp; trong đó có 5 câu nhiều bước và 2 câu liên video, cần cho bước 7.
1. **Tài liệu này.**
2. **Dò trạng thái slide:** bản 1 đã có, chưa đo trên video thật. Gồm [slide_change.py](../backend/src/vskr/pipeline/slide_change.py) và lệnh `vskr slides`, chạy tay, tắt khỏi ingest và lập chỉ mục; cách dò dùng tỉ lệ vùng đổi, chưa kiểm bằng OCR. Có trang gán nhãn `scripts/eval/label_slide_changes.py` và phép đo `scripts/eval/eval_slide_states.py`. Việc còn lại: chủ dự án gán nhãn 10 phút đầu "[Hệ điều hành] Bài 01", đo F1, chỉnh ngưỡng.
3. **Đồ thị trạng thái v2, chỉ gồm các cạnh do thuật toán tạo.** Ranh giới trạng thái đưa vào `make_scene_chunks` ([chunker.py](../backend/src/vskr/retrieval/chunker.py)). Dùng lại `_add_edge`.
4. **Khái niệm kiểu LinearRAG + PPR + `graph_weight`.**
5. **Tầng đóng gói**, sau khi PR #24 đã chốt.
6. **Bộ chọn đường và Gemini:** lúc hỏi đáp, và lớp hiểu sâu lúc xử lý video.
7. **Đánh giá 4 cách** (mục 7).

Ba việc cần duyệt tải riêng: SenOCR-Vi (khoảng 4 GB), e5-large hoặc bge-m3 (khoảng 2,2 GB), reranker bge-reranker-v2-m3 (khoảng 2,3 GB).

## 10. Kết quả tìm trên mạng (27–28/11/2026)

### 10.1. Gemini API
- **Token video (Gemini 3):** 70 token/frame (với video, mức low và medium như nhau), âm thanh 25 token/giây, mặc định 1 frame/giây. Một giờ video vào khoảng 342 nghìn token. fps chỉnh được trong khoảng (0, 24], có cắt đoạn bằng `start_offset`/`end_offset`. Trên diễn đàn có báo lỗi: khi cắt video YouTube, phần âm thanh không bị cắt theo.
- **Chế độ agentic video understanding** (09/2026, trên Gemini 3.8/3.7/3.6 Flash và 3.5 Flash-Lite): model tự chọn đoạn cần xem. Google công bố giảm tới 88% token và đúng hơn tới 7%; đây là số của chính Google.
- **Quota miễn phí** (chưa chắc; số từ diễn đàn và blog, Google chưa công bố chính thức): Gemini 3.8 Flash khoảng 20 request/ngày, 3.5 Flash-Lite khoảng 500 request/ngày.
- **Giá:**
  - Trang giá ghi context caching của 3.8 Flash bản thường là miễn phí.
  - Batch không có bản miễn phí.
  - Diễn đàn báo Gemini 4 bỏ explicit caching (chưa chắc).
- **Điều khoản bản miễn phí** (bản lưu năm 2025):
  - Google có thể dùng dữ liệu để cải thiện sản phẩm, và người duyệt có thể đọc.
  - Google khuyên không gửi thông tin nhạy cảm hoặc thông tin cá nhân.
  - Bật Cloud Billing thì dữ liệu được xử lý như bản trả phí (chưa chắc còn đúng).

### 10.2. Model
- **SenOCR-Vi:**
  - Fine-tune tiếng Việt của PaddleOCR-VL-1.6, 958.588.736 tham số, Apache-2.0.
  - Chạy bằng `transformers>=5.0.0`, không cần PaddlePaddle; `.venv-vlm` đang có 5.22.1.
  - Điểm tổng hợp tiếng Việt 82,83, so với 80,90 của PaddleOCR-VL-1.6. Đây là số tự báo cáo trên 160 trang tài liệu.
  - Chưa được thử trên slide hay code. VRAM không được nêu. Cần `trust_remote_code`.
- **Qwen3.5-2B:** 02/2026, Apache-2.0, đọc ảnh và video, ngữ cảnh 262.144 token.
- **Gemma 4:** gồm E2B, E4B, 26B MoE, 31B. Bản E2B và E4B nhận cả âm thanh và chạy được trên Android.
- **Qwen3-ASR:** số đo tiếng Việt còn trái chiều so với Whisper large-v3. Trên FLEURS, Qwen3-ASR-0.6B có WER 9,32%, còn Whisper large-v3 là 8,74%. Chưa có kết luận.
- **Tìm kiếm tiếng Việt** (EACL 2026): multilingual-e5-large và bge-m3 tốt nhất; dense kết hợp BM25 luôn đứng đầu.
- **Cắt cảnh và chọn frame:**
  - PERSIST: arXiv 2608.29287, BMVC 2026, Apache-2.0.
  - OmniShotCut: arXiv 2604.24762, NeurIPS 2026.
  - FOCUS (ICLR 2026): chỉ xem 1,6% số frame trên LongVideoBench.
  - MarKey: arXiv 2609.15408.

### 10.3. Graph và video RAG
- **GraphRAG-Bench (ICLR 2026)**, số lấy qua bài viết của Persistent: câu suy luận phức tạp, graph 53,4 so với đoạn văn 42,9; câu tra cứu một dữ kiện 60,1 so với 60,9.
- **LinearRAG (ICLR 2026):** đồ thị thực thể–câu–đoạn, không trích quan hệ; tìm bằng kích hoạt thực thể rồi chạy PPR; giảm hơn 77% thời gian lập chỉ mục.
- **AtlasKV (ICLR 2026, bản sửa 04/2026):** KG2KV và HiKVP, model nền LLaMA 3.1 8B, cần huấn luyện. Không tìm thấy bài tiếp nối.
- **arXiv 2608.03161 (08/2026):**
  - Dựng đồ thị tri thức đa phương thức cho bài giảng, dùng gần đúng bộ công cụ của VSKR: Faster-Whisper large-v3, 1 frame/giây, EasyOCR, Qwen2.5-VL-7B.
  - Có 8 loại quan hệ. Mỗi lần xuất hiện lưu timestamp, frame, loại nguồn, trích nguyên văn và độ tin cậy.
  - Họ chỉ thử 3 câu hỏi, tiếng Anh, không so với RAG trên transcript.
  - **Chỗ trống VSKR lấp được:** đo nghiêm túc trên tiếng Việt và so với hybrid.
- **Công trình liên quan:** C2F-RAG (tìm từ thô đến mịn, arXiv 2606.07924), Graph-to-Frame RAG, VideoRAG, Vgent, SceneRAG.

### 10.4. Chưa kiểm chứng được
- TransVLM, Moondream 3.1, phương pháp chọn frame QCA.
- Số tham số của PERSIST v1.1 và OmniShotCut v1.5.
- Con số F1 0,879 của bài Slide Annotation System, lấy từ bản ý tưởng.
- Quota chính thức của Gemini.

## 11. Nguồn

- Gemini:
  - [Media resolution](https://ai.google.dev/gemini-api/docs/media-resolution)
  - [Video understanding](https://ai.google.dev/gemini-api/docs/generate-content/video-understanding)
  - [Agentic video](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-agentic-video-in-gemini/)
  - [Pricing](https://ai.google.dev/gemini-api/docs/pricing)
  - [Terms (bản lưu)](https://ai.google.dev/gemini-api/terms-archive/terms_10_07_25)
  - Diễn đàn: [20 RPD](https://discuss.ai.google.dev/t/gemini-3-8-flash-free-tier-20-rpd-is-too-limited-for-practical-evaluation/180609), [Flash-Lite 500](https://discuss.ai.google.dev/t/free-tier-daily-quota-not-reset-at-midnight-pacific-on-2-nov-2026-429-limit-500-after-one-request-gemini-3-5-flash-lite/197849), [Gemini 4 caching](https://discuss.ai.google.dev/t/title-gemini-4-removes-explicit-context-caching-my-per-call-cost-goes-up-4x-with-no-way-to-control-it/200723), [lỗi cắt video YouTube](https://discuss.ai.google.dev/t/videometadata-clipping-on-youtube-urls-no-longer-clips-audio-frames-still-clipped-20x-token-inflation-on-long-videos/178970)
- Cắt cảnh, chọn frame, slide:
  - [PERSIST](https://arxiv.org/abs/2608.29287)
  - [OmniShotCut](https://arxiv.org/abs/2604.24762)
  - [FOCUS](https://proceedings.iclr.cc/paper_files/paper/2026/hash/a010577f93a8301dada61f0b97cda923-Abstract-Conference.html)
  - [MarKey](https://arxiv.org/pdf/2609.15408)
  - [Slide Annotation System](https://doi.org/10.3390/a19020110)
  - [MaViLS](https://arxiv.org/pdf/2409.16765)
- OCR, VLM, ASR:
  - [SenOCR-Vi](https://huggingface.co/VietAlphaLabs/SenOCR-Vi/blob/main/README.md)
  - [PaddleOCR-VL-1.6](https://arxiv.org/pdf/2606.03264)
  - [MORE](https://arxiv.org/html/2607.02956)
  - [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B)
  - [Gemma 4](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/)
  - [Qwen3-ASR](https://arxiv.org/html/2601.21337)
  - [Bảng ASR tiếng Việt](https://models.handy.computer/languages/vi)
- Tìm kiếm tiếng Việt:
  - [EACL 2026](https://aclanthology.org/2026.findings-eacl.110.pdf)
  - [SEA-BED](https://arxiv.org/html/2508.12243v1)
- Graph:
  - [GraphRAG-Bench](https://arxiv.org/html/2506.05690v3)
  - [Bài viết của Persistent](https://www.persistent.com/blogs/when-does-graphrag-outperform-traditional-rag/)
  - [LinearRAG](https://arxiv.org/html/2510.10114v4)
  - [AtlasKV](https://arxiv.org/abs/2510.17934)
  - [Đồ thị tri thức đa phương thức cho bài giảng](https://arxiv.org/html/2608.03161)
  - [C2F-RAG](https://arxiv.org/html/2606.07924)
  - [Graph-to-Frame RAG](https://arxiv.org/html/2604.04372)
  - [VideoRAG](https://arxiv.org/pdf/2502.01549)
  - [Vgent](https://arxiv.org/pdf/2510.14032)
  - [SceneRAG](https://arxiv.org/pdf/2506.07600)
