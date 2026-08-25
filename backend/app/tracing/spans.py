from __future__ import annotations

import functools
import inspect
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from app.db.models import RunStatus, RunTrigger, SpanKind, SpanStatus
from app.tracing import pricing, store
from app.tracing.context import (
    RunHandle,
    SpanFrame,
    Token,
    get_current_run,
    get_current_span_frame,
    reset_current_run,
    reset_current_span_frame,
    set_current_run,
    set_current_span_frame,
)

F = TypeVar("F", bound=Callable[..., Any])

# Holds the contextvars Token from start_run's set_current_run() call,
# keyed by run_id, so end_run can reset it. A Token is only valid to
# reset from the same context (or a context copied from it) that created
# it -- fine here, since start_run/end_run are always called as a
# matching pair from the same coroutine/function (spec 8.5's loop
# pseudocode brackets a whole turn this way).
_run_tokens: dict[uuid.UUID, Token] = {}

# Spans close innermost-first (a child's exit() always runs before its
# parent's), but Span.parent_span_id is a plain, non-deferrable FK to
# spans.id -- inserting a child row the instant it closes would reference
# a parent row that doesn't exist yet and the database rejects it
# immediately. So instead of writing each span at its own exit(), we
# buffer the finished span's kwargs here, keyed by run_id, and flush them
# all in end_run() sorted by `sequence`. Sequence numbers are handed out
# at *enter* time (see _ActiveSpan.enter), and a span can only ever be
# entered after its parent has already entered, so ascending-sequence
# order is always a valid parent-before-child insert order.
_pending_spans: dict[uuid.UUID, list[dict[str, Any]]] = {}


class SpanRecorder:
    """Yielded by span()'s decorator wrapper and its async-with form alike
    (reachable from inside a decorated function via current_span()). Lets
    the code running inside a span attach model/usage/input/output before
    the span closes and gets persisted."""

    def __init__(self) -> None:
        self.model: str | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cache_read_tokens: int | None = None
        self.cache_write_tokens: int | None = None
        self.input: dict | None = None
        self.output: dict | None = None
        self.metadata: dict = {}

    def record_usage(
        self,
        *,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cache_write_tokens: int | None = None,
    ) -> None:
        if model is not None:
            self.model = model
        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens
        if cache_read_tokens is not None:
            self.cache_read_tokens = cache_read_tokens
        if cache_write_tokens is not None:
            self.cache_write_tokens = cache_write_tokens


def current_span() -> SpanRecorder | None:
    """Returns the recorder for the innermost active span, or None if no
    span is currently active. Works whether that span was opened via the
    decorator or the async-with form -- both push the same kind of frame."""
    frame = get_current_span_frame()
    return frame.recorder if frame else None


class _ActiveSpan:
    """Shared enter/exit logic for both the decorator and async-with forms
    of `span`. Not part of the public API."""

    def __init__(self, kind: SpanKind, name: str) -> None:
        self.kind = kind
        self.name = name
        self.recorder = SpanRecorder()

    def enter(self) -> SpanRecorder:
        run = get_current_run()
        if run is None:
            raise RuntimeError(
                f"span({self.kind.value!r}, {self.name!r}) used outside of an "
                "active run -- call start_run() first"
            )
        self._run = run
        parent_frame = get_current_span_frame()
        self._parent_span_id = parent_frame.span_id if parent_frame else None
        self._span_id = uuid.uuid4()
        self._sequence = run.next_sequence()
        self._started_at = datetime.now(timezone.utc)
        self._frame_token = set_current_span_frame(SpanFrame(span_id=self._span_id, recorder=self.recorder))
        return self.recorder

    def exit(self, exc: BaseException | None) -> None:
        ended_at = datetime.now(timezone.utc)
        duration_ms = int((ended_at - self._started_at).total_seconds() * 1000)
        status = SpanStatus.ERROR if exc is not None else SpanStatus.OK
        error_text = f"{type(exc).__name__}: {exc}" if exc is not None else None

        cost = None
        if self.recorder.model is not None:
            cost = pricing.cost_for(
                self.recorder.model,
                input_tokens=self.recorder.input_tokens or 0,
                output_tokens=self.recorder.output_tokens or 0,
                cache_read_tokens=self.recorder.cache_read_tokens or 0,
                cache_write_tokens=self.recorder.cache_write_tokens or 0,
            )

        _pending_spans.setdefault(self._run.run_id, []).append(
            dict(
                run_id=self._run.run_id,
                span_id=self._span_id,
                parent_span_id=self._parent_span_id,
                sequence=self._sequence,
                kind=self.kind,
                name=self.name,
                status=status,
                started_at=self._started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                input=self.recorder.input,
                output=self.recorder.output,
                model=self.recorder.model,
                input_tokens=self.recorder.input_tokens,
                output_tokens=self.recorder.output_tokens,
                cache_read_tokens=self.recorder.cache_read_tokens,
                cache_write_tokens=self.recorder.cache_write_tokens,
                cost_usd=cost,
                error=error_text,
                metadata=self.recorder.metadata,
            )
        )
        reset_current_span_frame(self._frame_token)


class span:
    """Use as `@span(kind, name)` to decorate a sync or async function, or
    as `async with span(kind, name) as recorder:` to wrap an inline block.
    Either way, code running inside can call
    `app.tracing.current_span().record_usage(...)` to attach model/token
    info before the span closes and is persisted."""

    def __init__(self, kind: SpanKind | str, name: str) -> None:
        self.kind = kind if isinstance(kind, SpanKind) else SpanKind(kind)
        self.name = name

    def __call__(self, func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                active = _ActiveSpan(self.kind, self.name)
                active.enter()
                try:
                    result = await func(*args, **kwargs)
                except BaseException as exc:
                    active.exit(exc)
                    raise
                active.exit(None)
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            active = _ActiveSpan(self.kind, self.name)
            active.enter()
            try:
                result = func(*args, **kwargs)
            except BaseException as exc:
                active.exit(exc)
                raise
            active.exit(None)
            return result

        return sync_wrapper  # type: ignore[return-value]

    async def __aenter__(self) -> SpanRecorder:
        self._active = _ActiveSpan(self.kind, self.name)
        return self._active.enter()

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        self._active.exit(exc)
        return False


def start_run(
    trigger: RunTrigger | str,
    *,
    conversation_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> RunHandle:
    trigger = trigger if isinstance(trigger, RunTrigger) else RunTrigger(trigger)
    run_id = store.insert_run(trigger=trigger, conversation_id=conversation_id, user_id=user_id)
    handle = RunHandle(run_id=run_id)
    _run_tokens[run_id] = set_current_run(handle)
    _pending_spans[run_id] = []
    return handle


def end_run(handle: RunHandle, *, status: RunStatus | str, error: str | None = None) -> None:
    status = status if isinstance(status, RunStatus) else RunStatus(status)
    pending = _pending_spans.pop(handle.run_id, [])
    for kwargs in sorted(pending, key=lambda kwargs: kwargs["sequence"]):
        store.insert_span(**kwargs)
    store.finalize_run(run_id=handle.run_id, status=status, error=error)
    token = _run_tokens.pop(handle.run_id, None)
    if token is not None:
        reset_current_run(token)
