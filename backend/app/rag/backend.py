from __future__ import annotations

from typing import Protocol, TypedDict


class QueryResult(TypedDict):
    ids: list[str]
    documents: list[str]
    metadatas: list[dict]
    distances: list[float]


class RagBackend(Protocol):
    async def heartbeat(self) -> bool: ...

    async def upsert(
        self, collection: str, ids: list[str], documents: list[str], metadatas: list[dict]
    ) -> None: ...

    async def query(
        self, collection: str, query_text: str, where: dict | None, k: int
    ) -> QueryResult: ...

    async def delete(self, collection: str, ids: list[str]) -> None: ...


def get_rag_backend(backend_name: str | None = None) -> RagBackend:
    """Returns a fresh backend instance selecting on `backend_name`, or on
    `settings.chroma_backend` if not given. Deliberately uncached: callers
    that need a long-lived instance (a script running once, a future
    request-scoped dependency) hold onto the return value themselves."""
    from app.config import get_settings
    from app.rag.direct_client import DirectChromaBackend
    from app.rag.mcp_client import McpChromaBackend

    name = backend_name or get_settings().chroma_backend
    if name == "direct":
        return DirectChromaBackend()
    if name == "mcp":
        return McpChromaBackend()
    raise ValueError(f"Unknown CHROMA_BACKEND: {name!r}")
