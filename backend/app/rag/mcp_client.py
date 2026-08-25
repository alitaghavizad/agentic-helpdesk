from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import get_settings
from app.rag.backend import QueryResult


def _parse_chroma_url(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or "localhost", parsed.port or 8000


class McpChromaBackend:
    """RagBackend implementation that talks to a spawned `chroma-mcp`
    subprocess over stdio via the official MCP Python SDK. `chroma-mcp`
    itself connects to our real Chroma server over HTTP
    (--client-type http), so this process never talks to Chroma directly.

    The MCP session and its subprocess are started lazily, on first use,
    and reused for the lifetime of this instance. Call aclose() when done
    with it to terminate the subprocess cleanly.

    The stdio_client/ClientSession context managers are driven by anyio task
    groups, which require __aenter__ and __aexit__ to run in the same asyncio
    Task. Callers such as pytest-asyncio fixtures may run fixture setup and
    teardown (a `finally: await backend.aclose()` after the yield) in
    different Tasks, which would otherwise raise "Attempted to exit cancel
    scope in a different task than it was entered in". To make aclose() safe
    to call from any Task, the whole session lifecycle -- start, use, and
    shutdown -- runs inside a single dedicated background asyncio.Task owned
    by this instance; other methods only ever signal that task via events."""

    def __init__(self) -> None:
        settings = get_settings()
        self._host, self._port = _parse_chroma_url(settings.chroma_url)
        self._session: ClientSession | None = None
        self._lifecycle_task: asyncio.Task | None = None
        self._ready_event: asyncio.Event | None = None
        self._close_event: asyncio.Event | None = None
        self._startup_error: BaseException | None = None

    async def _run_session(self) -> None:
        server_params = StdioServerParameters(
            command="uvx",
            args=[
                "chroma-mcp",
                "--client-type",
                "http",
                "--host",
                self._host,
                "--port",
                str(self._port),
                # chroma-mcp's --ssl flag defaults to true, which makes it
                # attempt a TLS handshake against our plain-HTTP local Chroma
                # server and fail with an SSL record-layer error. Force it off.
                "--ssl",
                "false",
            ],
        )
        assert self._ready_event is not None
        assert self._close_event is not None
        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready_event.set()
                    await self._close_event.wait()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller waiting on _ready_event
            self._startup_error = exc
            self._ready_event.set()
        finally:
            self._session = None

    async def _ensure_session(self) -> ClientSession:
        if self._session is not None:
            return self._session
        if self._lifecycle_task is None:
            self._ready_event = asyncio.Event()
            self._close_event = asyncio.Event()
            self._lifecycle_task = asyncio.create_task(self._run_session())
        await self._ready_event.wait()
        if self._session is None:
            error = self._startup_error
            self._lifecycle_task = None
            self._ready_event = None
            self._close_event = None
            self._startup_error = None
            if error is not None:
                raise error
            raise RuntimeError("chroma-mcp session failed to start")
        return self._session

    async def aclose(self) -> None:
        if self._lifecycle_task is not None:
            if self._close_event is not None:
                self._close_event.set()
            await self._lifecycle_task
            self._lifecycle_task = None
            self._ready_event = None
            self._close_event = None
            self._session = None
            # A startup failure is already raised to whichever caller was
            # waiting in _ensure_session(), which -- as part of consuming it
            # -- resets _startup_error (and _lifecycle_task) to None before
            # aclose() could ever observe it. So if _startup_error is still
            # set here, it can only be a fresh, never-surfaced teardown-time
            # failure (e.g. ClientSession.__aexit__ or stdio_client.__aexit__
            # raising while shutting the subprocess down) -- surface it
            # instead of silently swallowing it.
            teardown_error = self._startup_error
            self._startup_error = None
            if teardown_error is not None:
                raise RuntimeError(
                    f"chroma-mcp session teardown failed: {teardown_error!r}"
                ) from teardown_error

    async def heartbeat(self) -> bool:
        try:
            session = await self._ensure_session()
            await session.list_tools()
            return True
        except Exception:
            return False

    async def upsert(
        self, collection: str, ids: list[str], documents: list[str], metadatas: list[dict]
    ) -> None:
        session = await self._ensure_session()
        try:
            await session.call_tool("chroma_create_collection", {"collection_name": collection})
        except Exception:
            pass  # collection may already exist -- fine
        # chroma-mcp's own duplicate-id handling on chroma_add_documents isn't
        # verified to replace existing content, so delete first to guarantee
        # true upsert (replace-if-exists) semantics. Empirically (see Task 3
        # report), deleting ids that don't exist yet does not raise and does
        # not set is_error -- ChromaDB treats delete as a no-op-safe/
        # idempotent operation on missing ids -- so we can check is_error
        # directly and treat any error here as a genuine server-side failure,
        # not a benign "ids don't exist yet on first ingest" case.
        delete_result = await session.call_tool(
            "chroma_delete_documents", {"collection_name": collection, "ids": ids}
        )
        if delete_result.is_error:
            raise RuntimeError(f"chroma_delete_documents failed: {delete_result.content}")
        result = await session.call_tool(
            "chroma_add_documents",
            {
                "collection_name": collection,
                "documents": documents,
                "ids": ids,
                "metadatas": metadatas,
            },
        )
        if result.is_error:
            raise RuntimeError(f"chroma_add_documents failed: {result.content}")

    async def query(
        self, collection: str, query_text: str, where: dict | None, k: int
    ) -> QueryResult:
        session = await self._ensure_session()
        result = await session.call_tool(
            "chroma_query_documents",
            {
                "collection_name": collection,
                "query_texts": [query_text],
                "n_results": k,
                "where": where or None,
            },
        )
        payload = _parse_tool_result(result)
        return QueryResult(
            ids=payload["ids"][0],
            documents=payload["documents"][0],
            metadatas=payload["metadatas"][0],
            distances=payload["distances"][0],
        )

    async def delete(self, collection: str, ids: list[str]) -> None:
        session = await self._ensure_session()
        result = await session.call_tool(
            "chroma_delete_documents", {"collection_name": collection, "ids": ids}
        )
        if result.is_error:
            raise RuntimeError(f"chroma_delete_documents failed: {result.content}")


def _parse_tool_result(result) -> dict:
    """MCP tool results arrive as content blocks; chroma-mcp's dict-returning
    tools serialize to a single text block containing JSON."""
    if result.is_error:
        raise RuntimeError(f"chroma-mcp tool call failed: {result.content}")
    text = result.content[0].text
    return json.loads(text)
