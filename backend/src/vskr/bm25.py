"""BM25 (Okapi) tự cài, không cần thư viện ngoài. Token hóa theo âm tiết (đủ dùng cho tiếng Việt)."""
import math
import re
import unicodedata
from collections import Counter

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text):
    return _TOKEN.findall(unicodedata.normalize("NFC", text).lower())


class BM25:
    def __init__(self, docs_tokens, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.n = len(docs_tokens)
        self.tf = [Counter(d) for d in docs_tokens]
        self.dl = [len(d) for d in docs_tokens]
        self.avgdl = (sum(self.dl) / self.n) if self.n else 0.0
        df = Counter()
        for c in self.tf:
            df.update(c.keys())
        self.idf = {t: math.log(1 + (self.n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query_tokens):
        res = [0.0] * self.n
        for t in set(query_tokens):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, c in enumerate(self.tf):
                f = c.get(t, 0)
                if f:
                    denom = f + self.k1 * (1 - self.b + self.b * self.dl[i] / (self.avgdl or 1))
                    res[i] += idf * f * (self.k1 + 1) / denom
        return res
