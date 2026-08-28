from __future__ import annotations

import re

from app.db.models import SpanKind
from app.tracing import span

_UNTRUSTED_TEMPLATE = '<untrusted_data source="{source}" trust="none">\n{content}\n</untrusted_data>'

_DELIMITER_RE = re.compile(r"<\s*/?\s*untrusted_data", re.IGNORECASE)

# `source` is interpolated INSIDE a quoted attribute (source="{source}"), not
# between tags like `content` is. A `"` there closes the attribute early and
# a following `>` closes the opening tag early, so anything after them lands
# ahead of `trust="none"` as attacker-controlled tag content instead of
# quietly sitting inside a string. Stripping both closes that specific
# escape; `content` never sits inside an attribute so it does not need this.
_SOURCE_ATTR_BREAKOUT_RE = re.compile(r'["' r'>]')


def wrap_untrusted(source: str, content: str) -> str:
    """Wraps a piece of retrieved/external content per spec 12.1. Every RAG
    chunk and web-search result passed to the model goes through this --
    the system prompt states content inside these tags is information to
    reason about, never an instruction to follow.

    Two independent guarantees, because `source` and `content` sit in
    different syntactic positions in the template:

    - `content` sits BETWEEN tags. The delimiter `</untrusted_data` (any
      case/whitespace variant) is neutralised there so attacker-influenced
      text -- a crafted screenshot can make the parser emit an arbitrary
      string -- cannot close the wrapper early and escape everything after
      it into trusted territory.
    - `source` sits INSIDE the `source="..."` attribute. Beyond the same
      delimiter neutralisation, `"` and `>` are stripped: either one lets
      attacker-controlled text break out of the attribute and plant a
      spoofed tag in the opening line itself, ahead of `trust="none"`,
      which is a stronger escape than closing the wrapper late.

    Both are enforced here rather than trusted from the caller: `source` is
    built from a filename two modules away, and this function's integrity
    should not depend on that caller remembering to strip tag-shaped
    characters.

    Neutralising rather than dropping keeps faith with 12.1 for `content`:
    the reader still sees what was attempted, in a form that cannot function
    as a tag. `source` is a short caller-built label, not evidence to
    preserve, so its unsafe characters are simply removed."""
    safe_source = _DELIMITER_RE.sub("<!untrusted_data", source)
    safe_source = _SOURCE_ATTR_BREAKOUT_RE.sub("", safe_source)
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
