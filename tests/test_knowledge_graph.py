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


def _video_chunks(*texts):
    return [{"chunk_id": f"v:{i:04d}", "video_id": "v", "start": i * 60.0, "end": i * 60.0 + 50,
             "text": text, "source_types": ["speech"]} for i, text in enumerate(texts)]


class TestGraphRanking(unittest.TestCase):
    """Đoạn khớp nhiều khái niệm (và khái niệm hiếm) phải đứng trước, không xếp theo thứ tự index."""

    def _ids(self, hits):
        return [hit["chunk"]["chunk_id"] for hit in hits]

    def test_chunk_matching_more_query_concepts_ranks_first(self):
        chunks = _video_chunks(
            "nhiều thành viên đã thành công",          # chỉ khớp "thành", lại đứng đầu index
            "cpu gồm alu cu và thanh ghi",              # khớp "cpu" và "gồm"
            "thành phố lớn được thành lập",            # chỉ khớp "thành"
        )
        hits = search_graph(build_graph(chunks), chunks, "CPU gồm những thành phần nào?", top_k=3)
        self.assertEqual(hits[0]["chunk"]["chunk_id"], "v:0001")
        self.assertGreater(hits[0]["score"], hits[1]["score"])

    def test_rare_concept_outweighs_common_concept(self):
        chunks = _video_chunks(
            "thành phố thành lập",
            "thành viên thành công",
            "thanh ghi nằm trong cpu",
            "thành tích thành quả",
        )
        hits = search_graph(build_graph(chunks), chunks, "cpu thành", top_k=4)
        self.assertEqual(hits[0]["chunk"]["chunk_id"], "v:0002")

    def test_short_concept_does_not_match_inside_another_word(self):
        chunks = _video_chunks("chi phí lưu trữ cao", "phần mềm được chia thành hai loại")
        hits = search_graph(build_graph(chunks), chunks, "Phần mềm chia thành mấy loại?", top_k=5)
        concepts = {term for hit in hits for term in hit["matched_concepts"]}
        self.assertIn("chia", concepts)
        self.assertNotIn("chi", concepts)
        self.assertNotIn("v:0000", self._ids(hits))

    def test_multi_word_relation_concept_still_matches_as_phrase(self):
        chunks = _video_chunks("docker compose khởi chạy nhiều container cùng lúc", "học về mạng máy tính")

        def extractor(_chunk):
            return [{"head": "Docker Compose", "relation": "USES", "tail": "container",
                     "evidence": "docker compose khởi chạy nhiều container"}]

        graph = build_graph(chunks, relation_extractor=extractor)
        hits = search_graph(graph, chunks, "dùng docker compose để làm gì", top_k=2)
        self.assertEqual(hits[0]["chunk"]["chunk_id"], "v:0000")
        self.assertIn("docker compose", hits[0]["matched_concepts"])
        # Cụm từ chỉ khớp khi đứng liền nhau và đủ cả từ, không khớp một phần.
        partial = search_graph(graph, chunks, "dockerx compose", top_k=2)
        self.assertNotIn("docker compose", {t for hit in partial for t in hit["matched_concepts"]})


if __name__ == "__main__":
    unittest.main()
