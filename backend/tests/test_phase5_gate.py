from __future__ import annotations

import uuid

import pytest

from app.db.models import ResolutionPath, RunStatus, RunTrigger, Task, Ticket
from app.tickets.routing import rank_specialists

# (category, problem summary, exact set of specialization names a *correct*
# router could legitimately return for that category). These sets were
# built by reading all 25 real specializations in
# corporate_rag_dataset/helpdesk/*.md and deciding, independently of any
# test run, which ones genuinely own that category's responsibility --
# not by pinning to whatever a given run happened to produce. Matching is
# exact-set membership (case-insensitive) rather than substring, per
# fix-round-1 review: substrings like "network" or "access" each hit
# multiple unrelated specializations among the 25 (see full list below),
# so they could let a real routing regression slip through silently.
#
# Full 25 for reference (HD-001..HD-025): Identity and Access Management,
# Windows endpoint support, macOS support, Linux developer workstations,
# VPN and network access, Microsoft 365 and collaboration, Jira and
# Confluence, GitHub Enterprise, Cloud and Kubernetes access, Database
# access, SAP and Finance systems, Salesforce and CRM, HR systems, Security
# incidents, Endpoint encryption, Hardware and peripherals, Office
# networking, SSO and MFA, Developer tooling, Monitoring and observability,
# ServiceNow workflows, Email and calendar, Mobile device management,
# Privileged access escalation, General L1 triage.
_ROUTING_CASES = [
    # Only "VPN and network access" owns remote VPN connectivity. "Office
    # networking" is a distinct specialization (in-office LAN/WiFi/printers,
    # not a home VPN client) and must NOT be accepted even though it also
    # contains the word "network".
    ("vpn_network", "I cannot connect to the corporate VPN from home; the client times out.", {"vpn and network access"}),
    # "SSO and MFA" is the single, dedicated owner of this category. Per
    # the design spec (2026-08-24-agentic-helpdesk-design.md:359) and
    # routing.py's own module docstring, the 25 specializations are
    # deliberately distinct and single-holder -- a purpose-built MFA
    # specialist exists precisely so a broader catch-all shouldn't need to
    # be treated as an equally-correct answer. "Identity and Access
    # Management" is NOT accepted: it is a plausible-sounding but broader
    # specialization, and admitting it would reintroduce the overbreadth
    # this fix is about, just at smaller scale. (All 25 profile files share
    # one boilerplate template, including the "Route tickets to HD-NNN
    # when the dominant issue concerns <specialization>" routing-guidance
    # line -- that line is not specific evidence of any specialization's
    # scope, since it appears verbatim, with only the name substituted, on
    # every one of the 25 files.) Also excluded: "Mobile device management"
    # (the query mentions a replaced phone, but device management is not
    # authentication) and "Privileged access escalation" (privilege
    # elevation, not MFA) -- both were accidentally admitted by the old
    # "access" substring.
    ("authentication_mfa", "My MFA token stopped working after I replaced my phone.", {"sso and mfa"}),
    # Only "Database access" owns this category; no other specialization
    # among the 25 concerns databases.
    ("database_access", "I need read access to the analytics reporting database.", {"database access"}),
]


@pytest.fixture(autouse=True)
def _traced_run(cleanup_run):
    """rank_specialists queries Chroma through McpChromaBackend, which wraps
    every MCP call in a tracing span; span() hard-requires an active run."""
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


@pytest.mark.parametrize("category,summary,expected_specializations", _ROUTING_CASES)
async def test_routing_picks_a_specialist_whose_specialization_matches_the_category(
    db_session, category, summary, expected_specializations,
):
    """The 'assigned to a specialist whose specialization matches the
    category' half of the spec-18 phase-5 gate, measured against the real
    seeded helpdesk collection -- not against fabricated candidates."""
    candidates = await rank_specialists(db_session, problem_summary=summary, category=category, severity="medium")

    assert candidates, f"no candidate returned for {category!r}"
    top = candidates[0]
    specialization = (top["specialization"] or "").strip().lower()
    assert specialization in expected_specializations, (
        f"top candidate for {category!r} was {top['helpdesk_ref']} "
        f"({top['specialization']!r}), which is not one of {sorted(expected_specializations)}"
    )


_GATE_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000005a1")
_gate_orphans: dict[str, list] = {"user_ids": [], "conversation_ids": []}


@pytest.fixture(scope="module", autouse=True)
def _sweep_gate_orphans_after_module():
    """The end-to-end test below must hard-commit its User/Conversation
    (run_turn's tracing session commits on its own connection and cannot see
    db_session's savepoint -- the cross-connection FK-visibility gap that bit
    every Phase 4 task from Task 6 onward). Those rows therefore survive
    db_session's rollback and must be swept here, after every test in this
    module has released its locks. Mirrors
    tests/test_chat_router.py::_cleanup_sse_test_orphans_after_module,
    including its UsageCounter sweep -- without which repeated suite runs
    accumulate rows against the same fixed user_key and eventually trip the
    real 30/hour cap, and the leaked User row breaks test_seed.py's exact
    `assert total == 126`."""
    yield

    from app.db.models import Conversation, Run, Span, Ticket, UsageCounter, User
    from app.db.session import get_sessionmaker

    Session = get_sessionmaker()
    with Session() as session:
        conv_ids = list(_gate_orphans["conversation_ids"])
        user_ids = list(_gate_orphans["user_ids"])
        if user_ids:
            session.query(UsageCounter).filter(UsageCounter.user_key.in_([str(u) for u in user_ids])).delete(synchronize_session=False)
        if conv_ids:
            session.query(Ticket).filter(Ticket.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
            session.query(Task).filter(Task.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
            run_ids = [row[0] for row in session.query(Run.id).filter(Run.conversation_id.in_(conv_ids))]
            if run_ids:
                session.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
                session.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
            session.query(Conversation).filter(Conversation.id.in_(conv_ids)).delete(synchronize_session=False)
        if user_ids:
            session.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        session.commit()


async def test_a_scripted_conversation_yields_a_task_row_and_a_ticket(db_session):
    """The 'a conversation yields a tasks row and a ticket' half of the
    spec-18 phase-5 gate. The assignee ref is scripted here (a fake client
    cannot react to find_helpdesk_specialist's output), so this asserts the
    PLUMBING -- task row, ticket row, resolution_path, assignee_user_id
    resolution. Routing quality is the separately-measured half above, and
    the live-API test in Task 11 proves the model genuinely does both."""
    from app.agent.loop import run_turn
    from app.db.models import Conversation, EscalationAuthority, Role, TaskCategory, User
    from app.db.session import get_sessionmaker
    from app.rbac.policy import Principal
    from tests.support.fake_anthropic import (
        FakeAnthropicClient, make_text_message, make_tool_use_message,
    )

    Session = get_sessionmaker()
    with Session() as setup:
        user = setup.get(User, _GATE_USER_ID)
        if user is None:
            user = User(
                id=_GATE_USER_ID, username="phase5gate", email="phase5gate@northstar.example",
                full_name="Phase Five Gate", password_hash="x", role=Role.EMPLOYEE,
                clearance=None, employee_ref="EMP-5A1",
            )
            setup.add(user)
        specialist = setup.query(User).filter(User.helpdesk_ref == "HD-5A1").one_or_none()
        if specialist is None:
            specialist = User(
                username="hd-5a1", email="hd-5a1@northstar.example", full_name="HD-5A1",
                password_hash="x", role=Role.HELPDESK, helpdesk_ref="HD-5A1",
                specialization="Network and VPN Support", escalation_authority=EscalationAuthority.STANDARD,
            )
            setup.add(specialist)
        conv = Conversation(user_id=_GATE_USER_ID, title="Gate conversation")
        setup.add(conv)
        setup.commit()
        conversation_id = conv.id
        specialist_id = specialist.id

    _gate_orphans["user_ids"].append(_GATE_USER_ID)
    # NOTE: deviation from the brief -- the brief's snippet only tracks
    # _GATE_USER_ID here, never the specialist row it creates above. HD-5A1
    # is not a seeded helpdesk_ref (seed.py uses HD-0XX), so that specialist
    # row leaked (confirmed: users count was 127, not the expected 126,
    # before this fix). Tracking it here restores the "users == 126" gate
    # this same fixture's own docstring promises.
    _gate_orphans["user_ids"].append(specialist_id)
    _gate_orphans["conversation_ids"].append(conversation_id)

    principal = Principal(
        kind="user", user_id=str(_GATE_USER_ID), role="employee", clearance="standard",
        department="Engineering", employee_ref="EMP-5A1", helpdesk_ref=None,
    )

    # The scripted turn: classify -> file a ticket -> answer.
    client = FakeAnthropicClient([
        make_tool_use_message(tool_name="record_task", tool_use_id="tu1", tool_input={
            "title": "VPN client times out",
            "category": TaskCategory.VPN_NETWORK.value,
            "severity": "medium",
            "summary": "User cannot connect to the corporate VPN from home.",
            "affected_systems": ["vpn"],
            "evidence": {"error": "timeout"},
        }),
        make_text_message(text="I have recorded the problem."),
    ])

    task_id: str | None = None
    async for event in run_turn(
        client, db_session, principal, conversation_id=conversation_id,
        user_key=str(_GATE_USER_ID), history=[], user_message="My VPN will not connect.",
    ):
        if event.type == "task_recorded":
            task_id = event.data["task_id"]

    assert task_id is not None, "the scripted record_task call produced no task_recorded event"

    task = db_session.get(Task, uuid.UUID(task_id))
    assert task is not None
    assert task.category == TaskCategory.VPN_NETWORK

    # Second turn: now that task_id exists, script the create_ticket call.
    client2 = FakeAnthropicClient([
        make_tool_use_message(tool_name="create_ticket", tool_use_id="tu2", tool_input={
            "task_id": task_id,
            "assignee_helpdesk_ref": "HD-5A1",
            "priority": "medium",
            "title": "VPN client times out",
            "body": "User cannot connect to the corporate VPN from home.",
            "assignment_rationale": "Semantic match rank 1; current workload: 0 open ticket(s).",
            "matched_specialization": "Network and VPN Support",
            "assignment_score": 0.95,
        }),
        make_text_message(text="Ticket filed."),
    ])

    async for event in run_turn(
        client2, db_session, principal, conversation_id=conversation_id,
        user_key=str(_GATE_USER_ID), history=[], user_message="Please raise a ticket.",
    ):
        pass

    ticket = db_session.query(Ticket).filter(Ticket.task_id == uuid.UUID(task_id)).one()
    assert ticket.assignee_helpdesk_ref == "HD-5A1"
    assert ticket.assignee_user_id == specialist_id, "create_ticket did not resolve the assignee FK"

    db_session.refresh(task)
    assert task.resolution_path == ResolutionPath.TICKETED, "task was not marked ticketed"
