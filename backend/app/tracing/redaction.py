from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

# Field names that are redacted wholesale, regardless of their value's
# shape -- a password/secret/token/api-key is never safe to persist even
# partially, so there's no attempt to redact "just the sensitive part".
_SECRET_KEY_NAME_RE = re.compile(r"pass(word)?|secret|token|api[_-]?key", re.IGNORECASE)

# API-key-shaped strings appearing inside otherwise-ordinary free text
# (span input/output is often a blob of tool arguments or LLM output, not
# a field named "api_key" -- a leaked key can show up anywhere in the text).
_API_KEY_RE = re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{10,}\b|\bAIza[A-Za-z0-9_-]{20,}\b")
_BEARER_RE = re.compile(r"\bBearer\s+\S{10,}", re.IGNORECASE)
_LONG_DIGITS_RE = re.compile(r"\b\d{9,}\b")


def redact(value: Any) -> Any:
    """Recursively redacts secrets out of a jsonb-shaped value (span
    input/output are always dict/list/str/int/float/bool/None -- the only
    shapes that occur in practice, since they came from json.loads or a
    plain Python literal in the first place)."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if _SECRET_KEY_NAME_RE.search(str(key)) else redact(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _redact_string(text: str) -> str:
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _API_KEY_RE.sub(REDACTED, text)
    text = _LONG_DIGITS_RE.sub(REDACTED, text)
    return text
