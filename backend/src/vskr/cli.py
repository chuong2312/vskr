"""Điểm vào dòng lệnh. Mỗi bước chạy trong tiến trình riêng nên VRAM tự được giải phóng sau mỗi bước."""
import argparse

from .config import load_config


def main():
    ap = argparse.ArgumentParser(prog="vskr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor", help="kiểm tra môi trường")
    p.add_argument("--gpu-test", action="store_true", help="chạy thử faster-whisper thật trên GPU (tải model tiny ~75MB)")
    p = sub.add_parser("ingest", help="tải video/âm thanh từ URL được yt-dlp hỗ trợ")
    p.add_argument("urls", nargs="+")
    p.add_argument("--with-video", action="store_true", help="tải cả hình 720p (cần cho OCR sau này)")
    p = sub.add_parser("asr", help="nhận dạng giọng nói")
    p.add_argument("--video", action="append", help="video_id (mặc định: tất cả)")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("frames", help="trích frame (PySceneDetect + lấy mẫu định kỳ + lọc trùng)")
    p.add_argument("--video", action="append", help="video_id (mặc định: tất cả)")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("ocr", help="đọc chữ trên frame bằng service PaddleOCR")
    p.add_argument("--video", action="append", help="video_id (mặc định: tất cả)")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("vlm", help="mô tả hình ảnh bằng service VLM")
    p.add_argument("--video", action="append", help="video_id (mặc định: tất cả)")
    p.add_argument("--force", action="store_true")
    sub.add_parser("index", help="chunk + embedding + lưu chỉ mục")
    p = sub.add_parser("ask", help="hỏi thử trên dòng lệnh")
    p.add_argument("question")
    p.add_argument("--mode", choices=["vector", "bm25", "hybrid", "graph"])
    p = sub.add_parser("serve", help="chạy trang demo")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    cfg = load_config()

    if args.cmd == "doctor":
        from . import doctor

        doctor.main(cfg, gpu_test=args.gpu_test)
    elif args.cmd == "ingest":
        from .ingest import ingest

        for u in args.urls:
            m = ingest(u, cfg, with_video=args.with_video)
            print(f"[ingest] {m['video_id']} | {m['title']} | {m['duration']}s")
    elif args.cmd == "asr":
        from . import asr
        from .index import list_video_ids

        asr.run(cfg, args.video or list_video_ids(cfg), force=args.force)
    elif args.cmd == "frames":
        from . import frames
        from .index import list_video_ids

        for vid in args.video or list_video_ids(cfg):
            frames.run(cfg, vid, force=args.force)
    elif args.cmd in ("ocr", "vlm"):
        from .index import list_video_ids
        module = __import__(f"vskr.{args.cmd}", fromlist=["run"])
        module.run(cfg, args.video or list_video_ids(cfg), force=args.force)
    elif args.cmd == "index":
        from . import index
        from .embedder import Embedder

        emb = Embedder(cfg["embedding"])
        try:
            index.build(cfg, emb)
        finally:
            emb.close()
    elif args.cmd == "ask":
        from .service import Service
        from .util import fmt_time

        res = Service(cfg).ask(args.question, mode=args.mode)
        print("\n" + res["answer"] + "\n")
        for s in res["sources"]:
            tag = "" if s["cited"] else " (liên quan, mô hình không dẫn nguồn)"
            print(f"  [{s['n']}] {s['title']} | {s['label']}{tag}\n      {s['link']}")
        print(f"\nthời gian: tìm {res['timing']['retrieve_s']}s, LLM {res['timing']['llm_s']}s, chế độ {res['mode']}")
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("vskr.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
