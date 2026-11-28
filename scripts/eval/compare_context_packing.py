"""So câu trả lời thật của hệ thống giữa hai cách thu gọn nguồn: ``prefix`` (cũ) và ``extract`` (mới).

Với mỗi câu trong ``data/eval/questions.jsonl``, script tìm nguồn bằng ``hybrid`` rồi dựng prompt theo
cả hai cách. Câu nào hai cách cho prompt giống hệt nhau thì chỉ hỏi Ollama 1 lần, vì khác biệt lúc đó
chỉ là do model trả lời ngẫu nhiên, không do cách thu gọn. Script chỉ đọc chỉ mục, không sửa file chấm
cũ, và cần Ollama đang chạy.

    python scripts/eval/compare_context_packing.py

Ghi ra ``data/eval/drafts/``:
- ``answers-compare.jsonl``: câu trả lời của cả hai cách cho mọi câu
- ``answers-compare.md``: trang chấm, chỉ gồm các câu có prompt khác nhau. Mỗi câu có 3 ô cho câu trả
  lời mới và 1 dòng so sánh với câu trả lời cũ; người chấm là chủ dự án, script không tự chấm.
"""
import json
import time

from vskr.config import data_dir, load_config
from vskr.evaluation import matches_reference
from vskr.qa import build_messages
from vskr.util import fmt_time

MODES = ("prefix", "extract")


def ask(service, question, mode):
    """Hỏi một câu với cách thu gọn ``mode``; trả về câu trả lời, các nguồn được trích và thời gian."""
    service.cfg["llm"]["context_packing"] = mode
    started = time.time()
    result = service.ask(question["question"], admin=True)
    sources = [{"n": src["n"], "start": src.get("start"), "end": src.get("end"),
                "matches_reference": any(matches_reference(src, ref) for ref in question["relevant"]),
                "text": (src.get("text") or "")[:400]}
               for src in result["sources"]]
    return {"answer": result.get("answer"), "found": result.get("found"), "sources": sources,
            "seconds": round(time.time() - started, 1)}


def collect(service, questions, num_ctx, log=print):
    """Hỏi từng câu theo cả hai cách; câu có prompt giống hệt nhau chỉ hỏi 1 lần."""
    idf = service.index.bm25.idf
    rows = []
    for number, q in enumerate(questions, 1):
        hits = service.retriever.search(q["question"], mode="hybrid")
        prompts = {mode: build_messages(q["question"], hits, num_ctx=num_ctx, packing=mode,
                                        term_weights=idf)[1]["content"]
                   for mode in MODES}
        same = prompts["prefix"] == prompts["extract"]
        new = ask(service, q, "extract")
        old = new if same else ask(service, q, "prefix")
        rows.append({"id": q["id"], "question": q["question"], "evidence": q.get("evidence", "spoken"),
                     "same_prompt": same, "prefix": old, "extract": new})
        status = "prompt giống nhau, hỏi 1 lần" if same else f"cũ {old['seconds']}s, mới {new['seconds']}s"
        log(f"[{number}/{len(questions)}] {q['id']}: {status}")
    return rows


def not_found_counts(rows):
    """Số câu trả lời "không tìm thấy" của từng cách, do máy đếm."""
    return {mode: sum(not row[mode]["found"] for row in rows) for mode in MODES}


def _quote(text):
    return ["> " + line if line.strip() else ">" for line in (text or "").splitlines()]


def build_review_page(rows, expected, model, generated_at):
    """Trang chấm Markdown cho các câu có prompt khác nhau giữa hai cách."""
    changed = [row for row in rows if not row["same_prompt"]]
    counts = not_found_counts(rows)
    md = [
        "# So câu trả lời: cách thu gọn nguồn cũ (prefix) và mới (extract)",
        "",
        f"Model `{model}` · sinh lúc {generated_at} · {len(changed)}/{len(rows)} câu có prompt khác nhau"
        f" (các câu còn lại prompt giống hệt nên không cần chấm).",
        "",
        f"Số câu trả lời \"không tìm thấy\": cũ {counts['prefix']}, mới {counts['extract']} (máy tự đếm).",
        "",
        "Với mỗi câu: tick 3 ô cho **câu trả lời mới**, rồi tick đúng 1 ô so sánh với câu trả lời cũ.",
        "",
    ]
    for row in changed:
        old, new = row["prefix"], row["extract"]
        md += [f"### {row['id']} · {row['evidence']}", "", f"**Hỏi:** {row['question']}", "",
               f"**Đáp án mong đợi:** {expected.get(row['id'], '(chưa có)')}", "", "**Câu trả lời cũ:**", ""]
        md += _quote(old["answer"]) + ["", "**Câu trả lời mới:**", ""]
        md += _quote(new["answer"]) + ["", "<details><summary>Nguồn câu trả lời mới trích</summary>", ""]
        for src in new["sources"]:
            where = f"{fmt_time(src['start'])}–{fmt_time(src['end'])}" if src.get("start") is not None else ""
            mark = "trúng nguồn đúng" if src["matches_reference"] else "không trùng nguồn đúng"
            md += [f"- **[{src['n']}]** {where} · {mark}", f"  > {' '.join(src['text'].split())[:300]}"]
        md += ["", "</details>", "",
               "- [ ] Trả lời đúng", "- [ ] Dựa vào nguồn", "- [ ] Trích dẫn đúng", "",
               "So với câu trả lời cũ: [ ] tốt hơn · [ ] như nhau · [ ] kém hơn", "", "Ghi chú:", ""]
    return "\n".join(md) + "\n"


def _read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main():
    """Hỏi 36 câu theo cả hai cách rồi ghi file kết quả và trang chấm vào data/eval/drafts/."""
    from vskr.service import Service

    cfg = load_config()
    root = data_dir(cfg) / "eval"
    drafts = root / "drafts"
    questions = _read_jsonl(root / "questions.jsonl")
    expected = {row["id"]: row["expected_answer"] for row in _read_jsonl(drafts / "answers-ground-truth.jsonl")}
    service = Service(cfg)
    rows = collect(service, questions, cfg["llm"]["num_ctx"], log=lambda line: print(line, flush=True))
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "answers-compare.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    page = drafts / "answers-compare.md"
    page.write_text(build_review_page(rows, expected, cfg["llm"]["model"], time.strftime("%Y-%m-%d %H:%M")),
                    encoding="utf-8")
    counts = not_found_counts(rows)
    print(f"Xong. {sum(not r['same_prompt'] for r in rows)} câu cần chấm: {page}")
    print(f"Không tìm thấy: cũ {counts['prefix']}, mới {counts['extract']}")


if __name__ == "__main__":
    main()
