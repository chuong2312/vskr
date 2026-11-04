"""Chunking cửa sổ trượt trên các đoạn ASR. Nguồn phụ (OCR, mô tả hình...) gắn theo thời gian."""


def make_chunks(video_id, segments, window_s=50.0, overlap_s=10.0, min_chars=20, extras=None):
    """segments: [{start,end,text}], extras: [{start,end,text,type}] (tùy chọn).

    Trả về [{chunk_id, video_id, start, end, text, source_types}].
    Chunk gồm các đoạn ASR liên tiếp có tổng độ dài <= window_s (ít nhất 1 đoạn).
    Chunk kế tiếp bắt đầu ở đoạn đầu tiên có start >= start_chunk + (window - overlap).
    """
    if overlap_s >= window_s:
        raise ValueError("overlap_s phải nhỏ hơn window_s")
    segs = sorted((s for s in segments if s["text"].strip()), key=lambda s: s["start"])
    stride = window_s - overlap_s
    out = []
    i, n = 0, len(segs)
    while i < n:
        t0 = segs[i]["start"]
        j = i + 1
        while j < n and segs[j]["end"] - t0 <= window_s:
            j += 1
        group = segs[i:j]
        start, end = group[0]["start"], group[-1]["end"]
        text = " ".join(s["text"].strip() for s in group)
        types = ["speech"]
        if extras:
            seen = []
            for e in extras:
                t = e["text"].strip()
                if t and e["end"] > start and e["start"] < end and t not in seen:
                    seen.append(t)
                    if e.get("type", "screen") not in types:
                        types.append(e.get("type", "screen"))
            if seen:
                text += "\n[Trên màn hình] " + " | ".join(seen)
        if len(text) >= min_chars:
            out.append(
                {
                    "chunk_id": f"{video_id}:{len(out):04d}",
                    "video_id": video_id,
                    "start": start,
                    "end": end,
                    "text": text,
                    "source_types": types,
                }
            )
        if j >= n:
            break
        k = i + 1
        while k < j and segs[k]["start"] < t0 + stride:
            k += 1
        i = k
    return out
