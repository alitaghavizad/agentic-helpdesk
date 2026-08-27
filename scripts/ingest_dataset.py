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

            # NOTE -- changing which chunks get produced (e.g. the
            # non-discriminating filter above) requires DROPPING the
            # collections and re-ingesting, not just re-running this script.
            # upsert() only adds or replaces, so chunks a previous ingestion
            # wrote would otherwise linger. Deleting them by id is NOT a
            # valid substitute: Chroma's HNSW index degrades sharply when
            # vectors are deleted rather than rebuilt. Measured on this
            # dataset with byte-identical final content -- Recall@5 was
            # 0.6069 after deleting the stale ids in place, versus 0.7125
            # after dropping both collections and re-ingesting from
            # scratch. Use scripts/recreate_chroma.sh (or delete the
            # `employees`/`helpdesk` collections) before re-ingesting.

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
