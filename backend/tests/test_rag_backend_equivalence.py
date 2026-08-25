import uuid

from app.rag.direct_client import DirectChromaBackend
from app.rag.mcp_client import McpChromaBackend

FIXTURE_DOCS = [
    ("d1", "The quick brown fox jumps over the lazy dog.", {"topic": "animals"}),
    ("d2", "Quarterly revenue increased by twelve percent.", {"topic": "finance"}),
    ("d3", "The mitochondria is the powerhouse of the cell.", {"topic": "biology"}),
]


async def _seed(backend, collection: str) -> None:
    await backend.upsert(
        collection,
        ids=[d[0] for d in FIXTURE_DOCS],
        documents=[d[1] for d in FIXTURE_DOCS],
        metadatas=[d[2] for d in FIXTURE_DOCS],
    )


async def test_mcp_and_direct_backends_return_equivalent_top_result(drop_chroma_collection):
    suffix = uuid.uuid4().hex[:8]
    direct_collection = f"test_equiv_direct_{suffix}"
    mcp_collection = f"test_equiv_mcp_{suffix}"
    ids = [d[0] for d in FIXTURE_DOCS]

    direct = DirectChromaBackend()
    mcp = McpChromaBackend()
    try:
        await _seed(direct, direct_collection)
        await _seed(mcp, mcp_collection)

        direct_result = await direct.query(
            direct_collection, "cell biology powerhouse", where=None, k=1
        )
        mcp_result = await mcp.query(mcp_collection, "cell biology powerhouse", where=None, k=1)

        assert direct_result["ids"][0] == "d3"
        assert mcp_result["ids"][0] == "d3"
        assert direct_result["ids"][0] == mcp_result["ids"][0]
    finally:
        await direct.delete(direct_collection, ids=ids)
        await mcp.delete(mcp_collection, ids=ids)
        await mcp.aclose()
        drop_chroma_collection(direct_collection)
        drop_chroma_collection(mcp_collection)


async def test_mcp_and_direct_backends_agree_on_where_filtering(drop_chroma_collection):
    suffix = uuid.uuid4().hex[:8]
    direct_collection = f"test_equiv_direct_{suffix}"
    mcp_collection = f"test_equiv_mcp_{suffix}"
    ids = [d[0] for d in FIXTURE_DOCS]

    direct = DirectChromaBackend()
    mcp = McpChromaBackend()
    try:
        await _seed(direct, direct_collection)
        await _seed(mcp, mcp_collection)

        direct_result = await direct.query(
            direct_collection, "any topic", where={"topic": "finance"}, k=5
        )
        mcp_result = await mcp.query(mcp_collection, "any topic", where={"topic": "finance"}, k=5)

        assert direct_result["ids"] == ["d2"]
        assert mcp_result["ids"] == ["d2"]
    finally:
        await direct.delete(direct_collection, ids=ids)
        await mcp.delete(mcp_collection, ids=ids)
        await mcp.aclose()
        drop_chroma_collection(direct_collection)
        drop_chroma_collection(mcp_collection)


async def test_mcp_and_direct_backends_agree_on_empty_where(drop_chroma_collection):
    # Both backends must treat where={} identically to where=None (no
    # filter applied). DirectChromaBackend already normalizes this
    # (`where or None`); McpChromaBackend must do the same, since
    # chroma-mcp's chroma_query_documents tool rejects a bare `{}` outright.
    suffix = uuid.uuid4().hex[:8]
    direct_collection = f"test_equiv_direct_{suffix}"
    mcp_collection = f"test_equiv_mcp_{suffix}"
    ids = [d[0] for d in FIXTURE_DOCS]

    direct = DirectChromaBackend()
    mcp = McpChromaBackend()
    try:
        await _seed(direct, direct_collection)
        await _seed(mcp, mcp_collection)

        direct_result = await direct.query(
            direct_collection, "cell biology powerhouse", where={}, k=1
        )
        mcp_result = await mcp.query(mcp_collection, "cell biology powerhouse", where={}, k=1)

        assert direct_result["ids"][0] == "d3"
        assert mcp_result["ids"][0] == "d3"
    finally:
        await direct.delete(direct_collection, ids=ids)
        await mcp.delete(mcp_collection, ids=ids)
        await mcp.aclose()
        drop_chroma_collection(direct_collection)
        drop_chroma_collection(mcp_collection)
