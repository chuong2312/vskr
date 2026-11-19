import unittest
from types import SimpleNamespace

from vskr.retriever import Retriever


class TestAllowedMaskCache(unittest.TestCase):
    def test_allowed_mask_cache_isolated_by_permission_source_set(self):
        retriever = Retriever(SimpleNamespace(chunks=[
            {"video_id": "private-a"},
            {"video_id": "private-b"},
        ]), None, {})

        mask_a = retriever._allowed_mask({"private-a"})
        mask_b = retriever._allowed_mask({"private-b"})

        self.assertEqual(mask_a.tolist(), [True, False])
        self.assertEqual(mask_b.tolist(), [False, True])
        self.assertIsNot(mask_a, mask_b)

    def test_allowed_masks_are_cached_with_a_bounded_lru(self):
        retriever = Retriever(SimpleNamespace(chunks=[{"document_id": "doc"}]), None, {})
        for index in range(512):
            retriever._allowed_mask({f"source-{index}"})
        retriever._allowed_mask({"source-0"})
        retriever._allowed_mask({"source-512"})

        self.assertEqual(len(retriever._allowed_mask_cache), 512)
        self.assertIn(frozenset({"source-0"}), retriever._allowed_mask_cache)
        self.assertNotIn(frozenset({"source-1"}), retriever._allowed_mask_cache)
