import unittest
from types import SimpleNamespace

from vskr.retriever import Retriever


class TestAllowedMaskCache(unittest.TestCase):
    def test_allowed_masks_are_cached_with_a_bounded_lru(self):
        retriever = Retriever(SimpleNamespace(chunks=[{"document_id": "doc"}]), None, {})
        for index in range(512):
            retriever._allowed_mask({f"source-{index}"})
        retriever._allowed_mask({"source-0"})
        retriever._allowed_mask({"source-512"})

        self.assertLess(len(retriever._allowed_mask_cache), 513)
        self.assertIn(frozenset({"source-0"}), retriever._allowed_mask_cache)
        self.assertNotIn(frozenset({"source-1"}), retriever._allowed_mask_cache)
