"""Đo thời gian chờ và hết hạn của câu hỏi khi khoá model dùng chung đang bị giữ."""

import argparse
import json
import threading
import time

from vskr.service import ModelBusyError, Service
from vskr.config import load_config
from vskr.api.handlers.runtime import _gpu_model_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True, help="câu hỏi để chạy retrieval thật")
    parser.add_argument("--hold-seconds", type=float, default=12.0,
                        help="thời gian giữ lock mô phỏng ASR; mặc định vượt timeout ask 10 giây")
    parser.add_argument("--ask-timeout", type=float, default=10.0)
    args = parser.parse_args()
    if args.hold_seconds <= 0 or args.ask_timeout < 0:
        parser.error("Thời gian giữ lock phải > 0 và timeout phải >= 0.")

    service = Service(load_config())
    acquired = threading.Event()

    def occupy_lock():
        with _gpu_model_lock:
            acquired.set()
            time.sleep(args.hold_seconds)

    worker = threading.Thread(target=occupy_lock, name="model-lock-benchmark", daemon=True)
    worker.start()
    if not acquired.wait(timeout=5):
        raise RuntimeError("Không lấy được shared model lock để chạy benchmark.")
    started = time.monotonic()
    timed_out = False
    try:
        service.ask(args.question, llm_lock=_gpu_model_lock, lock_timeout=args.ask_timeout)
    except ModelBusyError:
        timed_out = True
    finally:
        elapsed = time.monotonic() - started
        worker.join()
        service.embedder.close()
    print(json.dumps({
        "ask_timeout_seconds": args.ask_timeout,
        "lock_held_seconds": args.hold_seconds,
        "ask_wait_seconds": round(elapsed, 3),
        "timed_out": timed_out,
        "note": "Lock được giữ trong thread benchmark; đây không phải phép đo thời lượng ASR/GPU.",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
