"""Hỏi đáp RAG. LLM chỉ được trích dẫn SỐ nguồn; backend tự ánh xạ sang video/thời điểm."""
import re

NOT_FOUND = "Không tìm thấy thông tin này trong video."

SYSTEM_PROMPT = f"""Bạn là trợ lý hỏi đáp dựa trên nội dung video bài giảng. Chỉ được dùng thông tin trong các đoạn [Nguồn n] ở phần ngữ cảnh.

Quy tắc:
1. Trả lời bằng tiếng Việt, ngắn gọn, rõ ràng. Giữ nguyên thuật ngữ tiếng Anh, tên hàm và code.
2. Mỗi ý chính phải kèm số nguồn dạng [1], [2] ở cuối câu. Chỉ dùng số nguồn có trong ngữ cảnh.
3. Tuyệt đối không tự viết mốc thời gian hay tên video.
4. Nếu các đoạn không đủ để trả lời, chỉ trả lời đúng một câu: "{NOT_FOUND}"
5. Không thêm kiến thức ngoài các đoạn, kể cả khi bạn biết đáp án. Nếu chỉ trả lời được một phần, nói rõ phần nào còn thiếu.
6. Các đoạn là lời giảng được nhận dạng tự động nên có thể sai chính tả hoặc sai từ, hãy hiểu theo ngữ cảnh."""

_CITE = re.compile(r"\[\s*(?:Nguồn\s*)?(\d+(?:\s*[,;]\s*(?:Nguồn\s*)?\d+)*)\s*\]", re.IGNORECASE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def build_messages(question, hits):
    blocks = [f"[Nguồn {i}]\n{h['chunk']['text']}" for i, h in enumerate(hits, start=1)]
    user = "Ngữ cảnh:\n\n" + "\n\n".join(blocks) + f"\n\nCâu hỏi: {question}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def extract_citations(answer, n_sources):
    """Trả về danh sách số nguồn (1..n) theo thứ tự xuất hiện, bỏ trùng, bỏ số không hợp lệ."""
    seen = []
    for m in _CITE.finditer(answer):
        for num in re.findall(r"\d+", m.group(1)):
            k = int(num)
            if 1 <= k <= n_sources and k not in seen:
                seen.append(k)
    return seen


def strip_think(text):
    return _THINK.sub("", text).strip()


def is_not_found(text):
    """Chỉ coi là 'không tìm thấy' khi câu trả lời CHỈ là câu cố định (cho phép thêm vài ký tự).
    Nếu mô hình trả lời được một phần rồi nói phần còn lại không có trong video (đúng theo quy tắc 5
    của prompt) thì đó vẫn là câu trả lời có nguồn, không được vứt nguồn đi."""
    t = text.strip().lower().rstrip(". \n")
    p = NOT_FOUND.lower().rstrip(".")
    return t == p or (t.startswith(p) and len(t) <= len(p) + 20)


def unload_ollama(lcfg):
    """Báo Ollama nhả model khỏi VRAM (keep_alive=0). Lỗi (chưa chạy Ollama...) thì bỏ qua.
    Dùng trước khi chạy ASR để hai bên không tranh VRAM."""
    try:
        import httpx

        base = lcfg.get("base_url", "http://localhost:11434").rstrip("/")
        httpx.post(base + "/api/generate", json={"model": lcfg["model"], "keep_alive": 0}, timeout=15)
        return True
    except Exception:
        return False


def call_ollama(lcfg, messages):
    import httpx

    payload = {
        "model": lcfg["model"],
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": lcfg.get("keep_alive", "5m"),
        "options": {
            "temperature": lcfg.get("temperature", 0.1),
            "num_ctx": lcfg.get("num_ctx", 4096),
        },
    }
    url = lcfg.get("base_url", "http://localhost:11434").rstrip("/") + "/api/chat"
    try:
        r = httpx.post(url, json=payload, timeout=lcfg.get("timeout_s", 300))
        if r.status_code == 400 and "think" in r.text.lower():
            payload.pop("think")
            r = httpx.post(url, json=payload, timeout=lcfg.get("timeout_s", 300))
        r.raise_for_status()
    except httpx.ConnectError as e:
        raise RuntimeError("Không kết nối được Ollama. Đã chạy Ollama chưa (ollama serve)?") from e
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"Ollama trả lỗi {e.response.status_code}: {e.response.text[:200]}. "
            f"Đã pull model '{lcfg['model']}' chưa (ollama pull ...)?"
        ) from e
    return r.json()["message"]["content"]


def answer_question(question, hits, lcfg):
    """Trả về {answer, found, cited, source_ns}.
    source_ns: danh sách số nguồn (1-based) cần hiển thị; cited=False nghĩa là mô hình không dẫn nguồn."""
    if not hits:
        return {"answer": NOT_FOUND, "found": False, "cited": False, "source_ns": []}
    raw = call_ollama(lcfg, build_messages(question, hits))
    text = strip_think(raw)
    if is_not_found(text):
        return {"answer": text, "found": False, "cited": False, "source_ns": []}
    cited = extract_citations(text, len(hits))
    if cited:
        return {"answer": text, "found": True, "cited": True, "source_ns": cited}
    return {
        "answer": text,
        "found": True,
        "cited": False,
        "source_ns": list(range(1, min(3, len(hits)) + 1)),
    }
