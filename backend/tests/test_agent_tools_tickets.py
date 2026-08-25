from __future__ import annotations

from app.agent.tools.tickets import (
    CreateTicketArgs,
    GetTicketArgs,
    ListMyTicketsArgs,
    RecordTaskArgs,
    create_ticket_handler,
    get_ticket_handler,
    list_my_tickets_handler,
    record_task_handler,
)
from app.db.models import Conversation, Run, RunStatus, RunTrigger, Ticket
from app.rbac.policy import Principal


def _guest_principal(guest_email: str) -> Principal:
    return Principal(kind="guest", user_id=None, role="guest", clearance=None, department=None, employee_ref=None, helpdesk_ref=None)


def _make_conversation(db_session, guest_email="g@example.com"):
    conv = Conversation(guest_name="Guest", guest_email=guest_email)
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    return conv


def _make_run(db_session):
    """Created directly via db_session rather than tracing.start_run()/end_run()
    so it lives in the same savepoint transaction as the Task/Ticket rows and
    rolls back automatically -- avoids a real Postgres FK-lock deadlock between
    db_session's held transaction and cleanup_run's separate-connection DELETE."""
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run.id


async def test_record_task_handler_creates_task(db_session):
    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)
    args = RecordTaskArgs(
        conversation_id=str(conv.id), title="VPN broken", category="vpn_network",
        severity="medium", summary="Can't connect", affected_systems=["vpn"], evidence={},
    )
    result = await record_task_handler(_guest_principal(conv.guest_email), db_session, args, run_id=run_id, guest_email=conv.guest_email)
    assert "task_id" in result


async def test_create_ticket_handler_and_list_my_tickets_round_trip(db_session):
    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)
    record_args = RecordTaskArgs(
        conversation_id=str(conv.id), title="VPN broken", category="vpn_network",
        severity="medium", summary="Can't connect", affected_systems=[], evidence={},
    )
    task_result = await record_task_handler(_guest_principal(conv.guest_email), db_session, record_args, run_id=run_id, guest_email=conv.guest_email)

    create_args = CreateTicketArgs(
        task_id=task_result["task_id"], assignee_helpdesk_ref="HD-001", priority="medium",
        title="VPN broken", body="Full body", assignment_rationale="Best match",
        matched_specialization="Network Support", assignment_score=0.9,
    )
    ticket_result = await create_ticket_handler(_guest_principal(conv.guest_email), db_session, create_args, conversation_id=conv.id, guest_email=conv.guest_email)
    assert ticket_result["status"] == "open"
    assert "TCK-" in ticket_result["ticket_number"]

    list_result = await list_my_tickets_handler(_guest_principal(conv.guest_email), db_session, ListMyTicketsArgs(status=None), guest_email=conv.guest_email)
    assert len(list_result["tickets"]) == 1
    assert list_result["tickets"][0]["ticket_number"] == ticket_result["ticket_number"]


async def test_get_ticket_handler_denies_access_to_other_guests_ticket(db_session):
    conv = _make_conversation(db_session, guest_email="owner@example.com")
    run_id = _make_run(db_session)
    record_args = RecordTaskArgs(
        conversation_id=str(conv.id), title="t", category="other", severity="low",
        summary="s", affected_systems=[], evidence={},
    )
    task_result = await record_task_handler(_guest_principal(conv.guest_email), db_session, record_args, run_id=run_id, guest_email=conv.guest_email)
    create_args = CreateTicketArgs(
        task_id=task_result["task_id"], assignee_helpdesk_ref="HD-001", priority="low",
        title="t", body="b", assignment_rationale="r", matched_specialization="s", assignment_score=0.5,
    )
    ticket_result = await create_ticket_handler(_guest_principal(conv.guest_email), db_session, create_args, conversation_id=conv.id, guest_email=conv.guest_email)

    db_session.expire_all()
    ticket_row = db_session.query(Ticket).filter(Ticket.ticket_number == int(ticket_result["ticket_number"].removeprefix("TCK-"))).one()
    result = await get_ticket_handler(
        _guest_principal("stranger@example.com"), db_session, GetTicketArgs(ticket_id=str(ticket_row.id)), guest_email="stranger@example.com",
    )
    assert result.get("is_error") is True
