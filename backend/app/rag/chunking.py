from __future__ import annotations

import collections
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
    # The section's own prose, WITHOUT the identity prefix that `text`
    # carries. Kept so drop_nondiscriminating_chunks() can compare raw
    # section content across documents -- comparing `text` would find
    # everything "unique", since the prefix differs per document by
    # construction.
    raw_text: str = ""


def drop_nondiscriminating_chunks(chunks: list[Chunk]) -> tuple[list[Chunk], set[str]]:
    """Remove chunks belonging to sections whose prose is byte-identical in
    every document of the corpus, returning (kept_chunks, dropped_sections).

    Why this exists. These datasets are generated from a shared template, so
    several sections are literally the same paragraph repeated in all 25
    helpdesk (or all 100 employee) files -- "Ticket documentation
    standards", "Security and privacy behavior", "Access and authorization
    boundaries", "Device and endpoint context", "Business process
    behavior". Such a chunk cannot, even in principle, tell one document
    from another: it is the same sentence in each. But contextual chunking
    prepends a per-document identity prefix, which gives each copy a small
    amount of variance -- just enough for all N copies to land in a dense,
    near-tied band near the top of any topically-related query, crowding
    out the sections that actually identify the right document.

    Measured on this dataset: for "My MFA token stopped working after I
    replaced my phone", the correct specialist beat the runner-up by
    0.0002 -- effectively a coin flip, and it did flip between suite runs,
    which is what made the retrieval gate look "flaky". After dropping
    these chunks the same margin is 0.1616. Eval Recall@5 rose 0.7014 ->
    0.7125, and Q025 (recall@5 = 0.00, where all ten top hits were the
    identical "Security and privacy behavior" chunk from ten unrelated
    specialists) now returns the correct specialist first.

    The rule is deliberately "carries zero discriminating signal", not a
    hardcoded list of section names: it re-derives itself from whatever
    corpus is passed in, so a dataset change cannot silently leave stale
    exclusions behind. Raising the threshold above exact-duplicate was
    measured and gained nothing further (identical 0.7125 when also
    dropping sections with up to 8 distinct values), so the strictest,
    least surprising rule is the one used.

    Note this only removes template scaffolding shared by every document.
    It cannot remove a section that distinguishes any two documents, so no
    per-person or per-specialist fact is lost, and it never widens what a
    principal can retrieve -- it only shrinks the indexed corpus.
    """
    by_section: dict[str, set[str]] = collections.defaultdict(set)
    for chunk in chunks:
        by_section[chunk.section].add(chunk.raw_text)

    # A section seen in only one document is trivially "unique" and must not
    # be dropped -- with a single document there is nothing to discriminate
    # between, so duplication cannot be established.
    doc_count = len({c.source_file for c in chunks})
    dropped = {
        section for section, texts in by_section.items()
        if doc_count > 1 and len(texts) == 1
    }
    return [c for c in chunks if c.section not in dropped], dropped


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


def _build_chunk(
    text: str, chunk_index: int, section: str, doc_metadata: dict, identity_prefix: str = ""
) -> Chunk:
    metadata = {**doc_metadata, "section": section}
    source_file = doc_metadata["source_file"]
    return Chunk(
        id=f"{source_file}::chunk-{chunk_index}",
        text=identity_prefix + text,
        chunk_index=chunk_index,
        source_file=source_file,
        section=section,
        metadata=metadata,
        raw_text=text,
    )


def _chunk_document(text: str, doc_metadata: dict, identity_prefix: str = "") -> list[Chunk]:
    """Split `text` into section chunks. `identity_prefix` (e.g. "Employee:
    Jane Doe (EMP-042), Engineering, SRE.\n\n") is prepended to every chunk's
    embedded text, applied uniformly including Overview. This is "contextual
    chunking": it ensures every chunk carries identity signal even when a
    section's own prose is generic/templated boilerplate shared across many
    documents (e.g. helpdesk "representative issue" text), which otherwise
    causes near-duplicate embeddings and ranking ties across unrelated docs.
    """
    first_heading_pos = text.find("\n## ")
    overview_text = text[:first_heading_pos].strip() if first_heading_pos != -1 else text.strip()
    chunks = [_build_chunk(overview_text, 0, OVERVIEW_SECTION, doc_metadata, identity_prefix)]

    body = text[first_heading_pos:] if first_heading_pos != -1 else ""
    for idx, (heading, section_text) in enumerate(_split_sections(body), start=1):
        chunks.append(_build_chunk(section_text, idx, heading, doc_metadata, identity_prefix))
    return chunks


def chunk_employee_file(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    fields = parse_fields(text)
    access_match = ACCESS_CLASS_RE.search(text)
    if not access_match:
        raise ValueError(f"{path.name}: no 'Access classification' line found")

    employee_id = require_field(fields, "Employee ID", path)
    name = parse_name(text, fallback=employee_id)
    department = fields.get("Department", "")
    role = fields.get("Role", "")
    doc_metadata = {
        "employee_id": employee_id,
        "name": name,
        "department": department,
        "role": role,
        "location": fields.get("Location", ""),
        "access_classification": map_access_classification(access_match.group(1)).value,
        "source_file": path.name,
        "doc_type": "employee",
    }
    identity_prefix = f"Employee: {name} ({employee_id}), {department}, {role}.\n\n"
    return _chunk_document(text, doc_metadata, identity_prefix)


def chunk_helpdesk_file(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    fields = parse_fields(text)

    helpdesk_id = require_field(fields, "Helpdesk ID", path)
    escalation_authority = require_field(fields, "Escalation authority", path)
    name = parse_name(text, fallback=helpdesk_id)
    role = fields.get("Role", "")
    specialization = fields.get("Primary specialization", "")
    doc_metadata = {
        "helpdesk_id": helpdesk_id,
        "name": name,
        "role": role,
        "specialization": specialization,
        "shift": fields.get("Shift", ""),
        "escalation_authority": map_escalation_authority(escalation_authority),
        "source_file": path.name,
        "doc_type": "helpdesk",
    }
    identity_prefix = f"Helpdesk specialist: {name} ({helpdesk_id}), {role}, specializing in {specialization}.\n\n"
    return _chunk_document(text, doc_metadata, identity_prefix)
