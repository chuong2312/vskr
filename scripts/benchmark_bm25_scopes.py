"""Benchmark BM25 scoring over synthetic chunks and permission scopes."""

import argparse
import json
import platform
import statistics
import time
from types import SimpleNamespace

from vskr.retrieval.bm25 import BM25, tokenize
from vskr.retrieval.retriever import Retriever


def measure(index, scopes, query_tokens, repeats):
    retriever = Retriever(index, None, {})
    elapsed = []
    for _ in range(repeats):
        started = time.perf_counter()
        for scope in scopes:
            mask = retriever._allowed_mask(scope)
            index.bm25.scores(query_tokens, allowed_docs=mask)
        elapsed.append(time.perf_counter() - started)
    return {
        "queries_per_repeat": len(scopes),
        "repeat_seconds": [round(value, 6) for value in elapsed],
        "median_seconds": round(statistics.median(elapsed), 6),
        "median_ms_per_query": round(statistics.median(elapsed) * 1000 / len(scopes), 4),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=int, default=10_000)
    parser.add_argument("--scopes", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.chunks < args.scopes or args.scopes < 1 or args.repeats < 1:
        parser.error("Require chunks >= scopes >= 1 and repeats >= 1.")

    chunks = [
        {"video_id": f"video-{index}", "text": f"sharedtopic physics lesson concept{index}"}
        for index in range(args.chunks)
    ]
    started = time.perf_counter()
    bm25 = BM25([tokenize(chunk["text"]) for chunk in chunks])
    build_seconds = time.perf_counter() - started
    index = SimpleNamespace(chunks=chunks, bm25=bm25)
    scope_width = max(1, args.chunks // args.scopes)
    permission_scopes = [
        {f"video-{position * scope_width + offset}" for offset in range(scope_width)
         if position * scope_width + offset < args.chunks}
        for position in range(args.scopes)
    ]
    query_tokens = tokenize("sharedtopic physics")
    one_scope = measure(index, [permission_scopes[0]] * args.scopes, query_tokens, args.repeats)
    many_scopes = measure(index, permission_scopes, query_tokens, args.repeats)
    print(json.dumps({
        "python": platform.python_version(),
        "chunks": args.chunks,
        "scope_count": args.scopes,
        "scope_width": scope_width,
        "queries_per_repeat": args.scopes,
        "bm25_build_seconds": round(build_seconds, 6),
        "one_reused_permission_scope": one_scope,
        "distinct_permission_scopes": many_scopes,
        "note": "Synthetic worst-case common query term; timings are local and not production estimates.",
    }, indent=2))


if __name__ == "__main__":
    main()
