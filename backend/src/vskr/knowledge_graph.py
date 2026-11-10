"""Dependency-free concept graph built from indexed VSKR chunks.

Each source chunk is a timestamped video segment (or future document section).
It connects to concepts it mentions; concepts co-occurring in a source are
connected by a weighted edge. The schema allows document sections to join the
same graph when document parsing is added.
"""
from collections import Counter, defaultdict

from .bm25 import tokenize


_STOP_WORDS = set(
    "a an and are as at be been bằng các cái cho có của cùng cũng đã đang để đến "
    "được đây đó do dùng gì khi không là lại làm lên mà một này nó những nào "
    "nội như ở phải qua ra rằng sau sẽ thì trong từ với vào và về vì "
    "the this that these those to of in on for from is was were use using "
    "what how why when where which who video đoạn phần bài học ví dụ chúng ta "
    "mình bạn tôi họ ấy nhé thôi".split()
)


def keywords(text, limit=12):
    """Return frequent non-stopword Vietnamese syllables/technical tokens."""
    counts = Counter(
        token for token in tokenize(text)
        if len(token) >= 2 and token not in _STOP_WORDS and not token.isdigit()
    )
    return [token for token, _count in counts.most_common(limit)]


def build_graph(chunks, max_keywords_per_segment=12):
    """Build timestamped source nodes, concept nodes and weighted edges."""
    nodes = {}
    edges = {}
    for chunk in chunks:
        source_types = chunk.get("source_types") or ["speech"]
        is_document = "doc" in source_types or chunk.get("source_type") == "doc"
        source_id = str(chunk["chunk_id"])
        source_node_id = f"source:{source_id}"
        nodes[source_node_id] = {
            "id": source_node_id,
            "kind": "document_section" if is_document else "video_segment",
            "chunk_id": source_id,
            "video_id": chunk.get("video_id"),
            "document_id": chunk.get("document_id"),
            "title": chunk.get("title"),
            "start": chunk.get("start"),
            "end": chunk.get("end"),
            "text": chunk["text"],
        }
        terms = keywords(chunk["text"], max_keywords_per_segment)
        concept_ids = []
        for term in terms:
            concept_id = f"concept:{term}"
            nodes.setdefault(concept_id, {"id": concept_id, "kind": "concept", "label": term})
            concept_ids.append(concept_id)
            edge_id = (concept_id, source_node_id, "MENTIONS")
            edge = edges.setdefault(edge_id, {
                "from": concept_id, "to": source_node_id, "type": "MENTIONS", "weight": 0
            })
            edge["weight"] += 1
        for i, left in enumerate(concept_ids):
            for right in concept_ids[i + 1:]:
                a, b = sorted((left, right))
                edge_id = (a, b, "CO_OCCURS")
                edge = edges.setdefault(edge_id, {
                    "from": a, "to": b, "type": "CO_OCCURS", "weight": 0
                })
                edge["weight"] += 1
    return {"version": 1, "nodes": list(nodes.values()), "edges": list(edges.values())}


def search_graph(graph, chunks, question, top_k=5, expansion_weight=0.35):
    """Traverse query concepts -> co-occurring concepts -> source chunks.

    The result shape matches the existing vector/BM25 retrievers, preserving
    the current answer generation and timestamp citation behavior.
    """
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    query_terms = list(dict.fromkeys(keywords(question, limit=32)))
    anchors = {f"concept:{term}" for term in query_terms if f"concept:{term}" in nodes}
    if not anchors:
        return []

    scores = defaultdict(float)
    direct_hits = defaultdict(int)
    mentions_by_concept = defaultdict(list)
    for edge in graph.get("edges", []):
        if edge.get("type") == "MENTIONS":
            mentions_by_concept[edge["from"]].append(edge["to"])
            if edge["from"] in anchors:
                scores[edge["to"]] += 1.0
                direct_hits[edge["to"]] += 1

    for edge in graph.get("edges", []):
        if edge.get("type") != "CO_OCCURS":
            continue
        left, right = edge["from"], edge["to"]
        expanded = right if left in anchors else left if right in anchors else None
        if expanded is not None:
            for source_id in mentions_by_concept[expanded]:
                scores[source_id] += expansion_weight

    chunks_by_id = {
        str(chunk["chunk_id"]): (i, chunk) for i, chunk in enumerate(chunks)
    }
    ranked = []
    for source_node_id, score in scores.items():
        source = nodes.get(source_node_id, {})
        item = chunks_by_id.get(str(source.get("chunk_id")))
        if item is not None:
            idx, chunk = item
            ranked.append((idx, chunk, score, direct_hits[source_node_id]))
    ranked.sort(key=lambda row: (-row[2], -row[3], row[1].get("start", 0), row[0]))
    return [
        {"idx": idx, "score": round(score, 6), "chunk": chunk}
        for idx, chunk, score, _direct in ranked[:top_k]
    ]
