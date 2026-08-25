from __future__ import annotations

import itertools
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunHandle:
    """The handle returned by tracing.start_run(). Not part of the DB
    schema -- this is purely an in-process stand-in that carries the run's
    id plus a per-run sequence counter, so nested/concurrent spans within
    the same run get monotonically increasing `sequence` values without a
    round-trip to the database for every span."""

    run_id: uuid.UUID
    _sequence_counter: Any = field(default_factory=itertools.count, repr=False)

    def next_sequence(self) -> int:
        return next(self._sequence_counter)


@dataclass
class SpanFrame:
    """The 'currently active span' as seen by code running inside it.
    `recorder` is typed loosely (not as spans.SpanRecorder) so this module
    never has to import spans.py -- context.py is a leaf module with no
    dependency on the rest of the tracing package."""

    span_id: uuid.UUID
    recorder: Any


_current_run: ContextVar[RunHandle | None] = ContextVar("current_run", default=None)
_current_span_frame: ContextVar[SpanFrame | None] = ContextVar("current_span_frame", default=None)


def get_current_run() -> RunHandle | None:
    return _current_run.get()


def set_current_run(handle: RunHandle | None) -> Token:
    return _current_run.set(handle)


def reset_current_run(token: Token) -> None:
    _current_run.reset(token)


def get_current_span_frame() -> SpanFrame | None:
    return _current_span_frame.get()


def set_current_span_frame(frame: SpanFrame | None) -> Token:
    return _current_span_frame.set(frame)


def reset_current_span_frame(token: Token) -> None:
    _current_span_frame.reset(token)
