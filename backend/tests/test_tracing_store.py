import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db.models import Run, RunStatus, RunTrigger, Span, SpanKind, SpanStatus
from app.db.session import get_sessionmaker
from app.tracing import store


def test_insert_run_creates_row_with_running_status(cleanup_run):
    run_id = store.insert_run(trigger=RunTrigger.CHAT_TURN, conversation_id=None, user_id=None)
    try:
        Session = get_sessionmaker()
        with Session() as session:
            run = session.get(Run, run_id)
            assert run is not None
            assert run.status == RunStatus.RUNNING
            assert run.trigger == RunTrigger.CHAT_TURN
    finally:
        cleanup_run(run_id)


def test_insert_span_redacts_planted_secrets_before_persisting(cleanup_run):
    run_id = store.insert_run(trigger=RunTrigger.CHAT_TURN, conversation_id=None, user_id=None)
    span_id = uuid.uuid4()
    started = datetime.now(timezone.utc)
    try:
        store.insert_span(
            run_id=run_id, span_id=span_id, parent_span_id=None, sequence=0,
            kind=SpanKind.TOOL, name="test_tool", status=SpanStatus.OK,
            started_at=started, ended_at=started + timedelta(milliseconds=5), duration_ms=5,
            input={"api_key": "sk-VERYSECRETVALUE1234567890"},
            output={"password": "hunter2", "ok": True},
            model=None, input_tokens=None, output_tokens=None,
            cache_read_tokens=None, cache_write_tokens=None, cost_usd=None,
            error=None, metadata={},
        )
        Session = get_sessionmaker()
        with Session() as session:
            span = session.get(Span, span_id)
            assert span is not None
            assert "sk-VERYSECRETVALUE1234567890" not in str(span.input)
            assert span.output["password"] == "[REDACTED]"
            assert span.output["ok"] is True
    finally:
        cleanup_run(run_id)


def test_insert_span_redacts_secrets_in_metadata_and_error(cleanup_run):
    run_id = store.insert_run(trigger=RunTrigger.CHAT_TURN, conversation_id=None, user_id=None)
    span_id = uuid.uuid4()
    started = datetime.now(timezone.utc)
    try:
        store.insert_span(
            run_id=run_id, span_id=span_id, parent_span_id=None, sequence=0,
            kind=SpanKind.TOOL, name="test_tool", status=SpanStatus.ERROR,
            started_at=started, ended_at=started + timedelta(milliseconds=5), duration_ms=5,
            input=None, output=None,
            model=None, input_tokens=None, output_tokens=None,
            cache_read_tokens=None, cache_write_tokens=None, cost_usd=None,
            error="AuthError: invalid key sk-VERYSECRETVALUE1234567890",
            metadata={"api_key": "sk-ANOTHERSECRETVALUE1234567890", "note": "ok"},
        )
        Session = get_sessionmaker()
        with Session() as session:
            span = session.get(Span, span_id)
            assert span is not None
            assert "sk-VERYSECRETVALUE1234567890" not in span.error
            assert "[REDACTED]" in span.error
            assert span.metadata_["api_key"] == "[REDACTED]"
            assert span.metadata_["note"] == "ok"
    finally:
        cleanup_run(run_id)


def test_finalize_run_sums_span_costs_and_tokens_and_counts_calls_by_kind(cleanup_run):
    run_id = store.insert_run(trigger=RunTrigger.CHAT_TURN, conversation_id=None, user_id=None)
    started = datetime.now(timezone.utc)
    try:
        store.insert_span(
            run_id=run_id, span_id=uuid.uuid4(), parent_span_id=None, sequence=0,
            kind=SpanKind.LLM, name="call_1", status=SpanStatus.OK,
            started_at=started, ended_at=started, duration_ms=1,
            input=None, output=None, model="claude-opus-5",
            input_tokens=100, output_tokens=50, cache_read_tokens=0, cache_write_tokens=0,
            cost_usd=Decimal("0.001750"), error=None, metadata={},
        )
        store.insert_span(
            # An unpriced span (e.g. a tool call) must not poison the sum --
            # it simply contributes 0 tokens/cost, not None/error.
            run_id=run_id, span_id=uuid.uuid4(), parent_span_id=None, sequence=1,
            kind=SpanKind.TOOL, name="call_2", status=SpanStatus.OK,
            started_at=started, ended_at=started, duration_ms=1,
            input=None, output=None, model=None,
            input_tokens=None, output_tokens=None, cache_read_tokens=None, cache_write_tokens=None,
            cost_usd=None, error=None, metadata={},
        )
        store.finalize_run(run_id=run_id, status=RunStatus.OK, error=None)

        Session = get_sessionmaker()
        with Session() as session:
            run = session.get(Run, run_id)
            assert run.status == RunStatus.OK
            assert run.ended_at is not None
            assert run.duration_ms is not None
            assert run.input_tokens == 100
            assert run.output_tokens == 50
            assert run.cost_usd == Decimal("0.001750")
            assert run.llm_calls == 1
            assert run.tool_calls == 1
    finally:
        cleanup_run(run_id)


def test_finalize_run_with_no_priced_spans_leaves_cost_null(cleanup_run):
    run_id = store.insert_run(trigger=RunTrigger.CHAT_TURN, conversation_id=None, user_id=None)
    started = datetime.now(timezone.utc)
    try:
        store.insert_span(
            run_id=run_id, span_id=uuid.uuid4(), parent_span_id=None, sequence=0,
            kind=SpanKind.TOOL, name="unpriced_tool", status=SpanStatus.OK,
            started_at=started, ended_at=started, duration_ms=1,
            input=None, output=None, model=None,
            input_tokens=None, output_tokens=None, cache_read_tokens=None, cache_write_tokens=None,
            cost_usd=None, error=None, metadata={},
        )
        store.finalize_run(run_id=run_id, status=RunStatus.OK, error=None)

        Session = get_sessionmaker()
        with Session() as session:
            run = session.get(Run, run_id)
            assert run.cost_usd is None
    finally:
        cleanup_run(run_id)


def test_finalize_run_records_error_status_and_message(cleanup_run):
    run_id = store.insert_run(trigger=RunTrigger.CHAT_TURN, conversation_id=None, user_id=None)
    try:
        store.finalize_run(run_id=run_id, status=RunStatus.ERROR, error="budget exceeded")
        Session = get_sessionmaker()
        with Session() as session:
            run = session.get(Run, run_id)
            assert run.status == RunStatus.ERROR
            assert run.error == "budget exceeded"
    finally:
        cleanup_run(run_id)


def test_finalize_run_redacts_secrets_in_the_error_message(cleanup_run):
    """`error` is built from an exception's own message, which routinely
    embeds a secret -- the exact reason insert_span redacts its own. This
    was latent until the admin API put run.error on the wire through
    GET /runs and GET /runs/{id}/trace, where a leaked key becomes
    copy-pasteable; the dossier path feeds it raw transport errors like
    anthropic.AuthenticationError."""
    run_id = store.insert_run(trigger=RunTrigger.CHAT_TURN, conversation_id=None, user_id=None)
    leaky = "AuthenticationError: invalid x-api-key sk-ant-LEAKED1234567890abcd"
    try:
        store.finalize_run(run_id=run_id, status=RunStatus.ERROR, error=leaky)
        Session = get_sessionmaker()
        with Session() as session:
            run = session.get(Run, run_id)
        assert "sk-ant-LEAKED1234567890abcd" not in run.error
        assert "[REDACTED]" in run.error
        # The exception type survives: redaction removes the secret, not the
        # diagnosis. An error message that said nothing would be its own bug.
        assert "AuthenticationError" in run.error
    finally:
        cleanup_run(run_id)


def test_a_run_and_its_span_redact_the_same_error_identically(cleanup_run):
    """The two write paths must not disagree. They did: insert_span redacted
    and finalize_run did not, so the same string was safe on one row and
    leaked on the other."""
    run_id = store.insert_run(trigger=RunTrigger.CHAT_TURN, conversation_id=None, user_id=None)
    leaky = "AuthenticationError: invalid x-api-key sk-ant-LEAKED1234567890abcd"
    started = datetime.now(timezone.utc)
    span_id = uuid.uuid4()
    try:
        store.insert_span(
            run_id=run_id, span_id=span_id, parent_span_id=None, sequence=0,
            kind=SpanKind.LLM, name="call", status=SpanStatus.ERROR,
            started_at=started, ended_at=started, duration_ms=0, error=leaky,
            input=None, output=None, model=None, input_tokens=None,
            output_tokens=None, cache_read_tokens=None, cache_write_tokens=None,
            cost_usd=None, metadata=None,
        )
        store.finalize_run(run_id=run_id, status=RunStatus.ERROR, error=leaky)
        Session = get_sessionmaker()
        with Session() as session:
            run = session.get(Run, run_id)
            span = session.get(Span, span_id)
        assert run.error == span.error
    finally:
        cleanup_run(run_id)


def test_trace_tree_reconstructs_parent_child_nesting_ordered_by_sequence(cleanup_run):
    run_id = store.insert_run(trigger=RunTrigger.CHAT_TURN, conversation_id=None, user_id=None)
    started = datetime.now(timezone.utc)
    root_id = uuid.uuid4()
    child_id = uuid.uuid4()
    other_root_id = uuid.uuid4()
    try:
        store.insert_span(
            run_id=run_id, span_id=root_id, parent_span_id=None, sequence=0,
            kind=SpanKind.LLM, name="root", status=SpanStatus.OK,
            started_at=started, ended_at=started, duration_ms=1,
            input=None, output=None, model=None, input_tokens=None, output_tokens=None,
            cache_read_tokens=None, cache_write_tokens=None, cost_usd=None, error=None, metadata={},
        )
        store.insert_span(
            run_id=run_id, span_id=other_root_id, parent_span_id=None, sequence=2,
            kind=SpanKind.LLM, name="second_root", status=SpanStatus.OK,
            started_at=started, ended_at=started, duration_ms=1,
            input=None, output=None, model=None, input_tokens=None, output_tokens=None,
            cache_read_tokens=None, cache_write_tokens=None, cost_usd=None, error=None, metadata={},
        )
        store.insert_span(
            run_id=run_id, span_id=child_id, parent_span_id=root_id, sequence=1,
            kind=SpanKind.TOOL, name="child", status=SpanStatus.OK,
            started_at=started, ended_at=started, duration_ms=1,
            input=None, output=None, model=None, input_tokens=None, output_tokens=None,
            cache_read_tokens=None, cache_write_tokens=None, cost_usd=None, error=None, metadata={},
        )

        trace = store.trace_tree(run_id)

        assert trace.run.id == run_id
        assert [r.span.name for r in trace.roots] == ["root", "second_root"]
        assert len(trace.roots[0].children) == 1
        assert trace.roots[0].children[0].span.name == "child"
        assert trace.roots[1].children == []
    finally:
        cleanup_run(run_id)
