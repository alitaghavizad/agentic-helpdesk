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
from app.rag.chunking import chunk_employee_file, chunk_helpdesk_file  # noqa: E402
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

            n_employees = await _ingest_collection(backend, "employees", employee_chunks)
            n_helpdesk = await _ingest_collection(backend, "helpdesk", helpdesk_chunks)

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
