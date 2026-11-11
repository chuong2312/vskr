"""Knowledge graph over timestamped video scenes and document sections.

The graph remains a local JSON artifact: sources preserve evidence, concepts
connect the modalities, and optional typed relations support multi-hop search.
"""
from collections import Counter, defaultdict, deque
import re

from .bm25 import tokenize


_STOP_WORDS = set(
    "a an and are as at be been bằng các cái cho có của cùng cũng đã đang để đến "
    "được đây đó do dùng gì khi không là lại làm lên mà một này nó những nào "
    "nội như ở phải qua ra rằng sau sẽ thì trong từ với vào và về vì "
    "the this that these those to of in on for from is was were use using "
    "what how why when where which who video đoạn phần bài học ví dụ chúng ta "
    "mình bạn tôi họ ấy nhé thôi".split()
)
_RELATION_TYPES = {
    "DEPENDS_ON", "PART_OF", "CAUSES", "USES", "DEFINES", "IS_A",
    "HAS_PROPERTY", "COMPARES_WITH", "RELATED_TO", "NEXT_SECTION",
}


def keywords(text, limit=12):
    """Return frequent non-stopword Vietnamese syllables/technical tokens."""
    counts = Counter(
        token for token in tokenize(text)
        if len(token) >= 2 and token not in _STOP_WORDS and not token.isdigit()
    )
    return [token for token, _count in counts.most_common(limit)]


def _add_edge(edges, source, target, relation, **metadata):
    key = (source, target, relation)
    edge = edges.setdefault(key, {"from": source, "to": target, "type": relation, "weight": 0})
    edge["weight"] += 1
    edge.update(metadata)


def _link_modalities(chunks, vectors, threshold=0.78, max_links=2):
    """Link document chunks to semantically similar video chunks with evidence."""
    if vectors is None or len(vectors) != len(chunks):
        return []
    videos = [i for i, chunk in enumerate(chunks) if chunk.get("video_id")]
    documents = [i for i, chunk in enumerate(chunks) if chunk.get("document_id")]
    links = []
    for doc_i in documents:
        similarities = [(float(vectors[doc_i] @ vectors[video_i]), video_i) for video_i in videos]
        similarities.sort(reverse=True)
        accepted = 0
        doc_terms = set(keywords(chunks[doc_i]["text"], 32))
        for score, video_i in similarities:
            if score < threshold or accepted >= max_links:
                break
            video_terms = set(keywords(chunks[video_i]["text"], 32))
            shared = sorted(doc_terms & video_terms)
            # Require semantic similarity plus a lexical anchor, or a stronger
            # embedding match when the sources use different surface forms.
            if not shared and score < min(0.95, threshold + 0.1):
                continue
            links.append({
                "document_chunk": str(chunks[doc_i]["chunk_id"]),
                "video_chunk": str(chunks[video_i]["chunk_id"]),
                "score": round(score, 6),
                "shared_concepts": shared,
                "verified_by": "embedding_threshold_and_concept_overlap",
            })
            accepted += 1
    return links


def build_graph(chunks, max_keywords_per_segment=12, vectors=None,
                link_threshold=0.78, max_links=2, relation_extractor=None):
    """Build scene/document nodes, concept edges, optional typed facts and links."""
    nodes = {}
    edges = {}
    chunk_nodes = {}
    ordered_documents = defaultdict(list)
    for chunk in chunks:
        source_types = chunk.get("source_types") or ["speech"]
        is_document = "doc" in source_types or chunk.get("source_type") == "doc"
        source_id = str(chunk["chunk_id"])
        source_node_id = f"source:{source_id}"
        chunk_nodes[source_id] = source_node_id
        nodes[source_node_id] = {
            "id": source_node_id,
            "kind": "document_section" if is_document else ("video_scene_segment" if chunk.get("scene_id") else "video_segment"),
            "chunk_id": source_id,
            "video_id": chunk.get("video_id"),
            "document_id": chunk.get("document_id"),
            "section": chunk.get("section"),
            "scene_id": chunk.get("scene_id"),
            "title": chunk.get("title"),
            "start": chunk.get("start"),
            "end": chunk.get("end"),
            "text": chunk["text"],
        }
        if chunk.get("scene_id"):
            scene_node_id = f"scene:{chunk['scene_id']}"
            nodes.setdefault(scene_node_id, {
                "id": scene_node_id, "kind": "video_scene", "video_id": chunk.get("video_id"),
                "scene_id": chunk["scene_id"], "start": chunk.get("scene_start", chunk.get("start")),
                "end": chunk.get("scene_end", chunk.get("end")),
            })
            _add_edge(edges, source_node_id, scene_node_id, "PART_OF_SCENE")
        if is_document:
            ordered_documents[str(chunk.get("document_id"))].append((int(chunk.get("section") or 0), source_node_id))
        terms = keywords(chunk["text"], max_keywords_per_segment)
        concept_ids = []
        for term in terms:
            concept_id = f"concept:{term}"
            nodes.setdefault(concept_id, {"id": concept_id, "kind": "concept", "label": term})
            concept_ids.append(concept_id)
            _add_edge(edges, concept_id, source_node_id, "MENTIONS")
        for i, left in enumerate(concept_ids):
            for right in concept_ids[i + 1:]:
                a, b = sorted((left, right))
                _add_edge(edges, a, b, "CO_OCCURS")

        if relation_extractor:
            text_norm = re.sub(r"\s+", " ", chunk["text"]).casefold()
            for relation in relation_extractor(chunk) or []:
                head = str(relation.get("head", "")).strip()
                tail = str(relation.get("tail", "")).strip()
                label = str(relation.get("relation", "RELATED_TO")).strip().upper().replace(" ", "_")
                evidence = re.sub(r"\s+", " ", str(relation.get("evidence", ""))).strip()
                if (not head or not tail or label not in _RELATION_TYPES or not evidence or
                        re.sub(r"\s+", " ", evidence).casefold() not in text_norm):
                    continue
                head_norm, tail_norm = head.casefold(), tail.casefold()
                if head_norm not in text_norm or tail_norm not in text_norm:
                    continue
                head_id, tail_id = f"concept:{head_norm}", f"concept:{tail_norm}"
                nodes.setdefault(head_id, {"id": head_id, "kind": "concept", "label": head})
                nodes.setdefault(tail_id, {"id": tail_id, "kind": "concept", "label": tail})
                _add_edge(edges, head_id, source_node_id, "MENTIONS")
                _add_edge(edges, tail_id, source_node_id, "MENTIONS")
                _add_edge(edges, head_id, tail_id, label, evidence=evidence,
                          evidence_chunk_id=source_id, verified=True)

    for doc_id, sections in ordered_documents.items():
        sections.sort()
        for (_, previous), (_, current) in zip(sections, sections[1:]):
            _add_edge(edges, previous, current, "NEXT_SECTION")

    for link in _link_modalities(chunks, vectors, threshold=link_threshold, max_links=max_links):
        doc_source = chunk_nodes[link["document_chunk"]]
        video_source = chunk_nodes[link["video_chunk"]]
        _add_edge(edges, doc_source, video_source, "VIDEO_DOCUMENT_MATCH",
                  score=link["score"], shared_concepts=link["shared_concepts"],
                  verified_by=link["verified_by"], verified=True)
    return {"version": 2, "nodes": list(nodes.values()), "edges": list(edges.values())}


def search_graph(graph, chunks, question, top_k=5, expansion_weight=0.55, max_hops=3,
                semantic_terms=None, idf_weights=None, ppr=False, damping=0.5,
                seed_weight=0.05):
    """Traverse query concepts through typed relations and cross-modal links.

    The result shape matches the existing vector/BM25 retrievers, preserving
    the current answer generation and timestamp citation behavior.
    """
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    query_terms = list(dict.fromkeys(keywords(question, limit=32)))
    query_tokens = set(tokenize(question))
    normalized_question = " ".join(tokenize(question))
    anchor_weights = {
        f"concept:{term}": 1.0 for term in query_terms if f"concept:{term}" in nodes
    }
    for node_id, node in nodes.items():
        if node.get("kind") != "concept" or node_id in anchor_weights:
            continue
        label_tokens = set(tokenize(node.get("label", "")))
        phrase = " ".join(tokenize(node.get("label", "")))
        if (phrase and phrase in normalized_question) or (len(label_tokens) > 1 and label_tokens <= query_tokens):
            anchor_weights[node_id] = 1.0
    for label, similarity in (semantic_terms or {}).items():
        concept_id = f"concept:{label}"
        if concept_id in nodes and concept_id not in anchor_weights:
            anchor_weights[concept_id] = max(0.0, min(1.0, float(similarity)))
    if not anchor_weights:
        return []

    adjacency = defaultdict(list)
    for edge in graph.get("edges", []):
        left, right = edge.get("from"), edge.get("to")
        if left in nodes and right in nodes:
            adjacency[left].append((right, edge))
            # Graph retrieval is evidence discovery, so traverse typed relations
            # in both directions while retaining their direction in the graph.
            adjacency[right].append((left, edge))

    source_depth = {}
    matched_by_source = defaultdict(dict)
    queue = deque((anchor, anchor, 0) for anchor in anchor_weights)
    visited = {(anchor, anchor) for anchor in anchor_weights}
    while queue:
        current, anchor, depth = queue.popleft()
        if depth >= max_hops:
            continue
        for neighbor, edge in adjacency[current]:
            next_depth = depth + 1
            if edge.get("type") == "CO_OCCURS" and next_depth > 2:
                continue
            key = (neighbor, anchor)
            if key not in visited:
                visited.add(key)
                queue.append((neighbor, anchor, next_depth))
            kind = nodes.get(neighbor, {}).get("kind")
            if kind == "document_section" or kind in ("video_segment", "video_scene_segment"):
                previous = source_depth.get(neighbor)
                if previous is None or next_depth < previous:
                    source_depth[neighbor] = next_depth
                term = anchor.removeprefix("concept:")
                matched_by_source[neighbor][term] = max(
                    matched_by_source[neighbor].get(term, 0.0), anchor_weights[anchor]
                )

    ppr_scores = {}
    if ppr and source_depth:
        personalization = {}
        anchor_mass = 1.0
        direct_sources = [node_id for node_id, depth in source_depth.items() if depth == 1]
        if direct_sources:
            anchor_mass = 1.0 - seed_weight
            for source_id in direct_sources:
                personalization[source_id] = seed_weight / len(direct_sources)
        anchor_total = sum(anchor_weights.values()) or 1.0
        for anchor, weight in anchor_weights.items():
            personalization[anchor] = personalization.get(anchor, 0.0) + anchor_mass * weight / anchor_total

        relevant_nodes = {node_id for node_id, _anchor in visited}
        neighbors = {node_id: set() for node_id in relevant_nodes}
        for node_id in relevant_nodes:
            neighbors[node_id].update(other for other, _edge in adjacency.get(node_id, []) if other in relevant_nodes)
        ranks = {node_id: personalization.get(node_id, 0.0) for node_id in relevant_nodes}
        for _ in range(40):
            updated = {node_id: (1.0 - damping) * personalization.get(node_id, 0.0) for node_id in relevant_nodes}
            dangling = 0.0
            for node_id, rank in ranks.items():
                linked = neighbors.get(node_id, set())
                if not linked:
                    dangling += rank
                    continue
                share = damping * rank / len(linked)
                for neighbor in linked:
                    updated[neighbor] += share
            if dangling:
                for node_id, value in personalization.items():
                    updated[node_id] += damping * dangling * value
            difference = sum(abs(updated.get(node_id, 0.0) - ranks.get(node_id, 0.0)) for node_id in relevant_nodes)
            ranks = updated
            if difference < 1e-9:
                break
        ppr_scores = ranks

    chunks_by_id = {
        str(chunk["chunk_id"]): (i, chunk) for i, chunk in enumerate(chunks)
    }
    ranked = []
    for source_node_id, depth in source_depth.items():
        source = nodes.get(source_node_id, {})
        item = chunks_by_id.get(str(source.get("chunk_id")))
        if item is not None:
            idx, chunk = item
            matched = matched_by_source[source_node_id]
            score = expansion_weight ** max(0, depth - 1)
            if matched:
                score *= sum(matched.values()) / len(matched)
            if idf_weights and matched:
                query_idfs = [idf_weights.get(node_id.removeprefix("concept:"), 0.0) for node_id in anchor_weights]
                mean_query_idf = sum(query_idfs) / len(query_idfs) if query_idfs else 0.0
                mean_match_idf = sum(idf_weights.get(term, 0.0) for term in matched) / len(matched)
                score *= 1.0 + min(1.0, mean_match_idf / mean_query_idf) if mean_query_idf else 1.0
            ranked.append((idx, chunk, score, int(depth == 1), matched))
    if ppr:
        ranked.sort(key=lambda row: (-ppr_scores.get(f"source:{row[1]['chunk_id']}", 0.0), -row[2], -row[3], row[1].get("start") or 0, row[0]))
    else:
        ranked.sort(key=lambda row: (-row[2], -row[3], row[1].get("start") or 0, row[0]))
    return [
        {"idx": idx, "score": round(ppr_scores.get(f"source:{chunk['chunk_id']}", score) if ppr else score, 6), "chunk": chunk,
         "matched_concepts": sorted(concepts)}
        for idx, chunk, score, _direct, concepts in ranked[:top_k]
    ]
