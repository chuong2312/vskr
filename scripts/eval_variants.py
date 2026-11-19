"""So sánh retrieval khi index chỉ có transcript, có thêm OCR, và có thêm OCR + VLM.

Với mỗi biến thể, script chép các file JSON cần cho việc lập chỉ mục từ data/ sang một thư mục
tạm, dựng index ở đó, rồi chạy 5 chế độ tìm kiếm trên bộ câu hỏi. Không đọc hay ghi data/index
thật và không sửa file nào trong data/videos. Kết quả in ra là Recall@5 / MRR, chia theo nơi chứa
đáp án (evidence). Bản đầy đủ được ghi vào data/eval/variants.json.

    python scripts/eval_variants.py
    python scripts/eval_variants.py --questions data/eval/drafts/spoken.jsonl --variants transcript

Mặc định không gọi Ollama để trích quan hệ cho đồ thị (thêm --relations để bật), vì đầu ra của
LLM thay đổi giữa các lần dựng và sẽ làm nhiễu phép so sánh giữa các biến thể.
"""
import argparse
import copy
import json
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from vskr import index as index_mod
from vskr.config import data_dir, load_config
from vskr.evaluation import EVIDENCE_TYPES, score_results, validate_question
from vskr.retriever import Retriever

MODES = ("vector", "bm25", "hybrid", "graph", "hybrid_graph")
# Biến thể: (file phụ được chép, ghi đè cấu hình chunk). ocr_info.json đi kèm OCR để bộ lọc chữ rác
# biết danh sách frame; ocr_raw tắt bộ lọc để so trước/sau khi lọc.
OCR_FILES = ("screen_text.json", "ocr_info.json")
VARIANTS = {
    "transcript": ((), {}),
    "ocr_raw": (OCR_FILES, {"screen_text_filter": {"enabled": False}}),
    "ocr": (OCR_FILES, {}),
    "vlm": (("captions.json",), {}),
    "ocr_vlm": (OCR_FILES + ("captions.json",), {}),
}
VARIANT_LABELS = {"transcript": "chỉ transcript", "ocr_raw": "+ OCR chưa lọc", "ocr": "+ OCR",
                  "vlm": "+ VLM", "ocr_vlm": "+ OCR + VLM"}
REQUIRED_EXTRAS = ("screen_text.json", "captions.json")
GROUP_LABELS = {"spoken": "lời nói", "screen": "màn hình", "figure": "hình vẽ",
                "unspecified": "chưa gắn", "all": "tất cả"}
VIDEO_FILES = ("meta.json", "transcript.json", "scenes.json", "frames.json")
DOCUMENT_FILES = ("meta.json", "sections.json")
VISUAL_MARKER = "[Trên màn hình]"


def read_questions(path):
    questions = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            questions.append(validate_question(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise SystemExit(f"{path}:{number}: {exc}") from exc
    ids = [q["id"] for q in questions]
    duplicates = sorted({qid for qid in ids if ids.count(qid) > 1})
    if duplicates:
        raise SystemExit(f"{path}: trùng id câu hỏi: {', '.join(duplicates)}")
    return questions


def stage_variant(source, target, extra_files):
    """Chép file JSON của video/tài liệu từ source sang target; trả về số video có từng file phụ."""
    found = {name: 0 for name in extra_files}
    videos = source / "videos"
    for vdir in sorted(videos.iterdir()) if videos.is_dir() else []:
        if not (vdir / "meta.json").is_file():
            continue
        out = target / "videos" / vdir.name
        out.mkdir(parents=True)
        for name in VIDEO_FILES + tuple(extra_files):
            if (vdir / name).is_file():
                shutil.copy2(vdir / name, out / name)
                if name in found:
                    found[name] += 1
    documents = source / "documents"
    for ddir in sorted(documents.iterdir()) if documents.is_dir() else []:
        if not (ddir / "sections.json").is_file():
            continue
        out = target / "documents" / ddir.name
        out.mkdir(parents=True)
        for name in DOCUMENT_FILES:
            if (ddir / name).is_file():
                shutil.copy2(ddir / name, out / name)
    return found


def run_variant(cfg, name, questions, embedder, workdir, relations=False):
    vcfg = copy.deepcopy(cfg)
    vdata = Path(workdir) / name
    files, chunk_overrides = VARIANTS[name]
    found = stage_variant(data_dir(cfg), vdata, files)
    for artifact, count in found.items():
        if count == 0 and artifact in REQUIRED_EXTRAS:
            print(f"[cảnh báo] {name}: chưa video nào có {artifact}, biến thể này sẽ giống 'transcript'.")
    vcfg["paths"]["data_dir"] = str(vdata)
    vcfg["chunk"]["use_screen_text"] = True
    vcfg["chunk"].update(chunk_overrides)
    vcfg.setdefault("knowledge_graph", {})["extract_relations"] = bool(relations)
    print(f"\n[{name}] dựng index tạm ở {vdata / 'index'}")
    index_mod.build(vcfg, embedder)
    idx = index_mod.load(vcfg)
    retriever = Retriever(idx, embedder, vcfg["retrieval"])
    hits, errors = {}, {}
    for mode in MODES:
        try:
            hits[mode] = {q["id"]: retriever.search(q["question"], mode=mode, top_k=5) for q in questions}
        except RuntimeError as exc:
            errors[mode] = str(exc)
            print(f"[cảnh báo] {name}/{mode}: {exc}")
    return {
        "label": VARIANT_LABELS[name],
        "files_found": found,
        "chunks": len(idx.chunks),
        "chunks_with_visual_text": sum(1 for c in idx.chunks if VISUAL_MARKER in c["text"]),
        "errors": errors,
        **score_results(questions, hits, k=5),
    }


def run_variants(cfg, questions, variants, embedder, workdir, relations=False):
    return {name: run_variant(cfg, name, questions, embedder, workdir, relations) for name in variants}


def _cell(res, mode, group):
    data = res["summary"].get(mode, {}).get("all") if group == "all" else res["by_evidence"].get(mode, {}).get(group)
    return f"{data['recall_at_5']:.2f} / {data['mrr']:.2f}" if data else "—"


def format_report(results):
    details = next(iter(results.values()))["details"]
    counts = Counter(d["evidence"] for d in details)
    counts["all"] = len(details)
    groups = [g for g in EVIDENCE_TYPES + ("unspecified",) if counts[g]] + ["all"]
    header = f"{'':16}" + "".join(f"{GROUP_LABELS[g] + f' ({counts[g]})':>18}" for g in groups)
    lines = ["Recall@5 / MRR theo nơi chứa đáp án (số câu trong ngoặc)."]
    for mode in MODES:
        lines += ["", f"== {mode}{' (mặc định)' if mode == 'hybrid_graph' else ''} ==", header]
        for res in results.values():
            lines.append(f"{res['label']:16}" + "".join(f"{_cell(res, mode, g):>18}" for g in groups))
    lines += ["", "Số chunk (có chữ màn hình / tổng):"]
    lines += [f"  {res['label']:16} {res['chunks_with_visual_text']} / {res['chunks']}" for res in results.values()]
    best = Counter()
    for res in results.values():
        for det in res["details"]:
            best[det["id"]] = max([best[det["id"]]] + [m["recall_at_5"] for m in det["methods"].values()])
    never = [d for d in details if best[d["id"]] == 0]
    if never:
        lines += ["", "Câu không chế độ/biến thể nào tìm thấy (nên kiểm tra lại mốc thời gian hoặc câu hỏi):"]
        lines += [f"  {d['id']}: {d['question']}" for d in never]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", type=Path, help="mặc định: data/eval/questions.jsonl")
    parser.add_argument("--variants", default="transcript,ocr_raw,ocr,ocr_vlm",
                        help=f"danh sách cách nhau bằng dấu phẩy, chọn trong: {', '.join(VARIANTS)}")
    parser.add_argument("--out", type=Path, help="mặc định: data/eval/variants.json")
    parser.add_argument("--relations", action="store_true", help="gọi Ollama trích quan hệ khi dựng đồ thị")
    parser.add_argument("--keep-tmp", action="store_true", help="giữ lại thư mục index tạm để xem")
    args = parser.parse_args()

    cfg = load_config()
    eval_dir = data_dir(cfg) / "eval"
    questions_path = args.questions or eval_dir / "questions.jsonl"
    if not questions_path.is_file():
        parser.error(f"Không thấy file câu hỏi: {questions_path}")
    questions = read_questions(questions_path)
    if not questions:
        parser.error(f"File câu hỏi rỗng: {questions_path}")
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown or not variants:
        parser.error(f"Biến thể không hợp lệ: {', '.join(unknown) or '(trống)'}")
    if not args.relations:
        print("Không gọi Ollama trích quan hệ (thêm --relations để bật); đồ thị chỉ dùng khái niệm và liên kết.")

    from vskr.embedder import Embedder

    embedder = Embedder(cfg["embedding"])
    workdir = Path(tempfile.mkdtemp(prefix="vskr-eval-variants-"))
    try:
        results = run_variants(cfg, questions, variants, embedder, workdir, args.relations)
    finally:
        embedder.close()
        if args.keep_tmp:
            print(f"Giữ thư mục tạm: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    print("\n" + format_report(results))
    out = args.out or eval_dir / "variants.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "questions_file": str(questions_path),
        "question_count": len(questions),
        "relations": args.relations,
        "embedding": cfg["embedding"].get("model"),
        "variants": results,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nĐã ghi {out}")


if __name__ == "__main__":
    main()
