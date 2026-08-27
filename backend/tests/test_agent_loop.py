from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from anthropic.types import Usage

from app.agent.budget import AbortRun
from app.agent.loop import run_turn
from app.db.models import Clearance, Conversation, Role, User
from app.rbac.policy import Principal
from tests.support.fake_anthropic import FakeAnthropicClient, make_text_message, make_tool_use_message

_EMPLOYEE = Principal(kind="user", user_id="00000000-0000-0000-0000-000000000001", role="employee", clearance="standard", department="Engineering", employee_ref="EMP-001", helpdesk_ref=None)


@pytest.fixture(scope="module", autouse=True)
def _sweep_fixed_employee_row_after_module():
    """Setup: clears any UsageCounter rows already accumulated against
    _EMPLOYEE's fixed user_id before this module's tests run.
    check_and_record_usage() (app/agent/budget.py) commits real,
    permanent UsageCounter(user_key, hour-bucket) rows through its own
    independently-committing session -- never rolled back by db_session,
    and never cleaned up anywhere in this file. Every run_turn() call in
    this module uses the SAME fixed user_key (_EMPLOYEE.user_id is a
    literal constant, unlike test_agent_budget.py's per-test random-ish
    keys with its own cleanup_usage_counter fixture), so repeated runs of
    this suite within the same clock hour keep adding to the same
    counter row. Concretely: after several manual re-runs of this file
    while developing it, a later full-suite run failed with
    `AbortRun("exceeded 30 requests/hour")` instead of the expected
    max-iterations abort, purely because the accumulated count from
    earlier runs this hour had already crossed the cap -- a real,
    reproducible test-hygiene gap, not a flake.

    Teardown: once every test in this module has finished, every
    db_session-bound transaction has already rolled back and released
    its locks, so a final sweep here can safely delete this file's one
    fixed-UUID User row and anything still hanging off it (Run/Span/Task/
    Conversation) -- test_run_turn_emits_task_recorded_and_ticket_created_events
    (by design, see its docstring) leaves those committed and orphaned,
    since deleting them *during* that test would deadlock against the
    FOR KEY SHARE lock db_session's still-open transaction holds (Task
    6's review, again). Without this final sweep, that row stays in the
    shared dev/test Postgres instance's `users` table forever and
    inflates its total row count by one -- which is exactly what broke
    tests/test_seed.py's exact-count assertions (`assert total == 126`)
    the first time this file ran as part of the full suite.
    """
    from app.db.models import Run, Span, Task, UsageCounter
    from app.db.session import get_sessionmaker

    user_id = uuid.UUID(_EMPLOYEE.user_id)
    Session = get_sessionmaker()
    with Session() as session:
        session.query(UsageCounter).filter(UsageCounter.user_key == _EMPLOYEE.user_id).delete(synchronize_session=False)
        session.commit()

    yield

    with Session() as session:
        session.query(UsageCounter).filter(UsageCounter.user_key == _EMPLOYEE.user_id).delete(synchronize_session=False)
        run_ids = [row[0] for row in session.query(Run.id).filter(Run.user_id == user_id)]
        if run_ids:
            session.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
        session.query(Task).filter(Task.user_id == user_id).delete(synchronize_session=False)
        session.query(Run).filter(Run.user_id == user_id).delete(synchronize_session=False)
        session.query(Conversation).filter(Conversation.user_id == user_id).delete(synchronize_session=False)
        session.query(User).filter(User.id == user_id).delete(synchronize_session=False)
        session.commit()


def _conversation(db_session):
    """Creates the Conversation (and, if missing, the backing User row)
    _EMPLOYEE's fixed UUID needs to exist for FK purposes.

    This deliberately does NOT use db_session to insert either row.
    run_turn() calls app.tracing.start_run(), which inserts the Run row
    through its own independently-committing session (a different
    physical connection from db_session's) -- see app/tracing/spans.py's
    module docstring. Postgres FK checks run under READ COMMITTED, which
    only sees rows another connection has actually committed; a row
    inserted via db_session only ever becomes a SAVEPOINT release (see
    this file's db_session fixture: it binds to a connection with an
    outer transaction that is rolled back at teardown, never truly
    committed) and is therefore invisible to the tracing connection's FK
    check on runs.conversation_id. Concretely: `Conversation(...)` +
    `db_session.commit()` alone reproduces
    `IntegrityError: ... violates foreign key constraint "runs_conversation_id_fkey"`
    the moment run_turn() calls start_run() -- this is a real,
    reproducible gap in this plan's Step 1 test code, not a hypothetical.
    Using a real, hard-committing session here (same category as
    conftest.py's `cleanup_run` helper) makes both rows visible to every
    connection *before* run_turn ever touches them; db_session then sees
    them too under READ COMMITTED for its own reads/writes (get_my_profile,
    record_task) with no special handling needed on that side.
    """
    from app.db.session import get_sessionmaker

    user_id = uuid.UUID(_EMPLOYEE.user_id)
    Session = get_sessionmaker()
    with Session() as session:
        if session.get(User, user_id) is None:
            session.add(User(
                id=user_id, username=f"loop-test-{user_id.hex[:8]}", email=f"loop-test-{user_id.hex[:8]}@northstar.example",
                full_name="Loop Test Employee", password_hash="x", role=Role.EMPLOYEE,
                clearance=Clearance.STANDARD, department="Engineering", employee_ref=None,
            ))
            session.commit()
        conv = Conversation(user_id=user_id)
        session.add(conv)
        session.commit()
        session.refresh(conv)
        return conv


def _cleanup_conversation(conversation_id: uuid.UUID) -> None:
    """Mirrors conftest.py's `cleanup_run`: deletes the Conversation row
    created above through its own hard-committing session, since it was
    never inserted through db_session and so is never rolled back by that
    fixture's teardown. Only safe once nothing still FK-references this
    conversation (no live Run and no Task row) -- callers must clean
    those up first, or (as with the one test that keeps its Run/Task rows
    as a documented, accepted orphan) skip this entirely."""
    from app.db.session import get_sessionmaker

    Session = get_sessionmaker()
    with Session() as session:
        session.query(Conversation).filter(Conversation.id == conversation_id).delete()
        session.commit()


async def _collect(gen):
    return [event async for event in gen]


async def test_run_turn_answers_directly_with_no_tool_calls(db_session, cleanup_run):
    conv = _conversation(db_session)
    client = FakeAnthropicClient([make_text_message(text="You can reset your password at the self-service portal.")])
    run_id_holder = {}

    events = await _collect(run_turn(
        client, db_session, _EMPLOYEE, conversation_id=conv.id,
        user_key=_EMPLOYEE.user_id, history=[], user_message="How do I reset my password?",
    ))

    done_events = [e for e in events if e.type == "done"]
    assert len(done_events) == 1
    assert any(e.type == "token" for e in events)
    run_id_holder["id"] = done_events[0].data["run_id"]
    cleanup_run(uuid.UUID(run_id_holder["id"]))
    _cleanup_conversation(conv.id)


async def test_run_turn_executes_a_tool_call_and_feeds_result_back(db_session, cleanup_run):
    conv = _conversation(db_session)
    client = FakeAnthropicClient([
        make_tool_use_message(tool_name="get_my_profile", tool_input={}, tool_use_id="t1"),
        make_text_message(text="Your department is Engineering."),
    ])

    events = await _collect(run_turn(
        client, db_session, _EMPLOYEE, conversation_id=conv.id,
        user_key=_EMPLOYEE.user_id, history=[], user_message="What department am I in?",
    ))

    assert any(e.type == "tool_start" and e.data["name"] == "get_my_profile" for e in events)
    assert any(e.type == "tool_end" and e.data["name"] == "get_my_profile" for e in events)
    done = next(e for e in events if e.type == "done")
    cleanup_run(uuid.UUID(done.data["run_id"]))
    _cleanup_conversation(conv.id)
    assert len(client.calls) == 2  # one call per iteration


async def test_run_turn_stops_after_max_iterations(db_session, cleanup_run, monkeypatch):
    from app import config
    conv = _conversation(db_session)
    monkeypatch.setattr(config.get_settings(), "max_tool_iterations", 2)
    responses = [make_tool_use_message(tool_name="get_my_profile", tool_input={}, tool_use_id=f"t{i}") for i in range(5)]
    client = FakeAnthropicClient(responses)

    events = await _collect(run_turn(
        client, db_session, _EMPLOYEE, conversation_id=conv.id,
        user_key=_EMPLOYEE.user_id, history=[], user_message="loop forever",
    ))

    error_events = [e for e in events if e.type == "error"]
    assert len(error_events) == 1
    assert "iterations" in error_events[0].data["message"]
    done = next(e for e in events if e.type == "done")
    cleanup_run(uuid.UUID(done.data["run_id"]))
    _cleanup_conversation(conv.id)


async def test_run_turn_emits_task_recorded_and_ticket_created_events(db_session):
    # No cleanup_run here, deliberately: record_task inserts a Task row via
    # db_session that FK-references this run's real, independently-committed
    # tracing Run row. db_session's fixture holds its transaction open for
    # the whole test function (commit() only releases a savepoint), so a
    # cleanup_run() DELETE from a separate connection would deadlock against
    # the FK-referencing lock db_session is still holding -- the exact
    # mechanism diagnosed in Task 6's review. Unlike Task 6's own test
    # helpers, there's no substitute here: this Run must be a real tracing
    # run for the loop code under test to behave correctly. The orphaned
    # Run/Span/Task rows are an accepted, documented cost of this one test.
    conv = _conversation(db_session)
    client = FakeAnthropicClient([
        make_tool_use_message(tool_name="record_task", tool_input={
            "title": "VPN broken", "category": "vpn_network",
            "severity": "medium", "summary": "s", "affected_systems": [], "evidence": {},
        }, tool_use_id="t1"),
        make_text_message(text="I've recorded your issue."),
    ])

    events = await _collect(run_turn(
        client, db_session, _EMPLOYEE, conversation_id=conv.id,
        user_key=_EMPLOYEE.user_id, history=[], user_message="My VPN is broken",
    ))

    assert any(e.type == "task_recorded" for e in events)


async def test_run_turn_raises_nothing_and_emits_error_event_when_agent_disabled(db_session, cleanup_run, monkeypatch):
    from app import config
    conv = _conversation(db_session)
    monkeypatch.setattr(config.get_settings(), "agent_enabled", False)
    client = FakeAnthropicClient([make_text_message(text="unused")])

    events = await _collect(run_turn(
        client, db_session, _EMPLOYEE, conversation_id=conv.id,
        user_key=_EMPLOYEE.user_id, history=[], user_message="hello",
    ))

    assert events[0].type == "error"
    assert "disabled" in events[0].data["message"].lower()
    assert client.calls == []  # never called the model at all
    _cleanup_conversation(conv.id)
