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

from app.rag.chunking import chunk_employee_file, chunk_helpdesk_file  # noqa: E402
from app.rag.backend import get_rag_backend  # noqa: E402

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


async def main() -> None:
    backend = get_rag_backend()

    employee_chunks = []
    for path in sorted((DATASET_DIR / "employees").glob("EMP-*.md")):
        employee_chunks.extend(chunk_employee_file(path))

    helpdesk_chunks = []
    for path in sorted((DATASET_DIR / "helpdesk").glob("HD-*.md")):
        helpdesk_chunks.extend(chunk_helpdesk_file(path))

    n_employees = await _ingest_collection(backend, "employees", employee_chunks)
    n_helpdesk = await _ingest_collection(backend, "helpdesk", helpdesk_chunks)

    print(f"Ingested {n_employees} employee chunks, {n_helpdesk} helpdesk chunks.", file=sys.stderr)

    aclose = getattr(backend, "aclose", None)
    if aclose is not None:
        await aclose()


if __name__ == "__main__":
    asyncio.run(main())
