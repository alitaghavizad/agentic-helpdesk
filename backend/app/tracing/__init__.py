"""Run/span tracing for agent turns.

Spans are buffered in memory (see spans._pending_spans) and only reach the
database when `end_run()` runs -- they are never written incrementally as
they close, because a span's row has a plain, non-deferrable FK to its
parent, and children close before their parents do (see the comment above
`_pending_spans` in spans.py for the full reasoning).

This means a crash between `start_run()` and `end_run()` silently loses
every span buffered for that run, and leaves the run stuck in
`RunStatus.RUNNING` forever. The module does not enforce a fix for this
itself, by design, to keep the API simple -- it is the caller's
responsibility, similar to any other resource that needs closing. Callers
MUST bracket `start_run()`/`end_run()` in `try/finally`, calling
`end_run(handle, status=RunStatus.ABORTED, error=str(exc))` on the failure
path so a crash doesn't silently lose the whole run's spans:

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        ...  # spans opened via @span(...) or `async with span(...)`
        end_run(handle, status=RunStatus.OK)
    except Exception as exc:
        end_run(handle, status=RunStatus.ABORTED, error=str(exc))
        raise
"""

from __future__ import annotations

from app.tracing.context import RunHandle
from app.tracing.spans import SpanRecorder, current_span, end_run, span, start_run
from app.tracing.store import RunTrace, SpanNode, trace_tree

__all__ = [
    "span",
    "current_span",
    "start_run",
    "end_run",
    "RunHandle",
    "trace_tree",
    "RunTrace",
    "SpanNode",
    "SpanRecorder",
]
