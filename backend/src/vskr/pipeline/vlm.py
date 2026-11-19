"""Describe diagrams/figures in lecture frames with a vision-language model.

Runs after OCR. Frames whose area is mostly text (slides, code) are already
covered by OCR, so only visual frames are captioned: frames with little OCR
text, deduplicated by dHash and capped evenly over the lecture. The frame's
OCR text is passed as context so the caption can name the parts of a diagram
without copying text verbatim.
"""
import re
import time

from ..config import video_dir
from ..util import read_json, write_json

EMPTY_ANSWERS = {"KHÔNG", "KHONG", "NONE", "NO"}


def _hash(frame):
    value = frame.get("hash")
    if isinstance(value, str):
        return int(value, 16)
    return value


def select_frames(frames, ocr_frames, vcfg):
    """Pick frames worth captioning; frames without OCR stats are treated as visual."""
    from .frames import cap_evenly, hamming

    max_area = float(vcfg.get("max_text_area", 0.15))
    min_dist = int(vcfg.get("min_hash_dist", 10))
    stats = {item["path"]: item for item in ocr_frames or []}
    picked, kept_hashes = [], []
    for frame in frames:
        frame_stats = stats.get(frame["path"])
        if frame_stats is not None and float(frame_stats.get("text_area", 0.0)) >= max_area:
            continue
        value = _hash(frame)
        if value is not None and any(hamming(value, kept) < min_dist for kept in kept_hashes):
            continue
        picked.append(frame)
        if value is not None:
            kept_hashes.append(value)
    return cap_evenly(picked, int(vcfg.get("max_frames", 120)))


def build_prompt(ocr_text=""):
    # English instructions with a Vietnamese answer: with a Vietnamese instruction the 3B
    # model answered the opt-out word even for clear diagrams/charts (smoke test 2026-11-19).
    prompt = (
        "Describe the diagram, chart, table, figure or software screen in this lecture video frame "
        "in Vietnamese, in at most 2 sentences: its main components and how they relate. "
        "Do not copy the text verbatim. If the frame shows no diagram, chart, table, figure or "
        "software screen (for example only a person talking or a blank screen), answer exactly: NONE"
    )
    ocr_text = (ocr_text or "").strip()
    if ocr_text:
        prompt += f"\nText read from the frame (for reference only): {ocr_text[:300]}"
    return prompt


# The model sometimes explains the absence instead of answering NONE,
# e.g. "Bản đồ này không có bất kỳ biểu đồ, bảng, hình ảnh hay giao diện phần mềm nào."
_NO_FIGURE = re.compile(r"không\s+(có|chứa)\s+(bất\s*kỳ|sơ đồ|biểu đồ|hình minh họa)", re.IGNORECASE)


def clean_caption(caption, ocr_text=""):
    """Drop paragraphs that only repeat on-screen text OCR already indexed.

    Despite the instruction, the model sometimes opens with the slide title
    verbatim ("Bài 3: Truy vấn dữ liệu với JOIN\\n\\nTrong hình ảnh này…").
    """
    seen = " ".join((ocr_text or "").split()).casefold()
    paragraphs = [p.strip() for p in caption.split("\n\n") if p.strip()]
    kept = [p for p in paragraphs if not (seen and " ".join(p.split()).casefold() in seen)]
    return "\n\n".join(kept).strip()


def is_empty_caption(text):
    cleaned = text.strip().strip(".!")
    return cleaned.upper() in EMPTY_ANSWERS or bool(_NO_FIGURE.search(cleaned))


def fit_pixels(image, max_pixels):
    """Downscale so width*height <= max_pixels; fewer visual tokens = faster, less VRAM."""
    width, height = image.size
    if not max_pixels or width * height <= max_pixels:
        return image
    scale = (max_pixels / (width * height)) ** 0.5
    return image.resize((max(28, int(width * scale)), max(28, int(height * scale))))


def _load(cfg, vcfg):
    import torch
    from transformers import AutoProcessor, BitsAndBytesConfig, Qwen2_5_VLForConditionalGeneration

    if vcfg.get("unload_ollama_first", True):
        from ..qa import unload_ollama

        unload_ollama(cfg.get("llm", {}))
    name = vcfg.get("model", "Qwen/Qwen2.5-VL-3B-Instruct")
    started = time.time()
    processor = AutoProcessor.from_pretrained(name)
    on_gpu = vcfg.get("device", "cuda") != "cpu" and torch.cuda.is_available()
    if on_gpu:
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        kwargs = {"device_map": "auto", "dtype": dtype}
        if vcfg.get("load_in_4bit", True):
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=dtype, bnb_4bit_use_double_quant=True,
            )
    else:
        kwargs = {"device_map": "cpu", "dtype": torch.float32}
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(name, **kwargs).eval()
    print(f"[vlm] đã nạp {name} trên {'GPU' if on_gpu else 'CPU'} ({time.time() - started:.0f}s)", flush=True)
    return torch, processor, model


def _caption(torch, processor, model, image, prompt, max_new_tokens):
    messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt}]}]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[text], images=[image], return_tensors="pt").to(model.device)
    with torch.inference_mode():
        generated = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    # generate() returns prompt + answer; decode only the new tokens.
    answer = generated[:, inputs["input_ids"].shape[1]:]
    return processor.batch_decode(answer, skip_special_tokens=True)[0].strip()


def run(cfg, video_ids=None, force=False):
    from PIL import Image

    vcfg = cfg.get("vlm", {})
    max_pixels = int(vcfg.get("max_pixels", 602112))
    max_new_tokens = int(vcfg.get("max_new_tokens", 96))
    torch = processor = model = None
    results = []
    for video_id in video_ids or []:
        vdir = video_dir(cfg, video_id)
        fp = vdir / "frames.json"
        if not fp.exists():
            print(f"[vlm] bỏ qua {video_id}: chưa có frames.json")
            continue
        out = vdir / "captions.json"
        if out.exists() and not force:
            print(f"[vlm] bỏ qua {video_id} (đã có captions.json)")
            continue
        frames = read_json(fp).get("frames", [])
        ocr_path = vdir / "ocr_info.json"
        ocr_frames = read_json(ocr_path).get("frames") if ocr_path.is_file() else None
        chosen = select_frames(frames, ocr_frames, vcfg)
        print(f"[vlm] {video_id}: chọn {len(chosen)}/{len(frames)} frame có hình để mô tả"
              f"{'' if ocr_frames is not None else ' (chưa có OCR, không lọc theo chữ)'}", flush=True)
        ocr_text = {item["path"]: item.get("text", "") for item in ocr_frames or []}
        rows, skipped, started = [], 0, time.time()
        if chosen and model is None:
            torch, processor, model = _load(cfg, vcfg)
        for n, frame in enumerate(chosen, start=1):
            with Image.open(vdir / frame["path"]) as raw:
                image = fit_pixels(raw.convert("RGB"), max_pixels)
            frame_text = ocr_text.get(frame["path"], "")
            caption = clean_caption(
                _caption(torch, processor, model, image, build_prompt(frame_text), max_new_tokens), frame_text)
            if caption and not is_empty_caption(caption):
                rows.append({"start": frame["t"], "end": frame["t"], "text": caption, "type": "caption", "frame": frame["path"]})
            else:
                skipped += 1
            if n % 10 == 0 or n == len(chosen):
                elapsed = time.time() - started
                print(f"[vlm] {video_id}: {n}/{len(chosen)} frame ({elapsed:.0f}s, {elapsed / n:.1f}s/frame)", flush=True)
        write_json(out, rows)
        info = {"video_id": video_id, "n_frames": len(frames), "n_selected": len(chosen),
                "n_captions": len(rows), "n_no_figure": skipped, "seconds": round(time.time() - started, 1)}
        write_json(vdir / "vlm_info.json", info)
        print(f"[vlm] {video_id}: {len(rows)} mô tả, {skipped} frame không có hình đáng kể ({info['seconds']}s)")
        results.append(info)
    if model is not None:
        del model, processor
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    return results
