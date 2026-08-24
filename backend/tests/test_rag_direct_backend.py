import uuid

from app.rag.direct_client import DirectChromaBackend


async def test_direct_backend_heartbeat():
    backend = DirectChromaBackend()
    assert await backend.heartbeat() is True


async def test_direct_backend_upsert_and_query_roundtrip():
    backend = DirectChromaBackend()
    collection = f"test_direct_{uuid.uuid4().hex[:8]}"
    ids = ["c1", "c2"]
    try:
        await backend.upsert(
            collection,
            ids=ids,
            documents=["The sky is blue and vast.", "Bananas are a yellow fruit."],
            metadatas=[{"topic": "sky"}, {"topic": "fruit"}],
        )
        result = await backend.query(collection, "What color is the sky?", where=None, k=1)
        assert result["ids"][0] == "c1"
        assert result["metadatas"][0]["topic"] == "sky"
    finally:
        await backend.delete(collection, ids=ids)


async def test_direct_backend_query_respects_where_filter():
    backend = DirectChromaBackend()
    collection = f"test_direct_{uuid.uuid4().hex[:8]}"
    ids = ["c1", "c2"]
    try:
        await backend.upsert(
            collection,
            ids=ids,
            documents=["Alpha document about cats.", "Beta document about cats too."],
            metadatas=[{"owner": "alice"}, {"owner": "bob"}],
        )
        result = await backend.query(collection, "Tell me about cats", where={"owner": "bob"}, k=5)
        assert result["ids"] == ["c2"]
    finally:
        await backend.delete(collection, ids=ids)


async def test_direct_backend_upsert_is_idempotent_by_id():
    backend = DirectChromaBackend()
    collection = f"test_direct_{uuid.uuid4().hex[:8]}"
    ids = ["c1"]
    try:
        await backend.upsert(collection, ids=ids, documents=["Original text."], metadatas=[{"v": 1}])
        await backend.upsert(collection, ids=ids, documents=["Updated text."], metadatas=[{"v": 2}])
        result = await backend.query(collection, "text", where=None, k=1)
        assert result["documents"][0] == "Updated text."
        assert result["metadatas"][0]["v"] == 2
    finally:
        await backend.delete(collection, ids=ids)
