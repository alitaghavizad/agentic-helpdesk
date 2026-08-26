from __future__ import annotations

import pytest

from app.agent.tools.knowledge import (
    GetMyProfileArgs,
    SearchKnowledgeArgs,
    get_my_profile_handler,
    search_knowledge_handler,
)
from app.db.models import Clearance, Role, User
from app.rbac.policy import Principal


@pytest.fixture(autouse=True)
def _traced_run(cleanup_run):
    """search_knowledge_handler calls McpChromaBackend.query, which wraps
    every MCP call in a tracing span (Task 3); span() hard-requires an
    active run and raises RuntimeError otherwise (see
    app/tracing/spans.py's module docstring and
    tests/test_agent_tools_routing.py's identical fixture)."""
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


def _make_user(db_session, **overrides):
    base = dict(
        username="jdoe", email="jdoe@northstar.example", full_name="Jane Doe", password_hash="x", role=Role.EMPLOYEE,
        clearance=Clearance.STANDARD, department="Engineering", employee_ref="EMP-001",
    )
    base.update(overrides)
    user = User(**base)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _principal_for(user: User) -> Principal:
    return Principal(
        kind="user", user_id=str(user.id), role=user.role.value,
        clearance=user.clearance.value if user.clearance else None,
        department=user.department, employee_ref=user.employee_ref, helpdesk_ref=user.helpdesk_ref,
    )


async def test_get_my_profile_handler_returns_own_record_only(db_session):
    user = _make_user(db_session)
    result = await get_my_profile_handler(_principal_for(user), db_session, GetMyProfileArgs())
    assert result["employee_ref"] == "EMP-001"
    assert result["department"] == "Engineering"


async def test_search_knowledge_handler_wraps_results_as_untrusted(db_session):
    user = _make_user(db_session)
    result = await search_knowledge_handler(_principal_for(user), db_session, SearchKnowledgeArgs(query="VPN setup", scope="employees", k=3))
    assert "results" in result
    for wrapped in result["results"]:
        assert wrapped.startswith('<untrusted_data source="employees')
        assert wrapped.endswith("</untrusted_data>")
