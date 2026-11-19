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


def make_scene_chunks(video_id, segments, scene_starts, window_s=50.0, overlap_s=10.0,
                      min_chars=20, extras=None):
    """Create timestamped chunks aligned to detected visual scene boundaries.

    Long scenes are subdivided with the normal sliding window. Transcript segments
    without a nearby detected scene boundary remain in a single fallback scene.
    """
    starts = sorted({max(0.0, float(t)) for t in scene_starts if float(t) >= 0})
    if not starts or starts[0] > 0:
        starts.insert(0, 0.0)
    clean = sorted((s for s in segments if s.get("text", "").strip()), key=lambda s: s["start"])
    if not clean:
        return []
    boundaries = starts + [float("inf")]
    out = []
    for scene_number, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        group = [s for s in clean if start <= float(s["start"]) < end]
        if not group:
            continue
        scene_chunks = make_chunks(
            video_id, group, window_s, overlap_s, min_chars, extras
        )
        for chunk in scene_chunks:
            chunk["chunk_id"] = f"{video_id}:scene:{len(out):04d}"
            chunk["scene_id"] = f"{video_id}:scene:{scene_number:04d}"
            chunk["scene_start"] = start
            chunk["scene_end"] = end if end != float("inf") else chunk["end"]
            chunk["source_types"] = list(dict.fromkeys(chunk.get("source_types", []) + ["scene"]))
            out.append(chunk)
    return out
