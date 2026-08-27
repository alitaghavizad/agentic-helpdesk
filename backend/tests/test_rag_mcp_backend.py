import uuid

import pytest

from app.rag.mcp_client import McpChromaBackend


@pytest.fixture(autouse=True)
def _traced_run(cleanup_run):
    """McpChromaBackend now wraps every MCP call in a tracing span (Task 3),
    and span() hard-requires an active run (see app/tracing/spans.py's
    module docstring) -- it raises RuntimeError otherwise. None of this
    file's tests started a run before tracing existed, so without this
    fixture every test below would break, not just the new spans test.
    Autouse + module-local keeps this invisible to each test's own body:
    tests that don't care about spans get a run for free (its spans, if
    any, are cleaned up here); the dedicated spans test below starts its
    own inner run on top of this one (contextvars nest correctly) so it
    can assert on exactly its own run's trace."""
    from app.db.models import RunStatus, RunTrigger
    from app.tracing import end_run, start_run

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        yield
        end_run(handle, status=RunStatus.OK)
    except Exception:
        end_run(handle, status=RunStatus.ABORTED)
        raise
    finally:
        cleanup_run(handle.run_id)


@pytest.fixture()
async def mcp_backend():
    backend = McpChromaBackend()
    try:
        yield backend
    finally:
        await backend.aclose()


async def test_mcp_backend_heartbeat(mcp_backend):
    assert await mcp_backend.heartbeat() is True


async def test_mcp_backend_upsert_and_query_roundtrip(mcp_backend, drop_chroma_collection):
    collection = f"test_mcp_{uuid.uuid4().hex[:8]}"
    ids = ["c1", "c2"]
    try:
        await mcp_backend.upsert(
            collection,
            ids=ids,
            documents=["The sky is blue and vast.", "Bananas are a yellow fruit."],
            metadatas=[{"topic": "sky"}, {"topic": "fruit"}],
        )
        result = await mcp_backend.query(collection, "What color is the sky?", where=None, k=1)
        assert result["ids"][0] == "c1"
        assert result["metadatas"][0]["topic"] == "sky"
    finally:
        await mcp_backend.delete(collection, ids=ids)
        drop_chroma_collection(collection)


async def test_mcp_backend_query_respects_where_filter(mcp_backend, drop_chroma_collection):
    collection = f"test_mcp_{uuid.uuid4().hex[:8]}"
    ids = ["c1", "c2"]
    try:
        await mcp_backend.upsert(
            collection,
            ids=ids,
            documents=["Alpha document about cats.", "Beta document about cats too."],
            metadatas=[{"owner": "alice"}, {"owner": "bob"}],
        )
        result = await mcp_backend.query(collection, "Tell me about cats", where={"owner": "bob"}, k=5)
        assert result["ids"] == ["c2"]
    finally:
        await mcp_backend.delete(collection, ids=ids)
        drop_chroma_collection(collection)


async def test_mcp_backend_query_with_empty_where_does_not_raise(mcp_backend, drop_chroma_collection):
    # chroma-mcp's chroma_query_documents tool rejects a bare `where={}` with
    # "Expected where to have exactly one operator, got {}". RBAC's
    # retrieval_filter() returns {} (no restriction) for admin/privileged
    # lookups on several collections, so query() must normalize an empty
    # dict the same way DirectChromaBackend.query() does (`where or None`)
    # -- an empty filter should behave identically to no filter at all.
    collection = f"test_mcp_{uuid.uuid4().hex[:8]}"
    ids = ["c1", "c2"]
    try:
        await mcp_backend.upsert(
            collection,
            ids=ids,
            documents=["The sky is blue and vast.", "Bananas are a yellow fruit."],
            metadatas=[{"topic": "sky"}, {"topic": "fruit"}],
        )
        result = await mcp_backend.query(collection, "What color is the sky?", where={}, k=1)
        assert result["ids"][0] == "c1"
    finally:
        await mcp_backend.delete(collection, ids=ids)
        drop_chroma_collection(collection)


async def test_mcp_backend_upsert_raises_when_delete_errors(mcp_backend):
    # "ab" is too short to be a valid Chroma collection name (Chroma requires
    # 3-63 characters), so chroma_create_collection fails server-side with
    # is_error=True -- which upsert()'s bare `except Exception: pass` around
    # that call doesn't catch (it never raises, so there's nothing to catch)
    # and the collection is never actually created. The subsequent
    # chroma_delete_documents call then targets a nonexistent collection,
    # which chroma-mcp also reports as a normal (non-raising) is_error=True
    # result rather than an exception. upsert() must check that result and
    # raise, rather than silently falling through to chroma_add_documents.
    with pytest.raises(RuntimeError, match="chroma_delete_documents failed"):
        await mcp_backend.upsert("ab", ids=["x"], documents=["doc"], metadatas=[{}])


async def test_mcp_backend_delete_raises_on_error(mcp_backend):
    # Same triggering technique as
    # test_mcp_backend_upsert_raises_when_delete_errors above: "ab" is too
    # short to be a valid Chroma collection name, so chroma_delete_documents
    # against it fails server-side with is_error=True (a non-raising
    # result). Unlike upsert() (which checks is_error on both of its calls
    # to chroma_delete_documents/chroma_add_documents), delete() previously
    # discarded the result without checking is_error, silently swallowing a
    # genuine delete failure. It must now raise.
    with pytest.raises(RuntimeError, match="chroma_delete_documents failed"):
        await mcp_backend.delete("ab", ids=["x"])


async def test_mcp_backend_upsert_is_idempotent_by_id(mcp_backend, drop_chroma_collection):
    collection = f"test_mcp_{uuid.uuid4().hex[:8]}"
    ids = ["c1"]
    try:
        await mcp_backend.upsert(collection, ids=ids, documents=["Original text."], metadatas=[{"v": 1}])
        await mcp_backend.upsert(collection, ids=ids, documents=["Updated text."], metadatas=[{"v": 2}])
        result = await mcp_backend.query(collection, "text", where=None, k=1)
        assert result["documents"][0] == "Updated text."
        assert result["metadatas"][0]["v"] == 2
    finally:
        await mcp_backend.delete(collection, ids=ids)
        drop_chroma_collection(collection)


async def test_mcp_backend_upsert_and_query_record_mcp_spans(mcp_backend, cleanup_run, drop_chroma_collection):
    from app.db.models import RunTrigger, SpanKind
    from app.tracing import end_run, start_run, trace_tree

    collection = f"test_mcp_spans_{uuid.uuid4().hex[:8]}"
    ids = ["c1"]
    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        await mcp_backend.upsert(collection, ids=ids, documents=["hello"], metadatas=[{"k": "v"}])
        await mcp_backend.query(collection, "hello", where=None, k=1)
        await mcp_backend.delete(collection, ids=ids)
        end_run(handle, status="ok")

        trace = trace_tree(handle.run_id)
        span_names = [node.span.name for node in trace.roots]
        span_kinds = {node.span.kind for node in trace.roots}
        assert span_kinds == {SpanKind.MCP}
        assert span_names == [
            "chroma_mcp.chroma_create_collection",
            "chroma_mcp.chroma_delete_documents",
            "chroma_mcp.chroma_add_documents",
            "chroma_mcp.chroma_query_documents",
            "chroma_mcp.chroma_delete_documents",
        ]
        assert all(node.span.duration_ms is not None and node.span.duration_ms >= 0 for node in trace.roots)
    finally:
        cleanup_run(handle.run_id)
        # Without this the collection leaks into shared Chroma on every run,
        # unlike the four sibling tests above which all drop theirs. Left
        # unfixed since Phase 4 it had accumulated 45 orphaned
        # test_mcp_spans_* collections, and that churn was the standing
        # explanation for the retrieval gate's "flakiness".
        drop_chroma_collection(collection)
