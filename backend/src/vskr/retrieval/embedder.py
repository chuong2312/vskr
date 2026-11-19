"""Mô hình nhúng văn bản (sentence-transformers, mặc định E5) dùng cho tìm kiếm theo nghĩa.

Vector được chuẩn hoá về độ dài 1, nên tích vô hướng chính là độ tương đồng cosine.
"""
import gc

import numpy as np


class Embedder:
    """Bọc sentence-transformers. Tiền tố query/passage lấy từ config (E5 cần, bge-m3 thì để rỗng)."""

    def __init__(self, ecfg):
        from sentence_transformers import SentenceTransformer

        self.cfg = ecfg
        self.model = SentenceTransformer(ecfg["model"], device=ecfg.get("device", "cpu"))
        if ecfg.get("max_seq_length"):
            self.model.max_seq_length = int(ecfg["max_seq_length"])

    def _enc(self, texts, bar=False):
        v = self.model.encode(
            texts,
            batch_size=self.cfg.get("batch_size", 32),
            normalize_embeddings=True,
            show_progress_bar=bar,
            convert_to_numpy=True,
        )
        return np.asarray(v, dtype=np.float32)

    def encode_passages(self, texts):
        """Nhúng các đoạn cần lập chỉ mục (thêm ``passage_prefix`` vào trước mỗi đoạn)."""
        p = self.cfg.get("passage_prefix", "")
        return self._enc([p + t for t in texts], bar=True)

    def encode_query(self, text):
        """Nhúng một câu truy vấn (thêm ``query_prefix``)."""
        return self._enc([self.cfg.get("query_prefix", "") + text])[0]

    def close(self):
        """Giải phóng model khỏi bộ nhớ (và VRAM nếu chạy trên GPU)."""
        del self.model
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
