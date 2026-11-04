"""Optional visual captioning for frames not sufficiently described by OCR."""
import time

from .config import video_dir
from .util import read_json, write_json


def run(cfg, video_ids=None, force=False):
    import torch
    from PIL import Image
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    vcfg = cfg.get("vlm", {})
    if vcfg.get("unload_ollama_first", True):
        from .qa import unload_ollama

        unload_ollama(cfg.get("llm", {}))
    model_name = vcfg.get("model", "Qwen/Qwen2.5-VL-3B-Instruct")
    processor = AutoProcessor.from_pretrained(model_name)
    kwargs = {"device_map": "auto"}
    if vcfg.get("load_in_4bit", True):
        kwargs["load_in_4bit"] = True
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(model_name, **kwargs)
    prompt = "Mô tả ngắn gọn sơ đồ, biểu đồ hoặc hình minh họa trong ảnh; bỏ qua chữ nếu OCR đã đọc được."
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
        rows, started = [], time.time()
        frames = read_json(fp).get("frames", [])[: int(vcfg.get("max_frames", 200))]
        for frame in frames:
            image = Image.open(vdir / frame["path"]).convert("RGB")
            inputs = processor(text=prompt, images=image, return_tensors="pt").to(model.device)
            with torch.inference_mode():
                generated = model.generate(**inputs, max_new_tokens=int(vcfg.get("max_new_tokens", 96)))
            text = processor.batch_decode(generated, skip_special_tokens=True)[0].strip()
            if text:
                rows.append({"start": frame["t"], "end": frame["t"], "text": text, "type": "caption", "frame": frame["path"]})
        write_json(out, rows)
        info = {"video_id": video_id, "n_frames": len(frames), "n_captions": len(rows), "seconds": round(time.time() - started, 1)}
        write_json(vdir / "vlm_info.json", info)
        print(f"[vlm] {video_id}: {len(rows)} caption từ {len(frames)} frame ({info['seconds']}s)")
        results.append(info)
    del model, processor
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return results
