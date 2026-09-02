from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.db.models import Run, RunStatus, RunTrigger, Span, SpanKind, SpanStatus
from app.db.session import get_sessionmaker
from app.tracing.redaction import redact


def insert_run(
    *, trigger: RunTrigger, conversation_id: uuid.UUID | None, user_id: uuid.UUID | None
) -> uuid.UUID:
    Session = get_sessionmaker()
    with Session() as session:
        run = Run(
            trigger=trigger, status=RunStatus.RUNNING,
            conversation_id=conversation_id, user_id=user_id,
            # Set explicitly rather than left to the column's server_default.
            # Postgres's now() is the TRANSACTION start time, so any two runs
            # opened inside one transaction would share a byte-identical
            # started_at and the admin run list would fall through to ordering
            # by a random uuid4. This path commits per run so that is rare
            # today, but the ordering must not depend on that staying true.
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.commit()
        return run.id


def insert_span(
    *,
    run_id: uuid.UUID,
    span_id: uuid.UUID,
    parent_span_id: uuid.UUID | None,
    sequence: int,
    kind: SpanKind,
    name: str,
    status: SpanStatus,
    started_at: datetime,
    ended_at: datetime,
    duration_ms: int,
    input: dict | None,
    output: dict | None,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
    cost_usd: Decimal | None,
    error: str | None,
    metadata: dict,
) -> None:
    """Inserts exactly one completed Span row. SpanStatus has no
    in-progress value, so a span is written once, at completion -- never
    updated afterward. `input`/`output`/`error`/`metadata` are all redacted
    here, automatically, so no call site can forget to -- `metadata` is a
    public, writable dict a caller can put anything into, and `error` is
    built from an exception's own message, which routinely embeds secrets
    (e.g. "AuthError: invalid key sk-...")."""
    Session = get_sessionmaker()
    with Session() as session:
        span = Span(
            id=span_id,
            run_id=run_id,
            parent_span_id=parent_span_id,
            sequence=sequence,
            kind=kind,
            name=name,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            input=redact(input) if input is not None else None,
            output=redact(output) if output is not None else None,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=cost_usd,
            error=redact(error) if error is not None else None,
            metadata_=redact(metadata),
        )
        session.add(span)
        session.commit()


def finalize_run(*, run_id: uuid.UUID, status: RunStatus, error: str | None) -> None:
    """Updates the Run row with rollups computed from its own persisted
    spans. Unpriced spans (cost_usd is None) contribute nothing to the
    sum rather than making the whole run's cost None -- only if every
    single span is unpriced does the run's cost stay None."""
    Session = get_sessionmaker()
    with Session() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise ValueError(f"No run with id {run_id}")
        spans = session.query(Span).filter(Span.run_id == run_id).all()

        ended_at = datetime.now(timezone.utc)
        run.status = status
        # Redacted, exactly as insert_span redacts a span's error and for the
        # same reason: this string is built from an exception's own message,
        # which routinely embeds a secret ("AuthError: invalid key sk-..."),
        # and the dossier path feeds it raw transport errors. The omission was
        # latent until phase 8a put run.error on the wire through GET /runs
        # and GET /runs/{id}/trace, where a secret becomes copy-pasteable.
        run.error = redact(error) if error is not None else None
        run.ended_at = ended_at
        run.duration_ms = int((ended_at - run.started_at).total_seconds() * 1000)
        run.input_tokens = sum(s.input_tokens or 0 for s in spans)
        run.output_tokens = sum(s.output_tokens or 0 for s in spans)
        run.cache_read_tokens = sum(s.cache_read_tokens or 0 for s in spans)
        run.cache_write_tokens = sum(s.cache_write_tokens or 0 for s in spans)
        priced = [s.cost_usd for s in spans if s.cost_usd is not None]
        run.cost_usd = sum(priced) if priced else None
        run.llm_calls = sum(1 for s in spans if s.kind == SpanKind.LLM)
        run.tool_calls = sum(1 for s in spans if s.kind == SpanKind.TOOL)

        # Built BEFORE the commit, published AFTER it. Assembling it first
        # avoids re-reading every attribute back out of the database (the
        # sessionmaker expires instances on commit); publishing after means
        # a subscriber can never be told about a finalisation that then
        # rolled back. `broker` imports nothing from this project, so the
        # local import only guards against an import cycle appearing later.
        event = {
            "type": "run_finished",
            "id": str(run.id),
            "trigger": run.trigger.value,
            "status": run.status.value,
            "duration_ms": run.duration_ms,
            "cost_usd": float(run.cost_usd) if run.cost_usd is not None else None,
        }
        session.commit()

    from app.notifications import broker

    broker.publish(broker.ADMIN_RUNS_CHANNEL, event)


@dataclass
class SpanNode:
    span: Span
    children: list["SpanNode"]


@dataclass
class RunTrace:
    run: Run
    roots: list[SpanNode]


def trace_tree(run_id: uuid.UUID) -> RunTrace:
    """Reconstructs the full span tree for a run, ordered by `sequence`.
    No cost rollup happens here -- each node shows only its own cost_usd;
    Run.cost_usd (computed once, in finalize_run) is the flat sum."""
    Session = get_sessionmaker()
    with Session() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise ValueError(f"No run with id {run_id}")
        spans = (
            session.query(Span)
            .filter(Span.run_id == run_id)
            .order_by(Span.sequence)
            .all()
        )
        session.expunge(run)
        for s in spans:
            session.expunge(s)

    nodes: dict[uuid.UUID, SpanNode] = {s.id: SpanNode(span=s, children=[]) for s in spans}
    roots: list[SpanNode] = []
    for s in spans:
        node = nodes[s.id]
        if s.parent_span_id is not None and s.parent_span_id in nodes:
            nodes[s.parent_span_id].children.append(node)
        else:
            roots.append(node)
    return RunTrace(run=run, roots=roots)
