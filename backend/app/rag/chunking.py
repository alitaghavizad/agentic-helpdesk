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
