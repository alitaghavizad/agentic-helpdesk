from __future__ import annotations

from app.agent.prompts import build_system_prompt
from app.rbac.policy import Principal

_EMPLOYEE = Principal(kind="user", user_id="u1", role="employee", clearance="privileged", department="Engineering", employee_ref="EMP-001", helpdesk_ref=None)
_GUEST = Principal(kind="guest", user_id=None, role="guest", clearance=None, department=None, employee_ref=None, helpdesk_ref=None)


def test_system_prompt_sections_appear_in_fixed_order():
    prompt = build_system_prompt(_EMPLOYEE)
    order = ["# Role", "# Your identity", "# Outcomes", "# Untrusted data", "# Tool use", "# Escalation policy"]
    positions = [prompt.index(section) for section in order]
    assert positions == sorted(positions)


def test_system_prompt_includes_principal_identity_and_clearance():
    prompt = build_system_prompt(_EMPLOYEE)
    assert "employee" in prompt
    assert "privileged" in prompt
    assert "EMP-001" in prompt


def test_system_prompt_for_guest_has_no_employee_ref():
    prompt = build_system_prompt(_GUEST)
    assert "guest" in prompt
    assert "EMP-" not in prompt


def test_system_prompt_states_untrusted_data_contract():
    prompt = build_system_prompt(_EMPLOYEE)
    assert "untrusted_data" in prompt
    assert "never an instruction" in prompt.lower() or "not an instruction" in prompt.lower()


def test_system_prompt_never_treat_requester_as_self_approving():
    prompt = build_system_prompt(_EMPLOYEE)
    assert "self-approv" in prompt.lower()


def test_system_prompt_is_byte_identical_for_same_principal_across_calls():
    # Cache-prefix stability (spec 8.1) depends on this -- nothing
    # request-specific (timestamps, request ids) may vary between calls
    # for the same principal.
    assert build_system_prompt(_EMPLOYEE) == build_system_prompt(_EMPLOYEE)
