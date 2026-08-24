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
