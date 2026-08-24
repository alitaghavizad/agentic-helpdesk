import pytest

from app.rbac.policy import (
    Principal, RetrievalDenied, map_access_classification,
    map_escalation_authority, retrieval_filter,
)


def _principal(role, **overrides):
    base = dict(
        kind="user", user_id="u1", role=role, clearance=None,
        department=None, employee_ref=None, helpdesk_ref=None,
    )
    base.update(overrides)
    return Principal(**base)


# -- clearance / escalation mapping ------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Standard", "standard"),
    ("Sensitive business-data access", "sensitive"),
    ("Privileged production access with approval", "privileged"),
])
def test_map_access_classification_known_values(raw, expected):
    assert map_access_classification(raw).value == expected


def test_map_access_classification_unknown_value_raises():
    with pytest.raises(ValueError):
        map_access_classification("Something else entirely")


@pytest.mark.parametrize("raw,expected", [("Standard", "standard"), ("High", "high")])
def test_map_escalation_authority_known_values(raw, expected):
    assert map_escalation_authority(raw) == expected


def test_map_escalation_authority_unknown_value_raises():
    with pytest.raises(ValueError):
        map_escalation_authority("Extreme")


# -- guest: denied on every people/lesson collection -------------------------

@pytest.mark.parametrize("collection", ["employees", "helpdesk", "lessons"])
def test_guest_denied_on_every_collection(collection):
    guest = _principal("guest", kind="guest", user_id=None)
    with pytest.raises(RetrievalDenied):
        retrieval_filter(guest, collection)


# -- employee / standard ------------------------------------------------------

def test_standard_employee_sees_only_own_employee_record():
    principal = _principal("employee", clearance="standard", employee_ref="EMP-042")
    assert retrieval_filter(principal, "employees") == {"employee_id": "EMP-042"}


def test_standard_employee_sees_only_routing_section_of_helpdesk():
    principal = _principal("employee", clearance="standard")
    result = retrieval_filter(principal, "helpdesk")
    assert result == {"section": {"$in": ["Overview", "Routing guidance"]}}


# -- employee / sensitive -----------------------------------------------------

def test_sensitive_employee_sees_own_record_or_department():
    principal = _principal(
        "employee", clearance="sensitive", employee_ref="EMP-042", department="Finance",
    )
    result = retrieval_filter(principal, "employees")
    assert result == {"$or": [
        {"employee_id": "EMP-042"},
        {"department": "Finance"},
    ]}


def test_sensitive_employee_helpdesk_scope_same_as_standard():
    principal = _principal("employee", clearance="sensitive")
    assert retrieval_filter(principal, "helpdesk") == {"section": {"$in": ["Overview", "Routing guidance"]}}


# -- employee / privileged ----------------------------------------------------

def test_privileged_employee_sees_own_dept_and_other_depts_excluding_hr_legal():
    principal = _principal(
        "employee", clearance="privileged", employee_ref="EMP-001", department="Engineering",
    )
    result = retrieval_filter(principal, "employees")
    assert result == {"$or": [
        {"employee_id": "EMP-001"},
        {"department": "Engineering"},
        {"department": {"$nin": ["HR", "Legal"]}},
    ]}


def test_privileged_employee_sees_full_helpdesk_documents():
    principal = _principal("employee", clearance="privileged")
    assert retrieval_filter(principal, "helpdesk") == {}


# -- employee: missing employee_ref must fail closed, never emit None --------

@pytest.mark.parametrize("clearance", ["standard", "sensitive", "privileged"])
def test_employee_with_no_employee_ref_is_denied_not_null_filtered(clearance):
    principal = _principal("employee", clearance=clearance, employee_ref=None)
    with pytest.raises(RetrievalDenied):
        retrieval_filter(principal, "employees")


# -- employee: missing department must never appear as a None-valued clause --
# Documented choice: a sensitive/privileged employee with department=None is
# scoped to their own employee_id only; the "$or" department clause is
# omitted rather than emitting {"department": None}.

def test_sensitive_employee_with_no_department_falls_back_to_own_record_only():
    principal = _principal(
        "employee", clearance="sensitive", employee_ref="EMP-042", department=None,
    )
    result = retrieval_filter(principal, "employees")
    assert result == {"employee_id": "EMP-042"}
    assert "department" not in str(result) or "None" not in str(result)
    for clause in result.get("$or", []):
        assert None not in clause.values()


def test_privileged_employee_with_no_department_omits_own_department_clause():
    principal = _principal(
        "employee", clearance="privileged", employee_ref="EMP-001", department=None,
    )
    result = retrieval_filter(principal, "employees")
    assert result == {"$or": [
        {"employee_id": "EMP-001"},
        {"department": {"$nin": ["HR", "Legal"]}},
    ]}
    for clause in result["$or"]:
        assert None not in clause.values()


# -- helpdesk ------------------------------------------------------------------

def test_helpdesk_sees_only_requesters_on_their_tickets():
    principal = _principal("helpdesk", helpdesk_ref="HD-001")
    result = retrieval_filter(
        principal, "employees", helpdesk_visible_employee_ids=["EMP-007", "EMP-018"]
    )
    assert result == {"employee_id": {"$in": ["EMP-007", "EMP-018"]}}


def test_helpdesk_with_no_assigned_tickets_sees_no_employees():
    principal = _principal("helpdesk", helpdesk_ref="HD-001")
    result = retrieval_filter(principal, "employees")
    assert result == {"employee_id": {"$in": []}}


def test_helpdesk_sees_full_helpdesk_documents():
    principal = _principal("helpdesk", helpdesk_ref="HD-001")
    assert retrieval_filter(principal, "helpdesk") == {}


# -- admin ----------------------------------------------------------------------

@pytest.mark.parametrize("collection", ["employees", "helpdesk"])
def test_admin_is_unrestricted(collection):
    principal = _principal("admin")
    assert retrieval_filter(principal, collection) == {}


# -- lessons: allowed for everyone except guest ---------------------------------

@pytest.mark.parametrize("role,extra", [
    ("employee", {"clearance": "standard"}),
    ("helpdesk", {}),
    ("admin", {}),
])
def test_lessons_allowed_for_every_non_guest_role(role, extra):
    principal = _principal(role, **extra)
    assert retrieval_filter(principal, "lessons") == {}


# -- unknown inputs ---------------------------------------------------------------

def test_unknown_collection_raises_value_error():
    principal = _principal("admin")
    with pytest.raises(ValueError):
        retrieval_filter(principal, "not_a_real_collection")


@pytest.mark.parametrize("collection", ["employees", "helpdesk", "lessons"])
def test_unrecognized_role_raises_on_every_collection(collection):
    principal = _principal("superuser")
    with pytest.raises(RetrievalDenied):
        retrieval_filter(principal, collection)
