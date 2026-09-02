"""Makes ONE real Claude call. Excluded from the default run by the
`live_dossier` marker.

This is the ONLY thing that proves a real model can fill IncidentDossier.
Every other dossier test stubs the client and proves the assembly and the
error handling around it -- necessary, but they would all stay green
against a schema no model can satisfy. The phase report must cite this
run, not those.

Builds its ticket through a committing session rather than the conftest
`make_ticket` fixture: build_dossier calls start_run(conversation_id=...),
which inserts on the tracing store's own connection, and a conversation
that exists only inside db_session's savepoint is invisible there --
measured, it fails immediately with ForeignKeyViolation.
"""
from __future__ import annotations

import uuid

import pytest

from app.db.session import get_sessionmaker

pytestmark = pytest.mark.live_dossier

_committed: dict[str, list[uuid.UUID]] = {"conversations": [], "runs": [], "tasks": [], "tickets": []}


@pytest.fixture(scope="module", autouse=True)
def _sweep_committed_rows_after_module():
    """Same order as tests/test_admin_dossier.py: spans, tickets, tasks,
    runs, messages, conversations. Runs are swept by conversation id too,
    because build_dossier creates a DOSSIER run whose id no test sees."""
    yield
    from app.db.models import Conversation, Message, Run, Span, Task, Ticket

    Session = get_sessionmaker()
    try:
        with Session() as s:
            conv_ids = _committed["conversations"]
            run_ids = list(_committed["runs"])
            if conv_ids:
                run_ids += [
                    r.id for r in s.query(Run).filter(Run.conversation_id.in_(conv_ids)).all()
                ]
            if run_ids:
                s.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
            if _committed["tickets"]:
                s.query(Ticket).filter(
                    Ticket.id.in_(_committed["tickets"]),
                ).delete(synchronize_session=False)
            if _committed["tasks"]:
                s.query(Task).filter(
                    Task.id.in_(_committed["tasks"]),
                ).delete(synchronize_session=False)
            if run_ids:
                s.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
            if conv_ids:
                s.query(Message).filter(
                    Message.conversation_id.in_(conv_ids),
                ).delete(synchronize_session=False)
                s.query(Conversation).filter(
                    Conversation.id.in_(conv_ids),
                ).delete(synchronize_session=False)
            s.commit()
    finally:
        for key in _committed:
            _committed[key].clear()


def _committed_ticket():
    from app.db.models import (
        Conversation, Message, MessageRole, Run, RunStatus, RunTrigger, Severity,
        Span, SpanKind, Task, TaskCategory, Ticket, TicketPriority, TicketStatus,
    )

    Session = get_sessionmaker()
    with Session() as s:
        conv = Conversation(guest_name="Dana Reyes", guest_email="dana@northstar.example")
        run = Run(
            trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK,
            cost_usd=0.0412, input_tokens=3120, output_tokens=580,
        )
        s.add_all([conv, run])
        s.commit()
        _committed["conversations"].append(conv.id)
        _committed["runs"].append(run.id)

        s.add_all([
            Span(run_id=run.id, sequence=1, kind=SpanKind.GUARDRAIL, name="check_inbound",
                 started_at=run.started_at, ended_at=run.started_at),
            Span(run_id=run.id, sequence=2, kind=SpanKind.TOOL, name="search_knowledge",
                 started_at=run.started_at, ended_at=run.started_at),
            Span(run_id=run.id, sequence=3, kind=SpanKind.LLM, name="classify_task",
                 started_at=run.started_at, ended_at=run.started_at),
        ])
        task = Task(
            conversation_id=conv.id, user_id=None, guest_email="dana@northstar.example",
            title="VPN client times out from home",
            category=TaskCategory.VPN_NETWORK, severity=Severity.HIGH,
            summary="VPN authenticates then times out after roughly thirty seconds, "
                    "only from the home network; the office network is unaffected.",
            affected_systems=["vpn", "mfa"], evidence={"client_version": "6.2.1"},
            classified_by_run_id=run.id,
        )
        s.add(task)
        s.commit()
        _committed["tasks"].append(task.id)

        ticket = Ticket(
            task_id=task.id, conversation_id=conv.id,
            requester_user_id=None, requester_guest_email="dana@northstar.example",
            assignee_helpdesk_ref="HD-005",
            matched_specialization="Network and VPN Support",
            assignment_rationale="VPN timeouts match this queue's specialisation.",
            assignment_score=0.92, priority=TicketPriority.HIGH, status=TicketStatus.OPEN,
            title="VPN client times out from home",
            body="The VPN connects, shows authenticated, then drops after about "
                 "thirty seconds. Works fine from the office.",
        )
        s.add(ticket)
        s.commit()
        _committed["tickets"].append(ticket.id)

        for role, content in [
            (MessageRole.USER, "My VPN keeps timing out when I work from home."),
            (MessageRole.ASSISTANT, "How long after connecting does it drop?"),
            (MessageRole.USER, "About thirty seconds. It's fine at the office."),
        ]:
            s.add(Message(conversation_id=conv.id, role=role, content=content))
        s.commit()

        s.refresh(ticket)
        s.expunge(ticket)
        return ticket


def test_a_real_dossier_validates(db_session):
    import anthropic

    from app.admin.dossier import IncidentDossier, build_dossier
    from app.config import get_settings

    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY is not configured")

    ticket = _committed_ticket()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    result = build_dossier(db_session, client, ticket)

    assert isinstance(result, IncidentDossier)
    assert result.problem_statement.strip()
    assert result.timeline, "a dossier with an empty timeline records nothing"
    # The figures are ours, not the model's -- see dossier._true_cost_summary.
    assert result.cost_summary.input_tokens == 3120
    assert result.cost_summary.cost_usd == pytest.approx(0.0412)
    print(f"\nDOSSIER -> {result.model_dump_json(indent=2)[:2000]}")
