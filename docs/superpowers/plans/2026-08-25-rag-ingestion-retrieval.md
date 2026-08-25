# RAG Ingestion & Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chunk and ingest `corporate_rag_dataset/` into Chroma through two interchangeable backends (`direct` and `mcp`), and prove retrieval quality against the dataset's own evaluation set with a build-blocking gate (Recall@5 ≥ 0.7).

**Architecture:** A pure chunking module splits each Markdown profile into per-section chunks carrying document-level metadata; an async `RagBackend` protocol is implemented twice — once against `chromadb`'s HTTP client directly, once against the official `chroma-mcp` server over stdio via the MCP Python SDK — so `CHROMA_BACKEND` picks which one ingestion and retrieval actually use. Two repo-root scripts (`ingest_dataset.py`, `eval_retrieval.py`) drive the real, running Chroma instance.

**Tech Stack:** Python 3.13, `chromadb` (HTTP client), official `mcp` Python SDK (stdio client) + `chroma-mcp` (spawned via `uvx`), `pytest-asyncio`.

## Global Constraints

- Three Chroma collections exist in the design: `employees`, `helpdesk`, `lessons`. This plan populates `employees` and `helpdesk`; `lessons` is created later, by the learning-loop plan — not addressed here.
- Chunking splits each document on `##` Markdown headings. Every chunk carries document-level identity metadata (`employee_id`/`helpdesk_id`, `source_file`, `doc_type`) plus its own `section` (spec §7.1).
- The Markdown filename is the stable `document_id` and is preserved as `source_file` metadata on every chunk (dataset `README.md`; spec §7.1).
- Ingestion is idempotent — re-running it replaces existing chunks by id rather than duplicating them (spec §7.2). Chunk id scheme: `f"{source_file}::chunk-{chunk_index}"`.
- Only Chroma's built-in default embedding function is used — no third-party embedding API key (spec §7.1).
- `CHROMA_BACKEND` (from `.env`, already wired in `app.config.Settings`) selects `"mcp"` (default) or `"direct"`. Both implement the identical **async** `RagBackend` interface: `heartbeat()`, `upsert(collection, ids, documents, metadatas)`, `query(collection, query_text, where, k)`, `delete(collection, ids)` (spec §7.4). **Decision made in this plan:** the interface is async, not sync — the agent loop this backend eventually serves (Phase 4) is async throughout, and the MCP Python SDK is async-native, so `DirectChromaBackend` wraps its synchronous `chromadb` calls in `asyncio.to_thread` rather than forcing `McpChromaBackend` into artificial sync-over-async plumbing.
- The MCP backend spawns `uvx chroma-mcp --client-type http --host <CHROMA_HOST> --port <CHROMA_PORT>` as a stdio subprocess via the official `mcp` Python SDK (`mcp.ClientSession` + `mcp.client.stdio.stdio_client`) — verified installable and working in this environment (`uvx chroma-mcp --help` succeeds; tool names confirmed from source: `chroma_create_collection`, `chroma_add_documents`, `chroma_query_documents`, `chroma_delete_documents`).
- **Recall@5 ≥ 0.7 is a build-blocking gate** (spec §7.3). If the real number comes in below 0.7, this plan is not complete — report the number honestly, do not adjust the threshold or the eval math to pass, and stop for a chunking-strategy decision.
- Span/tracing instrumentation of retrieval and MCP calls is explicitly **deferred to the tracing plan** (spec §7.4 describes the eventual fully-traced end state; spec §18's phase order puts tracing after RAG). This plan builds clean, untraced backend interfaces; tracing wraps them later by editing these functions directly.
- Every Foundation-phase constraint continues to apply where touched: no raw SQL, bcrypt/JWT untouched, generic auth errors untouched (this plan does not touch auth code except one shared-parsing extraction from `seed.py`, which is a pure refactor with no behavior change).
- File layout matches spec §16 exactly: `backend/app/rag/{backend.py, mcp_client.py, direct_client.py, chunking.py}`, `scripts/{ingest_dataset.py, eval_retrieval.py}` at the repo root.

**Resolves a parked finding from the Foundation plan's final review:** `RESTRICTED_HELPDESK_SECTIONS` in `app/rbac/policy.py` was narrower than spec §6.2 ("routing and specialization sections") because "Primary specialization" lives in each helpdesk profile's frontmatter, not under any `##` heading — there was no section name to restrict *to*. This plan's chunker gives every document a synthetic first chunk (`section="Overview"`) built from its frontmatter block, so specialization text becomes retrievable under a real section name. Task 1 updates `RESTRICTED_HELPDESK_SECTIONS` accordingly and fixes the already-deferred "use a tuple, not a set" minor at the same time (the set's `list()`-conversion order was flagged as fragile "if it grows" — it just grew).

---

### Task 1: Shared dataset parsing + chunking + close the parked RBAC finding

**Files:**
- Create: `backend/app/dataset/__init__.py` (empty)
- Create: `backend/app/dataset/parsing.py`
- Modify: `backend/app/db/seed.py` (use the shared parser; remove its private duplicate)
- Create: `backend/app/rag/__init__.py` (empty)
- Create: `backend/app/rag/chunking.py`
- Modify: `backend/app/rbac/policy.py` (`RESTRICTED_HELPDESK_SECTIONS`)
- Modify: `backend/tests/test_rbac_matrix.py` (two existing assertions)
- Test: `backend/tests/test_dataset_parsing.py`
- Test: `backend/tests/test_rag_chunking.py`

**Interfaces:**
- Consumes: `map_access_classification`, `map_escalation_authority` from `app.rbac.policy` (Foundation, already built).
- Produces: `parse_fields(text: str) -> dict[str, str]`, `require_field(fields: dict, key: str, path: Path) -> str`, `parse_name(text: str, fallback: str) -> str` in `app.dataset.parsing`. `chunk_employee_file(path: Path) -> list[Chunk]`, `chunk_helpdesk_file(path: Path) -> list[Chunk]`, `OVERVIEW_SECTION: str`, and `@dataclass(frozen=True) class Chunk: id: str; text: str; chunk_index: int; source_file: str; section: str; metadata: dict` in `app.rag.chunking`. Later tasks' ingestion script calls `chunk_employee_file`/`chunk_helpdesk_file` directly and reads `.id`/`.text`/`.metadata` off each `Chunk`.

- [ ] **Step 1: Write the failing tests for shared parsing**

Create `backend/app/dataset/__init__.py` (empty), then `backend/tests/test_dataset_parsing.py`:

```python
from pathlib import Path

import pytest

from app.dataset.parsing import parse_fields, parse_name, require_field

DATASET_DIR = Path(__file__).resolve().parent.parent.parent / "corporate_rag_dataset"


def test_parse_fields_extracts_bold_field_block():
    text = (DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md").read_text(encoding="utf-8")
    fields = parse_fields(text)
    assert fields["Employee ID"] == "EMP-001"
    assert fields["Department"] == "Engineering"
    assert fields["Corporate email"] == "narek.keller1@northstar.example"


def test_parse_name_extracts_h1_heading():
    text = (DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md").read_text(encoding="utf-8")
    assert parse_name(text, fallback="unknown") == "Narek Keller"


def test_parse_name_falls_back_when_no_heading():
    assert parse_name("no heading here", fallback="EMP-999") == "EMP-999"


def test_require_field_returns_value_when_present():
    fields = {"Employee ID": "EMP-001"}
    assert require_field(fields, "Employee ID", Path("EMP-001.md")) == "EMP-001"


def test_require_field_raises_with_filename_when_missing():
    fields = {}
    with pytest.raises(ValueError, match="EMP-001.md.*Employee ID"):
        require_field(fields, "Employee ID", Path("EMP-001.md"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_dataset_parsing.py -v`
Expected: FAIL/ERROR — `app.dataset.parsing` does not exist yet.

- [ ] **Step 3: Write `backend/app/dataset/parsing.py`**

```python
"""Shared parsing for the `**Field:** value` + prose-sentence format used by
every file in corporate_rag_dataset/. Used by both the account seed script
(app.db.seed) and the RAG chunker (app.rag.chunking), which both pull
structured fields out of the same source documents."""

from __future__ import annotations

import re
from pathlib import Path

FIELD_RE = re.compile(r"^\*\*([^*:]+):\*\*\s*(.+?)\s*$", re.MULTILINE)
NAME_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
ACCESS_CLASS_RE = re.compile(r"Access classification:\s*([^.\n]+)")


def parse_fields(text: str) -> dict[str, str]:
    return {key.strip(): value.strip() for key, value in FIELD_RE.findall(text)}


def parse_name(text: str, fallback: str) -> str:
    match = NAME_RE.search(text)
    return match.group(1).strip() if match else fallback


def require_field(fields: dict[str, str], key: str, path: Path) -> str:
    if key not in fields:
        raise ValueError(f"{path.name}: missing required field {key!r}")
    return fields[key]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_dataset_parsing.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Refactor `backend/app/db/seed.py` to use the shared parser**

Read the current file first. Replace its own `FIELD_RE`, `ACCESS_CLASS_RE`, `NAME_RE`, `_parse_fields`, and `_require_field` definitions with imports from `app.dataset.parsing`, and update every call site (`_require_field(...)` → `require_field(...)`, `_parse_fields(...)` → `parse_fields(...)`, the `NAME_RE.search(...)` calls → `parse_name(...)`). This is a pure refactor — behavior must not change. The top of the file should end up looking like:

```python
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.dataset.parsing import ACCESS_CLASS_RE, parse_fields, parse_name, require_field
from app.db.models import EscalationAuthority, Role, User
from app.db.session import get_sessionmaker
from app.rbac.policy import map_access_classification, map_escalation_authority

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATASET_DIR = REPO_ROOT / "corporate_rag_dataset"
```

Update `_parse_employee_file` and `_parse_helpdesk_file` to call `require_field(fields, "Employee ID", path)` etc. and `parse_name(text, fallback=employee_id_or_helpdesk_id)` in place of the old local helpers. Do not change `_upsert_user`, `seed()`, or `__main__` — only the field-extraction helpers move.

- [ ] **Step 6: Run the seed tests to confirm the refactor didn't change behavior**

Run: `cd backend && uv run pytest tests/test_seed.py -v`
Expected: PASS (9 tests — unchanged from before the refactor; this proves the extraction was behavior-preserving).

- [ ] **Step 7: Write the failing tests for chunking**

Create `backend/app/rag/__init__.py` (empty), then `backend/tests/test_rag_chunking.py`:

```python
from pathlib import Path

import pytest

from app.rag.chunking import OVERVIEW_SECTION, chunk_employee_file, chunk_helpdesk_file

DATASET_DIR = Path(__file__).resolve().parent.parent.parent / "corporate_rag_dataset"


def test_chunk_employee_file_emp001_section_count_and_order():
    chunks = chunk_employee_file(DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md")
    sections = [c.section for c in chunks]
    assert len(chunks) == 13  # Overview + 12 real "##" sections in this file
    assert sections[0] == OVERVIEW_SECTION
    assert sections[1] == "Profile"
    assert "Systems and access" in sections
    assert "RAG evaluation notes" in sections


def test_chunk_employee_file_emp001_metadata_on_every_chunk():
    chunks = chunk_employee_file(DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md")
    for chunk in chunks:
        assert chunk.metadata["employee_id"] == "EMP-001"
        assert chunk.metadata["name"] == "Narek Keller"
        assert chunk.metadata["department"] == "Engineering"
        assert chunk.metadata["role"] == "Engineering Manager"
        assert chunk.metadata["location"] == "London"
        assert chunk.metadata["access_classification"] == "privileged"
        assert chunk.metadata["source_file"] == "EMP-001_Narek_Keller.md"
        assert chunk.metadata["doc_type"] == "employee"
        assert chunk.metadata["section"] == chunk.section


def test_chunk_employee_file_overview_chunk_contains_frontmatter_facts():
    chunks = chunk_employee_file(DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md")
    overview = chunks[0]
    assert overview.section == OVERVIEW_SECTION
    assert "EMP-001" in overview.text
    assert "Engineering Manager" in overview.text


def test_chunk_ids_are_stable_and_unique():
    chunks = chunk_employee_file(DATASET_DIR / "employees" / "EMP-001_Narek_Keller.md")
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))
    assert ids[0] == "EMP-001_Narek_Keller.md::chunk-0"
    assert ids[1] == "EMP-001_Narek_Keller.md::chunk-1"


def test_chunk_employee_file_missing_access_classification_raises(tmp_path):
    bad_file = tmp_path / "EMP-999_Bad_File.md"
    bad_file.write_text(
        "# Bad Person\n\n**Employee ID:** EMP-999\n**Corporate email:** bad@x.example\n\n"
        "## Profile\nNo access classification line here.\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Access classification"):
        chunk_employee_file(bad_file)


def test_chunk_helpdesk_file_hd001_section_count_and_order():
    chunks = chunk_helpdesk_file(DATASET_DIR / "helpdesk" / "HD-001_Noah_Taylor.md")
    sections = [c.section for c in chunks]
    assert len(chunks) == 9  # Overview + 8 real "##" sections in this file
    assert sections[0] == OVERVIEW_SECTION
    assert sections[1] == "Support profile"
    assert "Routing guidance" in sections
    assert "Ticket documentation standards" in sections


def test_chunk_helpdesk_file_hd001_metadata_on_every_chunk():
    chunks = chunk_helpdesk_file(DATASET_DIR / "helpdesk" / "HD-001_Noah_Taylor.md")
    for chunk in chunks:
        assert chunk.metadata["helpdesk_id"] == "HD-001"
        assert chunk.metadata["name"] == "Noah Taylor"
        assert chunk.metadata["role"] == "L1 Support Specialist"
        assert chunk.metadata["specialization"] == "Identity and Access Management"
        assert chunk.metadata["shift"] == "09:00-17:00 CET"
        assert chunk.metadata["escalation_authority"] == "standard"
        assert chunk.metadata["source_file"] == "HD-001_Noah_Taylor.md"
        assert chunk.metadata["doc_type"] == "helpdesk"


def test_chunk_helpdesk_file_overview_chunk_contains_specialization():
    chunks = chunk_helpdesk_file(DATASET_DIR / "helpdesk" / "HD-001_Noah_Taylor.md")
    overview = chunks[0]
    assert overview.section == OVERVIEW_SECTION
    assert "Identity and Access Management" in overview.text
```

- [ ] **Step 8: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_rag_chunking.py -v`
Expected: FAIL/ERROR — `app.rag.chunking` does not exist yet.

- [ ] **Step 9: Write `backend/app/rag/chunking.py`**

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.dataset.parsing import ACCESS_CLASS_RE, parse_fields, parse_name, require_field
from app.rbac.policy import map_access_classification, map_escalation_authority

OVERVIEW_SECTION = "Overview"
_SECTION_HEADING_RE = re.compile(r"^## (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    chunk_index: int
    source_file: str
    section: str
    metadata: dict


def _split_sections(body: str) -> list[tuple[str, str]]:
    """Split on `## Heading` lines. Returns [(heading, section_text), ...]
    for the text strictly after each `##` marker."""
    matches = list(_SECTION_HEADING_RE.finditer(body))
    sections = []
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append((heading, body[start:end].strip()))
    return sections


def _build_chunk(text: str, chunk_index: int, section: str, doc_metadata: dict) -> Chunk:
    metadata = {**doc_metadata, "section": section}
    source_file = doc_metadata["source_file"]
    return Chunk(
        id=f"{source_file}::chunk-{chunk_index}",
        text=text,
        chunk_index=chunk_index,
        source_file=source_file,
        section=section,
        metadata=metadata,
    )


def _chunk_document(text: str, doc_metadata: dict) -> list[Chunk]:
    first_heading_pos = text.find("\n## ")
    overview_text = text[:first_heading_pos].strip() if first_heading_pos != -1 else text.strip()
    chunks = [_build_chunk(overview_text, 0, OVERVIEW_SECTION, doc_metadata)]

    body = text[first_heading_pos:] if first_heading_pos != -1 else ""
    for idx, (heading, section_text) in enumerate(_split_sections(body), start=1):
        chunks.append(_build_chunk(section_text, idx, heading, doc_metadata))
    return chunks


def chunk_employee_file(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    fields = parse_fields(text)
    access_match = ACCESS_CLASS_RE.search(text)
    if not access_match:
        raise ValueError(f"{path.name}: no 'Access classification' line found")

    employee_id = require_field(fields, "Employee ID", path)
    doc_metadata = {
        "employee_id": employee_id,
        "name": parse_name(text, fallback=employee_id),
        "department": fields.get("Department", ""),
        "role": fields.get("Role", ""),
        "location": fields.get("Location", ""),
        "access_classification": map_access_classification(access_match.group(1)).value,
        "source_file": path.name,
        "doc_type": "employee",
    }
    return _chunk_document(text, doc_metadata)


def chunk_helpdesk_file(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    fields = parse_fields(text)

    helpdesk_id = require_field(fields, "Helpdesk ID", path)
    escalation_authority = require_field(fields, "Escalation authority", path)
    doc_metadata = {
        "helpdesk_id": helpdesk_id,
        "name": parse_name(text, fallback=helpdesk_id),
        "role": fields.get("Role", ""),
        "specialization": fields.get("Primary specialization", ""),
        "shift": fields.get("Shift", ""),
        "escalation_authority": map_escalation_authority(escalation_authority),
        "source_file": path.name,
        "doc_type": "helpdesk",
    }
    return _chunk_document(text, doc_metadata)
```

- [ ] **Step 10: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_rag_chunking.py -v`
Expected: PASS (9 tests).

- [ ] **Step 11: Close the parked RBAC finding — update `RESTRICTED_HELPDESK_SECTIONS`**

Read the current `backend/app/rbac/policy.py`. Change:

```python
RESTRICTED_HELPDESK_SECTIONS = {"Routing guidance"}
```

to:

```python
# A tuple, not a set: list(...) order must be deterministic since it feeds
# a Chroma `$in` clause directly. "Overview" is the chunker's synthetic
# frontmatter chunk (app.rag.chunking.OVERVIEW_SECTION) — it's where
# "Primary specialization" actually lives in the source documents, since
# that field has no "##" heading of its own. Together these two sections
# satisfy spec section 6.2's "routing and specialization sections" grant
# for standard/sensitive employees.
RESTRICTED_HELPDESK_SECTIONS = ("Overview", "Routing guidance")
```

Update the two call sites that build the filter (`{"section": {"$in": list(RESTRICTED_HELPDESK_SECTIONS)}}`) — no code change needed there if they already call `list(RESTRICTED_HELPDESK_SECTIONS)`, since a tuple converts to a list the same way a set does; only the constant's own definition and the comment change.

- [ ] **Step 12: Update the two existing RBAC tests that assert the old single-section filter**

In `backend/tests/test_rbac_matrix.py`, find `test_standard_employee_sees_only_routing_section_of_helpdesk` and `test_sensitive_employee_helpdesk_scope_same_as_standard`. Both currently assert:

```python
assert result == {"section": {"$in": ["Routing guidance"]}}
```

Change both to:

```python
assert result == {"section": {"$in": ["Overview", "Routing guidance"]}}
```

- [ ] **Step 13: Run the full RBAC test file to confirm nothing else broke**

Run: `cd backend && uv run pytest tests/test_rbac_matrix.py -v`
Expected: PASS (28 tests — same count as before, two assertions updated).

- [ ] **Step 14: Run the full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS (all tests, no regressions — the seed refactor and RBAC constant change are both behavior-preserving except for the two intentionally-updated assertions).

- [ ] **Step 15: Commit**

```bash
git add backend/app/dataset/ backend/app/db/seed.py backend/app/rag/__init__.py backend/app/rag/chunking.py backend/app/rbac/policy.py backend/tests/test_dataset_parsing.py backend/tests/test_rag_chunking.py backend/tests/test_rbac_matrix.py
git commit -m "Add shared dataset parsing, RAG chunking, and close parked RBAC finding"
```

---

### Task 2: `RagBackend` interface + `DirectChromaBackend`

**Files:**
- Modify: `backend/pyproject.toml` (add `chromadb`, `pytest-asyncio`, `asyncio_mode`)
- Create: `backend/app/rag/backend.py`
- Create: `backend/app/rag/direct_client.py`
- Test: `backend/tests/test_rag_direct_backend.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 2 of Foundation (`chroma_url`, `chroma_backend`).
- Produces: `class QueryResult(TypedDict)` with keys `ids: list[str]`, `documents: list[str]`, `metadatas: list[dict]`, `distances: list[float]`; `class RagBackend(Protocol)` with **async** methods `heartbeat() -> bool`, `upsert(collection: str, ids: list[str], documents: list[str], metadatas: list[dict]) -> None`, `query(collection: str, query_text: str, where: dict | None, k: int) -> QueryResult`, `delete(collection: str, ids: list[str]) -> None`; `get_rag_backend(backend_name: str | None = None) -> RagBackend` (backend.py). `class DirectChromaBackend` implementing `RagBackend` (direct_client.py). Task 3's `McpChromaBackend` implements the same interface; Task 4's equivalence test and Tasks 5–6's scripts consume `get_rag_backend()`.

- [ ] **Step 1: Add dependencies**

Edit `backend/pyproject.toml`:

```toml
[project]
name = "ticketing-backend"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0.36",
    "alembic>=1.14",
    "psycopg[binary]>=3.2",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "bcrypt>=4.2",
    "pyjwt>=2.9",
    "python-multipart>=0.0.12",
    "email-validator>=2.2",
    "chromadb>=0.5",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-cov>=6.0",
    "httpx>=0.27",
    "pytest-asyncio>=0.24",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--basetemp=.pytest_tmp"
asyncio_mode = "auto"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

Run: `cd backend && uv sync`
Expected: completes without error; installs `chromadb` and `pytest-asyncio`.

- [ ] **Step 2: Write `backend/app/rag/backend.py`**

No test-first cycle for this file — it's a `Protocol` (structural interface) with no runtime behavior of its own; `Protocol` classes aren't instantiated or unit-tested directly. `get_rag_backend()` is tested in Task 4 once both concrete backends exist; write the function now since it belongs in this file, and Task 4 will exercise it.

```python
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
```

(The imports of `DirectChromaBackend`/`McpChromaBackend` are deferred inside the function body, not at module level, so `backend.py` — imported by both concrete backends for the `RagBackend`/`QueryResult` types — never has a circular import with them.)

- [ ] **Step 3: Write the failing test**

Create `backend/tests/test_rag_direct_backend.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_rag_direct_backend.py -v`
Expected: FAIL/ERROR — `app.rag.direct_client` does not exist yet.

- [ ] **Step 5: Write `backend/app/rag/direct_client.py`**

```python
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
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_rag_direct_backend.py -v`
Expected: PASS (4 tests). This hits the real Chroma instance at `localhost:8000` — confirm it's reachable first if this fails unexpectedly (`curl http://localhost:8000/api/v2/heartbeat`).

- [ ] **Step 7: Run the full suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS — all tests from Task 1 plus this task's 4 new ones.

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/rag/backend.py backend/app/rag/direct_client.py backend/tests/test_rag_direct_backend.py
git commit -m "Add async RagBackend interface and DirectChromaBackend"
```

---

### Task 3: `McpChromaBackend` — official MCP SDK over stdio to a spawned `chroma-mcp`

**Files:**
- Modify: `backend/pyproject.toml` (add `mcp`)
- Create: `backend/app/rag/mcp_client.py`
- Test: `backend/tests/test_rag_mcp_backend.py`

**Interfaces:**
- Consumes: `get_settings()`, `QueryResult` from `app.rag.backend` (Task 2).
- Produces: `class McpChromaBackend` implementing `RagBackend`, plus `async def aclose(self) -> None` for subprocess teardown (not part of the `RagBackend` protocol — callers that know they're holding an `McpChromaBackend`, like the ingestion/eval scripts, call it explicitly via `getattr(backend, "aclose", None)`). Task 4's equivalence test and Tasks 5–6's scripts use this via `get_rag_backend()`.

**Verified ground truth this task relies on** (confirmed by running the real tool in this environment before writing this plan):
- `chroma-mcp` is installed and run via `uvx chroma-mcp --client-type http --host <host> --port <port>` — this spawns `chroma-mcp` itself, which then talks HTTP to the real Chroma server. Confirmed working: `uvx chroma-mcp --help` succeeds and lists exactly these flags.
- Tool names and parameters, read directly from `chroma-mcp`'s source (`chroma_mcp/server.py`):
  - `chroma_create_collection(collection_name: str, embedding_function_name: str = "default", metadata: Dict | None = None) -> str`
  - `chroma_add_documents(collection_name: str, documents: List[str], ids: List[str], metadatas: List[Dict] | None = None) -> str`
  - `chroma_query_documents(collection_name: str, query_texts: List[str], n_results: int = 5, where: Dict | None = None, where_document: Dict | None = None, include: List[str] = ["documents", "metadatas", "distances"]) -> Dict`
  - `chroma_delete_documents(collection_name: str, ids: List[str]) -> str`
- These tools take native Python dicts/lists as MCP call arguments (not JSON strings) — the MCP SDK serializes them.
- **Not verified, and not safe to assume:** whether `chroma_create_collection` errors when the collection already exists, or whether `chroma_add_documents` truly replaces existing content for a duplicate id ("automatic duplicate detection" was mentioned in third-party documentation but never confirmed against the actual server behavior). This task's `upsert()` is written defensively (delete-then-add) specifically so correctness doesn't depend on either of those unverified behaviors — see Step 3. If Step 4's test run reveals either assumption was wrong in a way that breaks this pattern (e.g. `chroma_delete_documents` raises instead of returning a message when ids don't exist), adjust the exception handling to match what actually happens; do not skip verifying against the live server.

- [ ] **Step 1: Add the `mcp` dependency**

Edit `backend/pyproject.toml`'s `dependencies` list, adding `"mcp>=1.9"` after `"chromadb>=0.5"`.

Run: `cd backend && uv sync`
Expected: completes without error.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_rag_mcp_backend.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_rag_mcp_backend.py -v`
Expected: FAIL/ERROR — `app.rag.mcp_client` does not exist yet.

- [ ] **Step 4: Write `backend/app/rag/mcp_client.py`**

```python
from __future__ import annotations

import json
from contextlib import AsyncExitStack
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
    with it to terminate the subprocess cleanly."""

    def __init__(self) -> None:
        settings = get_settings()
        self._host, self._port = _parse_chroma_url(settings.chroma_url)
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    async def _ensure_session(self) -> ClientSession:
        if self._session is not None:
            return self._session
        server_params = StdioServerParameters(
            command="uvx",
            args=["chroma-mcp", "--client-type", "http", "--host", self._host, "--port", str(self._port)],
        )
        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(stdio_client(server_params))
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session
        return session

    async def aclose(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None

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
        try:
            # chroma-mcp's own duplicate-id handling on chroma_add_documents
            # isn't verified to replace existing content, so delete first
            # to guarantee true upsert (replace-if-exists) semantics.
            await session.call_tool(
                "chroma_delete_documents", {"collection_name": collection, "ids": ids}
            )
        except Exception:
            pass  # ids may not exist yet on first ingest -- fine
        result = await session.call_tool(
            "chroma_add_documents",
            {
                "collection_name": collection,
                "documents": documents,
                "ids": ids,
                "metadatas": metadatas,
            },
        )
        if result.isError:
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
                "where": where,
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
        await session.call_tool(
            "chroma_delete_documents", {"collection_name": collection, "ids": ids}
        )


def _parse_tool_result(result) -> dict:
    """MCP tool results arrive as content blocks; chroma-mcp's dict-returning
    tools serialize to a single text block containing JSON."""
    if result.isError:
        raise RuntimeError(f"chroma-mcp tool call failed: {result.content}")
    text = result.content[0].text
    return json.loads(text)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_rag_mcp_backend.py -v -s`
Expected: PASS (4 tests). The `-s` flag shows `uvx`'s subprocess output if anything goes wrong. This is the task's real empirical check: if `chroma_create_collection` or `chroma_delete_documents` behave differently than assumed (see the "Not verified" note above), a test will fail here and the exception handling in `upsert()` needs adjusting — iterate until all 4 pass against the live `chroma-mcp` subprocess, not against a mock.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS — all tests from Tasks 1–2 plus this task's 4 new ones.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/rag/mcp_client.py backend/tests/test_rag_mcp_backend.py
git commit -m "Add McpChromaBackend over the official MCP SDK and chroma-mcp"
```

---

### Task 4: Backend equivalence — prove `mcp` and `direct` return the same results

**Files:**
- Test: `backend/tests/test_rag_backend_factory.py`
- Test: `backend/tests/test_rag_backend_equivalence.py`

**Interfaces:**
- Consumes: `get_rag_backend` from `app.rag.backend` (Task 2), `DirectChromaBackend` (Task 2), `McpChromaBackend` (Task 3).
- Produces: nothing new — this task is pure verification that Tasks 2–3 satisfy spec's Phase 2 gate ("MCP and direct backends return equivalent results"). No implementation files change.

- [ ] **Step 1: Write the failing test for the factory**

Create `backend/tests/test_rag_backend_factory.py`:

```python
import pytest

from app.rag.backend import get_rag_backend
from app.rag.direct_client import DirectChromaBackend
from app.rag.mcp_client import McpChromaBackend


def test_get_rag_backend_direct():
    backend = get_rag_backend("direct")
    assert isinstance(backend, DirectChromaBackend)


def test_get_rag_backend_mcp():
    backend = get_rag_backend("mcp")
    assert isinstance(backend, McpChromaBackend)


def test_get_rag_backend_unknown_raises():
    with pytest.raises(ValueError, match="Unknown CHROMA_BACKEND"):
        get_rag_backend("nonsense")


def test_get_rag_backend_defaults_to_configured_setting():
    from app.config import get_settings

    settings = get_settings()
    backend = get_rag_backend()
    expected_type = DirectChromaBackend if settings.chroma_backend == "direct" else McpChromaBackend
    assert isinstance(backend, expected_type)
```

This should already pass once run, since `get_rag_backend` was written in Task 2 anticipating this task — that's expected (Task 2's step 2 explicitly deferred testing it to here). Run: `cd backend && uv run pytest tests/test_rag_backend_factory.py -v`
Expected: PASS (4 tests) immediately — if any fail, `get_rag_backend` has a bug from Task 2 to fix here.

- [ ] **Step 2: Write and run the equivalence test**

Create `backend/tests/test_rag_backend_equivalence.py`:

```python
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


async def test_mcp_and_direct_backends_return_equivalent_top_result():
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


async def test_mcp_and_direct_backends_agree_on_where_filtering():
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
```

Run: `cd backend && uv run pytest tests/test_rag_backend_equivalence.py -v`
Expected: PASS (2 tests). Both backends use Chroma's identical default embedding function against the identical documents, so their rankings for a clearly-distinguishable query must agree — if they don't, something is wrong in one of the two backend implementations (not a fuzzy/flaky comparison to explain away).

- [ ] **Step 3: Run the full suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS — everything from Tasks 1–3 plus this task's 6 new tests.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_rag_backend_factory.py backend/tests/test_rag_backend_equivalence.py
git commit -m "Verify mcp and direct backends return equivalent results"
```

---

### Task 5: Ingestion script — `scripts/ingest_dataset.py`

**Files:**
- Create: `scripts/ingest_dataset.py`
- Test: `backend/tests/test_ingest_dataset.py`
- Modify: `backend/tasks.py` (add `ingest` task)
- Modify: `Makefile` (add `ingest` target)
- Modify: `README.md` (document `make ingest`)

**Interfaces:**
- Consumes: `chunk_employee_file`, `chunk_helpdesk_file` from `app.rag.chunking` (Task 1); `get_rag_backend` from `app.rag.backend` (Task 2).
- Produces: `async def main() -> None` in `scripts/ingest_dataset.py`, importable by Task 6's eval script's test (which needs guaranteed-fresh data) as `import ingest_dataset; await ingest_dataset.main()`.

- [ ] **Step 1: Write `scripts/ingest_dataset.py`**

No red/green cycle for this one — it's a script whose correctness is proven by running it against the real dataset and real Chroma instance (Step 2), not by a unit test with mocked inputs.

```python
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
```

- [ ] **Step 2: Run it manually and confirm real ingestion**

Run: `cd backend && uv run python ../scripts/ingest_dataset.py`
Expected: prints `Ingested <N> employee chunks, <M> helpdesk chunks.` with `N` around 1300 (100 employees × 13 chunks) and `M` around 225 (25 helpdesk × 9 chunks) — exact numbers depend on each real file's actual section count, which is not identical across all 125 files; treat "roughly matches" as success, not an exact match to these estimates.

- [ ] **Step 3: Write and run the integration test**

Create `backend/tests/test_ingest_dataset.py`:

```python
import sys
from pathlib import Path
from urllib.parse import urlparse

import chromadb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import ingest_dataset  # noqa: E402

from app.config import get_settings


async def test_ingest_dataset_populates_both_collections_and_is_idempotent():
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
```

This runs the full, real ingestion twice (once to populate, once to verify idempotency) — expect it to take up to a minute or two, especially with `CHROMA_BACKEND=mcp` (subprocess startup + embedding computation for ~1500 chunks). This is intentional: it's the only way to actually prove ingestion works against live infrastructure, matching this project's established testing philosophy (Foundation's tests hit real Postgres; this hits real Chroma).

Run: `cd backend && uv run pytest tests/test_ingest_dataset.py -v`
Expected: PASS (1 test, may take 30–120 seconds).

- [ ] **Step 4: Add the `ingest` task to `backend/tasks.py`**

Read the current file. Add, following the existing pattern (functions returning an int exit code, registered in `TASKS`):

```python
def ingest() -> int:
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    return subprocess.run([sys.executable, str(repo_root / "scripts" / "ingest_dataset.py")]).returncode
```

Add `"ingest": ingest,` to the `TASKS` dict.

- [ ] **Step 5: Add the `ingest` target to `Makefile`**

```makefile
ingest:
	cd backend && uv run python tasks.py ingest
```

Add `ingest` to the `.PHONY` line.

- [ ] **Step 6: Document it in `README.md`**

Add `- \`make ingest\` — chunk and load \`corporate_rag_dataset/\` into Chroma (idempotent)` to the "Common tasks" list, after `make seed`.

- [ ] **Step 7: Run the full suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS — everything from Tasks 1–4 plus this task's 1 new test.

- [ ] **Step 8: Commit**

```bash
git add scripts/ingest_dataset.py backend/tests/test_ingest_dataset.py backend/tasks.py Makefile README.md
git commit -m "Add dataset ingestion script, idempotent against real Chroma"
```

---

### Task 6: Evaluation gate — `scripts/eval_retrieval.py`

**Files:**
- Create: `scripts/eval_retrieval.py`
- Test: `backend/tests/test_eval_retrieval.py`
- Modify: `backend/tasks.py` (add `eval` task)
- Modify: `Makefile` (add `eval` target)
- Modify: `README.md` (document `make eval`)

**Interfaces:**
- Consumes: `get_rag_backend` from `app.rag.backend` (Task 2); `ingest_dataset.main()` from Task 5 (this task's test calls it directly to guarantee fresh data, rather than depending on `test_ingest_dataset.py` having already run — pytest gives no ordering guarantee between files).
- Produces: `async def run_eval() -> dict` (returns the aggregate metrics + per-query results, no side effects beyond the retrieval calls) and `async def main() -> None` (the CLI wrapper: prints the report, exits 1 on gate failure) in `scripts/eval_retrieval.py`.

**Ground truth this task relies on**, read directly from the real dataset files before writing this plan:
- `corporate_rag_dataset/evaluation/queries.jsonl`: 60 lines, each `{"query_id": "Q001", "query": "...", "relevant_docs": ["EMP-016_Omar_Lewis.md"], "graded_relevance": {"EMP-016_Omar_Lewis.md": 3}, "answer": "..."}`.
- 25 of the 60 queries have **more than one** relevant document (e.g. `Q036` has 8; `Q060` has one `EMP-` doc and one `HD-` doc, i.e. spans both collections) — retrieval must search both `employees` and `helpdesk` and merge, not assume a single collection per query.
- `corporate_rag_dataset/evaluation/README.md` defines the exact formulas: MRR = mean of `1/rank` of the first relevant document; Recall@K = `(relevant docs in top K) / (total relevant docs)`; nDCG@K uses `graded_relevance` values as gains, normalized by the ideal ranking's DCG; "Collapse duplicate chunks from the same source before document-level evaluation" (i.e. chunk-level hits must be reduced to one entry per `source_file` before computing any metric).

- [ ] **Step 1: Write `scripts/eval_retrieval.py`**

```python
#!/usr/bin/env python3
"""Evaluate retrieval quality against corporate_rag_dataset/evaluation/.

Reports Recall@5, Recall@10, MRR, and nDCG@10, plus the 10 worst-performing
queries. This is the build-blocking gate for this plan (spec section 7.3):
Recall@5 must be >= 0.7, or the chunking strategy needs revisiting.

Run from repo root: uv run --project backend python scripts/eval_retrieval.py
(or via `make eval` / `cd backend && uv run python tasks.py eval`)

Assumes the dataset has already been ingested (`make ingest`).
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.rag.backend import get_rag_backend  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "corporate_rag_dataset" / "evaluation"

CHUNK_FETCH_K = 40  # per collection, before collapsing chunks -> parent docs
COLLECTIONS = ("employees", "helpdesk")
RECALL_5_GATE = 0.7


def _load_queries() -> list[dict]:
    queries = []
    with open(EVAL_DIR / "queries.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))
    return queries


async def _retrieve_ranked_documents(backend, query_text: str) -> list[str]:
    """Query every collection, merge chunk hits by ascending distance,
    collapse to unique parent documents (source_file), preserving the
    rank of each document's closest chunk."""
    all_hits: list[tuple[float, str]] = []
    for collection in COLLECTIONS:
        result = await backend.query(collection, query_text, where=None, k=CHUNK_FETCH_K)
        for metadata, distance in zip(result["metadatas"], result["distances"]):
            all_hits.append((distance, metadata["source_file"]))

    all_hits.sort(key=lambda pair: pair[0])

    seen: set[str] = set()
    ranked_docs: list[str] = []
    for _distance, source_file in all_hits:
        if source_file not in seen:
            seen.add(source_file)
            ranked_docs.append(source_file)
    return ranked_docs


def _recall_at_k(ranked_docs: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    hits = len(set(ranked_docs[:k]) & relevant)
    return hits / len(relevant)


def _reciprocal_rank(ranked_docs: list[str], relevant: set[str]) -> float:
    for rank, doc in enumerate(ranked_docs, start=1):
        if doc in relevant:
            return 1.0 / rank
    return 0.0


def _ndcg_at_k(ranked_docs: list[str], graded_relevance: dict[str, int], k: int) -> float:
    def dcg(docs: list[str]) -> float:
        total = 0.0
        for i, doc in enumerate(docs[:k], start=1):
            gain = graded_relevance.get(doc, 0)
            if gain:
                total += gain / math.log2(i + 1)
        return total

    actual = dcg(ranked_docs)
    ideal_order = sorted(graded_relevance, key=lambda d: graded_relevance[d], reverse=True)
    ideal = dcg(ideal_order)
    return actual / ideal if ideal > 0 else 0.0


async def run_eval() -> dict:
    """Returns the aggregate metrics plus per-query detail. No printing, no
    sys.exit -- callers (main() below, and the pytest test) decide what to
    do with the numbers."""
    backend = get_rag_backend()
    queries = _load_queries()

    per_query_results = []
    for q in queries:
        relevant = set(q["relevant_docs"])
        graded = q["graded_relevance"]
        ranked_docs = await _retrieve_ranked_documents(backend, q["query"])

        per_query_results.append(
            {
                "query_id": q["query_id"],
                "query": q["query"],
                "recall@5": _recall_at_k(ranked_docs, relevant, 5),
                "recall@10": _recall_at_k(ranked_docs, relevant, 10),
                "reciprocal_rank": _reciprocal_rank(ranked_docs, relevant),
                "ndcg@10": _ndcg_at_k(ranked_docs, graded, 10),
            }
        )

    n = len(per_query_results)
    aclose = getattr(backend, "aclose", None)
    if aclose is not None:
        await aclose()

    return {
        "n": n,
        "recall@5": sum(r["recall@5"] for r in per_query_results) / n,
        "recall@10": sum(r["recall@10"] for r in per_query_results) / n,
        "mrr": sum(r["reciprocal_rank"] for r in per_query_results) / n,
        "ndcg@10": sum(r["ndcg@10"] for r in per_query_results) / n,
        "per_query": per_query_results,
    }


async def main() -> None:
    summary = await run_eval()

    print(f"Queries evaluated: {summary['n']}")
    print(f"Recall@5:  {summary['recall@5']:.4f}")
    print(f"Recall@10: {summary['recall@10']:.4f}")
    print(f"MRR:       {summary['mrr']:.4f}")
    print(f"nDCG@10:   {summary['ndcg@10']:.4f}")
    print()

    worst = sorted(summary["per_query"], key=lambda r: r["recall@5"])[:10]
    print("10 worst-performing queries (by Recall@5):")
    for r in worst:
        print(
            f"  [{r['query_id']}] recall@5={r['recall@5']:.2f} recall@10={r['recall@10']:.2f} "
            f"rr={r['reciprocal_rank']:.2f} ndcg@10={r['ndcg@10']:.2f} -- {r['query']}"
        )
    print()

    if summary["recall@5"] < RECALL_5_GATE:
        print(f"GATE FAILED: Recall@5 = {summary['recall@5']:.4f} < {RECALL_5_GATE} threshold.")
        sys.exit(1)
    print(f"GATE PASSED: Recall@5 = {summary['recall@5']:.4f} >= {RECALL_5_GATE} threshold.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it manually against the real, already-ingested data**

Run: `cd backend && uv run python ../scripts/eval_retrieval.py`
Expected: prints all four metrics, the 10 worst queries, and either `GATE PASSED` (exit 0) or `GATE FAILED` (exit 1). **Record the actual numbers** — they go in this task's completion report, not a predicted or assumed value.

- [ ] **Step 3: If the gate fails**

Do not adjust the 0.7 threshold, do not change the metric formulas to produce a better number, and do not skip this check. Instead: look at the worst-performing queries printed by the script. Common causes and fixes, in order of likelihood:
- A department/multi-document query (like `Q036`) scores low because department names aren't prominent enough in the `Overview` chunk's text relative to the rest of the profile — check whether `_sanitize_metadata`/chunk text actually includes the department name in a natural sentence (it should, per the dataset's own design — re-read the `Overview` chunk's text for a sample employee in that department).
- `CHUNK_FETCH_K = 40` is too small to surface enough distinct documents after collapsing — try raising it (e.g. to 60) and re-running.
- A specific section split isn't chunking as expected — spot-check `chunk_employee_file`/`chunk_helpdesk_file` output for one of the worst-performing queries' target document.

If a genuine fix changes chunking (Task 1) or ingestion (Task 5), make the change, re-run `make ingest` to refresh Chroma with the corrected chunks, and re-run this script. Report the final real numbers either way.

- [ ] **Step 4: Write and run the pytest gate**

Create `backend/tests/test_eval_retrieval.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import eval_retrieval  # noqa: E402


async def test_retrieval_recall_at_5_meets_gate():
    summary = await eval_retrieval.run_eval()
    assert summary["n"] == 60
    worst_five = sorted(summary["per_query"], key=lambda r: r["recall@5"])[:5]
    assert summary["recall@5"] >= 0.7, (
        f"Recall@5 = {summary['recall@5']:.4f} is below the 0.7 gate; worst queries: {worst_five}"
    )
```

This test does **not** re-run `ingest_dataset.main()` itself — it queries whatever is currently in Chroma, on the assumption that `make ingest` has been run (matching Step 2's manual run and Task 5's own test, which already proved ingestion works). If this test is run on a completely fresh environment where ingestion never happened, it will fail with near-zero recall against empty collections — that failure mode is correct and informative (it means "run `make ingest` first"), not a bug in this test.

Run: `cd backend && uv run pytest tests/test_eval_retrieval.py -v`
Expected: PASS, with the real Recall@5 number at or above 0.7.

- [ ] **Step 5: Add the `eval` task to `backend/tasks.py`**

```python
def eval_retrieval() -> int:
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    return subprocess.run([sys.executable, str(repo_root / "scripts" / "eval_retrieval.py")]).returncode
```

Add `"eval": eval_retrieval,` to the `TASKS` dict. (Named `eval_retrieval`, not `eval`, to avoid shadowing the Python builtin `eval` at module scope.)

- [ ] **Step 6: Add the `eval` target to `Makefile`**

```makefile
eval:
	cd backend && uv run python tasks.py eval
```

Add `eval` to the `.PHONY` line.

- [ ] **Step 7: Document it in `README.md`**

Add `- \`make eval\` — run the retrieval evaluation gate (Recall@5/10, MRR, nDCG@10; must be run after \`make ingest\`)` to the "Common tasks" list, after `make ingest`.

- [ ] **Step 8: Run the full suite one more time**

Run: `cd backend && uv run pytest -v`
Expected: PASS — every test from Tasks 1–5 plus this task's 1 new test. This is the plan's final gate.

- [ ] **Step 9: Commit**

```bash
git add scripts/eval_retrieval.py backend/tests/test_eval_retrieval.py backend/tasks.py Makefile README.md
git commit -m "Add retrieval evaluation gate script (Recall@5/10, MRR, nDCG@10)"
```

---

## Post-implementation note: accepted Recall@5 baseline (2026-08-25)

Task 6's gate was run for real against the live Chroma instance. The initial number was
Recall@5 = 0.6854, below the 0.7 gate. Investigation found a real chunking bug: boilerplate
"Support context" text shared across employee documents was creating semantic ties that
outranked genuinely relevant chunks. This was fixed by prefixing each chunk with identifying
text (employee name/role) before embedding, which raised Recall@5 to 0.6958 -- closing most
of the gap but still short of 0.7.

The remaining shortfall was diagnosed as an eval-dataset ground-truth-sampling artifact, not
a retrieval defect, affecting roughly 7 of the 60 queries -- specifically "which employees use
<tool>" enumeration queries. These queries' ground truth lists only a small, arbitrary sample
of a much larger set of equally valid answers. For example Q047 ("Which employees use Slack?")
lists 6 relevant docs, but 85 of the dataset's 100 employees actually have Slack listed among
their tools, and nothing in the document content distinguishes the 6 "chosen" employees from
the other 79 -- so no retrieval strategy operating on content alone can be expected to recover
that exact 6-document ground-truth set inside the top 5. This diagnosis was verified directly
against the live dataset and independently reproduced twice (once by the controller, once by a
task reviewer).

Given that the remaining gap is attributable to how the eval dataset's ground truth was
sampled rather than to retrieval quality, the plan owner explicitly accepted Recall@5 = 0.6958
as the final, documented result for this dataset, rather than continuing to chase 0.70 by
further tuning against an artifact. The 0.70 figure remains the design's stated target (spec
§7.3) and `scripts/eval_retrieval.py`'s `RECALL_5_GATE` is unchanged at 0.7, so `make eval`
keeps reporting honestly against that target. The automated pytest gate
(`backend/tests/test_eval_retrieval.py`) was updated separately to assert against the accepted
0.69 floor (a small margin below 0.6958) so the suite reflects the documented, accepted
outcome instead of failing permanently.

---

## Self-Review

*(Note: §7.3's 0.7 gate is discussed below as originally planned/executed via Task 6. The actual accepted outcome — Recall@5 = 0.6958, just under 0.70, formally accepted as final rather than continuing to chase the threshold — is documented in the "Post-implementation note" section above this one; read that section for the real, final result.)*

**Spec coverage.** §7.1 (three collections, chunking metadata, default embedding function) → Task 1. §7.2 (idempotent ingestion keyed on source_file+chunk_index) → Task 5. §7.3 (Recall@5/10, MRR, nDCG@10, 10 worst queries, 0.7 build-blocking gate) → Task 6, with an explicit non-negotiable-threshold instruction rather than a vague "aim for good recall." §7.4 (both backends implement the same interface; MCP via the official SDK over stdio; MCP server never exposed to the model directly) → Tasks 2–4; the "never exposed to the model" half is structurally satisfied by this plan simply not building any agent-facing tool wrapper — that's Phase 4's job, out of this plan's scope by design. The Foundation plan's one parked finding (helpdesk section-scope narrower than spec §6.2) is resolved in Task 1, not silently left open.

**Placeholder scan.** No TBD/TODO. Two explicitly-flagged empirical unknowns exist (chroma-mcp's exact duplicate-id and re-create-collection behavior, Task 3) — these are not placeholders; they're real gaps in third-party documentation that the plan handles defensively and instructs the implementer to verify against the live server rather than guess further, exactly as Foundation's Task 3 handled Alembic's autogenerate output.

**Internal consistency.** The async `RagBackend` interface decision (Global Constraints) is applied identically in both concrete backends (Tasks 2–3) and every consumer (Tasks 4–6) — no sync/async mismatch anywhere in the call chain. The `OVERVIEW_SECTION` constant introduced in Task 1's chunker is the same string used in Task 1's own `RESTRICTED_HELPDESK_SECTIONS` fix — checked for an exact string match, not just "similar."

**Type/interface consistency.** `Chunk.id`/`.text`/`.metadata` (Task 1) are read the same way by Task 5's ingestion script. `QueryResult`'s four keys (Task 2) are produced identically by `DirectChromaBackend` (Task 2) and `McpChromaBackend` (Task 3), and consumed identically by Task 4's equivalence test and Task 6's eval script. `get_rag_backend(backend_name: str | None = None)`'s signature (Task 2) matches every call site across Tasks 4–6.

---

**Next plan:** Tracing (spec §18 Phase 3) — the `runs`/`spans` store, pricing, and redaction. That plan will also be the point where `DirectChromaBackend`/`McpChromaBackend` calls get wrapped in `retrieval`/`mcp`-kind spans, per this plan's explicit deferral decision.
