#!/usr/bin/env python3
"""Ingest corporate_rag_dataset/ into Chroma via the configured backend
(CHROMA_BACKEND from .env). Idempotent -- safe to re-run; re-ingesting
replaces existing chunks by id rather than duplicating them.

Run from repo root: uv run --project backend python scripts/ingest_dataset.py
(or via `make ingest` / `cd backend && uv run python tasks.py ingest`)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db.models import RunStatus, RunTrigger  # noqa: E402
from app.rag.chunking import (  # noqa: E402
    chunk_employee_file,
    chunk_helpdesk_file,
    drop_nondiscriminating_chunks,
)
from app.rag.backend import get_rag_backend  # noqa: E402
from app.tracing import end_run, start_run  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = REPO_ROOT / "corporate_rag_dataset"


def _sanitize_metadata(metadata: dict) -> dict:
    """Chroma metadata values must be str/int/float/bool -- drop empty/None."""
    return {k: v for k, v in metadata.items() if v not in (None, "")}


async def _ingest_collection(backend, collection: str, chunks: list) -> int:
    if not chunks:
        return 0
    await backend.upsert(
        collection,
        ids=[c.id for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[_sanitize_metadata(c.metadata) for c in chunks],
    )
    return len(chunks)


def _verify_no_stale_chunks(expected_counts: dict[str, int]) -> None:
    """Fail loudly when a collection holds more chunks than this ingestion
    produced -- i.e. leftovers from an earlier run with different chunking.

    This guard exists because the failure it catches is otherwise
    completely silent, and self-healing in the worst way. `upsert()` only
    adds or replaces, and chunk ids are `{source_file}::chunk-{index}` with
    indices NOT renumbered after filtering, so re-running this script over
    a pre-filter collection rewrites the kept chunks in place and leaves
    every dropped chunk exactly where it was -- reconstituting the old
    corpus byte for byte. Ingestion reports success. Worse, the resulting
    Recall@5 (measured: 0.6958) still clears the eval test's floor, so the
    suite reports green too, while the routing gate quietly returns to
    deciding near-ties on template boilerplate.

    Deleting the stale ids is NOT a valid remedy: Chroma's HNSW index
    degrades sharply when vectors are deleted rather than rebuilt. With
    byte-identical final content, Recall@5 measured 0.6069 after deleting
    stale ids in place versus 0.7125 after dropping the collections and
    re-ingesting from scratch. Drop the collections and re-ingest.
    """
    import chromadb

    from app.config import get_settings
    from app.rag.direct_client import _parse_chroma_url

    host, port = _parse_chroma_url(get_settings().chroma_url)
    client = chromadb.HttpClient(host=host, port=port)
    for collection, expected in expected_counts.items():
        try:
            actual = client.get_collection(collection).count()
        except Exception as exc:  # collection genuinely absent, or Chroma unreachable
            print(f"  warning: could not verify {collection!r} chunk count: {exc}", file=sys.stderr)
            continue
        if actual > expected:
            raise RuntimeError(
                f"{collection!r} holds {actual} chunks but this ingestion produced {expected}. "
                f"{actual - expected} stale chunk(s) remain from an earlier run with different "
                "chunking, which silently restores the old corpus. Drop the collection and "
                "re-ingest -- do not delete the stale ids, which degrades Chroma's HNSW index "
                "(measured Recall@5 0.6069 deleted-in-place vs 0.7125 rebuilt)."
            )


def _check_dataset_dirs() -> None:
    """Path.glob() on a nonexistent directory returns an empty iterator
    rather than raising, which would otherwise make a missing/misconfigured
    DATASET_DIR silently "succeed" with 0 chunks ingested. Fail loudly
    instead."""
    employees_dir = DATASET_DIR / "employees"
    helpdesk_dir = DATASET_DIR / "helpdesk"
    missing = [
        str(p)
        for p in (DATASET_DIR, employees_dir, helpdesk_dir)
        if not p.is_dir()
    ]
    if missing:
        raise FileNotFoundError(
            "Dataset directory/directories not found: " + ", ".join(missing)
        )


async def main() -> None:
    _check_dataset_dirs()

    backend = get_rag_backend()
    aclose = getattr(backend, "aclose", None)
    # McpChromaBackend's upsert() now wraps every MCP call in a tracing span
    # (Task 3 of docs/superpowers/plans/2026-08-25-agent.md), and span()
    # hard-requires an active run -- see app/tracing/spans.py's module
    # docstring. This script has no per-conversation "turn" of its own, so
    # it brackets its whole ingestion pass as one RunTrigger.INGEST_EVAL run
    # (that trigger value exists in app/db/models.py for exactly this kind
    # of batch job), mirroring the try/except/finally contract the tracing
    # module itself documents as mandatory for every start_run()/end_run()
    # pair.
    handle = start_run(RunTrigger.INGEST_EVAL)
    try:
        try:
            employee_chunks = []
            for path in sorted((DATASET_DIR / "employees").glob("EMP-*.md")):
                employee_chunks.extend(chunk_employee_file(path))

            helpdesk_chunks = []
            for path in sorted((DATASET_DIR / "helpdesk").glob("HD-*.md")):
                helpdesk_chunks.extend(chunk_helpdesk_file(path))

            if not employee_chunks and not helpdesk_chunks:
                raise RuntimeError(
                    "No chunks found in either "
                    f"{DATASET_DIR / 'employees'} or {DATASET_DIR / 'helpdesk'} "
                    "-- refusing to silently 'succeed' with an empty ingestion."
                )

            # Drop template scaffolding that is byte-identical across every
            # document -- it cannot discriminate between documents but does
            # crowd the top-k with a near-tied band, which is what made the
            # retrieval gate oscillate. See drop_nondiscriminating_chunks'
            # docstring for the measured before/after.
            employee_chunks, dropped_employee = drop_nondiscriminating_chunks(employee_chunks)
            helpdesk_chunks, dropped_helpdesk = drop_nondiscriminating_chunks(helpdesk_chunks)

            for collection, dropped in (("employees", dropped_employee), ("helpdesk", dropped_helpdesk)):
                if dropped:
                    print(
                        f"  {collection}: skipping {len(dropped)} non-discriminating section(s) "
                        f"(identical in every document): {', '.join(sorted(dropped))}",
                        file=sys.stderr,
                    )

            n_employees = await _ingest_collection(backend, "employees", employee_chunks)
            n_helpdesk = await _ingest_collection(backend, "helpdesk", helpdesk_chunks)

            _verify_no_stale_chunks({"employees": n_employees, "helpdesk": n_helpdesk})

            print(f"Ingested {n_employees} employee chunks, {n_helpdesk} helpdesk chunks.", file=sys.stderr)
            end_run(handle, status=RunStatus.OK)
        except Exception as exc:
            end_run(handle, status=RunStatus.ABORTED, error=str(exc))
            raise
    finally:
        if aclose is not None:
            await aclose()


if __name__ == "__main__":
    asyncio.run(main())
