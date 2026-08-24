from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import chromadb

from app.config import get_settings
from app.rag.backend import QueryResult


def _parse_chroma_url(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or "localhost", parsed.port or 8000


class DirectChromaBackend:
    """RagBackend implementation using chromadb's synchronous HttpClient,
    wrapped with asyncio.to_thread so it satisfies the async RagBackend
    interface without blocking the event loop."""

    def __init__(self) -> None:
        settings = get_settings()
        host, port = _parse_chroma_url(settings.chroma_url)
        self._client = chromadb.HttpClient(host=host, port=port)

    def _collection(self, name: str):
        return self._client.get_or_create_collection(name)

    async def heartbeat(self) -> bool:
        def _check() -> bool:
            try:
                self._client.heartbeat()
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_check)

    async def upsert(
        self, collection: str, ids: list[str], documents: list[str], metadatas: list[dict]
    ) -> None:
        def _do() -> None:
            self._collection(collection).upsert(ids=ids, documents=documents, metadatas=metadatas)

        await asyncio.to_thread(_do)

    async def query(
        self, collection: str, query_text: str, where: dict | None, k: int
    ) -> QueryResult:
        def _do() -> QueryResult:
            result = self._collection(collection).query(
                query_texts=[query_text], n_results=k, where=where or None
            )
            return QueryResult(
                ids=result["ids"][0],
                documents=result["documents"][0],
                metadatas=result["metadatas"][0],
                distances=result["distances"][0],
            )

        return await asyncio.to_thread(_do)

    async def delete(self, collection: str, ids: list[str]) -> None:
        def _do() -> None:
            self._collection(collection).delete(ids=ids)

        await asyncio.to_thread(_do)
