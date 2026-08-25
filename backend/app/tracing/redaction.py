from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

# Field names that are redacted wholesale, regardless of their value's
# shape -- a password/secret/token/api-key is never safe to persist even
# partially, so there's no attempt to redact "just the sensitive part".
#
# Keys are split into words on snake_case/kebab-case separators AND on
# camelCase humps, so both `db_password` and `dbPassword` split into the
# same ["db", "password"] word list. Matching whole words (rather than
# substrings via a lookaround regex) is what keeps "input_tokens" (API
# usage telemetry) and "passed" (verb form) from false-positiving --
# a plain `(?<![a-z])token(?![a-z])` lookaround under IGNORECASE fails on
# camelCase, since IGNORECASE makes `[a-z]` match the uppercase hump too
# (e.g. "authToken" has a lowercase letter immediately before "Token",
# so the lookaround never fires and the secret field silently isn't
# redacted -- this is exactly the bug this word-splitting approach fixes).
_WORD_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SECRET_WORDS = frozenset({"pass", "password", "secret", "token", "apikey", "pwd"})


def _is_secret_key(key: str) -> bool:
    words = [w.lower() for w in _WORD_SPLIT_RE.split(str(key)) if w]
    if any(w in _SECRET_WORDS for w in words):
        return True
    # "api_key" / "apiKey" split into two words ("api", "key"), neither of
    # which is in _SECRET_WORDS on its own -- check the adjacent pair too.
    return any(a == "api" and b == "key" for a, b in zip(words, words[1:]))


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
            key: (REDACTED if _is_secret_key(key) else redact(val))
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
