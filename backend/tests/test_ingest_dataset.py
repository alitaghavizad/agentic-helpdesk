import sys
from pathlib import Path
from urllib.parse import urlparse

import chromadb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import ingest_dataset  # noqa: E402

from app.config import get_settings


async def test_ingest_dataset_missing_dataset_dir_raises(monkeypatch, tmp_path):
    # A missing/misconfigured DATASET_DIR must fail loudly rather than
    # silently "succeeding" with 0 chunks ingested (Path.glob() on a
    # nonexistent directory returns an empty iterator, not an error). The
    # check must fire before any backend/subprocess is spawned, so this
    # stays fast -- no real ingestion happens.
    monkeypatch.setattr(ingest_dataset, "DATASET_DIR", tmp_path / "does-not-exist")
    with pytest.raises(FileNotFoundError):
        await ingest_dataset.main()


async def test_ingest_dataset_closes_backend_on_chunking_failure(monkeypatch):
    # Finding 1: if chunking (or upsert) raises partway through, the
    # spawned chroma-mcp subprocess must still be torn down via aclose(),
    # not leaked. Spy on aclose() of whatever backend main() constructs, and
    # force a failure between get_rag_backend() and any upsert call by
    # making chunk_employee_file blow up on first use -- this never gets far
    # enough to actually spawn the chroma-mcp subprocess (aclose() is a
    # no-op in that case), but it does prove the exception propagates AND
    # aclose() runs on every exit path, matching the try/finally structure.
    aclose_calls = []
    original_get_rag_backend = ingest_dataset.get_rag_backend

    def _spying_get_rag_backend(*args, **kwargs):
        # Force "mcp" explicitly (independent of .env) since this is the
        # backend with a real subprocess/session to leak -- "direct" has no
        # aclose() at all.
        backend = original_get_rag_backend("mcp")
        original_aclose = backend.aclose

        async def _spying_aclose():
            aclose_calls.append(True)
            await original_aclose()

        backend.aclose = _spying_aclose
        return backend

    def _boom(path):
        raise ValueError(f"malformed dataset file: {path}")

    monkeypatch.setattr(ingest_dataset, "get_rag_backend", _spying_get_rag_backend)
    monkeypatch.setattr(ingest_dataset, "chunk_employee_file", _boom)

    with pytest.raises(ValueError, match="malformed dataset file"):
        await ingest_dataset.main()

    assert aclose_calls == [True]


async def test_ingest_dataset_populates_both_collections_and_is_idempotent(
    drop_chroma_collection,
):
    try:
        await ingest_dataset.main()

        settings = get_settings()
        parsed = urlparse(settings.chroma_url)
        client = chromadb.HttpClient(host=parsed.hostname, port=parsed.port)

        employees_count_1 = client.get_collection("employees").count()
        helpdesk_count_1 = client.get_collection("helpdesk").count()
        assert employees_count_1 > 0
        assert helpdesk_count_1 > 0

        # Re-run: idempotent, no duplication -- document counts must not change.
        await ingest_dataset.main()
        employees_count_2 = client.get_collection("employees").count()
        helpdesk_count_2 = client.get_collection("helpdesk").count()

        assert employees_count_2 == employees_count_1
        assert helpdesk_count_2 == helpdesk_count_1
    finally:
        # This test calls main() twice against the REAL employees/helpdesk
        # collections (there's no isolated test_* namespace for RAG data),
        # and main() upserts in place rather than rebuilding the index.
        # Repeated in-place upsert over byte-identical content measurably
        # degrades Chroma's HNSW graph: a clean drop-and-re-ingest measures
        # Recall@5 = 0.7125, but after this test ran (twice-upserted) in a
        # full-suite run, test_eval_retrieval's measured Recall@5 dropped to
        # ~0.5708 (or only limped to the gate by burning its retry, ~115s
        # instead of ~30s). test_eval_retrieval sorts BEFORE this file
        # alphabetically ("e" < "i"), so the contamination is not within a
        # single run -- this run's ingest degrades the index that the NEXT
        # full-suite run's eval measures, which is why it looked like
        # intermittent flakiness rather than a deterministic ordering
        # problem. Dropping both collections and re-ingesting once from
        # scratch is the only thing that was observed to restore Recall@5 to
        # 0.7125, so we leave the collections in that state regardless of
        # whether the assertions above passed.
        drop_chroma_collection("employees")
        drop_chroma_collection("helpdesk")
        await ingest_dataset.main()
