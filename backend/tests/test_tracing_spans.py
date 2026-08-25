import asyncio
import uuid
from decimal import Decimal

import pytest

from app.db.models import RunStatus, RunTrigger, SpanKind
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
