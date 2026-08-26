from __future__ import annotations

import dataclasses

import pytest

from app.agent.registry import TOOLS, dispatch_tool, to_anthropic_tool_params
from app.db.models import Conversation
from app.rbac.policy import Principal

_GUEST = Principal(kind="guest", user_id=None, role="guest", clearance=None, department=None, employee_ref=None, helpdesk_ref=None)
_EMPLOYEE = Principal(kind="user", user_id="00000000-0000-0000-0000-000000000001", role="employee", clearance="standard", department="Engineering", employee_ref="EMP-001", helpdesk_ref=None)

_FORBIDDEN_TOOL_NAMES = {
    "send_email", "grant_access", "grant_system_access", "update_user_clearance",
    "reassign_ticket_cross_department", "cross_department_ticket_assignment", "reset_credential", "run_sql", "execute_sql",
}


@pytest.fixture(autouse=True)
def _traced_run(cleanup_run):
    """dispatch_tool always wraps rbac.authorize (and, when authorized, the
    handler itself) in a tracing span; span() hard-requires an active run
    and raises RuntimeError otherwise (see app/tracing/spans.py's module
    docstring and tests/test_agent_tools_routing.py's identical fixture)."""
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


def test_tool_catalog_has_exactly_the_twelve_buildable_tools():
    names = {t.name for t in TOOLS}
    assert names == {
        "search_knowledge", "search_lessons", "web_search", "get_my_profile",
        "list_my_tickets", "get_ticket", "find_helpdesk_specialist", "get_helpdesk_workload",
        "request_attachment", "record_task", "create_ticket", "create_approval_request",
    }
    assert "parse_attachment" not in names  # genuinely blocked this phase (Phase 7)


def test_serialized_tool_catalog_contains_no_forbidden_tool_names():
    params = to_anthropic_tool_params()
    serialized_names = {p["name"] if isinstance(p, dict) else p.name for p in params}
    assert serialized_names.isdisjoint(_FORBIDDEN_TOOL_NAMES)


def test_serialized_tool_catalog_is_strict_with_no_additional_properties():
    for param in to_anthropic_tool_params():
        p = param if isinstance(param, dict) else param.model_dump()
        if p.get("type", "custom") not in ("custom", None):
            continue  # server tools (web_search) don't carry input_schema/strict
        assert p.get("strict") is True
        assert p["input_schema"].get("additionalProperties") is False


async def test_dispatch_tool_denies_guest_for_search_knowledge():
    result = await dispatch_tool(_GUEST, db=None, tool_name="search_knowledge", tool_use_id="t1", raw_input='{"query": "x"}', extra_context={})
    assert result["is_error"] is True


async def test_dispatch_tool_returns_error_for_invalid_json_arguments(db_session):
    result = await dispatch_tool(_EMPLOYEE, db=db_session, tool_name="get_my_profile", tool_use_id="t1", raw_input="not json", extra_context={})
    assert result["is_error"] is True


async def test_dispatch_tool_returns_error_for_unknown_tool(db_session):
    result = await dispatch_tool(_EMPLOYEE, db=db_session, tool_name="totally_made_up_tool", tool_use_id="t1", raw_input="{}", extra_context={})
    assert result["is_error"] is True


async def test_dispatch_tool_filters_extra_context_to_handlers_own_parameters(db_session):
    # get_helpdesk_workload_handler accepts ONLY (principal, db, args) -- if
    # extra_context isn't filtered by the handler's own signature, this
    # raises TypeError instead of returning a result.
    result = await dispatch_tool(
        _EMPLOYEE, db=db_session, tool_name="get_helpdesk_workload", tool_use_id="t1",
        raw_input="{}", extra_context={"conversation_id": "00000000-0000-0000-0000-000000000099", "run_id": "00000000-0000-0000-0000-000000000098", "guest_email": None},
    )
    assert result.get("is_error") is not True


async def test_dispatch_tool_converts_handler_exception_to_is_error(db_session, monkeypatch):
    import app.agent.registry as registry_module

    async def _boom(principal, db, args):
        raise RuntimeError("simulated handler failure")

    try:
        monkeypatch.setattr(registry_module.TOOLS_BY_NAME["get_my_profile"], "handler", _boom)
    except dataclasses.FrozenInstanceError:
        monkeypatch.setattr(
            registry_module, "TOOLS_BY_NAME",
            {**registry_module.TOOLS_BY_NAME, "get_my_profile": dataclasses.replace(registry_module.TOOLS_BY_NAME["get_my_profile"], handler=_boom)},
        )
    result = await dispatch_tool(_EMPLOYEE, db=db_session, tool_name="get_my_profile", tool_use_id="t1", raw_input="{}", extra_context={})
    assert result["is_error"] is True
    assert "simulated handler failure" in result["content"]
