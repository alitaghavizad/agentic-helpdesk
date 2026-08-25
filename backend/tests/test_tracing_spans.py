from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest import mock

import pytest

from app.db.models import Run, RunStatus, RunTrigger, SpanKind
from app.db.session import get_sessionmaker
from app.tracing import spans as spans_module
from app.tracing import store
from app.tracing.spans import current_span, end_run, span, start_run
from app.tracing.store import trace_tree


def test_span_decorator_on_sync_functions_builds_nested_tree(cleanup_run):
    @span(SpanKind.TOOL, "inner_tool")
    def inner():
        return "inner-result"

    @span(SpanKind.LLM, "outer_call")
    def outer():
        return inner()

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        assert outer() == "inner-result"
        end_run(handle, status=RunStatus.OK)

        trace = trace_tree(handle.run_id)
        assert len(trace.roots) == 1
        root = trace.roots[0]
        assert root.span.name == "outer_call"
        assert root.span.sequence == 0
        assert root.span.kind == SpanKind.LLM
        assert len(root.children) == 1
        assert root.children[0].span.name == "inner_tool"
        assert root.children[0].span.sequence == 1
        assert root.children[0].span.parent_span_id == root.span.id
    finally:
        cleanup_run(handle.run_id)


async def test_span_async_context_manager_records_usage_and_cost(cleanup_run):
    @span(SpanKind.RETRIEVAL, "async_inner")
    async def inner():
        current_span().record_usage(model="claude-opus-5", input_tokens=10, output_tokens=5)
        return "ok"

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        async with span(SpanKind.LLM, "outer_block") as recorder:
            recorder.record_usage(model="claude-opus-5", input_tokens=1000, output_tokens=500)
            result = await inner()
            assert result == "ok"
        end_run(handle, status=RunStatus.OK)

        trace = trace_tree(handle.run_id)
        outer_node = trace.roots[0]
        # (1000*5 + 500*25) / 1e6 = 0.0175
        assert outer_node.span.cost_usd == Decimal("0.017500")
        assert len(outer_node.children) == 1
        inner_span = outer_node.children[0].span
        assert inner_span.name == "async_inner"
        # (10*5 + 5*25) / 1e6 = 0.000175
        assert inner_span.cost_usd == Decimal("0.000175")

        # costs sum: run total equals the flat sum of its spans' costs
        assert trace.run.cost_usd == Decimal("0.017675")
    finally:
        cleanup_run(handle.run_id)


async def test_concurrent_sibling_spans_have_correct_independent_parents(cleanup_run):
    async def branch(label: str):
        async with span(SpanKind.TOOL, f"tool_{label}"):
            await asyncio.sleep(0)

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        async with span(SpanKind.LLM, "parent"):
            await asyncio.gather(branch("a"), branch("b"))
        end_run(handle, status=RunStatus.OK)

        trace = trace_tree(handle.run_id)
        parent_node = trace.roots[0]
        assert parent_node.span.name == "parent"
        assert {c.span.name for c in parent_node.children} == {"tool_a", "tool_b"}
        for child in parent_node.children:
            assert child.span.parent_span_id == parent_node.span.id
    finally:
        cleanup_run(handle.run_id)


def test_span_records_error_status_and_reraises(cleanup_run):
    @span(SpanKind.TOOL, "failing_tool")
    def boom():
        raise ValueError("something broke")

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        with pytest.raises(ValueError, match="something broke"):
            boom()
        end_run(handle, status=RunStatus.ERROR, error="something broke")

        trace = trace_tree(handle.run_id)
        failing_span = trace.roots[0].span
        assert failing_span.status.value == "error"
        assert "something broke" in failing_span.error

        assert trace.run.status.value == "error"
        assert trace.run.error == "something broke"
    finally:
        cleanup_run(handle.run_id)


def test_span_outside_active_run_raises_runtime_error():
    @span(SpanKind.TOOL, "orphan")
    def orphan():
        return None

    with pytest.raises(RuntimeError, match="active run"):
        orphan()


def test_current_span_is_none_outside_any_span(cleanup_run):
    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        assert current_span() is None
    finally:
        end_run(handle, status=RunStatus.OK)
        cleanup_run(handle.run_id)


async def test_async_span_decorator_records_error_status_and_reraises(cleanup_run):
    @span(SpanKind.TOOL, "async_failing_tool")
    async def boom():
        raise ValueError("async boom")

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        with pytest.raises(ValueError, match="async boom"):
            await boom()
        end_run(handle, status=RunStatus.ERROR, error="async boom")

        trace = trace_tree(handle.run_id)
        failing_span = trace.roots[0].span
        assert failing_span.status.value == "error"
        assert "async boom" in failing_span.error

        assert trace.run.status.value == "error"
    finally:
        cleanup_run(handle.run_id)


async def test_async_context_manager_records_error_and_does_not_swallow_exception(cleanup_run):
    """__aexit__ must return False so the exception actually propagates out
    of the `async with` block, rather than being silently swallowed."""
    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        with pytest.raises(ValueError, match="context boom"):
            async with span(SpanKind.TOOL, "async_ctx_failing"):
                raise ValueError("context boom")
        end_run(handle, status=RunStatus.ERROR, error="context boom")

        trace = trace_tree(handle.run_id)
        failing_span = trace.roots[0].span
        assert failing_span.status.value == "error"
        assert "context boom" in failing_span.error
    finally:
        cleanup_run(handle.run_id)


def test_span_decorator_rejects_async_generator_functions():
    """inspect.iscoroutinefunction doesn't detect async generator functions,
    so without an explicit guard they'd silently fall into the sync-wrapper
    path and produce a meaningless span around just the generator object's
    construction, not its actual work."""
    with pytest.raises(TypeError, match="async generator"):

        @span(SpanKind.TOOL, "bad_gen")
        async def gen():
            yield 1


def test_pending_spans_buffer_flushes_in_sequence_order_not_close_order(cleanup_run):
    """The buffering strategy (see the comment above _pending_spans in
    spans.py) exists because children close before their parents, but a
    child span row has a non-deferrable FK to its not-yet-inserted parent.
    This test makes the ordering mismatch explicit: the in-memory buffer
    accumulates finished spans in CLOSE order (innermost-first) -- the
    reverse of a valid insert order -- and end_run() must still produce a
    correct tree because it flushes sorted by `sequence` (assigned at OPEN
    time, so ascending sequence is always parent-before-child)."""

    @span(SpanKind.TOOL, "grandchild")
    def grandchild():
        return None

    @span(SpanKind.TOOL, "child")
    def child():
        return grandchild()

    @span(SpanKind.LLM, "root")
    def root():
        return child()

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        root()

        pending = spans_module._pending_spans[handle.run_id]
        assert [p["name"] for p in pending] == ["grandchild", "child", "root"]

        end_run(handle, status=RunStatus.OK)

        trace = trace_tree(handle.run_id)
        assert len(trace.roots) == 1
        root_node = trace.roots[0]
        assert root_node.span.name == "root"
        assert len(root_node.children) == 1
        child_node = root_node.children[0]
        assert child_node.span.name == "child"
        assert len(child_node.children) == 1
        assert child_node.children[0].span.name == "grandchild"
    finally:
        cleanup_run(handle.run_id)


def test_end_run_called_twice_is_a_no_op(cleanup_run):
    @span(SpanKind.TOOL, "solo")
    def solo():
        return None

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        solo()
        end_run(handle, status=RunStatus.OK)

        trace_before = trace_tree(handle.run_id)
        assert len(trace_before.roots) == 1

        # _pending_spans.pop(..., []) on an already-popped run_id returns
        # [], so a second call must not raise, double-insert, or otherwise
        # corrupt state.
        end_run(handle, status=RunStatus.OK)

        trace_after = trace_tree(handle.run_id)
        assert len(trace_after.roots) == 1
        assert trace_after.roots[0].span.id == trace_before.roots[0].span.id
    finally:
        cleanup_run(handle.run_id)


def test_end_run_finalizes_and_cleans_up_token_even_if_a_span_flush_fails(cleanup_run):
    """If store.insert_span raises partway through the flush loop, end_run
    must still finalize the run (so it isn't stuck in RunStatus.RUNNING
    forever) and still clean up the contextvar token (so it isn't leaked)."""

    @span(SpanKind.TOOL, "tool_1")
    def tool_1():
        return None

    @span(SpanKind.TOOL, "tool_2")
    def tool_2():
        return None

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        tool_1()
        tool_2()

        real_insert_span = store.insert_span
        call_count = {"n": 0}

        def flaky_insert_span(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated DB failure")
            return real_insert_span(**kwargs)

        with mock.patch("app.tracing.store.insert_span", side_effect=flaky_insert_span):
            with pytest.raises(RuntimeError, match="simulated DB failure"):
                end_run(handle, status=RunStatus.OK)

        # The run must have been finalized despite the failure -- not left
        # stuck in RunStatus.RUNNING.
        Session = get_sessionmaker()
        with Session() as session:
            run = session.get(Run, handle.run_id)
            assert run.status == RunStatus.OK

        # The contextvar token and the pending-spans buffer entry must both
        # have been cleaned up, not leaked.
        assert handle.run_id not in spans_module._run_tokens
        assert handle.run_id not in spans_module._pending_spans

        # A second end_run call must remain a safe no-op afterward.
        end_run(handle, status=RunStatus.OK)
    finally:
        cleanup_run(handle.run_id)


def test_span_exiting_after_its_run_already_ended_is_dropped_not_leaked(cleanup_run, capsys):
    """A span whose exit() runs after its run's end_run() already popped
    the buffer (e.g. a fire-and-forget task outliving its run) must be
    dropped with a warning, not silently recreate (and leak) the buffer
    entry via setdefault."""
    handle = start_run(RunTrigger.CHAT_TURN)
    active = spans_module._ActiveSpan(SpanKind.TOOL, "late_span")
    active.enter()
    try:
        end_run(handle, status=RunStatus.OK)

        active.exit(None)

        assert handle.run_id not in spans_module._pending_spans
        assert "dropped" in capsys.readouterr().err
    finally:
        cleanup_run(handle.run_id)
