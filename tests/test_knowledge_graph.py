import unittest

from vskr.knowledge_graph import build_graph, search_graph


class TestKnowledgeGraph(unittest.TestCase):
    def setUp(self):
        self.chunks = [
            {
                "chunk_id": "video-a:0000", "video_id": "video-a",
                "start": 12.0, "end": 28.0,
                "text": "Docker Compose điều phối container và service trong dự án.",
                "source_types": ["speech"],
            },
            {
                "chunk_id": "video-a:0001", "video_id": "video-a",
                "start": 40.0, "end": 55.0,
                "text": "Container đóng gói ứng dụng và thư viện cần thiết.",
                "source_types": ["speech"],
            },
            {
                "chunk_id": "doc-1:0000", "document_id": "doc-1",
                "title": "Tài liệu Docker", "start": 2, "end": 2,
                "text": "Docker Compose khai báo service và mạng container.",
                "source_types": ["doc"],
            },
        ]
        self.graph = build_graph(self.chunks)

    def test_segments_and_document_sections_are_timestamped_source_nodes(self):
        nodes = {node["chunk_id"]: node for node in self.graph["nodes"] if node["kind"] != "concept"}
        self.assertEqual(nodes["video-a:0000"]["kind"], "video_segment")
        self.assertEqual(nodes["video-a:0000"]["start"], 12.0)
        self.assertEqual(nodes["doc-1:0000"]["kind"], "document_section")
        self.assertEqual(nodes["doc-1:0000"]["document_id"], "doc-1")

    def test_graph_links_shared_concept_across_video_and_document(self):
        mentions = {
            (edge["from"], edge["to"])
            for edge in self.graph["edges"] if edge["type"] == "MENTIONS"
        }
        self.assertIn(("concept:docker", "source:video-a:0000"), mentions)
        self.assertIn(("concept:docker", "source:doc-1:0000"), mentions)

    def test_query_traverses_cooccurrence_to_retrieve_supporting_segment(self):
        hits = search_graph(self.graph, self.chunks, "Docker", top_k=3)
        ids = [hit["chunk"]["chunk_id"] for hit in hits]
        self.assertIn("video-a:0000", ids)
        self.assertIn("video-a:0001", ids)
        self.assertIn("doc-1:0000", ids)
        self.assertEqual(hits[0]["chunk"]["chunk_id"], "video-a:0000")

    def test_unknown_query_returns_no_graph_hits(self):
        self.assertEqual(search_graph(self.graph, self.chunks, "quantum xyzzy"), [])


if __name__ == "__main__":
    unittest.main()
