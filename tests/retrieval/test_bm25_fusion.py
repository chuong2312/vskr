"""Tách từ tiếng Việt, chấm điểm BM25 và gộp thứ hạng bằng RRF."""
import unittest

from vskr.retrieval.bm25 import BM25, tokenize
from vskr.retrieval.fusion import rrf_fuse


class TestRetrievalLogic(unittest.TestCase):
    def test_tokenize_nfc(self):
        a = tokenize("Biến số")
        b = tokenize("Bie\u0302\u0301n so\u0302\u0301")  # dạng tổ hợp
        self.assertEqual(tokenize("Biến"), tokenize("Bie\u0302\u0301n"))
        self.assertEqual(a[0], "biến")
        self.assertEqual(len(b), 2)

    def test_bm25_ranks_relevant_first(self):
        docs = ["khai báo biến trong python", "vòng lặp for và while", "hàm và tham số"]
        bm = BM25([tokenize(d) for d in docs])
        s = bm.scores(tokenize("khai báo biến"))
        self.assertEqual(max(range(3), key=lambda i: s[i]), 0)
        self.assertEqual(bm.scores(tokenize("không có từ này")), [0.0, 0.0, 0.0])

    def test_bm25_scores_use_only_authorized_documents_for_idf(self):
        bm = BM25([
            ["sharedterm"],
            ["sharedterm", "private"],
            ["sharedterm", "private"],
        ])

        score = bm.scores(["sharedterm"], allowed_docs=[True, False, False])

        self.assertAlmostEqual(score[0], 0.28768207245178085)
        self.assertEqual(score[1:], [0.0, 0.0])

    def test_rrf(self):
        fused = rrf_fuse([[1, 2, 3], [3, 1, 4]], k=60)
        ids = [i for i, _ in fused]
        self.assertEqual(ids[0], 1)  # 1: hạng 1 và 2; 3: hạng 3 và 1 -> 1 cao hơn
        self.assertEqual(set(ids), {1, 2, 3, 4})
        self.assertGreater(fused[0][1], fused[-1][1])


if __name__ == "__main__":
    unittest.main()
