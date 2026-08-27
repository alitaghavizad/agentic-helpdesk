from __future__ import annotations

import uuid

import pytest

from app.db.models import ResolutionPath, Task, Ticket

pytestmark = pytest.mark.live_api

_LIVE_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000005b1")

# The same tightened, exact-set expectation the offline gate uses
# (tests/test_phase5_gate.py). Deliberately NOT a substring match: "Office
# networking" is a distinct specialization (in-office LAN/WiFi/printers, not
# a home VPN client) and would pass a naive `"network" in specialization`
# check while being the wrong answer. Both halves of the phase-5 gate must
# hold the model to the same standard.
_ACCEPTED_VPN_SPECIALIZATIONS = {"vpn and network access"}


@pytest.mark.live_api
async def test_a_real_conversation_yields_a_task_and_a_matching_ticket(db_session):
    """THE spec-18 phase-5 gate, end to end against the real API: a real
    conversation must yield a tasks row AND a ticket whose assignee's
    specialization matches the category the model itself chose.

    Unlike the scripted half, nothing here is fed to the model -- it picks
    the category, calls find_helpdesk_specialist, and chooses the assignee
    on its own. Assertions are on measured outcomes, printed so the numbers
    go into the phase report rather than being merely 'green'.
    """
    import anthropic

    from app.agent.loop import run_turn
    from app.config import get_settings
    from app.db.models import Conversation, Role, User
    from app.db.session import get_sessionmaker
    from app.rbac.policy import Principal

    # Hard-committed through its own session: run_turn's tracing and
    # usage-counter writes commit on their own connections and cannot see
    # db_session's savepoint (the cross-connection FK-visibility gap that
    # bit every task from Phase 4 onward). Cleaned up by the controller
    # after the run -- see the plan's Task 11 Step 4.
    Session = get_sessionmaker()
    with Session() as setup:
        user = setup.get(User, _LIVE_USER_ID)
        if user is None:
            setup.add(User(
                id=_LIVE_USER_ID, username="phase5live", email="phase5live@northstar.example",
                full_name="Phase Five Live", password_hash="x", role=Role.EMPLOYEE,
                clearance="standard", department="Engineering", employee_ref="EMP-5B1",
            ))
            # Commit the User BEFORE building the Conversation that
            # references it. Staging both and committing once relies on the
            # unit of work ordering the inserts by FK dependency, which it
            # does not reliably do here -- conversations was inserted first
            # and tripped conversations_user_id_fkey.
            setup.commit()
        conv = Conversation(user_id=_LIVE_USER_ID, title="Live gate conversation")
        setup.add(conv)
        setup.commit()
        conversation_id = conv.id

    principal = Principal(
        kind="user", user_id=str(_LIVE_USER_ID), role="employee", clearance="standard",
        department="Engineering", employee_ref="EMP-5B1", helpdesk_ref=None,
    )
    client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)

    message = (
        "I can't connect to the corporate VPN from my home office. The client "
        "sits at 'negotiating' for about 30 seconds and then times out. I've "
        "rebooted and reinstalled it. Please raise a ticket for this."
    )

    events = []
    async for event in run_turn(
        client, db_session, principal, conversation_id=conversation_id,
        user_key=str(_LIVE_USER_ID), history=[], user_message=message,
    ):
        events.append(event)

    kinds = [e.type for e in events]
    print(f"\nevent types: {kinds}")
    for event in events:
        if event.type == "error":
            print(f"ERROR EVENT: {event.data}")

    task = db_session.query(Task).filter(Task.conversation_id == conversation_id).one_or_none()
    assert task is not None, f"no tasks row was written; events were {kinds}"
    print(f"task: category={task.category.value} severity={task.severity.value} title={task.title!r}")

    ticket = db_session.query(Ticket).filter(Ticket.conversation_id == conversation_id).one_or_none()
    assert ticket is not None, f"no ticket was created; events were {kinds}"

    specialist = db_session.query(User).filter(User.helpdesk_ref == ticket.assignee_helpdesk_ref).one_or_none()
    print(
        f"ticket: TCK-{ticket.ticket_number:06d} -> {ticket.assignee_helpdesk_ref} "
        f"({specialist.specialization if specialist else 'UNKNOWN REF'}) "
        f"score={float(ticket.assignment_score)}"
    )
    print(f"rationale: {ticket.assignment_rationale}")

    assert specialist is not None, f"model assigned {ticket.assignee_helpdesk_ref!r}, which is not a real helpdesk ref"
    assert ticket.assignee_user_id == specialist.id, "create_ticket did not resolve the assignee FK"

    specialization = (specialist.specialization or "").strip().lower()
    assert specialization in _ACCEPTED_VPN_SPECIALIZATIONS, (
        f"gate FAILED: category {task.category.value!r} routed to {specialist.specialization!r}, "
        f"expected one of {sorted(_ACCEPTED_VPN_SPECIALIZATIONS)}"
    )

    db_session.refresh(task)
    assert task.resolution_path == ResolutionPath.TICKETED
