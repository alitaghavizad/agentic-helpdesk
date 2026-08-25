from __future__ import annotations

import pytest

from app.rbac.policy import Allow, Deny, Principal, authorize


def _principal(role: str, **overrides) -> Principal:
    base = dict(
        kind="user" if role != "guest" else "guest", user_id="u1", role=role, clearance=None,
        department=None, employee_ref=None, helpdesk_ref=None,
    )
    base.update(overrides)
    return Principal(**base)


@pytest.mark.parametrize("tool_name", ["search_knowledge", "search_lessons", "get_helpdesk_workload"])
def test_guest_denied_specific_tools(tool_name):
    decision = authorize(_principal("guest"), tool_name, {})
    assert isinstance(decision, Deny)
    assert tool_name in decision.reason


@pytest.mark.parametrize("tool_name", [
    "web_search", "get_my_profile", "list_my_tickets", "get_ticket",
    "find_helpdesk_specialist", "request_attachment", "record_task",
    "create_ticket", "create_approval_request",
])
def test_guest_allowed_remaining_tools(tool_name):
    assert authorize(_principal("guest"), tool_name, {}) == Allow()


@pytest.mark.parametrize("role", ["employee", "helpdesk", "admin"])
@pytest.mark.parametrize("tool_name", ["search_knowledge", "search_lessons", "get_helpdesk_workload"])
def test_non_guest_roles_allowed_all_tools(role, tool_name):
    assert authorize(_principal(role), tool_name, {}) == Allow()


def test_unknown_tool_name_is_allowed_by_default():
    # authorize() is a coarse pre-check, not a tool-existence check -- the
    # registry itself rejects unknown tool names with its own error. A tool
    # name authorize() has no rule for is not a security decision it should
    # be making, so it defers (Allow) rather than guessing.
    assert authorize(_principal("employee"), "some_future_tool", {}) == Allow()
