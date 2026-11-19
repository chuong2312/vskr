"""BM25 (Okapi) tự cài, không cần thư viện ngoài. Token hóa theo âm tiết (đủ dùng cho tiếng Việt)."""
import math
import re
import unicodedata
from collections import Counter

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text):
    """Tách văn bản thành các từ (âm tiết) viết thường, sau khi chuẩn hoá Unicode NFC."""
    return _TOKEN.findall(unicodedata.normalize("NFC", text).lower())


class BM25:
    """Chỉ mục BM25 (Okapi) trên các đoạn đã tách từ; ``k1``, ``b`` là tham số chuẩn của BM25."""
    def __init__(self, docs_tokens, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.n = len(docs_tokens)
        self.tf = [Counter(d) for d in docs_tokens]
        self.dl = [len(d) for d in docs_tokens]
        self.avgdl = (sum(self.dl) / self.n) if self.n else 0.0
        self.postings = {}
        df = Counter()
        for i, c in enumerate(self.tf):
            df.update(c.keys())
            for token, frequency in c.items():
                self.postings.setdefault(token, []).append((i, frequency))
        self.idf = {t: math.log(1 + (self.n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query_tokens, allowed_docs=None):
        """Điểm BM25 của câu truy vấn cho từng đoạn, theo thứ tự trong chỉ mục.

        ``allowed_docs`` là mặt nạ True/False cho từng đoạn: IDF và độ dài trung bình chỉ tính trên các
        đoạn được phép, nên tần suất từ trong học liệu người xem không được thấy không làm lộ thông tin.
        """
        res = [0.0] * self.n
        if allowed_docs is None:
            allowed = None
            doc_count = self.n
            avgdl = self.avgdl
        else:
            if len(allowed_docs) != self.n:
                raise ValueError("allowed_docs must have one entry per indexed document")
            allowed = {i for i, permitted in enumerate(allowed_docs) if permitted}
            doc_count = len(allowed)
            avgdl = sum(self.dl[i] for i in allowed) / doc_count if doc_count else 0.0
        if not doc_count:
            return res
        for t in set(query_tokens):
            postings = self.postings.get(t)
            if not postings:
                continue
            matches = [(i, f) for i, f in postings if allowed is None or i in allowed]
            if not matches:
                continue
            idf = math.log(1 + (doc_count - len(matches) + 0.5) / (len(matches) + 0.5))
            for i, f in matches:
                denom = f + self.k1 * (1 - self.b + self.b * self.dl[i] / (avgdl or 1))
                res[i] += idf * f * (self.k1 + 1) / denom
        return res
