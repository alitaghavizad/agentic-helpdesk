from __future__ import annotations

import uuid

import pytest

from app.db.models import Conversation, ResolutionPath, RunStatus, RunTrigger, TaskCategory, TicketPriority
from app.tickets.service import create_ticket, record_task
from app.tracing import end_run, start_run


def _make_conversation(db_session):
    conv = Conversation(guest_name="Guest", guest_email="guest@example.com")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    return conv


def _make_run():
    """Creates one real, committed Run row (app.tracing.store commits on its
    own connection independent of db_session's transaction) so
    Task.classified_by_run_id's FK is satisfiable.

    Deliberately does NOT delete the row afterwards via the `cleanup_run`
    fixture, unlike every other test in this codebase that creates a Run.
    Those other tests only ever reference the run from tracing's own
    tables (Run/Span); these tests additionally INSERT a Task row *through
    db_session* whose classified_by_run_id column FK-references this run.
    Postgres takes a FOR KEY SHARE lock on the referenced `runs` row for
    the referencing transaction's full lifetime -- and db_session's
    transaction (see conftest.db_session: an outer `connection.begin()`
    that individual `db.commit()` calls only savepoint into, never
    actually ending) does not end until the fixture's own teardown runs
    *after* this test function returns. Any attempt to delete the run row
    from cleanup_run's separate connection before then -- whether inline,
    in a `finally`, or via `request.addfinalizer` (which still fires
    before db_session's fixture-level rollback) -- blocks on that lock for
    the lifetime of the test process, i.e. hangs. So this run row is left
    behind as a harmless orphan; only its id is needed for the FK."""
    handle = start_run(RunTrigger.CHAT_TURN)
    end_run(handle, status=RunStatus.OK)
    return handle.run_id


def test_record_task_creates_row_with_pending_resolution_path(db_session):
    conv = _make_conversation(db_session)
    run_id = _make_run()

    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="Can't connect to VPN", category=TaskCategory.VPN_NETWORK, severity="medium",
        summary="User reports VPN client fails to connect.", affected_systems=["vpn"],
        evidence={"error": "timeout"}, classified_by_run_id=run_id,
    )

    assert task.id is not None
    assert task.conversation_id == conv.id
    assert task.resolution_path == ResolutionPath.PENDING
    assert task.category == TaskCategory.VPN_NETWORK


def test_create_ticket_succeeds_for_valid_task(db_session):
    conv = _make_conversation(db_session)
    run_id = _make_run()
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="VPN issue", category=TaskCategory.VPN_NETWORK, severity="medium",
        summary="s", affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )

    ticket = create_ticket(
        db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
        requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-001",
        priority=TicketPriority.MEDIUM, title="VPN issue", body="Full description",
        assignment_rationale="Best semantic match for VPN category.",
        matched_specialization="Network and VPN Support", assignment_score=0.87,
    )

    assert ticket.ticket_number is not None
    assert ticket.task_id == task.id
    assert ticket.status.value == "open"


def test_create_ticket_raises_for_nonexistent_task(db_session):
    with pytest.raises(ValueError, match="does not exist"):
        create_ticket(
            db_session, task_id=uuid.uuid4(), conversation_id=uuid.uuid4(), requester_user_id=None,
            requester_guest_email="g@example.com", assignee_helpdesk_ref="HD-001",
            priority=TicketPriority.LOW, title="t", body="b", assignment_rationale="r",
            matched_specialization="s", assignment_score=0.5,
        )


def test_create_ticket_raises_when_task_belongs_to_different_conversation(db_session):
    conv = _make_conversation(db_session)
    other_conv = _make_conversation(db_session)
    run_id = _make_run()
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="t", category=TaskCategory.OTHER, severity="low", summary="s",
        affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )

    with pytest.raises(ValueError, match="does not belong"):
        create_ticket(
            db_session, task_id=task.id, conversation_id=other_conv.id, requester_user_id=None,
            requester_guest_email=other_conv.guest_email, assignee_helpdesk_ref="HD-001",
            priority=TicketPriority.LOW, title="t", body="b", assignment_rationale="r",
            matched_specialization="s", assignment_score=0.5,
        )


def test_create_ticket_raises_when_task_already_has_a_ticket(db_session):
    conv = _make_conversation(db_session)
    run_id = _make_run()
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="t", category=TaskCategory.OTHER, severity="low", summary="s",
        affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )
    create_ticket(
        db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
        requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-001",
        priority=TicketPriority.LOW, title="t", body="b", assignment_rationale="r",
        matched_specialization="s", assignment_score=0.5,
    )

    with pytest.raises(ValueError, match="already has a ticket"):
        create_ticket(
            db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
            requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-002",
            priority=TicketPriority.LOW, title="t", body="b", assignment_rationale="r",
            matched_specialization="s", assignment_score=0.5,
        )
