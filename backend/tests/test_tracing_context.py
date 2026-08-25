import asyncio
import uuid

from app.tracing.context import (
    RunHandle,
    SpanFrame,
    get_current_run,
    get_current_span_frame,
    set_current_run,
    set_current_span_frame,
)


def test_run_handle_next_sequence_increments_from_zero():
    handle = RunHandle(run_id=uuid.uuid4())
    assert handle.next_sequence() == 0
    assert handle.next_sequence() == 1
    assert handle.next_sequence() == 2


def test_two_run_handles_have_independent_counters():
    a = RunHandle(run_id=uuid.uuid4())
    b = RunHandle(run_id=uuid.uuid4())
    assert a.next_sequence() == 0
    assert a.next_sequence() == 1
    assert b.next_sequence() == 0


def test_current_run_defaults_to_none():
    assert get_current_run() is None


def test_current_span_frame_defaults_to_none():
    assert get_current_span_frame() is None


def test_set_and_get_current_run_round_trips():
    handle = RunHandle(run_id=uuid.uuid4())
    token = set_current_run(handle)
    try:
        assert get_current_run() is handle
    finally:
        from app.tracing.context import reset_current_run
        reset_current_run(token)
    assert get_current_run() is None


async def test_current_span_frame_isolated_across_concurrent_gather_tasks():
    """Proves the property Phase 4's loop.py needs: asyncio.gather runs each
    coroutine as its own Task, and each Task gets its own COPY of the
    contextvars context at creation time, so one branch's current-span
    frame never leaks into a sibling's — required for concurrent tool
    calls to each report the correct parent span."""
    results: dict[str, uuid.UUID] = {}

    async def branch(label: str, span_id: uuid.UUID) -> None:
        set_current_span_frame(SpanFrame(span_id=span_id, recorder=None))
        await asyncio.sleep(0)  # yield control so the other branch can run
        frame = get_current_span_frame()
        assert frame is not None
        results[label] = frame.span_id

    id_a, id_b = uuid.uuid4(), uuid.uuid4()
    await asyncio.gather(branch("a", id_a), branch("b", id_b))
    assert results["a"] == id_a
    assert results["b"] == id_b
