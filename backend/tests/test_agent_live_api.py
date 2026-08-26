from __future__ import annotations

import uuid

import pytest

from app.agent.registry import TOOLS, to_anthropic_tool_params
from app.agent.loop import run_turn
from app.db.models import Conversation, Role, User
from app.rbac.policy import Principal

_FORBIDDEN_TOOL_NAMES = {
    "send_email", "grant_access", "grant_system_access", "update_user_clearance",
    "reassign_ticket_cross_department", "cross_department_ticket_assignment", "reset_credential", "run_sql", "execute_sql",
}


def test_forbidden_tools_are_structurally_absent_from_the_serialized_catalog():
    """Not live-marked -- this is a structural check on the static TOOLS
    list, costs nothing, and is the other half of the phase-4 gate."""
    params = to_anthropic_tool_params()
    serialized_names = {p["name"] if isinstance(p, dict) else p.name for p in params}
    assert serialized_names.isdisjoint(_FORBIDDEN_TOOL_NAMES)
    # 12 tools total: the 11 dispatchable custom tools plus web_search,
    # which occupies a real TOOLS/ToolSpec slot (Task 9's own Interfaces
    # section requires this) even though it's served as a raw server-tool
    # dict, not a Pydantic-schema custom tool, by to_anthropic_tool_params().
    assert len(TOOLS) == 12


@pytest.mark.live_api
async def test_two_turn_conversation_shows_cache_read_on_second_turn(db_session, cleanup_run):
    """THE gate (spec 18): a real two-turn conversation must show
    usage.cache_read_input_tokens > 0 on the second turn. Run manually
    once as this phase's final verification: `uv run pytest tests/test_agent_live_api.py -v -m live_api`.
    Requires a real ANTHROPIC_API_KEY in .env.

    The User/Conversation rows are created through a real, hard-committing
    session (not db_session) for the same reason established in Tasks 11
    and 12: run_turn() calls tracing.start_run(conversation_id=...)
    internally, which inserts a Run row via tracing's own independently-
    committing session (a different physical Postgres connection). Under
    READ COMMITTED, that connection cannot see a Conversation that only
    exists as a SAVEPOINT inside db_session's still-open transaction --
    confirmed by reproducing exactly this IntegrityError on
    runs_conversation_id_fkey when this test used db_session directly, as
    the plan's original draft did."""
    import anthropic
    from app.config import get_settings
    from app.db.session import get_sessionmaker

    client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    Session = get_sessionmaker()
    with Session() as session:
        user = User(
            username="live-api-test-user", email="live-api-test@northstar.example", full_name="Live API Test User",
            password_hash="x", role=Role.EMPLOYEE,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        principal = Principal(kind="user", user_id=str(user.id), role="employee", clearance="standard", department="Engineering", employee_ref=None, helpdesk_ref=None)
        conv = Conversation(user_id=user.id)
        session.add(conv)
        session.commit()
        session.refresh(conv)
        user_id, conv_id = user.id, conv.id

    run_ids = []
    first_events = [e async for e in run_turn(client, db_session, principal, conversation_id=conv_id, user_key=str(user_id), history=[], user_message="What is a VPN, briefly?")]
    first_done = next(e for e in first_events if e.type == "done")
    run_ids.append(first_done.data["run_id"])
    first_text = "".join(e.data["text"] for e in first_events if e.type == "token")
    history = [{"role": "user", "content": "What is a VPN, briefly?"}, {"role": "assistant", "content": [{"type": "text", "text": first_text}]}]

    second_events = [e async for e in run_turn(client, db_session, principal, conversation_id=conv_id, user_key=str(user_id), history=history, user_message="And what does MFA stand for?")]
    second_done = next(e for e in second_events if e.type == "done")
    run_ids.append(second_done.data["run_id"])

    from app.tracing import trace_tree
    second_trace = trace_tree(second_done.data["run_id"])
    llm_spans = [node.span for node in second_trace.roots if node.span.kind.value == "llm"]
    print(f"MEASURED cache_read_tokens per LLM span on turn two: {[(s.name, s.cache_read_tokens) for s in llm_spans]}")
    assert any((s.cache_read_tokens or 0) > 0 for s in llm_spans), (
        f"Expected cache_read_tokens > 0 on turn two's LLM span(s); got: "
        f"{[(s.name, s.cache_read_tokens) for s in llm_spans]}"
    )

    for run_id in run_ids:
        cleanup_run(uuid.UUID(run_id))

    # cleanup_run only removes Run/Span rows; the hard-committed
    # User/Conversation rows created above need their own cleanup since
    # they were never inserted via db_session (which would auto-rollback).
    with Session() as session:
        session.query(Conversation).filter(Conversation.id == conv_id).delete()
        session.query(User).filter(User.id == user_id).delete()
        session.commit()
