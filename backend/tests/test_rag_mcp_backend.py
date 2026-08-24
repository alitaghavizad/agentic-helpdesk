import uuid

import pytest

from app.rag.mcp_client import McpChromaBackend


@pytest.fixture()
async def mcp_backend():
    backend = McpChromaBackend()
    try:
        yield backend
    finally:
        await backend.aclose()


async def test_mcp_backend_heartbeat(mcp_backend):
    assert await mcp_backend.heartbeat() is True


async def test_mcp_backend_upsert_and_query_roundtrip(mcp_backend):
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


async def test_mcp_backend_query_respects_where_filter(mcp_backend):
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


async def test_mcp_backend_upsert_is_idempotent_by_id(mcp_backend):
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
