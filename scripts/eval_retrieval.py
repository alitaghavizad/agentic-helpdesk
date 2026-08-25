#!/usr/bin/env python3
"""Evaluate retrieval quality against corporate_rag_dataset/evaluation/.

Reports Recall@5, Recall@10, MRR, and nDCG@10, plus the 10 worst-performing
queries. This is the build-blocking gate for this plan (spec section 7.3):
Recall@5 must be >= 0.7, or the chunking strategy needs revisiting.

Run from repo root: uv run --project backend python scripts/eval_retrieval.py
(or via `make eval` / `cd backend && uv run python tasks.py eval`)

Assumes the dataset has already been ingested (`make ingest`).
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.rag.backend import get_rag_backend  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "corporate_rag_dataset" / "evaluation"

CHUNK_FETCH_K = 40  # per collection, before collapsing chunks -> parent docs
COLLECTIONS = ("employees", "helpdesk")
RECALL_5_GATE = 0.7


def _load_queries() -> list[dict]:
    queries = []
    with open(EVAL_DIR / "queries.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))
    return queries


async def _retrieve_ranked_documents(backend, query_text: str) -> list[str]:
    """Query every collection, merge chunk hits by ascending distance,
    collapse to unique parent documents (source_file), preserving the
    rank of each document's closest chunk."""
    all_hits: list[tuple[float, str]] = []
    for collection in COLLECTIONS:
        result = await backend.query(collection, query_text, where=None, k=CHUNK_FETCH_K)
        for metadata, distance in zip(result["metadatas"], result["distances"]):
            all_hits.append((distance, metadata["source_file"]))

    all_hits.sort(key=lambda pair: pair[0])

    seen: set[str] = set()
    ranked_docs: list[str] = []
    for _distance, source_file in all_hits:
        if source_file not in seen:
            seen.add(source_file)
            ranked_docs.append(source_file)
    return ranked_docs


def _recall_at_k(ranked_docs: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    hits = len(set(ranked_docs[:k]) & relevant)
    return hits / len(relevant)


def _reciprocal_rank(ranked_docs: list[str], relevant: set[str]) -> float:
    for rank, doc in enumerate(ranked_docs, start=1):
        if doc in relevant:
            return 1.0 / rank
    return 0.0


def _ndcg_at_k(ranked_docs: list[str], graded_relevance: dict[str, int], k: int) -> float:
    def dcg(docs: list[str]) -> float:
        total = 0.0
        for i, doc in enumerate(docs[:k], start=1):
            gain = graded_relevance.get(doc, 0)
            if gain:
                total += gain / math.log2(i + 1)
        return total

    actual = dcg(ranked_docs)
    ideal_order = sorted(graded_relevance, key=lambda d: graded_relevance[d], reverse=True)
    ideal = dcg(ideal_order)
    return actual / ideal if ideal > 0 else 0.0


async def run_eval() -> dict:
    """Returns the aggregate metrics plus per-query detail. No printing, no
    sys.exit -- callers (main() below, and the pytest test) decide what to
    do with the numbers."""
    backend = get_rag_backend()
    queries = _load_queries()

    per_query_results = []
    for q in queries:
        relevant = set(q["relevant_docs"])
        graded = q["graded_relevance"]
        ranked_docs = await _retrieve_ranked_documents(backend, q["query"])

        per_query_results.append(
            {
                "query_id": q["query_id"],
                "query": q["query"],
                "recall@5": _recall_at_k(ranked_docs, relevant, 5),
                "recall@10": _recall_at_k(ranked_docs, relevant, 10),
                "reciprocal_rank": _reciprocal_rank(ranked_docs, relevant),
                "ndcg@10": _ndcg_at_k(ranked_docs, graded, 10),
            }
        )

    n = len(per_query_results)
    aclose = getattr(backend, "aclose", None)
    if aclose is not None:
        await aclose()

    return {
        "n": n,
        "recall@5": sum(r["recall@5"] for r in per_query_results) / n,
        "recall@10": sum(r["recall@10"] for r in per_query_results) / n,
        "mrr": sum(r["reciprocal_rank"] for r in per_query_results) / n,
        "ndcg@10": sum(r["ndcg@10"] for r in per_query_results) / n,
        "per_query": per_query_results,
    }


async def main() -> None:
    summary = await run_eval()

    print(f"Queries evaluated: {summary['n']}")
    print(f"Recall@5:  {summary['recall@5']:.4f}")
    print(f"Recall@10: {summary['recall@10']:.4f}")
    print(f"MRR:       {summary['mrr']:.4f}")
    print(f"nDCG@10:   {summary['ndcg@10']:.4f}")
    print()

    worst = sorted(summary["per_query"], key=lambda r: r["recall@5"])[:10]
    print("10 worst-performing queries (by Recall@5):")
    for r in worst:
        print(
            f"  [{r['query_id']}] recall@5={r['recall@5']:.2f} recall@10={r['recall@10']:.2f} "
            f"rr={r['reciprocal_rank']:.2f} ndcg@10={r['ndcg@10']:.2f} -- {r['query']}"
        )
    print()

    if summary["recall@5"] < RECALL_5_GATE:
        print(f"GATE FAILED: Recall@5 = {summary['recall@5']:.4f} < {RECALL_5_GATE} threshold.")
        sys.exit(1)
    print(f"GATE PASSED: Recall@5 = {summary['recall@5']:.4f} >= {RECALL_5_GATE} threshold.")


if __name__ == "__main__":
    asyncio.run(main())
