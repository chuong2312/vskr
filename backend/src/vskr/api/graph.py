"""Admin graph query presentation, kept separate from request orchestration."""

from fastapi import HTTPException


def inspect_graph(service, query: str, adjacent: bool = False, idf_weighting: bool = False,
                  semantic_expansion: bool = False, ppr: bool = False,
                  damping: float = 0.5, seed_weight: float = 0.05):
    if not 0.0 <= damping <= 0.99 or not 0.0 <= seed_weight <= 1.0:
        raise HTTPException(422, "Tham số lan truyền graph phải nằm trong khoảng hợp lệ.")
    graph = service.index.graph
    if not graph.get("nodes"):
        raise HTTPException(409, "Chỉ mục chưa có Đồ thị tri thức. Hãy chạy lại: vskr index")
    hits = service.retriever.search(query, mode="graph", top_k=8, experiment={
        "idf_weighting": idf_weighting, "semantic_expansion": semantic_expansion,
        "ppr": ppr, "damping": damping, "seed_weight": seed_weight,
    })
    chunks = service.index.chunks
    hit_by_id = {hit["chunk"]["chunk_id"]: hit for hit in hits}
    source_ids = {f"source:{chunk_id}" for chunk_id in hit_by_id}
    by_video = {}
    if adjacent:
        for chunk in chunks:
            if chunk.get("video_id") and chunk.get("start") is not None:
                by_video.setdefault(chunk["video_id"], []).append(chunk)
        for group in by_video.values():
            group.sort(key=lambda chunk: chunk.get("start", 0))
            positions = {chunk["chunk_id"]: i for i, chunk in enumerate(group)}
            for chunk_id in list(hit_by_id):
                hit = hit_by_id[chunk_id]
                video_id = hit["chunk"].get("video_id")
                if not video_id or chunk_id not in positions:
                    continue
                i = positions[chunk_id]
                for j in (i - 1, i + 1):
                    if 0 <= j < len(group):
                        neighbor = group[j]
                        neighbor_id = neighbor["chunk_id"]
                        hit_by_id.setdefault(neighbor_id, {"chunk": neighbor, "score": 0.0, "matched_concepts": []})
                        source_ids.add(f"source:{neighbor_id}")
    concept_labels = {label for hit in hits for label in hit.get("matched_concepts", [])}
    concept_ids = {f"concept:{label}" for label in concept_labels}
    selected_ids = source_ids | concept_ids
    for edge in graph.get("edges", []):
        if edge.get("type") == "PART_OF_SCENE" and edge.get("from") in source_ids:
            selected_ids.add(edge["to"])
    node_scores = {f"source:{chunk_id}": hit["score"] for chunk_id, hit in hit_by_id.items()}
    nodes = []
    for node in graph.get("nodes", []):
        if node["id"] not in selected_ids:
            continue
        item = dict(node)
        if item["id"] in node_scores:
            item["score"] = node_scores[item["id"]]
            chunk = hit_by_id.get(item.get("chunk_id"), {}).get("chunk", {})
            item["title"] = item.get("title") or chunk.get("title")
            item["section"] = item.get("section") or chunk.get("section")
            if item.get("video_id"):
                item["title"] = item.get("title") or service.meta.get(item["video_id"], {}).get("title")
        nodes.append(item)
    edges = [edge for edge in graph.get("edges", []) if edge["from"] in selected_ids and edge["to"] in selected_ids]
    if adjacent:
        chunk_positions = {}
        for video_id, group in by_video.items():
            for i, chunk in enumerate(group):
                chunk_positions[(video_id, chunk["chunk_id"])] = (i, group)
        existing = {(edge["from"], edge["to"]) for edge in edges}
        for chunk_id, hit in hit_by_id.items():
            video_id = hit["chunk"].get("video_id")
            pos = chunk_positions.get((video_id, chunk_id))
            if not pos:
                continue
            i, group = pos
            for j in (i - 1, i + 1):
                if 0 <= j < len(group):
                    neighbor_id = f"source:{group[j]['chunk_id']}"
                    src_id = f"source:{chunk_id}"
                    pair = (src_id, neighbor_id)
                    if neighbor_id in source_ids and pair not in existing:
                        edges.append({"from": src_id, "to": neighbor_id, "type": "NEXT_SEGMENT", "weight": 1})
                        existing.add(pair)
    return {
        "query": query, "nodes": nodes, "edges": edges,
        "matched_concepts": sorted(concept_labels), "result_count": len(hits), "adjacent": adjacent,
        "experiments": {
            "idf_weighting": idf_weighting, "semantic_expansion": semantic_expansion,
            "ppr": ppr, "damping": damping, "seed_weight": seed_weight,
        },
        "score_name": "điểm graph có áp dụng tùy chọn thử nghiệm nếu được bật",
    }
