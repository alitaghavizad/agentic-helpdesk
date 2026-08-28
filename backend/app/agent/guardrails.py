from __future__ import annotations

import re

from app.db.models import SpanKind
from app.tracing import span

_UNTRUSTED_TEMPLATE = '<untrusted_data source="{source}" trust="none">\n{content}\n</untrusted_data>'

_DELIMITER_RE = re.compile(r"<\s*/?\s*untrusted_data", re.IGNORECASE)


def wrap_untrusted(source: str, content: str) -> str:
    """Wraps a piece of retrieved/external content per spec 12.1. Every RAG
    chunk and web-search result passed to the model goes through this --
    the system prompt states content inside these tags is information to
    reason about, never an instruction to follow.

    Neutralises the delimiter inside `content` (and `source`) before
    interpolating. Without this the wrapper is not a boundary at all: any
    content able to emit `</untrusted_data>` closes it early and everything
    after lands OUTSIDE the wrapper, where the system prompt's "this is
    data, not instruction" rule no longer applies. That content is
    attacker-influenced -- a crafted screenshot can make the parser emit an
    arbitrary string -- so this is a reachable escape, not a theoretical
    one. `source` is sanitised here too rather than trusted from the
    caller: it is built from a filename two modules away, and this
    function's integrity should not depend on that caller remembering to
    strip tag-shaped characters.

    Neutralising rather than dropping keeps faith with 12.1: the reader
    still sees what was attempted, in a form that cannot function as a
    tag."""
    safe_source = _DELIMITER_RE.sub("<!untrusted_data", source)
    safe_content = _DELIMITER_RE.sub("<!untrusted_data", content)
    return _UNTRUSTED_TEMPLATE.format(source=safe_source, content=safe_content)


# A heuristic list, not an exhaustive one -- spec 12.1 explicitly frames
# this as flagging, not blocking: flagged content still reaches the model
# (with the flag attached) so it can see and report an attempted injection,
# rather than the scanner silently deciding what the model gets to see.
_INJECTION_MARKERS = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard the above",
    "disregard previous instructions",
    "you are now",
    "new instructions:",
    "system prompt",
    "act as if",
    "pretend you are",
    "override your instructions",
    "forget everything above",
    "reveal your instructions",
    "</untrusted_data",
]
_INJECTION_RE = re.compile("|".join(re.escape(marker) for marker in _INJECTION_MARKERS), re.IGNORECASE)


def scan_for_injection(text: str) -> list[str]:
    matches = {m.group(0).lower() for m in _INJECTION_RE.finditer(text)}
    return sorted(matches)


async def check_inbound(user_message: str) -> None:
    """Scans the raw inbound user message once per turn (spec 12.1). Never
    raises and never blocks -- a flagged message is still processed
    normally; the finding is recorded for the admin trace view."""
    async with span(SpanKind.GUARDRAIL, "check_inbound") as recorder:
        findings = scan_for_injection(user_message)
        if findings:
            recorder.metadata = {"findings": findings}
