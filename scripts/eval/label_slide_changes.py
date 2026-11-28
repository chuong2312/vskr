"""Tạo trang gán nhãn thời điểm nội dung trên màn hình đổi, để đo bộ dò trạng thái slide.

Script cắt ảnh nhỏ cứ ``--every`` giây một ảnh trong đoạn [``--start``, ``--end``] của video (mặc định
10 phút đầu), rồi ghi một trang HTML mở trên máy: không cần mạng, không gửi gì ra ngoài. Trên trang, bấm
vào ảnh ĐẦU TIÊN có nội dung mới (slide mới, thêm node, mũi tên, dòng chữ, dòng code). Không bấm khi chỉ
con trỏ chuột, webcam hay khung chat đổi. Lựa chọn được lưu trong trình duyệt nên tải lại trang không
mất. Bấm "Tải file nhãn", rồi chép file tải về vào ``data/eval/slides/<video_id>.json``.

    python scripts/eval/label_slide_changes.py --video user-4a6f11cb6615
    python scripts/eval/label_slide_changes.py --video <id> --start 600 --end 1200 --every 2

Ảnh và trang nằm trong ``data/eval/slides/<video_id>/`` (git bỏ qua). Chạy lại sẽ xoá ảnh ``t_*.jpg``
cũ trong thư mục đó rồi cắt lại.
"""
import argparse
import html
import json
import subprocess
from pathlib import Path

from vskr.config import data_dir, load_config, video_dir
from vskr.pipeline.ingest import resolve_source
from vskr.util import fmt_time, read_json


def thumbnail_cmd(video_path, start, end, every, width, pattern):
    """Lệnh ffmpeg cắt ảnh nhỏ cứ ``every`` giây một ảnh trong đoạn [start, end]."""
    return [
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file,pipe",
        "-ss", f"{float(start):.2f}", "-t", f"{float(end) - float(start):.2f}", "-i", str(video_path),
        "-an", "-sn", "-vf", f"fps=1/{float(every)},scale={int(width)}:-2", "-q:v", "4", str(pattern),
    ]


def thumb_times(count, start, every):
    """Giây ứng với từng ảnh: ảnh thứ k (đếm từ 0) ở ``start + k * every``."""
    return [round(float(start) + k * float(every), 2) for k in range(count)]


def build_label_page(video_id, title, items, start, end, every):
    """Trang HTML gán nhãn. ``items`` là danh sách (giây, đường dẫn ảnh tương đối)."""
    figures = "\n".join(
        f'<figure data-t="{t}"><img src="{html.escape(src)}" loading="lazy" alt="">'
        f"<figcaption>{fmt_time(t)}</figcaption></figure>"
        for t, src in items
    )
    settings = json.dumps({"video_id": video_id, "start": start, "end": end, "every": every},
                          ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<title>Gán nhãn đổi nội dung – {html.escape(title)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 16px; background: #f6f6f6; color: #222; }}
header {{ position: sticky; top: 0; background: #f6f6f6; padding: 8px 0; z-index: 1; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 8px; }}
figure {{ margin: 0; background: #fff; border: 3px solid transparent; cursor: pointer; }}
figure.on {{ border-color: #d22; }}
figure img {{ width: 100%; display: block; }}
figcaption {{ font-size: 13px; padding: 2px 6px; }}
figure.on figcaption::after {{ content: " · đổi ở đây"; color: #d22; font-weight: 600; }}
button {{ font-size: 15px; padding: 6px 14px; }}
</style>
</head>
<body>
<header>
<h1>Gán nhãn đổi nội dung: {html.escape(title)}</h1>
<p>Bấm vào <b>ảnh đầu tiên</b> có nội dung mới: slide mới, thêm node, mũi tên, dòng chữ hay dòng code.
Không bấm khi chỉ con trỏ chuột, webcam hay khung chat đổi. Bấm lần nữa để bỏ chọn.
Ảnh cứ {every:g} giây một ảnh, từ {fmt_time(start)} đến {fmt_time(end)}.</p>
<p><button id="save">Tải file nhãn</button> Đã chọn: <b id="count">0</b>.
Chép file tải về vào <code>data/eval/slides/{html.escape(video_id)}.json</code>.</p>
</header>
<div class="grid">
{figures}
</div>
<script>
const SETTINGS = {settings};
const KEY = "vskr-slide-labels-" + SETTINGS.video_id + "-" + SETTINGS.start + "-" + SETTINGS.end;
const chosen = new Set(JSON.parse(localStorage.getItem(KEY) || "[]"));
const counter = document.getElementById("count");
function refresh() {{
  for (const fig of document.querySelectorAll("figure")) {{
    fig.classList.toggle("on", chosen.has(Number(fig.dataset.t)));
  }}
  counter.textContent = chosen.size;
  localStorage.setItem(KEY, JSON.stringify([...chosen]));
}}
document.querySelector(".grid").addEventListener("click", (event) => {{
  const fig = event.target.closest("figure");
  if (!fig) return;
  const t = Number(fig.dataset.t);
  if (chosen.has(t)) chosen.delete(t); else chosen.add(t);
  refresh();
}});
document.getElementById("save").addEventListener("click", () => {{
  const labels = {{...SETTINGS, changes: [...chosen].sort((a, b) => a - b), labeled_at: new Date().toISOString()}};
  const blob = new Blob([JSON.stringify(labels, null, 2)], {{type: "application/json"}});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = SETTINGS.video_id + ".json";
  link.click();
  URL.revokeObjectURL(link.href);
}});
refresh();
</script>
</body>
</html>
"""


def main():
    """Cắt ảnh nhỏ và ghi trang gán nhãn cho một video."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--video", required=True, help="video_id")
    ap.add_argument("--start", type=float, default=0.0, help="giây bắt đầu (mặc định 0)")
    ap.add_argument("--end", type=float, default=600.0, help="giây kết thúc (mặc định 600 = 10 phút)")
    ap.add_argument("--every", type=float, default=2.0, help="khoảng cách giữa hai ảnh, giây (mặc định 2)")
    ap.add_argument("--width", type=int, default=320, help="chiều rộng ảnh nhỏ, px (mặc định 320)")
    args = ap.parse_args()

    cfg = load_config()
    vdir = video_dir(cfg, args.video)
    meta = read_json(vdir / "meta.json")
    vpath = resolve_source(vdir, meta)
    if not meta.get("has_video_file") or vpath is None:
        ap.error(f"{args.video}: chưa có file video.")
    end = min(args.end, float(meta.get("duration") or args.end))
    if end <= args.start or args.every <= 0:
        ap.error("đoạn thời gian hoặc --every không hợp lệ.")

    out_dir = data_dir(cfg) / "eval" / "slides" / args.video
    thumbs = out_dir / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    for old in thumbs.glob("t_*.jpg"):
        old.unlink()
    subprocess.run(
        thumbnail_cmd(vpath, args.start, end, args.every, args.width, thumbs / "t_%05d.jpg"),
        check=True, timeout=3600,
    )
    files = sorted(thumbs.glob("t_*.jpg"))
    items = [(t, f"thumbs/{p.name}") for t, p in zip(thumb_times(len(files), args.start, args.every), files)]
    page = out_dir / "label.html"
    page.write_text(
        build_label_page(args.video, meta.get("title") or args.video, items, args.start, end, args.every),
        encoding="utf-8",
    )
    print(f"[nhãn] {len(items)} ảnh, trang: {page}")
    print(f'Mở trên Windows: explorer.exe "$(wslpath -w {Path(page).resolve()})"')


if __name__ == "__main__":
    main()
