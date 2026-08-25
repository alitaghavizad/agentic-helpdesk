from __future__ import annotations

import uuid

import pytest

from app.agent.tools.routing import (
    FindHelpdeskSpecialistArgs,
    GetHelpdeskWorkloadArgs,
    find_helpdesk_specialist_handler,
    get_helpdesk_workload_handler,
)
from app.db.models import (
    Conversation, EscalationAuthority, Role, Run, RunStatus, RunTrigger, Task, TaskCategory,
    Ticket, TicketPriority, TicketStatus, User,
)
from app.rbac.policy import Principal

_EMPLOYEE = Principal(kind="user", user_id="u1", role="employee", clearance="standard", department="Engineering", employee_ref="EMP-001", helpdesk_ref=None)


@pytest.fixture(autouse=True)
def _traced_run(cleanup_run):
    """find_helpdesk_specialist_handler calls McpChromaBackend.query, which
    wraps every MCP call in a tracing span (Task 3); span() hard-requires an
    active run and raises RuntimeError otherwise (see app/tracing/spans.py's
    module docstring and tests/test_rag_mcp_backend.py's identical fixture).
    Autouse + module-local keeps this invisible to each test's own body."""
    from app.db.models import RunStatus, RunTrigger
    from app.tracing import end_run, start_run

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        yield
        end_run(handle, status=RunStatus.OK)
    except Exception:
        end_run(handle, status=RunStatus.ABORTED)
        raise
    finally:
        cleanup_run(handle.run_id)


def _make_helpdesk_user(db_session, ref: str, specialization: str, escalation: EscalationAuthority = EscalationAuthority.STANDARD):
    user = User(
        username=ref.lower(), email=f"{ref.lower()}@northstar.example", full_name=ref, password_hash="x",
        role=Role.HELPDESK, helpdesk_ref=ref, specialization=specialization, escalation_authority=escalation,
    )
    db_session.add(user)
    db_session.commit()
    return user


def _make_ticket(db_session, *, assignee_helpdesk_ref: str, status: TicketStatus) -> Ticket:
    """Ticket.task_id and Ticket.conversation_id are NOT NULL foreign keys --
    real Conversation/Task rows must exist first. Task.classified_by_run_id
    is itself a NOT NULL FK to runs.id, created directly via db_session
    (not tracing.start_run()/end_run()) so it lives in the same savepoint
    transaction as everything else here and rolls back automatically at
    test teardown -- going through tracing's independently-committing
    session for this would deadlock any cleanup against db_session's held
    FK lock (see Task 6's fix in this same plan for the full mechanism)."""
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    conv = Conversation(guest_name="Guest", guest_email="g@example.com")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    task = Task(
        conversation_id=conv.id, title="t", category=TaskCategory.OTHER, severity="low",
        summary="s", affected_systems=[], evidence={}, classified_by_run_id=run.id,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    ticket = Ticket(
        task_id=task.id, conversation_id=conv.id, assignee_helpdesk_ref=assignee_helpdesk_ref,
        matched_specialization="x", assignment_rationale="r", assignment_score=0.5,
        priority=TicketPriority.LOW, status=status, title="t", body="b",
    )
    db_session.add(ticket)
    db_session.commit()
    return ticket


async def test_get_helpdesk_workload_counts_open_and_in_progress_tickets(db_session):
    _make_helpdesk_user(db_session, "HD-900", "Network and VPN Support")
    for status in (TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED):
        _make_ticket(db_session, assignee_helpdesk_ref="HD-900", status=status)

    result = await get_helpdesk_workload_handler(_EMPLOYEE, db_session, GetHelpdeskWorkloadArgs(helpdesk_ref="HD-900"))
    assert result["open_and_in_progress"] == 2


async def test_find_helpdesk_specialist_filters_out_standard_authority_for_critical_severity(db_session):
    _make_helpdesk_user(db_session, "HD-901", "Network and VPN Support", EscalationAuthority.STANDARD)
    _make_helpdesk_user(db_session, "HD-902", "Network and VPN Support", EscalationAuthority.HIGH)

    result = await find_helpdesk_specialist_handler(
        _EMPLOYEE, db_session,
        FindHelpdeskSpecialistArgs(problem_summary="VPN completely down for whole team", category="vpn_network", severity="critical"),
    )
    refs = [c["helpdesk_ref"] for c in result["candidates"]]
    assert "HD-901" not in refs
